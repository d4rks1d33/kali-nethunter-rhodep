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

## ==== SESSION 3: audit fixes + bisection start ====

### v51 = audit fixes (6 GUESS rpm ids -> -1, mas_snoc_bimc qos_port 6->7,
###        drop config_noc qos_offset), IPA still disabled
    -> HANG, no ramoops. The 6 guessed RPM ids were NOT (the only) cause.

### BUG found while preparing v52: of_match compatibles did NOT match the DTS
- driver of_match used "qcom,sm6375-sys-noc" / "qcom,sm6375-config-noc"
- DTS (patch 0028) + sm6115 use "qcom,sm6375-snoc" / "qcom,sm6375-cnoc"
=> sys_noc and config_noc NEVER probed in v48-v51. Only bimc + the 3 virtual
   buses (clk_virt/mmrt_virt/mmnrt_virt) actually matched and probed.
=> Therefore the early hang in v48-v51 is caused by bimc OR one of the 3 virtual
   buses (NOT sys_noc/config_noc, which never ran). Fixed the compatibles in the
   generator (snoc/cnoc) for future builds.

### Key probe insight (icc-rpm.c:563)
"/* If this fails, bus accesses will crash the platform! */" right before
clk_bulk_prepare_enable(intf_clks). A bad intf clock or an MMIO access on a
mis-clocked bus is a hard-crash (matches: no ramoops).

### Bisection plan (ONLY_BUS env in gen_sm6375.py emits of_match for one bus)
- v52 = ONLY clk_virt (virtual: no reg/MMIO, no intf_clks, only qup_clk RPM).
    If BOOTS: framework + a basic RPM vote are safe; culprit is MMIO/clocks of a
              real bus (bimc) or the mm* virtual buses' RPM votes.
    If HANGS: the fault is more fundamental (RPM vote path or DT nesting/regmap
              fallback for a no-reg bus, see AUDIT Note D).
- next: v53 = ONLY bimc, then add mmrt/mmnrt, then snoc/cnoc (now that
  compatibles match).

### v52 = ONLY clk_virt -> BOOTED, but INCONCLUSIVE
dmesg showed NO /sys/class/interconnect and NO clk-virt probe; instead:
  gcc-sm6375: sync_state() pending due to 1880000.interconnect
  gcc-sm6375: sync_state() pending due to 1900000.interconnect
=> clk_virt (a no-reg child of system_noc) never actually probed with that
   of_match (parent system_noc not matched), so v52 booting only proves "driver
   touched nothing", not that clk_virt is safe.

### v53 = ONLY bimc -> HANG
=> BIMC is the culprit. bimc is the only MMIO bus that matched in v48-v51, so it
   explains every earlier hang. bimc reg (0x04480000/0x80000), DTS (no clocks,
   #cells=2) and desc (type/regmap/bus_clk_desc/qos_offset/ab_coeff/keep_alive)
   are IDENTICAL to sm6115 — so the difference is the rhodep hardware, not the
   driver. Prime hypothesis: the BIMC MMIO range 0x04480000 is XPU/hypervisor
   protected on the locked Motorola device, so devm_ioremap_resource + the first
   regmap access triggers an XPU violation -> instant reset, no ramoops.

### v54 = ONLY bimc, NO_REGMAP (no regmap_cfg -> probe skips ioremap/MMIO)
  Tests the XPU-protected-MMIO hypothesis. If v54 BOOTS -> it was the bimc MMIO
  (we then keep bimc regmap-less / QoS-less, which is fine: QoS not needed for
  the IPA path). If v54 HANGS -> it's the bimc bus_clk (bimc_clk RPM vote), not
  MMIO.

### v54 = ONLY bimc, NO_REGMAP (no MMIO) -> HANG
=> NOT the MMIO. The hang is the bimc bus-rate vote, not the register access.

### VENDOR (holi.c) analysis — how the phone natively votes the BIMC
- Vendor holi.c gets bus clocks FROM THE DT via devm_clk_bulk_get(dev, "bus",
  "bus_a") and votes with clk_set_rate() on the CCF clock (bimc DTS:
  clock-names="bus","bus_a" -> <&rpmcc RPM_SMD_BIMC_CLK>,<&rpmcc RPM_SMD_BIMC_A_CLK>).
  i.e. the vote goes through the managed rpmcc clock (CCF path).
- Mainline sm6115.c (and our driver) instead use `.bus_clk_desc = &bimc_clk`,
  which sends a RAW SMD-RPM bus-rate message (qcom_icc_rpm_set_bus_rate),
  bypassing the CCF clock. icc-rpm.c has BOTH paths: `bus_clk` (CCF, ==vendor)
  vs `bus_clk_desc` (raw SMD).
- CRITICAL: mainline's sm6375 rpmcc table (clk-smd-rpm.c sm6375_clks[]) does NOT
  expose RPM_SMD_BIMC_CLK (only BIMC_FREQ_LOG). Neither does sm6115's table, yet
  sm6115 works with the raw bus_clk_desc path -> so the SM6375 RPM firmware
  itself appears to reject/hang on the raw BIMC bus-rate SMD message that sm6115
  tolerates. This is the difference between the working sm6115 and rhodep.

### v55 = ONLY bimc, NO_REGMAP + NO_BUSCLK (no MMIO, no bus-rate vote at all)
  With neither bus_clk_desc nor bus_clk, qcom_icc_rpm_set() returns 0 early
  (icc-rpm.c:377) -> the bimc provider registers but issues NO SMD-RPM vote and
  NO MMIO. If v55 BOOTS -> 100% confirmed the BIMC SMD-RPM bus-rate vote is the
  hang. Fix direction then: vote the BIMC via the CCF path like the vendor
  (add clock-names="bus","bus_a" to the bimc DTS + drop bus_clk_desc so the
  driver uses devm_clk_get_optional("bus") + clk_set_rate), OR leave BIMC
  vote-less if DDR is already clocked by the bootloader/other driver.

### v55 = ONLY bimc, NO_REGMAP + NO_BUSCLK -> HANG
=> Even a fully INERT bimc provider (no MMIO, no vote) hangs. This ruled out
   MMIO and the bus-rate vote, and pointed at a system-level side effect of
   merely registering the nodes.

### ROOT CAUSE FOUND (VENDOR-BIMC.md, via vendor core.c analysis)
mainline icc_node_add() (core.c) inits every node to init_avg=init_peak=INT_MAX
when the desc has NO get_bw callback, then immediately calls provider->set().
For the `ebi` node (SLAVE_EBI = DDR, slv_rpm_id=0) this fires an unsolicited
RPM_BUS_SLAVE_REQ with INT_MAX bandwidth to the DDR/EBI resource AT PROBE. The
SM6375 RPM firmware resets on that. This path runs BEFORE the !bus_clk_desc
early-return, so v54/v55 (no MMIO/no bus_clk) never removed it -> that's why the
inert provider still hung. The vendor downstream icc_node_add() does nothing but
link the node (no init bw, no set()) -> never blasts INT_MAX at DDR.
Confirmed rpmcc pins RPM_SMD_BIMC_CLK at INT_MAX via handoff (sync_state/clock
hypotheses disproved). Same fix precedent exists in mainline msm8974.c.

### v56 = THE FIX: get_bw() returning 0 on all descs (+ ignore_enxio), full driver
- Added `static int sm6375_get_bw(node,*avg,*peak){*avg=0;*peak=0;return 0;}`
  and `.get_bw = sm6375_get_bw, .ignore_enxio = true` on all 6 bus descs.
  => icc_node_add() now inits every node to 0, so provider->set() is NOT called
     at probe (mirrors vendor "no vote until a real consumer asks").
- Full driver re-enabled: all 6 buses, regmap, bus_clk_desc, keep_alive,
  compatibles fixed to snoc/cnoc so sys_noc/config_noc actually probe now.
- IPA still disabled (isolate: prove the full interconnect driver no longer
  resets the SoC). If v56 BOOTS -> root cause fixed; next = enable IPA (v57).

### v56 (full driver + get_bw, IPA off) -> HANG
### v57 (ONLY bimc + get_bw) -> HANG
=> get_bw does NOT save bimc. Confirmed in code that get_bw=0 prevents the
   probe-time provider->set() (icc_node_add core.c:1089), so the boot INT_MAX
   vote is NOT the (only) cause. bimc hangs during PROBE itself, before/around
   node registration, regardless of MMIO/vote/bw. (v53/54/55/57 all hang.)

### NEW leading hypothesis: adopting the bimc node triggers GCC/rpmcc sync_state
### to power OFF a clock the DDR/BIMC path needs.
- v52 (only clk_virt, which never actually probed) BOOTED and logged:
    gcc-sm6375: sync_state() pending due to 1880000.interconnect / 1900000...
  i.e. GCC waits for the interconnect providers before it may gate unused clocks.
- When a real provider (bimc) is adopted, sync_state can proceed and gate a
  clock nothing else claims but the memory path relies on -> instant reset.

### v58 = SAME kernel as v57 (bimc-only + get_bw) but cmdline adds
###       `clk_ignore_unused pd_ignore_unused`  (no kernel rebuild, just reboot img)
  If v58 BOOTS -> it's an unused-clock/power-domain being gated. Then the real
  fix is to mark the needed rpmcc/GCC clock CLK_IS_CRITICAL (or add it to the
  bimc DT node as a claimed clock) rather than the cmdline workaround.
  If v58 HANGS -> not a clock-gating issue; look elsewhere (SCM/XPU on node
  adoption, or the icc framework provider registration itself).

### v58 (bimc-only+get_bw + clk_ignore_unused pd_ignore_unused cmdline) -> HANG
=> Not clock/pd gating either.

### SSH STATE ANALYSIS (v47, working, no icc driver) — decisive findings
- /sys/class/interconnect absent; DDR/everything works with NO Linux icc provider.
- clk_summary: bimc/cnoc/mmnrt/mmrt/qup/snoc NOT listed as CCF clocks; only
  snoc_lpass/snoc_periph shown at INT_MAX. => rpmcc HANDS OFF the icc bus clocks
  to INT_MAX (clk-smd-rpm.c rpm_clk_sm6375.icc_clks = sm_qnoc_icc_clks) but does
  NOT register them as CCF clocks. The RPM keeps bimc/cnoc/snoc voted at INT_MAX
  on its own; Linux never needs to clock DDR.
- rpmcc device = child of "remoteproc:glink-edge:rpm-requests" (GLINK transport).
- rpmcc probe ALSO registers a platform_device "icc_smd_rpm" (interconnect
  transport layer, smd-rpm.c) — this is just the SMD send path our qnoc driver
  uses, NOT a node provider. So our qnoc+DT-nodes approach is architecturally OK.

### CONCLUSION: the bimc/NoC provider is not required for mobile data
Since rpmcc already pins bimc/cnoc/snoc to INT_MAX, and mainline IPA probes fine
with interconnect_count=0 (ipa_power.c: the init loop is skipped, of_icc_bulk_get
with 0 paths returns 0, icc_bulk_* become no-ops), we can ENABLE IPA WITHOUT any
interconnect provider at all. The early reset we saw in v48 was the BIMC provider
(bisected), NOT the IPA. Enabling IPA alone (no NoC nodes, count=0) was never
actually tested in isolation.

### NEXT: v59 = IPA enabled, NO interconnect driver/nodes
- Revert patch 0028 (drop the NoC provider DT nodes) and 0027 (drop the driver).
- Keep patch 0025 (ipa_data_v4_11_sm6375, interconnect_count=0).
- Patch 0026: flip IPA status=okay, and DO NOT add interconnects= (leave none).
- If it BOOTS and mmcli sees the modem doing data -> mobile data works, bypassing
  the whole BIMC hang. The interconnect driver becomes a nice-to-have, not a
  blocker.

### v59 = IPA okay, NO interconnect driver/nodes (count=0) -> BOOTLOOP (~20s)
  logo -> black ~20s -> reboot. ~20s = watchdog territory.
### v61 = v59 + cmdline `nowatchdog` -> INSTANT reboot after logo
  => not only the watchdog; IPA=okay itself resets the kernel.
### v62 = v59 + ramoops fixed (no-map, record-size 0x20000, max-reason=5)
  -> still BOOTLOOP, and /sys/fs/pstore STILL EMPTY after recovery.
  => the Motorola warm reset clears the ramoops RAM (or 0xd0000000 is wrong).
     We remain blind. See HANDOFF-SESSION3.md §4 for how to get a real log.

### SESSION 3 CLOSED. pmaport RESTORED to stable v47 (IPA disabled, ramoops
### original, no SM6375 icc config). See HANDOFF-SESSION3.md for full handoff.

## ==== SESSION 4: SOLVED — it was never the interconnect ====

See HANDOFF-SESSION4.md for the full write-up. Short version:

### The IPA node had the sdm845/sc7180 register layout
From IPA v4.5 on, the shared-SRAM direct access window is at reg_base + 0x10000,
not + 0x7000 (downstream ipahal_reg.c: [IPA_HW_v3_0] 0x7000 vs [IPA_HW_v4_5]
0x10000, with ipahal_get_reg_base() == 0x40000; mainline sm6350.dtsi/sm8350.dtsi
confirm it: ipa-reg 0x8000, ipa-shared at base+0x50000, gsi 0x23000).
So ipa-shared pointed at 0x5847000, a hole on this SoC where ANY access — even a
32-bit read — resets the SoC instantly with no exception and no output. That was
the cause of every "IPA okay" bootloop from v48 to v62, and the reason it looked
like IPA needed an interconnect vote. Fixed:
  reg = <0x05840000 0x8000>, <0x05850000 0x3000>, <0x05804000 0x23000>

Also fixed in the same node: the uc SMMU sid (0x4a2, like sm6350's 0x442) was
missing, and qcom,gsi-loader must be "self" (the AP loads the GSI firmware via
TZ PAS id 15, per downstream qcom,ipa_fws) with ipa_fws.mdt taken from the modem
(NON-HLOS) partition.

RESULT: "IPA driver initialized" + "IPA driver setup completed successfully",
rmnet_ipa0 created, ModemManager sees ports "qrtr0 (qmi), rmnet_ipa0 (net)".
No interconnect provider needed at all: patches 0027-0030 stay out of source=.

### And the reason sessions 1-3 were blind: ramoops at the wrong address
0xd0000000 comes from the generic holi/blair dtsi node, which Motorola boards
DELETE (blair-moto-base.dts: /delete-node/ ramoops). The real region is
RAMOOPS_BASE_ADDR 0xaf000000 (dt-bindings/moto/moto-mem-reserve.h). With that
fixed, the console log survives to "reboot: Restarting system".
NOTE: read it from /var/lib/systemd/pstore/, systemd-pstore moves it out of
/sys/fs/pstore/.

### Images
kali-boot-v65.img = final (clean). v64 = same + DIAG printks. v63 = ramoops fix
only (IPA node still wrong, boots fine). v47 = original rollback.
