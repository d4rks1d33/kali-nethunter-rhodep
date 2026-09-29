# rhodep — WiFi / Bluetooth / NFC / SAR

---

## 1. WiFi + Bluetooth: WCN3990 (FACT)

Confirmed as **WCN3990**, integrated (on-die-adjacent, driven via **icnss**), *not* PCIe,
*not* WCN6750.

```dts
icnss: qcom,icnss@C800000 {
    compatible = "qcom,icnss";
    reg = <0xC800000 0x800000>,        /* membase */
          <0xb0000000 0x10000>;        /* smmu_iova_ipa */
    interrupts = GIC_SPI 358, 359, 360, ...
    iommus = <&apps_smmu 0x80 0x1>;
    qcom,wlan-msa-fixed-region = <&pil_wlan_mem>;
    qcom,iommu-dma-addr-pool = <0xa0000000 0x10000000>;
    qcom,iommu-geometry      = <0xa0000000 0x10010000>;
};

pil_wlan_mem: wlan@86500000 { no-map; reg = <0x0 0x86500000 0x0 0x200000>; };   /* 2 MiB */
```

### Rails (identical set for WLAN and BT — FACT)

| Property | Rail | Config |
|---|---|---|
| `vdd-cx-mx` | **L2E** | `<640000 640000>` |
| `vdd-1.8-xo` | **L16A** | 1.7–1.9 V |
| `vdd-1.3-rfa` | **L2A** | 1.304 V |
| `vdd-3.3-ch0` | **L23A** | 3.0–3.312 V |
| `vdd-3.3-ch1` | **L21A** | 3.0–3.312 V |
| `vdd-smps` | **S5A** | 0.984 V |

```dts
bluetooth: bt_wcn3990 {
    compatible = "qcom,wcn3990";
    qcom,bt-sw-ctrl-gpio  = <&tlmm 69 GPIO_ACTIVE_HIGH>;   /* antenna / coex switch */
    qcom,bt-vdd-smps-supply  = <&S5A>;   qcom,bt-vdd-smps-config  = <984000 984000 1 0>;
    qcom,bt-vdd-io-supply    = <&L9A>;   qcom,bt-vdd-io-config    = <1700000 1900000 1 0>;
    qcom,bt-vdd-core-supply  = <&L2A>;   qcom,bt-vdd-core-config  = <1304000 1304000 1 0>;
    qcom,bt-vdd-pa-supply    = <&L23A>;  qcom,bt-vdd-pa-config    = <3000000 3312000 1 0>;
    qcom,bt-vdd-xtal-supply  = <&L16A>;  qcom,bt-vdd-xtal-config  = <1700000 1900000 1 0>;
};
```

**Answers to the asked questions:**
- *chip*: WCN3990
- *bus*: **neither SDIO nor PCIe nor UART for data** — WLAN is an integrated subsystem
  reached through **icnss + QMI over SMD/GLINK**; BT host transport is Qualcomm's
  in-SoC path (not a discrete UART HCI chip), and BT **audio** is SLIMbus (`btfmslim`).
  The "UART BT + SDIO WiFi" architecture you asked about is **not** what this device uses.
- *GPIO reset / wake*: none. The only BT GPIO is `tlmm 69` `bt-sw-ctrl` (RF/antenna switch).
- *IRQ*: `GIC_SPI 358/359/360...` to icnss, plus `smp2p_wlan_1_in` entries
  `qcom,smp2p-force-fatal-error` and `qcom,smp2p-early-crash-ind`.
- *regulators / clocks*: table above; XO via L16A.
- *antenna path*: `tlmm 69` is the WiFi/BT coexistence antenna switch.

WLAN also rides the **modem's** SMP2P edge and shares `pil_mpss_wlan_mem@0x8b800000`
(256 MiB) — see `05-modem-rf-gnss.md`. Consequence: **WiFi working proves the MSS boots.**

### Mainline status
`sm6375-motorola-rhodep.dts` already has `qcom,wcn3990` and `qcom,wcn3990-bt` — this is
ported, consistent with your report that both work. Use the rail table above to verify
voltages/loads if you are chasing stability rather than function.

---

## 2. NFC: Samsung, not NXP (FACT)

This is the most surprising finding in the corpus.

```dts
&qupv3_se7_i2c {
    sec-nfc@27 {
        compatible = "sec-nfc";
        reg = <0x27>;
        mmi,status = "/chosen", "mmi,nfc", "samsung";
        sec-nfc,ven-gpio     = <&tlmm 48 0x00>;
        sec-nfc,firm-gpio    = <&tlmm  8 0x00>;
        sec-nfc,irq-gpio     = <&tlmm  9 0x00>;
        sec-nfc,clk_req-gpio = <&tlmm  7 0x00>;
        interrupt-parent = <&tlmm>;
        interrupts = <9 0>;
        interrupt-names = "nfc_irq";
        pinctrl-names = "nfc_active", "nfc_suspend";
        pinctrl-0 = <&nfc_int_active &nfc_enable_active &nfc_clk_req_active>;
        pinctrl-1 = <&nfc_int_suspend &nfc_enable_suspend &nfc_clk_req_suspend>;
    };
};
```

Corroborated by the kernel config (`moto-holi-rhodep.config`):
```
# CONFIG_NFC_QTI_I2C is not set
```
— the Qualcomm/NXP NFC driver is explicitly **disabled** for rhodep.

| Item | Value |
|---|---|
| **NFC IC** | Samsung `sec-nfc` family (`mmi,status = ... "samsung"`) |
| Bus | I2C `qupv3_se7_i2c` (gpio27 SDA / gpio28 SCL) |
| Address | **0x27** |
| IRQ | **tlmm 9** (also the pinctrl `nfc_int_active`, 2 mA, bias-disable) |
| VEN / enable | **tlmm 48** |
| FIRM (download mode) | **tlmm 8** |
| CLK_REQ | **tlmm 7** (input-enable, 2 mA) |
| Regulator | **none in DT** — no `*-supply` on the node |
| Clock | request-based via CLK_REQ pin, no DT clock phandle |
| Antenna matching | **NOT IN SOURCES** (passive network, never in DT) |

> There is a bug in Motorola's own source at `blair-rhodep-common-overlay.dtsi`:
> the `nfc_clk_req_suspend` group has `pins = "gpi7"` (typo for `gpio7`). Harmless
> downstream because the suspend state is rarely applied, but do not copy the typo.

**Exact part number: NOT IN SOURCES.** The DT says "samsung" and the generic `sec-nfc`
binding; it does not name the die. Samsung's `sec-nfc` binding covers the S3NRN/S3NSN4V
series. Determining which requires the vendor NFC HAL or a physical read.

### Why your NFC "already reads"
Since the node needs **no regulator** and only four GPIOs, the minimum viable bring-up is
small — which is consistent with you getting reads already. For full function the pieces
that remain are CLK_REQ handling (tlmm 7) and the firmware-download path (tlmm 8).

**Mainline:** `grep -c nfc` → 0 in both `sm6375-motorola-rhodep.dts` and `sm6375.dtsi`.
Whatever is giving you reads is out-of-tree. Note mainline's `nfc/` subsystem has no
`sec-nfc` driver — Samsung's is downstream-only, so this will need either a port of
`sec-nfc` or an NCI-over-I2C shim.

---

## 3. SAR sensor: Semtech SX937x (FACT)

```dts
sx937x_se7: sx937x@2c {
    compatible = "Semtech,sx937x";
    reg = <0x2c>;
    interrupt-parent = <&tlmm>;  interrupts = <24 0x02>;
    Semtech,nirq-gpio = <&tlmm 24 0x02>;
    cap_vdd-supply = <&pm6125_l9>;
    Semtech,ref-phases-a = <5>;  ref-phases-b = <6>;  ref-phases-c = <0xff>;
    Semtech,button-flag = <0x1f>;
    Semtech,reg-num = <54>;
};
```

Present on **both** `se7` (rhodep override, `status = "ok"`) and `se10` (common overlay).
The base overlay sets `&sx937x { status = "disabled"; }` and enables the `se7` instance —
so on rhodep the **live SAR is on `se7` @ 0x2c, IRQ tlmm 24, powered from L9A**.

Its register init table encodes the **antenna map**, which is the closest thing to RF
layout information in the whole corpus:

| Phase | CS pin | Antenna (from Motorola's own comments) |
|---|---|---|
| PH0 | CS5 | ANT0 — **bottom center** |
| PH1 | CS0 | ANT1/6 — **top right** |
| PH2 | CS4 | ANT2 — **top center** |
| PH3 | CS6 | ANT5 — **bottom right** |
| PH4 | CS7 | ANT8 — **top left** |
| PH5 | CS2 | ANT0/5_REF — bottom reference |
| PH6 | CS1 | ANT8 REF — middle |
| PH7 | — | not used |

(`CS3` is deliberately hi-Z: *"set CS3 to hiz, because it used as IRQ pin"*.)

**INFERENCE.** This gives a physical antenna census: at least antennas 0,1,2,5,6,8 exist,
clustered bottom-center, bottom-right, top-center, top-right, top-left. Cross-referenced
with the two PA thermistors (`01-hardware-map.md` §5), the RF front-end is split between a
bottom cluster (main/primary TX) and a top cluster (diversity). This is the answer to the
"antenna switch / diversity" part of your RF question that the DTS can otherwise not give.

**Mainline:** `grep -c sx937` → 0. Unported.
