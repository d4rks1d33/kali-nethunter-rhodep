"""Start and stop Kali services.

The Android app kept a SQLite list of services and toggled them inside the
chroot. On NetHunter Pro these are ordinary systemd/init services, so this
just wraps the service manager. Ships a sensible default set; the state is
read live rather than stored.
"""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..executor import Result, run_async
from ..module import NHModule, register
from ..widgets import OutputView, toast

# name -> (label, description)
SERVICES = {
    "ssh": ("SSH", "OpenSSH server"),
    "postgresql": ("PostgreSQL", "Database for Metasploit"),
    "bettercap": ("bettercap", "Network attack framework"),
    "apache2": ("Apache", "Web server"),
    "nfs-server": ("NFS", "Network file system"),
}


@register
class KaliServices(NHModule):
    title = "Kali Services"
    icon = "system-run-symbolic"
    description = "Start and stop common Kali services"

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        group = Adw.PreferencesGroup(title="Services")
        self.rows: dict[str, Adw.SwitchRow] = {}
        for name, (label, desc) in SERVICES.items():
            row = Adw.SwitchRow(title=label, subtitle=desc)
            row.connect("notify::active", self._on_toggle, name)
            self.rows[name] = row
            group.add(row)
        box.append(group)

        self.output = OutputView()
        box.append(self.output)

        self._refresh_states()
        return box

    def _refresh_states(self) -> None:
        for name, row in self.rows.items():
            def done(result: Result, r=row) -> None:
                r.handler_block_by_func(self._on_toggle)
                r.set_active(result.stdout.strip() == "active")
                r.handler_unblock_by_func(self._on_toggle)

            run_async(
                ["systemctl", "is-active", name], done, timeout=10
            )

    def _on_toggle(self, row: Adw.SwitchRow, _param, name: str) -> None:
        action = "start" if row.get_active() else "stop"
        self.output.append(f"$ systemctl {action} {name}\n")

        def done(result: Result) -> None:
            if result.stderr:
                self.output.append(result.stderr)
            self.output.append(f"[exit {result.returncode}]\n\n")
            toast(self.app_window, f"{name} {action}ed")

        run_async(["systemctl", action, name], done, root=True, timeout=30)
