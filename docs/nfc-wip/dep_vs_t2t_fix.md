# S3FWRN5 card emulation: NFC-DEP -> NFC-A/T2T, and the "error when signaling tm activation" fix

Target: mainline 7.2-rc5 + rhodep port. Path under test: `emulate_host=1` (host
isolation, no eSE). Symptom (reader_result.txt / reader_dmesg.log):

    RF_INTF_ACTIVATED_NTF: activation_rf_tech_and_mode 0x82 (NFC-F LISTEN),
                           rf_protocol 0x5 (NFC-DEP), rf_interface 0x1 (FRAME)
    -> "error when signaling tm activation"
    -> RF_DEACTIVATE_NTF reason 0x2, and it repeats.

Goal: activate as **NFC-A / T2T (MIFARE)** and signal `NFC_EVENT_TM_ACTIVATED` to
userspace cleanly.

--------------------------------------------------------------------------------
## 1. Why it activates as NFC-F/NFC-DEP instead of NFC-A/T2T

Two independent reasons, BOTH only present on the `emulate_host=1` (host) path.
Both trace to 0113 gating things behind `nci_emu_ese_id(ndev)`, which returns 0
when `emulate_host=1`.

### (a) LF_PROTOCOL_TYPE=0 is never sent on the host path  [FACT]
In 0113, `nci_start_poll()` sends the NFC-DEP-peer suppression only inside
`if (nci_emu_ese_id(ndev)) { v=0; nci_set_config(LF_PROTOCOL_TYPE, ...); }`.
With `emulate_host=1`, `nci_emu_ese_id()==0`, so the block is skipped. The
firmware default `LF_PROTOCOL_TYPE = NFC_DEP` stays in effect, so when NFC-F
listen is offered the S3FWRN5 arms its **autonomous NFC-DEP (P2P) peer** and
answers the reader as NFC-DEP over the NFC-F carrier.

Wire proof (reader_dmesg.log lines 1-64): only 5 `CORE_SET_CONFIG` (opcode 0x2)
commands are sent before LMRT/RF_DISCOVER; **none carries LF_PROTOCOL_TYPE**
(that would be a 6th SET_CONFIG). Then activation comes up 0x82 / proto 0x5.

### (b) The LMRT routes NFC-F technology + NFC-DEP protocol to the host  [FACT]
The 0111 LMRT builder (`nci_rf_set_listen_mode_routing_req`) added, as a
diagnostic, two extra entries routing **NFC-F technology** and the **NFC-DEP
protocol** to the host (NFCEE 0x00) "so we learn what the chip comes up as."
That is exactly what steers the autonomous NFC-DEP activation to the host and
makes it raise RF_INTF_ACTIVATED_NTF as F/DEP. The LMRT sent on the wire is
`opcode 0x101 plen 27` (reader_dmesg.log line 49-51) = 5 entries, including the
NFC-F/NFC-DEP-to-host pair.

### Fix for (1)
- **(a)** Send `LF_PROTOCOL_TYPE = 0` **unconditionally** in the emulation block
  (remove the `nci_emu_ese_id()` gate). Suppresses the NFC-DEP peer on both the
  host and the eSE path. NFC-F listen is still offered in RF_DISCOVER, so the
  analog front-end still lights (the doc's "chip only wakes with NFC-F also
  offered"), but it no longer presents a P2P peer.
- **(b)** Delete the NFC-F-technology + NFC-DEP-protocol LMRT entries. The LMRT
  now routes only **NFC-A technology + T2T protocol + MIFARE protocol** to the
  host (or eSE). With no DEP peer armed (a) and DEP no longer routed to the host
  (b), the field falls through to the routed **NFC-A / T2T** activation.

Option chosen = (a)+(c): LF_PROTOCOL_TYPE=0 on the host path, plus RF_DISCOVER_MAP
already maps T2T->FRAME/LISTEN (0111) and the LMRT routes NFC-A/T2T to the host.
We deliberately KEEP NFC-F listen offered in RF_DISCOVER (option (b) "NFC-A only"
was already tried by the port and never armed the A listener -- see 0111 commit
notes / nfc.md EXP-2); NFC-F is only used to power the front-end. RF_DISCOVER_MAP
keeps only T2T mapped (MIFARE 0x80 is rejected in DISCOVER_MAP, only routed in
LMRT) -- unchanged, already correct in 0111.

--------------------------------------------------------------------------------
## 2. Why "error when signaling tm activation", and the fix

### Root cause  [FACT]
`net/nfc/nci/ntf.c` listen branch (mainline lines 872-885):

    if (err == NCI_STATUS_OK && ntf.rf_protocol == NCI_RF_PROTOCOL_NFC_DEP) {
        err = nfc_tm_activated(ndev->nfc_dev, NFC_PROTO_NFC_DEP_MASK,
                               NFC_COMM_PASSIVE,
                               ndev->remote_gb, ndev->remote_gb_len);
        ...
    }

The failing activation is `rf_protocol=0x5 (NFC-DEP)` but `rf_interface=0x1
(FRAME)` with `activation_params_len 0` (reader_dmesg.log line 78). Because the
interface is FRAME, ntf.c never called `nci_store_general_bytes_nfc_dep()`
(that only runs for `rf_interface == NCI_RF_INTERFACE_NFC_DEP`, ntf.c:843), so
`ndev->remote_gb` is empty and `remote_gb_len == 0`.

The condition matches on `rf_protocol == NFC_DEP` regardless of interface, so it
calls `nfc_tm_activated(..., gb=remote_gb, gb_len=0)`. Then:

    nfc_tm_activated (core.c:667)
      gb != NULL  -> nfc_set_remote_general_bytes(dev, gb, 0)
        -> nfc_llcp_set_remote_gb (llcp_core.c:657)
             if (gb_len < 3 || gb_len > NFC_MAX_GT_LEN) return -EINVAL;  <== HERE

`nfc_llcp_set_remote_gb` returns `-EINVAL` (gb_len 0 < 3). `nfc_tm_activated`
propagates it; ntf.c prints "error when signaling tm activation". The chip then
deactivates (reason 0x2) and retries.

Note the 0111 T2T branch (`else if rf_protocol == T2T`) is NEVER reached today,
because the chip comes up as NFC-DEP (0x5), so the first `if` wins. Part 1 is a
prerequisite: once it activates as T2T, this branch matters.

### Fix for (2)  [in ntf.c, via 0111]
Rewrite the listen branch to key on BOTH protocol and interface:

1. **Real NFC-DEP** only when `rf_protocol == NFC_DEP && rf_interface ==
   NFC_DEP` (ATR general bytes actually extracted): keep the LLCP path with
   `remote_gb`.
2. **NFC-A tag (card emulation)** when `rf_protocol == T2T || rf_protocol ==
   MIFARE (0x80)` (both arrive over the FRAME interface): call
   `nfc_tm_activated(..., NFC_PROTO_MIFARE_MASK, NFC_COMM_PASSIVE, NULL, 0)`.
   Passing `gb = NULL` skips `nfc_set_remote_general_bytes` entirely -> no
   `-EINVAL`, `nfc_genl_tm_activated()` fires `NFC_EVENT_TM_ACTIVATED` and
   `rf_mode` becomes `NFC_RF_TARGET`.
3. **NFC-DEP over a non-NFC-DEP interface** (the stray FRAME/empty case): do
   NOT touch the LLCP path; `pr_debug` and let the stack deactivate. This makes
   the pathological case harmless even if it ever recurs.

This is exactly "Cut 1" of nfc.md "Step 2: the kernel data path (net/nfc has no
HCE)": the listen branch only ever wired NFC-DEP; we extend it so T2T/MIFARE
listen signals `nfc_tm_activated(..., NULL, 0)` with `NFC_PROTO_MIFARE_MASK`.
(Cuts 2-4 -- RX routing by rf_mode, `nfc_tm_data_received` de-LLCP-ing, and a
passive target raw socket -- are follow-up work needed to actually ANSWER a
reader's READ; they are out of scope for "activate + signal to userspace".)

--------------------------------------------------------------------------------
## 3. The edited .patch files (verified against the real tree)

Base for verification: mainline `net/nfc/nci/ntf.c` + `include/net/nfc/nci.h`
with 0111 applied; `net/nfc/nci/core.c` = `core.c.with0112` (0001..0112).
Applied in APKBUILD order: 0111 -> 0112 -> 0113. `patch -p1` exit 0, no fuzz.

### 0111-...-set-a-fixed-nfcid1-for-listen.patch  (ntf.c hunk rewritten)
Hunk header changed `@@ -882,6 +882,14 @@` -> `@@ -873,15 +873,47 @@`. The listen
branch now:
- gates the NFC-DEP/LLCP call on `rf_interface == NCI_RF_INTERFACE_NFC_DEP`;
- adds a T2T/MIFARE branch calling `nfc_tm_activated(..., NFC_PROTO_MIFARE_MASK,
  NFC_COMM_PASSIVE, NULL, 0)`;
- adds a defensive branch that ignores NFC-DEP-over-non-DEP-interface.
(All other 0111 hunks unchanged.)

### 0113-...-route-mifare-listen-to-ese.patch  (two core.c changes)
- LF_PROTOCOL_TYPE block: removed the `if (nci_emu_ese_id(ndev))` gate; now sent
  unconditionally. Hunk `@@ -1024,34 +1024,72 @@` -> `@@ -1024,34 +1024,76 @@`
  (+4 added lines, comment expanded).
- LMRT builder: merged the MIFARE-entry hunk with a deletion of the NFC-F-tech +
  NFC-DEP-proto entries. Hunk `@@ -894,14 +901,16 @@` -> `@@ -894,43 +901,21 @@`.
(All other 0113 hunks unchanged.)

Hunk-count self-check: all `@@ old,new @@` headers match the actual context+/-
line counts (validated programmatically). `patch --dry-run` and real apply both
return exit 0 with no failed hunks and no fuzz.

--------------------------------------------------------------------------------
## 4. Test plan -- what to expect on the wire / dmesg if it works

Setup: `modprobe nci emulate_host=1` (or `echo 1 >
/sys/module/nci/parameters/emulate_host`), then a userspace START_POLL with a
7-byte `NFC_ATTR_TARGET_NFCID1` and `NFC_ATTR_TARGET_SEL_RES=0x08` (MIFARE) /
`0x00` (bare NFC-A). Hold a reader (Flipper/phone) to the antenna.

Expect in dmesg (nci debug on):
1. CORE_SET_CONFIG sequence NOW INCLUDES an extra SET_CONFIG for
   **LF_PROTOCOL_TYPE=0** (one more `opcode 0x2` than before, status 0x0).
2. LMRT (`opcode 0x101`) is SHORTER: `plen` drops from 27 (5 entries) to ~17
   (3 entries: NFC-A tech + T2T proto + MIFARE proto), status 0x0.
3. On reader present, `RF_INTF_ACTIVATED_NTF` with:
   - `activation_rf_tech_and_mode 0x80`  (NFC-A LISTEN)  -- not 0x82
   - `rf_protocol 0x4` (T2T)              -- not 0x5 (NFC-DEP)
   - `rf_interface 0x1` (FRAME)
4. **No** "error when signaling tm activation" line.
5. Kernel emits `NFC_EVENT_TM_ACTIVATED` netlink with
   `NFC_ATTR_TM_PROTOCOLS = NFC_PROTO_MIFARE_MASK`; `rf_mode` becomes
   `NFC_RF_TARGET` (verify with a netlink listener, e.g. neard/nfctool monitor
   or a small genl dump).
6. No immediate `RF_DEACTIVATE_NTF reason 0x2` retry loop.

If it still comes up 0x82/0x5: the front-end may need the NFC-F carrier but the
firmware is re-arming DEP despite LF_PROTOCOL_TYPE=0 -- next lever is dropping
NFC-F from RF_DISCOVER entirely (option (b)) or a vendor RF profile
(see nfc.md "the NFC-A listen front-end not engaging at RF" ceiling).

Reader actually ANSWERING (T2T READ) is NOT expected yet: that needs Cuts 2-4
(the data path). This fix delivers activation-as-T2T + clean TM_ACTIVATED only.

--------------------------------------------------------------------------------
## 5. FACT / INFERENCE / UNKNOWN

FACT
- reader_dmesg.log shows repeated activation 0x82 / proto 0x5 / iface 0x1 with
  activation_params_len 0, then "error when signaling tm activation", then
  RF_DEACTIVATE_NTF reason 0x2. (lines 65-101, repeats)
- Only 5 CORE_SET_CONFIG are sent pre-discovery; LF_PROTOCOL_TYPE is absent.
- 0113 gates LF_PROTOCOL_TYPE=0 behind `nci_emu_ese_id(ndev)`, which is 0 when
  `emulate_host=1` (source-verified in the patch).
- 0111 LMRT builder routes NFC-F tech + NFC-DEP proto to the host (0x00).
- `nfc_llcp_set_remote_gb` returns -EINVAL for gb_len < 3 (llcp_core.c:662).
- ntf.c only calls `nci_store_general_bytes_nfc_dep` for rf_interface==NFC_DEP
  (ntf.c:843), so remote_gb is empty for a FRAME activation.
- All edited hunks apply to the real tree with `patch -p1`, exit 0, no fuzz.

INFERENCE
- The autonomous NFC-DEP peer is armed because the firmware default
  LF_PROTOCOL_TYPE (NFC_DEP) is left in place; sending 0 will suppress it (this
  is the documented behaviour the eSE path already relies on, and matches
  nfc.md line 541 "Clearing LF_PROTOCOL_TYPE (0x50 = 0)").
- Removing the NFC-F/NFC-DEP-to-host LMRT entries plus LF_PROTOCOL_TYPE=0 will
  let the routed NFC-A/T2T activation win instead of DEP.
- Once activation is T2T (0x4) over FRAME, the 0111/rewritten T2T branch signals
  `nfc_tm_activated(..., NULL, 0)` which returns 0 -> TM_ACTIVATED fires.

UNKNOWN
- Whether the S3FWRN5 firmware, with LF_PROTOCOL_TYPE=0 and DEP unrouted, will
  actually raise an NFC-A/T2T RF_INTF_ACTIVATED_NTF, or whether the NFC-A listen
  front-end still fails to engage at RF (the vendor-RF-profile ceiling noted in
  0113's commit message and nfc.md ~line 587). Requires on-hardware retest.
- Whether SEL_RES 0x08 (MIFARE) yields rf_protocol 0x80 (MIFARE) or 0x4 (T2T)
  from this firmware in the host path (ntf.c handles both).
- Whether the reader will read anything -- needs Cuts 2-4 (data path), not in
  this fix.
