# S5KJN1 rhodep Sunny sensormodule — resolving 0x0df1, and finding the REAL power sequence

Blob: `s5kjn1_sunny.bin` = `com.qti.sensormodule.mot_rhodep_s5kjn1_sunny.bin`
(269222 B = 0x41ba6). Parser: `/tmp/camfinal/parse_final.py` (+ `parse3.py`).
Anchor: `DATA_BASE = 0x3401c` (re-confirmed).

---

## TL;DR — the prior "no power sequence" conclusion is WRONG

The stock ROM works with this exact .bin, and it does so because **the power
sequence IS in this file.** The prior decode chased the 16-byte `powerUpSequence`
entry (id 3705) in the *module-config schema branch*, found a `0x0df1` token, and
declared it an unresolvable "symbol reference". That was a dead end because it was
looking at the wrong copy of the data.

**Two hard results (both byte-exact FACTs):**

1. **The real S5KJN1 power-up/down sequence lives at file offset `0x35143`**, as a
   clean `(seq_type, config_val, delay)` u32-triplet array — 5 power-up + 5
   power-down tuples, matching `msm_camera_power_seq_type` and the `cam_cmd_power`
   layout exactly (delays in **ms**, config_val = raw level / clk_rate / voltage,
   per `OUT_kmd_default.md:109-119`).

2. **`sensorI2CFrequencyMode` (id 3681) = 1 = I2C_FAST_MODE (400 kHz)** — read from
   the *resolved instance data* at file offset `0x4147a`. The prior decode read the
   size-0 schema slot and wrongly concluded STANDARD. The blob DOES mandate FAST.

---

## 1. What 0x0df1 actually is  (FACT — resolves the contradiction)

The file has **two representations** of every field:

- **Schema/template branch** (the 56-byte parameter table + a token stream around
  `0x40be0`). Here each field is emitted as `count(u32) + id(u32) + descriptor`.
  The `0x0dXX` / `0x0eXX` / `0x6fXX` "tokens" the prior decode saw are **not symbol
  references** — they are the **`id` field of the parameter-table entry**, written
  inline. The stream at 0x40be0 is literally id 3540, 3541, 3542, … in order:

  ```
  0x40be4  0x0dd4 = 3540 = "delayUs"        (table id)
  0x40bec  0x0dd5 = 3541 = "slaveAddr"
  0x40bf8  0x0dd6 = 3542 = "registerData"
  ...
  0x40d60  0x0df1 = 3569 = "registerData"   <-- THE TOKEN
  ```

  So **`0x0df1` = decimal 3569 = the parameter-table `id` of a `registerData`
  field** (verified: entry id 3569, name "registerData"). It is a self-referential
  field id in a serialized register-write record, **not** a pointer to a power
  sequence and **not** an external symbol.

- The prior decode's "16-byte powerUpSequence = tag(0)+symbolref(0x0df1)+2,2" was a
  **mis-framed cross-section**: entry 3705's declared (offset 0xcd42 → 0x40d5e,
  size 16) is 2 bytes off the 4-byte record grid, so its 16 bytes straddle the tail
  of one `registerData` record and the head of the next. Realigned to the 4-byte
  grid the bytes are just `…count=1 | id=0x0df1 | 2 | 2 | 0 | count=1 | id=0x0df2…`
  — ordinary register-table records. `0x0df1` there is coincidental neighbouring
  data, nothing to dereference.

**Conclusion for 0x0df1: it is a valid negative for a power pointer.** It is the
table id (3569) of a `registerData` schema field. The `powerUpSequence` *schema
slot* (id 3705) in that branch is genuinely a placeholder (its resolved value in
the instance stream at 0x34b30 is 0). The power sequence is not stored there — it
is stored **inline inside the sensor driver-data record** (see §2).

---

## 2. The REAL power sequence  (FACT — byte-exact, file offset 0x35143)

The instance data uses a different, flat serialization. The sensor driver record
begins with the id-3839 marker + ASCII name `mot_s5kjn1\0` at `0x35124`:

```
0x35124  ff 0e 00 00                      id 3839 (record marker)
0x35128  "mot_s5kjn1\0"                   driver/module name
0x35133  01 00 00 00                      (record header)
0x35137  08 00 00 00                      (record header)   <- see UNKNOWN
0x3513b  00 00 00 00
0x3513f  01 00 00 00
0x35143  <power-sequence triplet array>   <-- HERE
0x351bb  0x0210 …                         register init table starts
```

The MCLK config value **24000000 (0x016e3600) occurs exactly ONCE in the whole
file, at 0x35147** — the anchor that pins the array. Parsed as consecutive u32
`(seq_type, config_val, delay)` triplets from 0x35143:

| # | file off | seq_type | name | config_val | delay |
|---|----------|----------|------|------------|-------|
| **POWER-UP** | | | | | |
| 0 | 0x35143 | 0 | SENSOR_MCLK | **24000000** (24 MHz) | 1 ms |
| 1 | 0x3514f | 3 | SENSOR_VIO  | 1 (on) | 0 ms |
| 2 | 0x3515b | 2 | SENSOR_VDIG | 1 (on) | 1 ms |
| 3 | 0x35167 | 1 | SENSOR_VANA | 1 (on) | 1 ms |
| 4 | 0x35173 | 8 | SENSOR_RESET| 1 (gpio high) | 4 ms |
| **POWER-DOWN** | | | | | |
| 5 | 0x3517f | 8 | SENSOR_RESET| 0 (gpio low) | 1 ms |
| 6 | 0x3518b | 1 | SENSOR_VANA | 0 (off) | 0 ms |
| 7 | 0x35197 | 2 | SENSOR_VDIG | 0 (off) | 0 ms |
| 8 | 0x351a3 | 3 | SENSOR_VIO  | 0 (off) | 1 ms |
| 9 | 0x351af | 0 | SENSOR_MCLK | 0 (off) | 1 ms |

Immediately after tuple #9 the stream turns into register-address ids
(0x0210, 0x020e, 0x0212, 0x0214, …) — the init register table — which cleanly
bounds the sequence to exactly these 10 tuples.

**Validation (all pass):**
- Types are all in 0..12 (`msm_camera_power_seq_type`).
- Configs are exactly the legal set {0, 1, 24000000}.
- Delays are 0–4 ms (plausible).
- Structure is a **perfect power-up / power-down mirror** (MCLK,VIO,VDIG,VANA,RESET
  ↑  →  RESET,VANA,VDIG,VIO,MCLK ↓).
- config_val semantics match the KMD executor exactly (`OUT_kmd_default.md`):
  MCLK config = `clk_rate` (2106), rail config = voltage-override/enable (2184),
  RESET config = **raw GPIO level, no active-low inversion** (1949-1951),
  delay in **ms** (2218-2222).

This is the S5KJN1 minimum the FEASIBILITY doc asked for: **MCLK(24 MHz), VIO,
VDIG, VANA, RESET** — all present.

### The critical delta vs mainline
- **Vendor order is MCLK → VIO → VDIG → VANA → RESET.** MCLK is enabled **FIRST**,
  before any rail — the opposite of mainline `s5kjn1_power_on`
  (`s5kjn1-mainline.c:1202`), which enables vddd/vdda/vddio, *then* MCLK, *then*
  reset. Stock brings the 24 MHz clock up before the rails.
- **RESET is driven config_val=1 (raw HIGH) to run, 0 (raw LOW) to power down.**
  The KMD writes this level raw (no inversion). Reset is released only 4 ms after
  MCLK+rails are up and there is **no reset pulse** (single high, held).
- Only **1 ms** settle after MCLK; **4 ms** after reset release before the chip-id
  read. Short delays overall.

This directly explains the bring-up symptom in CAMERA-SENSORS-FEASIBILITY.md:
mainline enables MCLK *after* the rails, so the S5KJN1 comes up in the wrong order
and never latches its id (reads 0x0000). Re-order to MCLK-first per the table above.

---

## 3. Sensor-branch re-examination for nested power refs  (FACT)

- The 56-byte table (`parse3.py`) parses to **3800 entries**, ids 1..3800, table
  spanning 0xc0..0x34000 (then 0x1c pad to DATA_BASE). Two addressing modes by `flag`:
  - `flag = 0 / 2` (ids 1–8): driver-data section; `flag=2` id-1 offset 0x41b92 is a
    **direct file offset** (ends exactly at EOF).
  - `flag = 0xffffffff` (ids 9+): offsets are **DATA_BASE(0x3401c)-relative**;
    size-0 fields share a null slot at 0x89f.
- The `id 13 powerSetting` (0x348bf, 72 B) is the **identification block**
  (sensorId 0x38e1 / mask 0xffffffff / eeprom 0xa0 reg 0x0d / "SU"=0x5355) — as
  before, and it is NOT a rail sequence.
- The big `id 34 slaveAddr` (off 0xc9b, **11080 B**, 0x34cb7..0x377ff) is a
  **container**: it holds the driver name record, **the power sequence at 0x35143
  (offset +0x48c inside it)**, and the register/stream init tables. The power
  tuples are a sub-structure *inside* this entry, which is exactly why a top-level
  "powerUpSequence entry" scan missed them.

So the sequence is a **referenced/embedded sub-structure**, not an inline
top-level field — the hypothesis in the task was correct.

---

## 4. Offset math for the 16-byte powerUpSequence vs the real target  (FACT)

- id 3705 `powerUpSequence`: data-offset 0xcd42 → file 0x40d5e, 16 B. Realigned,
  these bytes are `registerData` record fragments (see §1). Its *resolved* value in
  the instance stream (id 3705 @ 0x34b30) is **0** — an empty schema slot.
- The real sequence is not reachable by "0x0df1 × sizeof" from any base; 0x0df1 is a
  table id, not an offset. The real sequence is located structurally: it is the
  triplet block at **0x35143**, anchored by the unique MCLK=24000000 @ 0x35147, and
  bounded below by the `mot_s5kjn1` record header and above by the register table.

---

## 5. sensorI2CFrequencyMode — re-verified  (FACT, corrects prior decode)

- Prior decode: "id 3681 size 0 → STANDARD default." **Incorrect.** That read the
  schema template slot.
- The **resolved instance stream** (a 2-byte-packed `id,value` array at ~0x4145e)
  contains, byte-exact:
  ```
  0x41476  u32 = 3681   (id sensorI2CFrequencyMode)
  0x4147a  u32 = 1      (value)
  ```
  Enum `cam_i2c_freq_mode` (STANDARD=0, FAST=1, CUSTOM=2, FAST_PLUS=3) ⇒
  **FAST_MODE = 400 kHz.**
- Neighbours in the same stream: id 3680 sensorSlaveAddress=0 (the real 0xac slave
  is carried in the moduleType record at 0x348b0), id 3694 i2cFrequencyMode=0.

**Actionable:** the blob mandates **400 kHz** for the S5KJN1 CCI child bus. The
FEASIBILITY log tried 400 kHz and 100 kHz by hand; 400 kHz is the blob-specified
value — set `clock-frequency = <400000>` on the sensor's CCI node.

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- 0x0df1 = 3569 = parameter-table `id` of a `registerData` field; the `0x0dXX`
  "tokens" are inline entry ids, not external symbols. The 16-byte
  `powerUpSequence` (id 3705) schema slot is empty (resolved value 0).
- Real power sequence @ **file 0x35143**, 10 u32-triplets `(type,config,delay)`,
  5 up + 5 down, anchored by unique MCLK 24000000 @ 0x35147:
  MCLK(24e6,1ms) VIO(1,0) VDIG(1,1) VANA(1,1) RESET(1,4) |
  RESET(0,1) VANA(0,0) VDIG(0,0) VIO(0,1) MCLK(0,1). Delays in ms; config_val is
  raw level / clk_rate / voltage per `OUT_kmd_default.md:109-119`.
- **MCLK is enabled first, before any rail**; reset released 4 ms later, no pulse.
- sensorI2CFrequencyMode (id 3681) resolved value = **1 = FAST (400 kHz)**, byte
  0x4147a.
- Table: 3800 entries, 56 B each, 0xc0..0x34000; DATA_BASE 0x3401c; sensorId
  0x38e1, slave 0xac/0x56, "SU"=Sunny all re-confirmed.
- Power tuples are embedded inside id-34 container (0x34cb7, 11080 B) at +0x48c,
  next to the `mot_s5kjn1` name and the register init table.

**INFERENCE**
- Mainline fails because it enables MCLK *after* the rails; the vendor sequence
  enables **MCLK first**. Re-ordering `s5kjn1_power_on` to MCLK→VIO→VDIG→VANA→RESET
  (with the ms delays above) is the highest-probability fix, together with the
  400 kHz bus.
- RESET config_val 1=run / 0=down is written raw (no active-low inversion in the
  KMD path); on mainline with `GPIO_ACTIVE_LOW` in DT, logical asserts invert —
  match the *electrical* level (high = running) accordingly.

**UNKNOWN**
- The two record-header u32s before the triplets (`0x35137 = 8`, `0x35133 = 1`).
  `8` is not the tuple count (there are 5+5=10); likely a record type/size tag.
  Does not affect the tuple decode (bounded independently by the register table).
- Whether afvdd/VAF is required (not in this sequence; S5KJN1 main has no AF here).
