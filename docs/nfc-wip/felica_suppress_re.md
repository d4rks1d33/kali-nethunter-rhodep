# S3FWRN5 / S3NRN4V — Suppressing the autonomous FeliCa/Type-3 responder while routing NFC-A to the eSE (MIFARE SUBE)

Target: Moto G82 (rhodep), Samsung S3NRN4V CLF, `nfc_nci_sec.so` (ARM64, 117 KB,
stripped, BuildID f4972906…), `sec_s3nrn4v_firmware.bin` (packed Cortex-M image),
kernel `net/nfc/nci` with patches 0001-0113 applied.

Every claim is tagged **FACT** (cited from binary offset / log line / conf line /
source line), **INFERENCE** (derived), or **UNKNOWN**.

---

## 0. TL;DR — the two things that are actually wrong right now

1. **The eSE path sends NO listen-A identity SET_CONFIG at all.** In the live
   `keep_dep_peer=1` / eSE run only **one** SET_CONFIG goes out (plen 5 =
   `TOTAL_DURATION`). The four listen-A tags the *reader* run sent (`LA_BIT_FRAME_SDD`,
   `LA_PLATFORM_CONFIG`, `LA_NFCID1`, `LA_SEL_INFO`) are **deliberately skipped**
   by `core.c.with0113:1100 if (!nci_emu_ese_id(ndev))`. That is correct in intent
   (the eSE owns the NFC-A identity) but it is *also* why no NFC-A listen entry is
   armed by the host — combined with #2 below, the firmware falls back to its
   autonomous NFC-F/Type-3 peer. **FACT** (source + `keepdep2_dmesg.log:1`,
   `reader_dmesg.log:1,9,17,25,33`).

2. **The prior RE's assumption that `LF_PROTOCOL_TYPE=0` keeps the RF up is FALSE
   on this firmware.** Live: `LF_PROTOCOL_TYPE=0` sent ⇒ front-end never lights,
   reader sees nothing. `LF_PROTOCOL_TYPE=0` NOT sent ⇒ RF lights but the firmware
   answers as FeliCa autonomously. So the FeliCa peer and the RF are *coupled*: the
   same NFC-F/NFC-DEP listener that lights the analog front-end **is** the FeliCa
   responder. You cannot kill it with `LF_PROTOCOL_TYPE=0` without also killing the
   RF. **FACT** (`live_evidence.txt:16-19`).

The real fix therefore is **not** "keep NFC-F listen as an RF primer and disarm the
peer." It is: **stop relying on NFC-F as the primer, arm a real NFC-A LISTEN entry,
and let NFC-A activate directly** — routed to the eSE for the identity/Crypto1, or
to the host for the anticollision, with NFC-F/NFC-DEP removed from both the
RF_DISCOVER offer and the routing table so the firmware never brings its FeliCa
peer up. Details in §2, §5, §6.

---

## 1. Ground truth from the live captures (FACT)

`keepdep2_dmesg.log` and `felica_dmesg.log` are the **same run** (byte-identical
except timestamps — `diff` shows only the clock differs). Sequence sent by the
current 0113 eSE / `keep_dep_peer=1` path:

| line | cmd | opcode | plen | meaning |
|---|---|---|---|---|
| 1  | SET_CONFIG      | 0x02  | 5  | 1 param, id+len+2 bytes ⇒ **TOTAL_DURATION only** (`E8 03`) |
| 9  | RF_DISCOVER_MAP | 0x100 | 4  | 1 triplet (T2T/LISTEN/FRAME) |
| 17 | LMRT            | 0x101 | 27 | more+num+**5 entries** |
| 25 | RF_DISCOVER     | 0x103 | 5  | num=2 ⇒ **A-listen + F-listen** |
| 33 | RF_DEACTIVATE   | 0x106 | 1  | teardown |

Result: **0 `RF_INTF_ACTIVATED_NTF`** to the host (grep of the whole file), reader
sees FeliCa. **FACT.**

Contrast — the old `reader_dmesg.log` (host-DEP path):

| line | cmd | opcode | plen | inferred tag(s) |
|---|---|---|---|---|
| 1  | SET_CONFIG | 0x02 | 4 | 1 param, id+len+**1 byte** → `LA_BIT_FRAME_SDD = 0x04` |
| 9  | SET_CONFIG | 0x02 | 4 | 1 param, id+len+**1 byte** → `LA_PLATFORM_CONFIG = 0x00` |
| 17 | SET_CONFIG | 0x02 | 10| 1 param, id+len+**7 bytes** → `LA_NFCID1` (7-byte UID) |
| 25 | SET_CONFIG | 0x02 | 4 | 1 param, id+len+**1 byte** → `LA_SEL_INFO = emu_sel_res` |
| 33 | SET_CONFIG | 0x02 | 5 | 1 param, id+len+**2 bytes** → `TOTAL_DURATION = E8 03` |

That path got 96 activations, but as **NFC-F LISTEN (tech 0x82) / NFC-DEP (proto
0x5) / FRAME (intf 0x1)** (`reader_dmesg.log:66-77`) — i.e. the host-side DEP peer,
not a tag. **FACT.**

> The plen→tag mapping above is **INFERENCE**, but it is a *strong* one: it is
> exactly what `core.c.with0112` / `core.c.with0113` emit in the emulation branch
> (`nci_set_config(NCI_LA_BIT_FRAME_SDD,1,..)`, `…PLATFORM_CONFIG,1,..`,
> `…NFCID1,7,..`, `…SEL_INFO,1,..`, `…TOTAL_DURATION,2,..`), and the plens 4/4/10/4/5
> match 1:1. The kernel log prints only opcode+plen, never payload bytes, so the
> byte values themselves are read from the source, not the wire.

---

## 2. The 4 "missing" listen-A SET_CONFIG — tag + byte-exact value

These are the four the `reader_dmesg` run sent and the eSE run drops. Values are
the ones the source already computes; NCI tag IDs are the standard NCI 1.0/2.0
Listen-A config IDs (also present as `NCI_LA_*` in the kernel `nci.h`).

| # | tag name | tag ID | len | value (bytes) | on-wire SET_CONFIG frame |
|---|---|---|---|---|---|
| 1 | `LA_BIT_FRAME_SDD`  | **0x30** | 1 | `04`            | `20 02 04 01 30 01 04` |
| 2 | `LA_PLATFORM_CONFIG`| **0x31** | 1 | `00`            | `20 02 04 01 31 01 00` |
| 3 | `LA_NFCID1`         | **0x33** | 7 | `04 35 3d 6a f7 54 80` | `20 02 0A 01 33 07 04 35 3D 6A F7 54 80` |
| 4 | `LA_SEL_INFO`       | **0x34** | 1 | `emu_sel_res`   | `20 02 04 01 34 01 <SAK>` |

Notes (**FACT** unless marked):
- The UID `04:35:3d:6a:f7:54:80` is from `felica.log:2` (the app's advertised UID).
- `LA_SEL_INFO` (SAK): for a **MIFARE Classic 1K** it is **0x08**; for a 4K it is
  **0x18**. The SUBE is MIFARE Classic → **use 0x08** on the *host* path. On the
  eSE path the SAK must come from the eSE's real card, so LA_SEL_INFO should NOT be
  forced by the host (see §5.B). **INFERENCE** (MIFARE Classic SAK is standardised).
- `LA_BIT_FRAME_SDD=0x04` + a 7-byte `LA_NFCID1` makes the controller build ATQA
  low byte `0x44` (double-cascade) → matches the SUBE ATQA `0x0044`.
  `LA_PLATFORM_CONFIG=0x00` keeps the ATQA high byte 0. **INFERENCE**, as annotated
  in `core.c.with0113:1082-1090`.

**These four tags are what arm the NFC-A LISTEN responder in the *host* path.**
On the *eSE* path they must NOT be sent (the eSE supplies its own ATQA/SAK/UID/
Crypto1); instead the NFC-A technology is steered to the eSE by the LMRT (§5.B).

---

## 3. How does the FeliCa/Type-3 autonomous peer get suppressed? (the core question)

### 3.1 It is NOT a proprietary command in the HAL. (FACT)

Full survey of every `0x2F..` opcode the HAL can emit (each is a `mov wN,#imm`
whose low byte is 0x2F, stored little-endian as GID/OID by `strh`):

| site (offset) | immediate | on-wire opcode | function | purpose |
|---|---|---|---|---|
| `10178` | `0x172f` | `2F 17` | `hal_nci_send_setSWAPITrace` | SWAPI trace |
| `10604` | `0x282f` | `2F 28` | `hal_nci_send_prop_fw_cfg`   | **FW clock** (`FW_CFG_CLK_SPEED`/`FW_CFG_CLK_TYPE`) |
| `10a48` | `0x2f4f` | `4F 2F` | `nfc_hal_prehandler`         | RSP match (`NCI_PROP_FW_CFG_RSP`) |
| `16368` | `0x222f` | `2F 22` | `hal_vs_rfreg_update`        | RF register blob load |
| `165cc` | `0x2a2f` | `2F 2A` | `hal_vs_rfreg_update_dual_option` | dual RF register blob load |

**FACT** (offsets from `objdump -d nfc_nci_sec.so`; function names from the C++
mangled symbols; the `prop_fw_cfg` payload keys resolve to strings `FW_CFG_CLK_SPEED`
@0x6a77 and `FW_CFG_CLK_TYPE` @0x7aa0). None of these is a "card-emulation enable"
or a "disable Type-3" command. `hal_nci_send_prop_fw_cfg` is purely the clock
config, confirming the prior RE. **There is no vendor opcode that arms NFC-A tag
listen or that toggles the FeliCa peer.** **FACT.**

- `hal_nci_send_clearLmrt` (`0x10028`) emits a fixed const `21 01 02 00 00`
  (RF_SET_LISTEN_MODE_ROUTING, num_entries=0 → a clear). **FACT** (prior RE §1a).
- `nfc_hal_pre_discover` (`0xe740`) sends no NCI. **FACT** (prior RE §0).
- `device_set_mode` (`0xd250`) is an `ioctl` to `/dev/sec-nfc`, not NCI. **FACT.**

⇒ The HAL is transport + config. The FeliCa suppression must be done with
**CORE_SET_CONFIG (LF_* tags) and the RF_DISCOVER / routing offer**, driven by the
NFA stack on Android — which on Linux we must reproduce in the kernel.

### 3.2 The firmware image cannot be statically reversed for the responder. (FACT)

`sec_s3nrn4v_firmware.bin` starts with the ASCII build tag `202112140956` then a
header of section offsets (`hdr[+0c]=0x001735f6` etc.), and the body is
high-entropy (all 256 byte values present, no ASCII, no plausible Cortex-M vector
table — the "reset vector" reads as ASCII `0x34313231`). It is **packed/encrypted/
signed**; the FeliCa/Type-3 state machine is not recoverable from it without the
unpacker. **FACT.** This matches the prior RE's conclusion that the listen-A vs
FeliCa behaviour lives in the analog RF profile blobs, not in anything textual.

### 3.3 What actually gates the FeliCa peer (INFERENCE, backed by the live A/B)

The firmware arms an autonomous NFC-F(Type-3)/NFC-DEP listener **whenever NFC-F
LISTEN is offered in RF_DISCOVER and NFC-F/NFC-DEP is routed**. The live A/B proves:

- Offer NFC-F listen + route NFC-DEP, `LF_PROTOCOL_TYPE` unset ⇒ FeliCa answers
  (autonomous, 0 host NTF). **FACT.**
- Offer NFC-F listen + route NFC-DEP, `LF_PROTOCOL_TYPE=0` ⇒ RF dies entirely.
  **FACT.**

Therefore, on this firmware, **the only reliable way to suppress the FeliCa/Type-3
peer is to not offer NFC-F LISTEN and not route NFC-F/NFC-DEP at all.** The peer is
not a separately-gateable feature layered on top of an NFC-F "carrier"; the NFC-F
listener *is* the peer. `LF_T3T_FLAGS=0` / `LF_PROTOCOL_TYPE=0` do not carve the
carrier away from the responder — they just make the whole NFC-F listen slot
invalid so nothing comes up. **INFERENCE (strong), from the live A/B.**

The corollary is the important one: **NFC-A LISTEN must be made to light the front-
end on its own.** The reason it "never lights for A alone" in the earlier
experiments is #1 in §0 — the A-listen *identity* config (the four §2 tags, or a
proper eSE technology route) was never programmed in the runs that offered A. When
a valid NFC-A LISTEN entry exists (identity on host, or NFC-A technology routed to
an enabled eSE) and NFC-F is *removed* from the offer, the CLF has a real A-listen
slot to bring up. **INFERENCE** — this is the hypothesis the fix in §6 tests, and it
is the only configuration not yet tried (every prior A-listen run either omitted
the identity/route or kept NFC-F alongside).

### 3.4 Byte-exact config to try for suppression (in priority order)

1. **Remove NFC-F from RF_DISCOVER and from the LMRT** (primary lever, §3.3). No
   NFC-F listen offered ⇒ no FeliCa peer to arm. **This is the change that matters.**
2. Keep `LF_PROTOCOL_TYPE (0x50) = 0x00` sent as belt-and-suspenders (no NFC-DEP
   over F even if F ever appears): `20 02 04 01 50 01 00`. Harmless with F removed.
   **INFERENCE.**
3. **Do NOT** send `LF_T3T_FLAGS (0x3F) = 0` on its own: with no matching
   `LF_T3T_IDENTIFIERS`/`LF_T3T_PMM` the S3FWRN5 rejects it (SET_CONFIG status 0x9)
   and leaves the listen config inconsistent (documented at `core.c.with0113:1131-1133`).
   With NFC-F removed from the offer it is unnecessary anyway. **FACT** (port note) +
   **INFERENCE.**
4. `LF_CON_BITR_F`, `LF_T3T_PMM`, `LF_T3T_IDENTIFIERS`, `LF_ADV_FEAT`: leave unset.
   They only matter if you *keep* an NFC-F listen slot; we are removing it. **INFERENCE.**

---

## 4. NFCEE_POWER_AND_LINK_CTRL (GID 0x2 OID 0x3) — is it needed?

- **In the HAL?** No. `21 03` / opcode 0x203 does not appear as a constant or a
  send site in `nfc_nci_sec.so` (the HAL never builds RF/NFCEE commands at all —
  §3.1). **FACT.**
- **Is it needed for the eSE to answer NFC-A?** **UNKNOWN**, but **plausible and
  cheap to add**. `OFFHOST_AID_ROUTE_PWR_STATE=0x3B` (`libnfc-nci.conf:87`) and
  `DEFAULT_ROUTE=0x83` mean Android intends the eSE to answer in low-power/off
  states, which on NCI 2.0 is exactly what `NFCEE_POWER_AND_LINK_CTRL` keeps alive.
  It is the standard "keep the eSE powered and NFCC↔eSE link up during listen"
  command.
- **Bytes to send** (after a successful `NFCEE_MODE_SET(0x83, ENABLE)`):

  ```
  20 03 02 83 03
  │  │  │  │  └─ PLI = 0x03  (NFCC keeps power AND link always active)
  │  │  │  └──── NFCEE id 0x83 (the eSE holding the MIFARE card)
  │  │  └─────── payload len 2
  │  └────────── OID 0x03  (NFCEE_POWER_AND_LINK_CTRL)
  └───────────── GID 0x02  (NCI_GID_NFCEE_MGMT)
  ```

  > Note the GID byte is **0x20** (NFCEE mgmt is GID 0x2, MT=cmd), NOT `0x21`. The
  > prior RE and the port note wrote `21 03 02 83 03`; that is the *RF* GID (0x1) and
  > is wrong for this command. Correct opcode is **0x203**, first byte **0x20**.
  > **FACT** (NCI 2.0 §3/§10: NFCEE management is GID 0x2).

- Only send it if the controller advertises the feature; if it rejects with
  `status 0x03/0x09`, drop it — it is not a hard dependency for MIFARE routing,
  which is a pure LMRT operation. **INFERENCE.**

---

## 5. RF_DISCOVER_MAP and the LMRT

### 5.A RF_DISCOVER_MAP (opcode 0x100)
- Current listen map (`nci_rf_discover_map_listen_req`, `core.c.with0113:912`):
  a single triplet **T2T / LISTEN / FRAME** (`plen 4`, matches
  `keepdep2_dmesg.log:9`). **FACT.**
- MIFARE (0x80) must **never** be put in DISCOVER_MAP (chip rejects, status 0x1).
  **FACT** (prior RE §2, reproduced).
- For eSE routing you do **not** need a DISCOVER_MAP entry at all: routing an NFC-A
  technology to an NFCEE is a pure LMRT operation; the CLF forwards RF to the NFCEE
  without a host RF interface. Keeping the T2T/FRAME/LISTEN entry is harmless and is
  the fallback interface for the host path. **INFERENCE (strong).**

### 5.B LMRT (opcode 0x101) — the change that steers NFC-A to the eSE, FeliCa off

Current 0113 table (5 entries, plen 27 — `keepdep2_dmesg.log:17`):

```
1) TECH  A     -> eSE 0x83  @0x3F(ALL)   00 03 3F 83 00   <- decisive: eSE owns NFC-A
2) PROTO 0x80  -> eSE 0x83  @0x3F(ALL)   01 03 3F 83 80   <- MIFARE
3) PROTO 0x04  -> eSE 0x83  @0x3F(ALL)   01 03 3F 83 04   <- ISO-DEP
4) TECH  F     -> host 0x00 @0x08(RF)    00 03 08 00 02   <- **arms the FeliCa peer**
5) PROTO 0x05  -> host 0x00 @0x08(RF)    01 03 08 00 05   <- NFC-DEP, arms the peer
```

> (Entry wire order is `type,len,power_state,nfcee_id,value` per
> `struct nci_lmrt_{tech,proto}_entry`. Power values from the source:
> `NCI_LMRT_POWER_STATE_ALL` for 1-3, `NCI_LMRT_POWER_STATE_RF` for 4-5.)

**Entries 4 and 5 are exactly what tell the firmware to bring up its NFC-F/NFC-DEP
listener — i.e. the FeliCa peer (§3.3).** Remove them:

```
more=0  num_entries=3
1) TECH  A     -> eSE 0x83  @0x3B   00 03 3B 83 00
2) PROTO 0x80  -> eSE 0x83  @0x3B   01 03 3B 83 80
3) PROTO 0x04  -> eSE 0x83  @0x3B   01 03 3B 83 04
```
Full command: `21 01 11 00 03  00 03 3B 83 00  01 03 3B 83 80  01 03 3B 83 04`
(plen 0x11 = 17).

- Power state **0x3B** = the vendor `OFFHOST_AID_ROUTE_PWR_STATE`
  (`libnfc-nci.conf:87`) — on + off + battery-off + screen-off variants, so the
  eSE answers with the phone powered down. Use 0x3B for eSE entries. **FACT** (conf)
  + **INFERENCE** (reuse for tech/proto).
- eSE id **0x83**: trust the live enumeration — `ese_dmesg` NFCEE_DISCOVER_NTF shows
  eSE = 0x83 with proto MIFARE (0x80); the conf label `OFFHOST_ROUTE_ESE={82}` is
  the swapped-label board quirk noted in the prior RE §6. `DEFAULT_ROUTE=0x83`,
  `DEFAULT_OFFHOST_ROUTE=0x83`, `DEFAULT_NFCF_ROUTE=0x83` all corroborate 0x83.
  **FACT/INFERENCE.**

> This 3-entry, no-F/DEP table is the one the prior RE labelled "the nothing-
> activates case." That label was drawn under the wrong assumption (that F primes
> the RF). Per the live A/B (§3.3), the nothing-activates earlier runs also had **no
> NFC-A LISTEN offered together with a valid A route**. The fix in §6 offers NFC-A
> LISTEN *and* routes A-tech to an *enabled* eSE with this 3-entry table — a
> combination not previously tested.

### 5.C RF_DISCOVER (opcode 0x103)
- Current: `num=2` = NFC-A listen (0x80) + NFC-F listen (0x82) (plen 5,
  `keepdep2_dmesg.log:25`). **FACT.**
- **Change: offer NFC-A LISTEN only** → `num=1`:
  ```
  21 03 03 01  80 01
             │  │  └─ frequency 1
             │  └──── NFC-A PASSIVE LISTEN (0x80)
             └─────── num_configs = 1
  ```
  Dropping 0x82 (NFC-F listen) is the RF_DISCOVER half of removing the FeliCa peer.
  **INFERENCE**, paired with 5.B.

---

## 6. The complete correct sequence (byte-exact where possible)

Two variants. **Try the eSE variant first** (real SUBE card in eSE, answers Crypto1
itself). If the front-end still won't light for A-only, fall back to the host
variant to confirm the A-listen analog path works at all, then return to eSE.

### Variant A — NFC-A LISTEN routed to eSE (goal state)
```
# identity: none on host (eSE owns ATQA/SAK/UID/Crypto1)
CORE_SET_CONFIG  TOTAL_DURATION   20 02 05 01 00 02 E8 03      # 1000 ms listen window
CORE_SET_CONFIG  LF_PROTOCOL_TYPE 20 02 04 01 50 01 00        # belt-and-suspenders, no DEP-over-F
NFCEE_DISCOVER                    22 00 00                     # NCI 2.0 (learns 0x83)
NFCEE_MODE_SET(0x83, ENABLE)      22 01 02 83 01              # skip if already ENABLED (status 0x03)
NFCEE_POWER_AND_LINK_CTRL         20 03 02 83 03              # try; drop if rejected (§4)
RF_DISCOVER_MAP                   21 00 04 01 02 02 01        # T2T/LISTEN/FRAME (harmless fallback)
RF_SET_LISTEN_MODE_ROUTING       21 01 11 00 03 00 03 3B 83 00 01 03 3B 83 80 01 03 3B 83 04
RF_DISCOVER                       21 03 03 01 80 01           # NFC-A LISTEN only, NO NFC-F
```

### Variant B — NFC-A LISTEN answered by the host (diagnostic / host-CE)
```
CORE_SET_CONFIG  LA_BIT_FRAME_SDD   20 02 04 01 30 01 04
CORE_SET_CONFIG  LA_PLATFORM_CONFIG 20 02 04 01 31 01 00
CORE_SET_CONFIG  LA_NFCID1          20 02 0A 01 33 07 04 35 3D 6A F7 54 80
CORE_SET_CONFIG  LA_SEL_INFO        20 02 04 01 34 01 08      # SAK 0x08 = MIFARE Classic 1K
CORE_SET_CONFIG  TOTAL_DURATION     20 02 05 01 00 02 E8 03
CORE_SET_CONFIG  LF_PROTOCOL_TYPE   20 02 04 01 50 01 00
RF_DISCOVER_MAP                     21 00 04 01 02 02 01      # T2T/LISTEN/FRAME
RF_SET_LISTEN_MODE_ROUTING         21 01 11 00 03 00 03 3F 00 00 01 03 3F 00 02 01 03 3F 00 80
                                    #   A-tech->host, T2T->host, MIFARE->host
RF_DISCOVER                         21 03 03 01 80 01         # NFC-A LISTEN only, NO NFC-F
```

Order rationale (**INFERENCE**, canonical NCI 2.0 CE order): SET_CONFIG →
NFCEE_DISCOVER → NFCEE_MODE_SET → (POWER_AND_LINK_CTRL) → RF_DISCOVER_MAP → LMRT →
RF_DISCOVER.

---

## 7. Exactly what to edit in core.c / ntf.c

All line numbers are for `core.c.with0113` / `ntf.c.with0113`.

### 7.1 core.c — `nci_rf_discover_req()` (lines 329-346): stop offering NFC-F LISTEN
The block that adds `NCI_NFC_A_PASSIVE_LISTEN_MODE` **and**
`NCI_NFC_F_PASSIVE_LISTEN_MODE` must offer **A only** in the emulation path.
Concretely, delete the second (F-listen) `disc_configs[…]` assignment (lines
342-345). Keep the A-listen one (331-334). This turns RF_DISCOVER from `num=2`
(80,82) into `num=1` (80). **This is the RF_DISCOVER half of killing the FeliCa peer.**

```c
if ((cmd.num_disc_configs < NCI_MAX_NUM_RF_CONFIGS) &&
    (param->tm_protocols & NFC_PROTO_NFC_DEP_MASK)) {
        cmd.disc_configs[cmd.num_disc_configs].rf_tech_and_mode =
                NCI_NFC_A_PASSIVE_LISTEN_MODE;
        cmd.disc_configs[cmd.num_disc_configs].frequency = 1;
        cmd.num_disc_configs++;
        /* DO NOT offer NFC-F LISTEN: on the S3FWRN5 the NFC-F listen slot
         * *is* the autonomous FeliCa/Type-3 responder; offering it makes the
         * firmware answer the reader as FeliCa (0 host NTF). NFC-A listen is
         * armed by the eSE technology route (LMRT) / host LA_* identity. */
}
```

### 7.2 core.c — `nci_rf_set_listen_mode_routing_req()` (lines 930-1036): drop entries 4 & 5
Delete the two blocks that add **NFC-F technology → host** (1013-1020) and
**NFC-DEP protocol → host** (1025-1032). Change the eSE entries' power state from
`NCI_LMRT_POWER_STATE_RF`/`ALL` to `0x3B` (the vendor `OFFHOST_AID_ROUTE_PWR_STATE`)
for entries 1-3. Result: 3-entry table `A-tech→0x83@0x3B`, `MIFARE(0x80)→0x83@0x3B`,
`ISO-DEP(0x04)→0x83@0x3B` (plen 17). Define `#define NCI_LMRT_POWER_STATE_OFFHOST 0x3B`
or reuse the conf-derived value.

### 7.3 core.c — `nci_start_poll()` emulation branch (lines 1076-1170)
- Keep the eSE-vs-host `LA_*` split (1100-1110) as-is (correct).
- Keep `TOTAL_DURATION` (1117) and `LF_PROTOCOL_TYPE=0` (1137-1140) — but note
  `LF_PROTOCOL_TYPE=0` is now only belt-and-suspenders since NFC-F is no longer
  offered. **Remove the `keep_dep_peer` gate** (line 1137): always send it (or drop
  the module param entirely — it was an experiment and the "keep peer" mode is the
  bug that produced the FeliCa symptom).
- After `NFCEE_MODE_SET` (1161-1164) and before the LMRT (1169), add an optional
  `NFCEE_POWER_AND_LINK_CTRL(0x83, 0x03)` (see §4), guarded so a reject is
  non-fatal. New helper:
  ```c
  /* NCI 2.0 NFCEE mgmt: GID 0x2 OID 0x3 -> opcode 0x203 (first byte 0x20). */
  #define NCI_OP_NFCEE_POWER_AND_LINK_CTRL_CMD nci_opcode_pack(NCI_GID_NFCEE_MGMT, 0x03)
  ```
  and send `{nfcee_id=0x83, pli=0x03}` with `nci_send_cmd(...,2,...)`. Ignore a
  non-OK RSP.

### 7.4 ntf.c — surface the listen activation & NFCEE action (lines 1081, 331)
- `NCI_OP_RF_NFCEE_ACTION_NTF` (line 1081) is currently a bare `break;`. Add a
  `pr_info` dumping the NFCEE id + action + trigger so you can *see* whether the CLF
  actually hands the NFC-A field to 0x83 when the reader appears (this NTF, GID 0x1
  OID 0x9, is the direct evidence that routing to the eSE fired). This is the single
  most useful diagnostic to confirm the fix.
- In `nci_rf_intf_activated_ntf_packet` (the T3T branch near line 331), when a
  **listen** activation arrives as tech 0x80 (NFC-A LISTEN) / proto MIFARE(0x80) or
  T2T, make sure it is not dropped by the existing `error when signaling tm
  activation` path (that error in `reader_dmesg.log:79` is for the DEP peer;
  with A-listen it should route to the tag/host CE handler). Verify the switch
  covers `activation_rf_tech_and_mode == 0x80`.

### 7.5 Header additions (nci.h / nci_core.h)
Ensure these tag IDs / opcodes exist (add if missing):
```
NCI_LA_BIT_FRAME_SDD    0x30
NCI_LA_PLATFORM_CONFIG  0x31
NCI_LA_SEL_INFO         0x34   /* NCI_LA_NFCID1 already 0x33 */
NCI_LF_PROTOCOL_TYPE    0x50
NCI_TOTAL_DURATION      0x00
NCI_GID_NFCEE_MGMT      0x02
NCI_LMRT_POWER_STATE_OFFHOST 0x3B
```

---

## 8. What to expect, and the residual risk

- **Expected**: with NFC-F removed from both RF_DISCOVER and the LMRT, the firmware
  has no NFC-F listen slot, so it cannot bring up its autonomous FeliCa/Type-3 peer.
  With NFC-A LISTEN offered and NFC-A technology routed to an *enabled* eSE (0x83,
  power 0x3B) — optionally kept alive by NFCEE_POWER_AND_LINK_CTRL — the reader's
  NFC-A field is handed to the eSE, which answers the SUBE MIFARE with its own
  ATQA/SAK/UID and runs Crypto1. Confirmation signal: an `RF_NFCEE_ACTION_NTF`
  (0x1/0x9) naming 0x83 (§7.4), and the reader reading the MIFARE UID instead of a
  FeliCa IDm.
- **Residual UNKNOWN** (the wall the prior RE hit): this firmware may only arm the
  NFC-A *listen* analog path when a specific **NFC-A-listen RF register profile** is
  loaded via `2F 2A` at init (`hal_vs_rfreg_update_dual_option`). The default
  `sec_s3nrn4v_hwreg.bin`/`swreg.bin` may be reader/NFC-F-listen tuned. If, after
  §6/§7, RF_DISCOVER for A-only still yields no activation and no
  RF_NFCEE_ACTION_NTF, the missing piece is in the `.bin` RF blobs, not in the NCI
  layer — it would need a live capture of the exact `2F 2A` stream Android sends
  while the SUBE is being read, or a diff of the rfreg blobs for a listen-A profile.
  This is **UNKNOWN** and not resolvable from `nfc_nci_sec.so` alone (the blobs are
  separate files and the firmware body is packed, §3.2).

---

## Evidence index
- `live_evidence.txt:16-19` — the LF_PROTOCOL_TYPE A/B (RF-vs-FeliCa coupling).
- `keepdep2_dmesg.log` == `felica_dmesg.log` (diff: only timestamps) — eSE run:
  1×SET_CONFIG(plen5)=TOTAL_DURATION, MAP plen4, LMRT plen27, DISCOVER plen5, 0 NTF.
- `reader_dmesg.log:1-40` — 5×SET_CONFIG(plen 4,4,10,4,5)=the four LA_* + TOTAL_DURATION;
  `:66-77` activations as tech 0x82 / proto 0x5 / intf 0x1 (host DEP peer).
- `ese_dmesg.log:162,181,184,193,202,212` — init MAP plen19, 2×SET_CONFIG, MAP plen4,
  LMRT plen17 (3 entries), DISCOVER plen5; init 2F2A rfreg + NFCEE_DISCOVER (83/15).
- `nfc_nci_sec.so`: prop opcodes @0x10178(2F17),0x10604(2F28),0x16368(2F22),
  0x165cc(2F2A); `NFA_CORE_SET_CFG`@0x5db7, `FW_CFG_CLK_SPEED`@0x6a77,
  `NFA_PROPRIETARY_CFG`@0x5223; `hal_nci_send_clearLmrt` const `21 01 02 00 00`.
- `sec_s3nrn4v_firmware.bin`: build tag `202112140956`@0x0, section-offset header,
  high-entropy packed body (not statically reversible).
- `core.c.with0113`: RF_DISCOVER A+F @329-346; LMRT 5 entries @930-1036;
  emulation SET_CONFIG @1076-1170; eSE LA_* skip @1100; LF_PROTOCOL_TYPE gate @1137.
- `ntf.c.with0113`: NFCEE_DISCOVER_NTF parse @974-1016 (learns 0x83/enabled);
  RF_NFCEE_ACTION_NTF bare break @1081.
- `libnfc-nci.conf:87` OFFHOST_AID_ROUTE_PWR_STATE=0x3B; `:90` LEGACY_MIFARE_READER=1.
- `libnfc-sec-vendor.conf:9-15` DEFAULT_*ROUTE=0x83, NFA_PROPRIETARY_CFG idx5=0x80.
</content>
</invoke>
