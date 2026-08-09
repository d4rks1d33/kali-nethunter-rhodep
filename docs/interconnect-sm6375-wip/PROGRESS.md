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

## TODO (next session)
1. Write sm6375.c: for each of the 6 buses, port its nodes from holi.c into
   mainline qcom_icc_node format (translate QoS). Mirror qcm2290.c structure.
2. Get the bus register bases + clocks from vendor holi.dtsi (bimc@..., 
   system_noc@..., config_noc@...) for the DT nodes.
3. Add the 6 NoC provider nodes to sm6375.dtsi (patch).
4. Kconfig + Makefile + CONFIG_INTERCONNECT_QCOM_SM6375=y.
5. Put interconnects=<...> on the IPA node + status=okay.
6. Build, flash, test mobile data. Iterate (a wrong QoS port hangs the SoC).

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
