# GNSS on SM6375 (rhodep) — measured, and it is not what the docs said

**Result: starting a GNSS session watchdog-resets the SoC in under a second,
reproducibly, with `ipa.ko` not loaded.**

> **The phone reports a position now, and none of it comes from a satellite.**
> This document is about the *satellite* path, which is still unusable. A
> separate piece of work gets a location out of WiFi access points, the
> identities of the cells the modem can hear, and the modem's own `cellid` LOC
> mode, and republishes it as ordinary NMEA for gpsd and geoclue — measured 22 m
> from WiFi and 250 m from a cell tower, live, with an unprovisioned SIM. It
> deliberately never opens the QMI LOC service unless the modem is registered,
> so the reset below is out of reach rather than merely guarded against.
> [`docs/GPS-USERSPACE.md`](../GPS-USERSPACE.md).
>
> Nothing here is fixed by that. It is worth stating in the first paragraph
> because "GPS does not work on this port" stopped being true, while "a satellite
> fix reboots the phone" stayed true, and the two are easy to conflate.

> ## 2026-09-06: the engine was waiting for UTC time. Read the last section first.
>
> The `powerMode 3`/`5` sessions that this file calls survivable are survivable
> **only because the engine has no time and will not start the receiver without
> it**. One accepted `QMI_LOC_INJECT_UTC_TIME` — before the session, during it,
> or in a different session twenty seconds earlier — turns `powerMode 3` into
> the same ~100 ms watchdog reset as `powerMode 0/1/2`. Injecting a *position*
> instead does not, and 78 time injections into a live `cellid` session do not.
> Six resets and two controls, at
> ["Answered: the engine was waiting for UTC time"](#answered-the-engine-was-waiting-for-utc-time-and-giving-it-to-it-is-what-fires-the-reset).
>
> Two things below are now wrong as written and are corrected there, not edited
> out: "`powerMode 3` survives 300 s" is conditional on no time having been
> injected on that boot, and the event registration mask turns out to be a
> second, independent reset lever. Still **no fix, no measurement report, no
> satellite ever observed**.

> ## Narrowed: it is the measurement engine, and `cellid` positioning works
>
> One operation mode changes the outcome completely. With the mode set to
> `cellid` **from the same QMI client that opens the session**, a live GNSS
> session runs to completion and the phone does not reset:
>
> ```
> POS    session=2
> NMEA   $PSTIS,*61
> alive 24400 ms (indications so far: 50)
> indications received: 50            <- survived, 25 s, uptime kept running
> ```
>
> Fifty indications, real position reports and NMEA. In `standalone` the same
> script kills the SoC within milliseconds of `QMI_LOC_START` returning.
>
> Run back to back **on one boot**, so that the operation mode is the only
> variable — no reboot, no reload, same modem, same client code path:
>
> ```
> uptime 366.8   opmode=cellid       survives 22 s, 16 NMEA indications
> uptime 380.8   opmode=standalone   dead
>
> [  380.799731] AB-SAMEBOOT-STANDALONE
> [  381.464682] rhodep-gnss: setting operation mode standalone from this client
> [  381.469661] rhodep-gnss: operation mode set to standalone
> [  381.477273] rhodep-gnss: session 11 started; listening 15 s
> [  381.477725] rhodep-gnss: -----------------------------------------------
> ECC: No errors detected
> ```
>
> Fifteen milliseconds from `LOC_START` to the end of the console. Note the
> `operation mode set to ...` line in both: the mode really was applied, which
> is worth checking every time, because a failed set falls through to
> `standalone` and would look like a survival that means nothing.
>
> So the trigger is not the session, not the indications, not QMI and not "the
> session being allowed to stay alive" as this document concluded below — a
> `cellid` session stays alive indefinitely. It is specifically **the GNSS
> measurement engine being brought up**, which `standalone` does and `cellid`
> never does.
>
> Two consequences:
>
> * **Cell-id positioning is a working feature today**, which is more than this
>   port had before. `scripts/rhodep-gnss-test.py 60 min opmode=cellid`.
> * The reproducer is now a *bisection* rather than just a trigger, and it costs
>   about ninety seconds a round with no SIM, no LTE and no IPA.
>
> `qmicli --loc-set-operation-mode=cellid` **does not work** and fails in a way
> that reads like success: it prints `Successfully set operation mode` and a
> following `--loc-get-operation-mode` still answers `standalone`, because
> qmicli releases its LOC client on exit and the setting goes with it. That is
> why `rhodep-gnss-test.py` grew an `opmode=` flag that sends
> `QMI_LOC_SET_OPERATION_MODE` from the client it then starts the session on.
>
> Everything ruled out since, each with an instrument, all on the reproducer:
>
> | suspect | how it was excluded |
> | --- | --- |
> | CPU frequency / an APSS current droop | every policy forced to `powersave` (300 MHz / 691 MHz) — still resets, unchanged |
> | the modem wanting a file at engine start | `tqftpserv` + `rmtfs` journals bridged into `/dev/kmsg` (bridge verified live first) — **not one line** between the marker and the death |
> | CX or MX corner starvation | both pinned at `RPM_SMD_LEVEL_TURBO_NO_CPR` with `kernel/diag-modules/rhodep_pdhold.c` for the whole run — still resets |
> | binding the NoC provider (this file's "one untested cheap lead") | already true: `qnoc-sm6375` is built in since 0065 and `/sys/kernel/debug/interconnect/` is populated |
> | a vendor `no-map` region mainline does not reserve | full re-derivation from `blair.dtsi`: the only one missing is `memshare_mem` (dynamic, 8 MB). **Since closed properly** — the modem was given a real 5 MiB buffer it took and kept, and the reset is unchanged; see "Four more hypotheses down" §1 |
>
> And one non-result that must not be read as an elimination: the QDSS clock was
> voted explicitly and nothing changed, but `clk_smd_rpm_handoff()` already
> votes every RPM clock at probe, so the test changed nothing and **QDSS is not
> excluded**. See `kernel/diag-modules/README.md`.
>
> Four more went down on 2026-09-05 — memshare answered with a real buffer,
> `pmr735a_l1` held on, the CE1/HWKM/PKA RPM clocks, and VDD_MX re-confirmed —
> together with the recovery of the stock vendor device tree from the untouched
> A/B slot. All of it is below, under "Four more hypotheses down" and "The stock
> vendor device tree has been recovered from the phone itself".

## Live confirmation: memshare is silent, and the NoC binding does not help

Two of the leads above were re-checked directly on the device, standalone
reproducer, radio online, `qmicli --dms-set-operating-mode=online` first. Both
runs ended in `bootreason=watchdog`, so both reproduced the reset; the point was
what the instrumentation caught before it.

**memshare emits nothing when the measurement engine starts — empirically.** The
`rhodep-memshare` daemon (QMI 52) was followed live with `journalctl -f` piped
into `/dev/kmsg`, so its every line lands in the ramoops console and survives the
reset. Across the whole standalone run the only memshare traffic is the one
`MEM_QUERY_SIZE (0x0024)` the modem sends **at boot** (answered `size 0`). Between
`session 11 started` and the death there is **not one memshare request** — no
`MEM_ALLOC`, no `MEM_QUERY`, nothing. Console tail, kernel timestamps:

	6660.130 MEMSHARE-LIVE: ... MEM_QUERY_SIZE (0x0024) txn=1   <- boot, 02:30
	6660.130 MEMSHARE-LIVE: ... reporting size 0
	6661.389 rhodep-gnss: operation mode set to standalone
	6661.392 rhodep-gnss: session 11 started; listening 30 s
	6661.392 rhodep-gnss: ------------------------------------  <- console ends, SoC gone

> ### RETRACTED 2026-09-05 — the paragraph that used to stand here
>
> The three lines that followed the console tail above said:
>
> > This closes the memshare hypothesis with evidence rather than inference:
> > reserving `memshare_mem` and starting the daemon with a non-zero offer would
> > not change this, because the engine never asks memshare for anything.
> > `offer=0` stays.
>
> **The conclusion turned out to be right and the reasoning was a non sequitur.**
> It has since been tested properly — the modem was given a real 5 MiB buffer it
> legally owns, took it, and the SoC reset at exactly the same point — so the
> hypothesis really is dead. But it was not dead *for this reason*, and the
> difference matters, because the same argument would have talked anyone out of
> running the experiment that actually settled it.
>
> memshare is a **boot-time negotiation, answered once**. The trace above shows
> that plainly: the modem's only `MEM_QUERY_SIZE` happened **at boot** — the
> `6660.130` is when the journal bridge replayed it into `/dev/kmsg`, and the
> `<- boot, 02:30` annotation is the time it was actually sent, long before the
> session. A modem that was told `size 0` at boot has
> already adapted to having no buffer; it would not ask again at engine start
> whether it had one or not. So "no memshare traffic between `session 11
> started` and the death" is exactly what a modem *holding* a 5 MiB buffer would
> also produce. The observation cannot distinguish the two cases, and the
> inference drawn from it — that offering a real buffer at boot would change
> nothing later — does not follow from it at all.
>
> The one thing that would have made the argument sound is a control run in
> which the offer was non-zero, and that had never been done against the
> positioning path. The only prior non-zero memshare work is session 13c
> (`kali-boot-v93-memshare.img`) in `HANDOFF-SESSION4.md`, which measured
> `offline` / `DeviceNotReady` / `check-personalization-state` on the **LTE
> bring-up** path — at that point in this port's history the radio had never come
> online, so the GNSS reproducer could not have been run on it even in principle.
>
> The experiment was therefore legitimate, unrun, and cheap. What it found is in
> "Four more hypotheses down" below. `offer=0` still stays, but now because the
> buffer demonstrably makes no difference, not because the modem was assumed not
> to want it.

**Binding the NoC provider by hand does not help either.** `qnoc-sm6375` was
loaded with `modprobe --ignore-install` (past the `rhodep-icc-hold.conf`
`install ... /bin/false`); it bound cleanly, no oops, and
`interconnect_summary` went from empty to fully populated (epss L3, the IPA
voting its a2noc/qxm_ipa paths, the display voting mdp0). With the provider
live and votes flowing, the standalone reproducer **still resets**, same
signature. So the "one untested cheap lead" is now tested from the other
direction too (the module bound, not just built-in) and excluded.

Everything above was captured on the shipped `#1-motorola-rhodep` 7.2.0-rc5,
`ipa.ko` now loaded (the `rhodep-ipa-hold.conf` is gone on this image), which
also shows the reset does not need IPA held out.

Every revision of the root `README.md` before this one said GPS was the cheapest
open item in the port — "the modem publishes the location service and
`qmicli --loc-start` works, it has never emitted NMEA, but it has only ever been
tried indoors; testing it outdoors is free and is the next thing to try". That
is wrong on both halves. It was never an indoors problem, and it is not free.

Measured on `kali-boot-v94-STABLE.img`, kernel 7.2.0-rc5, radio online, `ipa.ko`
blacklisted as shipped.

## Four more hypotheses down, 2026-09-05

Four candidates went into this session and none of them survived. Two cost a
boot each and are eliminations with an instrument; one was falsified by reading
code and cost nothing; one was already excluded and now has a reason that holds
up. A fifth is recorded as deliberately *not* probed, with the reason.

None of them changes the reset. It is still: `opmode=standalone`, session starts,
the SoC is gone inside 100 ms, `bootreason=watchdog`,
`powerup_reason=0x00008000`.

### 1. memshare answered 5 MiB instead of 0 — negative, and every gate passed

This is the run that supersedes the retraction above. The question was narrow:
does answering the modem's one startup memshare query with a **real** buffer —
the 5 MiB stock's `qcom,client_3` produces — change what happens when the
measurement engine comes up?

Method, with **no device tree change and no reflash**. A kernel module
(`kernel/diag-modules/rhodep_memassign.c`, `alloc=1`) takes 5 MiB of physically
contiguous CMA at `0xfd300000` and calls `qcom_scm_assign_mem()` to move it from
`QCOM_SCM_VMID_HLOS` to `QCOM_SCM_VMID_MSS_MSA` with RW, then publishes the
region through sysfs; `userspace/modem/memshare-daemon.c` was taught to take the
address from there instead of a compiled-in constant.

**The SCM call returned 0**, and `srcvm` came back `0x8000` = `BIT(0xf)` =
MSS_MSA. That is worth recording on its own: this firmware *does* accept the
HLOS → MSS_MSA transition for an ordinary CMA range, which was not obvious
beforehand and is the thing a mainline memshare driver would have to do.

Ordering, from `journalctl -b -o short-monotonic`, because the assignment has to
be in place before the modem is ever told the address:

	11.66 s   modem firmware loaded
	12.95 s   rhodep_memassign: region assigned to MSS_MSA
	12.98 s   memshare daemon publishes the address
	16.21 s   the modem's MEM_QUERY_SIZE arrives      <- 3.2 s later

Result:

	memshare: reporting size 5242880

and **the modem sent `MEM_ALLOC_GENERIC (0x0022)` in the same millisecond and
took the address.** It decodes exactly as stock's client 3 — `num_bytes
0x500000`, `client_id 1`, `proc_id 0`, `alloc_contiguous 1` — and it never sent
`MEM_FREE`. The device then ran healthy for about 100 s with the region
MSS_MSA-owned: no XPU fault, no SError, no new errors of any kind.

Then `opmode=standalone`, with the mode confirmed applied (`operation mode set
to standalone` present in the console record, which is the check that stops a
failed set from looking like a survival): **the SoC reset within milliseconds of
`session 11 started`. Zero of the 40 seconds ran.** Next boot,
`androidboot.bootreason=watchdog` / `powerup_reason=0x00008000`, calibrated
against the clean `systemctl reboot` immediately before it, which recorded
`reboot` / `0x00004000`. Reproduced across two boots.

So the modem genuinely wanted the buffer, genuinely received it, was in a
position to use it, and the reset is unchanged and just as fast. That is a
strong elimination rather than a weak one: unlike the `offer=0` runs there is no
argument left that the modem was merely working around a refusal.

The full gate-by-gate table, the console tail and the residual caveat about the
CMA cacheable alias are in
[`kernel/diag-modules/README.md`](../../kernel/diag-modules/README.md),
"The 5 MiB memshare run".

Three sub-findings from building it, each of which would otherwise cost somebody
a session:

* `CONFIG_ARCH_FORCE_MAX_ORDER=10` caps `alloc_pages()` at 4 MiB on this kernel,
  so there is no "round 5 MiB up to 8 MiB" option. CMA via `dma_alloc_pages()`
  is required.
* **CMA memory keeps a cacheable linear alias after the SCM call**, which
  `no-map` memory would not. The allocator's own zeroing goes through that
  alias, so dirty lines for the buffer exist at the moment of handover and their
  write-back would itself be a violation; the range has to be cleaned to the
  point of coherency before the assignment. Speculation through the alias could
  *not* be fenced — `set_memory_valid()` and `set_direct_map_invalid_noflush()`
  are not exported — so that is the residual risk of doing this without a dts
  reservation. Not observed to bite.
* **Do not `rmmod` it while the modem holds the buffer.** The reverse SCM call
  does not return, that CPU stops answering IPIs, and the next unrelated
  `smp_call_function()` user stalls forever. Correct teardown is to remove the
  modprobe.d/drop-in configuration and reboot.

### 2. `pmr735a_l1` held on — negative

The one rail the stock tree keeps on and this port does not. Stock enables it at
boot through RPM resource `ldoe` id 1, with `qcom,proxy-consumer-enable`,
`qcom,init-voltage = <600000>` and `qcom,proxy-consumer-current = <62000>`.
Mainline declares the same LDO and references it from nowhere, so on this port
it reads:

	l1   0  0  0  unknown  576mV   0mA        <- off, at its floor, no users

It earned a boot because it is **the only proxy-enabled rail in the whole stock
tree with no identifiable AP-side consumer** — which is the shape of a rail that
exists for a subsystem the AP does not model, and the mpss is the obvious
candidate.

`kernel/diag-modules/rhodep_reghold.c` takes it with
`regulator_get_optional(NULL, "l1")`. That works with no consumer node at all
because `regulator_dev_lookup()` falls back to `regulator_lookup_by_name()`,
which matches `desc->name` over the regulator class; plain `regulator_get()`
would have handed back the dummy regulator with a `dev_warn` and looked exactly
like success. Held:

	l1   1  2  0  unknown  600mV        <- enabled, at stock's voltage
	   l1  1              62mA          <- the consumer row

**The reset is unchanged**: same sub-100 ms latency from `session 11 started`,
`bootreason=watchdog`.

Two caveats, because this elimination is not as complete as it looks:

* **The current vote almost certainly never left the AP.** `set_load()` returned
  0 and the 62 mA shows in the consumer row, but `drms_uA_update()` bails out
  unless `REGULATOR_CHANGE_DRMS` is in `valid_ops_mask`, and
  `of_get_regulation_constraints()` sets that only for
  `regulator-allow-set-load`, which this dts does not have. So **enable and
  voltage were real; the load — and therefore the LDO's HPM/LPM mode — probably
  was not.** If the rail's *mode* is what the modem needs, this run did not test
  it. `kernel/patches/0121` is the dts version that would.
* **Stock asserts this rail at boot, not ~2300 s in.** The module was loaded on
  a running system, so the rail was off for the whole earlier part of the boot.
  If anything latched during modem bring-up while it was off, holding it later
  cannot undo that.

### 3. CE1 / HWKM / PKA RPM clocks — falsified by analysis, no boot spent

Stock has a whole crypto subsystem mainline sm6375 lacks: `qcedev@1b20000`,
`qcrypto@1b20000`, `qseecom@c1800000`, `mcd`, `hwkm@4440000` and `qrng@4453000`.
Nothing on this port claims their RPM clocks, so `ce1_clk`, `ce1_a_clk`,
`hwkm_clk`, `hwkm_a_clk`, `pka_clk` and `pka_a_clk` all read `enable_cnt 0`.

It looked like a good lead for a specific reason. The vendor NoC id map has

	ICBID_MASTER_MSS_NAV            27
	ICBID_SLAVE_MSS_NAV_CE_MPU_CFG  218

and the string `NAV_CE_MPU_CFG` appears in the modem firmware itself. A
positioning engine reaching a crypto MPU whose clock nobody voted is exactly the
class of "bus access into something unclocked" this whole file keeps circling.

**It is nevertheless already covered by v99, and running it would re-test that.**
`clk_disable_unused()` returns *early* when `clk_ignore_unused` is set — before
it reaches `clk_unprepare_unused_subtree()` — so the v99 boot left **every**
`clk_smd_rpm` clock still holding the `INT_MAX` handoff vote
`clk_smd_rpm_handoff()` writes into both the active and the sleep set at probe.
These six included. v99 still reset.

The obvious objection is the caveat recorded against v99 in the table below —
"this only moved 3 clocks (249 → 246 off), so it is weaker evidence than it
looks". **That caveat does not apply here.** The 249/246 count was read off the
hardware-enable column, and for `clk_smd_rpm` clocks that column is a constant
`Y`: the driver has no `.is_enabled` op, so `clk_core_is_enabled()` returns true
by default. It could never have shown these six changing state in either
direction. The count measured a different population of clocks entirely.

`kernel/diag-modules/rhodep_cehold.c` was written anyway and is verified to move
all six from `enable_cnt 0` to `1` and back, so the instrument exists if a
future finding makes it worth a boot. It has not been spent.

### 4. VDD_MX — already excluded, and now for a reason that holds up

`rhodep_pdhold.c` pins **rpmpd power domains**, not regulators, and the earlier
"CX and MX pinned at `TURBO_NO_CPR`, still resets" result was recorded without
checking that those two things reach the same place. On this SoC they do:

* rpmpd `SM6375_VDDMX` is `RPMPD_RWMX`, id 0, key `KEY_LEVEL`.
* Stock's `pmr735a_s1_level` is `qcom,resource-name = "rwmx"`, id `<0x00>`, with
  `qcom,use-voltage-level` — i.e. `vlvl`.

Same RPM resource, same id, same key. `pdhold` additionally sends
`KEY_ENABLE`(`swen`)`=1` through `rpmpd_power_on()`, and clamps to
`RPM_SMD_LEVEL_TURBO_NO_CPR = 416` against stock's TURBO = **384**. So the test
was a **strict superset** of what stock asks for, not an approximation of it.

Independently: the stock `qcom,mss@06000000` node has only `vdd_cx-supply` and
**no `vdd_mx-supply`**. The vendor does not vote MX for the modem either, and
mainline's `.proxy_pd_names = { "cx", NULL }` matches the vendor exactly. There
is nothing here to fix.

**And one thing that must not be tried.** Adding `"mss"` to
`sm6375_mpss_resource.proxy_pd_names` — an obvious-looking "the modem should
have its own domain" change — would be a **regression, not a fix**. Every device
tree in the kernel that declares an `"mss"` power domain uses `&rpmhpd`, never
`&rpmpd`; the string `"mss"` does not occur anywhere in `rpmpd.c`; and
`sm6375_rpmpds[]` contains only CX, MX, GX and LPI. The attach would return
`-ENODATA` and the modem would stop probing altogether.

### 5. CX iPeak Limit Manager — weak, and deliberately not probed

`cx_ipeak@3ed000` and `cxip-cdev@3ed000` exist in stock and are absent in
mainline, which put them on the list. They came off it on their own evidence:
**every** consumer of the `cx_ipeak` phandle in the stock tree is a camera node
(12 of them, `qcom,cam-cx-ipeak`) plus one video codec (`qcom,vidc@5a00000`).
There is no modem client and no GNSS client.

It was then **deliberately not probed**, and the reason is worth stating because
it is a general rule for this device: `0x3ed000` sits inside the same TCSR
aperture as `0x3d3000`, and a plain `readl()` at `0x3d3000` takes the SoC down
in silence with the *identical* signature to the bug under investigation. A read
there could not be distinguished from reproducing it, so the experiment would
have produced an uninterpretable result at the cost of a boot.

## The stock vendor device tree has been recovered from the phone itself

Every previous session in this directory argued against `blair.dtsi` as read on
GitHub, or against hand transcriptions of it. That is no longer necessary: the
**stock device tree the bootloader actually gives this board** has been
extracted from the device.

It came out of the **unused A/B slot**. pmOS runs from `_a` and has overwritten
it — `dtbo_a` is all zeros — but nothing this port ever did touched `_b`:

* `dtbo_b` is an Android DTBO table (magic `0xd7b7ab1e`), 4 entries, every one
  `model = "rhodep"`, `compatible = "qcom,blair-rhodep", "qcom,blair-moto",
  "qcom,blair"`.
* `vendor_boot_b` is a boot header v3 carrying a **323 677-byte FDT**,
  `model = "Qualcomm Technologies, Inc. Blair"` — the base DTB.

Merged with `fdtoverlay` into a single 21 894-line source. **The rhodep overlays
touch nothing in `reserved-memory`, `qcom,memshare`, `qcom,msm-imem` or the
`mss` node**, so for all of those the base DTB is authoritative on its own and
the overlay merge is not load-bearing.

The stock `/vendor` partition was recovered the same way, out of `super` (LP
metadata slot 0, `vendor_a` at offset 3 476 029 440, size 632 545 280, mounted
read-only through a loop device).

### What it says that is relevant here

**There is no GNSS or NAV device node in the application-processor device tree
at all — on either side.** Not in the base DTB, not in any of the four overlays.
The measurement engine, the `nav_cc` clock controller, `NAV_CE_MPU_CFG` and the
eLNA control lines (`gnss_l1_elna_ctrl`, `gnss_l2_5_elna_ctrl`) are all inside
the MPSS. Stock Android runs GNSS on this board with a GCC driver that has **zero
NAV clocks**. So "mainline is missing an AP-side NAV clock" cannot be the cause
of the reset: stock does not have one either.

`qcom,rmtfs_sharedmem@0` carries `qcom,vm-nav-path`, and mainline already
translates that correctly to `qcom,vmid = <0x0f 0x2b>` — `QCOM_SCM_VMID_MSS_MSA`
and `QCOM_SCM_VMID_NAV` — confirmed live on the device. **The NAV VMID is not a
gap.** That closes a question this document has been carrying implicitly.

`qcom,mss@06000000` in full, since several arguments above depend on it:

	compatible = "qcom,pil-tz-generic";
	clocks = <...>;  clock-names = "xo";      <- one clock, and it is XO
	vdd_cx-supply = <...>;                    <- and no vdd_mx-supply
	qcom,vdd_cx-uV-uA = <0x180 0x186a0>;
	qcom,proxy-timeout-ms = <0x2710>;
	qcom,pas-id = <4>;
	qcom,ssctl-instance-id = <0x12>;
	qcom,smem-id = <0x1a5>;

and the memshare node, which is what §1 above was built to answer:

	qcom,memshare {
		qcom,client_1 { qcom,peripheral-size = <0x00>; qcom,client-id = <0x00>;
		                qcom,allocate-boot-time; label = "modem"; };
		qcom,client_2 { qcom,peripheral-size = <0x00>; qcom,client-id = <0x02>; };
		qcom,client_3 { qcom,peripheral-size = <0x500000>;  qcom,client-id = <0x01>;
		                memory-region = <&memshare_region>;
		                qcom,allocate-on-request; label = "modem"; };
	};

	memshare_region {
		compatible = "shared-dma-pool";
		no-map;
		alloc-ranges = <0x00 0x00 0x00 0xffffffff>;
		alignment = <0x00 0x100000>;
		size = <0x00 0x800000>;
	};

— 8 MiB, `no-map`, 1 MiB aligned, **dynamically placed** (no fixed `reg`,
`alloc-ranges` covering the whole 32-bit space), of which client 3 advertises
5 MiB. That is exactly the answer the run in §1 gave the modem.

The `reserved-memory` list also confirms `removed_region@c0000000` at
`0x7100000` **from the device's own firmware**, not from a GitHub tree — see
[`REMOVED-MEM-SM6375.md`](REMOVED-MEM-SM6375.md).

`qcom,msm-imem@c125000` is present with **eight** children:
`mem_dump_table@10`, `dload_type@1c`, `diag_dload@c8`, `restart_reason@65c`,
`boot_stats@6b0`, `kaslr_offset@6d0`, `pil@6dc` (`pil-disable-timeout`) and
`pil@94c`. Mainline has the same aperture as `sram@c125000`
(`compatible = "qcom,sm6375-imem", "syscon", "simple-mfd"`) but declares
**one** child, `pil-reloc@94c` — stock's `pil@94c`. The other seven are absent,
`mem_dump_table@10` among them.

Whether any of that matters is **not established**. Nothing in this session
tested it, and there is no evidence tying imem to the reset; it is recorded
because it is a concrete, checkable difference that was previously invisible.

### The stock GNSS configuration, for the record

From the recovered `/vendor`. None of it implies an AP-side hardware resource,
which is the reason it is recorded rather than acted on:

| file | what it says |
| --- | --- |
| `gps.conf` | `NMEA_PROVIDER=0` — NMEA is generated **by the modem**, not the AP. `CAPABILITIES=0x17`, `LPP_PROFILE = 2`, `MODEM_TYPE = 1`, `NTP_SERVER=time.xtracloud.net`, `XTRA_CA_PATH=/usr/lib/ssl-1.1/certs`, and every `RF_LOSS_*` is `0`. |
| `izat.conf` | `lowi-server` and `xtra-daemon` `PROCESS_STATE=ENABLED`; `slim_daemon` `DISABLED`; `SAP=MODEM_DEFAULT`. |
| `sap.conf` | `SENSOR_CONTROL_MODE=2` — **sensors are not used by GNSS** on this board. Consistent with the ADSP already having been eliminated on the reproducer. |
| `gnss_antenna_info.conf` | present but **unpopulated**: the values are Qualcomm's template examples (`PC_OFFSET_0 = 1.2 0.1 3.4 0.2 5.6 0.3`, `... = 11.22 33.44 55.66 77.88`), not a survey of this antenna. |

There is also a `/vendor/rfs/apq/gnss/` remote-filesystem root with no equivalent
on this port. Whether the modem ever reaches for anything under it is untested —
`tqftpserv -d` across a whole boot showed the modem asking for nothing during a
GNSS session, so it probably does not, but that is inference.

### These artefacts should be committed

They currently live outside the repo, in `/tmp/opencode/rhodep/`
(`base_0.dts`, `dtbo_b_0..3.dts`, `merged.dts`, `live-mainline.dts`,
`vendor-gnss/etc/*.conf`), **and `/tmp` will not survive**.

Recovering them again means unpacking `vendor_boot_b` and mounting `vendor_a`
out of `super` on a device that still has an untouched `_b` slot — which is true
today and stops being true the moment anyone flashes the other slot. This is the
single highest-value missing artefact in the repository: every "the vendor does
X" claim in this directory could be checked against it in seconds instead of
being re-derived.

**Recommendation: commit them under `docs/vendor-dt/`**, with the extraction
method written next to them, and with the provenance (slot `_b`, dates, sizes)
recorded so a later reader knows which firmware they describe.

## What actually happens

	rhodep-gnss: device open
	rhodep-gnss: LOC client allocated (cid 1)
	rhodep-gnss: event mask: min (0x7)
	rhodep-gnss: events registered
	rhodep-gnss: session 11 started; listening 40 s
	rhodep-gnss: ----------------------------------------------------------
	<console ends here>

That is the tail of `/var/lib/systemd/pstore/console-ramoops-0` after the reset.
The script writes a heartbeat to `/dev/kmsg` every second and **not one
heartbeat survives**, so the SoC dies less than a second after
`QMI_LOC_START` returns success. Next boot:

	androidboot.bootreason=watchdog
	androidboot.powerup_reason=0x00008000

No panic, no oops, no SMMU fault, console stopping mid-line. That is the same
signature as the IPA/LTE reset in `HANDOFF-SESSION4.md` §5.

Reproduced 3/3: once with the full event mask, twice with the minimal one
(NMEA + position report + satellite info). The event mask does not matter.

## The bisection: it is the session running, and nothing else

Run on `kali-boot-v98-removed-mem.img`. Each row cost one reboot. The script
takes bare-word flags (`bare`, `nostart`, `noreg`, `stopnow`) and a mask group
list, so every row below is one invocation of `scripts/rhodep-gnss-test.py`.

| what was done | result |
| --- | --- |
| register events (NMEA+pos+SV), never start a session | **survives**, 0 indications |
| register + `QMI_LOC_START` with optional TLVs | reset |
| register + start with **session id only**, no optional TLVs (`bare`) | reset |
| register with an **empty event mask** (0x0) + start | reset |
| **no** `register_events` at all + start (`noreg`) | reset |
| `qmicli --loc-start`, which frees the client immediately after | **survives** |

Conclusions, in order of how much they narrow it:

1. **It is not the indications.** An empty registration mask still resets, and
   skipping `register_events` entirely still resets. No indication was ever
   delivered in any run.
2. **It is not the optional start TLVs.** A start carrying nothing but the
   session id resets.
3. **It is not `QMI_LOC_START` as a message.** `qmicli` sends the same message
   and survives — because it releases the QMI client on exit, which ends the
   session a few milliseconds later. The engine never gets to run.
4. **It is the session being allowed to stay alive.** That is the whole trigger.

**It is not the WWAN radio either.** Repeated with the modem in `low-power`
instead of `online`: same reset. So the cellular RF path is not involved, and
this is the GNSS engine on its own.

## It dies in under 100 ms

The script heartbeats into `/dev/kmsg`. At one beat per second, none survived.
Lowered to one per 100 ms, still none:

	[  236.346904] rhodep-gnss: session 11 started; listening 20 s
	[  236.346967] rhodep-gnss: ----------------------------------------
	<console ends>

The mainloop never reaches its first 100 ms timer. The SoC is gone between
236.347 and 236.447, i.e. **within 100 ms of `QMI_LOC_START` returning
success** — effectively synchronous with the engine starting.

Dynamic debug was enabled on `qrtr`, `qrtr_smd`, `qcom_glink_smem`,
`qcom_q6v5`, `qcom_q6v5_pas`, `qcom_sysmon` and `rpmsg_char` for one of the
runs. **Not one kernel line appears between the start and the reset.** Whatever
kills it does not go through those subsystems in any way the AP gets to log.

An instant, silent death with no CPU exception is the signature this port has
already met once: §1 of this directory's README describes the IPA pointed at
`0x5847000`, "a hole on this SoC where any access, even a single 32-bit read,
resets the SoC instantly with no CPU exception and no log output". That makes a
bus access into something unpowered or unclocked the leading hypothesis, with
the mpss rather than the AP as the initiator.

### The hang is instant; the watchdog is just the undertaker

`watchdog_thresh=5` is on the cmdline, so the APSS watchdog cannot fire in under
100 ms. The sequence is therefore: the AP wedges immediately, stops petting the
watchdog, and the watchdog resets the board seconds later.
`bootreason=watchdog` describes the reset, not the fault.

What wedges is **every** CPU, instantly — a Python mainloop on one core cannot
even reach its next 100 ms timer. A single core stuck on an unresponsive load
would not do that; a hung interconnect would.

### Ruled out on the device, each one reboot

| suspect | how it was excluded |
| --- | --- |
| the QMI indications | empty mask, and `noreg`, both still reset |
| the optional `QMI_LOC_START` TLVs | `bare` start still resets |
| `QMI_LOC_START` as a message | `qmicli` sends it and survives |
| the WWAN radio / cellular RF | resets with the modem in `low-power` |
| `removed_mem` being 32 MB short | fixed and confirmed in v98; still resets |
| clocks Linux switched off as unused | `clk_ignore_unused pd_ignore_unused` (v99); still resets. Caveat as written: "this only moved 3 clocks (249 -> 246 off) … weaker evidence than it looks". **That caveat is narrower than it reads** — the 249/246 count came from the hardware-enable column, which is a constant `Y` for `clk_smd_rpm` clocks and cannot show them changing at all. For every RPM clock, v99 is strong evidence: `clk_disable_unused()` returns early under `clk_ignore_unused`, so all of them kept the `INT_MAX` handoff vote in both the active and the sleep set |
| power domains left unpowered | `pd_ignore_unused`, same run |
| memshare handing out live RAM | the daemon runs with `offer=0`, answers `MEM_QUERY_SIZE` with 0 and refuses `MEM_ALLOC`; no allocation in any boot's journal |
| **memshare being answered with a real buffer** | 2026-09-05. 5 MiB of CMA at `0xfd300000`, `qcom_scm_assign_mem()` HLOS -> MSS_MSA **returned 0**, `memshare: reporting size 5242880`, and the modem sent `MEM_ALLOC_GENERIC (0x0022)` and took the address. Still resets, same millisecond-scale latency, two boots. See "Four more hypotheses down" §1 |
| **`pmr735a_l1`, the rail stock holds and mainline does not** | held on at 600 mV with `rhodep_reghold.c` (`0,0 -> 1,2`, `62mA` in the consumer row) for the whole run; still resets. Caveat: `set_load` almost certainly never reached RPM, so the LDO's *mode* is not tested — §2 |
| **CE1 / HWKM / PKA RPM clocks** | not spent. `clk_ignore_unused` on v99 already left all six holding their `INT_MAX` handoff vote, and v99 reset. `rhodep_cehold.c` exists and is verified to move them 0 -> 1 if this is ever worth re-opening — §3 |
| a missing VDD_MX vote for the mpss | the vendor declares only `vdd_cx` for `pil_modem` too — now confirmed against the **recovered stock DTB**: `qcom,mss@06000000` has `vdd_cx-supply` and no `vdd_mx-supply`. And `rhodep_pdhold`'s MX pin is a strict superset of stock's request (same RPM resource `rwmx` id 0, level 416 against stock's 384, plus `swen=1`) — §4 |
| an AP-side SMMU stream id | the mpss is not behind `apps_smmu`; it uses its `no-map` carveout directly |
| a missing AP-side NAV clock | the recovered stock device tree has **no GNSS/NAV node at all**, on either side, and stock's GCC driver has zero NAV clocks. There is nothing for mainline to be missing |
| the NAV VMID on the rmtfs region | mainline already emits `qcom,vmid = <MSS_MSA NAV>`, which is the vendor's `qcom,vm-nav-path`; confirmed live |

### Instrumented kernel: the AP is innocent, proven

`kali-boot-v100-instrumented.img` was built for this (see "The instrumented
kernel" below). With `HARDLOCKUP_DETECTOR_BUDDY`, `FUNCTION_TRACER` and
`PSTORE_FTRACE`, and the ramoops region re-split to give ftrace 0x40000, the
last function calls per CPU now survive the reset. Three runs, three findings:

**1. Every CPU was idle when it died.** The tail of `ftrace-ramoops-0`, minus
the noise, is the whole story:

	ts:33185-33192  CPU:0  qcom_glink_native_rx / qcom_glink_handle_rx_done /
	                       qcom_glink_tx            <- the modem's reply to START
	ts:33195-33196  CPU:5  __sys_recvfrom / ____sys_recvmsg
	                                                <- our process reading "success"
	ts:33203-33263  CPU:7  icc_set_bw <- _set_opp, three times, periodic
	                                                <- cpufreq voting L3, routine
	everything else:       cpuidle_get_cpu_driver / cpuidle_not_available
	                                                <- all 8 CPUs in do_idle
	<cut>

After the AP reads the "session started" reply it touches nothing else. It is
not spinning on a lock, not in a driver, not talking to the modem. It is idle,
and then it is gone.

(`cpuidle_not_available` in that trace is a separate finding: this device has no
cpuidle driver at all, so `do_idle` takes the "not available" path every time.
See the note at the end of this file.)

**2. No panic, ever, and that is the informative part.** Zero calls to
`kmsg_dump` or `pstore_dump` in any run, and no `dmesg-ramoops` produced. The
`panic_in_progress` / `panic_on_this_cpu` entries in the trace are the routine
checks `watchdog_timer_fn` and printk make, not a panic — that misreading cost
one run.

The buddy hardlockup detector works by having each CPU check its neighbour. It
fired `watchdog_hardlockup_check` on every CPU right up to the end and never
reported anything. For that to happen with the machine dying, **all CPUs have to
stop in the same instant** — a single wedged core with the others alive is
exactly what it would have caught.

**3. The logging path was healthy to the last moment.** `pstore_console_write`
appears at ts:992 of the final run, so writes to the ramoops region were still
completing. The reset is not "the console died first".

Also checked and excluded along the way: `earlycon` never registers on this
device (only `tty0` and `ramoops-1` are consoles), and unbinding the framebuffer
console changed nothing.

**Conclusion: the application processor does not cause this.** It is idle, it is
not hung on anything, it never gets a chance to fault, and all eight cores stop
together. That is a hardware-level reset of the whole SoC, initiated somewhere
the AP cannot see — the mpss, TrustZone, or a fatal bus error escalated by
hardware. Further progress needs visibility on the modem side (DIAG/QXDM, or a
modem ramdump), not more AP-side instrumentation.

### What is needed next, and why it needs a kernel build

The instrumentation to go further is not compiled in:

	# CONFIG_HARDLOCKUP_DETECTOR is not set     (CONFIG_HAVE_..._BUDDY=y, so it can be)
	CONFIG_HAVE_FUNCTION_TRACER=y               (but FUNCTION_TRACER itself is off)
	CONFIG_PSTORE_RAM=y                         (PSTORE_FTRACE absent)

With `HARDLOCKUP_DETECTOR` + `HARDLOCKUP_DETECTOR_BUDDY` a wedged CPU produces a
backtrace instead of silence — and `AUDIO-SM6375.md` §7 notes it was deliberately
left off because it panics, which is exactly what is wanted here. With
`FUNCTION_TRACER` + `PSTORE_FTRACE`, ramoops keeps the last function calls per
CPU across the reset, which would say what the AP was doing when it stopped.

*(Both of these have since been done — `kali-boot-v100-instrumented.img` above is
the result, and it is what cleared the AP.)*

**The "cheaper experiment first" this section used to recommend is spent.** It
said: boot `kali-boot-v97-ipa-interconnect.img`, `modprobe qnoc-sm6375`, then run
the reproducer, "the one untested cheap lead left". It was run, from both
directions — the provider built in since 0065, and the module bound by hand — and
the reproducer still resets. See the top of this file.

## Ruled out: the removed_mem reservation

`kali-boot-v98-removed-mem.img` gives the secure firmware the full 0x7100000 it
owns instead of mainline's 0x5100000, confirmed on the running device:

	c0000000-c70fffff : reserved
	c7100000-f38fffff : System RAM

**It still resets.** So the 32 MB discrepancy documented in
[`REMOVED-MEM-SM6375.md`](REMOVED-MEM-SM6375.md) is a real correctness bug and
worth upstreaming, but it is not the cause of this and, by extension, probably
not the cause of the LTE reset either.

## Why nobody had seen it

Because `qmicli` cannot start a GNSS session properly, and the way it fails is
silent. The LOC session belongs to the QMI client that created it, and:

- `qmicli` refuses two LOC actions in one invocation:
  `error: too many LOC actions requested`. So `--loc-start` and
  `--loc-follow-nmea` cannot share a client.
- Without `qmi-proxy` the QMI client dies with the process — the CID is bound
  to the QRTR socket — so a second invocation gets
  `error: operation failed: Unknown client 1 for service loc`.
- With `qmi-proxy` the CID survives, but two processes cannot use it at once:
  both get `Transaction timed out` on `Register Events`.
- `--loc-timeout` only applies to `--loc-get-position-report` and
  `--loc-get-gnss-sv-info`, not to the `--loc-follow-*` actions.

So every shell-only attempt ends up in one of two states: a session started by a
client with no indications registered, or indications registered on a client
with no session. **Both are quiet.** No NMEA, no satellites, no position, no
reset — which reads exactly like "GPS works but we are indoors", and is what the
old README concluded.

Doing it from a single client needs libqmi directly. That is
`scripts/rhodep-gnss-test.py`, ~200 lines of Python against the `Qmi` GIR
bindings. It needs `gir1.2-qmi-1.0`, which pulls in no held packages
(`gir1.2-qrtr-1.0` comes with it; verified with `apt-get install -s`).

## What the location service does answer

The control plane is entirely healthy, which is worth recording because it
means the modem's location stack is present and configured, not absent:

| query | answer |
| --- | --- |
| `qrtr-lookup` | service **16**, "Location service (~ PDS v2)", node 0 port 106 |
| `--loc-get-operation-mode` | `standalone` |
| `--loc-get-nmea-types` | `gga, rmc, gsv, gsa, vtg, pqxfi, pstis` |
| `--loc-get-predicted-orbits-data-source` | 3 XTRA servers, `path1..3.xtracloud.net` |
| `--loc-get-predicted-orbits-data-validity` | valid from 2026-07-28T21:00:00Z for 168 h |
| `--loc-inject-time` | `Successfully injected time` |
| `--loc-start` / `--loc-stop` | both succeed |
| `--loc-set-engine-lock=none` | `Successfully set engine lock` |
| `--loc-get-engine-lock` | `error: couldn't get engine lock: missing` |

Two details from that table are worth keeping:

- **The XTRA almanac on the device expired on 2026-08-04.** Stale predicted
  orbits make a cold fix slower; they do not stop an engine from emitting empty
  NMEA. It is not the explanation, but any future test should refresh it.
- **`get-engine-lock` returns no TLV while `set-engine-lock` succeeds.** The
  write path is accepted and the read path has nothing to report, which is a
  small hint that the engine's state is not actually populated.

## Why this matters more than GPS

**It is a five-second reproducer for what may be the same bug that blocks
mobile data.**

The open bug in §5 of `HANDOFF-SESSION4.md` needs `ipa.ko` loaded *and* the
modem attached to LTE, and then 3 to 10 minutes of waiting. That makes it
expensive: it needs a SIM that actually registers, ModemManager, and a reset
costs a reboot at an unpredictable moment. Session 15 could not even run the
decisive experiment because attaching to LTE without the IPA needs the attach
APN set by hand.

This one needs no SIM, no IPA, no ModemManager and no network. Radio online, run
one script, reset in under a second, every time. If the two resets share a
cause, the whole investigation gets a cheap deterministic trigger.

**The hypothesis this suggests** — stated as a hypothesis, because it has not
been tested — is that neither reset is really about the IPA. Both are the modem
being asked to move data to the application processor: the IPA case is the LTE
data path, this one is a stream of QMI indications. What they have in common is
mpss doing sustained work against AP memory. That would explain why every IPA
side correction in patches 0044-0048 was individually right and none of them
fixed anything.

Against the hypothesis: the IPA reset takes minutes and this takes under a
second, so if it is one bug the trigger rate differs by three orders of
magnitude.

## What to do next

**Three of the four steps this section used to list are done.** Kept, struck
through, so nobody repeats them:

1. ~~Get a log out of the modem side by watching TFTP~~ — done. `tqftpserv` and
   `rmtfs` journals were bridged into `/dev/kmsg` with the bridge verified live
   first, and there is **not one line** between the marker and the death. The
   modem asks the AP for nothing. (The *other* half of step 1 — real modem-side
   logging, DIAG/QXDM or a ramdump — is not done and is now the whole remaining
   plan; see below.)
2. ~~Bisect the `QMI_LOC_START` optional TLVs~~ — done. A `bare` start carrying
   nothing but the session id resets. See the bisection table above.
3. ~~Check whether it is the indications~~ — done. An empty event mask resets,
   and skipping `register_events` entirely resets. No indication was ever
   delivered in any run.
4. **Compare against the IPA reset with the same instrumentation.** Still open,
   and partly answered: `HANDOFF.md` reports the two deaths as indistinguishable
   at the PMIC and at the bootloader's bitmask. What has not been done is a
   side-by-side of the ftrace records.

**What is left, in the order it is worth doing.**

1. **Modem-side visibility.** DIAG/QXDM or a modem ramdump. The AP has been
   cleared with evidence and four more AP-side hypotheses died in one session on
   2026-09-05 (see "Four more hypotheses down"); continuing to guess at a
   processor nobody can see into has a poor recent record.
   `docs/modem-diag-wip/` has the state of the DIAG work, including why the
   channel will not open on this firmware.
2. **QDSS/CoreSight as a subsystem** — 110 device tree nodes downstream, zero in
   mainline. Note what is *not* left of this: its RPM clock half is covered by
   the `clk_ignore_unused` argument in §3 above, so the open question is the
   nodes, not the clock.
3. **The `pmr735a_l1` operating mode.** §2 held the rail on but almost certainly
   never delivered the 62 mA load vote, so the LDO's HPM/LPM mode is untested.
   That needs `regulator-allow-set-load` in the dts and therefore a flash;
   `kernel/patches/0121` is written.
4. **The seven `msm-imem` children mainline does not declare**, newly visible
   from the recovered stock DTB. Completely untested, no evidence tying them to
   anything, listed last for that reason.

And before any of it: **commit the recovered stock device tree** (see above).
It is free, it is at risk, and every item on this list is easier to argue about
with it in the repository.

## Reproducing

	sudo apt-get install -y gir1.2-qmi-1.0
	sudo qmicli -d qrtr://0 --dms-set-operating-mode=online
	sudo ./scripts/rhodep-gnss-test.py 40 min      # <-- phone reboots here

	# after the reboot:
	sudo grep -a rhodep-gnss /var/lib/systemd/pstore/console-ramoops-0
	tr ' ' '\n' < /proc/cmdline | grep bootreason

Put the radio back when finished, so the phone is not holding it up for nothing:

	sudo qmicli -d qrtr://0 --dms-set-operating-mode=low-power

### Verify the script before every run — a survival can be a lie

On 2026-09-05 the on-device copy at `/home/kali/rhodep-gnss-test.py` was found
**replaced by 12 507 bytes of ASCII spaces**. That is the same byte count as the
good copy, so `ls -l` looked correct and nothing about it drew attention. How it
happened is not known; what matters is that a run against it starts nothing,
prints nothing useful, and the phone stays up — which is indistinguishable from
the survival this reproducer exists to detect.

Two commands, before any run whose result you intend to believe:

	md5sum ~/rhodep-gnss-test.py            # against the repo copy
	python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" \
		~/rhodep-gnss-test.py

The same rule applies to `opmode=`: always confirm `operation mode set to ...`
in the console record. A set that fails falls through to `standalone`, and the
script starts the session anyway (see below), so *both* directions of this
experiment can lie if nobody checks.

### The reference script has only two of the three safety gates

`scripts/rhodep-gnss-test.py` sets the operation mode and then, on failure:

	log("set_operation_mode(%s) failed: %s -- starting anyway" % (OPMODE, e))

It checks the request ack, and it does **not** wait for the
set-operation-mode *indication*, which is where LOC reports the real outcome. So
a mode that is acked but never applied leaves the engine in `standalone` and the
script starts a session on it — which is precisely the path that reboots the
phone. That is acceptable in a bisection tool whose job is to cause the reset,
and it is not acceptable anywhere else.

`userspace/gnss/rhodep-gnss-daemon` has all three gates (ack, indication,
`get_operation_mode` readback) and refuses to send `QMI_LOC_START` if any of them
fails. See [`../GPS-USERSPACE.md`](../GPS-USERSPACE.md).

## The instrumented kernel

`kali-boot-v100-instrumented.img` is the diagnostic build. It is **not** for
daily use: `FUNCTION_TRACER` adds a hook to every kernel function and the phone
is measurably slower.

Config deltas from the shipped kernel:

	CONFIG_HARDLOCKUP_DETECTOR=y
	CONFIG_HARDLOCKUP_DETECTOR_BUDDY=y
	CONFIG_BOOTPARAM_HARDLOCKUP_PANIC=y
	CONFIG_FTRACE=y
	CONFIG_FUNCTION_TRACER=y
	CONFIG_PSTORE_FTRACE=y

Device tree delta, in patch 0008: ramoops had `ftrace-size = <0x0>`, so there
was nowhere to record. The region is fixed at 0xbf800 total, so console was
halved to make room:

	console-size = <0x40000>    (was 0x80000)
	record-size  = <0x3f800>    (unchanged, this is where a panic would land)
	ftrace-size  = <0x40000>    (was 0)

It also carries patch 0049 (`removed_mem`).

### Using it without hanging the phone

**Do not enable `record_ftrace` with an empty filter.** Tracing every kernel
function into persistent storage wedged the device hard enough to need a
power-button reset — it did not even reboot. Filter first, then enable:

	cd /sys/kernel/debug/tracing
	echo > set_ftrace_filter
	for p in '*q6v5*' 'rproc_*' '*glink*' 'qrtr*' 'panic*' '*watchdog*'; do
		echo "$p" >> set_ftrace_filter
	done
	wc -l < set_ftrace_filter          # a few hundred is fine, not 60000
	echo 1 > /sys/kernel/debug/pstore/record_ftrace

	# confirm the phone is still responsive before doing anything else
	uptime

After the reset, the trace is at `/var/lib/systemd/pstore/ftrace-ramoops-0`
(systemd-pstore moves it out of `/sys/fs/pstore`). Read it with the routine
noise stripped:

	grep -vE 'cpuidle_|tick_nohz_restart|panic_in_progress <- watchdog_timer_fn' \
		/var/lib/systemd/pstore/ftrace-ramoops-0 | tail -30

`record_ftrace` does not survive a reboot, so a hung phone is recovered by
power-cycling it and nothing else.

## Side finding: this device has no cpuidle at all

Visible in every trace as `cpuidle_not_available <- do_idle`, and confirmed
directly: `/sys/devices/system/cpu/cpu0/cpuidle/` does not exist.

	psci: [Firmware Bug]: failed to set PC mode: -3
	CPUidle PSCI: failed to create CPU PM domains ret=-517
	CPUidle PSCI: CPU 0 failed to PSCI idle
	CPUidle PSCI: Failed to create psci-cpuidle device

**Diagnosed since, and it is neither a missing device tree nor a missing
config.** Everything is present and correct: `sm6375.dtsi` has the
`idle-states`, the CPUs have `power-domain-names = "psci"`, the `psci` node
carries all nine power domains, and `CONFIG_ARM_PSCI_CPUIDLE`,
`CONFIG_ARM_PSCI_CPUIDLE_DOMAIN` and `CONFIG_QCOM_MPM` are all `=y`. What kills
it is a **race between two initcalls**, and the timestamps make it plain:

	[0.109751] CPUidle PSCI: failed to create CPU PM domains ret=-517
	[0.373495] CPUidle PSCI: CPU 0 failed to PSCI idle
	[0.373539] CPUidle PSCI: Failed to create psci-cpuidle device
	[0.424671] CPUidle PSCI: Initialized CPU PM domain topology using OSI mode

`-517` is `-EPROBE_DEFER`. The `psci-cpuidle-domain` platform driver
(`core_initcall`) cannot build the domain topology on its first attempt,
because the CPU cluster domain's parent is the MPM node —
`power-domain-cpu-cluster0` has `power-domains = <&mpm>`, and `mpm` is a
`#power-domain-cells = <0>` provider that has not probed yet. That is an
ordinary deferral and it resolves correctly at 0.4247.

The problem is who loses the race. `psci_idle_init()` is a `device_initcall`,
so it runs at 0.3735 — **51 ms before the domains exist**. `psci_dt_attach_cpu()`
→ `dev_pm_domain_attach_by_name()` returns `-EPROBE_DEFER`, which surfaces as
"CPU 0 failed to PSCI idle", and then:

	fdev = faux_device_create("psci-cpuidle", NULL, &psci_cpuidle_ops);

**a faux device has no deferred-probe support**, so nothing ever retries and
cpuidle is lost for the whole boot. The domain topology coming up 51 ms later
is of no use to anyone.

This is an upstream bug rather than a rhodep one: any SoC whose CPU cluster
domain has a parent that probes late loses cpuidle entirely, silently, with
every relevant config option enabled. Fixing it means touching generic
`drivers/cpuidle/cpuidle-psci.c` — retry on `-EPROBE_DEFER` rather than give up,
or go back to a platform device, which supports deferral — and since
`ARM_PSCI_CPUIDLE` is built in, that is a new kernel Image and a fastboot flash,
not a module swap.

For the record the vendor declares its own states differently: `blair.dtsi`
gives every CPU `cpu-idle-states = <&SLVR_PC &SLVR_RAIL_OFF>`.

Unrelated to the reset, but it means the cores never enter a low power state,
which costs battery continuously. It is its own piece of work.

---

# Narrowed again: the reset needs the engine in *continuous full-power* tracking

**A `standalone` session survives indefinitely if `QMI_LOC_START` carries a
duty-cycled `powerMode`. The same session, same boot, same client, without that
TLV, watchdog-resets the SoC.**

This is the first cut *inside* the measurement engine. Everything before it was
all-or-nothing: `standalone` dies, `cellid` lives. The lever is one optional TLV
on `QMI_LOC_START` that libqmi does not model and `qmicli` cannot send.

	| powerMode (QMI_LOC_START TLV 0x1A) | meaning                             | outcome        |
	| ---------------------------------- | ----------------------------------- | -------------- |
	| absent (engine default)            | -                                   | watchdog reset |
	| 1  IMPROVED_ACCURACY               | full power, **non-DPO**             | watchdog reset |
	| 3  BACKGROUND_DEFINED_POWER        | duty-cycles to a power budget       | survives 60 s  |
	| 5  BACKGROUND_KEEP_WARM            | <1 mA duty cycling                  | survives 15 s (x2) |

The two survivals and the two resets are the same binary, the same session id,
the same event mask, the same verified `operation mode = standalone (4)`.

## The controlled pair, one boot, 55 seconds apart

`/var/lib/systemd/pstore/console-ramoops-0`, kernel timestamps, `alive N ms`
heartbeats stripped:

	[  232.617015] BISECT-R4-standalone-powermode5-keepwarm-1788657231
	[  236.790416] rhodep-gnss: SET_OPERATION_MODE: standalone (4) from this client
	[  236.793407] rhodep-gnss:   SET_OPERATION_MODE: qmi_result=(0, 0) loc_status=SUCCESS(0)
	[  236.801114] rhodep-gnss:   read back: operation mode = standalone (4)
	[  240.836168] rhodep-gnss: config verified: opmode=standalone, events=min,engine
	[  240.838370] rhodep-gnss: QMI_LOC_START  session=11  powerMode=5/1000ms
	[  248.897007] rhodep-gnss: fix criteria read back after start: {... 'powerMode': (5, 0)}
	[  263.934842] rhodep-gnss: SURVIVED 15 s; indications received: 3
	[  268.968696] rhodep-gnss: session stopped

	[  288.295719] BISECT-R5-CONTROL-standalone-nopowermode-1788657287
	[  292.469194] rhodep-gnss: SET_OPERATION_MODE: standalone (4) from this client
	[  292.475365] rhodep-gnss:   read back: operation mode = standalone (4)
	[  296.509074] rhodep-gnss: config verified: opmode=standalone, events=min,engine
	[  296.511577] rhodep-gnss: QMI_LOC_START  session=11
	                                       <- console ends here, SoC gone

	androidboot.bootreason=watchdog
	androidboot.powerup_reason=0x00008000

Note the read-back lines in **both**. The mode really was `standalone` in the
survival; this is not the "a failed set falls through to standalone" trap in
reverse. And note that the survival ran its `session stopped` and its post-run
configuration dump — it is a session that completed, not a session that hung.

`powerMode=1` was run on a later boot and ends the console identically:

	[  183.852753] rhodep-gnss: QMI_LOC_START  session=11  powerMode=1/1000ms
	                                       <- console ends here, SoC gone
	androidboot.bootreason=watchdog   androidboot.powerup_reason=0x00008000

That is the interesting one. `eQMI_LOC_POWER_MODE_IMPROVED_ACCURACY` is
documented in the IDL as *"GNSS receiver operates in full power mode
(non-DPO)"*. So **disabling** dynamic power optimisation does not save it —
which kills the obvious reading of this result. What survives is not "DPO off",
it is "the receiver allowed to duty-cycle". The reset wants the receiver
continuously on.

## What this does NOT prove

The surviving runs produced **no NMEA, no satellite list and no
`ENGINE_STATE=ON` indication**, over 60 s with `POSITION_REPORT |
GNSS_SATELLITE_INFO | NMEA | ENGINE_STATE | FIX_SESSION_STATE | INJECT_*`
registered and verified. So this evidence **cannot distinguish**:

* the measurement engine comes up and runs safely when duty-cycled, from
* the measurement engine is never brought up in modes 3 and 5 either, exactly
  as in `cellid`, and this is the same old negative in new clothes.

Two things argue weakly for the first reading and neither is conclusive:

* the position report carries `session=0` (`eQMI_LOC_SESS_STATUS_SUCCESS`) in
  `standalone` + powerMode 3/5, where `cellid` gives `session=2`
  (`GENERAL_FAILURE`);
* the 60 s run got `QMI_LOC_EVENT_INJECT_TIME_REQ_IND (0x0028)`, i.e. the engine
  asked the AP for time — `cellid` never does.

For mode 5 the absence of indications is expected and says nothing at all: the
IDL states *"All QMI Indications are blocked in this mode to save power."* For
mode 3 there is no such excuse and the silence is unexplained. **Anyone
continuing this should settle it first**, because the whole value of the result
depends on it. The cheapest instrument is the modem's own measurement report:
register `QMI_LOC_EVENT_GNSS_MEASUREMENT_REPORT_IND (0x0086)`, which only a
running correlator can produce.

Second limitation: only `powerMode` was verified applied. `minInterval` was
asked for 60000 and read back as 1000, so **no claim is made about fix
recurrence, minimum interval or horizontal accuracy** — those were never
demonstrated to take effect and were dropped from the bisection rather than
reported as negatives.

## Constellation control exists on this modem, and refuses to restrict

The plan was to bisect by constellation and by band. That is not reachable here,
and the way it fails is worth writing down because it fails *silently*.

`qmicli --help-loc` exposes twenty actions and not one of them touches
constellations, bands, signals, DPO or LNA. libqmi 1.38.0 is no better: its LOC
model has eleven messages, `QMI_LOC_START` with three of its nine optional TLVs,
and no binding for constellation control, SV blacklisting, multiband config or
`powerMode`. There is no raw escape hatch either — `QmiMessage` is a
`GByteArray` typedef and is absent from the introspection data, so
`gi.repository.Qmi` has no `Message` attribute and a python-gi program cannot
hand-build a PDU. Hence `scripts/rhodep-gnss-bisect.py` speaks QMI to node 0
port 108 directly over `AF_QIPCRTR`, the way `pdr-tool.py` and `ssc-*.py`
already do.

Asked what it implements, the service under-reports itself.
`QMI_LOC_GET_SUPPORTED_MSGS (0x001E)` returns a 22-byte bitmap, 141 message ids,
stopping at `0x00AA`:

	supported bitmap: 22 bytes (count field 22)
	141 message ids supported
	  0x001e QMI_LOC_GET_SUPPORTED_MSGS ... 0x00aa (last)

`QMI_LOC_SET_CONSTELLATION_CONTROL` is `0x00B8`, `SET_BLACKLIST_SV` is `0x00B6`,
`SET_MULTIBAND_CONFIG` is `0x00E0` — all above the cut. **The bitmap is wrong.**
Every one of them answers:

	0x00b7 GET_BLACKLIST_SV        RESP {0x02: 00000000}
	                               IND  {0x01: 00000000, 0x10..0x15: 8 bytes each, all zero}
	0x00bf GET_CONSTELLATION_CONTROL
	                               IND  {0x01: 00000000, 0x10: gps=0, 0x11: glo=1,
	                                     0x12: bds=101, 0x13: qzss=101,
	                                     0x14: gal=1, 0x15: navic=100}
	0x00e1 GET_MULTIBAND_CONFIG    IND  {0x01: 00000000, 0x10: 0000000000000000}

Read that second one: **GPS `ENABLED_MANDATORY`, GLONASS and Galileo
`ENABLED_INTERNALLY`, BDS and QZSS `DISABLED_INTERNALLY`, NavIC
`DISABLED_NOT_SUPPORTED`.** And the third: `secondaryGnssConfig = 0`, i.e. **not
one secondary band is enabled for any system**. The engine on this device is
already L1/G1/E1 only — GPS L2, GPS L5, GAL E5a, BDS B2A are all off before
anything is asked of it. That disposes of "one frequency band" and "NavIC"
without spending a run on either.

Writing is where it goes wrong. The encoding is right — 32-bit masks are
rejected with QMI error `0x0001` (malformed), 64-bit masks are accepted — and
*enabling* works:

	A: enable QZSS   en=0x24 dis=0x1b u64 -> RESP (0,0)  IND status=SUCCESS
	   read back: qzss 101 DISABLED_INTERNALLY -> 2 ENABLED_BY_CLIENT   <- took
	C: enable BDS+GAL, disable GLO+NavIC     -> RESP (0,0)  IND status=SUCCESS
	   read back: bds 101->2, gal 1->2                                  <- took
	   read back: glo 1 ENABLED_INTERNALLY -> 1 ENABLED_INTERNALLY      <- ignored

The modem reports `SUCCESS` at both the QMI layer and the LOC layer and leaves
the constellation enabled. `SET_MULTIBAND_CONFIG` behaves the same way: asked to
enable the GPS secondary band it answers `SUCCESS` and reads back `0`.

So on this firmware, constellation and band configuration is **expansion-only**.
A control point can switch things on; it cannot switch anything off. Turning
GLONASS or Galileo off — the actual experiment — is not reachable.

This is exactly the failure mode the repo already warned about for operation
mode, one layer deeper, and it is worse: the operation-mode failure at least
tells you if you read the value back. Here the *set* reports success. The first
bisection run was **voided by the tool** on the read-back, not by inspection:

	SET_CONSTELLATION_CONTROL: enable=0x20 disable=0x1f
	  SET_CONSTELLATION_CONTROL: qmi_result=(0, 0) loc_status=SUCCESS(0)
	  read back: gps=ENABLED_MANDATORY glo=ENABLED_INTERNALLY ... gal=ENABLED_INTERNALLY
	VOID: constellation glo asked off, modem says ENABLED_INTERNALLY
	VOID: the run proves nothing; fix the step and repeat it

Had the tool trusted `SUCCESS`, that run would have started a full unrestricted
`standalone` session, reset the SoC, and been written up as "GPS-L1-only still
resets" — a wrong result with a plausible mechanism, which is the expensive kind.

### The one route left, deliberately not taken

`QMI_LOC_SET_BLACKLIST_SV (0x00B6)` would do it from the other side: blacklist
every GLONASS and Galileo SV and the engine has nothing to search. It is
implemented here — `GET_BLACKLIST_SV` answers with six 64-bit masks, all
currently zero, and there is an explicit `*_clear_persist_blacklist_sv` undo.

It was not run. Every field in that message is named `*_persist_*`: it writes
the modem's EFS, which reaches the eMMC through `rmtfs`. Issuing an EFS write
immediately before an operation whose *expected* outcome is a watchdog reset is
not a good trade on a phone with no USB link, and this session's brief forbids
writing partitions. Someone with recovery available should do it; it is the
clean version of this experiment.

`QMI_LOC_SET_PARAMETER (0x00D7)` has
`eQMI_LOC_PARAMETER_TYPE_CONSTELLATION_DISABLE_CONFIG`, which is precisely the
"disable a constellation on legacy service providers where the other API is not
available" escape. It answers `GET_PARAMETER` with QMI error `0x0011`
(missing mandatory argument), so it exists. It is also documented as *"to write
to nonvolatile memory"*, so it was declined for the same reason.

## What was not tested, and why

The untested operation modes — `msa`, `msb`, `wwan`, `default` — were left
alone. Once `powerMode` turned out to be a live lever that flips the outcome
inside `standalone`, spending the remaining reset budget on four more
whole-session modes was the worse buy: each would have produced one more
reset with no read-back to distinguish "this mode is safe" from "this mode
failed to apply". They remain open and are cheap for whoever picks this up
(`rhodep-gnss-bisect.py 15 opmode=msa`, and the tool will void the run itself
if the mode does not stick).

No `ENGINE_STATE` indication has ever been observed on this device, in any mode,
including the ones that reset. That is worth a moment: the event is registered
and acknowledged, and the engine never reports itself on or off.

## Reproducing

	sudo systemctl stop rhodep-gnss.service     # it pins a LOC client in cellid
	md5sum scripts/rhodep-gnss-bisect.py        # a zeroed copy looks like a survival
	python3 -c "import ast; ast.parse(open('scripts/rhodep-gnss-bisect.py').read())"
	sudo qmicli -p -d qrtr://0 --dms-set-operating-mode=online

	sudo ./scripts/rhodep-gnss-bisect.py probe                                # reads only
	sudo ./scripts/rhodep-gnss-bisect.py 15 opmode=standalone powermode=5,1000  # survives
	sudo ./scripts/rhodep-gnss-bisect.py 15 opmode=standalone                   # RESETS

Run the last two back to back on one boot. That pair is the result.

A caveat on the record for this session: one reboot in the middle of it was
`bootreason=reboot` / `powerup_reason=0x00004000`, a deliberate
`systemd-reboot` issued by another user of the phone, not a watchdog and not
caused by anything here. Both watchdog resets attributed above were confirmed
with `0x00008000` and with the console ending mid-session.

---

# Settled: the correlator never runs. The `powerMode` survivals were idle sessions

**`QMI_LOC_EVENT_GNSS_MEASUREMENT_REPORT` was registered and the receiver never
emitted one — not in 300 s of a session that cycled 308 times, and not under a
full 64-bit event mask. Reading (b) from the section above is the correct one.
The previous result is weaker than it looked, and part of it was an artefact.**

No position fix was obtained. This is not working satellite GPS.

Two separate errors in the previous write-up are corrected below: the surviving
`powerMode 3`/`5` sessions were **single-shot sessions that had already ended**
eight seconds in, and the IDL's "mode 5 blocks all indications" excuse for their
silence is **false on this firmware**.

## First, a tool bug that would have voided everything: the LOC port moves

`rhodep-gnss-bisect.py` had `LOC_NODE, LOC_PORT = 0, 108` hardcoded. QRTR hands
out service ports as services register, so they are stable within a boot and not
across boots. Measured, on consecutive boots of this phone:

	boot A (hardware_reset)   16  2  0  0  107   Location service (~ PDS v2)
	boot B (watchdog)         16  2  0  0  108   Location service (~ PDS v2)

Every run in this session was preceded by `qrtr-lookup | awk '$1==16'`, and the
port was 107 for the first three runs and 108 for the rest. A hardcoded 108
would have sent the first runs into a dead port; `INFORM_CLIENT_REVISION` would
have timed out and the tool would have reported

	VOID: INFORM_CLIENT_REVISION: no response from the LOC service

which is safe — it Voids rather than half-configuring — but it costs a run and
reads like "the modem ignored us" rather than "we were talking to nobody". The
tool now looks the service up at startup (`find_loc()`), logs what it resolved,
and takes `port=N` to override. The banner line is the receipt:

	LOC client bound to (1, 16415) (qrtr 0:107)

## The correction: `recurrence` was never sent, and the default is single-shot

Every run in the previous section, survivals and resets alike, omitted
`QMI_LOC_START` TLV `0x10` (recurrence). The tool only appends it when the
caller asks. What the default actually does is now visible, because the
measurement-report run logged raw TLVs with kernel timestamps.

Run 1 — `300 opmode=standalone powermode=3,1000 events=min,engine,inject,meas,svpoly`,
i.e. the previously-reported "survives" configuration, given 300 s instead of 60 s:

	[  455.022342] rhodep-gnss: QMI_LOC_START  session=11  powerMode=3/1000ms
	[  463.097956] rhodep-gnss: POSTSTART-VERIFY: powerMode = (3, 1000)  APPLIED
	[  463.100059] rhodep-gnss: FIXSESS state=STARTED  raw 0x01[4]=01000000
	[  463.101126] rhodep-gnss: EVENT  INJECT_TIME_REQ #1 0x10[62]=e803000003127469...
	[  463.102313] rhodep-gnss: POS    session=0(SUCCESS)
	[  463.103453] rhodep-gnss: POS    #1 raw 0x01[4]=00000000 0x02[1]=0b
	[  463.104463] rhodep-gnss: FIXSESS state=FINISHED  raw 0x01[4]=02000000
	                        <- nothing whatsoever for the next 300 seconds
	[  763.118727] rhodep-gnss: SURVIVED 300 s; indications received: 4
	[  763.123032] rhodep-gnss: MEASUREMENT REPORTS: 0

`FIX_SESSION_STATE` goes `STARTED` and then `FINISHED` inside the same
millisecond-scale burst, 8 s after `START` (the 8 s is the tool's own
`want_ind` timeout on a message that never sends an indication, not modem
latency — the four indications were queued during it and replayed together).

So the session was **over** before the listening window really began. The
remaining 292 s were an idle engine. "Survives 60 s" in the previous section
meant "the engine did one fix attempt, gave up, closed the session, and then sat
there". That is not a receiver duty-cycling safely.

Note also what the "weak evidence for (a)" actually is. The
`POSITION_REPORT` cited as `session=0 (SUCCESS)` carries **two TLVs**: `0x01`
the session status and `0x02` the session id `0x0b`. No latitude, no longitude,
no uncertainty, no `svUsed`, no GPS week or TOW. It is an empty report that
declares itself successful. It should not have been read as evidence that the
engine was doing anything.

## With `recurrence=periodic` the session really does cycle — and is still silent

Run 3 — the same thing with recurrence made explicit:

	300 opmode=standalone powermode=3,1000 recurrence=periodic \
	    events=min,engine,inject,meas,svpoly

	rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=3/1000ms
	rhodep-gnss: POSTSTART-VERIFY: powerMode = (3, 1000)  APPLIED
	rhodep-gnss: SURVIVED 300 s; indications received: 1541
	rhodep-gnss:   indication POSITION_REPORT            x308
	rhodep-gnss:   indication NMEA                       x308
	rhodep-gnss:   indication INJECT_TIME_REQ            x308
	rhodep-gnss:   indication FIX_SESSION_STATE          x617
	rhodep-gnss: MEASUREMENT REPORTS: 0

308 complete fix cycles at ~1 Hz over five minutes. Each cycle is identical:

	FIXSESS state=STARTED  raw 0x01[4]=01000000
	EVENT  INJECT_TIME_REQ #n 0x10[62]=e8030000031274696d652e78747261636c6f75642e6e6574..
	POS    session=0(SUCCESS)
	POS    #n raw 0x01[4]=00000000 0x02[1]=0b
	NMEA   $PSTIS,*61
	FIXSESS state=FINISHED  raw 0x01[4]=02000000

**This is the run that settles it.** The session is unambiguously alive and
repeating; the "the session had already ended" escape is gone. And in 308
cycles:

* `MEASUREMENT REPORTS: 0` — no correlator output, ever;
* zero `GNSS_SV_INFO` indications — no satellite list, not even an empty one;
* every `POSITION_REPORT` is the same empty two-TLV report;
* the only NMEA ever emitted is `$PSTIS,*61`, a Qualcomm proprietary sentence
  with an empty payload. No `GGA`, no `RMC`, no `GSV`, no `GSA`, despite
  `rhodep-xtra status` reporting NMEA types `0x7fff` (gga, rmc, gsv, gsa, vtg,
  pqxfi, pstis);
* still no `ENGINE_STATE`, on any run, consistent with everything before.

`FIX_SESSION_STATE` *does* arrive, 617 times. That matters: it is the other half
of the same `engine` mask bit-pair (`0x080 | 0x100`) as `ENGINE_STATE`, so the
registration path demonstrably works and the silence of the rest is the
modem's, not the tool's.

### The event-mask bit index is not the excuse either

The obvious objection is that `meas = 0x01000000` is taken from an IDL that has
already been caught being wrong about this service twice. Run 2 closes that.
It is Run 3 with one thing changed — the event mask set to every bit:

	REG_EVENTS mask 0xffffffffffffffff (0xffffffffffffffff)
	  REG_EVENTS: qmi_result=(0, 0) loc_status=None

Over 253 s of the same 1 Hz cycling, with **all 64 bits registered**, still zero
measurement reports and zero SV info. Whatever bit `GNSS_MEASUREMENT_REPORT`
really lives on, it was registered, and nothing came. The wide mask did surface
three indication ids the decoder does not know, and they are recorded here
verbatim because that is the whole point of the raw-TLV logging:

	EVENT  0x002d #1  0x01[4]=00000000 0x10[2]=e803 0x11[1]=00     (at each fix start)
	EVENT  0x002d #2  0x01[4]=02000000 0x10[2]=0000 0x11[1]=00     (at each fix end)
	EVENT  0x00be #1  0x01[4]=01000000 0x10[18]=000000000000000000000000000000000000
	EVENT  0x00cd #1  0x01[8]=0000000000000000 0x12[8]=0000000000000000

By message-id ordering in the IDL (`0x2B` engine state, `0x2C` fix session
state) `0x002d` should be `QMI_LOC_EVENT_WIFI_REQ` — the engine asking the AP
for WiFi aiding once per fix cycle, `0x10 = 0x03e8 = 1000` ms, even in
`standalone`. That identification is **inference from numbering only and is not
confirmed**. `0x00be` and `0x00cd` are not identified at all; both carry only
zeros.

## Honest reading, and the strongest remaining lead

The measurement engine is not demonstrated to run in `powerMode 3` or `5`. What
is demonstrated is a fix state machine that starts, asks for time, produces an
empty successful position report, and finishes, once per second, indefinitely,
without ever reporting a satellite or a measurement. On the evidence available
that is indistinguishable from `cellid` with better manners.

The single most promising lead is in every one of those 308 cycles:

	EVENT  INJECT_TIME_REQ 0x10[62]=e8030000 03 12 74696d652e78747261636c6f75642e6e6574..
	                                 |        |  |  \_ "time.xtracloud.net" (18 bytes)
	                                 |        |  \____ 0x12 = 18, the string length
	                                 |        \_______ 0x03
	                                 \________________ 0x000003e8 = 1000

**The engine asks the AP for time on every single fix attempt and is never
answered.** Nothing in this session ever replied to an `INJECT_TIME_REQ`. Run 2
also produced one `INJECT_PREDICTED_ORBITS_REQ` carrying the
`https://path1.xtracloud...` server list, despite XTRA having been injected and
`rhodep-xtra status` reporting 165 h remaining at the start of the session.

So there is a live alternative to "duty cycling stops the receiver coming up":
**the engine may be blocked waiting on assistance it keeps asking for and never
receives.** The previous section ruled out assistance "in both directions" on
the basis of *injecting* it up front; it did not test *answering the engine's
own request during the session*. That is the next experiment, it is cheap, and
it does not need a reset: answer `QMI_LOC_EVENT_INJECT_TIME_REQ` with
`QMI_LOC_INJECT_UTC_TIME (0x002E)` inside the `powerMode 3` periodic loop and
see whether the cycle ever progresses past the empty report.

## The `powerMode` enum, mapped

All entries below are `opmode=standalone`, `recurrence=periodic`,
`events=min,engine,inject,meas,svpoly`, `tBetween=1000` unless stated. Resets
are confirmed by `androidboot.bootreason=watchdog` /
`powerup_reason=0x00008000` **and** by the console ending mid-session.

	| powerMode | IDL meaning                | START result | outcome                        | meas |
	| --------- | -------------------------- | ------------ | ------------------------------ | ---- |
	| absent    | engine default             | accepted     | watchdog reset, ~100 ms        | -    |
	| 0         | (unset / not a valid enum) | accepted     | watchdog reset, ~100 ms        | -    |
	| 1         | IMPROVED_ACCURACY, non-DPO | accepted     | watchdog reset, ~100 ms        | -    |
	| 2         | NORMAL, DPO on             | accepted     | watchdog reset, ~100 ms        | -    |
	| 3         | BACKGROUND_DEFINED_POWER   | accepted     | survives 300 s, 308 fix cycles | 0    |
	| 4         | BACKGROUND_DEFINED_TIME    | QMI error 3  | not supported, run Voids       | -    |
	| 5         | BACKGROUND_KEEP_WARM       | accepted     | survives 20 s, 29 fix cycles   | 0    |

`powerMode=0` and `powerMode=2` are new resets from this session. Both end the
console at exactly the same line, with nothing after it:

	[  853.185170] rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=0/1000ms
	[  266.791666] rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=2/1000ms

`powerMode=2` is worth a moment. `eQMI_LOC_POWER_MODE_NORMAL` is the ordinary
mode with dynamic power optimisation **enabled** — the mode an ordinary GPS
application gets. It resets the SoC exactly like mode 1 with DPO explicitly
off. Combined with mode 1 from the previous session, this kills the last
version of the "DPO" reading: it is not DPO on, it is not DPO off. Every mode
that is not one of the two *background* modes resets the phone.

`powerMode=4` is simply absent from this firmware. `QMI_LOC_START` rejects it
with QMI error 3 at `tBetween=1000` and again at `tBetween=10000`, so this is
not a bad companion value. The tool Voids and no session is created, which
costs nothing — three of this session's runs ended this way and none of them
cost a reboot.

### Mode 5 is not silent because the IDL says it is

The previous section excused mode 5's lack of indications with the IDL line
*"All QMI Indications are blocked in this mode to save power"*, and concluded
that mode 5's silence "says nothing at all". That is wrong here. With
`recurrence=periodic`, `powerMode=5` emits indications at exactly the same rate
as mode 3:

	QMI_LOC_START  session=11  recurrence=periodic  powerMode=5/1000ms
	SURVIVED 20 s; indications received: 145
	  indication POSITION_REPORT            x29
	  indication NMEA                       x29
	  indication INJECT_TIME_REQ            x29
	  indication FIX_SESSION_STATE          x58
	MEASUREMENT REPORTS: 0

145 indications in 20 s, byte-identical in kind to mode 3. So indications are
**not** blocked in mode 5 on this firmware, and mode 5's zero measurement
reports carry exactly as much weight as mode 3's.

## The duty-cycle knob cannot be walked — it is validated, then inert

`QMI_LOC_START` TLV `0x1A` is two `uint32`: the mode and `tBetween`. The
previous section nominated this as "the duty-cycle knob and the thing to walk
when looking for the threshold". It is not. Walking it toward continuous
operation, `powerMode=3`, `recurrence=periodic`:

	| tBetween | START            | result                                      |
	| -------- | ---------------- | ------------------------------------------- |
	| 100      | qmi_result=(1,3) | rejected, VOID, no session created          |
	| 500      | qmi_result=(1,3) | rejected, VOID, no session created          |
	| 1000     | qmi_result=(0,0) | survives 300 s, 308 cycles, 0 measurements  |
	| 2000     | qmi_result=(0,0) | survives 20 s, 29 cycles, 0 measurements    |
	| 10000    | qmi_result=(0,0) | survives 20 s, 29 cycles, 0 measurements    |

Two things fall out, and they point opposite ways.

**Downward, the firmware refuses.** Anything below the 1000 ms `minInterval` is
rejected outright with QMI error 3. `powerMode 3` cannot be pushed into the
continuous region at all — the firmware will not let a client use this TLV to
reach the dangerous configuration. So there is no threshold to bisect along this
axis, and no number to report. That is a negative result but a clean one: the
"convert the finding into a number" plan does not work through `powerMode`.

**Upward, the value is inert.** `tBetween=2000` and `tBetween=10000` produced
*identical* indication counts — 29 / 29 / 29 / 58, 145 total, in 20 s. A 10 s
measurement spacing did not slow the 1 Hz cycle down at all. The value is
stored, not honoured: it reads back through `GET_FIX_CRITERIA` TLV `0x18`, and
it is also mirrored into the field this tool labels `positionReportTimeout`
(TLV `0x16`), which read back `10000` after the `tBetween=10000` run and
`255000` before any session. Stored, echoed, and with no observable effect —
the same trap as `minInterval`, one TLV over.

The tool now checks the second word as well as the first and says so rather than
plotting a curve through a parameter that does nothing:

	POSTSTART-UNVERIFIED: powerMode enum 5 applied but tBetween asked 1000,
	  reads back 0 -- the duty-cycle knob did NOT take

That line is from `powerMode=5`, which ignores `tBetween` entirely and zeroes
it. Under the old check that run would have printed `APPLIED`.

## An unexplained reset: the wide event mask, one controlled pair

Run 2 and Run 3 are the same command with one difference, the event mask. Both
`opmode=standalone powermode=3,1000 recurrence=periodic`, both 300 s, one boot
apart:

	Run 2  events=0xffffffffffffffff   START t=896.651   console ends t=1149.647
	                                   -> watchdog, 0x00008000, after 253 s
	Run 3  events=min,engine,inject,meas,svpoly (0x30001f7)
	                                   -> SURVIVED 300 s, 1541 indications

Run 2's console ends four seconds after the last routine cycle, immediately
after a burst of the two unidentified indication types:

	[ 1145.683150] rhodep-gnss: FIXSESS state=FINISHED
	[ 1145.684823] rhodep-gnss: EVENT  0x002d #500 0x01[4]=02000000 0x10[2]=0000 0x11[1]=00
	[ 1149.643508] rhodep-gnss: EVENT  0x00cd #1 0x01[8]=0000000000000000 0x12[8]=0000000000000000
	[ 1149.644126] rhodep-gnss: EVENT  0x00cd #2 0x01[8]=0000000000000000 0x12[8]=0000000000000000
	[ 1149.645663] rhodep-gnss: EVENT  0x00be #2 0x01[4]=01000000 0x10[18]=0000000000...
	[ 1149.647583] rhodep-gnss: EVENT  0x00be #3 0x01[4]=01000000 0x10[18]=0000000000...
	                                       <- console ends here, SoC gone

**This is one pair, n=1 on each side, and it is not established.** It is
recorded because it is a clean single-variable difference and because it is a
trap for the next person: registering unknown event bits is not free, and a
`powerMode 3` run that resets is not necessarily evidence about `powerMode 3`.
It also means this reset has a second, slower flavour — 253 s rather than
100 ms — which no previous run had seen. Anyone reproducing should run the pair
again before believing it.

## What is now ruled out, and what is not

Ruled out by this session:

* that `powerMode 3`/`5` demonstrate a safely duty-cycling receiver — they do
  not; nothing tracks in either;
* that the `GNSS_MEASUREMENT_REPORT` event bit index was the problem — a full
  64-bit mask produced nothing either;
* that mode 5's silence is explained by the IDL's indication-blocking note;
* that DPO is the variable — mode 2 (DPO on) and mode 1 (DPO off) both reset;
* that `tBetween` is a usable duty-cycle threshold parameter.

**Not** ruled out, and still open:

* whether answering `INJECT_TIME_REQ` in-session unblocks acquisition — the
  strongest lead, and it needs no reset;
* whether the receiver can track at all on this device under any configuration.
  There is still **no positive control** for the measurement-report path: the
  only configurations that plausibly run the correlator continuously are exactly
  the ones that take the SoC down in ~100 ms, far too fast to emit anything. So
  "no measurement report" cannot be fully separated from "measurement reports
  never work here";
* `msa`, `msb`, `wwan` and `default` operation modes, still never run.

## Reproducing

	sudo systemctl stop rhodep-gnss.service     # it pins a LOC client in cellid
	md5sum scripts/rhodep-gnss-bisect.py        # bcb004cc33c0db50bed3c31505ac2fea, 35289 bytes
	python3 -c "import ast; ast.parse(open('scripts/rhodep-gnss-bisect.py').read())"
	qrtr-lookup | awk '$1==16'                  # the port moves between boots
	sudo qmicli -p -d qrtr://0 --dms-set-operating-mode=online

	# the result of this session, safe, 300 s, no reset:
	sudo ./scripts/rhodep-gnss-bisect.py 300 opmode=standalone powermode=3,1000 \
	     recurrence=periodic events=min,engine,inject,meas,svpoly

	# the same without recurrence -- looks like a survival, is an idle engine:
	sudo ./scripts/rhodep-gnss-bisect.py 300 opmode=standalone powermode=3,1000 \
	     events=min,engine,inject,meas,svpoly

	# RESETS THE PHONE:
	sudo ./scripts/rhodep-gnss-bisect.py 20 opmode=standalone powermode=2,1000 \
	     recurrence=periodic

Session ledger, for boot-reason bookkeeping: three watchdog resets, all
`0x00008000`, all with the console ending mid-session — `powerMode 3` with the
64-bit event mask (253 s), `powerMode 0` (~100 ms), `powerMode 2` (~100 ms).
Runs 3, 4 and the whole `tBetween` ladder ran on a single boot, confirmed by
`uptime` continuity (13 min) across all of them. Three runs Voided for free with
QMI error 3 and created no session. The phone was left with
`rhodep-gnss.service` active, no failed units, wlan0 up, `qrtr-lookup` showing
service 16, and `mmcli -L` listing the modem.

---

# Answered: the engine was waiting for UTC time, and giving it to it is what fires the reset

**The `INJECT_TIME_REQ` lead is closed, and it closed in the opposite direction
from the one it was opened in. Answering the engine's time request does not
unblock acquisition. It removes an interlock. One `QMI_LOC_INJECT_UTC_TIME`
accepted by the modem converts the *only* GNSS configuration that survives —
`standalone`, `powerMode 3`, `recurrence=periodic` — into a watchdog reset
within ~100 ms of `QMI_LOC_START`.**

No fix was obtained. No measurement report, no satellite list, no NMEA beyond
`$PSTIS,*61`. **This is still not working satellite GPS.**

What it does supply is the missing explanation for the whole `powerMode` table
above. `powerMode 3` and `5` never looked safe because they duty-cycle the
receiver. They looked safe because **the correlator was never started at all**:
the engine sits in a fix loop asking the AP for time once per second and will
not proceed without it. Give it time — before the session, during the session,
or minutes earlier in a different session — and `powerMode 3` behaves exactly
like `powerMode 0`, `1` and `2`.

The eight runs below were done in one sitting: six watchdog resets, two clean
survivals used as controls, and one deliberate `systemctl reboot` recorded as a
calibration point.

## The controlled series

Every row is `opmode=standalone`, `powermode=3,1000`, `recurrence=periodic`,
`events=min,engine,inject,meas,svpoly` (mask `0x30001f7`) unless the row says
otherwise. Every reset is confirmed by `androidboot.bootreason=watchdog` /
`androidboot.powerup_reason=0x00008000` **and** by the console record ending
mid-run. Every survival is confirmed by `/proc/uptime` continuity across the
run **and** by the tool's own `SURVIVED` line.

	| # | what changed                              | UTC time in the engine? | outcome                                   |
	| - | ----------------------------------------- | ----------------------- | ----------------------------------------- |
	| 1 | event mask widened to 0x3203fff           | no                      | RESET at QMI_LOC_START                    |
	| 2 | answer INJECT_TIME_REQ in-session         | supplied in-session     | 5 fix cycles, then RESET ~1 s later       |
	| 3 | repeat of 2                               | supplied in-session     | identical, RESET ~1 s later               |
	| 4 | opmode=cellid, 78 time injections at 1 Hz | yes, 78 of them         | **SURVIVED 90 s**, uptime 198 -> 309      |
	| 5 | nothing; same boot as run 4               | left over from run 4    | RESET at QMI_LOC_START                    |
	| 6 | preinject=time before START, answer nothing | yes, one, pre-session | RESET at QMI_LOC_START                    |
	| 7 | preinject=position before START           | **no**                  | **SURVIVED 300 s**, 309 fix cycles        |
	| 8 | repeat of 6, on a clean boot              | yes, one, pre-session   | RESET at QMI_LOC_START                    |

Runs 6/7 are the pair that carries the result. They are the same command with
one word changed, `preinject=time` versus `preinject=position`, both injections
confirmed accepted by the modem before `QMI_LOC_START` was sent, and they land
on opposite sides of the reset.

## Run 7, the survivor: position injected, time refused, 309 empty fix cycles

	bisect run: 300 opmode=standalone powermode=3,1000 recurrence=periodic \
	  events=min,engine,inject,meas,svpoly preinject=position lat=-32.9542220 lon=-60.6442330 acc=50
	...
	PREINJECT (no session running): position
	RESPOND position: RESP ok   IND status=SUCCESS 0x01[4]=00000000
	PREINJECT: all accepted, RESP and indication status checked
	QMI_LOC_START  session=11  recurrence=periodic  powerMode=3/1000ms
	POSTSTART-VERIFY: powerMode = (3, 1000)  APPLIED
	...
	SURVIVED 300 s; indications received: 1545
	  indication POSITION_REPORT            x309
	  indication NMEA                       x309
	  indication INJECT_TIME_REQ            x309
	  indication FIX_SESSION_STATE          x618
	MEASUREMENT REPORTS: 0
	SATELLITE INFO INDICATIONS: 0
	REQUEST INVENTORY: 1 kind(s)
	  request INJECT_TIME_REQ              x309
	RESPONDER position  sent=1  RESP ok=1 err=0  IND ok=1 err=0

`uptime` 188 s before, 517 s after: one boot, no reset. This reproduces the
previously-documented 308-cycle run almost exactly (309 here) and confirms the
baseline is still the baseline on the current firmware state — which matters,
because run 5 had made that doubtful.

Note what the engine does with the position: **nothing observable**. It was
given a 22 m WiFi fix and it went on emitting empty `POSITION_REPORT`s and
asking for time 309 more times. Coarse position is not the gate.

## Run 6 and run 8, the resets: one time injection, no session, then start

Run 8, verbatim from `/var/lib/systemd/pstore/console-ramoops-0`, clean boot,
`bootreason=reboot` calibration immediately before it:

	[   82.130817] rhodep-gnss: bisect run: 300 opmode=standalone powermode=3,1000 recurrence=periodic events=min,engine,inject,meas,svpoly preinject=time lat=-32.9542220 lon=-60.6442330 acc=50
	[   86.203111] rhodep-gnss: REG_EVENTS mask 0x30001f7 (min,engine,inject,meas,svpoly)
	[   90.232152] rhodep-gnss: config verified: opmode=standalone, events=min,engine,inject,meas,svpoly
	[   90.232693] rhodep-gnss: PREINJECT (no session running): time
	[   90.233070] rhodep-gnss: RESPOND time -> 0x0038 txn=9
	[   90.233572] rhodep-gnss: RESPOND time: RESP ok (1 ms)
	[   90.233954] rhodep-gnss: RESPOND time: IND status=SUCCESS 0x01[4]=00000000
	[   98.265549] rhodep-gnss: RESPONDER time      sent=1  RESP ok=1 err=0  IND ok=1 err=0
	[   98.266051] rhodep-gnss: PREINJECT: all accepted, RESP and indication status checked
	[   98.266670] rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=3/1000ms
	                                       <- console ends here, SoC gone

	androidboot.bootreason=watchdog   androidboot.powerup_reason=0x00008000

Run 6 is the same file with different timestamps. Both are `~100 ms` deaths of
exactly the shape `powerMode 0/1/2` produce, in a `powerMode` that had run for
300 s an hour earlier.

The 8 s between the injection and `QMI_LOC_START` is the tool's own verification
window, not modem latency: it waits for the RESP *and* the indication status
before it will start a session, and Voids the run if either is missing. Nothing
was assumed here — the modem said `SUCCESS` at both layers.

## Runs 2 and 3: answering in-session, the same thing one second later

These were the runs the brief actually asked for, and they are the same
mechanism with the time arriving later. The session starts *without* time,
runs its normal fix cycles, is answered, goes quiet, and dies.

	[  206.017432] rhodep-gnss: FIXSESS state=STARTED  raw 0x01[4]=01000000
	[  206.019234] rhodep-gnss: REQUEST INJECT_TIME_REQ #1 0x10[62]=e8030000031274696d652e78747261636c6f75642e6e65741274696d652e78747261636c6f75642e6e65741274696d652e78747261636c6f75642e6e6574  [u32=1000 strings=time.xtracloud.net,time.xtracloud.net,time.xtracloud.net]
	[  206.021040] rhodep-gnss: RESPOND time -> 0x0038 txn=11
	[  206.022332] rhodep-gnss: POS    session=0(SUCCESS)
	[  206.024438] rhodep-gnss: NMEA   $PSTIS,*61
	[  206.025309] rhodep-gnss: FIXSESS state=FINISHED  raw 0x01[4]=02000000
	  ... four more identical cycles ...
	[  206.056292] rhodep-gnss: RESPOND time: RESP ok (35 ms)
	[  206.057791] rhodep-gnss: RESPOND time: IND status=SUCCESS 0x01[4]=00000000
	[  206.058906] rhodep-gnss: RESPOND time: RESP ok (29 ms)
	[  206.060277] rhodep-gnss: RESPOND time: IND status=SUCCESS 0x01[4]=00000000
	[  206.061264] rhodep-gnss: RESPOND time: RESP ok (23 ms)
	[  206.062498] rhodep-gnss: RESPOND time: IND status=SUCCESS 0x01[4]=00000000
	[  206.218580] rhodep-gnss: alive 111 ms (indications so far: 45)
	[  206.320299] rhodep-gnss: alive 214 ms (indications so far: 45)
	  ... the 1 Hz cycle has stopped dead; the count does not move ...
	[  207.009028] rhodep-gnss: alive 935 ms (indications so far: 46)
	                                       <- console ends here, SoC gone

Two things in that trace are worth reading slowly.

* **The answer was accepted, fast.** 23-35 ms round trip, `RESP ok`, indication
  status `SUCCESS`. This is not a rejected or malformed message.
* **The fix loop stops the moment it is answered.** In every unanswered run the
  cycle continues at 1 Hz for the full 300 s. Here it emits nothing for the
  ~950 ms between the last acknowledgement and the death. The engine left the
  "ask for time, report nothing, finish" loop it had been in and did something
  else, once, and that was the end of the SoC.

Run 2 is byte-identical in shape (`alive 936 ms (indications so far: 45)` as
its last line). Both `bootreason=watchdog` / `0x00008000`.

A caveat on these two specifically, which is why runs 6/8 exist: the five
answers went out in an 8 ms burst, because the indications had been queued
during the tool's `want_ind` wait on `QMI_LOC_START` and were all replayed at
once. "Five time injections in eight milliseconds" is not the same experiment
as "one time injection". Runs 6 and 8 send exactly one, with no session
running, and get the same reset, so the burst was not the mechanism. The tool
grew `respondafter=` to remove that artefact.

## Run 4, the control that decides the reading: cellid takes 78 of them and lives

The obvious alternative explanation is that `QMI_LOC_INJECT_UTC_TIME` into a
*live LOC session* is fatal by itself, whatever the engine is doing. It is not.

`cellid` runs the identical session state machine and never brings up the
measurement engine. It also never asks for time — `REQUEST INVENTORY: 0 kind(s)`
— so the injections were sent on a 1 Hz timer instead (`injectevery=1`), which
is what that option exists for:

	bisect run: 90 opmode=cellid powermode=3,1000 recurrence=periodic \
	  events=min,engine,inject,meas,svpoly respond=time injectevery=1 respondafter=10 ...
	...
	RESPOND time: RESP ok (2 ms)
	RESPOND time: IND status=SUCCESS 0x01[4]=00000000
	...
	SURVIVED 90 s; indications received: 48
	  indication POSITION_REPORT            x12
	  indication NMEA                       x12
	  indication FIX_SESSION_STATE          x24
	REQUEST INVENTORY: 0 kind(s)
	RESPONDER time      sent=78  RESP ok=78 err=0  IND ok=78 err=0
	after:  operation mode = cellid

`uptime` 198 s before, 309 s after. **Seventy-eight accepted UTC time
injections into a live session, at 1 Hz, and the phone did not so much as
hiccup** — with `powerMode 3` and `recurrence=periodic` set identically to the
runs that died. The only difference is the operation mode, i.e. whether the
measurement engine is in the picture at all.

So the reset needs both: the measurement engine, *and* time.

## Run 5, which was an accident and is the best evidence in the set

Run 5 was meant to be run 2 with the answers delayed 30 s. It reset at
`QMI_LOC_START`, before answering anything, which made no sense until the
ordering was checked. It had run on the same boot as run 4, twenty seconds
after it, and run 4 had just fed the modem 78 UTC times:

	[  304.633376] rhodep-gnss: RESPONDER time      sent=78  RESP ok=78 err=0  IND ok=78 err=0
	[  309.665063] rhodep-gnss: session stopped
	[  309.673552] rhodep-gnss: after:  operation mode = cellid
	[  329.605889] rhodep-gnss: bisect run: 120 opmode=standalone powermode=3,1000 recurrence=periodic events=min,engine,inject,meas,svpoly respond=time respondafter=30 ...
	[  333.684977] rhodep-gnss: REG_EVENTS mask 0x30001f7 (min,engine,inject,meas,svpoly)
	[  337.716375] rhodep-gnss: config verified: opmode=standalone, events=min,engine,inject,meas,svpoly
	[  337.719661] rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=3/1000ms
	                                       <- console ends here, SoC gone

This is the strongest form of the result because nothing in run 5 touched the
engine at all. No injection, no answer, no in-session traffic; just the
knowledge, held by the modem for twenty seconds across an operation-mode change
and a session teardown, that it knows what time it is. That was sufficient.

It also means **the arming is persistent across sessions within a boot**, and
that any tool which injects time is arming the reset for whatever runs next.

## An independent finding: the event registration mask is a reset lever of its own

Run 1 was supposed to be the cheap inventory run and it reset the phone
instead. It is a clean single-variable difference against the documented
300 s survivor: the same command, the same `powerMode 3`, the same
`recurrence=periodic`, **no time injected**, with the request-shaped event bits
added to the mask.

	[ 1152.877256] rhodep-gnss: bisect run: 120 opmode=standalone powermode=3,1000 recurrence=periodic events=min,engine,inject,req,meas,svpoly
	[ 1156.955597] rhodep-gnss: REG_EVENTS mask 0x3203fff (min,engine,inject,req,meas,svpoly)
	[ 1160.991061] rhodep-gnss:   REG_EVENTS: qmi_result=(0, 0) loc_status=None
	[ 1160.992188] rhodep-gnss: config verified: opmode=standalone, events=min,engine,inject,req,meas,svpoly
	[ 1160.995498] rhodep-gnss: QMI_LOC_START  session=11  recurrence=periodic  powerMode=3/1000ms
	                                       <- console ends here, SoC gone

`0x3203fff` is `0x30001f7` — the known-good mask — OR `0x00203e08`, the seven
request-shaped bits: NI notify verify (`0x8`), wifi (`0x200`), sensor streaming
ready (`0x400`), time sync (`0x800`), SPI streaming (`0x1000`), location server
connection (`0x2000`), inject wifi AP data (`0x200000`). Bit indices are from
the IDL and are **not** confirmed against this firmware.

This makes the previously-`n=1` "wide event mask reset" observation `n=2`, with
a far narrower mask and a far faster death — ~100 ms rather than 253 s. It is
still not known *which* bit does it, and this run does not narrow it: seven
bits went on at once. What is now established is that **registering event bits
is a lever on the reset independently of `powerMode` and independently of
assistance**, and that a run which changes the mask is not a controlled run.

The practical consequence is unfortunate: the complete request inventory the
brief asked for **cannot be taken on this device**, because taking it is itself
a reset. Under the largest mask that survives, the inventory is:

	REQUEST INVENTORY: 1 kind(s)
	  request INJECT_TIME_REQ              x309

One request type. Over 309 fix cycles, with `INJECT_POSITION_REQ` (`0x40`) and
`INJECT_PREDICTED_ORBITS_REQ` (`0x20`) both registered, the engine asked for
neither. Its payload never varies:

	0x10[62]=e8030000 03 12 74696d652e78747261636c6f75642e6e6574 (x3)
	         |         |  |  \_ "time.xtracloud.net", three times
	         |         |  \____ 0x12 = 18, the string length
	         \_________|_______ 0x000003e8 = 1000

## What was built, and the receipts for it

`scripts/rhodep-gnss-bisect.py` grew a responder that answers on the **same
AF_QIPCRTR socket that owns the session**, inside the fix cycle that asked,
with no blocking. New options: `respond=`, `preinject=`, `injectevery=`,
`respondafter=`, `lat= lon= acc= possrc= timesrc= xtrafile=`, and `injecttest`.

The three injection wire formats were **not taken from an IDL and not guessed**.
libqmi 1.38.0 was made to print its own traffic on this device and the bytes
were copied:

	0x0038 INJECT_UTC_TIME    0x10 u32 time source, 0x02 u32 unc ms, 0x01 u64 ms
	0x0039 INJECT_POSITION    0x1D u32 source, 0x1B u64 utc ms, 0x13 u8 confidence,
	                          0x12 f32 hor unc, 0x11 f64 lon, 0x10 f64 lat
	0x00A7 INJECT_XTRA_DATA   0x01 u32 total size, 0x02 u16 total parts,
	                          0x03 u16 part number, 0x04 part data

`qmicli --loc-inject-time --verbose` and `--loc-inject-position-*  --verbose`
give the first two directly. The third has no qmicli binding, so it came from a
throwaway python-gi program with `Qmi.utils_set_traces_enabled(True)` and
`G_MESSAGES_DEBUG=all`. Two details that are easy to get wrong and would have
produced silent failures:

* `0x0038` TLV `0x10` (Time Source) is marked optional by libqmi and is
  **mandatory on this firmware**, as another agent had already found. qmicli
  always sends it, which is why `qmicli --loc-inject-time` works where a
  hand-rolled two-TLV message does not.
* `0x00A7` TLV `0x04` (Part Data) is a **length-prefixed array**: a `uint16`
  count then the bytes, so a 1024-byte part is a 1026-byte TLV. Sending 1024
  raw bytes is a different message.

Enum values (`LocInjectedTimeSource`, `LocPositionSource`) were read out of the
gir on the phone rather than assumed: `NTP = 2`, `WIFI = 3`.

The tool then verifies rather than assumes, with `injecttest`, which sends one
of each with **no session running**:

	INJECTTEST: one of each, no session
	RESPOND time -> 0x0038 txn=6
	RESPOND position -> 0x0039 txn=7
	RESPOND xtra: 40 parts, 40949 bytes sent
	RESPOND time: RESP ok        IND status=SUCCESS 0x01[4]=00000000
	RESPOND position: RESP ok    IND status=SUCCESS 0x01[4]=00000000
	RESPOND xtra: RESP ok        IND status=SUCCESS 0x01[4]=00000000 0x10[2]=0100
	RESPONDER position  sent=1   RESP ok=1  err=0  IND ok=1  err=0
	RESPONDER time      sent=1   RESP ok=1  err=0  IND ok=1  err=0
	RESPONDER xtra      sent=40  RESP ok=40 err=0  IND ok=40 err=0

All 42 messages accepted at both layers, hand-rolled over raw QRTR. `preinject=`
Voids the run unless every part comes back acknowledged, so a reset can never be
attributed to assistance the engine did not actually receive.

## What this does NOT prove

* **It is not a fix and it is not GPS working.** Zero measurement reports, zero
  satellite info, zero `GGA`/`RMC`/`GSV`/`GSA`, in every run, including all six
  that reset. Nothing here observed a satellite.
* **"The correlator starts" is an inference, not a measurement.** What is
  measured is that supplying time changes `powerMode 3` from *survives 300 s
  emitting empty fixes* to *dies in ~100 ms*, and that the death is
  indistinguishable from `powerMode 0/1/2`. That the mechanism in between is
  the correlator coming up is the natural reading, and it is unverified: the
  configurations that would emit a measurement report are exactly the ones that
  do not live long enough to emit one. The `no positive control` problem
  recorded in the previous section is unchanged.
* **Only time was varied.** The XTRA almanac was valid in the modem's EFS for
  the whole session — `rhodep-xtra status` read `valid from 2026-09-05T23:00:00Z,
  valid for 168 hours, 163.6 hours remaining` afterwards — so every run had
  predicted orbits. This series shows time is sufficient to arm the reset and
  that position is not; it does **not** show orbits are irrelevant, because
  orbits were never absent.
* **Which of the seven request event bits fires run 1's reset is unknown.**
  Seven went on together.
* **`n` is small.** Runs 6/8 are `n=2`, runs 2/3 are `n=2`, runs 4/5/7 are
  `n=1` each. They agree, and they agree across three different ways of getting
  time into the engine, which is the reason for stating the conclusion as
  firmly as it is stated. Anyone who wants to lean on it should run 6/7 again
  as a pair; that costs one reset and ninety seconds.

## The network-assistance reading, checked

The brief's alternative — that the engine is waiting on a *network* path rather
than a local one — does not survive contact with the measurements, though the
supporting facts are all true.

	== assistance servers ==
	  UMTS SLP (SUPL) : supl.google.com:7275
	  CDMA PDE        : <unset>
	...
	registration: searching     packet service state: detached
	network rejection operator id: 72234

A SUPL server *is* configured, and the modem is indeed unregistered with an
unprovisioned SIM, so any SUPL/network-initiated assistance genuinely cannot
complete. But:

* the engine never asked for a network path. `LOCATION_SERVER_CONNECTION_REQ`
  was registered in run 1 (bit `0x2000`) and no such indication was ever seen —
  though run 1 died at `QMI_LOC_START`, so that is a weak negative;
* the one thing it does ask for, 309 times, is time, from `time.xtracloud.net`
  — the XTRA-T time service, which stock's `xtra-daemon` fetches over ordinary
  IP. WiFi is up and that path is available on this phone. It is not a
  cellular-registration dependency;
* and it was answered, from the AP, accepted, and the result was a reset.

So the honest statement is: **stock would behave differently here, but not
because it has a network we do not.** Stock answers the same request the same
way over the same WiFi, from `xtra-daemon` and the location HAL, and its
receiver then runs. The gap between stock and this port is not the assistance —
we can now deliver the assistance — it is whatever happens in the ~100 ms
*after* the engine has time and decides to bring the receiver up. That is where
the remaining work is, and it is the same place all the eliminated hypotheses
in this file were pointing.

## An operational hazard this creates

`rhodep-xtra.timer` is enabled and active on the shipped image, and
`rhodep-xtra inject` sends `QMI_LOC_INJECT_UTC_TIME` every time it runs. By run
5's result that **arms the reset for the rest of the boot**: after the timer has
fired, even `powerMode 3` — the configuration this file has been calling the
safe one — resets the SoC at `QMI_LOC_START`.

This is not currently a live risk, for one reason: `rhodep-gnss.service` only
ever runs `cellid`, and run 4 shows `cellid` is unaffected by any amount of
injected time. But anybody experimenting with `standalone` must stop
`rhodep-xtra.timer` first and check `systemctl show rhodep-xtra.service -p
ExecMainStartTimestamp` is empty for this boot, or they will attribute a reset
to whatever they were testing. Both of this session's `preinject` runs did
exactly that check.

## A tool-integrity incident, recorded because the brief warned about it

After run 1's watchdog reset, the copy of `rhodep-gnss-bisect.py` on the phone
had **the same byte count as the good file and a different md5**:

	local: a8fcccb999eb17e82f757386182fc71b  56539 bytes
	phone: 7b3384699c0939ed1447f815a896a552  56539 bytes
	  and it still passed `python3 -c "import ast; ast.parse(...)"`

Diffing it against the local original showed a *mixture* of the current and the
previous revision — some 4 KB blocks new, some stale. It was not tampering: it
is what an `scp` overwrite that was never `fsync`ed looks like after the page
cache is lost to a watchdog reset. The file length came from the new write and
some of the contents did not.

The operational rule that follows is stronger than the one in this file: after
**every** reset, re-copy the tool, `sync`, and compare the md5 against the host
copy. A size check is not enough and a syntax check is not enough — both passed
here.

## Reproducing

	sudo systemctl stop rhodep-gnss.service rhodep-xtra.timer
	systemctl show rhodep-xtra.service -p ExecMainStartTimestamp   # must be empty
	md5sum scripts/rhodep-gnss-bisect.py    # compare against the host copy, always
	python3 -c "import ast; ast.parse(open('scripts/rhodep-gnss-bisect.py').read())"
	qrtr-lookup | awk '$1==16'              # 107 and 108 both seen this session
	sudo qmicli -p -d qrtr://0 --dms-set-operating-mode=online

	# free, no session, proves the injections are accepted before anything
	# depends on them:
	sudo ./scripts/rhodep-gnss-bisect.py injecttest lat=-32.9542220 lon=-60.6442330

	# SURVIVES 300 s, 309 empty fix cycles, 309 unanswered time requests:
	sudo ./scripts/rhodep-gnss-bisect.py 300 opmode=standalone powermode=3,1000 \
	     recurrence=periodic events=min,engine,inject,meas,svpoly \
	     preinject=position lat=-32.9542220 lon=-60.6442330 acc=50

	# RESETS THE PHONE at QMI_LOC_START -- one word different:
	sudo ./scripts/rhodep-gnss-bisect.py 300 opmode=standalone powermode=3,1000 \
	     recurrence=periodic events=min,engine,inject,meas,svpoly \
	     preinject=time lat=-32.9542220 lon=-60.6442330 acc=50

	# the control: 78 time injections into a live cellid session, survives:
	sudo ./scripts/rhodep-gnss-bisect.py 90 opmode=cellid powermode=3,1000 \
	     recurrence=periodic events=min,engine,inject,meas,svpoly \
	     respond=time injectevery=1 respondafter=10 lat=-32.9542220 lon=-60.6442330

Run the second and third back to back. **On a boot where no time has been
injected** — that qualifier is now part of the reproducer, and was not part of
any reproducer in this file before.

Session ledger, for boot-reason bookkeeping: **six watchdog resets**, all
`androidboot.bootreason=watchdog` / `androidboot.powerup_reason=0x00008000`,
all with the console ending mid-run — runs 1, 2, 3, 5, 6 and 8. **Two
survivals**, runs 4 and 7, each confirmed by `/proc/uptime` continuity across
the run (198 -> 309 and 188 -> 517) as well as by the tool's `SURVIVED` line.
**One deliberate `systemctl reboot`** between runs 7 and 8, which recorded
`bootreason=reboot` / `powerup_reason=0x00004000`, calibrating the watchdog
readings against a known-clean restart on this same image. Two `injecttest`
runs created no session and cost nothing. The phone was left with
`rhodep-gnss.service` and `rhodep-xtra.timer` active, `rhodep-qmi-proxy`
running, no failed units, `wlan0` up at 192.168.1.53, `qrtr-lookup` showing
service 16, and `mmcli -L` listing the modem.
