"""Async multi-protocol discovery orchestrator.

Fires arp-scan + mDNS + SSDP + UDP broadcast probes + TCP port sweeps in
parallel, feeds the results into a :class:`~models.Device` per MAC and
hands them to the :class:`~registry.DeviceRegistry`.

The design is intentionally boring: everything is coroutines on the same
event loop, semaphores keep the phone's Wi-Fi chip from drowning, and a
global ``asyncio.TaskGroup`` (Python 3.11+) means one failing probe
doesn't take the run down.

Cancellation: ``Discovery.stop()`` cancels the task group. Every probe
handles ``CancelledError`` by closing its socket in ``finally`` -- so
Stop takes <500 ms from the UI's perspective.
"""
from __future__ import annotations

import asyncio
import re
import socket
import struct
import subprocess
import time
from typing import Callable

from .fingerprint import FingerprintEngine, Observations
from .models import Device, Port
from .registry import DeviceRegistry


# ----------------------------------------------------------- helpers
def _now() -> float:
    return time.monotonic()


async def _sh(cmd: list[str], timeout: float = 10.0) -> str:
    """Run a subprocess and return stdout as text.

    Wraps ``asyncio.create_subprocess_exec`` with a hard timeout and
    guaranteed cancellation cleanup.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return ""
    try:
        out, _err = await asyncio.wait_for(proc.communicate(),
                                           timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    except asyncio.CancelledError:
        proc.kill()
        raise
    return out.decode("utf-8", errors="replace")


# ----------------------------------------------------------- probes
async def probe_arp(iface: str,
                    on_host: Callable[[str, str, str], None]) -> None:
    """arp-scan the local network, emit (ip, mac, vendor) triples.

    Uses the arp-scan binary (part of the app's install.sh deps). Falls
    back silently if arp-scan is missing -- other probes still run.
    """
    out = await _sh(
        ["arp-scan", "--interface=" + iface, "--localnet",
         "--ouifile=/usr/share/arp-scan/ieee-oui.txt",
         "--macfile=/dev/null"],
        timeout=15,
    )
    for line in out.splitlines():
        # arp-scan lines: "IP\tMAC\tVENDOR"
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        ip = parts[0].strip()
        mac = parts[1].strip()
        vendor = parts[2].strip() if len(parts) >= 3 else ""
        if not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
            continue
        if not re.match(r"^[0-9a-fA-F:]{17}$", mac):
            continue
        on_host(ip, mac.upper(), vendor)


async def probe_mdns(timeout: float,
                     on_service: Callable[[str, dict], None]) -> None:
    """Enumerate mDNS services via avahi-browse.

    Rather than rolling our own zeroconf browser (which is doable but
    brings in a dep), we shell out to ``avahi-browse -atrp`` which is
    already available on the phone.
    """
    out = await _sh(
        ["avahi-browse", "-a", "-t", "-r", "-p", "-l"],
        timeout=timeout + 2)
    for line in out.splitlines():
        parts = line.split(";")
        # Resolved records start with "=", have >=8 fields.
        if len(parts) < 10 or parts[0] != "=":
            continue
        svc_type = parts[4]
        name = parts[3]
        fqdn = parts[6]
        ip = parts[7]
        port = parts[8]
        txt = parts[9] if len(parts) > 9 else ""
        if ":" in ip:   # skip IPv6
            continue
        on_service(svc_type, {
            "name": name, "fqdn": fqdn, "ip": ip,
            "port": port, "txt": txt,
        })


async def probe_ssdp(targets: list[str], timeout: float,
                     on_reply: Callable[[str, dict], None]) -> None:
    """Fire M-SEARCH for each target, forward every reply.

    ``on_reply(ip, {st, location, server, usn})``.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setblocking(False)
    loop = asyncio.get_event_loop()
    try:
        for st in targets:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                "ST: %s\r\n\r\n" % st
            ).encode()
            try:
                await loop.sock_sendto(
                    sock, msg, ("239.255.255.250", 1900))
            except (OSError, NotImplementedError):
                # Some kernels want the sendto through non-async path.
                sock.sendto(msg, ("239.255.255.250", 1900))
        deadline = _now() + timeout
        while _now() < deadline:
            try:
                data = await asyncio.wait_for(
                    loop.sock_recv(sock, 4096),
                    timeout=deadline - _now())
            except asyncio.TimeoutError:
                break
            except (OSError, ConnectionError):
                break
            # sock_recv returns bytes; we need the addr, so use a
            # helper socket call directly.
            try:
                data, addr = sock.recvfrom(4096)
            except BlockingIOError:
                continue
            except OSError:
                break
            headers = _parse_ssdp_headers(data)
            on_reply(addr[0], headers)
    finally:
        sock.close()


def _parse_ssdp_headers(data: bytes) -> dict:
    out = {"st": "", "location": "", "server": "", "usn": ""}
    for line in data.decode("latin1", errors="replace").splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().lower()
        v = v.strip()
        if k in out:
            out[k] = v
    return out


# Kasa / TP-Link discovery: XOR-encrypted JSON to UDP/9999
def _kasa_encrypt(payload: str) -> bytes:
    key = 0xAB
    out = bytearray()
    for ch in payload.encode():
        key ^= ch
        out.append(key)
    return bytes(out)


def _kasa_decrypt(data: bytes) -> str:
    key = 0xAB
    out = bytearray()
    for b in data:
        out.append(b ^ key)
        key = b
    return out.decode("utf-8", errors="replace")


async def probe_kasa(timeout: float,
                     on_reply: Callable[[str, bytes], None]) -> None:
    """Broadcast Kasa discovery request on UDP 9999.

    Kasa/TP-Link plugs listen on 9999 UDP; the JSON body is XOR'd with
    key 0xAB. Cheap and reliable identifier.
    """
    payload = _kasa_encrypt('{"system":{"get_sysinfo":{}}}')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        try:
            sock.sendto(payload, ("255.255.255.255", 9999))
        except OSError:
            return
        deadline = _now() + timeout
        while _now() < deadline:
            try:
                remaining = deadline - _now()
                if remaining <= 0:
                    break
                await asyncio.wait_for(
                    asyncio.get_event_loop().sock_recv(sock, 4096),
                    timeout=remaining)
            except asyncio.TimeoutError:
                break
            except OSError:
                break
            try:
                data, addr = sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                continue
            on_reply(addr[0], data)
    finally:
        sock.close()


async def probe_wiz(timeout: float,
                    on_reply: Callable[[str, bytes], None]) -> None:
    """WiZ bulbs broadcast on UDP 38899 with a JSON getPilot request."""
    payload = b'{"method":"getPilot","params":{}}'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        try:
            sock.sendto(payload, ("255.255.255.255", 38899))
        except OSError:
            return
        deadline = _now() + timeout
        while _now() < deadline:
            try:
                remaining = deadline - _now()
                if remaining <= 0:
                    break
                await asyncio.wait_for(
                    asyncio.get_event_loop().sock_recv(sock, 4096),
                    timeout=remaining)
            except asyncio.TimeoutError:
                break
            except OSError:
                break
            try:
                data, addr = sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                continue
            on_reply(addr[0], data)
    finally:
        sock.close()


async def probe_tcp_ports(ip: str, ports: list[int],
                          timeout: float = 1.5,
                          semaphore: asyncio.Semaphore | None = None
                          ) -> list[int]:
    """Rapid TCP connect scan of one host.

    Returns the list of open ports. Used sparsely -- only against hosts
    the arp-scan already found alive.
    """
    open_ports: list[int] = []

    async def one(port: int) -> None:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            try:
                fut = asyncio.open_connection(ip, port)
                reader, writer = await asyncio.wait_for(
                    fut, timeout=timeout)
                open_ports.append(port)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
            except (OSError, asyncio.TimeoutError,
                    ConnectionRefusedError):
                pass
        finally:
            if semaphore is not None:
                semaphore.release()

    await asyncio.gather(*(one(p) for p in ports))
    return sorted(open_ports)


async def http_banner(ip: str, port: int = 80, path: str = "/",
                      timeout: float = 3.0) -> dict | None:
    """Fetch a single HTTP endpoint. Returns {body, headers} or None.

    Rolled with the stdlib rather than pulling aiohttp so the module
    works out of the box; aiohttp will come in when we start driving
    plugins that need it (Kasa/Shelly login, etc).
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        req = ("GET %s HTTP/1.0\r\n"
               "Host: %s\r\n"
               "User-Agent: NetHunterPro/1.0\r\n"
               "Accept: */*\r\n"
               "Connection: close\r\n\r\n") % (path, ip)
        writer.write(req.encode())
        await writer.drain()
        try:
            data = await asyncio.wait_for(reader.read(65536),
                                          timeout=timeout)
        except asyncio.TimeoutError:
            return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
    # Parse HTTP response
    text = data.decode("utf-8", errors="replace")
    if "\r\n\r\n" in text:
        head, body = text.split("\r\n\r\n", 1)
    else:
        head, body = text, ""
    headers: dict[str, str] = {}
    for line in head.splitlines()[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    return {"body": body[:32000], "headers": headers}


# --------------------------------------------------------- orchestrator
# Ports we probe by default. Curated for IoT devices; not exhaustive.
DEFAULT_TCP_PORTS = [
    21, 22, 23, 53, 80, 81, 88, 443, 445, 554, 631, 1400, 1880, 1883,
    1900, 5000, 5001, 5555, 6053, 6100, 6667, 6668, 7443, 7676, 8000,
    8001, 8008, 8009, 8060, 8080, 8081, 8123, 8181, 8443, 8484, 8580,
    8883, 9000, 9100, 9999, 16021, 20002, 32400, 49152, 49153, 55443,
    55000,
]

# The SSDP search targets that give us broad IoT coverage. Kept short
# so a broadcast round-trip fits in ~2s.
SSDP_TARGETS = [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:dial-multiscreen-org:service:dial:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:Basic:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:Belkin:device:controllee:1",
    "urn:Belkin:device:insight:1",
    "urn:schemas-kinoma-com:device:shell:1",
    "roku:ecp",
]


class Discovery:
    """Async coordinator running all probes for one scan.

    Callers create one instance per scan run, hand it a registry, then
    ``await discovery.run(iface)``. Signals fire on the registry as
    devices are found -- the UI subscribes to those, not to Discovery
    directly.
    """
    def __init__(self, registry: DeviceRegistry,
                 fingerprint: FingerprintEngine,
                 iface: str = "wlan0",
                 tcp_ports: list[int] | None = None) -> None:
        self.registry = registry
        self.fingerprint = fingerprint
        self.iface = iface
        self.tcp_ports = tcp_ports or DEFAULT_TCP_PORTS
        self._task: asyncio.Task | None = None
        self._stopping = False
        # per-host: {ip: {mac, vendor, ports:[], observations, ...}}
        self._hosts: dict[str, dict] = {}
        self._obs: dict[str, Observations] = {}

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def run(self, on_progress: Callable[[str], None] | None = None
                  ) -> None:
        """Execute the full discovery pipeline once."""
        prog = on_progress or (lambda _msg: None)

        # ---- L2 sweep (arp-scan): populate hosts dict ------------
        prog("arp-scan on " + self.iface)
        await probe_arp(self.iface, self._on_arp_host)

        # ---- mDNS + SSDP + broadcast probes in parallel ---------
        prog("mDNS + SSDP + UDP broadcasts")
        tcp_sem = asyncio.Semaphore(20)
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(probe_mdns(6, self._on_mdns))
                tg.create_task(probe_ssdp(SSDP_TARGETS, 4,
                                          self._on_ssdp))
                tg.create_task(probe_kasa(3, self._on_kasa))
                tg.create_task(probe_wiz(3, self._on_wiz))
                # TCP port sweep on every known host, semaphored
                for ip in list(self._hosts.keys()):
                    tg.create_task(self._sweep_ports(ip, tcp_sem))
        except* asyncio.CancelledError:
            # Propagate cancellation cleanly
            raise

        # ---- HTTP banner grab on any open web port ---------------
        prog("HTTP banners")
        await self._http_banners_pass()

        # ---- Score every host against every fingerprint plugin ---
        prog("fingerprint scoring")
        for ip, host in self._hosts.items():
            obs = self._obs.get(ip)
            if obs is None:
                obs = Observations()
            dev = Device(
                mac=host.get("mac", ""),
                ip=ip,
                vendor=host.get("vendor", ""),
            )
            for p in host.get("ports", []):
                dev.add_port(p)
            matches = self.fingerprint.score(dev, obs)
            for fp in matches:
                if fp.matcher_name == "_aggregate":
                    from .models import DeviceType
                    self.registry.attach_fingerprint(
                        dev.mac, fp,
                        device_type=DeviceType(
                            fp.extracted.get("device_type", "unknown")),
                        vendor=fp.extracted.get("vendor", ""),
                    )
                else:
                    self.registry.attach_fingerprint(dev.mac, fp)
            # Even without a match, register the device so the UI can
            # show "unknown" hosts under a section.
            if not matches:
                self.registry.upsert(dev)
            else:
                self.registry.upsert(dev)
                self.registry.attach_ports(dev.mac, dev.ports)

        prog("done")

    # ------------------------------------------------ callbacks
    def _on_arp_host(self, ip: str, mac: str, vendor: str) -> None:
        self._hosts.setdefault(ip, {
            "mac": mac, "vendor": vendor, "ports": []})
        self._hosts[ip]["mac"] = mac
        if vendor and not self._hosts[ip].get("vendor"):
            self._hosts[ip]["vendor"] = vendor
        self._obs.setdefault(ip, Observations())

    def _on_mdns(self, svc_type: str, hit: dict) -> None:
        ip = hit.get("ip", "")
        if not ip:
            return
        obs = self._obs.setdefault(ip, Observations())
        obs.mdns.setdefault(svc_type, []).append(hit)
        # Even if arp missed this IP, register it now.
        self._hosts.setdefault(ip, {"mac": "", "vendor": "",
                                    "ports": []})

    def _on_ssdp(self, ip: str, headers: dict) -> None:
        obs = self._obs.setdefault(ip, Observations())
        obs.ssdp.append(headers)
        self._hosts.setdefault(ip, {"mac": "", "vendor": "",
                                    "ports": []})

    def _on_kasa(self, ip: str, data: bytes) -> None:
        obs = self._obs.setdefault(ip, Observations())
        obs.udp_reply[(ip, 9999)] = data
        # Decode for later scoring convenience
        try:
            obs.udp_reply[(ip, 9999)] = _kasa_decrypt(data).encode()
        except Exception:  # noqa: BLE001
            pass
        self._hosts.setdefault(ip, {"mac": "", "vendor": "",
                                    "ports": []})

    def _on_wiz(self, ip: str, data: bytes) -> None:
        obs = self._obs.setdefault(ip, Observations())
        obs.udp_reply[(ip, 38899)] = data
        self._hosts.setdefault(ip, {"mac": "", "vendor": "",
                                    "ports": []})

    # ------------------------------------------------ sweep helpers
    async def _sweep_ports(self, ip: str,
                           sem: asyncio.Semaphore) -> None:
        open_ports = await probe_tcp_ports(
            ip, self.tcp_ports, timeout=1.5, semaphore=sem)
        host = self._hosts.setdefault(
            ip, {"mac": "", "vendor": "", "ports": []})
        host["ports"] = [Port(number=p, protocol="tcp",
                              service=_common_service(p))
                         for p in open_ports]

    async def _http_banners_pass(self) -> None:
        """Grab HTTP body for every host with 80/443/8080/8081 open."""
        interesting = (80, 443, 8080, 8008, 8081, 8123, 8181, 5000,
                       6052, 6053, 8443, 8090, 9000, 16021)
        tasks = []
        for ip, host in self._hosts.items():
            for p in host.get("ports", []):
                if p.number in interesting:
                    tasks.append(self._one_http(ip, p.number))
        # Bound concurrency
        sem = asyncio.Semaphore(10)

        async def guarded(coro):
            async with sem:
                await coro
        await asyncio.gather(*(guarded(t) for t in tasks),
                             return_exceptions=True)

    async def _one_http(self, ip: str, port: int) -> None:
        resp = await http_banner(ip, port, "/", timeout=3)
        if resp is None:
            return
        obs = self._obs.setdefault(ip, Observations())
        scheme = "https" if port in (443, 8443) else "http"
        obs.http["%s://%s:%d/" % (scheme, ip, port)] = resp


# -------------------------------------------------- port name lookup
_COMMON_SERVICES = {
    21: "ftp", 22: "ssh", 23: "telnet", 53: "dns", 80: "http",
    81: "http-alt", 88: "kerberos", 443: "https", 445: "smb",
    554: "rtsp", 631: "ipp", 1400: "sonos", 1880: "node-red",
    1883: "mqtt", 1900: "ssdp", 5000: "synology-dsm", 5001: "dsm-tls",
    5555: "adb", 6053: "esphome", 6100: "shelly-coiot", 6667: "tuya",
    6668: "tuya-ctl", 7443: "hue-hap", 7676: "samsung-upnp",
    8000: "dahua/http-alt", 8001: "samsung-tv-ws",
    8008: "chromecast", 8009: "chromecast-tls", 8060: "roku-ecp",
    8080: "http-alt", 8081: "sonoff-diy", 8090: "bose-soundtouch",
    8123: "home-assistant", 8181: "openhab", 8443: "https-alt",
    8484: "shelly-ws", 8580: "shelly", 8883: "mqtt-tls",
    9000: "vizio", 9100: "printer-raw",
    9999: "kasa", 16021: "nanoleaf", 20002: "tapo",
    32400: "plex", 49152: "wemo", 49153: "wemo",
    55443: "yeelight", 55000: "samsung-legacy-tv",
}


def _common_service(port: int) -> str:
    return _COMMON_SERVICES.get(port, "")
