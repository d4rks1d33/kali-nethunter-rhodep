# Handoff: the LTE reset on motorola-rhodep

Read this first, then `README.md` in this directory for the full evidence trail.

## Start here: there is a ninety-second reproducer, and it is not LTE

Everything below was written while the only way to reach this reset was a SIM,
an LTE attach and ten to thirty seconds of waiting, one reboot per data point.
That is no longer true, and it changes how to work on this.

`docs/interconnect-sm6375-wip/GNSS-SM6375.md` documents a second reset with the
**same signature** — silent, whole-SoC, no panic, no oops, `bootreason=watchdog`
with `powerup_reason=0x00008000` — reached by starting a GNSS session. It needs
no SIM, no LTE, no ModemManager and no network, and it fires within
milliseconds. Re-confirmed on the current build, with `ipa.ko` loaded and a
GPRS data call up:

```
[ 591.190283] GNSS-TFTP-MARKER-D2
[ 592.061491] rhodep-gnss: session 11 started; listening 20 s
[ 592.061991] rhodep-gnss: ---------------------------------------
ECC: No errors detected            -> bootreason=watchdog
```

And it has now been bisected one step further, which is the useful part. Set
the operation mode to `cellid` from the same QMI client that opens the session
and **it survives**: twenty-five seconds, fifty indications, real position
reports and NMEA. So what kills the SoC is the **GNSS measurement engine being
brought up**, not the session, not the indications and not QMI.

Held next to the LTE finding — that it needs a *real attach*, not merely LTE
selected, and that a SIM-less modem scans in LTE for two minutes and lives —
the shape of the two is the same: **the modem switches on one of its hardware
engines and the chip dies.** Not the Q6 running software: GSM registers,
carries a GPRS call through IPA, sends SMS and places calls.

Whether it is one bug is not proven. Two things argue that it is: identical
signature down to the bootloader's bitmask, and both needing modem *hardware*
rather than modem software. One thing argues against it: milliseconds versus
nine seconds.

**Use the GNSS reproducer to test anything that is not IPA-specific.** Every
elimination in this document cost a reboot and an LTE attach; the same
eliminations now cost ninety seconds each.

## A correction to the timing reading below

"It is a timeout, not a race" is stated below on the strength of 9.7 s, 9.9 s
and 8.7 s from the last GSI interrupt to death, read as a ten second Q6
watchdog. That spread is 1.2 s. **A hardware watchdog does not do that** — it
would give 9.7, 9.7, 9.7. A process that takes about nine seconds to reach a
particular step does.

So the more likely reading, and the one the GNSS result supports, is that the
modem is alive and working through the attach for those nine seconds and then
does something that resets the chip instantly — exactly the GNSS shape. The
"modem hangs, its watchdog bites ten seconds later" story is not required by
the evidence and should not be leaned on.

## The problem in one paragraph

Moto G82 5G (`motorola-rhodep`, SM6375), Kali NetHunter Pro on mainline
7.2.0-rc5. Everything below LTE works: the modem registers on GSM, carries a
real GPRS data call through IPA with zero errors, sends and receives SMS, and
places calls. Force LTE and the whole SoC resets ten to thirty seconds later,
reproducibly, with no kernel panic, no exception and nothing in any log.

## What is established, with evidence

**It is a timeout, not a race.** Measured from the last GSI interrupt to the
moment the machine stops answering: 9.7 s, 9.9 s, 8.7 s across three runs.

**The AP does not crash.** A userspace heartbeat sampling into the kernel log
four times a second is still being scheduled on time 250 ms before the end,
then stops mid-stride with no final line.

**The modem does not assert.** Its SMEM crash reason still holds the Qualcomm
placeholder, `SFR Init: wdog or kernel error suspected.`, on the last sample
before the machine dies. A subsystem replaces that string if it manages an
orderly assert. It never does.

**So the modem hangs hard** -- hard enough that it cannot record why, which
points at a bus transaction that never completes rather than a software fault
-- **its Q6 watchdog bites about ten seconds later, and Trust Zone resets the
whole chip** instead of handing the watchdog to the AP for a subsystem restart.
That is why the AP's copy of the modem watchdog interrupt stays at zero, no
fatal ever arrives, and the bootloader reports `powerup_reason=0x00008000` and
`bootreason=watchdog` (against `0x00004000` and `reboot` for a clean restart).

**It needs a real attach.** With no SIM the modem sits in LTE scanning for over
two minutes and survives. Note the limit: without a SIM it never does a random
access, so it barely transmits, and anything that only happens at attach
transmit power is not excluded.

**The IPA data path is not involved.** During the attach the GSI counter moves
about a dozen interrupts and then stops, and the modem's own GSI channels are
in the same state at the moment of death as they hold on GSM -- none in error,
none stuck mid-transition. And GSM already proved those rings carry real
traffic correctly.

## Ruled out, each with an instrument rather than by absence

IPA runtime PM (pinned active, still dies) · unanswered QMI messages (0074
reports every dropped message; ten during boot, none during the LTE window) ·
GSI errors (mainline enables and logs them for every EE, including the
modem's) · IPA error interrupts (0072) · rmtfs and tqftpserv (journals piped
into ramoops, only two `del_client` lines) · the reserved memory map (all
fourteen vendor `no-map` regions present) · the IPA SRAM layout (all twenty
regions match `ipa_4_11_mem_part` exactly) · CX and MX rail voting (downstream
proxies CX only, mainline votes CX only, neither votes MX) · modem route count
(8 both sides) · the full INIT_DRIVER field list · the IPA core clock tier
(built at 220 MHz, still dies) · the three NoC paths (all voted, including the
one to IMEM) · the SMP2P clock handshake (never used, `cq=0` always) · the AP
watchdog (armed with a bark, never barks) · battery brownout (100%, 4.397 V) ·
the modem's SMMU stream IDs (`0x4a0` and `0x4a2` both declared).

3G cannot serve as a middle case: the network returns `registration-denied` on
UMTS, most likely the carrier retiring 3G rather than the port.

## Ruled out on the GNSS reproducer, which counts for this bug too

None of these is IPA-specific, so a negative on the fast trigger is a negative
here. Each is an instrument, not an absence.

**The region size the AP declares to TrustZone.** `qcom_mdt_pas_init()` calls
`qcom_scm_pas_mem_setup(pas_id, mem_phys, max_addr - min_addr)` — the extent of
the loaded ELF, not the carveout — so a modem heap growing past the ELF would
hit an XPU violation, which is exactly this signature. Parsed all 36 program
headers of the device's own `modem.mdt`: `min=0x8b800000`, `max=0x9b800000`,
extent `0x10000000`. **Exactly** the 256 MB of `pil_mpss_wlan_mem`. No gap.

**CPU frequency and an APSS droop.** Every cpufreq policy forced to
`powersave`, 300 MHz and 691 MHz: resets unchanged. Not a current or voltage
interaction with what the AP is doing.

**The modem wanting a file.** `tqftpserv` and `rmtfs` journals bridged into
`/dev/kmsg` so they land in ramoops, with the bridge verified live before the
run. **Not one line** between the marker and the death. The modem asks for
nothing. (This closes the "next step 1" the GNSS document left open.)

**CX and MX corners.** Both pinned at `RPM_SMD_LEVEL_TURBO_NO_CPR` for the
whole window with `kernel/diag-modules/rhodep_pdhold.c`, confirmed in
`/sys/kernel/debug/pm_genpd/{cx,mx}/perf_state`: resets unchanged. Rail corners
the AP can vote for are out.

**Binding the NoC provider**, which the GNSS document called its one remaining
cheap lead: already true. `qnoc-sm6375` is built in since 0065 and
`/sys/kernel/debug/interconnect/` is populated.

**A vendor `no-map` region mainline fails to reserve.** Re-derived the whole
`reserved-memory` block from `blair.dtsi` rather than trusting the earlier
count. Every fixed `no-map` region is present in mainline; everything else in
the vendor list is `reusable` (CMA) and harmless. The single exception is
`memshare_mem` — dynamic, `no-map`, 8 MB — which mainline does not reserve, and
which does not matter today because the daemon answers `MEM_QUERY_SIZE` with 0
and refuses `MEM_ALLOC`, so the modem never holds an address in it.

### Also ruled out on the reproducer, second round

**The AP being idle is not a precondition.** Every trace of this reset shows all
eight cores in `do_idle` at the end, which invited a story about a system-wide
low power transition. All eight cores pegged with `yes`, running at 1.80 and
2.21 GHz, `opmode=standalone` applied and confirmed in the console record: it
still dies.

**Stale or corrupt GNSS assistance data.** Plausible because the port ran
`rmtfs -r` for a long time, where the modem's EFS writes go to a RAM shadow and
are answered successfully — so the modem could have been reading back its own
lost state. `qmicli --loc-delete-assistance-data`, confirmed by
`--loc-get-predicted-orbits-data-validity` dropping to `1980-01-06T00:00:00Z,
valid for 0 hours`, then `standalone`: still dies. (The `-r` is also stale as
documentation: the shipped unit's `ExecStart=` reset means rmtfs actually runs
as `rmtfs -P -s`.)

**The GDSCs.** All twelve match: `holi-gdsc.dtsi` and mainline's
`gcc-sm6375.c` + `dispcc` + `gpucc` register the same set, and none of them is
a modem GDSC.

**The MPM window into RPM message RAM.** This one mattered because mainline's
`irq-qcom-mpm` writes an enable and polarity array into RPM MSG RAM, where
*every* master's votes live, so a wrong offset would corrupt the modem's own
requests. It is right: the vendor's `qcom,mpm-gic-blair` has
`reg = <0x45f01b8 0x1000>` and mainline's `apss_mpm: sram@1b8` sits at
`0x45f0000 + 0x1b8`. Same address.

**The rmtfs shared region.** Same size on both, `0x280000`, same client id, and
mainline already carries `qcom,vmid = <QCOM_SCM_VMID_MSS_MSA QCOM_SCM_VMID_NAV>`
— which is the vendor's `qcom,vm-nav-path`, the GNSS virtual machine. Nothing
missing.

**A file the modem asks for and does not get.** `tqftpserv -d` for a whole
boot: 1489 lines, exactly three rejections —
`/readwrite/ota_firewall/ruleset`, `/readwrite/datablock/id_01` and
`/readonly/vendor/fsg/mcfg_hw/mbn_hw.dig`. The last one looked promising, since
`mcfg_hw` is where RF and band configuration lives and `mcfg_sw/mbn_sw.dig` *is*
served. It is a false lead: mounting the real `fsg_a` partition shows it
contains `mcfg_sw/` and no `mcfg_hw/` at all, so Android answers that request
with ENOENT too.

**The carrier configuration cannot be used to bisect the LTE attach.** The
active `mcfg_sw` is `PERSONAL_ARG`, one of 25, all of them operator-specific --
there is no generic one to fall back to. Deactivating it (`qmicli
--pdc-deactivate-config`) leaves the modem unable to register on LTE at all,
which is the uninformative outcome, since an unregistered modem already
survives. Restored afterwards.

**Answering QMI service 60 changes nothing.** The modem contacts it the instant
it is published -- it does not look services up, it is told -- and asks:

```
service 60, msg_id 0x0020
  TLV 0x10 (8): "msm_mpss"      TLV 0x11 (4): 0x1388 = 5000 ms
```

Nothing on this port answers that today, and Android does, which made it a
candidate for the escalation question: a modem that believes the AP cannot
restart it might reset the chip rather than assert. Published with
`service-probe --answer 60 56`, confirmed in `qrtr-lookup`, confirmed the modem
talks to it -- and `standalone` still resets. (A bare success may not be the
answer that service wants, so this eliminates "the modem only needs to be
acknowledged", not the whole idea.)

**The ADSP is not in the loop.** Qualcomm GNSS uses sensor assistance and the
SSC lives inside the ADSP on this SoC, which made it a plausible third party.
The ADSP cannot be stopped through `/sys/class/remoteproc/remoteproc1/state`
for the same reason the modem cannot -- the write only drops a reference and
`auto_boot` holds it -- but unbinding the device works:

```sh
echo a400000.remoteproc > /sys/bus/platform/drivers/qcom_q6v5_pas/unbind
# remoteproc remoteproc1: stopped remote processor adsp
# remoteproc remoteproc1: releasing adsp
```

With the ADSP stopped and released, `standalone` still resets the SoC. (Worth
keeping as a technique: unbind is how you stop a remoteproc on this port.)

**Modem-side DIAG is not reachable on this firmware.** Worth recording because
it is the obvious way to get the modem's own log. The modem's glink edge
advertises `DATA1-4`, `DATA11`, `DS`, `IPCRTR`, `LOOPBACK_CTL_MPSS`,
`SSM_RTR_MODEM_APPS`, `apr_voice_svc`, `glink_ssr` and `rpmsg_ctrl` — no DIAG
channel. Creating endpoints named `DIAG`, `DIAG_CTRL`, `DIAG_CNTL`, `DIAG_CMD`,
`DIAG_DATA`, `DIAG_DCI_DATA` and `DIAG_DCI_CMD` through
`/dev/rpmsg_ctrl2` succeeds — `rpmsg_ctrl` always creates the local device —
but every `open()` then fails with `EINVAL`, which is
`qcom_glink_create_ept()` finding no matching remote channel and
`qcom_glink_create_local()` never getting an `OPEN_ACK`. The modem does not
answer to any of those names.

### The two failure modes on this SoC are not the same, and that matters

Worth stating before the eliminations, because it reframes them. This port has
now produced, three separate times, a failure where the application processor
touches an address that does not answer:

* patch 0046, writing a NoC QoS register on an unclocked port -- "a core that
  will not take an NMI is a core parked on a bus transaction that never
  completes";
* reading the modem's live carveout at `0x8b800000` through `/dev/mem`;
* reading past the end of the 4 KB IMEM window while chasing the TrustZone log.

Every one of those **hangs the machine**. The CPU parks, sshd stops answering,
and it takes a long power press. **None of them resets the SoC.**

The bug does reset the SoC, instantly. So it is not "an access that never
completes" -- that failure has a different, known signature here. It is
something that is *actively escalated*: a violation that somebody decides to
turn into a reset. That points at TrustZone or the PMIC rather than at a hung
bus, and it is the reason the remaining candidates are what they are.

### mainline disables the TrustZone debug image on every boot, and it does not matter

A real divergence from downstream, found in `qcom_scm_probe()`:

```c
qcom_scm_set_download_mode(download_mode);
/* Disable SDI if indicated by DT that it is enabled by default. */
if (of_property_read_bool(pdev->dev.of_node, "qcom,sdi-enabled") || !download_mode)
	qcom_scm_disable_sdi();
```

`download_mode` is zero unless `CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT` is set,
which this port does not set, so `!download_mode` is true and **mainline calls
`qcom_scm_disable_sdi()` on every boot**. Downstream calls it only from
`qcom_scm_shutdown()`, on a clean poweroff. SDI is the System Debug Image: the
TrustZone code that runs on a watchdog bite or a fatal error, saves context and
then resets. With it off, the same event is an immediate reset with nothing
saved -- which is exactly the shape of this bug, and would explain why no
capture has ever contained anything.

It can be tested without arming download mode at all, which is the neat part.
This SoC has no mechanism to write the dload cookie -- `qcom_scm` says so
outright, `No available mechanism for setting download mode` -- so setting the
parameter changes nothing except that the `if` above becomes false. **There is
no EDL risk.** Boot with the parameter on the kernel command line, since
`CONFIG_QCOM_SCM=y` makes it built in:

```
qcom_scm.download_mode=full
```

Confirmed live: `/sys/module/qcom_scm/parameters/download_mode` reads `full`,
dmesg still says `No available mechanism`, and the modem comes up normally.
Then the reproducer:

* it still resets, and
* `tzlog_dump@aefa2000` and `wdog_cpuctx@aefd2000` come back **byte for byte
  identical**, same md5 as before the run.

So SDI writes nothing even when mainline is told not to disable it. Either it
is off for another reason -- most likely the same production debug fuses that
turn out to disable CoreSight, see below -- or the reset does not go through
TrustZone's error path at all. Either way this is not the cause, and
`wdog_cpuctx` being empty is not explained by the AP disabling SDI.

Keep the finding anyway: disabling SDI on every boot is a real behavioural
difference from downstream and is worth raising upstream on its own terms.

The current `boot_a` carries that command line, added by patching the 512-byte
cmdline field of the boot image in place -- 28 bytes changed, kernel, DTB and
ramdisk untouched, and the header `id[8]` is a SHA1 over the kernel and ramdisk
only, so it stays valid. `boot_a-backup.img` reverts it.

### QDSS and CoreSight: was the leading candidate, and it is dead

It was a good candidate on paper. A node-by-node diff of `blair.dtsi` plus its
`holi-*.dtsi` includes against `sm6375.dtsi` gives 146 nodes only the vendor
has, and **about 110 of them are CoreSight** — `stm`, `tmc`, `tpdm`, `tpda`,
`funnel`, `replicator`, `cti`, `csr`, `etm`, `tgu` — against none at all in
mainline. The modem publishes three instances of service 51, CoreSight remote
tracing, and the ADSP and CDSP three more. A hardware trace source coming up
with an engine and pushing onto an ATB nobody configured is a bus transaction
that never completes, which is this signature exactly.

It is not that, and three independent measurements say so.

**The first attempt at testing it was a no-op and must not be counted.** The
QDSS clock was voted explicitly with `kernel/diag-modules/rhodep_clkhold.c` and
nothing changed — but `clk_smd_rpm_handoff()` in `clk-smd-rpm.c` already writes
an enable to RPM for every clock in the sm6375 table, in both the active and
the sleep set, at probe. `qdss_clk` reads `enable Y` at 19.2 MHz in
`clk_summary` with a prepare count of zero before the module is loaded. The
vote was redundant and the run changed nothing about the machine.

**1. The remote ETM is disabled everywhere, and turning it on is harmless.**
`scripts/modem/rhodep-coresight-etm.py` speaks the same two messages
downstream's `coresight-remote-etm.c` does — service 51, `GET_ETM` `0x002B`,
`SET_ETM` `0x002C`. All six instances answer, all six report `DISABLED`:

```
node   port   instance  result   state
0      19     2         ok       DISABLED      <- modem
0      21     11        ok       DISABLED      <- modem
0      115    3         ok       DISABLED      <- modem
5      6      5         ok       DISABLED      <- adsp
5      13     12        ok       DISABLED      <- adsp
10     6      13        ok       DISABLED      <- cdsp
```

Enabling two of the modem's instances and leaving them running: the phone stays
up, the modem stays registered and its data call stays attached, for as long as
it was left. A real trace source emitting into a fabric this kernel has never
configured does **not** reset this SoC.

**2. The AP can read the whole fabric, including the modem's own blocks.** Five
blocks read through `/dev/mem` — `tmc@8048000`, `funnel@8041000`, `stm@8002000`
and, from `holi-coresight.dtsi`, the two that carry the modem's trace,
`tpdm_modem0@8800000` and `funnel_modem0@8804000`. Every one returns the
standard CoreSight component id `0d 90 05 b1` and a sane `DEVTYPE`. Nothing
stalls, nothing faults. So the fabric is powered, clocked and addressable, and
this is not a repeat of the 0026 hole at `0x5847000`.

**3. It is inert, not unconfigured-and-dangerous.** Every funnel reads
`FUNCTL = 0x00000300`: the hold-time field set, and **all eight input port
enables clear**. The TMC-ETF reads `CTL = 0`, the STM reads `CTL = 0`. Nothing
is routing and nothing is capturing, which is what a disabled funnel is
supposed to do — drop the ATB data, not back-pressure it into a hang.

The 110 missing nodes are a real gap in the port and worth filling one day for
tracing. They are not what resets the SoC.

## Traps that have already cost time

**`/tmp/opencode/kernel/linux-7.2-rc5` is pristine upstream. The port's patches
are not applied to it.** Fine for reading files the port does not touch, wrong
for the ones it does. This produced a false alarm about `MODEM_PROC_CTX` being
undersized -- patch 0048 sets it correctly. Before comparing anything against
downstream, check whether the file is one the port patches.

**Read whole functions, not grep fragments.** `init_modem_driver_req()` looked
like it never sent the hashed table locations because the grep behind that
belief stopped at line 345, one line before the code that sends them. That
produced patch 0078, a no-op, and then a wrong causal claim built on top of it.
Both are parked in `docs/qmi-hashed-tables-no-op/`.

**Verify the instrument before trusting the measurement.** The deathwatch
script sat at zero bytes on the device for two whole test runs, which were
therefore untraced. Check the size or md5 after copying.

**The diagnostic can be the signal.** Reading `modem_gsi_state` takes a runtime
PM reference, so sampling it four times a second wakes IPA four times a second:
the GSI counter climbed 93 to 225 and almost none of it was the modem. Pin IPA
active first and the same window gives 41 to 45.

**Detect resets by uptime, never by the SSH stream dropping.** The stream
closes on timeouts without the phone dying.

**`/tmp` on the phone does not survive the reset you are causing.** A test run
came back "SURVIVED" and the log said `sudo: /tmp/gnss.py: command not found` —
the previous reset had wiped the script. Re-copy and re-check the md5 of every
instrument after every reset, not once at the start.

**Copying out of `/tmp` is not enough either: `sync` before you trigger the
reset.** The same script, put in `/home/kali` and md5-verified immediately after
the copy, came back after the next reset as **12507 bytes of NUL** — the right
size, no content. The copy was still in the page cache when the SoC went down.
`file` said `data`, `sudo ./script` printed nothing at all, and the run was
scored as a survival. Three test runs in this session were lost to a missing or
zeroed instrument. The check that actually works:

```sh
scp instrument kali@phone:/home/kali/
ssh kali@phone 'sync; sudo sh -c "echo 3 > /proc/sys/vm/drop_caches"
                md5sum /home/kali/instrument; python3 -m py_compile /home/kali/instrument'
```

`drop_caches` is the part that matters — without it the md5 is computed from the
same page cache that is about to be lost, so it always matches and proves
nothing.

**rhodep is a `blair` board, and `blair.dtsi` is not `holi.dtsi`.** The board
file is `blair-moto-rhodep-base.dts`, which includes `blair.dtsi`, which in turn
includes the `holi-*.dtsi` subsystem files. So the platform really is holi, but
anything defined *in* `blair.dtsi` must be read there — `holi.dtsi` is a
different SoC with `pm6350`/`pm6150l` instead of this board's
`pm6125`/`pmr735a`. This document quoted holi twice and both were wrong for
rhodep:

* `pil_modem` proxies `vdd_cx-supply = <&S2E_LEVEL>` on blair, not `S1E_LEVEL`.
* On blair **`S1E` is VDD_MX**, `qcom,resource-name = "rwmx"` — and downstream
  *does* vote it, just not from `pil_modem`: the regulator node itself carries
  `qcom,init-voltage-level = <RPM_SMD_REGULATOR_LEVEL_TURBO>` and
  `qcom,proxy-consumer-enable`. "Neither side votes MX" is wrong as written.
  It is not the cause — MX pinned at maximum still resets — but the method was.

**Modules live in the rootfs, not in the boot image.** `ipa.ko`,
`qmi_helpers.ko`, `qcom_q6v5.ko` and `qcom_q6v5_pas.ko` are all modules, so
flashing an image changes nothing about them. Copy the `.ko`, run `depmod -a`,
reboot. No flashing is needed for any of the diagnostics here.

## Working environment

* `ssh kali@192.168.1.53`, password `1234`. For root use
  `SUDO_ASKPASS=/tmp/ap.sh sudo -A`, recreating the askpass helper on every
  boot: `printf '#!/bin/sh\necho 1234\n' > /tmp/ap.sh; chmod +x /tmp/ap.sh`.
* Build: `pmbootstrap build --force linux-motorola-rhodep`, about three and a
  half minutes. Aport in
  `~/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep`.
  Patches must be copied there, added to `source=`, and `pmbootstrap checksum`
  run. Keep the three copies in sync: `kernel/patches/`, the aport, and
  `postmarketos/linux-motorola-rhodep/`.
* Crash logs: `/root/pstore-history/`, one file per boot, kept by
  `rhodep-pstore-keep` before systemd wipes them. `console-ramoops` holds about
  13 KB of the tail and has working ECC since 0067. A `dmesg-ramoops` file
  appears only on a panic, and there never is one here.
* Mobile data works today via `userspace/modem-gsm-only/`, which pins the modem
  to GSM before ModemManager can touch it and takes the IPA hold out of the
  way. GPRS speeds. That is what keeps the phone usable between tests, and the
  guard restores itself after every reset.

## Tools

`/usr/local/sbin/rhodep-lte-deathwatch [seconds]` samples into `/dev/kmsg` at
4 Hz so the last state before the reset survives in console-ramoops. Fields:

```
DW 64 t=136.80 run f=0 r=3 h=1 w=0 ipa=1 gsi=73 cq=0 pm=act
               mgs=2223333300000000 cr=init
```

`run` modem remoteproc state · `f` modem fatal irq count · `r`/`h` ready and
handover · `w` modem watchdog irq · `ipa`/`gsi` interrupt counts · `cq`
ipa-clock-query · `pm` IPA runtime PM · `mgs` the modem's GSI channel states,
one hex digit each, 1 allocated 2 started 3 stopped 4 stopping f error · `cr`
the modem's SMEM crash reason, `init` meaning still the placeholder.

`kernel/diag-modules/` holds two out-of-tree modules that build **on the
phone** in seconds against the installed headers package, so a clock or a rail
corner can be tested without a device tree change, a kernel build or a flash:
`rhodep_clkhold` (hold RPM clocks by index) and `rhodep_pdhold` (pin rpmpd
domains at a performance state). Read that directory's README before believing
a null result from `clkhold`.

`scripts/modem/rhodep-coresight-etm.py` reads and sets the remote ETM state on
the modem, the ADSP and the CDSP over QMI service 51, which is what ruled QDSS
out. It is also a worked example of raw QMI over QRTR from Python, including
the two constants that make or break it:

```
QRTR_TYPE_NEW_SERVER = 4    # not 2
QRTR_TYPE_NEW_LOOKUP = 10   # not 8
```

Those come from `include/uapi/linux/qrtr.h`, and getting them wrong is
completely silent — the name service simply never answers.
`scripts/modem/memshare-probe.py` has 2 and 8, which is the likeliest reason
its publish "appeared to succeed" and never appeared in `qrtr-lookup`, a
mystery that file documents at length and never solved.

`scripts/rhodep-gnss-test.py` grew an `opmode=` flag, which is what produced
the bisection at the top of this file:

```
sudo ./rhodep-gnss-test.py 60 min opmode=cellid    # survives, 50 indications
sudo ./rhodep-gnss-test.py 20 min                  # standalone: resets
```

Diagnostic patches, all outside `source=` unless noted:

* `0074` reports every QMI message dropped for want of a handler
* `0075` exposes the modem's GSI channel states and `ipa->mem_offset`
* `0076` exposes the remote's SMEM crash reason on demand
* `0079` is a real fix and is in `source=`: mainline adds `mem_offset` to two
  statistics *sizes* where it belongs only in addresses. Harmless here because
  `mem_offset` reads `0x00000000`, but wrong, and worth sending upstream.

## Reading the modem's memory instead of tracing it

With CoreSight fused off and no DIAG channel, the modem cannot be watched while
it runs. It might still be readable *after* it dies: DDR survives the reset, and
the only thing that overwrites the modem's 256 MB carveout is the AP loading
`modem.mdt` into it twelve seconds into the next boot.

`userspace/debug-tools/rhodep-mpss-dump` does that, and the mechanism works --
it produced a 256 MB `mpss-...-watchdog.bin` on the boot after a GNSS reset and
disarmed itself afterwards. **The dump is entirely zero, and that is not yet
interpretable**, because the validation that would say whether the read path
works at all has not been done.

**One hazard came out of trying that validation: reading the modem's live
carveout at `0x8b800000` through `/dev/mem` hung the application processor.**
Not a reset -- a wedge, recovered with a long power press. Do not read
`0x8b800000..0x9b800000` while the modem is running.

Full write-up, including the safe way to finish the validation, in
[`MODEM-MEMORY.md`](MODEM-MEMORY.md). It is the first thing to do next.

## Where to go next

Restated after the GNSS bisection, because the question has changed. It is no
longer "what does the modem touch during an LTE attach". It is **"what does the
modem touch when it switches on a hardware engine"**, and there are two of them
that do it — the GNSS measurement engine and whatever LTE brings up at attach —
against a Q6 running software that is demonstrably fine.

1. **Modem tracing is impossible on this handset, and that is now measured.**
   This used to be direction 1, "the only avenue that can answer the question
   directly". It is closed, and closing it cost one evening rather than the
   large piece of work it was billed as.

   The whole path was built and validated. `scripts/modem/rhodep-modem-trace.py`
   programs it by hand through `/dev/mem` -- mainline describes none of these
   blocks, so there is no driver in the way -- following the graph in
   `holi-coresight.dtsi`:

   ```
   modem_etm1 (QMI 51 inst 11) -> funnel_modem0@8804000 port 1
   modem_etm0 (QMI 51 inst 2)  -> funnel_modem1@880c000 port 0
                               -> funnel_modem1_dup@880d000 port 1
                               -> funnel_modem0@8804000 port 3
   tpdm_modem0@8800000         -> tpda_modem@8803000 -> funnel_modem0 port 0
   funnel_modem0               -> funnel_in1@8042000 port 4
   funnel_in1                  -> funnel_merg@8045000 port 1
   funnel_merg                 -> tmc_etf@8047000 -> replicator@8046000
                               -> tmc_etr@8048000 -> DDR
   ```

   **The sink works.** Do not skip this validation, it is what makes the rest
   mean anything: with the STM enabled as a source through `funnel_in0` port 7,
   the ETF's write pointer moves and its SRAM fills with real CoreSight frames.

   ```
   etf RSZ=0x1000 (16384 bytes of SRAM)
   etf RWP  00002220 -> 00002620   STS=0x00000001
   etf SRAM: a468c0ec 8ea069c0 d0ca4426 24c0ed0c a8189c0e 010cab52 ...
   ```

   **The modem emits nothing.** Same funnels, same sink, remote ETM reporting
   `ENABLED` on instances 2 and 11 over QMI 51, six seconds: the write pointer
   does not move by one byte.

   **And here is why, from the hardware.** The CoreSight authentication status
   register at offset `0xfb8` says which debug classes the fuses permit:

   ```
   stm            AUTHSTATUS=0xff  NSNID=enabled  NSID=enabled  SNID=enabled  SID=enabled
   tpdm_modem0    AUTHSTATUS=0xaa  NSNID=DISABLED NSID=DISABLED SNID=DISABLED SID=DISABLED
   etm0 (cpu0)    AUTHSTATUS=0x88  NSNID=n/i      NSID=DISABLED SNID=n/i      SID=DISABLED
   ```

   Every field of the modem's trace domain reads `0b10`, disabled. The CPUs'
   own ETMs are disabled too. The STM is the exception because it is a software
   source that needs no debug authentication -- which is exactly why the
   validation above worked and the modem test did not.

   So this is production silicon with trace fused off. No amount of device tree
   or funnel programming will get an instruction trace out of the modem, or out
   of the application processor for that matter. `PSTORE_FTRACE`, which the
   GNSS investigation used, works because it is software.

   This also retroactively settles the QDSS elimination on harder ground: with
   trace authentication disabled, no hardware trace source in the modem can
   emit anything at all, so "a trace source pushing into an unconfigured ATB"
   was never possible in the first place.

2. **Compare against the working LineageOS build on this handset.** The device,
   common and kernel trees are public
   (`Motorola-SM6375-Devs/android_{device_motorola_rhodep,device_motorola_sm6375-common,kernel_motorola_sm6375}`)
   and the kernel one is already checked out at `/tmp/opencode/vendor-kernel`
   as a partial clone — widen it with `git sparse-checkout add <dir>` rather
   than re-cloning. The modem firmware and EFS are identical on both, so
   anything downstream does at engine bring-up that mainline does not is by
   definition in the difference.

3. **Make the reset legible instead of guessing at it.** Downstream registers a
   dump table with TrustZone by writing its physical address into IMEM at
   offset `0x10` — `qcom,msm-imem-mem_dump_table@10`, `init_memory_dump()` in
   `drivers/soc/qcom/memory_dump_v2.c`, and it is a plain `memcpy_toio`, not an
   SCM call. Mainline has no equivalent, which is exactly why `wdog_cpuctx`
   came back byte-identical across a reset. The format is fully described in
   `include/soc/qcom/memory_dump.h`. Registering it would say whether the reset
   even goes through TrustZone's error path, and is a small module rather than
   a driver.

4. **Attach transmit power** remains open from before: the no-SIM test rules out
   LTE receiver bring-up but not transmit. Note that GNSS is receive-only and
   dies anyway, which makes a pure "TX power" explanation less attractive than
   it was.
