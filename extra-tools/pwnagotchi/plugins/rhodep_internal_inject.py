"""
rhodep_internal_inject — pwnagotchi plugin

Route deauth and (optionally) associate requests through the WCN3990 internal
radio's STA offchannel/ROC path instead of bettercap's raw radiotap injection.

Why this plugin exists
----------------------
On this device (Motorola rhodep, ath10k_snoc / WCN3990), bettercap's normal
injection path — pcap.WritePacketData on a monitor pcap handle, i.e. raw
radiotap TX on a monitor vdev — **crashes the WCN3990 firmware on the first
frame**. So when pwnagotchi is running against the INTERNAL radio (mon0), we
must NEVER let `wifi.deauth`/`wifi.assoc` reach bettercap.

Instead: mgmt-frame injection on WCN3990 works over the STA vdev's OFFCHANNEL
path (NL80211_CMD_FRAME + IEEE80211_TX_CTL_TX_OFFCHAN), which
`nl80211-mgmt-tx.py` / `rhodep-inject-lab` already implement. Verified OTA
with an external witness (item 13 in the top-level README).

This plugin intercepts `agent.deauth()` and `agent.associate()` before they
call `agent.run('wifi.deauth …')` on bettercap, and shells out to
`/usr/local/sbin/rhodep-inject-lab` instead. Other plugins that subscribe to
`plugins.on('deauthentication', ...)` / `plugins.on('association', ...)` are
still notified, so nothing downstream breaks.

Enable only when the internal radio is selected. When RHODEP_PWN_RADIO is
"external" (the shipped TP-Link path), this plugin should be disabled — that
path has real raw injection in bettercap and doesn't need us.

Config (add to /etc/pwnagotchi/config.toml):

    [main.plugins.rhodep_internal_inject]
    enabled = true
    inject_lab = "/usr/local/sbin/rhodep-inject-lab"

If enabled, MUST also set:

    [personality]
    deauth = true
    associate = true

(otherwise agent.deauth/.associate are never called at all and this plugin
does nothing.)
"""

import logging
import os
import subprocess
import threading

import pwnagotchi.plugins as plugins


class RhodepInternalInject(plugins.Plugin):
    __author__ = "rhodep port"
    __version__ = "0.1"
    __license__ = "same as pwnagotchi"
    __description__ = (
        "Route deauth/associate through the WCN3990 STA-offchannel injector "
        "(rhodep-inject-lab) instead of bettercap raw radiotap injection."
    )

    def __init__(self):
        self._agent = None
        self._inject_lab = "/usr/local/sbin/rhodep-inject-lab"
        self._orig_deauth = None
        self._orig_associate = None
        self._lock = threading.Lock()

    def on_loaded(self):
        self._inject_lab = self.options.get("inject_lab", self._inject_lab)
        if not os.path.isfile(self._inject_lab) or not os.access(self._inject_lab, os.X_OK):
            logging.error(
                "[rhodep_internal_inject] %s not found or not executable — "
                "plugin loaded but will be a no-op",
                self._inject_lab,
            )
        else:
            logging.info(
                "[rhodep_internal_inject] loaded; inject_lab=%s", self._inject_lab
            )

    def on_ready(self, agent):
        """Monkey-patch agent.deauth and agent.associate."""
        self._agent = agent

        # Guard: if the shipped rhodep-inject-lab isn't there, do NOT patch —
        # let pwnagotchi run its normal path (which on WCN3990 mon0 would
        # crash the firmware, but that's on the operator for enabling this
        # plugin without the injector installed).
        if not os.path.isfile(self._inject_lab):
            logging.warning(
                "[rhodep_internal_inject] inject_lab missing; NOT patching agent"
            )
            return

        self._orig_deauth = getattr(agent, "deauth", None)
        self._orig_associate = getattr(agent, "associate", None)

        agent.deauth = self._patched_deauth
        agent.associate = self._patched_associate
        logging.info(
            "[rhodep_internal_inject] agent.deauth / agent.associate now go "
            "through %s (internal-radio STA offchannel path)",
            self._inject_lab,
        )

    def on_unload(self, ui):
        if self._agent is None:
            return
        # Best-effort restore
        if self._orig_deauth is not None:
            self._agent.deauth = self._orig_deauth
        if self._orig_associate is not None:
            self._agent.associate = self._orig_associate

    # ---- interception ------------------------------------------------------

    def _mac_of(self, entry, key="mac"):
        try:
            return entry[key].lower()
        except Exception:
            return None

    def _channel_of(self, ap):
        # pwnagotchi's AP dict has 'channel' as int
        try:
            return int(ap.get("channel", 0))
        except Exception:
            return 0

    def _run_inject(self, mode, ap_mac, sta_mac, channel, count):
        """Spawn rhodep-inject-lab as a subprocess so the agent tick isn't blocked."""
        if not channel:
            logging.warning(
                "[rhodep_internal_inject] no channel for AP %s — skipping %s",
                ap_mac, mode,
            )
            return
        if mode == "deauth":
            # deauth: dest = client (or broadcast if unknown), bssid = AP
            dest = sta_mac or "ff:ff:ff:ff:ff:ff"
            cmd = [
                self._inject_lab, "deauth", str(channel),
                dest, ap_mac, str(count), "7",
            ]
        elif mode == "assoc":
            # rhodep-inject-lab doesn't have an 'assoc' subcommand; emulate
            # with a raw auth-then-assoc via 'probe' targeting the AP for now.
            # (Association forgery would need addr2 = a fake STA, which
            # cfg80211 refuses for non-auth/deauth. This is a stub.)
            cmd = [
                self._inject_lab, "probe", str(channel), ap_mac, ap_mac, "5",
            ]
        else:
            return

        try:
            # Fire-and-forget: pwnagotchi calls deauth() from its main loop and
            # we don't want to block it on subprocess.wait(). rhodep-inject-lab
            # runs ~4s per burst; run in background.
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logging.info(
                "[rhodep_internal_inject] %s: %s (ch %d) via %s",
                mode, ap_mac, channel, os.path.basename(self._inject_lab),
            )
        except Exception as e:
            logging.error("[rhodep_internal_inject] %s failed: %s", mode, e)

    def _patched_deauth(self, ap, sta, throttle=0):
        ap_mac = self._mac_of(ap)
        sta_mac = self._mac_of(sta) if isinstance(sta, dict) else None
        ch = self._channel_of(ap)
        if not ap_mac:
            return
        with self._lock:
            self._run_inject("deauth", ap_mac, sta_mac, ch, count=40)
        # keep other plugins in the loop
        try:
            plugins.on("deauthentication", self._agent, ap, sta)
        except Exception:
            pass

    def _patched_associate(self, ap, throttle=0):
        ap_mac = self._mac_of(ap)
        ch = self._channel_of(ap)
        if not ap_mac:
            return
        with self._lock:
            self._run_inject("assoc", ap_mac, None, ch, count=5)
        try:
            plugins.on("association", self._agent, ap)
        except Exception:
            pass
