# Cross-reference: research findings vs the live nethunter-rhodep-repo port

**This is a living map, not a closure.** It records, for each finding in docs 01–09,
whether the live port (`/opt/postmarket/nethunter-rhodep-repo`, kernel 7.2-rc5, patches
`kernel/patches/00xx–01xx`) already has it, still needs it, or — most usefully — where the
research **corrects or improves** something already applied.

Why it matters: the research baseline was `/tmp/v62test`, an older snapshot. The live port
has since moved well past it and independently converged on several of the same fixes. So a
lot of the "gaps" in `09-mainline-gaps.md` are already closed. But the research still earns
its keep in three ways, and those are the point of this file:

1. **Corrections** — cases where we applied a fix that is incomplete or aimed at the wrong
   root cause, and the research shows the real one (flash is the clearest: we drive only half
   the circuit).
2. **Improvements** — cleaner mainline paths than our workarounds (Type-C via `tusb320`).
3. **Unfinished depth** — the modem works but dies day-to-day on LTE and will do the same on
   5G; GNSS satellite fix kills the SoC; NFC. The research has the vendor-side detail to keep
   pushing these, not just to tick them off.

Status legend: **DONE** (in the port, matches research) · **CORRECT** (in the port but the
research changes the approach) · **IMPROVE** (works, but research has a better path) ·
**OPEN** (not done) · **DEPTH** (done but not finished; research helps finish it).

---

## Modem / RF / GNSS (`05-modem-rf-gnss.md`)

| Finding | Port status | Notes |
|---|---|---|
| `qcom,gsi-loader = "self"` + IPA memory-region | **DONE** | patch 0026. Modem registers on LTE, ~24 Mbit/s, calls/SMS. The research's P0 "4G kills the modem" was the *old baseline* symptom; the port fixed it independently. |
| `qcom,memshare` region reserved | **DONE** | patch 0120 reserves the region. |
| `qcom,memshare` **clients** (GPS id 0, FTM id 1, DIAG id 2) + the QMI service | **DEPTH / OPEN** | We reserve the memory but the *client protocol* (answering QMI 0x34 memory-loan) is the part the research ties to both the LTE watchdog reset **and** the dead GNSS satellite fix. This is the live lead — see "What to pull from the research next". |
| rmtfs address `f3900000` is invented vs vendor's dynamic alloc | **CHECK** | Worth verifying our rmtfs reserved-memory against a real vendor allocation; RF calibration/RFNV read from EFS exactly when the radio goes online (i.e. when LTE attaches — the reset window). |
| IPA memory map copied from QCM2290 (imem_addr, SMMU banks) | **CHECK** | Research: vendor maps `0x0C123000` into the IPA AP context bank and declares 4 SMMU banks vs mainline's 1. Relevant to the LTE-attach reset. |
| No QMI thermal mitigation; 110 °C critical trip | **OPEN** | Downstream has modem_pa/modem_tj/modem_skin cooling maps; mainline has bare passive alerts. |
| Ruled out (do not chase): mpss carveout, SMP2P/SMEM/IPCC, MX/MSS rails | — | Research verified byte-identical / TZ-owned. Matches our own "ruled out" notes. |

**Day-to-day 4G and the path to 5G:** the port's own README says the modem works but the SoC
watchdog-resets 3–10 min after LTE attach, so `ipa.ko` is held out of boot. The research
points the next work at `qcom,memshare` clients + the rmtfs/IPA-imem details, which are
exactly the things touched "when the radio goes online". This is the most valuable open
thread and 5G will hit the same wall until it is closed.

## Camera flash (`03-camera-flash.md` §4) — **CORRECT (this explains our bug)**

- **Port (patch 0092):** flash exposed as a plain `gpio-led` on **`tlmm 49`**. Verified
  on-device that toggling tlmm 49 blinks the LED — but it turns off after a few seconds
  (README known-limitation; an LPG/PWM attempt was rolled back as "SPMI ownership").
- **Research:** `tlmm 49` is only the **strobe/enable**. The actual current source is
  **PM6125 GPIO8 in alternate function `func1`, driven by `pm6125_pwm` channel 0**
  (`blair-camera-flash-pwm.dtsi`, `CONFIG_CAMERA_FLASH_PWM=y`). With no PWM duty the LED gets
  the enable pulse and the current source then sits at zero duty → **lights up, then dies in
  seconds. Our exact symptom.**
- **Action:** add a `pm6125_pwm` node + the gpio8 `func1` pinctrl and drive both the PWM and
  the tlmm 49 enable. This is a different root cause than the SPMI/LPG rollback we recorded;
  the rollback was for the *wrong* block. Device needed to confirm the LED stays on.

## Display brightness glitch (`04-display.md`)

| Finding | Port status |
|---|---|
| Backlight max **3514** (not 4095), min 9, default 1757 | **DONE** — panel patch 0003 sets `.brightness/.max_brightness = 3514`. |
| Brightness DCS in **HS mode** (`qcom,bl-dsc-cmd-state = "dsi_hs_mode"`) | **DONE/CORRECT** — 0003 references dsi_hs_mode; 0062 explored LP mode. Research says HS is correct; our own DSI work (cmd-DMA window, FIFO-underflow modeset recovery) is downstream of this. |
| TE (`tlmm 23`) into vblank; SSC off (`qcom,dsi-pll-ssc-en` deleted) | **CHECK** — verify in the panel/DSI path. |

The glitch is characterised in the port far beyond the research (docs/display-cmd-dma-window-wip);
the research corroborates the panel constants, nothing to change there.

## Audio — headset unplug kills speaker (`06-audio.md` §4)

- **Port:** audio works out-of-tree (patch 0035 enables it; aw88261 firmware shipped in
  `_common/firmware-aw88261`).
- **Research:** the speaker is the **AW882XX @ se10/0x34** (receiver @0x35), a separate smart
  PA, not the codec. The bug is that after the jack-removal DAPM transition the PA is powered
  down and never re-enabled. Ranked causes and a concrete on-device split test (read the PA
  regs over i2c after unplug: shutdown = re-enable bug; enabled-but-silent = DAPM routing).
- **Action:** re-run the AW882XX power-up + calibration-restore on the jack-removal DAPM
  event. Device needed for the register read that picks the right cause.

## Type-C (`08-usb-typec-power.md` §2) — **IMPROVE (cleaner mainline path)**

- **Port:** no Type-C driver; VBUS in host mode is poked by hand via the SGM41542 charger's
  OTG_CONFIG bit over i2c (`packages/rhodep-usb-otg/otg`). Works, but manual.
- **Research:** the retail part is almost certainly **SGM7220** (`qupv3_se8_i2c`, addr
  **0x47**, IRQ **tlmm 11**), a register-level clone of the **TI TUSB320**. Mainline has the
  driver: `drivers/extcon/extcon-usbc-tusb320.c` (`compatible = "ti,tusb320"`).
- **Action:** add a `tusb320` node at se8/0x47 with IRQ tlmm 11 for real orientation + role
  detection. **First confirm on-device**: read register 0x09 at i2c se8/0x47 for the TUSB320
  ID signature before trusting the binding. Keep the OTG script as fallback until proven.

## Connectivity — NFC and SAR (`07-connectivity.md`)

| Finding | Port status |
|---|---|
| NFC is **Samsung `sec-nfc` @0x27**, not NXP (config has `# CONFIG_NFC_QTI_I2C is not set`) | **DONE + DEPTH** — the port drives it via mainline `s3fwrn5` (patches 0101–0113). Card emulation is blocked at the RF listen front-end (see repo docs/nfc.md). The research's connectivity doc has the vendor NFC wiring (VEN/IRQ/RESET/firmware) that may hold the RF-profile detail we still need to break that wall — worth mining `07-connectivity.md` §2 against docs/nfc.md's open question. |
| SAR `sx937x` @0x2c + Motorola antenna map (ANT0 bottom-center, ANT2 top-center, …) | **OPEN** — not ported. The antenna map was recovered from the SAR register-map comments. |

## Sensors — structural, not a DTS gap (`09-mainline-gaps.md` §3)

Accel/gyro/mag/prox/ALS have **no** DT nodes anywhere in the vendor tree; they hang off the
ADSP (SSC) and are spoken to over QMI. Not a copy-a-node job. Matches the port's own qrtr
diagnostics. **OPEN**, and honestly a large piece of new software (QMI SSC client or IIO with
the ADSP not claiming the bus).

## Other unported subsystems (`09-mainline-gaps.md` §2)

Camera CAMSS/CSIPHY/CCI, WL2868C camera LDO, fingerprint (tlmm 6/17/18), SM5602 gauge,
BQ2597x charge pump — all **OPEN**. The research has the full power tree, MCLK/CSI/reset
GPIOs and rail maps for camera (`03-camera-flash.md` §1–2), which is the reference when that
work starts.

---

## What to pull from the research next (ranked, living)

1. **Type-C `tusb320`** — cleanest win, mostly device-independent DTS; confirm the 0x09 ID
   byte on-device, then bind. Replaces the manual OTG poke.
2. **Flash `pm6125_pwm` + gpio8 `func1`** — fixes the "lights then dies" bug at its real root
   (PWM current source), which our gpio-led-only node cannot.
3. **AW882XX re-enable on jack removal** — the speaker bug; device read decides the exact fix.
4. **Modem depth for day-to-day 4G → 5G** — `qcom,memshare` clients (GPS/FTM/DIAG) + verify
   rmtfs/IPA-imem against the vendor allocations. This is the LTE-attach reset and the dead
   GNSS satellite fix, together.
5. **NFC RF profile** — mine `07-connectivity.md` §2 for the vendor listen/RF-register detail
   against the open question in the port's docs/nfc.md.
6. **SAR sx937x**, then the larger unported subsystems.
