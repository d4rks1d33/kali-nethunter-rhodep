# SM6375 interconnect driver — COLD AUDIT

Cross-check of `sm6375.c` against mainline `sm6115.c` + `qcm2290.c` (same SMD-RPM
NoC family) and vendor `holi.c`. No build/flash. Goal: rank the cause of the
very-early SoC reset now that IPA and QoS-register writes have been ruled out.

Line numbers below refer to `_common/interconnect-sm6375-wip/sm6375.c`.

---

## TOP SUSPECTS (ranked)

### #1  `qxm_ipa.mas_rpm_id = 59` votes a bogus RPM master req on SM6375  (sm6375.c:679)
- Our value: `mas_rpm_id = 59` (from generator ICBID map, copied blindly from qcm2290/sm6115).
- Mainline: sm6115 `qxm_ipa` = 59, qcm2290 `mas_ipa` = 59 — **but on those SoCs the
  RPM firmware actually implements master id 59 (ICBID_MASTER_IPA).**
- Why this is the #1 hang candidate: with `keep_alive=true` and IPA still probed as a
  node (even with the IPA *device* disabled, the interconnect *node* qxm_ipa is still
  registered on sys_noc), `qcom_icc_set()` → `qcom_icc_rpm_set()` sends
  `RPM_BUS_MASTER_REQ` for id 59 during the very first aggregate. If SM6375's RPM does
  not implement that master resource, the SMD-RPM transaction can wedge/timeout and
  reset the SoC before any log survives — exactly the observed symptom. The vendor
  holi.c sets `qxm_ipa.mas_rpm_id = ICBID_MASTER_IPA`; the generator resolved that via
  `/tmp/icbid_map.txt`, i.e. a GUESS that ICBID==mainline-RPM-id. This is the single
  most likely "early RPM vote wedges SoC" path and it is on the IPA node — consistent
  with the original motivation but NOT ruled out by "IPA device disabled" (the node
  still exists in sys_noc_nodes, sm6375.c:1540).

### #2  `mas_snoc_bimc.qos_port = 6` but SM6375/holi hardware port is 7  (sm6375.c:117)
- Our value: `qos.qos_port = 6` (came from the QCM_ALIAS path: qcm2290/sm6115
  `mas_snoc_bimc` use port 6).
- Vendor holi.c: `mas_snoc_bimc_qos.offsets = { 0x24300 }`. Using the driver's own
  BIMC formula `(0x24300-0x8300)/0x4000 = 7`. So the **real SM6375 BIMC port is 7, not 6.**
- Why it can hang: this is a BIMC node. Even with `qos.ap_owned` forced false in the
  v50 diag (which skips `qcom_icc_qos_set`), this port is still wrong for any future
  QoS re-enable, and more importantly it signals the generator's QCM_ALIAS override
  diverged from the true holi offset. NOTE: because v50 disabled QoS writes and still
  hung, a wrong *port alone* is not the active cause — but it is a real latent bug and
  must be fixed to 7 before re-enabling QoS. Kept high because it proves the
  offset→port derivation for shared BIMC nodes was overridden by qcm2290 values that
  do NOT match holi (apps_proc 0x8300→0, rt 0x10300→2, nrt 0x14300→3, snoc_bimc
  0x24300→**7**; driver has 0/2/3/**6**).

### #3  `qhm_qup1.mas_rpm_id = 41` is almost certainly wrong (collides with a slave id)  (sm6375.c:546)
- Our value: `mas_rpm_id = 41`.
- Vendor holi.c: `qhm_qup1.mas_rpm_id = ICBID_MASTER_QUP_1`. There is **no QUP_1 master
  node in either sm6115.c or qcm2290.c** (those SoCs only have QUP_0), so the generator
  had NO mainline value to confirm against and fell through the ICBID map to `41`.
- 41 is a suspicious value: in the mainline SMD-RPM slave-id space 41 = `slv_pdm`
  (qcm2290 slv_pdm.slv_rpm_id = 41). A master req to id 41 on SM6375 may hit an
  unimplemented/wrong resource. This is a GUESS-path id on a `keep_alive` bus
  (clk-virt/sys_noc) that gets an early vote. Fix requires a verified SM6375 RPM master
  id for QUP_1 (downstream rpm-ids.h), not the ICBID fallback.

> Summary of the mechanism for #1/#3: `keep_alive=true` (sm6375.c:1603/1615/1649 etc.)
> forces `active_rate = max(19200, …)` and an unconditional first `qcom_icc_set()`
> pass. Every registered node with `mas_rpm_id != -1` / `slv_rpm_id != -1` and
> `ap_owned=false` emits a real `qcom_icc_rpm_smd_send()`. Any id the SM6375 RPM does
> not implement → failed/hung SMD-RPM handshake → early reset with no ramoops.
> The generator produced several of these ids from an **ICBID→number guess**, not from
> verified mainline values (see section 6). Those are the prime bus-level suspects.

---

## Bus config comparison

| bus | field | our sm6375.c | sm6115.c | qcm2290.c | verdict |
|-----|-------|--------------|----------|-----------|---------|
| bimc | type | QCOM_ICC_BIMC | QCOM_ICC_BIMC | QCOM_ICC_BIMC | MATCH |
| bimc | bus_clk_desc | `&bimc_clk` | `&bimc_clk` | `&bimc_clk` | MATCH |
| bimc | regmap max_register | 0x80000 | 0x80000 | 0x80000 | MATCH |
| bimc | reg (DT) | 0x04480000 / 0x80000 | 0x04480000 / 0x80000 | — | MATCH (== sm6115.dtsi) |
| bimc | qos_offset | 0x8000 | 0x8000 | 0x8000 | MATCH |
| bimc | ab_coeff | 153 | 153 | 153 | MATCH |
| bimc | keep_alive | true | true | true | MATCH |
| sys_noc | type | QCOM_ICC_QNOC | QCOM_ICC_QNOC | QCOM_ICC_QNOC | MATCH |
| sys_noc | bus_clk_desc | `&bus_2_clk` | `&bus_2_clk` | `&bus_2_clk` | MATCH |
| sys_noc | regmap max_register | 0x5f080 | 0x5f080 | 0x60200 | MATCH vs sm6115 |
| sys_noc | reg (DT) | 0x01880000 / 0x5f080 | 0x01880000 / 0x5f080 | — | MATCH (== sm6115.dtsi) |
| sys_noc | qos_offset | 0x15000 | 0x15000 | 0x15000 | MATCH |
| sys_noc | intf_clocks | cpu_axi/ufs_axi/usb_axi/ipa | same | — | MATCH (see note A) |
| sys_noc | keep_alive | true | true | true | MATCH |
| config_noc | type | QCOM_ICC_QNOC | QCOM_ICC_QNOC | QCOM_ICC_NOC | MATCH vs sm6115 (note B) |
| config_noc | bus_clk_desc | `&bus_1_clk` | `&bus_1_clk` | `&bus_1_clk` | MATCH |
| config_noc | regmap max_register | 0x6200 | 0x6200 | 0x8200 | MATCH vs sm6115 |
| config_noc | reg (DT) | 0x01900000 / 0x6200 | 0x01900000 / 0x6200 | — | MATCH (== sm6115.dtsi) |
| config_noc | qos_offset | 0x15000 | (unset) | (unset) | **MISMATCH (note C)** |
| config_noc | intf_clocks | usb_axi | usb_axi | — | MATCH |
| config_noc | keep_alive | true | true | true | MATCH |
| clk_virt | type | QCOM_ICC_QNOC | QCOM_ICC_QNOC | QCOM_ICC_QNOC | MATCH |
| clk_virt | bus_clk_desc | `&qup_clk` | `&qup_clk` | `&qup_clk` | MATCH |
| clk_virt | regmap | `&sys_noc_regmap_config` | `&sys_noc_regmap_config` | (none) | MATCH vs sm6115 (note D) |
| clk_virt | keep_alive | true | true | true | MATCH |
| mmrt_virt | bus_clk_desc | `&mmaxi_1_clk` | `&mmaxi_1_clk` | `&mmaxi_1_clk` | MATCH |
| mmrt_virt | qos_offset | 0x15000 | 0x15000 | 0x15000 | MATCH |
| mmrt_virt | ab_coeff | 139 | 139 | 139 | MATCH |
| mmnrt_virt | bus_clk_desc | `&mmaxi_0_clk` | `&mmaxi_0_clk` | `&mmaxi_0_clk` | MATCH |
| mmnrt_virt | qos_offset | 0x15000 | 0x15000 | 0x15000 | MATCH |
| mmnrt_virt | ab_coeff | 142 | 142 | 142 | MATCH |

**Note A (intf_clocks "ipa"):** sm6115 wires the "ipa" clock-name to
`&rpmcc RPM_SMD_IPA_CLK` in its DTS (sm6115.dtsi:945). If SM6375's DTS wires "ipa" to a
different/absent clock, `devm_clk_bulk_get` for the sys_noc intf clocks fails, but that
returns an error (probe abort), not a silent early reset. Lower risk than an RPM vote,
but verify the SM6375 sys_noc `clock-names` include a working "ipa" source.

**Note B (config_noc type):** qcm2290 uses `QCOM_ICC_NOC` for cnoc, sm6115 uses
`QCOM_ICC_QNOC`. Our driver follows sm6115 (QNOC). This is internally consistent with
using `qos_offset=0x15000` and the QNOC MCTL register layout. NOT a hang cause, but see
Note C.

**Note C (config_noc qos_offset=0x15000 — MISMATCH):** Neither sm6115 nor qcm2290 set a
`qos_offset` on the cnoc/config_noc desc (it defaults to 0). Our driver sets
`0x15000` (sm6375.c:1614). config_noc has **no ap_owned/QoS nodes** (all cnoc nodes are
plain `.qos` = 0/INVALID), so `qcom_icc_qos_set` is never called and the offset is
currently harmless — but it is a real deviation from both references and should be
dropped to 0 to match mainline. Not the active cause (QoS path unused on cnoc).

**Note D (clk_virt regmap):** clk_virt is a virtual (no-reg) bus. Our desc points
`regmap_cfg = &sys_noc_regmap_config`; sm6115 does the same. In `qnoc_probe`, when the
DT node has no `reg`, `platform_get_resource` returns NULL and the code falls back to
the parent regmap (icc-rpm.c:523-529). This matches sm6115's DT layout where clk_virt is
a *child* of system_noc. **If SM6375's DTS does not nest clk_virt/mmrt/mmnrt under
system_noc (DT-NODES.md shows them as separate top-level nodes), the fallback
`dev_get_regmap(dev->parent)` returns NULL and probe returns -ENODEV** — a probe failure,
not a reset, but it means these providers never come up. Verify DTS nesting.

---

## Full RPM-id comparison table

Legend: role match by meaning across holi/sm6115/qcm2290. "GUESS" = generator produced
the id via the ICBID→number fallback with NO mainline node to confirm it.

| node (ours) | our id | mainline equiv (drv) | mainline id | verdict |
|-------------|--------|----------------------|-------------|---------|
| mas_snoc_bimc (SNOC_BIMC_MAS) `mas_rpm_id` | 3 | mas_snoc_bimc (sm6115 & qcm2290) | 3 | **MATCH** |
| qup0_core_master (MASTER_QUP_CORE_0) `mas` | 170 | qup0_core_master (sm6115/qcm2290) | 170 | **MATCH** |
| qup1_core_master (MASTER_QUP_CORE_1) `mas` | 171 | *(none — SM6375-only)* | — | **SM6375-ONLY / GUESS** |
| mas_a1noc_snoc (A1NOC_SNOC_MAS) `mas` | 111 | *(none; qcm2290 anoc=110)* | 110 (anoc) | **SM6375-ONLY / GUESS** |
| mas_a2noc_snoc (A2NOC_SNOC_MAS) `mas` | 112 | *(none)* | — | **SM6375-ONLY / GUESS** |
| mas_bimc_snoc (BIMC_SNOC_MAS) `mas` | 21 | mas_bimc_snoc (sm6115 & qcm2290) | 21 | **MATCH** |
| qhm_qup0 (MASTER_QUP_0) `mas` | 166 | qhm_qup0/mas_qup_0 (sm6115/qcm2290) | 166 | **MATCH** |
| qhm_qup1 (MASTER_QUP_1) `mas` | 41 | *(none — SM6375-only)* | — | **SM6375-ONLY / GUESS (suspect)** |
| xm_sdc2 (MASTER_SDCC_2) `mas` | 35 | xm_sdc2/mas_sdcc_2 (sm6115/qcm2290) | 35 | **MATCH** |
| mas_crypto_c0 (MASTER_CRYPTO_CORE0) `mas` | 23 | crypto_c0/mas_crypto_core0 | 23 | **MATCH** |
| qxm_ipa (MASTER_IPA) `mas` | 59 | qxm_ipa/mas_ipa (sm6115/qcm2290) | 59 | MATCH string, **RPM-impl unverified (suspect #1)** |
| slv_bimc_snoc (BIMC_SNOC_SLV) `slv` | 2 | slv_bimc_snoc (sm6115 & qcm2290) | 2 | **MATCH** |
| slv_snoc_cnoc (SNOC_CNOC_SLV) `slv` | 25 | slv_snoc_cnoc (sm6115 & qcm2290) | 25 | **MATCH** |
| qxs_imem (SLAVE_OCIMEM) `slv` | 26 | qxs_imem/slv_imem (sm6115/qcm2290) | 26 | **MATCH** |
| slv_snoc_bimc (SNOC_BIMC_SLV) `slv` | 24 | slv_snoc_bimc (sm6115 & qcm2290) | 24 | **MATCH** |
| xs_qdss_stm (SLAVE_QDSS_STM) `slv` | 30 | xs_qdss_stm/slv_qdss_stm | 30 | **MATCH** |
| slv_a1noc_snoc (A1NOC_SNOC_SLV) `slv` | 142 | *(none; qcm2290 anoc=141)* | 141 (anoc) | **SM6375-ONLY / GUESS** |
| slv_a2noc_snoc (A2NOC_SNOC_SLV) `slv` | 143 | *(none)* | — | **SM6375-ONLY / GUESS** |
| ebi (SLAVE_EBI) `slv` | 0 | ebi/slv_ebi1 (sm6115/qcm2290) | 0 | **MATCH** |

All other nodes in sm6375.c have `mas_rpm_id = -1` and `slv_rpm_id = -1` (no RPM
vote) — confirmed by inspection; they cannot cause an RPM-vote hang.

### The A1/A2 NOC ids are the second cluster of concern
SM6375/holi splits the aggregators into A1NOC + A2NOC (ids 111/112 master, 142/143
slave). qcm2290 has a single ANOC (110 master / 141 slave); sm6115 has none of these
(it uses ANOC_SNOC without exposed rpm ids the same way). So **111, 112, 142, 143 all
came from the ICBID guess** and are each a candidate for an unimplemented SM6375 RPM
resource. They sit on `keep_alive` sys_noc and get an early vote. Rank them just below
qxm_ipa and qhm_qup1.

---

## Section 6 — nodes whose RPM id came from the GENERATOR GUESS path

`gen_sm6375.py:16-21` (`rpm()`): if the vendor value is an `ICBID_*` symbol it is
replaced by `int(icbid_map[...])`. There is **no cross-check against a verified SM6375
RPM id list** — the ICBID number is assumed to equal the mainline SMD-RPM id. Nodes that
were resolved this way AND have no confirming mainline node:

- `qup1_core_master` mas=171  (ICBID_MASTER_QUP_CORE_1) — no mainline QUP_CORE_1
- `qhm_qup1`         mas=41   (ICBID_MASTER_QUP_1)      — no mainline QUP_1  ← suspicious value
- `mas_a1noc_snoc`   mas=111  (ICBID_MASTER_A1NOC_SNOC) — no mainline A1NOC
- `mas_a2noc_snoc`   mas=112  (ICBID_MASTER_A2NOC_SNOC) — no mainline A2NOC
- `slv_a1noc_snoc`   slv=142  (ICBID_SLAVE_A1NOC_SNOC)  — no mainline A1NOC slave
- `slv_a2noc_snoc`   slv=143  (ICBID_SLAVE_A2NOC_SNOC)  — no mainline A2NOC slave

Nodes resolved via ICBID but CONFIRMED equal to a mainline id (safe): mas_snoc_bimc=3,
qxm_ipa=59, mas_bimc_snoc=21, qhm_qup0=166, xm_sdc2=35, mas_crypto_c0=23, ebi=0,
slv_bimc_snoc=2, slv_snoc_cnoc=25, qxs_imem=26, slv_snoc_bimc=24, xs_qdss_stm=30,
qup0_core_master=170.

The QoS ports for SM6375-only nodes (UFS/EMMC/GPU/CDSP/camera/video/QUP1) also went
through the offset→port fallback (`off_to_port`, gen_sm6375.py:58-65); those only matter
once QoS is re-enabled (currently ap_owned forced false), so they are not the active
reset but must be re-derived from holi offsets before QoS is turned back on.

---

## Recommended bisection order for the next HW test

The v50 result (ap_owned=false everywhere, still hung) proves the fault is an **RPM
vote** (bus_clk_desc rate vote and/or a bad mas/slv_rpm_id), not MMIO/QoS. All
bus_clk_desc, reg bases, regmap sizes and qos_offsets MATCH the sm6115 reference, so the
RPM *clock* resources are very likely fine. That points the finger at the per-node
mas/slv_rpm_id GUESS values. Test in this order:

1. **Register ONLY the virtual clk_virt bus first** (qup_clk), with every node's
   `mas_rpm_id`/`slv_rpm_id` = -1 except the two qup_core masters. If it boots, the RPM
   *clock* votes are safe and the culprit is a per-node bandwidth id. (Also confirms the
   DT nesting / parent-regmap fallback issue in Note D.)

2. **Add bimc** (bimc_clk). bimc's only RPM-id nodes are `mas_snoc_bimc=3`,
   `slv_bimc_snoc=2`, `ebi=0` — all MATCH-verified. Should boot. If it hangs, the bimc
   RPM clock vote itself is the problem (unlikely; matches sm6115).

3. **Add sys_noc but first neutralize the GUESS ids**: set to -1 the six guessed ids
   (`mas_a1noc_snoc=111`, `mas_a2noc_snoc=112`, `qhm_qup1=41`, `slv_a1noc_snoc=142`,
   `slv_a2noc_snoc=143`, and temporarily `qxm_ipa=59`). Keep only MATCH-verified ids.
   If it now boots, re-enable them **one at a time** to find the offending id.

4. **Prime single-id suspects to re-enable last, in this order:** `qxm_ipa` (59),
   `qhm_qup1` (41), then the A1/A2 NOC quartet (111/112/142/143).

### Concrete id fixes to apply before the next test
- Set the six GUESS ids (41, 111, 112, 142, 143, and qup1_core 171) to a **verified
  SM6375 RPM id** from downstream `rpm-ids.h`/`rpm-smd` bootlog, or to `-1` if SM6375's
  RPM aggregates those paths implicitly (mainline sm6115 leaves A1/A2-equivalent inner
  masters without exposed ids). Do NOT trust the ICBID==RPM-id assumption for these.
- Fix `mas_snoc_bimc.qos_port` 6 → **7** (sm6375.c:117) to match holi offset 0x24300,
  before any QoS re-enable.
- Drop `config_noc` `qos_offset = 0x15000` (sm6375.c:1614) → remove/0 to match sm6115.
- Verify SM6375 DTS: (a) sys_noc "ipa" clock-name resolves to a real clock (Note A);
  (b) clk_virt/mmrt_virt/mmnrt_virt are **children of system_noc** so the parent-regmap
  fallback works (Note D).
