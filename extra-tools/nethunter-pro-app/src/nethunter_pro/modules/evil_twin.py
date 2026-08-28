"""Evil Twin attack helper (EvilTwinFragment).

Scans for APs on a monitor interface and launches an airbase-ng evil twin.
"""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..module import NHModule, register
from ..widgets import ToolRunner


@register
class EvilTwin(NHModule):
    title = "Evil Twin"
    icon = "network-wireless-encrypted-symbolic"
    description = "Clone an AP with airbase-ng"
    required_tools = ["airbase-ng"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        group = Adw.PreferencesGroup(
            title="Evil Twin",
            description="Needs a monitor-mode interface (see USB and Radio).",
        )
        self.iface = Adw.EntryRow(title="Monitor interface")
        self.iface.set_text("wlan1mon")
        group.add(self.iface)
        self.essid = Adw.EntryRow(title="ESSID to clone")
        group.add(self.essid)
        self.channel = Adw.EntryRow(title="Channel")
        self.channel.set_text("6")
        group.add(self.channel)

        row = Adw.ActionRow(title="Start evil twin", subtitle="airbase-ng")
        b = Gtk.Button(label="Start", valign=Gtk.Align.CENTER)
        b.add_css_class("suggested-action")
        b.connect("clicked", self._start)
        row.add_suffix(b)
        group.add(row)
        box.append(group)

        self.runner = ToolRunner()
        box.append(self.runner)
        return box

    def _start(self, _b: Gtk.Button) -> None:
        essid = self.essid.get_text().strip()
        if not essid:
            self.runner.output.append("[set an ESSID first]\n\n")
            return
        self.runner.run(
            ["airbase-ng", "-e", essid, "-c", self.channel.get_text().strip() or "6",
             self.iface.get_text().strip()],
            root=True,
        )
