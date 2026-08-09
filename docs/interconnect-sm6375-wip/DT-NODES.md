# NoC provider DT nodes (from vendor holi.dtsi -> for sm6375.dtsi patch)

6 buses. Virtual ones (clk_virt, mmrt_virt, mmnrt_virt) have NO reg (clock-only).
RPM clocks come from &rpmcc (already in sm6375.dtsi).

## bimc: interconnect@4480000
  reg = <0x0 0x04480000 0x0 0x80000>
  compatible = "qcom,sm6375-bimc"  (mainline naming)
  clocks = <&rpmcc RPM_SMD_BIMC_CLK>, <&rpmcc RPM_SMD_BIMC_A_CLK>
  util-factor 151, keepalive

## system_noc: interconnect@1880000
  reg = <0x0 0x01880000 0x0 0x5f080>
  compatible = "qcom,sm6375-sys-noc"
  clocks = <&rpmcc RPM_SMD_SNOC_CLK>, <&rpmcc RPM_SMD_SNOC_A_CLK>,
           <&gcc GCC_CFG_NOC_USB3_PRIM_AXI_CLK>, <&gcc GCC_SYS_NOC_USB3_PRIM_AXI_CLK>
  intf_clocks = "cfg_noc_usb3_prim_axi", "sys_noc_usb3_prim_axi" (check mainline names)
  keepalive

## config_noc: interconnect@1900000
  reg = <0x0 0x01900000 0x0 0x6200>
  compatible = "qcom,sm6375-config-noc"
  clocks = <&rpmcc RPM_SMD_CNOC_CLK>, <&rpmcc RPM_SMD_CNOC_A_CLK>
  keepalive

## clk_virt: interconnect  (no reg, virtual)
  compatible = "qcom,sm6375-clk-virt"
  clocks = <&rpmcc RPM_SMD_QUP_CLK>, <&rpmcc RPM_SMD_QUP_A_CLK>
  keepalive

## mmnrt_virt: interconnect@0  (no reg)
  compatible = "qcom,sm6375-mmnrt-virt"
  clocks = <&rpmcc RPM_SMD_MMNRT_CLK>, <&rpmcc RPM_SMD_MMNRT_A_CLK>
  util-factor 142, keepalive

## mmrt_virt: interconnect@1  (no reg)
  compatible = "qcom,sm6375-mmrt-virt"
  clocks = <&rpmcc RPM_SMD_MMRT_CLK>, <&rpmcc RPM_SMD_MMRT_A_CLK>
  util-factor 142, keepalive

## Bus -> nodes mapping (from holi.c desc, 6 arrays):
- bimc_nodes:       apps_proc(MASTER_AMPSS_M0), mas_snoc_bimc_rt/nrt, mas_snoc_bimc,
                    qnm_gpu(GRAPHICS_3D), qnm_cdsp(CDSP_PROC), tcu_0, + SLAVE_EBI, BIMC_SNOC_SLV
- clk_virt_nodes:   qup0/1_core master+slave (QUP_CORE_0/1)
- config_noc_nodes: mas_snoc_cnoc(SNOC_CNOC_MAS), qhm_qdss_dap + all SLAVE_*_CFG (incl SLAVE_IPA_CFG)
- mmrt_virt_nodes:  camera_rt, mdp0, snoc_rt + slaves
- mmnrt_virt_nodes: camera_nrt, venus0, venus_cpu, snoc_nrt + slaves
- sys_noc_nodes:    (33 nodes incl MASTER_IPA=qxm_ipa) -> see holi.c line ~1600

## IPA node interconnects (add to sm6375-motorola-rhodep.dts, patch 0026 update):
interconnects = <&system_noc MASTER_IPA &bimc SLAVE_EBI>,
                <&system_noc MASTER_IPA &system_noc SLAVE_OCIMEM>,
                <&bimc MASTER_AMPSS_M0 &config_noc SLAVE_IPA_CFG>;
interconnect-names = "memory", "imem", "config";
And flip status = "okay". Also set the ipa_data variant back to real
interconnect_count (revert the count=0 hack in patch 0025) OR keep 0 and rely on
these DT interconnects — check ipa_power.c: it uses interconnect_count from the
DATA table, not from DT. So ALSO restore interconnect_count in ipa_data_v4_11_sm6375
(memory/config) once the provider exists.
