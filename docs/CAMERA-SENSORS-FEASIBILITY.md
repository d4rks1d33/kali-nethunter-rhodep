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

> Superseded in part — see "Sensors: which bus they are really on" at the end of
> this file. The buses are now known (the IMU is on I3C, which settles it: no AP
> device tree node can reach it), the QMI transport and message format have been
> worked out and verified against the hardware, and the client turned out not to
> be what blocks it.

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

## The power-on order matters, and it changes what the bus reports

The mainline driver enables the rails in the order vddd, vdda, vddio. The
vendor lists them the other way round - `regulator-names = "cam_vio",
"cam_vana", "cam_vdig"` - and VDDIO is what feeds a sensor's I2C buffers, so
bringing the core up first can leave the part undefined.

Swapping the driver to the vendor's order visibly changes the bus. The address
that had been returning a different value on every read became stable:

	driver order:  0x56 -> 0000 8b05   noise
	vendor order:  0x56 -> 8b05 8b05   STABLE
	                       8 consecutive reads all 8b01

That is worth knowing, and it may well matter once the sensor is found. But it
did not find the sensor: dumping consecutive registers at 0x56 shows the reads
are a byte-shifted stream, not addressable registers -

	0x0000 = 8b05
	0x0001 = 05be     <- "05" is the tail of the previous read
	0x0002 = beaf     <- "be" likewise
	0x0003 = af8b

so the device is not decoding the register address at all. 0x56 is a bus
artefact: deterministic under the vendor order, but still an artefact.

**Method note, twice learned the hard way:** on this bus neither an ACK nor a
stable value proves a device. Repeated reads catch the noisy case; only a
plausible register map catches this one.

## Definitive state of the sensor

A sweep run from inside the driver, over the native CCI adapter, with rails,
MCLK, GDSC, reset and pin mux all verified in their working state, reading every
address twice:

	0x50 -> 3130 3130 STABLE     EEPROM
	0x52 -> 73f0 73f0 STABLE     EEPROM-like
	0x56 -> byte-shifted stream  ARTEFACT
	0x72 -> 0000 0000 STABLE     answers, all zeroes

The S5KJN1 never answers with 0x38e1 at any address on either master, under
either power-on order. Everything the application processor controls has been
measured correct. What is left needs information the device tree does not carry:
the sensor's strapped address and power sequence live in Qualcomm's
`com.qti.sensormodule.mot_rhodep_s5kjn1_{qtech,sunny}.bin`, on the stock ROM.

## What is left to try

1. ~~Reset polarity~~ — ruled out from the vendor pinctrl.
2. ~~A missing rail~~ — all seven PMIC LDOs plus `cam_ois_ldo` (TLMM 86)
   enabled at once changes nothing.
3. **Extract the sensormodule blob from the stock ROM** and read the address and
   power sequence out of it. This is the one avenue that carries new
   information; everything else has been exhausted.
4. Try the vendor power-on order *and* whatever that blob says, together.
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

---

# Reading the vendor blob: what it settled

**Vendor blobs are usable here, but as data, not as code.** The camera HALs
(`camera.qcom.so`, `libcamx*.so`) cannot run on mainline. But Qualcomm's
per-module sensor configuration is a plain data file, and it carries exactly the
facts the device tree needs.

## Getting at it

Same approach as `scripts/extract-aw88261-acf.sh`: `vendor` is a logical
partition inside `super`, so it has to be mapped before mounting. `dmsetup`
failed on this kernel (`dm_mod` will not load, "Exec format error"), so use a
loop device with an offset instead — it needs no device-mapper:

	OFF=$((6789120*512)); SZ=$((1235440*512))
	losetup -r -o $OFF --sizelimit $SZ /dev/loop7 /dev/disk/by-partlabel/super
	mount -o ro /dev/loop7 /mnt/vendor_ro
	ls /mnt/vendor_ro/lib64/camera/com.qti.sensormodule.*

rhodep ships two for the main camera, one per module vendor:
`..._s5kjn1_qtech.bin` and `..._s5kjn1_sunny.bin`, ~269 KB each.

## The structure worth knowing

Search for the expected chip id as a little-endian u32 and the surrounding
words are the sensor's I2C description:

	0x348b0:  ac 00 00 00    i2c address, 8-bit form -> 0x56 in 7-bit
	          02 00 00 00    register address width, bytes
	          02 00 00 00    data width, bytes
	          00 00 00 00    chip id register = 0x0000
	0x348c0:  e1 38 00 00    expected chip id = 0x38e1
	          ff ff ff ff    mask

A second entry of the same shape identifies the module vendor through the
EEPROM:

	addr 0xa0 (= 0x50 7-bit), register 0x0d, expected 0x5154 = "QT" (Qtech)

**Read that register on the device to pick the right blob.** This board answers
`0x5355` = "SU", so it is a **Sunny** module and the qtech blob is the wrong
one. Both agree on the sensor address, but the field is there for a reason.

## What it settled, and what it did not

Settled: **the sensor is at 0x56**, with 2-byte registers and 2-byte data, chip
id 0x38e1 at register 0x0000. The Fairphone FP5 uses 0x10 for the same part, so
this is strapped differently and no amount of reading mainline would have found
it.

Also settled, by a clean control: the bus distinguishes empty addresses
properly, and 0x56 behaves like a sensor and not like noise —

	0x11, 0x33 (empty)   -> ENXIO
	0x56 without MCLK    -> ENXIO
	0x56 with MCLK       -> ACKs (the driver reports a chip id mismatch, not ENXIO)
	0x50, 0x52           -> always answer

Needing MCLK to answer on I2C is exactly what an image sensor does, and what
tells it apart from the EEPROMs on the same master.

Not settled: **the chip id register reads zero.** The part acknowledges its
address with the clock running, but returns 0x0000 where 0x38e1 is expected.
Tried and did not change it: the vendor power-on order (which did make reads
stable instead of noisy - kept as patch 0056), dropping the CCI to 100 kHz, and
a 150 ms post-reset delay instead of 10 ms.

## Where to pick this up

The blob still holds two things that have not been decoded: the
`powerUpSequence`/`powerDownSequence` tables and `sensorI2CFrequencyMode`. The
field names are at 0x32b10, 0x32b48 and 0x325d0; the values live in the data
region around 0x34800. Decoding the power sequence is the obvious next move,
since it would give the exact rail order, delays and any register writes the
part expects before it will identify itself.

## Result: something real is at 0x56, and it will not identify itself

Two more things tried, both negative, both narrowing it:

**A soft reset does not help.** Writing 1 to 0x0103, the standard reset for
Samsung parts, changes what the id register returns but never to 0x38e1:

	before soft reset   0x0000
	after soft reset    0xe7a1
	after standby (0x0100 = 0)  0x8b01
	register 0x0002     0xbedb

Every write reports success. A real part would answer 0x38e1 reproducibly.

**The MCLK drive strength was not the cause.** The device tree had 4 mA where
the vendor pinctrl specifies 2 mA, and a 24 MHz clock driven harder than
specified is a plausible source of coupling onto the neighbouring CCI lines.
Lowered to 2 mA (which is the correct value regardless, and is kept): the
behaviour is unchanged, still an ACK and still zero.

That settles the question this test was built to answer. **The address is not
noise.** With MCLK running, the driver's own sweep sees only 0x50, 0x52, 0x56
and 0x72 and nothing at any other address, so the bus does discriminate; and
0x56 answers there while returning ENXIO with the clock off. Combined with the
blob naming 0xac as the sensor, the conclusion is that the sensor is present and
acknowledging, but is not in a state where it reports its identity.

**So the remaining work is the initialisation sequence, and it is in the blob.**
`powerUpSequence`, `powerDownSequence` and `sensorI2CFrequencyMode` are still
undecoded; the field names sit at 0x32b10, 0x32b48 and 0x325d0 and the values in
the data region around 0x34800, with metadata entries 56 bytes apart. Decoding
those gives the exact rail order, the delays, and any register writes the part
wants before it will answer - which is precisely the gap that remains.

## Five power-on sequences tried, none identifies the sensor

Run in a single module load so each hypothesis cost nothing extra, reading the
id register after each:

	[0] as-is (vddio,vdda,vddd + mclk + reset, 10ms)   id=0x0000
	[1] longer settle after reset (200 ms)             id=0x8b05
	[2] reset pulse: low 20ms, high, 200ms             id=0x0000
	[3] mclk stopped, rails settle, mclk again, reset  id=0x0000
	[4] double reset pulse                             id=0x0000

Never 0x38e1. Note the pattern: with no settle time the register reads zero,
with 200 ms it reads 0x8b0x. Something changes state, but not into a part that
reports its identity.

### The vendor's power sequence enum, for whoever decodes the blob

From `techpack/camera/drivers/cam_sensor_module/cam_sensor_utils/cam_sensor_cmn_header.h`
in the vendor kernel:

	SENSOR_MCLK = 0        SENSOR_VAF_PWDM = 5     SENSOR_STANDBY = 9
	SENSOR_VANA = 1        SENSOR_CUSTOM_REG1 = 6  SENSOR_CUSTOM_GPIO1 = 10
	SENSOR_VDIG = 2        SENSOR_CUSTOM_REG2 = 7  SENSOR_CUSTOM_GPIO2 = 11
	SENSOR_VIO = 3         SENSOR_RESET = 8        SENSOR_VANA1 = 12
	SENSOR_VAF = 4

Searching the blob for runs of (type, config, delay) triplets with a valid type
produces only false positives — the matches have nonsensical configs and delays
and are really index tables. **The power sequence is not a flat triplet array**;
the format needs proper work, not pattern matching.

### Everything ruled out on the sensor so far

	reset polarity        vendor pinctrl says active-low, which is what the DT has
	a missing rail        all 7 PMIC LDOs + cam_ois_ldo on: no change
	MCLK drive strength   4 mA vs the vendor's 2 mA: no change (2 mA kept, correct)
	CCI bus speed         400 kHz and 100 kHz: no change
	soft reset            write 1 to 0x0103: id changes value, never to 0x38e1
	standby exit          write 0 to 0x0100: same
	post-reset delay      10 ms, 150 ms, 200 ms: same
	power-on order        vendor order fixes read *stability*, not identification

### The honest position

Everything the application processor controls has been verified correct and
measured. The sensor is at the address its own configuration blob names, it
acknowledges that address when MCLK runs, and it returns ENXIO when the clock
is off — it is present and alive. It just will not report its identity, and the
remaining variable is an initialisation detail that lives in a proprietary blob
whose format has resisted a day of pattern matching.

Two routes carry real information, and neither is more guessing:

1. **Decode the blob properly.** Metadata entries are 56 bytes,
   `[u32 mask][u32 b][u32 c][u32 id][char name[40]]`, with `powerUpSequence` at
   0x32b10 carrying c=0x10 and `powerSetting` at 0x32cc0 carrying c=0x24. The
   values live in a separate data region; the link between the two is what has
   not been worked out.
2. **Capture the real sequence from Android.** The stock ROM is on the other
   slot. Booting it and tracing the camera bring-up — the kernel driver logs
   every power step at debug level — gives the exact order, delays and register
   writes, with no reverse engineering at all. This is the cheaper of the two.

---

# The sensormodule blob, decoded

Done properly rather than by pattern matching, because guessing had run out.
The format is documented here so nobody has to redo it.

## Container

	0x0000  "QTI Chromatix Header"
	0x001c  u32 total file size (0x41ba6 = 269222, matches)
	0x0028  "Parameter Parser V3.0.1 (2009151142)"
	0x0058  "com.qti.sensormodule.mot_rhodep_s5kjn1_sunny"
	0x00c0  start of the parameter table

## Parameter table

A flat array of **56-byte entries**, 3727 of them, each:

	+0x00  u32  flag      0, 2, or 0xffffffff
	+0x04  u32  offset    into the data area
	+0x08  u32  size      in bytes, 0 means "no value"
	+0x0c  u32  id        sequential, 1..3727
	+0x10  char name[40]

Two things make it readable. The offsets are **contiguous** — each field's
`offset + size` equals the next field's offset — which is how the layout can be
verified without guessing. And the data area is **not** at file offset 0:

	DATA_BASE = 0x3401c

derived by taking a field whose value was already known from the hardware
(`sensorSlaveAddress`, offset 0x894) and locating that value in the file
(0x348b0). Every other field then resolves correctly.

## What it actually contains

Reading `powerSetting` (id 13, offset 0x8a3, 72 bytes) at DATA_BASE gives the
identification block, which is what the camera stack probes with:

	0x38e1  0xffffffff  1  10     sensor chip id and mask
	0xa0    2  2  0x0d            EEPROM at 0xa0 (0x50 7-bit), register 0x0d
	0x5355                        expected "SU" = Sunny

The qtech blob carries 0x5154 ("QT") in the same place. Reading register 0x0d of
the EEPROM on the device returns 0x5355, so this board is Sunny — that is how
the right blob gets picked, and it also confirms the parse is correct.

This is where the sensor's I2C address comes from, and it is worth repeating
that it is **0xac (8-bit) = 0x56 (7-bit)**, not the 0x10 the Fairphone FP5 uses.

## What it does NOT contain: the power sequence

	powerUpSequence     16 bytes   (belongs to the actuator/flash branch)
	powerDownSequence   0 bytes
	powerSetting        108 bytes total across 4 entries, all identification
	                    or actuator

**There is no sensor power-up sequence in this file.** Searching for
(type, config, delay) triplets with valid `msm_camera_power_seq_type` values
returns only false positives — index tables whose configs and delays are
nonsense.

That is a real answer, not a failure: the blob was the leading candidate for
where the missing initialisation lived, and it can now be crossed off instead of
being suspected indefinitely. On this CamX version the sensor's power sequence
lives in the sensor driver library (`com.qti.sensor.mot_s5kjn1.so`), which is
AArch64 code rather than data — extracting it means disassembly, a different and
much larger job.

## Where that leaves the sensor

Confirmed from the vendor's own data: correct address, correct register widths,
correct expected id. Confirmed on the hardware: the part is at that address,
acknowledges when MCLK runs, and returns ENXIO when it does not. Still refuses
to report its identity, and the initialisation detail that would explain it is
not in the file that was supposed to hold it.

The remaining route that carries information is tracing the stock ROM: booting
Android on the other slot and watching the kernel's camera driver log each power
step gives the sequence directly, with no disassembly and no guessing.

---

# Sensors: which bus they are really on, and how far the SSC protocol got

**Status: the I2C route is ruled out by measurement, and the SSC transport
works.** The sensor core accepts our requests and answers; it just has no
sensors to offer. What blocks it is missing infrastructure on the AP side, not
the protocol.

This section supersedes the guesswork in "Sensors: still hard, and for a
specific reason" above. That section was right that the sensors are behind the
SSC, but it did not say which bus they sit on, and it treated the protocol as an
unbounded reverse-engineering project. Both are now pinned down.

## The vendor states the bus and the address outright

The configuration the SSC uses is plain JSON in the vendor partition, in
`/vendor/etc/sensors/config/`, one file per part. Mount it read-only the usual
way (`dmsetup` still fails, `dm_mod` will not load):

	losetup -r -o $((6789120*512)) --sizelimit $((1235440*512)) \
	        /dev/loop7 /dev/disk/by-partlabel/super
	mount -o ro /dev/loop7 /mnt/vendor_ro

Each file carries the bus, the address and the interrupt:

	sensor                    bus       instance  address  IRQ
	lsm6dso (accel + gyro)    I3C       1         0x6A     GPIO 95
	icm4x6xx (alternate IMU)  I3C       1         0x68     GPIO 95
	ak991x (magnetometer)     I2C       2         0x0E     -
	mmc56x3x (magnetometer)   I2C       2         0x30     -

`bus_type` 3 means I3C, and `max_bus_speed_khz` of 12500 agrees. `vddio_rail` is
`/pmic/client/sensor_vddio`. The IMU is the interesting one: it is on I3C, not
I2C, which no amount of device tree work on the AP's QUP controllers will reach.

## Why no device tree node can work

	mainline sm6375.dtsi     2 geniqup controllers, 0 i3c nodes
	vendor holi-qupv3.dtsi   6 SE I2C nodes (se0,2,6,7,8,10), 0 i3c nodes

Neither tree has an I3C controller, and the vendor does not declare the SSC's
buses at all — because the application processor does not own them. There is no
controller to hang an `st_lsm6dsx` or `stk3310` node off. Writing one is not a
partial solution, it is a non-solution.

The only reference to these parts anywhere in the vendor DTS is
`bindings/iio/imu/st_lsm6dsx.txt`, which is upstream documentation, not a node.

## The SSC transport works

Service 400 is published by the ADSP at node 5, port 14. A client written from
scratch in Python over `AF_QIPCRTR` gets a real answer:

	02 0100 2000 1900
	02 0400 00000000     result = 0 SUCCESS, error = 0
	10 0800 04000...     client_id = 4

So the wrapping is right: QMI message 0x20, one TLV of type 0x01, and — this one
costs an afternoon if you miss it — the payload inside that TLV is a
variable-length array, so it carries its own 16-bit element count before the
protobuf bytes. Without that count the service replies `error 19`
(`ARG_TOO_LONG`).

## The message format, taken from the blob rather than guessed

`libssc.so` is the client and `libsnsapi.so` the generated message code. It is
protobuf **lite** (161 references to `MessageLite`, zero `descriptor_table`), so
there are no embedded descriptors to dump — the field numbers have to come out
of the generated serialisers. `SerializeWithCachedSizes` gives them exactly:

	sns_client_request_msg   1 message   2 FIXED32   3 message  4 message
	suspend_config           1 enum      2 enum      3 fixed32
	sns_suid_req             1 string    2 bool      3 bool

`msg_id` being a `fixed32` rather than a varint is a genuine trap: encoding it as
a varint still produces a well-formed TLV, so QMI answers `SUCCESS` and hands
back a client id, and nothing tells you the sensor core read the wrong field.

The lookup sensor's SUID is `0xABABABABABABABAB` in both `suid_low` and
`suid_high`. Two values that look like SUIDs in a disassembly of
`suid_lookup::request_suid` — `0x02DA89938702F355` and `0x0FB862E2AF8723EE` —
are **CFI type ids** passed to `__cfi_slowpath`, not data. Trial and error would
have accepted them.

## What actually blocks it

Twenty-one variants were tried: five `client_proc_type` values, both
`delivery_type` values, `suspend_config` field 3 present and absent, three
`register_updates` settings, ten `data_type` strings, and both connected and
unconnected sockets with a 30 second wait. Every one was accepted, every one
allocated a fresh client id (they ran up to 37), and **not one produced an
indication**. A malformed message does not behave like that. The sensor core is
running and servicing QMI, and has no sensors registered.

Two things are missing on the AP side, both verifiable:

- **No `/dev/fastrpc`.** `CONFIG_QCOM_FASTRPC=m` and `fastrpc.ko` is installed,
  but mainline sm6375 declares no `fastrpc` node under either `glink-edge`, so
  the driver never probes. Checked in the live `/proc/device-tree` and in the
  dtsi.
- **The AP publishes no QMI service at all.** In `qrtr-lookup` every service
  belongs to node 0, 5 or 10. On Android the AP serves RFSA, which is how the
  ADSP reads files off the AP.

That last point matters because the configuration above — the JSON files and
`sns_reg_config` — lives on the AP, not in the ADSP firmware. If nothing serves
it, the ADSP cannot instantiate `lsm6dso` or the magnetometers, a lookup for
`accel` matches nothing, and with `register_updates=1` the request sits waiting
forever. Which is precisely what we see.

## Scripts

	scripts/ssc-probe.py   SUID lookup: QRTR + QMI + protobuf, correct field
	                       types. Prints the decoded reply.
	scripts/ssc-sweep.py   the variant sweep, kept so nobody repeats it.

## Where to pick this up

Add the `fastrpc` nodes to the sm6375 device tree under the ADSP and CDSP
`glink-edge`, load `fastrpc.ko`, and see whether the nodes appear and whether the
ADSP starts registering sensors. It is a bounded patch, it is checkable in one
boot, and `/dev/fastrpc` is a prerequisite for anything further on this path
regardless of how the RFSA question resolves.

---

# fastrpc, pd-mapper and the ADSP: three things fixed, sensors still absent

**Status: verified on the device.** `/dev/fastrpc` exists, pd-mapper serves
domains for the first time, and Linux now boots the ADSP, CDSP and modem from
firmware instead of inheriting whatever the bootloader left running. The sensor
core still returns no sensors.

These are worth having on their own — they are prerequisites for anything on
this path and they fix real gaps in the port — but none of them made sensors
appear, so the honest summary is three steps forward and the original question
still open.

## fastrpc (patch 0057)

SM6375 declared no fastrpc nodes, so the driver never probed despite
`CONFIG_QCOM_FASTRPC=m` and `fastrpc.ko` being installed. Adding them under both
`glink-edge` nodes gives:

	/dev/fastrpc-adsp
	/dev/fastrpc-cdsp
	/dev/fastrpc-cdsp-secure

with five context banks on the ADSP and seven on the CDSP registering into IOMMU
groups. Stream IDs are the vendor's, from `holi.dtsi`: `0x00a3..0x00a7` for the
ADSP, `0x1001..0x1006` and `0x1009` for the CDSP. They follow the same
base-plus-bank-number pattern as sm6115, which is a useful independent check,
and getting one wrong here is not a soft failure — a wrong SID copied from
qcm2290 for camss bootlooped the device with no log output at all.

## pd-mapper was running and serving nothing

It logged `Cannot open firmware path: /lib/firmware/postmarketos` on every boot
and the AP published no QMI service whatsoever. The fix is not simply creating
that directory. Tracing it shows what it actually does:

	read /sys/class/remoteproc/<n>/firmware   -> qcom/sm6375/motorola/rhodep/adsp.mbn
	opendir <firmware_class path>/updates/qcom/sm6375/motorola/rhodep   -> ENOENT
	opendir <firmware_class path>/qcom/sm6375/motorola/rhodep           -> ok

It derives the *directory of the remoteproc firmware* and looks for `.jsn` files
**inside it**, not in the firmware root. So the domain files belong in
`qcom/sm6375/motorola/rhodep/`, next to the firmware. Dropping them in the base
directory appears to work only while that subdirectory does not exist yet, which
is a confusing intermediate state worth naming: it publishes the locator, and
then stops the moment real firmware is installed.

The `.jsn` files from qrb4210 work unmodified. Their `qmi_instance_id` values
match what this device's remote processors publish as servreg instances:

	adspr/adsps/adspua  74    ADSP publishes servreg instance 74
	cdspr               76    CDSP publishes servreg instance 76
	modemr/modemuw     180    modem publishes servreg instance 180

pd-mapper only parses `*.jsn`, so sharing the directory with the firmware blobs
is fine. One failure mode to recognise, because it cost time here: `unable to
match a value` means a `.jsn` failed to parse, and in this case the files were
zero bytes — copied moments before a forced power-off, so the inodes were on
disk and the contents never were.

## Linux now boots the DSPs

`adsp.mbn` was failing with -2. The firmware is on the **modem** partition, not
on `dsp`: `/image/adsp.mdt` plus 34 `.bXX` segments, and the same for the CDSP.
`qcom_mdt_load` treats the file it is given as the MDT header and derives the
segment names, so copying `adsp.mdt` to `adsp.mbn` is enough. After that, on a
clean boot:

	remoteproc1: Booting fw image qcom/sm6375/motorola/rhodep/adsp.mbn
	remoteproc2: remote processor cdsp is now up
	remoteproc1: remote processor adsp is now up
	remoteproc0: remote processor modem is now up

Until now the ADSP only answered QMI because the bootloader had started it;
remoteproc's own load failed and Linux did not manage it.

## What appeared, and what still does not

With all of it in place on a clean boot the AP finally publishes services:

	14    Remote file system service      (RFSA - how the DSP reads AP files)
	52    Dynamic Heap Memory Sharing
	64    Service registry locator service (pd-mapper)
	4096  TFTP

RFSA is specifically the piece the earlier analysis predicted was missing, since
the sensor configuration lives on the AP in `/vendor/etc/sensors/`. It is there
now. The SUID lookup still returns nothing: request accepted, `result=0`, fresh
client id, no indication.

Two things to know before continuing. Restarting the ADSP by hand drops RFSA
from the bus — it comes back only on a clean boot, so hand-sequencing the
subsystems is not equivalent to booting with everything already in place. And
rebooting while Linux manages the DSPs hung the device once during shutdown,
needing a forced power-off; that is worth pinning down separately, and it is
also how the zero-byte `.jsn` files happened.

The remaining question is unchanged and now much better isolated: nothing asks
the ADSP to start its `sensor_pd`. The locator that would let it resolve that
domain exists at last, which was the prerequisite, but the domain is still not
running.

---

# The sensor_pd is already running, which rules out the leading theory

**Status: measured.** `msm/adsp/sensor_pd` reports `SERVREG_SERVICE_STATE_UP`.
The protection domain that the sensor drivers live in is up, has been all along,
and the sensor core still reports no sensors.

The previous section ended by saying nothing asks the ADSP to start its
`sensor_pd`. That turns out to be the wrong question: it is started.

## Talking SERVREG without reverse engineering

Mainline already implements this protocol, so the wire format is not guesswork.
`drivers/soc/qcom/pdr_interface.c` for the flow, `qcom_pdr_msg.c` for the TLV
layout, `include/linux/soc/qcom/qmi.h` for the service numbers:

	64 (0x40)  SERVREG_LOCATOR   published by pd-mapper on the AP
	66 (0x42)  SERVREG_NOTIFIER  published by each remote processor

	0x20  REGISTER_LISTENER    TLV 1 = enable (u8), TLV 2 = service_path
	0x21  GET_DOMAIN_LIST      TLV 1 = service name, TLV 0x10 = offset
	0x24  RESTART_PD           TLV 1 = service_path

Two details that cost a round trip each. In `register_listener` the TLVs are
**not** in the order the struct suggests — `enable` is TLV 1 and the path is TLV
2; getting it backwards returns `error=1` (MALFORMED_MSG). And top-level
`QMI_STRING` carries no length prefix, the TLV length is the length
(`qmi_encode_string_elem`, `enc_level == 1`).

`scripts/pdr-tool.py` implements list, state and restart.

## What it reports

Registering a listener against the ADSP notifier (node 5, instance 74) succeeds
for all three domains, and every one of them is up:

	msm/adsp/sensor_pd    result=0   state UP (0x1FFFFFFF)
	msm/adsp/audio_pd     result=0   state UP
	msm/adsp/root_pd      result=0   state UP

The other notifiers reject these paths with `error=45`, which is correct — they
are not their domains. The state values are the `servreg_service_state` enum
from `pdr.h`, where UP is `0x1FFFFFFF` rather than anything intuitive.

`RESTART_PD` on `msm/adsp/sensor_pd` returns `error=69`,
**`QMI_ERR_DISABLED_V01`**: the firmware does not permit the AP to restart that
domain. Worth knowing before anyone plans around being able to.

## The startup-order theory, tested and also wrong

There is a real ordering problem on this device:

	[11.1s]  kernel: remote processor adsp is now up
	[18.2s]  systemd: Started rmtfs.service

The ADSP initialises seven seconds before RFSA — the service the DSP would use
to read files off the AP, where the sensor configuration in
`/vendor/etc/sensors/` lives. A sensor core that came up with no file server
would plausibly instantiate nothing.

It can be tested. Restarting the ADSP by hand drops the AP's services off the
bus, but stopping it *first* avoids that:

	stop adsp -> restart rmtfs and pd-mapper -> RFSA (14) and locator (64) up
	           -> start adsp -> both still up

That gives an ADSP that initialised with RFSA present. The SUID lookup still
returns nothing. So the ordering, real as it is, is not what is stopping this.

## Where that leaves it

Ruled out by measurement, in order: the I2C route (the IMU is on I3C), the
client's message format (every field checked against the vendor's serialisers),
missing fastrpc, pd-mapper serving nothing, the DSPs not being managed by Linux,
the sensor_pd not running, and RFSA being absent when the ADSP starts.

The one observation that now looks most important, and that all of the above
leaves untouched: the sensor core returns nothing for `resampler` and `suid`
either, and those are framework sensors that do not depend on the sensor
drivers at all. Whatever is wrong is upstream of the individual sensors. Either
the client is not receiving indications that are being sent, or the framework
itself is not publishing sensors to clients on this transport.

Distinguishing those two is the next useful step, and it does not need more
guessing: capture what the ADSP actually sends. Either by having something else
on the bus observe the traffic, or by checking whether the indication is being
dropped before it reaches the socket.

---

# The sensor core works. It is missing its registry, and that is all

**Status: measured.** Indications now arrive, the framework's own sensors
enumerate, and the reason no physical sensor exists is identified: the
`registry` sensor is absent, and every driver depends on it.

This is the section that turns "sensors do not work" into a specific missing
piece.

## What was actually wrong with the client

One TLV. The request needs **TLV 0x10 set to 1** alongside the payload TLV:

	10 01 00 01      <- this
	01 33 00 ...     <- payload

Without it the sensor core accepts the request, returns `result=0`, allocates a
client id, and never sends an indication. Every failure mode looks like success.
With it, the indication arrives immediately.

This was not deduced, it was observed. Logging QRTR in the kernel showed a 32
byte response *and* a 71 byte indication going to some other port once a second,
while ours only ever got the 32 byte response. The other port belonged to
**`iio-sensor-proxy`**, which is already installed and has an SSC backend, and
diffing its request against ours left exactly one difference.

Two things worth taking from that. Instrumenting the transport answered in one
boot what a dozen protocol variants had not. And the useful comparison was not
against documentation but against a working client that was already running on
the device.

## iio-sensor-proxy is already wired up

It polls the sensor core once a second, from boot, and has done through this
whole investigation. That removes a whole step from the earlier plan: nobody
needs to write the bridge to userspace. `monitor-sensor` and the
`net.hadess.SensorProxy` D-Bus interface are in place and will start reporting
the moment the sensor core has sensors. Right now:

	HasAccelerometer = false
	HasAmbientLight  = false
	HasProximity     = false

## What the sensor core does and does not have

With indications working the SUID lookup can enumerate. Asking for every data
type gives a very clean split:

	timer            1 suid
	resampler        1 suid
	async_com_port   1 suid
	device_mode      1 suid
	suid             1 suid

	registry         0
	interrupt        0
	accel gyro mag proximity ambient_light pressure   0
	island low_lat_stream amd gravity                 0

Everything that exists is internal to the framework and needs no external
configuration. Everything missing sits downstream of one sensor: **`registry`**.

That is not a coincidence. On SSC every physical sensor driver reads its bus,
address, interrupt and calibration out of the registry at init. No registry, no
driver instantiates - which is exactly why `interrupt` is missing too, and why
`async_com_port` (the one that would drive the I3C and I2C buses) is present but
idle. The sensor core is healthy; it has nothing to configure itself from.

## Where the registry lives

On the `persist` partition, which is present and mountable:

	/persist/sensors/registry/registry/   229 files
	/persist/sensors/sensors_list.txt
	/persist/sensors/sensors_settings

These live on the AP. The ADSP reads them remotely - this is what RFSA (Remote
File System Access) is for, and it is why `/vendor/lib/rfsa/adsp/` exists at all.
Note that this is a different thing from RMTFS: `rmtfs` serves the modem's EFS
as block storage and publishes QMI service 14, and that is already running. It
is not what serves sensor registry files.

## Next step

Get the ADSP able to read `/persist/sensors/registry/`. That is now the whole
problem, and it is a much better defined one than anything earlier in this file:
the sensor core is up, its framework sensors work, the transport is understood,
the client is correct, and the consumer (`iio-sensor-proxy`) is already
installed and polling. Once the registry is served, drivers should instantiate
and sensors should appear in D-Bus without further work on our side.

Scripts: `ssc-probe.py` (single lookup, decodes the reply),
`ssc-enum.py` (enumerate data types).

## The remote heap, and what is left

The ADSP fastrpc node needed a `memory-region`. Without it the probe printed
`no reserved DMA memory for FASTRPC` on every boot and any attempt to create a
protection domain failed. Patch 0057 now declares one, and the values agree from
two independent directions: the vendor writes
`qcom,adsp-remoteheap-vmid = <22 37>`, and mainline's `QCOM_SCM_VMID_LPASS` is
0x16 with `QCOM_SCM_VMID_ADSP_HEAP` 0x25 - the same pair `kodiak.dtsi` uses,
8 MB in both. Declared as a reusable CMA pool with no fixed address, like the
vendor does, so it cannot collide with the firmware carve-outs. The warning is
gone.

With the heap in place both fastrpc entry points behave, and neither produces a
registry:

	FASTRPC_IOCTL_INIT_ATTACH_SNS      (_IO('R',8))    OK, no change
	FASTRPC_IOCTL_INIT_CREATE_STATIC   "sensorspd"     OK, then the ADSP dies

The second one is worth flagging. It returns success and then the ADSP stops
answering QMI entirely - `remoteproc1/state` still says `running` while every
service on node 5 disappears. Recovering needs a stop, an rmtfs and pd-mapper
restart, and a start. Do not run it casually.

One trap that will produce false negatives: **the sensor core's port moves**.
It was 5:14, and after an ADSP restart it came back on 5:13. Anything hardcoding
the port will silently test nothing. `ssc-enum.py` looks it up now.

## Why the registry is missing, as far as this goes

The registry sensor is not a loadable library. `/vendor/lib/rfsa/adsp/` holds 23
files, almost all camera and DSP, and the only sensor ones are
`libsns_device_mode_skel.so` and `libsns_low_lat_stream_skel.so`. The registry
lives inside the ADSP firmware, and the firmware's own strings say what it wants:

	/vendor/etc/sensors/sns_reg_config
	Registry dir '%s' not available
	Registry disabled, leaving default mode enabled
	Sending registry request for group name:%s

It wants to read a directory on the AP. That is what RFSA is for, and RFSA rides
on fastrpc with a **listener** on the AP side answering the DSP's file requests -
which is what `adsprpcd`/`sscrpcd` provide on Android. Mainline's fastrpc has no
listener support at all: no `listener`, no reverse-invocation path anywhere in
`drivers/misc/fastrpc.c`.

So the remaining gap is a real one and it is in the kernel, not in our client:
the DSP has no way to read files from the AP. Closing it means porting
listener/reverse-RPC support into mainline fastrpc and writing the userspace
daemon that serves `/persist/sensors/registry/`. That is a substantially bigger
job than anything in this file so far, and it should be sized honestly before
being started.

What is already done and does not need revisiting: the buses are known, the
transport and message format are understood and verified, the client is correct,
indications work, the sensor core is up with its framework sensors, the
protection domains are up, fastrpc works with a remote heap, and the consumer
(`iio-sensor-proxy`) is installed and polling. The single missing link is file
access from DSP to AP.

---

# SOLVED: the sensors work

**Status: verified on the device across three consecutive clean boots.**
Accelerometer, gyroscope, magnetometer, compass, proximity and ambient light are
all up, exposed on D-Bus through `iio-sensor-proxy`, and the screen
auto-rotates. The whole of it lives in [`userspace/sensors/`](../userspace/sensors/),
which has the full write-up; this section records what the section above got
wrong, because that is the part worth carrying forward.

## The sizing above was wrong, and expensively so

The previous section ended:

> Closing it means porting listener/reverse-RPC support into mainline fastrpc
> and writing the userspace daemon that serves `/persist/sensors/registry/`.
> That is a substantially bigger job than anything in this file so far, and it
> should be sized honestly before being started.

Both halves were wrong, and the reasoning behind them is a good example of a
conclusion that is locally sound and globally false.

**There is no reverse RPC to port.** The premise was that the DSP calls into the
AP and that mainline `drivers/misc/fastrpc.c` has no path for it — which is true
as stated, and irrelevant, because the DSP never initiates anything. The AP
daemon makes an ordinary *forward* invocation on static handle 3
(`adsp_listener`); the DSP answers it only when it has work for us; the daemon
does the work and invokes again carrying the result. It is all
`FASTRPC_IOCTL_INVOKE`, which mainline has always had. **No kernel patch was
written for any of this** and the running kernel is unchanged.

The mistake was reading the kernel driver for the word "listener", finding
nothing, and concluding the mechanism was missing — instead of reading what the
vendor's own daemon does. `/vendor/bin/sscrpcd` is 15 KB and does exactly one
thing: `dlopen("libssc_default_listener.so")` and call
`adsp_default_listener_start()`. Its strings name `remote_handle_open`,
`remote_handle_invoke` and `createstaticpd:` — all client-side calls. Five
minutes with `strings` would have settled the size of the job.

**And the userspace did not need writing.** Qualcomm publishes libadsprpc as
[`quic/fastrpc`](https://github.com/quic/fastrpc), BSD-3-Clause, including
`listener_android.c`, `apps_std_imp.c` and the generated stubs — and it has
supported the *mainline* uapi since 2024:

	src/fastrpc_ioctl.c
	  /* File only compiled when support to upstream kernel is required */
	  #define FASTRPC_IOCTL_INIT_ATTACH_SNS  _IO('R', 8)

with `-DENABLE_UPSTREAM_DRIVER_INTERFACE` on by default. It built for aarch64
unmodified on the first attempt, and its `sensorspd` path maps straight onto
`FASTRPC_IOCTL_INIT_ATTACH_SNS` — the ioctl mainline added for precisely this
case and which nothing on this port had ever used.

## What was genuinely missing

The registry files, on the application processor, where the ADSP could fetch
them. The section above had already identified that correctly. What it had not
noticed is that the DSP does not want them at `/persist/sensors/registry/`: the
paths are compiled into the ADSP firmware as the Android ones,
`/vendor/etc/sensors/` and `/mnt/vendor/persist/sensors/registry/registry`, and
`sns_reg_config` also asks for six `/sys/devices/soc0` attributes that mainline's
socinfo does not expose.

The first run, with the listener working but no files behind it, produced the
clearest error message this port has seen:

	fatal error received: err_qdi.c:1045:EF:sensor_process:0x1:SNS_REG_TASK:0xa3:
	  sns_registry_sensor.c:154: SNS_RC_SUCCESS == rc

— the registry task waking up for the first time, asking, being refused, and
asserting, which on this platform restarts the whole ADSP and kills audio.

## Corrections to the facts above

- The accelerometer on this board is the **TDK-Invensense icm4x6xx** on I3C, not
  the `lsm6dso` the vendor JSON also describes. Both are in the registry; only
  one answers.
- `iio-sensor-proxy` is not merely "installed and polling" — it contains
  `ssc-accel` and `ssc-proximity` drivers that its own udev rule never tags, so
  two of the four sensors it can do were never even probed.
- The registry's `icm4x6xx_0_platform.placement` matrix is twelve zeros, and its
  `.orient` entry is the sensor-to-device transform which the DSP already
  applies. The raw SSC stream is in the standard Android device frame, measured
  with `scripts/ssc-stream.py`. The 180-degree offset that remained is the panel
  mounting, and it is fixed with `ACCEL_MOUNT_MATRIX` in udev.

## What is left

The gyroscope, `rotv`, `gravity`, `game_rv`, `geomag_rv`, `step_detect`,
`pedometer` and the rest of the fusion set all enumerate and stream — see
`scripts/ssc-stream.py gyro` — but `iio-sensor-proxy` has no driver for them, so
nothing publishes them on D-Bus. That is a consumer-side gap, not a port one.

---
