# rhodep — cameras and flash LED

Source: `motorola/camera-dt-A12-S1SUS32.73-13-4-3/blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi`
(production revision) and `blair-camera.dtsi`, `blair-camera-flash-pwm.dtsi`.

---

## 1. Sensor topology (FACT)

Four `qcom,cam-sensor` nodes. Everything below is quoted from the DVT2 overlay.

| Node | cell-index | CSIPHY | CCI master | MCLK | MCLK GPIO | RESET GPIO | Role |
|---|---|---|---|---|---|---|---|
| `qcom,cam-sensor@0` | 0 | **0** | 1 | `GCC_CAMSS_MCLK1_CLK` @24 MHz | `tlmm 30` (CAMIF_MCLK1) | `tlmm 35` (CAM_RM_RST) | **rear main 50 MP** |
| `qcom,cam-sensor@1` | 1 | **3** | 0 | `GCC_CAMSS_MCLK0_CLK` @24 MHz | `tlmm 31` (CAMIF_MCLK0) | `tlmm 34` (CAM_FW_RST) | **front 16 MP** |
| `qcom,cam-sensor@2` | 2 | **2** | 0 | `GCC_CAMSS_MCLK3_CLK` @24 MHz | `tlmm 32` (CAMIF_MCLK3) | `tlmm 37` (CAM_RW_RST) | **macro 2 MP** |
| `qcom,cam-sensor@3` | 3 | **1** | 0 | `GCC_CAMSS_MCLK2_CLK` @24 MHz | `tlmm 31` (CAMIF_MCLK2) | `tlmm 36` (CAM_RW_RST) | **ultrawide 8 MP** |

Orientation (`sensor-position-roll/pitch/yaw`):
- rear main / macro / ultrawide: roll 90, pitch 0, **yaw 180**
- front: roll 270, pitch 0, **yaw 0**

Companions:
- `qcom,actuator@0` — CCI master 1, VAF from `cam_pmic_ldo3`, VIO from `cam_pmic_ldo7` (main cam AF)
- `ois_main` referenced by sensor@0 via `ois-src` → **the main camera has OIS**
- `eeprom@0..3` — one per module, all VIO on `cam_pmic_ldo7`

---

## 2. Power tree — resolved (FACT)

```
S6A ──► WL2868C (I2C se7 @0x2F, enable = tlmm 33)
         │
         ├── ldo1  0.6-1.8V  ──► cam0 (rear main)  cam_vdig
         ├── ldo2  0.6-1.8V  ──► cam3 (ultrawide)  cam_vdig   [also eeprom@3]
         ├── ldo3  1.2-4.3V  ──► actuator          cam_vaf
         ├── ldo4  1.2-4.3V  ──► cam0 (rear main)  cam_vana
         ├── ldo5  1.2-4.3V  ──► cam2 (macro)      cam_vana   [also eeprom@2/@3]
         ├── ldo6  1.2-4.3V  ──► cam1 (front)      cam_vana
         └── ldo7  1.2-4.3V  ──► ALL sensors       cam_vio  (1.8 V)

L21A (3.0-3.4V) ──► cam1 (front) cam_vdig      ← NOT from WL2868C
cam_ois_ldo     ──► actuator cam_v_custom1
gcc_camss_top_gdsc ──► every sensor's cam_clk
L7A ──► mipi-csi-vdd1   L4A ──► mipi-csi-vdd2   (blair-camera.dtsi:128-129)
```

Per-sensor `rgltr-min/max-voltage` + `rgltr-load-current` triples (order = vio, vana, vdig):

| Sensor | VIO | VANA | VDIG | loads (µA) |
|---|---|---|---|---|
| cam0 rear main | 1.800–1.805 V | 2.800–2.805 V | **1.050–1.100 V** | 120000 / 80000 / **1200000** |
| cam1 front | 1.800–1.805 V | 2.800–2.805 V | **2.500 V** | 120000 / 80000 / 2500000 |
| cam2 macro | 1.800–1.805 V | 2.800–2.805 V | — | 120000 / 80000 / 0 |
| cam3 ultrawide | 1.800–1.805 V | 2.800–2.805 V | **1.200–1.205 V** | 120000 / 80000 / 1200000 |

This is the exact `CAM0 ├── AVDD/DVDD/IOVDD` breakdown requested, with the caveat that
Qualcomm's naming is `cam_vana` = AVDD, `cam_vdig` = DVDD, `cam_vio` = IOVDD.

---

## 3. Sensor part numbers — NOT IN SOURCES

The Qualcomm CAMSS DT does **not** name sensors; probing is done by the userspace sensor
library against `cam_sensor` slave-address/ID tables in vendor blobs.

Searched and found nothing usable:
- `blair-camera-sensor-mot-rhodep-dvt2-overlay.dtsi` — no `sensor-name`, no I2C slave addr,
  no chip-id property.
- `techpack-camera-A13` + `motorola-kernel-modules` — grep for `imx###|s5k####|ov####|hi####|gc####`
  returns only `gc7371/gc7271/gc7202/gc1546`, which are unrelated Galaxycore parts in
  generic code, not rhodep bindings.

**To obtain them you need the vendor blobs**, not the kernel sources — specifically
`/vendor/lib64/camera/com.qti.sensormodule.*.so` or the `camera_config` XMLs from a rhodep
firmware image. Marketing specs (50/8/2/16 MP) do not determine the part number.

**INFERENCE (low confidence, do not code against):** the `cam_vdig` values are a strong
fingerprint — 1.05 V for a 50 MP main and 1.2 V for the 8 MP ultrawide are typical of
Samsung ISOCELL / OmniVision parts, and the presence of OIS + a dedicated actuator on the
main module narrows it further. But this is not determinable from the corpus.

---

## 4. Flash LED — this is your bug (FACT)

`camera-dt-A12-S1SUS32.73-13-4-3/blair-camera-flash-pwm.dtsi` in full:

```dts
&pm6125_gpios {
    camera_flash_pwm {
        camera_flash_pwm_default: camera_flash_pwm_default {
            pins = "gpio8";
            function = "func1";     /* <-- PWM alternate function, NOT plain GPIO */
            input-disable;
            output-low;
            bias-disable;
            power-source = <0>;
            drive-strength = <3>;
        };
    };
};

&soc {
    pm6125_flash_gpio: pm6125_flash_gpio {
        compatible = "qualcomm,pm6125_flash_gpio";
        pinctrl-names = "camera_flash_pwm_default";
        pinctrl-0 = <&camera_flash_pwm_default>;
        pwms = <&pm6125_pwm 0 0>;      /* <-- PWM channel 0 */
    };
};
```

And the kernel config confirms the variant selection
(`kernel-msm/arch/arm64/configs/vendor/ext_config/moto-holi-rhodep.config`):

```
CONFIG_CAMERA_FLASH_PWM=y
```

The flash is additionally referenced from each camera node as
`qcom,camera-flash@N { gpios = <&tlmm 49 0>; gpio-req-tbl-label = "CUSTOM_GPIO1"; }`.

### Why "it lights up then turns off after a few seconds"

**FACT:** the flash is **not** a simple enable-GPIO LED. It is a **PWM-driven current
source on PM6125 GPIO8 in alternate function `func1`**, plus a strobe/enable on `tlmm 49`.
There is also a dedicated thermal zone `cam_flash_therm` on `pm6125_adc_tm ADC5_GPIO3_100K_PU`
with a 125 °C trip (`blair-rhodep-common-overlay.dtsi`).

**INFERENCE — ranked causes, most likely first:**

1. **The PWM is never driven, only the GPIO.** If your port sets `pm6125 gpio8` as a plain
   output-high instead of `func1` + a real PWM duty cycle, the LED gets an
   initial pulse from the enable path and then the current source settles to zero duty.
   Symptom matches exactly: bright, then off, no error. **Check first.**
2. **PWM duty is applied but the period/consumer is released.** A `pwm_apply` followed by
   `pwm_disable` on the last `put` (e.g. LED class `brightness=0` on an unheld handle) kills
   the rail seconds later.
3. Torch/flash **timeout in the driver** — downstream distinguishes flash (ms strobe) from
   torch (continuous); a flash-mode current with no re-arm expires by design.
4. `cam_flash_therm` mitigation cutting the rail. Only plausible if the off-time grows with
   ambient temperature; least likely at "a few seconds".

Mainline has **no** PWM node for PM6375/PM6125 at all
(`grep -c pwm sm6375-motorola-rhodep.dts` → 0, `sm6375.dtsi` → 0), so (1) is almost
certainly what is happening.

---

## 5. Mainline status

`grep -c` over `sm6375-motorola-rhodep.dts` and `sm6375.dtsi`:

| Item | rhodep.dts | sm6375.dtsi |
|---|---|---|
| `camss` | 0 | 0 |
| `csiphy` | 0 | 0 |
| `cci` | 0 | 0 |
| `wl2868` | 0 | 0 |
| `flash` | 0 | 0 |
| `pwm` | 0 | 0 |

The entire camera subsystem is unported. Ordering to bring it up:

1. `pm6125_pwm` + PM6125 GPIO pinctrl → fixes the flash LED independently of CAMSS.
2. WL2868C regulator driver (needs a register map — not in this corpus; the mainline
   candidate to compare against is `drivers/regulator/` for similar Semi/SGMicro parts).
3. CAMSS + CSIPHY 0–3 + CCI0/CCI1 in `sm6375.dtsi`.
4. Sensor drivers — blocked on identifying the actual parts (§3).
