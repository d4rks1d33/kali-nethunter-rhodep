# S5KJN1 rhodep — CamX / chi-cdk power-up + init sequence, and the diff vs mainline

Scope: find the exact power-up/init sequence stock CamX runs to bring up the Samsung
S5KJN1 on the Moto G82 (rhodep/blair, SM6375), so it can be replicated in mainline.
All AP-side variables already proven correct (context_v3). This report adds the
open-source CamX/chi-cdk data that was the last unknown.

---

## TL;DR (what's new and what to try)

1. **The rhodep S5KJN1 power sequence is NOT in DT and NOT in a readable XML — it is
   compiled into the vendor sensormodule blob**
   `com.qti.sensormodule.mot_rhodep_s5kjn1_qtech.bin` (QTI "Parameter Parser V3"
   Chromatix container). I downloaded it and confirmed it carries a `powerUpSequence`
   / `powerDownSequence` / `powerSetting` block and `sensorSlaveAddress` /
   `sensorI2CFrequencyMode` fields. FACT (file downloaded, header parsed).

2. **A working mainline power-on for this exact silicon already exists and is
   Tested-by on real hardware: the Fairphone FP5.** FP5 runs the *same* mainline
   `drivers/media/i2c/s5kjn1.c` you have in /tmp/camre5 (Vladimir Zapolskiy v4,
   1487 lines, Tested-by Luca Weiss / Neil Armstrong). So the digital core DOES wake
   on rails+MCLK+reset alone, with **no CamX init/wake register write before
   MatchID**. This strongly narrows the rhodep failure to an electrical/sequencing
   detail, not a missing "magic write." FACT (LWN/lore v4 posting; FP5 DTS in
   mainline).

3. **The closest recoverable CamX powerUpSequence (S5KJD1SP, same S5K ISOCELL family,
   same 0x38xx id space, same 0x6028/0x602a/0x6f12 indirect port)** orders power as:
   `VIO → VANA → VDIG → RESET(low) → RESET(high) → MCLK`, i.e. **MCLK is enabled
   LAST, AFTER reset is released, and RESET is a real low→high pulse.** This is the
   reverse of mainline (mainline does MCLK first, then a single static reset release).
   i2cFrequencyMode = **FAST (400 kHz)**. FACT (chi-cdk XML, quoted below).

4. **Concrete change to try on rhodep**: in `s5kjn1_power_on`, (a) add an explicit
   MCLK-stable settle *before* reset, (b) make reset a real pulse
   (low → delay → high) with the JD1SP-style delays, and (c) as a separate axis, try
   enabling MCLK *after* reset release (JD1SP order). Exact code at the end.

---

## 1. Where the rhodep S5KJN1 config actually lives (and its contents)

### The DT node has no power sequence
FACT (`blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi:421-461`, the
`sensor_main: qcom,cam-sensor@0` node): it lists supplies
(`cam_vio`=ldo7, `cam_vana`=ldo4, `cam_vdig`=ldo1), MCLK1 @ 24 MHz, reset gpio35,
`cci-master = <1>`, but carries **no `qcom,cam-power-seq*` and no delays**. The
downstream sensor driver therefore takes the power sequence from the CamX
userspace probe packet, whose contents come from the sensormodule blob.
(Consistent with OUT_power_seq.md / cam_sensor_util.c:1383-1422.)

### The blob (this is the "module blob" context_v3 referred to)
Vendor tree: `TheMuppets/proprietary_vendor_motorola_rhodep`
(lineage-23.2), path:
```
proprietary/vendor/lib64/camera/com.qti.sensormodule.mot_rhodep_s5kjn1_qtech.bin   (269218 B)
proprietary/vendor/lib64/camera/com.qti.sensormodule.mot_rhodep_s5kjn1_sunny.bin   (269222 B)
proprietary/vendor/lib64/camera/com.qti.sensor.mot_s5kjn1.so                        (sensor lib)
proprietary/vendor/lib64/camera/com.mot.eeprom.mot_gt24p128e_s5kjn1_eeprom.so
```
FACT (git tree listing + blob downloaded to /tmp/camre5/blob/ptr_qtech.txt).

Header of the blob:
```
"QTI Chromatix Header" ... "Parameter Parser V3.0.1 (2009151142)"
"com.qti.sensormodule.mot_rhodep_s5kjn1_qtech"
sections: sensorDriverData, PDConfigData, cameraModuleData, flashDriverData
```
It **does** contain (schema field names confirmed present in the binary):
`sensorSlaveAddress`, `sensorI2CFrequencyMode`, `eepromSlaveAddress`,
`i2cFrequencyMode`, `powerUpSequence`, `powerDownSequence`, `powerSetting`,
`standbySettings`, `isFastStandbyEnabled`, sensorId **0x38e1** (found at file
offset 0x348c0 as little-endian `e1 38 00 00`), MCLK value **24000000** (0x35147),
and the regAddrInfo (xOutput=0x034C, yOutput=0x034E, FLL=0x0340, LLPC=0x0342,
coarseIntg=0x0202, gain=0x0204, testpattern 0x0602/4/6/8). FACT (binary parse).

> NOTE / partial-UNKNOWN: the `powerSetting` tuples are stored as a *nested* struct
> array in the Parameter-Parser-V3 container (schema separated from data, per-section
> local indices, mixed u32/double encoding). I decoded the scalar slaveInfo/regAddr
> fields and the sensorId reliably, but I could not byte-exactly decode every
> (configType, configValue, delayMs) tuple of the power array from the binary alone
> without the QTI parser. The **exact ordered tuples with delays are therefore given
> from the closest readable S5K-family XML (S5KJD1SP, section 3), marked INFERRED for
> rhodep.** If you want the byte-exact rhodep numbers, the definitive path is to
> ftrace `cam_sensor_core_power_up` on stock (OUT_corewake C2) — the blob confirms the
> sequence exists but the vendor parser is needed to render it.

context_v3's statement "blob has no power sequence" is **corrected**: the blob *does*
carry `powerUpSequence`; it just isn't human-readable and wasn't parsed before.

---

## 2. The working reference: Fairphone FP5 (same silicon, mainline, Tested-by)

FP5 uses the identical mainline driver and identifies fine. Its DT node
(FACT, `arch/arm64/boot/dts/qcom/qcm6490-fairphone-fp5.dts`, `&cci1_i2c1 camera@10`):
```dts
camera@10 {
    compatible = "samsung,s5kjn1";
    reg = <0x10>;                         /* FP5 uses 0x10; rhodep uses 0x56 - both ACK */
    vdda-supply  = <&vreg_l3p>;           /* AVDD ~2.8V */
    vddd-supply  = <&vreg_l2p>;           /* DVDD ~1.05V */
    vddio-supply = <&vreg_l6p>;           /* VDDIO 1.8V (always-on, CCI pull-up) */
    clocks = <&camcc CAM_CC_MCLK3_CLK>;
    assigned-clock-rates = <24000000>;    /* MCLK 24 MHz, same as rhodep */
    reset-gpios = <&tlmm 78 GPIO_ACTIVE_LOW>;
    link-frequencies = /bits/ 64 <700000000>;
    data-lanes = <1 2 3 4>;
};
```
Consequences for rhodep (all FACT):
- The S5KJN1 digital core **wakes on rails + 24 MHz MCLK + reset alone**, with the
  mainline power-on timing, no CamX-only init write. If FP5 works and rhodep doesn't
  with the same driver, the delta is electrical/sequencing/bus, not a missing wake
  write.
- Supplies match rhodep's (AVDD 2.8 / DVDD ~1.05 / VDDIO 1.8), MCLK matches (24 MHz).
- FP5's `vddio` (l6p) is `regulator-always-on` because it is the **CCI I2C pull-up
  rail**. On rhodep VDDIO is cam_pmic_ldo7 and is enabled — fine — but confirm it is
  actually up *before* the first SCL edge (it powers the pad ring / slave-address
  matcher, which is why 0x56 ACKs).

The mainline v4 changelog explicitly says the sensor init was modeled on downstream:
"set a step to the analog gain control like it's done in downstream", "reworded a
sequence of CCI commands in s5kjn1_enable_streams()". So the mainline enable-streams
wake block (0x6028/0x0000/0x001e/0x6010/0x6226) is itself transcribed from the vendor
stack. FACT (LWN v4 posting).

---

## 3. Closest CamX powerUpSequence template — S5KJD1SP (S5K ISOCELL family)

Source: chi-cdk `oem/qcom/sensor/s5kjd1sp_xr/s5kjd1sp_hmd_0_sensor.xml`
(mirror: OrphyWang/Qcom-CAMX-CHI @ 11se). FACT (file fetched). The S5KJD1SP is the
nearest Samsung S5K sensor with a *readable* CamX XML in a public chi-cdk; it uses the
same 0x38xx sensorId space (0x3841), the same 2-byte/2-byte reg format, and the same
0x6028/0x602A/0x6F12 indirect-port init writes that the S5KJN1 uses — so its
power/probe shape is the best available proxy.

### slaveInfo (S5KJD1SP)
```
slaveAddress      : 0x20            (8-bit write addr)
regAddrType       : 2  (WORD)
regDataType       : 2  (WORD)
sensorIdRegAddr   : 0x0000
sensorId          : 0x3841
i2cFrequencyMode  : FAST            (== 400 kHz)   <<< key: FAST, not STANDARD
```

### powerUpSequence (S5KJD1SP) — EXACT, with delays  [INFERRED for S5KJN1]
| # | configType | configValue | delayMs | meaning |
|---|-----------|-------------|---------|---------|
| 1 | VIO   | 1        | 1  | enable VDDIO 1.8 V, wait 1 ms |
| 2 | VANA  | 1        | 0  | enable AVDD 2.8 V |
| 3 | VDIG  | 1        | 10 | enable DVDD ~1.05 V, **wait 10 ms** |
| 4 | RESET | 0        | 5  | drive reset **LOW**, hold 5 ms |
| 5 | RESET | 1        | 10 | release reset **HIGH**, **wait 10 ms** |
| 6 | MCLK  | 24000000 | 10 | **start MCLK 24 MHz LAST**, wait 10 ms |

### powerDownSequence (S5KJD1SP)
```
MCLK=0 (1ms) → RESET=1 (1ms) → RESET=0 (1ms) → VDIG=0 (0ms) → VANA=0 (1ms) → VIO=0 (0ms)
```

Two structural facts here that differ from mainline and are the prime suspects:
- **A) Reset is a real pulse** (LOW 5 ms, then HIGH), not a static "release."
- **B) MCLK is enabled AFTER reset is de-asserted** (step 6), i.e. the sensor sees
  reset toggle *before* it ever sees a clock, then gets MCLK to lock its PLL.

> CAVEAT: CamX applies power settings in the *order listed*, but the qcom power-seq
> executor also groups/optimizes; some Samsung modules’ real ordering (esp. MCLK vs
> reset) can be tuning-specific. The S5KJN1 rhodep blob may order MCLK before reset
> (many JN1 tunings do MCLK→VANA→VDIG→VIO→reset). Treat A/B as *two independent
> hypotheses to sweep*, not gospel. The byte-exact rhodep order is only guaranteed by
> ftracing stock (C2).

### S5KJD1SP init (resSettings) — confirms the probe is COLD
The JD1SP `resSettings` (the big 0x6028/0x602A/0x6F12 block) is a **resolution/stream
init**, applied at stream configure, *not* at probe/MatchID. There is no register
write in the powerUpSequence and none required before reading the id. This matches
`cam_sensor_match_id` reading the id cold (cam_sensor_core.c:769-774) and matches
mainline reading the id immediately after power-on. FACT.

---

## 4. i2c frequency mode CamX uses

- S5KJD1SP XML: `i2cFrequencyMode = FAST` (400 kHz). FACT.
- Enum mapping (cam_sensor_cmn_header.h:93-99): `I2C_STANDARD_MODE=0, I2C_FAST_MODE=1,
  I2C_CUSTOM_MODE=2, I2C_FAST_PLUS_MODE=3`. FACT.
- rhodep blob carries a `sensorI2CFrequencyMode` field (present, value not byte-decoded
  here). INFERENCE: JN1-class modules run **FAST (1) or FAST_PLUS (3)**, essentially
  never STANDARD.
- Mainline `i2c-qcom-cci` picks the master mode only from the child i2c-bus
  `clock-frequency` (≤400k→FAST, ≤1M→FAST_PLUS, else STANDARD). If the rhodep mainline
  cci bus has no/!low `clock-frequency`, it defaults **STANDARD (100 kHz)** — a real
  mismatch vs the vendor's FAST. FACT (OUT_corewake Q1-b).

Action: set the mainline cci bus hosting 0x56 to `clock-frequency = <400000>` (FAST),
and as a second test `<1000000>` (FAST_PLUS). Cheap; pairs with the reset/MCLK sweep.

---

## 5. Any init/wake register write CamX does before/at identify that mainline omits?

**No — and this is important.** Both the CamX probe (MatchID cold, no writes,
cam_sensor_core.c:769-774) and the working FP5 mainline driver read the chip id with
**zero register writes** after power-on. The 0x6028/0x0000/0x001e/0x6010/0x6226 block
in mainline `s5kjn1_enable_streams()` (s5kjn1-mainline.c:905-922) is a **stream-enable
wake**, applied only at streamon, *after* identify — and it is itself transcribed from
the vendor stack. It was already tried on-device with no effect (context_v3:20,
OUT_corewake C5), which is consistent: you cannot "boot the core with a register write"
if the register interface is the thing that isn't alive yet.

Conclusion: the missing rhodep step is **not** a register write. It is a
power/clock/reset **sequencing/timing** detail (section 3 A/B) and/or the i2c
freq-mode (section 4).

---

## 6. Diff vs the mainline driver, and the concrete change to try

### The mainline power-on (what rhodep runs today)
FACT (`s5kjn1-mainline.c:1202-1241`, `s5kjn1_power_on`):
```
enable vddd  → sleep 1-2 ms
enable vdda
enable vddio
enable afvdd
clk_prepare_enable(mclk)          # MCLK on
gpiod_set_value(reset_gpio, 0)    # reset RELEASED immediately after MCLK on
usleep_range(10-15 ms)            # single settle, THEN read chip id
```
Key differences vs the CamX/JD1SP template (section 3):
1. **No MCLK-stable settle before reset.** Mainline enables MCLK and de-asserts reset
   back-to-back; the sensor PLL may not be locked when the digital top leaves reset →
   "powered, ACKs, holds state, but register core unclocked" = exactly the observed
   residue signature.
2. **Reset is a static release, not a pulse.** Vendor drives reset LOW (5 ms) then
   HIGH. Mainline (via reset-gpios ACTIVE_LOW + gpiod value 0 = de-assert) just lets
   it go high once. If the line was already high from a previous boot/bootloader,
   the sensor never sees a clean reset edge.
3. **MCLK/reset order.** Vendor (JD1SP) enables MCLK *after* reset release; mainline
   before. (Hypothesis B.)
4. **i2c freq mode.** Vendor FAST/FAST_PLUS; mainline defaults STANDARD unless the
   cci bus sets `clock-frequency`.

### Change to try on device (driver code) — sweep, cheapest first

Patch `s5kjn1_power_on()` in the rhodep mainline driver to mirror the vendor
sequencing. Try these variants in order; re-run probe (cold chip-id read) after each:

**Variant 1 — MCLK-settle before a real reset pulse (keep MCLK-before-reset):**
```c
/* rails first, as today */
regulator_enable(vddd); usleep_range(1000, 2000);   /* DVDD, +1 ms  */
regulator_enable(vdda);                              /* AVDD         */
regulator_enable(vddio);                             /* VDDIO        */
usleep_range(10000, 11000);                          /* +10 ms (VDIG-settle, JD1SP) */

clk_prepare_enable(mclk);                            /* MCLK 24 MHz  */
usleep_range(5000, 6000);                            /* NEW: 5 ms MCLK-stable BEFORE reset */

gpiod_set_value_cansleep(reset_gpio, 1);             /* assert reset LOW (active-low) */
usleep_range(5000, 6000);                            /* hold 5 ms (JD1SP step 4)      */
gpiod_set_value_cansleep(reset_gpio, 0);             /* release reset HIGH            */
usleep_range(12000, 15000);                          /* +12 ms post-reset (JD1SP 10 ms, margin) */
/* now read chip id */
```

**Variant 2 — MCLK LAST (vendor JD1SP order, hypothesis B):**
```c
regulator_enable(vddio); usleep_range(1000, 2000);   /* VIO +1 ms   */
regulator_enable(vdda);                              /* VANA        */
regulator_enable(vddd);  usleep_range(10000, 11000); /* VDIG +10 ms */

gpiod_set_value_cansleep(reset_gpio, 1);             /* reset LOW   */
usleep_range(5000, 6000);                            /* 5 ms        */
gpiod_set_value_cansleep(reset_gpio, 0);             /* reset HIGH  */
usleep_range(10000, 11000);                          /* 10 ms       */

clk_prepare_enable(mclk);                            /* MCLK LAST   */
usleep_range(10000, 11000);                          /* 10 ms, then read id */
```

**Variant 3 — pair either variant with FAST i2c** (DT, on the cci bus that hosts 0x56):
```dts
&cci_i2c_bus_for_0x56 {
    clock-frequency = <400000>;   /* FAST; then retest at <1000000> FAST_PLUS */
};
```

Rig-only (no rebuild) pre-check with camdiag, to decide before patching:
```sh
# 1) rails up (VIO, VANA, VDIG) with the 10 ms VDIG settle
# 2) MCLK 24 MHz ON, hold >= 5 ms
# 3) reset LOW 5 ms, reset HIGH, wait 12 ms
# 4) cold read:
i2ctransfer -f -y <bus> w2@0x56 0x00 0x00 r2      # expect 0x38e1
# then repeat with MCLK started AFTER the reset HIGH (variant 2 ordering)
```

### If none of the above yields 0x38e1
The only remaining information-bearing step is to capture the **byte-exact** rhodep
power buffer from stock (OUT_corewake C2): ftrace `cam_sensor_core_power_up` /
`cam_sensor_util.c` seq-type + delay logging on stock Android, and transcribe the exact
(seq_type, config_val, delay) tuples. The blob confirms that buffer exists; only the
QTI parser (or the ftrace) renders its exact numbers. Given FP5 works with plain
mainline timing, the electrical sweep (Variants 1-3) is more likely the fix than a
hidden tuple.

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- rhodep DT node has no `qcom,cam-power-seq`; power seq comes from CamX/blob
  (`blair-...overlay.dtsi:421-461`).
- The rhodep S5KJN1 power seq is compiled into
  `com.qti.sensormodule.mot_rhodep_s5kjn1_qtech.bin` (269 KB, QTI "Parameter Parser
  V3" Chromatix). Blob downloaded; contains `powerUpSequence`, `powerDownSequence`,
  `powerSetting`, `sensorSlaveAddress`, `sensorI2CFrequencyMode`, `standbySettings`,
  sensorId 0x38e1 (offset 0x348c0), MCLK 24000000 (offset 0x35147).
  Source: TheMuppets/proprietary_vendor_motorola_rhodep, path
  proprietary/vendor/lib64/camera/com.qti.sensormodule.mot_rhodep_s5kjn1_qtech.bin.
- context_v3's "blob has no power sequence" is incorrect; the blob does carry it.
- FP5 (qcm6490-fairphone-fp5.dts) runs the **same** mainline s5kjn1 driver and
  identifies fine: S5KJN1 wakes on rails+24 MHz MCLK+reset with mainline timing, no
  CamX-only pre-MatchID write. (mainline dts + LWN v4 Tested-by).
- CamX MatchID is cold: no register writes before chip-id read
  (cam_sensor_core.c:769-774).
- Closest readable CamX S5K template (S5KJD1SP chi-cdk XML):
  slave 0x20, WORD/WORD, id 0x3841, **i2cFrequencyMode FAST**, powerUp =
  VIO(1ms)→VANA(0)→VDIG(10ms)→RESET0(5ms)→RESET1(10ms)→MCLK 24M(10ms); its big
  0x6028/0x602A/0x6F12 block is stream init (post-identify), not probe.
- Mainline power-on (s5kjn1-mainline.c:1202-1241): MCLK enabled immediately before a
  single static reset release + one 10-15 ms settle; no MCLK-stable-before-reset, no
  reset pulse.
- i2c freq enum: STANDARD=0/FAST=1/CUSTOM=2/FAST_PLUS=3 (cam_sensor_cmn_header.h).
- Mainline cci picks freq mode from child bus `clock-frequency`, defaults STANDARD.
- The 0x6028/0x0000/0x001e/0x6010/0x6226 mainline block is in enable_streams
  (post-identify) and was already tried on-device with no effect (context_v3:20).

**INFERENCE**
- The rhodep failure vs working FP5 is an electrical/sequencing/bus-mode delta, not a
  missing register wake write (FP5 proves the core wakes with plain mainline timing +
  no pre-id write).
- Highest-value fixes: (A) add MCLK-stable settle before reset + make reset a real
  low→high pulse with JD1SP-style delays; (B) try MCLK enabled AFTER reset release
  (vendor JD1SP order); (C) pin the cci bus to FAST (400k), then FAST_PLUS (1M).
- rhodep blob's sensorI2CFrequencyMode is almost certainly FAST or FAST_PLUS, not
  STANDARD.

**UNKNOWN**
- The byte-exact rhodep (configType, configValue, delayMs) tuples inside the blob —
  the nested Parameter-Parser-V3 array was not byte-decoded here. Get them definitively
  by ftracing `cam_sensor_core_power_up` on stock (OUT_corewake C2), or by running the
  QTI parser over the .bin. The JD1SP order/delays are the working proxy until then.
- The exact numeric sensorI2CFrequencyMode value in the rhodep blob (field present,
  value not decoded).
- Whether Variant 1, Variant 2, or +FAST i2c is the single required change, or a
  combination.

## Sources
- chi-cdk S5KJD1SP XML: github.com/OrphyWang/Qcom-CAMX-CHI (fork of
  comprehensive9/vendor_qcom_proprietary), 11se branch,
  chi-cdk/oem/qcom/sensor/s5kjd1sp_xr/s5kjd1sp_hmd_0_sensor.xml (fetched).
- rhodep vendor blob: github.com/TheMuppets/proprietary_vendor_motorola_rhodep,
  lineage-23.2, proprietary/vendor/lib64/camera/com.qti.sensormodule.mot_rhodep_s5kjn1_qtech.bin
  (git blob e00b72530f49781d59be1f01d4499fa9ee902aeb, downloaded + parsed).
- FP5 working DT: torvalds/linux arch/arm64/boot/dts/qcom/qcm6490-fairphone-fp5.dts.
- Mainline driver + Tested-by: LWN/lore "[PATCH v4] media: i2c: add Samsung S5KJN1"
  (Vladimir Zapolskiy, Tested-by Neil Armstrong / Luca Weiss, FP5).
- Local: s5kjn1-mainline.c, cam_sensor_cmn_header.h, blair-...overlay.dtsi,
  OUT_corewake.md, OUT_power_seq.md, context_v3.txt.
