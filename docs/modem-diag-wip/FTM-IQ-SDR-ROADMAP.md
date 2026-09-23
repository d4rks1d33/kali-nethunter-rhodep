# FTM / IQ / general-purpose SDR on the rhodep modem — roadmap

Goal, stated plainly so the scope is not overclaimed: use the phone's Qualcomm
SM6375 modem transceiver as a software-defined radio — first raw-IQ **capture**
(RX), later arbitrary **emission** (TX). That is ambitious and the last steps may
not be reachable at all on a production-fused handset. This document is the plan
and the honest boundary between what is proven and what is speculation, in the
FACT / INFERENCE convention the rest of `docs/modem-diag-wip/` uses.

## Where we actually are (FACT, measured 2026-09-23)

The transport problem is **solved**. DIAG runs end to end over QRTR sockets, no
kernel driver, via `userspace/debug-tools/rhodep-diag-server.py`:

- control handshake completes: modem feature mask `f7fe1b` received, AP feature
  mask sent, DIAGID from the modem (`msm/modem/root_pd`, `msm/modem/wlan_pd`)
  acked, all masks enabled;
- the modem's DIAG **CMD** service is reachable (seen at node 0, port 152 in one
  run — the port is not stable, look it up by service `0x1001` instance 1);
- a command sent to it is answered (`--cmd 00` returns the build version string);
- the DATA log stream flows: ~476 packets in a short session, and among the
  plaintext F3 messages are RF-relevant ones already:
  - `CALDB::: DPD_MASTER:valid Bit Mask` — the calibration database and digital
    pre-distortion state,
  - `CC:: Taps of gain indexes - 00 01 02 03 are ...` — RX/TX gain-index tap
    coefficients,
  - `NR5G_RRC`, WLAN-coex frames, UICC APDUs.

So we can already *send an arbitrary DIAG request to the modem and read its
reply*, and *stream its internal logs*. Everything below builds on that one
primitive; none of it needs new kernel work.

Restarting the modem to bind a fresh DIAG client is safe **as long as LTE is not
attached** (it leaves IPA un-setup, which only matters for data). The SoC did not
reset across the restart (uptime kept climbing). Do not do it with a live LTE
attach.

## The ladder from here to SDR

Each rung is "send these DIAG bytes on the CMD service, read the reply". The
protocol is public (SCAT, QCSuper, MobileInsight, the old CLO diag headers); the
*firmware* is closed, so which sub-commands a production build actually honours is
the unknown at every rung, and the only way to know is to try the read-only ones
and watch.

1. **DIAG generic command interface** — DONE. `0x00` version, `0x0c` version no.,
   `0x7c` extended build id. These are the safe pokes that prove the CMD path.

2. **FTM subsystem reachability** — NEXT. FTM is DIAG command `0x4B` (SUBSYS_CMD)
   with subsystem id `0x0B` (FTM = 11), then a 2-byte FTM command id and a body.
   The request frame is:

       4B 0B <ftm_cmd_lo> <ftm_cmd_hi> <len_lo> <len_hi> <body...>

   The first thing to send is a **read-only** FTM command and see whether the
   modem answers, errors, or ignores it. Candidates, least invasive first:
   - `FTM_GET_STATE` / a version/query sub-command — asks, changes nothing;
   - a mode query. Do NOT send set-frequency, PA-enable or TX sub-commands here.

   Three outcomes, all informative:
   - a well-formed FTM response → the FTM interface is open, go to rung 3;
   - a DIAG "bad subsys" / "not supported" (`0x13`, `0x14` …) → FTM is refused on
     this build from the AP, which is a real answer and bounds the project;
   - silence → the sub-command id is wrong or gated; try the next candidate.

   INFERENCE: production Motorola builds often leave FTM reachable because the
   factory calibration line uses exactly this path, but they may require the
   modem to be in an FTM/offline operating mode first (DMS "set operating mode =
   FTM" over QMI, or the diag `MODE` command). That is the most likely reason a
   naive FTM poke returns nothing, and it is the first thing to vary.

3. **RF driver / transceiver query (still read-only)** — once FTM answers, the RF
   sub-commands that *read* state: current band, PLL/LO lock, RX gain state, the
   gain-index tables we already see logged as `CC:: Taps of gain indexes`. This
   builds the register/parameter map of the transceiver without transmitting.

4. **RX tune + measurement** — set the receiver to a frequency and read back
   RSSI / an FFT bin / a power measurement. Still no TX. This is where "does the
   RX chain actually retune off the cellular bands" gets answered. The transceiver
   is a wideband part; whether the firmware lets FTM park it on an arbitrary
   frequency, or clamps it to calibrated cellular bands, is unknown and is the
   first hard gate for general-purpose RX.

5. **Raw IQ capture (RX)** — the actual first goal. Qualcomm FTM has IQ-capture
   sub-commands (the calibration line uses them to sample the RX ADC). The
   request arms a capture; the samples come back either inline in the response or
   as a DIAG log/DATA burst (which is why the DATA stream plumbing already being
   proven matters). Deliverable: a `.cfile`/`.iq` a normal SDR toolchain
   (GNU Radio, inspectrum, numpy) can open.

6. **Arbitrary RX** — combine (4) and (5): tune anywhere the hardware allows,
   capture IQ. This is a usable receive-only SDR if the tuning range is open.

7. **TX — continuous wave, then waveform** — the ambitious end. FTM TX
   sub-commands key the PA and can emit a tone or a canned waveform; arbitrary
   TX waveform is a further step and may not be exposed at all. **Legal warning,
   not boilerplate:** transmitting outside the device's certified bands is
   illegal in most jurisdictions and can interfere with licensed services and
   emergency communications. TX rungs are for a shielded/lab setup only, and are
   deliberately last.

## What is unknown, and how each unknown gets resolved

- **Is FTM reachable from the AP on this fused build?** → rung 2, one read-only
  poke. Binary answer.
- **Does FTM need an offline/FTM operating-mode first?** → try the QMI DMS mode
  set (there is already a `service-probe`/AT path to the modem) or the diag MODE
  command before the FTM poke.
- **Can the RX be tuned off cellular bands?** → rung 4, read back the LO after a
  tune request to an out-of-band frequency; see if it locks or is rejected.
- **Does IQ capture return inline or as a DATA burst?** → rung 5, watch both the
  command reply and the DATA socket.
- **Sample format / rate / scaling** → from the capture itself plus the SCAT /
  QCSuper FTM parsers, not from guessing.

## Tools and where the protocol knowledge lives

- `userspace/debug-tools/rhodep-diag-server.py` — the working DIAG client;
  `--cmd HEX` sends an arbitrary request to the CMD service. This is where FTM
  request framing gets added.
- SCAT (`fgsect/scat`), QCSuper, MobileInsight — open-source DIAG/FTM parsers and
  the reference for command ids and response layouts. Prefer reading these over
  reverse-engineering a packet at a time.
- The modem's own logs (`CALDB`, `DPD_MASTER`, gain-index taps) are a free map of
  what the RF driver is doing; decode them alongside the active FTM work.

## The one rule carried over from HANDOFF.md

A decoder — or a command — that reports confident nonsense is worse than one that
reports nothing. Every rung here is a question with a measurable answer; when the
answer is "refused" or "silent", that is the result, and it gets written down as
the boundary rather than papered over.
