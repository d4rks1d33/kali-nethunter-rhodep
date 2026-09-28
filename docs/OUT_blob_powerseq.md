# S5KJN1 rhodep sensormodule blob — power-up sequence decode (definitive)

Blob: `s5kjn1_sunny.bin` (269222 B = 0x41ba6) =
`com.qti.sensormodule.mot_rhodep_s5kjn1_sunny.bin`.
Parser: `/tmp/camre8/parse*.py`. Anchor: **DATA_BASE = 0x3401c** (re-verified below).

---

## TL;DR

- **FACT.** The 56-byte parameter table was parsed cleanly (3839 entries, ids 1..3839,
  names all decode, `moduleType`/`sensorId`/`slaveAddr`/`SU` all resolve at
  DATA_BASE=0x3401c). The parse is trustworthy.
- **FACT.** There is exactly **one** `powerUpSequence` entry in the whole file:
  **id 3705, 16 bytes**, and it sits in the **module-configuration / non-sensor
  branch** (right next to `oisName`, `eepromName`, `flashName`, `actuatorID`,
  `depthConversionLibInfo`). Its 16 bytes are **not** a rail array — they are a
  `tag(0) + symbolref(0x0df1) + 2,2` descriptor. `powerDownSequence` (id 3706) is
  **size 0**.
- **FACT.** The **sensor branch (ids 1–60)** contains `moduleType`, `powerSetting`
  (the chip-id/identification block), the gain/integration reg addresses,
  `streamConfiguration`, and thousands of `slaveAddr/registerData/delayUs` register
  writes — **but no `powerUpSequence`/`powerDownSequence` and no rail/gpio/reset/
  standby power tuples of any kind.**
- **FACT.** A brute-force value scan of the entire data region for any isolated array
  of valid `(seq_type∈0..12, config∈{0,1,24000000}, delay≤200)` tuples yields **only
  chance false positives** (MCLK=0 matches every zero byte); no coherent, isolated
  power-up array exists as decodable data.
- **CONCLUSION (valid negative).** rhodep's Sunny sensormodule blob **does not carry a
  sensor power-up sequence**. On this CamX/Parameter-Parser-V3 format, the sensor's
  (seq_type, config, delay) power buffer is **not** stored here; it is built at runtime
  by CamX/the sensor driver library and handed to the kernel as a `cam_cmd_power`
  packet (parsed by `cam_sensor_update_power_settings`, `cam_sensor_util.c:1314`).
  The blob only holds **probe/identification + register/stream tables**, not the rail
  ordering. The earlier "no sensor power sequence" conclusion is **correct**; the
  intermediate note in `OUT_camx_seq.md` that "the blob does carry powerUpSequence"
  is **half-right**: the *name* exists (id 3705) but it is in the non-sensor branch and
  is a symbol reference, not the rail tuples.

So: **the missing rhodep step is not recoverable as data from this file.** What *is*
recoverable, and what to do, is below.

---

## 1. Verification the parse/anchor is correct  (FACT)

Container (matches prior RE):
```
0x0000 "QTI Chromatix Header"; 0x001c u32 total=0x41ba6 (==file size);
0x0028 "Parameter Parser V3.0.1"; 0x0058 "...mot_rhodep_s5kjn1_sunny";
0x00c0 parameter table start; entry = {u32 flag; u32 offset; u32 size; u32 id; char name[40]}.
```
DATA_BASE re-derived independently: `moduleType` (id 9) has `offset=0x894`; the value
`ac 00 00 00 02 00 00 00 02 00 00` occurs in the file at exactly **0x348b0**, so
`0x348b0 - 0x894 = 0x3401c`. Confirmed.

Sensor branch, resolved at DATA_BASE (file offsets shown):

| id | name | file off | size | bytes / meaning |
|----|------|----------|------|-----------------|
| 9  | moduleType | 0x348b0 | 11 | `ac 000000 | 02 000000 | 02 0000` = slave **0xac** (0x56 7-bit), addrType=**WORD(2)**, dataType=**WORD(2)** |
| 12 | powerSetting | 0x348bb | 0 | (empty sentinel) |
| 13 | powerSetting | 0x348bf | 72 | identification block, decoded below |
| 34 | slaveAddr | 0x34cb7 | 11080 | the big register/stream table (init/res writes) |

### id 13 `powerSetting` = identification block (FACT, byte-exact)
Layout is `tag(1 byte=0x00)` then u32 fields:
```
[0] 0x000038e1  sensorId              = S5KJN1 chip id  (0x38e1)   FACT
[1] 0xffffffff  sensorId mask                                     FACT
[2] 0x00000001  (num id regs / present)
[3] 0x0000000a  (0x0a)
[4] 0x000000a0  eeprom slave addr     = 0xa0 (0x50 7-bit)         FACT
[5] 0x00000002  addr type = WORD
[6] 0x00000002  data type = WORD
[7] 0x0000000d  eeprom reg 0x0d       (module-code register)      FACT
[8] 0x00005355  module code "SU"      = Sunny                     FACT
[10]0x0000000b
```
This is the probe/MatchID metadata (chip id + how to read the module-code EEPROM to
pick Sunny vs Qtech). It is **identification, not a rail sequence.** `slaveAddress`
0xac and `sensorId` 0x38e1 are confirmed here and via `moduleType`.

---

## 2. The one `powerUpSequence` in the file is NOT the sensor's  (FACT)

Distinct entry-name census (relevant rows):
```
      8  powerSetting          (id 12,13 sensor-ident; 3712/3713/3729/3730/3803/3804 OIS/actuator)
      1  powerUpSequence       (id 3705)  <-- the ONLY one
      1  powerDownSequence     (id 3706, size 0)
      1  sensorSlaveAddress    (id 3680)
      1  sensorI2CFrequencyMode(id 3681, size 0)
      1  i2cFrequencyMode      (id 3694)
      1  standbySettings       (id 3720, size 0)
      1  isFastStandbyEnabled  (id 3427, size 0)
```

`powerUpSequence` id 3705 lives in the **module-configuration branch** (its
immediate neighbours are `oisName`, `eepromName`, `flashName`, `pdafName`,
`depthConversionLibInfo`, `actuatorID`), **not** the sensor branch. Its 16 bytes:
```
00 00f10d 00000200 00000200 00000000
tag=0, symbolref=0x0df1, 2, 2
```
That is a Parameter-Parser descriptor (a reference token `0x0df1` + WORD/WORD), the
same shape as every other entry in that branch (they are all `0x0dxx`/`0x0exx`/`0x6fxx`
symbol-reference tokens, e.g. `moduleName` id 3677 is a long list of
`… d40d0000 … d50d0000 2a600000 …` reference tokens). **No inline (type,config,delay)
rail tuples.** There is **no symbol/string table in the file** that maps `0x0df1` →
enum name (the only ascii strings in the data region are the entry *name* fields
themselves). The QTI parser resolves those tokens against its own compiled schema,
which is not in this .bin.

`powerDownSequence` (id 3706) and `sensorI2CFrequencyMode` (id 3681) and
`standbySettings` (id 3720) and `isFastStandbyEnabled` (id 3427) are all **size 0** —
present as schema slots, empty of data.

---

## 3. Value-scan for a hidden rail array — negative  (FACT)

Scanned the whole data region (0x3401c→EOF) for any run of ≥4 elements at strides
12/16/20/24 whose leading field is a valid `msm_camera_power_seq_type` (0..12) with
≥2 distinct types. Result: **1315 / 247 / 2478 / 212** "candidates" — i.e. pure noise:
`MCLK` (=0) matches every zero dword, so triplets appear everywhere. None form an
isolated array with valid configs (0/1/24000000) and plausible delays. There is no
genuine, decodable sensor power-up array in the file. (Same result the prior RE got;
now confirmed structurally, not just by pattern-search.)

---

## 4. `sensorI2CFrequencyMode`  (FACT / INFERENCE)

- **FACT.** `sensorI2CFrequencyMode` (id 3681) is **size 0** in this blob — the field
  is present but empty (no explicit value stored).
- **FACT.** The nearby generic `i2cFrequencyMode` (id 3694, 22 B) decodes as symbol
  reference tokens, leading u32 = **0**.
- **INFERENCE.** Empty/0 maps to the enum default
  `I2C_STANDARD_MODE = 0` (`cam_sensor_cmn_header.h:93-99`:
  STANDARD=0, FAST=1, CUSTOM=2, FAST_PLUS=3). i.e. **this blob does not force FAST**;
  it leaves it at the container/driver default. Note: the sensor-bus master mode on
  downstream is actually taken from the CamX runtime config, not this empty field, so
  "STANDARD by default" here is not proof the hardware runs 100 kHz — but it does mean
  **the blob carries no FAST/FAST_PLUS override.** Treat the blob as *silent* on freq.

---

## 5. Confirmations requested  (FACT)

- **slaveAddress = 0xac** (8-bit) = **0x56** (7-bit): `moduleType` id 9 @0x348b0 and
  `sensorSlaveAddress` id 3680. ✔
- **sensorId = 0x38e1**, mask 0xffffffff: `powerSetting` id 13 @0x348bf, field[0]/[1]. ✔
- **module code "SU" = 0x5553** (Sunny), read from EEPROM 0xa0 reg 0x0d. ✔
- addrType/dataType = WORD/WORD (2/2). ✔

---

## 6. rhodep decoded sequence vs mainline `s5kjn1_power_on`

**rhodep blob power-up sequence (as data): NONE PRESENT.** The blob's sensor branch
carries identification + register/stream tables only. So there is no ordered
(type/config/delay) list to lay beside mainline from *this file*.

Mainline `s5kjn1_power_on` (`s5kjn1-mainline.c:1202-1241`), for reference:
```
enable vddd (DVDD)  -> sleep 1-2 ms
enable vdda (AVDD)
enable vddio (VDDIO)
enable afvdd (AF)
clk_prepare_enable(mclk)             # MCLK 24 MHz ON
gpiod_set_value(reset_gpio, 0)       # reset de-asserted immediately after MCLK
usleep_range(10-15 ms)               # single settle, then read chip id cold
```
CamX MatchID is also cold (no register writes before the id read,
`cam_sensor_core.c:769-790`), and FP5 identifies with exactly this mainline timing.

**Exact diff that this blob proves:** the blob adds **no** STANDBY, **no**
CUSTOM_GPIO, **no** CUSTOM_REG, **no** second reset toggle, **no** materially different
delay — because it stores no rail sequence at all. Any such step, if rhodep needs one,
is **not encoded in the sensormodule .bin**; it would come from the CamX runtime
(`com.qti.sensor.mot_s5kjn1.so` + chi-cdk), not from this file.

---

## 7. Concrete next step (where the real sequence is, and how to get it)

Because the rail ordering is **not** in the .bin, the two information-bearing routes are:

1. **ftrace stock Android (definitive, no RE).** Boot stock, enable
   `cam_sensor_core_power_up` / the `CAM_DBG(CAM_SENSOR,"Seq Type[%d]: %d Config_val")`
   log in `cam_sensor_util.c:1403` and the `WAIT`/delay accumulation at
   `cam_sensor_util.c:1418-1422`. That prints the exact ordered
   `(seq_type, config_val, delay)` tuples CamX applies — the numbers this .bin does
   not contain. Transcribe straight into `s5kjn1_power_on`.

2. **Disassemble `com.qti.sensor.mot_s5kjn1.so`** (AArch64) — larger job; the sequence
   is code/data there, not in the sensormodule bin.

Given FP5 (same driver, same silicon) identifies with plain mainline timing, the
rhodep delta is most likely **electrical/sequencing/bus-mode**, not a hidden tuple.
The cheap, blob-independent sweep to try in `s5kjn1_power_on` (values from the closest
readable S5K-family CamX template, S5KJD1SP, per `OUT_camx_seq.md` §3):

```c
/* rails, then MCLK settle BEFORE a real reset pulse */
regulator_enable(vddd);  usleep_range(1000, 2000);    /* DVDD  +1 ms  */
regulator_enable(vdda);                               /* AVDD         */
regulator_enable(vddio);                              /* VDDIO        */
usleep_range(10000, 11000);                           /* +10 ms VDIG settle */
clk_prepare_enable(mclk);                             /* MCLK 24 MHz  */
usleep_range(5000, 6000);                             /* NEW 5 ms MCLK-stable */
gpiod_set_value_cansleep(reset_gpio, 1);              /* reset LOW (active-low) */
usleep_range(5000, 6000);                             /* hold 5 ms    */
gpiod_set_value_cansleep(reset_gpio, 0);              /* reset HIGH   */
usleep_range(12000, 15000);                           /* +12 ms, then read id */
```
Second axis: enable MCLK *after* reset release (vendor JD1SP order). Third axis:
set the cci child bus `clock-frequency = <400000>` (FAST), then `<1000000>`
(FAST_PLUS) — noting the blob is silent on freq, so this is a mainline-default fix,
not a blob-derived value.

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- Table parse verified; DATA_BASE=0x3401c re-derived from `moduleType`@0x348b0.
- Sensor branch (ids 1–60): identification `powerSetting` (id 13 @0x348bf: sensorId
  0x38e1, mask 0xffffffff, eeprom 0xa0 reg 0x0d, "SU"=0x5553), `moduleType`
  (slave 0xac, WORD/WORD), gain/integration reg-addrs, `streamConfiguration`,
  register tables — **no powerUpSequence/powerDownSequence/rail tuples**.
- Exactly one `powerUpSequence` file-wide: id 3705, 16 B, in the module-config
  (non-sensor) branch; its bytes are a `tag+0x0df1 symbolref+2,2` descriptor, not
  rail tuples. `powerDownSequence` id 3706 size 0.
- Whole-region value scan for valid (type,config,delay) arrays → only chance false
  positives; no genuine array.
- No symbol/string table in the file resolves the `0x0dxx` tokens.
- `sensorI2CFrequencyMode` (id 3681) size 0; generic `i2cFrequencyMode` leading u32 = 0.
- slaveAddr 0xac/0x56, sensorId 0x38e1 confirmed twice.
- Downstream applies the power seq from a runtime `cam_cmd_power` packet
  (`cam_sensor_util.c:1314-1443`), not from this .bin; MatchID reads id cold
  (`cam_sensor_core.c:769-790`).

**INFERENCE**
- sensorI2CFrequencyMode default = STANDARD(0); the blob carries **no** FAST/FAST_PLUS
  override (freq is set by the CamX runtime, not this file).
- The missing rhodep step (vs working FP5) is an electrical/sequencing/bus-mode delta,
  not a STANDBY/CUSTOM_GPIO/CUSTOM_REG/second-reset value hidden in this blob — because
  no rail sequence is stored here at all.

**UNKNOWN**
- The byte-exact rhodep `(seq_type, config_val, delay)` tuples — **not present in this
  file**. Obtain via ftrace of `cam_sensor_core_power_up` /
  `cam_sensor_util.c:1403` on stock Android, or by disassembling
  `com.qti.sensor.mot_s5kjn1.so`.
- Whether MCLK-settle-before-reset-pulse, MCLK-after-reset, and/or FAST i2c is the
  single required fix.
