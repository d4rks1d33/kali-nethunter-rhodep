# S5KJN1 rhodep Sunny — decoding the "register init table" at 0x351bb (and the real ones)

Blob: `s5kjn1_sunny.bin` (`com.qti.sensormodule.mot_rhodep_s5kjn1_sunny`),
269222 B = 0x41ba6. Parsers: `/tmp/camr10/parse_initregs.py`, `parse2.py`,
`parse5.py`, `final2.py`. Container per OUT_symbolref / FEASIBILITY:
56-byte parameter table @0x00c0 (3839 entries, id 1..3839), `DATA_BASE = 0x3401c`,
`flag=0xffffffff` offsets are DATA_BASE-relative.

---

## TL;DR (the headline result)

**The thing OUT_symbolref called a "REGISTER INIT TABLE beginning 0x0210, 0x020e,
0x0212, 0x0214 at 0x351bb" is NOT a register table.** It is the parameter-table
*id/value instance stream* — a run of consecutive u32 field-IDs with their inline
values. `0x0210/0x020e/0x0212/0x0214` are the low 16 bits of the u32 IDs
`528, 526, 530, 532`, which resolve in the 56-byte parameter table to the schema
field names **`delayUs`, `slaveAddr`, `registerData`, `slaveAddr`** — i.e. the
serialized *layout* of a `regSetting` record, not I2C register addresses. (FACT,
byte-exact, cross-checked against the parameter table.)

**The blob DOES contain the real Samsung register tables**, elsewhere:

| what | file offset | container entry | values? |
|------|-------------|-----------------|---------|
| **core-boot + init-array** (`6028→6010→6226→6028→602a/6f12…`) | **0x40a38** | id ~3760 area | addresses in schema stream, values in packed array @0x40fb4 |
| init-array VALUE array (`1354,7017,13b2,…,9600,0011`) | **0x40fb4** | id 3765 `slaveAddr` | **populated (real)** |
| mode/resolution table (222 elems, `2400,1a28,4c00,…`) VALUE run | **0x38077** | id 34 `slaveAddr` container | **populated (real)** |
| mode table ADDRESS template (222 elems) | **0x35533** | id 34 `slaveAddr` container | addresses only, values 0 |
| `preProgSettings` (id 3732) small reg block (`6f12,6028…`) | **0x40e2e** | id 3732 | populated |

**The decoded register data in the blob is the SAME data as the mainline
driver's `init_array_setting` and `s5kjn1_4080x3072_30fps_mode`** — identical
register addresses and (bar one 0x3401→0x3403 tuning delta) identical values.
There is **no secret extra init table** the blob has and mainline lacks.

**Answer to "is there a mandatory init-before-chip-id sequence?": NO.** In the
blob the core-boot (`0x6010=1`, `0x6226=1`) and the init-array all live in the
**streamOn / mode-config** grouping (the `6028→6010→6226→6028→602a/6f12` block at
0x40a38), the same place mainline puts them (`s5kjn1_enable_streams`, run at
*stream-on*, long after the cold chip-id read). Nothing in the blob is written
between power-up and the chip-id read. So the residue-instead-of-0x38e1 symptom is
**not** explained by a missing init-before-id table. See §5 for what this means.

---

## 1. Byte-exact proof that 0x351bb is the id/value stream, not registers

Raw bytes @0x351bb (FACT, `od -A x -t x1`):

```
0351bb 10 02 00 00 0e 02 00 00 12 02 00 00 14 02 00 00
0351cb 00 00 00 00 1a 00 00 00 04 00 00 00 1b 00 00 00
0351db 01 00 00 00 01 00 00 00 1c 00 00 00 f0 21 00 00
```

Read as **u16 (reg,val) pairs** (OUT_symbolref's assumption):

```
0x0351bb reg=0x0210 val=0x0000
0x0351bf reg=0x020e val=0x0000
0x0351c3 reg=0x0212 val=0x0000
0x0351c7 reg=0x0214 val=0x0000   <- every "val" is 0x0000; addrs 0x210/0x20e/0x212
0x0351cb reg=0x0000 val=0x0000      are not a Samsung write sequence
```

Read as **u32** it is obviously an id/value stream:

```
0x210 (528)  0x20e (526)  0x212 (530)  0x214 (532)  0  0x1a  4  0x1b  1  1  0x1c …
```

Resolving those u32 values as parameter-table IDs (`parse2.py`, FACT):

```
id 528 (0x210) = 'delayUs'              id 26 (0x1a) = 'sensorVersion'
id 526 (0x20e) = 'slaveAddr'            id 27 (0x1b) = 'resolutionData'
id 530 (0x212) = 'registerData'         id 28 (0x1c) = 'streamConfiguration'
id 532 (0x214) = 'slaveAddr'            id 29 (0x1d) = 'sensorFps'
                                        id 33 (0x21) = 'regSetting'
```

Further down the same stream the IDs run perfectly consecutively
(`0x361,0x362,0x363,0x364,0x365,…` = 865,866,867,…), each followed by its value —
the signature of the id/value instance serialization OUT_symbolref §1 already
documented for the 0x40be0 stream. **0x351bb is the tail of that same stream**,
immediately after the power-sequence triplets. It carries the *schema* of the
sensor's `regSetting`/`streamConfiguration` records, not I2C writes.

> Correction to OUT_symbolref lines 83, 105-107: "register init table starts /
> the init register table" at 0x351bb is a misread of consecutive u32 field-IDs
> (low half = 0x0210, 0x020e, …) as u16 register addresses. Bounding the power
> sequence at 0x351bb is still correct; the *label* on what follows is wrong.

---

## 2. Where the real register tables actually are

### Element format (FACT, derived at 0x35533, verified at 0x40a38)

Qualcomm serializes each I2C write of a `cam_cmd_i2c_random_wr` reg_setting as a
**40-byte tagged element** (a running field-index `idx` is interleaved before each
struct field):

```
+0x00  u32  reg_addr            (e.g. 0x6028 / 0x602a / 0x6f12 / 0x6010 / 0x6226)
+0x04  u32  count = 1
+0x08  u32  idx  (running tag)
+0x0c  u32  addr_type = 2       (2-byte register address)   <- discriminator
+0x10  u32  data_type = 2       (2-byte data)               <- discriminator
+0x14  u32  delay  (µs; 0 here)
+0x18  u32  = 1
+0x1c  u32  idx
+0x20  u32  reg_data            (the 16-bit value, zero-extended)
+0x24  u32  idx (next element)
```

Stride = 0x28 = 40 bytes (0x6028@0x35537 → 0x602a@0x3555f = +0x28). `addr_type=2,
data_type=2` is the reliable discriminator for a real reg element. This matches
`0x0210/'delayUs' + 0x212/'registerData' + 0x20e/'slaveAddr'` schema: each write
carries slaveAddr, registerData, delayUs.

### The tables

**(a) Mode/resolution table — 222 elements @ 0x35533** (inside the id-34
`slaveAddr` container, 0x34cb7..0x377ff — the same 11080-B container that holds the
power sequence at 0x35143). Address order (FACT, `parse5.py`):

```
6028 602a 6f12 602a 6f12 602a 6f12 602a 6f12 602a 6f12 6f12 602a 6f12 602a 6f12 6f12 …
(222 elements: one 0x6028 page-select, then 602a/6f12 page-offset/indirect-data pairs)
```

In this copy the `reg_data` field is 0x0000 for every element — it is the
**address/structure template**. The matching **values** are stored as a packed
u32 run at **0x38077** (FACT):

```
2400 1a28 4c00 065a 139e 139c 13a0 0120 2072 1a64 19e6 1a30 3403 19fc 19f4 19f8 …
```

which is byte-for-byte the mainline `s5kjn1_4080x3072_30fps_mode` values
(`0x2400, 0x1a28, 0x4c00, 0x065a, 0x139e, 0x139c, 0x13a0, …`), with a single
tuning delta **blob 0x3403 vs mainline 0x3401** at reg 0x1a30. ⇒ this is the
**4080×3072 mode config** ("res0"/mode table).

**(b) Core-boot + init-array — @ 0x40a38** (schema-token stream; addresses only,
values 0). Address order (FACT, `final2.py`):

```
0x40a10 001e          (0x001e — reset/version strobe, cf. mainline cci_write 0x001e=0x0007)
0x40a38 6028          page select
0x40a60 6010          <-- CORE BOOT / release  (mainline: 0x6010 = 0x0001)
0x40a88 6226          <-- CORE enable          (mainline: 0x6226 = 0x0001)
0x40ab0 6028 ─┐
0x40ad8 602a  │ init-array page-offset / indirect-data pairs
0x40b00 6f12  │ (602a offset, 6f12 data), 11 pairs, matching init_array_setting
   …    …    ─┘
```

The matching **init VALUES** are the packed u32 run at **0x40fb4** (FACT):

```
0x40fb0 1388 2710                                 (timing consts 5000/10000)
0x40fb8 1354 7017 13b2 1236 1a0a 4c0a 2210 2176   \
0x40fd8 6400 222e 06b6 06bc 1001 2140 1a0e 9600    } init-array core values
0x40ff8 0011                                       / (mainline 0xf44e=0x0011 tail)
```

These are exactly the distinctive mainline `init_array_setting` values
(`0x1354, 0x7017, 0x13b2, 0x1236, 0x1a0a, 0x4c0a, 0x2210, 0x2176, 0x6400,
0x222e, 0x06b6, 0x06bc, 0x1001, 0x2140, 0x1a0e, 0x9600, 0x0011`). Each of
`0x1354/0x7017/0x4c0a/…` occurs **exactly once** in the whole file, right here —
these value literals are unique anchors (FACT, `scan_regtables.py`):

```
0x1354 -> 1 hit @0x40fb8      0x7017 -> 1 hit @0x40fbc
0x4c0a -> 1 hit @0x40fcc      (mainline init magic, present verbatim)
```

**(c) `preProgSettings` (id 3732) @ 0x40e2e, 74 B** — a short reg block beginning
`… 6f12 (indirect data), 6028 (page) …`. This is the "pre-program" sub-block that
precedes the mode writes; it is part of the same stream-config grouping, not a
power-on-time write. (FACT: bytes `02 0e … 12 6f 00 00 … 05 0e … 28 60 00 00`.)

---

## 3. What each table IS (with evidence)

| table | file off | classification | evidence |
|-------|----------|----------------|----------|
| id/value stream "0x0210 0x020e…" | 0x351bb | **parameter schema, NOT registers** | u32 IDs resolve to `delayUs/slaveAddr/registerData` field names; all "values" 0x0000 |
| 222-elem `6028/602a/6f12` + values@0x38077 | 0x35533 / 0x38077 | **mode/resolution config (res0, 4080×3072@30)** | value run == mainline `s5kjn1_4080x3072_30fps_mode` |
| `6028→6010→6226→6028→602a/6f12` + values@0x40fb4 | 0x40a38 / 0x40fb4 | **streamOn: core-boot + global init-array** | order & values == mainline `enable_streams` + `init_array_setting` |
| `preProgSettings` | 0x40e2e | pre-program reg sub-block (part of stream cfg) | `6f12,6028` tokens, id 3732 name |

Specifically for the registers the task asked about:
- **0x6010 (core boot/release)** — present **once**, @0x40a60, inside the streamOn
  block (data value = 1 per mainline). (FACT)
- **0x6226 (core enable)** — @0x40a88 (streamOn), also @0x38057 (mode). (FACT)
- **0x6028/0x602a/0x6f12 (indirect access port)** — the bulk of every table
  (18× 6028, 327× 602a, 645× 6f12). (FACT)
- **0x0100 (stream ctrl)** and **0x0103 (soft reset)** — 0x0103 appears only
  twice in the whole file (@0x393c, @0x360eb) and neither is in a reg-write
  element with `addr_type=2/data_type=2`; there is **no 0x0103 soft-reset write in
  any decoded table**. 0x0100 raw-u16 appears often but not as a standalone
  pre-id write element. (FACT / INFERENCE)
- **No write to any register sits between power-up and the chip-id read.** (FACT —
  the only things after the 0x35143 power block and before the reg tables is the
  id/value schema stream of §1.)

---

## 4. Is there an init-before-chip-id sequence to wake the module? — NO

**The blob groups its writes exactly like mainline groups them.** The core-boot
strobe (`0x6010=1`, `0x6226=1`) and the whole init-array are in the **streamOn**
block (§2b, @0x40a38), which in the Qualcomm flow — like mainline's
`s5kjn1_enable_streams` — runs at **stream start**, not at probe/identify time.

Cross-reference with the mainline driver (FACT, `s5kjn1-mainline.c`):

```
probe (line 1375-1379):   s5kjn1_power_on()          # rails+MCLK+reset only
                          s5kjn1_identify_sensor()   # cci_read(0x0000)  COLD, no writes
                                                     # line 1142: reads chip id with
                                                     #   ZERO register writes first
enable_streams (908-935): cci_write 0x0000=0x0003; 0x0000=0x38e1; 0x001e=0x0007
                          cci_write 0x6028=0x4000; 0x6010=0x0001            # core boot
                          cci_write 0x6226=0x0001                           # core en
                          cci_multi_reg_write(init_array_setting)           # init
                          cci_multi_reg_write(mode reg_list)                # mode
                          cci_write 0x0100 = STREAMING                      # stream on
```

So **both** the vendor blob and mainline read chip-id 0x0000 cold and only push
0x6010/0x6226/init-array at stream-on. There is no evidence in the blob of a
"release internal core before you can read the id" sequence for this module. The
init tables are the ordinary global-init + mode config, deferred to streamOn in
both stacks.

**Consequence for the bring-up bug:** the residue (0x0000 / 0xe7a1 / 0x8b0x)
returned by the cold chip-id read is *not* fixed by any table in this blob,
because the vendor stack also reads the id cold. The remaining variable is the
**power/clock/reset conditioning** that gets the digital core to the point where
0x0000 returns 0x38e1 cold — i.e. the §OUT_symbolref power-sequence delta
(MCLK-first ordering, the 24 MHz/1 ms/4 ms timing, 400 kHz bus), **not** a
register write. This report rules the register tables *in* as fully decoded and
*out* as the missing fix.

---

## 5. If you still want to try a wake sequence on the device (testable)

The blob does not mandate it before id, but the mainline core-boot strobe is the
one register operation most likely to make a stuck Samsung core report its id.
These are the exact writes the blob/ mainline use, worth trying *after* power-up
and *before* re-reading 0x0000, over `i2ctransfer` on the sensor at 7-bit 0x56
(slave 0xac), 16-bit reg / 16-bit data, 400 kHz:

```
# core boot / release (from streamOn block @0x40a38, values from mainline)
w 0x6028 0x4000        # page select (top)
w 0x6010 0x0001        # 0x6010 = 1   (soft/core boot)   <-- @0x40a60
w 0x6226 0x0001        # 0x6226 = 1   (core enable)      <-- @0x40a88
# then wait ~5-10 ms and read chip id
r 0x0000               # expect 0x38e1
```

i2ctransfer form (bus = the CCI child bus number, e.g. 4):

```
i2ctransfer -y 4 w4@0x56 0x60 0x28 0x40 0x00
i2ctransfer -y 4 w4@0x56 0x60 0x10 0x00 0x01
i2ctransfer -y 4 w4@0x56 0x62 0x26 0x00 0x01
i2ctransfer -y 4 w2@0x56 0x00 0x00 r2       # read chip id -> expect 38 e1
```

If that still returns residue, the fault is upstream of I2C (power/clock/reset
timing per OUT_symbolref), because there is nothing else in the blob to write.

The full init-array (if the core boot alone is not enough) is the 602a/6f12 pair
list decoded in §2b; but note the init-array is only meaningful once 0x6010 has
booted the core, and it is a *streaming* prerequisite, not an *identification*
one.

---

## 6. Parameter-table entries cross-referenced (task item 4)

Resolved (FACT, `parse2.py`; DATA_BASE-relative offsets):

| id | name | offset | size | note |
|----|------|--------|------|------|
| 13 | powerSetting | 0x8a3 | 72 | identification block: chip id 0x38e1 / mask 0xffffffff / eeprom 0xa0 reg 0x0d / "SU" (per OUT_symbolref) |
| 28 | streamConfiguration | 0x947 | 816 | stream/mode geometry |
| 34 | slaveAddr (container) | 0xc9b | 11080 | holds power seq @0x35143 **and** the 222-elem mode table @0x35533 + values @0x38077 |
| 3496 | slaveAddr (container) | 0xc0fc | 1560 | streamOn address template region (0x40118..0x40730) |
| 3613 | preInitSetting | 0x37e7 | 4 | shares the size-4 `0x37e7` slot (`slaveAddr`-family); no bulk data |
| 3732 | preProgSettings | 0xce12 | 74 | short `6f12/6028` pre-program block @0x40e2e |
| 3765 | slaveAddr (container) | 0xcf98 | 80 | **init-array VALUE array @0x40fb4** |
| 3807 | registerData | 0xd110 | 2 | small register-data leaf |
| 3431 | regSetting | 0xbe07 | 1 | `regSetting` leaf (`6f12` token) |

No entry named `init` / `initSettings` / `sensorInitSettings` / `streamOn` /
`res0` exists as a distinct populated top-level field; those concepts are carried
structurally inside the `slaveAddr` containers (34, 3496, 3765) as the mode and
streamOn reg tables above. (FACT: name scan in `parse2.py` finds
`preInitSetting`, `preProgSettings`, `streamConfiguration`, `regSetting`,
`registerData`, but no `initSettings`/`res0`/`streamOn`.)

---

## FACT / INFERENCE / UNKNOWN

**FACT**
- 0x351bb is the parameter id/value instance stream. u32 IDs 528/526/530/532 =
  `delayUs/slaveAddr/registerData/slaveAddr`; all u16 "values" are 0x0000. It is
  NOT a register-write table. (bytes @0x351bb; `parse2.py`)
- Reg element format = 40-byte tagged `cam_cmd_i2c_random_wr` (addr_type=2,
  data_type=2 discriminator). (0x35533, 0x40a38)
- Mode table (222 elems) addresses @0x35533; its values @0x38077 == mainline
  `s5kjn1_4080x3072_30fps_mode` (delta: reg 0x1a30 blob 0x3403 vs mainline 0x3401).
- StreamOn block @0x40a38: order `001e → 6028 → 6010 → 6226 → 6028 → 602a/6f12…`;
  its init values @0x40fb4 == mainline `init_array_setting`
  (`1354,7017,13b2,1236,1a0a,4c0a,2210,2176,6400,222e,06b6,06bc,1001,2140,1a0e,
  9600,0011`). Each of 0x1354/0x7017/0x4c0a occurs exactly once in the file, here.
- 0x6010 appears once (@0x40a60), 0x6226 twice (@0x40a88, @0x38057); both inside
  streamOn/mode blocks. 0x0103 (soft reset) appears only twice in the whole file
  and never as a reg element. No register write sits between power-up (0x35143)
  and the id/value stream.
- Mainline reads chip id 0x0000 cold at probe (`s5kjn1_identify_sensor`,
  line 1142, no prior writes) and pushes core-boot + init-array only at
  `enable_streams`/stream-on.

**INFERENCE**
- The blob's register tables are the *same data* as the mainline driver already
  ships; the vendor stack, like mainline, reads chip id cold. Therefore **no
  missing init-before-id table exists in this blob**, and the register tables are
  not the fix for the cold-read residue.
- The remaining lever is power/clock/reset conditioning (OUT_symbolref: MCLK-first
  ordering, 24 MHz / 1 ms / 4 ms timings, 400 kHz bus), not an I2C init write.
- If a register wake is nonetheless attempted, `0x6028=0x4000; 0x6010=0x0001;
  0x6226=0x0001` then re-read 0x0000 is the highest-probability try (it is the
  vendor/mainline core-boot strobe), §5.

**UNKNOWN**
- Whether the packed value runs at 0x38077 / 0x40fb4 pair 1:1 positionally with
  the zeroed address templates, or via the interleaved `idx` tags. The *identity*
  of the tables (== mainline) is certain from the unique value literals; the exact
  per-element re-pairing inside the compacted value run was not needed and not
  fully reconstructed.
- The single mode delta 0x3403 vs mainline 0x3401 (reg 0x1a30): likely a module-
  specific tuning value; effect on identification is none (it is a mode write).
- Whether 0x6010/0x6226 written *before* the id read (rather than at stream-on)
  actually unsticks this Sunny part — untested; §5 is the experiment.
