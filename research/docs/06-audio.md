# rhodep — audio

Source: `blair-audio-wcd9370-aw882xx-acf-dual-overlay.dtsi`, `holi-audio.dtsi`,
`msm-audio-lpass.dtsi`, `holi-lpi.dtsi`, `blair-rhodep-common-overlay.dtsi`.
Techpack: `motorola/techpack-audio-A13` (A13 — no A12 rhodep techpack tag exists).

---

## 1. Topology (FACT)

```
                 SM6375 LPASS
                      │
      ┌───────────────┼────────────────┐
      │               │                │
  va_macro       tx_macro          rx_macro
  @0xA730000     @0xA620000        @0xA600000
      │                                │
  va_swr_master (swr0)            rx SoundWire
  @0xA740000                           │
      │                                │
  wcd937x_tx_slave              wcd937x (WCD9370 codec)
  reg <0x0A 0x01170223>
                                  ── analog out ──
                                        │
                        ┌───────────────┴───────────────┐
                        │                               │
         AW882XX @ I2C 0x34                AW882XX @ I2C 0x35
         sound-channel = <0>               sound-channel = <1>
         irq = <&tlmm 59 0x2008>           irq = <&tlmm 60 0x2008>
         → LOUDSPEAKER                     → RECEIVER / earpiece
                    (both on qupv3_se10_i2c)
```

- **Codec: WCD9370** (`wcd937x`), attached over **SoundWire**, not SLIMbus.
- **Speaker amps: two Awinic AW882XX smart PAs**, `compatible = "awinic,aw882xxacf_smartpa"`.
- Kernel config confirms the stereo variant
  (`moto-holi-rhodep.config`): `CONFIG_AW882XX_STEREO_SMARTPA=y`,
  `CONFIG_AW882XX_PARAMS_FROM_BIN=y`, `CONFIG_AW882XX_MAPSIZE_16K=y`.

AW882XX DSP topology/port IDs (identical on both PAs):
```
aw-tx-topo-id = <0x1000ff00>   aw-rx-topo-id = <0x1000ff01>
aw-rx-port-id = <0x1002>       aw-tx-port-id = <0x1003>
aw-re-min = <4000>             aw-re-max = <30000>     aw-cali-mode = "aw_attr"
```
`aw-re-min/max` are the speaker DC-resistance calibration bounds (4–30 Ω).

Bluetooth audio: `btfmslim_codec: wcn3990 { compatible = "qcom,btfmslim_slave";
elemental-addr = [00 01 20 02 17 02]; }` — BT audio rides **SLIMbus** to the WCN3990.

---

## 2. Codec supplies (FACT)

```
cdc-vdd-rxtx   = <&L9A>    (blair-rhodep-common-overlay.dtsi:71)
cdc-vddpx      = <&L9A>    (blair-rhodep-common-overlay.dtsi:72)
cdc-vdd-rxtx   = <&L11A>   (audio overlay :234)   ← overlay default, overridden above
cdc-vddpx      = <&L11A>   (audio overlay :238)
cdc-vdd-buck   = <&L14A>   (audio overlay :242)
cdc-vdd-mic-bias = <>      ← explicitly NULLED for rhodep
```

The rhodep overlay deliberately overrides `cdc-vdd-rxtx`/`cdc-vddpx` from L11A to **L9A**
and blanks the mic-bias supply, with this comment (`blair-rhodep-common-overlay.dtsi:74-78`):

> *"Overriding cdc-vdd-mic-bias-supply to dummy value to avoid compilation errors as BOB is
> not defined for pm6125"*

and sets `qcom,cdc-static-supplies = "cdc-vdd-rxtx", "cdc-vddpx";`

**There is no boost (BOB) regulator on rhodep.** Mic bias comes from the codec internally.

---

## 3. Headset / MBHC (FACT)

```
qcom,msm-mbhc-hphl-swh = <1>;     /* HPHL uses a mechanical switch */
qcom,msm-mbhc-gnd-swh  = <1>;     /* ground   uses a mechanical switch */
qcom,msm-mbhc-hs-mic-max-threshold-mv = <2700>;
qcom,msm-mbhc-hs-mic-min-threshold-mv = <116>;
```

`hphl-swh = 1` / `gnd-swh = 1` means the 3.5 mm jack has **mechanical detection switches**
that physically reroute HPHL and ground when a plug is inserted/removed.

---

## 4. Your symptom: unplugging the headset kills the speaker

**FACT groundwork:** the speaker is *not* driven by the codec directly — it is driven by the
**AW882XX at I2C 0x34**, a separate I2C device with its own enable/IRQ and its own DSP
calibration state. The codec only feeds it. So "speaker stops working" is a statement about
the AW882XX, not about the WCD9370.

**INFERENCE, ranked:**

1. **The AW882XX is never re-enabled after the jack-removal DAPM transition.** On plug-in,
   the route switches to HPH and the smart PA is powered down. On removal, the machine
   driver must re-run the AW882XX power-up + calibration-restore sequence. If your port has
   the PA as a plain "always on" amp with no jack-event handling, the power-down happens but
   the power-up never does. This fits the symptom exactly (works until first plug cycle,
   then dead until reboot/reload).
2. **Only one of the two PAs is being managed.** With `sound-channel = <0>` (speaker) and
   `<1>` (receiver) sharing a driver, a port that instantiates only one can end up muting
   the wrong channel on the jack event.
3. **MBHC switch semantics inverted.** With `hphl-swh`/`gnd-swh` = 1, the mechanical switch
   already reroutes ground. If the driver *also* applies a software reroute, the removed
   state lands in a combination the PA never recovers from.
4. Mic-bias: since `cdc-vdd-mic-bias-supply` is blanked and mic bias is internal, a port
   that tries to drive an external mic-bias rail on jack events can stall the MBHC state
   machine, leaving DAPM in the "headset present" state forever — speaker stays muted.

Diagnostic that separates (1) from (3): after unplugging, read the AW882XX registers over
I2C (bus `se10`, addr 0x34). If the PA is in shutdown, it is (1)/(2). If the PA is enabled
but silent, the problem is upstream in DAPM routing → (3)/(4).

---

## 5. Mainline status (FACT)

```
grep -c  in sm6375-motorola-rhodep.dts / sm6375.dtsi
  wcd937  →  0 / 0
  aw882   →  0 / 0
  lpass   →  0 / 5
  sound   →  0 / 1
  audio   →  0 / 4
```

So `sm6375.dtsi` has LPASS scaffolding but **rhodep.dts declares no audio at all**.
You said audio works — that means it is coming from somewhere outside this DTS
(a separate overlay, a machine driver with hardcoded routing, or an out-of-tree patch).

Worth reconciling, because the jack bug is most likely in exactly that layer. The
downstream facts to port across:
- WCD9370 over **SoundWire** (`va_swr_master`, slave addr `0x0A 0x01170223`)
- two AW882XX on `se10` at **0x34** (spk, ch0, IRQ tlmm 59) and **0x35** (recv, ch1, IRQ tlmm 60)
- `cdc-vdd-rxtx`/`cdc-vddpx` = **L9A**, `cdc-vdd-buck` = **L14A**, no BOB, internal mic bias
- MBHC thresholds 116–2700 mV, both switches mechanical
