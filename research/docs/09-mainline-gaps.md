# rhodep — downstream → mainline gap analysis

Baseline: `/tmp/v62test/linux-7.2-rc5` (your active port, newest tree, 1195-line
`sm6375-motorola-rhodep.dts`). Compared against the official A12 rhodep DTS chain.

> Note on kernel version: you mentioned 7.3-rc4, but every local tree is **7.2.0-rc5**
> and `/tmp/v62test` is the most recent (2026-08-11 18:15) and most complete. Diffs below
> are against that. `v59test` differs only in ramoops sizing (11 diff lines).

---

## 1. What is already ported (FACT)

`compatible` strings present in `sm6375-motorola-rhodep.dts`:

```
motorola,rhodep + qcom,sm6375        qcom,sm6375-mdss / -dpu / -dispcc
motorola,rhodep-nt37701  (panel)     qcom,sm6375-dsi-ctrl / -dsi-phy-7nm
goodix,gt9916  (touch)               qcom,sm6115-ufshc + sc7280-qmp-ufs-phy
cellwise,cw2217  (gauge)             qcom,adreno-619.1 / adreno-gmu-wrapper
sgm,sgm41542  (charger)              qcom,wcn3990 + qcom,wcn3990-bt
qcom,rpm-pm6125-regulators           qcom,sm6375-ipa
qcom,rpm-pmr735a-regulators          gpio-keys, gpio-vibrator, ramoops
```

Remoteprocs enabled: `remoteproc_adsp`, `remoteproc_cdsp`, `remoteproc_mss`
(`:370-383`), all with `qcom/sm6375/motorola/rhodep/*.mbn` firmware paths.

---

## 2. Ranked gaps

Ranking is by *impact on your stated bugs*, then by effort.

### P0 — 4G enable kills the modem

**Finding (highest confidence): `qcom,gsi-loader = "modem"` is wrong.**

`sm6375-motorola-rhodep.dts:1183`:
```dts
qcom,gsi-loader = "modem";
```
Downstream loads the IPA/GSI firmware **from the AP** via TZ PAS
(`blair.dtsi:3166-3172`, `pas-id = 0xf`, carveout `pil_ipa_fw_mem@8aa00000`), and there is
no `memory-region` / `firmware-name` on your IPA node. Every comparable upstream Android
port (e.g. `sm7325-motorola-dubai.dts:732`) uses `"self"`.

→ **Change to `qcom,gsi-loader = "self"` and add the IPA firmware memory-region.**
Cheapest possible test, highest probability.

Supporting P0 items, in order:

2. **`qcom,memshare` is entirely absent from mainline.** Downstream declares GPS (id 0,
   boot-time), FTM (id 1, 5 MiB) and DIAG (id 2) clients plus an 8 MiB pool
   (`blair.dtsi:1080-1106, 436-442`). Unanswered QMI 0x34 memory-loan requests can assert
   the modem — **and this is the reason GNSS via modem is dead** (see §3).
3. **rmtfs address is invented.** Mainline pins `rmtfs@f3900000` (`sm6375.dtsi:620`);
   `f3900000` appears **nowhere** in the vendor DT — downstream allocates it dynamically
   (`blair.dtsi:2436-2442`). RF calibration/RFNV live in EFS and are read exactly when the
   radio goes online, i.e. when you enable 4G.
4. **IPA memory map copied from QCM2290 and unverified.** `ipa_data_v4_11_sm6375` uses
   `.imem_addr = 0x146a8000`, but the vendor maps `0x0C123000 /* modem tables in IMEM */`
   into the IPA AP context bank (`blair.dtsi:3248-3250`), and declares 4 SMMU context banks
   (0x4A0–0x4A3) vs mainline's one.
5. **No QMI thermal mitigation, and a 110 °C critical trip.** Downstream has
   `modem_pa`/`modem_tj`/`modem_skin` cooling devices (`holi-thermal-modem.dtsi:2-142`);
   mainline `mdm-core0-thermal` has passive alerts with **no cooling-maps** and
   `type = "critical"` @110 °C (`sm6375.dtsi:2563`). Only fits if death takes tens of seconds.

**Explicitly ruled out** (do not waste time here): mpss carveout mismatch —
`pil_mpss_wlan_mem @0x8b800000 size 0x10000000` is **byte-identical** downstream
(`blair.dtsi:352`) and mainline (`sm6375.dtsi:610`); SMP2P/SMEM/IPCC all match; TLMM
reserved-GPIO `<13 4>` matches `pinctrl-blair.c:1595-1600`; missing MX/MSS rails — there
are none, TZ/PAS owns modem power (`pas-id = 4` both sides), only `vdd_cx` proxy exists.

### P0 — flash LED turns off after seconds

Mainline has **no PWM at all**: `grep -c pwm` → 0 in both files.

Downstream: `pm6125_gpios gpio8` in **`function = "func1"`** driven by `pwms = <&pm6125_pwm 0 0>`,
selected by `CONFIG_CAMERA_FLASH_PWM=y`. Your port is almost certainly driving gpio8 as a
plain GPIO, so the LED gets an initial pulse and the current source then sits at zero duty.

→ Add PM6125 PWM + the `func1` pinctrl. Details in `03-camera-flash.md` §4.

### P1 — display brightness glitch

Panel is ported and the reset GPIO is correctly on `pmr735a_gpios 2`. What to verify in the
panel driver (invisible from DTS):
- backlight max **3514** (not the generic 4095), min **9**, default **1757**
- brightness DCS must be emitted in **HS mode** (`qcom,bl-dsc-cmd-state = "dsi_hs_mode"`)
- TE (`tlmm 23`) wired into vblank
- rhodep explicitly deletes `qcom,dsi-pll-ssc-en` — confirm SSC is off

### P1 — headset unplug kills speaker

`grep -c` → `wcd937` 0, `aw882` 0, audio nodes 0 in `rhodep.dts`. Your working audio is
out-of-tree. The speaker is a **separate I2C smart PA (AW882XX @ se10/0x34)**, not the
codec — it must be re-enabled on the jack-removal DAPM transition. See `06-audio.md` §4.

### P2 — GNSS only via WiFi, never via modem

GNSS has **no** independent hardware: zero GNSS nodes, GPIOs, regulators or clocks in the
whole vendor tree. It lives inside the modem DSP; the only footprint is `qcom,vm-nav-path`
on rmtfs and `nav_gpio`/`NAV_PPS` (TLMM 101/102).

→ GNSS cannot work until the modem is healthy **and** `qcom,memshare` GPS client id 0 is
answered. It is downstream of P0, not a separate bug. Do not look for a GPS chip.

### P2 — fully unported subsystems

| Subsystem | Mainline | Downstream reference |
|---|---|---|
| Camera (CAMSS/CSIPHY/CCI) | absent | `03-camera-flash.md` |
| WL2868C camera LDO | absent | `02-pmic-regulators.md` §4 |
| NFC (`sec-nfc` @0x27) | absent | `07-connectivity.md` §2 |
| SAR `sx937x` @0x2c | absent | `07-connectivity.md` §3 |
| Type-C `sgm7220` binding | mentioned, unbound | `08-usb-typec-power.md` §2 |
| Fingerprint | absent | `rhodep-fps-overlay.dtsi` (tlmm 6/17/18) |
| Sensors (accel/gyro/mag/prox/ALS) | absent | see §3 below |
| SM5602 gauge, BQ2597x pump | absent | `08-usb-typec-power.md` §3 |

---

## 3. Sensors — a structural finding, not a gap you can close with DTS

**FACT:** there are **no** accelerometer / gyroscope / magnetometer / proximity / ALS nodes
anywhere in the 56-file rhodep chain. Greps for `bosch|bmi|bma|lsm6|akm|stk3|ltr|vcnl|icm2|lis[23]`
return nothing.

**INFERENCE:** on SM6375 these sensors hang off the **ADSP** (Qualcomm SSC), on the ADSP's
own I2C, and are managed entirely by ADSP firmware and the Android sensors HAL over QMI.
They are invisible to Linux by design. `holi-lpi.dtsi` is LPI *audio* pinctrl, not sensors.

Consequences for mainline:
- Copying a DTS node will not work — there is nothing to copy.
- Options are (a) speak QMI to the ADSP sensor service, or (b) determine the physical I2C
  bus/addresses and drive the parts directly from Linux IIO, which requires the ADSP to
  not claim them. Neither is a DTS change.
- The `sensors/` directory you saw in the rhodep device config is the **Android HAL**
  config, not kernel bindings. `motorola-kernel-modules/drivers/sensors/` contains only
  `sensors_class.c` (a sysfs class helper), no actual sensor drivers.

This is the honest answer: the schematic + HAL cross-reference you wanted is not
obtainable from the published kernel sources.

---

## 4. Suggested order of work

1. `qcom,gsi-loader = "self"` + IPA firmware memory-region → retest 4G. *(one line)*
2. Add `qcom,memshare` clients → retest 4G stability and GNSS.
3. Verify/fix `rmtfs` reserved-memory against a real vendor allocation.
4. PM6125 PWM + `func1` pinctrl → flash LED.
5. Panel backlight max 3514 + HS-mode DCS → brightness glitch.
6. AW882XX re-enable on jack removal → speaker bug.
7. Then the unported subsystems in §2, cheapest first: SAR → Type-C(tusb320) → NFC → camera.
