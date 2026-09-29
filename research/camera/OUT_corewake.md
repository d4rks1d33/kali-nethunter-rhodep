# S5KJN1 rhodep — core-wake vs read-path: Q1 & Q2 resolved against fresh live data

Scope: everything AP-side is proven correct (CCI 37.5 MHz, EEPROM reads on the same
bus both Sr and STOP, MCLK 24 MHz, all rails, reset released, vendor order). Sensor
at 0x56 ACKs but returns unloaded-shift-register residue (0x8b01, mod-4 aliasing,
writes don't persist) in **every** i2c format, raw i2ctransfer included. The
Sr-vs-STOP theory is falsified live (both give 0x8b01). This report answers the two
open questions with that constraint respected and does not re-tread the ruled-out list.

Sources fetched live for this analysis:
- mainline `drivers/i2c/busses/i2c-qcom-cci.c` (torvalds/linux v6.12)
- mainline `drivers/media/v4l2-core/v4l2-cci.c` (v6.12)
- mainline `drivers/base/regmap/regmap-i2c.c` (v6.12)
- downstream `cam_cci_core.c`, `cam_cci_soc.c`, `cam_sensor_*.c` (provided)

---

## Q1 ANSWER — how mainline's CCI read path handles the register address

### The prior claim ("cci_i2c_read never sends the register address") is FALSE.

The register address **is** transmitted on mainline. The confusion comes from
looking only at `cci_i2c_read()` in isolation. A `cci_read()` register read is a
**two-message** i2c transfer and the address rides in the *first* message, which
`cci_xfer()` dispatches to `cci_i2c_write()`, not to `cci_i2c_read()`.

Trace, top to bottom:

1. **s5kjn1 driver** uses the generic v4l2 CCI regmap:
   FACT `s5kjn1-mainline.c:1303` `devm_cci_regmap_init_i2c(client, 16)`.
   FACT `v4l2-cci.c:186-194` builds the regmap config
   `{ .reg_bits = 16, .val_bits = 8, .reg_format_endian = REGMAP_ENDIAN_BIG,
   .disable_locking = true }`.

2. **cci_read** → `regmap_bulk_read(map, reg, buf, len)`:
   FACT `v4l2-cci.c:41` (a 16-bit BE chip-id read: `len=2`, big-endian recombine at
   `v4l2-cci.c:54` `get_unaligned_be16`).

3. **regmap bus selection**: the mainline CCI adapter advertises
   `I2C_FUNC_I2C` (FACT `i2c-qcom-cci.c` `cci_func()` returns
   `I2C_FUNC_I2C | I2C_FUNC_SMBUS_EMUL`). Therefore `regmap_get_i2c_bus()` selects
   `&regmap_i2c` (FACT `regmap-i2c.c:` `if (i2c_check_functionality(..., I2C_FUNC_I2C)) bus = &regmap_i2c;`),
   whose `.read = regmap_i2c_read`.

4. **regmap_i2c_read** emits a classic 2-message combined register read
   (FACT `regmap-i2c.c` `regmap_i2c_read`):
   ```
   xfer[0]: addr=0x56, flags=0        (WRITE), len=2, buf=<reg addr hi,lo>
   xfer[1]: addr=0x56, flags=I2C_M_RD (READ),  len=2, buf=<data>
   i2c_transfer(adap, xfer, 2);
   ```
   So the **register address is present**, as a preceding write message.

5. **cci_xfer** iterates the two messages and runs each as its **own complete CCI
   queue operation** (FACT `i2c-qcom-cci.c` `cci_xfer`):
   ```c
   for (i = 0; i < num; i++) {
       if (msgs[i].flags & I2C_M_RD)
           ret = cci_i2c_read(cci, master, msgs[i].addr, msgs[i].buf, msgs[i].len);
       else
           ret = cci_i2c_write(cci, master, msgs[i].addr, msgs[i].buf, msgs[i].len);
   }
   ```
   - msg[0] → `cci_i2c_write()`: loads `SET_PARAM | (slave<<4)`, then
     `CCI_I2C_WRITE | len<<4` + the two register-address bytes, then
     `CCI_I2C_REPORT` and runs QUEUE_0. FACT `i2c-qcom-cci.c` `cci_i2c_write`.
     The trailing `CCI_I2C_REPORT` terminates the queue → **a STOP is emitted after
     the address write.**
   - msg[1] → `cci_i2c_read()`: loads `SET_PARAM | (slave<<4)` then
     `CCI_I2C_READ | len<<4` and runs QUEUE_1. It does **not** re-send the register
     address (correct — it was already sent by msg[0]). FACT `i2c-qcom-cci.c`
     `cci_i2c_read`.

### So what actually differs from downstream?

Downstream fuses address-write + data-read into **one** locked CCI queue run with the
STOP suppressed:
FACT `cam_cci_core.c:1030-1089` (`cam_cci_read`):
```
SET_PARAM_CMD (sid,retries,id_map)              :1030
LOCK_CMD                                          :1041
WRITE_DISABLE_P_CMD | addr_type<<4 | <addr>      :1050   <- write reg addr, NO STOP
READ_CMD | num_byte<<4                             :1064   <- repeated-START read
UNLOCK_CMD                                        :1073
QUEUE_START (one run)                             :1089
```
`WRITE_DISABLE_P_CMD` = "write these bytes, do not emit a STOP." One queue run =
`START, slaveW, regHi, regLo, Sr, slaveR, data, STOP`.

Mainline = `START, slaveW, regHi, regLo, STOP` … `START, slaveR, data, STOP`
(two queue runs, STOP between).

**BUT** — and this is decisive given the fresh data — the Sr-vs-STOP difference was
tested live and **both give 0x8b01** (context_v3:17). And the raw i2ctransfer that
returns 0x8b01 is itself a combined (Sr) transfer
(`i2ctransfer w2@0x56 0x00 0x00 r2`, OUT_s5kjn1_bringup.md:232). So the framing
difference is real code but is **not** what makes the sensor return residue. The
sensor returns residue to *every* framing. **Q1's read-framing is therefore not the
blocker.** (This corrects OUT_identify.md, whose Sr/STOP theory is now falsified.)

### Then why does the driver read 0 while raw i2ctransfer reads 0x8b01?

This is NOT a "the address was never sent" bug, and it is NOT a fatal read-path bug.
The mechanism is mundane:

- FACT: the log line is `chip id mismatch: 38e1!=0` (`s5kjn1-mainline.c:1149`,
  format `"chip id mismatch: %x!=%llx"`, args `S5KJN1_CHIP_ID, val`). For that
  branch to run, `cci_read` must have returned **success** (`ret==0`) — the error
  branch (`s5kjn1-mainline.c:1143-1145`) prints "failed to read chip id" instead.
- FACT: `cci_read` pre-zeroes `*val = 0` (`v4l2-cci.c:32`) and only overwrites it on
  a successful `regmap_bulk_read`. A success with `val==0` means the two data bytes
  returned were `00 00`.
- The raw i2ctransfer that returned 0x8b01 was run **standalone, once, from a clean
  bus**. The driver's read runs **inside probe**, immediately after the CCI
  controller reset/init and possibly after other traffic on the shared bus (EEPROM
  probe on the same master).

INFERENCE (well-supported by the log evidence and by OUT_s5kjn1_bringup.md:190-193
"value depends on preceding bus traffic"): the residue is the state of an internal,
un-driven node. It is **not** a stored register value, so its readout is sensitive to
what happened on the bus immediately before. The driver path and the interactive
i2ctransfer path perturb that node differently (different inter-transaction gaps,
different immediately-preceding traffic, CCI FIFO left in a different state), so one
resolves to `0x0000` and the other to `0x8b01`. Both are "wrong"; the difference
between them is noise on a floating node, **not** a deterministic driver defect.

Corroboration that this is residue and not a decode: mod-4-word aliasing
(`0x0004` aliases `0x0000`, an 8-byte window) and the byte-shifted stream
`8b 05 be af …` where each read's tail leaks into the next
(FEASIBILITY.md:462-467). A real register file cannot alias mod-4-words or leak a
previous read's tail byte.

### Concrete Q1 fixes

Because Q1 is not the blocker, these are correctness/robustness fixes, not the cure.
Do them so that *once the core is awake* (Q2) the read is framed exactly like the
vendor and is robust:

**Fix Q1-a (the substantive, upstreamable one): fuse a {write,read} msg pair into a
single CCI queue run with STOP suppressed**, mirroring downstream
`WRITE_DISABLE_P + READ`. Patch `i2c-qcom-cci.c` `cci_xfer` so that when it sees
`msgs[i]` = write immediately followed by `msgs[i+1]` = read to the same address, it
issues them as one queue: load `SET_PARAM`, `CCI_I2C_WRITE | reglen<<4` + reg bytes
**without** the trailing `CCI_I2C_REPORT`, then `CCI_I2C_READ | len<<4`, then one
`CCI_I2C_REPORT` + one `QUEUE_START`. Sketch:

```c
/* in cci_xfer(), replace the naive per-msg loop with pair detection */
for (i = 0; i < num; i++) {
    if (i + 1 < num &&
        !(msgs[i].flags & I2C_M_RD) &&
        (msgs[i+1].flags & I2C_M_RD) &&
        msgs[i].addr == msgs[i+1].addr) {
        ret = cci_i2c_write_read(cci, master, msgs[i].addr,
                                 msgs[i].buf, msgs[i].len,      /* reg addr, no STOP */
                                 msgs[i+1].buf, msgs[i+1].len); /* data, Sr */
        i++;                 /* consumed two msgs */
    } else if (msgs[i].flags & I2C_M_RD) {
        ret = cci_i2c_read(cci, master, msgs[i].addr, msgs[i].buf, msgs[i].len);
    } else {
        ret = cci_i2c_write(cci, master, msgs[i].addr, msgs[i].buf, msgs[i].len);
    }
    if (ret < 0) break;
}
```
`cci_i2c_write_read()` = `cci_i2c_write`'s body minus the terminating
`CCI_I2C_REPORT`, then append the `CCI_I2C_READ | len<<4` word, then a single
`CCI_I2C_REPORT | CCI_I2C_REPORT_IRQ_EN`, one `QUEUE_START`, and drain the read FIFO
exactly as `cci_i2c_read` does (skip the first echo byte, `words_exp = len/4 + 1`).
This makes mainline emit the identical `Sr, no-STOP` waveform the vendor uses.
(FACT the vendor sequence to mirror: `cam_cci_core.c:1050-1089`.)

**Fix Q1-b (cheap, do too): pin the CCI master to the sensor's freq mode.** Mainline
picks the master mode only from the child i2c-bus `clock-frequency`
(FACT `i2c-qcom-cci.c` `cci_probe`: `I2C_MAX_FAST_MODE_FREQ`→FAST,
`I2C_MAX_FAST_MODE_PLUS_FREQ`→FAST_PLUS, else STANDARD). Downstream selects the mode
from the blob's `sensorI2CFrequencyMode` and reprograms the SCL/SDA timing block on
every read (FACT `cam_cci_core.c:982` `cam_cci_set_clk_param` → `:662-695`). Set the
mainline cci0 i2c-bus that hosts 0x56/0x50 to `clock-frequency = <1000000>`
(FAST_PLUS) and, as a separate test, `<400000>` (FAST). The per-mode timing tables
are byte-identical between mainline `cci_v2_data` and vendor
`blair-camera.dtsi:185-243`, so this only changes *which* table is used, i.e. the SCL
rate. (INFERENCE: JN1-class modules run FAST/FAST_PLUS, not STANDARD.)

Neither Q1 fix will, by itself, turn residue into 0x38e1 — the live evidence
(residue in every framing, EEPROM fine on the same bus) says the sensor's register
interface is not serving real data yet. That is Q2.

---

## Q2 ANSWER — what wakes the sensor's digital core that mainline omits

Because raw i2ctransfer *also* returns residue, the fault is on the sensor side of
the wire: its **digital top / register block is powered enough to ACK and hold state
but is not clocked/released to serve registers.** The I/O ring (pads, slave-address
matcher, MCLK gate) is on VDDIO and alive (EEPROM works, 0x56 ACKs). Everything the
AP *directly* controls is correct. So the missing step is something the vendor stack
does that mainline does not — and the most important structural fact is:

> FACT (`OUT_power_seq.md:50-57`, `cam_sensor_util.c:1383-1422`): the rear sensor's
> **power-up sequence is supplied by CamX/userspace as a command buffer**, not by the
> DT node and not by the sensormodule blob (the node has no `qcom,cam-power-seq-*`;
> the blob carries no `powerUpSequence`). Delays are attached from trailing WAIT ops.
> **This sequence is invisible to mainline and is the single largest unknown.**

Yet `cam_sensor_match_id` reads the id **cold, with zero register writes**
(FACT `cam_sensor_core.c:769-774`, and 789-794). So whatever CamX does, it is
rails/GPIO/clock/**delays only** — no magic init write. That means the wake is a
*physical* sequencing detail we have not exactly reproduced, OR a fabric/clock the
SoC brings up that the sensor's PLL lock depends on.

Ranked candidates, each with a concrete testable change:

### C1 (TOP) — MCLK must be present and stable *long enough before* reset release, and reset must be a real pulse, with CamX's exact delays.
The one thing we cannot see is CamX's exact delay pattern. Mainline releases reset
just 10-15 ms after enabling MCLK with **no explicit MCLK-settle before reset**
(FACT `s5kjn1-mainline.c:1234-1239`: `clk_prepare_enable(mclk)` immediately followed
by `gpiod_set_value(reset,0)` then a single 10-15 ms sleep *after*). The sensor's
internal PLL locks off MCLK; if reset de-asserts before the PLL is locked, the
digital top comes out of reset unclocked → exactly the "powered, holds state, not
clocked" residue signature.

Testable (rig, no rebuild) — insert a real MCLK-before-reset settle and a reset
pulse, then cold-read:
```sh
# camdiag holds rails; drive MCLK first, wait, then pulse reset
# (adapt to your rig's gpio/clk controls)
# 1) ensure MCLK 24MHz running >= 10 ms  BEFORE reset
# 2) reset LOW 10 ms, HIGH (released), wait 20-30 ms
# 3) cold read
i2ctransfer -f -y 4 w2@0x56 0x00 0x00 r2      # expect 0x38e1 if this was it
```
Driver change if it works: in `s5kjn1_power_on`, add an explicit
`usleep_range()` MCLK-settle *between* `clk_prepare_enable(mclk)` and reset release
(e.g. 5-10 ms), and make reset a **pulse** (assert low, delay, release) rather than a
static release. Sweep the post-reset settle 20/30/50 ms.
INFERENCE: highest-value because it is the exact axis CamX controls and we don't see.

### C2 — Reproduce CamX's power buffer delays precisely by decoding them from the stock trace, not guessing.
Because the sequence is a CamX command buffer, the surest fix is to capture the stock
`power_setting[]` (seq_type + config_val + accumulated delay) that CamX pushes for
this sensor and transcribe it verbatim into `s5kjn1_power_on`. This is the only
avenue that carries genuinely new information about the missing step.
Testable: on stock, trace `cam_sensor_core_power_up` (ftrace the `CAM_DBG` at
`cam_sensor_util.c:2061` "seq_type %d" and the delay applied at `:2218-2222`), then
replicate the exact ordered (rail/gpio/clk, delay) tuples on mainline.
FACT the executor honoring those delays: `cam_sensor_util.c:2051-2223`.

### C3 — Vote CPAS/CAMNOC + camss AHB before the id read.
FACT `cam_cci_soc.c:140-153`: downstream `cam_cci_init` calls `cam_cpas_start` with
an **AHB vote (`CAM_LOWSVS_VOTE`)** and an **AXI/CAMNOC bandwidth vote
(`CAM_CPAS_DEFAULT_AXI_BW`)** *before* any I2C transaction, plus enables platform
resources at `CAM_LOWSVS_VOTE` (`:158`). Mainline `i2c-qcom-cci` votes **nothing** on
CPAS/CAMNOC — it only `clk_bulk_prepare_enable`s its own CCI clocks
(FACT `i2c-qcom-cci.c` `cci_enable_clocks`).
INFERENCE: CPAS/CAMNOC is SoC-internal fabric, not a sensor supply, so this is
unlikely to wake external silicon — BUT it is a real, untested delta and it is what
the vendor always does before touching the bus. If C1/C2 fail, ensure the mainline
`camss`/`cci` GDSC + CAMNOC path is powered (e.g. camss driver bound, runtime-PM
resumed) before probe. Testable: confirm `camss` is bound and its GDSCs on (check
`/sys/kernel/debug/clk/clk_summary` and genpd) at the moment probe reads the id; if
not, bind camss first.

### C4 — Sweep i2c freq mode to FAST_PLUS/FAST during the wake test (pairs with Q1-b).
1 MHz was never tried on-device. The SCL rate changes the data-phase sampling window;
combine with C1. `clock-frequency = <1000000>` then `<400000>` on the cci0 bus.
FACT mode selection: `i2c-qcom-cci.c` `cci_probe`.

### C5 (RULED OUT as the mechanism, do not repeat) — indirect-port "boot the core" writes.
The `0x6028=0x4000; 0x001e=0x0007; 0x6010=0x0001; 0x6226=0x0001` wake was already
tried on-device with **no effect**, and writes don't persist
(context_v3:18,20). Consistent with the register interface being dead, not merely
asleep-behind-a-boot-bit. Do not spend more effort here until C1/C2 change the cold
read.

### What downstream does NOT do before identify (so mainline need not either)
FACT: no CSIPHY/CSID/VFE streaming block is brought up to identify — match_id runs
after only power-up + CCI init (`cam_sensor_core.c:769-774`; init is
`camera_io_init`→`cam_cci_init`, `cam_cci_soc.c:85`). MCLK is single-rate 24 MHz
(overlay:460). So the sensor is expected to identify on rails+MCLK+reset+CCI alone —
which is why the delay/sequencing (C1/C2) is the prime suspect, not a CAMSS block.

---

## Q1 vs Q2 — which is the blocker, and test order

**Q2 is the blocker.** The decisive datum is that **raw i2ctransfer also returns
residue in every framing** (Sr and STOP alike). If Q1's read path were the cause, a
correctly-framed raw combined read would return 0x38e1; it does not. The sensor's
register interface is not serving real data regardless of how the AP frames the read.
Q1 is a *separate, real* mainline stylistic difference (STOP-between vs Sr-fused, plus
freq-mode-not-pinned) that must be fixed for a clean vendor-identical read, but it
does not explain the residue and will not by itself yield 0x38e1.

The "driver reads 0, raw reads 0x8b01" asymmetry is a red herring: both are residue
off an un-driven internal node (the value tracks preceding bus traffic); the driver's
`cci_read` succeeded and returned 0x0000, the interactive tool happened to latch
0x8b01. Same class of garbage, different noise.

**Are they linked?** Only weakly. The core-wake does NOT require a register write
(match_id is cold — `cam_sensor_core.c:769-774`), so the "writes don't persist →
can't wake" chicken-and-egg does not actually gate identification. The wake is a
physical power/clock/reset **sequencing** matter (C1/C2), independent of the read
path. Fixing Q1 without Q2 = still residue; fixing Q2 without Q1 = likely readable
(the EEPROM proves the mainline CCI read path returns correct bytes from a live
slave), but do Q1-b (freq mode) alongside for margin.

### Recommended test order (rig, cheapest-first)
1. **C1** — MCLK-settle-before-reset + reset pulse + post-reset settle sweep, then
   cold `i2ctransfer w2@0x56 0x00 0x00 r2`. (No rebuild; pure sequencing.)
2. **C4/Q1-b** — repeat C1 at `clock-frequency=<1000000>` then `<400000>`.
3. **C2** — ftrace the stock CamX power buffer, transcribe exact (seq,delay) tuples
   into `s5kjn1_power_on`, retest. (The definitive, information-bearing step.)
4. **C3** — ensure camss/CPAS/CAMNOC fabric powered before probe; retest.
5. Only if 0x38e1 appears: land **Q1-a** (Sr-fused combined read in `cci_xfer`) so the
   driver read is vendor-identical and robust, plus keep Q1-b freq mode.

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- Mainline sends the register address: regmap 2-msg read
  (`regmap-i2c.c regmap_i2c_read`), address in `xfer[0]` (write),
  dispatched by `cci_xfer` to `cci_i2c_write`; `cci_i2c_read` correctly omits it
  (`i2c-qcom-cci.c` `cci_xfer`/`cci_i2c_read`/`cci_i2c_write`). The
  "never sends the address" claim is wrong.
- CCI adapter advertises `I2C_FUNC_I2C` → regmap uses `&regmap_i2c` full-I2C path
  (`i2c-qcom-cci.c cci_func`, `regmap-i2c.c regmap_get_i2c_bus`).
- regmap config: reg_bits=16, val_bits=8, BE (`v4l2-cci.c:186-194`).
- Mainline runs each i2c_msg as its own STOP-terminated CCI queue; downstream fuses
  addr-write+read in one queue with STOP suppressed
  (`i2c-qcom-cci.c cci_xfer` vs `cam_cci_core.c:1050-1089`).
- Sr-vs-STOP falsified live: both give 0x8b01 (context_v3:17); raw combined read also
  residue (OUT_s5kjn1_bringup.md:232 + context_v3:13-17).
- Driver log `38e1!=0` came from the *mismatch* branch → `cci_read` returned success
  with val=0 (`s5kjn1-mainline.c:1148-1150`, `v4l2-cci.c:32`).
- Residue signature: mod-4-word aliasing + byte-shifted stream + value tracks
  preceding bus traffic (FEASIBILITY.md:462-467, OUT_s5kjn1_bringup.md:190-193).
- match_id reads id cold, no register writes (`cam_sensor_core.c:769-774,789-794`).
- Rear power-up sequence is a CamX command buffer, not DT/blob; delays from WAIT ops
  (`OUT_power_seq.md:50-57`, `cam_sensor_util.c:1383-1422`, executor `:2051-2223`).
- Downstream `cam_cci_init` votes CPAS AHB(`LOWSVS`) + AXI/CAMNOC before any I2C
  (`cam_cci_soc.c:140-159`); mainline CCI votes none (`i2c-qcom-cci.c
  cci_enable_clocks`).
- Mainline power_on: MCLK enable immediately followed by reset release, single
  10-15 ms settle after (`s5kjn1-mainline.c:1234-1239`).
- No CSIPHY/CSID/VFE needed to identify (`cam_sensor_core.c:769-774`).
- Per-mode CCI timing tables identical mainline `cci_v2_data` == vendor
  `blair-camera.dtsi:185-243`.

**INFERENCE**
- Q2 (digital top powered but not clocked/released) is the blocker; residue in every
  framing rules Q1 out as the cause.
- The 0-vs-0x8b01 split is noise on an un-driven node, not a driver defect.
- Most likely missing step: MCLK-stable-before-reset settle and/or a reset pulse with
  CamX's exact delays (C1/C2), since the wake needs no register write.
- Downstream i2c freq mode is FAST or FAST_PLUS; mainline defaults STANDARD.

**UNKNOWN (resolve on device)**
- CamX's exact power-buffer (ordered seq_type + config_val + delay) — the biggest gap;
  get it by ftracing `cam_sensor_core_power_up` on stock.
- Exact numeric `sensorI2CFrequencyMode` (blob field undecoded).
- Whether C1 alone yields 0x38e1, or C1+C2 (and/or C3) are jointly required.
- Whether mainline camss/CPAS/CAMNOC fabric is actually powered at the instant probe
  reads the id.
