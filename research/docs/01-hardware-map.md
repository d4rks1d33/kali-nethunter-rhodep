# rhodep — master hardware map

Source of truth: `motorola/devicetree-A12-S1SUS32.73-13-4-3/qcom` (+ `camera-dt-A12-...`),
tag `S1SUS32.73-13-4-3`. Paths below are relative to that `qcom/` dir unless stated.

---

## 0. Reproducing the include chain

```python
import os,re
idx={}
for base in ['.','/opt/postmarket/research/motorola/camera-dt-A12-S1SUS32.73-13-4-3']:
    for dp,_,fs in os.walk(base):
        if '.git' in dp: continue
        for f in fs: idx.setdefault(f, os.path.join(dp,f))
stack=['blair-moto-rhodep-base.dts','blair-rhodep-common-overlay.dtsi','blair-rhodep-dvt2-overlay.dts']
done=set()
while stack:
    p=idx.get(os.path.basename(stack.pop()))
    if not p or p in done: continue
    done.add(p)
    stack += re.findall(r'#include\s+"([^"]+)"', open(p,errors='ignore').read())
# -> 56 files
```

---

## 1. Top-level block diagram

```
                          ┌──────────────────────────┐
                          │   SM6375 "blair"         │
                          │   2x A78 + 6x A55        │
                          │   Adreno 619 (GMU wrapper)│
                          └────────────┬─────────────┘
                                       │
   ┌──────────┬──────────┬─────────────┼───────────┬────────────┬──────────┐
   ▼          ▼          ▼             ▼           ▼            ▼          ▼
 MPSS      ADSP       CDSP          CAMSS        MDSS        LPASS      IPA/GSI
(modem)  (sensors+  (compute)    4x CSIPHY    DSI0 +7nm    SoundWire   (LTE data
  │       audio)        │         CCI0/CCI1     PHY         + MI2S      path)
  │          │          │             │           │            │          │
  │          │          │             ▼           ▼            ▼          │
  │          │          │        4 cameras    NT37701       WCD9370       │
  │          │          │        + PWM flash   AMOLED      + 2x AW882XX   │
  │          │          │                     120 Hz DSC                  │
  ▼          ▼          ▼                                                 ▼
 RF front-end (opaque: driven by dedicated MSS pads, not TLMM — see 05)  rmnet

                          ── power / control plane ──
   PM6125 (main PMIC, SPMI)      PMR735A (2nd PMIC)     PMK8350 (ADC/therm)
        │  L1..L24, S1..S8            L1E..L7E, S1E..S3E       VADC + ADC_TM
        └── plus discrete: WL2868C (camera 7x LDO, I2C 0x2F)
                           SGM41542 (charger, I2C 0x3B)
                           CW2217 / SM5602 (fuel gauges)
                           SGM7220 or RT1711H (Type-C, second-sourced)
```

---

## 2. Bus topology — what is on which controller

**FACT.** QUP instances and their pin groups (`holi-qupv3.dtsi`, `blair-pinctrl.dtsi`):

| SE | Base addr | Pins | Pin function | Role on rhodep |
|---|---|---|---|---|
| `qupv3_se0_spi` | `0x4a80000` | gpio0–3 | `qup00` | **Goodix GT9916 touchscreen** |
| `qupv3_se1_4uart` | `0x4a84000` | gpio61 | `gpio` | (debug/unused) |
| `qupv3_se2_i2c/spi` | `0x4a88000` | gpio45,46 / +56,57 | `qup02` | unused on rhodep |
| `qupv3_se6_i2c/spi` | `0x4c80000` | gpio13,14 / +15,16 | `qup10` | unused on rhodep |
| `qupv3_se7_i2c` | `0x4c84000` | gpio27,28 | `qup11_f1` | **NFC + SAR + camera PMIC** |
| `qupv3_se8_i2c` | `0x4c88000` | gpio19,20 | `qup12` | **Charger + gauges + Type-C** |
| `qupv3_se9_2uart` | `0x4c8c000` | gpio25,26 | `qup13_f2` | console UART |
| `qupv3_se10_i2c` | `0x4c90000` | gpio4,5 | `qup14` | **Audio smart PAs + charge pump** |

### Device inventory per bus (FACT)

```
qupv3_se0_spi  (gpio0-3)
  └── goodix_ts_spi@0        Goodix GT9916S touchscreen   [rhodep-touchscreen.dtsi]

qupv3_se7_i2c  (gpio27/28)
  ├── sec-nfc@0x27           Samsung NFC                   [blair-rhodep-common-overlay.dtsi:236]
  ├── sx937x@0x2c            Semtech SAR (label sx937x_se7)[blair-rhodep-common-overlay.dtsi]
  └── wl2868c@0x2F           Semi WL2868C 7x camera LDO    [rhodep-wl2868c.dtsi:16]

qupv3_se8_i2c  (gpio19/20)
  ├── sgm4154x@0x3B          SGM41542 charger              [discrete_charging_rhodep.dtsi]
  ├── bq25890@0x6A           alt charger (2nd source)      [discrete_charging_rhodep.dtsi]
  ├── cw2217@0x64            CellWise fuel gauge           [blair-rhodep-common-overlay.dtsi:36]
  ├── sm5602@0x71            SiliconMitus fuel gauge (alt) [blair-rhodep-common-overlay.dtsi:50]
  ├── sgm7220@0x47           SGMicro Type-C                [sgm7220.dtsi:16]
  └── rt1711h@0x4e           Richtek Type-C (2nd source)   [tcpc_config.dtsi:17]

qupv3_se10_i2c (gpio4/5)
  ├── aw882xxacf_smartpa@0x34       Awinic speaker amp     [blair-audio-...-dual-overlay.dtsi]
  ├── aw882xxacf_recv_smartpa@0x35  Awinic receiver amp    [blair-audio-...-dual-overlay.dtsi]
  ├── bq2597x@0x66                  charge pump            [discrete_charging_rhodep.dtsi]
  └── sx937x@0x2c                   SAR (common overlay variant)
```

**INFERENCE.** Where two parts share a bus+IRQ (`sgm7220@0x47` vs `rt1711h@0x4e`;
`sgm4154x@0x3B` vs `bq25890@0x6A`), these are *second-source alternates*: Motorola shipped
both BOM variants and the drivers probe whichever ACKs. `HARDWARE-FACTS.md` confirms the
retail unit is **SGM41542**, so the SGMicro branch is the live one — which makes **SGM7220**
the likely Type-C part (and SGM7220 is a TI **TUSB320** register clone, which matters for
mainline — see `08-usb-typec-power.md`).

---

## 3. TLMM GPIO map (rhodep chain only)

**FACT** — every `<&tlmm N ...>` reference resolved from the 56-file chain:

| GPIO | Function | Source |
|---|---|---|
| 0,1,2,3 | SPI0 (touchscreen) | `rhodep-touchscreen.dtsi` |
| 4,5 | I2C se10 (audio PA) | `blair-pinctrl.dtsi` |
| 6 | fingerprint `vcc_en` / charger irq | `rhodep-fps-overlay.dtsi` |
| 7 | **NFC CLK_REQ** | `blair-rhodep-common-overlay.dtsi:239` |
| 8 | **NFC FIRM (download mode)** | `blair-rhodep-common-overlay.dtsi:238` |
| 9 | **NFC IRQ** | `blair-rhodep-common-overlay.dtsi:240` |
| 11 | **Type-C interrupt** (sgm7220 / rt1711h) | `sgm7220.dtsi:26` |
| 12 | charger IRQ (bq2589x) | `discrete_charging_rhodep.dtsi` |
| 17 | fingerprint IRQ | `rhodep-fps-overlay.dtsi` |
| 18 | fingerprint RESET | `rhodep-fps-overlay.dtsi` |
| 19,20 | I2C se8 | `blair-pinctrl.dtsi` |
| 21 | **touchscreen RESET** | `rhodep-touchscreen.dtsi` |
| 22 | **touchscreen IRQ** | `rhodep-touchscreen.dtsi` |
| 23 | **display TE** | `rhodep-display.dtsi` |
| 24 | SAR sx937x nIRQ | `blair-rhodep-common-overlay.dtsi` |
| 25,26 | console UART | `blair-pinctrl.dtsi` |
| 27,28 | I2C se7 | `blair-pinctrl.dtsi` |
| 30 | **CAMIF_MCLK1** (main cam) | `blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi:453` |
| 31 | **CAMIF_MCLK0 / MCLK2** | camera overlay |
| 32 | **CAMIF_MCLK3** | camera overlay |
| 33 | **WL2868C chip-select/enable** | `rhodep-wl2868c.dtsi:19` |
| 34 | CAM_FW_RST (front) | camera overlay |
| 35 | CAM_RM_RST (rear main) | camera overlay |
| 36,37 | CAM_RW_RST (wide/macro) | camera overlay |
| 39,43 | camera (blair-camera.dtsi) | `blair-camera.dtsi` |
| 46 | **panel VCI enable** (fixed regulator) | `rhodep-display.dtsi:90` |
| 48 | **NFC VEN** | `blair-rhodep-common-overlay.dtsi:237` |
| 49 | **camera flash CUSTOM_GPIO1** | camera overlay |
| 59 | **AW882XX speaker IRQ** | audio overlay |
| 60 | **AW882XX receiver IRQ** | audio overlay |
| 69 | **BT SW_CTRL (antenna/coex)** | `blair.dtsi` |
| 84 | charger IRQ | `discrete_charging_rhodep.dtsi` |
| 86 | camera (OIS/custom) | camera overlay |
| 92 | touchscreen AVDD enable | `rhodep-touchscreen.dtsi` |
| 94 | SD card detect (`cd-gpios`) | `blair-rhodep-common-overlay.dtsi:210` |
| 100 | **vibrator LDO enable** | `blair-rhodep-common-overlay.dtsi` |
| 156 | (blair.dtsi reset-gpios) | `blair.dtsi` |

### Non-TLMM GPIOs (PMIC) — easy to miss

| Pin | Function | Source |
|---|---|---|
| `pmr735a_gpios 1` | **volume-up key** | `blair-rhodep-common-overlay.dtsi` |
| `pmr735a_gpios 2` | **display panel RESET** | `rhodep-display.dtsi:129` |
| `pm6125_gpios 8` (`func1`) | **camera flash PWM output** | `blair-camera-flash-pwm.dtsi:4` |

> The panel reset and the flash LED are **not on the TLMM**. Any port that assumes
> TLMM for these will silently fail.

---

## 4. Storage

**FACT** (`blair-rhodep-common-overlay.dtsi:178-233`):

```
UFS   ufshc_mem   vcc=L24A (2.95-2.96V, 800mA)  vccq2=L11A (1.8V, 800mA)
      ufsphy_mem  compatible="qcom,ufs-phy-qmp-v4-yupik"
                  vdda-phy=L7A (85.7mA)  vdda-pll=L4A (18.3mA)
                  vddp-ref-clk=L4A (100uA)   vdd-hba=gcc_ufs_phy_gdsc
SDHC1 vdd=L24A (2.96V) vdd-io=L11A (1.8V, always-on, lpm-sup)
SDHC2 vdd=L22A (2.96V) vdd-io=L5A (1.8-2.96V)  cd-gpios=<&tlmm 94>
```

---

## 5. Thermal sensor placement (tells you where the RF/PA hardware physically is)

**FACT** (`blair-rhodep-common-overlay.dtsi:430-530`):

| Zone | ADC channel | Meaning |
|---|---|---|
| `chg_therm` | `pmk8350 ADC7_AMUX_THM2_100K_PU` | charger |
| `usb_conn_therm` | `pmk8350 ADC7_AMUX_THM4_100K_PU` | USB-C connector |
| `msm_therm` | `pm6125_adc_tm_iio ADC5_GPIO1_100K_PU` | SoC skin |
| `rfc_camera_therm` | `pm6125_adc_tm_iio ADC5_GPIO2_100K_PU` | camera/RF corner |
| `cam_flash_therm` | `pm6125_adc_tm ADC5_GPIO3_100K_PU` | **flash LED** |
| `pa_therm1` | `pm6125_adc_tm ADC5_GPIO4_100K_PU` | **RF power amplifier 1** |
| `pa_therm2` | `pm6125_adc_tm ADC5_AMUX_THM1_100K_PU` | **RF power amplifier 2** |
| `quiet_therm` | `pm6125_adc_tm ADC5_AMUX_THM2_100K_PU` | chassis |

**INFERENCE.** Two independent PA thermistors ⇒ two PA/FEM clusters. Combined with the SAR
antenna decode in `05-modem-rf-gnss.md`, this is the strongest available evidence for the RF
front-end layout, since none of it is exposed as Linux peripherals.

Note that a `cam_flash_therm` zone exists at all — the flash is thermally managed in the
downstream stack. That is directly relevant to the "flash turns off after seconds" symptom
(see `03-camera-flash.md`).
