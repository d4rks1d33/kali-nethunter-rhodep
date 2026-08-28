"""Bluetooth arsenal (BTFragment, BtDuckyFragment).

The Android app managed rfcomm and ran a BadBT ducky. Here we expose the
bluetoothctl basics and a BlueDucky-style attack over an interface.
"""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..module import NHModule, register
from ..widgets import ToolRunner


@register
class Bluetooth(NHModule):
    title = "Bluetooth"
    icon = "bluetooth-symbolic"
    description = "Scan and pair Bluetooth devices"
    required_tools = ["bluetoothctl"]  # blueducky is optional, checked at click

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        scan = Adw.PreferencesGroup(title="Devices")
        self._row(scan, "Power on", "bluetoothctl power on",
                  "bluetoothctl power on", "On", root=True, suggested=True)
        self._row(scan, "Scan (10s)", "discover nearby devices",
                  "sh -c 'bluetoothctl --timeout 10 scan on'", "Scan", root=True)
        self._row(scan, "Paired devices", "bluetoothctl devices",
                  "bluetoothctl devices", "List")
        box.append(scan)

        self.runner = ToolRunner()
        box.append(self.runner)
        return box

    def _row(self, group, title, subtitle, cmd, label, *, root=False, suggested=False):
        r = Adw.ActionRow(title=title, subtitle=subtitle)
        btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if suggested:
            btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _b: self.runner.run(cmd, root=root))
        r.add_suffix(btn)
        group.add(r)
