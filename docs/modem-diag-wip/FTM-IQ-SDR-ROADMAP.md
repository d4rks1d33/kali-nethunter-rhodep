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

2. **FTM subsystem reachability** — **DONE, FTM answers (measured 2026-09-23).**
   FTM is DIAG command `0x4B` (SUBSYS_CMD) with subsystem id `0x0B` (FTM = 11),
   then a 2-byte FTM command id and a body:

       4B 0B <ftm_cmd_lo> <ftm_cmd_hi> <body...>

   First the CMD path was validated with apps-level requests, which answer on the
   QRTR socket the request is sent from (node 0, the modem's CMD port, which moves
   per boot — look it up by service 0x1001 instance 1):
   - `00` → 58-byte version response (`Sep 17 2024 ... strait.g`);
   - `7c` → extended build id `MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`.

   Then the SUBSYS dispatch and FTM itself, all replies on the same CMD socket:

   | request      | reply                         | reading |
   |--------------|-------------------------------|---------|
   | `4b 32 00`   | `15 4b 32 00`                 | subsys 0x32 dispatch is live; `0x15` = BAD_LEN |
   | `4b 0b 00 00`| (silence)                     | FTM cmd 0 dropped |
   | `4b 0b 01 00`| `14 4b 0b 01 00` + 14 zero bytes (20 B) | FTM cmd 1 answered with a status/echo |
   | `4b 0b 02 00`| `4b 0b 02 00` (clean echo, 7 B)| FTM cmd 2 **accepted, no error byte** |
   | `4b 0b 03 00`| (silence)                     | dropped |
   | `4b 0b 04 00`| `13 4b 0b 04 00`              | `0x13` = BAD_CMD, cmd 4 not implemented |

   So the FTM subsystem is reachable from the AP on this fused build — the factory
   calibration path is open. The leading byte is a DIAG status: `0x13` BAD_CMD,
   `0x14` (status/partial), `0x15` BAD_LEN; a clean echo with no status byte
   (cmd 2) is an accept. The reply comes back on the CMD socket, not DATA, for
   these — the `--match-prefix` DATA path was added for the log-stream case but
   was not needed here.

   Tooling: `rhodep-diag-server.py --restart-modem --cmd <hex> --cmd-after N`.
   `--restart-modem` is required because the modem binds diag to whoever connected
   first; it is safe as long as LTE is not attached. Each probe is one modem
   restart (~18 s), so the next step is a sweep mode that sends many FTM command
   ids after a single handshake.

   INFERENCE / still open: the RF-bearing FTM commands (set mode, tune, IQ) are
   at specific higher ids and take a structured request body; several likely
   require the modem in an offline/FTM **operating mode** first (DMS "set
   operating mode = FTM" over QMI, or the diag MODE command). The low ids probed
   here are FTM's own control/dispatch, not the RF driver yet.

3. **RF driver / transceiver query (still read-only)** — IN PROGRESS.

   **Entering FTM mode — DONE (measured 2026-09-23).** The modem is put in
   Factory Test Mode over QMI DMS, not DIAG:

       sudo systemctl stop ModemManager           # so MM does not fight it
       sudo qmicli -d qrtr://0 --dms-set-operating-mode=factory-test
       sudo qmicli -d qrtr://0 --dms-get-operating-mode   # -> 'factory-test'
       # ... FTM work ...
       sudo qmicli -d qrtr://0 --dms-set-operating-mode=online
       sudo systemctl start ModemManager

   It is reversible and does not persist across a reboot (only
   persistent-low-power(6) would). Notably, the diag `--restart-modem` SSR does
   NOT knock it back to online — the mode survives the subsystem restart — so the
   working recipe is: set factory-test, then run the diag server with
   `--restart-modem` to get a fresh DIAG handshake while already in FTM.

   **Key finding: FTM commands need the DIAG control handshake, not merely FTM
   mode.** Without the handshake (feature-mask + DIAGID + masks, which only
   completes on a modem restart) the FTM subsystem answers apps-level commands
   like 0x00 but drops 4b 0b requests. With the handshake, the whole low command
   space responds.

   **FTM command map, FTM mode + handshake, one socket per id so every
   request/reply is correlated** (`--ftm-sweep`, reply status byte: `0x13`
   BAD_CMD, `0x14` status, clean `4b0b XX 00` echo = accepted; a payload after
   the echo = data):

   | FTM cmd | reply | reading |
   |---------|-------|---------|
   | 0x02 | `4b0b0200` | accepted, empty |
   | 0x07 | `4b0b0700` + `00 00 00 03` | data |
   | 0x09 | `4b0b0900` | accepted, empty |
   | 0x0d | `4b0b0d00` + `01 10 00 00 00 00 00 00` | structured data |
   | 0x10 | `4b0b1000` + `03 10 00 03 00 00 01 00` | data, some bytes vary run-to-run |
   | 0x11 | `4b0b1100` | accepted, empty |
   | 0x14 | `4b0b1400` + `00 03 00 00 10 00 01 00 ...` | data |
   | 0x1b | `4b0b1b00` + `00 00 00 03 00 00 01 00` | data |
   | 0x01,0x03,0x12,0x15 | `14 4b0b XX00 ...` | recognised, status 0x14 |
   | 0x04-0x06,0x08,0x0a,0x0b,0x0c,0x0e,0x0f,0x13,0x16-0x1a,0x1c | `13 4b0b XX00` | BAD_CMD, not implemented |

   The commands returning data (0x07, 0x0d, 0x10, 0x14, 0x1b) are the read-only
   query candidates; the run-to-run variation in 0x10 means it reflects live
   transceiver state.

   **Correction on the structure (from cross-referencing the protocol).** The
   `4b 0b <cmd16>` byte I first called the "FTM command" is really the FTM
   **dispatcher selector** (which technology/handler), and the FTM packet is two
   levels deep:

       4b 0b <selector16> <ftm_cmd_id16> <len16> <payload...>

   So the level-1 sweep above enumerated *which dispatchers exist*, with an
   implicit inner cmd_id 0. `0x13` = no dispatcher registered there; a data reply
   = a live dispatcher. The canonical selector numbers (FTM_COMMON≈100,
   FTM_LTE_C≈39, FTM_1X≈13, …) are build-dependent and did NOT line up cleanly
   with this firmware, so they cannot be assumed.

   **Level-2 sweep (`--ftm-l2 SEL:LO-HI`), measured in FTM mode + handshake:**
   - selector 13: every inner cmd_id returns `4b0b0d00 0110 <cmd_id> 00 ...`,
     i.e. it echoes the cmd_id behind a fixed `0110` with no per-command
     behaviour — an ack/echo handler, not a rich dispatcher. cmd_id 0x0a is the
     one exception (27 B, longer).
   - selector 16: same shape (`0210 <cmd_id> ...`) but with a varying return
     byte — `01` for cmd_id 0x02-0x09, `04` for 0x0b-0x18 — and cmd_id 0x0a
     returns live-looking bytes (`00 fe dc fb cf c9`). This looks like a
     parameter/table read, the closest thing to HW telemetry found so far.

   **Conclusion of the live probing.** The low selectors answer but behave like
   echo/parameter-read handlers, not the classic per-technology RF dispatch.
   Identifying which selector is set-mode / tune / IQ-capture is not resolvable by
   blind probing — the numbers are proprietary to this firmware. The reliable
   source is the modem image itself, which is now backed up for offline analysis:
   `research/modem-blob/modem-blob-rhodep.tgz` (see its README) carries the split
   MBN; the DIAG/FTM dispatch tables live in the `modem.b26` Hexagon segment.
   That extraction is the next real step, cross-checked against these live
   results. IQ capture, when its command is found, will almost certainly deposit
   samples in the FTM memshare loan (client-id 1, 5 MiB) which this port already
   implements (`userspace/modem/memshare-daemon.c`) — a large inline reply is not
   how Qualcomm returns IQ.

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
