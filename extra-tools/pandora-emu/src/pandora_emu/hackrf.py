"""HackRF bridge: turn emulator TX edges into an OOK waveform and transmit it,
and (RX) demodulate a captured IQ file back into clean edges for the emulator.

Two backends:

  * PRIMARY (active): the hackrf_transfer / hackrf_info CLI tools. We synthesise
    an int8 interleaved I/Q file (the format hackrf_transfer expects), ASK/OOK
    modulated (carrier ON while the edge level is 1), and shell out to
    hackrf_transfer -t for TX. For RX we capture with hackrf_transfer -r, take
    the magnitude of the IQ, threshold it, and recover level/duration edges.

  * FUTURE (present but DISABLED): direct libhackrf bindings via the `pyhackrf`
    package. The code is written below behind USE_PYHACKRF (default False) and
    inside a commented block. To enable: `pip install pyhackrf` and flip the
    flag / uncomment. See PyHackRFBridge at the bottom of this file.

The module degrades gracefully: if no HackRF (or no CLI tools) is present, the
app still works as a pure visual emulator; TX/RX just report "no device".

TX waveform parameters come from RE_TX_FRAME_MODEL_D605.md: PB3 HIGH = carrier
ON, LOW = OFF; base cell 400us (pandora) / 500us (pmax); the emulator already
hands us exact per-edge microseconds, so we only need to render carrier on/off.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- config ------

# Flip to True and install `pyhackrf` to use the direct-binding backend below.
# Kept False so the app has zero hard dependency on libhackrf.
USE_PYHACKRF = False

# Default radio parameters.
DEFAULT_SAMPLE_RATE = 2_000_000       # 2 Msps
DEFAULT_TX_GAIN = 20                  # hackrf_transfer -x (TX VGA, 0..47 dB)
DEFAULT_RX_LNA = 32                   # -l (LNA gain, 0..40 dB, steps of 8)
DEFAULT_RX_VGA = 20                   # -g (RX VGA, 0..62 dB, steps of 2)

# Bands the keyfob/UI exposes (Hz). 433.92 is the default ISM; the others come
# from RE_RADIO_D605.md (per-brand bands). The UI picks one of these.
BANDS = {
    "433.92 MHz": 433_920_000,
    "434.42 MHz": 434_420_000,
    "868.35 MHz": 868_350_000,
    "313.87 MHz": 313_870_000,
}
DEFAULT_BAND = "433.92 MHz"


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
        # Extract a short identifier if present.
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
    amplitude: int = 100,
) -> bytes:
    """Render (level, duration_us) edges into interleaved int8 I/Q (OOK/ASK).

    Carrier ON (full amplitude DC in baseband -> a tone at the tuned centre
    once up-converted) while level==1, zero while level==0. hackrf_transfer
    expects signed 8-bit interleaved I,Q,I,Q,... We emit a constant-amplitude
    "on" (I=amplitude, Q=0) and zeros for "off" -- the simplest faithful OOK.

    amplitude is clamped to the int8 range; 100 leaves headroom below 127.
    """
    amp = max(1, min(127, int(amplitude)))
    samples_per_us = sample_rate / 1_000_000.0
    out = bytearray()
    for level, dur_us in edges:
        n = int(round(dur_us * samples_per_us))
        if n <= 0:
            continue
        if level:
            # On: I=amp, Q=0 repeated.
            chunk = struct.pack("bb", amp, 0) * n
        else:
            chunk = b"\x00\x00" * n
        out.extend(chunk)
    return bytes(out)


def transmit(
    edges: List[Tuple[int, int]],
    freq_hz: int,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    tx_gain: int = DEFAULT_TX_GAIN,
    amplitude: int = 100,
) -> Tuple[bool, str]:
    """Transmit the given edges as an OOK burst via hackrf_transfer.

    Returns (ok, message). Never raises; a missing device / tool is reported in
    the message so the GUI can toast it.
    """
    if USE_PYHACKRF:
        return _pyhackrf_transmit(edges, freq_hz, sample_rate, tx_gain, amplitude)
    if not _have_cli():
        return False, "hackrf_transfer not installed"
    if not edges:
        return False, "nothing to transmit (empty TX ring)"

    iq = edges_to_iq(edges, sample_rate, amplitude)
    tmp = tempfile.NamedTemporaryFile(
        prefix="pandora_tx_", suffix=".iq", delete=False)
    try:
        tmp.write(iq)
        tmp.close()
        cmd = [
            "hackrf_transfer",
            "-t", tmp.name,
            "-f", str(int(freq_hz)),
            "-s", str(int(sample_rate)),
            "-a", "1",                 # enable the TX/RX amp
            "-x", str(int(tx_gain)),   # TX VGA gain
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:  # noqa: BLE001
            return False, f"hackrf_transfer failed: {e}"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or
                           f"hackrf_transfer rc={proc.returncode}").strip()
        return True, f"transmitted {len(edges)} edges @ {freq_hz/1e6:.3f} MHz"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ---------------------------------------------------------------- RX demod ----

def iq_to_edges(
    iq: bytes,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    threshold: Optional[float] = None,
    min_us: int = 50,
) -> List[Tuple[int, int]]:
    """Demodulate interleaved int8 I/Q into (level, duration_us) edges (OOK).

    Takes |I/Q| magnitude, thresholds it (auto = half the peak if not given),
    and run-length-encodes the resulting on/off into edges. Edges shorter than
    min_us are merged into the previous one to suppress noise glitches.
    """
    if not iq:
        return []
    # Parse interleaved signed bytes into magnitude per sample.
    n = len(iq) // 2
    mags = []
    for i in range(n):
        ii = struct.unpack_from("b", iq, 2 * i)[0]
        qq = struct.unpack_from("b", iq, 2 * i + 1)[0]
        mags.append((ii * ii + qq * qq) ** 0.5)
    if not mags:
        return []

    peak = max(mags)
    if peak <= 0:
        return []
    thr = threshold if threshold is not None else peak * 0.5

    us_per_sample = 1_000_000.0 / sample_rate
    edges: List[Tuple[int, int]] = []
    cur_level = 1 if mags[0] >= thr else 0
    run = 1
    for m in mags[1:]:
        lvl = 1 if m >= thr else 0
        if lvl == cur_level:
            run += 1
        else:
            edges.append((cur_level, int(round(run * us_per_sample))))
            cur_level = lvl
            run = 1
    edges.append((cur_level, int(round(run * us_per_sample))))

    # Merge sub-min_us glitches into their predecessor.
    merged: List[Tuple[int, int]] = []
    for lvl, dur in edges:
        if merged and dur < min_us:
            plvl, pdur = merged[-1]
            merged[-1] = (plvl, pdur + dur)
        else:
            merged.append((lvl, dur))
    return merged


def receive(
    freq_hz: int,
    seconds: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    lna_gain: int = DEFAULT_RX_LNA,
    vga_gain: int = DEFAULT_RX_VGA,
) -> Tuple[bool, str, List[Tuple[int, int]]]:
    """Capture `seconds` of IQ with hackrf_transfer -r and demodulate to edges.

    Returns (ok, message, edges). The caller injects the edges into the
    emulator via PandoraEmu.rx_inject(). Per RE_RX_FRAME_MODEL_D605.md, the
    firmware's own demod needs synchronized samples; for the emulator we inject
    the clean edges we recover here.
    """
    if USE_PYHACKRF:
        return _pyhackrf_receive(freq_hz, seconds, sample_rate, lna_gain, vga_gain)
    if not _have_cli():
        return False, "hackrf_transfer not installed", []

    num_samples = int(sample_rate * seconds)
    tmp = tempfile.NamedTemporaryFile(
        prefix="pandora_rx_", suffix=".iq", delete=False)
    tmp.close()
    try:
        cmd = [
            "hackrf_transfer",
            "-r", tmp.name,
            "-f", str(int(freq_hz)),
            "-s", str(int(sample_rate)),
            "-n", str(num_samples),
            "-l", str(int(lna_gain)),
            "-g", str(int(vga_gain)),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=max(10, seconds * 4))
        except Exception as e:  # noqa: BLE001
            return False, f"hackrf_transfer failed: {e}", []
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or
                           f"hackrf_transfer rc={proc.returncode}").strip(), []
        with open(tmp.name, "rb") as f:
            iq = f.read()
        edges = iq_to_edges(iq, sample_rate)
        return True, f"captured {len(edges)} edges @ {freq_hz/1e6:.3f} MHz", edges
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ===========================================================================
# FUTURE: direct libhackrf backend via pyhackrf (DISABLED).
#
# This path avoids spawning hackrf_transfer and streams samples through
# callbacks, which is lower-latency and lets us do continuous RX. It is left
# here, fully written but inert (USE_PYHACKRF defaults to False and the import
# is deferred so the package is never required). To enable:
#
#     pip install pyhackrf            # provides the libhackrf python bindings
#     # then set USE_PYHACKRF = True at the top of this module
#
# The functions below are only called when USE_PYHACKRF is True. They are kept
# as a block comment so static analysis / linters never touch the optional API.
# ===========================================================================
#
# def _pyhackrf_transmit(edges, freq_hz, sample_rate, tx_gain, amplitude):
#     import pyhackrf  # noqa: F401  (optional dependency)
#     iq = edges_to_iq(edges, sample_rate, amplitude)
#     dev = pyhackrf.HackRF()
#     dev.set_freq(int(freq_hz))
#     dev.set_sample_rate(int(sample_rate))
#     dev.set_amp_enable(True)
#     dev.set_txvga_gain(int(tx_gain))
#     pos = {"i": 0}
#
#     def _tx_cb(buffer, length):
#         # Feed the pre-rendered IQ out, then signal end-of-stream.
#         chunk = iq[pos["i"]:pos["i"] + length]
#         pos["i"] += len(chunk)
#         buffer[:len(chunk)] = chunk
#         return 0 if pos["i"] < len(iq) else -1
#
#     dev.start_tx(_tx_cb)
#     while pos["i"] < len(iq):
#         pass
#     dev.stop_tx()
#     dev.close()
#     return True, f"transmitted {len(edges)} edges (pyhackrf)"
#
#
# def _pyhackrf_receive(freq_hz, seconds, sample_rate, lna_gain, vga_gain):
#     import pyhackrf  # noqa: F401
#     captured = bytearray()
#     want = int(sample_rate * seconds) * 2  # 2 bytes/sample (I,Q)
#     dev = pyhackrf.HackRF()
#     dev.set_freq(int(freq_hz))
#     dev.set_sample_rate(int(sample_rate))
#     dev.set_lna_gain(int(lna_gain))
#     dev.set_vga_gain(int(vga_gain))
#
#     def _rx_cb(buffer, length):
#         captured.extend(buffer[:length])
#         return 0 if len(captured) < want else -1
#
#     dev.start_rx(_rx_cb)
#     while len(captured) < want:
#         pass
#     dev.stop_rx()
#     dev.close()
#     edges = iq_to_edges(bytes(captured), sample_rate)
#     return True, f"captured {len(edges)} edges (pyhackrf)", edges
#
# ---------------------------------------------------------------------------
# Stubs so the names resolve when USE_PYHACKRF is accidentally True without the
# block above being uncommented. They fail loudly rather than silently.
def _pyhackrf_transmit(*_a, **_k):
    raise NotImplementedError(
        "pyhackrf backend is disabled; uncomment the block in hackrf.py and "
        "`pip install pyhackrf` to enable it")


def _pyhackrf_receive(*_a, **_k):
    raise NotImplementedError(
        "pyhackrf backend is disabled; uncomment the block in hackrf.py and "
        "`pip install pyhackrf` to enable it")


# ---------------------------------------------------------------------------
#  Bridge — the AUTOMATIC radio follower.
# ---------------------------------------------------------------------------
# The firmware does everything: it selects the band/frequency, decides when to
# transmit, and runs the protocol. The bridge does NOT configure the radio. It
# only:
#   * TX: whenever the firmware bit-bangs a waveform (TX edges accumulate in the
#     emulator), pop those edges and transmit them through the HackRF.
#   * RX: (future) keep the SDR listening and inject received edges back into the
#     firmware. RX is a bigger piece (continuous capture + demod loop) and is
#     left as a follow-up; pump() only does TX today.
#
# This class is driven from the single emulator worker thread (pump() is called
# each step). It degrades to a no-op when no HackRF is connected, so the app is
# a fully working visual emulator without the SDR.
class Bridge:
    def __init__(self) -> None:
        self._emu = None
        self._present = False
        self._freq_hz = BANDS[DEFAULT_BAND]
        self._min_tx_edges = 8   # ignore tiny spurious bursts
        # cache SDR presence once; a rescan happens on attach()
        try:
            self._present = detect().present
        except Exception:
            self._present = False

    def attach(self, emu) -> None:
        """Bind to a freshly-loaded emulator and (re)probe the SDR."""
        self._emu = emu
        try:
            self._present = detect().present
        except Exception:
            self._present = False

    def set_frequency(self, freq_hz: int) -> None:
        """If/when the firmware's selected band is decoded, set it here. Until
        then the default ISM band is used for TX."""
        self._freq_hz = int(freq_hz)

    def pump(self) -> None:
        """Called every worker step. Transmit any waveform the firmware emitted.

        We always drain the emulator's TX edge queue (so it never grows
        unbounded) but only key the radio when a HackRF is present and the burst
        is non-trivial.
        """
        emu = self._emu
        if emu is None or not getattr(emu, "loaded", False):
            return
        try:
            edges = emu.tx_pop_edges()
        except Exception:
            return
        if not edges or len(edges) < self._min_tx_edges:
            return
        if not self._present:
            return  # drained the queue; no radio to key
        # Transmit synchronously on this worker thread. transmit() never raises.
        try:
            transmit(edges, self._freq_hz)
        except Exception:
            pass
