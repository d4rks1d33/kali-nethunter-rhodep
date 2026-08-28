"""WPS scanning and attacks (WPSFragment): wash and reaver."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..module import NHModule, register
from ..widgets import ToolRunner


@register
class WifiAttacks(NHModule):
    title = "Wi-Fi (WPS)"
    icon = "network-wireless-symbolic"
    description = "WPS scanning and attacks (wash, reaver)"
    required_tools = ["wash"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        group = Adw.PreferencesGroup(
            title="WPS", description="Needs a monitor-mode interface (wlan1mon).")
        self.iface = Adw.EntryRow(title="Monitor interface")
        self.iface.set_text("wlan1mon")
        group.add(self.iface)
        self.bssid = Adw.EntryRow(title="Target BSSID (for reaver)")
        group.add(self.bssid)

        scan = Adw.ActionRow(title="Scan for WPS APs", subtitle="wash (live; Stop to end)")
        sb = Gtk.Button(label="Scan", valign=Gtk.Align.CENTER)
        sb.add_css_class("suggested-action")
        sb.connect("clicked", lambda _b: self.runner.run(
            f"wash -i {self.iface.get_text().strip()}", root=True))
        scan.add_suffix(sb)
        group.add(scan)

        attack = Adw.ActionRow(title="Reaver attack", subtitle="reaver against the BSSID")
        ab = Gtk.Button(label="Attack", valign=Gtk.Align.CENTER)
        ab.connect("clicked", self._reaver)
        attack.add_suffix(ab)
        group.add(attack)
        box.append(group)

        self.runner = ToolRunner()
        box.append(self.runner)
        return box

    def _reaver(self, _b: Gtk.Button) -> None:
        bssid = self.bssid.get_text().strip()
        if not bssid:
            self.runner.output.append("[set a target BSSID first]\n\n")
            return
        self.runner.run(
            f"reaver -i {self.iface.get_text().strip()} -b {bssid} -vv", root=True)
