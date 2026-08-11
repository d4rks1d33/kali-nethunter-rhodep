# VENDOR-BIMC.md — Why adopting the BIMC node resets rhodep, and how the vendor avoids it

Deep-research session (read-only). Goal: explain the "even a COMPLETELY INERT bimc
provider hangs the SoC" clue and produce a ranked, concrete fix list.

TL;DR of the root cause
-----------------------
The hang is **not** caused by anything the bimc driver actively does (MMIO, bus-rate
vote, QoS). It is caused by a **system-level side effect of mainline's `icc_node_add()`**:
mainline forces every freshly-registered node to `init_avg = init_peak = INT_MAX` and
immediately calls `provider->set(node, node)` **at probe time**. For the bimc provider
that fires a **raw SMD-RPM `RPM_BUS_SLAVE_REQ` to the EBI/DDR resource (id 0) with
INT_MAX bandwidth**, on a code path the downstream/vendor driver *never* exercises. The
vendor's downstream `icc_node_add()` does literally nothing but link the node — no
initial vote, no `set()` call. That is the exact behavioural difference that makes an
"inert" mainline bimc provider still wedge the RPM/DDR path and reset rhodep with no log.

This is consistent with every bisection result in PROGRESS.md:
- v50 (QoS writes disabled) still hung  -> not QoS.
- v53 (only bimc) hung; v54 (bimc, no MMIO) hung -> not the register/MMIO path.
- v55 (bimc, no MMIO, no bus_clk_desc) hung -> not the bus-rate vote either.
  BUT v55 still leaves the per-node `ebi.slv_rpm_id = 0` intact, and mainline
  `icc_node_add()` still emits that INT_MAX EBI SMD request at probe. That is the
  one thing none of v50/v54/v55 removed.

===================================================================================
1. THE DECISIVE EVIDENCE: mainline vs vendor `icc_node_add()`
===================================================================================

### Mainline core forces an INT_MAX floor vote + set() at node registration
`/tmp/opencode/kernel/linux-7.2-rc5/drivers/interconnect/core.c:1068`
```
void icc_node_add(struct icc_node *node, struct icc_provider *provider)
{
	...
	node->provider = provider;
	list_add_tail(&node->node_list, &provider->nodes);

	/* get the initial bandwidth values and sync them with hardware */
	if (provider->get_bw) {
		provider->get_bw(node, &node->init_avg, &node->init_peak);
	} else {
		node->init_avg = INT_MAX;          /* <-- our driver has NO get_bw */
		node->init_peak = INT_MAX;         /*     so every node = INT_MAX  */
	}
	node->avg_bw = node->init_avg;
	node->peak_bw = node->init_peak;

	if (node->avg_bw || node->peak_bw) {
		if (provider->pre_aggregate) provider->pre_aggregate(node);
		if (provider->aggregate)     provider->aggregate(node, 0, ...);
		if (provider->set)           provider->set(node, node);   /* <-- FIRES AT PROBE */
	}
	...
}
```
Our `sm6375.c` (like sm6115.c/qcm2290.c) does **not** set `provider->get_bw`, so every
node registered in `qnoc_probe()` is initialised to INT_MAX and immediately pushed to
hardware via `provider->set()`.

### The vendor's downstream core does NONE of that
`/tmp/opencode/dl/moto/drivers/interconnect/core.c` (downstream `icc_node_add`):
```
void icc_node_add(struct icc_node *node, struct icc_provider *provider)
{
	mutex_lock(&icc_lock);
	node->provider = provider;
	list_add_tail(&node->node_list, &provider->nodes);
	mutex_unlock(&icc_lock);
}
```
No `init_avg/init_peak`, no `get_bw`, no `provider->set()`. The vendor only ever votes
when a real consumer calls `icc_set_bw()`, and even then vendor `qcom_icc_rpm_set()`
early-returns unless `qn->dirty` is set (`icc-rpm.c:116`). **The vendor never issues an
unsolicited boot-time INT_MAX request to EBI/DDR.**

### The path that fires even for an "inert" bimc (no bus_clk, no MMIO)
`/tmp/opencode/kernel/linux-7.2-rc5/drivers/interconnect/qcom/icc-rpm.c:347`
```
static int qcom_icc_set(struct icc_node *src, struct icc_node *dst)
{
	...
	ret = qcom_icc_rpm_set(src_qn, src_qn->sum_avg, qp->ignore_enxio);  /* line 366 */
	...
	ret = qcom_icc_rpm_set(dst_qn, dst_qn->sum_avg, qp->ignore_enxio);  /* line 371 */
	...
	/* Some providers don't have a bus clock to scale */
	if (!qp->bus_clk_desc && !qp->bus_clk)
		return 0;                                                  /* line 377-378 */
```
Note the per-node `qcom_icc_rpm_set()` (lines 366/371) runs BEFORE the
`!bus_clk_desc && !bus_clk` early-return (line 377). So removing `bus_clk_desc` (v55)
does NOT remove the per-node EBI request. `qcom_icc_rpm_set()` only skips a node if
`qn->qos.ap_owned` is true (`icc-rpm.c:212`) — and `ebi` is not ap_owned.

`ebi` node in our driver (`_common/interconnect-sm6375-wip/sm6375.c:704`):
```
static struct qcom_icc_node ebi = {
	.name = "ebi",
	.id = SLAVE_EBI,
	.channels = 2,
	.buswidth = 4,
	.mas_rpm_id = -1,
	.slv_rpm_id = 0,        /* <-- RPM_BUS_SLAVE_REQ id 0 = EBI/DDR */
	.num_links = 0,
};
```
So at `icc_node_add(ebi)`, mainline sends:
`qcom_icc_rpm_smd_send(ACTIVE, RPM_BUS_SLAVE_REQ, 0, icc_units_to_bps(INT_MAX))`
and the same for SLEEP (`icc-rpm.c:207-243`, `smd-rpm.c:32`). An INT_MAX-bandwidth
slave request to the DDR/EBI resource, issued unsolicited at boot, is the most
plausible trigger for an early RPM/DDR wedge -> reset with no ramoops.

===================================================================================
2. RPM_SMD_BIMC_CLK / keepalive: mainline DOES keep BIMC alive (so this is NOT it)
===================================================================================

The original hypothesis (sync_state powering off BIMC) is disproven:

- Mainline rpmcc pins BIMC (and SNOC/CNOC/MMRT/MMNRT/QUP) at boot via a permanent
  handoff vote, independent of any consumer:
  `/tmp/opencode/kernel/linux-7.2-rc5/drivers/clk/qcom/clk-smd-rpm.c:1376`
  ```
  for (i = 0; i < desc->num_icc_clks; i++) {
  	...
  	ret = clk_smd_rpm_handoff(desc->icc_clks[i]);   /* sends INT_MAX active+sleep */
  }
  ```
  `clk_smd_rpm_handoff()` (`clk-smd-rpm.c:190`) writes `value = INT_MAX` to both
  ACTIVE and SLEEP states. These icc_clks are handed off but **not registered with
  CCF** (only `num_clks` get `devm_clk_hw_register`, line 1391), so no `sync_state`
  and no `clk_disable_unused` can ever turn them off.
- The sm6375 icc_clks set includes BIMC:
  `clk-smd-rpm.c:564` `sm_qnoc_icc_clks[] = { &clk_smd_rpm_bimc_clk, bus_1_cnoc,
  mmnrt, mmrt, qup, bus_2_snoc }`, referenced by `rpm_clk_sm6375` (`clk-smd-rpm.c:1254`).
- So `RPM_SMD_BIMC_CLK` (MEM_CLK 0) is held at INT_MAX by rpmcc for the whole boot,
  whether or not any icc provider adopts the bimc node. Adopting the node does NOT
  drop it. => "sync_state disables BIMC clock" is NOT the cause.

Note the mainline `sm6375_clks[]` (`clk-smd-rpm.c:1231`) indeed does NOT expose
`RPM_SMD_BIMC_CLK` as a CCF clock (only `RPM_SMD_BIMC_FREQ_LOG`), exactly as PROGRESS
observed — but it does not need to, because the keepalive is done through the
`icc_clks` handoff, not through a registered clock. The vendor achieves the same
"always-on" effect differently (see section 3).

===================================================================================
3. HOW THE VENDOR KEEPS BIMC/DDR ALIVE (for completeness; it is a *different* model)
===================================================================================

The vendor uses the **CCF clock path**, not the raw SMD bus_clk_desc path:

- Vendor bimc DT (`/tmp/opencode/dl/moto/arch/arm64/boot/dts/vendor/qcom/holi.dtsi:1061`):
  ```
  bimc: interconnect@4480000 {
  	reg = <0x04480000 0x80000>;
  	compatible = "qcom,holi-bimc";
  	#interconnect-cells = <1>;
  	qcom,util-factor = <151>;
  	qcom,keepalive;
  	clock-names = "bus", "bus_a";
  	clocks = <&rpmcc RPM_SMD_BIMC_CLK>, <&rpmcc RPM_SMD_BIMC_A_CLK>;
  };
  ```
  (No power-domain, no regulator on the bimc node or its parent -> rules out the
  genpd/regulator hypothesis, item 3 in the task.)

- Vendor probe holds the bus clocks enabled for the driver lifetime
  (`/tmp/opencode/dl/moto/drivers/interconnect/qcom/holi.c:1686`):
  ```
  qp->num_clks = ARRAY_SIZE(bus_clocks);              /* "bus","bus_a" */
  ret = devm_clk_bulk_get(dev, qp->num_clks, qp->bus_clks);
  ret = clk_bulk_prepare_enable(qp->num_clks, qp->bus_clks);   /* never disabled
                                                                  on success path */
  ```
  `bus_clocks[] = { {.id="bus"}, {.id="bus_a"} }` (`holi.c:31`). Disable happens only
  in the error path and in `qnoc_remove()` (`holi.c:1793`).

- Vendor votes via `clk_set_rate()` on the CCF clock, with an explicit keepalive floor:
  `/tmp/opencode/dl/moto/drivers/interconnect/qcom/icc-rpm.c:144`
  ```
  if (qp->keepalive && i == RPM_ACTIVE_CXT) {
  	if (qp->init)          clk_set_rate(bus_clks[i].clk, RPM_CLK_MAX_LEVEL);
  	else if (rate == 0)    clk_set_rate(bus_clks[i].clk, RPM_CLK_MIN_LEVEL);  /* 19.2 MHz */
  	else                   clk_set_rate(bus_clks[i].clk, rate);
  }
  ```
  and its sync_state (`holi.c:1832`) re-pins keepalive providers to at least
  `RPM_CLK_MIN_LEVEL`. Crucially, the vendor only ever runs this on a *real* dirty
  vote; it never blasts INT_MAX to EBI at node-add.

Vendor `icc-rpm.h` also shows the vendor bandwidth votes go per-node only when dirty,
never at registration (`/tmp/opencode/dl/moto/drivers/interconnect/qcom/icc-rpm.c:116`
`if (!qn->dirty) return 0;`).

===================================================================================
4. bwmon / bimc-log-stop: NOT relevant
===================================================================================
- `qcom,bimc-bwmon4 @4520300` (`holi.dtsi:1332`) is a *bandwidth monitor* at a
  different address (0x4520300) from bimc (0x4480000); it is a devfreq input, not a
  DDR-keepalive, and it is a separate driver. No conflict with the icc provider.
- `qcom,bimc-log-stop` (`holi.dtsi:1214`) is a property on the **rpmcc** node, not a
  hardware block that needs a driver. Irrelevant to the reset.

===================================================================================
5. IMPORTANT CONTEXT: there is NO upstream sm6375 icc DRIVER
===================================================================================
- Mainline ships the DT nodes and binding but no driver:
  - `arch/arm64/boot/dts/qcom/sm6375.dtsi:980-1061` already has
    interconnect@1880000 (snoc, with clk_virt/mmrt/mmnrt as CHILD nodes),
    interconnect@1900000 (cnoc), interconnect@4480000 (bimc, NO clocks).
    Header: "Copyright (c) 2022, Konrad Dybcio" (added speculatively).
  - `include/dt-bindings/interconnect/qcom,sm6375.h` exists.
  - `drivers/interconnect/qcom/sm6375.c` does NOT exist upstream.
- Our patch 0028 reproduces exactly that DT layout (bimc has no clocks; virtual buses
  are children of system_noc) — so the DT is correct/upstream-shaped and is NOT the bug.
- Consequence: there is no "known-good" sm6375 icc reference; the mainline
  `icc_node_add()` INT_MAX-floor behaviour has simply never been validated against this
  RPM firmware. This matches the observation that the generic mainline path resets the
  device.

===================================================================================
RANKED FIXES TO TRY (most-likely-first)
===================================================================================

### FIX 1 (highest): stop the boot-time INT_MAX floor vote to EBI/DDR
The mechanism is `icc_node_add()` calling `set()` with INT_MAX for `ebi`
(slv_rpm_id=0) at probe. Neutralise the EBI slave RPM id so no unsolicited DDR
request is emitted, while still letting the provider exist for the IPA path.
- File: `_common/interconnect-sm6375-wip/sm6375.c:704` (`ebi` node)
  Change `.slv_rpm_id = 0` -> `.slv_rpm_id = -1`.
  Rationale: `RPM_SMD_BIMC_CLK` is already pinned at INT_MAX by rpmcc handoff
  (section 2), so DDR bandwidth is guaranteed at boot regardless; the per-node EBI
  SMD request is redundant and is the exact thing the vendor never sends. With id=-1,
  `qcom_icc_rpm_set()` skips it (`icc-rpm.c:175` vendor / mainline `mas/slv_rpm_id==-1`
  guard) and no INT_MAX EBI vote is issued at node-add.
- This is the single change most consistent with "inert bimc still hangs":
  it removes the only remaining unsolicited RPM transaction on the bimc provider.
- Test as a bimc-only build first (ONLY_BUS=bimc). Expected: boots.

### FIX 2: provide a `get_bw` that returns 0 so `icc_node_add()` never calls set()
If FIX 1 is insufficient (i.e. some other node's INT_MAX vote also wedges RPM),
kill the whole boot-time floor mechanism the way the vendor does (vendor never
votes at node-add at all).
- Add a `get_bw` callback to the desc/driver that sets `*avg = *peak = 0`.
  In `icc_node_add()` (core.c:1080) `provider->get_bw` being present makes
  `init_avg/init_peak = 0`, so the `if (node->avg_bw || node->peak_bw)` block
  (core.c:1089) is skipped and `provider->set()` is NOT called at probe.
- Mainline precedent: `struct qcom_icc_desc.get_bw` + `provider->get_bw = desc->get_bw`
  already exist (`icc-rpm.c:559`). Several SoCs use a `qcom_icc_bw_null`-style
  get_bw. Implement:
  ```
  static int sm6375_get_bw(struct icc_node *node, u32 *avg, u32 *peak)
  { *avg = 0; *peak = 0; return 0; }
  ```
  and set `.get_bw = sm6375_get_bw` on every sm6375_* desc.
- Effect: exactly mirrors the vendor "no vote until a real consumer asks" model,
  but keeps sync_state/keep_alive for later real votes. Broadest, safest fix.
- NOTE: with no boot floor vote, rely on rpmcc handoff (section 2) to hold BIMC/SNOC
  up until IPA (or another consumer) votes — which it does.

### FIX 3: drop `keep_alive` on the bimc (and, if needed, other) desc
`keep_alive=true` forces `active_rate = max(19200, active)` (`icc-rpm.c:384`) and, via
the INT_MAX floor, participates in the boot vote. If FIX 1/2 are applied this is moot,
but if testing FIX 1 alone, also try:
- File: `_common/interconnect-sm6375-wip/sm6375.c:1594` set `.keep_alive = false` on
  `sm6375_bimc`. Lower priority: it changes the *rate* but not the fact that the EBI
  slave request fires; FIX 1 is the targeted change.

### FIX 4 (fallback, guaranteed non-reset provider): make bimc vote-less
If the DDR path must be left entirely untouched, register bimc with neither an RPM
bus clock nor per-node RPM ids:
- `sm6375_bimc`: drop `.bus_clk_desc = &bimc_clk` (`sm6375.c:1591`) AND set every bimc
  node's `mas_rpm_id`/`slv_rpm_id` to -1 (apps_proc already -1; set `ebi.slv_rpm_id`,
  `slv_bimc_snoc.slv_rpm_id`, `mas_bimc_snoc`/`mas_snoc_bimc` ids to -1).
- Result: `qcom_icc_set()` sends nothing to RPM for bimc and returns at
  `icc-rpm.c:377`. DDR stays on rpmcc's handoff floor. The bimc provider still exists
  so `&bimc SLAVE_EBI` xlate resolves for the IPA `interconnects` path (the vote just
  aggregates to a no-op on bimc; the real bandwidth is carried by rpmcc keepalive).
- This is the "safe inert-but-present" provider the task asked for; combine with FIX 2
  for the other buses.

### Ordering to test on HW
1. FIX 1 alone (ebi.slv_rpm_id = -1), bimc-only build.  [smallest, most targeted]
2. If still hangs -> FIX 2 (get_bw=0) on all descs, bimc-only, then all buses.
3. If a specific other node is implicated -> FIX 4 for bimc + FIX 2 for the rest.

### Why NOT to pursue the earlier hypotheses
- CLK_IS_CRITICAL on BIMC rpm clock: unnecessary — rpmcc handoff already pins it
  (section 2). Adding it changes nothing about the boot EBI vote.
- Adding `clocks=<&rpmcc RPM_SMD_BIMC_CLK ...>` to the bimc DT + dropping bus_clk_desc
  (vendor CCF path): this makes the provider a CCF consumer of BIMC_CLK, but it does
  NOT stop `icc_node_add()`'s per-node INT_MAX EBI SMD request (that goes through
  `mas/slv_rpm_id`, not the bus clock). So it would not fix the inert-hang by itself.
- Power-domain / regulator: vendor bimc has neither (section 3) -> not the cause.
