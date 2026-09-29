# 2F 28 (FW_CFG clock) — byte-exact format from `hal_nci_send_prop_fw_cfg` @0x105c0

Target: `nfc_nci_sec.so` (ARM64, BuildID f4972906…), Samsung S3NRN4V / S3FWRN5,
Moto G82 (rhodep). All disasm from `aarch64-linux-gnu-objdump -d`.
Tags: **FACT** (disasm byte / conf line), **INFERENCE** (derived), **UNKNOWN**.

---

## 0. TL;DR — the prior RE (§6.1) was WRONG about the TLV

**There is no TLV. No `count`. No per-key `id`. No per-key `len`.**
`2F 28` is a **flat, fixed-position byte array** whose length (`plen`) is either
**1** or **3** depending on the chip-revision argument passed to the function.

- The prior report read `plen=3` as a "count" and the `strb #1 @sp+14` as a
  "per-key len=1". Both are misreads:
  - `[sp+14]` is the **NCI payload-length byte** (`plen`), written once as `#1`
    (path A) or `#3` (path B). It is *not* a per-key length. **FACT** (0x1066c /
    0x106ac).
  - The three values are stored at **consecutive single-byte slots** `sp+15`,
    `sp+16`, `sp+17` with **no id/len bytes between them**. **FACT** (out-pointers
    `sp+15/16/17` to `__get_config_int`, 0x10734/0x10790/0x107ec).

**The two real wire forms:**

```
PATH A  (arg h >= 0x70):   2F 28 01 <SPEED>                       (4 bytes)
PATH B  (arg h <  0x70):   2F 28 03 <CLK_TYPE> <CLK_SPEED> <CLK_REQ>   (6 bytes)
```

For **this board** (conf `FW_CFG_CLK_SPEED=0x11`, `FW_CFG_CLK_TYPE` and
`FW_CFG_CLK_REQ` **absent → default 0**):

```
PATH A frame :  2F 28 01 11
PATH B frame :  2F 28 03 00 11 00
```

**FACT** for the structure and offsets; **FACT** for the conf values; the choice
of which path this board takes at runtime is **INFERENCE/UNKNOWN** (see §6).

---

## 1. Exact payload structure (with the bytes)

`__send_to_device(buf, len)` is called with `buf = sp+0xc` (= sp+12) and
`len = [sp+14] + 3`. So the **entire NCI packet** sits from sp+12:

```
sp+12 : 0x2F     GID (proprietary)          <- strh 0x282f @[sp+12]  (LE => 2F 28)
sp+13 : 0x28     OID (FW_CFG)                     (0x10604 mov w8,#0x282f; 0x1064c strh w8,[sp,#12])
sp+14 : plen     = 0x01 (path A) or 0x03 (path B) (0x1066c / 0x106ac strb)
sp+15 : payload byte 0
sp+16 : payload byte 1   (path B only)
sp+17 : payload byte 2   (path B only)
```

`len = plen + 3` ⇒ the 3 header bytes (`2F 28 plen`) + `plen` payload bytes.
**FACT** (0x10800 `ldrb w8,[sp,#14]`; 0x10808 `add x19,x8,#3`; 0x10804 `add
x0,sp,#0xc`; 0x10810 `bl __send_to_device`).

There is **no** `count` byte and **no** `{id,len,val}` triplets anywhere in the
buffer. The payload is raw values at fixed offsets. **FACT.**

### The branch that picks 1 vs 3

```
105f8 : cmp  w21, #0x70            ; w21 = (arg h) & 0xff
10650 : b.cc 0x10694               ; if h < 0x70  -> PATH B (plen 3)
        (fallthrough)              ; if h >= 0x70 -> PATH A (plen 1)
```
**FACT.** `w21` comes straight from the function argument (`and w21, w0, #0xff`
@0x105e8), **not** from `get_hw_rev()` (whose result `w19` is used only as the
`_REV%d` suffix number). **FACT.**

---

## 2. The three register slots (tags) — THERE ARE NO NUMERIC TAGS

**Question 2 as posed assumes a TLV with per-key BYTE ids. That assumption is
false.** The wire bytes carry **only the values**, positionally. The "identity"
of each byte is its **fixed offset**, decided by which conf key filled it:

| wire offset (path B) | filled from conf key | conf-key string @ | value source |
|---|---|---|---|
| payload byte 0 (sp+15) | `FW_CFG_CLK_TYPE`  | 0x7aa0 | `__get_config_int`, out=`sp+15` (0x10734) |
| payload byte 1 (sp+16) | `FW_CFG_CLK_SPEED` | 0x6a77 | `__get_config_int`, out=`sp+16` (0x10798) |
| payload byte 2 (sp+17) | `FW_CFG_CLK_REQ`   | 0x605a | `__get_config_int`, out=`sp+17` (0x107f4) |

**FACT** (base-key `adrp/add x3` immediates: 0x7aa0, 0x6a77, 0x605a; out-pointers
`x23|3=sp+15`, `x23+4=sp+16`, `x23+5=sp+17`, where `x23=sp+12`).

In **PATH A** only one slot exists: `FW_CFG_CLK_SPEED` (0x6a77) → `sp+15`
(out-ptr `x23|3`, 0x106dc). **FACT.**

> So the answer to "what BYTE id goes in the TLV for each key" is: **none — the
> firmware identifies each field by position, not by an id byte.** **FACT.**

### Per-key resolution detail (conf → value)

Each key is looked up with a **revision suffix first**: the helper @0x10518 is a
`vsnprintf`-style builder using format `"%s_REV%d"` (@0x6c61) with the base key
and `get_hw_rev()`; if `get_config_count("<KEY>_REV<n>") == 0` (or hw_rev<0) it
falls back to the **base** key. Then `__get_config_int(buf, key, &out, 0)`; on
failure the slot keeps its pre-zeroed default (`str wzr`). **FACT**
(0x10518 fmt; 0x1a630 `get_config_count`; 0x1a970 `__get_config_int`;
default stores 0x106ec/0x10744/0x107a0/0x107fc).

---

## 3. The values (from conf)

`libnfc-sec-vendor.conf` (this board):

```
FW_CFG_CLK_SPEED = 0x11      (line 22)                         FACT
FW_CFG_CLK_TYPE  = <absent>  -> defaults to 0x00               FACT (grep: not present)
FW_CFG_CLK_REQ   = <absent>  -> defaults to 0x00               FACT (grep: not present)
```

No `*_REV*` variants exist either, so the base keys are used. **FACT.**

- `CLK_SPEED = 0x11` — matches the prior RE and the driver's
  `S3FWRN5_CLK_SPEED_DUAL 0x11`. **FACT.**
- `CLK_TYPE = 0x00` — because the conf has no `FW_CFG_CLK_TYPE`, the HAL sends
  **0**, not 0x01. (The driver's hardcoded `clk_type=0x01` default is a *guess*
  that does **not** match what this HAL would put on the wire for this conf.)
  **FACT** (conf absence) / **INFERENCE** (that 0 is therefore sent).
- `CLK_REQ = 0x00` — same reasoning. **FACT/INFERENCE.**

> If Android's *base* (non-vendor) `libnfc-nci.conf` or a platform overlay set
> `FW_CFG_CLK_TYPE`/`FW_CFG_CLK_REQ`, those would override the 0. Neither file
> provided here contains them. **UNKNOWN** whether a deeper overlay sets them —
> but on the material given, both are **0x00**.

---

## 4. The order (path B)

Read order == wire order == store order:

```
byte0 = CLK_TYPE   (read first, 0x1069c/0x10734)
byte1 = CLK_SPEED  (read second, 0x10748/0x10798)
byte2 = CLK_REQ    (read third, 0x107a4/0x107f4)
```
**FACT.** So the on-wire order is **TYPE, SPEED, REQ** — *not* SPEED-first.
(The prior RE guessed "CLK_SPEED + CLK_TYPE + CLK_REQ"; the real lead byte is
**TYPE**.) This order **matches** the mainline driver struct
`nci_prop_fw_cfg_cmd { clk_type; clk_speed; clk_req; }`. **FACT.**

---

## 5. Complete 2F 28 frames, byte-for-byte

Generic:
```
PATH A (h>=0x70):  2F 28 01 SS                 SS=CLK_SPEED
PATH B (h< 0x70):  2F 28 03 TT SS RR           TT=CLK_TYPE  SS=CLK_SPEED  RR=CLK_REQ
```

This board's conf substituted:
```
PATH A :  2F 28 01 11
PATH B :  2F 28 03 00 11 00
```
**FACT** (structure + conf values). Note `plen` is `0x01`/`0x03` = payload length,
not a count. **FACT.**

The prior live dmesg observed the **driver's** frame `2F 28` "plen 1" — that is
the driver's single-byte `2F 28 01 11`, which happens to be byte-identical to the
HAL's **PATH A** frame. If the HAL actually took **PATH B** on this silicon, the
driver is 2 bytes short (missing the trailing `SS RR`/leading `TT`). See §6.

---

## 6. Is 2F 28 always sent? When? And which path?

- **Always sent during open/init**, once, before the RF-register (2F 2A)
  transfer. In the HAL VS state machine the clock `2F 28` is step 1, then
  `hal_vs_check_rfreg_update` → `merge` → `2F 2A 01` START → SET loop → STOP.
  **FACT** (prior RE §1; here confirmed `hal_nci_send_prop_fw_cfg` is the sole
  producer of OID 0x28). It is **not** gated on chip rev for *whether* it is
  sent — only the **payload length (1 vs 3)** is gated, by the `h` argument vs
  `0x70`. **FACT.**

- **Relative to 2F 2A:** `2F 28` (clock) comes **first**, then `2F 2A`
  (START/SET/STOP). **FACT** (prior RE §1 order; consistent with the driver,
  which also sends FW_CFG before the rfreg dual transfer).

- **Which path (1 vs 3) on this board?** Decided by the caller's `h` byte vs
  `0x70`. I could not statically resolve the runtime value of `h` (no direct
  `bl` xref to 0x105c0 — it is dispatched indirectly). The related RF path gates
  on chip-id bytes **0x74** and **0x84** (prior RE §1). If `h` is that same
  chip-id family byte:
  - chip-id `0x74`  → `0x74 >= 0x70` → **PATH A** (`2F 28 01 11`).
  - chip-id `>= 0x84` → **PATH A** as well.
  - only a pre-0x70 (older) part → **PATH B** (3 bytes).
  **INFERENCE.** For an S3NRN4V (a "0x74 or ≥0x84" dual-path part per prior RE),
  the HAL most likely sends the **1-byte** `2F 28 01 11` — i.e. **the driver's
  current single byte is probably already correct**, and the "short TLV" was a
  false lead. **INFERENCE (strong), UNKNOWN (exact runtime `h`).**

> Bottom line on the hypothesis: the clock command is **not** a mis-sized TLV.
> Either the HAL sends exactly what the driver sends (`2F 28 01 11`, PATH A), or,
> on an older part, it sends the flat 3-byte `2F 28 03 00 11 00`. Neither is a
> keyed TLV. If you want to be bit-identical across silicon, send the 3-byte
> flat form with the real conf-derived values.

---

## 7. Exact change to `nci.c.driver`

The current dual path (line 106-107) sends 1 byte:
```c
speed = (clk_speed == 0xff) ? S3FWRN5_CLK_SPEED_DUAL : clk_speed;   // 0x11
ret = nci_prop_cmd(info->ndev, NCI_PROP_FW_CFG, sizeof(speed), &speed);
```

The legacy path (line 200, 227-231) **already** has the correct struct and the
correct field order to match the HAL's PATH B:
```c
struct nci_prop_fw_cfg_cmd { u8 clk_type; u8 clk_speed; u8 clk_req; };  // in nci.h
fw_cfg.clk_type  = clk_type;
fw_cfg.clk_speed = clk_speed;
fw_cfg.clk_req   = clk_req;
nci_prop_cmd(info->ndev, NCI_PROP_FW_CFG, sizeof(fw_cfg), (__u8*)&fw_cfg);
```
`sizeof(fw_cfg) == 3`, wire = `2F 28 03 <clk_type> <clk_speed> <clk_req>`, order
`TYPE,SPEED,REQ` — **byte-for-byte the HAL PATH B**. **FACT.**

### To match the HAL wire exactly, in `s3fwrn5_nci_rf_configure_dual()`:

Replace lines 106-109 with the 3-byte struct **using the HAL's conf-derived
defaults** (TYPE=0, SPEED=0x11, REQ=0 for this board — NOT type=0x01):

```c
	struct nci_prop_fw_cfg_cmd fw_cfg;

	/*
	 * FW_CFG clock. The HAL builds a FLAT 3-byte payload
	 *   2F 28 03 <clk_type> <clk_speed> <clk_req>
	 * (hal_nci_send_prop_fw_cfg @0x105c0, PATH B). It is NOT a TLV:
	 * no count, no per-key id, no per-key len. Order is TYPE, SPEED, REQ.
	 * Values come from conf keys FW_CFG_CLK_TYPE / _SPEED / _REQ; on this
	 * board only FW_CFG_CLK_SPEED is set (=0x11), so TYPE and REQ default 0.
	 */
	fw_cfg.clk_type  = (clk_type  == 0xff) ? 0x00 : clk_type;
	fw_cfg.clk_speed = (clk_speed == 0xff) ? S3FWRN5_CLK_SPEED_DUAL : clk_speed; /* 0x11 */
	fw_cfg.clk_req   = (clk_req   == 0xff) ? 0x00 : clk_req;
	ret = nci_prop_cmd(info->ndev, NCI_PROP_FW_CFG,
			   sizeof(fw_cfg), (__u8 *)&fw_cfg);
	if (ret < 0)
		dev_info(dev, "clock configuration refused (%d)\n", ret);
```

Notes:
- **Do NOT keep `clk_type` default `0x01`** if the goal is to mirror *this
  board's* HAL: the conf has no `FW_CFG_CLK_TYPE`, so the HAL sends **0x00**.
  Setting 0x01 would send `2F 28 03 01 11 00`, which does **not** match. Use
  `0x00`. **FACT** (conf absence).
- If instead you want to mirror **PATH A** (likely the real path for a 0x74/≥0x84
  part), keep the current single byte `2F 28 01 11` — it is already exactly the
  HAL PATH A frame. **INFERENCE.**
- The mainline `struct nci_prop_fw_cfg_cmd` field order (`type,speed,req`)
  already matches the HAL; no struct edit is needed, only the value defaults and
  switching the dual path from the 1-byte form to the struct. **FACT.**

### A/B test matrix (what to actually try on hardware)

| variant | wire bytes | rationale |
|---|---|---|
| current | `2F 28 01 11` | = HAL PATH A; probably already correct for 0x74/≥0x84 |
| flat-3 (conf-exact) | `2F 28 03 00 11 00` | = HAL PATH B with this board's conf (TYPE/REQ=0) |
| flat-3 (type=1) | `2F 28 03 01 11 00` | only if a platform overlay sets FW_CFG_CLK_TYPE=1 |

**INFERENCE.** Given the analog symptom ("answers NCI, never sees a card"), the
clock is a *plausible* but now *weaker* suspect than the prior RE implied: the
1-byte form may already be HAL-correct. The **missing `NFCEE_MODE_SET (22 01)`**
(prior RE §6.2) remains the stronger, independently-confirmed gap.

---

## 8. Evidence index (offsets)

- `hal_nci_send_prop_fw_cfg` @0x105c0. Header build: `mov w8,#0x282f` @0x10604,
  `strh w8,[sp,#12]` @0x1064c (⇒ `2F 28`). **FACT.**
- Branch: `cmp w21,#0x70` @0x105f8, `b.cc 0x10694` @0x10650. `w21 = arg&0xff`
  @0x105e8. **FACT.**
- PATH A: `mov w8,#1` @0x10654, `strb w8,[sp,#14]` @0x1066c (plen=1); base key
  `FW_CFG_CLK_SPEED` 0x6a77 @0x1065c; out-ptr `sp+15` @0x106dc; `0xff` check +
  "Set a different value!" log @0x106f0-0x10728 (fmt @0x5dc8). **FACT.**
- PATH B: `mov w8,#3` @0x10694, `strb w8,[sp,#14]` @0x106ac (plen=3);
  KEY1 `FW_CFG_CLK_TYPE` 0x7aa0 @0x1069c → out `sp+15` @0x10734;
  KEY2 `FW_CFG_CLK_SPEED` 0x6a77 @0x10750 → out `sp+16` @0x10798;
  KEY3 `FW_CFG_CLK_REQ` 0x605a @0x107ac → out `sp+17` @0x107f4. **FACT.**
- Send: `add x0,sp,#0xc`, `ldrb w8,[sp,#14]`, `add x19,x8,#3`, `bl
  __send_to_device` @0x10800-0x10810. **FACT.**
- Key-string builder `"%s_REV%d"` @0x6c61 via `__vsprintf_chk` (helper @0x10518,
  called from `get_clock_info`-style inline). **FACT.**
- Conf: `FW_CFG_CLK_SPEED=0x11` (`libnfc-sec-vendor.conf:22`); `FW_CFG_CLK_TYPE`
  and `FW_CFG_CLK_REQ` **absent** in both `libnfc-nci.conf` and
  `libnfc-sec-vendor.conf`. **FACT.**
- Driver: single-byte dual `nci.c.driver:106-107`; correct 3-byte struct + order
  in legacy path `nci.c.driver:200,227-231`. **FACT.**

---

## 9. Corrections issued against the prior RE (rfreg_listen_re.md §1, §6.1)

1. `2F 28` is **not** a TLV `{id,len,val}`. It is a flat positional byte array.
   **FACT.**
2. There is **no `count` byte**; the byte the prior RE called "count" is the NCI
   **plen** (1 or 3). **FACT.**
3. There are **no per-key ids** and **no per-key len** bytes; `strb #1 @sp+14` is
   the plen of PATH A, not a key length. **FACT.**
4. Wire order is **TYPE, SPEED, REQ** (TYPE first), not SPEED first. **FACT.**
5. On this board `CLK_TYPE` and `CLK_REQ` are **0x00** (conf keys absent), not
   nonzero; the driver's hardcoded `clk_type=0x01` does **not** reproduce the HAL
   for this conf. **FACT/INFERENCE.**
6. The 1-vs-3 length is chip-rev gated (`h < 0x70`), so for a 0x74/≥0x84 part the
   HAL likely sends the **same single byte** the driver already sends → the
   clock is a weaker root-cause candidate than §6.1 claimed. **INFERENCE.**
