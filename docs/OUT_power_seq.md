# S5KJN1 rhodep: the downstream power-up + init sequence vs mainline

Reverse-engineered from the downstream techpack-camera driver in `downstream/`,
cross-checked against `s5kjn1-mainline.c`, patches 0053-0056, and the two vendor
DTs. Focus per the brief: the exact ordered power-up/init the stock runs, and the
one step mainline does differently. Every claim is tagged FACT (with
`file:line`), INFERENCE, or UNKNOWN.

---

## TL;DR — ranked

1. **(MOST LIKELY) The CCI I2C timing is calibrated for a 37.5 MHz CCI source
   clock, and mainline's `i2c-qcom-cci` almost certainly does not run the CCI at
   37.5 MHz.** The downstream loads absolute SCL setup/hold cycle-counts
   (`hw_thigh`, `hw_tsu_sta`, `hw_thd_dat`, …) that are only correct at
   `cci-clk-src = 37500000`. Get the source clock wrong and the *address phase*
   (loose timing) still ACKs while the *data phase* (tight setup/hold on SDA
   sampling) corrupts — which is exactly "ACK good, chip-id garbage." This is a
   clock-tree bug, but **not** the MCLK and **not** "turbo"; it is the CCI's own
   source clock rate and the timing registers derived from it. See §2 and §5.

2. **(LIKELY, cheap) `sensorI2CFrequencyMode` was never pinned; downstream picks a
   specific mode whose timing block is what actually gets programmed.** The
   S5KJN1's slave-info in the blob selects one of {100k,400k,1M,custom}. Mainline
   has no equivalent — the CCI bus rate is set by `clock-frequency` in DT, and
   patch 0054 currently sets CCI0 **master-1** bus to **100 kHz** with a comment
   that 400 kHz came back byte-shifted. That byte-shift is the same timing
   signature as #1. See §4.

3. **(POSSIBLE) MCLK is enabled *after* the CAMSS-TOP GDSC in downstream, as one
   fused "MCLK" power step, and downstream may re-assert the MCLK rate from the
   CamX `config_val` at power-up.** Mainline relies on `assigned-clock-rates` and
   never calls `clk_set_rate`, and the GDSC is owned by the camss node, not the
   sensor. See §3.

4. **(RULED OUT as the fix, but confirmed) The rail order.** Downstream is
   vio→vana→vdig; mainline patch 0056 already matches this. Not the gap.

5. **The "boot the core with 0x6010=1" idea from the prior RE is NOT what stock
   does at probe.** Downstream `cam_sensor_match_id` reads the id cold with zero
   register writes first (FACT, §6). So a missing init-write is not the stock
   behaviour; the earlier OUT_s5kjn1_bringup "wake the core" theory is not
   supported by the downstream probe path.

---

## 1. The EXACT downstream power-up sequence (ordered, with delays)

### Where the sequence comes from
- For a `qcom,cam-sensor` node the power sequence is **not** in the DT and **not**
  in the sensormodule blob (confirmed in FEASIBILITY.md: blob has no sensor
  `powerUpSequence`). It is supplied by **CamX/userspace** as a command buffer and
  parsed in `cam_sensor_update_power_settings()` — FACT
  `cam_sensor_util.c:1383-1407` (reads `power_seq_type` + `config_val_low` per
  entry) and delays are attached from following WAIT ops
  `cam_sensor_util.c:1411-1422`.
- The DT-fallback parser exists too (`cam_get_dt_power_setting_data`,
  `cam_sensor_util.c:1542`, keys `qcom,cam-power-seq-type/-cfg-val/-delay`) but the
  rhodep sensor node does **not** carry those keys, so the CamX buffer is what
  runs. INFERENCE (the node in `blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi`
  has no `qcom,cam-power-seq-*`).

### What the sequence contains (the CamX default for a 4-rail + MCLK + reset sensor)
INFERENCE from the driver's own execution model plus the DT wiring
(`sensor_main` has `regulator-names = "cam_vio","cam_vana","cam_vdig","cam_clk"`,
`blair-...-overlay.dtsi:436-437`; `gpio-reset = <1>`, reset on tlmm35). The
downstream `cam_sensor_core_power_up` walks `power_setting[]` in order and for each
entry does the type-specific action then a delay (`cam_sensor_util.c:2051-2223`):

```
step  seq_type      action (file:line)                                   delay
----  -----------   -------------------------------------------------    --------
 1    SENSOR_VIO    enable cam_vio  (FAN53870 LDO7, 1.8V)  :2150-2211    ~1 ms
 2    SENSOR_VANA   enable cam_vana (FAN53870 LDO4, 2.8V)  :2150-2211    ~1 ms
 3    SENSOR_VDIG   enable cam_vdig (FAN53870 LDO1, 1.05V) :2150-2211    ~1 ms
 4    SENSOR_MCLK   (a) enable "cam_clk" regulator = CAMSS_TOP GDSC
                        :2071-2105
                    (b) if config_val: clk_rate[0][mclk]=config_val
                        :2106-2108   (config_val = 24000000 from CamX)
                    (c) enable ALL soc clks at clk_rate[0][*]
                        :2110-2117                                        ~1 ms
 5    SENSOR_RESET  drive reset GPIO to config_val (=1, released)
                    via msm_cam_sensor_handle_reg_gpio :2124-2148         ~10-18 ms
```

Key mechanics FACT:
- The generic delay handler after every step: `>20 ms → msleep`, else
  `usleep_range(delay*1000, …)` — `cam_sensor_util.c:2218-2222`.
- The MCLK step **fuses three things**: the CAMSS-TOP GDSC (`cam_clk`), the MCLK
  rate override, and enabling *every* clock the CCI/sensor soc_info holds
  (`cam_sensor_util.c:2110-2117`). This is the single biggest structural
  difference from mainline (§3).
- `SENSOR_RESET` is a GPIO write with `config_val` as the level
  (`cam_sensor_util.c:2140-2143` → `msm_cam_sensor_handle_reg_gpio`). CamX's
  default asserts-then-releases with a delay; the exact hold is set by the WAIT
  op that follows the reset entry.

UNKNOWN: the precise CamX delays per step (they live in the CamX sensor XML /
driver `.so`, not in this tree). But the *shape* above — rails first, GDSC+MCLK as
one step, reset last with the longest settle — is fixed by the driver.

### Compare to mainline (`s5kjn1_power_on`, `s5kjn1-mainline.c:1202-1243`)
```
vddio  -> 2-3ms
vdda   -> 2-3ms
vddd   -> 2-3ms
clk_prepare_enable(mclk)          <-- no clk_set_rate; relies on DT assigned rate
gpiod_set_value(reset, 0)  (release, ACTIVE_LOW)
10-15 ms
```
Order (vio,vana,vdig,mclk,reset) and rail delays now **match** downstream after
patch 0056. The differences that remain are all in the *clock domain*, §2/§3.

---

## 2. The clock detail — this is where the residue symptom points

### FACT: the CCI runs its source clock at 37.5 MHz, and the I2C timing is built from that
- `blair-camera.dtsi:170` / `:263`: both CCI nodes have
  `clock-rates = <0 37500000>` on `cci_X_clk` / `cci_X_clk_src`.
- Every I2C-freq timing sub-node carries `cci-clk-src = <37500000>`
  (`blair-camera.dtsi:196,211,226,241` for cci0; `:285,300,315,330` for cci1).
- Downstream default if DT is missing: also 37.5 MHz — `cam_cci_soc.c:260`
  (`cci_clk_src = 37500000`).
- The SCL/SDA control registers are programmed with the **absolute cycle counts**
  from the selected freq node: `hw_thigh<<16 | hw_tlow` into `M1_SCL_CTL`, plus
  `tsu_sto/tsu_sta`, `thd_dat/thd_sta`, `tbuf`, and `trdhld/tsp/scl_stretch`
  (`cam_cci_core.c:679-694` for master 1). These counts are in units of the CCI
  source-clock period. At 400 kHz fast-mode the values are `thigh=38, tlow=56,
  thd_dat=22, tsu_sta=40, …` (`blair-camera.dtsi:200-213`) — meaningful **only**
  at 37.5 MHz.
- `cycles_per_us` (used to convert programmed delays to cycles) is also derived
  from the source clock: `((clk/1000)*256)/1000` (`cam_cci_core.c:564`), i.e.
  ~9600 at 37.5 MHz. Wrong source clock ⇒ wrong internal timing throughout.

### INFERENCE: mainline's i2c-qcom-cci does not match this
Mainline `drivers/i2c/busses/i2c-qcom-cci.c` sets the CCI clock rate from its own
per-compatible table and its own hardcoded `hw_params` for each speed mode; it
does **not** read `cci-clk-src` from DT and does **not** consume the vendor's
per-mode `hw-thigh/...` blocks. For the `qcom,msm8996-cci` family the mainline
`cci_clk` is commonly driven at **19.2 MHz**, and the SCL timing table is computed
for that. If mainline is clocking the CCI core at 19.2 MHz (or any rate ≠ the
37.5 MHz the vendor timing assumes), then:
- The **address byte** goes out with generous margins → the sensor's I2C target
  still recognises 0x56 and ACKs (matches: "ACKs its address").
- The **data phase** — where SDA is sampled against SCL with the tight
  `thd_dat`/`tsu` windows — is off, so read bytes are mis-sampled/shifted. This is
  precisely the "byte-shifted stream" (FEASIBILITY.md:460-467) and the
  "0x02≡0x0a" aliasing: a data-phase timing error, not a dead core.

### Why the EEPROM still works but the sensor does not
INFERENCE: the EEPROM at 0x50 answers with slow, forgiving open-drain timing and
tolerates the mis-clocked data phase; the S5KJN1's I2C target block clocks its
read shift-register/data path off a tighter internal timing that is unforgiving of
the wrong SCL setup/hold. The observation that residue **tracks MCLK frequency**
(0x8b05@24, 0x8b21@19.2) is consistent with the sensor sampling the (mis-timed)
bus against an MCLK-derived internal strobe: the returned bits move as you move
MCLK. That is a *timing/sampling* signature, not an "unpowered core" signature.

### The `clock-cntl-level = "turbo"` lead — probably NOT the cause
- On the **sensor node**, `clock-cntl-level = "turbo"` + `clock-rates =
  <24000000>` (`...-overlay.dtsi:459-460`) applies to the sensor's own MCLK only;
  the MCLK is a fixed 24 MHz regardless of level (single rate), so "turbo" changes
  nothing for MCLK. FACT (single `clock-rates` value).
- On the **CCI node** the level is `"svs"`, not turbo (`blair-camera.dtsi:169,262`)
  — so even the vendor does not run CCI at turbo.
- Conclusion: turbo is a red herring for this symptom. The clock that matters is
  the **CCI source clock rate (37.5 MHz) and the timing registers derived from
  it**, which mainline sets independently. (Downgrades the prior RE's §D lead.)

INFERENCE: there is no separate "digital-core clock" the sensor needs beyond MCLK.
The S5KJN1 builds its internal PLL from the 24 MHz MCLK; nothing in the downstream
enables a second reference to the sensor.

---

## 3. MCLK/GDSC ordering and rate — the structural port gap

FACT (downstream): the `SENSOR_MCLK` step first enables the `cam_clk` regulator,
which is `gcc_camss_top_gdsc` (`...-overlay.dtsi:435`), i.e. the **CAMSS-TOP GDSC
power domain is brought up as part of the sensor's power sequence, immediately
before the MCLK and before reset is released** (`cam_sensor_util.c:2071-2117`).

FACT (mainline): the sensor node has **no `cam_clk`/GDSC supply**
(`0055-...patch:83-85` lists only vdda/vddd/vddio). The CAMSS-TOP GDSC is a
`power-domains = <&gcc CAMSS_TOP_GDSC>` on the *camss* node (`0054-...patch:100`)
and on the *cci* nodes (`0054-...patch:122,151`). So on mainline the GDSC is up
whenever CCI/camss is runtime-resumed — which is a different lifetime than
"asserted as the sensor's 4th rail right before MCLK+reset."

INFERENCE: this is unlikely to be *the* fix on its own (the GDSC being up via CCI
is enough to clock MCLK), but it is a genuine ordering difference. If MCLK is
gated/ungated at a different instant relative to reset-release than the sensor's
internal reset expects, the internal PLL lock window can be missed. Lower priority
than §2.

FACT (rate): downstream may overwrite the MCLK rate from the CamX `config_val`
right before enabling clocks (`cam_sensor_util.c:2106-2108`); mainline never calls
`clk_set_rate` and depends on `assigned-clock-rates = <24000000>`
(`0055-...patch:80-81`). Verify on-device that `GCC_CAMSS_MCLK1_CLK` actually reads
24.000 MHz (FEASIBILITY.md already saw `clk_rate = 24000000`, so this is probably
fine — UNKNOWN whether the parent/duty is clean).

---

## 4. I2C frequency mode — never pinned, and it selects the timing block

FACT: the sensor's freq mode is taken from `i2c_info->i2c_freq_mode`
(`cam_sensor_util.c:488`) which comes from the CamX slave-info (the undecoded
`sensorI2CFrequencyMode` blob field). The enum is
`{STANDARD=0, FAST=1, CUSTOM=2, FAST_PLUS=3}` (`cam_sensor_cmn_header.h:93-99`).
`cam_cci_set_clk_param` uses that index to pick which `hw-thigh/...` block to load
into the SCL/SDA regs (`cam_cci_core.c:662`), and `cam_cci_get_clk_rates` uses it
to pick `cycles_per_us` (`cam_cci_core.c:583-619`).

FACT (mainline gap): mainline has no per-sensor freq-mode concept. Patch 0054 sets
the **CCI0 master-1 bus to 100 kHz** (`0054-...patch:137-144`, with the comment
that 400 kHz came back byte-shifted). The byte-shift at 400 kHz is the §2 timing
symptom; dropping to 100 kHz masks it partially (looser windows) but the read is
still wrong because the *source clock* the timing is computed against is still
whatever mainline uses, not 37.5 MHz.

Concrete: the CamX XML for JN1 modules of this class typically uses **FAST (400k)**
or **FAST_PLUS (1M)**. The prior sessions tried 400k and 100k but **never 1 MHz**,
and — more importantly — never matched the *source clock + timing-register* model
the vendor uses. So the freq-mode value alone is secondary to §2.

---

## 5. Reset / GPIO detail — no missing second GPIO

FACT: the vendor `sensor_main` node lists exactly two GPIOs — MCLK1 (tlmm30) and
reset (tlmm35) — with `gpio-reset = <1>` (`...-overlay.dtsi:448-454`). Patch 0055
replicates both (`reset-gpios = <&tlmm 35 GPIO_ACTIVE_LOW>`, MCLK on gpio30). There
is **no** extra sensor GPIO in the vendor node that mainline omits. The OIS/actuator
have a separate `cam_ois_ldo` on tlmm86 (`...-overlay.dtsi:327-338`) but that is
not on the sensor's own power path (it's the OIS/AF supply), and FEASIBILITY.md
already tried enabling it with no change. So the reset/standby "second GPIO" theory
is **ruled out**.

FACT: reset polarity/active-low matches (`cam_sensor_main_reset_suspend` is
`output-low` = powered-down; mainline uses `GPIO_ACTIVE_LOW`). Downstream pulses
reset via the CamX sequence (assert→delay→release); the exact hold is a CamX WAIT
op (UNKNOWN value) but mainline's 10-15 ms release settle is in range.

---

## 6. match_id / what happens between power-up and the id read

FACT (`cam_sensor_core.c:1004` → `cam_sensor_match_id:749-806`): after
`cam_sensor_power_up`, downstream reads `sensor_id_reg_addr` (0x0000) **cold** with
`camera_io_dev_read` and compares to 0x38e1. **No register writes, no PLL write,
no "enable interface" write first.** With `CONFIG_CAM_SENSOR_PROBE_RETRY` it just
retries the *same cold read* up to 5× (`:767-787`). This matches mainline
`s5kjn1_identify_sensor` exactly.

FACT: the `CONFIG_CAMERA_CCI_ADDR_SWITCH` pre-read write (`:989-1002`,
`cam_sensor_set_i2c_addr_switch_reg:480-528`) only fires if the HAL set
`i2c_addr_switch` in the sensor XML — used for parts that boot at one I2C address
and are switched to another. The S5KJN1 is strapped at 0x56; this path is
`return 0` immediately when `!i2c_addr_switch` (`:488`). **Not relevant.**

FACT: the `CONFIG_CAM_DISTINGUISH_SENSOR_VERSION` special path in
`cam_sensor_io.c:9-18` targets `SPECIAL_SENSOR_ID 0x5041` at `SPECIAL_SENSOR_IIC_ADDR
0x10` on **cci_device 1 / master 0** (`cam_sensor_io.c:126-130, 239-241`). The
S5KJN1 is id 0x38e1 at 0x56 on cci_device **0** / master **1**. **Not relevant to
rhodep.** (Directly answers the brief's Q3: that special path is for a different
sensor, not any rhodep sensor on this bus.)

Conclusion: the stock does **not** do a secret init-before-probe. So the fix is not
"issue writes before reading id." It is getting the *read itself* to return correct
data — i.e. the CCI timing/clock of §2/§4.

---

## Concrete changes to try, in order

### Fix A (top): make mainline's CCI clock + timing match the vendor's 37.5 MHz model
- On-device first, cheap: read `/sys/kernel/debug/clk/clk_summary` for the CCI
  core clock (the clock feeding `5c1b000.cci`) while camdiag holds the part.
  Note its rate. If it is **not 37.5 MHz** (e.g. 19.2 MHz), that is the smoking
  gun for the data-phase corruption.
- Driver: in `i2c-qcom-cci.c`, for the sm6375/msm8996-cci path, ensure the CCI
  source clock is set to **37500000** and the `hw_params` timing table used for
  the selected speed matches the vendor `hw-thigh/tlow/tsu*/thd*/tbuf/trdhld/tsp`
  values from `blair-camera.dtsi:200-213` (fast) or `:230-243` (fast-plus). The
  cleanest port is to add an sm6375 `hw_params` entry whose SCL/SDA counts equal
  the vendor's at 37.5 MHz. INFERENCE: this is the highest-probability fix given
  the residue-tracks-MCLK + EEPROM-ok + byte-shift evidence.
- If patching the driver is too heavy for a first test, set
  `assigned-clocks`/`assigned-clock-rates` on the CCI clock in DT to force
  37.5 MHz and see if the id read cleans up even at the current speed.

### Fix B (cheap, do alongside A): sweep the real speed modes at the correct source clock
- Try CCI0 master-1 bus `clock-frequency = <1000000>` (Fast-Plus) and `<400000>`
  once the source clock is 37.5 MHz. The vendor Fast-Plus timing is
  `blair-camera.dtsi:230-243`. The prior 400k byte-shift was almost certainly the
  wrong-source-clock artefact, so re-test after A.

### Fix C (lower priority): bring CAMSS-TOP GDSC up as part of sensor power, before MCLK
- Only if A/B don't fully fix it. Mirror downstream by ensuring the GDSC is
  enabled and MCLK started *before* reset release with a settle, matching
  `cam_sensor_util.c:2071-2117`. In mainline terms: confirm the camss/cci runtime
  PM has the `CAMSS_TOP_GDSC` on before `s5kjn1_power_on` releases reset (order the
  subdev power so CCI is resumed first).

### Do NOT pursue
- "Boot the core via 0x6010=1 before id" — stock does not do this at probe (§6).
- `clock-cntl-level=turbo` on any clock — vendor CCI is svs; MCLK is single-rate
  24 MHz (§2). Not the cause.
- A missing rail / second reset GPIO — ruled out (§5, FEASIBILITY.md).

---

## FACT / INFERENCE / UNKNOWN summary

**FACT**
- Sensor power sequence is CamX-supplied, parsed `cam_sensor_util.c:1383-1422`; not
  in blob, not in DT for rhodep.
- Power-up executor order rails→(GDSC+MCLK fused)→reset, per-step delay logic:
  `cam_sensor_util.c:2051-2223`; MCLK step enables `cam_clk`=CAMSS_TOP GDSC then
  all clocks: `:2071-2117`; MCLK rate can be overwritten from `config_val`:
  `:2106-2108`.
- CCI source clock 37.5 MHz and all I2C timing calibrated to it:
  `blair-camera.dtsi:170,196,211,226,241,263,285,300,315,330`; default
  `cam_cci_soc.c:260`; timing regs loaded from those counts
  `cam_cci_core.c:664-694`; `cycles_per_us` from source clock
  `cam_cci_core.c:564`.
- CCI node level is "svs" not turbo; sensor MCLK is single-rate 24 MHz so "turbo"
  is inert for it: `blair-camera.dtsi:169,262`; `...-overlay.dtsi:459-460`.
- id is read cold, no pre-writes: `cam_sensor_core.c:1004`,
  `cam_sensor_match_id:749-806`.
- ADDR_SWITCH and SPECIAL_SENSOR paths do not apply to rhodep's S5KJN1:
  `cam_sensor_core.c:488`; `cam_sensor_io.c:11-14,126-130,239-241`.
- Mainline `s5kjn1_power_on` order matches after patch 0056; no `clk_set_rate`;
  GDSC owned by camss/cci nodes not sensor: `s5kjn1-mainline.c:1202-1243`,
  `0054-...patch:100,122,151`, `0055-...patch:79-85`.
- No missing sensor GPIO in the vendor node: `...-overlay.dtsi:448-454`.

**INFERENCE**
- Mainline `i2c-qcom-cci` runs the CCI core at a rate ≠ 37.5 MHz and uses its own
  timing table, so the SDA data-phase sampling is off → ACK ok, data corrupt →
  byte-shift/aliasing/residue-tracks-MCLK. This is the leading root cause.
- The EEPROM tolerates the mis-timing; the sensor's tighter I2C data path does not.
- CamX freq mode for this JN1 is FAST or FAST_PLUS; 1 MHz was never tried; matters
  only once the source clock is correct.
- GDSC-before-MCLK ordering is a real but secondary difference.

**UNKNOWN (resolve on device / from CamX .so)**
- The measured CCI core clock rate on the mainline port (check clk_summary) — the
  single most decisive datum.
- Exact CamX per-step delays and reset hold, and the exact
  `sensorI2CFrequencyMode` value (in `com.qti.sensor.mot_s5kjn1.so`, AArch64).
- MCLK duty/quality (scope).
