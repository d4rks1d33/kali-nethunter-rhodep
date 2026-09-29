# 0056 rewrite — S5KJN1 vendor power-on sequence (MCLK first)

Rewrites `/tmp/camfix/0056-media-s5kjn1-vendor-power-order.patch` so
`s5kjn1_power_on()` matches the stock vendor bring-up **byte-exact** from the
sensormodule blob (`OUT_symbolref.md`, triplet array at file offset `0x35143`,
anchored by the unique MCLK config `24000000` @ `0x35147`).

The root cause of the bring-up failure (chip-id reads residue, not `0x38e1`):
mainline enabled the rails first and MCLK last. The vendor enables **MCLK
first, before any rail**. This patch reorders to the vendor sequence.

---

## 1. The rewritten sequence (conceptual diff)

### power_on — BEFORE (mainline, wrong)
```
regulator_enable(vddd)   + 1ms
regulator_enable(vdda)
regulator_enable(vddio)
regulator_enable(afvdd)
clk_prepare_enable(mclk)          <- MCLK LAST
gpiod_set_value(reset, 0)  + 10-15ms
```

### power_on — AFTER (vendor, blob 0x35143)
```
clk_prepare_enable(mclk)   + 1ms   <- MCLK FIRST (triplet 0: 24 MHz, 1 ms)
regulator_enable(vddio)    + 0ms   <- triplet 1: VIO on, 0 ms
regulator_enable(vddd)     + 1ms   <- triplet 2: VDIG on, 1 ms
regulator_enable(vdda)     + 1ms   <- triplet 3: VANA on, 1 ms
regulator_enable(afvdd)            <- AF actuator, NOT in blob seq; optional/last
gpiod_set_value(reset, 0)  + 4ms   <- triplet 4: RESET released, 4 ms, then id read
```
Reset stays a gpiod logical op: DT has `GPIO_ACTIVE_LOW`, requested
`GPIOD_OUT_HIGH` (asserted) at probe; `gpiod_set_value(...,0)` = deassert =
electrical high = running. No raw levels written.

### Error unwind (reverse of enable order)
```
disable_vdda:  regulator_disable(vdda)
disable_vddd:  regulator_disable(vddd)
disable_vddio: regulator_disable(vddio)
disable_mclk:  clk_disable_unprepare(mclk)
```
(afvdd on its own failure path just falls through to `disable_vdda`; it is
enabled last so nothing above it needs undoing when *it* fails.)

### power_off — mirror (blob triplets 5-9 @ 0x3517f)
```
gpiod_set_value(reset, 1)  + 1ms   <- RESET asserted, 1 ms
regulator_disable(afvdd)           <- undo the optional AF supply first
regulator_disable(vdda)            <- VANA off, 0 ms
regulator_disable(vddd)            <- VDIG off, 0 ms
regulator_disable(vddio)   + 1ms   <- VIO off, 1 ms
clk_disable_unprepare(mclk) + 1ms  <- MCLK off, 1 ms
```

Delays use `usleep_range(USEC_PER_MSEC, 2*USEC_PER_MSEC)` for the 1 ms steps and
`usleep_range(4*USEC_PER_MSEC, 5*USEC_PER_MSEC)` for the 4 ms post-reset settle,
matching the existing driver's style.

---

## 2. Validation

Env: `linux-motorola-rhodep-7.2_rc5.tar.gz` extracted; patches 0001-0055
applied with `patch -p1` in the APKBUILD `source=` order. (s5kjn1.c in the
pristine tarball is **identical** to `s5kjn1.c.with0055`; no patch 0001-0055
touches `drivers/media/i2c/s5kjn1.c` — 0055 only edits the rhodep .dts — so the
file is upstream at 0056 time, as stated.)

- **`patch -p1 --dry-run < 0056.patch` => exit 0, 0 FAILED.**
- Verbose: all three hunks succeed at **exactly** their headered lines with
  **no offset and no fuzz**:
  ```
  Hunk #1 succeeded at 1205.
  Hunk #2 succeeded at 1241.
  Hunk #3 succeeded at 1285.
  ```
- Hunk headers recomputed and confirmed by regenerating the diff mechanically
  (`diff -u`): `@@ -1205,10 +1205,34 @@`, `@@ -1217,46 +1241,41 @@`,
  `@@ -1266,23 +1285,29 @@`. Real-apply output byte-identical to the intended
  file.
- **Compiles:** the rewritten `s5kjn1_power_on` / `s5kjn1_power_off` bodies were
  compiled standalone with kernel-symbol stubs under
  `gcc -std=gnu11 -Wall -Wextra` => **exit 0, no warnings**. This confirms C
  well-formedness: balanced braces, every `goto` (`disable_mclk`,
  `disable_vddio`, `disable_vddd`, `disable_vdda`) resolves to an existing
  label, no orphaned labels, no unreachable/duplicate-label errors. (A full
  kernel object build was not run; the module was not wired into a kbuild
  tree — see UNKNOWN.)

---

## 3. SEPARATE recommendation — DT I2C frequency mode (NOT changed here)

**Recommended, for the human to decide — I did NOT edit patch 0054.**

The blob mandates `sensorI2CFrequencyMode` (id 3681) = **1 = FAST = 400 kHz**
(resolved instance value @ file `0x4147a`, `OUT_symbolref.md` §5). The current
DT (patch 0054) sets the sensor's CCI child bus to FAST_PLUS 1 MHz:

`0054-...patch:146`  `cci0_i2c1 { ... clock-frequency = <1000000>; }`

Recommended change (0054, do NOT apply blindly):
```
-				clock-frequency = <1000000>;   /* FAST_PLUS 1 MHz (our guess) */
+				clock-frequency = <400000>;    /* FAST, per blob id 3681 = 1 */
```
The `1000000` value was the port's guess (comment cites Fairphone FP5). The
blob's own resolved value is 400 kHz. Also update/remove the FAST_PLUS
justification comment at `0054-...patch:141-145` if the value is changed. This
is orthogonal to the power-order fix and can be tried independently.

---

## 4. FACT / INFERENCE / UNKNOWN

**FACT**
- s5kjn1.c in the pristine 7.2-rc5 tarball == `s5kjn1.c.with0055`; no patch
  0001-0055 modifies it (0055 is DT-only). So dry-running 0056 against the
  pristine driver == against the 0001-0055 tree.
- `patch -p1 --dry-run` of the rewritten 0056 = exit 0, 0 FAILED, no fuzz, no
  offset; hunks land at 1205/1241/1285.
- The rewritten power_on/power_off implement exactly the blob order:
  power-up MCLK(1ms)->VIO(0)->VDIG(1ms)->VANA(1ms)->RESET-release(4ms);
  power-down RESET(1ms)->VANA->VDIG->VIO(1ms)->MCLK(1ms).
- The two functions compile clean under `gcc -Wall -Wextra` with stubs; all
  gotos/labels resolve.
- Blob `sensorI2CFrequencyMode` id 3681 = 1 = FAST (400 kHz); current DT
  (0054) sets cci0_i2c1 to 1 MHz.

**INFERENCE**
- MCLK-first is the fix for the chip-id failure. This is stated in
  `OUT_symbolref.md` as INFERENCE (highest-probability), backed by the
  byte-exact FACT of the vendor order. Not yet confirmed on hardware here.
- `clk_set_rate(mclk, 24000000)` from the task target was intentionally
  **omitted**: probe already validates `clk_get_rate(mclk) ==
  S5KJN1_MCLK_FREQ_24MHZ` (s5kjn1.c:1313-1317) and errors out otherwise, so the
  24 MHz rate is guaranteed by DT/clock config before power_on runs. Adding a
  redundant `clk_set_rate` would be dead code and less upstreamable. If the
  maintainer prefers an explicit set, it is a one-line add before
  `clk_prepare_enable`; behaviourally identical.

**UNKNOWN**
- Full in-tree kernel compile / `modules_prepare` object build of s5kjn1.o was
  not performed (no configured kbuild tree was set up in this task; a standalone
  stubbed compile was used instead). Syntactic validity is confirmed; a real
  cross-build is the final gate.
- Whether afvdd/VAF is actually required for this module (not in the blob
  sequence; kept optional/-ENODEV-tolerant, enabled last / disabled first).
- On-hardware confirmation that 400 kHz (vs the current 1 MHz) is required —
  the blob says FAST; the FP5 comment says 1 MHz worked elsewhere. Left as a
  human decision, DT unchanged.
