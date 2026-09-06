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
