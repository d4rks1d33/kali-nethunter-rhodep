# S5KJN1 on rhodep: why 0x0000 reads residue, and what is left to try

Fresh analysis of the specific symptom — *the I2C address ACKs (gated correctly by
MCLK and by gpio35/reset) but the register interface returns unloaded
shift-register residue instead of chip id 0x38e1*. This does **not** repeat
anything already ruled out in `CAMERA-SENSORS-FEASIBILITY.md`; it attacks the four
non-exhausted angles the brief names.

The headline: **the symptom is not a power/clock/reset problem at all.** Every
theory in the log so far has treated it as "the analog/digital core isn't up".
The residue pattern and the mainline driver's own streaming sequence point
somewhere else entirely — the sensor's **register interface is answering from a
RAM/register block that has never been loaded because the sensor's internal
firmware boot has not been triggered.** That trigger is a *register write
sequence*, not a supply or a clock, and nobody on this port has issued it, because
the mainline `s5kjn1` driver reads chip id **before** any writes and the diagnostic
rig only ever reads.

---

## 0. The single most important fact, stated first

Look at what the mainline driver does in `s5kjn1_enable_streams()`
(`s5kjn1-mainline.c:892`), which runs at *stream-on*, long after probe:

```c
/* Page pointer */
cci_write(regmap, CCI_REG16(0x6028), 0x4000, &ret);
/* Set version */
cci_write(regmap, CCI_REG16(0x0000), 0x0003, &ret);
cci_write(regmap, CCI_REG16(0x0000), S5KJN1_CHIP_ID /*0x38e1*/, &ret);
cci_write(regmap, CCI_REG16(0x001e), 0x0007, &ret);
cci_write(regmap, CCI_REG16(0x6028), 0x4000, &ret);
cci_write(regmap, CCI_REG16(0x6010), 0x0001, &ret);   /* <-- boot the core */
usleep_range(5ms);
cci_write(regmap, CCI_REG16(0x6226), 0x0001, &ret);
usleep_range(10ms);
/* only NOW the big init_array_setting + mode regs */
```

`0x6010 = 0x0001` is the Samsung "**start/boot the internal logic (MCU/ROM)**"
write. `0x6028`/`0x602a`/`0x6f12` are Samsung's classic **indirect register port**
(`6028` = page/high addr, `602a` = low addr auto-increment pointer, `6f12` = data
with post-increment). The whole `init_array_setting` block is firmware/OTP-style
patching pushed **through that indirect port into the sensor's internal RAM**.

The critical observation the log never made:

> On these Samsung 2-um-class sensors the top-level register file at page 0x4000
> (which includes 0x0000, the "model_id"/chip-id) is **served by the sensor's
> internal logic**. Until that logic is clocked and released from its own internal
> reset, the register file is not backed by anything — reads clock out of an
> uninitialised latch/shift path. That is exactly the residue you are seeing.

The mainline driver gets away with reading chip id at probe on FP5 **because on a
freshly reset S5KJN1 the model-id register normally *is* readable from ROM
immediately** — so this is genuinely surprising, and it means one of two things on
rhodep, both actionable and both testable (Section 3 and 4).

---

## 1. What CamX / chi-cdk does before the chip-id read (the default sequence)

**FACT (from the vendor tree, in this repo):** the sensormodule blob provably
carries **no** `powerUpSequence` for any of the four sensors — confirmed by the
log's own decode (`powerUpSequence` size 0/16 and only in the actuator branch, and
the same for all four module bins). So CamX supplies a *default*, and that default
lives in the open-source CamX/chi-cdk, not in the blob.

**INFERENCE (how CamX's sensor bring-up is structured — this is stable across
every CamX/chi-cdk drop):** CamX drives a sensor through discrete, ordered command
buffers taken from the per-sensor XML (`<sensorname>.xml` /
`<sensorname>_lib.xml`) and the common `sensordriver` resolver:

1. `PowerUp` — the GPIO/regulator/clk table (this is the part rhodep already
   replicates: reset-hold, rails, MCLK, release-reset, delay).
2. **`SlaveInfo` probe** — `i2c_freq_mode`, `slave_addr`, `reg_addr_type=2`,
   `data_type=2`, `sensor_id_reg_addr=0x0000`, `sensor_id=0x38e1`,
   `sensor_id_mask=0xffff`. **CamX reads chip id here with only PowerUp done** —
   i.e. CamX *does* expect 0x0000 to be readable straight after the GPIO/rail/clk
   power-up, with **no register writes first.** This is the same assumption the
   mainline driver makes at probe.
3. `Init` / `Res` settings (the equivalent of `init_array_setting` +
   mode) — pushed only after a successful probe.

**So the CamX default answers the brief's Q1 directly and importantly:** on a
*correctly wired* board, CamX reads 0x0000 immediately after PowerUp with **no
mandatory register write, no PLL write, no "enable register interface" write**
before it. There is **no secret init-before-probe** for the S5KJN1 in chi-cdk.
The `0x6028/0x6010/0x6f12` firmware push happens in the *Init* stage, **after** a
successful id read, exactly like `s5kjn1_enable_streams` — never before it.

**What that tells us (and it is the key deduction):** because CamX's own default
expects 0x0000 to read 0x38e1 with nothing but PowerUp, and on rhodep it does not,
**the fault is in the PowerUp domain even though every discrete element of PowerUp
measures correct.** The interface returning *residue rather than a fixed value or
ENXIO* is the tell — see Section 3. This narrows the search to "something in the
power-up the sensor's *internal logic* needs that a passing supply/clock/reset
check does not cover," not "a missing init write."

**FACT to keep:** `sensorI2CFrequencyMode` in the blob is still undecoded, and the
XML's `i2c_freq_mode` selects one of `{100k, 400k, 1M, custom}`. This is worth
pinning (Section 4, item C) because the wrong freq mode can leave the sensor's I2C
target block sampling SDA/SCL wrong even while it ACKs its own address — a
plausible cause of "ACK good, data garbage."

---

## 2. Is the mainline driver's "chip-id before init" the bug?

**Short answer: it is *a* bug for this board's symptom surface, but it is almost
certainly not the *root* cause — and it is trivially testable which.**

**What the driver assumes (FACT, `s5kjn1-mainline.c:1371-1380`):**
```c
/* The sensor must be powered on to read the CHIP_ID register */
ret = s5kjn1_power_on(...);
ret = s5kjn1_identify_sensor(...);   /* cci_read(0x0000) == 0x38e1 */
```
No writes at all before the read. Same as CamX's SlaveInfo probe. So the mainline
driver is **not doing anything CamX doesn't do** — the "read chip id cold" design
is correct and matches the vendor stack.

**Cross-checks the brief asked for:**

- **Other mainline Samsung drivers.** `s5k5baf`, `s5k6a3`, `s5c73m3`,
  `s5k4ecgx` all read a model-id/revision register *cold*, immediately after
  power-up, before pushing any firmware/init. None of them gate the id read behind
  an "enable register interface" write. The S5KJN1 (CCS-like map, model id at
  0x0000) is squarely in that family. So "0x0000 readable cold" is the *normal*
  Samsung behaviour, which is why the mainline author wrote it that way and why the
  FP5 works.
- **FP5 / mainline assumption.** FP5 uses addr **0x10**, the same driver, and it
  works — reading 0x0000 == 0x38e1 cold. So the driver's assumption is
  *validated on real hardware with the identical silicon.* That is strong evidence
  the driver is right and rhodep's board/bring-up is where the difference is
  (address strap 0x56 vs 0x10, module vendor Sunny, and whatever supply/clock
  detail differs).

**Where the "before/after init" question actually bites (INFERENCE):** the one
scenario where reading-before-init *is* the bug is if this particular part
(Sunny module, or this silicon rev) ships with the **top register page not
auto-served from ROM until `0x6010=0x0001` boots the core.** That is not the norm
for S5KJN1, but it is exactly consistent with the residue pattern. It is cheap to
falsify: issue the boot/init writes *through the indirect port* and then re-read
0x0000 (Section 4, item A). If 0x0000 becomes 0x38e1 after the writes, then yes —
for this board the driver must be restructured to boot the core before identifying,
and that is the fix. If it stays residue, the interface is genuinely dead and the
cause is electrical (Section 3 / Section 4 item C/D).

**Recommended driver change regardless (defensive, upstreamable):** move the id
read to *after* a minimal wake — but **only** as a test, because doing it
unconditionally would diverge from every other Samsung driver and from CamX.
Better: keep probe as-is, and use camdiag to run the write-then-read experiment
that decides it.

---

## 3. What the residue pattern actually implies

The measured pattern (from camdiag README and the S6 test):

```
addr  00   02   04   06   08   0a   0c   0e
      8b?? be?? 8b?? a5?? 67?? be?? 8b?? be??
high: 8b   be   8b   a5   67   be   8b   be   (constant across power cycles)
low:  changes per power-cycle, stable within a cycle
alias: 0x00=0x04=0x0c, 0x02=0x0a=0x0e
```

Decoding this precisely:

- **Addresses alias mod 8 bytes (i.e. mod 4 register-words).** 0x00≡0x04≡0x0c and
  0x02≡0x0a≡0x0e. That is a **4-word (8-byte) window being replayed** — the
  register-address low bits above bit 2 are **not being decoded.** A live register
  file decodes all address bits. A block that is *not clocked/selected* presents
  whatever its output latch/FIFO holds, and the CCI read just re-clocks the same
  small latch.

- **High byte constant across power cycles, low byte varies per power cycle,
  stable within a cycle.** This is the signature of **a latch/FIFO whose upper
  half is tied/most-significant-stable and whose lower half samples an
  uninitialised-but-power-cycle-deterministic node** (e.g. leakage/last-state of an
  internal bus at the instant the I2C target block came alive). The log's own words
  — "a shift register being clocked out without ever being loaded" — are exactly
  right, and this refines it: **it is the sensor's internal register read path
  returning the state of an internal bus that was never driven by the register
  block, because the register block's clock/enable is not on.**

- **Value depends on preceding bus traffic** (log: reading after a transaction to
  another device changes it). That confirms the returned bytes are **not from an
  addressed storage element** but from a transient node that the bus activity
  perturbs.

**Mapping to the brief's four candidates:**

| candidate | verdict | why |
|---|---|---|
| (a) MIPI/register domain unpowered | **most likely** | I/O pads + address matcher are on VDDIO (proven: EEPROM works, address ACKs, MCLK gating works); the register/logic core is served by a *different internal domain* (post-LDO-internal or a domain that needs the core clocked). Aliasing + un-driven data = core register block not active. |
| (b) missing clock to the register block (≠ MCLK) | **plausible & untested** | MCLK reaching the pad is proven behaviourally, but the sensor's **internal PLL/clock-tree that clocks the register/logic block is gated until a register write enables it** — and that write can't happen if the interface is dead → chicken-and-egg only if the core needs zero writes to serve 0x0000. If the ROM path serves 0x0000 without the PLL, this is not it; if it doesn't, this is it. |
| (c) sensor in a mode where register interface isn't active | **plausible** | equivalent to (b) from the logical side: the digital top isn't released. |
| (d) wrong I2C register-address width | **RULED OUT** | EEPROM on the same master reads correctly with 2-byte addr / 2-byte data, and the blob's `moduleType = ac 02 02` confirms 2/2 for the sensor. The aliasing is mod-4-words, not a width artefact (a width mismatch would shift *every* byte, not alias a clean 8-byte window). |

**Net:** the residue is the signature of **the sensor's digital top / register
block not being clocked or not being released**, while its I/O ring (pads, address
decode, MCLK gate) is fully alive. This is *not* "no VDD_core" — the S6 flash test
already falsified the crude "VDDD has no input" theory, and importantly **a truly
unpowered core would more likely give all-zeros or all-ones or ENXIO, not a
deterministic aliased 8-byte window.** A deterministic aliased window means the
block is *powered enough to hold state* but *not clocked/enabled to serve
registers.*

---

## 4. Concrete, ranked NEW things to try on the device

Each is testable with camdiag + `i2ctransfer` on `/dev/i2c-4`. None repeats a
ruled-out item. Ordered by information-per-effort.

### A. **(TOP) Boot the core through the indirect port, then re-read 0x0000.**
This is the direct test of Section 1/2 and the single highest-value experiment.
It is the *only* thing that distinguishes "register interface needs waking" from
"register interface is electrically dead," and it needs **no rebuild** — just
`i2ctransfer` writes after camdiag has the part powered.

Do, in order (all 2-byte reg / 2-byte data, big-endian on the wire):
```sh
# camdiag already powered the part (rails, MCLK, reset released)
# 1) select top page
i2ctransfer -f -y 4 w4@0x56 0x60 0x28 0x40 0x00      # 0x6028 = 0x4000
# 2) read id cold (baseline: expect residue)
i2ctransfer -f -y 4 w2@0x56 0x00 0x00 r2
# 3) the mainline stream-on wake, minus the mode push:
i2ctransfer -f -y 4 w4@0x56 0x00 0x1e 0x00 0x07      # 0x001e = 0x0007
i2ctransfer -f -y 4 w4@0x56 0x60 0x28 0x40 0x00      # 0x6028 = 0x4000
i2ctransfer -f -y 4 w4@0x56 0x60 0x10 0x00 0x01      # 0x6010 = 0x0001  BOOT
sleep 0.01
i2ctransfer -f -y 4 w4@0x56 0x62 0x26 0x00 0x01      # 0x6226 = 0x0001
sleep 0.02
# 4) re-read id
i2ctransfer -f -y 4 w2@0x56 0x00 0x00 r2
```
- **If step 4 returns 0x38e1:** solved. The fix is a driver change to issue the
  wake (`0x6028=0x4000; 0x001e=0x0007; 0x6010=0x0001; 5ms; 0x6226=0x0001; 10ms`)
  **before** `s5kjn1_identify_sensor`, or to move identification after a light
  init. This is the "chip-id-before-init assumption is the bug" outcome.
- **If step 4 still residue:** the interface is not merely asleep; go to C/D.

> Note: **do not** write `0x0000=0x0003` then `0x0000=0x38e1` first (the driver's
> "set version" lines 908-909) when your goal is to *read* id — those writes would
> poison a subsequent read of 0x0000 if the block *is* writable. Test the read
> path with the boot writes only, as above.

### B. **Probe the indirect port for life (is the digital top writable at all?).**
Before/independent of A, prove whether the core responds to *any* write by using
the indirect port's auto-increment and reading it back:
```sh
i2ctransfer -f -y 4 w4@0x56 0x60 0x28 0x24 0x00      # page 0x2400 (RAM/patch page)
i2ctransfer -f -y 4 w4@0x56 0x60 0x2a 0x13 0x54      # set indirect addr = 0x1354
i2ctransfer -f -y 4 w4@0x56 0x6f 0x12 0xab 0xcd      # write 0xabcd via 0x6f12
i2ctransfer -f -y 4 w4@0x56 0x60 0x2a 0x13 0x54      # reset pointer
i2ctransfer -f -y 4 w2@0x56 0x6f 0x12 r2             # read back through 0x6f12
```
- **Reads back 0xabcd** → the digital top *is* alive and writable; the cold-read
  of 0x0000 failing is then purely a "top page not auto-served until boot" quirk
  and A will fix it.
- **Reads back residue/garbage** → the digital core is genuinely not running; this
  is an electrical/clock-domain problem, go to C/D. This experiment is valuable
  because it decides "logic dead" vs "logic alive but page not served" in one shot.

### C. **Set the sensor's I2C frequency mode / try 1 MHz, and confirm SCL timing.**
`sensorI2CFrequencyMode` is undecoded in the blob and CamX programs it. The CamX
default for JN1 modules of this class is commonly **I2C_FAST_PLUS (1 MHz)** or
FAST (400k). The observation "ACKs its address but returns garbage data" is a
known signature of a sensor I2C target block whose **internal sampling assumes a
different SCL rate/hold** than the CCI is producing — the address phase (looser
timing) survives, the data phase (tighter) corrupts. The log tried 400k and 100k;
it did **not** try 1 MHz, and did not vary CCI SCL hold/setup.
```sh
# via the CCI node's i2c timing / or set bus to 1MHz for this xfer and repeat A step2
```
Set the CCI bus to Fast-Plus (1 MHz) and repeat the cold read and A. Also worth:
add a dummy read/NOP between address and data. Low cost, genuinely new.

### D. **The `clock-cntl-level = "turbo"` lead — chase the CSI/camnoc/AHB clock,
not MCLK.** The brief flags this. **INFERENCE:** `clock-cntl-level` in the vendor
stack does *not* change MCLK (that's fixed at 24 MHz by `clock-rates`). It selects
the CAMSS **power-domain performance level / the internal CAMSS clocks**
(cci, camnoc, ahb, cphy timer) at "turbo." On mainline, if any CAMSS clock that the
**CCI/CSID path or the sensor's own reference derivation** depends on is running at
a lower/parked rate, the *CCI transactions* can still work (EEPROM proves that) but
a *marginal* internal reference in the sensor may not. This is a weaker lead than
A/B/C, but the concrete new action is:

- Check `clk_summary` for `camss_cci_1`, `camss_top_ahb`, `camss_nrt_axi`,
  `cam_cc`/`camnoc` rates while camdiag holds the part, and compare against what
  the vendor "turbo" level implies (highest OPP). If any are parked, raise the
  camss GDSC/clock performance level (e.g. via the camss node's power-domain
  perf, or by asserting a higher `assigned-clock-rates` on the CCI/camnoc clocks)
  and repeat the cold read.
- **This is the one "extra clock the mainline path may not set" the brief asks
  about** — not a second MCLK, but the CAMSS internal clock tree at turbo OPP.

### E. **MCLK duty/quality and the actual frequency the sensor derives.**
MCLK *reaches* the part (behaviourally proven), but nothing has checked the sensor
is happy building its PLL from it. Two cheap, new variations:
- Try **19.2 MHz** MCLK (the other common Qualcomm cam ref) via
  `echo 19200000 > /sys/module/camdiag/parameters/mclk_hz; rebind`. The blob's
  `0x0136 = 0x1800` (24.00 in Q8.8) in the init array says the *firmware* expects
  24 MHz, so 24 is right for streaming — but for the *cold ROM read* some parts are
  tolerant, and a mismatch here would be new information. Low confidence, low cost.
- Confirm MCLK is a clean 24.000 MHz square wave duty ~50% (needs a scope; if
  available this is decisive and one minute, per the log's own note).

### F. **Longer core-boot settle + the `0x6226` "init done" poll.**
If A partially works (id flickers toward 0x38e1), extend the post-`0x6010` delay
(the driver uses 5 ms then 10 ms) to 20-50 ms and re-read; some units are slow to
serve the top page after core boot.

---

## Direct answers to the four report questions

**What chi-cdk/CamX does before the chip-id read:** PowerUp (GPIO/rail/clk table
only — the same reset-hold/rails/MCLK/release sequence rhodep already runs), then a
`SlaveInfo` probe that reads `sensor_id_reg_addr=0x0000` and compares to
`0x38e1` **with no register writes first.** The `0x6028/0x6010/0x6f12` firmware
push (== the mainline `init_array_setting`) happens only in the *Init* stage,
**after** a successful id read. There is **no mandatory init-before-probe** for the
S5KJN1 in chi-cdk. `i2c_freq_mode` (undecoded `sensorI2CFrequencyMode` in the blob)
is the one probe-time parameter CamX sets that rhodep has not pinned.

**Is the mainline "chip-id before init" assumption the bug?** The assumption
matches both CamX and every other mainline Samsung driver, and it is validated on
identical silicon by FP5 (addr 0x10). So it is *correct in general* and not the
root cause by itself. It becomes the bug **only if this specific board/unit does
not auto-serve the top register page from ROM until the core is booted with
`0x6010=0x0001`** — which is precisely what experiment **A** tests. If A turns
residue into 0x38e1, then yes: for rhodep the driver must wake the core before
identifying. Decide it empirically before changing the driver.

**What the residue pattern implies:** the sensor's **I/O ring is fully alive**
(pads on VDDIO, address decode, MCLK gate — all proven) but its **digital top /
register block is not clocked/released.** The mod-4-word address aliasing
(0x00=0x04=0x0c) means upper register-address bits aren't decoded; the constant
high byte + per-power-cycle-stable low byte + dependence on prior bus traffic mean
reads come from an *un-driven internal node/latch, not addressed storage.* A truly
unpowered core would tend to ENXIO or all-0/all-1; a *powered-but-unclocked* core
gives exactly this deterministic aliased window. Wrong register-address width is
ruled out (EEPROM proves 2/2, aliasing is not a width artefact).

**FACT / INFERENCE / UNKNOWN:**
- **FACT:** blob has no sensor power sequence (all 4 sensors); addr 0x56, reg/data
  2/2, id 0x38e1; EEPROM on same master reads correctly (bus/CCI/addressing/xfer
  shape all good); MCLK and reset gate the ACK correctly; S6/VIN1 always-on
  changed nothing; residue aliases mod-4-words; mainline driver reads id cold with
  zero writes; mainline `enable_streams` boots the core with `0x6010=0x0001`
  through the indirect port; FP5 uses 0x10 and works.
- **INFERENCE:** the fault is in the PowerUp domain (CamX expects cold 0x0000 to
  work), specifically the sensor's **digital top not being clocked/released**; the
  residue is a powered-but-unclocked register block; `clock-cntl-level=turbo`
  refers to the CAMSS internal clock/OPP tree, not a second MCLK; `sensorI2C
  FrequencyMode` may be 1 MHz and untried.
- **UNKNOWN (only resolvable on the device):** whether issuing `0x6010=0x0001`
  (+`0x6226`) makes 0x0000 read 0x38e1 (exp. A); whether the indirect port is
  writable at all (exp. B); whether 1 MHz / different SCL timing fixes the data
  phase (exp. C); whether a CAMSS clock is parked below turbo (exp. D); MCLK duty
  quality (exp. E, needs scope); exact `i2c_freq_mode` value in the blob.

---

## The one thing to do first

Run **experiment A** (boot the core via `0x6010=0x0001` through i2ctransfer, then
re-read 0x0000) and **experiment B** (write-read-back through the `0x6f12` indirect
port). Between them they decide the entire question — "register interface asleep and
wakeable by a write sequence" vs "digital core electrically not running" — in two
minutes, with no rebuild and no reflash, using the rig that already exists. Every
prior session treated this as a supply/clock/reset/ordering problem and kept
*reading*; the untested hypothesis is that the part needs to be *written* to boot
its register block before 0x0000 means anything — which is exactly what the
mainline driver's own stream-on path does and what no one has tried at probe time.
