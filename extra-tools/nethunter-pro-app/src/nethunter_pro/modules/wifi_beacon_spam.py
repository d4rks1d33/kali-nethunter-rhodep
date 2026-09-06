"""WiFi Beacon Spam — port of the Flipper Zero ESP32 implementation
(Sor3nt/Flipper-Zero-ESP32-Port, wlan_hal.c + scene_ssid_spam.c).

Four modes, faithful to the Flipper:
  • Funny SSIDs   — 22 hardcoded joke network names (exact Flipper list)
  • Rickroll      — 8 SSIDs spelling out "Never Gonna Give You Up"
  • Random        — SSID_<0-9998> each frame, fresh per transmission
  • Custom        — user-supplied base string → "<base><1..9999>" cycling

Backend: mdk4 -b (beacon flood, same effect as the Flipper ESP32 raw
beacon injection). Falls back to mdk3 -b if mdk4 is absent. Both need
the WiFi interface in monitor mode first — the module offers a one-tap
"Start Monitor" button that runs airmon-ng.

mdk4 reads SSIDs from a text file, one per line, and cycles through them
on channels 1-11 with random MACs — exactly what the Flipper does.
"""
from __future__ import annotations

import os
import random
import string
import subprocess
import tempfile
import threading

from gi.repository import Adw, GLib, Gtk

from ..executor import run_async, which
from ..module import NHModule, register
from ..widgets import OutputView, toast

# ── Flipper SSID lists (verbatim from wlan_hal.c) ────────────────────────

FUNNY_SSIDS: list[str] = [
    "Mom Use This One",
    "Abraham Linksys",
    "Benjamin FrankLAN",
    "Martin Router King",
    "John Wilkes Bluetooth",
    "Pretty Fly for a Wi-Fi",
    "Bill Wi the Science Fi",
    "I Believe Wi Can Fi",
    "Tell My Wi-Fi Love Her",
    "No More Mister Wi-Fi",
    "LAN Solo",
    "The LAN Before Time",
    "Silence of the LANs",
    "House LANister",
    "Winternet Is Coming",
    "FBI Surveillance Van 4",
    "Area 51 Test Site",
    "Never Gonna Give You Up",
    "Loading...",
    "VIRUS.EXE",
    "Free Public Wi-Fi",
    "404 Wi-Fi Unavailable",
]

RICKROLL_SSIDS: list[str] = [
    "01 Never gonna give you up",
    "02 Never gonna let you down",
    "03 Never gonna run around",
    "04 And desert you",
    "05 Never gonna make you cry",
    "06 Never gonna say goodbye",
    "07 Never gonna tell a lie",
    "08 And hurt you",
]

# ── helpers ───────────────────────────────────────────────────────────────

def _make_random_ssid() -> str:
    """SSID_<0-9998> matching Flipper random mode."""
    return "SSID_%d" % (random.randint(0, 9998),)


def _make_ssid_file(ssids: list[str]) -> str:
    """Write SSIDs to a temp file; return its path. Caller must unlink."""
    fd, path = tempfile.mkstemp(prefix="nhpro_beacon_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        for s in ssids:
            f.write(s[:32] + "\n")   # 802.11 SSID max 32 bytes
    return path


def _get_monitor_iface() -> str | None:
    """Return the first monitor-mode interface found, or None."""
    try:
        out = subprocess.check_output(["iw", "dev"], text=True,
                                      stderr=subprocess.DEVNULL)
        iface = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                iface = line.split()[1]
            elif line == "type monitor" and iface:
                return iface
    except Exception:
        pass
    return None


def _get_managed_iface() -> str | None:
    """Return first managed-mode wireless interface."""
    try:
        out = subprocess.check_output(["iw", "dev"], text=True,
                                      stderr=subprocess.DEVNULL)
        iface = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                iface = line.split()[1]
            elif line == "type managed" and iface:
                return iface
    except Exception:
        pass
    return None


# ── Module ────────────────────────────────────────────────────────────────

@register
class WifiBeaconSpam(NHModule):
    """WiFi Beacon Flood — Funny SSIDs / Rickroll / Random / Custom.

    Port of the Flipper Zero ESP32 beacon spam (Sor3nt/Flipper-Zero-ESP32-Port).
    Uses mdk4 -b (or mdk3 -b) which injects raw 802.11 beacon frames with
    random MACs on channels 1–11, one SSID per frame at ~100 fps — identical
    to what the Flipper does over its ESP32 radio.

    Requires the WiFi interface in monitor mode.
    """

    title = "WiFi Beacon Spam"
    icon = "network-wireless-symbolic"
    description = (
        "Flood the 2.4 GHz band with fake WiFi networks. "
        "Funny SSIDs, Rickroll, random or custom — port of the Flipper Zero ESP32 beacon spam."
    )
    required_tools = ["mdk4"]   # mdk3 is a fallback, checked at runtime

    def __init__(self, app_window):
        super().__init__(app_window)
        self._proc: subprocess.Popen | None = None
        self._ssid_file: str | None = None
        self._running = False
        self._lock = threading.Lock()

    # ── build ─────────────────────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ── monitor mode helper ──────────────────────────────────────────
        mon_grp = Adw.PreferencesGroup(
            title="Monitor Mode",
            description="mdk4 requires a monitor-mode interface. "
                        "Tap Start Monitor if wlan0 is in managed mode.")
        self.mon_row = Adw.ActionRow(
            title="Interface",
            subtitle="checking…")
        self.mon_btn = Gtk.Button(label="Start Monitor", valign=Gtk.Align.CENTER)
        self.mon_btn.add_css_class("suggested-action")
        self.mon_btn.connect("clicked", lambda _b: self._toggle_monitor())
        self.mon_row.add_suffix(self.mon_btn)
        mon_grp.add(self.mon_row)
        box.append(mon_grp)

        # ── mode ─────────────────────────────────────────────────────────
        mode_grp = Adw.PreferencesGroup(
            title="Mode",
            description="Choose which SSIDs to broadcast. "
                        "Faithful port of Flipper Zero ESP32 beacon spam modes.")
        self.mode_combo = Adw.ComboRow(
            title="SSID set",
            subtitle="Flipper modes: Funny / Rickroll / Random / Custom")
        self.mode_combo.set_model(Gtk.StringList.new([
            "Funny SSIDs (22 jokes)",
            "Rickroll (8 verses)",
            "Random (SSID_0-9998)",
            "Custom",
        ]))
        self.mode_combo.connect("notify::selected", lambda *_: self._on_mode_change())
        mode_grp.add(self.mode_combo)

        # Custom SSID base entry (only visible in Custom mode)
        self.custom_row = Adw.EntryRow(title="Base SSID (max 28 chars)")
        self.custom_row.set_text("MyNetwork")
        self.custom_row.set_visible(False)
        mode_grp.add(self.custom_row)

        # Custom count
        self.custom_count = Adw.SpinRow.new_with_range(1, 9999, 1)
        self.custom_count.set_title("Number of SSIDs to generate")
        self.custom_count.set_value(20)
        self.custom_count.set_visible(False)
        mode_grp.add(self.custom_count)

        box.append(mode_grp)

        # ── preview ───────────────────────────────────────────────────────
        prev_grp = Adw.PreferencesGroup(title="SSID Preview")
        self.preview_row = Adw.ActionRow(
            title="SSIDs to broadcast",
            subtitle="(will appear here)")
        prev_grp.add(self.preview_row)
        box.append(prev_grp)

        # ── start / stop ──────────────────────────────────────────────────
        ctrl_grp = Adw.PreferencesGroup(title="Control")
        ctrl_row = Adw.ActionRow(
            title="Beacon flood",
            subtitle="Injects raw 802.11 beacons with random MACs on ch 1-11")
        self.start_btn = Gtk.Button(label="Start", valign=Gtk.Align.CENTER)
        self.start_btn.add_css_class("destructive-action")
        self.start_btn.connect("clicked", lambda _b: self._start())
        self.stop_btn = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER)
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", lambda _b: self._stop())
        ctrl_row.add_suffix(self.start_btn)
        ctrl_row.add_suffix(self.stop_btn)
        ctrl_grp.add(ctrl_row)
        box.append(ctrl_grp)

        # ── output ────────────────────────────────────────────────────────
        self.output = OutputView()
        box.append(self.output)

        # Initial state
        self._refresh_monitor_status()
        self._update_preview()

        return box

    # ── monitor mode ──────────────────────────────────────────────────────

    def _refresh_monitor_status(self) -> None:
        mon = _get_monitor_iface()
        if mon:
            self.mon_row.set_subtitle(f"✓ {mon} is in monitor mode")
            self.mon_btn.set_label("Stop Monitor")
            self.mon_btn.get_style_context().remove_class("suggested-action")
            self.mon_btn.get_style_context().add_class("destructive-action")
        else:
            managed = _get_managed_iface() or "wlan0"
            self.mon_row.set_subtitle(f"{managed} is in managed mode")
            self.mon_btn.set_label("Start Monitor")
            self.mon_btn.get_style_context().remove_class("destructive-action")
            self.mon_btn.get_style_context().add_class("suggested-action")

    def _toggle_monitor(self) -> None:
        mon = _get_monitor_iface()
        if mon:
            # Stop monitor
            self.output.append(f"# stopping monitor on {mon}…\n")
            run_async(["airmon-ng", "stop", mon],
                      lambda r: self._on_monitor_done(r), root=True)
        else:
            managed = _get_managed_iface() or "wlan0"
            self.output.append(f"# starting monitor on {managed}…\n")
            run_async(["airmon-ng", "check", "kill"],
                      lambda _r: self._start_airmon(managed), root=True)

    def _start_airmon(self, iface: str) -> None:
        run_async(["airmon-ng", "start", iface],
                  lambda r: self._on_monitor_done(r), root=True)

    def _on_monitor_done(self, result) -> None:
        self.output.append((result.stdout or "") + (result.stderr or ""))
        GLib.idle_add(self._refresh_monitor_status)

    # ── mode / preview ────────────────────────────────────────────────────

    def _on_mode_change(self) -> None:
        idx = self.mode_combo.get_selected()
        custom = (idx == 3)
        self.custom_row.set_visible(custom)
        self.custom_count.set_visible(custom)
        self._update_preview()

    def _build_ssid_list(self) -> list[str]:
        idx = self.mode_combo.get_selected()
        if idx == 0:
            return list(FUNNY_SSIDS)
        elif idx == 1:
            return list(RICKROLL_SSIDS)
        elif idx == 2:
            # Random: generate 50 samples for the file; mdk4 loops forever
            return [_make_random_ssid() for _ in range(50)]
        else:
            base = self.custom_row.get_text().strip()[:28] or "MyNetwork"
            count = int(self.custom_count.get_value())
            return [f"{base}{i}" for i in range(1, count + 1)]

    def _update_preview(self) -> None:
        ssids = self._build_ssid_list()
        preview = ", ".join(ssids[:5])
        if len(ssids) > 5:
            preview += f"… (+{len(ssids)-5} more)"
        self.preview_row.set_subtitle(preview)

    # ── start / stop ──────────────────────────────────────────────────────

    def _start(self) -> None:
        with self._lock:
            if self._running:
                return

        mon = _get_monitor_iface()
        if not mon:
            toast(self.app_window,
                  "No monitor interface — tap Start Monitor first")
            return

        # Pick backend: prefer mdk4, fall back to mdk3
        backend = which("mdk4") or which("mdk3")
        if not backend:
            toast(self.app_window, "mdk4/mdk3 not found")
            return

        ssids = self._build_ssid_list()
        if not ssids:
            toast(self.app_window, "No SSIDs to broadcast")
            return

        # Write SSID file
        self._ssid_file = _make_ssid_file(ssids)
        mode_names = ["Funny SSIDs", "Rickroll", "Random", "Custom"]
        mode_name = mode_names[self.mode_combo.get_selected()]

        self.output.append(
            f"# beacon flood: {mode_name} × {len(ssids)} SSIDs on {mon}\n"
            f"# backend: {os.path.basename(backend)}\n"
            f"# SSIDs: {', '.join(ssids[:3])}{'…' if len(ssids)>3 else ''}\n")

        # mdk4 -b = beacon flood, -f = SSID file
        # -s = speed (pkts/sec, default unlimited), channels 1-11 random
        cmd = [backend, mon, "b", "-f", self._ssid_file, "-s", "100"]

        with self._lock:
            self._running = True
        self.start_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)

        def _run_proc():
            try:
                # Run as root (mdk4 needs raw socket)
                from ..executor import run_async as _ra
                proc = subprocess.Popen(
                    ["sudo", "-n"] + cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                with self._lock:
                    self._proc = proc
                for line in proc.stdout:
                    GLib.idle_add(self.output.append, line)
                proc.wait()
            except Exception as e:
                GLib.idle_add(self.output.append, f"[error] {e}\n")
            finally:
                self._cleanup()

        threading.Thread(target=_run_proc, daemon=True).start()

    def _stop(self) -> None:
        with self._lock:
            proc = self._proc
        if proc:
            try:
                subprocess.run(["sudo", "-n", "kill", str(proc.pid)],
                               capture_output=True)
                proc.terminate()
            except Exception:
                pass
        self._cleanup()

    def _cleanup(self) -> None:
        with self._lock:
            self._running = False
            self._proc = None
            f = self._ssid_file
            self._ssid_file = None
        if f and os.path.exists(f):
            try:
                os.unlink(f)
            except Exception:
                pass
        GLib.idle_add(self._on_stopped)

    def _on_stopped(self) -> None:
        self.start_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.output.append("# stopped\n")
        self._refresh_monitor_status()
