# S5KJN1 rhodep — the chip-id READ path & timing: stock (CamX HAL + cam_sensor KMD) vs mainline

Scope: *only* how/when the chip-id read is issued, not the power sequence (that was
decoded byte-exact in `OUT_symbolref.md` and already applied). Question: is the
remaining delta in the read framing, a post-reset settle, a retry-with-delay, the
freq-mode programming order, or a pre-read register op?

**Sources examined:** `camera.qcom.so` (.gnu_debugdata, 7646 syms — extracted to
`debugdata.syms.elf`, 14708 readelf lines), `cam_sensor_core.c` (KMD),
`s5kjn1-mainline.c`, `OUT_camx_hal.md`, `OUT_symbolref.md`, `OUT_initregs.md`,
`CAMERA-SENSORS-FEASIBILITY.md` (live-test log).

---

## TL;DR — the read path is NOT the difference

Every candidate in the task brief resolves to **"stock and mainline do effectively the
same thing on the read"**, and each has already been (or is here) closed:

| Candidate | Stock (CamX HAL / KMD) | Mainline | Material diff? |
|---|---|---|---|
| Settle after RESET-high before read | tuple delay only (**4 ms**, from `.bin`), then **immediate** read | `usleep_range(10–15 ms)` then immediate read | **Mainline waits LONGER**, not shorter. No hidden HAL settle. |
| Retry loop w/ delay | 1 read, or 5 reads **back-to-back, no delay** (`#ifdef CONFIG_CAM_SENSOR_PROBE_RETRY`) | 1 read, no retry | Both immediate; delay-between-tries = **0** on both. |
| Read framing (Sr vs STOP) | CCI hw read = addr-WRITE(no-stop) + repeated-START READ | `cci_read` via i2c-qcom-cci = same Sr | Same; and live test proved **both Sr and STOP give identical residue**. |
| Freq mode & its order | freq set into `cci_client->i2c_freq_mode` at `update_i2c_info`, i.e. **before** power-up/read; blob mandates FAST(400k) | i2c-qcom-cci picks master mode from child-bus `clock-frequency`; set at bus probe, before read | Order matches (freq configured before power-up on both). |
| Pre-read register write | **none** — probe packet is cold (no I2C_WR cmd) | **none** — `s5kjn1_identify_sensor` reads cold | Same. |

**Conclusion (INFERENCE, strongly supported):** the read path/timing is not the bug.
The stock read is *less* forgiving than mainline's (shorter settle, no inter-try
delay), so if timing were the issue mainline would already be *more* likely to
succeed. The live log confirms the core is electrically non-functional under the AP's
control (residue tracks MCLK, writes don't latch) — a read-path change cannot fix
that. Details and the exact facts below; two low-cost read-path experiments are still
worth running to fully close the door (§6).

---

## 1. Stock: how long CamX waits after power-up before MatchID  (FACT)

**There is NO CamX-HAL-side settle or WAIT between power-up and the id read.** The
read is done entirely kernel-side; the HAL just blocks on one ioctl.

- The boot probe is assembled in `CamX::HwEnvironment::ProbeImageSensorModules`
  (`camera.qcom.so` vaddr `0x9b5528`, size 14268 → ends `~0x9b8ce4`) and submitted by
  `CSLImageSensorProbeHW` (`0xabe5b0`, sym
  `_Z21CSLImageSensorProbeHWimP25CSLImageSensorProbeResult`). FACT (readelf sym 7310/7311).
- The probe **packet** contains, in order: i2c-info, slaveInfo (hdr `0x0103`), power-up
  `cam_cmd_power`, power-down `cam_cmd_power`; opcode `0x01000003` =
  `CAM_SENSOR_PACKET_OPCODE_SENSOR_PROBE` (`0x9b7f68`). FACT (`OUT_camx_hal.md` §4).
- **No WAIT/settle command is inserted into the probe packet.** Disassembly of the
  whole `ProbeImageSensorModules` body (`/tmp/camr10/probe_disasm.txt`, 3574 insns)
  contains **no** WAIT header emission (`mov …,#0x903`, the `cam_cmd_unconditional_wait`
  marker used by `CreatePowerSequenceCmd`), and **no** call to any Sleep/Delay routine.
  FACT (grep of the disasm for `#0x903|sleep|delay` = 0 hits).
- `CSLImageSensorProbeHW` disassembly (`0xabe5b0`) is a CSL submit/ioctl wrapper: it
  takes a lock, increments a refcount, calls the CSL submit helper (`bl` at
  `0xabe650`/`0xabe6b4`) and returns the result code — **no sleep/nanosleep/usleep in
  its body.** FACT (objdump of `0xabe5b0`–`0xabeaa0`).
- Whole-HAL symbol scan for a generic sleep in the probe path: the only "Delay" syms
  are `SensorNode::HandleModeSwitchDelay` (`0x8efb48`, a *streaming* mode-switch), and
  `Node::…DelayedNotification/DelayedUnmap` — **none on the probe/MatchID path.** FACT.

So the HAL contributes **zero** additional delay: after `CSLImageSensorProbeHW` fires
the ioctl, everything — power-up execution, its embedded tuple delays, CCI init, and
the id read — happens inside the KMD, back-to-back.

### The KMD side (FACT — `cam_sensor_core.c`)

`cam_sensor_driver_cmd`, `case CAM_SENSOR_PROBE_CMD` (`cam_sensor_core.c:937`):
```
:983   rc = cam_sensor_power_up(s_ctrl);      // runs tuples+delays, then camera_io_init (CCI)
:1004  rc = cam_sensor_match_id(s_ctrl);      // <-- called IMMEDIATELY, no sleep in between
```
`cam_sensor_power_up` (`:1414`) does exactly:
```
:1445  cam_sensor_core_power_up(power_info, soc_info);  // applies each tuple + its ms delay
:1451  camera_io_init(&io_master_info);                 // CCI client init (cci_init)
:1457  return;                                           // NO trailing settle
```
The **only** post-RESET-high delay is the tuple's own delay. From `OUT_symbolref.md`
the last power-up tuple is `RESET(high, 4 ms)` (file off `0x35173`), applied by the
executor's per-step delay (`cam_sensor_util.c:2218`, ms units). So stock waits
**exactly 4 ms** after reset-high, then `camera_io_init` (CCI init, sub-ms), then the
first `camera_io_dev_read` of reg `0x0000`.

**Answer to Q1:** No extra HAL settle exists. Stock's post-RESET-high delay before the
id read = the `.bin`'s **4 ms** tuple delay + CCI-init overhead. That is **shorter**
than mainline's `10–15 ms` (`s5kjn1-mainline.c:1239`). Stock is not waiting longer;
it is waiting *less*. FACT.

---

## 2. The CCI read framing: opcodes stock queues vs mainline  (FACT / INFERENCE)

`cam_sensor_match_id` reads via `camera_io_dev_read(&io_master_info,
sensor_id_reg_addr, &chipid, sensor_probe_addr_type, sensor_probe_data_type)`
(`cam_sensor_core.c:769` / `:789`). For a `CCI_MASTER` this dispatches to the kernel
CCI driver's `cam_cci_i2c_read`, which frames a 16-bit-register read as the CCI
hardware's canonical read protocol:

- queue `CCI_I2C_WRITE`/`SET_PARAM` of the register address (WORD) **with no STOP**,
  then issue a **repeated-START** and `CCI_I2C_READ` of N data bytes. This is the fixed
  CCI read master-sequence (addr-write, Sr, data-read) — a single bus transaction.
  INFERENCE (standard cam_cci behaviour; the KMD source for cam_cci is not in the
  material set, but `camera_io_dev_read`'s CCI path is the only framing available and
  is not parameterised for STOP-separated reads).
- `addr_type` / `data_type` = `sensor_probe_addr_type/data_type` from the probe packet
  (`cam_sensor_core.c:445-446`) = WORD/WORD for this sensor (reg `0x0000`, id `0x38e1`,
  per slaveInfo in `OUT_camx_hal.md` §4). FACT.
- `cci_client->retries = 3` (`cam_sensor_core.c:417`) is the **CCI NACK retry** count
  (hw-level), not a read-value retry.

Mainline: `s5kjn1_identify_sensor` → `cci_read(regmap, CCI_REG16(0x0000), &val, NULL)`
(`s5kjn1-mainline.c:1142`). `devm_cci_regmap_init_i2c(client, 16)`
(`s5kjn1-mainline.c:1303`) builds a regmap over **i2c-qcom-cci**, whose read op is the
same CCI addr-write-(Sr)-read repeated-START framing.

**Both use repeated-START.** And the live log settles it empirically:
> "The transaction shape does not matter. A repeated-START read and a
> write-STOP-then-read give the sensor the identical value" (`CAMERA-SENSORS-FEASIBILITY.md:1583-1585`),
> "tested both repeated-START and STOP; sensor residue became STABLE" (`:1746`).

**Answer to Q2:** stock frames the read as CCI repeated-START (Sr, no STOP between the
addr-write and the data-read); mainline's cci-regmap does the same. There is no
framing difference, and framing has been ruled out live. FACT.

---

## 3. The retry loop in `cam_sensor_match_id`  (FACT)

`cam_sensor_core.c:749-806`:
```c
int cam_sensor_match_id(struct cam_sensor_ctrl_t *s_ctrl) {
#ifdef CONFIG_CAM_SENSOR_PROBE_RETRY
    int retries = 5; bool matched = false;
    while (retries-- && !matched) {
        rc = camera_io_dev_read(&io_master_info, sensor_id_reg_addr, &chipid,
                                addr_type, data_type);       // :769
        CAM_INFO("read id: 0x%x expected id 0x%x", chipid, sensor_id);   // :776
        if (cam_sensor_id_by_mask(s_ctrl, chipid) == sensor_id) matched = true;
        if (!matched && !retries) return -ENODEV;            // give up after 5
    }
#else
    rc = camera_io_dev_read(...);                            // single read  :789
    if (cam_sensor_id_by_mask(...) != sensor_id) return -ENODEV;
#endif
}
```

- **There is NO delay between retries.** The loop is 5 reads back-to-back; nothing
  sleeps inside the loop body. FACT (`:768-787` — no `msleep/usleep/udelay`).
- The retry is even conditional on `CONFIG_CAM_SENSOR_PROBE_RETRY`; without it, it is a
  **single** immediate read (`:789`).
- The only `msleep(20)` near probe is the *failure/power-down* path
  (`cam_sensor_core.c:999`, `:1007`) — it runs **after** `cam_sensor_match_id` has
  already returned `-ENODEV`, as a cooldown before `power_down`. It does **not** delay a
  re-read; the whole probe fails and unwinds. FACT.

Mainline: single `cci_read`, no retry (`s5kjn1-mainline.c:1142`). (A prior note
mentioned "5x no delay" — that note was describing exactly this KMD loop, not
mainline; mainline reads once.)

**Answer to Q3:** stock retries at most 5× with **zero** inter-try delay (and only if
the build enables it). It does **not** wait 10–50 ms between tries. If the core needed
tens of ms after reset, this loop would *not* catch it — the 5 reads all complete in
well under a millisecond. So stock has no timing advantage here over mainline. FACT.

---

## 4. Freq mode set on the CCI client, and its order  (FACT / INFERENCE)

- Stock programs the freq mode into the CCI client in
  `cam_sensor_update_i2c_info` (`cam_sensor_core.c:419`):
  `cci_client->i2c_freq_mode = i2c_info->i2c_freq_mode;` — set from the probe packet's
  i2c-info command, i.e. **before** `cam_sensor_power_up`/`camera_io_init`/read.
  `camera_io_init` (`:1451`) then does the CCI `cci_init` using that already-set mode.
  FACT.
- The blob mandates **FAST = 400 kHz** (`sensorI2CFrequencyMode` id 3681 = 1,
  `OUT_symbolref.md` §5, byte `0x4147a`). FACT. (Supersedes the earlier "STANDARD"
  reading in `OUT_camx_hal.md` §4, which read the empty schema slot.)
- Order: freq mode is configured on the client **before** power-up and before the read.
- Mainline: i2c-qcom-cci derives the master i2c mode from the child bus
  `clock-frequency` at bus/adapter setup — also **before** the read. The device tree
  was set to 400 kHz (FAST) this session (`CAMERA-SENSORS-FEASIBILITY.md:1821`),
  matching the blob.

**Answer to Q4:** stock sets FAST(400k) on the CCI client before power-up, then reads.
Mainline now does the same (DT `clock-frequency = <400000>`). Order and value match.
Live tests at 100 kHz, 400 kHz, and 1 MHz FAST_PLUS all returned residue
(`:1749-1753`, `:1823`), so freq mode is not the lever. FACT.

---

## 5. Any read of a DIFFERENT register first, or a write to wake the interface  (FACT)

**No.** The stock probe is cold:
- The probe packet carries only i2c-info + slaveInfo + pwrUp + pwrDown — **no
  `CAMERA_SENSOR_CMD_TYPE_I2C_WR` and no extra READ** command (`OUT_camx_hal.md` §4,
  "probe is cold"). FACT.
- `cam_sensor_match_id` reads **only** `sensor_id_reg_addr` (= `0x0000`); it reads no
  status/revision register first (`cam_sensor_core.c:769/789`). FACT.
- The vendor blob's core-boot strobe (`0x6010=1`, `0x6226=1`) and the whole init-array
  live in the **streamOn** block, not the probe path — identical grouping to mainline's
  `s5kjn1_enable_streams` (`OUT_initregs.md` §4, `:214-249`). Both stacks read id cold.
  FACT.
- Live confirmation: writing `0x6010=1; 0x6226=1` (and full
  `0x6028=0x4000;0x001e=7;0x6010=1;0x6226=1;` + soft-reset `0x0103=1`) before re-reading
  `0x0000` changed nothing — residue unchanged, and a write/read-back to `0x3000` did
  not store (`CAMERA-SENSORS-FEASIBILITY.md:1688-1705`). FACT.

**Answer to Q5:** stock does no different/pre-read register access. There is no
"wake the interface" op that mainline is missing. FACT.

---

## 6. The precise difference vs mainline, and what to try  (INFERENCE)

**Precise difference on the read path: essentially none.** The only quantitative
delta is that mainline waits **10–15 ms** after reset-deassert
(`s5kjn1-mainline.c:1239`) whereas stock waits the blob's **4 ms**
(`OUT_symbolref.md`, RESET tuple) + CCI-init. Mainline is the *more* generous of the
two. Retry, framing, freq, and pre-read ops are identical or already ruled out.

Because the read path cannot explain the failure, and because the live evidence
(`:1707-1720`: residue tracks MCLK, writes don't latch, read-back fails) shows the
sensor's **digital core is not executing**, the remaining fault is upstream of I2C.
Still, to fully close the read-path door, three cheap, non-destructive experiments:

### Change A — long post-reset settle before the FIRST read (test the "core needs > 4 ms" idea)
Even though stock uses 4 ms, verify a much longer settle doesn't help (already
partially tested: 200 ms gave `0x8b0x`, `:630`). Make it explicit in the driver:
```c
/* s5kjn1_power_on, after gpiod_set_value_cansleep(reset_gpio, 0): */
gpiod_set_value_cansleep(s5kjn1->reset_gpio, 0);   /* release reset (phys HIGH) */
usleep_range(50000, 60000);                         /* 50 ms, >> stock 4 ms and mainline 15 ms */
```
Expectation: **no change** (INFERENCE, per the 200 ms live result). If it *does* flip
to `0x38e1`, the core just needed more boot time — cheapest possible win.

### Change B — retry-with-delay in the identify path (mimic a generous version of the KMD loop)
Mirror the KMD retry but *add* a delay stock lacks, to catch a slow-booting core:
```c
static int s5kjn1_identify_sensor(struct s5kjn1 *s5kjn1) {
    u64 val; int ret, tries;
    for (tries = 0; tries < 10; tries++) {
        ret = cci_read(s5kjn1->regmap, S5KJN1_REG_CHIP_ID, &val, NULL);
        if (!ret && val == S5KJN1_CHIP_ID) return 0;
        usleep_range(10000, 11000);   /* 10 ms between tries — stock has 0 */
    }
    dev_err(s5kjn1->dev, "chip id mismatch: %x!=%llx\n", S5KJN1_CHIP_ID, val);
    return -ENODEV;
}
```
This is strictly more forgiving than both stock (0 delay, ≤5 tries) and mainline (1
try). Expectation: **no change** (INFERENCE), but it costs nothing and definitively
rules out "slow core boot".

### Change C — force a clean STOP-separated read (rule the regmap framing edge case out in-driver)
The live raw-`i2ctransfer` test compared Sr vs STOP and saw identical residue, but the
*driver's* cci-regmap path was not independently exercised with a forced STOP. Do a raw
two-transaction read inside identify to prove the regmap path isn't the variable:
```c
/* address write (STOP), then separate data read (START) — NOT repeated-START */
u8 a[2] = {0x00, 0x00}, d[2];
struct i2c_msg m1 = { .addr = client->addr, .flags = 0,        .len = 2, .buf = a };
struct i2c_msg m2 = { .addr = client->addr, .flags = I2C_M_RD, .len = 2, .buf = d };
i2c_transfer(client->adapter, &m1, 1);   /* STOP after addr   */
i2c_transfer(client->adapter, &m2, 1);   /* fresh START+read  */
/* chipid = (d[0]<<8)|d[1]; compare to 0x38e1 */
```
Expectation: **no change** (INFERENCE, framing already ruled out live), but it removes
the last "the driver's regmap might differ from a clean transfer" doubt.

### Priority
B and C are one-file, reversible driver edits; run both, expect negative, and the
read-path hypothesis is fully closed. A is a one-line settle bump. If all three are
negative (as predicted), the read path is conclusively **not** the bug, and effort
should return to the core power/clock domain that the AP cannot observe
(`CAMERA-SENSORS-FEASIBILITY.md:1716-1734`) — a scope on the sensor flex, or the
SM6375 CCI/MCLK clock-tree backport delta vs FP5's sc7280 (`:1760-1762`).

---

## 7. FACT / INFERENCE / UNKNOWN

**FACT (addr / file:line / disasm)**
- HAL probe assembled in `ProbeImageSensorModules` (`camera.qcom.so:0x9b5528`),
  submitted by `CSLImageSensorProbeHW` (`0x9b5528`→`0xabe5b0`, readelf sym 7310).
- No WAIT/settle in the probe packet and no Sleep/Delay call in the probe body:
  `probe_disasm.txt` (3574 insns) has 0 hits for `#0x903|sleep|delay`;
  `CSLImageSensorProbeHW` body (`0xabe5b0-0xabeaa0`) has no sleep, only lock +
  refcount + CSL-submit ioctl.
- KMD: `cam_sensor_power_up` (`cam_sensor_core.c:1414`) = `cam_sensor_core_power_up`
  (`:1445`) + `camera_io_init` (`:1451`) + return (`:1457`), **no trailing settle**;
  caller does `cam_sensor_match_id` immediately (`:1004`) after power_up (`:983`).
- Stock post-RESET-high delay = the `.bin` RESET tuple = **4 ms** (`OUT_symbolref.md`
  file off `0x35173`; executor applies ms per `cam_sensor_util.c:2218`). Mainline waits
  **10–15 ms** (`s5kjn1-mainline.c:1239`).
- `cam_sensor_match_id` retry loop (`cam_sensor_core.c:749-806`): ≤5 reads,
  **no inter-try delay**, gated on `CONFIG_CAM_SENSOR_PROBE_RETRY`; else single read
  (`:789`). Mainline: single `cci_read` (`s5kjn1-mainline.c:1142`).
- `msleep(20)` at `:999/:1007` is the post-failure power-down cooldown, not a re-read
  delay.
- Read framing = CCI repeated-START (addr-write no-stop, Sr, data-read) on both stock
  (`camera_io_dev_read` CCI path) and mainline (cci-regmap over i2c-qcom-cci).
  Live: Sr and STOP give identical residue (`CAMERA-SENSORS-FEASIBILITY.md:1583,1746`).
- Freq mode set on `cci_client->i2c_freq_mode` before power-up
  (`cam_sensor_core.c:419`), value FAST/400k (`OUT_symbolref.md` §5, byte `0x4147a`);
  mainline DT `clock-frequency=<400000>` (`CAMERA-SENSORS-FEASIBILITY.md:1821`).
- Probe is cold: no pre-read register write and no different register read on either
  stack; core-boot/init-array is stream-on only (`OUT_initregs.md:214-249`,
  `cam_sensor_core.c` probe packet). Live: boot writes don't wake the core; writes
  don't latch (`CAMERA-SENSORS-FEASIBILITY.md:1688-1705`).
- Reset polarity verified correct live: gpio35 HIGH=running (ACKs, residue), LOW=reset
  (ENXIO) (`CAMERA-SENSORS-FEASIBILITY.md:1782-1786`).

**INFERENCE**
- The chip-id read path/timing is not the bug: stock's read is *less* forgiving than
  mainline's (4 ms vs 10–15 ms settle; 0-delay retries vs mainline's single read), so a
  read-path change cannot be what makes stock succeed where mainline fails.
- The failure is upstream of I2C — the sensor's digital core is not executing (residue
  tracks MCLK, writes don't latch), consistent with a core power/clock-domain issue the
  AP can't observe, or the SM6375 CCI/MCLK clock-tree backport differing subtly from
  FP5's sc7280.
- Experiments A/B/C are expected negative but definitively close the read-path door.

**UNKNOWN**
- The kernel `cam_cci`/`cam_cci_i2c_read` source is not in the material set; the exact
  CCI queue opcodes (`SET_PARAM`, `WRITE`-no-stop, `READ`) are inferred from the
  canonical CCI read protocol and `camera_io_dev_read`'s CCI dispatch, not read from
  source. (Framing conclusion is nonetheless backed by the live Sr-vs-STOP test.)
- Whether stock Android's *effective* per-step delays match the decoded `.bin` tuples
  exactly, or whether the KMD folds an extra WAIT — resolvable only by ftracing
  `cam_sensor_core_power_up` on stock (destructive; needs userdata backup). This is a
  power-sequence question, out of this report's read-path scope.
