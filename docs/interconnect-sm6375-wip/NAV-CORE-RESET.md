# The NAV core reset: what it needs from the application processor

**Answer, up front: on this chip the application processor is not on the NAV
core's reset path at all. Everything the sequence needs — the GDSC, the PLL, the
XO branch, the RTC, the STMR, the per-block clocks, the interrupts, the SNOC
master and the memory-protection unit on the far side of it — is named only
inside the MPSS address space or inside the secure world. The single AP-side
resource on the whole path is the XO proxy vote during modem bring-up, and this
port already makes it, at parity with stock, dropped at the same instant by both
kernels. This branch is closed. The remaining work is in the modem image and the
secure world.**

Nothing here is a fix and nothing here saw a satellite. No register was read; no
file was written on the device; the phone was alive at the end (`up 30 min`,
`bootreason` unchanged, `rhodep-gnss.service` untouched).

Scope: this document answers only the question in its title. It does not re-open
anything closed in [`GNSS-SM6375.md`](GNSS-SM6375.md),
[`POWER-DELIVERY.md`](POWER-DELIVERY.md) or
[`../watchdog-ipa-lte-wip/HANDOFF.md`](../watchdog-ipa-lte-wip/HANDOFF.md).
Where a finding here overlaps one of those, it is marked **[already covered]**
and the earlier reference is given.

---

## Result in one table

| # | What the NAV-core reset needs | Where it lives | Does stock's AP supply it? | Does this port? | Testable without a flash | Likelihood this is the bug |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `nav_gdsc`, `nav_cc_pll4_pll`, `nav_cc_xo_src_clk`, `nav_cc_stmr_xo_clk`, `nav_cc_bb_core/cp/dp/dma/snoc/qlink` clocks | `MSS_NAV_PWRCTRL_BASE`, `MSS_NAV_CC_NAV_CC_REG_BASE`, `MSS_NAV_BB_BASE` — all inside the MPSS aperture | **no** | no | n/a | **nil.** Parity |
| 2 | `gcc_nav_snoc_axi_clk`, `gcc_nav_mbist_clk` — the only two names in GCC | GCC, register hole 0x15000 region | **no** — `gcc-blair.c` and `gcc-sm6375.c` are byte-identical, 183 clocks, 20 resets, zero NAV | no | yes (`clk_summary`, done below) | **nil for the AP.** See §1.4 for who does own them |
| 3 | The GNSS RTC and the MSS STMR running | `MSS_STMR`, NAV RTC inside the NAV core | **no** | no | n/a | **nil.** Parity |
| 4 | XO (19.2 MHz) held while the modem boots | RPM resource, voted by the AP as a proxy | yes, `qcom,proxy-clock-names = "xo"` | yes, `clock-names = "xo"`, `clk_prepare_enable(pas->xo)` | yes | **nil.** Parity, and both drop it at the same interrupt |
| 5 | An architected-timer counter the modem can agree with | CNTFRQ, QTIMER frames | stock writes `clock-frequency = <19200000>` on both timer nodes | writes neither; relies on CNTFRQ_EL0 | yes — **measured 19.20 MHz, and the mmio counter agrees** | **very low.** A real quotable difference with a measured negative |
| 6 | `ICBID_MASTER_MSS_NAV` / `ICBID_SLAVE_MSS_NAV_CE_MPU_CFG` described to an AP-side NoC provider | vendor `holi.c` and port `sm6375.c` | **no — neither node exists in the vendor's own provider** | no | yes (`interconnect_summary`, done below) | **nil.** The ICBIDs come from a generic all-SoCs header |
| 7 | The `NAV_CE` MPU programmed | NoC memory-protection unit | **no AP-side driver in either kernel writes any NoC MPU/XPU** | no | n/a | **nil.** Secure-world owned; branch closed, §3 |
| 8 | A QMI Time-service (service 22) client on the AP | the modem is the **server**; Android's `time_daemon` is the client | yes (`time_daemon`) | **no** | yes, cheaply | **low**, but it is the only genuine AP-side absence found. §2.5 |

Two things in that table change what is worth doing next, and they are stated
in full below:

* **The premise the brief was written on needs a correction.** `GNSS RTC not
  running. Not resetting NAV core` does not sit in a receiver bring-up path and
  "GNSS RTC" is not UTC time. Both strings that mention it sit in the correlator
  controller's **critical-error-recovery** and **clock-mode-change** paths, and
  the RTC in question is a free-running hardware counter inside the NAV core.
  §1.3.
* **The `gcc_nav_*` conclusion recorded in `GNSS-SM6375.md` — "this generation
  moved them behind the secure image-loading boundary" — is not the best-supported
  reading.** The better-supported one is that the MPSS programs them itself,
  through its own mapping of `GCC_CLK_CTL_REG`. Either way the AP is not the
  owner, so the branch closes; but the difference matters for where to look next.
  §1.4.

---

## 1. The firmware's own account

### 1.1 Provenance

	QC_IMAGE_VERSION_STRING=MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
	$Id: //service/MPSS/MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452/
	     modem_proc/gps/gnss/diag/src/aries_gpsdiag.c#1 $

Method: `strings -n 4` over all 34 `/readonly/firmware/image/modem.b*` segments
on the device, each line tagged with its segment, 676 933 lines / 13 MB, copied
off and searched offline. Read-only; nothing was written; the scratch file was
removed from `/tmp` afterwards. **All 29 hits for `nav_cc|NAV_CE_MPU_CFG|GNSS
RTC|navbb_|me_NavHwStop|MSS_NAV` are in one segment, `modem.b21`**
(`1794971a5ef5a073df8afd07ac7fbadd`, 13 833 716 bytes). The GNSS island/uImage
strings are in `modem.b07` (`884c64be4e987ac687f8e465f8602532`).

### 1.2 The harvest, grouped, verbatim

**(a) The hardware base-address table.** These are entries in the modem's own
HWIO base-name list. Note that every NAV entry is prefixed `MSS_`:

	MSS_CC_MSS_CC_REG
	MSS_CONF_BUS_TIMEOUT
	MSS_STMR
	MSS_MSS_RSCC
	MSS_RSCC_RSCC_RSC
	MSS_RFC
	MSS_PDMEM
	MSS_RFC_SWI
	MSS_RFC_SWI_2
	MSS_NAV
	MSS_NAV_PWRCTRL_BASE
	MSS_NAV_CC_NAV_CC_REG_BASE
	MSS_NAV_BB_BASE
	MSS_QDSP6V67SS_PUB
	MSS_QDSP6SS_PLL_LUCID_BLK
	MSS_QDSP6SS_QDSP6SS_QTMR_AC
	MSS_QDSP6SS_QTMR_F0_0
	MSS_QDSP6SS_QTMR_F1_1
	MSS_QDSP6SS_QTMR_F2_2

and, elsewhere in the same table and load-bearing for §1.4:

	TCSR_TCSR_REGS
	GCC_CLK_CTL_REG
	GCC_RPU_RPU32Q2N7S1V0_200_CL36L12
	SECURITY_CONTROL_CORE
	RPM_SS_MSG_RAM_START_ADDRESS
	NAV_CE_MPU_CFG
	SNOC_MSS_NAV_CE_MS_MPU_NAV_CE_MPU_CFG

with the mapping machinery next to it:

	BaseMap
	AlwaysMap
	HWIO: Unable to find "%s"
	HWIO: Unable to find address "0x%08x"
	HWIO mapping failed: %s @ 0x%08x
	HWIO mapping already exists: %s @ 0x%08x

**(b) The clock controller.** From the modem's ClockDAL name table, in file
order, with the surrounding names kept so the interleaving is visible:

	nav_cc_qlbr_rx_dbg_clk
	nav_cc_snoc_clk
	nav_cc_snoc_dbg_clk
	gcc_boot_rom_ahb_clk
	gcc_mss_q6_msmpu_cfg_ahb_clk
	gcc_prng_ahb_clk
	mss_cc_axi_offline_clk
	gcc_nav_mbist_clk
	gcc_wcss_tsctr_clk
	...
	gcc_sec_ctrl_clk_src
	gcc_wcss_timeout_clk
	gcc_nav_snoc_axi_clk
	gcc_wcss_ahb_s0_clk
	...
	mss_cc_bus_mgpi_clk
	mss_cc_bus_nav_clk
	mss_cc_bus_offline_clk
	...
	mss_cc_bus_stmr_clk
	...
	nav_cc_bb_core_clk
	nav_cc_bb_core_dbg_clk
	nav_cc_cp_clk
	nav_cc_dma_ahb2axi_clk
	nav_cc_dma_clk
	nav_cc_dp_clk

immediately followed by the *sources and domains* the same driver owns:

	/xo/cxo
	mss_cc
	nav_cc
	gpll0
	gpll6
	mpss_pll
	qdsp6ss_pll
	mss_cc_q6
	nav_cc_pll4_pll
	nav_gdsc
	/node/clk/generic
	mss_cc_vq6

and the driver's own diagnostics:

	Cannot turn on source[%s] that hasn't been configured.
	Source[%s] requires calibration, but no calibration config.
	Failed to calibrate source[%s].
	Unable to enable source[%s] vote
	Unable to enable source[%s]
	Clock_InitTarget failed.
	Clock_InitNPA failed.
	Unable to signal that the clock driver is done initial votes.

**Two names are new to this repository and both matter:** `nav_gdsc` (a power
domain, not a clock) and `mss_cc_bus_nav_clk` (the MSS_CC-side bus clock to the
NAV block, distinct from `nav_cc_snoc_clk` and from `gcc_nav_snoc_axi_clk`).
`nav_cc_bb_core_dbg_clk` is also new; the list previously recorded in
`GNSS-SM6375.md` had twelve `nav_cc_*` names, the real list has thirteen.

**(c) The NAV baseband clock driver, and a register-level failure report.**
Grouped as they appear, around `navbb_clkdrv`:

	/GNSS/ME/%s
	Md %u Fifo 0x%x RTC 0x%x STMR 0x%x SpCtl 0x%x GDSC 0x%x Retry %d %d
	NavBB_ULog
	NavBB_ECOULog
	RtcMs %d CSStat 0x%x Cnt %d %d %d Tot %d Avg %d CSDiff %d ElpsdMs %d
	RSTRZN Fifo 0x%x RTC 0x%x STMR 0x%x Cfg 0x%x Addr 0x%x St 0x%x ErrTs 0x%x Cntl 0x%x CSSt 0x%x
	navbb_clkdrv
	Not able to set Nav BB main clock to %u clk mode

	Unsupported clock mode %d. Default to 201.6 MHz
	DP: NAV clock = %dMHz
	DP: NAV clock is invalid. Cycle count = %d

	TASK: NAVBB SM Zone Rstr En %d Hi %d Lo %d
	TASK: NAVBB SM Zone Rstr FAILED. En %d Hi %d Lo %d

The `Md ... GDSC 0x%x Retry` line is the clearest single statement in the whole
harvest: a mode change in the NAV baseband clock driver logs, together, the
**FIFO, the RTC, the STMR, a "SpCtl" and the GDSC**, and it retries. The
`RSTRZN` line (restoration) logs the same set plus config, address, status,
error-timestamp and control registers. These are the registers the NAV core's
power/clock sequence walks.

**(d) The NAV hardware enable/disable sequence.** Verbatim, in file order, which
is the order of a stop sequence read bottom-up:

	HW: CC uCodes loaded POR 0x%x LoadUc 0x%x
	cc_hwconfig.c
	NavRX module ON failed
	Clocks are not enabled
	Failed to enable Nav SDD HW. Clock Status: Flag %d, BCR 0x%x, QDSCR 0x%x, PllMode 0x%x, NavClkStat 0x%x
	SAS: enter navbb_QftOff
	SAS: enter navsdd_HwDisable
	SAS: enter navbb_PrintQlinkIrdDbg
	SAS: enter navrx_Off
	NavRX module OFF failed
	SAS: enter navbb_PrintQlinkIrdDbg again
	SAS: enter navbb_PowerdownSeq
	SAS: enter navbb_DisablePerClksForNavCoreAccess
	SAS: enter cc_SendTtrData
	SAS: exit me_NavHwStop

`me_NavHwStop` names the measurement engine (`me_`) stopping the NAV hardware.
`navbb_DisablePerClksForNavCoreAccess` is the counterpart of an enable step that
switches on *peripheral clocks in order to reach the NAV core's registers at
all* — i.e. the NAV core's configuration space is behind a clock gate. And
`Failed to enable Nav SDD HW` reports, on failure, exactly five things: a flag,
a **BCR** (block control register — the Qualcomm name for a reset control), a
QDSCR, a **PllMode**, and a **NavClkStat**.

**(e) The reset itself, in context.** From `cc_slicer.c`:

	SLICER: Job Flush completed GNSS 0x%x Band 0x%x DedRscRel %d
	Resetting Nav Core for recovery
	GNSS RTC not running. Not resetting NAV core
	CC initiating critical error recovery. Pending CP intr %d DP intr %d
	CC recovery timer fired in non error state

and from `cc_task.c`, a second site with the same guard:

	TASK: CC Clk Config Req: %d Cur: %d RTCOn: %d
	CLK mode not compatible (New %d, Old %d) or GNSS RTC not running (%d)
	TASK: Failed to configure BB clk to: %d

and, in the same file, the reset entry points:

	HARD RESET triggered by MC
	HARD RESET initiated while CC is DISABLED
	HARD RESET initiated while CC is in error recovery
	HARD RESET initiated while CC in state :%d
	Error recovery initiated while CC in state %d
	Resetting CC with a delay

and what *asks* for the reset, from the NavRX layer:

	NavRX boot-up calibration attempt failed
	NavRX in uncalibrated state
	NavRX ADC cal is failded. Request CC reset!
	NavRX ADC cal is failed. Request CC reset!
	NavRX BP ampl check failed %d times. Request CC reset!
	NavRx ADC cal failed. Ignore error after NavRxErr %d
	NavRX BP ampl check failed %d times. Ignore after NavRxErr %d
	Failure to send NavRx bootup Cal msg.
	NavRX XO compensation processing failed
	nav_AdcPeriodicOnCalibrated: Out of cal (abs(%f/%f) > %f mV)
	nav_AdcPeriodicOnUncalibrated: ADC %u Mean I/Q > Vref (abs(%f/%f) > %d mV)

**(f) The link to the RF device.** The correlator reaches the transceiver over
QLINK, and QLINK has its own error interrupt into the correlator:

	C_GNSS_CC_QLINK_ERR_IRQ: RC_IRQ 0x%08X
	Failed to process C_GNSS_CC_QLINK_ERR_IRQ
	cc_ProcessQlinkErrorIRQ: RC_IRQ 0x%08X
	Qlink CSR WDOG timeout in command process
	Qlink(%d) detected CSR timeout at 0x%x,QLink0ErrCode=0x%x,QLink1ErrCode=0x%x
	Qlink mK reports critical error: 0X%x at state: 0X%x
	Qlink(%d) RF stuck in start state: %d at 0x%x
	Qlink(%d) Shutdown finalize timeout
	Qlink(%d)stmr out of sync detected, delta: 0X%xXO cnt
	RF stuck in QLINK(%d) enter_island_mode for 3000us
	RF stuck in QLINK(%d) exit_island_mode for 3000us
	QLSS0_NAV_LANE_SLICE_RIF_TOP_RX_NAV
	QLSS1_NAV_LANE_SLICE_RIF_TOP_RX_NAV

**(g) Island / uImage.** The GNSS engine has a micro-image low-power mode with
its own memory pool and its own low-power resource:

	power_island.GNSS
	GPS_ISLAND_POOL
	cc_islandstm_uimage.c
	cc_IslandStmInitialize: Registered CB with MGP: %p
	UIMG: CC_STATE_CHANGE to ISLAND
	UIMG: STATE_CHANGE to CC_READY_FOR_ISLAND_MODE
	C_ISLAND_STATE to ISLAND IlWaitRTC %d mgpInLS %d
	FATAL: LARGE ISLAND ENTRY LATENCY DETECTED
	FATAL: VERY LARGE ISLAND EXIT LATENCY DETECTED (Exit start: 0x%llx)
	Qlink(%d) HW enter island mode failed

with an LPR/LPI mode list that is otherwise entirely the modem's own sleep
framework, including one more NAV name:

	l2.ret + tcm.ret + cpu_config.apcr_pll_lpm + wakeup.qtimer + cpu_vdd.apcr_pll_lpm
	...
	bcr_hm
	crypto_nav
	power_debug

**(h) XO, calibration, sleep-clock trim.** Every one of these is a modem-internal
NV item or a modem-internal driver:

	/nv/item_files/mcs/tcxomgr/xo_factory_cal_data
	/nv/item_files/mcs/tcxomgr/field_cal_params
	/nv/item_files/mcs/tcxomgr/factory_cal_version
	XOCAL SEQ - %d
	26ftm_calv3_xo_cal_seq_class
	pm_dev_mega_xo_get_xo_trim
	pm_dev_mega_xo_set_xo_trim
	XO_SHUTDOWN_RSRC
	XO_THERM_GPS   XO_THERM_GPS_LOW   XO_THERM_GPS_MED   XO_THERM_GPS_HIGH
	/modem/xo/cxo
	/node/xo/cxo
	slpc.c   slpc_init   slpc_trim   slpc_xo_trim.c   slpc_l2vic_wake_irq
	slpc_worker_0   slpc_worker_1   slpc_worker_2
	/nv/item_files/mcs/slpc/slpc_trim
	sleep_slpc_mode
	nr5g_ml1_sleepmgr_slpc.c

`slpc` is the modem's sleep-clock calibration against XO, entirely inside the
MPSS, with its own L2VIC wake interrupt. **There is nothing for the AP to
provide here.** No `warmup` string anywhere in the image belongs to GNSS — every
hit is LTE/NR (`rf_warmup_usec_conn_fdd`, `nr5g_ml1_sleepmgr_warmup.c`).

**(i) Interrupts.** From the modem's L2VIC interrupt-name table:

	stmr_evt19 ... stmr_evt31
	q6_wdog_irq
	vq6ss_wdog_irq
	modem_offline_irq[255:0]
	modem_offline_l2_irq[55:0]
	nav_irq
	nav_dm_irq
	reserved[451:450]
	mgpi_irq
	mpss_ahb_access_err_irq
	config_timeout_slave_irq

**`nav_irq` and `nav_dm_irq` are entries in the modem's own L2VIC, not GIC SPIs.**
The NAV core does not interrupt the application processor.

**(j) Calibration and configuration files.** All EFS paths, all served over the
modem's own filesystem, none of which the AP is asked for
(`tqftpserv` measured silent through a whole session — **[already covered]**,
`GNSS-SM6375.md`, "Ruled out on the GNSS reproducer"):

	/GNSS/ME/CGPSFreqBias    /GNSS/ME/CGPSGnssGGBiasEsti    /GNSS/ME/CGPSGnssTGPSEsti
	/GNSS/ME/CGPSGnssMBCapSvLists   /GNSS/ME/CGPSSbasSearchList   /GNSS/ME/EnergyTimers
	/GNSS/ME/mcc_bsa.txt     /GNSS/PE/AlmFile    /GNSS/PE/GloAlmFile
	/GNSS/TLE/nv_items.bin   /GNSS/WWANME/GTS/gtspmic.bin
	/GNSS/CFG/LBS/  /GNSS/CFG/NF/  /GNSS/CFG/SDP/
	gpsoffsets.bin           /cgps/nv/item_files/%s

### 1.3 The sequence these strings imply — and a correction to the brief

Read together, the NAV bring-up the firmware describes is:

1. **Power and clock the NAV core.** `nav_gdsc` on; `nav_cc_pll4_pll` configured
   and calibrated (`Source[%s] requires calibration`); `nav_cc_xo_src_clk` and
   `nav_cc_stmr_xo_clk` from XO; `mss_cc_bus_nav_clk` and `nav_cc_snoc_clk` for
   the bus; and — crucially — a set of *peripheral* clocks that must be on
   merely to reach the NAV core's registers
   (`navbb_DisablePerClksForNavCoreAccess` is the teardown of that step).
2. **Release the NAV core from reset**, via the `BCR` the failure line reports
   alongside `PllMode` and `NavClkStat`.
3. **The RTC and the STMR start.** From this point `RTC 0x%x STMR 0x%x` are
   readable and non-zero, and the correlator's whole timebase (`CurRTC`,
   `StartRTC`, `RtcMs`, `TM_RTC`, `EnRtc`) is derived from them.
4. **Bring up NavRX and QLINK to the transceiver**, run the ADC bootup
   calibration, check the band-pass amplitude.
5. If any of that fails: `NavRX ADC cal is failed. Request CC reset!` →
   `CC initiating critical error recovery` → `Resetting Nav Core for recovery`,
   **unless** the RTC is not running, in which case
   `GNSS RTC not running. Not resetting NAV core`.

**This is not the reading the brief was written on, and the difference is
material.** The brief takes `GNSS RTC not running. Not resetting NAV core` to
mean "with no valid *time* the firmware skips the NAV core reset, and with valid
time it performs it". On the evidence:

* the string is emitted from `cc_slicer.c`, in the **critical-error-recovery**
  path, three lines from `CC initiating critical error recovery`, not from any
  session-start path;
* "GNSS RTC" is used everywhere else in this firmware as a **free-running
  hardware counter** — `SlotLoad RTC: %d`, `CP: CurRTC %d StartRTC %d`,
  `TimeMark(%d) - JobId ..., ToaRTCMs %d`, `LS250 EnRtc 0x%x CurrRtc 0x%x`,
  `Md %u Fifo 0x%x RTC 0x%x STMR 0x%x` — never as UTC or as an injected
  wall-clock;
* the second occurrence, `TASK: CC Clk Config Req: %d Cur: %d RTCOn: %d` /
  `CLK mode not compatible (New %d, Old %d) or GNSS RTC not running (%d)`, is a
  guard on a **baseband clock-mode change**. Wall-clock time has no bearing on
  whether a clock mode may be changed; a running hardware timebase does.

So the literal content of the string is: *if the NAV core's own RTC is not
ticking — i.e. the core is not powered and clocked — do not try to take it
through a reset.*

**That does not overturn the two-sided switch; it relocates it.** The measured
result stands exactly as recorded: injecting UTC turns `powerMode 3` from a
300 s survivor into a ~100 ms reset, and `powerMode 0/1/2` reset with no time
anywhere on the boot. The most economical way to reconcile the two is that the
engine will not start the NAV *hardware* at all without time in the background
modes, so with no time the RTC never runs, the correlator never runs, and the
recovery path is never reachable. The reset then belongs to steps 1–4 above —
bringing the NAV core up and running NavRX — with step 5 as a possible
consequence rather than the cause.

**None of steps 1–5 names an application-processor resource.** Every base is
`MSS_NAV_*`; every clock is `nav_cc_*`, `mss_cc_*` or the two `gcc_nav_*` in
§1.4; every interrupt is in the modem's L2VIC; the RF link is QLINK from the
MPSS; the calibration data is in the modem's own EFS.

### 1.4 Re-examining `gcc_nav_snoc_axi_clk` and `gcc_nav_mbist_clk`

The earlier conclusion in `GNSS-SM6375.md` — *"this generation moved them behind
the secure image-loading boundary"* — was reached from a true premise. The
premise is now confirmed harder than it was:

	vendor gcc-blair.c clocks: 183      mainline gcc-sm6375.c clocks: 183
	vendor gcc-blair.c resets:  20      mainline gcc-sm6375.c resets:  20
	vendor-only:   []       mainline-only:   []
	reset vendor-only: []   reset mainline-only: []
	NAV entries in either: []

(computed by parsing `gcc_blair_clocks[]`/`gcc_blair_resets[]` against
`gcc_sm6375_clocks[]`/`gcc_sm6375_resets[]`. Note in passing that the count is
**183, not 166**; `GNSS-SM6375.md` and `backups/vendor-dt-rhodep-20260906/README.md`
both say 166. The conclusion those documents draw is unaffected — the two sets
are still identical and still contain no NAV clock — but the number should be
corrected when either file is next touched. `gcc-blair.c` is byte-identical
between the `sixteen` and `lineage-22.1` branches; so is `holi.c`.)

The *inference* is where it should be revised. Three pieces of evidence point at
the MPSS rather than at TrustZone:

1. **The modem maps GCC.** `GCC_CLK_CTL_REG` is an entry in the modem's own
   HWIO base-name table (§1.2a), next to `TCSR_TCSR_REGS`,
   `SECURITY_CONTROL_CORE` and `RPM_SS_MSG_RAM_START_ADDRESS`, and the modem has
   the mapping machinery to use it (`BaseMap`, `AlwaysMap`, `HWIO mapping
   failed: %s @ 0x%08x`).
2. **The two names sit inside the modem's ClockDAL table**, interleaved with
   `gcc_mss_q6_msmpu_cfg_ahb_clk`, `gcc_boot_rom_ahb_clk`, `gcc_wcss_*` and the
   `mss_cc_*`/`nav_cc_*` families the MPSS unambiguously owns (§1.2b) — and that
   table is followed immediately by `nav_gdsc` and `nav_cc_pll4_pll`, which
   nothing outside the MPSS could plausibly be enumerating.
3. **The AP GCC drivers leave a hole exactly where a `GCC_NAV_*` block would
   sit.** In the vendor tree, `techpack/dataipa/.../ipa4.1/ipa_gcc_hwio.h` (a
   different SoC's GCC dump, so this is suggestive and not proof) defines

		#define HWIO_GCC_NAV_BCR_ADDR      (GCC_CLK_CTL_REG_REG_BASE + 0x00015000)
		#define HWIO_GCC_NAV_AXI_CBCR_ADDR (GCC_CLK_CTL_REG_REG_BASE + 0x00015004)

   and `gcc-sm6375.c` has **no register anywhere between 0x10000 and 0x16fff** —
   its lowest offset above 0x0ffff is 0x17000. So on the AP side that address
   range is unclaimed, in both kernels.

The counter-argument, which has to be stated: the same ClockDAL table also
contains `gcc_qupv3_wrap0_s0_clk` … `gcc_qupv3_wrap1_s5_clk`, and the modem
plainly does not own the serial engines. A name in a shared DAL table is not
proof of ownership. So:

* **Proved:** the application processor does not own `gcc_nav_snoc_axi_clk` or
  `gcc_nav_mbist_clk` on either side. Stock takes a satellite fix without them.
* **Supported, not proved:** the MPSS programs them itself through
  `GCC_CLK_CTL_REG`.
* **Not excluded:** TrustZone does it during PAS authentication.

**Either way the branch is closed for this port**, and adding NAV clocks to
`gcc-sm6375.c` would be inventing hardware description that Qualcomm's own
`gcc-blair.c` does not have. Do not do it.

---

## 2. The timekeeping path

### 2.1 `sleep_clk` and `xo_board`

Stock, `backups/vendor-dt-rhodep-20260906/merged.dts` lines 5397–5414 (the
recovered tree, base DTB + overlay 0; read that directory's `README.md` first —
it is git-ignored, is Motorola's material, and the rhodep overlays touch nothing
relevant here, so the base is authoritative):

	clocks {

		xo-board {
			compatible = "fixed-clock";
			clock-frequency = <0x249f000>;
			clock-output-names = "xo_board";
			#clock-cells = <0x00>;
			phandle = <0x211>;
		};

		sleep-clk {
			compatible = "fixed-clock";
			clock-frequency = <0x7ffc>;
			clock-output-names = "chip_sleep_clk";
			#clock-cells = <0x00>;
			phandle = <0x62>;
		};
	};

The vendor kernel source agrees, `blair.dtsi:1191` (`sixteen`; `lineage-22.1` is
the same at line 1197):

	clocks {
		xo_board: xo-board {
			compatible = "fixed-clock";
			clock-frequency = <38400000>;
			clock-output-names = "xo_board";
			#clock-cells = <0>;
		};

		sleep_clk: sleep-clk {
			compatible = "fixed-clock";
			clock-frequency = <32764>;
			clock-output-names = "chip_sleep_clk";
			#clock-cells = <0>;
		};
	};

This port, `live-mainline.dts` — i.e. the tree the running kernel holds:

		sleep-clk {
			#clock-cells = <0x00>;
			clock-frequency = <0x7ffc>;
			compatible = "fixed-clock";
			phandle = <0x33>;
			name = "sleep-clk";
		};

		xo-board-clk {
			#clock-cells = <0x00>;
			clock-frequency = <0x124f800>;
			compatible = "fixed-clock";
			phandle = <0x30>;
			name = "xo-board-clk";
		};

confirmed live from `/proc/device-tree/clocks/`:

	-- /proc/device-tree/clocks/sleep-clk/
	   clock-frequency = 00007ffc            <- 32764
	-- /proc/device-tree/clocks/xo-board-clk/
	   clock-frequency = 0124f800            <- 19200000

The frequency comes from `sm6375-motorola-rhodep.dts:559` (patch 0001):

	&xo_board_clk {
		clock-frequency = <19200000>;
	};

**Findings.**

* `sleep_clk` is **32764 Hz on both sides, exactly.** No discrepancy.
* `xo_board` is **38 400 000 on stock and 19 200 000 here**, and that reads like
  a factor-of-two error until you check the consumers. **Stock's `xo-board` node
  has no consumer at all**: `phandle = <0x211>` occurs exactly once in
  `merged.dts`, at its own definition. Downstream's GCC takes XO from the RPM
  clock controller instead —

		clock-controller@1400000 {
			compatible = "qcom,blair-gcc\0syscon";
			clocks = <0x5f 0x00 0x5f 0x01 0x62>;
			clock-names = "bi_tcxo\0bi_tcxo_ao\0sleep_clk";
		};

  where `0x5f` is `qcom,rpmcc-holi` and `0x62` is `sleep-clk`. Mainline wires the
  same three the same way (`sm6375.dtsi:951`), differing only in that its
  `rpmcc` takes `<&xo_board_clk>` as its `xo` input, which is where the 19.2 MHz
  actually lands. **19.2 MHz is the right number**: the AP's own counter runs at
  19.20 MHz (§2.3), stock's two timer nodes both declare `19200000`, and the
  modem asserts its QTIMER frame frequency against `FW_TIME_XO_CLK_HZ`.
  Stock's 38.4 MHz is an unconsumed board-level declaration and is inert. This
  is a difference; it is not a gap.

### 2.2 The architected timer nodes

Stock, `merged.dts:3689` (identical to `blair.dtsi:508`, quoted from source for
the macro expansion):

	timer {
		compatible = "arm,armv8-timer";
		interrupts = <0x01 0x01 0xff08 0x01 0x02 0xff08 0x01 0x03 0xff08 0x01 0x00 0xff08>;
		clock-frequency = <0x124f800>;
	};

	timer@f420000 {
		#address-cells = <0x01>;
		#size-cells = <0x01>;
		ranges;
		compatible = "arm,armv7-timer-mem";
		reg = <0xf420000 0x1000>;
		clock-frequency = <0x124f800>;

		frame@f421000 {
			frame-number = <0x00>;
			interrupts = <0x00 0x08 0x04 0x00 0x06 0x04>;
			reg = <0xf421000 0x1000 0xf422000 0x1000>;
		};
		frame@f423000 { ... status = "disabled"; };   /* frames 1..6 all disabled */
	};

	arch_timer: timer {                    /* blair.dtsi:508, unexpanded */
		compatible = "arm,armv8-timer";
		interrupts = <GIC_PPI 1 (GIC_CPU_MASK_SIMPLE(8) | IRQ_TYPE_LEVEL_LOW)>,
			     <GIC_PPI 2 (GIC_CPU_MASK_SIMPLE(8) | IRQ_TYPE_LEVEL_LOW)>,
			     <GIC_PPI 3 (GIC_CPU_MASK_SIMPLE(8) | IRQ_TYPE_LEVEL_LOW)>,
			     <GIC_PPI 0 (GIC_CPU_MASK_SIMPLE(8) | IRQ_TYPE_LEVEL_LOW)>;
		clock-frequency = <19200000>;
	};

This port, `sm6375.dtsi:2513` and `sm6375.dtsi:1806`:

	timer {
		compatible = "arm,armv8-timer";
		interrupts = <GIC_PPI 1 IRQ_TYPE_LEVEL_LOW>,
			     <GIC_PPI 2 IRQ_TYPE_LEVEL_LOW>,
			     <GIC_PPI 3 IRQ_TYPE_LEVEL_LOW>,
			     <GIC_PPI 0 IRQ_TYPE_LEVEL_LOW>;
	};

	timer@f420000 {
		compatible = "arm,armv7-timer-mem";
		reg = <0 0x0f420000 0 0x1000>;
		ranges = <0 0 0 0x20000000>;
		#address-cells = <1>;
		#size-cells = <1>;

		frame@f421000 {
			reg = <0x0f421000 0x1000>, <0x0f422000 0x1000>;
			interrupts = <GIC_SPI 8 IRQ_TYPE_LEVEL_HIGH>,
				     <GIC_SPI 6 IRQ_TYPE_LEVEL_HIGH>;
			frame-number = <0>;
		};
		/* frames 1..6 identical to stock, all status = "disabled" */
	};

live-confirmed from `/proc/device-tree`:

	=== /proc/device-tree/timer
	   compatible = "arm,armv8-timer"
	   interrupts = 00000001 00000001 00000008  00000001 00000002 00000008
	                00000001 00000003 00000008  00000001 00000000 00000008
	=== /proc/device-tree/soc@0/timer@f420000
	   compatible = "arm,armv7-timer-mem"
	   ranges = 00000000 00000000 00000000 20000000
	   reg    = 00000000 0f420000 00000000 00001000

**Findings, in order of how much they could matter.**

1. **The memory-mapped architected timer is present on both sides and is live
   here.** Same base, same seven frames, same enable pattern, same interrupts
   (GIC SPI 8 and 6 on frame 0). It is not merely declared — it is bound and
   being used as the broadcast timer:

		30:  14185  9679  7935  7233  6120  5517  5176  6327  GICv3  38 Level  arch_mem_timer
		00000000-00000000 : arch_mem_timer     (/proc/iomem, 0x0f421000-0x0f421fff)
		[0.417565] clocksource: arch_mmio_counter: mask: 0xffffffffffffff ...

   **There is no missing `qtimer` node.** This was the most plausible-looking
   item in the brief and it is not there.

2. **`clock-frequency` is the one real difference, and it costs nothing.**
   Stock writes `19200000` on both timer nodes; mainline writes it on neither
   and relies on `CNTFRQ_EL0` from the boot chain, which is what
   `arm,cpu-registers-not-fw-configured` exists to override when the firmware
   gets it wrong. Measured on the running device:

		[0.000000] arch_timer: cp15 timer running at 19.20MHz (virt).
		[0.000000] clocksource: arch_sys_counter: mask: 0xffffffffffffff
		                        max_cycles: 0x46d987e47, max_idle_ns: 440795202767 ns
		[0.000000] sched_clock: 56 bits at 19MHz, resolution 52ns
		[0.417565] clocksource: arch_mmio_counter: mask: 0xffffffffffffff
		                        max_cycles: 0x46d987e47, max_idle_ns: 440795202767 ns

   The cp15 counter and the memory-mapped counter report **identical
   `max_cycles`**, so both frames read the same 19.2 MHz — the firmware
   programmed CNTFRQ and CNTFID0 correctly and the missing DT property changes
   nothing. Adding `clock-frequency = <19200000>` to both nodes would be a
   correct hardening change and would not alter behaviour.

3. **The `0xff08` versus `0x08` interrupt flag is cosmetic.** The upper byte is
   `GIC_CPU_MASK_SIMPLE(8)`, a GICv2 legacy field. This SoC has a GICv3
   (`GICD`/`GICR` in `/proc/iomem`, `GICv3` in `/proc/interrupts`), where the
   mask is ignored. The four PPIs are the same four with the same trigger.

4. **Neither side declares `always-on`, `arm,no-tick-in-suspend` or
   `arm,cpu-registers-not-fw-configured`.** Parity.

### 2.3 Does the modem agree about the counter?

The modem does not use the AP's frames. Its own are in its address space —
`MSS_QDSP6SS_QDSP6SS_QTMR_AC`, `MSS_QDSP6SS_QTMR_F0_0`, `_F1_1`, `_F2_2`, and the
same set again for the VQ6 — and it checks their programming itself:

	Assertion (HWIO_IN(MSS_QDSP6SS_QTMR_V1_CNTFRQ_0) == FW_TIME_XO_CLK_HZ) failed

That assertion would fire if the shared counter had been set up at anything
other than the XO rate. It has not fired: the modem boots, registers on GSM,
carries data and runs a `cellid` LOC session for as long as you like.

The modem also has its own timetick plumbing, all of it internal —
`timetick_sclk64.c`, `tms_get_timetick`, `hw_timer_curr_timetick`,
`qurt_timer_timetick_to_us`, `utimetick_init` — plus `MSS_STMR` and the 32
`stmr_evt*` L2VIC lines. **The AP is not in any of it.** The vendor kernel has
no `qtimer` node, no `timetick` driver and nothing named `slpc` (§4).

So the brief's "if the two disagree about the counter, a receiver that has just
been given absolute time and then compares it against that shared counter would
be in an unusual state" is a sound hypothesis with a measured negative: the two
agree, at 19.2 MHz, on both the cp15 and the memory-mapped path.

### 2.4 Sleep counter, MPM

`sleep_clk` feeds the RPM/MPM wake path. The port's MPM window into RPM message
RAM was checked address-for-address against the vendor and is right —
**[already covered]**, `HANDOFF.md`, "The MPM window into RPM message RAM". It is
bound and live here:

	15:  0  0  0  0  0  0  0  0  GICv3  229 Edge  qcom_mpm
	108: ... mpm  94 Edge  dp_hs_phy_irq
	110: ... mpm  12 Level ss_phy_irq
	045f0000-045f6fff : 45f0000.sram sram@45f0000

`sleep-clk` itself is enabled with one consumer:

	 sleep-clk    1  1  0  32764  0  0  50000  Y  f410000.watchdog

### 2.5 The one genuine AP-side absence: no QMI Time-service client

`qrtr-lookup` on the running device shows the modem publishing:

	Service Version Instance Node  Port
	     22       1       16    0    25 Time service

and the firmware contains a full time server and client:

	time_ipc_task initialized
	time_ipc connecting to service %x
	time_ipc rcvd TIME_IPC_QMI_CLIENT_INITIALIZATION_DONE
	time_server_register: err=%d
	time_server_send_ind: err=%d, msg_id=%d, base=%d, fulltime=0x%.8x%.8x, at_timetick=0x%.8x%.8x
	QMI_TIME_REMOTE_QTIMER_TIMESTAMP_DELTA_IND_V01 sent msg = %d, err= %d
	qmi_time_client: remote_local_RTC_delta=0x%.8x%.8x
	qmi_time_client: genoff_opr base=%d, fulltime=0x%.8x%.8x, at_timetick=0x%.8x%.8x
	qmi_time_client: base=%d;time_genoff_set=0x%.8x%.8x
	qmi_time_genoff_set_required: USER base received
	qmi_time_genoff_get_required: error connection_handle=%x, base=%x
	Returning from qmi_time_ind_cb; qtimer delta unavailable
	time_tcxomgr_report_rmt_qcnt err returned remote_local_QTIMER_delta =  0x%x
	/dev/time_genoff

Android runs `time_daemon` on the AP as a **client** of this service; this port
runs nothing. That is a real difference and it is the only AP-side absence on
anything time-shaped that this session found.

**Why it is ranked low anyway, stated honestly:**

* the modem is the *server*. A server with no clients does not block; it simply
  never sends an indication;
* the thing the engine actually asks for during a session is `INJECT_TIME_REQ`
  over **LOC**, not over service 22, and this port can now answer that —
  demonstrated, accepted at both QMI layers, 78 times into a live `cellid`
  session (**[already covered]**, `GNSS-SM6375.md`, run 4);
* answering it is what *arms* the reset, so the AP supplying more time, not
  less, moves in the wrong direction;
* `QMI_TIME_REMOTE_QTIMER_TIMESTAMP_DELTA` is a correlation service between the
  modem's counter and a peer's, used for logging and for ADSP/CDSP time
  alignment. Nothing in the harvest ties it to the NAV core.

**Testable without a flash, cheaply**, and worth one afternoon by somebody with
reset budget: register as a client of service 22 over raw `AF_QIPCRTR` (the
`rhodep-gnss-bisect.py` transport already does exactly this shape of thing),
see whether the modem sends anything, and whether an engine that has been told
the time through service 22 rather than through LOC behaves differently. **Do
not run it on a boot you intend to use for a `powerMode 3` control**: by run 5's
result, any route that gets time into the modem arms the reset for the rest of
the boot.

---

## 3. The bus path out of the NAV core

### 3.1 What the port describes

`docs/interconnect-sm6375-wip/sm6375.c` and `qcom,sm6375.h` describe 39 masters
and 66 slaves. **There is no MSS master of any kind and no NAV anything.** The
only `MPU` slave in the whole binding is `SLAVE_QM_MPU_CFG`, the QoS Manager's
config aperture, which is a routing endpoint on cnoc and is never programmed.
Live confirmation on the device:

	$ grep -icE "nav|mss" /sys/kernel/debug/interconnect/interconnect_summary
	0

### 3.2 What stock describes — and this is the decisive part

`ICBID_MASTER_MSS_NAV = 27` and `ICBID_SLAVE_MSS_NAV_CE_MPU_CFG = 218` come from
`drivers/interconnect/qcom/rpm-ids.h`, which is a **generic, SoC-agnostic
enumeration** shipped in every Qualcomm downstream tree. It defines around 280
identifiers, most of which do not exist on any given part. It is not a
description of this chip.

The description of this chip is `drivers/interconnect/qcom/holi.c` in the vendor
kernel — the provider that actually runs on stock Android on this board.
Verbatim search result:

	$ grep -in "nav\|mss\|mpu" drivers/interconnect/qcom/holi.c
	263:		   SLAVE_QM_CFG, SLAVE_QM_MPU_CFG,
	295:		   SLAVE_QM_CFG, SLAVE_QM_MPU_CFG,
	898:static struct qcom_icc_node qhs_compute_dsp_cfg = {
	1128:static struct qcom_icc_node qhs_qm_mpu_cfg = {
	1509:	[SLAVE_DSP_CFG] = &qhs_compute_dsp_cfg,
	1532:	[SLAVE_QM_MPU_CFG] = &qhs_qm_mpu_cfg,

**`ICBID_MASTER_MSS_NAV` and `ICBID_SLAVE_MSS_NAV_CE_MPU_CFG` appear nowhere in
it.** `holi.c` is byte-identical between `sixteen` and `lineage-22.1`. The port's
`sm6375.c` was transcribed from it, and `holi_nodes_dump.txt` in this directory
is the extracted node list from that same file — 156 lines, zero NAV, zero MSS.

Nor is there a NoC topology in the AP device tree to compare against: the stock
tree contains **zero** `qcom,icbid` properties and only two `qcom,msm-bus,name`
strings, both `sdhc`. On this generation the topology lives in the driver, not
in DT.

**So the NAV master is not on the AP's NoC graph on either side.** It is on the
MSS's internal fabric — the firmware calls the path `nav_cc_snoc_clk`,
`nav_cc_snoc_dbg_clk`, `mss_cc_bus_nav_clk` and (in GCC, §1.4)
`gcc_nav_snoc_axi_clk`.

### 3.3 What 0046, 0065 and 0088 already established — read, not re-proposed

* **0046** (`interconnect: sm6375: do not program QoS`): every node has
  `qos.ap_owned = false`, so `icc-rpm.c` returns before writing any QoS register.
  The AP writes **nothing** into any NoC QoS register on this port. The reason
  was that programming them wedged CPU 3 on a bus transaction that never
  completed. So the boot values stand.
* **0065** (`rhodep: display interconnect paths`): gave `mdss` its two paths and
  made `qnoc-sm6375` built-in. Nothing to do with the modem.
* **0088** (`interconnect: sm6375: keep the boot floors`): removed
  `.sync_state`, so every node keeps the `INT_MAX` floor `icc_node_add()`
  installs. Confirmed live — the summary is full of `2147483647`. Consequence
  for this question: no path this provider knows about is ever *lowered* below
  what the RPM handed over at boot, and no path it does not know about is
  touched at all.

**Taken together: the application processor neither describes, nor votes for,
nor programs QoS on, nor can lower, anything on the NAV core's bus path.**

### 3.4 Is the "MPU" in `MSS_NAV_CE_MPU_CFG` configured by the AP or the secure world?

**By the secure world. Close the branch.** Four independent negatives:

1. **No AP-side driver in either kernel writes any NoC MPU or XPU.** A full
   search of the vendor tree for `MPU_CFG|XPU_CFG|xpu_` returns, on the Qualcomm
   side, only: `RMB_MBA_XPU_UNLOCKED` / `STATUS_XPU_UNLOCKED` in
   `qcom_q6v5_mss.c` and `pil-msa.c` — a *status word the AP reads back from the
   MBA's RMB* during legacy modem authentication, not a configuration write —
   and `SLAVE_*_MPU_CFG` appearing as a routing endpoint in `sdm845.c` and
   `scshrike.c` interconnect graphs. Nothing writes an MPU register. Mainline
   has nothing at all.
2. **This SoC does not even use the path where that status word is read.** It is
   a PAS part: `compatible = "qcom,sm6375-mpss-pas"`, `qcom,pas-id = <4>` on both
   sides. The image is authenticated and released by TrustZone through SCM; the
   AP never sees an MBA handshake.
3. **The name is in the modem's HWIO table, not the AP's.**
   `SNOC_MSS_NAV_CE_MS_MPU_NAV_CE_MPU_CFG` and `NAV_CE_MPU_CFG` appear in
   `modem.b21`; neither string, nor anything like it, appears in either AP
   kernel or either device tree.
4. **The modem has its own XPU fault handling**, which is what you would expect
   of a subsystem living behind protection units somebody else programmed:

		Modem Sec XPU ISR Thread started
		Failed to start modem sec XPU IST
		Failed to register XPU interrupt
		 xpu:>>> [%u] XPU error dump, XPU Base 0x%0x <<<
		HAL_XPU2_ERROR_F_CLIENT_PORT   HAL_XPU2_ERROR_F_CONFIG_PORT
		HAL_XPU2_BUS_F_MSA_RG_MATCH    HAL_XPU2_BUS_F_SECURE_RG_MATCH
		HAL_XPU2_BUS_F_NONSECURE_RG_MATCH
		mpss_ahb_access_err_irq        config_timeout_slave_irq

This is consistent with everything else this port has measured about protection
units: the modem's accesses are policed by the SoC's own units rather than by
Linux's SMMU, and those units kill silently — **[already covered]**,
`HANDOFF.md`, "The two deaths are indistinguishable at the PMIC".

---

## 4. The manufacturer's kernel, restricted to this path

Method: full source of `Motorola-SM6375-Devs/android_kernel_motorola_sm6375`
branch `sixteen` (69 176 blobs), grepped locally. `blair.dtsi`, `gcc-blair.c` and
`holi.c` were also fetched from `LineageOS/android_kernel_motorola_sm6375`
`lineage-22.1`: **`gcc-blair.c` and `holi.c` are byte-identical between the two
branches**; `blair.dtsi` differs only in CPU `enable-method` ordering,
`cdsp_secure_heap_mem`, and `removed_region` size (0x5100000 on `lineage-22.1`
against 0x7100000 on `sixteen` — the discrepancy `REMOVED-MEM-SM6375.md` already
documents), none of it on this path.

| search | hits on this path | file:line |
| --- | --- | --- |
| `nav_cc` / `NAV_CC` / `nav_gdsc` / `NAV_GDSC` | **none** | — |
| `gcc_nav` / `GCC_NAV` | **one, and it is not this SoC** | `techpack/dataipa/drivers/platform/msm/ipa/ipa_v3/dump/ipa4.1/ipa_gcc_hwio.h:11287-11293` — `HWIO_GCC_NAV_BCR` at GCC+0x15000, `HWIO_GCC_NAV_AXI_CBCR` at GCC+0x15004. An IPA-4.1 register dump header, i.e. a different part. Quoted in §1.4 as suggestive only |
| `gnss` / `GNSS` (whole tree) | **nothing Qualcomm** | `drivers/gnss/{core,serial,sirf,ubx,mtk}.c`, `drivers/soc/qcom/gnsssirf/`, `drivers/clk/sirf/clk-atlas7.c`, AMD `navi*`, `drm_agpsupport.c`, `zd1211rw`, TI keystone. Confirms `POWER-DELIVERY.md` §5 **[already covered]** |
| `nav` / `NAV` in `arch/arm64/boot/dts/vendor/qcom/blair*` and `holi*` | **one** | `blair.dtsi:2438` — `qcom,vm-nav-path;` inside `qcom,rmtfs_sharedmem@0`. Mainline already emits the equivalent `qcom,vmid = <0x0f 0x2b>` **[already covered]** |
| `qtimer` | **no node, no driver, nothing for blair** | only `kgsl`'s GPU QTIMER descriptor, `npucc-sm8150.c`, `sensors_ssc.c`'s `sns_read_qtimer()`, `qti-can.c`'s `qcom,use_qtimer`. None on this SoC's timekeeping |
| `timetick` | **nothing relevant** | `cdsprm.c` (CDSP resource-manager message field), `apds990x.c`, `q6voice.h`. No AP-side timetick management |
| `sleep_clk` | **one reference, same as mainline** | `blair.dtsi:1222` — the GCC node's third input. Mainline `sm6375.dtsi:956` does the same |
| `MPU_CFG` / `XPU_CFG` / `xpu_` | **no writer** | §3.4 |
| acts when the modem subsystem starts (`subsys_notif_register_notifier("modem", …)`) | **three, none on this path** | `drivers/soc/qcom/memshare/msm_memshare.c:971` (memshare — **[already covered]**, tested with a real 5 MiB buffer and negative), `drivers/soc/qcom/microdump_collector.c:84` (ramdump collection), `drivers/soc/qcom/icnss2/main.c:1913` (WLAN) |

The stock modem node, for the record, from `blair.dtsi:2941` — the source of the
`merged.dts` node already quoted in `backups/vendor-dt-rhodep-20260906/README.md`:

	pil_modem: qcom,mss@06000000 {
		compatible = "qcom,pil-tz-generic";
		reg = <0x06000000 0x100>;

		clocks = <&rpmcc RPM_SMD_XO_CLK_SRC>;
		clock-names = "xo";
		qcom,proxy-clock-names = "xo";

		vdd_cx-supply = <&S2E_LEVEL>;
		qcom,vdd_cx-uV-uA = <RPMH_REGULATOR_LEVEL_TURBO 100000>;
		qcom,proxy-reg-names = "vdd_cx";

		qcom,firmware-name = "modem";
		memory-region = <&pil_mpss_wlan_mem>;
		qcom,proxy-timeout-ms = <10000>;
		...
	};

**One clock, XO, held as a proxy. One rail, CX.** This port's
`remoteproc@6080000` declares `clocks = <&rpmcc RPM_SMD_XO_CLK_SRC>;
clock-names = "xo";` and `power-domains = <&rpmpd SM6375_VDDCX>;`, and
`qcom_q6v5_pas.c` does `clk_prepare_enable(pas->xo)` at start and
`clk_disable_unprepare(pas->xo)` in `qcom_pas_handover()` — the same one-shot
proxy window stock has (**[already covered]**, `POWER-DELIVERY.md` §4.1, which
established that stock also drops on the interrupt and not on the 10 s timeout).
Patch 0070 is in the shipped kernel and the handover interrupt has fired exactly
once this boot:

	146:  0  0  0  0  0  0  1  0  smp2p-modem  2 Edge  q6v5 handover

so the repeated-unvote failure mode that would otherwise follow from the line
transitioning ~26 000 times per boot is not live.

**Two checks made and cleared, recorded so nobody spends time on them.**

* **TLMM base address.** Stock declares `pinctrl@400000` with `reg = <0x400000
  0x800000>` and mainline declares `pinctrl@500000` with `reg = <0 0x00500000 0
  0x800000>`, which looks like a 1 MiB error. It is not: `pinctrl-blair.c` has
  `.ctl_reg = REG_BASE + REG_SIZE * id` with `REG_BASE 0x100000`, while
  `pinctrl-sm6375.c` has `.ctl_reg = REG_SIZE * id` and never uses its own
  vestigial `REG_BASE` define. Both land on the same absolute addresses. Also
  neither AP tree declares `gpio-reserved-ranges` on the TLMM node; the port
  reserves *more* than stock, not less (`gpio-reserved-ranges = <13 4>` in
  `sm6375-motorola-rhodep.dts:504`).
* **The GNSS RF control pins.** `gnss_l1_elna_ctrl`, `gnss_l2_5_elna_ctrl`,
  `nav_gpio_0`, `nav_gpio_1`, `qlink0_wmss_reset_n`, `qlink0_request`,
  `qlink0_enable` all appear in a **board pin-name table inside the modem's own
  devcfg** (`modem.b21`, interleaved with `nfc_interrupt`, `ts_reset_n`,
  `fp_int_n`, `cam_mclk0`, `torch`, `sd_card_det_n`). That confirms
  `POWER-DELIVERY.md` §5 from the other side — the modem owns and names these
  pins, the AP tree names none of them, on either side — and it is **not** a
  re-opening of the eLNA branch, which stays closed.

---

## 5. Live state, read-only

Taken on the running `#1-motorola-rhodep` 7.2.0-rc5, uptime 17–30 min, nothing
written, no module loaded, no MMIO or PMIC register read.

**Clocks on this path**, from `/sys/kernel/debug/clk/clk_summary` (423 lines):

	 clock                 enable prepare protect  rate       hw  consumer
	 sleep-clk                 1      1      0     32764       Y  f410000.watchdog
	 xo-board-clk              2      2      0     19200000    Y  deviceless
	    bi_tcxo_a              2      2      0     19200000    Y  deviceless
	    bi_tcxo              141    141      0     19200000    Y  b300000.remoteproc  xo
	                                                              a400000.remoteproc  xo
	                                                              6080000.remoteproc  xo
	                                                              4784000.mmc         xo
	       gpll0                3      3      0     600000000  N
	       gpll1                0      0      0     399999902  N
	       gpll3                0      0      0     1065999902 N
	       gpll4                0      0      0     806400000  N
	       gpll5                0      0      0     933000000  N
	       gpll6                0      0      0     768000000  N
	       gpll7                0      0      0     807999902  N
	       gpll8                0      0      0     399999902  N
	       gpll9                0      0      0     1440000000 N
	       gpll10               0      0      0     1152000000 N
	       gpll11               0      0      0     531999902  N
	 gpucc_gx_cxo_clk           1      1      0     0           Y
	 disp_cc_xo_clk             1      1      0     0           Y
	 disp_cc_sleep_clk          1      1      0     0           Y
	 gcc_usb30_prim_sleep_clk   1      1      0     0           Y
	 gcc_pdm_xo4_clk            0      0      0     0           N
	 gcc_video_xo_clk           0      0      0     0           N
	 gcc_disp_sleep_clk         0      0      0     0           N
	 gpucc_sleep_clk            0      0      0     0           N
	 gpucc_cxo_clk              0      0      0     0           N   gpu@5900000
	 gpucc_cxo_aon_clk          0      0      0     0           N

	$ grep -ic nav /sys/kernel/debug/clk/clk_summary
	0
	$ ls /sys/kernel/debug/clk/ | grep -c nav
	0

**No `nav` clock exists in the application processor's view. Confirmed again.**
XO is up at 19.2 MHz with 141 enables and is held by all three remoteprocs;
`gpll4` — the only GPLL whose number matches `nav_cc_pll4_pll` — is a completely
unrelated AP GPLL at 806.4 MHz, off, with no users. The `nav_cc_pll4_pll` in the
firmware is inside `MSS_NAV_CC_NAV_CC_REG_BASE`.

**Device tree**, `/proc/device-tree`: the `clocks` and `timer` dumps are quoted
verbatim in §2.1 and §2.2. The complete list of anything timer- or SRAM-shaped
under `/proc/device-tree/soc@0/`:

	sram@45f0000     sram@4690000     sram@c125000     timer@f420000

**Interconnect**: `interconnect_graph` and `interconnect_summary` both present
and populated; zero nodes matching `nav` or `mss`; every node at the `2147483647`
boot floor, as patch 0088 intends.

**Power domains**, `/sys/kernel/debug/pm_genpd/`: 22 domains — `cx`, `cx_ao`,
`cx_vfl`, `mx`, `mx_ao`, `mx_vfl`, `gx`, `gx_ao`, `lpi_cx`, `lpi_mx`, eight CPU
domains plus the cluster, and the GDSCs (`camss_top`, `gpu_cx`, `gpu_gx`, `mdss`,
`ufs_phy`, `usb30_prim`, `vcodec0`, `venus`, four `hlos1_vote_*` TBUs).
**There is no `nav_gdsc` and no modem GDSC of any kind**, which is the expected
counterpart of `HANDOFF.md`'s "The GDSCs. All twelve match … none of them is a
modem GDSC" **[already covered]**.

**Interrupts** relevant to the modem:

	 14:   3360 ...  ipcc         0 Edge  glink-rpm
	 15:      0 ...  GICv3      229 Edge  qcom_mpm
	 18:      6 ...  ipcc    131074 Edge  smp2p-modem
	141:      1 ...  GICv3      289 Edge  ipa
	143:      0 ...  GICv3      339 Edge  q6v5 wdog
	144:      0 ...  smp2p-modem 0 Edge  q6v5 fatal
	145:      4 ...  smp2p-modem 1 Edge  q6v5 ready
	146:      1 ...  smp2p-modem 2 Edge  q6v5 handover
	150:      0 ...  smp2p-modem 0 Edge  ipa-clock-query

Nothing NAV-shaped reaches the GIC, as §1.2(i) predicts.

---

## 6. Prioritised answer

### P1 — Nothing. The application processor is not on this path.

**Evidence:** §1 (every base, clock, domain, PLL, RTC, STMR, interrupt and RF
link named by the firmware is `MSS_*` or modem-L2VIC), §3 (no NAV node in either
NoC provider, no AP-side MPU/XPU writer), §4 (the vendor kernel has nothing),
§5 (the AP's live view has no NAV clock, no NAV domain, no NAV interrupt).

**What stock does:** votes XO as a proxy and CX at level 384 with a 100 mA hint
while the modem boots, and nothing else. **What this port does:** votes XO as a
proxy and CX at `INT_MAX` — a strict superset on the level axis — and nothing
else. **Testable without a flash:** already tested to destruction; CX and MX have
been pinned at `TURBO_NO_CPR` for whole runs and the reset was unchanged
(**[already covered]**). **Likelihood this is the bug: nil.**

**The branch is closed. Redirect to the modem image and the secure world.**

### P2 — `gcc_nav_snoc_axi_clk` / `gcc_nav_mbist_clk`: closed for the AP, and the earlier reasoning should be amended

**Evidence:** §1.4. **What stock does:** nothing — `gcc-blair.c` and
`gcc-sm6375.c` have the identical 183 clocks and 20 resets and neither has a NAV
entry; stock takes a satellite fix regardless. **What this port does:** the same
nothing. **Testable without a flash:** yes, and done — `grep -ic nav
clk_summary` is `0` here, and the driver comparison is a source fact.
**Likelihood: nil for the AP.**

The amendment: "moved behind the secure image-loading boundary" should become
"owned by the MPSS, most likely programmed by its own ClockDAL through
`GCC_CLK_CTL_REG`, with TrustZone not excluded". Do **not** add NAV clocks to
`gcc-sm6375.c`; it would be inventing hardware description Qualcomm's own driver
does not have, and it cannot be the difference between this port and stock
because there is no difference.

### P3 — `clock-frequency` on the two timer nodes: a correctness gap with a measured negative

**Evidence:** §2.2. **What stock does:** `clock-frequency = <19200000>` on both
`timer` and `timer@f420000`. **What this port does:** neither; relies on the boot
chain's `CNTFRQ_EL0` and `CNTFID0`. **Testable without a flash:** yes, and done —
`arch_timer: cp15 timer running at 19.20MHz`, and the memory-mapped counter
reports the identical `max_cycles`, so both agree. **Likelihood: very low.**

Worth fixing as hardening (it is two lines in `sm6375.dtsi` and matches what the
vendor ships), not as a candidate cause.

### P4 — No QMI Time-service (service 22) client on the AP

**Evidence:** §2.5. **What stock does:** runs `time_daemon` as a client.
**What this port does:** nothing; the service is published and nobody connects.
**Testable without a flash:** yes — register over raw `AF_QIPCRTR` and observe.
**Likelihood: low.** The modem is the server, the LOC time path demonstrably
works, and supplying more time is the direction that *arms* the reset. It is
listed because it is the only genuine AP-side absence found and because it is
cheap to settle.

### P5 — Nothing else was found, and two things were checked and cleared

TLMM base address and GPIO reserved ranges (§4); the memory-mapped timer's
existence and binding (§2.2). Both non-issues; recorded so they are not
re-derived.

### Redirect: where the evidence now points

Not an action item for this port, but the harvest names a concrete mechanism
that is entirely inside the modem and that fits the measured switch:

	NavRX boot-up calibration attempt failed
	NavRX ADC cal is failed. Request CC reset!
	NavRX BP ampl check failed %d times. Request CC reset!
	  -> CC initiating critical error recovery. Pending CP intr %d DP intr %d
	  -> Resetting Nav Core for recovery
	     (guarded by: GNSS RTC not running. Not resetting NAV core)

with the QLINK to the transceiver as the most fragile link in it
(`Qlink CSR WDOG timeout in command process`, `Qlink(%d) RF stuck in start
state: %d at 0x%x`, `Qlink mK reports critical error: 0X%x at state: 0X%x`,
`Qlink(%d)stmr out of sync detected`). Reading any of that requires modem-side
visibility — DIAG, QXDM or a modem ramdump — which `docs/modem-diag-wip/` says is
not currently reachable on this firmware. That remains the whole plan.

---

## 7. What is not proven

* **Nothing here demonstrates a cause.** Every entry in §6 is an absence
  established from device trees, driver sources, firmware strings and read-only
  sysfs. The strongest claim made is a negative: the AP has nothing on this path
  to be missing.
* **The re-reading of `GNSS RTC` in §1.3 is an inference from usage, not a
  decompilation.** The string sits three lines from `CC initiating critical
  error recovery` in a string table; string-table adjacency is strong evidence
  of source-file adjacency and weak evidence of control flow. Nobody has
  disassembled `cc_slicer.c`. The brief's reading and this one make the same
  prediction about the two-sided switch, so the measurement cannot distinguish
  them; only the register-level meaning differs, and that is where it matters.
* **"The MPSS programs `gcc_nav_*` itself" is supported, not proved** (§1.4).
  The same DAL table contains QUPv3 clock names the MPSS does not own. What is
  proved is only that the AP does not own them, on either side.
* **`ICBID_*` absence from `holi.c` proves the AP's NoC graph has no NAV node.
  It does not prove the hardware port does not exist.** It plainly does exist —
  the MPSS names `SNOC_MSS_NAV_CE_MS_MPU_NAV_CE_MPU_CFG`. The claim is about who
  describes and configures it, not about whether it is there.
* **No register was read, so no hardware state was observed directly.** Every
  live reading here is a kernel-maintained view: `clk_summary` reflects the AP's
  own bookkeeping (`POWER-DELIVERY.md` §1.8 makes the same point about
  `regulator_summary`), and `interconnect_summary` reflects requests, not the
  NoC.
* **The counter agreement in §2.3 is between the AP's two frames.** That the
  modem's `MSS_QDSP6SS_QTMR_*` frames agree is inferred from its own assertion
  not having fired, which is an argument from silence — a strong one on a modem
  that boots and runs, but not a measurement.
* **The service-22 experiment in §2.5 was not run.** It is proposed, not
  reported.
* **`strings` is a lower bound.** Anything the firmware builds at runtime, or
  keeps in a compressed or encrypted segment, is invisible to this method. The
  absence of a string is not the absence of a behaviour.

---

## Method and safety notes

	# firmware harvest, on the phone, read-only
	cd /readonly/firmware/image
	for f in modem.b*; do strings -n 4 "$f" | sed "s|^|$f\t|"; done > /tmp/modemstr.txt
	# 676933 lines, 13 MB; copied off with scp, then removed from /tmp

	# live reads used here, all read-only
	/sys/kernel/debug/clk/clk_summary
	/sys/kernel/debug/interconnect/interconnect_summary
	/sys/kernel/debug/pm_genpd/           /proc/interrupts   /proc/iomem
	/proc/device-tree/clocks/  /proc/device-tree/timer/  /proc/device-tree/soc@0/timer@f420000/
	dmesg | grep arch_timer                qrtr-lookup

No register was read by any means. `0x3d3000` and `0x045f1000` were not
approached. No module was loaded or removed, no partition or boot image was
written, no `*_persist_*` QMI field was sent, and no GNSS session was started.
The phone was left as it was found: `rhodep-gnss.service` active, `wlan0` up at
192.168.1.53, service 16 present in `qrtr-lookup`, uptime continuous throughout
(17 min → 30 min across the session).

Offline sources: `backups/vendor-dt-rhodep-20260906/{merged.dts,live-mainline.dts}`
(read that directory's `README.md` first — git-ignored, Motorola's material, not
to be redistributed), `kernel/patches/0001`, `0046`, `0065`, `0070`, `0088`,
mainline 7.2-rc5, and full checkouts of
`Motorola-SM6375-Devs/android_kernel_motorola_sm6375@sixteen` plus three files
from `LineageOS/android_kernel_motorola_sm6375@lineage-22.1` for cross-checking.
