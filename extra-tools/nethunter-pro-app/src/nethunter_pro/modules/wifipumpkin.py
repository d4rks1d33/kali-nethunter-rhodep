"""Wifipumpkin3 rogue-AP framework (WifipumpkinFragment).

Options are set in the GUI, including the captive-portal template, which is
discovered from wifipumpkin3's templates folder at runtime the same way the
Android app did, and then wifipumpkin3 opens in a terminal with an -x startup
script applying them.
"""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..executor import Result, run_async
from ..module import NHModule, register
from ..widgets import ToolRunner

# wifipumpkin3 keeps its captive-portal templates in one of these; the Debian
# package uses the dist-packages path.
TEMPLATE_DIRS = [
    "/usr/lib/python3/dist-packages/wifipumpkin3/data/config/templates",
    "/usr/share/wifipumpkin3/config/templates",
    "/root/.config/wifipumpkin3/config/templates",
]
# set proxy <name>; the captive portal is captiveflask, not pumpkinproxy
# (pumpkinproxy is the transparent MITM proxy, a different thing).
PROXIES = {
    "None": None,
    "Captive Flask portal": "captiveflask",
    "Transparent proxy (pumpkinproxy)": "pumpkinproxy",
    "Sniffkin3 (capture creds)": "sniffkin3",
}


@register
class Wifipumpkin(NHModule):
    title = "Wifipumpkin3"
    icon = "nethunter-wifipumpkin-symbolic"
    description = "Rogue access point framework"
    required_tools = ["wifipumpkin3"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        group = Adw.PreferencesGroup(
            title="Access point",
            description="Needs a Wi-Fi interface that supports AP mode.",
        )
        self.iface = Adw.EntryRow(title="AP interface")
        self.iface.set_text("wlan1")
        group.add(self.iface)
        # The interface with internet, shared to the AP's clients (-iNet).
        # Leave empty for a captive portal that does not pass traffic through.
        # wlan0 is the internal Wi-Fi, which normally holds the real connection.
        self.iface_net = Adw.EntryRow(title="Internet interface (share to clients)")
        self.iface_net.set_text("wlan0")
        group.add(self.iface_net)
        self.ssid = Adw.EntryRow(title="SSID")
        self.ssid.set_text("Free WiFi")
        group.add(self.ssid)

        self.proxy = Adw.ComboRow(
            title="Proxy / capture module",
            model=Gtk.StringList.new(list(PROXIES)),
        )
        group.add(self.proxy)

        # Captive-portal template, discovered from the templates folder.
        self.template = Adw.ComboRow(title="Captive-portal template")
        self.template_model = Gtk.StringList()
        self.template_model.append("None")
        self.template.set_model(self.template_model)
        group.add(self.template)

        refresh = Adw.ActionRow(title="Refresh templates", subtitle="Rescan wifipumpkin3 templates")
        rb = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        rb.connect("clicked", lambda _b: self._load_templates())
        refresh.add_suffix(rb)
        group.add(refresh)

        # Where the templates live, so more can be dropped in from a terminal.
        where = Adw.ActionRow(
            title="Templates folder",
            subtitle="/usr/share/wifipumpkin3/config/templates — add your own "
            "here, then Refresh")
        where.set_subtitle_selectable(True)
        group.add(where)
        box.append(group)

        actions = Adw.PreferencesGroup()
        start = Adw.ActionRow(
            title="Start rogue AP",
            subtitle="Opens wifipumpkin3 in a terminal with your options",
        )
        sb = Gtk.Button(label="Start", valign=Gtk.Align.CENTER)
        sb.add_css_class("suggested-action")
        sb.connect("clicked", self._start)
        start.add_suffix(sb)
        actions.add(start)
        box.append(actions)

        self.runner = ToolRunner()
        box.append(self.runner)

        self._load_templates()
        return box

    def _load_templates(self) -> None:
        def done(result: Result) -> None:
            # Reset to just "None", then add discovered templates.
            while self.template_model.get_n_items() > 1:
                self.template_model.remove(1)
            for name in sorted(result.stdout.split()):
                if name:
                    self.template_model.append(name)

        globbed = " ".join(f"{d}/" for d in TEMPLATE_DIRS)
        run_async(
            ["sh", "-c", f"for d in {globbed}; do [ -d \"$d\" ] && ls -1 \"$d\"; done 2>/dev/null | sort -u"],
            done, timeout=10,
        )

    def _start(self, _b: Gtk.Button) -> None:
        iface = self.iface.get_text().strip() or "wlan1"
        ssid = self.ssid.get_text().strip() or "Free WiFi"
        script = f"set interface {iface}; set ssid {ssid}; "

        # Share internet to the AP's clients if an internet interface is given.
        # This is the -iNet option; wifipumpkin3 sets up the NAT/forwarding.
        iface_net = self.iface_net.get_text().strip()
        if iface_net:
            script += f"set interface_net {iface_net}; "

        proxy = PROXIES[list(PROXIES)[self.proxy.get_selected()]]
        if proxy:
            # Just select the proxy. The old "ignore <proxy> off" was wrong --
            # ignore takes a logger class name, not a proxy, and wifipumpkin3
            # answered with 'requires a correct name of logger class'.
            script += f"set proxy {proxy}; "

        # With the captive portal, the template is chosen by enabling it on the
        # captiveflask proxy -- "set captiveflask.<Template> true" -- which is
        # what writes DarkLogin=true into captive-portal.ini. It is not a
        # templates.custom path; that command installs new templates rather than
        # selecting an existing one, which is why the old script showed no portal.
        tidx = self.template.get_selected()
        if proxy == "captiveflask" and tidx > 0:
            item = self.template_model.get_item(tidx)
            if item:
                tmpl = item.get_string()
                script += f"set captiveflask.{tmpl} true; "
        script += "start"
        self.runner.run_in_terminal(["wifipumpkin3", "-x", script], root=True)
