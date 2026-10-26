"""Turn the WiFi Pineapple port on and off, and manage its web panel.

This is the control screen for the *software* WiFi Pineapple port that runs on
the phone itself (not the Hak5 USB connector, which lives in pineapple.py). The
whole stack is one systemd target, pineapple.target, and it is deliberately
*not* enabled on boot: it draws power, so the user starts it only while auditing
and stops it when done.

  start  -> systemctl start pineapple.target
              brings up pineapple-net, dnsmasq, php-fpm, nginx, pineapd, the AP.
  stop   -> systemctl stop pineapple.target
              tears the stack down and *reverts the network* (br-lan, NAT),
              leaving the phone as a normal device again.

The web panel is served at http://172.16.84.1:1471 (user: root). Its password
lives in SQLite at /etc/pineapple/pineapple.db, table auth(username,
password_hash), hashed with PHP's password_hash (bcrypt). Because bcrypt is
one-way, a password that has already been changed cannot be read back: this
screen shows the *default* (pineapplesareyummy) as a reference, and lets the
user set a new one or reset to the default. All of start/stop and the password
write are root operations, routed through the app's helper/pkexec like every
other module.
"""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..executor import Result, run_async
from ..module import NHModule, register
from ..widgets import OutputView, toast

TARGET = "pineapple.target"
# The unit whose "active" state best represents "the Pineapple is up": the
# daemon. The target can report active while sub-units are still settling, so
# we key the switch/status on pineapd.
CORE = "pineapd.service"
# Key units shown in the status group, in start order.
UNITS = (
    "pineapple-net.service",
    "pineapple-dnsmasq.service",
    "pineapple-php-fpm.service",
    "pineapple-nginx.service",
    "pineapd.service",
    "pineapple-ap.service",
)
# LAN moved to 172.16.84.1/24 so the Pineapple bridge no longer collides with
# usb0 (NetHunter USB tether = 172.16.42.1/24, the SSH lifeline).
UI_URL = "http://172.16.84.1:1471"
DB = "/etc/pineapple/pineapple.db"
DEFAULT_PASSWORD = "pineapplesareyummy"


@register
class WiFiPineapple(NHModule):
    title = "WiFi Pineapple (Port)"
    icon = "network-wireless-hotspot-symbolic"
    description = "Turn the WiFi Pineapple on/off and manage the panel"
    required_tools = ["systemctl"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        # ---- power -------------------------------------------------------
        g = Adw.PreferencesGroup(
            title="WiFi Pineapple",
            description="The stack is started on demand so it does not drain "
            "the battery. Stopping it reverts the network and leaves the phone "
            "normal.",
        )
        self.power = Adw.SwitchRow(
            title="Enable WiFi Pineapple",
            subtitle="Starts the Pineapple stack (panel, AP, recon). "
            "Stopping reverts the network.",
        )
        self.power.connect("notify::active", self._on_power)
        g.add(self.power)
        box.append(g)

        # ---- status ------------------------------------------------------
        s = Adw.PreferencesGroup(title="Status")
        self.state_row = Adw.ActionRow(
            title="Pineapple stack", subtitle="checking…")
        self.state_icon = Gtk.Image.new_from_icon_name(
            "content-loading-symbolic")
        self.state_row.add_prefix(self.state_icon)
        s.add(self.state_row)

        # A compact one-line summary of the key units (AP, recon, ...).
        self.units_row = Adw.ActionRow(title="Services", subtitle="—")
        s.add(self.units_row)

        ui_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        ui_btn.add_css_class("suggested-action")
        ui_btn.connect("clicked", lambda _b: self._open_ui())
        ui_row = Adw.ActionRow(
            title="Web panel", subtitle="%s (user: root)" % UI_URL)
        ui_row.add_suffix(ui_btn)
        s.add(ui_row)
        box.append(s)

        # ---- panel password ---------------------------------------------
        p = Adw.PreferencesGroup(
            title="Panel password",
            description="The panel password is stored as a bcrypt hash, which "
            "is one-way: a password you have already changed cannot be shown "
            "again, only replaced.",
        )
        # Show the default as a reference so the user always knows the factory
        # login. If they have changed it, this is no longer the live password --
        # the subtitle says so.
        self.default_row = Adw.ActionRow(
            title="Default password",
            subtitle="%s — the factory default. If you changed it, it is not "
            "this; set a new one below." % DEFAULT_PASSWORD)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic",
                              valign=Gtk.Align.CENTER)
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy default password")
        copy_btn.connect("clicked", lambda _b: self._copy_default())
        self.default_row.add_suffix(copy_btn)
        p.add(self.default_row)

        self.pw_entry = Adw.PasswordEntryRow(title="New panel password")
        self.pw_entry.set_show_apply_button(True)
        self.pw_entry.connect("apply", self._on_password_apply)
        p.add(self.pw_entry)

        reset_row = Adw.ActionRow(
            title="Reset to default",
            subtitle="Set the panel password back to %s" % DEFAULT_PASSWORD)
        reset_btn = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        reset_btn.add_css_class("destructive-action")
        reset_btn.connect("clicked", lambda _b: self._reset_password())
        reset_row.add_suffix(reset_btn)
        p.add(reset_row)
        box.append(p)

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
                short = unit.replace("pineapple-", "").replace(".service", "")
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

    def _copy_default(self) -> None:
        try:
            self.app_window.get_clipboard().set(DEFAULT_PASSWORD)
            toast(self.app_window, "Default password copied")
        except Exception:
            toast(self.app_window, "Default: %s" % DEFAULT_PASSWORD)

    # ------------------------------------------------------------ password
    def _on_password_apply(self, row: Adw.PasswordEntryRow) -> None:
        new = row.get_text()
        if not new:
            return
        row.set_text("")
        self._set_password(new, "Panel password updated")

    def _reset_password(self) -> None:
        self._set_password(DEFAULT_PASSWORD, "Panel password reset to default")

    def _set_password(self, new: str, ok_msg: str) -> None:
        """Hash `new` with bcrypt and write it into the panel DB, as root.

        Done with a single `php -r` so it works whether or not the panel/nginx
        is running: password_hash() produces the exact bcrypt format PHP's
        password_verify() (what the panel logs in with) expects, and the row is
        upserted for username 'root'. The password is passed as argv[1], never
        interpolated into the shell or the SQL command string, so a password
        containing quotes, ';' or '$' cannot inject anything -- only the hash
        (which is a safe [A-Za-z0-9./$] string) is escaped into the SQL.
        """
        php = (
            '$h = password_hash($argv[1], PASSWORD_DEFAULT);'
            '$db = new SQLite3("%s");'
            '$db->exec("INSERT OR REPLACE INTO auth(username, password_hash) '
            'VALUES(\\"root\\", \\"" . SQLite3::escapeString($h) . "\\")");'
        ) % DB
        self.output.append("$ php -r '<bcrypt update>' (panel password)\n")

        def done(result: Result) -> None:
            if result.ok:
                toast(self.app_window, ok_msg)
            else:
                self.output.append(result.stderr or "")
                toast(self.app_window, "Could not update the panel password")

        run_async(["php", "-r", php, new], done, root=True, timeout=30)
