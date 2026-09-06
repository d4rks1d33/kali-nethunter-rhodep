# Power delivery on rhodep: what this port does differently from stock

**Scope.** The SoC dies with a PS_HOLD deassert whenever the modem is made to
run one of its hardware engines continuously — GNSS `standalone` at full
`powerMode`, an LTE attach, a modem restart. Every application-processor
*configuration* hypothesis has come back negative (see
[`GNSS-SM6375.md`](GNSS-SM6375.md) and
[`../watchdog-ipa-lte-wip/HANDOFF.md`](../watchdog-ipa-lte-wip/HANDOFF.md)).
This document asks the remaining structural question: **is there anything in
power delivery that stock guarantees and this port does not?**

Everything below is derived from the recovered stock device tree
(`backups/vendor-dt-rhodep-20260906/merged.dts`, 21894 lines), the vendor kernel
(`Motorola-SM6375-Devs/android_kernel_motorola_sm6375`, branch `sixteen`;
`LineageOS/android_kernel_motorola_sm6375` `lineage-22.1` agrees byte for byte
on every file quoted), mainline `torvalds/linux` `master`, this port's patch
series, and read-only sysfs on the running device. **No register was read.**

## Result in one table

| # | Difference | Stock | This port | Testable without a flash | Could it cause a PS_HOLD deassert under sustained receive? |
| --- | --- | --- | --- | --- | --- |
| 1 | **DRMS (load/mode voting) is enabled per-rail by the driver downstream and per-rail by the device tree upstream** | on for **all 40** RPM resources (50 `qcom,rpm-smd-regulator` nodes), unconditionally, in `rpm-smd-regulator.c` | `regulator-allow-set-load` on **2** rails (`pm6125_l5`, `pm6125_l22` — the SD-card slot) | no | **Possible but narrow.** Only bites on a rail the modem needs and does *not* vote for itself. Exactly one such rail exists: `pmr735a_l1` |
| 2 | `pmr735a_l1`: `qcom,proxy-consumer-enable` + `qcom,proxy-consumer-current = <0xf230>` | held on at 600 mV in HPM **from probe to `sync_state`** | never enabled, never voted | no — needs 0121 | Possible; the enable half has already been tested negative, the **mode** half never has |
| 3 | Four live `regulator_set_load()` requests on this port are silently discarded (UFS ×2, UFS PHY ×2, DSI) | all reach RPM | all return 0 and send nothing | **yes** (`regulator_summary`) | **No** — none of these rails is on a modem path. It is a real correctness bug, not this bug |
| 4 | `qcom,vdd_cx-uV-uA = <0x180 0x186a0>` on the modem: TURBO **and a 100 mA current hint on `rwcx`** | both halves sent | level only (`rpmpd`, `INT_MAX`, a strict superset); **no current hint is possible** | no | Weak. CX is voted by many masters and RPM sums the `ma` key; the AP contributing 0 does not reduce anyone else's vote |
| 5 | `qcom,proxy-timeout-ms = <0x2710>` | **does not hold for 10 s.** With `qcom,proxy-unvote` present the vote is dropped on that interrupt, same as mainline's handover | same behaviour | n/a | **No — branch closed.** See §4.1 |
| 6 | External LNA (`gnss_l1_elna_ctrl`, `gnss_l2_5_elna_ctrl`) | **nothing.** No LNA supply, GPIO, pinctrl or node anywhere in the stock AP tree or the vendor kernel | nothing | n/a | **No — branch closed.** See §5 |
| 7 | PMIC over-current / UVLO / OVLO / brownout programming | **nothing.** The vendor's `qpnp-power-on` programs key debounce, reset type and reboot reason only | mainline programs the same subset | n/a | **No — branch closed.** See §2 |
| 8 | QMI thermal-mitigation client (`qmi-tmd-devices`: `modem_pa`, `modem_current`, `modem_bcl`, `modem_bw_backoff`) | 19 cooling devices registered against the modem over QMI | absent entirely | partially | Weak on the 100 ms timescale; see §4.3 |

Two things in that table are worth naming up front because they change what is
worth doing next:

* **`regulator_summary` cannot tell you whether a rail is on.** On this SoC
  `rpm_reg_is_enabled()` returns the AP driver's own `vreg->is_enabled`, a
  variable initialised to zero at probe and never compared against hardware.
  The `576mV / 0 users / off` reading of `pmr735a_l1` quoted throughout
  `GNSS-SM6375.md` means *the application processor does not vote for this
  rail*. It does **not** mean the rail is off. §1.6.
* **A new instrument exists and it is cheap.** `in_voltage_vph_pwr_input` on
  `1c40000.spmi:pmic@0:adc@3100` samples the PMIC input rail at **735 reads per
  second** with a **272 µV** standard deviation, and it measures a clean,
  repeatable **−3.17 mV** droop when all eight cores are pegged. That puts a
  hard number on how stiff this supply is, and the number argues strongly
  *against* a whole-system brownout. §3.

---

## 1. Regulator load and operating mode

### 1.1 How stock expresses it

Every rail on this board is an RPM resource. Downstream drives them with
`drivers/regulator/rpm-smd-regulator.c`. Two properties matter.

`qcom,hpm-min-load` sits on the **resource** node and is the threshold at which
the driver asks for high power mode. There are 40 RPM resource nodes
(`compatible = "qcom,rpm-smd-regulator-resource"`) carrying 50 regulator nodes,
and across all 40 the property takes exactly two values:

	qcom,hpm-min-load = <0x2710>;    /* 10000 uA — every LDO,  31 resources */
	qcom,hpm-min-load = <0x186a0>;   /* 100000 uA — every SMPS, 9 resources */

(A further seven `qcom,hpm-min-load = <0x00>` sit on `qcom,pm8008-l1..l7`, the
I2C camera PMIC, which is a different driver and not an RPM resource.)

`qcom,proxy-consumer-current` sits on the **regulator** node and is fed
straight into `regulator_set_load()` at probe by
`drivers/regulator/proxy-consumer.c`:

	consumer->enable
		= of_property_read_bool(node, "qcom,proxy-consumer-enable");
	of_property_read_u32(node, "qcom,proxy-consumer-current",
				&consumer->current_uA);
	...
	if (consumer->current_uA > 0) {
		ret = regulator_set_load(consumer->reg, consumer->current_uA);

There are **three** in the entire stock tree, and there are **zero**
`qcom,init-current` and **zero** `qcom,system-load` properties — that grep is
worth recording as a negative:

	$ grep -o "qcom,init-[a-z\-]*" merged.dts | sort | uniq -c
	     32 qcom,init-voltage
	      3 qcom,init-voltage-level
	$ grep -c "qcom,init-current\|qcom,system-load\|qcom,send-defaults" merged.dts
	0

Because `hpm_min_load` is 10 mA for every LDO and every proxy current is well
above it, **`qcom,proxy-consumer-current` on this board is not a current
budget. It is a one-bit request: "put this LDO in HPM."** The three values are:

| rail | property, verbatim | decimal | what it is |
| --- | --- | --- | --- |
| `pm6125_l8` | `qcom,proxy-consumer-current = <0xd13a8>;` | 857000 µA | display AVDD. The same 857000 appears as `qcom,supply-enable-load = <0xd13a8>` in `dsi_panel_pwr_supply_avdd/qcom,panel-supply-entry@1`, so this proxy is the display's own load carried across the bootloader-to-Android handover |
| `pm6125_l13` | `qcom,proxy-consumer-current = <0xf230>;` | 62000 µA | display VDDIO, `qcom,supply-enable-load = <0xf230>` in the same tables |
| `pmr735a_l1` | `qcom,proxy-consumer-current = <0xf230>;` | 62000 µA | **no consumer node anywhere in the tree** |

And the complete set of per-consumer load requests in the stock tree, from
`grep -o "[a-z0-9,\-]*load[a-z0-9\-]*"`:

	     47 qcom,hpm-min-load          40 RPM resources + 7 pm8008 camera LDOs
	     22 qcom,supply-enable-load    display panel supply tables
	     22 qcom,supply-disable-load   display panel supply tables
	     14 rgltr-load-current         camera: csiphy, cam-sensor, eeprom, ois, actuator

plus the `-uV-uA` pairs on the PIL subsystem nodes (§4). **Nothing else in the
stock tree requests a load on any rail, and nothing that does is on a modem
path.**

### 1.2 The structural difference, and it is the whole of §1

Downstream never uses `regulator-allow-set-load`. It does not have to.
`rpm_vreg_device_probe()` sets the constraint itself, for every rail:

	if (reg->rdesc.ops->get_mode) {
		init_data->constraints.valid_ops_mask
			|= REGULATOR_CHANGE_MODE | REGULATOR_CHANGE_DRMS;
		init_data->constraints.valid_modes_mask
			|= REGULATOR_MODE_NORMAL | REGULATOR_MODE_IDLE;
	}

`ldo_ops` and `smps_ops` both have `.get_mode`, so **every LDO and every SMPS on
both PMICs gets `REGULATOR_CHANGE_DRMS` unconditionally.** Any driver anywhere
in the vendor kernel that calls `regulator_set_load()` on any rail reaches RPM.

Mainline enables it only from the device tree, in `of_get_regulation_constraints()`:

	if (of_property_read_bool(np, "regulator-allow-set-load"))
		constraints->valid_ops_mask |= REGULATOR_CHANGE_DRMS;

and `drms_uA_update()` returns success without doing anything if it is missing:

	if (!regulator_ops_is_valid(rdev, REGULATOR_CHANGE_DRMS)) {
		rdev_dbg(rdev, "DRMS operation not allowed\n");
		return 0;
	}

This port has the property on **two** rails. `live-mainline.dts`, i.e. the tree
the running kernel actually holds:

	$ grep -c "allow-set-load" live-mainline.dts
	2

both under `regulators-0` (`qcom,rpm-pm6125-regulators`):

	l22 {
		regulator-max-microvolt = <0x2d2a80>;
		regulator-allow-set-load;
		regulator-min-microvolt = <0x294280>;
	};
	l5 {
		regulator-max-microvolt = <0x2d2a80>;
		regulator-allow-set-load;
		regulator-min-microvolt = <0x192d50>;
	};

`pm6125_l5` is `4784000.mmc-vqmmc` and `pm6125_l22` is `4784000.mmc-vmmc` — the
SD-card slot, and nothing else. **Zero rails on this port on any modem-adjacent
path can have their operating mode voted.**

### 1.3 What that costs today, measured

`/sys/kernel/debug/regulator/regulator_summary` on the running device, load
column only, consumers that are enabled and request a non-zero load:

	    l4                            2    2      0 unknown  1232mV
	       5e94000.dsi-vdda           1                                21mA
	       4807000.phy-vdda-pll       1                                18mA
	    l7                            2    2      0 unknown   880mV
	       4807000.phy-vdda-phy       1                                97mA
	    l11                           3    3      0 unknown  1800mV
	       4804000.ufshc-vccq2        1                               800mA
	    l24                           1    1      0 unknown  2960mV
	       4804000.ufshc-vcc          1                               800mA

**Every one of those numbers is bookkeeping.** `pm6125_l4`, `l7`, `l11` and
`l24` have no `regulator-allow-set-load`, so `drms_uA_update()` returned early
and no `ma` key was ever sent for any of them. `regulator_set_load()` records
`regulator->uA_load` *before* calling `drms_uA_update()`, which is why the
summary prints them. The port's own DTS asks for the two 800 mA ones
explicitly:

	vcc-max-microamp = <800000>;
	vccq2-max-microamp = <800000>;

Four more will join them the moment Bluetooth is brought up — the `*` in the
summary marks a load requested by a consumer that is currently disabled:

	       serial0-0-vddrf            0                               300mA*
	       serial0-0-vddch0           0                               450mA*
	       serial0-0-vddxo            0                                80mA*
	       serial0-0-vddio            0                                15mA*

That is eight rails whose HPM/LPM state this port has never voted. **None of
them is on a modem path**, so this is a genuine correctness bug worth fixing
and worth upstreaming, and it is *not* the bug under study. Recorded here so
nobody spends a boot on it expecting a reset to go away.

### 1.4 Per-rail: what stock guarantees against what this port guarantees

Ranked by how plausibly the rail could feed the modem's RF front end. "AP
consumer" means a `*-supply` phandle somewhere in the tree; the stock and port
columns are the *voltage/enable/mode votes the application processor makes*.

| rank | rail | stock resource / range / init | AP consumer, stock | stock AP vote | this port's AP vote | why ranked here |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **`pmr735a_l1`** | `ldoe` id 1, 570000–650000 µV, `qcom,init-voltage = <0x927c0>` (600000) | **none** | `swen=1`, `uv=600000`, **HPM** (62 mA ≥ 10 mA), released at `sync_state` | **nothing at all** | the only proxy-enabled rail in either PMIC with no AP consumer. 600 mV is a PLL/analogue-core voltage, and the PMIC is the SoC's companion, not the camera one |
| 2 | `pmr735a_l6` | `ldoe` id 6, 504000–868000 µV, init 504000 | none | none | none | 0.5 V, unconsumed, on the same PMIC. Nobody on the AP has ever wanted it either side |
| 3 | `pmr735a_l5` | `ldoe` id 5, 751000–824000 µV, init 751000 | none | none | none | as above |
| 4 | `pmr735a_l3` | `ldoe` id 3, 1000000–1200000 µV, init 1128000 | none | none | none | as above |
| 5 | `pmr735a_l4` | `ldoe` id 4, 1504000–2000000 µV, init 1504000 | none | none | none | as above |
| 6 | `pm6125_l12` | `ldoa` id 12, 1620000–2000000 µV, init 1620000 | none | none | none | unconsumed 1.8 V class rail |
| 7 | `pm6125_l15` | `ldoa` id 15, 1650000–3544000 µV, init 1650000 | none | none | none | unconsumed, wide range |
| 8 | `pm6125_l19`, `l20` | `ldoa` id 19/20, 1620000–3300000 µV, init 1620000 | none | none | none | unconsumed, wide range |
| — | `pmr735a_s2_level` (VDD_CX) | `rwcx` id 0, level, init level 384, `qcom,proxy-consumer-voltage = <0x180 0x200>` | `clock-controller@{1400000,5f00000,5990000}`, `qcom,turing@b000000`, **`qcom,mss@06000000`** | level 384 at boot; then TURBO + `ma=100` from PIL (§4) | `rpmpd` at `INT_MAX`, and it has been pinned at `TURBO_NO_CPR` for a whole run — **already eliminated** | included for completeness only |
| — | `pmr735a_s1_level` (VDD_MX) | `rwmx` id 0, level, init level 384, same proxy voltage | clock controllers, DSI PHY | level 384 at boot | `rpmpd`, pinned — **already eliminated** | ditto |
| — | `pm6125_l8`, `l13` | display AVDD/VDDIO | `qcom,dsi-display-primary` | HPM proxy at boot, handed to the panel driver | this port's panel uses `l3`/`l13`/`l4`; display works | display, not modem |
| — | `l4`, `l7`, `l11`, `l24`, `l2`, `l9`, `l16`, `l23` | UFS, PHYs, WCN, DSI | yes | HPM when the consumer asks | **enable and voltage only, mode never voted** | §1.3. Real bug, wrong bug |
| — | `l5`, `l22` | SD-card slot | `sdhci@4784000` | HPM when a card is in | **HPM when a card is in** — the only two rails at parity | the port's two `regulator-allow-set-load` |

**The honest reading of that table.** On the enable axis there is exactly one
difference between the two trees on a rail that could belong to the modem:
`pmr735a_l1`. On the *mode* axis there is a difference on every rail, but it
only has consequences where the AP is the sole voter — see the counterargument
below.

### 1.5 `pmr735a_l1`: what was tested, what was not, and why 0121 is still open

`GNSS-SM6375.md` and `HANDOFF.md` both record `pmr735a_l1` as eliminated,
correctly, with a caveat. The caveat is exactly right and is now confirmed from
both sides of the source:

* `kernel/diag-modules/rhodep_reghold.c` called `regulator_get_optional(NULL, "l1")`,
  `regulator_set_voltage()`, `regulator_set_load(62000)` and `regulator_enable()`.
  The summary showed `l1 1 2 0 unknown 600mV` and a `62mA` consumer row, and
  the reset was unchanged.
* `regulator_set_load()` returned 0 **and sent nothing**, because
  `regulator-allow-set-load` is absent from `pmr735a_l1` and
  `drms_uA_update()` returns early. The `62mA` in the summary is
  `regulator->uA_load`, assigned before the early return.

So **the enable and the voltage were real, and no rail's operating mode has
ever been tested on this port at all.** Not `pmr735a_l1`, not any other.
`kernel/patches/0121-arm64-dts-qcom-rhodep-keep-pmr735a-l1-on-for-the-modem.patch`
is the only thing that can change that, and it needs a boot image.

Two corrections to 0121's own commit message, both from source:

1. It says the vendor proxy holds the rail "for the whole of the vendor
   kernel's life". It does not. `regulator_proxy_consumer_sync_state()` is
   `regulator_proxy_consumer_unregister()`, and
   `regulator_proxy_consumer_remove()` calls `regulator_disable()`,
   `regulator_set_load(reg, 0)` and `regulator_set_voltage(reg, 0, INT_MAX)`.
   (In the legacy build it is a `late_initcall_sync`.) **Stock's hold is a
   boot-window hold.** `regulator-always-on` is therefore a strict superset of
   stock, which is fine for an experiment, but the *stock steady state* is
   "whatever the modem votes for itself" — which is the counterargument 0121
   already states honestly, now with a second reason behind it.
2. It says the patch produces "the same single RPM message the vendor's proxy
   produces on resource ldoe/1: swen=1, uv=600000, ma=62." It produces
   `swen`/`uv`/`ma`. The vendor produces `swen`/`uv`/**`lsmd`**. See §1.7.

### 1.6 The counterargument, stated plainly

RPM aggregates requests **per master**. The modem is an RPM master in its own
right and can vote any resource without the AP's help. For the `ma` key the
aggregation is a sum; for enable it is an OR. Therefore:

* The AP holding a rail enabled with **no** current vote contributes 0 to the
  sum. It **cannot** drag a shared rail into LPM against a modem that is
  voting for HPM.
* The only way the missing mode vote can bite is on a rail that the modem needs
  and **does not vote for itself**, relying on the AP to hold it. That is
  precisely and only the `qcom,proxy-consumer-*` shape, and there is exactly
  one instance of it with no AP consumer: `pmr735a_l1`.

This is the strongest argument against the whole of §1, and it should be
carried into any write-up of the 0121 result. It does not close the branch —
stock demonstrably does something on `pmr735a_l1` that this port does not, and
whether the modem covers for it is unknown — but it bounds the branch to one
rail.

### 1.7 A second-order defect: mainline sends the wrong key for LDOs

Mainline's `rpm_reg_set_load()` sends one key and only one key,
`drivers/regulator/qcom_smd-regulator.c`:

	#define RPM_KEY_MA	0x0000616d /* "ma" */
	...
	if (vreg->load_updated && vreg->is_enabled) {
		req[reqlen].key = cpu_to_le32(RPM_KEY_MA);
		req[reqlen].nbytes = cpu_to_le32(sizeof(u32));
		req[reqlen].value = cpu_to_le32(vreg->load / 1000);
		reqlen++;
	}

The vendor driver for this RPM generation says `ma` is **not a valid key for an
LDO resource**. `params[]` in `rpm-smd-regulator.c`, columns are
`LDO SMPS VS NCP BOB`:

	PARAM(CURRENT,           0,  1,  0,  0,  0, "ma",   0, 0x1FFF,     "qcom,init-current"),
	PARAM(MODE_LDO,          1,  0,  0,  0,  0, "lsmd", 0, 1,          "qcom,init-ldo-mode"),

and `ldo_ops.set_load` accordingly writes the mode, not a current:

	static int rpm_vreg_ldo_set_load(struct regulator_dev *rdev, int load_uA)
	{
		mode = (load_uA + reg->system_load >= reg->rpm_vreg->hpm_min_load)
			? REGULATOR_MODE_NORMAL : REGULATOR_MODE_IDLE;
		rc = _rpm_vreg_ldo_set_mode(rdev, mode);
	}

	#define REGULATOR_MODE_PMIC5_LDO_LPM	4
	#define REGULATOR_MODE_PMIC5_LDO_HPM	7

Only SMPS resources go through `rpm_vreg_get_optimum_mode()` /
`rpm_vreg_set_mode()`, which are the two that touch `RPM_REGULATOR_PARAM_CURRENT`.

**Consequence.** Adding `regulator-allow-set-load` to `pmr735a_l1` is
*necessary* and may not be *sufficient*: it will make mainline send `ma=62` to
resource `ldoe`/1, and the vendor's own table says that resource does not take
`ma`. Whether the RPM firmware on this build ignores it, honours it, or NAKs
the message is **not determinable from source**. Two notes for whoever runs it:

* `rpm_reg_write_active()` returns the result of `qcom_rpm_smd_write()`, which
  waits for the RPM's acknowledgement. If the RPM rejects the message,
  `regulator_set_load()` will return non-zero and `drms_uA_update()` will print
  `failed to set load ...`. **A clean 0 with no error line means the RPM at
  least parsed and accepted the request.** That is the only observable, and it
  should be checked explicitly in the 0121 run rather than assumed.
* There is no read-back. Mainline's `rpm_smps_ldo_ops` has no `.get_mode`,
  which is why the `opmode` column of `regulator_summary` reads `unknown` for
  every rail on this device. This port cannot observe a rail's mode by any
  means available to it.

If 0121 comes back negative, that negative is only as strong as the `ma`/`lsmd`
question. A properly conclusive test would need mainline's RPM regulator driver
taught to emit `lsmd` for LDO resources — a driver change, not a device tree
change.

### 1.8 `regulator_summary` does not report hardware state

Stated separately because it invalidates a reading used repeatedly in
`GNSS-SM6375.md`. Mainline's RPM regulator driver has no read path at all:

	static int rpm_reg_is_enabled(struct regulator_dev *rdev)
	{
		struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);

		return vreg->is_enabled;
	}

`vreg->is_enabled` is `int is_enabled;` in a `devm_kzalloc`'d struct, set only
by `rpm_reg_enable()` and `rpm_reg_disable()`. Nothing reads the PMIC.
Likewise `rpm_reg_get_voltage()` returns `vreg->uV`, which the core initialises
to the constraint floor when nothing votes — which is why an unvoted
`pmr735a_l1` prints `576mV`, the bottom of the port's declared 570000–650000
range, rather than anything the hardware is doing.

So this line, quoted in two documents:

	l1   0  0  0  unknown  576mV   0mA        <- off, at its floor, no users

means **"the application processor does not vote for l1"**. It does not mean
the rail is off, and it is fully consistent with the modem holding the rail at
600 mV in HPM through RPM the whole time. The only way to learn the real state
is to read the PMIC, which this session is forbidden to do and which has killed
the phone before.

---

## 2. What pulls PS_HOLD

### 2.1 What the PON block would distinguish, derived from source

`drivers/input/misc/qpnp-power-on.c` in the vendor kernel is the only decoder
for these registers; mainline has none. Register map for this PMIC generation:

	#define QPNP_PON_REASON1(pon)			/* 0x8C0 */
	#define QPNP_PON_WARM_RESET_REASON1(pon)	/* 0x8C2 */
	#define QPNP_POFF_REASON1(pon)			/* 0x8C5 */
	#define QPNP_PON_OFF_REASON(pon)		((pon)->base + 0xC7)
	#define QPNP_FAULT_REASON1(pon)			((pon)->base + 0xC8)
	#define QPNP_S3_RESET_REASON(pon)		((pon)->base + 0xCA)

`PON_OFF_REASON` (0x8C7) is the selector: bit 7 → the POFF sequence ran, bit 6 →
the FAULT sequence ran, bit 5 → the S3 sequence ran. The reason tables are one
flat array indexed by sequence, `qpnp_poff_reason[]`, verbatim:

	/* QPNP_PON_GEN1 POFF reasons */
	[0] = "Triggered from SOFT (Software)",
	[1] = "Triggered from PS_HOLD (PS_HOLD/MSM Controlled Shutdown)",
	[2] = "Triggered from PMIC_WD (PMIC Watchdog)",
	...
	[12] = "Triggered from TFT (Thermal Fault Tolerance)",
	[13] = "Triggered from UVLO (Under Voltage Lock Out)",
	[14] = "Triggered from OTST3 (Over Temperature)",
	[15] = "Triggered from STAGE3 (Stage 3 Reset)",

	/* QPNP_PON_GEN2 FAULT reasons */
	[16] = "Triggered from GP_FAULT0",
	...
	[20] = "Triggered from MBG_FAULT",
	[21] = "Triggered from OVLO (Over Voltage Lock Out)",
	[22] = "Triggered from UVLO (Under Voltage Lock Out)",
	[23] = "Triggered from AVDD_RB",
	[27] = "Triggered from FAULT_FAULT_N",
	[28] = "Triggered from FAULT_PBS_WATCHDOG_TO",
	[29] = "Triggered from FAULT_PBS_NACK",
	[30] = "Triggered from FAULT_RESTART_PON",
	[31] = "Triggered from OTST3 (Over Temperature)",

**Yes, there is a bit that separates the two cases, and it is the sequence
selector itself.** A hardware fault forcing the shutdown runs the FAULT
sequence and sets `OFF_REASON` bit 6, or the S3 sequence and sets bit 5. A
software-requested shutdown runs the POFF sequence and sets bit 7, with
`POFF_REASON1` bit 1 for PS_HOLD.

The recorded measurement is `off reason 80`, `POFF status 0002`. Bit 7 only,
POFF bit 1 only. **Bits 6 and 5 are clear.** So:

* the PMIC did not detect UVLO, OVLO, MBG fault, over-temperature, `FAULT_N`,
  a PBS watchdog timeout, a PBS NACK, or a stage-3 reset;
* the PMIC did not run its own watchdog (POFF bit 2);
* the SoC deasserted PS_HOLD and the PMIC did what it was told.

**This is an electrical negative and it is a strong one.** Whatever else is
happening, it is not the power-management hardware deciding the SoC has
misbehaved, and it is not an input-rail collapse. That was already recorded in
`HANDOFF.md`; it is re-derived here from the tables rather than from a summary
string, because the whole of this document's framing depends on it.

The limit of that negative: `POFF_REASON` describes VPH_PWR-side and
PMIC-global protection. **A single downstream rail drooping under load does not
appear in any of these registers**, because nothing in the PON block measures a
per-rail output. Per-rail current limiting on a PMIC5 part is handled inside
the PMIC and by RPM firmware, and it manifests as the rail folding back, not as
a PMIC-initiated shutdown. So the negative excludes "the PMIC pulled the plug",
not "a rail sagged and something inside the SoC then asked to be shut down".

### 2.2 Is anything programmed at boot that mainline does not program?

**No.** This is a clean negative and it took a while to establish, so the
working is here.

The enabled PON node in the stock tree, in full — this is the *only* enabled
`qcom,qpnp-power-on` on either PMIC (`pmk8350`'s `pon_hlos@1300` and
`pon_pbs@800` are both `status = "disabled"`):

	//soc/qcom,spmi@1c40000/qcom,pm6125@0/qcom,power-on@800
	qcom,power-on@800 {
		phandle = <0x453>;
		qcom,log-kpd-event;
		qcom,store-hard-reset-reason;
		qcom,system-reset;
		qcom,kpdpwr-sw-debounce;
		qcom,pon-dbc-delay = <0x3d09>;
		interrupt-names = "kpdpwr\0resin";
		interrupts = <0x00 0x08 0x00 0x03 0x00 0x08 0x01 0x03>;
		reg = <0x800>;
		compatible = "qcom,qpnp-power-on";

		qcom,pon_2 { linux,code = <0x72>; qcom,pull-up; qcom,pon-type = <0x01>; };
		qcom,pon_1 { linux,code = <0x74>; qcom,pull-up; qcom,pon-type = <0x00>; };
	};

The vendor driver can program S1/S2 reset timers, S2 type, S3 source, S3
debounce, SMPL, `TRIGGER_EN` and `PULL_CTL`, and can be told to panic on UVLO —
these are the properties it looks for:

	"qcom,s1-timer"  "qcom,s2-timer"  "qcom,s2-type"
	"qcom,s3-src"    "qcom,s3-debounce"
	"qcom,uvlo-panic"  "qcom,support-reset"  "qcom,use-bark"
	"qcom,modem-reset"  "qcom,secondary-pon-reset"

**Not one of them is present in the stock tree.** `qcom,pon-dbc-delay = <0x3d09>`
is 15625 µs of key debounce; `qcom,system-reset` and
`qcom,store-hard-reset-reason` are reset-type and reboot-reason plumbing;
`qcom,log-kpd-event` is logging. The vendor leaves every reset-sequence timer
and every fault threshold at its PBS/boot default.

Mainline covers the same ground with two drivers. `pm8941-pwrkey.c` programs
the same debounce and the same reset type:

	#define PON_PS_HOLD_RST_CTL		0x5a
	#define PON_PS_HOLD_RST_CTL2		0x5b
	#define  PON_PS_HOLD_ENABLE		BIT(7)
	#define  PON_PS_HOLD_TYPE_WARM_RESET	1
	#define  PON_PS_HOLD_TYPE_SHUTDOWN	4
	#define  PON_PS_HOLD_TYPE_HARD_RESET	7
	#define PON_DBC_CTL			0x71

and `qcom-pon.c` writes the reboot reason to `PON_SOFT_RB_SPARE` (0x8f). The
port's tree declares exactly that:

	pon@800 {
		mode-bootloader = <0x02>;
		mode-recovery = <0x01>;
		compatible = "qcom,pm8998-pon";
		reg = <0x800>;
		resin  { debounce = <0x3d09>; ... };
		pwrkey { debounce = <0x3d09>; ... };
	};

Same debounce value, same peripheral. **No over-current, over-temperature,
UVLO or brownout threshold is programmed at boot by either kernel**, because on
an RPM SoC that configuration belongs to RPM firmware and to the PBS sequences
in the PMIC, neither of which the application processor writes. There is
nothing for mainline to be missing.

One near-miss worth recording so it is not re-investigated: the stock tree does
contain over-current thresholds, on `bq2597x-charger@66` —
`ti,bq2597x,bus-ocp-threshold = <0xea6>`, `bat-ocp-threshold = <0x1f40>`,
`sense-resistor-mohm = <0x05>`. That is a TI charge-pump on the charging path,
it only acts while a charger is attached, and it is not populated on this port
(the port uses `bq256xx`/`sgm41542`). It is not in the PS_HOLD path.

There is also **no PMIC BCL node** in the stock tree at all — `grep -i bcl`
returns only `modem_bcl`, which is a QMI thermal client (§4.3), not the PMIC's
battery-current-limiter peripheral. So the AP does not configure BCL in stock
either.

### 2.3 What that leaves

Something inside the SoC deasserted PS_HOLD, no PMIC protection fired, and
Linux did not do it (established in `HANDOFF.md`, "Linux does not ask for the
shutdown"). The remaining actors are TrustZone and RPM firmware. This document
adds nothing to that question; it only removes the electrical branch from it
with a stated derivation instead of an assertion.

---

## 3. Battery and current telemetry

### 3.1 What exists

	/sys/class/power_supply/cw2217-battery/     capacity charge_full_design current_now
	                                            cycle_count health present status temp
	                                            technology voltage_now ...
	/sys/class/power_supply/bq256xx-charger/    charge_type constant_charge_current
	                                            constant_charge_voltage input_current_limit
	                                            input_voltage_limit online status usb_type ...
	/sys/class/power_supply/bq256xx-battery/    charge_full_design charge_term_current ...

There is **no `qcom_battmgr`** on this device and there cannot be: that driver
is for PMIC-glink/RPMh platforms, and this is an RPM platform with a
third-party fuel gauge on I²C.

The only current sensor is `cw2217-battery/current_now`. From patch
`0012-power-supply-add-cellwise-cw2217-fuel-gauge.patch`:

	/*
	 * Current is a signed value across the external sense resistor, with a
	 * step of 1600 uA divided by the shunt resistance in milliohms, and is
	 * positive while the battery is charging.
	 */
	#define CW2217_CURRENT_TO_UA(r, mohm)	((int)(s16)(r) * 1600 / (mohm))

	/* Cache device reads for this long to keep I2C traffic down. */
	#define CW2217_CACHE_MS			2000

The shunt is 5 mΩ, so the LSB is **320 µA**, and the driver caches for **2 s**.
`bq256xx` exposes only setpoints — `input_current_limit` and friends are
configuration, not measurement; this charger has no ADC exposed.

### 3.2 Measured: resolution, rate, noise floor

Refresh interval, sampling `current_now` at 100 ms for 12 s and logging only
changes:

	changes: 5
	(0.007, '0',    '4396875')
	(2.055, '-960', '4396562')
	(4.112, '-320', '4396562')
	(6.179, '-640', '4396562')
	(10.29, '-320', '4396562')

Intervals 2.06 s, 2.06 s, 2.07 s, 4.11 s. That is the 2000 ms cache exactly.
**Effective sample rate 0.49 Hz.** Values move in multiples of 320 µA, as
predicted.

Baseline / load / recovery, all eight cores pegged for 18 s in the middle:

	baseline  n=8 uA=[-640, -1280, -640, -1280, -960, -320, -960, -640]  mean=-840   V=4396913
	8-core    n=6 uA=[-320, -640, -960, -640, 0, -960]                   mean=-587   V=4397083
	recovery  n=7 uA=[-960, 320, -640, 0, -1280, -320, 640]              mean=-320   V=4396785

**Noise floor ±1280 µA peak, σ ≈ 400 µA. Delta from an eight-core load: none
measurable, and the sign is wrong.**

### 3.3 Verdict: not usable for this

Three independent reasons, in order of how fatal they are:

1. **It measures battery current, and the phone is on charge.**
   `bq256xx-charger/online` is `1`, `status` is `Full`, `cw2217` reports
   `capacity 100`, `status Full`, `voltage_now 4397187`. With a full pack and a
   charger attached, essentially nothing flows through the sense resistor
   regardless of what the system draws — which is exactly what the eight-core
   test shows. Every measurement of system current through this path requires
   the charger physically removed, and that is not something this session can
   arrange.
2. **0.49 Hz cannot see a 100 ms event.** The reset happens within 100 ms of
   `QMI_LOC_START` returning. The gauge would produce at most one sample inside
   the entire lifetime of the event, and probably zero.
3. Off charger and in steady state it *would* resolve tens of milliamps —
   320 µA LSB against ~400 µA of noise, so a 20 mA step is 60σ. So the sensor
   is not intrinsically too coarse; it is too slow, and it is measuring the
   wrong node whenever a charger is present.

**Do not build an instrument on `current_now`.** It cannot answer any question
about this bug.

### 3.4 A better instrument, and a result from it

The port instantiates the pm6125 VADC and it has a VPH_PWR channel:

	/sys/bus/iio/devices/iio:device0   1c40000.spmi:pmic@0:adc@3100
	  in_voltage_vph_pwr_input   in_voltage_vph_pwr_label   -> "vph_pwr"
	  in_voltage_vcoin_input     in_voltage_vref_1p25_input   in_voltage_ref_gnd_input

This is an ordinary IIO channel served by the in-tree `qcom-spmi-adc5` driver —
the same peripheral the thermal zones already poll continuously — not a raw
register read.

	reads per second: 735
	baseline vph n=50 mean=4453052.6 sd=272.2 min=4452507 max=4453674
	8-core   vph n=45 mean=4449886.5 sd=523.3 min=4449003 max=4451340
	recovery vph n=40 mean=4453071.9 sd=339.1 min=4452507 max=4453869
	delta under load: -3166 units

Units come out as microvolts (IIO names the attribute `_input`, whose
convention is millivolts, but the value 4453052 against a 4.397 V pack on
charge is unambiguously µV). So:

* **735 samples/s**, 1.36 ms per read — 1500× faster than the fuel gauge.
* Idle noise **σ = 272 µV**; under load σ = 523 µV.
* Eight cores idle→100% costs **−3.17 mV** on VPH_PWR, and the rail returns to
  within 20 µV of baseline when the load is released.

**What that number means.** An eight-core SM6375 going from idle to fully
pegged is, conservatively, several hundred milliamps to about an amp at VPH.
Three millivolts for that step puts the total source impedance from the cell
through the sense resistor and the charger into the low single-digit
milliohms. Scaled down, a receiver drawing a few tens of milliamps would move
VPH by **0.1–0.3 mV**, i.e. under one standard deviation of the idle noise.

Two conclusions:

* **VPH_PWR cannot resolve the GNSS receiver's own load**, so it is not the
  instrument for measuring what the receiver draws.
* **VPH_PWR is a decisive negative for the brownout story.** The supply is
  stiff, the pack is at 4.4 V, VPH sits at 4.45 V, and UVLO on this class of
  part is around 3 V. There is no mechanism by which a load of tens of
  milliamps — or of hundreds — brings this rail near any threshold, and §2.1
  already shows the PMIC recorded no undervoltage. Combined, "the battery could
  not deliver the current" is closed.

The one thing VPH_PWR *would* be good for, and which is left as a suggestion
rather than run here: sampling it at ~700 Hz into a ring buffer through a
reset, to see whether the last 20 ms before the machine stops contain a droop
at all. That needs the sampler's output to reach ramoops, and it must not be
run against a positioning session by anyone who does not own the phone.

---

## 4. What downstream votes at subsystem power-up

### 4.1 `qcom,proxy-timeout-ms` does not do what the brief assumed — branch closed

The stock modem node carries `qcom,proxy-timeout-ms = <0x2710>` (10000 ms), and
it is natural to read that as "stock guarantees the proxy vote for ten seconds
where mainline drops it at the handover interrupt". **That reading is wrong.**
`drivers/soc/qcom/peripheral-loader.c`:

	static void pil_proxy_unvote(struct pil_desc *desc, int immediate)
	{
		...
		if (desc->ops->proxy_unvote) {
			if (WARN_ON(!try_module_get(desc->owner)))
				return;

			if (immediate)
				timeout = 0;

			if (!desc->proxy_unvote_irq || immediate)
				schedule_delayed_work(&priv->proxy,
						      msecs_to_jiffies(timeout));
		}
	}

	static irqreturn_t proxy_unvote_intr_handler(int irq, void *dev_id)
	{
		pil_info(desc, "Power/Clock ready interrupt received\n");
		if (!desc->priv->unvoted_flag) {
			desc->priv->unvoted_flag = 1;
			__pil_proxy_unvote(priv);
		}
	}

The delayed work is only scheduled `if (!desc->proxy_unvote_irq || immediate)`.
The stock modem node **has** that interrupt:

	interrupt-names = "qcom,wdog\0qcom,err-fatal\0qcom,proxy-unvote\0qcom,err-ready\0qcom,stop-ack\0qcom,shutdown-ack";

so the 10 s timeout is a fallback for subsystems without the line, and for the
failure path (`immediate`). In the normal case stock drops the proxy **on the
proxy-unvote interrupt** — which is the same signal mainline calls "handover"
and uses for exactly the same purpose in `qcom_pas_handover()`. The two are
behaviourally identical. Nothing to do.

(Related and already in the tree: patch 0070 masks the handover IRQ after the
first one, because the line keeps transitioning on this board — ~26000 times in
a boot before it was ratelimited. That is a fix for repeated proxy *drops*, in
the same direction as holding the vote longer, and it is already in the running
kernel.)

### 4.2 `qcom,vdd_cx-uV-uA` — the current half really is unexpressible, and it probably does not matter

`subsys-pil-tz.c` parses `qcom,<name>-uV-uA` into a `{uV, uA}` pair and
`enable_regulators()` applies both:

	if (regs[i].uV > 0) {
		rc = regulator_set_voltage(regs[i].reg, regs[i].uV, INT_MAX);
	}

	if (regs[i].uA > 0) {
		rc = regulator_set_load(regs[i].reg, regs[i].uA);
	}
	...
	rc = regulator_enable(regs[i].reg);

The three PIL nodes in the stock tree, verbatim:

	qcom,mss@06000000     qcom,vdd_cx-uV-uA     = <0x180 0x186a0>;   /* 384, 100000 */
	qcom,turing@b000000   qcom,vdd_cx-uV-uA     = <0x180 0x186a0>;   /* 384, 100000 */
	qcom,lpass@a400000    qcom,vdd_lpi_cx-uV-uA = <0x180 0x00>;      /* 384, 0      */
	                      qcom,vdd_lpi_mx-uV-uA = <0x180 0x00>;      /* 384, 0      */

`vdd_cx-supply` on the mss node is `pmr735a_s2_level`, resource `rwcx` id 0,
`qcom,regulator-type = <0x01>` (SMPS), `qcom,hpm-min-load = <0x186a0>`. So the
100 mA request goes through `smps_ops` → `get_optimum_mode()` →
`RPM_VREG_SET_PARAM(reg, CURRENT, 100)` → `ma=100` on `rwcx`, and 100000 is
*exactly* `hpm_min_load`, i.e. the smallest value that forces HPM.

This port cannot express that. `rpmpd` speaks `KEY_LEVEL`/`KEY_ENABLE` and
nothing else, and there is no `vdd_cx-supply` on the port's modem node at all:

	remoteproc@6080000 {
		power-domains = <0x3e 0x00>;
		power-domain-names = "cx";
		clock-names = "xo";
		compatible = "qcom,sm6375-mpss-pas";
		...
	};

with `qcom_pas_pds_enable()` doing `dev_pm_genpd_set_performance_state(pds[i], INT_MAX)`
— a strict superset of stock on the *level* axis (stock asks 384, this asks the
maximum), and nothing at all on the *current* axis.

**Practical consequence: probably none.** CX is voted by the AP's clock
controllers, the CDSP, the display and the modem simultaneously, and the RPM
sums the `ma` key across masters. The AP contributing 0 mA does not reduce the
modem's own contribution. And CX corner pinning has already been tested to
destruction: `rhodep_pdhold.c` held `RPM_SMD_LEVEL_TURBO_NO_CPR` = 416 against
stock's 384, plus `swen=1`, for whole runs, and the reset was unchanged. What
that test did *not* cover is the SMPS operating mode, for the same
`REGULATOR_CHANGE_DRMS` reason as everywhere else — but the aggregation
argument in §1.6 applies with more force here than anywhere, because CX has
many voters.

Recorded as a real, quotable difference with a weak mechanism.

### 4.3 CX iPeak, bus bandwidth floors, thermal and DCVS

**`cx_ipeak` — already closed, and the closure holds.** Every consumer of the
`cx_ipeak` phandle in the stock tree is camera (`qcom,cam-cx-ipeak`, 12 nodes)
or the video codec (`qcom,cx-ipeak-data = <0x13e 0x06>` on
`qcom,vidc@5a00000`). There is no modem client and no GNSS client. It also must
not be probed: `0x3ed000` is inside the same TCSR aperture as `0x3d3000`, where
a plain read takes the SoC down with this bug's exact signature.

**DDR and bus bandwidth floors — nothing for the modem, either side.** The
complete set of bandwidth properties in the stock tree:

	     25 qcom,bus-freq-ddr / qcom,bus-max-ddr / qcom,bus-min-ddr   (display)
	     12 qcom,msm-bus,*                                             (sdhc1, sdhc2, cameras)
	      2 qcom,bus-bw-vectors-bps                                    (sdhc1, sdhc2)
	      1 qcom,bus-table-ddr7                                        (qcom,kgsl-3d0, the GPU)
	      1 qcom,bus-range-kbps                                        (qcom,vidc, the codec)
	      2 qcom,bus-vector-names                                      (ufs, ipa)

The stock `qcom,mss@06000000` node has **no** bus vote, no `interconnects` and
no bandwidth table; the port's `remoteproc@6080000` likewise. The modem
arbitrates its own bandwidth. Nothing to add.

**Thermal and DCVS registration tied to the modem — this port has none, and
stock has a lot of it.** `qmi-tmd-devices` registers the application processor
as a QMI thermal-mitigation client against the modem instance, with nineteen
cooling devices:

	qmi-tmd-devices {
		compatible = "qcom,qmi-cooling-devices";
		modem {
			qcom,instance-id = <0x00>;
			modem_pa            { qcom,qmi-dev-name = "pa"; };
			modem_pa_fr1        { qcom,qmi-dev-name = "pa_fr1"; };
			modem_tj            { qcom,qmi-dev-name = "modem"; };
			modem_bw_backoff    { qcom,qmi-dev-name = "modem_bw_backoff"; };
			modem_current       { qcom,qmi-dev-name = "modem_current"; };
			modem_skin          { qcom,qmi-dev-name = "modem_skin"; };
			modem_bcl           { qcom,qmi-dev-name = "vbatt_low"; };
			modem_charge_state  { qcom,qmi-dev-name = "charge_state"; };
			modem_vdd           { qcom,qmi-dev-name = "cpuv_restriction_cold"; };
			modem_wlan          { qcom,qmi-dev-name = "wlan"; };
			modem_wlan_bw       { qcom,qmi-dev-name = "wlan_bw"; };
			/* + mmw_skin0..3, mmw0..3 — nineteen in all */
		};
		cdsp { ... };
	};

and they are wired into cooling maps on the `pa_therm1`, `pa_therm2` and
`quiet_therm` zones, e.g. `cooling-device = <0x4c 0xfffffffe 0xfffffffe>` for
`modem_pa` and four zones driving `modem_tj` through states 1/2/3. The modem
also publishes its own sensors back:

	qcom,qmi-sensor-names = "pa\0pa_1\0qfe_wtr0\0modem_tsens\0...\0qfe_wtr_pa0\0..."

Mainline has **no `qcom,qmi-cooling-devices` driver**, so this port registers
nothing, receives nothing and commands nothing.

Practical consequence, stated honestly: the modem is never asked to reduce PA
power, back off bandwidth, or limit current. That is a real gap and it will
matter for sustained transmit — but the deaths under study happen within
100 ms of the engine starting, and every one of these mechanisms is a
thermal-timescale feedback loop. `modem_current` and `modem_bcl` are the only
two that are not obviously thermal, and both are *mitigation commands from the
AP to the modem*, not permissions the modem waits for. **Weak. Worth
implementing for a phone, not worth a boot for this bug.**

---

## 5. The external low-noise amplifier — branch closed

The question was whether `gnss_set_ext_lna_api_on`, `gnss_l1_elna_ctrl` and
`gnss_l2_5_elna_ctrl` correspond to an application-processor regulator or GPIO
that this port fails to provide. They do not. Four independent negatives:

1. **The stock AP device tree contains no LNA anything.**

		$ grep -ni "elna|lna|rffe|wtr|qat|qdm|fem" merged.dts
		# every hit is either a thermal sensor label (pa_therm1, pa_therm2,
		# qfe_wtr0, qfe_wtr_pa0 in qcom,qmi-sensor-names), a CoreSight funnel
		# (coresight-funnel-qatb), or a pinctrl node name for those thermistors.

   No `elna`, no LNA supply, no LNA enable GPIO, no LNA pinctrl state.

2. **There is no GNSS or NAV node in the stock AP tree at all**, on either side
   of the A/B slot — established in
   `backups/vendor-dt-rhodep-20260906/README.md` and re-confirmed here. A
   control line for a GNSS eLNA would have to hang off a node that does not
   exist.

3. **The vendor kernel has nothing either.** A full recursive tree listing of
   `Motorola-SM6375-Devs/android_kernel_motorola_sm6375@sixteen` (73669 paths)
   matched on `elna|lna|gnss|gps` only: generic `drivers/gnss/` (sirf, u-blox,
   mtk — none of them Qualcomm), `drivers/soc/qcom/gnsssirf/`, a Garmin USB
   serial driver, and `drm_agpsupport.c`. Nothing Qualcomm-GNSS, nothing eLNA.

4. **The vendor GNSS configuration names no AP-side resource.** From the
   recovered `/vendor/etc`: `gps.conf` (`NMEA_PROVIDER=0` — NMEA comes from the
   modem), `izat.conf`, `sap.conf`, `lowi.conf`, `flp.conf`, `xtwifi.conf`,
   `gnss_antenna_info.conf` (Qualcomm's unpopulated template). No GPIO, no
   regulator, no clock, no reset, no LNA enable in any of them.

There is also no RF PMIC in the AP tree to hang such a rail off. The three
PMICs the AP sees are `pmk8350` (VADC, SDAM, RTC), `pm6125` (the main PMIC) and
`pmr735a` (the SoC companion supplying VDD_MX and VDD_CX). The two I²C
regulator chips, `pm8008@8/@9` and `wl2868c@2F`, are both camera — every one of
their LDOs is a `cam_vio`/`cam_vana`/`cam_vdig`/`cam_vaf` supply.

**Conclusion: `gnss_l1_elna_ctrl` and `gnss_l2_5_elna_ctrl` are modem-owned RF
control lines, driven over the modem's own RFFE/GPIO from inside the MPSS, and
the application processor is not in that path on stock either. This branch is
closed.** A clean negative: nothing this port could add would change it.

---

## 6. Prioritised list, with testability

**P1 — `pmr735a_l1` operating mode, patch 0121.** Stock:
`qcom,proxy-consumer-enable` with `qcom,proxy-consumer-current = <0xf230>` on a
`ldoe`/1 resource whose `qcom,hpm-min-load = <0x2710>`, i.e. HPM at 600 mV,
held from probe to `sync_state`. This port: absent, and the *enable* half has
been tested negative while the *mode* half has never been sent at all
(§1.5). Needs a boot image. Could it cause the reset? Only under the narrow
condition that the modem does not vote this resource for itself (§1.6), and
subject to the `ma`/`lsmd` question (§1.7). **Test it, check
`regulator_set_load()`'s return value explicitly, and do not read the result as
conclusive if it comes back negative.**

**P1b — do the other candidate rails in the same flash.** One boot image can
test all of them; there is no reason to spend one flash per rail. The rails
with no AP consumer in *either* tree, on the SoC companion PMIC, are the ones
worth adding — with the caveat that unlike `pmr735a_l1` **stock does not assert
these either**, so a positive result would be a surprise and a negative one
proves less:

	&rpm_requests {
		regulators-1 {   /* qcom,rpm-pmr735a-regulators */
			pmr735a_l1: l1 { /* as in 0121 */ };

			/* the four other unconsumed pmr735a LDOs. stock's
			 * qcom,init-voltage in the comment. */
			pmr735a_l3: l3 {            /* stock init 1128000 */
				regulator-min-microvolt = <1128000>;
				regulator-max-microvolt = <1128000>;
				regulator-always-on;
				regulator-allow-set-load;
				regulator-system-load = <62000>;
			};
			pmr735a_l4: l4 {            /* stock init 1504000 */ ... };
			pmr735a_l5: l5 {            /* stock init  751000 */ ... };
			pmr735a_l6: l6 {            /* stock init  504000 */ ... };
		};
	};

Cost: five rails held on continuously, tens of milliamps of standby current.
Do not ship it; it is a diagnostic image. If the reset survives all five, the
"a rail the modem needs is unvoted" hypothesis is finished for the LDO axis.

**P2 — fix the discarded loads on `l4`, `l7`, `l11`, `l24` (and the four
Bluetooth rails).** Stock puts them in HPM whenever their consumer asks; this
port never does. Observable without a flash today in `regulator_summary`. It is
a real bug — UFS asking for 800 mA on a rail RPM may be running in LPM is not
harmless — and it is almost certainly **not** this bug, because none of those
rails is on a modem path. Fix it because it is wrong, not because it will make
the reset go away.

**P3 — mainline's RPM regulator driver cannot set an LDO's mode on this RPM
generation.** It sends `ma`; the vendor driver for this exact firmware says
`ma` is SMPS-only for LDO resources and sends `lsmd` instead (§1.7). This is a
driver change, it affects every RPM SoC with PMIC5 LDOs, and it is the only way
to make a `regulator-allow-set-load` result on an LDO conclusive.

**P4 — QMI thermal mitigation client.** Nineteen cooling devices in stock, none
here (§4.3). Implement it for the phone's sake. Weak as an explanation for a
100 ms death.

**Closed, do not re-open without new evidence:**

* The external LNA. Modem-owned, absent from every AP-side source (§5).
* PMIC over-current / UVLO / OVLO / brownout / stage-3 programming. Neither
  kernel programs any of it; the POFF/FAULT/S3 sequence bits show none of it
  fired (§2).
* Battery brownout / VPH collapse. VPH sits at 4.45 V and moves 3.17 mV under a
  full eight-core load; a receiver drawing tens of milliamps is 1–2 orders of
  magnitude below the noise floor of that measurement, and the PMIC recorded no
  UVLO (§2.1, §3.4).
* `qcom,proxy-timeout-ms` as a 10 s guarantee. It is not one; both kernels drop
  the proxy on the same interrupt (§4.1).
* `cx_ipeak`. Camera and video only, and unprobeable (§4.3).
* Bus/DDR bandwidth floors for the modem. Neither tree has any (§4.3).
* `current_now` as an instrument. 0.49 Hz, and it reads zero on charge (§3.3).

## 7. What is not proven

* **Nothing here demonstrates a cause.** Every item in §6 is a difference
  between two device trees. The only one with a plausible mechanism is P1, and
  its mechanism requires an assumption — that the modem relies on the AP to
  hold `pmr735a_l1` — for which there is no evidence either way.
* **Whether the modem votes `ldoe`/1 for itself is unknown and unknowable from
  the AP**, because mainline's RPM regulator driver has no read path (§1.8) and
  reading the PMIC is forbidden. The `576mV / off` reading everyone has been
  quoting is AP bookkeeping, not hardware state.
* **Whether the RPM firmware on this build honours `ma` on an LDO resource is
  unknown** (§1.7). This bounds how much a 0121 negative would be worth.
* **The current draw of the receiver has not been measured** and cannot be with
  the instruments on this device. The `powerMode` ladder is inferred from the
  IDL's own descriptions, not from a measurement, and the open question of
  whether the engine actually runs in modes 3 and 5 is not settled here either.
  Nothing in this document depends on it: every conclusion above is about what
  the device trees do and do not contain, and holds whichever way that
  question falls. If the duty-cycled modes turn out not to start the engine at
  all, the "sustained power" framing loses its evidence and §1 becomes a list
  of correctness bugs rather than a lead — but the list itself does not change.
* **The measurements in §3 were taken on a charging phone at 100 %.** The VPH
  droop figure and the source-impedance estimate derived from it are therefore
  charger-dominated and are an upper bound on stiffness, not a battery
  characterisation. The eight-core current step is an estimate, not a
  measurement; the impedance figure is order-of-magnitude only.
* The device was busy with another session's work throughout. Everything read
  was read-only; nothing was written; no module was loaded; the phone was alive
  at the end.
