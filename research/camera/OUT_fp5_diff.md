# FP5 (works) vs rhodep (fails) — S5KJN1 DT/driver diff

Goal: with the SAME mainline `drivers/media/i2c/s5kjn1.c` working on Fairphone FP5 but
failing to identify on the Moto G82 rhodep (SM6375) port, find the rhodep-specific
DT/hardware/config delta. Sensor ACKs 0x56 but its register block returns residue
(0x8b01), never 0x38e1.

Sources are marked FACT (with file/line/URL), INFERENCE, or UNKNOWN.

---

## 0. TL;DR — ranked verdict

1. **CCI child-bus clock-frequency (bus SPEED mode).** FACT: FP5's s5kjn1 sits on
   `cci1_i2c1`, and in the SoC dtsi that bus is `clock-frequency = <1000000>` — i.e.
   **FAST_PLUS / 1 MHz**. rhodep's 0055 patch puts the sensor on `cci0_i2c1` but the
   patch **never sets `clock-frequency` on that child bus**, so it takes the CCI
   driver default. This is the single most likely cause of "ACK but garbage register
   reads" that differs from FP5. (See §4, ranked #1.)

2. **The whole CCI/CAMSS/camcc tree is absent from mainline SM6375.** FACT:
   `arch/arm64/boot/dts/qcom/sm6375.dtsi` contains **no `cci`, no `camss`, no camcc**
   node at all (verified: zero matches for cci/camss/camcc/cam_cc). The 0055 patch
   references `&cci0`, `&cci0_i2c1`, `&camss` and `GCC_CAMSS_MCLK1_CLK` — all of which
   must be provided by the rhodep port itself. If the port's CCI node is a hand-rolled
   backport, its correctness (register base, clocks, GDSC/power-domain, child-bus
   clock-frequency, compatible) is unproven and is the most probable place the bug
   lives. On FP5 the CCI node is the mature, upstream sc7280 one. (See §2.)

3. **reset-gpios polarity is CORRECT on rhodep** — identical to FP5
   (`GPIO_ACTIVE_LOW`), and the driver drives the right physical level. This is NOT
   the bug. Definitive proof in §3.

Everything else (supplies, MCLK rate, link-frequency, data-lanes, chip-id reg,
0x56 vs 0x10 address) is either equivalent or provably irrelevant to i2c identify.

---

## 1. The full FP5 s5kjn1 DT node (FACT)

Source: `arch/arm64/boot/dts/qcom/qcm6490-fairphone-fp5.dts` (torvalds/linux master,
fetched). The sensor is a child of `&cci1_i2c1`:

```dts
&camss {
	vdda-phy-supply = <&vreg_l10c>;
	vdda-pll-supply = <&vreg_l6b>;
	status = "okay";
	ports {
		port@3 {
			csiphy3_ep: endpoint {
				data-lanes = <0 1 2 3>;
				bus-type = <MEDIA_BUS_TYPE_CSI2_DPHY>;
				remote-endpoint = <&camera_s5kjn1_ep>;
			};
		};
	};
};

&cci1 {
	status = "okay";
};

&cci1_i2c1 {
	camera@10 {
		compatible = "samsung,s5kjn1";
		reg = <0x10>;

		vdda-supply = <&vreg_l3p>;   /* AVDD  2.7-3.0 V (pm8008 ldo3) */
		vddd-supply = <&vreg_l2p>;   /* DVDD  0.95-1.152 V (pm8008 ldo2) */
		vddio-supply = <&vreg_l6p>;  /* VDDIO 1.7-1.9 V (pm8008 ldo6, always-on) */

		clocks = <&camcc CAM_CC_MCLK3_CLK>;
		assigned-clocks = <&camcc CAM_CC_MCLK3_CLK>;
		assigned-clock-rates = <24000000>;

		reset-gpios = <&tlmm 78 GPIO_ACTIVE_LOW>;

		pinctrl-0 = <&cam_mclk3_default>;
		pinctrl-names = "default";

		orientation = <0>;   /* Front facing */
		rotation = <270>;

		port {
			camera_s5kjn1_ep: endpoint {
				data-lanes = <1 2 3 4>;
				link-frequencies = /bits/ 64 <700000000>;
				remote-endpoint = <&csiphy3_ep>;
			};
		};
	};

	eeprom@51 { compatible = "giantec,gt24p128f","atmel,24c128"; reg = <0x51>;
		    vcc-supply = <&vreg_l6p>; read-only; };
};
```

And the crucial parent-bus property that is *not* in the sensor node but governs it —
from the SoC dtsi (`kodiak.dtsi`, i.e. sc7280, fetched):

```dts
cci1: cci@ac4b000 {
	compatible = "qcom,sc7280-cci", "qcom,msm8996-cci";
	...
	cci1_i2c1: i2c-bus@1 {
		reg = <1>;                    /* CCI master 1 */
		clock-frequency = <1000000>;  /* <<< FAST_PLUS / 1 MHz */
		#address-cells = <1>;
		#size-cells = <0>;
	};
};
```
FACT (`kodiak.dtsi:5146-5151`). Both `cci0_i2c0/1` and `cci1_i2c0/1` are
`clock-frequency = <1000000>` in sc7280 (`kodiak.dtsi:5099-5151`).

---

## 2. Field-by-field FP5 vs rhodep (0055 patch)

| Field | FP5 (works) | rhodep 0055 (fails) | Same? |
|---|---|---|---|
| compatible | samsung,s5kjn1 | samsung,s5kjn1 | ✅ |
| reg (i2c addr) | 0x10 | 0x56 | ⚠ different, both valid¹ |
| vdda (AVDD) | vreg_l3p 2.7-3.0 V | cam_vana 2.804 V (FAN53870 ldo4) | ✅ equiv |
| vddd (DVDD) | vreg_l2p 0.95-1.152 V | cam_vdig 1.056 V (ldo1) | ✅ equiv |
| vddio | vreg_l6p 1.7-1.9 V, **always-on** | cam_vio 1.804 V (ldo7) | ⚠ see ² |
| afvdd | (not in node) | (not in node) | ✅ |
| MCLK source | camcc CAM_CC_MCLK3_CLK | gcc GCC_CAMSS_MCLK1_CLK | ✅ diff src, both 24 MHz |
| MCLK rate | 24 MHz | 24 MHz | ✅ |
| reset-gpios | `<&tlmm 78 GPIO_ACTIVE_LOW>` | `<&tlmm 35 GPIO_ACTIVE_LOW>` | ✅ **same polarity** |
| data-lanes | 1 2 3 4 | 1 2 3 4 | ✅ |
| link-frequencies | 700000000 | 700000000 | ✅ |
| CCI instance | cci1, master 1 (`cci1_i2c1`) | cci0, master 1 (`cci0_i2c1`) | matches vendor³ |
| **child-bus clock-frequency** | **1000000 (FAST_PLUS)** in SoC dtsi | **absent in patch → default** | ❌ **KEY DIFF** |
| CCI node provenance | upstream sc7280 CCI (mature) | port-supplied (not in mainline SM6375) | ❌ see §2b |
| port/csiphy | csiphy3 | csiphy0 | ✅ irrelevant to i2c |

¹ 0x10 vs 0x56: both are legal S5KJN1 slave addresses selected by the module's SADDR
strap. The upstream binding example itself uses `camera@56 { reg = <0x56> }`
(FACT, `samsung,s5kjn1.yaml` examples). rhodep's 0x56 comes from the vendor blob
(`0055 patch:60-74`). The sensor ACKs 0x56 live, so the address is right. **Not the bug.**

² VDDIO always-on: FP5's vreg_l6p is `regulator-always-on` because it is the CCI I2C
pull-up rail (FACT, fp5.dts comment "Pull-up for CCI I2C busses"). On rhodep cam_vio is
a FAN53870 LDO that is enabled and measured at 1.804 V live (context_v3:8), and the
EEPROM on the same bus reads fine — so the pull-up/pad-ring rail is up. **Not the bug.**

³ Vendor overlay confirms rhodep sensor is CCI0 master 1: `sensor_main` under
`&cam_cci0` with `cci-master = <1>` (FACT, blair-...overlay.dtsi:421-455). The 0055
patch's `cci0_i2c1` (reg=<1>) is the correct bus. **Wiring is right.**

### 2b. The elephant: mainline SM6375 has no camera subsystem at all
FACT: fetched `arch/arm64/boot/dts/qcom/sm6375.dtsi` (torvalds/linux master) and
searched it — **zero** occurrences of `cci`, `camss`, `camcc`, `cam_cc`. The GCC block
*does* export the clocks (`GCC_CAMSS_MCLK1_CLK` = 47, `GCC_CAMSS_CCI_0_CLK` = 29, etc.
in `qcom,sm6375-gcc.h`, FACT), and there is a `CAMSS_TOP_GDSC` (=2) and
`GCC_CAMSS_TOP_BCR`, but **no `cci@…` device node, no `camss` isp node, and no camcc
clock controller** exists upstream for SM6375. Note SM6375 routes CCI+MCLK straight
off **GCC** (there is no separate camcc), unlike sc7280/qcm6490 which use `camcc`.

Consequence: everything the 0055 patch dereferences with `&cci0`, `&cci0_i2c1`,
`&camss` must be defined by the rhodep port's own DTS (a hand-written CCI/CAMSS
backport), because upstream provides none. On FP5, the identical labels resolve to the
**mature, upstream, known-good** sc7280 CCI node. This is the biggest structural
difference and the most likely home of the defect: the rhodep CCI node's
`compatible`, register base (`cci@…`), clock list/order, `power-domains` (GDSC), and —
critically — the **child i2c-bus `clock-frequency`** are all unverified here.

INFERENCE: if rhodep's CCI node is even slightly off (wrong CCI IP `compatible`, wrong
`cci`/`cci_src` clock wiring, or a default/omitted child-bus clock-frequency), the CCI
master can clock the SCL correctly enough to ACK an address (address phase is forgiving)
yet mis-time the multi-byte data phase, yielding stable but wrong data bytes — exactly
the "ACK, then residue that aliases mod-4" signature. The EEPROM reading fine does not
fully exonerate this: an EEPROM is a dumb, extremely timing-tolerant slave; a sensor's
register state machine is far less tolerant of SCL duty/hold/setup deviations.

---

## 3. Reset polarity — VERDICT: rhodep is driving the CORRECT physical level

Definitive chain of evidence:

- **Datasheet convention (FACT, general S5K/ISOCELL + binding text):** S5KJN1
  XSHUTDOWN/RESET is **active-low** — LOW = held in reset, HIGH = running. The upstream
  binding states it explicitly: *"reset-gpios: Active low GPIO connected to RESET pad of
  the sensor."* (FACT, `samsung,s5kjn1.yaml`).

- **Driver logic (FACT, s5kjn1-mainline.c):**
  - probe requests the GPIO as `GPIOD_OUT_HIGH` (line 1324-1325). With the DT flag
    `GPIO_ACTIVE_LOW`, `GPIOD_OUT_HIGH` means **logical asserted = physical LOW** at
    request time (i.e. starts in reset).
  - `s5kjn1_power_on()` line 1238: `gpiod_set_value_cansleep(reset_gpio, 0)` →
    logical 0 = **de-assert** → with ACTIVE_LOW that is **physical HIGH** → sensor
    **runs**. Then `usleep_range(10-15 ms)` before the chip-id read.
  - `s5kjn1_power_off()` line 1269: `gpiod_set_value(reset_gpio, 1)` → logical 1 =
    assert → physical **LOW** → sensor held in reset.

- **DT flag (FACT):** rhodep `reset-gpios = <&tlmm 35 GPIO_ACTIVE_LOW>` — **identical
  polarity to FP5** (`<&tlmm 78 GPIO_ACTIVE_LOW>`).

- **Live measurement (FACT, context_v3:9):** during the id read, gpio35 = "out high".

Putting it together: logical-deassert (0) → physical HIGH → sensor released/running,
and live shows gpio35 HIGH during the read. **HIGH = released = running for this part**,
which is exactly what the driver intends and what the datasheet requires. The core is
therefore NOT being held in reset. The "ACK but residue" is happening with the core
*out* of reset.

**Verdict: reset polarity and physical level are correct on rhodep. Reset is not the
bug.** (This also matches context_v3:25 "RULED OUT: reset polarity" from live sweeps.)

---

## 4. What else — ranked by likelihood of "ACK but no identify"

### #1 (most likely) — CCI child-bus `clock-frequency` missing on rhodep → wrong speed mode
- FACT: FP5's sensor bus `cci1_i2c1` is `clock-frequency = <1000000>` (FAST_PLUS)
  in the SoC dtsi (`kodiak.dtsi:5146-5151`). All sc7280 CCI child buses are 1 MHz.
- FACT: the rhodep 0055 patch's `&cci0_i2c1` block sets NO `clock-frequency`
  (0055 patch:60-99 — it only adds the `camera@56` child, no bus property).
- FACT: the mainline CCI driver derives the master speed mode solely from the child
  bus `clock-frequency` (≤100k STANDARD, ≤400k FAST, ≤1M FAST_PLUS); if absent it uses
  the driver default (100 kHz STANDARD) — a real mismatch vs FP5's 1 MHz and vs the
  vendor blob's FAST/FAST_PLUS (OUT_camx_seq.md §4, cam_sensor_cmn_header.h enum).
- Why this fits the symptom: a slow/mis-configured SCL can still get an address ACK
  (single byte, generous timing) while the 2-byte data phase reads back wrong/stale
  bits — "ACK but residue." The EEPROM tolerating it does not clear the sensor.
- NOTE / caveat: context_v3:25 says "CCI speed 400/100k" was swept live and ruled out.
  But FP5 uses **1 MHz (FAST_PLUS)**, which is a *third* mode that may not have been
  tried, and the sweep was via the camdiag rig, not by giving the *driver-bound* CCI
  child node the `clock-frequency` property so the kernel's `cci_probe` programs the
  master timing registers itself. **These are not equivalent.** The fix must be applied
  as a DT property on the driver-owned CCI child bus, then let the s5kjn1 driver probe.

**Concrete fix (try first, cheapest):**
```dts
&cci0_i2c1 {
	clock-frequency = <1000000>;   /* match FP5 FAST_PLUS; if unhappy try <400000> */
	/* ...existing camera@56... */
};
```

### #2 — rhodep CCI/CAMSS/camcc node correctness (backport vs upstream)
- FACT: no CCI/camss/camcc in mainline sm6375.dtsi (§2b). The port supplies them.
- INFERENCE: verify the rhodep `cci0` node against a known-good Qualcomm CCI:
  correct `compatible` (must be a `qcom,msm8996-cci`-compatible variant the
  `i2c-qcom-cci` driver binds), the five clocks (`camnoc_axi, slow_ahb_src, cpas_ahb,
  cci, cci_src`) wired to the right GCC clocks (on SM6375: `GCC_CAMSS_CCI_0_CLK` +
  `_CLK_SRC`, plus the camnoc/ahb/cpas clocks), and a valid `power-domains` GDSC
  (`CAMSS_TOP_GDSC`). If `cci_src` is wrong/unclamped, SCL timing is wrong → the exact
  symptom. context_v3 already flagged CCI source clock had to be fixed to 37.5 MHz.
- Action: dump the rhodep port's actual `cci0` node and diff its clocks/compat/
  power-domain/child-bus against sc7280's `cci0` (`kodiak.dtsi:5074-5112`). (Node not
  provided in /tmp/camre6 — UNKNOWN here; get it from the rhodep DTS.)

### #3 — MCLK source/quality
- FACT: FP5 MCLK from `camcc CAM_CC_MCLK3_CLK`; rhodep from `gcc GCC_CAMSS_MCLK1_CLK`
  (different because SM6375 has no camcc). Both assigned 24 MHz; both live-verified.
- INFERENCE: unlikely to be *the* bug (24 MHz confirmed at the sensor live,
  context_v3:8), but the driver hard-requires exactly 24 MHz
  (s5kjn1-mainline.c:1313-1317) and it is satisfied. Leave as-is.

### #4 — link-frequencies / data-lanes (driver hwcfg validation)
- FACT: driver `s5kjn1_check_hwcfg()` (lines 1157-1200) requires 4 data-lanes and a
  link-frequency present in {700 MHz}. rhodep provides `data-lanes = <1 2 3 4>` and
  `link-frequencies = <700000000>` (0055 patch:93-94) — **matches** FP5 and passes
  validation. Also, `check_hwcfg` runs *before* power-on/identify, so a failure here
  would abort earlier with a different error, not "38e1!=…". **Not the bug.**

### #5 — the "driver reads 0, raw reads 0x8b01" asymmetry
- FACT: driver reads chip id via `cci_read(regmap, CCI_REG16(0x0000))`
  (lines 1142, 22). context_v3:22 notes probe logs "38e1!=0" (val=0) while raw
  i2ctransfer returns 0x8b01. INFERENCE: this is consistent with the CCI master (when
  the kernel programs it via the bound node) being in a *different* timing/format state
  than the ad-hoc camdiag raw path — reinforcing #1/#2 (the driver-owned CCI config,
  esp. speed mode, is what's wrong, not the raw bus). It is *not* a driver register-map
  bug: FP5 uses the identical `cci_read` path and works.

---

## 5. Single most likely fix (concrete)

Add the FAST_PLUS bus speed that FP5 gets for free from the SoC dtsi, to rhodep's
driver-owned CCI child bus, and (belt-and-braces) audit the port's CCI node clocks:

```dts
&cci0_i2c1 {
	/* FP5's sensor bus runs at 1 MHz (FAST_PLUS) via sc7280 dtsi; rhodep's
	 * backported cci0 child bus omits this, so the CCI master falls back to
	 * STANDARD (100 kHz) and the 2-byte register phase reads back residue while
	 * the address still ACKs. Match FP5. */
	clock-frequency = <1000000>;

	camera_rear: camera@56 {
		/* ...unchanged... */
	};
};
```
If 1 MHz misbehaves, step down to `<400000>` (FAST). Re-run `s5kjn1` probe (a real
bound probe, not a camdiag raw read) and check for `chip id 0x38e1`.

Second, if that alone does not fix it, diff the rhodep port's `cci0` node against
`kodiak.dtsi:5074-5112`: same `qcom,msm8996-cci`-family `compatible`, the five
clocks mapped to SM6375 GCC (`GCC_CAMSS_CCI_0_CLK`/`_SRC`, camnoc/cpas/ahb),
`power-domains = <&gcc CAMSS_TOP_GDSC>`, and both child buses at 1 MHz. A wrong
`cci_src` clock is the classic cause of "ACK but wrong data."

---

## 6. FACT / INFERENCE / UNKNOWN summary

**FACT**
- FP5 s5kjn1 node quoted in full; on `cci1_i2c1` (CCI1 master 1), addr 0x10, reset
  `<&tlmm 78 GPIO_ACTIVE_LOW>`, MCLK camcc MCLK3 24 MHz, lanes 1-2-3-4, link-freq
  700 MHz. (qcm6490-fairphone-fp5.dts, fetched.)
- FP5 sensor bus `clock-frequency = <1000000>` (FAST_PLUS); all sc7280 CCI child
  buses are 1 MHz. (kodiak.dtsi:5099-5151.)
- rhodep 0055 node: addr 0x56, on `cci0_i2c1`, reset `<&tlmm 35 GPIO_ACTIVE_LOW>`,
  MCLK gcc MCLK1 24 MHz, lanes 1-2-3-4, link-freq 700 MHz; **no clock-frequency on
  the CCI child bus.** (0055 patch:60-99.)
- reset polarity identical (both GPIO_ACTIVE_LOW); driver drives logical-0 =
  physical-HIGH = released in power_on; live gpio35 = HIGH during read → core is out
  of reset. Reset is correct. (s5kjn1-mainline.c:1238/1269/1324, binding text,
  context_v3:9/25.)
- mainline sm6375.dtsi has NO cci/camss/camcc nodes; GCC exports the camera clocks;
  SM6375 has no camcc (CCI/MCLK via GCC). (sm6375.dtsi fetched, zero matches;
  qcom,sm6375-gcc.h.)
- driver requires MCLK==24 MHz (1313-1317), 4 lanes + 700 MHz link-freq (1157-1200);
  rhodep satisfies all. chip id read is a cold `cci_read(CCI_REG16(0x0000))`
  (1142) compared to 0x38e1 (1148).
- 0x56 is a legal S5KJN1 address (binding example uses camera@56/reg 0x56).

**INFERENCE**
- The rhodep-specific delta that best explains "ACK + stable residue, driver reads 0"
  is the CCI **speed mode / child-bus clock-frequency** (rhodep omits it → default
  STANDARD; FP5 = 1 MHz FAST_PLUS), and/or a subtly wrong port-supplied CCI node
  (clocks/compatible/GDSC), since the entire CCI tree is a backport on SM6375.
- The live "400/100k swept, ruled out" does not cover FP5's 1 MHz mode and did not
  necessarily program the *driver-bound* CCI child node's clock-frequency; applying it
  as DT on the bound node is a distinct, untried test.

**UNKNOWN**
- The rhodep port's actual `cci0`/`camss`/camcc(or GCC-based) node text — not in
  /tmp/camre6. Needs to be diffed against sc7280's `cci0` (kodiak.dtsi:5074-5112) to
  confirm clocks/compatible/power-domain and whether the child-bus clock-frequency is
  truly absent or set elsewhere.
- Whether 1 MHz vs 400 kHz is the working value on rhodep silicon (FP5 = 1 MHz).

## Sources
- FP5 DT: torvalds/linux `arch/arm64/boot/dts/qcom/qcm6490-fairphone-fp5.dts` (fetched).
- SoC CCI/CAMSS: torvalds/linux `arch/arm64/boot/dts/qcom/kodiak.dtsi` (sc7280) 5074-5231 (fetched).
- SM6375 SoC: torvalds/linux `arch/arm64/boot/dts/qcom/sm6375.dtsi` (fetched; no cci/camss/camcc).
- GCC IDs: torvalds/linux `include/dt-bindings/clock/qcom,sm6375-gcc.h` (fetched).
- Binding: torvalds/linux `Documentation/devicetree/bindings/media/i2c/samsung,s5kjn1.yaml` (fetched).
- Driver: /tmp/camre6/s5kjn1-mainline.c.
- rhodep node: /tmp/camre6/0055-arm64-dts-qcom-rhodep-add-rear-camera.patch.
- Vendor wiring: /tmp/camre6/blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi.
- Prior RE: /tmp/camre6/OUT_camx_seq.md, context_v3.txt.
```
