"""Turn the WiFi Pineapple Pager port on and off, and open its panel/app.

This is the control screen for the *software* WiFi Pineapple **Pager** port that
runs on the phone itself (the companion to the NANO port in wifi_pineapple.py).
The whole stack is one systemd target, pager.target, and -- like the NANO -- it
is deliberately *not* enabled on boot: it draws power, so the user starts it only
while auditing and stops it when done.

  start  -> systemctl start pager.target
              brings up pager-net, dnsmasq, the pineapd-pager daemon and the
              Pager server/UI.
  stop   -> systemctl stop pager.target
              tears the stack down and *reverts the network*, leaving the phone
              as a normal device again.

The web panel is served at http://172.16.52.1:1471. Note the LAN (172.16.52.1)
is distinct from the NANO port (172.16.84.1) so both stacks can coexist without
colliding. Unlike the NANO panel, the Pager port's backend does not use the
bcrypt/SQLite auth(username, password_hash) scheme, so this screen has no panel
password section (see the note where the NANO keeps it).

In addition to the web panel, the Pager ships a native kiosk app ("WiFi Pineapple
Pager") that this screen can launch full-screen, falling back to the browser URL
if the kiosk launcher is not installed. All of start/stop are root operations,
routed through the app's helper/pkexec like every other module.
"""
from __future__ import annotations

import os

from gi.repository import Adw, GLib, Gtk

from ..executor import Result, run_async
from ..module import NHModule, register
from ..widgets import OutputView, toast

TARGET = "pager.target"
# The unit whose "active" state best represents "the Pager is up": the daemon.
# The target can report active while sub-units are still settling, so we key the
# switch/status on pineapd-pager.
CORE = "pineapd-pager.service"
# Key units shown in the status group, in start order.
UNITS = (
    "pager-net.service",
    "pager-dnsmasq.service",
    "pineapd-pager.service",
    "pager-server.service",
)
# LAN on 172.16.52.1/24 so the Pager bridge does not collide with the NANO port
# (172.16.84.1/24) or usb0 (NetHunter USB tether = 172.16.42.1/24, the SSH
# lifeline).
UI_URL = "http://172.16.52.1:1471"
# Native kiosk app: the installed .desktop launches it full-screen. If the
# desktop/launcher is missing we fall back to the raw script, then to the URL.
KIOSK_DESKTOP = "WiFi Pineapple Pager"
KIOSK_SCRIPT = "/opt/pineapple-pager/app/pager-kiosk.sh"


@register
class WiFiPineapplePager(NHModule):
    title = "WiFi Pineapple Pager (Port)"
    icon = "network-wireless-hotspot-symbolic"
    description = "Turn the WiFi Pineapple Pager on/off and open its panel"
    required_tools = ["systemctl"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        # ---- power -------------------------------------------------------
        g = Adw.PreferencesGroup(
            title="WiFi Pineapple Pager",
            description="The stack is started on demand so it does not drain "
            "the battery. Stopping it reverts the network and leaves the phone "
            "normal.",
        )
        self.power = Adw.SwitchRow(
            title="Enable WiFi Pineapple Pager",
            subtitle="Starts the Pager stack (panel, AP, recon). "
            "Stopping reverts the network.",
        )
        self.power.connect("notify::active", self._on_power)
        g.add(self.power)
        box.append(g)

        # ---- status ------------------------------------------------------
        s = Adw.PreferencesGroup(title="Status")
        self.state_row = Adw.ActionRow(
            title="Pager stack", subtitle="checking…")
        self.state_icon = Gtk.Image.new_from_icon_name(
            "content-loading-symbolic")
        self.state_row.add_prefix(self.state_icon)
        s.add(self.state_row)

        # A compact one-line summary of the key units (daemon, server, ...).
        self.units_row = Adw.ActionRow(title="Services", subtitle="—")
        s.add(self.units_row)

        ui_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        ui_btn.add_css_class("suggested-action")
        ui_btn.connect("clicked", lambda _b: self._open_ui())
        ui_row = Adw.ActionRow(
            title="Web panel", subtitle="%s (user: root)" % UI_URL)
        ui_row.add_suffix(ui_btn)
        s.add(ui_row)

        # Launch the native kiosk app full-screen (falls back to the browser).
        app_btn = Gtk.Button(label="Open Pager App", valign=Gtk.Align.CENTER)
        app_btn.add_css_class("suggested-action")
        app_btn.connect("clicked", lambda _b: self._open_app())
        app_row = Adw.ActionRow(
            title="Pager app",
            subtitle="Launch the native Pager kiosk full-screen")
        app_row.add_suffix(app_btn)
        s.add(app_row)
        box.append(s)

        # ---- panel password ---------------------------------------------
        # NOTE: the NANO port has a "Panel password" section here that writes a
        # bcrypt hash into /etc/pineapple/pineapple.db. The Pager port's backend
        # is different and does not use that auth(username, password_hash)
        # scheme, so there is intentionally no password management on this
        # screen. See wifi_pineapple.py if the Pager ever gains the same DB.

        # ---- output ------------------------------------------------------
        self.output = OutputView()
        box.append(self.output)

        self._refresh()
        # keep the switch/status in step with reality while the screen is open
        self._poll_id = GLib.timeout_add_seconds(5, self._refresh_tick)
        return box

    # ---------------------------------------------------------------- power
    def _on_power(self, row: Adw.SwitchRow, _param) -> None:
        if row.get_active():
            self.output.append("$ systemctl start %s\n" % TARGET)
            run_async(["systemctl", "start", TARGET], self._after_action,
                      root=True, timeout=90)
        else:
            self.output.append("$ systemctl stop %s\n" % TARGET)
            run_async(["systemctl", "stop", TARGET], self._after_action,
                      root=True, timeout=60)

    def _after_action(self, result: Result) -> None:
        if result.stderr:
            self.output.append(result.stderr)
        self._refresh()

    # -------------------------------------------------------------- status
    def _refresh_tick(self) -> bool:
        self._refresh()
        return True

    def _refresh(self) -> None:
        def done(result: Result) -> None:
            active = result.stdout.strip() == "active"
            # Update the switch without firing _on_power (which would re-issue
            # start/stop in a loop).
            self.power.handler_block_by_func(self._on_power)
            self.power.set_active(active)
            self.power.handler_unblock_by_func(self._on_power)
            if active:
                self.state_row.set_subtitle("running")
                self.state_icon.set_from_icon_name("emblem-ok-symbolic")
            else:
                self.state_row.set_subtitle("stopped")
                self.state_icon.set_from_icon_name(
                    "media-playback-stop-symbolic")
        run_async(["systemctl", "is-active", CORE], done,
                  root=False, timeout=10)

        # One is-active call per key unit, summarised on the Services row.
        def units_done(result: Result) -> None:
            states = (result.stdout or "").split()
            parts = []
            for unit, state in zip(UNITS, states):
                short = unit.replace("pager-", "").replace(
                    "pineapd-pager", "pineapd").replace(".service", "")
                mark = "●" if state == "active" else "○"
                parts.append("%s %s" % (mark, short))
            self.units_row.set_subtitle(
                "  ".join(parts) if parts else "unknown")
        run_async(["systemctl", "is-active", *UNITS], units_done,
                  root=False, timeout=10)

    # ------------------------------------------------------------------ ui
    def _open_ui(self) -> None:
        try:
            Gtk.UriLauncher.new(UI_URL).launch(self.app_window, None, None, None)
        except Exception:
            toast(self.app_window, "Open %s in a browser" % UI_URL)

    def _open_app(self) -> None:
        """Launch the native Pager kiosk full-screen.

        Prefer the installed .desktop (so it runs exactly as the launcher does).
        If that is missing, run the kiosk script directly. If neither is present,
        fall back to opening the web panel in the browser so the button always
        does *something* useful.
        """
        self.output.append("$ open Pager app (kiosk)\n")

        def done(result: Result) -> None:
            if not result.ok:
                # launcher/script not available -- fall back to the URL.
                self.output.append(result.stderr or "")
                self._open_ui()

        if os.path.exists(KIOSK_SCRIPT):
            run_async([KIOSK_SCRIPT], done, root=False, timeout=10)
        else:
            # gtk-launch resolves the .desktop by its filename/Name; if the
            # desktop entry is absent this returns non-zero and we fall back.
            run_async(["gtk-launch", KIOSK_DESKTOP], done,
                      root=False, timeout=10)
