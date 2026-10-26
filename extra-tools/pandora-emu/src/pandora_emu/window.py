"""The Pandora D650 keyfob emulator window (GTK4 / libadwaita).

Layout (phone-first, adapts via Adw.Breakpoint):
  * a big DrawingArea rendering the keyfob's 88x64 2bpp OLED, scaled up;
  * a touch button pad with every button the firmware reads (the six keyfob
    buttons) that distinguishes SHORT taps from real LONG presses (the raw GPIO
    pin is held down the whole time your finger is on the button) because the
    firmware acts differently on each;
  * a firmware loader (FileDialog for any .hex); the core is AGNOSTIC (one HW
    model runs any firmware) so there is no profile picker;
  * a knock-code / unlock panel (enter the button sequence, auto-unlock);
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
    command queue (button nav, knock+unlock, load firmware, RX edges), and
    drives the HackRF bridge. It is the ONLY thread that touches the core.
  * The GTK main thread only reads the latest framebuffer to paint and pushes
    commands onto the queue. A button tap enqueues ONE ATOMIC nav command
    (core.nav -> pe_nav -> emu_nav_button): the core drives the pin low, runs the
    firmware's poll bursts with the pin held, lifts the pin, then runs the settle
    bursts -- all as one unit on the worker thread, so the firmware samples a
    sustained press and dispatches the action on the release edge (hold=True is a
    long press). A short tap and a long hold differ only by that hold flag.
  * The HackRF RX reader thread does NOT touch the core either: it hands its
    demodulated edges to the worker via post_rx_edges(), and the worker injects
    them (core.rx_inject) on its own thread. So under real RX there is still a
    single thread owning the core.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from gi.repository import Adw, GLib, Gdk, Gio, Gtk

from . import core
from . import hackrf

# --- touch/tap debug logging ---------------------------------------------
# Set PANDORA_EMU_DEBUG=1 (or point PANDORA_EMU_LOG at a file) to append a
# timestamped trace of every pad interaction (clicked / long-press / nav
# enqueue) to a log file. This exists so that when the user taps a pad button
# WITH A FINGER on the touchscreen we get a durable record of whether the tap
# reached GTK and whether a nav command was enqueued -- the exact chain that
# was previously broken by the ScrolledWindow stealing the touch sequence.
_DEBUG_LOG_PATH = os.environ.get("PANDORA_EMU_LOG")
if _DEBUG_LOG_PATH is None and os.environ.get("PANDORA_EMU_DEBUG"):
    _DEBUG_LOG_PATH = "/tmp/pandora-emu-tap.log"


def _dlog(msg: str) -> None:
    """Append a timestamped line to the tap-debug log (no-op unless enabled)."""
    if not _DEBUG_LOG_PATH:
        return
    try:
        with open(_DEBUG_LOG_PATH, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} "
                     f"{time.monotonic():.3f} {msg}\n")
    except Exception:
        pass

# --- emulation loop tuning ------------------------------------------------
REDRAW_MS = 60            # GTK redraw cadence (~16 fps when the UI changes)
INSNS_PER_STEP = 200_000  # instructions per idle worker step
LONG_PRESS_MS = 600       # UI threshold: >= this held time is shown as a LONG
                          # press (the raw pin is held either way; this only
                          # drives the toast/label text)

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

# The six keyfob buttons as shown on the pad: (core button index, UI label,
# manual function, grid col, grid row). This is the SINGLE source of truth for
# the button mapping (UI label -> core pin). B1=UP pin, B2=OK pin, B3=DOWN pin,
# B4=BACK pin, B5, B6; labels/functions copied from the Flipper PandoraARM app.
KEYFOB_BUTTONS = [
    (core.B_UP,   "B1", "On / Menu / Cell +", 0, 0),
    (core.B_BACK, "B4", "RX Frequency",       1, 0),
    (core.B_OK,   "B2", "Confirm / Mode",     0, 1),
    (core.B5,     "B5", "AM+FM / Open",       1, 1),
    (core.B_DOWN, "B3", "Menu / Cell \u2212", 0, 2),
    (core.B6,     "B6", "Trunk",              1, 2),
]

# UI label for each core button index (for toasts / knock editor chips).
BTN_LABEL = {btn: name for btn, name, *_ in KEYFOB_BUTTONS}

# Recognisable glyph per directional/confirm/back button (B5/B6 keep name-only
# since they have no universal symbol). Purely presentational.
PAD_GLYPHS = {
    core.B_UP:   "\u25b2",   # ▲
    core.B_DOWN: "\u25bc",   # ▼
    core.B_OK:   "\u25cf",   # ●  (confirm)
    core.B_BACK: "\u2190",   # ←
}


# ==========================================================================
#  Emulator worker thread — the ONLY thread that touches the core.
# ==========================================================================
class EmuWorker(threading.Thread):
    """Owns the emulator. Runs it continuously, executes queued commands
    (atomic button nav, knock+unlock, load firmware, RX edges) synchronously,
    and drives the HackRF bridge.

    Commands are tuples pushed by the GTK thread (or the HackRF RX reader):
      ("nav",     btn, hold)     -> ATOMIC button press: drive the GPIO pin low,
                                    run the firmware's poll bursts with the pin
                                    held, lift it, then run the settle bursts --
                                    one indivisible unit (core.nav / emu_nav_button
                                    / press_button_timed). hold=True is a long press.
      ("knock",   seq, gate)     -> set the knock-code, reset + auto-unlock
      ("load",    path)          -> load a .hex and reset+unlock (agnostic)
      ("rx",      edges)         -> inject demodulated RX edges into the core (the
                                    HackRF reader thread enqueues them so ONLY the
                                    worker thread ever touches the core)
      ("quit",)                  -> stop the thread (and the bridge)
    The latest framebuffer is published under a lock for the GTK thread to read.

    Button presses are ATOMIC: the canonical press+hold+release+settle cycle the
    firmware's nav_reader expects runs as ONE command on this thread (exactly like
    auto_unlock and the reference pandora_tui.press_button), so the firmware
    samples a sustained press and dispatches the action on the release edge. The
    cycle runs on the worker, never blocking the GTK main thread; the framebuffer
    is published right after so the navigation is visible immediately.
    """

    def __init__(self, on_status, on_frame=None):
        super().__init__(daemon=True)
        self._emu = None
        self._cmds: "queue.Queue" = queue.Queue()
        self._fb_lock = threading.Lock()
        self._fb = None
        self._on_status = on_status           # callback(str) marshalled to GTK
        # callback() marshalled to GTK asking for an IMMEDIATE screen redraw
        # (used right after a nav publishes so the navigated frame is shown at
        # once instead of waiting for the next 60ms redraw tick -- otherwise the
        # free-run loop can overwrite the navigated frame before the tick reads
        # it, which makes navigation look like it "did nothing").
        self._on_frame = on_frame
        self._running = True
        # While a nav's navigated frame is being shown, the free-run loop must
        # NOT re-publish a (possibly reverted) frame on top of it. _do_nav sets
        # this to a short deadline; _publish_fb from free-run is suppressed until
        # then so the navigated frame stays on screen long enough to be seen.
        self._nav_hold_until = 0.0
        # auto RX/TX follower (no-op w/o SDR). The bridge's RX reader thread must
        # NOT touch the core directly: give it an rx_sink that enqueues the
        # demodulated edges back onto THIS worker so only the worker thread ever
        # calls into the core (see post_rx_edges / _do_rx).
        self._bridge = hackrf.Bridge(rx_sink=self.post_rx_edges)
        # Last knock the user configured, re-applied across reloads.
        self._knock_seq: list = []
        self._knock_gate: int = -1

    # ---- GTK-thread API (thread-safe: only enqueues) ----------------------
    def post_nav(self, btn: int, hold: bool) -> None:
        """Queue an ATOMIC nav press (press+hold+release+settle) on the worker.

        hold=True is a long press. This is the canonical pattern the firmware's
        nav_reader expects (same as auto_unlock / pandora_tui.press_button); the
        worker runs the whole cycle as one unit so the action dispatches on the
        release edge and the framebuffer updates."""
        _dlog(f"post_nav ENQUEUE btn={btn} hold={hold}")
        self._cmds.put(("nav", int(btn), bool(hold)))

    def post_rx_edges(self, edges) -> None:
        """Queue demodulated RX edges for injection on the WORKER thread.

        Called from the HackRF RX reader thread. Routing the edges through the
        command queue (instead of calling emu.rx_inject() directly from the
        reader thread) guarantees that ONLY the worker thread ever touches the
        core, preserving the single-thread-owns-the-core invariant under real RX."""
        if edges:
            # Copy to a plain list so the reader thread's buffer can be reused.
            self._cmds.put(("rx", list(edges)))

    def post_knock(self, seq, gate: int) -> None:
        self._cmds.put(("knock", list(seq), int(gate)))

    def post_load(self, path: str) -> None:
        # Agnostic core: no profile argument any more.
        self._cmds.put(("load", path))

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
        # Ask the GTK thread to repaint NOW (idle_add marshals to the main loop).
        # Without this, a navigated frame produced by _do_nav would only reach
        # the glass on the next 60ms redraw tick, by which point the free-run
        # loop may have already overwritten it with the repainted base menu.
        if self._on_frame is not None:
            GLib.idle_add(self._on_frame)

    def _status(self, text: str) -> None:
        GLib.idle_add(self._on_status, text)

    def _do_load(self, path: str) -> None:
        try:
            # Agnostic core: single hardware model, no profile selection.
            emu = core.PandoraEmu()
            emu.load_hex(path)
            self._status(f"Booting {os.path.basename(path)}...")
            # Re-apply any configured knock so reloads keep auto-unlocking.
            if self._knock_seq:
                emu.set_knock(self._knock_seq, self._knock_gate)
            unlocked = emu.reset_unlock()
            self._emu = emu
            self._bridge.attach(emu)
            self._publish_fb()
            self._status("Ready" + ("" if unlocked else " (unlock not confirmed)"))
        except Exception as exc:  # noqa: BLE001 - surface any load failure
            self._status(f"Load failed: {exc}")

    # Instruction bursts run AFTER a nav so the firmware finishes repainting the
    # menu into the framebuffer before we publish it. core.nav() dispatches the
    # action on the release edge, but the firmware then needs a few poll cycles
    # to actually redraw the selected row; without this settle we would publish a
    # transient (half-cleared) frame and the real navigated menu would only show
    # up via the free-run loop -- where it races the base-menu repaint and often
    # loses. Measured on the G82: this makes the navigated frame the one the UI
    # shows, instead of a flicker that reverts to the base menu.
    NAV_SETTLE_BURSTS = 6
    # Keep the navigated frame on screen for at least this long before the
    # free-run loop is allowed to publish over it (seconds).
    NAV_HOLD_S = 0.18

    def _do_nav(self, btn: int, hold: bool) -> None:
        if self._emu is None or not self._emu.loaded:
            _dlog(f"_do_nav SKIP (no firmware) btn={btn} hold={hold}")
            return
        _dlog(f"_do_nav RUN btn={btn} hold={hold}")
        # ATOMIC press: core.nav() -> pe_nav -> emu_nav_button -> press_button_timed
        # drives the pin low, runs the firmware's poll bursts with the pin held,
        # lifts the pin, then runs the settle bursts -- all as one unit, so the
        # firmware dispatches the navigation action on the release edge. THIS is
        # why navigation registers (same cycle as auto_unlock / pandora_tui).
        self._emu.nav(btn, hold)
        # Let the firmware converge its menu repaint before we publish, so the
        # published frame is the navigated menu, not a transient clear.
        for _ in range(self.NAV_SETTLE_BURSTS):
            self._emu.run(INSNS_PER_STEP)
        self._publish_fb()
        # Protect this navigated frame from being immediately overwritten by the
        # free-run loop's publish (which tends to revert to the base menu).
        import time as _time
        self._nav_hold_until = _time.monotonic() + self.NAV_HOLD_S

    def _do_rx(self, edges) -> None:
        if self._emu is None or not self._emu.loaded:
            return
        # Inject each demodulated RX edge into the firmware's own demodulator
        # (PB0 + TIMER1). Runs on the worker thread so the core is only ever
        # touched here, even while the HackRF reader thread is streaming.
        for level, dur_us in edges:
            try:
                self._emu.rx_inject(level, dur_us)
            except Exception:
                break

    def _do_knock(self, seq, gate: int) -> None:
        self._knock_seq = list(seq)
        self._knock_gate = int(gate)
        if self._emu is None or not self._emu.loaded:
            self._status("Knock saved (load a firmware to unlock)")
            return
        try:
            self._emu.set_knock(self._knock_seq, self._knock_gate)
            unlocked = self._emu.reset_unlock()
            self._publish_fb()
            self._status("Unlocked" if unlocked else "Unlock not confirmed")
        except Exception as exc:  # noqa: BLE001
            self._status(f"Unlock failed: {exc}")

    def run(self) -> None:
        try:
            while self._running:
                # 1) Drain pending commands first — synchronous, take priority.
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
                        self._do_load(cmd[1])
                    elif kind == "nav":
                        self._do_nav(cmd[1], cmd[2])
                    elif kind == "rx":
                        self._do_rx(cmd[1])
                    elif kind == "knock":
                        self._do_knock(cmd[1], cmd[2])
                    continue

                # 2) Free-run the firmware a bit, then follow the radio + publish.
                if self._emu is not None and self._emu.loaded:
                    self._emu.run(INSNS_PER_STEP)
                    self._bridge.pump()  # TX firmware waveform / inject RX edges
                    # Do NOT publish over a freshly navigated frame: during the
                    # short post-nav hold window the firmware is still settling
                    # and a free-run publish here would overwrite the navigated
                    # menu with the reverted base menu (measured: ~13/15 navs lost
                    # on pmax/B_OK without this guard). The firmware keeps running;
                    # we just skip clobbering the published frame until the hold
                    # expires, then resume normal live publishing.
                    import time as _time
                    if _time.monotonic() >= self._nav_hold_until:
                        self._publish_fb()
                else:
                    # nothing loaded yet; avoid a busy spin
                    threading.Event().wait(0.05)
        finally:
            # Clean radio teardown so no hackrf_transfer is left streaming.
            try:
                self._bridge.shutdown()
            except Exception:
                pass


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
        self.set_content_width(440)   # 88*5
        self.set_content_height(320)  # 64*5
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

        # on_frame is called (marshalled to the GTK main loop) whenever the
        # worker publishes a new framebuffer, so a navigated frame repaints
        # immediately instead of waiting for the periodic redraw tick.
        self.worker = EmuWorker(on_status=self._set_status,
                                on_frame=self._on_frame)
        self.worker.start()

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        toolbar = Adw.ToolbarView()
        self.toasts.set_child(toolbar)
        self._toolbar = toolbar

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.status_lbl = Gtk.Label(label="No firmware loaded")
        self.status_lbl.add_css_class("dim-label")
        header.set_title_widget(self.status_lbl)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar.set_content(scroller)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.content.set_margin_top(16)
        self.content.set_margin_bottom(24)
        self.content.set_margin_start(12)
        self.content.set_margin_end(12)

        # Adw.Clamp caps the content measure on wide screens (tablet/desktop)
        # while letting it fill the full width on a narrow phone. Horizontal
        # scrolling is disabled above, so nothing can overflow sideways.
        clamp = Adw.Clamp()
        clamp.set_maximum_size(560)
        clamp.set_tightening_threshold(420)
        clamp.set_child(self.content)
        scroller.set_child(clamp)

        self._build_screen()
        # The keyfob pad is built as a FIXED bottom bar (outside the
        # ScrolledWindow), see _build_pad. On a touchscreen the ScrolledWindow's
        # kinetic/drag gesture used to steal the touch sequence from the pad's
        # button, so a finger tap never completed its nav. Keeping the pad out of
        # the scrollable area means its buttons never compete with scroll and are
        # always visible/accessible -- the standard mobile pattern.
        self._build_pad()
        self._build_unlock()
        self._build_firmware()
        self._build_hackrf_status()
        # The per-screen cheat-sheet is informational; keep it in the scrollable
        # content (it was previously appended from _build_pad).
        self._build_button_cheatsheet()

        # Narrow-phone breakpoint: tighten outer margins so the pad/chips get
        # every available pixel of the G82's narrow glass (the Clamp already
        # handles the wide case).
        try:
            bp = Adw.Breakpoint.new(
                Adw.BreakpointCondition.parse("max-width: 400sp"))
            bp.add_setter(self.content, "margin-start", 8)
            bp.add_setter(self.content, "margin-end", 8)
            self.add_breakpoint(bp)
        except Exception:
            # Older libadwaita without Breakpoint: the Clamp + vertical-only
            # scroll already keep the layout within the screen width.
            pass

        # redraw timer (GTK thread only reads the published framebuffer)
        self._last_fb = None
        GLib.timeout_add(REDRAW_MS, self._redraw_tick)

        self.connect("close-request", self._on_close)

    # ------------------------------------------------------------ screen ----
    def _build_screen(self) -> None:
        """The emulated OLED is the HERO of the window: a device-like slab
        (bezel + recessed glass) sitting at the top so the eye lands on the
        screen first. The DrawingArea render is untouched; only its framing
        around it is new."""
        device = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         halign=Gtk.Align.CENTER)
        device.add_css_class("pe-device")

        frame = Gtk.Frame()
        frame.add_css_class("pe-screen-frame")
        self.screen = KeyfobScreen(self.worker.latest_fb)
        frame.set_child(self.screen)
        device.append(frame)

        caption = Gtk.Label(label="P A N D O R A   D 6 5 0")
        caption.add_css_class("pe-device-caption")
        caption.set_halign(Gtk.Align.CENTER)
        device.append(caption)

        self.content.append(device)

    # ------------------------------------------------------------ pad -------
    def _build_pad(self) -> None:
        """The SIX physical keyfob buttons (B1..B6), exactly like the Flipper
        PandoraARM app. The keyfob has six numbered buttons; each maps to one
        GPIO pin and the UI labels them B1..B6 with the function from the fob's
        manual (same text as the Flipper app).

        Button -> core pin -> function (from the Flipper app BUTTON LAYOUT):
          B1 = UP  pin  -> On / menu / cell +
          B2 = OK  pin  -> confirm / mode (hold: erase cell)
          B3 = DOWN pin -> menu / cell -  (hold: auto)
          B4 = BACK pin -> RX frequency / close car
          B5 = B5  pin  -> AM+FM / open car
          B6 = B6  pin  -> trunk   (hold: back to menu)

        Each button holds its GPIO pin down for as long as you press it (real
        long-press), turning amber while held; the single KEYFOB_BUTTONS table
        is the source of truth for the label -> core pin mapping.

        TOUCH FIX: the pad is mounted as a FIXED bottom bar on the ToolbarView,
        OUTSIDE the ScrolledWindow. On a touchscreen (Plasma Mobile / Phosh) the
        ScrolledWindow runs a kinetic drag gesture that claims the touch
        sequence; a custom GestureClick inside it would lose its "released" edge
        (the scroll "ate" the gesture) so a finger tap never enqueued a nav. By
        keeping the pad outside the scroll AND using a real Gtk.Button (whose
        "clicked" handles the tap natively, exactly like the working Unlock
        button), the tap ALWAYS fires. A separate Gtk.GestureLongPress provides
        the hold without fighting the click.
        """
        # 2 columns x 3 rows, homogeneous + hexpand so the pad always fills the
        # phone width (and never overflows) while staying a comfortable height.
        grid = Gtk.Grid(row_spacing=10, column_spacing=10,
                        hexpand=True,
                        column_homogeneous=True, row_homogeneous=True)
        grid.add_css_class("pe-pad-bar")
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        grid.set_margin_start(10)
        grid.set_margin_end(10)

        # B1/B2/B3 on the left (side buttons), B4/B5/B6 on the right (top
        # buttons), just like the real fob.
        for btn, name, subtitle, col, row in KEYFOB_BUTTONS:
            grid.attach(self._pad_button(btn, name, subtitle), col, row, 1, 1)

        # Mount the pad as a FIXED bottom bar so it never scrolls and its buttons
        # never compete with the ScrolledWindow's touch/drag gesture.
        self._toolbar.add_bottom_bar(grid)

    def _build_button_cheatsheet(self) -> None:
        """Collapsible cheat-sheet: what each button does per screen. The
        firmware changes button functions by screen, so this is the real
        reference."""
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

    def _pad_button(self, btn: int, name: str, subtitle: str) -> Gtk.Button:
        """A big touch button for a keyfob button (B1..B6) with a REAL hold.

        TOUCH-ROBUST DESIGN (the fix): the button is a real Gtk.Button and the
        SHORT tap rides its native "clicked" signal -- exactly like the Unlock
        button, which always worked on the touchscreen. Gtk.Button resolves the
        press/release of a finger tap internally and is NOT defeated by a
        ScrolledWindow's drag gesture (and the pad now lives outside the scroll
        anyway), so a finger tap ALWAYS enqueues one atomic nav (hold=False).

        The LONG press (hold=True) comes from a SEPARATE Gtk.GestureLongPress
        attached to the same button. When the finger is held past LONG_PRESS_MS
        the gesture fires, claims the sequence (which cancels the pending
        Gtk.Button click), enqueues the atomic nav with hold=True, and marks the
        interaction so the suppressed/duplicate click is ignored.

        Each interaction still yields EXACTLY ONE atomic nav command
        (press+hold+release+settle) -- the canonical cycle the firmware's
        nav_reader expects (same path as auto_unlock / pandora_tui.press_button).

        Amber feedback: a GestureLongPress "pressed" turns the button amber while
        the finger is down and "end" clears it; a plain tap also gets a brief
        amber flash via the click path so there is always visible feedback.
        `name` (B1..B6) labels it; `subtitle` is the manual function.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

        # Glyph hint for the directional / confirm / back buttons so they are
        # recognisable at a glance (B5/B6 have no universal glyph -> name only).
        glyph = PAD_GLYPHS.get(btn)
        title = f"{glyph}  {name}" if glyph else name
        lbl = Gtk.Label(label=title)
        lbl.add_css_class("pe-pad-name")
        box.append(lbl)
        sub = Gtk.Label(label=subtitle)
        sub.add_css_class("pe-pad-sub")
        sub.set_wrap(True)
        sub.set_justify(Gtk.Justification.CENTER)
        sub.set_max_width_chars(16)
        box.append(sub)

        b = Gtk.Button(child=box, valign=Gtk.Align.FILL,
                       hexpand=True, height_request=60)
        b.add_css_class("pe-pad")

        # "long_fired" = this interaction already enqueued a LONG nav via the
        #                long-press gesture, so the Gtk.Button's "clicked" (which
        #                GTK may still emit on release) must NOT enqueue a tap too.
        state = {"long_fired": False, "flash": 0}

        def clear_amber():
            b.remove_css_class("pe-pad-active")
            state["flash"] = 0
            return False

        # ---- SHORT tap: native Gtk.Button click (robust on touch) ----------
        def on_clicked(_btn):
            _dlog(f"pad CLICKED name={name} btn={btn} long_fired={state['long_fired']}")
            if state["long_fired"]:
                # The long-press already handled this interaction; swallow the
                # trailing click so we do not double-fire.
                state["long_fired"] = False
                return
            # Brief amber flash so a quick tap still shows feedback.
            b.add_css_class("pe-pad-active")
            if state["flash"]:
                GLib.source_remove(state["flash"])
            state["flash"] = GLib.timeout_add(140, clear_amber)
            self.worker.post_nav(btn, False)
            self._toast(f"{name} tap")

        b.connect("clicked", on_clicked)

        # ---- LONG hold: separate GestureLongPress (coexists with click) ----
        def on_long_pressed(_g, _x, _y):
            # Finger held past the threshold -> LONG press. Claiming the gesture
            # cancels the Gtk.Button's pending click so only ONE nav is sent.
            _dlog(f"pad LONGPRESS name={name} btn={btn}")
            state["long_fired"] = True
            b.add_css_class("pe-pad-active")
            self.worker.post_nav(btn, True)
            self._toast(f"{name} LONG (held)")

        def on_long_begin(gesture, _seq):
            # Finger down: show amber immediately for responsive feedback.
            b.add_css_class("pe-pad-active")

        def on_long_end(gesture, _seq):
            # Finger up / gesture over: clear amber (the flash timer, if any,
            # will also no-op).
            GLib.timeout_add(60, clear_amber)

        long_press = Gtk.GestureLongPress()
        long_press.set_delay_factor(1.0)
        # Touch-only would ignore mouse; keep it unset so it also works with a
        # stylus/mouse during testing. Claim on long-press so it wins the click.
        long_press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        long_press.connect("pressed", on_long_pressed)
        long_press.connect("begin", on_long_begin)
        long_press.connect("end", on_long_end)
        b.add_controller(long_press)

        self._pad_widgets[btn] = b
        return b

    # ------------------------------------------------------------ unlock -----
    def _build_unlock(self) -> None:
        """Knock-code / unlock panel (like the newer Flipper apps): enter the
        button sequence (the "knock"), optionally a final held gate button, and
        hit Unlock. The worker calls core.set_knock(seq, gate) + reset_unlock().

        Fallback: leave the sequence empty and just drive the six buttons above
        by hand to unlock manually -- the knock is only an auto-unlock helper.
        """
        self._knock_seq: list = []
        self._knock_gate: int = -1

        group = Adw.PreferencesGroup(
            title="Knock-code / Unlock",
            description="Tap the buttons below to build an unlock sequence, then "
                        "Unlock. Or leave it empty and unlock by hand with the "
                        "buttons above.")

        # Sequence readout: its own wrapping, monospace label (NOT the ActionRow
        # subtitle) so a long knock flows onto multiple lines instead of
        # widening the window past the phone's screen.
        seq_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                          hexpand=True)
        self.seq_lbl = Gtk.Label(label="(empty)", xalign=0.0)
        self.seq_lbl.add_css_class("pe-seq")
        self.seq_lbl.add_css_class("pe-seq-empty")
        self.seq_lbl.set_wrap(True)
        self.seq_lbl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.seq_lbl.set_hexpand(True)
        seq_box.append(self.seq_lbl)

        self.knock_row = Adw.ActionRow(title="Sequence")
        clear_btn = Gtk.Button(
            icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Clear the sequence")
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", lambda _b: self._knock_clear())
        self.knock_row.add_suffix(clear_btn)
        group.add(self.knock_row)
        group.add(self._wrap(seq_box))

        # "Add button" chips in a FlowBox: they WRAP onto as many rows as the
        # width needs (homogeneous, no selection), so the six chips always fit
        # the phone width and never overflow horizontally.
        chip_flow = Gtk.FlowBox()
        chip_flow.add_css_class("pe-chip-flow")
        chip_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        chip_flow.set_homogeneous(True)
        chip_flow.set_max_children_per_line(3)
        chip_flow.set_min_children_per_line(2)
        chip_flow.set_row_spacing(6)
        chip_flow.set_column_spacing(6)
        chip_flow.set_hexpand(True)
        for btn, bname, *_ in KEYFOB_BUTTONS:
            chip = Gtk.Button(label=f"+ {bname}", valign=Gtk.Align.CENTER,
                              hexpand=True)
            chip.add_css_class("pe-chip")
            chip.set_tooltip_text(f"Add {bname} to the unlock sequence")
            chip.connect("clicked", lambda _b, x=btn: self._knock_add(x))
            chip_flow.insert(chip, -1)
        group.add(self._wrap(chip_flow))

        # Gate selector (final held button) + Unlock action.
        self.gate_row = Adw.ComboRow(title="Gate (final held button)")
        gate_model = Gtk.StringList()
        self._gate_options = [-1] + [b for b, *_ in KEYFOB_BUTTONS]
        gate_model.append("(none)")
        for b, bname, *_ in KEYFOB_BUTTONS:
            gate_model.append(bname)
        self.gate_row.set_model(gate_model)
        self.gate_row.set_selected(0)
        self.gate_row.connect("notify::selected", self._on_gate_changed)
        group.add(self.gate_row)

        unlock_row = Adw.ActionRow(
            title="Unlock",
            subtitle="Apply the knock and reset the firmware to unlock")
        unlock_btn = Gtk.Button(label="Unlock", valign=Gtk.Align.CENTER)
        unlock_btn.add_css_class("suggested-action")
        unlock_btn.add_css_class("pill")
        unlock_btn.connect("clicked", lambda _b: self._do_unlock())
        unlock_row.add_suffix(unlock_btn)
        group.add(unlock_row)

        self.content.append(group)

    def _knock_label(self) -> str:
        if not self._knock_seq:
            return "(empty)"
        return " \u2192 ".join(BTN_LABEL.get(b, str(b)) for b in self._knock_seq)

    def _knock_refresh(self) -> None:
        """Update the wrapping sequence readout and its empty-state styling."""
        empty = not self._knock_seq
        self.seq_lbl.set_label(self._knock_label())
        if empty:
            self.seq_lbl.add_css_class("pe-seq-empty")
        else:
            self.seq_lbl.remove_css_class("pe-seq-empty")

    def _knock_add(self, btn: int) -> None:
        self._knock_seq.append(btn)
        self._knock_refresh()

    def _knock_clear(self) -> None:
        self._knock_seq = []
        self._knock_refresh()

    def _on_gate_changed(self, row, _param) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._gate_options):
            self._knock_gate = self._gate_options[idx]

    def _do_unlock(self) -> None:
        # Push knock + unlock onto the worker (it owns the core).
        self.worker.post_knock(self._knock_seq, self._knock_gate)
        if self._knock_seq:
            self._toast("Unlocking with knock: " + self._knock_label())
        else:
            self._toast("Resetting (no knock) \u2014 unlock by hand")

    # ------------------------------------------------------------ firmware --
    def _build_firmware(self) -> None:
        group = Adw.PreferencesGroup(title="Firmware")

        self.fw_row = Adw.ActionRow(title="Firmware image",
                                    subtitle="No firmware loaded")
        load_btn = Gtk.Button(label="Open \u2026", valign=Gtk.Align.CENTER)
        load_btn.add_css_class("suggested-action")
        load_btn.add_css_class("pill")
        load_btn.connect("clicked", self._on_load_clicked)
        self.fw_row.add_suffix(load_btn)
        group.add(self.fw_row)

        # NOTE (agnostic core): the Profile selector (ComboRow + _on_profile_changed)
        # was REMOVED. The emulator now runs ANY .hex on a single hardware model;
        # there is no per-firmware profile to pick.
        self.content.append(group)

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
        self.worker.post_load(path)

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

    def _on_frame(self) -> bool:
        """Immediate repaint request from the worker (runs on the GTK thread via
        GLib.idle_add). Called right after the worker publishes a framebuffer --
        notably right after a nav settles -- so the navigated menu reaches the
        glass at once instead of waiting up to REDRAW_MS for the next tick."""
        screen = getattr(self, "screen", None)
        if screen is not None:
            screen.queue_draw()
        return False  # one-shot (idle_add)

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
