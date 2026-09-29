# rhodep — PMIC, regulators, and the rail → consumer map

Primary sources: `holi-regulators-pm6125.dtsi`, `pm6125-rpm-regulator.dtsi`,
`pmr735a-rpm-regulator.dtsi`, `holi-pmic-overlay-pm6125.dtsi`, `blair.dtsi`,
`rhodep-wl2868c.dtsi`, `blair-rhodep-common-overlay.dtsi`.

---

## 1. PMIC lineup (FACT)

| Part | Role | Access |
|---|---|---|
| **PM6125** | main PMIC: S1–S8, L1–L24, GPIOs, ADC_TM, PWM | SPMI |
| **PMR735A** | secondary PMIC: S1–S3, L1–L7, GPIOs | SPMI |
| **PMK8350** | ADC/thermal companion (VADC + ADC_TM) | SPMI |
| **WL2868C** | discrete 7-channel camera LDO | I2C `se7` @ `0x2F` |
| **SGM41542** | charger (`sgm4154x`) | I2C `se8` @ `0x3B` |
| **BQ2597x** | charge pump | I2C `se10` @ `0x66` |
| **CW2217 / SM5602** | fuel gauges | I2C `se8` @ `0x64` / `0x71` |

`PM8008_EN` appears in `holi-pmic-overlay-pm6125.dtsi:421` but **PM8008 is not fitted on
rhodep** — the camera LDO role is taken by WL2868C instead. Do not port PM8008.

### Naming: three aliases for the same node

Downstream uses up to three labels per regulator:

```
L9A : pm6125_l9 : regulator-l9 { ... }
^^^   ^^^^^^^^^   ^^^^^^^^^^^^
board  driver      node name
alias  handle
```

`L<N>A` = PM6125, `L<N>E` = PMR735A. Both forms appear in consumer references, so grepping
for only one form will miss consumers. (This is why `blair-rhodep-common-overlay.dtsi:283`
uses `&pm6125_l9` while `:71` uses `&L9A` — same rail.)

---

## 2. Regulator table (FACT — values from `holi-regulators-pm6125.dtsi`)

### PM6125

| Alias | Handle | RPM res | min µV | max µV | init µV | Flags |
|---|---|---|---|---|---|---|
| — | `pm6125_s1` | `rwmx[0]` | — | — | — | VDD_APC |
| — | `pm6125_s3` | `rwcx[0]` | — | — | — | VDD_CX |
| S5A | `pm6125_s5` | `rwmx[0]` | 382000 | 1120000 | 952000 | VDD_MX |
| S6A | `pm6125_s6` | `smpa[6]` | 382000 | 1374000 | 1352000 | |
| S7A | `pm6125_s7` | `smpa[7]` | 1574000 | 2040000 | 2040000 | |
| S8A_LEVEL | `pm6125_s8_level` | `smpa[8]` | level | level | LOW_SVS | VDD_GX |
| L1A_LEVEL | `pm6125_l1_level` | `ldoa[1]` | level | level | — | LPI_CX |
| L2A | `pm6125_l2` | `ldoa[2]` | 1170000 | 1304000 | 1170000 | |
| L3A | `pm6125_l3` | `ldoa[3]` | 1100000 | 1300000 | 1100000 | |
| L4A | `pm6125_l4` | `ldoa[4]` | 1100000 | 1300000 | 1232000 | |
| L5A | `pm6125_l5` | `ldoa[5]` | 1650000 | 3050000 | 2960000 | |
| L6A | `pm6125_l6` | `ldoa[6]` | 1080000 | 1304000 | 1080000 | |
| L7A | `pm6125_l7` | `ldoa[7]` | 788000 | 1050000 | 880000 | |
| L8A | `pm6125_l8` | `ldoa[8]` | 1100000 | 1304000 | 1200000 | **PROXY 857 mA** |
| L9A | `pm6125_l9` | `ldoa[9]` | 1504000 | 2000000 | 1800000 | |
| L10A | `pm6125_l10` | `ldoa[10]` | 1620000 | 1980000 | 1800000 | |
| L11A | `pm6125_l11` | `ldoa[11]` | 1620000 | 1980000 | 1800000 | |
| L12A | `pm6125_l12` | `ldoa[12]` | 1620000 | 2000000 | 1620000 | |
| L13A | `pm6125_l13` | `ldoa[13]` | 1650000 | 1980000 | 1650000 | **PROXY 62 mA** |
| L14A | `pm6125_l14` | `ldoa[14]` | 1700000 | 1900000 | 1700000 | |
| L15A | `pm6125_l15` | `ldoa[15]` | 1650000 | 3544000 | 1650000 | |
| L16A | `pm6125_l16` | `ldoa[16]` | 1620000 | 1980000 | 1620000 | |
| L17A_LEVEL | `pm6125_l17_level` | `ldoa[17]` | level | level | — | LPI_MX |
| L18A | `pm6125_l18` | `ldoa[18]` | 830000 | 920000 | 880000 | |
| L19A | `pm6125_l19` | `ldoa[19]` | 1620000 | 3300000 | 1620000 | |
| L20A | `pm6125_l20` | `ldoa[20]` | 1620000 | 3300000 | 1620000 | |
| L21A | `pm6125_l21` | `ldoa[21]` | 3000000 | 3400000 | 3000000 | |
| L22A | `pm6125_l22` | `ldoa[22]` | 2700000 | 3544000 | 2960000 | |
| L23A | `pm6125_l23` | `ldoa[23]` | 3000000 | 3400000 | 3000000 | |
| L24A | `pm6125_l24` | `ldoa[24]` | 2700000 | 3544000 | 2960000 | |

### PMR735A

| Alias | Handle | RPM res | min µV | max µV | init µV | Flags |
|---|---|---|---|---|---|---|
| S1E_LEVEL | `pmr735a_s1_level` | `smpe[1]` | level | level | **TURBO** | **PROXY**, VDD_MX |
| S2E_LEVEL | `pmr735a_s2_level` | `smpe[2]` | level | level | **TURBO** | **PROXY**, VDD_CX |
| — | `pmr735a_s3` | `smpe[3]` | — | — | — | |
| L1E | `pmr735a_l1` | `ldoe[1]` | 570000 | 650000 | 600000 | PROXY 62 mA |
| L2E | `pmr735a_l2` | `ldoe[2]` | 360000 | 752000 | 704000 | |
| L3E | `pmr735a_l3` | `ldoe[3]` | 900000 | 1200000 | 1128000 | |
| L4E | `pmr735a_l4` | `ldoe[4]` | 1504000 | 2000000 | 1504000 | |
| L5E | `pmr735a_l5` | `ldoe[5]` | 751000 | 824000 | 751000 | |
| L6E | `pmr735a_l6` | `ldoe[6]` | 504000 | 868000 | 504000 | |
| L7E | `pmr735a_l7` | `ldoe[7]` | 2700000 | 3300000 | 3080000 | |

> **Important:** VDD_CX and VDD_MX are reached through the **PMR735A `_level` regulators**
> (`S2E_LEVEL` → `vdd_cx` at `blair.dtsi:2917`; `S1E_LEVEL` → `vdd_mx` at `blair.dtsi:1250`),
> both held at **TURBO** by `qcom,proxy-consumer-enable`
> (`holi-regulators-pm6125.dtsi:697,755`). The PM6125 `rwcx`/`rwmx` entries are the same RPM
> resources seen from the other PMIC and are left disabled.

---

## 3. Rail → consumer map (FACT, rhodep chain only)

This is the table to write your DTS from. Every entry was produced by resolving
`*-supply = <&rail>` across the 56-file chain — nothing else in the repo was considered,
so there are no cross-device false positives.

| Rail | V | Consumers |
|---|---|---|
| **L2A** | 1.17–1.30 | WCN3990 `3-rfa`, BT `vdd-core` (`blair.dtsi:3691,3711`) |
| **L3A** | 1.10–1.30 | **panel DVDD** (`rhodep-display.dtsi:108`) |
| **L4A** | 1.10–1.30 | UFS `vdda-pll`, UFS `vddp-ref-clk`, USB `core`, **CSI `mipi-csi-vdd2`** (`blair-camera.dtsi:129`) |
| **L5A** | 1.65–3.05 | SDHC2 `vdd-io` |
| **L7A** | 0.788–1.05 | UFS `vdda-phy`, USB PHY `vdd`, **CSI `mipi-csi-vdd1`** (`blair-camera.dtsi:128`) |
| **L9A** | 1.50–2.00 | codec `cdc-vdd-rxtx`, `cdc-vddpx`, BT `vdd-io`, SAR `cap_vdd` |
| **L10A** | 1.62–1.98 | USB `vdda18` |
| **L11A** | 1.62–1.98 | SDHC1 `vdd-io` (always-on), UFS `vccq2`, codec `cdc-vdd-rxtx/vddpx` |
| **L13A** | 1.65–1.98 | **panel VDDIO** (`rhodep-display.dtsi:107`) |
| **L14A** | 1.70–1.90 | codec `cdc-vdd-buck` |
| **L16A** | 1.62–1.98 | WCN3990 `8-xo`, BT `vdd-xtal` |
| **L18A** | 0.83–0.92 | USB `vdd` (`blair-usb.dtsi:121`) |
| **L21A** | 3.0–3.4 | WCN3990 `3-ch1`, **front camera `cam_vdig`** |
| **L22A** | 2.70–3.544 | SDHC2 `vdd` |
| **L23A** | 3.0–3.4 | WCN3990 `3-ch0`, BT `vdd-pa` |
| **L24A** | 2.70–3.544 | SDHC1 `vdd`, **UFS `vcc`** |
| **S5A** | 0.382–1.12 | WCN3990 `vdd-smps`, BT `vdd-smps` |
| **S6A** | 0.382–1.374 | PM6125 `vdd_l1_l2`, **WL2868C `vin1`** (`rhodep-wl2868c.dtsi:25`) |
| **S7A** | 1.574–2.04 | PM6125 `vdd_l6` |
| **L2E** | 0.36–0.752 | WCN3990 `vdd-cx-mx` |
| **L7E** | 2.70–3.30 | USB `vdda33` |
| **S1E_LEVEL** | level | `vdd_mx` |
| **S2E_LEVEL** | level | `vdd_cx`, GPU parent |
| **S8A_LEVEL** | level | `vdd_gx` (GPU) |
| **L1A_LEVEL / L17A_LEVEL** | level | LPASS `vdd_lpi_cx` / `vdd_lpi_mx` |
| `panel_vci` | 3.0 fixed | **panel VCI**, GPIO-controlled via `&tlmm 46` |

### GDSCs consumed

`gcc_ufs_phy_gdsc` (UFS hba), `gcc_usb30_prim_gdsc` (USB), `gcc_camss_top_gdsc` (CAMSS +
every camera `cam_clk`), `gcc_venus_gdsc` / `gcc_vcodec0_gdsc` (video), `gpu_cx_gdsc` /
`gpu_gx_gdsc`, `mdss_core_gdsc`, plus SMMU TBU vote GDSCs in `msm-arm-smmu-holi.dtsi`.

---

## 4. WL2868C — the camera LDO (FACT, `rhodep-wl2868c.dtsi`)

```
wl2868c@0x2F on qupv3_se7_i2c        compatible = "semi,wl2868c"
  vin1-supply   = <&S6A>
  semi,cs-gpios = <&tlmm 33 1>        (enable/chip-select, bias-pull-up, output-high)
  pinctrl-0     = <&pmic_en_default>  (gpio33, drive-strength 2)

  ldo1  0.6 – 1.8 V     ldo5  1.2 – 4.3 V
  ldo2  0.6 – 1.8 V     ldo6  1.2 – 4.3 V
  ldo3  1.2 – 4.3 V     ldo7  1.2 – 4.3 V
  ldo4  1.2 – 4.3 V
```

Which camera rail each output feeds is resolved in `03-camera-flash.md` §2.

**NOT IN SOURCES:** the WL2868C register map. No `wl2868c` driver exists under
`motorola-kernel-modules` at tag `S1SUS32.73-13-4-3` (searched; only `sensors_class.c`
is present under `drivers/sensors/`). The ordering above is from the DT node order, which
is the driver's index order, but the I2C register offsets are not in this corpus.

---

## 5. PMIC GPIO and ADC assignments (FACT)

| Resource | Use |
|---|---|
| `pmr735a_gpios 1` | volume-up key (`GPIO_ACTIVE_LOW`, `KEY_VOLUMEUP`, wakeup, debounce 15) |
| `pmr735a_gpios 2` | **display panel reset** |
| `pm6125_gpios 8` | **camera flash PWM** (`function = "func1"`, drive-strength 3) |
| `pm6125_pwm 0` | PWM source for the flash |

ADC/thermal channel assignments are tabulated in `01-hardware-map.md` §5.

---

## 6. Mainline status

Mainline `sm6375-motorola-rhodep.dts` already declares `qcom,rpm-pm6125-regulators` and
`qcom,rpm-pmr735a-regulators`, so the RPM regulator framework is in place and the
RPM resource names map 1:1 (`s5`→`pm6125_s5`, `l9`→`pm6125_l9`, …).

Gaps are enumerated and ranked in `09-mainline-gaps.md`; the regulator-specific ones are:

- `panel_vci` fixed regulator **is** present mainline (`:135`) — good.
- **WL2868C is entirely absent** mainline → all four cameras have no analog/digital rails.
- PM6125 PWM / `pm6125_gpios 8` absent → no flash LED path.
- Proxy-consumer semantics (L8A 857 mA, L13A 62 mA, S1E/S2E TURBO) have no direct mainline
  equivalent; mainline relies on the RPM's own boot votes. This is correct for CX/MX
  (see `05-modem-rf-gnss.md`) but means L8A/L13A are **not** held up early in boot.
