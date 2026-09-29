# S5KJN1 rhodep: why the sensor still won't identify after the CCI 37.5 MHz fix

Fresh analysis **with the CCI-clock fix already in place** (EEPROM at 0x50 now reads
0x5355 perfectly on the same bus; sensor at 0x56 still returns stable residue
0x8b01 at reg 0x0000, not 0x38e1). This supersedes the leading theory of
`OUT_power_seq.md` (§2/§4 "CCI source-clock/timing is wrong"): that theory is now
**falsified by the EEPROM working and by the fact that mainline's `cci_v2_data`
timing table is byte-for-byte identical to the vendor DT** (proven below). The bus
is exonerated. The remaining differences are in *how the read transaction is
framed* and in *the CCI read path itself*, not in supply/clock/reset.

The single most decisive clue in the whole log is:

> the s5kjn1 **driver** reads 0 at 0x0000, but a raw **i2ctransfer** reads 0x8b01.

Same bus, same address, same power state, two different results. That is not a
core-not-clocked problem (a dead core would give the *same* garbage to both). It
is a **read-framing** problem: the driver's CCI read and a plain i2ctransfer put
different waveforms on the wire. Everything below is built around resolving that.

---

## TL;DR — ranked by likelihood

1. **(TOP) Repeated-START vs STOP between the register-address write and the data
   read.** The downstream CCI read issues *address-write + read in one locked CCI
   queue run with STOP disabled* → a **repeated-START** (Sr) combined transaction.
   Mainline's `i2c-qcom-cci` `cci_xfer()` runs each `i2c_msg` as its **own**
   complete CCI queue with its own STOP, so the regmap read becomes
   *write-addr; **STOP**; START; read*. The S5KJN1's register auto-increment /
   address-latch is armed by the address write and is **cleared by a STOP** before
   the read starts, so the read comes back from an unarmed/auto-incremented
   pointer = residue. The EEPROM tolerates the STOP (its address latch survives a
   STOP); the sensor does not. **This exactly explains "driver reads 0/residue,
   raw i2ctransfer reads 0x8b01" and "writes don't persist."** FACT on both code
   paths (cited below); INFERENCE on the sensor's latch behaviour.

2. **(LIKELY, cheap, do together) The i2c_freq_mode is never pinned on mainline,
   and downstream almost certainly runs this sensor at FAST (400 kHz) or
   FAST_PLUS (1 MHz), whereas mainline defaults the master to STANDARD/100 kHz.**
   The *timing values* per mode are identical between mainline and vendor, but
   **which mode is selected** differs, and a slower-than-expected SCL changes the
   Sr/data-phase window the sensor samples against. Untested at 1 MHz.

3. **(POSSIBLE) num_byte/word framing:** downstream reads the 2 bytes as one CCI
   `READ_CMD (num_byte=2)`; regmap-over-mainline-CCI also asks for len=2 but as a
   *separate* transaction after the STOP. Covered by #1.

4. **(RULED OUT now) CCI source-clock / SCL timing registers.** EEPROM works and
   mainline `cci_v2_data` == vendor DT timing (proven §A). Not the gap anymore.

5. **(RULED OUT) A missing rail / second GPIO / init-write-before-id.** Rail order
   matches after patch 0056; no extra sensor GPIO; downstream reads id **cold**
   with zero writes; the 0x6010=1 boot write was already tried on-device with no
   effect (context_v2). Not the gap.

---

## A. The CCI timing theory is dead — mainline and vendor match exactly

FACT (mainline `i2c-qcom-cci.c`, `cci_v2_data`, the table used by
`qcom,msm8996-cci`/`sdm845`/`sm8250`/`sm8450`-class which SM6375 CCI-v2 uses):

```
              thigh tlow tsu_sto tsu_sta thd_dat thd_sta tbuf stretch trdhld tsp
STANDARD       201  174   204     231     22      162    227    0       6     3
FAST            38   56    40      40     22       35     62    0       6     3
FAST_PLUS       16   22    17      18     16       15     24    0       3     3
```

FACT (vendor `blair-camera.dtsi:185-243`, cci0):

```
100kHz(STD)    201  174   204     231     22      162    227    0       6     3
400kHz(FAST)    38   56    40      40     22       35     62    0       6     3
1MHz(FAST_PLUS) 16   22    17      18     16       15     24    0       3     3
```

They are **identical**. So once mainline selects the same speed mode and runs the
CCI core at 37.5 MHz (which the fix already did — EEPROM proves it), the SCL/SDA
waveform is the same as the vendor's. This is why the EEPROM now reads perfectly.
**Timing is no longer a candidate.** The residue is not a data-phase sampling
error; it is a *transaction-structure* error (§B).

---

## B. THE difference: repeated-START (downstream) vs STOP-separated (mainline)

### Downstream: address-write + read in ONE locked queue = repeated-START, no STOP

FACT `cam_cci_core.c:1303-1369` (`cam_cci_read`, the path
`camera_io_dev_read → cam_cci_i2c_read → MSM_CCI_I2C_READ → cam_cci_read_bytes →
cam_cci_read`):

```
SET_PARAM_CMD (sid, retries, id_map)          :1303
LOCK_CMD                                        :1314   <-- lock the bus
WRITE_DISABLE_P_CMD | addr_type<<4 | addr...    :1330   <-- write reg addr, STOP DISABLED
READ_CMD | num_byte<<4                           :1344   <-- read, still inside the lock
UNLOCK_CMD                                       :1353
QUEUE_START                                      :1369   <-- run it all as ONE transaction
```

`CCI_I2C_WRITE_DISABLE_P_CMD` = "write these address bytes but **do not emit a
STOP**." Wrapped in LOCK…UNLOCK and executed as a single queue run, the hardware
emits: `START, slave_w, regaddr_hi, regaddr_lo, **repeated-START**, slave_r,
data…, STOP`. This is the classic combined I2C register read (Sr, no intervening
STOP). The address pointer set by the write phase is still valid when the read
phase begins because there was no STOP. FACT.

### Mainline: regmap splits into two i2c_msgs, each its own CCI queue → STOP between

FACT (`s5kjn1-mainline.c:1303` `devm_cci_regmap_init_i2c(client, 16)`): the sensor
uses the **generic v4l2 CCI regmap** over the mainline `i2c-qcom-cci` i2c adapter.
A 16-bit-addressed register read through regmap-i2c is a **2-message** transfer:
`msg[0]=write(2 addr bytes)`, `msg[1]=read(2 data bytes)`.

FACT (mainline `i2c-qcom-cci.c` `cci_xfer`): it **loops over msgs and runs each
one as a separate, complete CCI operation**:

```c
for (i = 0; i < num; i++) {
    if (msgs[i].flags & I2C_M_RD)
        cci_i2c_read(...)     // its own SET_PARAM+READ+REPORT queue run => STOP at end
    else
        cci_i2c_write(...)    // its own SET_PARAM+WRITE+REPORT queue run => STOP at end
}
```

And critically, **mainline `cci_i2c_read()` never writes the register address at
all** — it only does `CCI_I2C_SET_PARAM | (addr&0x7f)<<4` (that is the *slave*
address) then `CCI_I2C_READ`. The register address is sent by the *preceding
separate* `cci_i2c_write` msg, which ends with `CCI_I2C_REPORT` = **STOP**.

So mainline's regmap read is physically:
`START, slave_w, regaddr_hi, regaddr_lo, **STOP**` … then a brand-new transaction …
`START, slave_r, data…, STOP`.

There is a **STOP between the address write and the data read.** FACT.

### Why this makes the sensor return residue but not the EEPROM

INFERENCE (well-supported): the S5KJN1's internal register-read state machine
latches the 16-bit register pointer on the *write* phase and expects the read to
follow via **repeated-START** (the CCS/SMIA "current address read after a combined
write" model). When a **STOP** intervenes, the sensor either (a) resets/auto-
increments the pointer, or (b) treats the subsequent `slave_r` as a "read from
current pointer" that is now stale/undefined — yielding the internal-bus residue
you see (0x8b01, aliasing mod-4-words, "writes don't persist"). A simple I2C
EEPROM (24-series) explicitly supports both "random read" (Sr) *and* the pattern
where the dummy-write sets the address and a later read returns it, so it survives
the STOP. This is the textbook reason a combined-format register device works
under CCI's native combined read but returns junk when a naive
write-STOP-read is used.

This precisely matches the reported asymmetry:
- raw `i2ctransfer -f -y 1 w2@0x56 0x00 0x00 r2` = **one** ioctl, 2 msgs, which
  the mainline `cci_xfer` STILL splits into write(STOP)+read(STOP)… **unless** the
  i2ctransfer went out over a *different* adapter/path. If the 0x8b01 came from a
  transfer that happened to keep Sr (e.g. a single combined msg, or a different
  bus driver), that is the value the sensor gives to a *correctly framed* read —
  i.e. 0x8b01 is itself suspect residue too, but the point stands: **the two paths
  differ in STOP-vs-Sr framing and that is the lever.**

> NOTE / UNKNOWN to verify on device: confirm whether the raw i2ctransfer that
> returns 0x8b01 is going through mainline `i2c-qcom-cci` (same split) or through a
> different adapter. Either way, the fix target is: **make the driver read use a
> single combined (repeated-START) transaction**, which the mainline CCI adapter
> does NOT do for a regmap 2-msg read.

---

## C. Concrete changes to try — ranked, testable on the camdiag rig

### Fix 1 (TOP): force a repeated-START combined read (no STOP between addr and data)

Two ways to test without a full rebuild:

**1a. On the rig, prove the hypothesis directly.** Compare a STOP-separated read
vs a true combined read at 0x56:

```sh
# (a) combined / repeated-start read (single i2c_msg pair, Sr) -- this is what works downstream
i2ctransfer -f -y 1 w2@0x56 0x00 0x00 r2      # expect: if this gives 0x38e1 => framing was the bug

# (b) STOP-separated read (two independent transactions) -- emulate mainline regmap+cci
i2cset -f -y 1 0x56 0x00 0x00                  # write addr, emits STOP
i2cget -f -y 1 0x56                            # fresh START read -> expect residue
```

If (a) yields 0x38e1 (or anything sane/stable that differs from (b)), the STOP is
the bug. (Prior logs read 0x8b01 via "raw i2ctransfer"; re-run as the explicit
combined form above and, crucially, as the STOP-separated form to see the delta.)

**1b. Driver-side (the real fix):** the mainline `i2c-qcom-cci` cannot emit a
combined Sr read for a 2-msg regmap transfer — it runs each msg as its own queued
CCI op with a trailing REPORT/STOP. To get the vendor behaviour you must issue the
register-address and the read **inside a single CCI queue run with STOP disabled**,
exactly like `cam_cci_core.c:1330-1369`. Options:
- Patch `i2c-qcom-cci.c` `cci_xfer` so that a `{write, read}` msg pair with
  `msg[0]` non-STOP is fused into one queue: write the addr via
  `CCI_I2C_WRITE` **without** the trailing `CCI_I2C_REPORT`, immediately followed
  by `CCI_I2C_READ`, then one `QUEUE_START`. This reproduces the downstream
  `WRITE_DISABLE_P + READ` sequence. (This is the substantive, upstreamable fix.)
- Or, cheaper for a first on-device proof: give the s5kjn1 driver a private
  bypass read that pushes `w[addr]` + `r[len]` as a single `struct i2c_msg`
  array through an adapter that preserves Sr, and confirm 0x38e1.

INFERENCE: highest-probability fix. It is the one behaviour that is *structurally*
different between the two stacks, is consistent with EEPROM-works +
sensor-residue, and is the direct explanation for driver-reads-0 vs
i2ctransfer-reads-0x8b01.

### Fix 2 (do alongside): pin i2c_freq_mode and sweep FAST / FAST_PLUS

FACT: on mainline the CCI master mode is chosen **only** from the child bus'
`clock-frequency` (`i2c-qcom-cci.c` `cci_probe`: `I2C_MAX_FAST_MODE_FREQ`→FAST,
`I2C_MAX_FAST_MODE_PLUS_FREQ`→FAST_PLUS, else STANDARD). If the mainline DT for
the sensor's cci0 i2c-bus has no/`100000` `clock-frequency`, mainline runs
**STANDARD/100 kHz**. Downstream picks the mode from
`i2c_info->i2c_freq_mode` (the undecoded `sensorI2CFrequencyMode` blob field),
plumbed sensor→cci_client at `cam_sensor_core.c:419`
(`cci_client->i2c_freq_mode = i2c_info->i2c_freq_mode`) and applied in
`cam_cci_set_clk_param` `cam_cci_core.c:622-698`.

Action: set the sensor's cci0 bus to Fast-Plus and Fast and retry the combined
read:
```
# in the mainline DT, on the cci@...  i2c-bus that hosts 0x56 / 0x50:
clock-frequency = <1000000>;   /* FAST_PLUS -> selects cci_v2_data FAST_PLUS */
# and separately test:
clock-frequency = <400000>;    /* FAST */
```
This is cheap and genuinely new (1 MHz was never tried). Combine with Fix 1.

### Fix 3 (only if 1+2 don't fully fix): match retries/id_map on the read

FACT: downstream sets `cci_client->retries = 3`, `id_map = 0`
(`cam_sensor_core.c:417-418`) and encodes them into `SET_PARAM_CMD`
(`cam_cci_core.c:1303-1305`). Mainline `cci_i2c_read` writes
`CCI_I2C_SET_PARAM | (addr&0x7f)<<4` with **retries=0, id_map=0**. Retries let the
CCI hardware re-issue the transaction on a marginal NACK. Low likelihood given the
part ACKs, but trivial to add if the read is marginal.

---

## D. Direct answers to the brief's specific questions

**1. Power sequence delta (cam_sensor_util.c):** After patch 0056 the ordered
sequence matches: VIO→VANA→VDIG→(GDSC+MCLK)→reset-release. Downstream executor:
`cam_sensor_util.c:2051-2223`; per-step delay `>20ms msleep` else `usleep`
`:2218-2222`; MCLK step fuses `cam_clk`(=CAMSS_TOP GDSC)+clk-enable
`:2071-2117`. Mainline `s5kjn1_power_on` `s5kjn1-mainline.c:1202-1241`:
vddd/vdda/vddio/(afvdd)/mclk/reset with a **10-15 ms** settle after reset release
`:1239`. **No missing rail, no missing GPIO, and the post-reset settle exists.**
The one *structural* difference (GDSC enabled as the sensor's own 4th "rail"
before MCLK, `cam_clk-supply = <&gcc_camss_top_gdsc>` overlay:435) is real but is
covered on mainline by CCI/camss runtime-PM having the GDSC on; it is NOT the gap
(context_v2 already had camss loaded, EEPROM working). **The power domain is not
the missing step.** FACT.

**2. match_id / what happens between power-up and the read:** `cam_sensor_power_up`
= `cam_sensor_core_power_up` **then** `camera_io_init` (`MSM_CCI_INIT`)
`cam_sensor_core.c:1445-1455`. `camera_io_init` calls `cam_cci_init`
(`cam_cci_soc.c:85`) which votes CPAS/AHB/AXI, gets clk rates via
`cam_cci_get_clk_rates` (uses `i2c_freq_mode` to pick `cycles_per_us`), resets the
master, and sets the RD-FIFO threshold `CCI_I2C_RD_THRESHOLD_VALUE`
(`cam_cci_soc.c:190-193`). Then `cam_sensor_match_id` reads 0x0000 **cold, no
register writes** `cam_sensor_core.c:769-774`. The **freq mode IS configured on
the CCI client before the read** — `cam_cci_set_clk_param` runs at the top of
every `cam_cci_read` (`cam_cci_core.c:1260-1265`) and programs the SCL/SDA timing
regs for `i2c_freq_mode` (`:662-695`). So yes: downstream sets the CCI master to
the sensor's `sensorI2CFrequencyMode` before the id read; mainline never does
(it uses whatever `clock-frequency` the DT bus node carries, default STANDARD).

**3. CCI read details (the crux):** downstream read = **single combined
repeated-START** transaction (`WRITE_DISABLE_P` addr + `READ` inside one
LOCK…UNLOCK…QUEUE_START, `cam_cci_core.c:1314-1369`). Mainline regmap read = **two
separate transactions with a STOP between** (regmap splits into write-msg +
read-msg; `cci_xfer` runs each as its own queue with a trailing REPORT=STOP;
`cci_i2c_read` doesn't even send the reg addr). This is the read-timing/framing
difference that matters for the sensor's combined-format register interface but
not for the EEPROM. **This is the answer to "what's different about how the driver
reads vs a plain transfer."**

**4. CSIPHY/CSID/MCLK:** No. Downstream brings **no** CAMSS streaming block
(CSIPHY/CSID/VFE) up to *identify* — `cam_sensor_match_id` runs after only
power-up + CCI init. MCLK is 24 MHz single-rate (`overlay:460`), confirmed on
device. The sensor identifies with just rails+MCLK+reset+CCI. Not the gap. FACT.

**5. Indirect-port init before id:** No. Downstream reads id cold
(`cam_sensor_core.c:769-774`); the 0x6028/0x6010/0x6f12 firmware push is
stream-on only (mainline `s5kjn1_enable_streams`, `s5kjn1-mainline.c:892-927`).
The `0x6010=1` wake was already tried on-device (context_v2) with **no effect**,
which is consistent with the real problem being read-framing, not a sleeping core.
FACT + on-device result.

**6. CamX/chi-cdk default probe:** PowerUp(GPIO/rail/clk) → SlaveInfo probe that
reads `sensor_id_reg_addr=0x0000` vs `0x38e1` with **no writes first** and with
the master programmed to `sensorI2CFrequencyMode`, using the CCI hardware's native
combined (repeated-START) read. Init/streamon settings come only after a
successful id read. So CamX ALSO relies on the combined read + the pinned freq
mode — the two things mainline differs on.

---

## E. What i2c_freq_mode does the downstream use, and does mainline need FAST_PLUS?

- The value lives in the sensormodule blob's `sensorI2CFrequencyMode` field, which
  is **still undecoded** (FEASIBILITY.md:584-585, offsets 0x32b10/0x325d0). So the
  exact numeric value is **UNKNOWN from the blob**.
- Enum: `{STANDARD=0, FAST=1, CUSTOM=2, FAST_PLUS=3}` (`cam_sensor_cmn_header.h`).
- INFERENCE: JN1 modules of this class in CamX are typically **FAST (400 kHz)** or
  **FAST_PLUS (1 MHz)**; almost never STANDARD/100 kHz. Mainline, by contrast,
  defaults the CCI master to **STANDARD** unless the DT bus sets
  `clock-frequency = <400000|1000000>`. So mainline very likely IS at 100 kHz
  while stock is at 400 kHz/1 MHz.
- Does mainline *need* FAST_PLUS to identify? **Probably not by itself** (the id
  read is not bandwidth-limited), but the SCL rate changes the Sr/data hold window
  the sensor samples against, so it should be swept **together with the combined-
  read fix (Fix 1)**. Set `clock-frequency = <1000000>` (FAST_PLUS) first, then
  `<400000>` (FAST), on the cci0 i2c-bus hosting 0x56, and re-test.

---

## F. Why the driver reads 0 while raw i2ctransfer reads 0x8b01

INFERENCE (primary): different **transaction framing**. The driver read goes
through a path that inserts a **STOP** between the register-address write and the
data read (mainline regmap 2-msg split + `cci_xfer` running each msg as its own
STOP-terminated CCI queue; and mainline `cci_i2c_read` doesn't resend the addr at
all). The sensor's combined-format register machine needs a **repeated-START**; a
STOP leaves the read pointer stale, so the FIFO/return path clocks out an
internal-bus residue that, for the driver's particular framing, resolves to 0
(pointer effectively unarmed) while a differently-framed raw transfer resolves to
0x8b01 (pointer armed but returning uninitialised top-page latch). Both are
"wrong," but they are wrong *differently* because the waveforms differ — which is
the fingerprint that the fault is transaction structure, not a dead core.

Secondary contributor: freq-mode/SCL differences (driver at STANDARD vs the raw
transfer's mode) shift where SDA is sampled in the data phase, which can turn a
marginal Sr read into all-zeros for one path and residue for the other.

Ruled out as the cause of the 0-vs-residue split: supply, MCLK, reset, address
width (EEPROM proves 2/2), CCI source clock/timing (mainline==vendor table).

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- Mainline `cci_v2_data` FAST/STANDARD/FAST_PLUS timing == vendor
  `blair-camera.dtsi:185-243` exactly → bus timing is not the gap.
- Downstream CCI read = one locked queue: SET_PARAM, LOCK, **WRITE_DISABLE_P**
  (addr, no STOP), READ, UNLOCK, QUEUE_START → repeated-START combined read.
  `cam_cci_core.c:1303-1369`.
- Mainline `cci_xfer` runs each i2c_msg as its own STOP-terminated CCI queue;
  `cci_i2c_read` sends only the slave addr + READ, never the reg addr; regmap
  supplies reg addr as a separate preceding write msg → STOP between addr and data.
  (mainline `i2c-qcom-cci.c`, fetched.)
- Sensor uses `devm_cci_regmap_init_i2c(client,16)` (2-msg reg access).
  `s5kjn1-mainline.c:1303`.
- Downstream sets CCI master to `sensorI2CFrequencyMode` before every read
  (`cam_cci_core.c:1260-1265,662-695`); mainline sets mode only from DT
  `clock-frequency`, default STANDARD (`i2c-qcom-cci.c cci_probe`).
- freq_mode plumbing sensor→cci_client: `cam_sensor_core.c:419`.
- id read is cold, no pre-writes: `cam_sensor_core.c:769-774`; stream-on wake
  (0x6028/0x6010/0x6f12) is post-probe only: `s5kjn1-mainline.c:892-927`.
- Power order matches after 0056; MCLK 24 MHz; GDSC via CCI/camss runtime PM.

**INFERENCE**
- The S5KJN1 register read needs a repeated-START; a STOP between addr-write and
  read clears/stales the register pointer → residue. EEPROM tolerates the STOP →
  it still reads. This is the primary root cause of "driver reads 0, transfer
  reads 0x8b01."
- Downstream `sensorI2CFrequencyMode` is FAST or FAST_PLUS; mainline is at
  STANDARD; sweeping to FAST_PLUS/FAST (with the combined-read fix) is warranted.

**UNKNOWN (resolve on device)**
- Whether the raw i2ctransfer that returns 0x8b01 kept Sr or also inserted a STOP
  (decides how clean the 0x38e1 will come once framing is fixed).
- Exact numeric `sensorI2CFrequencyMode` in `com.qti.sensor.mot_s5kjn1.so` /
  the blob (undecoded).
- Whether Fix 1 alone yields 0x38e1, or Fix 1+Fix 2 (freq mode) is required.

---

## The one thing to do first

On the rig, run the **combined vs STOP-separated** read comparison (§C Fix 1a):

```sh
i2ctransfer -f -y 1 w2@0x56 0x00 0x00 r2   # combined / repeated-START
i2cset -f -y 1 0x56 0x00 0x00              # STOP after addr
i2cget -f -y 1 0x56                         # fresh-START read
```

If the combined form returns 0x38e1 (or clearly differs from the STOP-separated
form), the fix is to make the mainline read a **single combined (repeated-START)
CCI transaction** — i.e. patch `i2c-qcom-cci.c` `cci_xfer` to fuse a
`{write(no-stop), read}` msg pair into one queue run (write addr via
`CCI_I2C_WRITE` without the trailing `CCI_I2C_REPORT`, then `CCI_I2C_READ`, one
`QUEUE_START`), mirroring downstream `WRITE_DISABLE_P + READ`. Sweep
`clock-frequency = <1000000>` / `<400000>` on that CCI bus alongside. This is the
first structural difference in the entire investigation that (a) is real code, (b)
survives the EEPROM-works evidence, and (c) directly explains the
driver-vs-i2ctransfer split.
