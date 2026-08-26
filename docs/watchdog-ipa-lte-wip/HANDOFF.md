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

**`pkill -f` matches your own SSH session.** `pkill -f service-probe` run over
ssh kills the shell running it, because that shell's command line contains the
pattern. The command returns nothing and the connection dies, which looks
exactly like the phone having crashed. Use `pkill -x` or a pidfile.

**When the phone stops answering over WiFi, try the USB link before concluding
anything.** An hour was lost to a phone that was perfectly healthy -- sshd
listening, uptime climbing, the user's own ssh working -- while the container
could not reach it over WiFi and got an alternating refused/timed-out pattern
that reads like a boot loop. `ssh kali@172.16.42.1` worked throughout.

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

## The experiment the README called decisive cannot be run, and now that is measured

The root `README.md` has listed this for months as "the experiment that would
settle it has never run": attach to LTE with `ipa.ko` never loaded. It has now
been run, and the answer is that it is not runnable.

With `blacklist ipa` in place from boot, `lsmod` showing no ipa, no
`rmnet_ipa0`, ModemManager and the GSM guard both stopped, the radio set
`online` and the mode preference read back as **`lte`**:

```
t+6s  ... t+96s   Registration state: 'not-registered-searching'
uptime 375 s, ipa modules loaded: 0
```

Ninety-six seconds of searching, never registered, and the SoC survives. The
modem gates registration on its own data path being ready, that needs the IPA
QMI handshake, and so "IPA loaded" and "modem registered on LTE" cannot be
varied independently on this device. The suspicion was already in this
document; it is now measured with the mode preference verified rather than
assumed.

## The LTE reset and the GNSS reset are the same hardware path, measured

The PMIC keeps three registers across the reset that the bootloader does not
clear, and they can be read safely through the spmi driver's regmap debugfs --
`/sys/kernel/debug/regmap/0-00/registers`, no `/dev/mem`, no chance of parking
a CPU on an address that does not answer.
`userspace/debug-tools/rhodep-pon-dump` dumps the PON peripheral.

| register | clean reboot | after any of these resets |
| -------- | ------------ | ------------------------- |
| `0x88d` | `00` | `01` |
| `0x8c2` | `00` | `02` |
| `0x8c4` | `80` | `40` |

And the result that matters:

```
GNSS standalone reset  vs  LTE band 4 reset   ->  IDENTICAL
LTE band 4 reset       vs  LTE band 28 reset  ->  IDENTICAL
```

**Every one of these resets leaves the PMIC in exactly the same state.** So the
GNSS reproducer and the LTE reset go through the same hardware path, which is
the evidence that was missing when this document downgraded the GNSS
reproducer to "probably the same bug, unconfirmed". It is confirmed, at the
PMIC, and the ninety-second GNSS reproducer is a legitimate stand-in for the
LTE reset.

`0x8c0` also differs between a clean boot (`41`) and one watchdog boot (`80`),
but it is **not** stable across two watchdog boots, so it tracks something else
-- do not read it as a reset cause.

### The controlled correlation

| case | band | RSRP | RSRQ | SNR | outcome |
| ---- | ---- | ---- | ---- | --- | ------- |
| GSM + GPRS data | gsm-850 | -- | -- | -- | **stable indefinitely** |
| LTE band 28 | 700 MHz, EARFCN 9360, 10 MHz | -100 dBm | -16 dB | 5.8 dB | reset |
| LTE band 5 | 850 MHz | -- | -- | -- | reset |
| LTE band 4 | AWS 1700/2100 | -117 dBm | -15 dB | 3.0 dB | reset |
| GNSS standalone | -- | -- | -- | -- | reset, same PON |

**A correction to this document.** An earlier round noted that the more capable
the band, the faster it died, and suggested that pointed downstream of the
radio link. More runs do not support it: band 28 with the *stronger* signal
died almost immediately in one run and took twelve seconds in another, and
band 4 with a much weaker signal took six to nine. Run-to-run variance
dominates, and the correlation should not be leaned on.

What remains true is that signal quality is poor in both cases -- RSRP -100 to
-117 dBm -- and that the one test which would separate transmit power from
everything else has not been run, because it needs the handset physically
moved: **the same band at strong and at weak signal.**

## There is an AT command interface to the modem, and nobody knew

The modem's glink edge advertises `DS`, `DATA1` through `DATA4` and `DATA11`,
and no driver on this port binds any of them. Opening them through
`/dev/rpmsg_ctrl2` with `RPMSG_CREATE_EPT_IOCTL` works -- unlike DIAG, which
fails with `EINVAL` because the modem never acks the open -- and **`DS` and
`DATA1` answer AT commands**:

```
>>> ATI
Manufacturer: Motorola Mobility
Model: 334
Revision: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133  1  [Sep 13 2024]
IMEI: 351287971159510
+GCAP: +CGSM,+DS
OK
>>> AT$QCSIMSTAT?
$QCSIMSTAT: 0,SIM INIT COMPLETED
```

Qualcomm vendor commands work too, so this is a real modem console and a path
that does **not** go through QMI and does not depend on the debug fuses.
`scripts/modem/rhodep-modem-at.py` drives it.

One trap: a non-blocking write returns `EBUSY`, which is glink saying there is
no remote intent to put the data in, not the port being dead. Open blocking and
bound the wait with an alarm.

**`AT$QCDMG` returns `ERROR`.** That was the prize -- on Qualcomm modems it
switches the AT port into DIAG mode, which would have given the modem's own F3
log. This firmware refuses, consistently with advertising no DIAG channel and
with the trace fuses being closed.

### What the AT port says while the SoC is dying

Polled continuously across an LTE attach on band 4, logging into `/dev/kmsg`,
the port answers every single command until 200 ms before the machine
disappears:

```
ATW: 29 t=524.53 CEREG AT+CEREG? +CEREG: 0,1 OK
<end of console, SoC gone>
```

`+CEREG: 0,1` is registered on the home network, and `+CEER` reports `No GPRS
context`, which is the benign "nothing to report" answer rather than a cause.

Put together with the fast deathwatch below, **both processors report
themselves healthy right up to the instant of the reset**, through two
independent paths. That is very hard to reconcile with a software fault on
either side.

## The fast deathwatch: IPA sees nothing, and neither does the modem

Rebuilt `ipa.ko`, `qmi_helpers.ko` and `qcom_q6v5_pas.ko` with the diagnostic
patches 0072, 0074, 0075 and 0076 all in `source=`, installed by copying the
`.ko` files and running `depmod` -- no flashing, modules live in the rootfs --
and sampled at **10 Hz** with IPA pinned active so the observer cannot
contaminate the GSI counter. Band 4 was chosen because it kills five to seven
seconds after registering, so the window is small and repeatable.

The full sequence, at last:

```
t=156.2   band 4 forced
t=172.01  mgs 2222333300000000    ch3 starts
t=172.98  mgs 2223222200000000    ch3 stops, ch4-7 start
t=175.04  mgs 2222222200000000    ch3-7 all started
t=175.87  gsi 49 -> 53            four GSI interrupts in the whole window
t=176.16  mgs 2223333300000000    everything back to the GSM baseline
          ... 7 seconds of complete silence ...
t=183.03  dead
```

A second run with the modem's own signals added, 167 samples at 10 Hz:

```
LTEW2 167 t=281.91 ipa=1 gsi=20 w=0 f=0 mgs=2223333300000000 cr=[SFR Init: wdog or kernel]
```

* `w=0` and `f=0` to the last sample: the modem's watchdog never reaches the AP
  and it never raises its fatal bit.
* `cr` is the Qualcomm placeholder, **sampled 63 times during the silent
  window** at 2.5x the rate the earlier captures used. The modem never writes a
  reason.
* `ipa` and `gsi` never move, no IPA error interrupt fires with 0072 unmasking
  all six, and no QMI message is dropped with 0074 watching.

So the modem brings its LTE channels up, takes them back down to the GSM
baseline, goes completely quiet, and seven to nine seconds later the chip is
gone -- with every observable on both processors reading healthy.

## It is not the band, and band restriction does work after all

This document says band-by-band testing is unavailable because "the modem
answers a single-band restriction with QMI error 25, DeviceUnsupported,
through either qmicli or ModemManager". That is true of qmicli and
ModemManager and false of the modem. The message they cannot send properly,
`QMI_NAS_SET_SYSTEM_SELECTION_PREFERENCE` with the LTE band preference TLV,
is a header and three TLVs, and the modem answers `result=0`:

```
TLV 0x11  u16  mode preference        0x0010   (LTE only)
TLV 0x15  u64  lte band preference    bitmask, bit N-1 = band N
TLV 0x17  u8   change duration        0        (this power cycle only)
```

`scripts/modem/rhodep-qmi-raw.py` sends it. Reading the current preference
back also gives the modem's own supported-band mask,
`0x000007e08a0f18df` = bands 1 2 3 4 5 7 8 12 13 17 18 19 20 26 28 32 38 39 40
41 42 43, which matches the published figures for this handset.

With that, the band hypothesis can be tested, and it is wrong:

| band | range | registered | died after registering |
| ---- | ----- | ---------- | ---------------------- |
| 28 | 700 MHz, low | t+2 s | ~12 s |
| 5 | 850 MHz, low | t+15 s | ~10 s |
| 4 (twice) | AWS 1700/2100, high | t+3 s | **~5-7 s** |

Three bands across both ranges, four runs, all fatal. Also worth having: the
network here serves band 28 by default, and masking it out moves the modem to
band 5 and then to band 4, so the restriction really is taking effect.

**One correlation is worth keeping.** The more capable the band, the faster it
dies: band 4 kills in five to seven seconds where band 28 takes twelve. A
purely RF trigger would give the opposite, or nothing. Something that goes
faster where there is more throughput available points downstream of the radio
link -- at the data path, which is exactly the thing that cannot be removed,
because without IPA the modem does not register at all.

## Ruled out on the LTE attach itself

**Disabling the IMS profile.** The window between registration and death is
where the modem would bring up IMS, which needs a second PDN through IPA, so
the `ims` APN looked like the LTE equivalent of the GNSS `cellid` bisection.
`qmicli --wds-modify-profile="3gpp,2,disabled=yes"`, confirmed by the profile
reading `APN disabled: 'yes'`: still dies, at fourteen seconds.

## The only thing the modem asks the AP for during an LTE attach

Session 13d swept every QMI service id **at boot** and concluded that no
missing service gates the modem. That sweep was never repeated in the window
that actually matters. Doing it during an LTE attach, with the probe's output
bridged into `/dev/kmsg` so it survives the reset, turns up one thing:

```
[  378.282474] SVCLTE: *** service 40: 28 bytes from node=0 port=148
[  378.282639] SVCLTE:     QMI type=0x00 txn=1 msg_id=0x0020
[  378.282926] SVCLTE:     raw 000100200015 0001040000efe005 1104000000000 012040000efe005
[  378.283075] SVCLTE:     answered with success
<end of console>
```

Decoded: a request, txn 1, `msg_id 0x0020`, 21 bytes of TLVs -- `0x01` =
`0x05e0ef00`, `0x11` = `0`, `0x12` = `0x05e0ef00`. **Service 40 is IMSRTP** in
libqmi's table, which fits: an LTE attach is exactly when the modem brings up
IMS, and nothing on this port provides it. Service 60 still turns up at boot as
it always has; 40 appears only during the attach.

**It is one occurrence in three attaches, so treat it as a lead and not a
cause.** Runs two and three died at fifteen and twenty-four seconds without the
modem ever contacting 40.

Followed up by publishing **only** service 40, twice, which settles it:

| run | published | answered | modem contacted 40 | died at |
| --- | --- | --- | --- | --- |
| A | 40 only | yes, bare success | **immediately, 8 s before LTE was forced** | t+28 s |
| B | 40 only | no | immediately, same request | t+19 s |

So the earlier reading was wrong: **40 is not attach-specific.** The modem has
a client waiting and connects the instant the service appears, exactly as it
does for memshare and for service 60. And neither answering it nor withholding
the answer changes the outcome -- 28 s against 19 s is inside the 10 to 30 s
spread this reset has always had.

The payload differs every time, which is worth recording for whoever decodes
this properly. Always `msg_id 0x0020` with three TLVs, `0x11` always zero, and
`0x01` and `0x12` always equal to each other:

```
0x05e0ef00    0x00980700    0x0090bd00
```

**Conclusion: service 40 is a real hole -- an AP-side QMI service Android
provides, that this port does not, and that the modem actively wants -- and it
is not the cause of the reset.** Worth implementing for its own sake; not worth
expecting anything from.

`userspace/debug-tools/rhodep-svc-sweep-lte.sh` runs it. **Compute the id list
before publishing, never after.** The first attempt published service 69, the
WLAN firmware service the modem itself provides and that `ath10k` binds to, and
took the WiFi link down -- which on a phone reached over WiFi means losing the
machine mid-experiment.

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

## A freeze is not always a hung bus access

The taxonomy in this document said that a freeze needing the power button
means an access to an address that does not answer, and that the bug under
investigation resets instantly instead. That still holds for the three cases
it was written from, but it is not the only way to reach a freeze, and
assuming so would have sent the next person hunting a bus hang that is not
there.

On 2026-08-25 the phone froze twice while the AT console was being tested. The
ramoops record shows a plain kernel Oops in a syscall, not a stalled
transaction:

    [ 191.909585] close ack on unknown channel
                  qcom_glink_native.c:1755, x23: dead000000000100
    [ 885.474811] rpmsg rpmsg0: failed to open DS
    [ 925.532523] Unable to handle kernel paging request at 0x10000
                  pc : __pi_strcmp   lr : qcom_glink_create_ept
                  rpmsg_eptdev_open [rpmsg_char] / chrdev_open / openat

This is the missing reference of kernel/patches/0080, reached without any
modem crash at all: the channel is freed one put too early, the close ack no
longer finds it, the dangling pointer stays in the rcids idr, and the next
attempt to create an endpoint walks that idr and strcmp()s freed memory. x3
holds 0x5344, the "DS" being looked for.

So: read the ramoops record before classifying a freeze. `journalctl -b -1`
also survives, and did here.

The first stage reproduces on demand, which the teardown signature never did.
`userspace/debug-tools/rhodep-glink-uaf-check` opens and releases an endpoint
on DS and counts the warnings. Do not run it on a kernel without the patch
unless a freeze is acceptable, because that loop is what froze the phone.

## Patch 0080 verified on the device, and what it did not fix

Flashed 2026-08-25 (kernel built Aug 25 23:30, the one before it Aug 24
18:30; the WARN line number also moved 1755 -> 1761, which is exactly the six
lines the patch inserts, so the running kernel is definitely the patched one).

Fixed, with evidence:

  * The freeze is gone. Creating an endpoint on DS and reopening it is what
    froze the phone twice on the unpatched kernel, once within three cycles.
    Eight cycles of exactly that pattern now pass with no Oops and continuous
    uptime, and 200 rapid open/close cycles complete in 1.3 s.
  * No refcount underflow appeared at any point.

Not fixed, and it was never the same bug:

  * 'close ack on unknown channel' at qcom_glink_native.c:1761 still fires.
    The explanation written into the patch was wrong: a merely freed channel
    would leave a dangling pointer in lcids, and idr_find would return it, not
    NULL. NULL means something removed the lcid first. It turned out to be
    this tool's fault, not the kernel's. destroy() reopened the device to
    issue RPMSG_DESTROY_EPT_IOCTL, and rpmsg_eptdev_open() builds a whole
    second glink channel, handshake and all, just to tear it down. One warning
    per run, and none at all from plain open/close cycles. Issuing the ioctl
    on the descriptor already held removes it: three runs, count unchanged.

Patch 0080 is incomplete and should not go upstream as it stands. There is
exactly one kref_get() in qcom_glink_native.c, at send_open_req, so the
premise written into the commit message -- one reference per idr entry -- is
not the design. For a locally-created channel the accounting is alloc 1, plus
send_open_req 1 for lcids, and the two puts at close (rx_close_ack for lcids,
rx_close for rcids) reach zero only by consuming the reference that belongs to
the endpoint. That is why it crashes when the endpoint outlives the channel,
and why adding a reference for rcids stops the crash. But nothing then drops
the endpoint's own reference: qcom_glink_device_release() only covers channels
that have an rpdev, which locally-created ones do not. So the patch most
likely trades a use-after-free for a leak of one channel per open. That is a
better trade and it is measurably safer, but the complete fix needs a matching
put, probably in qcom_glink_destroy_ept(), and that has to be worked out for
rpdev-backed channels too before anyone sends it anywhere.

## A faster way to kill the SoC than LTE, seen twice

Hammering the DS glink channel with open/close resets the phone the same way
LTE does. Second occurrence, with the log kept in evidence/:

    cycle 20:  EINVAL on /dev/rpmsg0
    cycle 532: EINVAL
    cycle 533: /dev/rpmsg0 no longer exists
    ... every later cycle fails; SoC resets seconds afterwards

An eptdev only disappears when its parent destroys it, which means the glink
edge went down: the modem died. Then the SoC followed. Boot afterwards:
bootreason=watchdog, pstore empty, PON 088d=01 08c2=02 08c4=40 -- the same
fingerprint as the LTE and GNSS deaths.

Be careful about how much this is worth. That fingerprint appears after any
abnormal reset, so it says 'same class of death', not 'same cause'. It is not
deterministic either: the identical 200-cycle pattern reset the phone once and
then survived twice, and 1000 cycles were needed the second time. What makes
it worth chasing is the shape -- modem dies first, SoC follows immediately,
nothing written anywhere -- which is precisely the shape of the bug this
directory exists for, reachable in about ten seconds with no SIM, no LTE and
no radio involved at all.

userspace/debug-tools/rhodep-glink-hammer.py is the reproducer. It needs the
modem's rpmsg control device passed in, because that number changes between
boots:

    C=$(rhodep-modem-at -l | sed -n "s/.*control device: //p")
    sudo python3 rhodep-glink-hammer.py 1000 "$C"

## The SoC dies when the modem comes back, not when it dies

This is the shortest reproducer found so far, and it needs no SIM, no radio,
no coverage and no LTE attach:

    echo 1 > /sys/kernel/debug/remoteproc/remoteproc0/crash

About three and a half seconds later the SoC resets, with bootreason=watchdog,
nothing in pstore and PON 088d=01 08c2=02 08c4=40, which is the fingerprint of
every death in this directory. Sampled at 200 ms with each line fsync'd:

    [112.08] crashing the modem
    [112.09] running -> crashed
    [112.30] crashed -> offline
    [115.52] offline -> running     <- the modem is back
             ... reset within the next few seconds

The timing was not a guess. Two glink hammer runs put it in the same place:

    run A   recovery finished 130.74    reset before 135.3
    run B   recovery finished 218.08    reset before 222

and one control points the same way from the other side. When the modem was
crashed and recovery hung forever inside ipa_modem_stop, the SoC was never
reset at all:

    INFO: task kworker/u32:6:130 blocked for more than 241 seconds
     __gsi_channel_stop [ipa]
     ipa_endpoint_disable_one / ipa_stop / ipa_modem_stop
     ipa_modem_notify / ssr_notify_stop [qcom_common]
     rproc_stop / rproc_boot_recovery / rproc_crash_handler_work

The modem stayed 'crashed' and the machine stayed up. So: modem dies and comes
back, SoC resets. Modem dies and never comes back, SoC lives. It is the
bring-up that is fatal, not the crash.

That reframes the whole investigation. An LTE attach is a bring-up of the data
path, and so is GNSS standalone, and so is this. What they have in common is
the modem starting to use something, not the modem failing.

Two cautions worth keeping. It is not perfectly deterministic: the same
debugfs write hung recovery once instead of completing it, and that run did
not reset. And 'no reset while recovery is stuck' is consistent with the
bring-up theory but does not prove it, since a stuck recovery also means the
data path is never re-established.

The hung recovery is a real bug in its own right and should be reported
separately: ipa_modem_stop waits on a completion in __gsi_channel_stop that a
dead modem never delivers, with no timeout, so the worker is stuck in D state
forever. It also makes shutdown take fifteen minutes, because systemd waits
for a task that cannot be killed.

Tools:
  userspace/debug-tools/rhodep-modem-restart-watch.py   the short reproducer
  userspace/debug-tools/rhodep-glink-hammer.py          the longer one

Next thing to try: IPA is the obvious suspect, since ipa_modem_notify runs on
both halves of the transition and the hang is inside it. Compare a recovery
with ipa.ko loaded against one without. Without ipa.ko the modem cannot
register on LTE, but it can still be crashed and recovered, which is all this
reproducer needs.

## Patch 0080 verified, second version

Flashed 2026-08-26 (kernel built Aug 26 00:28). Proof it is the running
kernel, rather than the build date: the warning in qcom_glink_rx_close_ack
moves with the patch, and forcing it deliberately printed

    WARNING: drivers/rpmsg/qcom_glink_native.c:1782

against 1755 unpatched and 1761 for the first version, which is exactly what
the hunks insert. No refcount underflow and no Oops appeared in any run since.

The leak measurement is still outstanding. Every attempt to run 500 cycles and
compare slab counters was cut short by the reset described above, which is not
the patch's doing but does mean the leak question is answered by reasoning
rather than by measurement so far.

## IPA is required for the reset

The reproducer is short enough to A/B properly now, so it was. One debugfs
write per round, the modem crashes and comes back, and the only question is
whether the SoC survives the bring-up.

    ipa.ko loaded at boot     4 resets in 5 bring-ups
                              (the fifth was the run where recovery hung
                              inside ipa_modem_stop and the modem never
                              returned, so there was no bring-up to survive)

    ipa.ko removed            0 resets in 6 bring-ups, uptime continuous
                              from 683 to 920, four rounds in one run and two
                              in another

    ipa.ko modprobe'd back    0 resets in 2 bring-ups -- and this one does not
                              count, see below

The last line is not a control and must not be read as one. Loading ipa while
the modem is already running is not the same state as loading it at boot: it
never performs the modem setup handshake it normally does during probe, so
there is no data path for the bring-up to touch. It is recorded because it was
run, not because it proves anything.

The control that does count is a clean reboot, where ipa loads normally, and
that reset on the first round:

    [72.02] round 1: crashing the modem
    [72.23] crashed -> offline
    [74.85] offline -> running
            log ends here; next boot has bootreason=watchdog

So the reset needs the modem to come back up, and it needs IPA to be there in
the state its own probe leaves it in. That is now a four second experiment
with two variables, on a machine with no SIM in it.

What this does not say: that IPA is the bug. Removing ipa.ko removes the
entire modem data path, so it is a large hammer. It could be IPA's GSI channel
setup that is fatal, or it could be anything the modem only does when it has
somewhere to send traffic. The next cut has to be inside IPA rather than
around it: the modem endpoints and GSI channel setup at ipa_modem_start, and
the config the modem receives during ipa_modem_notify, are where to start.

It does explain why every previous elimination round kept coming back to LTE.
An attach is the one thing that makes the modem bring the data path up, and
the data path is IPA.

## The file-restore half of the purge guard had never worked

Found by deleting a registered file on purpose instead of trusting that the
protection worked. `rhodep-protect-files enforce` noticed the deletion and then
said `could not restore`, every time, for every file in the port.

`register <component> <mode> <path>...` wants an octal mode, and all four
callers pass the word `protected` instead, so the manifest lines read

    protected /usr/local/sbin/rhodep-modem-at

and enforce ran `install -D -m protected ...`, which is an error. The immutable
flag was carrying the whole scheme on its own: root could still delete a file
by deciding to, and nothing would ever put it back.

Fixed in enforce rather than by migrating the manifests, so old and new entries
both work: an octal mode is still honoured, anything else means "keep whatever
the snapshot has", which is what `cp -a` does and what the callers meant.

Verified end to end, which is the only way this should be believed: the AT
console was deleted, `systemctl start rhodep-holds-enforce` put it back, it
ran, and the immutable flag was on it again.

One trap worth knowing when changing any protected file, including
rhodep-protect-files itself. The live file and the canonical copy are separate;
installing a new version over the live one and then running enforce restores
the *old* one over your change and reports it as tampering. The sequence is
release, install, register.

## What is protected now

Registered with both layers and verified restoring: the AT console, and the
debug tools that had been getting copied by hand every time the phone reset --
rhodep-pon-dump, rhodep-glink-uaf-check, rhodep-glink-refcount-test,
rhodep-glink-hammer.py, rhodep-modem-restart-watch.py.

Both installers also gained a `release` before their `install` lines. Without
it the second run of either script fails, because install(1) cannot overwrite
an immutable file, and the first run is what makes them immutable.

rhodep-holds-enforce.service was found `disabled`. Its unit has
`WantedBy=multi-user.target`, so it is meant to run at boot; only the timer was
enabled, which left the first three minutes of every boot unguarded. Enabled.

The ipa hold is deliberately off, and now says so where somebody would look
for it: `/etc/modprobe.d/README-rhodep-ipa-hold-is-OFF` explains that
rhodep-ipa-hold.conf was moved to /root/ipa-hold.off, that this phone will
reset with a SIM in it, that this is expected in this state, and how to put it
back. Nothing restores that file automatically, and nothing removes it either.

## The APSS watchdog is not what is biting

Worth ruling out before hunting an AP lockup. The reset lands about four
seconds after the modem finishes coming up, and the APSS watchdog cannot be
the cause of a four second reset:

  * drivers/watchdog/qcom-wdt.c sets `wdd->timeout = min(max_timeout, 30U)`,
    and the node this port adds in patch 0068 carries no `timeout-sec`, so the
    bite is programmed at thirty seconds.
  * The hard lockup detector is off anyway: "watchdog: NMI not fully
    supported / Hard watchdog permanently disabled".
  * `/sys/class/watchdog/watchdog0` has no timeout, state or bootstatus
    attributes at all, because CONFIG_WATCHDOG_SYSFS is not set. Reading or
    changing the timeout needs the /dev/watchdog0 ioctls, and opening that
    device starts the watchdog, so it is not a free measurement.

So `bootreason=watchdog` is what the bootloader reports for the class of
reset, not evidence that the kernel's watchdog fired. Nothing about the timing
fits an AP livelock caught by the APSS watchdog thirty seconds later. Something
external takes the SoC down almost immediately, which is consistent with
everything else here: instant, silent, no crash reason written, no
notification to the AP.

## IPA version and regions check out against the vendor

Not a lead, but worth not re-checking: the vendor tree agrees with the
mainline configuration this port uses.

    blair.dtsi qcom,ipa@0x5800000
      qcom,ipa-hw-ver = <20>            /* IPA v4.11 */
      reg = <0x5800000 0x84000>,        ipa-base
            <0x5804000 0x23000>         gsi-base

Our node uses the same GSI window, 0x05804000 size 0x23000, and picks
ipa_data_v4_11_sm6375, which is IPA v4.11. So the version is right and the GSI
region is right.

That weakens the idea that the shared-memory map is wrong. It is taken from
sc7280, which is also v4.11, and IPA_MEM_MODEM at offset 0x1f18 size 0x100c
ends at 0x2f24, inside the 0x3000 ipa-shared window. The vendor DTS does not
state an SRAM size to contradict it; the downstream driver computes its own.
Qualcomm ships IPA outside the kernel tree (techpack/dataipa), so it cannot be
compared from the vendor kernel clone that this port uses.

## Reading the kernel's last words, now that ramoops cannot

ramoops does not survive this reset. The console record is there for an Oops
and empty every single time the SoC goes down at modem bring-up, so the normal
way of reading a dying kernel does not work here.

`userspace/debug-tools/rhodep-kmsg-tail` reads /dev/kmsg and fsyncs every line
before reading the next. One fsync per message is far too expensive to leave
running and exactly right for a four second window that ends in a reset.

    sudo rhodep-kmsg-tail --out /var/log/rhodep-kmsg.log &
    sudo rhodep-modem-restart-watch.py --rounds 1

What it says about the fatal window is: nothing. Not an error, not a warning,
no IPA or QMI complaint. The last two kernel lines are always these, and then
the machine is gone:

    ipa 5840000.ipa: received modem running event
    remoteproc remoteproc0: remote processor modem is now up

So the AP is not detecting a problem and reacting badly to it. It is being
switched off while it has nothing to say.

## IPA never recovers from a modem restart, and that is a mainline gap

The watcher also samples /sys/class/net, because rmnet_ipa0 is registered by
ipa_modem_start() and only exists once the modem has finished setting up the
data path. Measured:

    interfaces at the start: ... rmnet_ipa0 ...   <- boot handshake succeeded
    round 1: rmnet_ipa went away                  <- modem crashed
    round 1: modem is up
    ...                                           <- it never comes back

Reading why makes it plain. `ipa_modem_crashed()` calls
`ipa_smp2p_irq_disable_setup()`, whose comment says "Prevent the modem from
triggering a call to ipa_setup()", and that sets `smp2p->setup_disabled = true`.
Nothing in the tree ever sets it back to false: grep for it and there are three
hits, the declaration, the comment and that one assignment. So the GSI-ready
interrupt from the modem is masked once and stays masked.

`ipa_smp2p_notify_reset()`, which does run on QCOM_SSR_BEFORE_POWERUP, only
clears the two power notification bits. It does not re-arm that interrupt.

`ipa_setup()` also has no guard against running twice; it would redo
gsi_setup(), re-enable already-enabled endpoints and call ipa_qmi_setup()
again. So masking the interrupt is protecting against a real problem, and the
consequence is that after any modem crash, mainline IPA is finished until
reboot. Also worth noting: `ipa_modem_crashed()` never calls `ipa_teardown()`
and never clears `ipa->setup_complete`.

That gives a coherent story for the reset, and it also explains the A/B. The
first boot establishes the IPA relationship with the modem. After a restart the
modem announces GSI ready and the AP, by design, does not answer. Roughly four
seconds later the SoC goes down with no AP involvement. With ipa.ko never
loaded there is no relationship to break, nothing is expected of the AP, and
six restarts passed.

It is a story, not a proof. What is measured is: the interrupt is masked
permanently, rmnet_ipa never returns, the kernel says nothing, and the SoC dies.
That the modem is escalating because it was left unanswered is inference.

One loose end, deliberately not overclaimed: an unhandled QMI indication shows
up at every transition, twice, from two nodes.

    qmi: no handler on local port 0 for type=4 msg_id=0x0023 txn=8 len=7 ...

IPA's own ids are 0x22 for INIT_COMPLETE and 0x35 for DRIVER_INIT_COMPLETE, so
0x23 is not one of them, though the length of 7 matches
IPA_QMI_INIT_COMPLETE_IND_SZ. Unidentified for now.

## A caveat that matters more the better this reproducer gets

This reproducer is a modem *restart*. The LTE bug is not: there the phone boots
once, IPA sets up normally, rmnet_ipa exists, the modem attaches, and the reset
comes three to ten minutes later with no modem crash anywhere in it.

They share a fingerprint that is not specific -- any abnormal reset leaves
088d=01 08c2=02 08c4=40 -- and they share IPA. They may well be two different
bugs. Nothing here has shown they are the same one, and treating the fast one
as a stand-in for the slow one is exactly the sort of assumption this document
exists to prevent.

Next step: re-arm the setup interrupt on bring-up and make the second setup
safe, then see whether the restart reset goes away. ipa is a module, so that
can be built and swapped with depmod, without flashing.

## Tearing the IPA setup down on a modem crash stops the reset

kernel/patches/0081. Two changes: ipa_modem_crashed() calls ipa_teardown() at
the end, and QCOM_SSR_BEFORE_POWERUP lifts the setup-ready interrupt mask.
Built as a module and swapped in with depmod, no flashing; the phone was
rebooted afterwards so ipa would load at boot, since loading it late is not the
same state.

    before   4 resets in 5 bring-ups
    after    0 resets in 4 bring-ups, uptime continuous throughout

Three honest qualifications.

It does not bring mobile data back. rmnet_ipa0 goes when the modem crashes and
is still gone afterwards. ipa_setup() never runs again: no "IPA driver setup
completed successfully" in the log, and the interrupt handler never fires. The
modem does not re-raise setup ready after a restart, so arming that interrupt
was not enough and may well be doing nothing at all.

Which means the teardown is almost certainly the half that matters, since the
other half demonstrably never fires. That is inference from the handler never
running, not a measurement; the two halves have not been applied separately.

And why an AP-side teardown changes what the modem does is still unknown. The
reset always arrived with the AP silent and nothing written, so the modem
escalating rather than the AP failing was always the better reading, and this
result is consistent with it. Consistent is not proven.

The LTE caveat above stands and matters more now. This reproducer is a modem
restart; the LTE bug involves no modem crash at all. 0081 may do nothing
whatsoever for LTE. Testing that needs the ipa hold removed, a SIM with a
working data plan, and an attach left running -- and it is the only test that
answers the question this port actually cares about.

Evidence: evidence/20260826-ipa-teardown-fix-kmsg.log for the clean crash and
bring-up sequence, evidence/20260826-ipa-teardown-fix-rounds.log for the three
consecutive survived rounds.

## 0081 does not fix the LTE reset

Tested directly, with a SIM that has a working data plan, on the kernel with
0081 in place. It reset.

    mmcli -m 0 --set-allowed-modes=4g    -> access tech: lte
    nmcli con up personal                -> and the machine went down

    bootreason=watchdog, pstore empty, PON 088d=01 08c2=02 08c4=40
    uptime 249 before, 109 after

So the four second restart reset and the LTE reset are not the same bug, which
is what the caveat above said might be true. 0081 keeps its own value -- four
resets in five bring-ups became none in four, and a modem crash no longer takes
the SoC with it -- but the problem this directory exists for is still open.

Worth being precise about what was and was not exercised. The bearer first came
up on GSM/GPRS and ran fine, which matches everything known. Forcing 4g dropped
that bearer and registered on LTE, and it was bringing the bearer up again on
LTE that killed it. So the trigger remains what it always was: the modem
attaching a data path on LTE.

## The first kernel log of the LTE death

rhodep-kmsg-tail earns its keep here. This has never been readable before:
ramoops was always empty or corrupt for this reset. The complete tail is in
evidence/20260826-lte-death-kmsg.log; the last two entries are

    [  219.263027] ath10k_snoc c800000.wifi: chan info: invalid frequency 0
    [  233.939110] ipa 5840000.ipa: IPA interrupt 14 (other)

and then nothing, for fifteen seconds, until the reset. The userspace watcher
kept sampling to 249.11, so the AP was alive and well through that silence.

IPA interrupt 14 is IPA_IRQ_TX_SUSPEND, which patch 0072 prints as "other"
because it only names the error conditions. It lands at 233.939 and the bearer
disappears at 234.05, so it belongs to the 4g switch tearing the GPRS bearer
down, not to the death.

The useful part is what is absent. Patch 0072 exists to unmask the IPA error
interrupts, BAD_SNOC_ACCESS above all, on the grounds that "an access the
interconnect refuses looks like that from in here, and that is the shape of the
reset being chased". None of them fired. Not BAD_SNOC_ACCESS, not RX_ERR, not
DEAGGR_ERR, not TX_ERR, not PROC_ERR, not TX_HOLB_DROP, not GSI_EE.

So whatever kills the machine is not an access that IPA itself sees refused.
Combined with the AP staying responsive through the silence and writing
nothing, the remaining shapes are an access refused somewhere IPA cannot
observe, or something outside the AP entirely deciding to pull the SoC down.

## The PMIC says PS_HOLD: the SoC asks to be shut down

The PON registers have been read here for a long time as raw bytes compared
between runs -- 088d 00 -> 01, 08c2 00 -> 02, 08c4 80 -> 40 -- which was enough
to tell one reset from another and nothing more. Mainline has no decoder for
them. The vendor has one, in drivers/input/misc/qpnp-power-on.c, and
`userspace/debug-tools/rhodep-pon-reason` is that logic and those tables.

For this PMIC generation the registers are

    PON_REASON1        0x8C0    why it powered on
    WARM_RESET_REASON1 0x8C2
    PON_OFF_REASON     0x8C7    which of three sequences ran
    POFF_REASON1       0x8C5      if bit 7 set
    FAULT_REASON1      0x8C8      if bit 6, two bytes, table index + 16
    S3_RESET_REASON    0x8CA      if bit 5, table index + 32

The decoder was checked against a known answer first: after the phone was
brought back with a long power-button hold it said "KPDPWR_N (long power key
hold)", which is exactly what had happened.

Then, on the reset after a modem restart with the unpatched ipa.ko:

    off reason register 80
    POFF sequence ran, status 0002
    -> PS_HOLD (the SoC asked to be shut down)
    warm reset reason1: 02

PS_HOLD deasserted means the SoC itself asked to go down. It was not the PMIC
watchdog (that is POFF bit 2), not a fault sequence, not UVLO or OVLO, not
over-temperature, not stage 3. So a whole class of explanation is gone: this is
not the power management hardware deciding the SoC has misbehaved, and it is
not electrical.

Something inside the chip pulls PS_HOLD, and it is not Linux: the AP keeps
answering and writes nothing right up to the moment it stops. That leaves the
secure world. TZ deasserting PS_HOLD is how a controlled system reset is done
on these parts, and it fits everything else -- instant, silent, no crash
reason, no ramoops, and a bootloader that reports the class as "watchdog".

Two limits on this. It was measured on the modem-restart reset, not the LTE
one; confirming the LTE reset reports PS_HOLD too needs the data SIM back in.
And PS_HOLD says who pulled the switch, not why: a subsystem fatal error
escalated by TZ, or a memory protection violation, both end here.

## Two ways this tool broke things before it worked

Both are mine and both are worth knowing.

Its first version looked for the PON block by globbing every regmap in
/sys/kernel/debug/regmap and reading each one. That froze the phone hard enough
to need the power button, because the glob matched i2c devices -- 0-0035,
3-003b, 4-0034, 4-0035 -- and dumping the registers of an i2c device that is
asleep is exactly the "access something that does not answer" hazard this
document opens with. It now asks /sys/bus/spmi/devices which devices exist and
reads only those.

Then it could not find the block at all, and the reason is worth writing down
for anyone reading regmap dumps: a plain read() of that file returns about
8 KB and stops, nowhere near 0x800. The dump is positional and every line is
exactly nine bytes, "%04x: %02x\n", so seeking to addr*9 lands on that
register. Seek to the block you want instead of reading from zero.

## Eliminated after the PS_HOLD result

With PS_HOLD known, the question became what asks TZ to pull it. The obvious
shape is an access somewhere the AP cannot see, made by the modem rather than
by IPA, since none of IPA's own error interrupts fire. Four candidates checked
and all four closed.

**IPA's SMMU stream IDs are complete.** The vendor declares four context banks
for IPA in blair.dtsi: 0x04A0 for the AP with an IOVA pool of
0x20000000+0x20000000, 0x04A1 for WLAN offload, 0x04A2 for the microcontroller,
and 0x04A3 for 11ad. Our node declares 0x4A0 and 0x4A2, the two that mainline
uses; WLAN offload and 11ad are features mainline's IPA does not implement, and
this phone has no 60 GHz radio. There is no modem context bank, so the modem
does not reach memory through IPA's stream IDs at all.

**The AP does not hand the modem any DDR address.** init_modem_driver_req()
fills the QMI request with IPA SRAM offsets only -- ipa->mem_offset plus each
region's offset -- along with an endpoint number and the hardware statistics
bases, which are also SRAM. There is no pointer into main memory for the AP to
get wrong.

**ipa->mem_offset comes from the hardware, not from the device tree.** It is
read out of the IPA SHARED_MEM_SIZE register, MEM_BADDR field, times eight, and
mainline then checks the regions fit inside what the same register reports as
the size. So the offsets handed to the modem cannot be inconsistent with the
hardware unless the driver complains, and it does not: the only things IPA says
at boot are "IPA driver initialized" and "IPA driver setup completed
successfully".

**Patch 0079 is in and correct.** The one place in this path that really did
add an offset to a size, req.hw_stats_quota_size and req.hw_stats_drop_size,
is fixed in the running kernel.

## Why nothing is ever dumped, and what would change that

    qcom_scm firmware:scm: No available mechanism for setting download mode

That line explains every empty pstore in this document. It is not that TZ
declines to record anything; the AP has no way to ask it to.

The earlier note here said mainline disables TrustZone's SDI every boot via
`!download_mode` in qcom_scm_probe(). Re-reading that code, the condition is

	if (of_property_read_bool(np, "qcom,sdi-enabled") || !download_mode)
		qcom_scm_disable_sdi();

and this port already boots with qcom_scm.download_mode=full, and sm6375.dtsi
has no qcom,sdi-enabled. So SDI is *not* being disabled. The problem is the
other half: qcom_scm_set_download_mode() needs either an SCM call this
firmware does not offer, or a register to poke, and it finds neither.

The register comes from `qcom,dload-mode = <&tcsr OFFSET>` on the scm node.
Other SoCs have it -- agatti 0x13000, eliza 0x1a000, glymur 0x4000 -- and
sm6375.dtsi has no general TCSR syscon at all, only tcsr_mutex at 0x340000.
The vendor does it a different way again, through IMEM children like
dload_type@1c with compatible "qcom,msm-imem-dload-type", which does not match
the single masked register mainline writes.

So the next step is a device tree one: add a TCSR syscon for SM6375 and point
qcom,dload-mode at the boot-misc-detect register inside it. The missing piece
is that register's address on this SoC, which is not in the vendor device tree
and will have to come from somewhere else.

Two warnings for whoever does it. Download mode means the phone stops rebooting
after a crash and sits in EDL or the crash dumper instead, so recovery needs a
PC with fastboot or EDL tooling every single time. And it changes what the
reset does, so it is an instrument that alters the experiment: worth having for
one careful look at what TZ recorded, not worth leaving on.

## The three Motorola carveouts are empty, and that says something

patch 0067 already reserves the three regions blair keeps below ramoops, so
their contents survive a reset untouched by Linux:

    mmi_annotate@aefa1800    2 KiB
    tzlog_dump@aefa2000    192 KiB   backup of TrustZone's log
    wdog_cpuctx@aefd2000   184 KiB   per-CPU context saved on a watchdog bite

The addresses check out against the vendor's own header,
include/dt-bindings/moto/moto-mem-reserve.h: WDOG_CPUCTX_BASE is
RAMOOPS_BASE_ADDR 0xaf000000 minus 0x5c00 * 8, which is 0xaefd2000, exactly
what is reserved here.

They cannot be read through /dev/mem: CONFIG_STRICT_DEVMEM is set and a read
returns zero bytes with no error at all, which is easy to mistake for an empty
region. kernel/diag-modules/rhodep_dbgmem.c memremaps all three and exposes
them read-only under /sys/kernel/debug/rhodep_dbgmem. It builds on the phone in
seconds and all three regions map without trouble.

All three contain uninitialised DRAM, not data. The byte distribution settles
it: across 64 KiB of tzlog the commonest values are ff, fd, fb, f7 and fe, all
bits-mostly-set, which is what unwritten cells look like. Encrypted content --
and this platform's TZ log is encrypted, the vendor driver has an
enc_tzlog_info for it -- would be flat. A log would have structure.
wdog_cpuctx contains two kernel-looking pointers in 64 KiB, so it is not saved
CPU context, and mmi_annotate has no readable boot text.

Which is consistent, and worth the trouble to establish:

  * mmi_annotate and tzlog_dump are filled by downstream Motorola drivers this
    port does not have. Reserving the memory keeps it safe; nothing writes it.
  * wdog_cpuctx is filled by TZ when the APSS watchdog bites. It is empty,
    which is independent confirmation of the earlier finding that the APSS
    watchdog is not what fires: its bite is programmed at thirty seconds and
    the reset comes four seconds after bring-up.

So this route is closed: there is no stored account of what TZ saw, because
nothing on this system stores one. Reading TZ's log live needs an SCM call, and
the log does not survive the reset anyway, so it would have to be read in the
instant before the machine goes down.

The module stays, because it is the only way to read those regions at all and
the next person will otherwise repeat the /dev/mem dead end.

## Why nothing in DDR can ever survive this reset

Patch 0082 works: the TCSR syscon is there, /proc/device-tree/soc@0/syscon@3c0000
exists, and "No available mechanism for setting download mode" is gone. qcom_scm
can now clear download mode and ask for a clean reset.

It changed nothing about the evidence. pstore is still empty after the reset.

The reason is in the PON registers, read together rather than one at a time:

    powered on because: Hard Reset, CBL (external power supply)
    POFF sequence ran, status 0002 -> PS_HOLD

PS_HOLD deasserted is the SoC asking the PMIC to remove its power, and the
following boot reports itself as a Hard Reset power-on. That is a power cycle,
not a warm reset: DDR loses its contents. So ramoops cannot survive, the three
Motorola carveouts cannot survive, and no amount of driver work will change
that. It is not that something is wiping the evidence -- the memory holding it
is unpowered.

This closes the whole "read what survived" family of ideas, and it explains
every empty pstore, every noise-filled carveout, and why the console record was
there for an Oops (the kernel was alive to write it, and the reboot after was
warm) and never for this.

It also settles the instrument question. Anything that is to be read after the
fact has to be on flash before the reset, which is what
`userspace/debug-tools/rhodep-kmsg-tail` does with an fsync per line, and it is
the only reason the last kernel lines before the LTE death are known at all.

0082 keeps its value on its own terms: sm6375 could not touch download mode at
all, mainline has the property for the rest of this family, and the reasoning
in the qcm2290 commit applies here word for word. It just does not help this
hunt. Entering download mode rather than clearing it -- putting
qcom_scm.download_mode=full back with the register now present -- would leave
the phone in EDL after each reset, which needs a PC and a firehose loader every
time, and is the one remaining way to ask TZ anything.

# Where this stands, and what to do next

## What is known now, in order of how much it constrains the search

**The SoC asks the PMIC to cut its power.** POFF_REASON1 says PS_HOLD, and the
next boot reports Hard Reset. Not the PMIC watchdog, not a fault, not UVLO,
OVLO, thermal or stage 3. Not the APSS watchdog either, whose bite is
programmed at thirty seconds when the reset comes four seconds after bring-up,
and whose CPU-context carveout is empty.

**It is not Linux doing it.** The AP answers a userspace sampler right up to
the moment it stops, and writes nothing: the last kernel lines are always
"received modem running event" and "remote processor modem is now up", or for
LTE an IPA TX_SUSPEND that belongs to the bearer being torn down. No error, no
warning, no complaint.

**It is not an access IPA sees refused.** Patch 0072 unmasks every IPA error
interrupt precisely because BAD_SNOC_ACCESS is what a refused access looks like
from in there. None of them fire.

**Nothing in DDR can survive to be read.** PS_HOLD means a power cycle, so
ramoops, the tzlog backup and the wdog context are all unpowered, which is why
every one of them holds uninitialised noise. Anything to be read afterwards has
to reach flash before the reset.

**There are two resets, not one.** A modem restart with ipa loaded resets the
SoC about four seconds after the modem comes back, and patch 0081 stops that
completely: four resets in five bring-ups became none in four. The LTE reset is
a different thing -- no modem crash anywhere in it -- and 0081 does not touch
it, tested directly with a data SIM.

## What the phone is running right now

  * kali-boot-dload.img: patches through 0082, and deliberately no
    qcom_scm.download_mode=full on the command line.
  * ipa.ko is the patched one, 50f12d45, so a modem crash no longer takes the
    SoC with it. /home/kali/ipa-original.ko is the stock one, 12dc1306, for
    when the four second reproducer is wanted back. Swap, depmod -a, reboot.
  * The ipa boot hold is still off on purpose. With a data SIM and LTE data
    enabled this phone resets; that is expected in this state and is spelled
    out in /etc/modprobe.d/README-rhodep-ipa-hold-is-OFF.
  * A SIM without a data plan is in it, which is safe.

## Next, in the order I would do it

1. **Finish the leak measurement 0081 still owes.** Three attempts were cut
   short by the very reset being studied. It can be done cleanly now: remove
   ipa.ko first, which is what stops the reset, then run
   `rhodep-glink-refcount-test 500` and read the slab deltas. Cheap, and it is
   the one open question about a patch that is otherwise ready to send.

2. **Ask what the modem was doing in those four seconds.** The reset needs the
   modem to come back up and needs IPA present. The modem's own view is the
   one thing not yet sampled during the window; the AT console works and the
   modem answers until the last moment.

3. **Then download mode, if it comes to that.** With 0082 in place, putting
   qcom_scm.download_mode=full back makes the phone stay in EDL after a reset
   instead of rebooting, which is the only remaining way to ask TZ anything.
   Worth correcting an overcautious note made earlier: this does not risk the
   device. fastboot is always reachable, so the cost is having a PC to hand
   and the time it takes, not a phone that cannot be recovered. It still comes
   third because the two above are cheaper and neither needs a PC.

## Instruments built for this, and what each is for

    rhodep-kmsg-tail            the kernel's last words, fsync per line. The
                                only reason the LTE death is readable at all.
    rhodep-pon-reason           why the PMIC cut power, in words, using the
                                vendor's own tables
    rhodep-modem-restart-watch  the four second reproducer, and a netdev
                                sampler that shows whether IPA came back
    rhodep-glink-hammer         the longer reproducer, kept as a second path
    rhodep-glink-refcount-test  crash, leak and recovery for patch 0080
    rhodep-dbgmem               the three Motorola carveouts, which /dev/mem
                                cannot read and which turn out to be empty
    rhodep-modem-at             the AT console, with help for all 256 commands
