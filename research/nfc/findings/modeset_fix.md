# 0113 fix — NFCEE_MODE_SET(eSE 0x83, ENABLE) made unconditional

Source of truth: `rfreg_listen_re.md` §6.2 (and §6). Fix applied to
`/tmp/nfcre8/0113-nfc-route-mifare-listen-to-ese.patch`.

## The bug (FACT — from aonly_dmesg.log)
To emulate a MIFARE card on the eSE (NFCEE 0x83) via card emulation, the eSE must
be ENABLED before routing/discover. In the live capture the flow sends
`NFCEE_DISCOVER` (0x200), receives 2× `NFCEE_DISCOVER_NTF` (GID=0x2 OID=0x0), then
jumps straight to `NFCEE_POWER_AND_LINK_CTRL` (GID=0x2 OID=0x3). **`NFCEE_MODE_SET`
(opcode 0x2201, wire `22 01 02 83 01`) is NEVER sent** — `grep -c 0x22 aonly_dmesg.log`
= 0. Result: eSE never ENABLED, the NFC-A route to 0x83 never fires
(0 RF_NFCEE_ACTION_NTF, 0 activations).

Cause (FACT): the pre-fix 0113 gated MODE_SET on
`nci_emu_ese_id(ndev) && ndev->ese_enabled != NCI_NFCEE_ENABLE`. `ese_enabled` is
read from `skb->data[1]` of the `NFCEE_DISCOVER_NTF` and comes up 0x01
(CONNECTED/ENABLED), so the `!= NCI_NFCEE_ENABLE` half is false and MODE_SET is
skipped entirely.

## The fix (conceptual diff)
In `net/nfc/nci/core.c`, `nci_start_poll()` listen path (`emu_nfcid1_len` branch),
after `RF_DISCOVER_MAP` and before `NFCEE_POWER_AND_LINK_CTRL`:

- **MODE_SET is now UNCONDITIONAL on the eSE path.** The gate is only
  `if (nci_emu_ese_id(ndev))` (i.e. an eSE id exists and `emulate_host=0`).
  The `&& ndev->ese_enabled != NCI_NFCEE_ENABLE` clause was removed.
- **Tolerant to status 0x3 (REJECTED = already enabled).** The call return value
  was already ignored (bare statement → the sequence never aborted on a non-OK
  RSP). The fix now captures it and `pr_info(...)`s "continuing" on any non-zero
  rc, so a 0x3 is logged and the sequence proceeds to POWER_AND_LINK / LMRT /
  DISCOVER regardless. Not fatal.
- `ese_enabled` is **kept** (still populated in ntf.c for logging) but no longer
  gates MODE_SET.

Before (pre-fix 0113):
```c
if (nci_emu_ese_id(ndev) &&
    ndev->ese_enabled != NCI_NFCEE_ENABLE)
        nci_nfcee_mode_set(ndev, nci_emu_ese_id(ndev),
                           NCI_NFCEE_ENABLE);
```
After (fixed):
```c
if (nci_emu_ese_id(ndev)) {
        int mrc = nci_nfcee_mode_set(ndev,
                                     nci_emu_ese_id(ndev),
                                     NCI_NFCEE_ENABLE);
        if (mrc)
                pr_info("NFCEE_MODE_SET(0x%x, ENABLE) rc %d (e.g. status 0x3 REJECTED if already enabled) -- continuing\n",
                        nci_emu_ese_id(ndev), mrc);
}
```

Only the hunk `@@ -1050,6 +1108,44 @@` (the MODE_SET block) was changed; its header
was recomputed to `@@ -1050,6 +1108,51 @@` (old side unchanged: ctx 6 + del 0 = 6;
new side: ctx 6 + add 45 = 51). No other hunk was touched. POWER_AND_LINK_CTRL,
LMRT, RF_DISCOVER blocks are unchanged.

## Order (verified in the applied core.c)
NFCEE_DISCOVER (done earlier at open) →
**RF_DISCOVER_MAP** (`nci_rf_discover_map_listen_req`, core.c:1108) →
**NFCEE_MODE_SET(ENABLE)** (unconditional, core.c:1127) →
**NFCEE_POWER_AND_LINK_CTRL** (core.c:1146) →
**LMRT** (`nci_rf_set_listen_mode_routing_req`, core.c:1159) →
**RF_DISCOVER** (`nci_rf_discover_req`, core.c:1169).

Matches the required DISCOVER → MODE_SET → POWER_AND_LINK → MAP → LMRT → DISCOVER
(RF_DISCOVER_MAP precedes MODE_SET in this driver's listen window, which is the
canonical NCI CORE_SET_CONFIG→MAP→LMRT→DISCOVER order; MODE_SET/POWER_AND_LINK are
inserted between MAP and LMRT — eSE is ENABLED before LMRT and before RF_DISCOVER,
which is what matters).

## Expected wire bytes
- `NFCEE_MODE_SET` = **`22 01 02 83 01`**
  - byte0 `0x22` = MT_CMD(0x20) | GID NFCEE_MGMT(0x2)
  - byte1 `0x01` = OID MODE_SET
  - byte2 `0x02` = plen = sizeof(struct nci_nfcee_mode_set_cmd) (nfcee_id+mode)
  - byte3 `0x83` = nfcee_id (eSE)
  - byte4 `0x01` = NCI_NFCEE_ENABLE
  Confirmed against nci.h (`NCI_OP_NFCEE_MODE_SET_CMD = pack(0x2,0x1)`,
  `NCI_NFCEE_ENABLE=0x01`) and `nci_send_cmd` header serialization.
- `NFCEE_POWER_AND_LINK_CTRL` = `20 03 02 83 03` (unchanged by this fix).

## Validation (FACT)
Method per task: extracted `linux-motorola-rhodep-7.2_rc5.tar.gz` to a scratch dir
(`/tmp/opencode/nfcval`, NOT the repo), applied the 95 patches preceding 0113 in
the exact order of `source=` in the pmaports APKBUILD with `patch -p1` (0113 is
entry #96). All applied with 0 FAILED (offsets/fuzz only in unrelated DTS/other
subsystems, present in the baseline tree, none in the NFC files). The resulting
`net/nfc/nci/core.c` and `net/nfc/nci/ntf.c` are byte-identical to the provided
`core.c.with0112` / `ntf.c.with0112`.

Then:
```
patch -p1 --dry-run < 0113-nfc-route-mifare-listen-to-ese.patch
  checking file include/net/nfc/nci.h
  checking file include/net/nfc/nci_core.h
  checking file net/nfc/nci/ntf.c
  checking file net/nfc/nci/core.c
  rc=0
  FAILED count: 0
  fuzz/offset count: 0
```
**0 FAILED, 0 fuzz, 0 offset, rc=0.** A real (non-dry) apply also succeeds
(rc=0), and a reverse-then-clean-dry-run re-confirms 0/0/0.

## FACT / INFERENCE / UNKNOWN
- **FACT** — MODE_SET (`22 01`) is absent in aonly_dmesg.log; the pre-fix 0113
  gated it out via `ese_enabled != NCI_NFCEE_ENABLE`; `ese_enabled` is read from
  `NFCEE_DISCOVER_NTF` `skb->data[1]`.
- **FACT** — the fixed patch applies cleanly (0 FAILED / 0 fuzz / 0 offset) on the
  real 0001–0112 tree; wire bytes `22 01 02 83 01`; order MODE_SET before
  POWER_AND_LINK before LMRT before RF_DISCOVER.
- **FACT** — the call already never aborted the sequence (return ignored); the fix
  makes tolerance explicit with a log line.
- **INFERENCE** — enabling the eSE (unconditional MODE_SET) is what lets the NFC-A
  route to 0x83 fire and produce RF_NFCEE_ACTION_NTF / an activation (per RE §6.2).
- **UNKNOWN** — whether the S3FWRN5 actually answers MODE_SET on the
  already-enabled 0x83 with status 0x03 vs 0x00 at runtime (handled either way by
  the tolerance); and the §6.1 clock-TLV gap (2F28) which is a separate, still-open
  candidate root cause and is NOT addressed by this patch.
