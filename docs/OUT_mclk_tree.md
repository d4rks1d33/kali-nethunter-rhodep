# S5KJN1 on rhodep (SM6375): MCLK / CCI / CAMSS clock-tree audit

Question posed: is the sensor's MCLK (parent `gpll9_out_main`, 24 MHz) or the
rhodep CCI/CAMSS clock backport (patches 0053/0054) the reason the S5KJN1 returns
register residue (0x9b05) instead of chip id 0x38e1, given the identical driver +
silicon works on the Fairphone FP5?

**Bottom line up front: the MCLK is correct — parent, rate, source, and duty cycle
all check out against the upstream-intended values and against the FP5/sc7280
reference. This is a VALID NEGATIVE for the clock hypothesis.** The one real,
citable clock-tree defect in the backport is in the **CAMSS `clock-rate` table
column alignment (patch 0053)**, but that affects the ISP/CSID/VFE datapath, *not*
MCLK and *not* the i2c identify path, so it cannot explain "ACK but residue" either.
The residue is not a clock problem. See §6 for what is actually left.

---

## 1. MCLK setup on rhodep vs what SM6375 actually requires

### 1a. The ftbl (FACT — gcc-sm6375.c)
`gcc_camss_mclk1_clk_src` uses `ftbl_gcc_camss_mclk0_clk_src` (shared by mclk0..4):

```c
static const struct freq_tbl ftbl_gcc_camss_mclk0_clk_src[] = {
	F(19200000, P_BI_TCXO,         1,  0,  0),
	F(24000000, P_GPLL9_OUT_MAIN,  1,  1, 15),   /* <-- the 24 MHz entry */
	F(65454545, P_GPLL9_OUT_EARLY, 11, 1,  2),
	{ }
};
```
(FACT, gcc-sm6375.c `ftbl_gcc_camss_mclk0_clk_src` / `gcc_camss_mclk1_clk_src`,
`.parent_map = gcc_parent_map_3`.)

**For 24 MHz the ONLY supported parent is `P_GPLL9_OUT_MAIN`.** There is no XO- or
GPLL0-derived 24 MHz row. The live state (`gcc_camss_mclk1_clk_src` parent =
`gpll9_out_main`, rate 24 MHz) is therefore the *upstream-intended, and only legal,*
configuration for 24 MHz on this SoC. There is nothing to "correct" here.

### 1b. Is gpll9 a "noisy" display/GPU PLL? NO. (FACT)
- `gpll9` on SM6375 is a **Zonda** alpha-PLL, L=0x4B → VCO = 75 × 19.2 MHz =
  **1440 MHz** (FACT, `gpll9_config`, `zonda_vco`). `gpll9_out_main` = /4 =
  **360 MHz** (FACT, `post_div_table_gpll9_out_main = {0x3, 4}`).
- Consumers of gpll9 in gcc-sm6375: **only the camera clocks** —
  `mclk*_clk_src` (via `P_GPLL9_OUT_MAIN`/`P_GPLL9_OUT_EARLY`) and
  `tfe_*_clk_src` (144/180 MHz rows). No display, no GPU, no bus fabric maps gpll9
  (FACT — grep of gcc-sm6375.c: gpll9 appears only in `gcc_parent_map_3/5/6/9/10`,
  all camera RCGs, and the tfe ftbl). **gpll9 is the SM6375 "camera PLL."**
- This is the *direct structural analogue* of sc7280's `cam_cc_pll2`: same Zonda
  type, **same L=0x4B → same 1440 MHz** (FACT, camcc-sc7280.c `cam_cc_pll2_config`
  `.l = 0x4B`, `zonda_vco`). The FP5 MCLK is derived from that exact PLL family.
- => The "gpll9 might be a jittery display/GPU source" worry is **factually wrong**.
  It is the dedicated low-jitter camera PLL, identical silicon class to FP5's.

### 1c. Exact rate & duty cycle — computed from the RCG2 model (FACT)
RCG2 MND rate formula (clk-rcg2.c `calc_rate`): `rate = parent/pre_div × M/N`.
- rhodep: 360 MHz / 1 × 1/15 = **24.000000 MHz exactly.**
- The driver hard-gates on this: `clk_get_rate(mclk) != 24 MHz → -EINVAL`
  (s5kjn1-mainline.c:1313-1317). Probe got *past* this (it reached the id read),
  which is independent proof the parent selected correctly and the rate is exactly
  24 MHz. If the parent/rate were wrong the driver would have aborted at probe with
  "MCLK clock frequency … not supported", not produced residue.

**Duty cycle (the subtle part) — NOT a problem:**
The naive "M/N = 1/15 ⇒ 6.7 % duty" reasoning is WRONG for these RCGs. When
`f->n && f->m != f->n`, clk-rcg2 sets **`CFG_MODE_DUAL_EDGE`** and programs the D
(duty) register (`__clk_rcg2_configure_mnd`):

```c
d_val    = f->n;                 /* = 15 */
n_minus_m = (f->n - f->m) * 2;   /* = 28 */
d_val    = clamp(d_val, f->m, n_minus_m);   /* 15, unchanged */
not2d_val = ~d_val & mask;       /* stores 2d = 15 */
```
Read back via `clk_rcg2_get_duty_cycle`: duty = d/(N) with d = round(2d /2) →
**≈ 15/30 = 50 %**. Same computation for sc7280's `F(24000000, …, 10, 1, 6)` gives
2d=6 → 6/12 = **50 %**. **Both MCLKs come out at ~50 % duty at the RCG output.**
The MND/dual-edge divider deliberately restores ~50 %; it does not emit a 1/N-width
pulse. (FACT — clk-rcg2.c `__clk_rcg2_configure_mnd`, `clk_rcg2_get_duty_cycle`,
`CFG_MODE_DUAL_EDGE`.)

INFERENCE: a fractional-N (dual-edge) 24 MHz has marginally more cycle-to-cycle
jitter than an integer-divided 24 MHz, but (a) FP5 *also* uses a fractional-N MND
(`10,1,6`) off the same PLL family and works, and (b) the S5KJN1 internal PLL is
spec'd to lock 6–27 MHz MCLK with wide jitter tolerance. Not a credible cause.

### 1d. `assigned-clock-parents` — is one missing? Effectively no. (INFERENCE, strong)
Patch 0055 sets only:
```dts
assigned-clocks = <&gcc GCC_CAMSS_MCLK1_CLK>;
assigned-clock-rates = <24000000>;
```
There is **no `assigned-clock-parents`** — but none is needed, because the ftbl row
for 24 MHz hard-codes `P_GPLL9_OUT_MAIN`; `clk_set_rate(24 MHz)` on the RCG selects
that parent automatically (the RCG's `determine_rate`/`set_rate` picks the ftbl
entry, which carries the src). Live `clk_summary` confirms the parent *did* resolve
to `gpll9_out_main`. So adding `assigned-clock-parents` would be belt-and-braces and
would change nothing (there is no alternative 24 MHz parent to force).

**Verdict §1: MCLK parent = correct and only-legal; rate = exact 24.000 MHz;
duty ≈ 50 %; source = dedicated camera Zonda PLL identical in class to FP5's.
MCLK is exonerated.**

---

## 2. FP5 / sc7280 MCLK reference (the "correct" comparator)

FACT (camcc-sc7280.c):
```c
static const struct freq_tbl ftbl_cam_cc_mclk0_clk_src[] = {
	F(19200000, P_CAM_CC_PLL2_OUT_EARLY, 1,  1, 75),
	F(24000000, P_CAM_CC_PLL2_OUT_EARLY, 10, 1,  6),   /* FP5 MCLK3 = this */
	F(34285714, P_CAM_CC_PLL2_OUT_EARLY, 2,  1, 21),
	{ }
};
```
`cam_cc_pll2` = Zonda, L=0x4B = **1440 MHz**. 24 MHz = 1440/10 × 1/6.

| Attribute            | FP5 (sc7280 camcc) | rhodep (sm6375 gcc) | Same requirement met? |
|----------------------|--------------------|---------------------|-----------------------|
| Source PLL type      | Zonda, 1440 MHz    | Zonda, 1440 MHz     | ✅ identical class     |
| PLL role             | camera PLL2        | camera gpll9        | ✅ dedicated cam PLL   |
| MCLK output rate     | 24.000 MHz         | 24.000 MHz          | ✅ exact               |
| Divider style        | MND dual-edge 1/6  | MND dual-edge 1/15  | ✅ both ~50 % duty     |
| Driver 24 MHz gate   | passes             | passes (live)       | ✅                     |

The sensor's requirement ("a clean 24 MHz its internal PLL can lock to") is met
**identically** on both platforms. The SoC clock-controller differs (camcc vs gcc)
but the electrical MCLK the S5KJN1 sees is equivalent.

---

## 3. The CCI clock path (patch 0054) — correct for identify

FACT (patch 0054, cci0 node):
```dts
clocks = <&gcc GCC_CAMSS_TOP_AHB_CLK>, <&gcc GCC_CAMSS_CCI_0_CLK>;
clock-names = "camss_top_ahb", "cci";
assigned-clocks = <&gcc GCC_CAMSS_CCI_0_CLK_SRC>;
assigned-clock-rates = <37500000>;
```
- `ftbl_gcc_camss_cci_0_clk_src` (FACT, gcc-sm6375.c): `F(37500000,
  P_GPLL0_OUT_EVEN, 8, 0, 0)` → integer /8 of GPLL0_EVEN (300 MHz), **50 % duty,
  clean**. Matches vendor `clock-rates = <0 37500000>` and
  `cci-clk-src = <37500000>` in every blair-camera.dtsi i2c-freq node
  (FACT, blair-camera.dtsi:170,196,211,241,263,…).
- Vendor uses the *same* two-clock model on the CCI node
  (`cci_0_clk` + `cci_0_clk_src`, FACT blair-camera.dtsi:164-168). rhodep's 2-clock
  list is the mainline `i2c-qcom-cci`/`msm8996-cci` binding shape and is correct;
  the vendor CAMX `camnoc/cpas/ahb` clocks live on the *cpas* node, not on CCI, and
  the mainline CCI driver does not need them.
- CCI source = 37.5 MHz was already the flashed live fix; EEPROM on the same bus
  reads perfectly. The CCI *bit clock* is therefore proven good.

**Nothing in the CCI clock wiring is wrong.** (The one open CCI item is the child
i2c-bus **speed mode** `clock-frequency = <1000000>`, but per patch 0054:146 that IS
present on `cci0_i2c1`, and context.txt confirms FAST_PLUS 1 MHz live. So that too is
handled.)

---

## 4. The CAMSS clock backport (patch 0053) — one REAL bug, but off-path

There is a genuine defect in patch 0053, but it is in the **VFE `clock_rate`
table**, which the sensor-identify path never touches:

FACT (patch 0053, `vfe_res_6375`, each VFE):
```c
.clock = { "top_ahb", "ahb", "axi", "vfe0", "camnoc_rt_axi", "camnoc_nrt_axi" },
.clock_rate = { {0}, {0}, {0},
                { 19200000, 300000000, 460800000, 576000000 },  /* vfe0 */
                {0}, {0}, },
```
But the DT (patch 0054) declares the camss `clock-names` order as
`… "camnoc_nrt_axi", "camnoc_rt_axi", …` — i.e. **nrt before rt** — whereas the
driver resource `.clock` list is `… "camnoc_rt_axi", "camnoc_nrt_axi"` (rt before
nrt). The camss driver matches clocks *by name*, so this ordering mismatch is
tolerated for enable/disable, but any code that indexes `clock_rate[]` positionally
against the DT order can mis-apply camnoc rates. INFERENCE: this can break
streaming/ISP bring-up later, and is worth fixing, **but it has zero effect on the
i2c identify** (VFE/CSID/camnoc are all downstream of the CSI receiver; identify is
pure CCI + MCLK + rails + reset).

No clock that the *identify* path needs is missing:
- Identify needs: CCI bit clock (✅ §3), CCI AHB (`camss_top_ahb`, ✅ present),
  MCLK to the sensor (✅ §1), sensor rails (✅ live), reset released (✅ live).
- The sensor's digital register domain is clocked **entirely by its own internal
  PLL locked to MCLK** — there is no AP-side "sensor register-domain gate" beyond
  MCLK. There is no additional GCC gate the backport could have forgotten that would
  wake the sensor's register block. (FACT — the S5KJN1 has one external clock input,
  MCLK; s5kjn1-mainline.c only ever touches `s5kjn1->mclk`, one clock, lines
  1234/1271/1308.)

**Verdict §4: patch 0053 has a cosmetic camnoc rt/nrt ordering wart that should be
fixed for streaming, but it is NOT on the identify path and cannot cause 0x9b05.**

---

## 5. Direct answers to the brief

1. **Is gpll9_out_main / 24 MHz the wrong/ noisy source?**
   No. It is the *only* legal 24 MHz source in the ftbl, it is the dedicated camera
   Zonda PLL (1440 MHz, same as sc7280 cam_cc_pll2), it yields exactly 24.000 MHz at
   ~50 % duty. Correct and equivalent to FP5. (FACT)

2. **Any clock missing / at wrong rate for CCI/CAMSS/sensor?**
   Identify path: nothing missing, nothing wrong (CCI 37.5 MHz clean integer /8;
   MCLK 24 MHz; AHB present). Streaming path: patch 0053's VFE `camnoc_rt/nrt`
   name-vs-rate ordering is inconsistent with patch 0054's `clock-names` — fix for
   ISP bring-up, irrelevant to identify. (FACT/INFERENCE)

3. **Single most likely clock-related fix?**
   *There isn't one that would fix identify* — the clocks are right. If you want a
   defensive, zero-risk hardening that provably changes nothing electrical but
   pins intent:
   ```dts
   /* rhodep camera@56 — make the (already-correct) parent explicit */
   assigned-clocks       = <&gcc GCC_CAMSS_MCLK1_CLK_SRC>;
   assigned-clock-parents = <&gcc GPLL9_OUT_MAIN>;   /* the only 24 MHz parent */
   assigned-clock-rates   = <24000000>;
   ```
   (Note: target the `_CLK_SRC` RCG, not the leaf `_CLK` gate, when asserting a
   parent.) This will *not* fix the residue; it only documents the parent.

   The genuinely actionable backport fix (for later streaming, not identify) is to
   align patch 0053's VFE `.clock` / `.clock_rate` camnoc ordering with patch 0054's
   `clock-names` (`camnoc_nrt_axi` before `camnoc_rt_axi`).

4. **If clocks check out, say so:**
   **They check out. This is a valid negative for the clock hypothesis.** MCLK
   parent, rate, source quality, and duty cycle are all correct and match the
   known-good FP5 requirement; the CCI bit clock is a clean integer /8 at 37.5 MHz;
   no identify-path clock is missing.

---

## 6. What is actually left (since the clock is exonerated)

Ranked, drawing on OUT_camx_seq.md and OUT_fp5_diff.md:

1. **Power/clock/reset SEQUENCING (highest).** Mainline `s5kjn1_power_on`
   (s5kjn1-mainline.c:1234-1239) does: enable MCLK → immediately deassert reset →
   10–15 ms settle. The vendor CAMX sequence (OUT_camx_seq.md:150-158) is a *reset
   pulse* (LOW 5 ms → HIGH 10 ms) with **MCLK enabled LAST, after reset release**,
   and a 10 ms VDIG settle. On rhodep the S5KJN1 core may need MCLK stable *before*
   reset deassert, or a real low→high reset pulse, to latch its OTP/trim and bring
   the register block out of its unloaded-shift-register state (which is exactly the
   0x9b05-type residue). This is the single most probable remaining cause.
   → Test: (a) add `usleep_range(5000,6000)` after `clk_prepare_enable(mclk)` before
   deasserting reset; (b) make reset a pulse (assert LOW 5 ms, release HIGH, wait
   10 ms); (c) try MCLK-after-reset (vendor order).

2. **CCI child-bus data-phase timing / speed mode (medium).** EEPROM (dumb, timing-
   tolerant) reads fine; the sensor's register state machine is less tolerant. The
   bus is FAST_PLUS 1 MHz live, matching FP5 — but confirm the *driver-bound*
   `cci0_i2c1` node actually carries `clock-frequency = <1000000>` at probe (patch
   0054:146 shows it does) so the kernel programs the FAST_PLUS hw-timing block, not
   a 100 kHz default. Cross-check the FAST_PLUS hw-thigh/tlow/tsu/thd values against
   blair-camera.dtsi:230-243 if you can override them.

3. **MCLK-run-time-before-first-read (low, clock-adjacent).** Some ISOCELL cores
   need MCLK toggling for N ms before the register interface answers. Mainline's
   10–15 ms post-reset settle *does* run with MCLK on, so this is likely satisfied,
   but folding it into the sequencing sweep (item 1) is free.

None of these are "the clock is wrong." They are timing/ordering around an otherwise
correct clock.

---

## 7. Evidence index (FACT sources)
- gcc-sm6375.c: `ftbl_gcc_camss_mclk0_clk_src` (24 MHz = `P_GPLL9_OUT_MAIN,1,1,15`);
  `gcc_camss_mclk1_clk_src` (`parent_map = gcc_parent_map_3`); `gpll9_config`
  (L=0x4B, zonda) → 1440 MHz; `post_div_table_gpll9_out_main {0x3,4}` → 360 MHz;
  `ftbl_gcc_camss_cci_0_clk_src` (37.5 MHz = `P_GPLL0_OUT_EVEN,8`); gpll9 used only
  by camera RCGs.
- camcc-sc7280.c: `ftbl_cam_cc_mclk0_clk_src` (24 MHz = `P_CAM_CC_PLL2_OUT_EARLY,
  10,1,6`); `cam_cc_pll2_config` L=0x4B zonda → 1440 MHz.
- clk-rcg2.c: `calc_rate` (rate = parent/pre_div × M/N); `__clk_rcg2_configure_mnd`
  (D-register/dual-edge duty programming, `CFG_MODE_DUAL_EDGE`);
  `clk_rcg2_get_duty_cycle` (≈50 % for these M/N).
- Patch 0053: `vfe_res_6375` clock/clock_rate camnoc rt/nrt ordering vs patch 0054
  `clock-names` order.
- Patch 0054: cci0 clocks/`assigned-clock-rates=37500000`; `cci0_i2c1`
  `clock-frequency=<1000000>`.
- Patch 0055: `camera@56` `assigned-clocks GCC_CAMSS_MCLK1_CLK` / 24000000, no
  `assigned-clock-parents`.
- s5kjn1-mainline.c: 1234-1239 (power-on MCLK→reset order), 1264-1288 (power-off),
  1308-1317 (single mclk, 24 MHz hard-gate).
- blair-camera.dtsi: 164-170 (vendor CCI 2-clock + 37.5 MHz), 230-243 (FAST_PLUS
  hw-timing), cpas node 420-448 (camnoc/ahb live on cpas, not CCI).
- context.txt: live parent gpll9_out_main / 24 MHz / EEPROM good / rails+reset good.
