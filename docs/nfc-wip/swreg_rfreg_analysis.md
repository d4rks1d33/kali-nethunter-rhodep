# RE of the Samsung S3FWRN5 / S3NRN4V RF register blobs (rhodep, mainline)

Reverse engineering of the two vendor RF-register images the `s3fwrn5` driver
loads into the NFC controller of a Moto G82 (rhodep):

- `sec_s3fwrn5_rfreg.bin` — 3232 bytes, the "hwreg" (RF analog register image).
- `sec_s3fwrn5_swreg.bin` — 336 bytes, the "swreg" (software/protocol register image).

Goal: determine whether these blobs contain a listen / card-emulation (NFC-A
CE) configuration, or only a poll/reader configuration — i.e. whether the
"chip accepts the NCI listen setup with status 0x0 but never raises
`RF_INTF_ACTIVATED_NTF` on NFC-A" wall is caused by the blob missing a listen
RF profile.

All offsets are decimal unless prefixed `0x`.

---

## 0. How the blobs reach the chip (the "dual opcode", patch 0104)

FACT (from the port's `0104-nfc-s3fwrn5-dual-opcode-rfreg-transfer.patch` and
the vendor `drivers/nfc/s3fwrn5/nci.c`):

There are **two** transfer protocols in this driver family, selected by the
chip code the part reports:

| form   | chip code            | opcodes                                  |
|--------|----------------------|------------------------------------------|
| legacy | 0x80–0x83 (S3FWRN5=0x82) | START `2F 26`, SET `2F 22`, STOP `2F 27` (three distinct opcodes) |
| dual   | 0x74, or ≥0x84 (S3NRN4V=0x86) | everything through **`2F 2A`** with a 1-byte sub-command: `0x01`=start, `0x02 <index>`=chunk, `0x03 <checksum>`=stop |

The rhodep part is the **S3NRN4V, chip code 0x86 → dual**, so `rfreg_dual=1`
is mandatory (matches `nfc.md` "the one thing that must be set").

What "dual opcode" implies for the **blob format** (this is the important
inference for the task):

- **"Dual" is NOT about the file having two opcodes; it means the two images
  (`rfreg` + `swreg`) are concatenated into ONE transfer.** The vendor merges
  `hwreg ∥ swreg` before opening the session and sends the merged buffer in
  252-byte chunks through `2F 2A/0x02`. So the chip sees a single contiguous
  register stream = `rfreg.bin (3232)` immediately followed by `swreg.bin (336)`
  = 3568 bytes.
- The trailing checksum covers the **concatenation**.
- Chunk size is 252 bytes; the last chunk is short. Each image's own 16-byte
  trailer (see §1.4) is part of the data and part of the checksum.

### Checksum — FACT, verified

Algorithm (from `s3fwrn5_nci_rf_configure_dual`): sum of little-endian `u32`
words over `hwreg` then `swreg`, truncated to 16 bits.

```
sum_le32(rfreg.bin) & 0xffff = 0x0607
sum_le32(swreg.bin) & 0xffff = 0x5593
sum_le32(rfreg ∥ swreg) & 0xffff = 0x5B9A   ← matches dmesg exactly
```

`dmesg: "rfreg configuration update: success (checksum 0x5b9a)"` is reproduced
**only** when both blobs are summed together. This independently proves:
1. the checksum algorithm above,
2. that the two blobs are transferred as one concatenated image,
3. that both blobs on disk are byte-exact and complete.

---

## 1. Structure of `rfreg.bin` (3232 bytes, "hwreg")

This is a **flat RF analog register image**, not a TLV/`{opcode,addr,len,data}`
stream. It is a set of fixed-layout register *blocks* plus several raw
value/lookup tables, followed by a 16-byte trailer ending in the magic
`44 45 46 00` = `"DEF\0"`. There is no per-entry opcode/length framing inside
the image — the chip DMAs the whole thing into its RF register file; the framing
(START/chunk/STOP) is added by the driver on the wire, not stored in the file.

### 1.1 Overall layout

| region (bytes) | content |
|----------------|---------|
| 0 – 675   | **13 primary register blocks**, 52 bytes each (TX/antenna profiles) |
| 676 – 918 | RF tuning / AGC / gain tables (`0x4f`-marked groups, `aa aa`/`ff ff`/`99 99` calibration patterns) |
| 918 – 1230| **4 secondary register blocks** (`ef`-type, `40 22 40 04`), interleaved with `88 …` control words |
| 1230 – 1360| **3 tertiary register blocks** (`f8`-type, flag byte `00`, trailing `40`, `31` marker) |
| 1360 – 2592| large `{value_le32, param_le32}` register-value ladder (`xx 63 09 00` = regs in the 0x000963xx block, grouped by gain/step) |
| 2592 – 3007| per-technology coefficient arrays (triplet-of-triplets, see §1.3) + a final trailing 52-byte-style record |
| 3008 – 3215| zero padding |
| 3216 – 3231| 16-byte trailer (`65 03 6a 00 … 44 45 46 00`) |

### 1.2 The 20 register blocks (the core RF config)

Every RF register block carries the anchor `14 <XX> 80 fe 00 00 <flag>` where
`XX ∈ {f8, ef}` and `flag ∈ {c0, 00}`, followed by a 3-byte antenna/TX-driver
field. There are exactly **20** such blocks in three groups:

```
GROUP A — 13 blocks, 52 bytes, @0..675   type=f8  flag=c0   (trailing 40 = 00)
  recs 0-3  : sig 02e0, antenna [1f 40 20]
  recs 4-7  : sig 82e2/82e4, antenna [87 4f a0]
  recs 8-9  : sig 02e0, antenna [87 4f a0]
  rec 10    : sig 02e0, antenna [10 40 20]
  recs 11-12: sig 02e0, antenna [1f 40 20]

GROUP B — 4 blocks, @975..1211           type=ef  flag=c0   antenna [00 40 22 40 04]
  (embed 88 ea .. and the b8 21 0a 30 2a control word)

GROUP C — 3 blocks, @1259..1347          type=f8  flag=00   antenna [.. 40], marker 31
  rec: antenna [1f 40 20 40]  (reuses GROUP A's 1f 40 20)
  rec: antenna [87 4f a0 40]  (reuses GROUP A's 87 4f a0)  ×2
```

Field decoding of a GROUP-A 52-byte block (rec 0 as example):
```
02 00 00 00 00 00 00 00 00 | 84 | 02 e0 02 82 80 d2 54 2d 95 91 10 52 | 00 00 |
02 00 00 00 00 00 00 00 00 | 14 f8 80 fe 00 00 | c0 | 1f 40 20 | 00 00 00 00 00 | 08 00 00 00
 ^tag=02          ^b9=mode  ^--- RF mixer/PLL coeffs (12B) ---^        ^flag ^antenna/TX
```
- `tag = 0x02` on every GROUP-A/C block (block type = "register set").
- `b9` (byte 9) takes `84 / c4 / e0` — a per-block mode/tech selector.
- bytes 12–23: the 12-byte RF datapath coefficient (mixer/PLL/filter), the
  field that actually differs between technologies.
- **`flag` byte** (`c0` vs `00`) — see §2, the key poll/listen discriminator.
- **antenna/TX field** `1f 40 20`, `87 4f a0`, `10 40 20`, `40 22 40 04` —
  TX driver strength / modulation-index / load settings.

### 1.3 Per-technology coefficient arrays (2672–2870)

Classic "indexed by technology" tables, the shape gives them away:
```
07 07 04 07 07 04 05 03 03   (9 = 3 techs × 3 sub)
17 27 27 17 27 27 17 27 27   (3×3, repeated block)
0f 17 17 0f 17 17 0f 17 17
07 07 0f 07 07 0f 07 07 0f
1f 00 00 1f 00 00 1f 00 00
47 17 17 47 17 17 47 17 17
```
These are per-technology TX shaping / rise-fall / overshoot coefficients. The
3× replication is per-antenna-driver or per-bitrate. **All of these are
symmetric across the three techs — there is no A-vs-F asymmetry and no separate
listen column.**

### 1.4 Trailer (16 bytes @3216)

```
65 03 6a 00 00 82 10 10 34 08 02 02 | 44 45 46 00
^^ ^^ version 0x0365 (=3.65?)         ^^ "DEF\0" magic
```
`swreg`'s trailer is analogous: `65 03 66 01 01 7c 0f 0a 04 2b 01 00 | 44 45 46 00`.
Same `65 03` version tag, same `DEF\0` magic. This confirms both files are the
same family of "RF analog register DEF image", exactly as `nfc.md` states
("they end in the magic `DEF\0`, not NCI config").

---

## 2. Does rfreg distinguish poll (reader) from listen (CE)?

This is the central question. Findings:

### 2.1 The `c0` / `00` flag byte — FACT (structural), INFERENCE (meaning)

Of the 20 RF blocks:
- **17 blocks** carry flag `= 0xc0` (all of GROUP A's 13, all of GROUP B's 4),
- **3 blocks** carry flag `= 0x00` (all of GROUP C), and those 3 uniquely add a
  trailing `40` byte and a `0x31` marker, while **reusing GROUP A's antenna
  values** (`1f 40 20`, `87 4f a0`).

INFERENCE: the `c0` blocks are the **TX / poll (reader-mode) driver** profiles
(field-generating), and the 3 `00`+`40`+`31` GROUP-C blocks are a **distinct
RX-load / receiver-side** profile that reuses the same antenna match. This is
exactly the shape you would expect if the image carried an RX/receiver config
alongside the TX/poll config. The count is telling: **13 TX profiles vs only 3
RX-side blocks**, and the 3 do not obviously cover a full A/B/F/V listen matrix.

### 2.2 What is clearly present: a full POLL/TX configuration

- 13 TX-driver blocks with distinct per-tech RF coefficients and antenna
  strengths (`1f 40 20` = one antenna tune, `87 4f a0` = a higher/stronger tune,
  `10 40 20` = a reduced one).
- Per-technology TX shaping coefficient arrays (§1.3).
- A large gain/step register ladder (0x000963xx block).

This matches reality: **reader mode works for all technologies** (`nfc.md`
"What works").

### 2.3 What is NOT clearly present: an NFC-A LISTEN / CE RF profile

- There are **no `LA_*` listen TLVs** in the image (BIT_FRAME_SDD, PLATFORM,
  NFCID1, SEL_INFO) — expected, since those are NCI config, not RF analog. But
  more importantly:
- There is **no per-technology listen/RX matrix** comparable to the 13-entry TX
  table. The only receiver-side candidate is the 3-block GROUP C, and it is not
  organized per-listen-technology the way the TX side is per-poll-technology.
- The receiver/load-modulation parameters that an NFC-A *tag* needs (subcarrier
  load-modulation drive, RX front-end sensitivity thresholds for the
  106 kbit/s A listen slot) are not present as a distinct, per-technology
  listen block. FeliCa (NFC-F) listen works — and FeliCa listen on this family
  is done autonomously in firmware (see `nfc.md` line 41 "NFC-F listen also
  works autonomously, in firmware"), so it does not need a host-supplied listen
  RF profile the way NFC-A does.

INFERENCE (high confidence): **this rfreg image is a POLL/reader RF profile
plus a minimal receiver block; it does NOT contain a dedicated NFC-A listen
(card-emulation) RF/load-modulation profile.** That is fully consistent with
the observed symptom — the chip accepts every NCI listen command with
`status 0x0` (the NCI-layer config is fine) but the **analog listen front-end
for NFC-A never engages at RF**, so no `RF_INTF_ACTIVATED_NTF`. NFC-F listen
engages because that path is firmware-autonomous and does not depend on a
host-loaded NFC-A listen profile.

CAVEAT / UNKNOWN: the exact semantics of the `c0`/`00` flag and the `0x31`
marker are inferred, not proven from a datasheet. The absence of an NFC-A
listen profile is an inference from *structure and symmetry* (13 TX blocks, no
matching listen matrix) plus the *behavioral* evidence in `nfc.md`, not from a
labelled field that says "poll" or "listen". A register map for the S3NRN4V RF
IP would be needed to confirm which register the `c0` bit lands in.

---

## 3. Structure of `swreg.bin` (336 bytes, "swreg")

A small software/protocol register image with the same `DEF\0` family trailer.
It is **timing/protocol parameters**, not RF and not listen config.

### 3.1 Layout

| region (bytes) | content |
|----------------|---------|
| 0 – 15   | header: `04 00 01 07 03 e8 00 0a …` (`03 e8` = 1000, a duration/timeout) |
| 16 – 203 | packed protocol/framing parameter registers (guard times, FDT, bitrate flags — e.g. `73 ff ff ff ff ff`, `50 50 05 05`, `32 06 00 32`) |
| 204 – 259| **doubling ladder**: `0x12e, 0x25c, 0x4b8, 0x970, 0x12e1, 0x25c2, 0x4b84, 0x9708, 0x12e10, 0x25c21, 0x4b842, 0x97084, 0x12e109, 0x25c213` — each ≈ 2× the previous. A timing / FDT / frame-delay ladder indexed by bitrate or retry step. |
| 260 – 319| **bit-shift ladder**: `0x2000, 0x4000, 0x8000, 0x10000 … 0x400000` (single walking bit) — a mask/threshold table. |
| 320 – 335| 16-byte trailer `65 03 66 01 01 7c 0f 0a 04 2b 01 00 44 45 46 00` |

### 3.2 Listen / CE content in swreg?

**None found.** The ladders and header are protocol-timing (FDT, guard time,
frame-delay, bitrate masks). There are no `LA_*`/`LF_*` fields, no per-mode
poll/listen split, no CE enable. The two `03 e8` (=1000) values are durations
(poll/discovery window), not a listen switch.

INFERENCE: swreg is the **NCI/RF protocol timing register set** (common to poll
and listen). It does not gate NFC-A card emulation.

---

## 4. Comparison to what NFC-A listen (CE) needs, and what is missing

For an S3FWRN5-family part to activate as an **NFC-A tag** in listen mode, two
things must be in place:

1. **NCI-layer listen config** — `LA_BIT_FRAME_SDD`, `LA_PLATFORM_CONFIG`,
   `LA_NFCID1`, `LA_SEL_INFO`, `RF_DISCOVER_MAP` (T2T→FRAME, LISTEN),
   `RF_SET_LISTEN_MODE_ROUTING`, `RF_DISCOVER` with NFC-A passive listen.
   → **This is patch 0111, and it is already correct**: the chip returns
   `status 0x0` to all of it (`nfc.md`).

2. **Analog RF listen profile** — the receiver front-end + load-modulation
   registers that let the antenna *answer* an NFC-A reader's field at 106 kbit/s
   as a tag (subcarrier generation, RX threshold/sensitivity for the listen
   slot). On Android this is loaded by the vendor HAL as part of the RF-register
   update. → **This is what these blobs would have to contain, and the NFC-A
   listen portion appears to be absent** (§2.3): the image is a poll/TX profile
   with a small receiver block that does not span the listen technologies the
   way the TX side spans the poll technologies.

So the missing piece is category (2): a **listen-specific analog RF profile for
NFC-A**. `nfc.md` reaches the same conclusion behaviorally ("the NFC-A listen
front-end never engages at RF … most likely a vendor RF profile the plain NCI
path does not reproduce"). This RE of the blob **corroborates that from the data
side**: the blob the mainline driver loads is a poll-oriented image without a
dedicated NFC-A listen RF matrix.

Whether the *fix* is "modify the blob" is nuanced (see §6).

---

## 5. The dual opcode and the blob format (task item 5) — summary

- "Dual opcode" = the single `2F 2A` opcode carrying a sub-command byte, used by
  chip codes 0x74 / ≥0x84 (this part, 0x86). It replaces the legacy three-opcode
  (`26/22/27`) scheme.
- For the **format of the blob it means the two files are one concatenated
  register image** on the wire (`rfreg ∥ swreg`), 252-byte chunks, one 16-bit
  LE-u32-sum checksum over the whole (`0x5b9a`, verified). It does **not** mean
  there are two opcodes stored inside the file; the file is a flat DEF register
  image with no internal opcode framing.
- Both files independently carry the `65 03 … DEF\0` trailer, i.e. each is a
  standalone DEF image; the driver merges them for transport.

---

## 6. Recommendation: blob vs NCI vs kernel

**The blob is the most likely root cause of the NFC-A-listen-never-activates
symptom, but "edit this blob" is not a viable fix by itself.**

Ranked:

1. **Root cause = missing NFC-A listen RF profile (the blob), FACT-adjacent.**
   The evidence (13 poll/TX blocks, no matching listen matrix; NFC-F listen
   works because it is firmware-autonomous; every NCI listen command returns
   status 0x0 yet RF never engages; Flipper sees nothing on A) all points to the
   analog listen front-end for NFC-A not being configured. The blob mainline
   loads does not carry that configuration.

2. **The fix is NOT in the NCI config / patch 0111.** That layer is already
   complete and accepted by the chip (status 0x0 on everything, LMRT + MAP +
   DISCOVER all present). Adding more NCI commands will not light an unconfigured
   analog front-end. (Consistent with `nfc.md`'s conclusion.)

3. **The fix is NOT a hand-edit of `rfreg.bin`.** We can locate the candidate
   listen/RX blocks (GROUP C, the `00`/`31` blocks) and the `c0` poll flag, but
   we do **not** have the S3NRN4V RF register map, so we cannot know which bits
   enable NFC-A load modulation without a datasheet. Blindly flipping the `c0`
   flag or cloning a TX block into a listen slot risks detuning the antenna or
   bricking reader mode, and the checksum would have to be recomputed
   (trivial — §0 — but the *values* are the hard part).

4. **The realistic path (matches `nfc.md`) is to capture the vendor's listen RF
   profile, not synthesize it:**
   - Dump the **exact RF-register stream the Android vendor HAL sends when card
     emulation / the SUBE app is active** (the HAL may load a *different* or
     *additional* rfreg profile for listen, or issue extra `2F 25/26/27/28/2A`
     proprietary RF commands at CE start). Compare that stream against
     `rfreg ∥ swreg`; the delta is the listen profile.
   - Alternatively, disassemble the HAL's RF-register update path
     (`hal_vs_rfreg_update_dual_option` and its callers) specifically along the
     **card-emulation / listen** code path to see if a second/listen blob or a
     register override is applied that mainline never loads.
   - If such a listen profile exists, ship it as an additional firmware image (or
     an extended rfreg) and have the driver load it when entering listen — this
     is a **driver + firmware** change, not an NCI change.

5. **If no separate listen blob exists in the HAL**, then the listen front-end
   is armed by a proprietary command sequence (not by the register image), and
   the fix is in the **kernel driver** (issue that vendor sequence at CE start) —
   which requires the captured NCI + proprietary stream from Android.

Net: **root cause is on the RF-profile (blob/firmware) side; the actionable fix
is to capture the vendor listen RF profile/sequence from Android and add a
driver path to load it — not to edit these blobs by hand and not to add NCI
commands.**

---

## 7. FACT / INFERENCE / UNKNOWN

FACT
- `rfreg.bin` = 3232 B, `swreg.bin` = 336 B; both end in `DEF\0` and share a
  `65 03 …` version trailer.
- Checksum `0x5b9a` = `sum_le32(rfreg ∥ swreg) & 0xffff` (verified); proves the
  concatenated dual transfer and that both blobs are byte-exact.
- Dual opcode = `2F 2A` with sub-command `0x01/0x02/0x03`; 252-byte chunks;
  legacy path is `2F 26/22/27`. This part is chip code 0x86 → dual.
- `rfreg.bin` structure: 13 primary 52-byte TX/antenna blocks + tuning tables +
  4 secondary (`ef`) blocks + 3 tertiary (`f8`,flag=0,marker 0x31) blocks +
  a value ladder + per-tech coefficient arrays + zero pad + trailer.
- rfreg contains a complete poll/reader RF config (13 TX blocks, per-tech
  shaping). 17 blocks flag `c0`, 3 blocks flag `00`.
- `swreg.bin` is protocol-timing: a doubling FDT/timing ladder and a walking-bit
  mask ladder; no `LA_*`/`LF_*`, no CE/listen field.

INFERENCE
- `c0` blocks = poll/TX driver profiles; the 3 `00`/`0x31` GROUP-C blocks =
  receiver/RX-load profiles (reuse the poll antenna match). (Structural.)
- rfreg has NO dedicated per-technology NFC-A **listen** RF profile; it is a
  poll image with a minimal RX block. (Structure + symmetry + behavior.)
- This is the RF-side cause of "NCI listen accepted (status 0x0) but NFC-A
  front-end never engages at RF". NFC-F listen works because it is
  firmware-autonomous, independent of a host NFC-A listen profile.
- The fix is a driver+firmware change (load the vendor listen RF profile/
  sequence), not an NCI change and not a hand-edit of these blobs.

UNKNOWN
- Exact bit semantics of the `c0`/`00` flag and the `0x31` marker (no S3NRN4V RF
  register map available).
- Whether the Android vendor HAL ships a *separate* listen rfreg image or arms
  listen via a proprietary command sequence — needs an HAL disassembly of the
  CE/listen path or an on-device capture of the RF-register + NCI stream while
  Android runs card emulation. That capture is the decisive next experiment.
- Precise meaning of the `b9` mode byte (`84/c4/e0`) and the 12-byte RF
  coefficient per block (mixer/PLL/filter — identified by position, not decoded
  to physical values).
