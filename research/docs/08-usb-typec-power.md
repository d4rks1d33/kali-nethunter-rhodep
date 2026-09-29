# rhodep — USB, Type-C, charger, battery

Source: `blair-usb.dtsi`, `sgm7220.dtsi`, `tcpc_config.dtsi`,
`discrete_charging_rhodep.dtsi`, `discrete-rt-pd-manger.dtsi`,
`blair-rhodep-common-overlay.dtsi`, `bat_setting/*`.

---

## 1. USB PHY supplies (FACT, `blair-usb.dtsi`)

```
USB3_GDSC  = <&gcc_usb30_prim_gdsc>      (:37)
vdd        = <&L7A>     0.788–1.05 V     (:100)
vdda18     = <&L10A>    1.8 V            (:101)
vdda33     = <&L7E>     3.08 V           (:102)
vdd (2nd)  = <&L18A>    0.88 V           (:121)
core       = <&L4A>                      (:123)
dpdm       = <&usb2_phy0>                (:38)
```

`discrete_charging_rhodep.dtsi:135` also takes `dpdm = <&usb2_phy0>` — the charger uses the
USB D+/D− lines for BC1.2 charger-type detection, so the PHY and the charger share `dpdm`.

---

## 2. Type-C controller — second-sourced (FACT)

Two mutually exclusive parts, same bus, **same interrupt line**:

| Part | Compatible | Bus | Addr | IRQ |
|---|---|---|---|---|
| **SGM7220** | `sgm,usb_type_c_sgm7220` | `qupv3_se8_i2c` | **0x47** | `tlmm 11` |
| RT1711H | `richtek,rt1711h` | `qupv3_se8_i2c` | 0x4e | `tlmm 11` |

Both configured identically as DRP with Try.SNK:
```dts
tcpc-dual,supported_modes = <0>;     /* dfp/ufp */
role_def   = <5>;                    /* Try.SNK */
rp_level   = <0>;                    /* Default Rp */
intr_gpio  = <&tlmm 11 0x0>;         /* bias-pull-up, 2 mA */
```
SGM7220 sets `vconn_supply = <0>` (never); RT1711H sets `<1>` (always).

RT1711H additionally carries a full USB-PD profile (`pd,vid = 0x29cf`, `pd,pid = 0x1711`,
`charging_policy = 0x31` = MAX_POWER_LVIC) plus `discrete-rt-pd-manger.dtsi`.

**INFERENCE — and this is actionable.** `HARDWARE-FACTS.md` establishes the retail unit
uses the **SGMicro** charger (SGM41542), and the BOM is internally consistent, so the
retail Type-C part is almost certainly **SGM7220**. SGM7220 is a register-level clone of
the **TI TUSB320**, and mainline has `drivers/usb/typec/tusb320.c`
(`compatible = "ti,tusb320"`). That is a far cheaper path than writing a new driver.

Your `HARDWARE-FACTS.md` currently says *"No mainline Type-C driver → VBUS in host mode is
NOT auto-enabled"* and works around it by poking the charger's OTG_CONFIG bit over I2C.
That workaround is sound, but a `tusb320`-based node at `se8`/0x47 with IRQ `tlmm 11`
should give you real orientation + role detection instead. Worth testing — read register
0x09 at 0x47 first to confirm the TUSB320 ID signature before writing DTS.

Mainline currently has: `grep -c sgm7220 sm6375-motorola-rhodep.dts` → **1**, `typec` → 1,
`tusb320` → **0**. So there is a mention but no TUSB320 binding in use.

---

## 3. Charger and fuel gauges (FACT)

| Part | Compatible | Bus | Addr |
|---|---|---|---|
| **SGM41542** | `sgm4154x` | `se8` | **0x3B** |
| BQ25890 | (alt / 2nd source) | `se8` | 0x6A |
| **BQ2597x** | charge pump | `se10` | 0x66 |
| **CW2217** | `cellwise,cw2217` | `se8` | **0x64** |
| SM5602 | `sm,sm5602` | `se8` | 0x71 |

Charger IRQs: `tlmm 12` (`bq2589x_irq-gpio`) and `tlmm 84`.

Fuel-gauge config (`blair-rhodep-common-overlay.dtsi:36-70`):
```dts
cw2217@64 {
    sense_r_mohm = <5>;
    factory_mode_ntc_exist = <0>;
    df-serialnum = "SB18D38323";
    #include "bat_setting/CW_ng50-ATL-5000mah-rhodep.dtsi"
    #include "bat_setting/CW_ng50-SWD-5000mah-rhodep.dtsi"
};
sm5602@71 {
    sm,rsns = <0>;  sm,v_l_alarm = <3400>;  sm,v_h_alarm = <4450>;
    sm,low_soc1 = <1>;  sm,low_soc2 = <15>;
    df-serialnum = "SB18D38324";
    #include "bat_setting/SM5602_NE50_ATL_Rhodep.dtsi"
    #include "bat_setting/SM5602_NE50_SWD_Rhodep.dtsi"
};
```

Two battery cell vendors are supported: **ATL** and **SWD**, both 5000 mAh, distinguished at
runtime by `df-serialnum`. Sense resistor is **5 mΩ**.

Mainline already has `cellwise,cw2217`, `sgm,sgm41542`, and `simple-battery` — matching
your notes. **SM5602 and BQ2597x are unported** (`grep -c` → 0), which is fine unless your
unit happens to have the SM5602 variant; check which gauge ACKs on `se8`.

---

## 4. Misc power

```dts
ldo_vib {
    compatible = "moto,vibrator-ldo";
    moto,vib-ldo-gpio = <&tlmm 100 0x0>;
};
gpio_keys {
    vol_up { gpios = <&pmr735a_gpios 1 GPIO_ACTIVE_LOW>;
             linux,code = <KEY_VOLUMEUP>; gpio-key,wakeup; debounce-interval = <15>; };
};
```

Both are already in mainline (`gpio-vibrator`, `gpio-keys`).

Thermal zones for the USB/charge path: `chg_therm` (pmk8350 THM2) and `usb_conn_therm`
(pmk8350 THM4), both `user_space` governor with a 125 °C passive trip.
