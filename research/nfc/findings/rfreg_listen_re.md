# S3FWRN5 / S3NRN4V — RF register (2F2A) init path vs the NFC-A LISTEN front-end that never lights (rhodep, eSE MIFARE emulation)

Target: Moto G82 (rhodep), Samsung S3NRN4V CLF, HAL `nfc_nci_sec.so`
(ARM64, 117 KB, BuildID f4972906…, **has full .dynsym — not fully stripped**),
kernel `net/nfc/nci` + an **already-modified** `s3fwrn5` driver
(`/tmp/nfcre7/s3fwrn5-driver/`, not pristine mainline — it already contains a
dual-opcode rfreg push).

Every claim is tagged **FACT** (binary offset / blob byte / log line / source
line), **INFERENCE** (derived), or **UNKNOWN**.

---

## 0. TL;DR — the headline finding overturns the working hypothesis

**The RF register blobs ARE already being pushed to the chip, successfully, at
init, by this driver — and the NFC-A listen front-end still does not light.**
So the state.txt / felica_suppress_re §8 hypothesis ("mainline s3fwrn5 does not
push the RF registers → analog listen-A never configured") is **DISPROVEN by the
live log**. Evidence (`aonly_dmesg.log`):

- `2F 28` (FW_CFG clock) sent, RSP ok. **FACT** (`:13-17`).
- `2F 2A 01` START → ok; `2F 2A 02 <idx> <252B>` × **14** → all ok;
  `2F 2A 03 <cksum>` STOP → **"rfreg configuration update: success (checksum
  0x5b9a)"**. **FACT** (`:19-139`, 14× `opcode 0xf2a, plen 254`).
- 14×252 + 40 = **3568 bytes = 3232 (rfreg) + 336 (swreg)** — the *entire*
  concatenated blob was transferred. **FACT** (arithmetic on the frame count).

The two real gaps the live log exposes, neither of which is the rfreg blob:

1. **`NFCEE_MODE_SET` (`22 01`) is NEVER sent.** The eSE (0x83) is discovered by
   `NFCEE_DISCOVER` but never moved to the ENABLED state, so routing NFC-A to it
   cannot fire. state.txt line 6 ("NFCEE_MODE_SET(0x83) ok") **does not match
   this capture** — there is no `0x22xx` opcode anywhere in the run. **FACT**
   (`grep -c 0x22 aonly_dmesg.log` = 0; opcode list has no 0x2201).
2. The clock/FW_CFG the driver sends is a **single speed byte** (`2F 28` plen 1),
   whereas the Android HAL sends `2F 28` as a **multi-key TLV** (CLK_SPEED +
   CLK_TYPE + CLK_REQ). This is a candidate reason the analog side is mistuned
   even though the digital side answers everything. **INFERENCE**, see §6.

Read the rest for the byte-exact 2F2A format (so you can trust the current
driver push), the blob decode, and the concrete next steps.

---

## 1. Proprietary (2Fxx) commands the HAL emits at init — order and payload source

From `.dynsym` + `objdump -d`. Prop opcodes are built as `mov wN,#imm` with the
low byte 0x2F (stored LE as GID/OID by `strh`). The three RF/clock ones and their
payload origin (**FACT**, offsets cited):

| on-wire opcode | HAL function (offset) | payload source | notes |
|---|---|---|---|
| `2F 17` | `hal_nci_send_setSWAPITrace` (`0x1014c`) | debug arg | SWAPI trace, not RF |
| `2F 28` | `hal_nci_send_prop_fw_cfg` (`0x105c0`) | **conf keys** `FW_CFG_CLK_SPEED`@0x6a77, `FW_CFG_CLK_TYPE`@0x7aa0, `FW_CFG_CLK_REQ`@0x605a | TLV: `2F 28 <count> {id,len,val}…`; per-key `len=1` (`strb #1 @sp+14`). **Clock only.** |
| `2F 22` | `hal_vs_rfreg_update` (`0x161b0`) | VS_INFO blob ptr `[+320]`, len `[+328]` | legacy single-opcode RF reg set (chip ≠ 0x74 and < 0x84) |
| `2F 2A` | `hal_vs_rfreg_update_dual_option` (`0x1640c`) + the VS state machine | VS_INFO merged blob ptr `[+384]`, len `[+392]` | **dual-opcode RF reg set (chip == 0x74 OR ≥ 0x84)** |

Order the HAL runs the RF register transfer (from `nfc_hal_vs_sm` @0x14524,
gated on chip id byte `[VS_INFO+148]`; the two magic values are **0x74** and
**0x84** — `cmp #0x74`/`cmp #0x84` at `0x146e4/0x146ec`, `0x14734/0x1473c`, etc.):

1. **Clock** `2F 28` (from `hal_nci_send_prop_fw_cfg`, driven during open).
2. **`hal_vs_check_rfreg_update`** — compares stored version vs blob version
   (`nfc.fw.rfreg_ver` property vs the blob footer), decides whether to push.
   Log: *"hal_vs_check_rfreg_update return false"* skips it. **FACT** (`0x146d8`).
3. **`hal_vs_merge_rf_image`** — malloc(len_rfreg+len_swreg), `memcpy` rfreg then
   swreg → `[VS_INFO+384]`, total len → `[VS_INFO+392]`. **Only done for the
   0x74 / ≥0x84 (dual) chips.** **FACT** (`0x146f8`, function @0x16128).
4. **START** — `hal_vs_nci_send(0x2a, {0x01}, 1)` → `2F 2A 01`. Log *"Start
   setting RF register!!"*. **FACT** (`0x148f8`, string @0x655a).
5. **SET loop** — `hal_vs_rfreg_update_dual_option` sends `2F 2A 02 <idx> <≤252B>`
   until the merged blob is exhausted; accumulates a running u32 checksum.
   **FACT** (`0x1640c`).
6. **STOP** — `hal_vs_nci_send`-style build: `2F 2A 03 <cksum_lo> <cksum_hi>`
   (sub=0x03, 16-bit LE checksum, total len 3). Log *"Stop setting RF
   register!"* / *"RF register check sum is 0x%04X"*. **FACT** (`0x14f8c-0x14fac`).
   (On a set failure it emits `2F 2A 03` len 1 as an abort; on retry it re-sends
   `2F 2A 01` — *"Restart setting register!!"* @0x14bb4. **FACT**.)

For the **legacy (non-dual) chips** the same steps use distinct opcodes:
`2F 26` START (`NFC_HAL` state 0x26), `2F 22 <idx> <≤252B>` SET, `2F 27 <cksum16>`
STOP. **FACT** (`0x14744` state 0x26; `hal_vs_rfreg_update` @0x161b0; `0x14a88`
`mov w0=0x27`).

There is **no** proprietary "enable NFC-A listen" / "enable card emulation" /
"disable FeliCa" opcode. Confirms felica_suppress_re §3.1. **FACT.**

### How the payload file is chosen (config keys, decoded)

`nfc_hal_get_update_image(type, factory, &flag)` (@0x1121c) maps `type` →
conf-string key via jump table @0x7fa0 (decoded):

| type | conf key (string offset) | this board's value (`libnfc-sec-vendor.conf`) |
|---|---|---|
| 1 | `FW_FILE_NAME` (0x66fa)   | `sec_s3nrn4v_firmware.bin` |
| 2 | `RF_FILE_NAME` (0x715f)   | *(unset on SN4V)* — the `rfreg` variant |
| 3 | `RF_HW_FILE_NAME` (0x6456)| `sec_s3nrn4v_hwreg.bin` |
| 4 | `RF_SW_FILE_NAME` (0x6979)| `sec_s3nrn4v_swreg.bin` |

`hal_vs_get_rf_image` (@0x15910) loads the **primary** image (type 2 `RF_FILE`
in normal mode, or type 3 `RF_HW_FILE` — the code path picks between them) into
`[VS_INFO+312]/+320/+328`, and **always** the **swreg** (type 4) into
`[+360]/+368/+376`. `merge` then concatenates them for the dual push. **FACT.**

Directory prefix keys also exist: `RF_DIR_PATH`, `RF_HW_DIR_PATH`,
`RF_SW_DIR_PATH`, `FW_DIR_PATH` (all `/vendor/etc/` or `/vendor/firmware/`).
**FACT** (strings + conf `:25-30`).

> **Important equivalence:** on this SN4V board the "rfreg" file and the "hwreg"
> file are the **same content** (see §2). The HAL calls it `RF_HW_FILE` (hwreg);
> the driver calls it `sec_s3fwrn5_rfreg.bin`. Same bytes, different label.

---

## 2. RF blob format (decoded)

Files in `/tmp/nfcre7/vendor-blobs/` (md5):

```
299bdf922a8aaa0c3dcda8f1d22cdd98  sec_s3fwrn5_rfreg.bin   (3232 B)
299bdf922a8aaa0c3dcda8f1d22cdd98  sec_s3nrn4v_hwreg.bin   (3232 B)  <-- IDENTICAL to rfreg
865ac14e9ef54a84ee24ea6b89bca1cd  sec_s3fwrn5_swreg.bin   (336 B)
865ac14e9ef54a84ee24ea6b89bca1cd  sec_s3nrn4v_swreg.bin   (336 B)   <-- IDENTICAL to the other swreg
```

**FACT:** there are only **two distinct blobs** — a 3232-byte RF-register image
and a 336-byte SW-register image. `rfreg`==`hwreg`; the two `swreg` are equal.
So "rfreg vs hwreg, same 3232 size" (question 2) → **they are byte-identical;
the difference is only which opcode/label the loader uses, not the content.**

### 2.1 Container framing (matches `hal_vs_get_rf_image` parse) — FACT

`get_rf_image` reads: `header_len = data[0]`; header = `data[1 .. 1+header_len]`;
register payload = `data[1+header_len ..]`.

- **rfreg/hwreg (3232 B):** `header_len = 0x02`, header = `00 00`, payload =
  3229 B from offset 3. Footer (last 16 B): `65 03 6a 00 00 82 10 10 34 08 02 02
  44 45 46 00` — trailing `44 45 46 00` = ASCII **`"DEF\0"`** (default-profile
  tag); `65 03 6a` looks like a version/type stamp. **FACT.**
- **swreg (336 B):** `header_len = 0x04`, header = `00 01 07 03`, payload = 331 B
  from offset 5. Footer: `… 65 03 66 01 01 7c 0f 0a 04 2b 01 00 44 45 46 00` —
  same `"DEF\0"` tag, version `65 03 66`. **FACT.**

### 2.2 RF-register record layout — FACT (structure) / INFERENCE (semantics)

The RF payload opens with a run of **fixed 52-byte (0x34) records** (12 of them
detected by the recurring separator `08 00 00 00 02 00 00 00 00 00 00 00 00`, at
data offsets 0x2d,0x61,0x95,… step **52**). A record (rec0 vs rec1 differ only in
2 bytes — a register address byte and a value nibble):

```
rec0: 00 00 00 00 00 00 | 84 7f f2 02 e0 02 82 80 d2 54 2d 95 91 10 52 |
      00 00 02 00 00 00 00 00 00 14 f8 80 fe 00 00 c0 | 1f 40 20 00 |
      00 00 00 00 08 00 00 00 02 00 00
diff rec0↔rec1: byte[6] 0x84→0xc4 (addr), byte[19] 0x10→0x28 (value)
```

After ~offset 0x2a0 the format changes to denser blocks (e.g. the long
`f5 63 09 00`/`ed 63 09 00`/`eb 43 09 00` tables at 0x550-0x730 — analog gain /
timing sweeps keyed by a technology index) and short parameter arrays. This is
**firmware-internal analog calibration data**, not host-readable NCI TLVs.
**FACT** (structure) / **INFERENCE** (that these are analog RF-tuning registers).

### 2.3 Is there a "LISTEN NFC-A" profile inside? — INFERENCE / UNKNOWN

- No section index, no per-technology header, no ASCII profile names — only the
  single `"DEF"` (default) tag at the very end. **FACT.**
- The blob is applied **as a whole**; the firmware selects poll-A / listen-A /
  poll-F / listen-F register banks **internally** at RF-config time. There is no
  host-visible flag or offset that "turns on listen-A". The differing values in
  the 52-byte records (`1f 40 20` vs `87 4f a0` vs `10 40 20`, and the
  `xx 63 09`/`xx 43 09`/`xx 31 09` families) are almost certainly the per-mode
  banks (A vs B vs F, poll vs listen), but **which record is listen-A is not
  determinable from this blob alone** — it needs the firmware's register map.
  **INFERENCE / UNKNOWN.**

> Practical consequence: you cannot "select the listen-A profile" — you push the
> whole `DEF` blob, which is exactly what both the Android HAL and (already) this
> driver do. There is nothing more to select here.

### 2.4 swreg contents — INFERENCE

The 336-byte swreg is firmware behaviour config, not analog: visible fields like
`03 e8`=1000, `00 fa`=250, `50 01 00` (looks like an LF_PROTOCOL_TYPE default),
`44 04`, `3f 01`, and a table of doubling values `…08 00,10 00,20 00,40 00,
0100,0200,0400,0800,1000,2000,4000` (bitmasks/timeouts). Pushed concatenated
after the RF image in the dual path. **INFERENCE.**

---

## 3. The 2F 2A command, byte-for-byte (so the driver push can be trusted)

`hal_vs_nci_send(oid, data, len)` (@0x157d0) builds every VS packet as:
`strb 0x2F @sp+4` (GID), `strb oid @sp+5` (OID), `strb len @sp+6` (payload len),
data at sp+7. So `oid=0x2a` ⇒ wire `2F 2A <len> <data>`. **FACT.**

The three dual sub-commands (sub byte is `data[0]`):

```
START :  2F 2A 01                         (len 1; sub=0x01)                 FACT 0x148f8
SET   :  2F 2A 02 <index> <≤252 bytes>    (len = 2 + chunk; sub=0x02)       FACT 0x1640c
STOP  :  2F 2A 03 <cksum_lo> <cksum_hi>   (len 3; sub=0x03, 16-bit LE)      FACT 0x14f8c
```

`hal_vs_rfreg_update_dual_option` (@0x1640c) internals — SET frame assembly:
- chunk length = `min(remaining, 0xFC=252)` (`cmp #0xfc`, `csel`). **FACT.**
- writes `strb 0x02 @sp+0` (sub), `strb frag_index @sp+1` (from `[VS_INFO+4]`,
  post-incremented), copies chunk to sp+2 (`orr x,#0x2`). plen = chunk+2
  (`add x2, chunk, #0x2`). **FACT.**
- checksum: sum of little-endian u32 words over the chunk, NEON-accelerated,
  accumulated in `[VS_INFO+304]`; final 16-bit value goes into the STOP. **FACT.**
- source pointer = `[VS_INFO+384]` (the merged rfreg+swreg), total = `[+392]`.
  **FACT.**

(The legacy `2F 22` form @0x161b0 is identical minus the sub byte: `2F 22
<index> <≤252>`, source `[+320]`, len `[+328]`, STOP `2F 27 <cksum16>`.)

### 3.1 The driver already implements this correctly — FACT

`s3fwrn5-driver/nci.c` `s3fwrn5_nci_rf_configure_dual()` + `nci.h`:
```
NCI_PROP_RFREG_DUAL 0x2a ; START 0x01 ; SET 0x02 ; STOP 0x03
struct …_set_cmd  { u8 sub; u8 index; u8 data[252]; }
struct …_stop_cmd { u8 sub; __le16 checksum; } __packed
S3FWRN5_RFREG_SECTION_SIZE 252
```
This is **byte-for-byte the HAL's 2F2A**. The live log proves it works end to
end (14 SET frames, STOP, checksum 0x5b9a accepted). **FACT.** No change needed
to the 2F2A path.

---

## 4. What the driver does today vs the Android HAL

Driver init (from `core.c` + live `aonly_dmesg.log`), with `rfreg_dual=1`:

```
CORE_RESET (0x0)                                   ok, NCI 2.0
2F 28  (FW_CFG, plen 1 = speed only)               ok         <- s3fwrn5_nci_setup()→rf_configure_dual()
2F 2A 01 / 2F 2A 02×14 / 2F 2A 03                  success, cksum 0x5b9a
CORE_INIT (0x1)                                    ok, manufact_specific_info 0x02030065
RF_DISCOVER_MAP (0x100 plen 19, then plen 4)       ok
NFCEE_DISCOVER (0x200)                             ok, 2× NFCEE_DISCOVER_NTF
CORE_SET_CONFIG (0x02 plen 5, plen 4)              ok
NFCEE_POWER_AND_LINK_CTRL (0x203 plen 2)           chip answered (kernel: "unknown rsp 0x203")
RF_SET_LISTEN_MODE_ROUTING (0x101 plen 17)         ok
RF_DISCOVER (0x103 plen 3 = NFC-A listen only)     ok
… 18 s idle … RF_DEACTIVATE (0x106)                0 activations, 0 NFCEE_ACTION_NTF
```

**FACT** (opcode list + line refs in §0/§1).

Timing note: the driver pushes rfreg **between CORE_RESET and CORE_INIT** — from
`.setup` (`s3fwrn5_nci_setup`, gated by `rfreg_dual`), which `nci_open_device`
calls after CORE_RESET and before CORE_INIT. `core.c:140-176` documents this and
explicitly notes that doing it from `post_setup` (after CORE_INIT) leaves the
part answering nothing. **This matches the HAL**, which also loads RF registers
during device-open, before the NFA stack inits on top. **FACT / INFERENCE.**

So on the "does the driver push rfreg/hwreg/swreg" question: **YES, it already
does — correctly, in the right place, and the chip accepts it.** The felica_re §8
prediction that mainline omits this is true of *pristine* mainline, but the
driver in this tree is already patched for it.

### Differences that remain vs the HAL (candidate root causes for no listen-A)

| # | HAL does | this driver does | risk |
|---|---|---|---|
| A | `2F 28` = **TLV** {CLK_SPEED=0x11, CLK_TYPE, CLK_REQ} | `2F 28` = **1 byte** (speed 0x11 only) | **HIGH** — wrong/short clock config looks exactly like "answers NCI, never sees a card". §6.1 |
| B | Sends `NFCEE_MODE_SET(0x83,ENABLE)` before routing/discover (standard NCI CE) | **never sends `22 01`** | **HIGH** — eSE not ENABLED ⇒ NFC-A route to 0x83 can't fire, no NFCEE_ACTION_NTF. §6.2 |
| C | version-gates the push via `nfc.fw.rfreg_ver` (may **skip** if unchanged) | always pushes | low (pushing is safe) |
| D | uses the **merged rfreg+swreg** in one dual transfer | same (merged) | none — matches |

**FACT** for A/B (HAL TLV @0x105c0; no 0x22 in log). **INFERENCE** that A/B are
the operative gaps.

---

## 5. Answering the five RE questions directly

1. **Prop cmds at init, in order:** `2F 28` (clock, TLV from conf) → `2F 2A 01`
   (START) → `2F 2A 02 idx …` ×N (merged rfreg+swreg, 252-B chunks) → `2F 2A 03
   cksum16` (STOP). Legacy chips use `2F 22`/`2F 26`/`2F 27` instead. Payload
   files: `RF_FILE_NAME`/`RF_HW_FILE_NAME` (primary) + `RF_SW_FILE_NAME` (swreg),
   merged. **FACT.**
2. **Blob format:** container = `[header_len][header][payload][…"DEF\0" footer]`;
   rfreg header_len=2, swreg header_len=4. Payload = fixed 52-byte RF-register
   records then denser analog tables. **rfreg == hwreg (identical bytes).** No
   host-visible listen-A section; a single `DEF` (default) profile applied whole.
   **FACT** (format) / **INFERENCE-UNKNOWN** (which records are listen-A).
3. **Listen-A profile selection:** none exists at the host level — no register
   id / flag / 2Fxx toggles listen-A. The firmware picks the analog bank
   internally from the `DEF` blob. **UNKNOWN whether a *different* blob
   (non-DEF) would change listen-A tuning — we only have the DEF blob.**
4. **What mainline lacks:** pristine mainline s3fwrn5 pushes rfreg only via the
   FW-update path (after a firmware download); with no firmware file it pushes
   nothing, and it has no dual (2F2A) opcode at all. **This tree's driver already
   fixes both** (dual path + `.setup` push without a FW download). The remaining
   real gaps are **the short 2F28 clock TLV (A) and the missing NFCEE_MODE_SET
   (B)** — neither is an rfreg problem. **FACT.**
5. **2F 2A byte-exact:** §3. START `2F 2A 01`; SET `2F 2A 02 <idx> <≤252>`; STOP
   `2F 2A 03 <cksum_lo> <cksum_hi>`; checksum = Σ le32(chunk) & 0xFFFF over the
   merged blob. Already replicated correctly in the driver. **FACT.**

---

## 6. Concrete plan — what to change next (rfreg is NOT the lever)

Priority order. The rfreg push is done and verified, so do **not** spend more
effort there.

### 6.1 Fix the clock config `2F 28` to match the HAL (do first) — INFERENCE(strong)
The HAL sends `2F 28` as a TLV of **three** keys; the driver sends **one** byte.
A part with a half-configured clock answers all NCI and never sees a card — the
exact symptom. In `nci.c`:
- In `s3fwrn5_nci_rf_configure_dual()`, replace the single-byte
  `nci_prop_cmd(NCI_PROP_FW_CFG, 1, &speed)` with the **full 3-field** form the
  legacy `s3fwrn5_nci_rf_configure()` already builds:
  `struct nci_prop_fw_cfg_cmd { clk_type; clk_speed; clk_req; }` with
  `clk_type=0x01, clk_speed=0x11 (from conf FW_CFG_CLK_SPEED), clk_req=…`.
  (Better: mirror the HAL's *keyed* TLV `{id,len,val}` if the chip is strict,
  but the 3-byte struct is the more likely wire form for `2F 28`.)
- The driver comment at `nci.c:97-105` asserts dual chips want *speed only*;
  the live "0 activations" is consistent with that assertion being **wrong**.
  Test speed-only vs the 3-byte struct A/B. **INFERENCE.**

### 6.2 Send `NFCEE_MODE_SET(0x83, ENABLE)` before routing/discover — FACT(gap)
No `22 01` is emitted in the whole run. For NCI 2.0 card emulation to an NFCEE,
the eSE must be ENABLED first. This is a `net/nfc/nci` / `core.c` change, not a
blob change:
```
NFCEE_DISCOVER (0x200)                 -> learn 0x83 + its state from NTF
NFCEE_MODE_SET (0x2201): 22 01 02 83 01  <- MISSING; add this, wait RSP+NTF
NFCEE_POWER_AND_LINK_CTRL (0x203)      -> then keep it powered/linked
LMRT / RF_DISCOVER                     -> as today
```
Add a `.rsp`/`.ntf` handler so the request completes. Only then will
`RF_NFCEE_ACTION_NTF (0x1/0x9)` and an NFC-A listen activation appear. **FACT**
(absence) + **INFERENCE** (that enabling fixes the route).

### 6.3 Surface the diagnostics — INFERENCE
- Add a handler / `pr_info` for `RF_NFCEE_ACTION_NTF` (GID 0x1 OID 0x9) — it is
  the direct evidence the CLF handed the A-field to 0x83.
- Add a `.rsp` for opcode `0x203` (`NFCEE_POWER_AND_LINK_CTRL`) so the "unknown
  rsp opcode 0x203" stops (benign, but it means the request isn't completed
  cleanly).

### 6.4 rfreg blob delivery (already correct; for the record) — FACT
The blob must be reachable by `request_firmware()`. The driver requests
`sec_s3fwrn5_rfreg.bin` + `sec_s3fwrn5_swreg.bin`; the live log shows it found
them (success). So they are already installed under `/lib/firmware/…`. Keep them
there. If you package: ship `sec_s3fwrn5_rfreg.bin` (== hwreg) and
`sec_s3fwrn5_swreg.bin` in `/lib/firmware/`. No format change needed — push
verbatim. `sec_s3fwrn5_firmware.bin` is optional (chip keeps its own; live load
failed with -2 and init still proceeded). **FACT.**

### 6.5 Only if 6.1–6.3 still yield no listen-A — UNKNOWN
Then the residual is the one felica_re §8 flagged as unresolvable from static
material: whether Android, *at the moment it reads the SUBE*, pushes a
**different (non-DEF) RF register set** or an extra 2Fxx. We only have the `DEF`
blob; there is no second profile in these files. Resolving that needs a **live
`btsnoop`/NCI capture from stock Android while the eSE is being read**, diffed
against this DEF push. Not derivable from `nfc_nci_sec.so` + these blobs alone.
**UNKNOWN.**

---

## 7. Evidence index
- `aonly_dmesg.log:13-17` — `2F 28` FW_CFG (plen 1) ok.
- `aonly_dmesg.log:19-139` — `2F 2A` 01/02×14/03; "rfreg … success (checksum
  0x5b9a)"; 14× `opcode 0xf2a, plen 254` (= 3568 B = 3232+336).
- `aonly_dmesg.log:140-158` — CORE_INIT ok, manufact_specific_info 0x02030065.
- `aonly_dmesg.log` opcode list — **no `0x22xx`** (no NFCEE_MODE_SET).
- `aonly_dmesg.log:226-238` — RF_DISCOVER ok, then 18 s idle, RF_DEACTIVATE,
  **0 activations / 0 NFCEE_ACTION_NTF**.
- `nfc_nci_sec.so`: `hal_vs_nci_send`@0x157d0 (2F/oid/len pack);
  `hal_vs_rfreg_update`@0x161b0 (2F22, src +320/+328);
  `hal_vs_rfreg_update_dual_option`@0x1640c (2F2A sub=0x02, src +384/+392);
  `hal_vs_merge_rf_image`@0x16128 (rfreg∥swreg → +384/+392);
  `hal_vs_get_rf_image`@0x15910 (loads primary +312, swreg +360);
  `nfc_hal_get_update_image`@0x1121c (type→conf key, jt @0x7fa0:
  1=FW_FILE_NAME,2=RF_FILE_NAME,3=RF_HW_FILE_NAME,4=RF_SW_FILE_NAME);
  `nfc_hal_vs_sm`@0x14524 (chip-id 0x74/0x84 gate; START 0x148f8, STOP 0x14f8c);
  `hal_nci_send_prop_fw_cfg`@0x105c0 (2F28 TLV from CLK_* conf keys);
  strings: "Start setting RF register!!"@0x655a, "Stop setting RF register!",
  "RF register check sum is 0x%04X", "in factory mode, load factory RF reg!".
- vendor-blobs md5: rfreg==hwreg (299bdf92…), swreg==swreg (865ac14e…);
  rfreg hdr_len=2 hdr=`00 00` footer `…44 45 46 00`; swreg hdr_len=4
  hdr=`00 01 07 03` footer `…44 45 46 00`; 52-byte records, separator
  `08 00 00 00 02 00 00 00 00 00 00 00 00` step 52.
- `libnfc-sec-vendor.conf:22,26-30` FW_CFG_CLK_SPEED=0x11, FW_FILE_NAME,
  RF_HW_FILE_NAME=sec_s3nrn4v_hwreg.bin, RF_SW_FILE_NAME=sec_s3nrn4v_swreg.bin.
- `s3fwrn5-driver/nci.c:83-194` dual push; `nci.h:51-70` opcode/sub/struct
  defs; `core.c:149-176` setup() push between CORE_RESET and CORE_INIT.
```
```
