"""KRACK Attack test suite (Vanhoef 2017, CVE-2017-13077..13088).

Upstream: https://github.com/vanhoefm/krackattacks-scripts
Website:  https://www.krackattacks.com

The KRACK family exploits nonce/replay-counter reuse forced through
retransmission of 4-way / group-key / fast-BSS-transition handshake
messages. The attacker sits in a multi-channel MITM position (rogue
AP clone on a different channel), retransmits handshake messages,
and observes the resulting keystream reuse -- decrypting live
traffic and, on Linux/Android wpa_supplicant <= 2.6, injecting an
all-zero PTK.

Vanhoef ships a research fork of hostapd + a set of Python scripts
that test *whether a target device* is vulnerable to each variant:

  * ``krack-test-client.py`` -- stand up as a rogue AP, wait for
    the target client to associate to SSID ``testnetwork`` with
    PSK ``abcdefgh``, then run each of the 7 test-cases:
      1. --replay-broadcast       group-frame replay tolerance
      2. --group --gtkinit        GTK install RSC leak
      3. --group                  CVE-2017-13080 GTK reinstall
      4. (no flag)                CVE-2017-13077 / -13078 PTK
                                  reinstall (highest impact)
      5. --tptk                   CVE-2017-13077 with forged M1
      6. --tptk-rand              same, random ANonce
      7. --gtkinit                GTK install RSC via M3/4 replay
  * ``krack-ft-test.py`` -- 802.11r Fast BSS Transition tester;
    detects IV reuse on the AP side when a client roams.

This module is a wrapper around those scripts. It handles the
one-time setup (git clone + build + venv + hardware crypto disable)
and lets the operator pick a test case + monitor its output.

Hardware note: KRACK requires two Wi-Fi radios on the phone -- one
runs the modified hostapd (needs AP mode with injection), the other
does the multi-channel MITM. Vanhoef's tools were verified on
Atheros AR9271 (ath9k_htc) and Intel AC-7260. On the rhodep phone
the internal ath10k_snoc will not work; plug in an ath9k_htc
dongle for the AP side.
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

_KRACK_REPO = "https://github.com/vanhoefm/krackattacks-scripts.git"
_KRACK_DIR = Path(os.path.expanduser(
    "~/.local/share/nethunter-pro/krackattacks-scripts"))
_KRACK_MAIN = _KRACK_DIR / "krackattack" / "krack-test-client.py"
_KRACK_FT = _KRACK_DIR / "krackattack" / "krack-ft-test.py"

# Client-side test cases (rogue AP mode). Every test has:
#  (short user-visible label, argv suffix passed to krack-test-client.py,
#   CVE reference)
_CLIENT_TESTS = [
    ("Ordinary 4-way handshake replay (CVE-2017-13077 + 13078)",
     [],
     "PTK reinstall by replaying encrypted M3. Highest impact; "
     "flag test to run first."),
    ("With forged M1 (--tptk)",
     ["--tptk"],
     "For clients using Temporal PTK construction, same ANonce. "
     "wpa_supplicant 2.6 needs this."),
    ("With forged M1, random ANonce (--tptk-rand)",
     ["--tptk-rand"],
     "Same as --tptk but with a random ANonce in the forged M1."),
    ("Group key reinstall (--group, CVE-2017-13080)",
     ["--group"],
     "Tests reinstall of the GTK during the group-key handshake."),
    ("Broadcast replay tolerance (--replay-broadcast)",
     ["--replay-broadcast"],
     "Baseline test. If the client accepts replayed broadcast "
     "frames, patch that first before trusting the other tests."),
    ("GTK RSC leak in group handshake (--group --gtkinit)",
     ["--group", "--gtkinit"],
     "Verifies the RSC the client installs from a group handshake."),
    ("GTK RSC leak in 4-way (--gtkinit)",
     ["--gtkinit"],
     "Retransmits M3/4 with a high replay counter to detect if the "
     "client installs the GTK with a leaked RSC."),
]


@register
class Krack(NHModule):
    title = "KRACK Attack"
    icon = "channel-secure-symbolic"
    description = ("Vanhoef's KRACK test suite (CVE-2017-13077..13088). "
                   "Rogue AP + retransmit handshake -> observe nonce "
                   "reuse. Client tests + 802.11r FT AP test.")
    # We install the tool on demand; nothing has to be on PATH.
    required_tools = []

    def __init__(self, app_window):
        super().__init__(app_window)
        self._proc: Process | None = None
        self._loot_id: int | None = None

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ---- what KRACK is ----
        intro = Adw.PreferencesGroup(
            title="What KRACK does",
            description="This is a *client* + *AP* vulnerability "
                        "checker. It does NOT recover PSKs; instead "
                        "it verifies whether the peer accepts "
                        "retransmitted handshake messages that reset "
                        "the nonce counter, which is the KRACK bug. "
                        "Vulnerable devices leak keystream (info "
                        "disclosure) and, on Linux/Android wpa_"
                        "supplicant <= 2.6, install an all-zero PTK.")
        intro.add(Adw.ActionRow(
            title="Reference",
            subtitle="https://www.krackattacks.com"))
        box.append(intro)

        # ---- hardware warning ----
        hw = Adw.PreferencesGroup(
            title="Hardware requirements",
            description="Two radios: one runs a modified hostapd as "
                        "rogue AP; the other does the multi-channel "
                        "MITM. ath9k_htc (AR9271) is the tested "
                        "chipset. Hardware crypto must be disabled.")
        driver = self._detect_driver()
        hw.add(Adw.ActionRow(
            title="Primary Wi-Fi driver",
            subtitle=driver + (
                " -- OK" if driver.startswith("ath9k") else
                " -- NOT ath9k_htc; plug in a AR9271 dongle for "
                "the AP side")))
        box.append(hw)

        # ---- install ----
        inst = Adw.PreferencesGroup(
            title="Installation",
            description="Clones the upstream repo and builds the "
                        "modified hostapd + Python venv. First run "
                        "takes a few minutes.")
        self.inst_row = Adw.ActionRow(
            title="krackattacks-scripts",
            subtitle=str(_KRACK_DIR) +
                     (" -- installed" if _KRACK_MAIN.exists()
                      else " -- not installed"))
        inst_btn = Gtk.Button(label="Install",
                              valign=Gtk.Align.CENTER)
        inst_btn.add_css_class("suggested-action")
        inst_btn.connect("clicked", lambda _b: self._install())
        self.inst_row.add_suffix(inst_btn)
        inst.add(self.inst_row)

        hwc_row = Adw.ActionRow(
            title="Disable hardware crypto",
            subtitle="Writes /etc/modprobe.d/nohwcrypt.conf. "
                     "Reboot recommended.")
        hwc_btn = Gtk.Button(label="Disable HW crypto",
                             valign=Gtk.Align.CENTER)
        hwc_btn.connect("clicked",
                        lambda _b: self._disable_hwcrypto())
        hwc_row.add_suffix(hwc_btn)
        inst.add(hwc_row)
        box.append(inst)

        # ---- client test config ----
        test = Adw.PreferencesGroup(
            title="Client tests (rogue AP mode)",
            description="Runs a modified hostapd on the chosen "
                        "interface. Configure the *target device* "
                        "to join SSID 'testnetwork' with PSK "
                        "'abcdefgh' and let it DHCP.")
        self.iface = Adw.EntryRow(title="Rogue-AP interface")
        self.iface.set_text("wlan1")
        test.add(self.iface)

        self.testcase = Adw.ComboRow(title="Test case")
        self.testcase.set_model(Gtk.StringList.new(
            [t[0] for t in _CLIENT_TESTS]))
        test.add(self.testcase)

        self.hint_row = Adw.ActionRow(
            title="Details", subtitle=_CLIENT_TESTS[0][2])
        test.add(self.hint_row)
        self.testcase.connect(
            "notify::selected",
            lambda *_: self.hint_row.set_subtitle(
                _CLIENT_TESTS[self.testcase.get_selected()][2]))

        run_row = Adw.ActionRow(
            title="Run selected test",
            subtitle="Streams output live; look for 'IV reuse "
                     "detected' or 'reinstalls the pairwise key'")
        self.run_btn = Gtk.Button(label="Run",
                                  valign=Gtk.Align.CENTER)
        self.run_btn.add_css_class("destructive-action")
        self.run_btn.connect("clicked",
                             lambda _b: self._run_client_test())
        run_row.add_suffix(self.run_btn)
        self.stop_btn = Gtk.Button(label="Stop",
                                   valign=Gtk.Align.CENTER)
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", lambda _b: self._stop())
        run_row.add_suffix(self.stop_btn)
        test.add(run_row)
        box.append(test)

        # ---- 802.11r FT test ----
        ft = Adw.PreferencesGroup(
            title="AP test: 802.11r FT handshake (CVE-2017-13082)",
            description="Wraps wpa_supplicant while roaming between "
                        "APs of the same FT domain. Watches for IV "
                        "reuse after re-association.")
        self.ft_iface = Adw.EntryRow(title="Managed interface")
        self.ft_iface.set_text("wlan1")
        ft.add(self.ft_iface)
        self.ft_ssid = Adw.EntryRow(title="Target SSID (FT-PSK)")
        ft.add(self.ft_ssid)
        self.ft_psk = Adw.PasswordEntryRow(title="Target PSK")
        ft.add(self.ft_psk)

        ft_run = Adw.ActionRow(
            title="Run FT test",
            subtitle="Start it, then roam via wpa_cli in a terminal")
        self.ft_btn = Gtk.Button(label="Run FT",
                                 valign=Gtk.Align.CENTER)
        self.ft_btn.connect("clicked",
                            lambda _b: self._run_ft_test())
        ft_run.add_suffix(self.ft_btn)
        ft.add(ft_run)
        box.append(ft)

        # ---- output ----
        self.output = OutputView()
        box.append(self.output)
        return box

    # -------------------------------------------------------- helpers
    def _detect_driver(self) -> str:
        try:
            for phy in Path("/sys/class/ieee80211").iterdir():
                drv = phy / "device" / "driver"
                if drv.exists():
                    return os.readlink(drv).rsplit("/", 1)[-1]
        except OSError:
            pass
        return "?"

    # -------------------------------------------------------- install
    def _install(self) -> None:
        self.output.append("# installing krackattacks-scripts…\n")
        script = (
            "set -e; "
            "mkdir -p %s; "
            "if [ ! -d %s/.git ]; then "
            "  git clone --depth 1 -b research %s %s; "
            "else "
            "  git -C %s pull --ff-only; "
            "fi; "
            "cd %s/krackattack; "
            "./build.sh || echo 'build.sh FAILED'; "
            "./pysetup.sh || echo 'pysetup.sh FAILED'; "
            "echo ---installed---"
        ) % (
            _KRACK_DIR.parent, _KRACK_DIR, _KRACK_REPO, _KRACK_DIR,
            _KRACK_DIR, _KRACK_DIR,
        )

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)
            if _KRACK_MAIN.exists():
                self.inst_row.set_subtitle(
                    str(_KRACK_DIR) + " -- installed")
                toast(self.app_window, "krackattacks installed")
            else:
                self.inst_row.set_subtitle(
                    "install failed -- see log below")

        # Build + venv setup work fine as user; the tests themselves
        # need root when they actually run hostapd.
        run_async(["sh", "-c", script], done,
                  root=False, timeout=600)

    def _disable_hwcrypto(self) -> None:
        # Vanhoef's script writes a modprobe blacklist that disables
        # hardware crypto for ath9k/ath9k_htc/ath10k/etc. We drop
        # the same file via the helper.
        conf = (
            "options ath9k nohwcrypt=1\n"
            "options ath9k_htc nohwcrypt=1\n"
            "options ath5k nohwcrypt=1\n"
            "options ath10k_pci nohwcrypt=1\n"
            "options ath10k_snoc nohwcrypt=1\n"
            "options mt76 nohwcrypt=1\n"
            "options mt7601u nohwcrypt=1\n"
            "options mt76x2u nohwcrypt=1\n"
            "options mt7921u nohwcrypt=1\n"
            "options rt2800pci nohwcrypt=1\n"
            "options rt2800usb nohwcrypt=1\n"
            "options rt73usb nohwcrypt=1\n"
            "options rtl8xxxu nohwcrypt=1\n"
            "options carl9170 nohwcrypt=1\n"
        )
        b64 = __import__("base64").b64encode(
            conf.encode()).decode()
        script = (
            "printf '%s' | base64 -d > /etc/modprobe.d/nohwcrypt.conf "
            "&& echo written /etc/modprobe.d/nohwcrypt.conf"
        ) % b64

        def done(r: Result) -> None:
            self.output.append(r.stdout or "")
            if r.stderr:
                self.output.append(r.stderr)
            toast(self.app_window,
                  "HW crypto disabled -- reboot recommended")

        run_async(["sh", "-c", script], done,
                  root=True, timeout=10)

    # ------------------------------------------------- client test
    def _run_client_test(self) -> None:
        if self._proc is not None and self._proc.running:
            return
        if not _KRACK_MAIN.exists():
            toast(self.app_window,
                  "Install krackattacks first")
            return
        iface = self.iface.get_text().strip() or "wlan1"
        idx = self.testcase.get_selected()
        label, extra, _hint = _CLIENT_TESTS[idx]

        # The upstream tool reads its interface from
        # ``hostapd/hostapd.conf`` (the "interface=" line).
        # We rewrite it before every run so the operator does not
        # have to touch the file.
        conf_path = _KRACK_DIR / "hostapd" / "hostapd.conf"
        rewrite = (
            "sed -i.bak "
            "  -e 's|^interface=.*|interface=%s|' %s"
        ) % (iface, conf_path)
        run_async(
            ["sh", "-c", rewrite], lambda _r: None,
            root=False, timeout=5)

        # Run under the tool's venv. Root is needed because it opens
        # raw sockets. The venv also lives in the repo tree.
        argv_str = (
            "cd %s/krackattack && "
            "sudo bash -c '"
            "  source venv/bin/activate && "
            "  ./krack-test-client.py %s"
            "'"
        ) % (_KRACK_DIR, " ".join(extra))
        self.output.append("$ " + argv_str + "\n")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = loot_path(
            "krack",
            "krack-client-%s-%s.log"
            % (re.sub(r"[^A-Za-z0-9]+", "_", label)[:40], stamp))
        self._loot_id = get_loot_store().record(
            module="krack", type="krack_log",
            target=label,
            path=log_path,
            notes="running")

        verdict_seen = {"vuln": False}

        def on_line(text: str) -> None:
            self.output.append(text)
            try:
                with open(log_path, "a") as fp:
                    fp.write(text)
            except OSError:
                pass
            # Latch the moment the tool declares a verdict.
            up = (text or "").lower()
            for marker in ("reinstalls the pairwise key",
                           "reinstalls the group key",
                           "iv reuse detected",
                           "installing the all-zero key",
                           "device is vulnerable"):
                if marker in up:
                    verdict_seen["vuln"] = True
                    if self._loot_id is not None:
                        get_loot_store().append_notes(
                            self._loot_id,
                            "VULNERABLE: " + marker)

        def on_done(code: int) -> None:
            self.output.append("[krack test exited: %d]\n" % code)
            self._proc = None
            self.run_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)
            if self._loot_id is not None:
                get_loot_store().refresh_size(self._loot_id)
                if not verdict_seen["vuln"]:
                    get_loot_store().append_notes(
                        self._loot_id,
                        "no vulnerability marker before exit")

        # Everything wrapped in a shell so we can 'source venv/'.
        # Not root=True at the Process level because the sudo bash -c
        # inside is what escalates -- Process root=True re-nests through
        # the helper which does not interact well with the venv shell.
        self._proc = Process(
            ["sh", "-c", argv_str], on_line, on_done, root=False)
        self._proc.start()
        self.run_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)

    def _stop(self) -> None:
        if self._proc is not None:
            self._proc.stop()
        self.stop_btn.set_sensitive(False)

    # ------------------------------------------------- FT AP test
    def _run_ft_test(self) -> None:
        if self._proc is not None and self._proc.running:
            return
        if not _KRACK_FT.exists():
            toast(self.app_window, "Install krackattacks first")
            return
        iface = self.ft_iface.get_text().strip() or "wlan1"
        ssid = self.ft_ssid.get_text().strip()
        psk = self.ft_psk.get_text().strip()
        if not ssid or not psk:
            toast(self.app_window,
                  "Set target SSID and PSK first")
            return

        # Write a wpa_supplicant config for the FT connect.
        wpa_conf = (
            "ctrl_interface=/var/run/wpa_supplicant\n"
            "network={\n"
            "  ssid=\"" + ssid + "\"\n"
            "  key_mgmt=FT-PSK\n"
            "  psk=\"" + psk + "\"\n"
            "}\n"
        )
        conf_path = "/tmp/nhp-krack-ft.conf"
        b64 = __import__("base64").b64encode(
            wpa_conf.encode()).decode()
        write = ("printf '%s' | base64 -d > %s" % (b64, conf_path))
        run_async(
            ["sh", "-c", write], lambda _r: None,
            root=False, timeout=5)

        argv_str = (
            "cd %s/krackattack && "
            "sudo bash -c '"
            "  source venv/bin/activate && "
            "  ./krack-ft-test.py wpa_supplicant -D nl80211 "
            "    -i %s -c %s"
            "'"
        ) % (_KRACK_DIR, iface, conf_path)
        self.output.append("$ " + argv_str + "\n")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = loot_path(
            "krack", "krack-ft-%s-%s.log" % (
                re.sub(r"[^A-Za-z0-9]+", "_", ssid), stamp))
        self._loot_id = get_loot_store().record(
            module="krack", type="krack_ft_log",
            target=ssid, path=log_path,
            notes="FT roam test")

        def on_line(text: str) -> None:
            self.output.append(text)
            try:
                with open(log_path, "a") as fp:
                    fp.write(text)
            except OSError:
                pass

        def on_done(code: int) -> None:
            self.output.append("[krack-ft exited: %d]\n" % code)
            self._proc = None
            self.ft_btn.set_sensitive(True)
            if self._loot_id is not None:
                get_loot_store().refresh_size(self._loot_id)

        self._proc = Process(
            ["sh", "-c", argv_str], on_line, on_done, root=False)
        self._proc.start()
        self.ft_btn.set_sensitive(False)
