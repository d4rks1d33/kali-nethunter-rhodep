# CCI source-clock fix for rhodep (SM6375) camera bring-up

Fix per OUT_power_seq.md "Fix A": force the CCI I2C source RCG to 37.5 MHz so the
mainline `i2c-qcom-cci` timing table (calibrated for a 37.5 MHz CCI source clock)
samples the SDA data phase correctly. Without it, `GCC_CAMSS_CCI_{0,1}_CLK_SRC`
runs at its default 19.2 MHz XO (first freq_tbl entry), the data phase mis-samples,
and the sensor ACKs its address but chip-id reads return garbage (0x8b05 instead
of 0x38e1) while the more tolerant EEPROM still works.

## Exact lines added

To `cci0: cci@5c1b000` (right after `clock-names`, before `#address-cells`):

    assigned-clocks = <&gcc GCC_CAMSS_CCI_0_CLK_SRC>;
    assigned-clock-rates = <37500000>;

To `cci1: cci@5c1c000` (same placement):

    assigned-clocks = <&gcc GCC_CAMSS_CCI_1_CLK_SRC>;
    assigned-clock-rates = <37500000>;

The existing `clocks`/`clock-names` are unchanged — the CCI branch clock
`GCC_CAMSS_CCI_{0,1}_CLK` stays; `assigned-clocks` targets its parent RCG
`GCC_CAMSS_CCI_{0,1}_CLK_SRC`, which the gcc-sm6375 ftbl supports at
`F(37500000, P_GPLL0_OUT_EVEN, 8, 0, 0)`.

## Patch hunk header

Single hunk in the patch. Recomputed:
- Before: `@@ -1533,6 +1533,172 @@`
- After:  `@@ -1533,6 +1533,176 @@`

Old side (`-1533,6`) unchanged (only additions to new side). New side count
increased by 4 (2 lines added to each CCI node): 172 -> 176. Verified the hunk's
new-side body (`^[ +]` lines) is exactly 176.

## Validation (FACT)

Tree: fresh extract of `linux-motorola-rhodep-7.2_rc5.tar.gz` (linux-7.2-rc5),
patches 0001-0053 applied `patch -p1` in APKBUILD `source=` order — all applied
with no FAILED/rej.

Then the edited 0054:

    patch -p1 --dry-run < 0054-...patch
      checking file arch/arm64/boot/dts/qcom/sm6375.dtsi
    FAILED count: 0
    fuzz/offset: none

Applied for real; the resulting sm6375.dtsi shows:
- `cci@5c1b000`: `assigned-clocks = <&gcc GCC_CAMSS_CCI_0_CLK_SRC>;`
  `assigned-clock-rates = <37500000>;`
- `cci@5c1c000`: `assigned-clocks = <&gcc GCC_CAMSS_CCI_1_CLK_SRC>;`
  `assigned-clock-rates = <37500000>;`

## dt-binding macros (FACT)

From `include/dt-bindings/clock/qcom,sm6375-gcc.h` in the 7.2-rc5 tree:

    #define GCC_CAMSS_CCI_0_CLK         29
    #define GCC_CAMSS_CCI_0_CLK_SRC     30
    #define GCC_CAMSS_CCI_1_CLK         31
    #define GCC_CAMSS_CCI_1_CLK_SRC     32

Indices match the brief (30 and 32). Symbolic names used, not raw indices.

## No conflicting references (FACT)

`grep -rn GCC_CAMSS_CCI_[01]_CLK_SRC` over the qcom dts directory (with 0001-0053
applied) returns nothing outside these two new CCI nodes. No pre-existing
`assigned-clock-rates` targets these RCGs, so there is no conflict.

## FACT / INFERENCE / UNKNOWN

FACT
- The two `assigned-clocks`/`assigned-clock-rates` lines are added to both CCI
  nodes, placed after `clock-names`, before `#address-cells`.
- Existing `clocks`/`clock-names` lines are untouched.
- `patch -p1 --dry-run` on 0054 against the 0001-0053 tree = 0 FAILED, no
  fuzz, no offset.
- dt-binding macros exist with indices 30 (CCI_0_CLK_SRC) and 32 (CCI_1_CLK_SRC).
- No other DT reference sets a conflicting rate on these RCGs.
- gcc-sm6375 ftbl_gcc_camss_cci_0_clk_src includes a 37.5 MHz entry, so the RCG
  can be driven at 37500000.

INFERENCE
- Setting the RCG to 37.5 MHz makes the mainline i2c-qcom-cci hw_params timing
  match the hardware assumption, fixing the data-phase mis-sampling that yields
  the 0x8b05 chip-id garbage. (Root cause per OUT_power_seq.md; on-device
  confirmation via clk_summary still recommended.)

UNKNOWN
- On-device measured CCI core/source clock rate before/after (clk_summary) — the
  decisive datum; not measurable from the patch alone.
- Whether the correct source clock alone fully cleans the id read, or whether the
  i2c bus speed mode (Fix B) also needs sweeping.

## Notes
- Git repo and pmbootstrap were not touched. Validation was done in a throwaway
  extract under /tmp/camre4/validate.
- Fixed patch left at:
  /tmp/camre4/0054-arm64-dts-qcom-sm6375-add-camss-and-cci.patch
