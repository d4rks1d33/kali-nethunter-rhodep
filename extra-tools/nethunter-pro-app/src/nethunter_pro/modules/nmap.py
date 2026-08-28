"""Build and run an nmap scan, with live output and a Quick preset."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..module import NHModule, register
from ..widgets import ToolRunner

TECHNIQUES = {
    "SYN scan (-sS)": "-sS",
    "Connect scan (-sT)": "-sT",
    "UDP scan (-sU)": "-sU",
    "Ping scan (-sn)": "-sn",
    "No ping (-Pn)": "-Pn",
}
TIMING = {f"-T{i}": f"-T{i}" for i in range(6)}


@register
class Nmap(NHModule):
    title = "Nmap"
    icon = "network-workgroup-symbolic"
    description = "Scan hosts and networks"
    required_tools = ["nmap"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        group = Adw.PreferencesGroup(title="Target and options")
        self.target = Adw.EntryRow(title="Target (host, range or CIDR)")
        self.target.set_text("127.0.0.1")
        group.add(self.target)
        self.tech = Adw.ComboRow(title="Technique", model=Gtk.StringList.new(list(TECHNIQUES)))
        group.add(self.tech)
        self.timing = Adw.ComboRow(title="Timing", model=Gtk.StringList.new(list(TIMING)))
        self.timing.set_selected(3)
        group.add(self.timing)
        self.sv = Adw.SwitchRow(title="Service/version detection", subtitle="-sV")
        group.add(self.sv)
        self.os = Adw.SwitchRow(title="OS detection", subtitle="-O")
        group.add(self.os)
        box.append(group)

        actions = Adw.PreferencesGroup()
        full = Adw.ActionRow(title="Run scan", subtitle="Use the options above")
        sb = Gtk.Button(label="Scan", valign=Gtk.Align.CENTER)
        sb.add_css_class("suggested-action")
        sb.connect("clicked", self._on_scan)
        full.add_suffix(sb)
        actions.add(full)
        quick = Adw.ActionRow(title="Quick scan", subtitle="nmap -T4 -F (top 100 ports)")
        qb = Gtk.Button(label="Quick", valign=Gtk.Align.CENTER)
        qb.connect("clicked", self._on_quick)
        quick.add_suffix(qb)
        actions.add(quick)
        box.append(actions)

        self.runner = ToolRunner()
        box.append(self.runner)
        return box

    def _target(self) -> str:
        return self.target.get_text().strip() or "127.0.0.1"

    def _on_quick(self, _b: Gtk.Button) -> None:
        self.runner.run(f"nmap -T4 -F {self._target()}", root=True)

    def _on_scan(self, _b: Gtk.Button) -> None:
        parts = ["nmap", list(TECHNIQUES.values())[self.tech.get_selected()],
                 list(TIMING.values())[self.timing.get_selected()]]
        if self.sv.get_active():
            parts.append("-sV")
        if self.os.get_active():
            parts.append("-O")
        parts.append(self._target())
        self.runner.run(" ".join(parts), root=True)
