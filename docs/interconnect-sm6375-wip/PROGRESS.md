# Interconnect SM6375 driver — work in progress

## Goal
Write drivers/interconnect/qcom/sm6375.c so the IPA (mobile data) can vote its
NoC paths. Without it, enabling IPA (patch 0026 status=okay) hangs the SoC.

## Feasibility: CONFIRMED
- Template: mainline `qcm2290.c` (same SMD-RPM arch, icc-rpm.h, has mas_ipa).
- Source of truth: vendor `holi.c` (holi = SM6375), 6 buses, 94 nodes.
- Binding `qcom,sm6375.h` already created (copied from vendor qcom,holi.h).

## The 6 buses (holi -> sm6375)
bimc, clk_virt, config_noc, mmrt_virt, mmnrt_virt, sys_noc

## IPA path (what mobile data needs)
- sys_noc: MASTER_IPA (qxm_ipa) -> A2NOC_SNOC_SLV
- bimc: SLAVE_EBI (memory), SNOC_BIMC path
- config_noc: SLAVE_IPA_CFG
- IPA dtsi (from holi.dtsi): 
    interconnects = <&system_noc MASTER_IPA &bimc SLAVE_EBI>,
                    <&system_noc MASTER_IPA &system_noc SLAVE_OCIMEM>,
                    <&bimc MASTER_AMPSS_M0 &config_noc SLAVE_IPA_CFG>;
    names = "ipa_to_ebi1", "ipa_to_imem", "appss_to_ipa"
  (mainline IPA driver wants names: "memory", "imem", "config")

## API translation vendor -> mainline (icc-rpm.h differs)
Vendor node uses: .qosbox (regs/offsets/prio), .noc_ops, .regmap
Mainline node uses: .qos.{qos_port, qos_mode, areq_prio, ap_owned, prio_level,
                          urg_fwd_en}, .bus_clk_desc, .ab_coeff, .ib_coeff
Mainline desc: .type (QCOM_ICC_BIMC/QNOC), .nodes[], .bus_clk_desc, .regmap_cfg,
               .qos_offset, .ab_coeff, .keep_alive
QoS map: qosbox.config.prio -> qos.areq_prio ; qosbox.offsets -> qos_port ;
         mode fixed -> NOC_QOS_MODE_FIXED ; ap_owned=true for QoS nodes.

## Files here
- holi.c.vendor            : full vendor driver (tables source of truth)
- qcm2290.c.mainline-template : mainline template to mirror
- qcom,sm6375.h           : the binding (DONE)
- holi_nodes_dump.txt     : parsed node list (id/ch/bw/rpm/links/qos)
- icc-rpm.h.{vendor,mainline} : the two struct definitions to map between

## STATUS

### DONE
1. [x] sm6375.c WRITTEN + COMPILES CLEAN (no warnings) via cross-gcc arm64.
       - 1647 lines, 104 nodes, 6 buses. Generator: gen_sm6375.py (rerun to regen).
       - Uses qnoc_probe/qnoc_remove/icc_sync_state from icc-rpm.c.
       - #include <linux/regmap.h> added (regmap_config).
       - bus_clk_desc set per bus (mainline extern RPM clocks, verified vs qcm2290):
           bimc -> &bimc_clk        (MEM_CLK 0)
           sys_noc -> &bus_2_clk    (BUS_CLK 2 = SNOC)
           config_noc -> &bus_1_clk (BUS_CLK 1 = CNOC), type QCOM_ICC_NOC
           clk_virt -> &qup_clk
           mmrt_virt -> &mmaxi_1_clk
           mmnrt_virt -> &mmaxi_0_clk
       - IPA node qos_port=3 FIXED prio2 (matches qcm2290 mas_ipa).
       - BIMC RT/NRT/SNOC use BYPASS mode.
2. [x] Kconfig + Makefile entries (CONFIG_INTERCONNECT_QCOM_SM6375=m).
       Snippets saved: Kconfig.snippet, Makefile.snippet.
3. [x] All RPM clocks confirmed to exist in mainline qcom,rpmcc.h
       (RPM_SMD_MMNRT_CLK/MMRT_CLK/QUP_CLK/SNOC_CLK/CNOC_CLK/BIMC_CLK).

### TODO (next)
4. [ ] DTS patch: add the 6 NoC provider nodes to sm6375.dtsi (reg/clocks from
       DT-NODES.md). bimc@0x04480000, system_noc@0x01880000,
       config_noc@0x01900000, clk_virt/mmrt_virt/mmnrt_virt (virtual, no reg).
5. [ ] DTS: put interconnects=<...> on the IPA node + status=okay; restore
       real interconnect_count in ipa_data_v4_11_sm6375 (revert count=0 hack
       in kernel patch 0025).
6. [ ] Package as kernel patch 0027 (driver + binding + Kconfig/Makefile) and
       DTS patch. Build APK, flash, test mobile data. Iterate (wrong QoS port
       hangs the SoC).

## Register bases (from vendor holi.dtsi — TO EXTRACT)
Need: system_noc@<addr>, config_noc@<addr>, bimc/mc_virt@<addr>, clk_virt,
mmrt_virt, mmnrt_virt. Check arch/.../vendor/qcom/holi.dtsi for the icc nodes.

## CRITICAL: QoS offset -> qos_port conversion (vendor holi.c uses offsets, mainline uses ports)

Mainline register formulas (icc-rpm.c):
- BIMC:  M_BKE_REG_BASE(n) = 0x300 + 0x4000*n   (health/en registers per port n)
- QNOC:  MCTL_LOWn_ADDR(n) = 0x8 + n*0x1000
         PRIORITYn_ADDR(n) = 0x8 + n*0x1000 ; MODEn_ADDR(n) = 0xc + n*0x1000

Vendor holi.c uses absolute .offsets in the qosbox (see holi_qos_dump.txt):
- BIMC nodes: apps_proc 0x8300, snoc_bimc_rt 0x10300, snoc_bimc_nrt 0x14300,
              snoc_bimc 0x24300  -> with qos_offset=0x8000:
              port = (offset - 0x8000 - 0x300) / 0x4000
              apps_proc: (0x8300-0x8300)/0x4000 = 0  -> qos_port 0
              snoc_bimc_rt: (0x10300-0x8300)/0x4000 = 2 -> port 2
              snoc_bimc_nrt: (0x14300-0x8300)/0x4000 = 3 -> port 3
              snoc_bimc: (0x24300-0x8300)/0x4000 = 7 -> port 7
- SNOC/QNOC nodes (qxm_ipa 0x18000, etc.): offset is n*0x1000 based.
              With qos_offset for sys_noc, port = offset / 0x1000 (roughly).
              qxm_ipa 0x18000 -> port 0x18 = 24? VERIFY against real base.

SAFEST APPROACH for shared nodes: use the qos_port values that qcm2290.c already
has for the SAME node (mainline-verified), instead of converting offsets by hand.
qcm2290 mas_ipa = qos_port 3. Cross-check each shared node's port with qcm2290,
and only convert offsets for the sm6375-only nodes (UFS/EMMC/GPU/cam/video/QUP1).
A WRONG qos_port writes to the wrong register -> SoC hang. Test incrementally.

## Files added
- holi_qos_dump.txt : parsed qosbox (num_ports/offsets/prio/urg/mode) for the 24 QoS nodes

## ==== HARDWARE TEST RESULTS (session 2) ====

Driver + DTS built and validated (compiles clean, DTB phandles resolve).
Packaged as kernel patches 0027-0029 (+ 0030 DIAG). Flashed to real HW
(Moto G82 5G, Kali port). Three test boots, ALL hang (black screen, no
telnet, no SSH) and produce NO ramoops (pstore empty) -> reset happens very
early, before the kernel log survives. Recovered each time with kali-boot-v47.

### v48 = full: NoC nodes + driver + IPA okay (interconnects memory/config)
    -> HANG, no ramoops.

### v49 = NoC nodes + driver, IPA status=disabled
    -> HANG, no ramoops.
    => rules OUT the IPA/GSI. The interconnect DRIVER itself hangs the SoC
       at boot (it probes the buses as soon as it sees the NoC nodes).

### v50 = v49 + DIAG patch 0030: qos.ap_owned=false on ALL 24 nodes
    (probe skips qcom_icc_qos_set entirely; NO QoS register writes, only
     RPM clock/bandwidth votes)
    -> HANG, no ramoops.
    => rules OUT the QoS register writes. The hang is NOT a wrong qos_port.

### CONCLUSION / narrowed cause
The reset is caused by the BUS-LEVEL config, not QoS. Candidates, in order:
 1. Wrong bus_clk_desc / RPM bus clock: voting a bad RPM clock resource
    (bimc/bus_1/bus_2/qup/mmaxi_0/mmaxi_1) can wedge the RPM and reset.
 2. Wrong reg base/size or regmap max_register on a real bus (bimc/sys_noc/
    config_noc) -> bad MMIO access on probe.
 3. keep_alive=true forcing an early bandwidth vote on a mis-described bus.
 4. A mas_rpm_id/slv_rpm_id that is invalid for SM6375's RPM (we assumed the
    ICBID == mainline RPM id; may be wrong for some nodes).

### NEXT STEPS (next session) — bisect the BUS layer
Build variants that register buses ONE at a time (or a minimal subset):
 a. v51: only the 3 virtual buses (clk_virt/mmrt/mmnrt) — no reg/MMIO, only
    RPM clocks. If it boots, MMIO (reg/regmap) of a real bus is the fault.
 b. v52: add bimc only (has reg + bimc_clk). Then sys_noc, then config_noc.
    First one that hangs = the culprit bus.
 c. Cross-check every mas_rpm_id/slv_rpm_id against a known-good SM6375/holi
    RPM id list (not just the ICBID assumption). Check qxm_ipa mas_rpm_id=59
    and the bimc SLAVE_EBI slv_rpm_id=0 especially.
 d. Consider setting keep_alive=false on the descs to avoid the early vote.
 e. Get downstream holi bootlog RPM ids if possible.

### Rollback / stable
kali-boot-v47.img (sha 1612242a...) is the stable image WITHOUT any
interconnect work. All interconnect patches (0027-0030) are DIAGNOSTIC and
have been REMOVED from the pmaports APKBUILD so the default kernel build is
the stable v47-equivalent again. The patches themselves are kept in
pmaports dir and in this WIP folder for the next session.

### Test artifacts (bind mount /opt/postmarket/kali-nethunter/img/)
 - kali-boot-v48.img  (full, IPA okay)         HANG
 - kali-boot-v49.img  (driver, IPA disabled)   HANG
 - kali-boot-v50.img  (driver, no QoS writes)  HANG
 - kali-boot-v47.img  (STABLE rollback)        OK
 - linux-image-...rhodep3.deb (= v48 kernel, do NOT dpkg -i, hangs)
