"""Run Docker containers from the UI, with the engine off until asked.

Three things, in the order they are needed:

  1. The engine. docker.service is not left running -- it holds power and
     memory for nothing when no container is up -- so this screen starts and
     stops it on demand, and every other control is disabled until it is up.

  2. Running an image by name. Type "bkimminich/juice-shop", press Run, and it
     pulls then runs it detached. Ports are not guessed from a README, which is
     free text and unreliable; they are read from the pulled image's own
     metadata (the EXPOSE list) and published with -p, and if one of them looks
     like a web port the URL is shown.

  3. Finding an image. A search box queries Docker Hub's API and lists matches
     with their description and stars, so the name does not have to be known
     exactly. Picking one fills the run box.

Everything that touches Docker goes through the root helper, since the daemon's
socket is root-owned and this app never runs the UI thread privileged.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from gi.repository import Adw, GLib, Gtk

from ..executor import Result, run_async, which
from ..module import NHModule, register
from ..widgets import ToolRunner, toast

HUB_SEARCH = "https://hub.docker.com/v2/search/repositories/?query=%s&page_size=15"
# Ports that, if a container exposes them, are worth offering as a web link.
WEB_PORTS = {80, 443, 3000, 8080, 8000, 8888, 5000, 9000, 4200, 8081}


@register
class Docker(NHModule):
    title = "Docker"
    icon = "nethunter-docker-symbolic"
    description = "Start the engine on demand and run containers"
    required_tools = ["docker"]

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ---- engine ------------------------------------------------------
        eng = Adw.PreferencesGroup(
            title="Docker engine",
            description="Off by default. Nothing below works until it is up.")
        self.engine_row = Adw.ActionRow(title="Engine", subtitle="checking…")
        self.start_btn = Gtk.Button(label="Start", valign=Gtk.Align.CENTER)
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda _b: self._engine("start"))
        self.stop_btn = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER)
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", lambda _b: self._engine("stop"))
        self.engine_row.add_suffix(self.start_btn)
        self.engine_row.add_suffix(self.stop_btn)
        eng.add(self.engine_row)
        box.append(eng)

        # ---- run an image ------------------------------------------------
        rg = Adw.PreferencesGroup(
            title="Run an image",
            description="Pulls, then runs detached. Exposed ports are published "
            "automatically.")
        self.image_entry = Adw.EntryRow(title="Image, e.g. bkimminich/juice-shop")
        self.image_entry.connect("apply", lambda _r: self._run_image())
        self.image_entry.set_show_apply_button(False)
        rg.add(self.image_entry)

        self.run_btn = Gtk.Button(label="Run", valign=Gtk.Align.CENTER)
        self.run_btn.add_css_class("suggested-action")
        self.run_btn.connect("clicked", lambda _b: self._run_image())
        run_row = Adw.ActionRow(title="Pull and run")
        run_row.add_suffix(self.run_btn)
        rg.add(run_row)
        box.append(rg)

        # ---- search Docker Hub ------------------------------------------
        sg = Adw.PreferencesGroup(
            title="Search Docker Hub",
            description="Find an image by name; tap a result to fill the box "
            "above.")
        self.search_entry = Adw.EntryRow(title="Search hub.docker.com")
        self.search_entry.connect("apply", lambda _r: self._search())
        sg.add(self.search_entry)
        self._result_rows: list[Adw.ActionRow] = []
        self.search_group = sg
        box.append(sg)

        # ---- output ------------------------------------------------------
        self.runner = ToolRunner()
        box.append(self.runner)

        self._refresh_engine()
        self._poll = GLib.timeout_add_seconds(5, self._refresh_engine)
        return box

    # ------------------------------------------------------------- engine
    def _engine(self, action: str) -> None:
        run_async(["systemctl", action, "docker.service"],
                  lambda r: self._refresh_engine(), root=True, timeout=60)
        self.engine_row.set_subtitle("%sing…" % action)

    def _refresh_engine(self) -> bool:
        def done(r: Result) -> None:
            up = (r.stdout or "").strip() == "active"
            self.engine_row.set_subtitle("running" if up else "stopped")
            self.start_btn.set_sensitive(not up)
            self.stop_btn.set_sensitive(up)
            for w in (self.image_entry, self.run_btn, self.search_entry):
                w.set_sensitive(up)
        run_async(["systemctl", "is-active", "docker.service"], done,
                  root=True, timeout=10)
        return True  # keep the timeout alive

    # ------------------------------------------------------------- run
    def _run_image(self) -> None:
        image = self.image_entry.get_text().strip()
        if not image:
            toast(self.app_window, "Enter an image name first")
            return
        self.runner.output.append("Pulling %s …\n" % image)
        # Pull with the runner so progress streams, then inspect and run.
        # Chain it as one shell command through the root helper: pull, then read
        # the exposed ports out of the image and run detached publishing them.
        safe = shlex_quote(image)
        script = (
            "set -e\n"
            "docker pull %s\n"
            # Exposed ports come from the image's own config, not a README.
            "ports=$(docker image inspect --format "
            "'{{range $p,$_ := .Config.ExposedPorts}}{{$p}} {{end}}' %s)\n"
            "pub=\"\"\n"
            "for p in $ports; do n=${p%%/*}; pub=\"$pub -p $n:$n\"; done\n"
            "name=$(echo %s | tr '/:' '__')\n"
            "docker rm -f \"$name\" >/dev/null 2>&1 || true\n"
            "cid=$(docker run -d --name \"$name\" $pub %s)\n"
            "echo\n"
            "echo \"started $name ($cid)\"\n"
            "echo \"ports:$ports\"\n"
        ) % (safe, safe, safe, safe)
        self.runner.run(["sh", "-c", script], root=True)
        # After a moment, look at what came up and offer a URL if it is web.
        GLib.timeout_add_seconds(4, self._after_run, image)

    def _after_run(self, image: str) -> bool:
        name = image.replace("/", "_").replace(":", "_")
        script = (
            "docker inspect --format "
            "'{{range $p,$_ := .Config.ExposedPorts}}{{$p}} {{end}}' %s "
            "2>/dev/null"
        ) % shlex_quote(name)

        def done(r: Result) -> None:
            ports = []
            for tok in (r.stdout or "").split():
                num = tok.split("/")[0]
                try:
                    ports.append(int(num))
                except ValueError:
                    pass
            web = [p for p in ports if p in WEB_PORTS]
            if web:
                self.runner.output.append(
                    "web UI likely at http://127.0.0.1:%d\n" % web[0])
                toast(self.app_window,
                      "Running — open http://127.0.0.1:%d" % web[0])
            elif ports:
                toast(self.app_window,
                      "Running — ports %s" % ", ".join(map(str, ports)))
            else:
                toast(self.app_window, "Running (no exposed ports)")
        run_async(["sh", "-c", script], done, root=True, timeout=10)
        return False  # one-shot

    # ------------------------------------------------------------- search
    def _search(self) -> None:
        query = self.search_entry.get_text().strip()
        if not query:
            return
        self._clear_results()
        placeholder = Adw.ActionRow(title="Searching…")
        self.search_group.add(placeholder)
        self._result_rows.append(placeholder)

        # The HTTP request is off the UI thread; run_async runs any argv, so a
        # tiny python one-liner does the fetch without blocking or needing root.
        url = HUB_SEARCH % urllib.parse.quote(query)
        fetch = (
            "import json,urllib.request\n"
            "try:\n"
            "    d=json.load(urllib.request.urlopen(%r,timeout=15))\n"
            "    for r in d.get('results',[]):\n"
            "        print('\\t'.join([r.get('repo_name',''),"
            "str(r.get('star_count',0)),"
            "(r.get('short_description') or '').replace(chr(9),' ')[:80]]))\n"
            "except Exception as e:\n"
            "    print('ERR\\t'+str(e))\n"
        ) % url
        run_async(["python3", "-c", fetch], self._on_search, timeout=20)

    def _on_search(self, r: Result) -> None:
        self._clear_results()
        lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
        if not lines:
            row = Adw.ActionRow(title="No results")
            self.search_group.add(row)
            self._result_rows.append(row)
            return
        if lines[0].startswith("ERR"):
            row = Adw.ActionRow(title="Search failed",
                                subtitle=lines[0][4:].strip())
            self.search_group.add(row)
            self._result_rows.append(row)
            return
        for line in lines:
            parts = line.split("\t")
            name = parts[0]
            stars = parts[1] if len(parts) > 1 else "0"
            desc = parts[2] if len(parts) > 2 else ""
            row = Adw.ActionRow(title=name,
                                subtitle="★ %s   %s" % (stars, desc))
            row.set_activatable(True)
            row.connect("activated", self._pick, name)
            use = Gtk.Button(label="Use", valign=Gtk.Align.CENTER)
            use.add_css_class("flat")
            use.connect("clicked", lambda _b, n=name: self._pick(None, n))
            row.add_suffix(use)
            self.search_group.add(row)
            self._result_rows.append(row)

    def _pick(self, _row, name: str) -> None:
        self.image_entry.set_text(name)
        toast(self.app_window, "Filled in %s — press Run" % name)

    def _clear_results(self) -> None:
        for row in self._result_rows:
            self.search_group.remove(row)
        self._result_rows = []


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
