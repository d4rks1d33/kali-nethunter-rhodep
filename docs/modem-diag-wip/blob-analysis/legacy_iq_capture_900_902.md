# LEGACY IQ CAPTURE (FTM cmd 900/901/902) — wire format + presence verdict (SM6375)

**Target:** Qualcomm SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Scope:** Evaluate the LEGACY dedicated IQ-capture FTM commands
`_FTM_IQ_CAPTURE_GET_CONFIG=900 (0x384)`, `_FTM_IQ_CAPTURE_ACQUIRE_SAMPLES=901 (0x385)`,
`_FTM_IQ_CAPTURE_GET_SAMPLES=902 (0x386)` (+947/948 PRxDRx) as an alternative to the mode-105
RF_TEST IQ_CAPTURE path that returns `num_captures=0`.

**Binaries:** `/tmp/modemre/clade_dec_36m.bin` (code, VA base 0xd8000000) · `modem.b21` rodata
(0xc3553000) · `seg27_dec.bin` = decompressed `modem.b27` (RFLTE rodata, VA base 0xce480000).
**Tools:** `dis36.sh <va> <len>`, direct struct reads of modem.b21.
**Leyenda:** FACT = byte/instr/VA verified in THIS image · INFERENCE = hard-based deduction · UNKNOWN.

---

## 0. EXECUTIVE VERDICT

**These firmware does NOT implement the legacy 900/901/902 (nor 947/948) IQ-capture commands as
top-level FTM commands. They are UNREACHABLE — rejected by the FTM command router before any
handler runs. OBSERVED from disasm.** FACT.

Two independent facts prove it:

1. **The FTM command router `0xd814e034` indexes its dispatch table `@0xc37bc828` using ONLY the
   low byte `pkt[2]` of `ftm_cmd_id`, and the table is only 0x00..0x80 entries long.** For any
   command whose HIGH byte `pkt[3] != 0x00` and `!= 0x80`, the router branches to the error stub
   `0xd814e5e4` (BAD_CMD) *before* table lookup. 900/901/902 = `0x0384/0x0385/0x0386` → high byte
   `0x03` → **rejected**. (947/948 = `0x03b3/0x03b4`, same high byte `0x03` → rejected.) FACT.
2. **No legacy IQ-capture command handler exists in the image.** There is no `ACQUIRE_SAMPLES`,
   `GET_CONFIG`, or standalone `GET_SAMPLES` command handler string. The only
   `ftm_lte_iq_capture_get_samples_*` symbol is a **response-packet-alloc string** used *inside*
   the mode-105 RF_TEST FETCH prop-action, not a separate command. FACT.

⇒ **The legacy path is NOT a viable alternative on this build.** The only IQ channel present is the
mode-105 RF_TEST `rflte_ftm_iq_capture_*` path (see `iq_delivery_channel.md` / `map_iq_capture.md`).
The `num_captures=0` problem must be solved inside that path (it is a *setup/state* problem — no
carrier/capture armed — not a wrong-command problem), not by switching to 900/901/902.

---

## 1. JOB B — DISASM EVIDENCE (the router rejects 900/901/902)

### 1.1 FTM command router `0xd814e034` — low-byte index + high-byte gate [FACT, byte-exact]
DIAG transport reaches this via `0x4B` (DIAG_SUBSYS_CMD_F) → subsys `0x0B` (FTM) →
`ftm_cmd_id = pkt[2..3]` (corroborated: `ftm_set_mode.md` §2.1, `ftm_common_handler.md` §1.1,
`map_diag_core.md` §2).

```
d814e050:  r4 = memub(r17+#0x3) ; r3 = memub(r17+#0x2)   ; r4=pkt[3]=cmd_hi, r3=pkt[2]=cmd_lo
d814e054:  p0 = cmp.eq(r4,#0x80)                          ; is high byte 0x80 ? (production 0x8000)
d814e058:  r2 = r3
d814e05c:  if (p0.new) r3 = #0xff                          ; only the 0x80 path remaps the index
d814e060:  r2 |= asl(r4,#0x8)                              ; r2 = full 16-bit cmd_id (for logging)
d814e068:  if (p0) r22 = add(r29,##0x150)
d814e06c:  if (p0) jump 0xd814e0fc                         ; 0x80 path (separate)
d814e070:  if (p0.new) r4 = sxtb(r3)                       ; (0x80 path only)
d814e078:  p0 = cmp.eq(r4,#0x0) ; if (!p0.new) jump 0xd814e5e4   ; <<< HIGH-BYTE GATE
           ;   for non-0x80 cmds: r4 is STILL pkt[3]. If pkt[3]!=0  -> jump ERROR stub 0xd814e5e4
d814e0b4:  r13 = memw(r3<<#0x2 + ##0xc37bc828) ; jumpr r13 ; table index by r3 = pkt[2] (LOW byte)
```
**Trace for ftm_cmd_id = 900 (0x0384):** pkt[2]=0x84, pkt[3]=0x03.
- `0xd814e054`: p0 = (0x03==0x80) = **false**.
- `0xd814e06c/070`: `if(p0)` skipped → r4 stays **0x03**.
- `0xd814e078`: p0 = (0x03==0x0) = **false** → `if(!p0) jump 0xd814e5e4` ⇒ **ERROR STUB. No dispatch.**

Same for 901 (0x0385), 902 (0x0386), 947 (0x03b3), 948 (0x03b4): high byte 0x03 → all rejected. **FACT.**

### 1.2 The error stub `0xd814e5e4` [FACT]
```
d814e5e4:  r1:0 = combine(r2, ##err_ptr) ; jump 0xd814e600   ; r2 = the (unrecognized) cmd_id
d814e600:  call 0xd814d93c
d814e604:  r2 = #0x3 ; memb(r29+#0x160) = r2                 ; FTM error code 3 in the reply
d814e60c:  ... rebuilds cmd_id = pkt[2]|pkt[3]<<8 for the error response ...
```
⇒ a rejected FTM cmd returns an FTM error reply (code 3), never touching an IQ handler. **FACT.**

### 1.3 The dispatch table `@0xc37bc828` is only 0x00..0x80 long [FACT]
Read directly from `modem.b21` (base 0xc3553000):
```
idx 0x00..0x80 : valid code VAs (0xd814e1ec … 0xd814e370); 0xd814e5e4 = default/BAD_CMD stub
idx 0x27       : 0xd814e534   (FTM_LTE arm — the mode-105 RF_TEST entry)
idx 0x81       : 0x545f4652   ("RF_T…" ASCII rodata)  <-- TABLE ENDS; past here is string data
idx 0x84 (900) : 0x54460079   ("y\0FT" ASCII)   -- NOT a handler
idx 0x85 (901) : 0x4154204d   ("M TA" ASCII)   -- NOT a handler
idx 0x86 (902) : 0x52204b53   ("SK R" ASCII)   -- NOT a handler
idx 0xb3 (947) : 0x01020101   -- NOT a handler
idx 0xb4 (948) : 0x01010201   -- NOT a handler
```
Even if the high-byte gate did not exist, indices 0x84/0x85/0x86 fall **off the end** of the
96-entry table into adjacent rodata ⇒ garbage jump target. Double proof of non-implementation. **FACT.**

### 1.4 No legacy IQ-capture handler strings in the image [FACT]
`seg27_dec.bin` (decompressed modem.b27) — exhaustive scan. The ONLY `ftm_*iq_capture*` symbols:
```
0xce6deca0  ftm_lte_iq_capture_get_samples_rsp_pkt_ptr allocation failure   (RF_TEST FETCH rsp alloc)
0xce6ded20  rflte_ftm_iq_capture_prop_action_8bit_iq_buff allocation failure
0xce6ded68  rflte_ftm_iq_capture_prop_action_16bit_iq_buff allocation failure
0xce6e0c28  rflte_ftm_iq_capture_prop_action_get_cfg: rx_path_data_ptr is NULL ...
```
`clade_dec_36m.bin` (code): 0 hits for `iq_capture / acquire_samples / get_samples / get_config`.
`modem.b21` rodata IQ strings are all `ftm_rf_test_iq_capture.c` (mode-105) or `nr5g_ml1_iq_capture`
(NR5G ML1), plus the `[FTM.RFTEST][IQ_CAPTURE][UNPACK/REPACK]` format strings @0xc37c08f4/0949.
**There is NO dedicated `_FTM_IQ_CAPTURE_ACQUIRE_SAMPLES` / `_GET_CONFIG` command handler.** FACT.

⇒ The `ftm_lte_iq_capture_get_samples` name is the *internal* FETCH action of the mode-105 path
(it allocates the DIAG response packet that carries samples). It is **reached via RF_TEST sub-command
0x24 under ftm_cmd 0x27**, NOT via top-level ftm_cmd 902. The legacy 900/901/902 commands those names
came from in QMSL are simply **not compiled into this modem** — exactly like the legacy mode-5 path.

---

## 2. JOB A — WEB: the legacy wire format (for reference / other builds)

The legacy commands are top-level entries of `FTM_Command_ID_Type` (NOT mode-105 sub-commands):
`qlib_defines.h` lines 8589-8595 (enum `FTM_Command_ID_Type`, closes line 8646
`} FTM_Command_ID_Type;`), VERBATIM:
```
/*IQ Capture commands owns 900-915 and 947-948*/
_FTM_IQ_CAPTURE_GET_CONFIG            = 900,
_FTM_IQ_CAPTURE_ACQUIRE_SAMPLES       = 901,
_FTM_IQ_CAPTURE_GET_SAMPLES           = 902,
_FTM_IQ_CAPTURE_ACQUIRE_PRXDRX_SAMPLES= 947,
_FTM_IQ_CAPTURE_GET_PRXDRX_SAMPLES    = 948,
_FTM_IQ_CAPTURE_ANALYZE_SAMPLES       = 903,
```
Config constant: `#define FTM_IQ_SAMPLES_MAX_NUMBER_OF_BYTES 3584` (qlib_defines.h:235).

QMSL client API param order (QMSL_FTM_RF.h, branch main==master, #67 header):
- `QLIB_FTM_IQ_CAPTURE_GET_CONFIG` (3116): in `iRxPath(u16)`; out `MaxSampleSize(u16)`,
  `SampleDataFormat(u16: 0=8bit,1=16bit)`, `SamplingFrequency(u32 Hz)`, `MaxDiagSampleSize(u32)`,
  `FTM_Error_Code(u16)`.
- `QLIB_FTM_IQ_CAPTURE_ACQUIRE_SAMPLES` (3157): in `iRxPath(u16)`, `iSampleSize(u32)`,
  `iCarrierId(u8)`; out `FTM_Error_Code(u16)`.
- `QLIB_FTM_IQ_CAPTURE_GET_SAMPLES` (3204): in `iRxPath(u16)`, `iSampleOffset(u32)`,
  `iSampleSize(u32)` (I/Q pairs, ≤ max_diag_sample_size), `iSampleBufferSize(u16)`; out
  `FTM_Error_Code(u16)`, `SampleBuffer(void*)`. Err: 0 ok /1 fail /2 not-avail /3 illegal off+size.

**Wire framing** (standard QC FTM request; FACT from this image for the header, INFERENCE for the
legacy body field order since the handlers are absent here):
```
pkt[0]      = 0x4B            DIAG_SUBSYS_CMD_F        (FACT, map_diag_core.md)
pkt[1]      = 0x0B            DIAG_SUBSYS_FTM          (FACT)
pkt[2..3]   = u16 LE          ftm_cmd_id               (FACT: indexed at router 0xd814e034)
pkt[4..5]   = u16 LE          ftm_hdr "data_len"/rsvd  (std ftm_pkt_header; 0x0000 ok)
pkt[6..]    = command-specific body (little-endian)
```
> NOTE: on builds that DO implement 900/901/902, the body is the C request struct of
> `ftm_iq_capture_get_config_type` / `_acquire_samples_type` / `_get_samples_type`. Those packed
> structs are NOT in qlib_defines.h (QMSL ships only the client API above); the canonical field
> order is the embedded `ftm_iq_capture.h`, which is not in this public repo. The byte layouts in
> §3 follow the QMSL API param order (the documented semantic order) and are the correct starting
> point to try on a build where the commands exist.

---

## 3. DELIVERABLE — byte-exact packets (for a build that implements them)

> ⚠️ On THIS SM6375 build these will return an **FTM error (code 3) / BAD_CMD** because the router
> rejects high-byte 0x03 (§1.1). They are provided as the alternative to try ONLY if a different
> modem build is used, and to document the wire exactly. All multi-byte fields little-endian.

Let `iRxPath = 0x0000` (Rx0 / primary) and `iCarrierId = 0x00` (carrier 0). Sample size = 1024.

### GET_CONFIG (900 = 0x0384)   — body: u16 iRxPath
```
4B 0B 84 03 00 00 00 00
└┬ └┬ └──┬ └──┬ └──┬────── iRxPath = 0x0000
 │  │    │    │
 │  │    │    └ ftm_hdr data_len/rsvd = 0x0000
 │  │    └ ftm_cmd_id = 0x0384 (900)  [LE]
 │  └ subsys FTM 0x0B
 └ DIAG_SUBSYS_CMD_F 0x4B
```
Response carries: max_sample_size(u16), sample_data_format(u16), sampling_freq(u32),
max_diag_sample_size(u32), ftm_error_code(u16).

### ACQUIRE_SAMPLES (901 = 0x0385)   — body: u16 iRxPath, u32 iSampleSize, u8 iCarrierId
iSampleSize = 1024 = 0x00000400.
```
4B 0B 85 03 00 00 00 00 00 04 00 00 00
└┬ └┬ └──┬ └──┬ └──┬ └────┬──── └┬
 │  │    │    │    │      │      └ iCarrierId  = 0x00
 │  │    │    │    │      └ iSampleSize = 0x00000400 (1024) [LE]
 │  │    │    │    └ iRxPath = 0x0000
 │  │    │    └ ftm_hdr data_len/rsvd = 0x0000
 │  │    └ ftm_cmd_id = 0x0385 (901)
 │  └ 0x0B
 └ 0x4B
```
Response: ftm_error_code(u16). (Fills the internal capture buffer with 1024 IQ pairs.)

### GET_SAMPLES (902 = 0x0386)   — body: u16 iRxPath, u32 iSampleOffset, u32 iSampleSize, u16 iSampleBufferSize
Fetch first window: offset 0, 256 IQ pairs, buffer 1024 bytes (256*2*2 for 16-bit).
iSampleOffset=0x00000000, iSampleSize=0x00000100 (256), iSampleBufferSize=0x0400 (1024).
```
4B 0B 86 03 00 00 00 00 00 00 00 00 00 01 00 00 00 04
└┬ └┬ └──┬ └──┬ └──┬ └────┬──── └────┬──── └──┬
 │  │    │    │    │      │          │        └ iSampleBufferSize = 0x0400 (1024)
 │  │    │    │    │      │          └ iSampleSize   = 0x00000100 (256 pairs) [LE]
 │  │    │    │    │      └ iSampleOffset = 0x00000000 [LE]
 │  │    │    │    └ iRxPath = 0x0000
 │  │    │    └ ftm_hdr data_len/rsvd = 0x0000
 │  │    └ ftm_cmd_id = 0x0386 (902)
 │  └ 0x0B
 └ 0x4B
```
Response: ftm_error_code(u16) + sample_buffer[] (IQIQ interleaved, signed two's-complement LE;
bytes_per_sample = 2 for 8-bit, 4 for 16-bit per GET_CONFIG.sample_data_format). Loop GET_SAMPLES
advancing iSampleOffset until all pairs fetched (≤ max_diag_sample_size per call).

Python builder:
```python
import struct
def ftm(cmd, body=b''):            # cmd = ftm_cmd_id (900/901/902); body LE
    return bytes([0x4B,0x0B]) + struct.pack('<H', cmd) + struct.pack('<H',0) + body
GET_CONFIG     = ftm(900, struct.pack('<H', 0))                                   # iRxPath=0
ACQUIRE_1024   = ftm(901, struct.pack('<H',0) + struct.pack('<I',1024) + bytes([0]))
GET_SAMPLES_0  = ftm(902, struct.pack('<H',0) + struct.pack('<I',0) + struct.pack('<I',256) + struct.pack('<H',1024))
```

---

## 4. CROSS-CHECK vs the mode-105 path (why this matters)

| | LEGACY 900/901/902 | MODE-105 RF_TEST IQ_CAPTURE (present) |
|---|---|---|
| Selector | top-level `ftm_cmd_id` 900/901/902 @pkt[2..3] | `ftm_cmd 0x27` (FTM_LTE) @pkt[2..3] + sub-cmd 0x10xx @pkt[4..5] + field TLVs |
| In THIS build | **ABSENT** — router rejects hi-byte 0x03 (§1.1); table ends @0x80 (§1.3); no handler strings (§1.4) | **PRESENT** — `rflte_ftm_iq_capture_prop_action_*`, FETCH rsp via subsys-cmd 0x24 (`iq_delivery_channel.md`) |
| Verdict | NOT usable | the only option; fix setup to get num_captures>0 |

⇒ Switching to the legacy commands will NOT help. The `num_captures=0` is a state/arming issue on
the mode-105 path (carrier not active / ACQUIRE not armed), per `iq_capture_num_captures.md` and
`iq_delivery_channel.md` §5 (needs cal-mode + active carrier + ACQUIRE before FETCH).

---

## 5. REPRODUCE
```bash
# Router low-byte index + high-byte gate (rejects 0x03xx):
/tmp/modemre/dis36.sh 0xd814e034 0x90        # see 0xd814e050..0b4
/tmp/modemre/dis36.sh 0xd814e5e4 0x40        # error stub (FTM err code 3)
# Table @0xc37bc828 ends at idx 0x80 (read from modem.b21, base 0xc3553000):
python3 - <<'PY'
import struct; d=open('/tmp/modemre/modem.b21','rb').read(); B=0xc3553000; T=0xc37bc828
e=lambda i: struct.unpack_from('<I',d,(T-B)+i*4)[0]
for i in (0x27,0x80,0x81,0x84,0x85,0x86,0xb3,0xb4): print(hex(i),hex(e(i)))
PY
# Decompress seg27 and confirm no legacy IQ-capture command handler:
python3 -c "import zlib;open('/tmp/modemre/seg27_dec.bin','wb').write(zlib.decompress(open('/tmp/modemre/modem.b27','rb').read()))"
grep -aoE 'ftm_(lte_)?iq_capture[a-z_]*' /tmp/modemre/seg27_dec.bin | sort -u
```

## 6. FACT / INFERENCE / UNKNOWN
**FACT**
- Router `0xd814e034` indexes `@0xc37bc828` by `pkt[2]` (low byte); high-byte gate @0xd814e078
  rejects any `pkt[3]` that is not 0x00 (or 0x80). 900/901/902/947/948 all have pkt[3]=0x03 → rejected.
- Table `@0xc37bc828` valid only idx 0x00..0x80; idx 0x84/0x85/0x86 are ASCII rodata.
- Error stub `0xd814e5e4` returns FTM error code 3.
- No `ACQUIRE_SAMPLES/GET_CONFIG/get_samples` *command* handler strings in code or seg27; only the
  mode-105 `rflte_ftm_iq_capture_prop_action_*` + FETCH rsp-pkt alloc string.
- QMSL enum/consts: 900/901/902/947/948/903 (qlib_defines.h:8589-8595), MAX_BYTES 3584 (:235).
- DIAG/FTM header `4B 0B <cmd_id:u16LE> …` (map_diag_core.md, ftm_set_mode.md, ftm_common_handler.md).

**INFERENCE (hard)**
- Exact body field order of the three legacy packets (§3) = QMSL API param order (GET_CONFIG: rxpath;
  ACQUIRE: rxpath,u32 size,u8 carrier; GET_SAMPLES: rxpath,u32 off,u32 size,u16 bufsz). Correct for a
  build that implements them; unverifiable here because the handlers are absent.

**UNKNOWN**
- The embedded `ftm_iq_capture.h` packed-struct padding/order on builds that DO ship 900/901/902
  (not in the public QMSL repo). Would need an MSM8974-era / build-specific embedded header to be byte-certain.
```
