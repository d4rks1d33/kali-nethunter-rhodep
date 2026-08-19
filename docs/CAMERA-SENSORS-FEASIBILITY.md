# Camera and sensors: what the vendor tree actually says

Written after reading the vendor sources
(`Motorola-SM6375-Devs/android_kernel_motorola_sm6375` branch `fifteen`, and
`android_device_motorola_rhodep`). Nothing here has been built or tested — this
is a feasibility study, and its point is that **the root README's verdict on
the camera is wrong**.

## First, what vendor blobs can and cannot do

This port has had real success pulling blobs off the stock ROM: the WCN3990
`board-2.bin`, `ipa_fws.mdt`, the AW88261 tuning ACF. Those all work because
they are **firmware consumed by a mainline driver** — the driver is in Linux,
the data is proprietary.

The camera and sensor blobs are a different species. `camera.qcom.so`,
`libcamx*.so`, `com.qti.sensor.mot_s5kjn1.so`, `sensors.moto.so` are **Android
HALs**. They are userspace libraries that talk to the downstream kernel's
`qcom,cam-*` and SSC interfaces, neither of which exists in mainline. They
cannot be loaded, wrapped or shimmed into a mainline system in any practical
way. What the vendor tree gives us for these is not blobs — it is **the wiring
diagram**: which sensor, on which bus, behind which GPIOs and regulators.

## Camera: much more feasible than "effectively infeasible"

The root README calls the camera "CAMSS, effectively infeasible". That is not
what the sources show.

### The ISP pipeline is the one mainline already supports

Comparing `blair-camera.dtsi` (SM6375, this SoC) against `bengal-camera.dtsi`
(qcm2290 — which mainline **does** support, `qcom,qcm2290-camss`):

| block | qcm2290, in mainline | blair / SM6375 |
| --- | --- | --- |
| CSID | `qcom,csid530` | **`qcom,csid530`** — identical |
| front end | `qcom,tfe530` | **`qcom,tfe530`** — identical |
| test pattern gen | `qcom,tpg101` | **`qcom,tpg101`** — identical |
| CSIPHY | `qcom,csiphy-v2.0.1` | `qcom,csiphy-v1.2.1` — differs |
| PPI | absent | `qcom,ppi100` — extra |

Mainline drives qcm2290 with `csid_ops_340` + `vfe_ops_340` +
`csiphy_ops_3ph_1_0`. Since CSID and TFE are the *same hardware revision*, the
first two should apply unchanged. `csiphy_ops_3ph_1_0` is the generic 3-phase
CSIPHY implementation shared with sdm845, so a v1.2.1 PHY is very likely within
its range — that is the main unknown to verify.

### The clocks are already there

`gcc-sm6375` in mainline defines **52 `gcc_camss_*` clocks**, confirmed on the
running device via `clk_summary`: `camss_axi`, `camss_cci_0/1`,
`camss_cphy_0..3`, `camss_csi0..3phytimer`, `camss_tfe_*`, `camss_top_ahb`,
`camss_rt/nrt_axi`. Nobody has to add a clock driver.

### The main sensor has a mainline driver

From `proprietary-files.txt`, the four cameras on rhodep are:

| position | sensor | mainline driver |
| --- | --- | --- |
| **rear main (50 MP)** | Samsung **S5KJN1** | **`drivers/media/i2c/s5kjn1.c` — EXISTS** |
| wide | OmniVision OV16A1Q | no |
| front | Samsung S5K4H7 | no |
| macro | GalaxyCore GC02M1 | no |

So the interesting one — the main camera — is the one that already has a
driver. That single fact is what moves this from "infeasible" to "a project
with a defined path".

From `blair-camera-sensor-mot-rhodep-overlay.dtsi`, the main sensor's wiring is
fully specified: `cci-master = <1>`, MCLK1 on `tlmm 30`, reset on `tlmm 35`,
`csiphy-sd-index = <0>`, 24 MHz clock, and supplies `cam_vio`/`cam_vana`/
`cam_vdig` at 1.8 V / 2.8 V / 1.05 V.

### The one genuinely missing piece: the camera PMIC

Those supplies do not come from the main PMIC. `rhodep-wl2868c.dtsi` declares:

	compatible = "semi,wl2868c";
	reg = <0x2F>;              /* i2c */
	ldo1..ldo7

**`wl2868c` has no mainline driver.** It is, however, a plain I2C multi-LDO
regulator — the kind of device that is a few hundred lines against
`regmap`/`regulator` (compare `fan53880.c`, already in mainline). It is real
work but it is bounded, well-understood work, not research.

### Honest estimate of the camera path

1. Write the `semi,wl2868c` regulator driver. Small, self-contained, upstreamable.
2. Add `qcom,sm6375-camss`: define `csiphy_res_6375` / `csid_res_6375` /
   `vfe_res_6375` in `camss.c` using qcm2290's entries as the template and the
   register/clock/interrupt names from `blair-camera.dtsi`. Medium.
3. Add the `camss` node to `sm6375.dtsi`, plus the CCI nodes.
4. Add the sensor node for the S5KJN1 on CCI master 1 with the GPIOs and
   supplies above.
5. Expect the CSIPHY v1.2.1 vs v2.0.1 difference to be where time goes.

None of that is guaranteed to produce a working camera, and "the pipeline
brings up" is a long way from "usable photos" — but it is a normal porting job
with each step already done for a sibling SoC. The README's "effectively
infeasible" should be read as "nobody has started", not as a technical verdict.

## Sensors: still hard, and for a specific reason

The models are identifiable — `lsm6dso` (ST accel + gyro) and `stk3a5x`
(Sensortek proximity + light) — and both families have mainline drivers or close
relatives (`st_lsm6dsx`, `stk3310`).

That is not the problem. The problem is where they are attached:

	$ grep -rn 'lsm6dso|stk3|mmc56' blair.dtsi blair-moto-rhodep-base.dts \
	       blair-rhodep-common-overlay.dtsi
	(nothing)

**They do not appear in the application processor's device tree at all**,
because they hang off the SSC (Snapdragon Sensor Core) inside the ADSP, on a bus
Linux does not own. Having a driver for the chip is useless if the AP cannot
reach the chip.

The only route is to talk to the sensor core over QMI. That service *is*
reachable — `qrtr-lookup` on the device shows it published by the ADSP:

	400  1  0  5  14  Snapdragon Sensor Core service

but what runs on top of it is Qualcomm's SSC protocol, which is Protocol Buffers
over QMI with vendor-defined message sets, and the thing that speaks it on
Android is `sensors.moto.so`. This is a reverse-engineering project, not a
driver port. Rotation, proximity-on-call and auto-brightness all sit behind it.

So the README is right about sensors and wrong about the camera, which is worth
stating plainly because the two are usually mentioned in the same breath.

## What is worth doing first, if anyone picks this up

The camera PMIC driver (`wl2868c`), because it is bounded, useful on its own,
upstreamable, and it is a hard dependency for anything else on the camera path.
Nothing about it needs the SoC reset to be solved first.

---

# First step taken: the camera PMIC is alive and identified

**Status: verified on the device.** The bus is up, the chip answers, and it is
not the chip the vendor device tree says it is.

## Enabling the bus

`qupv3_se7_i2c` in the vendor tree is `i2c@4c84000`, which mainline already
describes as `i2c7` but leaves `status = "disabled"` and without a pinctrl
state. The pins come straight from the vendor pinctrl:

	blair-pinctrl.dtsi:  qupv3_se7_i2c_active: pins = "gpio27","gpio28";
	                     function = "qup11_f1";

and mainline's `pinctrl-sm6375.c` already defines that exact function for those
exact pins:

	[27] = PINGROUP(27, qup11_f1, ...),
	[28] = PINGROUP(28, qup11_f1, ...),

So the change is small — a pinctrl state plus `status = "okay"`. It was tested
by editing the DTB inside the boot image rather than rebuilding the kernel
(`kali-boot-v101-cam-i2c.img`); the decompiled diff against v100 is exactly
those two things and nothing else.

After flashing, the bus appears. Note the renumbering — enabling a bus with a
lower address shifts the existing ones:

	i2c-0 -> 4c84000.i2c   (new: camera PMIC)
	i2c-1 -> 4c88000.i2c   (was i2c-0: charger, fuel gauge)
	i2c-2 -> 4c90000.i2c   (was i2c-1: the two AW88261 amplifiers)

## The chip answers, but only with GPIO33 driven high

`i2cdetect -y -r 0` on a freshly booted phone finds **nothing**. The PMIC has an
enable line — `semi,cs-gpios = <&tlmm 33 1>` in the vendor DT — and it is low.
Driving it high makes a device appear:

	sudo gpioset -t0 -c gpiochip3 33=1 &     # gpiochip3 is 500000.pinctrl (TLMM)
	sudo i2cdetect -y -r 0
	30: -- -- -- -- -- 35 -- -- ...

That the device appears *only* with GPIO33 high is what confirms it is the
camera PMIC and not some other chip sharing the bus.

## It is a FAN53870, not a WL2868C

The vendor DT declares `semi,wl2868c` at `0x2F`. The chip is at **0x35**. The
vendor's own driver explains why — it probes 0x2F, and on an unexpected chip id
retries at 0x35:

	/* wl2868c-regulator.c */
	if ((ldo_chipid != 0x33) && (ldo_chipid != 0x01) && (ldo_chipid != 0x00)) {
		client->addr = fan53870_ET5907_ADDR;   /* 0x35 */
		...
	}
	if      (0x33 == ldo_chipid) "wl2868c"
	else if (0x01 == ldo_chipid) "fan53870"
	else if (0x00 == ldo_chipid) "et5907"

Reading the chip on this device:

	reg 0x00 = 0x01      reg 0x04 = 0x5d      reg 0x08 = 0xa2
	reg 0x01 = 0x01  <-- CHIP_REV             reg 0x09 = 0x30
	reg 0x02 = 0x7f      reg 0x05 = 0x80      reg 0x0a = 0x00
	reg 0x03 = 0x24      reg 0x06 = 0x80
	                     reg 0x07 = 0x80

`CHIP_REV` (0x01) reads **0x01**, so this is a **FAN53870**. Anyone writing the
driver from the vendor DT alone would have targeted the wrong chip at the wrong
address.

## What the driver needs to implement

From the vendor driver, for the fan53870 variant:

| item | value |
| --- | --- |
| i2c address | `0x35` |
| chip id register | `0x01`, reads `0x01` |
| enable register | `0x03`, one bit per LDO (`BIT(id)`) |
| LDO1..LDO7 vsel | `0x04`..`0x0a` (base `0x03` + `LDO_OFFSET` 1) |
| LDO1, LDO2 | min 800000 uV, step 8000 uV, 256 steps |
| LDO3..LDO7 | min 1372000 uV, step 8000 uV, 256 steps |
| enable gpio | TLMM 33, must be driven high before any transfer |

(The vendor source writes LDO1/LDO2 `min_uV` as `8000`, which is a typo for
`800000` — every other Qualcomm camera PMIC of this class starts at 0.8 V and a
6 mV/8 mV step over 256 codes only makes sense from there. Worth confirming
against a datasheet before trusting it.)

Mainline has no `fan53870`, but it does have **`fan53880.c`** — same vendor,
same class of multi-LDO camera PMIC, already using `regulator_desc` +
`regmap`. It is the natural template.

The one part with no precedent in `fan53880.c` is the enable GPIO: this chip
needs its `cs` line asserted before it will answer at all, so the driver has to
take that GPIO in `probe()` before the first regmap access.

## Where this leaves the camera

The bottom of the stack is now proven rather than assumed: correct bus, correct
pins, chip present, chip identified, register map known, enable line
understood. That is the piece that had to work before anything else on the
camera path was worth attempting.

---

# WORKING: the camera PMIC driver

**The FAN53870 driver is written, upstreamable, and running on the device.**
This is the first piece of camera hardware to work on this port.

	# dmesg
	fan53870 0-0035: FAN53870 camera PMIC, 7 LDOs

	# /sys/class/regulator
	cam_vdig     state=disabled   microvolts=1056000
	LDO2         state=disabled   microvolts=1824000
	LDO3         state=enabled    microvolts=2396000
	cam_vana     state=disabled   microvolts=2804000
	LDO5         state=disabled   microvolts=2668000
	LDO6         state=enabled    microvolts=1756000
	cam_vio      state=disabled   microvolts=1804000

## How it was verified, beyond "it probed"

A probe message only proves the chip answered. Two things were checked against
the hardware itself.

**The voltage maths.** The three camera rails are constrained to fixed voltages
in the device tree, so the core had to program them. Reading the chip back:

	reg 0x04 (LDO1) = 0x20   calculated (1056000-800000)/8000  = 32 = 0x20  OK
	reg 0x07 (LDO4) = 0xb3   calculated (2804000-1372000)/8000 = 179 = 0xb3 OK
	reg 0x0a (LDO7) = 0x36   calculated (1804000-1372000)/8000 = 54 = 0x36  OK

Those registers read 0x5d, 0x80 and 0x00 before the driver existed, so the
driver did not merely agree with the hardware, it changed it to the requested
values. `min_uV`, `uV_step` and the vsel register base are therefore all right.

**The enable mask.** The enable register (0x03) reads `0x24` = `0b0010_0100`,
i.e. bits 2 and 5 set, and the driver independently reports exactly LDO3 and
LDO6 as `enabled`. That confirms `enable_reg` and `enable_mask = BIT(n-1)`.

## The one thing that is not like fan53880

The part does not ACK its own i2c address while its enable pin is low, so the
GPIO has to be asserted before the first regmap access or probe fails with
-ENXIO and the chip looks absent:

	enable_gpio = devm_gpiod_get_optional(&i2c->dev, "enable", GPIOD_OUT_HIGH);
	if (enable_gpio)
		usleep_range(1000, 2000);

This is why `i2cdetect` finds nothing on a freshly booted phone, and it is the
detail that would have cost the most time to work out from scratch.

## Files

	kernel/patches/0051-regulator-add-fan53870-camera-pmic.patch
	kernel/patches/0052-arm64-dts-qcom-rhodep-add-camera-pmic.patch
	CONFIG_REGULATOR_FAN53870=m
	kali-boot-v102-cam-pmic.img

Both patches are in the aport's `source=` and the driver is a module. Nothing
else regressed: audio, wifi, battery, the three remoteprocs and the 47 apt
holds are all as before.

## What this does and does not mean

It means the camera rails can now be turned on and set from Linux, by a driver
that is clean enough to send upstream, and that the bottom of the camera stack
is done.

It does not mean there is a camera. Still ahead: a `qcom,sm6375-camss` entry,
the CSIPHY/CSID/TFE nodes, and the S5KJN1 sensor node — after which the first
meaningful test is a raw capture, not a photo.

---

# CAMSS is up on SM6375. The sensor is not.

**Working: the ISP pipeline enumerates.** `qcom,sm6375-camss` brings up four
CSIPHYs, three CSIDs and three TFEs, producing twelve video nodes and a media
device:

	/dev/media0
	video0..video3    msm_vfe0_video0..3
	video4..video7    msm_vfe1_video0..3
	video8..video11   msm_vfe2_video0..3

	# media-ctl -d /dev/media0 -p
	entity 1:  msm_csiphy0 (2 pads, 3 links)
	entity 4:  msm_csiphy1
	entity 7:  msm_csiphy2
	entity 10: msm_csiphy3
	entity 13: msm_csid0 (5 pads, 16 links)
	...

**Working: both CCI masters.** `i2c-qcom-cci` binds and creates two I2C buses,
with devices answering on both.

**Not working: the S5KJN1 does not respond.**

## Two real bugs found and fixed on the way

**1. SMMU stream id — caused a bootloop with no logs.** The first attempt copied
qcm2290's `iommus = <0x400 0x800 0x820 0x840>`. On SM6375 **0x820 belongs to the
display subsystem**, and duplicating it wedges the boot before pstore is even
registered, so ramoops stays empty and there is nothing to read. The vendor's own
camera context bank (`blair-camera.dtsi`, `msm_cam_smmu_tfe`) gives a single id:

	iommus = <&apps_smmu 0x800 0x0>;

**2. CCI pinctrl — each master has its own pin pair.** Declaring only
gpio39/40 leaves master 1 electrically dead:

	cci0_active: gpio39, gpio40   -> master 0
	cci1_active: gpio41, gpio42   -> master 1

The symptom is precise and worth recognising: `i2c-qcom-cci: master 1 queue 0
timeout`, and errors of **-110 (ETIMEDOUT)**. After adding all four pins the
error changed to **-6 (ENXIO)** — the bus now drives and something NAKs, which
is a completely different failure. Devices appeared on master 1 that had been
invisible before.

## What was verified on the sensor, and rules the AP out

Using a temporary driver patch that holds the bus idle for 40 s after power-on,
everything the application processor controls was measured in its working state:

| | |
| --- | --- |
| camera rails | `cam_vdig`/`cam_vana`/`cam_vio` all `enabled`; PMIC enable register `0x6d`, vsel `0x20`/`0xb3`/`0x36` |
| MCLK1 | `clk_enable_count = 1`, `clk_rate = 24000000` — exactly what the driver demands |
| CAMSS GDSC | `camss_top_gdsc: on` |
| reset line | claimed by the driver: `line 35: output active-low consumer="reset"` |
| pin muxing | `pin 30 function cam_mclk device 1-0010`, `pin 35 function gpio`, `pins 39-42 function cci_i2c` |

With all of that true, a 16-bit read of register 0x0000 was swept across every
address on both masters. Nothing answers with the S5KJN1's chip id (0x38e1):

	master 0:  0x51 -> 0x3130
	master 1:  0x50 -> 0x3130   0x52 -> 0x73f0   0x56 -> 0x0000   0x72 -> 0x0000

0x50/0x51 in the 0x50-0x57 range are EEPROMs, consistent with the vendor's
`eeprom_main`/`eeprom_front` and with the Fairphone FP5's `gt24p128f` at 0x51.
**The sensor itself is silent.**

## The wiring is not a guess

Cross-checked three ways and they agree: the vendor overlay
(`cci-master = <1>`, `csiphy-sd-index = <0>`, MCLK1 on gpio30, reset on gpio35),
the FP5's mainline node for the same sensor (`reg = <0x10>`, same three
supplies, `GPIO_ACTIVE_LOW` reset, 4 lanes, 700 MHz), and the vendor's
per-master layout:

	master 1: sensor_main (S5KJN1) + its eeprom/actuator/ois
	master 0: sensor_front, sensor_macro, sensor_wide + their eeproms

## Ruled out since: reset polarity, and a false lead at 0x56

**Reset polarity is correct**, settled from the vendor pinctrl without needing
a flash. `cam_sensor_main_reset_suspend` carries `output-low` and
`bias-pull-down`, so low is the powered-down state and high is running — i.e.
active-low, which is what the device tree says.

**0x56 is not the sensor.** Scanning CCI0 master 1 with and without MCLK
running showed a device appearing only with the clock up, which looked exactly
like an image sensor and nothing else on that master. Pointing the driver at it
even got past the bus layer:

	s5kjn1 4-0056: chip id mismatch: 38e1!=0

It is coupling noise on an empty address. Reading the same register repeatedly
is what settles it — the real devices answer identically every time, 0x56 does
not:

	0x50 -> 0x3032  0x3032  0x3032    stable, real
	0x52 -> 0x4520  0x4520  0x4520    stable, real
	0x72 -> 0x0000  0x0000  0x0000    stable, real
	0x56 -> 0xae99  0xbe9b  0xbee9    different every read, NOISE

A dump of a dozen registers at 0x56 also aliases (0x0002 and 0x000a return the
same value, so do 0x0016/0x001a and 0x0100/0x0104), which is another sign there
is no device decoding the address.

**Lesson worth keeping: an ACK is not a device.** On this bus, with MCLK
running, empty addresses can ACK. Always read one register several times before
believing a scan.

Note also that the vendor declares four devices on CCI0 master 1 (sensor,
eeprom, actuator, OIS) and only three real ones answer, so the actuator or OIS
is also missing — they may need a supply of their own.

## What is left to try

1. ~~Reset polarity~~ — ruled out above.
2. **The sensor's I2C address may be strapped.** Qualcomm's stack reads it from
   `com.qti.sensormodule.mot_rhodep_s5kjn1_{qtech,sunny}.bin`, and rhodep ships
   two module vendors. Those blobs are on the stock ROM; extracting the address
   from one would settle it. Note the sweep found nothing at *any* address, so
   if it is strapped it is also not answering.
3. **A supply that is not in the vendor's regulator-names list**, e.g. something
   enabled by the actuator or OIS node rather than the sensor node.

Everything above the sensor is done: the ISP enumerates, the buses work, the
power rails are correct and controllable. What remains is a single device that
will not ACK.
