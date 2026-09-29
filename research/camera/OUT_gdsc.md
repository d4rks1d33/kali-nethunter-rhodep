# OUT_gdsc.md — CAMSS GDSC / power-domain / CSIPHY dependency audit (rhodep S5KJN1)

Focus: is a CAMSS power-domain / GDSC / clock left OFF by the rhodep backport that
the sensor's digital core needs during probe?

**Bottom line up front (verdict): VALID NEGATIVE.** The one CAMSS power domain the
SM6375 camera block has — `CAMSS_TOP_GDSC` — is **ON** during probe (measured live),
and the mainline i2c-qcom-cci node in patch 0054 correctly declares it as its
`power-domains`. There is no second/hidden camera GDSC on this SoC (no camcc, no
titan/ife GDSC). The MCLK the sensor uses is gated only by `CAMSS_TOP_GDSC` +
`gcc_camss_mclk1_clk`, both of which are up. The GDSC/power path is not the missing
enable. What that leaves is stated at the end.

---

## 1. Vendor CAMSS/CSIPHY/CCI/GDSC dependency graph (blair-camera.dtsi)

### The single camera GDSC on SM6375
Every vendor camera node references **exactly one** power domain / GDSC:
`&gcc_camss_top_gdsc`. There is no separate titan/ife/camcc GDSC in the vendor tree —
SM6375 has no camcc, so all camera GDSCs collapse to the one GCC-hosted CAMSS_TOP_GDSC.

FACT — every vendor node's power supply is `gcc_camss_top_gdsc`:
- CSIPHY0..3: `gdscr-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:19, 55, 91, 127)
- CCI0: `gdscr-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:162)
- CCI1: `gdscr-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:255)
- CPAS: `camss-vdd-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:419)
- CPAS-CDM: `camss-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:657)
- OPE-CDM / OPE: `camss-supply = <&gcc_camss_top_gdsc>` (blair-camera.dtsi:679, 1045)
- TFE_CSID0/1/2, TFE0/1/2, TPG0/1: `camss-supply = <&gcc_camss_top_gdsc>`
  (blair-camera.dtsi:716, 752, 782, 818, 848, 884, 971, 998)

INFERENCE: there is no camera GDSC the vendor turns on that the mainline backport
cannot also turn on — they are the *same* GDSC. "camss_top_gdsc / titan / ife gdsc"
in the hypothesis are all one node here.

### Clocks the vendor CCI node needs (the sensor's I2C path)
FACT (blair-camera.dtsi:164-170):
- `GCC_CAMSS_CCI_0_CLK` (cci_0_clk)
- `GCC_CAMSS_CCI_0_CLK_SRC` (cci_0_clk_src) @ **37.5 MHz** (`clock-rates = <0 37500000>`)
- plus, implicitly, `gcc_camss_top_ahb_clk` for register access (the CPAS node lists
  `GCC_CAMSS_TOP_AHB_CLK` / `_SRC` at 80 MHz, blair-camera.dtsi:431-432).

### Clocks the sensor node itself needs (the MCLK path)
FACT (CAMERA-SENSORS-FEASIBILITY.md:1425-1427, blair sensor overlay):
- `cam_clk-supply = gcc_camss_top_gdsc` — the sensor's MCLK is powered by the SAME
  CAMSS_TOP_GDSC.
- `clock-rates = 24000000`, `clock-cntl-level = "turbo"`.
- The physical clock is **`gcc_camss_mclk1_clk`** (MCLK1 on tlmm 30, cci-master 1).

### Which of these gate the MCLK/sensor path
INFERENCE — only two things gate the sensor's clock domain:
1. `CAMSS_TOP_GDSC` (the power rail behind the whole CAMSS register/clock island).
2. `gcc_camss_mclk1_clk` (the actual 24 MHz pad clock), whose branch also sits behind
   CAMSS_TOP_GDSC in the GCC tree.
Everything else the vendor lists (CPHY, csiphytimer, tfe, csid, axi, camnoc, ope) is
**downstream data-path** clocking, not a prerequisite for the sensor's internal boot.

---

## 2. What the rhodep backport (0053/0054) enables — vs the vendor requirement

### The CCI node DOES declare the GDSC
FACT (0054-...patch:118-125):
```
cci0: cci@5c1b000 {
    compatible = "qcom,sm6375-cci", "qcom,msm8996-cci";
    ...
    power-domains = <&gcc CAMSS_TOP_GDSC>;      <-- present
    clocks = <&gcc GCC_CAMSS_TOP_AHB_CLK>,       <-- top_ahb present
             <&gcc GCC_CAMSS_CCI_0_CLK>;         <-- cci clk present
    clock-names = "camss_top_ahb", "cci";
    assigned-clocks = <&gcc GCC_CAMSS_CCI_0_CLK_SRC>;
    assigned-clock-rates = <37500000>;           <-- 37.5 MHz, matches vendor
};
```
This means when the i2c-qcom-cci driver probes, its `power-domains` reference makes
the PM core turn **CAMSS_TOP_GDSC ON** (genpd attach + runtime-PM get on probe/xfer).
So the CCI path *does* enable the GDSC the MCLK needs — it does not "come up without
its parent GDSC enabled."

Comparison vendor vs rhodep CCI:
| Item                | Vendor (blair)                | rhodep 0054            | Match |
|---------------------|-------------------------------|------------------------|-------|
| Power domain / GDSC | gcc_camss_top_gdsc            | CAMSS_TOP_GDSC         | YES   |
| CCI branch clk      | GCC_CAMSS_CCI_0_CLK           | GCC_CAMSS_CCI_0_CLK    | YES   |
| CCI src rate        | 37.5 MHz                      | 37.5 MHz (assigned)    | YES   |
| top_ahb clk         | GCC_CAMSS_TOP_AHB_CLK (@CPAS) | GCC_CAMSS_TOP_AHB_CLK  | YES   |

FACT: no GDSC, camnoc, cphy, csi-timer or top_ahb clock is *missing from the CCI +
sensor path*. The camnoc/axi/cphy/csitimer clocks live on the `camss@5c6e000` node
(0054:34-77) and on the CPAS node in the vendor tree; they are data-path and are not
required for the sensor to complete its internal boot / answer chip-id.

### Does the sensor's MCLK come from the CCI node or the camss node?
FACT: In mainline, the S5KJN1 is an i2c child of the CCI `i2c-bus@N` (a simple-bus),
and it grabs its own MCLK via `devm_v4l2_sensor_clk_get(dev, NULL)`
(s5kjn1-mainline.c:1308) → `clk_prepare_enable(s5kjn1->mclk)` (s5kjn1-mainline.c:1234).
The MCLK clock (`gcc_camss_mclk1_clk`) is a GCC clock whose enable path only requires
CAMSS_TOP_GDSC to be powered. Since the CCI parent holds CAMSS_TOP_GDSC on (via its
own power-domains), the GDSC is up while the sensor probes underneath it.

---

## 3. Is CAMSS_TOP_GDSC actually ON when only CCI runs (no camss/CSIPHY bound)? — LIVE

This is the decisive question, and it is answered by measurement, not inference.

FACT (CAMERA-SENSORS-FEASIBILITY.md:384): live `clk_summary` / genpd:
```
| CAMSS GDSC | camss_top_gdsc: on |
```
FACT (CAMERA-SENSORS-FEASIBILITY.md:383): `MCLK1 clk_enable_count = 1, clk_rate = 24000000`.
FACT (CAMERA-SENSORS-FEASIBILITY.md:1431): "the CAMSS GDSC is on regardless."
FACT (CAMERA-SENSORS-FEASIBILITY.md:1722): rails, **MCLK, GDSC**, reset, pin mux all
verified in their working state.

The camdiag rig does `clk_prepare_enable` on MCLK and holds the sensor powered; in that
state CAMSS_TOP_GDSC reads **on**. So even on the CCI-only / camdiag path (no camss or
CSIPHY subdev bound), the GDSC the MCLK needs is enabled. The hypothesis "the backport
leaves a GDSC off when only CCI+MCLK are up" is **directly falsified by live data**.

INFERENCE: this is expected — `gcc_camss_mclk1_clk` and `gcc_camss_cci_0_clk` share
the CAMSS_TOP_GDSC power domain; enabling either branch (which the CCI genpd/runtime-PM
does) is enough to bring the domain up. There is no distinct "sensor clock-domain GDSC"
on SM6375.

### Positive proof the clock domain behind MCLK is alive
FACT (CAMERA-SENSORS-FEASIBILITY.md:1707-1714): the chip-id residue **tracks MCLK
frequency**: 24.0 MHz → 0x8b05, 19.2 MHz → 0x8b21. A shift register clocked by MCLK is
producing output — i.e. MCLK is not only reaching the pad, it is clocking live logic
inside the die. If CAMSS_TOP_GDSC were off, MCLK would not even reach the pad and the
part would NAK (it NAKs with MCLK off — FEASIBILITY:1586-1587). The MCLK-tracking residue
is affirmative evidence the GDSC/MCLK clock domain is powered and gated correctly.

---

## 4. CSIPHY — does stock bring it up before/around sensor probe?

FACT (blair-camera.dtsi:9-151): the CSIPHY nodes consume CPHY_RX, CPHY_N, and
CSInPHYTIMER clocks and `gcc_camss_top_gdsc`. These are **MIPI D/C-PHY receive-lane**
resources — they clock the PHY that *receives* pixel data from the sensor.

INFERENCE: CSIPHY is a data-lane receiver. It is brought up as part of *streaming*
(post-identify), not as a precondition for the sensor's I2C chip-id read. The S5KJN1
mainline driver reads CHIP_ID immediately after `s5kjn1_power_on` (s5kjn1-mainline.c:1374)
with no CSIPHY interaction whatsoever, and FP5 — same driver, same silicon — identifies
the sensor with the identical no-CSIPHY-at-probe flow. The sensor does not receive a
clock or reference *from* the CSIPHY; MCLK is its only input clock and it comes from GCC.
FACT (FEASIBILITY:1832): "a CSIPHY/CSID init the stock does around probe" is listed as an
*unclosed candidate*, but the FP5 parity (identifies with no CSIPHY at probe) makes it
INFERENCE-unlikely to be the cause. CSIPHY is not a probe-time dependency.

UNKNOWN: whether stock CamX briefly touches CSIPHY GPIO/LDO (mipi-csi-vdd1/2 = L7A/L4A)
before probe. But those are PHY-side rails, not sensor-core rails, and FP5 does not need
them for identify.

---

## 5. The concrete "missing enable" — there isn't one on the GDSC/power path

**There is no missing GDSC / power-domain / clock enable on the sensor's clock domain.**
The one CAMSS GDSC is on, MCLK is enabled at 24 MHz, the CCI node correctly owns the
GDSC via `power-domains`, and the clock domain is provably live (residue tracks MCLK).

If you still want a *belt-and-braces DT change* to remove the last doubt (guarantee the
GDSC cannot be dropped, and pin the CCI top_ahb rate the vendor uses), the minimal,
low-risk, testable options are below. NONE of these is expected to fix identify — they
only close the "is the domain really up" question definitively.

### (a) Force CAMSS_TOP_GDSC always-on (diagnostic; rules the GDSC out for good)
In the GCC node, mark the GDSC always-on so it can never gate mid-probe:
```dts
&gcc {
    /* diagnostic: pin CAMSS_TOP_GDSC on across the whole probe window */
    /* implement by setting PWRSTS/ALWAYS_ON flag on the gdsc, e.g. in
       gcc-sm6375.c: camss_top_gdsc.pd.flags |= ALWAYS_ON; (driver change) */
};
```
Driver-side (clk/qcom/gcc-sm6375.c), one-line diagnostic:
```c
static struct gdsc camss_top_gdsc = {
    ...
    .flags = ALWAYS_ON,     /* was: 0 / VOTABLE — diagnostic only */
};
```
Testable: after this, `cat /sys/kernel/debug/clk/.../camss_top_gdsc` (or genpd
summary) must show `on` continuously; re-run camdiag — if residue is unchanged
(expected), the GDSC is conclusively exonerated.

### (b) Pin the CCI top_ahb / add camnoc AHB rate to match vendor (defensive)
Vendor runs `gcc_camss_top_ahb_clk_src` at 80 MHz (blair-camera.dtsi:441-443). If you
want parity, add to the cci node:
```dts
&cci0 {
    assigned-clocks = <&gcc GCC_CAMSS_CCI_0_CLK_SRC>,
                      <&gcc GCC_CAMSS_TOP_AHB_CLK_SRC>;
    assigned-clock-rates = <37500000>, <80000000>;
};
```
Testable: confirm `camss_top_ahb` rate in clk_summary; re-run identify. (Expected: no
change — top_ahb only clocks register access, which already works since the EEPROM and
CCI transactions succeed.)

### Why these will (almost certainly) not fix it — the valid negative
- CAMSS_TOP_GDSC is measured **on** (FEASIBILITY:384, 1431, 1722).
- MCLK is enabled at 24 MHz, count=1 (FEASIBILITY:383).
- The clock domain behind MCLK is **alive**: residue tracks MCLK rate (FEASIBILITY:1709-1710).
- The CCI node already owns the GDSC via `power-domains` (0054:122).
- SM6375 has **no second camera GDSC / no camcc** — nothing else to enable.
- FP5 identifies the same silicon with the same driver using the same
  "sensor gets MCLK, no CSIPHY at probe" flow.

---

## What the GDSC being fine actually leaves

The power/clock/GDSC/CSIPHY axis is exonerated. The failure is **inside the sensor die's
digital core boot**, downstream of a correctly powered and clocked pad:

FACT (FEASIBILITY:1701-1705): writes are not stored ("write 0x0a0a to 0x3000, read →
0x8b05"); the core neither latches boot writes nor stores anything.
FACT (FEASIBILITY:1712-1714): residue = a shift register clocked by MCLK with nothing
loaded behind it — I/O ring + MCLK gate alive, digital register block not.

This points away from CAMSS/GDSC and toward one of:
1. **The sensor's own core-supply integrity** — `/sys/class/regulator` "enabled" only
   means the PMIC bit is set, not that VDIG is actually at 1.05 V under the 1.2 A load
   the core draws (FEASIBILITY:1434-1435, 1724-1728). Needs a scope/meter on the flex —
   outside AP reach. This is the leading remaining hypothesis.
2. **The register-init table right after the power seq in the blob (@0x351bb)** may need
   to be pushed *before* the chip-id read on this specific module (FEASIBILITY:1833-1834).
3. **Chip-id read timing** — stock may insert a delay or read differently after
   power_on (FEASIBILITY:1831-1832).

None of these is a CAMSS power-domain / GDSC / CSIPHY problem.

---

## Evidence tags summary
- FACT: SM6375 has one camera GDSC (CAMSS_TOP_GDSC); every vendor camera node uses it
  (blair-camera.dtsi:19,55,91,127,162,255,419,657,679,716,752,782,818,848,884,971,998,1045).
- FACT: rhodep CCI node declares `power-domains = <&gcc CAMSS_TOP_GDSC>` and 37.5 MHz
  CCI src (0054:122, 126-127) — matches vendor.
- FACT: live CAMSS_TOP_GDSC = **on**; MCLK1 enabled @24 MHz, count=1
  (FEASIBILITY:383-384, 1431, 1722).
- FACT: residue tracks MCLK rate → clock domain behind MCLK is powered and clocking logic
  (FEASIBILITY:1709-1714).
- INFERENCE: CSIPHY is a post-identify data-lane resource; not a probe-time sensor
  dependency (blair-camera.dtsi:9-151; FP5 parity).
- INFERENCE/VERDICT: no missing GDSC/power-domain/clock on the sensor's clock domain.
  **Valid negative.** The blocker is the sensor die's internal core boot, most likely
  VDIG integrity under load — a hardware-domain issue outside AP software reach.
- UNKNOWN: whether stock briefly touches CSIPHY rails (L7A/L4A) pre-probe; FP5 parity
  makes this unlikely to matter.
