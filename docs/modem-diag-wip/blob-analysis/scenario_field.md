# scenario_field — Does the TECH_ENTER SCENARIO TLV (field 3) write session+0x89a8?

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Primary image:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`, 36 MB; valid code ~`0xd8000000..0xda440000`, RF driver code extends to ~`0xd9660000`).
**Tools this pass:** `dis36.sh`, `/tmp/refs36.py` (call/jump xref over the FULL 36 MB), `/tmp/scan89a8_all.py` (immext+store scan for `+0x89a8` over the FULL 36 MB), `/tmp/reach2.py` (static call BFS).
**Legend:** **FACT** = byte/instruction read at a VA · **INFERENCE** = deduction with hard anchors · **UNKNOWN** = runtime/RAM/out-of-image only.

---

## 0. TL;DR — DIRECT ANSWERS

1. **SCENARIO (field 3) does NOT write `session+0x89a8`.** No instruction in the TECH_ENTER unpack/handler/commit path stores to `session+0x89a8`. Every one of the 12 image-wide writers of `session+0x89a8` lives in the per‑tech **RF driver** (LTE/NR `rf_mode`/phone‑mode setters, `0xd84b1118`/`…2124`, `0xd8d3ee24`, `0xd8e76adc`, …), and **none is reachable by a direct call from the TECH_ENTER path**, and none takes its stored value from a TLV. **FACT.**
2. **The prior claim "zero stores to +0x89a8 in the image" was WRONG.** That earlier scan only covered `_big1.txt` (0xd8140000..0xd82c0000). A full-image scan finds **12 stores** to `+0x89a8`. They set the byte to **0, 2, or 7** based on **internal RF-driver mode state**, not on any FTM TLV. **FACT (correction).**
3. **`session+0x89a8 == 7` is produced internally by the LTE RF driver** (e.g. `0xd8d3ee24` writes 7 when an internal selector `r20==3`; `0xd84b2124` writes 7 vs 0 from an internal sub-config query at `0xd80d1750`). It is the `rf_mode`/phone-mode byte (seg27 strings: `FTM_RF_MODE_CAL`, `NR5G_LL1_CAL_FTM_RF_MODE_CAL`, `rf_mode == …`). There is **no SCENARIO-enum → 0x89a8 translation** anywhere; the LTE‑LL1 `scenario_id` in seg27 is a *different* concept (LTE bundle-message id), not the TECH_ENTER SCENARIO TLV. **FACT + INFERENCE.**
4. **`session+0xc` is NOT written by TECH_ENTER either.** The whole TECH_ENTER chain (unpacker → handler `0xd8174758` → per-tech commit `0xd81e5cec`/`0xd81dfecc`) only **reads** `session+0xc` (as a gate, `==2`) and only **reads** `session+0x89a8`. It writes neither. `session+0xc` is the session-active state set by a prior session/RF-mode step. **FACT.**
5. **Where SCENARIO actually goes:** SCENARIO (field 3) is parsed into the flattened FTM request struct and, together with SUB (field 1), is packed and passed **as call arguments** to the LTE per‑tech enter function (handler `0xd8174758` @`0xd81747c8..0xd8174810`). It is a per-tech config parameter consumed by the LTE enter/config code; it is **not** stored into `session+0x89a8` or `session+0xc`. **FACT (arg packing) + INFERENCE (config-parameter role).**
6. **Conclusion:** **TECH_ENTER alone CANNOT set `session+0x89a8=7` or `session+0xc=2`, for ANY value of SCENARIO (including 7).** Sending SCENARIO=7 will NOT arm the cal branch. Both gate bytes are armed by the prior **FTM set-mode / RF phone-mode** path (out-of-image / runtime binding), exactly as `tech_state_gate.md` and `tech_enter_callback.md` concluded — but now with the added FACT that the actual `+0x89a8` writers are the in-image RF-driver setters at `0xd84b…/0xd8d3…/0xd8e7…` (value chosen internally, not from a TLV). **FACT + INFERENCE.**

---

## 1. THE TECH_ENTER TLV PATH — dispatch → parse → store (FACT)

### 1.1 Wire → dispatch (unchanged, re-confirmed)
```
DIAG 4B 0B  27 00  0D 00 ...     ; SUBSYS_FTM, ftm_cmd 0x27 (FTM_LTE), RFDEBUG sub 0x000D (TECH_ENTER)
  ftm_cmd table @0xc37bc828[0x27] = 0xd814e534 (FTM_LTE)
  FTM_LTE routes the RFDEBUG group via unpacker 0xd8178a10 (immext'd @0xd814e5d4)
  RFDEBUG dispatch @0xd816d2d4: sub=memub(pkt+4)|memub(pkt+5)<<8; table @0xca65b414 stride 0xc; slot 13 -> handler 0xd8174758
```
**FACT.**

### 1.2 Field-id groups & names (FACT, from `rftest_tlv_groups_raw.txt`)
- **GROUP 16 @ 0xc906c8e8 (4 fields)** = the TECH_ENTER group: `[0]UNASSIGNED [1]SUB [2]TECH [3]SCENARIO`.
- Name-pointer array @ `0xc906c8e8` (b23 rodata): `[1]=0xc414c20d "SUB"`, `[2]=0xc40d24f5 "TECH"`, `[3]=0xc414c211 "SCENARIO"`. The name array carries **no offsets**; it is used only for log/error text (`memw(r1<<2+##0xc906c8e8)` @`0xd81880e8`). **FACT.**

### 1.3 The flattened request struct read by the handler (FACT)
Handler `0xd8174758`:
```
d8174764:  r16 = r0 ; r2 = memw(r0+#0x4)
d817476c:  r19 = memw(r16+#0x0)                 ; r19 = flattened FTM request struct
d817479c:  r18 = memub(r19+#0x12)               ; <<< TECH value (raw TLV)  -> +0x12
d81747a0:  call 0xd8169618 (r0=r18)             ; map raw TECH -> tech-index (table @0xc37bdfe0)
d81747c8:  r2 = add(r19,#0x16)                  ; <<< field region B  (+0x16)
d81747cc:  r1 = add(r19,#0x1a)                  ; <<< field region C  (+0x1a)
d81747e4..d8174800: pack memub(r2+0..3)/memub(r1..) into words on stack
d8174804:  r2 = memw(r17+#0x0) ; r1 = memw(r17+#0x4)   ; r17 = per-tech record[tech]
d817480c:  callr r2                             ; <<< LTE per-tech enter fn(args = packed +0x16/+0x1a)
```
- **TECH → struct +0x12** (byte). **FACT.**
- **SUB and SCENARIO occupy the +0x16 / +0x1a regions** and are **packed into the call arguments** to the LTE per-tech enter function — NOT written to `session`. **FACT (packing) + INFERENCE (which of +0x16/+0x1a is SUB vs SCENARIO; both are passed as config args, neither reaches a session mode byte).**
- Mapper `0xd8169618`: `r0 = memw(tech<<2 + ##0xc37bdfe0)`; guards `tech+1 <= 8`, else returns 0x15 (err). TECH=1→tech-index for LTE. **FACT.**

### 1.4 Per-tech commit / enter-writer only READ the session mode bytes (FACT)
`0xd81e5cec` (enter-writer, writes tech-state array):
```
d81e5d1c: r3 = memw(r2+#0xc)                    ; READ session+0xc
d81e5d20: if (r3 != 2) skip                     ; gate (needs ==2)
d81e5d2c: r17 = memub(r2+#0x12)                 ; tech-index
d81e5d54: memb(r17<<3 + ##0xca7897b0) = r3(=1)  ; writes TECH-STATE array (NOT session)
d81e5d5c: r2 = memb(r2 + ##0x89a8)              ; READ session+0x89a8 (post-check ==7 -> err 0x10)
```
`0xd81dfecc` (commit):
```
d81dff14: r16 = memw(r2+#0xc)                   ; READ session+0xc
d81dff20: r3  = memb(r2+##0x89a8)               ; READ session+0x89a8
d81dff30: cmp.eq(r3,#7) ...                     ; branch on ==7 (READ only)
```
**Neither writes `session+0x89a8` or `session+0xc`. Both only read them. FACT.**

---

## 2. WHO ACTUALLY WRITES `session+0x89a8` (FACT — full-image scan)

Full 36 MB immext+store scan (`/tmp/scan89a8_all.py`) — **12 writers, all in RF-driver code**, none in the FTM/TLV parse region (0xd814xxxx–0xd818xxxx), none in the RFDEBUG/TECH_ENTER handler region:

| VA | Instruction | Value written | Chosen by |
|---|---|---|---|
| `0xd84b1114/1118` | `memb(r3+##0x89a8)=r2` | `0` | reset arm (LTE fn `0xd84af364`) |
| `0xd84b1314/1318` | `memb(r22+##0x89a8)=r2` | `0` | same fn |
| `0xd84b2120/2124` | `memb(r16+##0x89a8)=r2` | **`7`** if `memw(r29+0x1c)!=0` (result of `0xd80d1750` sub-config query), else `0` | LTE fn `0xd84af364` |
| `0xd8d3ee20/ee24` | `memb(r0+##0x89a8)=r2` | **`7`** when internal selector `r20==3` | RF fn `0xd8d3edf0`+ |
| `0xd8e76ad0/adc` | `memb(r2+##0x89a8)=r3` | **`2`** (=FTM_RF_MODE_CAL) | RF fn `0xd8e76aa0`+ (also reads/compares `==7` first) |
| `0xd8f3ffb0` | `memb(...+0x89a8)=r?` | mode | RF driver |
| `0xd90c2bc8/2bcc` | `memb(r16+##0x89a8)=r18` | mode (also `memw(r16+0)=8`) | RF driver |
| `0xd912b2ac` | `memb(...+0x89a8)=` | mode | RF driver |
| `0xd91590c8` | `memb(...+0x89a8)=` | mode | RF driver |
| `0xd91b38dc` | `memb(...+0x89a8)=` | mode | RF driver |
| `0xd96531fc` | `memb(...+0x89a8)=` | mode | RF driver |
| `0xd965c388` | `memb(...+0x89a8)=` | mode | RF driver |

Key facts about the value:
- In every case the destination base register is the **session** (e.g. `0xd84b2124`: `r16` from `call 0xd81df9f8`; the LTE object session pointer). **FACT.**
- The stored value (0/2/7) is selected by **internal RF-driver state**, never from a TLV byte:
  - `0xd8d3ee24`: `r2=#0x7` guarded by `cmp.eq(r20,#0x3)` (an internal mode selector), **not** a SCENARIO field. **FACT.**
  - `0xd84b2124`: `r2=#0x7` only when the `0xd80d1750` sub-config query returns non-zero (internal), else `0`. **FACT.**
  - `0xd8e76adc`: writes `2` (= `FTM_RF_MODE_CAL`). **FACT.**
- All 12 writer functions have **no static callers** (`/tmp/refs36.py` → 0 CALLS/JUMPS): they are invoked via **runtime per-tech RF vtables** (same pattern as the LTE record `0xca79a820` in `tech_enter_callback.md`). **FACT.**

**Reachability check (`/tmp/reach2.py`, direct-call BFS over 36 MB):** none of
`{commit 0xd81dfecc, callback worker 0xd8247574, enter-writer 0xd81e5cec, handler 0xd8174758, RF-config chain 0xd84d389c/0xd84d3a6c/0xd824163c/0xd824d150}` reaches the `+0x89a8=7` writer function `0xd84af364` by direct calls. (Deeper `callr` edges are not statically resolvable, but the callback report already established the RF-config chain reaches the resident driver only through out-of-image code.) **FACT.**

---

## 3. VALUE MAPPING — is there a SCENARIO → 0x89a8==7 translation? (FACT/INFERENCE)

- **No.** There is no code that reads a TLV field and writes it (translated or not) to `session+0x89a8`. The `7` is a literal `#0x7` chosen by internal RF-driver predicates (`0xd8d3ee1c cmp.eq(r20,#3)`, `0xd84b2xxx` query result), independent of the TECH_ENTER TLV payload. **FACT.**
- The seg27 string `scenario_id` (e.g. `LTE_LL1_BUNDLE_MSG_RF_TUNE_ENV_MODE_CHANGE`, `bundle.scenario_id`) refers to **LTE LL1 bundle message scenario ids**, a different runtime concept from the DIAG TECH_ENTER SCENARIO TLV. Do not conflate them. **FACT (string) + INFERENCE (distinct concept).**
- Therefore **there is no SCENARIO value (0/1/7/anything) that lands in `session+0x89a8`.** SCENARIO=7 does not become `0x89a8=7`. **FACT.**

---

## 4. `session+0xc` (needs ==2) (FACT)

- TECH_ENTER only **reads** `session+0xc` as a gate (`0xd81e5d20 if(!=2) skip`, `0xd81dff14`). It does **not** write it, from any TLV. **FACT.**
- `+0xc` is a small mode/state enum (0/1/2/3). Image-wide it is written by generic session/state machinery (2174 `memw(rX+#0xc)=…` stores across the FTM window alone; the specific session-active `=2` write belongs to the session/RF-mode-start step, not TECH_ENTER). Consistent with `tech_state_gate.md §2` "session->0xc==2 requires a prior session-start". **FACT (TECH_ENTER doesn't write it) + INFERENCE (set by prior step).**

---

## 5. CAN TECH_ENTER ALONE ARM BOTH GATES?

**NO.** For any SUB/TECH/SCENARIO combination:
- `session+0x89a8` is written only by the RF-driver phone-mode setters (§2), which are not on the TECH_ENTER call path and derive the value from internal RF state — not from SCENARIO.
- `session+0xc` is not written by TECH_ENTER at all.

**Missing piece:** a prior **FTM set-mode / RF phone-mode** command that puts the modem in the LTE cal/RF phone-mode. That is what drives the RF-driver vtable functions in §2 to set `session+0x89a8 = 7` (and `= 2` for the cal `rf_mode`), and sets `session+0xc = 2`. The exact DIAG sub-cmd/TLV of that set-mode is **UNKNOWN statically** (runtime FTM dispatch binding), matching `ftm_set_mode_verified.md` and `tech_state_gate.md §5 PASO 0`.

**Do not bother testing SCENARIO=7** — it cannot reach `session+0x89a8`. The one-byte live test that actually matters is a DIAG memory-write (peek/poke) of `session+0x89a8 = 7` and `session+0xc = 2` directly, OR issuing the set-mode command first.

---

## 6. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- TECH_ENTER handler `0xd8174758`: TECH=`memub(r19+0x12)` @`0xd817479c`; SUB/SCENARIO in the `+0x16`/`+0x1a` regions `add(r19,0x16)`@`0xd81747c8`, `add(r19,0x1a)`@`0xd81747cc`, packed into call args, `callr` per-tech fn @`0xd817480c`. No store to session mode bytes.
- Tech mapper `0xd8169618`: `r0=memw(tech<<2+##0xc37bdfe0)`, guard `<=8`.
- Commit `0xd81dfecc`: READS `session+0xc`@`0xd81dff14`, `session+0x89a8`@`0xd81dff20`, `cmp.eq(#7)`@`0xd81dff30`. No writes.
- Enter-writer `0xd81e5cec`: READS `session+0xc`@`0xd81e5d1c` (gate `!=2` skip @`0xd81e5d20`), READS `session+0x89a8`@`0xd81e5d5c`; writes only the tech-state array `memb(tech<<3+##0xca7897b0)=1`@`0xd81e5d54`.
- **12 writers of `session+0x89a8`** exist image-wide, all RF-driver: `0xd84b1118`, `0xd84b1314`, `0xd84b2124`(=7 internal), `0xd8d3ee24`(=7 when `r20==3`), `0xd8e76adc`(=2), `0xd8f3ffb0`, `0xd90c2bc8`, `0xd912b2ac`, `0xd91590c8`, `0xd91b38dc`, `0xd96531fc`, `0xd965c388`. All value from internal state, none from a TLV, none statically-called (runtime vtable).
- `+0x89a8=7` writer host function `0xd84af364` (prologue @`0xd84af36c` `call 0xd814e6ac; allocframe(#0x88)`); not reachable by direct call from any TECH_ENTER-path root (`/tmp/reach2.py`).
- Group-16 (TECH_ENTER) name table @`0xc906c8e8` = `{UNASSIGNED, SUB(0xc414c20d), TECH(0xc40d24f5), SCENARIO(0xc414c211)}`; used only for log text @`0xd81880e8`.
- seg27 strings: `FTM_RF_MODE_CAL`, `NR5G_LL1_CAL_FTM_RF_MODE_CAL`, `rf_mode == (uint8)NR5G_LL1_CAL_FTM_RF_MODE_CAL` (0x89a8 = rf_mode/phone-mode byte).

**INFERENCE (hard-anchored)**
- SCENARIO (field 3) is an LTE per-tech config parameter passed as a call argument to the LTE enter function; it is not a session mode/scenario selector for `+0x89a8`.
- `session+0x89a8` and `session+0xc` are armed by the FTM set-mode / RF phone-mode path, which drives the §2 RF-driver setters via runtime vtables.
- The value `7` at `0x89a8` corresponds to the LTE cal/RF-config phone-mode selected internally (e.g. selector `==3` at `0xd8d3ee1c`), not to SCENARIO=7.

**UNKNOWN (runtime / RAM / out-of-image binding)**
- The exact DIAG sub-cmd/TLV of the FTM set-mode that puts the modem in the phone-mode which makes the §2 setters write `0x89a8=7` / `0xc=2`.
- Which internal condition (`r20==3` at `0xd8d3ee1c`; `0xd80d1750` query at `0xd84b2xxx`) is satisfied for LTE cal on the live target (needs live state).
- Live values of `session+0x89a8`, `session+0xc` (DIAG peek only).
- Exact assignment of SUB vs SCENARIO to struct `+0x16`/`+0x1a` (both are config args; neither is a session mode byte — verified sufficient for this question).

---

## 7. REPRODUCE
```bash
cd /tmp/modemre
# TECH_ENTER handler: TECH@+0x12, SUB/SCENARIO packed as call args (no session store)
./dis36.sh 0xd8174758 0x120
# Commit + enter-writer only READ 0x89a8 / +0xc
./dis36.sh 0xd81dfecc 0x120
./dis36.sh 0xd81e5cec 0xc0
# FULL-IMAGE writers of session+0x89a8 (12; all RF-driver, value 0/2/7 from internal state)
python3 /tmp/scan89a8_all.py
./dis36.sh 0xd84b20e0 0x60      # 0xd84b2124 writes 7 vs 0 from internal query
./dis36.sh 0xd8d3edf0 0x40      # 0xd8d3ee24 writes 7 when r20==3
./dis36.sh 0xd8e76aa0 0x50      # 0xd8e76adc writes 2 (FTM_RF_MODE_CAL)
# None statically called; none reachable from TECH_ENTER path by direct calls
python3 /tmp/refs36.py 0xd84af364
python3 /tmp/reach2.py
```
