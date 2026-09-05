# GNSS on SM6375 (rhodep) — measured, and it is not what the docs said

**Result: starting a GNSS session watchdog-resets the SoC in under a second,
reproducibly, with `ipa.ko` not loaded.**

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
> | a vendor `no-map` region mainline does not reserve | full re-derivation from `blair.dtsi`: the only one missing is `memshare_mem` (dynamic, 8 MB), and the daemon refuses the allocation so the modem never receives an address for it |
>
> And one non-result that must not be read as an elimination: the QDSS clock was
> voted explicitly and nothing changed, but `clk_smd_rpm_handoff()` already
> votes every RPM clock at probe, so the test changed nothing and **QDSS is not
> excluded**. See `kernel/diag-modules/README.md`.

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

This closes the memshare hypothesis with evidence rather than inference:
reserving `memshare_mem` and starting the daemon with a non-zero offer would not
change this, because the engine never asks memshare for anything. `offer=0` stays.

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
| clocks Linux switched off as unused | `clk_ignore_unused pd_ignore_unused` (v99); still resets. Caveat: this only moved 3 clocks (249 -> 246 off), because the bootloader had already left most of them off, so it is weaker evidence than it looks |
| power domains left unpowered | `pd_ignore_unused`, same run |
| memshare handing out live RAM | the daemon runs with `offer=0`, answers `MEM_QUERY_SIZE` with 0 and refuses `MEM_ALLOC`; no allocation in any boot's journal |
| a missing VDD_MX vote for the mpss | the vendor declares only `vdd_cx` for `pil_modem` too |
| an AP-side SMMU stream id | the mpss is not behind `apps_smmu`; it uses its `no-map` carveout directly |

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

The cheaper experiment first, since the image already exists:
**boot `kali-boot-v97-ipa-interconnect.img`, `modprobe qnoc-sm6375`, then run the
reproducer.** Session 15 showed the interconnect driver working on v97 and it did
not fix the LTE reset, but GNSS has never been tried with the NoC provider bound.
Given that the failure looks like a hung bus, that is the one untested cheap
lead left.

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

1. **Get a log out of the modem side.** The reset is silent on the AP, so the
   AP is probably not where it happens. `/var/lib/tqftpserv/` already collects
   modem-authored reports (`datablock_report.txt`, `simlock_report.txt`,
   `hob_report.txt`); check whether a GNSS one appears, and whether the modem
   asks for any file over TFTP in the moment before the reset — run
   `tqftpserv -d` and start a session.
2. **Bisect the QMI_LOC start itself.** `QMI_LOC_START` takes optional TLVs
   (fix recurrence, minimum interval, accuracy). The script sets three of them
   defensively; try a start with the session id only.
3. **Check whether it is the indications at all.** Register events, then start,
   and immediately `--loc-stop` from the same client before any indication can
   be delivered. If it still resets, delivery is not the trigger.
4. **Compare against the IPA reset with the same instrumentation.** Both now
   have a ramoops console and `/dev/kmsg` markers; the question worth answering
   is whether the last thing on the console is the same in both.

## Reproducing

	sudo apt-get install -y gir1.2-qmi-1.0
	sudo qmicli -d qrtr://0 --dms-set-operating-mode=online
	sudo ./scripts/rhodep-gnss-test.py 40 min      # <-- phone reboots here

	# after the reboot:
	sudo grep -a rhodep-gnss /var/lib/systemd/pstore/console-ramoops-0
	tr ' ' '\n' < /proc/cmdline | grep bootreason

Put the radio back when finished, so the phone is not holding it up for nothing:

	sudo qmicli -d qrtr://0 --dms-set-operating-mode=low-power

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
