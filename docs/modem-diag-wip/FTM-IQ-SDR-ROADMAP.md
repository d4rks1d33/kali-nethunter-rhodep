# FTM / IQ / general-purpose SDR on the rhodep modem — roadmap

Goal, stated plainly so the scope is not overclaimed: use the phone's Qualcomm
SM6375 modem transceiver as a software-defined radio — first raw-IQ **capture**
(RX), later arbitrary **emission** (TX). That is ambitious and the last steps may
not be reachable at all on a production-fused handset. This document is the plan
and the honest boundary between what is proven and what is speculation, in the
FACT / INFERENCE convention the rest of `docs/modem-diag-wip/` uses.

## Status update (2026-09-25, later) — gate resolved; blocker is the RFTEST wire layout

Further RE flipped the previous "async gate" understanding and narrowed the real
blocker to one concrete unknown: the exact wire layout of the RFTEST body.

- The RFTEST gate `0xd8202224` (`memb(tech<<3+0xca7897b0)!=1 -> 0x14`) lives in the
  deferred RF action executor `0xd8201d3c`, which NO DIAG dispatcher reaches (BFS
  from RADIO_CONFIG unpacker / handler 0x1002 / dispatcher `0xd82714b4` hits none of
  it). **RADIO_CONFIG is NOT gated** — there is no circularity. The gate only guards
  the measurement/action phase (RX_MEASURE/IQ_CAPTURE). `blob-analysis/gate_resolution.md`.
- The `enter_mode`/`enter_mode_cnf` we chased is the ONLINE ML1 path (seg27
  @0xce480000), not FTM. In factory-test the FTM path powers RF directly via
  `rflte_mc_carrier_activate` -> `rflte_ftm_mc_wakeup`. `blob-analysis/enter_mode_path.md`.
- **The RFTEST command is NOT selected by the sub_command @0x04.** The 24-slot table
  `@0xc37c649c` (sub = 0x1000+slot) routes to a shared envelope; the real command is
  `command_id = memub(req+0x0a)`, checked <=0x31, indexing the runtime table
  `@0xca79a850` (`0xd8272684: memw(memw(0xca79a850)+id*4+0x34)`). 0 static refs to
  any unpacker. Best INFERENCE: IQ_CAPTURE=0x1002, RADIO_CONFIG=0x1003,
  COMMAND_CAPABILITY=0x1004. `blob-analysis/slot_correlation.md`.

**The one blocker now: the wire offset of `command_id`.** The envelope `0xd8272c10`
(@0xd8272c50) copies wire bytes 0..7 verbatim into the internal buffer but writes
buffer[8]=r16 and buffer[9]=lsr(r16,8) (sub_command metadata, not wire), so
`buffer+0x0a` (command_id) does NOT map 1:1 to wire byte 10. Resolving the exact
wire byte needs a full trace of r1/r16/r17 through the copy chain. Live sweeps of
command_id at guessed offsets SSR the modem, and the static trace agent keeps
getting blocked by the model's content filter. So: protocol fully open, the last
missing datum is a byte offset.

Next: finish the static trace of `0xd8272c10`/`0xd86fd0e8` (r18 provenance) to pin
the wire byte, or brute-force it more carefully (one command_id per run to isolate
SSRs). Live logs: `research/modem-blob/live-logs/` (cid.log = the offset sweep).

## Status update (2026-09-25) — packet format solved, one async gate left

The long-standing "silence" is fully explained and fixed. The packet is
`4b 0b <ftm_cmd:u16 @0x02> <sub_command:u16 @0x04> <num_tlv:u16 @0x06> <TLVs>`
(TLV = `<field_id:u16><len:u16><value>`). The old wrapper `14 00 5a 03` put the
constant 0x14 at @0x02, which the diag core (`memuh(pkt+0x2)` @0xc0d55f48,
table 0xc37bd1e8) routes to a reserved handler that `return 0` = silent drop.
See `blob-analysis/ftm_subsys_activate.md`.

With the correct format and by becoming the modem's COMMAND peer
(`--cmd-peer`), live over QRTR:

- `4b0b 2700 0d00 0300 <SUB=0,TECH=1,SCENARIO=0>` (TECH_ENTER, ftm_cmd 0x27=LTE,
  sub 0x0d) → **reproducible 63-byte structured reply**, plus (some runs) 16 KB
  RF-test REPACK blocks on the DATA channel. The command parses and runs.
- sub_command @0x04 is one space partitioned by range: 0x0000-0x0FFF RFDEBUG
  (TECH_ENTER=0x0d), 0x1000-0x3FFF RFTEST (RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE
  / COMMAND_CAPABILITY). See `blob-analysis/sub_command_map.md`.

**The one remaining blocker is an async gate, not the protocol.** Every RFTEST
sub returns status 0x14 because the per-tech "entered" flag `@0xca7897b0[tech]`
is still 0. That flag is written =1 by `0xd81e5cec` only when `session->0xc == 2`
(gate `0xd81e5d20`); the RFTEST executor gates on it at `0xd8202224`
(`memb(tech<<3+0xca7897b0)!=1 → 0x14`). `session->0xc` is NOT set by any DIAG
sub_command and NOT by a missing TLV (our SUB/TECH/SCENARIO set is complete). It
is raised to 2 only as an **async side-effect**: TECH_ENTER sends an RF
`enter_mode` over MSGR and, when `enter_mode_cnf` returns, the state machine sets
`session->0xc=2`. In factory-test mode that confirmation does not complete
(RF/cal not in a state to confirm — asserts `rfm_inst->wakeup_req.use_enter_mode`
/ `enter_mode_cnf`). So the protocol path is fully open; the RF enter-mode
handshake is what stalls. See `blob-analysis/tech_state_gate.md` and
`blob-analysis/session_start.md`.

Next candidates to make enter-mode confirm: try SCENARIO/SUB variants, drive the
RF cal/wakeup sequence first, or attempt outside factory-test (online) mode.
`--cmd-peer` is now supported on `--raw`, `--raw-seq` and `--rftest-sweep`.

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

   **   Conclusion of the live probing.** The low selectors answer but behave like
   echo/parameter-read handlers, not the classic per-technology RF dispatch.
   Identifying which selector is set-mode / tune / IQ-capture is not resolvable by
   blind probing — the numbers are proprietary to this firmware. So the modem
   image was pulled and reverse-engineered offline (next section).

### Blob extraction — the FTM table is real and IQ capture exists (2026-09-24)

The modem MBN was reassembled into an ELF and disassembled with
`llvm-objdump 18 --triple=hexagon`. Full write-up and data in
`docs/modem-diag-wip/blob-analysis/` (REPORT.md + the raw tables). Backup blob:
`research/modem-blob/modem-blob-rhodep.tgz`.

What it establishes:

- **The FTM subsystem table is real.** `DIAG_SUBSYS_FTM = 0x0B` is registered
  with **75 entries** at vaddr `0xc37bd1e8` (segment b21, rodata). Each entry is
  `{u16 selector_lo, u16 selector_hi, u32 dispatch_ptr}`. Cross-check: the five
  selectors that answered live (7, 13, 16, 20, 27) are all in the table, and
  0x07 is in FTM only, so this is the right table. The 74 real selector values
  are in `blob-analysis/ftm_subsys_0x0B_selector_table.txt`.

- **The per-selector handlers cannot be read statically.** Every `dispatch_ptr`
  is the same value `0xd8150ed8`, a region that is not in any PT_LOAD — the FTM
  handlers are relocated into it at boot. So the second-level `ftm_cmd_id`
  decode is not disassemblable from the static image; it has to be probed live.

- **IQ capture is present in this firmware.** Confirmed by module names and log
  strings: `ftm_rf_test_iq_capture.c`, `nr5g_ml1_iq_capture.c` (+ `_stm`,
  `_log`), a full `NR5G_ML1_IQ_CAPTURE_STM` state machine
  (`START/STOP/ABORT_REQ`, `INACTIVE/WAIT`), and `[FTM.RFTEST][IQ_CAPTURE]
  [UNPACK|REPACK]` pack/unpack logging. RX tune too: `RX_TUNE_CMD`,
  `RX_TUNE_COMP_CMD`, `nrfw_rx_iq_capture` trace points.

- **The RF-test command is TLV-based, and the parameter names are extracted.**
  The IQ/RF-test payload carries named TLVs (full list in
  `blob-analysis/ftm_rftest_tlv_param_names.txt`). The ones that matter:
  - capture: `IQ_CAPTURE`, `FETCH_IQ`, `NUM_OF_SAMPLES`, `IQ_DATA_FORMAT`,
    `SAMP_FREQ`, `IQ_GAIN`, `PEAK FREQ`;
  - tune: `TECH_MODE`, `RX_MODE`, `RX_CARRIER`, `CENTER_FREQ`, `BWP_CENTER_FREQ`,
    `BANDWIDTH`, `CC_BANDWIDTH`, `FREQUENCY`, `INTER_FREQ`;
  - gain/AGC: `RX_AGC`, `RX_AGC_MIN/MAX`, `RX_GAIN_CTL_TYPE`, `MIXER_GAIN`.

So the RX-capture recipe is now shaped by real firmware evidence: select a
technology dispatcher, `TECH_MODE` + `RX_MODE`, tune with `CENTER_FREQ` /
`BANDWIDTH` (+ `RX_CARRIER`), then `IQ_CAPTURE` with `NUM_OF_SAMPLES` /
`SAMP_FREQ` / `IQ_DATA_FORMAT`, and `FETCH_IQ` to pull the samples. The exact
selector and second-level command number for RF-test still have to be found by
live probing (handlers are runtime-relocated), but the parameter model is no
longer guesswork.

Still expected: a large IQ buffer comes back through the FTM memshare loan
(client-id 1, 5 MiB), which this port already implements
(`userspace/modem/memshare-daemon.c`), not as a big inline DIAG reply.

### Live RF-test probing on top of the blob evidence (2026-09-24)

Coordinating FTM mode with the DIAG handshake was the practical blocker: FTM
commands only answer after the handshake (which needs a modem restart), and the
restart's SSR sometimes drops the operating mode back to online. Solved in
`rhodep-diag-server.py` with `--set-ftm-after`: do the restart for the handshake,
then set factory-test over QMI DMS from inside the tool (raw QRTR, msg 0x2E, the
DMS reply `0201002e0007 0002 0400 0000 0000` = result 0), then sweep. FTM + a
fresh handshake now hold together.

With that, `--rftest-sweep CMDID:LO-HI` sends `4b 0b <cmdid16> <sub16> <num_tlv=0>`
across sub-commands. Two candidate dispatchers were compared:

- **ftm_cmd_id 0x03 (FTM_RF):** every sub-command 0-15 returns the *same*
  `14 4b0b0300 <sub> .. a79eae9c` — a uniform status 0x14 with a constant magic,
  i.e. it does NOT discriminate sub-commands. Not the RF-test dispatcher (or it
  needs a different entry shape).
- **ftm_cmd_id 0x27 (FTM_LTE):** sub-commands behave *differently* from each
  other — sub 0, 5, 6 return a clean `4b0b2700 <sub> 0000` echo (accepted with an
  empty TLV list), sub 1-4 return status 0x14 (recognised but need TLVs), sub
  7-15 are absent. This is a real dispatcher that decodes the sub-command.

So the RF-test path on this build is the per-technology cmd_id (0x27 for LTE),
not the generic FTM_RF, and the accepted empty-TLV sub-commands (0, 5, 6) are the
ones to identify. The status-0x14 subs (1-4) take a TLV body.

### Where it stands, and why the enum needs a live-only method (2026-09-24)

A third RE pass established, by five independent checks (0 hits each), that the
**sub_command enum is NOT statically extractable**: the `[FTM.RFTEST][*]` format
strings have no xref in mapped code, the 25 TLV parameter tables have no pointer
referencing them (indexed by runtime calculation), the sub-command names do not
exist as standalone strings (no `{name,id,fptr}` table), and the FTM dispatch
entries all point into the runtime-relocated region. The enum lives in a compiled
`.h` inside that relocated code. Full write-up:
`blob-analysis/rftest_subcommand_enum_entermode.md`.

But the same pass found the firmware's own enumeration mechanism, which is the
way out. **COMMAND_CAPABILITY (TLV group 22) returns a CMD_MASK**: a bitmask
where bit N = 1 means sub_command N exists. Its TLVs are `QUERY_COMMAND` (id 1),
`QUERY_PROPERTY` (id 2), `CMD_MASK` (id 3, in the reply). So reading CMD_MASK
gives the valid sub_command numbers directly, and `QUERY_COMMAND=N` then names
each. The catch is circular: COMMAND_CAPABILITY's own sub_command number is
unknown, so it has to be found first.

Live probing this round, with the coordinated FTM+handshake tool:
- `0x27` subs echo their TLVs but stay status 0x14 (the LTE-legacy sub_command
  space, likely enter/exit/get-state at 0/5/6 and config/measure at 1-4, needing
  a technology entered first);
- `0x03` (FTM_RF, the generic multi-tech RF-test) answers status 0x14 uniformly
  and echoes any TLV — consistent with it being the multi-tech framework that
  needs a mode entered (`ftm_rf_debug_tech_enter_exit.c`) before RADIO_CONFIG /
  RX_MEASURE / IQ_CAPTURE will run.

**COMMAND_CAPABILITY hunt run (2026-09-24).** Swept sub_command on 0x03 and 0x27
with a `QUERY_COMMAND=0xFFFFFFFF` TLV (`--cap-sweep`):
- 0x03: every sub returns the same status-0x14 echo of the request — it does not
  decode the sub_command (needs a mode entered, or a different entry shape);
- 0x27: subs 0/5/6 now accept the TLV with a **clean status** (no 0x14) and echo
  it, subs 1-4 stay 0x14, 7+ absent. So under 0x27 the 0/5/6 subs take a
  QUERY_COMMAND TLV without error — the best COMMAND_CAPABILITY candidates.

The CMD_MASK did not come back inline in those echoes. But two non-log DATA
packets appeared right after the RF-test commands, starting `60xx 0200` (not the
`79/92/99` F3 log shapes), carrying `20 01 04 02 0000 a0000000 0a <10 bytes>`:
`01 02 03 04 05 06 07 08 09 0a` in one and `24 28 2c 30 90 95 99 9d a1 a5` in the
other. That is not a CMD_MASK — it is the RF gain-index tap tables (the same data
the `CC:: Taps of gain indexes` log prints), i.e. the commands are provoking real
RF-driver activity, and the structured replies come on the DATA service, not the
CMD socket. The COMMAND_CAPABILITY REPACK format is `[%3d][%12s][%lld][0x%8x]`,
so its CMD_MASK likely returns as a value+pointer on DATA too, which is why the
inline CMD-socket echo does not show it.

Live logs of this round are saved for offline correlation:
`research/modem-blob/live-logs/` (cap03, cap27, rft27, d27s0/5/6, DECODE.md).

**Correction (2026-09-24, second live pass + decode).** Capturing the DATA
socket per command and decoding it (`live-logs/DECODE.md`) showed the ~16 KB
`60xx` "REPACK" packets are NOT the command reply — they are background RF/cal
telemetry (RxDCO restore, WLAN/WCN), identical across sub 0/5/6, timestamped
*before* the command, with a constant pointer/size and field_ids 0x0120/0x0121,
not the group-22 CMD_MASK (field_id 3). Proven three ways (timing, invariance,
content). The real reply to a 0x27 sub-command is the CMD-socket echo, clean, no
CMD_MASK TLV. So:

- **CMD_MASK is still UNKNOWN** — it is not in these captures.
- **0x27 is FTM_LTE, and on this build its sub-commands only echo** (they do not
  carry the RF-test REPACK). Not one `[FTM.RFTEST]`/`REPACK` string appears in any
  capture, which suggests the RF-test F3/response path is not routed onto this
  channel yet, OR the multi-tech RF-test lives under the generic dispatcher.
- The generic multi-tech RF-test framework (RADIO_CONFIG / RX_MEASURE /
  IQ_CAPTURE / COMMAND_CAPABILITY) most likely hangs off **FTM_RF = 0x03**, whose
  sub-commands so far echo uniformly with status 0x14 — consistent with needing a
  technology entered first, or a different sub-header.

**Two concrete things to try next, both bounded:**
1. On 0x03, read the CMD-socket reply (not DATA) across sub 0-15 with
   QUERY_COMMAND, looking for field_id 3 in the *echo tail*; and try a tech-enter
   first (`ftm_rf_debug_tech_enter_exit`, group 16 SUB/TECH/SCENARIO) before the
   RF-test sub-commands, since 0x03's uniform 0x14 looks like "no mode entered".
2. Check whether the RF-test F3 response stream needs its own log mask / a
   different QRTR instance to be routed to the AP — no `[FTM.RFTEST]` text is
   reaching us, so the structured reply may simply not be delivered on the
   channel we serve. This is a routing question, answerable by enabling the RF
   subsystem's SSID range in the msg mask and re-checking.

Everything up to the enum is still solid (transport, FTM mode, dispatch table,
TLV model + field-ids, dispatcher identification). The open item is narrowed to:
get the RF-test framework to answer with its REPACK (right cmd_id + mode +
routing), which the decode pass turned from "read the 16 KB blob" into these two
specific checks.

### Both checks done — the gate is tech-enter (2026-09-24, third live pass)

**F3 routing: fixed.** The RF-test F3 messages carry MSG_SSID_FTM = 23 (verified
in the blob, `blob-analysis/ssid_ftm.md`). The handshake only enabled the ranges
the modem reports, so the tool now always sends an explicit ALL_ENABLED msg mask
for ssid 23 after the handshake. Confirmed the modem reports range 0-134 (covers
23) and accepts the build-derived mask. So routing is no longer the blocker.

**Still no `[FTM.RFTEST]` F3 and no CMD_MASK.** With ssid 23 enabled, 0x27 sub 5
(and 0/6) still return only the clean 11-byte echo and never emit an RF-test F3 —
so the command is *accepted but not executed*. The 4972/16 KB DATA packets remain
background heap/telemetry ("Small pool / SL pool / Large pool", RxDCO cal), not
the reply.

**What 0x14 means (RE, `blob-analysis/rftest_entry_0x14.md`):** 0x14 =
DIAG_BAD_PARM_F — the command is recognised (else it would be 0x13) but rejected
for an invalid parameter/state *before* executing (hence no F3). Also two
structural corrections from this pass:
- the FTM code is NOT runtime-relocated as earlier assumed — it is **q6zip
  compressed and demand-paged** (dlpager), which is why 0xd8150ed8 won't
  disassemble. seg27 was zlib and was decompressed (QSHRINK4 DB); the dispatch
  code is in seg26 (q6zip), still packed;
- **stop probing 0x03** — the RF-test hangs off the per-technology cmd_ids (0x27
  LTE, 0x8000/0x8001 NR5G), confirmed by `ftm_lte_rex_dispatch`. 0x03's uniform
  0x14 was a dead end.

Recovered from rodata: the per-command property handler counts (RADIO_CONFIG=46,
RX_MEASURE=70, IQ_CAPTURE=51, COMMAND_CAPABILITY=6, TX_MEASURE=237), but not the
sub_command→handler master table (it is in the q6zip code).

**So the one remaining gate is the tech-enter sequence** — the modem needs LTE (or
NR5G) *entered* in FTM before RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE will run; the
accepted-but-inert subs 0/5/6 are the enter/exit/get-state candidates, and 1-4
(the 0x14 ones) are the config/measure commands that fail because no mode is
entered. Its exact sub_command + TECH value is UNKNOWN statically (q6zip code).

**Two ways to close it, both bounded:**
1. Decompress the seg26 q6zip and read the ftm_common_dispatch / tech_enter_exit
   handler for the sub_command numbers and TECH enum. (RE, offline.)
2. Live: sweep 0x27 sub 0/5/6 as tech-enter with a TECH TLV (try LTE tech values)
   then immediately send a config command and watch for the 0x14 to clear and an
   F3 to appear. (Live, iterative.)

### Both tried (2026-09-24) — q6zip not statically decompressable, live sweep inconclusive

**q6zip (path 1): the tool works, the input is not in the ELF.** Using
`nlitsme/qualcomm-q6zip`, the format was fully understood (npages/version, two
dicts, a page-pointer table, 15-opcode bitstream). But seg26 (modem.b26,
0xcc000000, 38 MB, entropy 7.73) carries **no q6zip metadata in the ELF**: the
page-pointer table and dict are built at boot into the paged window 0xd8xxxxxx,
which is BSS (PH24: filesz 0, memsz 48 MB). Verified by six independent scans.
The dispatch handler 0xd8150ed8 is exactly in that boot-built window, so the
ftm_common_dispatch / tech_enter code is not recoverable from the static image.
Full analysis in `blob-analysis/tech_enter_decoded.md`. So the sub_command
numbers and the TECH enum value stay UNKNOWN-from-blob.

What the blob DID give (FACT): GROUP 16 = tech_enter_exit fields are SUB=1,
TECH=2, SCENARIO=3; GROUP 22 = COMMAND_CAPABILITY has QUERY_COMMAND=1,
QUERY_PROPERTY=2, CMD_MASK=3, PROPERTY_MASK=4..7; the sub_command NAMES
(RADIO_CONFIG, RX_MEASURE, IQ_CAPTURE, COMMAND_CAPABILITY, TECH_ENTER_EXIT, ...)
but not their numeric indices.

**Live (path 2): swept, no combination cleared the 0x14.** With the correct
GROUP-16 field-ids (SUB=1, TECH=2, SCENARIO=3), sent tech-enter on 0x27 sub
{0,5,6} for every TECH value 0-12, then a config sub {1-4} on the same socket —
the enter is accepted (echoes) but every config still returns 0x14. A config sub
1 with a full RADIO_CONFIG TLV set (TECH_MODE=25, BAND=5, CHANNEL=6) also stayed
0x14, and the reply echoed only the FIRST TLV before stopping, i.e. it rejects at
the first field. So on 0x27 the config commands are gated on something the blind
sweep did not hit: the real enter sub_command, or a different cmd_id for the
generic enter (FTM_COMMON 0x00 / FTM_RF 0x03 as a global set-mode), or a
mandatory TLV/order the field tables do not reveal.

### Honest status and what is actually left

Everything up to the RF-test execution is proven and reproducible: DIAG
transport, FTM mode, dispatch table, the full TLV field-id model, the dispatcher
(per-tech cmd_ids), F3 routing (ssid 23), and 0x14 = DIAG_BAD_PARM. The single
remaining unknown — how to make the RF-test framework execute (enter a
technology) — is not resolvable by static RE (code is boot-paged q6zip) and did
not fall out of the bounded live sweep. The realistic ways forward, for a future
session, ranked:

1. **Dump the paged code from a LIVE modem**, not the static image: the
   decompressed pages exist in the 0xd8xxxxxx window at runtime. A DIAG
   peek/memory-read command (if this build allows it) or reading the modem's
   memory over the debug path would give the real dispatch code, and with it the
   sub_command + TECH enum directly. This is the highest-value next step.
2. **COMMAND_CAPABILITY CMD_MASK** remains the firmware-blessed enumerator, but
   it did not answer with a mask on 0x27 sub 0/5/6; try it on other cmd_ids
   (0x00 FTM_COMMON, 0x03) and read field_id 3 in the reply / on DATA.
3. A **wider live sweep** driven by the runtime code dump from (1), rather than
   blind — blind proved too high-dimensional (sub × tech × field × value).

The IQ-capture field model and the memshare delivery path are both ready; the
gate is purely getting the framework to enter a technology, and the clean way to
crack it is a runtime code dump rather than more blind live fuzzing.

### Runtime code dump tried too — the modem code is not reachable from the AP (2026-09-24)

Went after the runtime code with a real MSS ram dump, and it closes this avenue
with evidence:

- DIAG memory-peek is out: an RE pass of the diag master command table
  (`blob-analysis/mem_read_cmds.md`) found PEEKB/PEEKW/PEEKD (0x02/0x03/0x04) are
  NOT registered in this firmware (compiled out, not gated), and there is no
  memory-read subsys. So there is no "read address" DIAG command.
- The remoteproc coredump path works: set
  `/sys/class/remoteproc/remoteproc0/coredump = inline`, crash the modem
  (`.../crash`), and a `devcd` appears. Read it: a valid 264 MB ELF core, 34
  program headers. Backed up at `research/modem-blob/mssdump-runtime.elf.gz`.
- BUT the dump is **data only, no code and no rodata**: it does not cover the
  0xd8xxxxxx dispatch window, and it contains ZERO readable strings (no
  `[FTM.RFTEST]`, no `ftm_*` module names). The qcom_q6v5_pas coredump on this
  build dumps RAM state, not the modem's read-only code/rodata segments — so the
  ftm_common_dispatch / tech_enter code is not in it either.

Net: the modem's code is not reachable from the AP on this production build by
any path tried — static q6zip (page table is boot-built, not in the ELF), DIAG
peek (compiled out), or ram coredump (data only). Reading the tech-enter
sub_command and TECH enum out of the code is therefore not available; the only
remaining route to IQ capture is **live protocol discovery** of the tech-enter
(high-dimensional: sub_command x TECH x TLV set/order), which blind sweeping has
not cracked.

### BREAKTHROUGH: the modem code WAS decompressed (2026-09-24, later)

The "code is unreachable" conclusion above was **overturned**. The dispatch code
is not q6zip — it is **CLADE** (a hardware decompression engine). Three RE passes
cracked it (`blob-analysis/clade_decomp.md`, `clade_exceptions.md`,
`clade_final.md`, `iq_sequence.md`):

1. The CLADE dictionaries are in `modem.b26` at offsets 0x2444000/0x2446000/
   0x2448000 (validated with cladetool's OR signature); `.clade.comp` = b26@0.
2. `unclade.py` alone got ~74% (its bug: CLADE code `0b11` is a zero-bit
   EXCEPTION marker, not an inline 32-bit literal — it desynced the bitstream).
3. The real Qualcomm codec **`libclade.so`** (from `mzakocs/qualcomm_baseband_
   scripts`, cross-compiled x86-64 under qemu) decompressed it HW-accurate:
   0.14% invalid over 1M instructions, the dispatcher page 1 invalid / 2049.
   Output: `clade_dec_full.bin` (10 MB of real Hexagon), reproducible with
   `clade_extractor_sm6375`.

With clean code, the dispatcher `0xd8150ed8` was read directly and it yielded
what live fuzzing never could (all FACT from disassembly):

- **The real FTM packet wrapper.** The command is NOT `4b 0b <cmd16>` (what every
  live attempt sent, and why they all got 0x14). It is:
  `4b 0b | 14 00 | 5a 03 | <ftm_len16> | <id16> | 27 00 | <sub_command16> |
  <num_tlv16> | <TLVs>` — DIAG SUBSYS_CMD, subsys 0x0b, **DIAG subsys cmd code
  0x0014**, **ftm command id 0x35a**, the ftm_cmd (0x27=LTE) at offset 0xa, the
  RF sub_command at 0xc.
- **TECH_ENTER_EXIT sub_command = 13 (0x0d)** — FACT, from the dynamic
  registration table @0xca65b414 (stride 0xc, TECH_ENTER at offset 0x9c → slot
  13). The string-order guess (slot 8) was wrong, which is why blind sweeps of
  0-6 never hit it.
- **TECH field_id = 2 (u32)** in the tech-enter TLV; TECH=LTE best estimate **1**
  (internal tech-index for cmd 0x27), candidates 1/4/5/0x27.
- The 0x14 gate lives inside the dispatcher (DIAG_BAD_PARM).

Live test with the corrected wrapper (`--raw` / `--raw-seq` added to the tool):
the TECH_ENTER packet (`4b0b14005a03...2700 0d00 ...`) and a following
RADIO_CONFIG are now ACCEPTED without the 0x14 rejection — but the modem returns
no direct reply and no `[FTM.RFTEST]` F3 yet, so it is not confirmed to execute.
The remaining unknowns are small and live-testable: the exact TECH enum value
(barrer 1/4/5/0x27), the RADIO_CONFIG/IQ_CAPTURE RF sub_command numbers
(candidates 0-3, from a second dynamic table @0xca789780), the ftm_len field, and
whether the reply/F3 comes back on a different port. This is the finish line: the
protocol is decoded from the firmware, only a handful of numeric values remain to
pin by live probing with the now-correct packet shape.

### The protocol is fully decoded; one live-plumbing detail remains

Reading the decompressed handlers (`blob-analysis/iq_final_values.md`) resolved
the packet down to the byte, all FACT from disassembly:
- header offset 0x06 = a handle/id passed to an alloc-lookup (`0xd816d20c` →
  `0xd8062414`), not a length; 0x08 = response length but only when the DIAG
  subcmd @0x02 is 0x24 (ignored for our 0x14);
- body: ftm_cmd at 0x0a, sub_command at 0x0c (`d816d1f0` reads pkt[0xc]/pkt[0xd]);
- **TECH_ENTER responds** (allocs a DIAG rsp, SSID 0x17, writes an 8-byte status)
  — it is not fire-and-forget; a status-0 success is just short and easy to miss;
- **TECH = 1 = LTE**, closed two ways (`0xd8169ec0` cmd 0x27→idx1, and table
  `@0xc37bdfe0` TECH-TLV 1→idx1);
- the 0x14 gate is a per-tech state byte `@0xca7897b0` (tech<<3) == 0x7 ("no
  tech") → error; TECH_ENTER clears it.

Live, the corrected TECH_ENTER packet
(`4b 0b 14 00 5a 03 <handle> 00 00 27 00 0d 00 03 00 <SUB/TECH=1/SCENARIO>`) is
accepted — no 0x14 rejection — but **no reply is observed on any served socket
(CMD/CNTL/DATA/DCI) nor the cmd socket**, and a following COMMAND_CAPABILITY sweep
(sub 0..0x14) also returns nothing. So the command reaches the handler but its
short success response is not landing where the tool listens. That is the one
open detail: where/how the TECH_ENTER DIAG response (SSID 0x17, subsys FTM) is
delivered back over QRTR — likely a different port/instance than the CMD service
we send to, or it needs the DIAG response routing that a real diag client sets up.

### The response-routing detail, pinned down (2026-09-24)

Traced the response path in the decompressed code
(`blob-analysis/diag_response_routing.md`), correcting two guesses:
- the handle @0x06 is NOT a response channel — it is a generic FTM context handle
  (`0xd8062414` → `0xc0988ef4`, a framekey/refcount allocator); handle 0 is fine
  and changing it does nothing to the response;
- the DIAG response goes through the standard Qualcomm pipeline
  `diagpkt_subsys_alloc(SSID 0x17)` → `diagpkt_commit` → diagbuffer drain →
  `diagcomm_io_transmit` → QRTR sendto, out the **DIAG_DATA** channel (the inst-2
  service the AP serves), NOT back on the CMD client socket. `diagpkt_subsys_alloc`
  only reserves a buffer; the destination is decided at drain time.

The gate: `diagcomm_io_transmit` is **`allow_flow`-gated**
(`diagcomm_io_transmit: allow_flow=%d, channel_type=%d, ...`), and allow_flow is
set only after the control handshake puts the DATA channel in Tx/real-time mode
(`diagpkt_process_ctrl_msg: Tx Mode=%d for stream_id=%d`). apps commands (0x00,
0x7c) return a short in-tick response that we catch; a subsys command's response
is committed for **async drain by the DATA thread**, so if allow_flow is not set
for our peer it stays in the buffer and never arrives.

Live: sending TECH_ENTER from a client socket AND from the served DATA (inst-2)
socket (`--from-data`), and dumping all DATA for 3-4 s, still shows no subsys
response — only the F3 log/telemetry stream (which does drain, so the DATA channel
is open for logs). So logs flow but the subsys command response does not, which
points squarely at allow_flow/Tx-mode not being set for command responses by our
handshake (the DIAGMODE we send sets real_time=1 but evidently not the exact
Tx-mode/stream_id the drain checks).

**Next session, concrete:** get `allow_flow=1` for the DATA channel — replay the
exact control-message sequence a real diag client sends (feature mask + the
Tx-mode/real-time control with the right stream_id) and confirm via the F3 string
`diagcomm_io_transmit: allow_flow=1`. Once a subsys response drains, TECH_ENTER's
8-byte status appears on the DATA socket, and then RADIO_CONFIG → IQ_CAPTURE run
with the byte-level packets already worked out.

### Response routing fully mapped — COMMAND vs DATA channel (2026-09-24, later)

An integral read of the diag socket transport in the decompressed code
(`blob-analysis/diag_transport_full.md`, `allow_flow.md`, `drain_to_peer.md`)
corrected the earlier "it all drains on DATA" guess and pinned the mechanism:

- The response gate (`diagpkt_rsp_send` @0xc0d36c44) needs feature-mask
  (@0xc92e43e0, ctrl type 8) AND diagID (@0xc92e4754 bit0, ctrl type 0x21) — and
  it PASSES (apps 0x00 answers; TECH_ENTER never returns 0x14). Not the blocker.
- **Two output channels, not one** (`diagpkt_rsp_send` @0xc0d55d54:
  `r1 = mux(rsp_entry+0x20==0, 1, 2)`): mask 1 = COMMAND channel (desc
  0xcb93f380), mask 2 = DATA channel (desc 0xcb93f608). Confirmed by the strings
  `allow_flow on command channel` / `on data channel`.
- **F3 logs drain on DATA (mask 2); command responses drain on COMMAND (mask 1).**
  That resolves the paradox: logs arrive, command responses don't, because they
  use different channels/peers.
- The COMMAND channel's destination is a single global node/port
  (0xc8c2d870/0xc8c2d874) the modem learned from the last NEW_SERVER it saw.
- There is NO kernel diag driver (`/dev/diag` absent, no module) — verified — so
  no kernel peer is stealing the response.

Live result: sending TECH_ENTER **from the served DATA socket** (`--from-data`)
finally made large responses flow back on the COMMAND path (16 KB packets) —
previously nothing came back. So the routing insight is right: the peer/socket
you send from and listen on matters. BUT the packets that arrive are the modem's
general F3/telemetry stream now draining to us, not TECH_ENTER's own response —
no `[FTM.RFDEBUG][TECH_ENTER_EXIT]` F3 and no `4b0b` echo appears. So the command
still is not producing its own visible response/effect.

### Honest conclusion for this line of work

Enormous, well-evidenced progress: the whole FTM protocol is decoded from the
decompressed firmware, and the response routing (COMMAND vs DATA channel, the
feature/diagID gate, the single global COMMAND peer, no kernel diag driver) is now
mapped from the code, not guessed. `--from-data` demonstrably unblocked the
COMMAND-channel response flow. The last unresolved piece is that TECH_ENTER,
though sent in the correct shape and accepted, does not emit its own response or a
visible RFDEBUG F3 — so either the command needs one more precondition to
actually run its handler, or its response is routed to a peer we still aren't the
registered destination for. Next: make our listening socket the modem's COMMAND
peer (be the last NEW_SERVER of the CMD/service, or send-and-listen on that exact
socket), and re-check for the TECH_ENTER response / RFDEBUG F3. Tooling (`--raw`,
`--raw-seq`, `--iq-hunt`, `--from-data`) and the byte-level sequence are ready.
Everything reusable is in place: the DIAG transport and tooling
(`rhodep-diag-server.py` with sweep/l2/rftest/cap/tech-enter-hunt modes), the
full firmware-derived TLV field model, the dispatcher id, F3 routing, and the
memshare delivery path. Closing the last gate needs either a lucky live-fuzz of
the enter sequence, a different firmware/build that ships the diag peek or a
symbol'd FTM, or vendor documentation of the FTM RF-test enter command. This is
recorded as a bounded, well-characterised stopping point rather than a failure:
the wall is the vendor's closed RF firmware, the same class as the NFC RF
front-end and the LTE-attach watchdog.

### Summary of the SDR ladder status

| rung | state |
|------|-------|
| DIAG transport | DONE (QRTR socket, handshake, log stream) |
| FTM mode | DONE (QMI DMS factory-test, coordinated with handshake) |
| FTM dispatch table | DONE (extracted + validated, 74 selectors) |
| RF-test command model | DONE (TLV structure + all field-ids extracted) |
| RF-test dispatcher id | DONE (per-tech cmd_ids: 0x27 LTE, 0x8000/0x8001 NR5G; NOT 0x03) |
| F3 routing (ssid 23) | DONE (explicit ALL_ENABLED mask for MSG_SSID_FTM) |
| status 0x14 meaning | DONE (DIAG_BAD_PARM_F: recognised, rejected pre-execute) |
| modem code | DECOMPRESSED (CLADE via libclade.so, HW-accurate, clade_dec_full.bin) |
| FTM packet wrapper | DECODED (4b 0b 14 00 5a 03 .. 27 00 <sub> <ntlv> ..) — FACT |
| TECH_ENTER sub_command | 0x0d (13) — FACT, from the registration table @0xca65b414 |
| TECH field / LTE value | field_id 2 (u32) FACT; LTE value best-guess 1 (probe 1/4/5/0x27) |
| RADIO_CONFIG/IQ_CAPTURE sub_command | candidates 0-3, second table @0xca789780 |
| TECH=LTE value | 1 (FACT, two ways) |
| 0x14 gate | per-tech state byte @0xca7897b0 == 0x7 -> error; TECH_ENTER clears it (FACT) |
| TECH_ENTER live | sent correctly + accepted (no 0x14); subsys response needs allow_flow on DATA |
| response routing | DECODED: cmd responses drain on the COMMAND channel (mask 1), F3 logs on DATA (mask 2); COMMAND peer = last NEW_SERVER's node/port |
| feature+diagID gate | PASSES (0x00 answers) — not the blocker |
| kernel diag driver | none (verified) — no kernel peer competing |
| --from-data | unblocked the COMMAND-channel response flow (16KB responses now arrive) |
| tune → IQ capture | protocol decoded; last piece = be the modem's COMMAND peer / confirm TECH_ENTER emits its response |
| IQ samples out via memshare | infra already in the port (5 MiB FTM loan) |
| arbitrary RX / TX | future |

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
