# rhodep (Moto G82 5G, XT2225-1, SM6375) — official-source hardware research

All data here is extracted from **official Motorola source releases**, not from repair-shop
schematics or LineageOS. Every claim is traceable to `file:line` in the cloned corpus.

---

## 1. Corpus actually downloaded

Root: `/opt/postmarket/research/motorola/`

| Directory | Repo | Ref | Size |
|---|---|---|---|
| `kernel-msm-A12-S1SUS32.73-13-4-3` | `MotorolaMobilityLLC/kernel-msm` | tag `S1SUS32.73-13-4-3` | 1.3G |
| `kernel-msm-A12-MMI-S1SU32.73-34-7` | `kernel-msm` | tag `MMI-S1SU32.73-34-7` | 1.3G |
| `kernel-msm-A13-T1SUS33.1-124-6-12` | `kernel-msm` | tag `MMI-T1SUS33.1-124-6-12` | 342M |
| **`devicetree-A12-S1SUS32.73-13-4-3`** | **`kernel-devicetree`** | tag `S1SUS32.73-13-4-3` | 30M |
| `devicetree-A12-MMI-S1SU32.73-34-7` | `kernel-devicetree` | tag `MMI-S1SU32.73-34-7` | 30M |
| `devicetree-A13-T1SUS33.1-124-6-12` | `kernel-devicetree` | tag `MMI-T1SUS33.1-124-6-12` | 31M |
| **`camera-dt-A12-S1SUS32.73-13-4-3`** | **`kernel-camera-devicetree`** | tag `S1SUS32.73-13-4-3` | — |
| `camera-dt-A13` | `kernel-camera-devicetree` | tag `MMI-T1SUS33.1-124-6-12` | — |
| `display-dt-A12-S1SUS32.73-13-4-3` | `kernel-display-devicetree` | tag `S1SUS32.73-13-4-3` | — |
| `kmodules-A12-S1SUS32.73-13-4-3` | `motorola-kernel-modules` | tag `S1SUS32.73-13-4-3` | 45M |
| `kmodules-A13-T1SUS33.1-124-6-12` | `motorola-kernel-modules` | tag `MMI-T1SUS33.1-124-6-12` | 50M |
| `techpack-audio-A13` | `kernel-msm-5.4-techpack-audio` | tag `MMI-T1SUS33.1-124-6-12` | 20M |
| `techpack-camera-A13` | `kernel-msm-5.4-techpack-camera` | tag `MMI-T1SUS33.1-124-6-12` | 9.1M |
| `techpack-display-A13` | `kernel-msm-5.4-techpack-display` | tag `MMI-T1SUS33.1-124-6-12` | 7.8M |
| `techpack-video-A13` | `kernel-msm-5.4-techpack-video` | tag `MMI-T1SUS33.1-124-6-12` | 1.7M |
| `wlan-qcacld-3.0-A12` | `vendor-qcom-opensource-wlan-qcacld-3.0` | tag `S1SUS32.73-13-4-3` | — |
| `wlan-host-cmn-A12` | `...wlan-qca-wifi-host-cmn` | tag `S1SUS32.73-13-4-3` | — |
| `wlan-fw-api-A12` | `...wlan-fw-api` | tag `S1SUS32.73-13-4-3` | — |

Clone logs: `/opt/postmarket/research/logs/`.

### Two corrections to the original assumptions

1. **The device trees are NOT in `kernel-msm`.** `kernel-msm` @ `S1SUS32.73-13-4-3`
   contains only `arch/arm64/configs/vendor/ext_config/*-holi-rhodep.config` — 9 files,
   zero DTS. The real DTS lives in a separate repo, **`MotorolaMobilityLLC/kernel-devicetree`**,
   which carries the same `S1SUS32.73-13-4-3` and `MMI-S1SU32.73-34-7` tags. Camera DTS is
   split again into `kernel-camera-devicetree`.

2. **The four techpacks have no Android 12 rhodep tag.** Verified by `git ls-remote` over all
   244/224/619/220 refs: the only rhodep refs in the techpacks are Android 13
   `MMI-T1SUS33.1-124-6-{7,8-1,8-3,11,12}`. There is no `S1SUS32.73-*` techpack tag.
   So the techpacks here are A13. This does not hurt: the A12 and A13 rhodep DTS differ
   only marginally (see `09-mainline-gaps.md`).

Also note: LineageOS references build `T1SUS33.1-124-6-16`, but Motorola's published sources
stop at `-12`. `-16` sources were never released.

---

## 2. Platform naming

| Name | Meaning |
|---|---|
| `rhodep` | Moto G82 5G, XT2225-1 (this device) |
| `rhodei`, `rhodec` | sibling devices in the same DTS repo — **do not copy from these** |
| `blair` | Qualcomm's board/SoC name for **SM6375**. `blair.dtsi` is the SoC dtsi. |
| `holi` | the wider chipset family (SM4350/SM6375). `holi-*.dtsi` are family-level files. |

DTS entry point: `qcom/blair-moto-rhodep-base.dts` → `blair.dtsi`.
Device overlay: `qcom/blair-rhodep-common-overlay.dtsi`.
Production hardware revision: **`blair-rhodep-dvt2-overlay.dts`** (evt1/evt2/dvt1 are prototypes).

The fully resolved include chain is **56 files**, listed in `/tmp/opencode/chain.txt` and
regenerable with the snippet in `01-hardware-map.md` §0.

---

## 3. Documents

| File | Contents |
|---|---|
| `01-hardware-map.md` | Master hardware map, bus topology, TLMM GPIO table |
| `02-pmic-regulators.md` | PMIC lineup, full regulator table, rail→consumer map |
| `03-camera-flash.md` | 4 sensors, WL2868C LDOs, CSI/MCLK/GPIO, flash LED (PWM) |
| `04-display.md` | NT37701 panel, DSI, backlight, brightness-glitch analysis |
| `05-modem-rf-gnss.md` | Modem power/clock/memory, RF, GNSS, 4G-crash root causes |
| `06-audio.md` | WCD9370 + dual AW882XX, MBHC, headset/speaker bug analysis |
| `07-connectivity.md` | WCN3990 WiFi/BT, NFC, SAR |
| `08-usb-typec-power.md` | Type-C, charger, fuel gauges, USB PHY |
| `09-mainline-gaps.md` | **Ranked downstream→mainline diff — start here for porting** |

## 4. On the schematic / layout

**Not obtained, and I would not rely on the one you have.**

Searched: GitHub repo + code search for `rhodep schematic`, `XT2225 schematic`,
`moto g82 service manual` — **0 results** in all cases. Motorola does not publish
schematics or PCB layout for consumer devices; they are not part of any GPL obligation,
so they are absent from every official source release used here.

Anything circulating under those names comes from repair-shop document resellers. Those
files are (a) frequently for the wrong variant — note this DTS repo also contains
`rhodei` and `rhodec`, which are different devices with different camera, display and
charging hardware — and (b) unverifiable. Your own observation that the one you found
"seems to come from a repair base" matches that.

What the official sources gave instead, which is strictly better for DTS work:

| You wanted from the schematic | Where it actually came from |
|---|---|
| NFC part, I2C addr, IRQ, VEN, RESET | `07-connectivity.md` §2 — complete |
| Camera sensor rails, MCLK, RESET, CSI | `03-camera-flash.md` §1–2 — complete |
| Flash LED driver topology | `03-camera-flash.md` §4 — complete (PWM) |
| DSI lanes, panel IC, reset, TE, biases | `04-display.md` §2 — complete |
| WiFi/BT rails, IRQ, antenna switch | `07-connectivity.md` §1 — complete |
| PMIC rail → consumer map | `02-pmic-regulators.md` §3 — complete |
| Antenna / diversity placement | `07-connectivity.md` §3 — recovered from the SAR register map |
| RF PA / FEM / duplexer part numbers | **Genuinely unavailable** — see below |

The only thing a schematic would have added is the RF front-end BOM (PA/FEM, filters,
duplexers, antenna switches). That is unavailable from software sources *by construction*:
on this platform the RF front-end is driven over dedicated MSS pads from RFFE/RFC data in
EFS, never through TLMM or any Linux peripheral. `05-modem-rf-gnss.md` documents this and
shows the only two software-visible traces of it (the PA thermistors and the SAR antenna
map). No RF part numbers are asserted anywhere in these documents.

## 5. Reliability convention

Every document marks claims as:
- **FACT** — quoted directly from the corpus with `file:line`.
- **INFERENCE** — reasoning on top of facts, labelled as such.
- **NOT IN SOURCES** — explicitly unknown. Nothing is guessed.
