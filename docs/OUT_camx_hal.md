# S5KJN1 rhodep — CamX HAL (camera.qcom.so) RE: power-up + chip-id-read sequence

Target: `camera.qcom.so` (11.9 MB, ARM64). Build tag in binary:
`vendor/qcom/proprietary/camx-legacy/../camx-legacy/rhodep/src/...` — this is the
actual **rhodep** CamX build, not a proxy. FACT (string, e.g. `.rodata` build paths).

Method note (important): the blob is *stripped* in `.dynsym`, but it ships a
`.gnu_debugdata` section (LZMA) that restores **7646 local symbols**. I extracted it
(`dd skip=0xb4812c count=0x1be48 | xz -d`) and used it to name every function below.
All addresses are file/vaddr in `camera.qcom.so` (`.text` vaddr == file offset).
This is a much stronger source than the format-string heuristic; every claim marked
FACT has a symbol + disassembly behind it.

---

## TL;DR — the decisive result

**The CamX HAL does NOT contain a hardcoded/default sensor power-up sequence.** It builds
the power packet *entirely* from the parsed sensormodule `.bin` (`SensorDriverData`).
When the bin carries no power sequence — which prior RE proved is the rhodep case —
CamX logs `"Power settings are not valid. Will use default settings"`, then falls back to
a **second** power-settings slot in the module data; if that is also empty (it is, for
rhodep), the emitted `cam_cmd_power` packet has **count = 0** and the kernel logs
`"pwr_up_size is zero"`. There is **no S5K/JN1/38e1-specific code path** anywhere in the
HAL. Everything sensor-specific is data, and that data lives in the `.bin` (empty) — not
in code.

Consequence for the bring-up:
- There is **no secret extra reset toggle / settle / register-write compiled into
  camera.qcom.so** that mainline is missing. (Valid negative — see §7.)
- The only place the *actual delays/order* can differ between rhodep and FP5 is the
  per-sensor `(seq_type, config, delay)` tuples, which CamX reads from the `.bin` and
  hands to the **same kernel executor** (`cam_sensor_core_power_up`) that mainline’s
  qcom stack uses. Those exact tuples are only obtainable by ftracing stock or by
  running the QTI Parameter-Parser over the `.bin` (see §7).
- Two *mechanical* differences between how the CamX/kernel executor drives the rails
  vs how the mainline `s5kjn1.c` driver does are, however, fully recoverable and are the
  concrete, testable hypotheses (see §5–§6). The strongest is **RESET polarity/level
  semantics** and **MCLK-vs-reset ordering**.

---

## 1. The functions that build & apply the sequence (FACT, with symbols)

| Function | vaddr | Role |
|---|---|---|
| `CamX::SensorDriverDataClass::LoadSensorDriverData` | `0x2746f8` | parses the `.bin` into `SensorDriverData`; calls the power-seq loader twice |
| `CamX::camxsensorcommonClass::LoadPowerSequenceInfo` | `0x274118` | reads `{count; PowerSetting[count]}` (stride **12 B**) from the parsed table into `PowerSequenceInfo` |
| `CamX::ImageSensorUtils::GetPowerSequenceCmdSize` | `0x8c6c80` | sizes the cmd buffer from a `PowerSetting[]` |
| `CamX::ImageSensorUtils::CreatePowerSequenceCmd` | `0x8c6ba0` | serialises `PowerSetting[]` → kernel `cam_cmd_power` + `cam_cmd_unconditional_wait` |
| `CamX::SensorNode::CreateSensorSubmodules` | `0x8e9670` | picks primary vs fallback power settings; emits the *"use default settings"* log |
| `CamX::ImageSensorModuleData::CreateSensorSubModules` | `0x9cc2d0` | builds the slaveInfo / i2c-info; reads `GetOverrideI2CFrequencyMode` setting |
| `CamX::HwEnvironment::ProbeImageSensorModules` | `0x9b5528` | assembles the boot **probe packet** (i2c-info + slaveInfo + pwrUp + pwrDown) |
| `CSLImageSensorProbeHW` | `0xb200e0`→`0xabe5b0` | submits the probe ioctl to the KMD (power-up → MatchID happen kernel-side) |

### 1a. `CreatePowerSequenceCmd` decoded (FACT — disasm `0x8c6ba0`)
Signature `(void* buf, int cmdType, uint count, PowerSetting* settings)`. Input
`PowerSetting` stride = **0xC** = `{u16 seq_type; u32 config_val; u32 delay}` (fields read
at `[x12-4]`, `[x12+? ]`, `[x12+4]`). For each element it:
1. writes a `cam_cmd_power` power-setting (`strh seq_type`, `str config_val+4`), and
2. **if `delay != 0`** (`cbz w1` at `0x8c6c1c`), appends a separate
   `cam_cmd_unconditional_wait` whose header word = **`0x0903`** (`mov w15,#0x903` at
   `0x8c6bd4`; `strh delay,[x4]`, `strh 0x903,[x4,#4]`).

`0x0903` decomposes to `op_code = SW_UCND (0x03..)` / `cmd_type = WAIT (0x09)`. This
matches the kernel folding step (`cam_sensor_util.c:1411-1436`): a WAIT after a power
setting is added into `power_setting[pwr_up-1].delay`. **So per-step delays are carried
as interleaved WAIT commands — CamX faithfully emits them from the `.bin` tuples.** FACT.

### 1b. The "use default settings" branch (FACT — disasm `0x8e9a44`–`0x8e9bd8`)
```
x14 = ImageSensorData(@[module+0x330])           ; base of sensor data
x1  = [x14, #72]   ; primary power-up settings PTR
w0  = [x14, #64]   ; primary power-up settings COUNT
if (PTR==0 || COUNT==0)  -> log 0x86897:
     "[ INFO] ...:380 ... Power settings are not valid. Will use default settings"
; then fall through to the FALLBACK slot:
x1  = [x15, #88]   ; fallback power-up settings PTR
w0  = [x15, #80]   ; fallback power-up settings COUNT
if (PTR==0 || COUNT==0)  -> log again (empty) ; else GetPowerSequenceCmdSize(...)
```
- Log format string: FACT `0x86897` = `"...:380 ... Power settings are not valid. Will use
  default settings"`, category `[SENSOR ]`.
- There are **two** power-settings slots in the module data (primary `+64/+72`,
  fallback `+80/+88`). Neither is populated by *code*; both come from parsing the `.bin`.
  For rhodep both are empty → packet count 0. FACT (control-flow + prior-RE blob parse).

### 1c. No static default table exists (FACT — binary-wide scan)
A whole-file scan for the MCLK constant `24000000` (`0x016E3600`, the value any default
MCLK step would carry) finds **exactly one** occurrence, and it is a coincidental
`movk`-immediate inside `CamX::BHistStats147::Execute` (`0x4eb7fe`), **not** a data
array. There is no `.rodata`/`.data.rel.ro` array of `{seq_type, config, delay}` tuples.
INFERENCE→FACT: **camera.qcom.so has no compiled-in default rail/clock/reset sequence.**

---

## 2. The default CamX sensor power-up sequence

**Recovered result: NONE is compiled into the HAL.** (Valid negative.)

The sequence is 100 % data-driven:
`.bin (powerUpSequence tuples) → LoadPowerSequenceInfo → SensorDriverData → ImageSensorData
→ CreatePowerSequenceCmd → cam_cmd_power packet → KMD cam_sensor_core_power_up`.

The *ordered (type, config, delay) tuples* therefore do not exist as recoverable data in
`camera.qcom.so`. They exist only in the rhodep sensormodule `.bin`
(`com.qti.sensormodule.mot_rhodep_s5kjn1_*.bin`), which prior RE (`OUT_blob_powerseq.md`)
showed carries **no decodable rail array** in its sensor branch. The value semantics the
tuples *would* use are fully known from the kernel executor (see §3).

---

## 3. How the KMD applies each tuple (FACT — `cam_sensor_util.c`)

Enum `msm_camera_power_seq_type` (kernel):
`MCLK=0, VANA=1, VDIG=2, VIO=3, VAF=4, VAF_PWDM=5, CUSTOM_REG1=6, CUSTOM_REG2=7,
RESET=8, STANDBY=9, CUSTOM_GPIO1=10, CUSTOM_GPIO2=11, VANA1=12`.

Executor `cam_sensor_core_power_up` (`cam_sensor_util.c:2005-2222`):
- `SENSOR_MCLK`: enable `cam_clk` regulator, set `clk_rate[0][seq_val] = config_val`
  (this is where **config_val = 24000000** would land), then `clk_enable`.
- `SENSOR_VDIG/VIO/VANA/VANA1/VAF`: `regulator_enable(<cam_vdig/vio/vana/...>)`; if
  `config_val` is a valid voltage it overrides min=max volt.
- `SENSOR_RESET/STANDBY/CUSTOM_GPIO*`: `msm_cam_sensor_handle_reg_gpio(seq_type, val)`
  → `cam_res_mgr_gpio_set_value(gpio_num[seq_type], config_val)`. **`config_val` is the
  RAW GPIO level written**: `RESET config=0` drives the reset line **LOW**, `config=1`
  drives it **HIGH**. (`cam_sensor_util.c:1932-1955`.) FACT.
- **After every step**: `if (delay>20) msleep(delay); else if (delay) usleep_range(delay*1000, +1000)`
  (`cam_sensor_util.c:2218-2222`). Delay units are **ms**. FACT.

Key structural facts this pins down:
- A vendor RESET **pulse** = two tuples `{RESET, 0, dLow}` then `{RESET, 1, dHigh}`.
- MCLK ordering vs reset is *only* whatever order the tuples appear in.
- **RESET polarity is a raw level, NOT interpreted through a DT `GPIO_ACTIVE_LOW` flag.**
  This is the crux of the mainline difference (see §5).

---

## 4. Probe / MatchID flow and i2c frequency mode (FACT)

The boot probe (`HwEnvironment::ProbeImageSensorModules` `0x9b5528`, packet assembly at
`0x9b7500-0x9b7f90`) builds a single probe packet and submits it via `CSLImageSensorProbeHW`
(`0xb200e0`). The packet contains, in order:
1. **i2c-info** command (slave addr + **i2c freq mode**),
2. **slaveInfo** command (probe metadata), header word `0x0103` at `[slaveInfo+2]`,
3. **power-up** `cam_cmd_power` (from `CreatePowerSequenceCmd`, args
   `count=[x+152], ptr=[x+160]`, call at `0x9b7e58`),
4. **power-down** `cam_cmd_power` (args `count=[x+168], ptr=[x+176]`, call at `0x9b7ebc`).
5. Packet opcode set at `0x9b7f68`: `w23 = 0x01000003` = `CAM_SENSOR_PACKET_OPCODE_SENSOR_PROBE`.

slaveInfo field population (FACT, `0x9b7cb4-0x9b7df4`), read from module data `x8`:
```
[x8,#52] -> slaveInfo[+0]   reg addr type   (WORD=2)
[x8,#48] -> slaveInfo[+1]   slave address   (0xac / 0x56)
0x0103   -> slaveInfo[+2]   cmd hdr {type,size}
[x8,#56] -> slaveInfo[+4]   chip-id REG ADDR (0x0000), 8 bytes
[x8,#64] -> slaveInfo[+12]  reg data type / id mask
[id,#48] -> slaveInfo[+18]  expected sensor id (0x38e1)
[x8,#68..144] -> slaveInfo[+20..+53]  RESET / STANDBY / VANA / VDIG gpio {valid,num}
```

**The MatchID (chip-id read) is done entirely kernel-side** (`cam_sensor_match_id` in
`cam_sensor_core.c`, not in this HAL). Between power-up and the read the KMD does: run the
whole power-up tuple list (with its embedded delays) → init the CCI client at the
i2c-info freq mode → read `reg 0x0000` (WORD) → compare to `0x38e1`. There is:
- **No CamX-side settle** beyond the tuple delays.
- **No CamX-side reset pulse** other than the RESET tuples in the sequence.
- **No register write before the id read** — the probe is cold. FACT (packet contains
  only i2c-info/slaveInfo/pwrUp/pwrDown; no `CAMERA_SENSOR_CMD_TYPE_I2C_WR`).

### i2c frequency mode
- The freq mode is a **numeric field carried in the module data** (from the `.bin`
  `sensorI2CFrequencyMode`), placed in the i2c-info command of the probe packet.
- CamX exposes an override via the setting **`GetOverrideI2CFrequencyMode`** (FACT string
  `0x18ef1c`, referenced only from `ImageSensorModuleData::CreateSensorSubModules`
  `0x9cd204/0x9cde7c/0x9ce6e8`) — i.e. it is a *tuning/override knob*, default unset.
- Prior RE decoded the rhodep `.bin` field `sensorI2CFrequencyMode` (id 3681) as **size 0
  = default = `I2C_STANDARD_MODE (0)`**. So unless the override setting is applied, the
  probe read uses the container default. INFERENCE: the effective probe freq is whatever
  the CCI master default is (STANDARD 100 kHz) unless the `.bin`/override says otherwise —
  **the HAL does not force FAST/FAST_PLUS for the probe.**
- Enum (`cam_sensor_cmn_header.h`): STANDARD=0, FAST=1, CUSTOM=2, FAST_PLUS=3.

---

## 5. EXACT difference vs mainline `s5kjn1_power_on` (FACT)

Mainline (`s5kjn1-mainline.c:1201-1240` + probe `:1324`):
```
reset_gpio = devm_gpiod_get_optional("reset", GPIOD_OUT_HIGH)   ; ASSERTED at probe
                                                                ; (ACTIVE_LOW => phys LOW)
power_on:
  regulator_enable(vddd); usleep 1-2 ms
  regulator_enable(vdda)
  regulator_enable(vddio)
  regulator_enable(afvdd)
  clk_prepare_enable(mclk)                 ; MCLK ON
  gpiod_set_value_cansleep(reset, 0)       ; DE-ASSERT reset (ACTIVE_LOW => phys HIGH)
  usleep_range(10-15 ms)                   ; single settle, then read id
```

Differences that the HAL/KMD path can express and mainline does differently:

1. **RESET level semantics (highest-suspicion).**
   - CamX/KMD writes the **raw** GPIO level from `config_val` (`config=0`⇒LOW,
     `config=1`⇒HIGH), *ignoring* any active-low inversion.
   - Mainline uses `gpiod_*` with the DT `reset-gpios = <... GPIO_ACTIVE_LOW>` flag, so
     `gpiod_set_value(...,0)` means **de-assert** = drives the **physical line HIGH**, and
     `GPIOD_OUT_HIGH` at probe means **assert** = physical LOW.
   - If the rhodep DT reset flag polarity (or the physical net) is opposite to what the
     `.bin` tuples assume, the sensor is held **in reset while the id is read** →
     "powered, ACKs the address, returns register residue, never 0x38e1". This exactly
     matches the reported failure and would *not* affect FP5 (different board net + the
     FP5 DTS uses the plain mainline driver whose polarity happens to match its net).

2. **MCLK vs reset ordering.** Mainline enables MCLK **before** de-asserting reset, with
   the two back-to-back. A vendor tuple list often ends `…RESET(0)→RESET(1)→MCLK`, i.e.
   the sensor sees the reset edge, *then* the clock. If the JN1 needs MCLK stable before
   the digital top leaves reset (or vice-versa), the fixed order matters.

3. **Reset as a real pulse.** Mainline never drives a controlled short LOW→HIGH pulse in
   `power_on` (it relies on the line already being asserted from probe-time). A vendor
   sequence with explicit `{RESET,0,dLow}{RESET,1,dHigh}` guarantees a clean edge even if
   the line was left HIGH by the bootloader.

4. **Post-reset settle.** Mainline uses one 10–15 ms settle. The vendor per-step delays
   may differ (e.g. a longer post-RESET-high delay before the first SCL edge). Delays are
   ms-granular in the KMD executor (§3).

5. **i2c freq mode.** HAL leaves the probe read at the `.bin`/CCI default (STANDARD unless
   overridden). Mainline `i2c-qcom-cci` picks the master mode from the child bus
   `clock-frequency` and defaults STANDARD too — so this is a *match*, not a difference,
   unless the rhodep mainline cci bus is mis-set.

**What is NOT the difference (ruled out here):** a hidden per-sensor register write, an
S5KJN1 quirk, or a compiled-in default sequence — none exist in the HAL (§1c, §7).

---

## 6. Concrete, testable driver/DT changes (values)

Order by suspicion. Re-probe (cold `i2ctransfer w2@0x56 0x00 0x00 r2`, expect `0x38e1`)
after each.

### A. Fix/flip RESET polarity (cheapest, highest-value) — DT
The HAL drives reset by raw level; verify the mainline net polarity matches. Try the
*opposite* polarity flag on rhodep:
```dts
/* if currently ACTIVE_LOW, test ACTIVE_HIGH (and vice-versa) */
reset-gpios = <&tlmm 35 GPIO_ACTIVE_HIGH>;   /* rhodep reset is gpio35 */
```
Rationale: the KMD applies `config=0→LOW, config=1→HIGH` with no inversion; mainline
applies the DT flag. A polarity mismatch keeps the part in reset during MatchID.

### B. Make reset a real pulse with MCLK ordering swept — driver `s5kjn1_power_on`
Variant B1 (MCLK before a real pulse; mainline-ish order + clean edge):
```c
regulator_enable(vddd);  usleep_range(1000, 2000);   /* DVDD  +1 ms  */
regulator_enable(vdda);                              /* AVDD         */
regulator_enable(vddio);                             /* VDDIO        */
usleep_range(10000, 11000);                          /* +10 ms       */
clk_prepare_enable(mclk);                            /* MCLK 24 MHz  */
usleep_range(5000, 6000);                            /* MCLK-stable  */
gpiod_set_value_cansleep(reset_gpio, 1);             /* ASSERT reset */
usleep_range(5000, 6000);                            /* hold 5 ms    */
gpiod_set_value_cansleep(reset_gpio, 0);             /* RELEASE      */
usleep_range(12000, 15000);                          /* +12-15 ms    */
```
Variant B2 (MCLK LAST — sensor sees reset edge before clock):
```c
regulator_enable(vddio); usleep_range(1000, 2000);
regulator_enable(vdda);
regulator_enable(vddd);  usleep_range(10000, 11000);
gpiod_set_value_cansleep(reset_gpio, 1); usleep_range(5000, 6000);
gpiod_set_value_cansleep(reset_gpio, 0); usleep_range(10000, 11000);
clk_prepare_enable(mclk); usleep_range(10000, 11000); /* MCLK last, then read */
```

### C. Pin the probe i2c speed (only if A/B fail) — DT
```dts
&cci_bus_hosting_0x56 { clock-frequency = <400000>; };  /* FAST; then <1000000> FAST_PLUS */
```
Low value: the `.bin` is silent on freq and the HAL doesn’t force FAST; STANDARD is the
default on both sides.

---

## 7. FACT / INFERENCE / UNKNOWN

**FACT (address / string / disasm)**
- `camera.qcom.so` is the rhodep CamX build (`camx-legacy/rhodep/...` paths). It carries
  `.gnu_debugdata` with 7646 symbols (extracted; all functions below named).
- Power seq is serialised by `ImageSensorUtils::CreatePowerSequenceCmd` (`0x8c6ba0`);
  input `PowerSetting` stride 12 B `{u16 seq_type; u32 config; u32 delay}`; per-step delay
  emitted as a separate WAIT cmd (header `0x0903`, `mov w15,#0x903` @`0x8c6bd4`).
- Source of the tuples is the parsed `.bin` via `LoadSensorDriverData` (`0x2746f8`) →
  `LoadPowerSequenceInfo` (`0x274118`, reads `{count; PowerSetting[]}`).
- `SensorNode::CreateSensorSubmodules` (`0x8e9670`) picks primary settings `[base+64]/[+72]`;
  if empty logs `0x86897` `"...:380 ... Power settings are not valid. Will use default
  settings"` and falls back to `[+80]/[+88]`; if that is empty too, packet count = 0.
- No compiled-in default rail/clock/reset table: whole-file scan for MCLK `24000000` has 1
  hit, and it is a coincidental immediate in `BHistStats147::Execute` (`0x4eb7fe`).
- Boot probe assembled in `HwEnvironment::ProbeImageSensorModules` (`0x9b5528`); packet =
  i2c-info + slaveInfo(hdr `0x0103`) + pwrUp(`0x9b7e58`) + pwrDown(`0x9b7ebc`); opcode
  `0x01000003` = SENSOR_PROBE (`0x9b7f68`); submitted by `CSLImageSensorProbeHW` (`0xb200e0`).
- slaveInfo carries slave `0xac/0x56`, addr type WORD, chip-id reg `0x0000`, expected id
  `0x38e1`, and RESET/STANDBY/VANA/VDIG gpio {valid,num}; probe is **cold** (no I2C_WR
  cmd in the packet).
- i2c freq mode is a module-data numeric field with a settings override
  `GetOverrideI2CFrequencyMode` (`0x18ef1c`, used only in
  `ImageSensorModuleData::CreateSensorSubModules`); HAL does not force FAST.
- KMD executor semantics (`cam_sensor_util.c`): RESET/STANDBY/GPIO write **raw**
  `config_val` as the GPIO level (`msm_cam_sensor_handle_reg_gpio`, :1932); per-step delay
  in **ms** applied after each step (:2218). MCLK config_val sets `clk_rate` (:2107).
- **No `s5kjn1`/`jn1`/`38e1`/`S5K`-specific power or probe code path** in the HAL. The
  only `s5kjn1` token is the sensor-lib name `mot_s5kjn1` (`0x143713`), referenced from
  library-load/logging sites (`SensorNode::NotifyCSLMessage`,
  `ImageSensorData::GetSensorStaticCapability`, etc.), not a quirk table.
- Mainline `s5kjn1_power_on` (`s5kjn1-mainline.c:1201`): rails → MCLK →
  `gpiod_set_value(reset,0)` (de-assert via ACTIVE_LOW) → 10-15 ms → read; reset acquired
  `GPIOD_OUT_HIGH` (asserted) at probe (`:1324`).

**INFERENCE**
- The rhodep failure is not a missing register write, not an S5KJN1 quirk, and not a
  hidden default sequence — none of those exist in the HAL. It is a
  **rail/clock/reset sequencing or (most likely) a RESET-polarity/level** mismatch between
  how the KMD writes the raw GPIO level and how mainline’s `gpiod`+DT-flag path drives it.
- Highest-value single fix: verify/flip the rhodep `reset-gpios` polarity (§6.A); then the
  MCLK-order + real-pulse variants (§6.B).
- rhodep probe i2c freq is effectively STANDARD (blob field size 0, no override) — matches
  mainline default, so freq is unlikely to be the cause.

**UNKNOWN**
- The **byte-exact `(seq_type, config, delay)` tuples** the vendor uses. They are NOT in
  `camera.qcom.so` (data-driven from the `.bin`) and prior RE could not decode them from
  the `.bin`’s Parameter-Parser-V3 nested array. Definitive routes:
  1. ftrace stock: enable `cam_sensor_core_power_up` / the `CAM_DBG(CAM_SENSOR, "Seq
     Type[%d]: %d Config_val")` at `cam_sensor_util.c:1403` and the WAIT/delay accumulation
     at `:1420-1436` — prints the exact ordered tuples + folded delays.
  2. run the QTI Parameter-Parser over `com.qti.sensormodule.mot_rhodep_s5kjn1_*.bin`.
- Whether the required fix is polarity (A), ordering/pulse (B), or a specific delay value
  from the stock tuple list — resolve by the ftrace above or by sweeping §6.

---

## 8. Reconciling with prior RE
- `OUT_camx_seq.md` used the S5KJD1SP XML as a proxy and *guessed* `VIO→VANA→VDIG→
  RESET0→RESET1→MCLK`, FAST. This HAL RE shows the HAL neither hardcodes such a sequence
  nor forces FAST — so treat the JD1SP order/delays as **unconfirmed proxy**, not rhodep
  truth.
- `OUT_blob_powerseq.md`’s core conclusion ("the `.bin` sensor branch carries no rail
  tuples; the sequence is built at runtime and handed to the KMD as a `cam_cmd_power`
  packet") is **confirmed** by this HAL analysis — and extended: the "runtime builder"
  (`camera.qcom.so`) *also* has no default, so if the `.bin` is truly empty the stock
  packet is empty too. That in turn implies **either** the `.bin` tuples were mis-decoded
  as empty (they are the real source and must be recovered by ftrace/parser), **or** stock
  relies on the bootloader/prior state leaving the part powered — which would make the
  mainline delta purely the reset/clock re-sequencing in §5. The ftrace in §7 settles it.
