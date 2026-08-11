# SM6375 mobile-data (interconnect + IPA) — HANDOFF for the next AI/session

Device: Motorola Moto G82 5G (motorola-rhodep, SoC SM6375 / Snapdragon 695,
vendor codename **holi**). Kernel 7.2.0-rc5. Goal: enable **mobile data** (IPA).

READ THIS FIRST, then PROGRESS.md (full chronological bisection), AUDIT.md
(RPM-id cold audit), VENDOR-BIMC.md (root-cause of the INT_MAX-at-probe theory),
and DIAGNOSIS.md if present.

--------------------------------------------------------------------------------
## 0. CURRENT STATE — what is stable, what is not (IMPORTANT)
--------------------------------------------------------------------------------
- **`kali-boot-v47.img` is the STABLE Kali image.** Everything works (display,
  wifi/BT, modem remoteprocs, GPU, USB/OTG + TP-Link rtl8188eus inject, Docker,
  battery, phone apps). This is the rollback. IP over wifi: 192.168.1.53; USB
  gadget: 172.16.42.1. Located in /opt/postmarket/kali-nethunter/img/.
- **postmarketOS port was NOT touched this session** — it should be exactly as it
  was. Do not assume any pmOS change.
- **The pmaports kernel aport was RESTORED to the stable v47 state** at the end of
  this session:
  - `/home/pmos/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep/`
  - source= ends at patch 0026, IPA node `status = "disabled"`, ramoops node
    reverted to original (no `no-map`), config has NO
    `CONFIG_INTERCONNECT_QCOM_SM6375`. sha512sums for 0008 and 0026 were
    recomputed. A fresh `pmbootstrap build linux-motorola-rhodep` now produces the
    stable v47-equivalent kernel. VERIFIED: patches 0001-0026 apply clean, IPA
    disabled, ramoops has no no-map, no SM6375 icc config.
  - Patches 0027-0030 (interconnect driver/DTS/DIAG) still exist ON DISK in that
    dir but are NOT in source=, so they don't affect the build.
- All the diagnostic boot images v51..v62 are in
  /opt/postmarket/kali-nethunter/img/ (they HANG or BOOTLOOP — do not ship them).

--------------------------------------------------------------------------------
## 1. WHERE WE GOT STUCK (the two blockers)
--------------------------------------------------------------------------------
Two independent things reset the SoC VERY early, and we could NOT capture a log
(ramoops/pstore stays empty — see §4):

### Blocker A: the interconnect (NoC) driver hangs on BIMC
We WROTE a full SM6375 interconnect driver (models: mainline sm6115.c/qcm2290.c;
source of truth: vendor holi.c). It compiles clean, DTB phandles resolve. On HW
it resets the SoC the moment the driver ADOPTS the bimc node
(`interconnect@4480000`). Bisected exhaustively:
- ONLY bimc, full  -> HANG
- ONLY bimc, no MMIO (no regmap_cfg) -> HANG
- ONLY bimc, no MMIO AND no bus_clk_desc (inert provider, does nothing) -> HANG
- ONLY bimc + get_bw()=0 (prevents the probe-time INT_MAX vote) -> HANG
- + `clk_ignore_unused pd_ignore_unused` on cmdline -> HANG
- ONLY clk_virt (never actually probed, compatible mismatch) -> BOOTED (moot)
=> Merely having our driver bind the bimc DT node resets the SoC, independent of
   MMIO / SMD bus-rate vote / boot bandwidth vote / clock gating. Cause still not
   definitively identified. Best remaining leads in VENDOR-BIMC.md and §3 below.

### Blocker B: enabling IPA (status=okay) bootloops
Because rpmcc already hands off bimc/cnoc/snoc to INT_MAX (see §2), we tried to
SKIP the interconnect driver entirely and just enable IPA with
interconnect_count=0 and NO interconnects= property (v59/v62):
- v59 (IPA okay, no icc nodes/driver): logo -> black ~20s -> reboot (bootloop).
  The ~20s smells like the **watchdog** firing after something wedges the boot.
- v61 (same, cmdline `nowatchdog`, removed watchdog_thresh): **instant** reboot
  after logo. So it's not only the watchdog; IPA=okay makes the kernel reset.
=> IPA needs something it isn't getting. Candidates: a real interconnect vote,
   GSI firmware timing vs the modem, or an AOSS/qmp resource. Needs the log.

--------------------------------------------------------------------------------
## 2. KEY FACTS discovered via SSH on the WORKING v47 (ground truth)
--------------------------------------------------------------------------------
- `/sys/class/interconnect/` does NOT exist on v47, yet DDR/modem/everything
  works. So Linux does NOT need an icc provider for the system to run.
- `clk_summary`: bimc/cnoc/mmnrt/mmrt/qup/snoc are NOT CCF clocks. Only
  snoc_lpass/snoc_periph shown, at rate 2147483647 (INT_MAX). => the **rpmcc
  hands off the icc bus clocks to INT_MAX** (clk-smd-rpm.c: rpm_clk_sm6375.icc_clks
  = sm_qnoc_icc_clks = {bimc,cnoc,mmnrt,mmrt,qup,snoc}) but does NOT register them
  as CCF clocks. The RPM keeps the buses voted up on its own.
- rpmcc device = child of `remoteproc:glink-edge:rpm-requests` => the RPM link is
  **GLINK** (rpm-requests over glink-edge), not classic SMD.
- rpmcc probe also spawns a `icc_smd_rpm` platform device (interconnect/qcom/
  smd-rpm.c) — that's only the SMD send transport our qnoc driver uses, NOT a node
  provider. So our qnoc + DT-nodes architecture is not obviously wrong.
- pm_genpd_summary: cx=on, mx=on; no bimc/ddr genpd. So DDR is not behind a Linux
  power domain.
- IMPORTANT UPSTREAM FACT: the reference tree
  /tmp/opencode/kernel/linux-7.2-rc5/arch/arm64/boot/dts/qcom/sm6375.dtsi ALREADY
  contains system_noc@1880000 / config_noc@1900000 / bimc@4480000 nodes (compat
  qcom,sm6375-snoc/-cnoc/-bimc, #interconnect-cells=2) AND the virtual clk/mmrt/
  mmnrt children — but there is NO matching driver in mainline
  (drivers/interconnect/qcom has no sm6375.c). The pmbootstrap tarball
  (linux-motorola-rhodep-7.2_rc5.tar.gz) does NOT have these nodes, so our patch
  0028 adds them (no duplication in the real build — verified DTB has each once).
  => Someone upstream staged the DT nodes ahead of a driver. Worth checking
  linux-next / linux-arm-msm for an in-flight sm6375 interconnect driver and
  copying THAT instead of our hand-written one.

--------------------------------------------------------------------------------
## 3. TOP HYPOTHESES / NEXT STEPS TO TRY (ranked)
--------------------------------------------------------------------------------
1. **Get a real crash log first (see §4).** Without it, both blockers are guesswork.
   Try: (a) fix ramoops so it truly persists across the Motorola warm reset
   (our no-map/record-size change did NOT help — pstore still empty; the
   bootloader likely wipes 0xd0000000, or the reset is a PMIC/PS_HOLD reset that
   clears DRAM). Consider a DIFFERENT persistent-RAM address from the vendor
   `RAMOOPS_BASE_ADDR` macro (holi-moto-common-overlay.dtsi uses a macro whose
   value is in a header NOT in the sparse checkout — fetch it). (b) Or bring up
   an EARLY serial console / UART if the device exposes test points. (c) Or use
   `earlycon` + a framebuffer console (`fbcon`) to SEE the panic on screen and
   photograph it — the display works early; the black screen might just be no
   console bound to the fb. This is probably the single most useful next step.
2. **For IPA (Blocker B)**: try IPA=okay but `qcom,gsi-loader = "skip"` or the AP
   loader instead of "modem", to rule out an IPA<->modem GSI-firmware deadlock.
   Also check whether mainline IPA on SM6375 REQUIRES the "ipa" interconnect
   path (memory/config) to be a real provider even with count=0.
3. **For interconnect (Blocker A)**: look for an in-flight/mainline sm6375
   interconnect driver (linux-next, linux-arm-msm list, Konrad Dybcio / Luca Weiss
   patches — they did a lot of SM6375/holi mainlining). Our from-scratch driver
   may be missing a subtlety their driver has. The DT nodes already being upstream
   strongly suggests a driver exists or is in review.
4. Re-examine the "inert bimc still hangs" clue against `icc_provider_register` /
   `of_platform_populate` of the virtual child nodes (clk_virt/mmrt/mmnrt are
   CHILDREN of system_noc in the DTS; when bimc alone is in of_match they aren't
   involved, yet bimc alone hangs — so it's specifically bimc adoption).
5. Consider whether the Motorola bootloader/XPU protects the BIMC region such that
   even ioremap-less node adoption triggers a secure-world reset. Compare against
   how the vendor's own driver runs (it DOES touch bimc via clk_set_rate on the
   DT rpmcc clock, not raw SMD — see VENDOR-BIMC.md).

--------------------------------------------------------------------------------
## 4. WHY WE'RE BLIND: ramoops/pstore never captured anything
--------------------------------------------------------------------------------
Every hang/bootloop this session left `/sys/fs/pstore/` EMPTY after recovering to
v47, even after adding `no-map` + bigger record-size + `max-reason=5` to the
ramoops node (patch 0008) in v62. Likely reasons:
- The reset is a hard PS_HOLD/PMIC reset (or SError in secure world) that clears
  DRAM, so ramoops at 0xd0000000 doesn't survive.
- Or 0xd0000000 is the wrong persistent address for rhodep (vendor uses a macro).
- Getting SOME visibility (fb console photo, uart, or correct persistent-ram
  address) is the prerequisite for real progress. Do this before more blind builds.

--------------------------------------------------------------------------------
## 5. HOW TO BUILD / FLASH / RECOVER (mechanics)
--------------------------------------------------------------------------------
- Kernel build: `cd /home/pmos && pmbootstrap build --force linux-motorola-rhodep`
  Output apk: ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk
- Boot image: extract the apk, then
  `python3 /opt/postmarket/_common/scripts/mkbootv2b.py <vmlinuz> <dtb> <ramdisk> "<cmdline>" out.img`
  - ramdisk reused from v47: `/tmp/v47_ramdisk` (pmOS initramfs, extracted from
    kali-boot-v47.img). cmdline of v47: `/tmp/v47_cmdline.txt`.
  - vmlinuz must be the FLAT Image (starts with `MZ`, NOT gzip 1f8b).
  - DTB is appended to the kernel by mkbootv2b.py (Motorola bootloader needs flat
    Image + appended DTB; Image.gz-dtb resets).
- Flash test:   `fastboot flash boot_a <img>` ; `fastboot reboot`
- Recover:      `fastboot flash boot_a kali-boot-v47.img` ; `fastboot reboot`
- `fastboot flash userdata` is ALWAYS permission-denied on this device (no
  fastbootd). userdata is written via dd from a running OS / rescue-boot.
- Cross toolchain build tree for quick .o/.dtb checks: /tmp/gpuwork/linux-7.2-rc5
  (`make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- ...`). dtc:
  /tmp/gpuwork/linux-7.2-rc5/scripts/dtc/dtc.

--------------------------------------------------------------------------------
## 6. THE DRIVER + GENERATOR (our WIP artifacts)
--------------------------------------------------------------------------------
In /opt/postmarket/_common/interconnect-sm6375-wip/ (and mirrored read-only in the
git repo docs/interconnect-sm6375-wip/):
- `gen_sm6375.py` — generator that emits sm6375.c. Supports diagnostic env vars:
  ONLY_BUS=bimc            -> of_match with a single bus (bisection)
  NO_REGMAP=bimc           -> omit regmap_cfg (no MMIO)
  NO_BUSCLK=bimc           -> omit bus_clk_desc (no SMD bus-rate vote)
  NO_KEEPALIVE=1           -> drop keep_alive
  NO_EBI_RPM=1             -> set ebi.slv_rpm_id = -1
  DIAG_NO_GETBW=1          -> do NOT emit get_bw (revert the INT_MAX-vote fix)
  The default (no env) now emits: full 6-bus driver WITH get_bw()=0 + ignore_enxio
  on every desc, RPM_NEUTRALIZE for the 6 guessed ids, mas_snoc_bimc qos_port=7,
  config_noc without qos_offset, compatibles snoc/cnoc (match DTS).
- `sm6375.c` — last generated driver (full + get_bw). Compiles clean.
- Patches (in the pmaports dir, currently OUT of source=): 0027 (driver+binding+
  Kconfig/Makefile), 0028 (6 NoC DT nodes), 0029 (IPA interconnects + okay +
  point compatible to ipa_data_v4_11), 0030 (DIAG: ap_owned=false).
- To re-enable the interconnect experiment: add 0027/0028 (and optionally 0029)
  back to APKBUILD source= + sha512sums, re-add CONFIG_INTERCONNECT_QCOM_SM6375=y
  to the config (+ update its sha), rebuild.

--------------------------------------------------------------------------------
## 7. RM: the modem itself already works
--------------------------------------------------------------------------------
Modem/ADSP/CDSP remoteprocs boot on v47 (dmesg: "remoteproc0: Booting fw image
qcom/sm6375/motorola/rhodep/modem.mbn"). QRTR/rmtfs/pd-mapper/tqftpserv run
(rhodep-modem-support package). So the modem is up over QRTR; what's missing for
DATA is the IPA datapath (Blocker B) — voice/SMS may be a separate ModemManager/
UCM-audio task. Phone apps (mobian-phosh-phone: Calls/Contacts/Chatty) are
installed and appear in the drawer; they'll be usable once the modem does data/
voice.

--------------------------------------------------------------------------------
## 8. DO NOT
--------------------------------------------------------------------------------
- Do NOT `dpkg -i` the rhodep3 linux-image .deb (it's the hanging v48 kernel).
  It was moved to img/_diag-interconnect-NO-USAR/.
- Do NOT ship v51..v62 images.
- Do NOT commit/push the interconnect WIP changes yet (user asked to hold).
  The rtl8188eus + headers + phone-apps work IS already committed/pushed and is
  good; only the interconnect/IPA experiments are uncommitted.
