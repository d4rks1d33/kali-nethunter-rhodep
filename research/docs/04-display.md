# rhodep — display / DSI

Source: `rhodep-display.dtsi`, `dsi-panel-mot-{csot,tianma}-nt37701-655-1080x2400-dsc-cmd-common*.dtsi`.

---

## 1. Panel identity (FACT)

- Controller: **Novatek NT37701**
- Panel size in the filename: **6.55"**, 1080 × 2400
- Mode: **DSC command mode** (`qcom,mdss-dsi-panel-type = "dsi_cmd_mode"`)
- DSI PHY: `qcom,dsi-phy-num = <0>` — single DSI, PHY 0
- Two panel vendors, second-sourced: **CSOT** and **Tianma**, each with revisions v0/v1/v2
- Default selected for rhodep (`rhodep-display.dtsi:109`):
  ```dts
  qcom,dsi-default-panel = <&mot_csot_nt37701_655_1080x2400_dsc_cmd_v0>;
  ```

**INFERENCE.** Marketing says 6.6"; the panel file says 6.55". Same panel, rounded
differently. Not a discrepancy that matters.

---

## 2. Power and control (FACT)

```
vddio  = <&L13A>            1.8 V   enable-load 60700 µA, disable-load 80 µA
                                    post-on-sleep 2 ms, post-off-sleep 0
dvdd   = <&L3A>             1.25 V  enable-load 60700 µA, disable-load 10 µA
                                    post-on-sleep 2 ms, post-off-sleep 2 ms
vci    = <&panel_vci>       3.0 V   enable-load 30000 µA, disable-load 0
                                    post-on-sleep 0,    post-off-sleep 2 ms

panel_vci: panel_gpio_regulator@1 {
    compatible = "qti-regulator-fixed";
    regulator-name = "panel_vci";
    gpio = <&tlmm 46 0>;
    pinctrl-0 = <&panel_vci_en_default>;     /* gpio46 */
};

reset  = <&pmr735a_gpios 2 0>      ← PMIC GPIO, not TLMM
TE     = <&tlmm 23 0>
```

The supply **ordering and the post-on/post-off sleeps** above are part of the panel power
sequence and come from `dsi_panel_pwr_supply_amoled` (`rhodep-display.dtsi:46-82`).

`blair-moto-rhodep-base.dts:  &mdss_dsi_phy0 { /delete-property/ qcom,dsi-pll-ssc-en; }`
— **spread-spectrum on the DSI PLL is explicitly disabled on rhodep.**

---

## 3. Backlight (FACT) — relevant to your brightness glitch

```dts
qcom,mdss-dsi-bl-pmic-control-type = "bl_ctrl_dcs";   /* DCS, not PWM, not WLED */
qcom,mdss-dsi-bl-min-level      = <9>;
qcom,mdss-dsi-bl-max-level      = <3514>;
qcom,mdss-dsi-bl-default-level  = <1757>;
qcom,mdss-brightness-max-level  = <3514>;
qcom,bl-dsc-cmd-state           = "dsi_hs_mode";      /* <-- HS, not LP */
```

Note the **generic** panel dtsi (`...-common-v1.dtsi:50-52`) says min 3 / max **4095**,
but the rhodep override (`rhodep-display.dtsi`) narrows it to min 9 / max **3514**.
The rhodep value is the correct one.

ESD/status check:
```dts
qcom,esd-check-enabled;
qcom,mdss-dsi-panel-status-check-mode = "te_chk_reg_rd";
qcom,mdss-dsi-panel-status-command = [06 01 00 01 00 00 01 0a];  /* read 0x0A */
qcom,mdss-dsi-panel-status-value = <0x9c>;
```

DSI PHY timings are per-refresh-rate, four active timings (one commented out):

| timing | clockrate (commented) | phy-timings |
|---|---|---|
| @0 | 320 MHz | `00 0D 03 03 10 1D 04 03 02 02 04 00 0C 08` |
| @1 | 400 MHz | `00 10 03 03 11 1E 04 04 03 02 04 00 0E 08` |
| @2 | 601 MHz | `00 16 05 05 14 1F 06 06 06 02 04 00 13 0A` |
| @3 | 801 MHz | `00 1C 07 07 17 15 07 07 08 02 04 00 18 0C` |
| (@4) | 961 MHz | commented out in source |

All timings use `qcom,display-topology = <1 1 1>` and
`qcom,mdss-dsi-panel-phy-drive-strength = <255>`.

---

## 4. Your symptom: glitches when ramping brightness

**INFERENCE, ranked.** The two facts that matter are: backlight is **DCS** (an in-band
MIPI command on the same link as pixel data), and downstream forces those commands to
`dsi_hs_mode`.

1. **Backlight DCS sent in LP mode instead of HS.** In a DSC command-mode panel, mixing an
   LP-mode DCS write into an active HS frame transfer forces an LP↔HS transition mid-stream.
   On NT37701 this shows up exactly as brief tearing/flicker *only while brightness changes*
   — which is your description. Downstream sets `qcom,bl-dsc-cmd-state = "dsi_hs_mode"`
   specifically to avoid this. **Check what mode your port emits brightness updates in first.**
2. **Backlight range mismatch.** If your port uses 4095 (the generic value) instead of
   **3514**, the top ~14 % of the range is clamped by the panel, and the DCS value wraps or
   saturates near the top of a ramp.
3. **No TE synchronisation on the brightness write.** With `tlmm 23` TE present, the write
   should be scheduled against TE; unsynchronised writes land mid-refresh.
4. DSI PLL SSC left enabled. Downstream explicitly deletes `qcom,dsi-pll-ssc-en` for rhodep;
   if mainline's `sm6375-dsi-phy-7nm` enables SSC by default you get low-amplitude,
   brightness-dependent shimmer. Cheap to test.

Since the glitch is "barely noticeable and only during ramps", (1) and (2) are by far the
best fits; (4) would be continuous rather than ramp-triggered.

---

## 5. Mainline status (FACT)

Mainline is in good shape here — this is the most complete subsystem in your port:

```
sm6375-motorola-rhodep.dts:996   panel@0 { compatible = "motorola,rhodep-nt37701"; }
                          :1002    reset-gpios = <&pmr735a_gpios 2 GPIO_ACTIVE_LOW>;  ✓ correct PMIC GPIO
                          :1004    vddio-supply = <&pm6125_l13>;                      ✓ matches L13A
                          :1005    dvdd-supply  = <&pm6125_l3>;                       ✓ matches L3A
                          :1006    vci-supply   = <&panel_vci>;                       ✓
                          :135     panel_vci regulator-fixed via gpio                 ✓
```

Present: `qcom,sm6375-mdss`, `-dpu`, `-dispcc`, `-dsi-ctrl`, `-dsi-phy-7nm`.

Things to verify against the table above, since they are *not* visible from the DTS alone
and live in your panel driver:
- backlight max **3514**, min **9**, default **1757**
- brightness DCS in **HS** mode
- TE on `tlmm 23` actually wired into the vblank path
- the per-supply post-on/post-off sleep values from §2
