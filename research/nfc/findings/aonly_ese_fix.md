# A-only / eSE FeliCa-suppression fix — patches 0111/0113 (rhodep, S3FWRN5)

Fix derived from `felica_suppress_re.md` (the source of truth). Goal: emulate the
MIFARE card in the eSE (NFCEE 0x83) and stop the CLF's autonomous FeliCa / Type-3
responder by **removing NFC-F from the RF_DISCOVER offer and from the LMRT**, arming
NFC-A LISTEN routed to the eSE instead.

Target: mainline linux 7.2-rc5, driver S3FWRN5, Moto G82 (rhodep). Path: eSE
(`emulate_host=0`, `ese_nfcee_id` valid).

---

## 1. Where the changes live

All five behavioural changes are in **0113** (the function bodies it already
touches). **0111 needs no change** — it only defines the `nci_lmrt_{tech,proto}_entry`
structs and adds opcodes/tags; the struct field order (`type,len,nfcee_id,
power_state,value`) is already what we need. 0111 was left byte-identical to the
original and re-validated to still apply.

Files touched by the new 0113: `include/net/nfc/nci.h`, `include/net/nfc/nci_core.h`
(unchanged hunk kept), `net/nfc/nci/ntf.c`, `net/nfc/nci/core.c`.

---

## 2. Conceptual diff of each change

### (1) RF_DISCOVER = NFC-A LISTEN only  — `nci_rf_discover_req()`
Removed the second `disc_configs[]` assignment that offered
`NCI_NFC_F_PASSIVE_LISTEN_MODE`. The listen block now adds only NFC-A passive
listen (0x80). The guard bound also changed from `NCI_MAX_NUM_RF_CONFIGS - 1`
back to `NCI_MAX_NUM_RF_CONFIGS` (only one entry is added now).

Before: A-listen (0x80) + F-listen (0x82), num=2, plen 5.
After:  A-listen (0x80) only, num=1, plen 3.

Rationale (RE §3.3/§5.C): the NFC-F listen slot *is* the autonomous FeliCa
responder; offering it makes the firmware answer the reader as FeliCa. It cannot
be neutralised with LF_PROTOCOL_TYPE/LF_T3T_FLAGS (RF and peer are coupled), so it
must not be offered.

### (2) LMRT = 3 entries, all to eSE @ 0x3B  — `nci_rf_set_listen_mode_routing_req()`
Deleted entry 4 (NFC-F technology -> host) and entry 5 (NFC-DEP protocol -> host).
Entries 1-3 already used `NCI_LMRT_POWER_STATE_ALL`, which patch 0111 defines as
**0x3b** (the vendor `OFFHOST_AID_ROUTE_PWR_STATE`), so no power-state edit was
needed. `nci_send_cmd(... 2 + (p - cmd.entries) ...)` now yields plen 17 with
num_entries=3 automatically.

Remaining table:
  1) NFC-A technology  -> eSE 0x83 @ 0x3B
  2) MIFARE   (0x80)   -> eSE 0x83 @ 0x3B
  3) ISO-DEP  (0x04)   -> eSE 0x83 @ 0x3B

### (3) LF_PROTOCOL_TYPE = 0 always  — `nci_start_poll()` listen branch
Removed the `keep_dep_peer` gate; the SET_CONFIG(LF_PROTOCOL_TYPE=0) is now sent
unconditionally on the listen path (belt-and-suspenders). The now-unused
`keep_dep_peer` module param definition was removed to avoid a build warning.
`LF_T3T_FLAGS=0` is intentionally NOT sent (chip rejects it, status 0x9).

### (4) NFCEE_POWER_AND_LINK_CTRL  — after NFCEE_MODE_SET(0x83), before LMRT
Added the opcode/struct to `nci.h`:
  `NCI_OP_NFCEE_POWER_AND_LINK_CTRL_CMD = nci_opcode_pack(NCI_GID_NFCEE_MGMT,0x03)`
  (GID 0x2, OID 0x3 -> opcode 0x203, first on-wire byte 0x20)
  `struct nci_nfcee_power_and_link_ctrl_cmd { __u8 nfcee_id; __u8 pli; }`
  `NCI_NFCEE_PWR_LINK_ALWAYS_ON = 0x03`
Sent only on the eSE path (`nci_emu_ese_id(ndev)` non-zero) with payload
`{nfcee_id=0x83, pli=0x03}` via `nci_send_cmd(...,2,...)` (fire-and-forget; a
non-OK RSP is harmless, MIFARE routing is a pure LMRT operation).

### (5) RF_NFCEE_ACTION_NTF log  — `ntf.c` (`NCI_OP_RF_NFCEE_ACTION_NTF`)
The bare `break;` now `pr_info`s the NFCEE id + trigger. This NTF (GID 0x1 OID 0x9)
is the direct evidence that the CLF handed the reader's NFC-A field to the eSE
0x83, i.e. the MIFARE listen fired.

---

## 3. Expected bytes on the air

### RF_DISCOVER (opcode 0x103)
```
21 03 03 01 80 01
│  │  │  │  │  └ frequency 1
│  │  │  │  └─── NFC-A passive listen 0x80
│  │  │  └────── num_configs = 1
│  │  └───────── plen 3
│  └──────────── OID 0x03
└─────────────── GID 0x21 (RF mgmt, MT=cmd)
```
(was `21 03 05 02 80 01 82 01` = A+F, num=2, plen 5)

### RF_SET_LISTEN_MODE_ROUTING / LMRT (opcode 0x101)
**Actual wire bytes** (verified by compiling the struct layout):
```
21 01 11 00 03 00 03 83 3B 00 01 03 83 3B 80 01 03 83 3B 04
│  │  │  │  │  └───────────── entry1 tech-A:  type00 len03 nfcee83 pwr3B tech00
│  │  │  │  └ num_entries = 3
│  │  │  └─── more = 0
│  │  └────── plen 0x11 = 17
│  └───────── OID 0x01
└──────────── GID 0x21
   entry2 MIFARE:  01 03 83 3B 80
   entry3 ISO-DEP: 01 03 83 3B 04
```
**IMPORTANT — field order:** `struct nci_lmrt_{tech,proto}_entry` (from 0111) is
`type,len,nfcee_id,power_state,value`, so on the wire **nfcee (0x83) comes BEFORE
power (0x3B)** -> `83 3B`. The RE/task tabulated these two octets power-first
(`3B 83`) as a documentation convention only; the RE itself and the task both
flag that the struct is authoritative. The values that matter are identical:
nfcee 0x83, power 0x3B, tech/proto {00,80,04}. plen 0x11 and num_entries 3 match.

### NFCEE_POWER_AND_LINK_CTRL (opcode 0x203)
```
20 03 02 83 03
│  │  │  │  └ PLI 0x03 (keep NFCEE powered AND NFCC<->eSE link active)
│  │  │  └─── NFCEE id 0x83
│  │  └────── plen 2
│  └───────── OID 0x03
└──────────── GID 0x20 (NFCEE mgmt, MT=cmd) — NOT 0x21
```

---

## 4. Patch validation (as required)

Reconstructed a clean tree
(`tar xzf .../linux-motorola-rhodep-7.2_rc5.tar.gz`), applied 0001–0112 in the
`source=` order from the pmaports APKBUILD with `patch -p1`, then dry-ran both
edited patches in order (0111 before 0113):

```
########## 0111 DRY-RUN ##########   -> rc=0
########## 0113 DRY-RUN ##########   -> rc=0
=== fuzz/offset/FAILED across both dry-runs ===  NONE
```

- `patch -p1 --dry-run` for **0111** = **0 FAILED, no fuzz, no offset**.
- `patch -p1 --dry-run` for **0113** = **0 FAILED, no fuzz, no offset**.
  (Pre-existing fuzz/offset noted while applying unrelated DTS patches 0001–0110
  are in *those* patches, not in 0111/0113, and do not affect the NFC files —
  core.c/ntf.c/nci.h applied by 0110/0111/0112 with clean offsets.)

### Compile check
With the shipped `config-motorola-rhodep.aarch64` (`CONFIG_NFC_NCI=m`) and the
gcc aarch64 cross toolchain, `make ... net/nfc/nci/core.o net/nfc/nci/ntf.o
net/nfc/nci/rsp.o` built **cleanly: 0 warnings, 0 errors** after 0111+0112+0113.
(LLVM/lld path used by the APKBUILD was unavailable in this sandbox, so gcc was
used only for the object-level syntax/semantic check; no repo/pmbootstrap state
was modified.)

---

## 5. FACT / INFERENCE / UNKNOWN

- **FACT** — The five changes map 1:1 to `felica_suppress_re.md` §7.1–§7.4 and §5.
- **FACT** — LMRT wire field order is nfcee-before-power (struct in 0111,
  confirmed by compiling the exact struct: `21 01 11 00 03 00 03 83 3B 00 ...`).
- **FACT** — `NCI_LMRT_POWER_STATE_ALL` == 0x3b in 0111's nci.h, so entries 1–3
  are already at 0x3B; no power edit was required.
- **FACT** — Both patches apply with 0 FAILED / no fuzz / no offset on a clean
  0001–0112 tree; the three NFC objects compile with 0 warnings/errors.
- **FACT** — NFCEE_POWER_AND_LINK_CTRL opcode first byte is 0x20 (GID 0x2), per
  NCI 2.0 (the RE corrected the earlier `0x21` typo).
- **INFERENCE** — Removing NFC-F from RF_DISCOVER + LMRT suppresses the FeliCa
  peer and lets NFC-A LISTEN route to the eSE (RE §3.3, strong, from the live
  A/B). Sending LF_PROTOCOL_TYPE=0 is harmless belt-and-suspenders once NFC-F is
  gone.
- **UNKNOWN** — Whether the front-end actually lights for A-only on this firmware
  may still depend on an NFC-A-listen RF register profile loaded via `2F 2A` at
  init (RE §8). If, after this fix, RF_DISCOVER for A-only yields no activation
  and no RF_NFCEE_ACTION_NTF, the missing piece is in the `.bin` RF blobs, not in
  the NCI layer, and is not resolvable from the NCI code. The added
  RF_NFCEE_ACTION_NTF log is the diagnostic to confirm which case occurred.

---

## 6. Deliverables
- `/tmp/nfcre6/0111-nfc-nci-set-a-fixed-nfcid1-for-listen.patch` (unchanged, re-validated)
- `/tmp/nfcre6/0113-nfc-route-mifare-listen-to-ese.patch` (corrected)
- `/tmp/nfcre6/aonly_ese_fix.md` (this file)

No git repo, pmbootstrap state, or `.tgz` was modified.
