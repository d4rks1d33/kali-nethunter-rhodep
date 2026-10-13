"""The Pandora D-605 keyfob emulator window (GTK4 / libadwaita).

Layout (phone-first, adapts via Adw.Breakpoint):
  * a big DrawingArea rendering the keyfob's 88x64 2bpp OLED, scaled up;
  * a touch button pad with every button the firmware reads (UP/DOWN/OK/BACK +
    B5/B6) that distinguishes SHORT taps from LONG presses (>600 ms) because the
    firmware acts differently on each;
  * a firmware loader (FileDialog for any .hex) with a profile picker;
  * an auto HackRF bridge status line.

DESIGN NOTE — the firmware does EVERYTHING. We only provide the hardware (CPU,
OLED, buttons/GPIO) and a bridge to the HackRF. There are NO manual band / TX /
RX controls: the firmware itself selects the band/frequency, decides when to
transmit, and which protocol to use. The HackRF bridge just follows the
firmware: it keeps the SDR listening (RX edges -> firmware) and transmits
whenever the firmware bit-bangs a waveform (TX edges -> SDR). All of it runs on
a single emulator worker thread so only one thread ever touches the core.

Threading model:
  * ONE worker thread owns the emulator. It loops run()+framebuffer, drains a
    command queue (button nav, load firmware), and drives the HackRF bridge.
  * The GTK main thread only reads the latest framebuffer to paint and pushes
    commands onto the queue. This is what makes the buttons actually work: a
    press is executed via the emulator's SYNCHRONIZED nav routine (which runs
    the core until the firmware's poll loop samples the pin), not a raw GPIO
    poke that the running loop would miss.
"""
from __future__ import annotations

import os
import queue
import threading

from gi.repository import Adw, GLib, Gdk, Gio, Gtk

from . import core
from . import hackrf

# --- emulation loop tuning ------------------------------------------------
REDRAW_MS = 60            # GTK redraw cadence (~16 fps when the UI changes)
INSNS_PER_STEP = 200_000  # instructions per idle worker step
LONG_PRESS_MS = 600       # threshold: >= this held time counts as a long press

# Default firmware search dir (falls back to ~ if it does not exist).
DEFAULT_FW_DIR = ("/opt/postmarket/nethunter-rhodep-repo/extra-tools/"
                  "pandora-emu/firmwares")

# OLED render colours per 2bpp level (0..3). Dark background, light pixels.
# The panel is grayscale 2bpp; level is decoded as (hi<<1)|lo per oled_render.py.
LEVEL_COLORS = {
    0: (0.06, 0.07, 0.10),   # background / off
    1: (0.35, 0.45, 0.40),   # dim
    2: (0.70, 0.85, 0.75),   # mid
    3: (0.85, 1.00, 0.88),   # bright
}
BG_COLOR = (0.02, 0.03, 0.05)

# Every logical button the firmware reads, shown on the pad: (btn index, label).
# UP/DOWN/OK/BACK form the nav cross; B5/B6 are the extra keyfob buttons.
PAD_BUTTONS = [
    (core.B_UP, "\u25b2"),    # up triangle
    (core.B_DOWN, "\u25bc"),  # down triangle
    (core.B_OK, "OK"),
    (core.B_BACK, "\u21a9"),  # back arrow
    (core.B5, "B5"),
    (core.B6, "B6"),
]


# ==========================================================================
#  Emulator worker thread — the ONLY thread that touches the core.
# ==========================================================================
class EmuWorker(threading.Thread):
    """Owns the emulator. Runs it continuously, executes queued commands
    (button nav, load firmware) synchronously, and drives the HackRF bridge.

    Commands are tuples pushed by the GTK thread:
      ("nav",  btn, hold)        -> synchronized button press
      ("load", path, profile)    -> load a .hex and reset+unlock
      ("quit",)                  -> stop the thread
    The latest framebuffer is published under a lock for the GTK thread to read.
    """

    def __init__(self, on_status):
        super().__init__(daemon=True)
        self._emu = None
        self._cmds: "queue.Queue" = queue.Queue()
        self._fb_lock = threading.Lock()
        self._fb = None
        self._on_status = on_status           # callback(str) marshalled to GTK
        self._running = True
        self._bridge = hackrf.Bridge()        # auto RX/TX follower (no-op w/o SDR)

    # ---- GTK-thread API (thread-safe: only enqueues) ----------------------
    def post_nav(self, btn: int, hold: bool) -> None:
        self._cmds.put(("nav", btn, hold))

    def post_load(self, path: str, profile) -> None:
        self._cmds.put(("load", path, profile))

    def stop(self) -> None:
        self._cmds.put(("quit",))

    def latest_fb(self):
        with self._fb_lock:
            return self._fb

    # ---- worker-thread internals ------------------------------------------
    def _publish_fb(self) -> None:
        if self._emu is None or not self._emu.loaded:
            return
        fb = self._emu.get_framebuffer()
        with self._fb_lock:
            self._fb = fb

    def _status(self, text: str) -> None:
        GLib.idle_add(self._on_status, text)

    def _do_load(self, path: str, profile) -> None:
        try:
            emu = core.PandoraEmu(profile if profile is not None
                                  else core.guess_profile(path))
            emu.load_hex(path)
            self._status(f"Booting {os.path.basename(path)} "
                         f"({emu.profile_name})...")
            unlocked = emu.reset_unlock()
            self._emu = emu
            self._bridge.attach(emu)
            self._publish_fb()
            self._status("Ready" + ("" if unlocked else " (unlock not confirmed)"))
        except Exception as exc:  # noqa: BLE001 - surface any load failure
            self._status(f"Load failed: {exc}")

    def _do_nav(self, btn: int, hold: bool) -> None:
        if self._emu is None or not self._emu.loaded:
            return
        # Synchronized press: runs the core until the firmware's poll loop
        # samples the pin, then releases. THIS is why the buttons work.
        self._emu.nav(btn, hold)
        self._publish_fb()

    def run(self) -> None:
        while self._running:
            # 1) Drain any pending commands (buttons/load) first — they are
            #    synchronous and take priority over free-running.
            try:
                cmd = self._cmds.get(timeout=0.01)
            except queue.Empty:
                cmd = None
            if cmd is not None:
                kind = cmd[0]
                if kind == "quit":
                    self._running = False
                    break
                elif kind == "load":
                    self._do_load(cmd[1], cmd[2])
                elif kind == "nav":
                    self._do_nav(cmd[1], cmd[2])
                continue

            # 2) Free-run the firmware a bit, then follow the radio + publish.
            if self._emu is not None and self._emu.loaded:
                self._emu.run(INSNS_PER_STEP)
                self._bridge.pump()      # TX firmware waveform / inject RX edges
                self._publish_fb()
            else:
                # nothing loaded yet; avoid a busy spin
                threading.Event().wait(0.05)


# ==========================================================================
#  Keyfob screen (DrawingArea)
# ==========================================================================
class KeyfobScreen(Gtk.DrawingArea):
    """Renders the emulator's 2bpp framebuffer, scaled and centred."""

    def __init__(self, get_fb) -> None:
        super().__init__()
        self._get_fb = get_fb
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_content_width(352)   # 88*4
        self.set_content_height(256)  # 64*4
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height) -> None:
        cr.set_source_rgb(*BG_COLOR)
        cr.paint()
        fb = self._get_fb()
        if fb is None:
            return
        cols = core.FB_VISIBLE_COLS   # 88
        pages = core.FB_PAGES         # 16
        rows = pages * 4              # 64
        # integer scale that fits, centred
        scale = max(1, min(width // cols, height // rows))
        ox = (width - cols * scale) // 2
        oy = (height - rows * scale) // 2
        stride = core.FB_COLS         # 92 bytes per page in the raw buffer
        for page in range(pages):
            for col in range(cols):
                byte = fb[page * stride + col]
                for k in range(4):
                    lo = (byte >> (2 * k)) & 1
                    hi = (byte >> (2 * k + 1)) & 1
                    level = (hi << 1) | lo      # 0..3 (grayscale 2bpp)
                    if level == 0:
                        continue
                    cr.set_source_rgb(*LEVEL_COLORS[level])
                    y = page * 4 + k
                    cr.rectangle(ox + col * scale, oy + y * scale, scale, scale)
                    cr.fill()


# ==========================================================================
#  Main window
# ==========================================================================
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Pandora Emu")
        self.set_default_size(420, 820)

        self._pad_widgets: dict = {}
        self._profile = None  # None = auto-detect from filename

        self.worker = EmuWorker(on_status=self._set_status)
        self.worker.start()

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        toolbar = Adw.ToolbarView()
        self.toasts.set_child(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.status_lbl = Gtk.Label(label="No firmware loaded")
        self.status_lbl.add_css_class("dim-label")
        header.set_title_widget(self.status_lbl)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        toolbar.set_content(scroller)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content.set_margin_top(12)
        self.content.set_margin_bottom(12)
        self.content.set_margin_start(12)
        self.content.set_margin_end(12)
        scroller.set_child(self.content)

        self._build_screen()
        self._build_pad()
        self._build_firmware()
        self._build_hackrf_status()

        # redraw timer (GTK thread only reads the published framebuffer)
        self._last_fb = None
        GLib.timeout_add(REDRAW_MS, self._redraw_tick)

        self.connect("close-request", self._on_close)

    # ------------------------------------------------------------ screen ----
    def _build_screen(self) -> None:
        frame = Gtk.Frame()
        frame.add_css_class("pe-screen-frame")
        self.screen = KeyfobScreen(self.worker.latest_fb)
        frame.set_child(self.screen)
        self.content.append(frame)

    # ------------------------------------------------------------ pad -------
    def _build_pad(self) -> None:
        """The SIX physical keyfob buttons (B1..B6), exactly like the Flipper
        PandoraARM app. The keyfob has six numbered buttons; each maps to one
        GPIO pin and the UI labels them B1..B6 with the function from the fob's
        manual (same text as the Flipper app).

        Button -> core pin -> function (from the Flipper app BUTTON LAYOUT):
          B1 = UP  pin (PF7)  -> On / menu / cell +
          B2 = OK  pin (PC9)  -> confirm / mode (hold: erase cell)
          B3 = DOWN pin (PF5) -> menu / cell -  (hold: auto)
          B4 = BACK pin (PA15)-> RX frequency / close car
          B5 = B5  pin (PA1)  -> AM+FM / open car
          B6 = B6  pin (PA6)  -> trunk   (hold: back to menu)
        """
        group = Adw.PreferencesGroup(title="Keyfob buttons")
        self.content.append(group)

        grid = Gtk.Grid(row_spacing=12, column_spacing=12,
                        halign=Gtk.Align.CENTER)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        group.add(self._wrap(grid))

        # 2 columns x 3 rows: B1/B2/B3 on the left (the side buttons), B4/B5/B6
        # on the right (the top buttons), just like the real fob. (core_btn,
        # label, subtitle, col, row). Labels/functions copied from the Flipper.
        BTNS = [
            (core.B_UP,   "B1", "On / Menu / Cell +", 0, 0),
            (core.B_BACK, "B4", "RX Frequency",       1, 0),
            (core.B_OK,   "B2", "Confirm / Mode",     0, 1),
            (core.B5,     "B5", "AM+FM / Open",       1, 1),
            (core.B_DOWN, "B3", "Menu / Cell \u2212", 0, 2),
            (core.B6,     "B6", "Trunk",              1, 2),
        ]
        W, H = 158, 64
        for btn, name, subtitle, col, row in BTNS:
            grid.attach(self._pad_button(btn, name, subtitle, W, H),
                        col, row, 1, 1)

        hint = Gtk.Label(
            label="Tap = short press \u00b7 hold \u2265 %d ms = long press "
            "(B6 long = SERVICE/DFU)" % LONG_PRESS_MS)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        self.content.append(hint)

        self._build_button_cheatsheet()

    def _build_button_cheatsheet(self) -> None:
        """Collapsible cheat-sheet: what each button does per screen, straight
        from RE_BUTTONS_D605.md §7. The firmware changes button functions by
        screen, so this is the real reference."""
        exp = Adw.ExpanderRow(
            title="What do the buttons do per screen?",
            subtitle="functions change per screen (firmware-driven)")

        # (screen title, [(button, function), ...]). Buttons are the UI labels
        # B1..B6 (B1=UP pin, B2=OK pin, B3=DOWN pin, B4=BACK pin, B5, B6).
        SHEET = [
            ("Main carousel (pandoramax)", [
                ("B6", "previous mode"),
                ("B4", "next mode"),
                ("B5", "enter submenu"),
                ("B1 / B3", "go to scanner"),
                ("B2", "set frequency"),
            ]),
            ("Main list (pandora)", [
                ("B3", "move selection down"),
                ("B6", "cycle modes"),
                ("B5", "enter / select"),
            ]),
            ("Submenu (AM+FM etc.)", [
                ("B1", "change frequency"),
                ("B3", "change modulation (AM+FM / AM)"),
                ("B2", "\u2b05 back to main menu"),
                ("B4 / B6", "memory cell +/-"),
            ]),
            ("Scanner", [
                ("B1", "scan frequency +1"),
                ("B2", "\u2b05 exit to carousel"),
                ("B4 / B6", "exit + change mode"),
            ]),
            ("Any screen", [
                ("B6 (long hold)", "SERVICE / DFU reset \u2014 avoid"),
            ]),
        ]
        for screen, rows in SHEET:
            r = Adw.ActionRow(title=screen)
            r.add_css_class("property")
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                           margin_top=4, margin_bottom=4)
            for btn, fn in rows:
                line = Gtk.Label(xalign=0.0,
                                 label=f"<b>{btn}</b>  \u2014  {fn}",
                                 use_markup=True)
                line.add_css_class("caption")
                body.append(line)
            r.set_child(body)
            exp.add_row(r)

        g = Adw.PreferencesGroup()
        g.add(exp)
        self.content.append(g)

    def _pad_button(self, btn: int, name: str, subtitle: str,
                    w: int, h: int) -> Gtk.Button:
        """A big touch button for a keyfob button (B1..B6). Always drives the
        same GPIO pin; distinguishes SHORT tap from LONG press. `name` (B1..B6)
        is shown big and in the toast; `subtitle` is the manual function. SHORT =
        quick tap; LONG = hold >= LONG_PRESS_MS (the fob's secondary function).
        The worker runs the core until the firmware's poll loop samples the pin.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      halign=Gtk.Align.CENTER)
        lbl = Gtk.Label(label=name)
        lbl.add_css_class("pe-pad-name")
        box.append(lbl)
        sub = Gtk.Label(label=subtitle)
        sub.add_css_class("pe-pad-sub")
        box.append(sub)

        b = Gtk.Button(child=box, valign=Gtk.Align.CENTER,
                       width_request=w, height_request=h)
        b.add_css_class("pe-pad")
        b.add_css_class("pill")

        # SHORT vs LONG detection, reliable on touch screens.
        #
        # One GestureClick drives everything. On "pressed" we start a timer for
        # LONG_PRESS_MS; we also highlight the button. Two outcomes:
        #   * the timer fires first  -> it was a LONG press: send hold=True now
        #     (and mark it so the release does not also send a short tap).
        #   * "released"/"end"/"cancel" fires first -> it was a SHORT tap:
        #     cancel the timer and send hold=False.
        # Either way the highlight is cleared on release/cancel/end, so no button
        # is ever left stuck highlighted. Using a single gesture avoids the
        # two-gesture claim races that dropped presses before.
        state = {"timer": 0, "fired": False}

        def fire(is_long: bool):
            state["fired"] = True
            self.worker.post_nav(btn, is_long)
            self._toast(f"{name} {'LONG' if is_long else 'tap'}")

        def on_long_timeout(btn=btn):
            state["timer"] = 0
            if not state["fired"]:
                fire(True)
            return False  # one-shot

        def on_pressed(_g, _n, _x, _y, b=b):
            state["fired"] = False
            b.add_css_class("pe-pad-active")
            if state["timer"]:
                GLib.source_remove(state["timer"])
            state["timer"] = GLib.timeout_add(LONG_PRESS_MS, on_long_timeout)

        def on_done(*_a, b=b):
            b.remove_css_class("pe-pad-active")
            if state["timer"]:
                GLib.source_remove(state["timer"])
                state["timer"] = 0
            if not state["fired"]:
                fire(False)   # released before the long threshold -> short tap

        click = Gtk.GestureClick()
        click.connect("pressed", on_pressed)
        click.connect("released", on_done)
        click.connect("cancel", on_done)
        click.connect("end", on_done)
        b.add_controller(click)

        self._pad_widgets[btn] = b
        return b

    # ------------------------------------------------------------ firmware --
    def _build_firmware(self) -> None:
        group = Adw.PreferencesGroup(title="Firmware")

        self.fw_row = Adw.ActionRow(title="Load .hex",
                                    subtitle="no firmware loaded")
        load_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        load_btn.add_css_class("suggested-action")
        load_btn.connect("clicked", self._on_load_clicked)
        self.fw_row.add_suffix(load_btn)
        group.add(self.fw_row)

        self.profile_row = Adw.ComboRow(title="Profile")
        pmodel = Gtk.StringList()
        for name in ("Auto (by filename)", "pandora", "pandoramax"):
            pmodel.append(name)
        self.profile_row.set_model(pmodel)
        self.profile_row.set_selected(0)
        self.profile_row.connect("notify::selected", self._on_profile_changed)
        group.add(self.profile_row)

        self.content.append(group)

    def _on_profile_changed(self, row, _param) -> None:
        sel = row.get_selected()
        self._profile = {0: None,
                         1: core.PROFILE_PANDORA,
                         2: core.PROFILE_PANDORAMAX}.get(sel)

    def _on_load_clicked(self, _btn) -> None:
        dialog = Gtk.FileDialog(title="Choose a keyfob firmware (.hex)")
        hexfilter = Gtk.FileFilter()
        hexfilter.set_name("Intel HEX (*.hex)")
        hexfilter.add_pattern("*.hex")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(hexfilter)
        dialog.set_filters(filters)
        dialog.set_default_filter(hexfilter)
        start = DEFAULT_FW_DIR if os.path.isdir(DEFAULT_FW_DIR) else os.path.expanduser("~")
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(start))
        except Exception:
            pass
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path = gfile.get_path()
        self.fw_row.set_subtitle(os.path.basename(path))
        self.worker.post_load(path, self._profile)

    # ------------------------------------------------------------ hackrf ----
    def _build_hackrf_status(self) -> None:
        group = Adw.PreferencesGroup(
            title="HackRF bridge",
            description="Automatic. The firmware drives the radio; the bridge "
                        "just transmits its waveform and feeds back received "
                        "edges. There is nothing to configure here.")
        self.hackrf_row = Adw.ActionRow(title="HackRF", subtitle="checking...")
        refresh = Gtk.Button(label="Rescan", valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda _b: self._refresh_hackrf())
        self.hackrf_row.add_suffix(refresh)
        group.add(self.hackrf_row)
        self.content.append(group)
        self._refresh_hackrf()

    def _refresh_hackrf(self) -> None:
        def worker():
            status = hackrf.detect()
            GLib.idle_add(lambda: self.hackrf_row.set_subtitle(status.detail))
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ loop ------
    def _redraw_tick(self) -> bool:
        # Always queue a redraw while a framebuffer exists. The draw itself is
        # cheap (88x64) and KeyfobScreen reads the latest published buffer, so
        # this reliably reflects every change (button nav, boot animation, etc.)
        # without depending on a possibly-stale equality check.
        if self.worker.latest_fb() is not None:
            self.screen.queue_draw()
        return True

    # ------------------------------------------------------------ helpers ---
    def _wrap(self, child) -> Adw.ActionRow:
        """Preferences groups host rows; wrap a bare widget in an ActionRow."""
        row = Adw.ActionRow()
        row.set_activatable(False)
        row.set_child(child)
        return row

    def _set_status(self, text: str) -> bool:
        self.status_lbl.set_label(text)
        return False

    def _toast(self, text: str) -> bool:
        self.toasts.add_toast(Adw.Toast.new(text))
        return False

    def _on_close(self, _win) -> bool:
        self.worker.stop()
        return False


class PandoraEmuApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.kali.PandoraEmu")

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # A "quit" action so the running app can be closed from D-Bus, e.g.:
        #   gdbus call --session --dest org.kali.PandoraEmu \
        #     --object-path /org/kali/PandoraEmu \
        #     --method org.gtk.Actions.Activate quit '[]' '{}'
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

    def do_activate(self) -> None:
        self._load_css()
        win = self.props.active_window or MainWindow(self)
        win.present()

    def _load_css(self) -> None:
        import importlib.resources as res
        try:
            css = (res.files("pandora_emu") / "style.css").read_bytes()
        except Exception:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
