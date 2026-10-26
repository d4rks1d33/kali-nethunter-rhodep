"""HackRF bridge: a faithful, real-time replacement for the keyfob's Si4432.

The emulated firmware drives a *virtual* Si4432: it selects the band, the
modulation (OOK / FSK / GFSK) and the operating mode (idle / ready / rx / tx),
exactly as it would on real silicon. This module makes a HackRF behave like that
Si4432 so the firmware's radio works unmodified:

  * RX  -- a long-running ``hackrf_transfer -r`` streams raw int8 I/Q over a pipe
           into a dedicated reader thread. We demodulate in blocks with numpy
           (magnitude, adaptive threshold, run-length-encode) and inject the
           recovered edges into the firmware in real time via ``rx_inject`` so
           the firmware's own demodulator runs on them (PB0 + TIMER1).
  * TX  -- when the firmware enters TX mode it bit-bangs PB3; we drain those
           edges (``tx_pop_edges``), render them into an I/Q waveform honouring
           the modulation, and run ``hackrf_transfer -t`` from a pipe.

The HackRF is **half-duplex**: it cannot RX and TX at once. We arbitrate off the
firmware's reported RF mode (``core.rf_mode()``): listen continuously by default,
and when the firmware goes to TX we stop RX, transmit, then resume RX.

The tuned frequency follows the firmware's Si4432 registers (``core.rf_freq()``),
and the modulation follows ``core.rf_mod()`` -- nothing is hard-coded to 433.92.

The module degrades gracefully: with no HackRF (or no CLI tools) present the app
is still a fully working visual emulator; the bridge just does nothing on the
radio side while still draining the firmware's edge queues.

The active backend is the ``hackrf_transfer`` CLI in *streaming* mode (one
long-lived process per direction, pipes, NOT a process per capture). This keeps
latency low enough for real-time demod with the phone's spare headroom.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from . import core

# ---------------------------------------------------------------- config ------

# Default radio parameters. The sample rate is the RX/TX baseband rate; 2 Msps
# gives ~0.5 us resolution, comfortably finer than the firmware's ~400 us cell.
DEFAULT_SAMPLE_RATE = 2_000_000       # 2 Msps
DEFAULT_TX_GAIN = 20                  # hackrf_transfer -x (TX VGA, 0..47 dB)
DEFAULT_RX_LNA = 32                   # -l (LNA gain, 0..40 dB, steps of 8)
DEFAULT_RX_VGA = 20                   # -g (RX VGA, 0..62 dB, steps of 2)

# Fallback carrier used only until the firmware has programmed the Si4432 (its
# reported rf_freq() is 0 before the band is selected). 433.92 is the ISM band
# most Pandora keyfobs default to.
FALLBACK_FREQ_HZ = 433_920_000

# OOK/ASK carrier amplitude in the int8 range. 100 leaves headroom below 127.
TX_AMPLITUDE = 100

# FSK deviation (Hz) when rf_mod() reports FSK/GFSK. The Si4432's typical narrow
# deviation; mark/space are +dev / -dev around the tuned centre.
FSK_DEVIATION_HZ = 30_000

# RX demod tuning.
RX_BLOCK_SAMPLES = 131_072   # samples per numpy demod block (power of two)
RX_MIN_EDGE_US = 50          # merge shorter run-length edges as glitches
RX_THRESHOLD_FRAC = 0.5      # adaptive threshold = noise + frac*(peak-noise)


@dataclass
class HackRFStatus:
    present: bool
    detail: str


# ---------------------------------------------------------------- detection ---

def _have_cli() -> bool:
    return shutil.which("hackrf_transfer") is not None


def detect() -> HackRFStatus:
    """Probe for a HackRF via hackrf_info. Never raises."""
    info = shutil.which("hackrf_info")
    if info is None:
        return HackRFStatus(False, "hackrf tools not installed")
    try:
        out = subprocess.run(
            [info], capture_output=True, text=True, timeout=5)
    except Exception as e:  # noqa: BLE001
        return HackRFStatus(False, f"hackrf_info failed: {e}")
    text = (out.stdout or "") + (out.stderr or "")
    if "Serial number" in text or "Found HackRF" in text:
        serial = ""
        for line in text.splitlines():
            if "Serial number" in line:
                serial = line.split(":", 1)[-1].strip()
                break
        return HackRFStatus(True, f"HackRF detected {serial}".strip())
    if "No HackRF boards found" in text:
        return HackRFStatus(False, "no HackRF connected")
    return HackRFStatus(False, "HackRF not detected")


# ---------------------------------------------------------------- TX modulate -

def edges_to_iq(
    edges: List[Tuple[int, int]],
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: int = TX_AMPLITUDE,
    mod: int = core.MOD_OOK,
    fsk_deviation_hz: int = FSK_DEVIATION_HZ,
) -> bytes:
    """Render (level, duration_us) edges into interleaved int8 I/Q.

    hackrf_transfer wants signed 8-bit interleaved I,Q,I,Q,... The modulation is
    chosen from ``mod`` (core.MOD_*):

      * OOK/ASK (and UNMOD) -- carrier ON (full amplitude DC in baseband, which
        is a tone at the tuned centre once up-converted) while level==1, zero
        while level==0. This is the classic Pandora OOK.
      * FSK/GFSK -- a constant-amplitude carrier whose baseband frequency hops
        to +deviation for level==1 and -deviation for level==0 (2-FSK). GFSK is
        approximated as plain FSK here (no Gaussian pulse shaping) which is still
        demodulable; the firmware's slicer only needs mark/space separation.

    The per-edge microseconds come straight from the firmware's bit-banging, so
    the cell timing is reproduced faithfully; only the carrier shape differs by
    modulation.
    """
    amp = max(1, min(127, int(amplitude)))
    samples_per_us = sample_rate / 1_000_000.0
    if not edges:
        return b""

    # Per-edge sample counts.
    counts = np.array(
        [max(0, int(round(d * samples_per_us))) for _, d in edges],
        dtype=np.int64)
    levels = np.array([1 if l else 0 for l, _ in edges], dtype=np.int8)
    total = int(counts.sum())
    if total <= 0:
        return b""

    # Expand the per-edge level into a per-sample level vector.
    lvl_per_sample = np.repeat(levels, counts)

    if mod in (core.MOD_FSK, core.MOD_GFSK):
        # 2-FSK: instantaneous baseband frequency = +/- deviation.
        dev = float(fsk_deviation_hz)
        inst_f = np.where(lvl_per_sample > 0, dev, -dev).astype(np.float64)
        # Integrate frequency -> phase (continuous phase FSK).
        dt = 1.0 / float(sample_rate)
        phase = np.cumsum(2.0 * np.pi * inst_f * dt)
        i = np.round(amp * np.cos(phase)).astype(np.int8)
        q = np.round(amp * np.sin(phase)).astype(np.int8)
    else:
        # OOK/ASK/UNMOD: carrier on (I=amp,Q=0) while high, zero while low.
        on = lvl_per_sample > 0
        i = np.where(on, np.int8(amp), np.int8(0)).astype(np.int8)
        q = np.zeros(total, dtype=np.int8)

    iq = np.empty(total * 2, dtype=np.int8)
    iq[0::2] = i
    iq[1::2] = q
    return iq.tobytes()


# ---------------------------------------------------------------- RX demod ----

def iq_to_edges(
    iq,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    threshold: Optional[float] = None,
    min_us: int = RX_MIN_EDGE_US,
) -> List[Tuple[int, int]]:
    """Demodulate interleaved int8 I/Q into (level, duration_us) edges (OOK).

    Fully vectorised with numpy:
      1. view the byte buffer as int8, split into I and Q,
      2. compute magnitude |I/Q| (envelope -- works for OOK and, good enough,
         for FSK presence detection),
      3. threshold adaptively (noise floor + frac*(peak-noise)) into a 0/1
         level vector,
      4. run-length-encode the level transitions into (level, duration_us),
      5. merge sub-min_us runs into their predecessor to suppress glitches.

    Accepts either raw ``bytes`` or a numpy int8 array (so the streaming reader
    can pass a slice without an extra copy).
    """
    if iq is None:
        return []
    if isinstance(iq, (bytes, bytearray, memoryview)):
        data = np.frombuffer(bytes(iq), dtype=np.int8)
    else:
        data = np.asarray(iq, dtype=np.int8)
    # Need an even number of samples (I,Q pairs).
    n_pairs = data.size // 2
    if n_pairs == 0:
        return []
    data = data[: n_pairs * 2]
    i = data[0::2].astype(np.float32)
    q = data[1::2].astype(np.float32)
    mag = np.sqrt(i * i + q * q)

    peak = float(mag.max())
    if peak <= 0.0:
        return []
    if threshold is not None:
        thr = float(threshold)
    else:
        # Noise floor estimate from the lower envelope; threshold sits between
        # the noise floor and the peak so weak-but-present signals still slice.
        noise = float(np.percentile(mag, 20.0))
        thr = noise + RX_THRESHOLD_FRAC * (peak - noise)
        if thr <= noise:
            thr = peak * 0.5

    level = (mag >= thr).astype(np.int8)

    # Run-length encode: find transition indices.
    us_per_sample = 1_000_000.0 / sample_rate
    change = np.nonzero(np.diff(level))[0] + 1
    # Boundaries of each run: [0, change..., len]
    bounds = np.concatenate(([0], change, [level.size]))
    run_lens = np.diff(bounds)
    run_levels = level[bounds[:-1]]
    run_us = np.round(run_lens * us_per_sample).astype(np.int64)

    edges: List[Tuple[int, int]] = []
    for lvl, dur in zip(run_levels.tolist(), run_us.tolist()):
        if dur <= 0:
            continue
        if edges and dur < min_us:
            plvl, pdur = edges[-1]
            edges[-1] = (plvl, pdur + dur)
        else:
            edges.append((int(lvl), int(dur)))
    return edges


# ---------------------------------------------------------------------------
#  Streaming RX reader — a dedicated thread owning one long-lived
#  `hackrf_transfer -r` process, demodulating blocks and calling a sink.
# ---------------------------------------------------------------------------
class _RxStreamer:
    """Owns a long-running ``hackrf_transfer -r - `` reading raw int8 I/Q from
    stdout, demodulates it in numpy blocks, and invokes ``on_edges(edges)`` for
    each block. Start/stop are idempotent and never raise.
    """

    def __init__(self, sample_rate: int, freq_hz: int,
                 lna: int, vga: int, on_edges) -> None:
        self._sample_rate = sample_rate
        self._freq_hz = freq_hz
        self._lna = lna
        self._vga = vga
        self._on_edges = on_edges
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        if not _have_cli():
            return False
        self._stop.clear()
        cmd = [
            "hackrf_transfer",
            "-r", "-",                     # stream raw I/Q to stdout
            "-f", str(int(self._freq_hz)),
            "-s", str(int(self._sample_rate)),
            "-l", str(int(self._lna)),
            "-g", str(int(self._vga)),
            # No -n: run until we kill it (continuous streaming).
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0)
        except Exception:
            self._proc = None
            return False
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    proc.kill()
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._proc = None
        self._thread = None

    def _reader(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        block_bytes = RX_BLOCK_SAMPLES * 2  # 2 int8 per complex sample
        try:
            while not self._stop.is_set():
                buf = proc.stdout.read(block_bytes)
                if not buf:
                    break  # process ended / pipe closed
                try:
                    edges = iq_to_edges(buf, self._sample_rate)
                except Exception:
                    edges = []
                if edges:
                    try:
                        self._on_edges(edges)
                    except Exception:
                        pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Blocking TX — one short-lived `hackrf_transfer -t` fed from a pipe.
# ---------------------------------------------------------------------------
def transmit(
    edges: List[Tuple[int, int]],
    freq_hz: int,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    tx_gain: int = DEFAULT_TX_GAIN,
    amplitude: int = TX_AMPLITUDE,
    mod: int = core.MOD_OOK,
) -> Tuple[bool, str]:
    """Transmit the given edges as one burst via ``hackrf_transfer -t -``.

    The I/Q is rendered with ``edges_to_iq`` (honouring ``mod``) and piped to
    hackrf_transfer's stdin, so no temp file and no per-sample Python loop.
    Returns (ok, message); never raises.
    """
    if not _have_cli():
        return False, "hackrf_transfer not installed"
    if not edges:
        return False, "nothing to transmit (empty TX ring)"

    iq = edges_to_iq(edges, sample_rate, amplitude, mod)
    if not iq:
        return False, "nothing to transmit (zero-length waveform)"

    cmd = [
        "hackrf_transfer",
        "-t", "-",                 # read I/Q from stdin
        "-f", str(int(freq_hz)),
        "-s", str(int(sample_rate)),
        "-a", "1",                 # enable the TX/RX amp
        "-x", str(int(tx_gain)),   # TX VGA gain
    ]
    try:
        proc = subprocess.run(
            cmd, input=iq, capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return False, f"hackrf_transfer failed: {e}"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or b"")
        try:
            msg = msg.decode(errors="replace")
        except Exception:
            msg = str(msg)
        return False, (msg or f"hackrf_transfer rc={proc.returncode}").strip()
    return True, f"transmitted {len(edges)} edges @ {freq_hz/1e6:.3f} MHz"


# ---------------------------------------------------------------------------
#  Bridge — the AUTOMATIC, half-duplex radio follower.
# ---------------------------------------------------------------------------
# The firmware does everything: it selects the band/frequency, the modulation,
# and decides when to transmit vs receive. The bridge does NOT configure the
# radio; it follows the firmware's virtual Si4432:
#
#   * RX (default): keep a long-lived `hackrf_transfer -r` streaming, demodulate
#     blocks with numpy, and inject the recovered edges into the firmware with
#     rx_inject() in real time. Re-tuned if the firmware changes rf_freq().
#   * TX (when rf_mode() == RF_TX): stop RX (half-duplex), drain the firmware's
#     bit-banged edges, render + transmit them, then resume RX.
#
# pump() is called from the single emulator worker thread each step, so only one
# thread ever touches the core on the TX side. The RX reader thread must NOT call
# into the core directly; instead it forwards its demodulated edges to rx_sink
# (the worker's post_rx_edges), and the WORKER injects them (core.rx_inject) on
# its own thread. So only one thread ever touches the core on the RX side too.
# The bridge degrades to a no-op when no HackRF is present (still draining the TX
# queue so it never grows unbounded).
class Bridge:
    def __init__(self, rx_sink=None) -> None:
        self._emu = None
        self._present = False
        self._freq_hz = FALLBACK_FREQ_HZ
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._min_tx_edges = 8   # ignore tiny spurious bursts
        self._rx: Optional[_RxStreamer] = None
        self._rx_mode_active = False
        self._lock = threading.Lock()
        # Sink for demodulated RX edges. The RX reader thread calls this instead
        # of touching the core; the worker thread drains it into core.rx_inject so
        # a single thread owns the core. None -> drop RX edges (visual-only mode).
        self._rx_sink = rx_sink
        try:
            self._present = detect().present
        except Exception:
            self._present = False

    def attach(self, emu) -> None:
        """Bind to a freshly-loaded emulator and (re)probe the SDR."""
        self.stop_rx()
        self._emu = emu
        self._rx_mode_active = False
        try:
            self._present = detect().present
        except Exception:
            self._present = False

    # -- RX streamer lifecycle --------------------------------------------
    def _rx_on_edges(self, edges: List[Tuple[int, int]]) -> None:
        """Called from the RX reader thread. It must NOT touch the core from this
        thread (that would race the worker). Instead it forwards the demodulated
        edges to rx_sink (the worker's post_rx_edges), and the WORKER thread
        injects them via core.rx_inject (PB0 + TIMER1) so the firmware's own
        demodulator runs on the real edge stream -- single-thread-owns-the-core."""
        if not edges:
            return
        sink = self._rx_sink
        if sink is None:
            return  # no worker wired up (visual-only): drop RX edges
        emu = self._emu
        if emu is None or not getattr(emu, "loaded", False):
            return
        try:
            sink(edges)   # enqueue onto the worker; worker calls rx_inject
        except Exception:
            return

    def start_rx(self) -> None:
        if not self._present:
            return
        with self._lock:
            if self._rx is not None and self._rx.running:
                return
            self._rx = _RxStreamer(
                self._sample_rate, self._freq_hz,
                DEFAULT_RX_LNA, DEFAULT_RX_VGA, self._rx_on_edges)
            self._rx.start()

    def stop_rx(self) -> None:
        with self._lock:
            if self._rx is not None:
                self._rx.stop()
                self._rx = None

    def _retune_rx_if_needed(self, freq_hz: int) -> None:
        """If the firmware changed frequency, restart the RX streamer on it."""
        if freq_hz <= 0 or freq_hz == self._freq_hz:
            return
        self._freq_hz = int(freq_hz)
        if self._rx is not None and self._rx.running:
            self.stop_rx()
            self.start_rx()

    # -- the per-step follower --------------------------------------------
    def pump(self) -> None:
        """Called every worker step. Arbitrate half-duplex off rf_mode():

          * follow the firmware's tuned frequency,
          * RF_TX  -> stop RX, drain + transmit the bit-banged edges, resume RX,
          * otherwise -> keep RX streaming (RF_RX/RF_READY/RF_IDLE all listen).

        Always drains the TX edge queue so it never grows unbounded, even with
        no SDR present.
        """
        emu = self._emu
        if emu is None or not getattr(emu, "loaded", False):
            return

        # Follow the firmware-selected frequency (0 until it programs the band).
        try:
            fw_freq = emu.rf_freq()
        except Exception:
            fw_freq = 0
        if fw_freq > 0:
            self._retune_rx_if_needed(fw_freq)

        # Firmware's current RF mode drives half-duplex arbitration.
        try:
            mode = emu.rf_mode()
        except Exception:
            mode = core.RF_IDLE

        # Current modulation (OOK vs FSK) for faithful TX rendering.
        try:
            mod = emu.rf_mod()
        except Exception:
            mod = core.MOD_OOK

        if mode == core.RF_TX:
            # Half-duplex: must not RX while transmitting.
            if self._rx is not None and self._rx.running:
                self.stop_rx()
            self._rx_mode_active = False
            self._do_tx(emu, mod)
        else:
            # Any non-TX mode: listen. Drain any stray TX edges first so the
            # ring never backs up, but only key the radio in TX mode.
            try:
                stray = emu.tx_pop_edges()
            except Exception:
                stray = []
            if stray and len(stray) >= self._min_tx_edges and self._present:
                # The firmware emitted a burst outside RF_TX (edge case); send it.
                self._transmit(stray, mod)
            if self._present and not self._rx_mode_active:
                self.start_rx()
                # Only mark RX active if the streamer actually came up (CLI
                # present + process launched); otherwise retry next pump.
                self._rx_mode_active = (
                    self._rx is not None and self._rx.running)

    def _do_tx(self, emu, mod: int) -> None:
        try:
            edges = emu.tx_pop_edges()
        except Exception:
            return
        if not edges or len(edges) < self._min_tx_edges:
            return
        if not self._present:
            return  # drained the queue; no radio to key
        self._transmit(edges, mod)

    def _transmit(self, edges: List[Tuple[int, int]], mod: int) -> None:
        try:
            transmit(edges, self._freq_hz, self._sample_rate, mod=mod)
        except Exception:
            pass

    def shutdown(self) -> None:
        """Stop the RX streamer cleanly (call on app close)."""
        self.stop_rx()
