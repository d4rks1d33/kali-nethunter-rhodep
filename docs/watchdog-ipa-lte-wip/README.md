# The watchdog reset with IPA loaded and the modem on LTE

Status: **narrowed to LTE, confirmed.** The reset does not happen on GSM. With
the modem restricted to 2G and IPA loaded, the phone registers, ModemManager
manages it, a GPRS bearer comes up, real traffic flows through IPA's channels
with zero errors, SMS works, and nothing resets -- across reboots.

`userspace/modem-gsm-only/` makes that configuration permanent for anyone who
wants working voice, SMS and slow data today.

## The finding

Everything in this document below this section was written while assuming the
trigger was "the modem reaches the network". It is narrower than that.

Set the modem's mode preference to GSM and the whole failure disappears:

```
qmicli -d qrtr://0 --nas-set-system-selection-preference="gsm"
```

```
Registration state: 'registered'   Description: 'PERSONAL'   RSSI: -93 dBm
state: connected      access tech: gsm, gprs
packet service state: attached
ipa=1     uptime 748s and counting
Messaging | supported storages: sm, me
  /org/freedesktop/ModemManager1/SMS/0 (receiving)
```

Registered, attached, IPA loaded, ModemManager managing the modem, an SMS
arriving, and no reset for as long as it was left running. On LTE the same
sequence kills the SoC within ten seconds of the radio coming online.

3G was tried first and never registered -- `not-registered` throughout, and a
network scan fails with QMI error 3 -- so it says nothing either way. GSM is
the one that answers the question.

## Settled: the data path works, the fault is LTE

The test that was missing has been run and it answers cleanly.

Booted with IPA loading first so the QMI handshake completes and the netdev
exists, mode pinned to GSM, ModemManager brought up a GPRS bearer on its own:

```
Bearer connected: yes    interface: qmapmux0.0
  address: 100.93.125.209/30   gateway: 100.93.125.210
  dns: 181.8.8.8, 181.9.194.73
```

Then two minutes of real traffic through it:

```
8 consecutive HTTP 204 over GPRS
rx 11822 B / tx 6696 B, 83 rx packets / 88 tx packets
rx_errors 0, tx_errors 0, dropped 0
no IPA or GSI errors in dmesg
```

The modem drove IPA's channels with live traffic and nothing reset. So the two
readings left open above are resolved: **the data path is fine and the fault is
specific to LTE.** GSM did not survive because its data call never came up; it
survives with the data call up and carrying packets.

It also survives a reboot and comes back on its own -- data up within about
forty seconds of boot, no intervention.

## The data path is not involved at all

`rhodep-lte-deathwatch` samples the modem's counters into the kernel log four
times a second, so that whatever is still true one tick before the SoC dies
survives in console-ramoops. Run across an LTE switch, with the ramoops ECC
reporting no errors, the tail is this:

```
[  326.652625] CAMBIO-A-LTE-AHORA
[  326.717242] DW 10 t=326.65 run f=0 r=3 h=3 w=0 ipa=1 gsi=18
...
[  339.817068] DW 50 t=339.75 run f=0 r=3 h=3 w=0 ipa=1 gsi=18

ECC: No errors detected
```

Fifty samples, evenly spaced, no jitter and no final line. Then nothing.

Read it field by field:

* the heartbeat is a userspace loop and it was still being scheduled on time
  250 ms before the end, so **the AP did not hang or panic**; it was cut off
  mid-stride;
* `f=0` and `w=0` across the whole window: the modem never raised its fatal
  smp2p bit and its watchdog never fired, and `r`/`h` never moved, so it never
  restarted either. Whatever happened, **the modem did not report dying**;
* `ipa=1 gsi=18` never change. For thirteen seconds of LTE attach **the IPA and
  GSI interrupts do not tick once.**

That last line settles a question this whole investigation was built around.
The data path is idle when the reset happens. IPA is a prerequisite for
reaching the failure -- without it the modem refuses to register at all -- but
it is not a participant in it. The endpoint configuration, the SRAM layout, the
GSI rings, the uC: none of them are doing anything at the moment the phone
dies, and the GSM run already showed they carry real traffic correctly.

So the search moves off the IPA driver and onto what an LTE attach asks of the
hardware that a GSM attach does not: RF front-end power. Supporting this, the
bootloader's own bitmask separates the two cases cleanly --
`powerup_reason=0x00004000` for a clean reboot against `0x00008000` for this
reset -- and the battery was at 100% and full at 4.397 V, so a flat battery
browning out under TX bursts is not the explanation.

The PMIC's standard reason registers (0x808-0x810) are identical across both,
because the bootloader reads and clears them, but three registers in the PON
peripheral's spare area do survive and do differ:

```
reg     clean reboot   lte reset
088d    00             01
08c2    00             02
08c4    80             40
```

Not yet decoded, but reproducible, and the only PMIC state that outlives the
reset.

### Checked and identical, do not redo

**CX and MX voting for the modem.** The thought was that LTE clocks the Q6
higher, so if mainline were missing a rail vote the Q6 could fail hard enough
that it never gets to raise `fatal`. It is not that. Downstream's `pil_modem`
proxies `vdd_cx-supply = <&S1E_LEVEL>` and nothing else; mainline's
`remoteproc_mss` has `power-domains = <&rpmpd SM6375_VDDCX>` and nothing else.
Neither side votes MX. The one difference left is downstream asking for 100 mA
of load alongside the corner and holding the proxy for 10 s, where mainline
releases at handover, and a load hint only steers the RPM regulator's mode.

**The modem's watchdog interrupt is wired correctly.** Downstream routes it as
`GIC_SPI 307`, mainline as `GIC_SPI 307`, and `/proc/interrupts` shows it
landing at hwirq 339 as it should.

But note what `w=0` does and does not prove. It counts the watchdog as
delivered *to the AP*. If the modem's watchdog fires and Trust Zone takes it
first and resets the whole chip instead of restarting just the subsystem, the
AP's counter never moves and there is no fatal either, because TZ acted before
any of it. That is consistent with every observation here, and it is not
excluded.

**Trying to cycle the modem to test that came out inconclusive.** Writing
`stop` then `start` to `/sys/class/remoteproc/remoteproc0/state` left it
`running` throughout with nothing in the kernel log. In mainline that write
only drops a reference, and something else holds the modem booted, so it did
nothing and said nothing. Testing the escalation theory needs a way to make the
modem crash for real.

## It is a ten second timeout, and it is not QMI

Three runs, measuring from the last GSI interrupt to the moment the machine
stops answering:

```
run 1   last GSI t=660.17   death ~669.9   9.7 s
run 2   last GSI t=140.36   death  150.26  9.9 s
run 3   last GSI t=58.30    death ~67.0    8.7 s
```

The shape is the same every time. The modem attaches, IPA wakes, GSI takes on
the order of a dozen interrupts while the channels are programmed, everything
goes quiet, and about ten seconds later the SoC is gone. A constant interval
is a timeout expiring. It is not a race and it is not a rail sagging.

Runtime PM was the first suspect, since the AP suspends IPA immediately after
the attach and downstream never does while the modem is up. It is not that:
with `power/control` forced to `on` and `pm=act` held across the whole window,
it still died, at nine point nine seconds.

QMI was the second, and a better one. Mainline's IPA answers exactly two
requests, `IPA_QMI_INDICATION_REGISTER` and `IPA_QMI_DRIVER_INIT_COMPLETE`,
where downstream implements a good deal more including filter rule
installation. And `qmi_invoke_handler()` drops anything it has no handler for
with a bare `return`, no message. So the silence this investigation kept
hitting could have been a dropped request all along.

Patch 0074 makes the generic QMI layer say so. It works -- ten messages get
reported during a normal boot, all `msg_id=0x0023` indications -- and during
the entire LTE window **not one further message is dropped**. The modem is not
asking the AP for anything it fails to answer. QMI is out, and out on evidence
rather than on absence.

What is left fits the rest. The modem programs its GSI channels, blocks on IPA
hardware that never answers, and its own Q6 watchdog -- ten seconds is the
usual setting -- takes it down. Trust Zone handles that first and resets the
chip rather than restarting the subsystem, which is why the AP's copy of the
modem watchdog interrupt stays at zero and no fatal ever arrives.

That makes the next question concrete: which GSI channel does the modem get
stuck on, and what is different about its configuration.

### Also checked in the ten second window, all clean

**GSI errors.** Mainline enables `ERROR_INT` in `CNTXT_GLOB_IRQ_EN` and
`BUS_ERROR | CMD_FIFO_OVRFLOW | MCS_STACK_OVRFLOW` in `CNTXT_GSI_IRQ_EN`, and
`gsi_isr_glob_chan_err()` prints the offending EE, so an error on the *modem's*
channels would be reported too, not just the AP's. Nothing is printed.

**rmtfs and tqftpserv.** The modem depends on both for EFS and NV, and LTE
needs more of that than GSM does, so a stalled storage service blocking a modem
task was a good fit for a ten second watchdog. Piped both journals into the
kernel log so they land in ramoops. Across the window the only output is two
`got del_client` lines at the instant of the mode switch, which is the modem
closing sessions. No errors, no stalls.

**Modem channel allocation.** Mainline allocates GSI channels on the modem's
behalf only as a v4.2 hardware workaround -- `modem_alloc = gsi->version ==
IPA_VERSION_4_2` -- so on v4.11 it leaves them to the modem, which is what
downstream does.

The list of things the AP can see and has been checked is now long: the data
path, runtime PM, QMI, GSI errors, IPA error interrupts, rmtfs, the reserved
memory map, CX and MX voting, the battery, the AP watchdog, modem fatal and
modem watchdog as delivered to the AP. All clean. Whatever expires after ten
seconds is not visible from this side of the chip.

## It needs a real attach, not just LTE

With the SIM not answering -- `Card state: no-atr-received`, modem healthy and
responding to QMI -- the mode was forced to LTE and left there:

```
+5s   ... +130s   up, reg=not-registered-searching
SURVIVED, uptime 292 s
```

Two minutes and a bit, where a working SIM gets ten to thirty seconds. So
selecting LTE and scanning for cells is not what kills it. The modem has to
actually attach.

The honest limit of this test: without a SIM the modem scans and decodes but
never does a random access, so it barely transmits. It rules out mode
selection and receiver bring-up. It does not rule out something that only
happens at attach transmit power.

Combined with everything else, the window has narrowed to the attach itself and
the default bearer that comes with it -- which is also where the GSI
programming happens, ten seconds before the end.

## A caveat on "GSM works" that has to be stated

The GSM run registered, attached, was managed by ModemManager and received an
SMS, and it did not reset. What it never did was complete a **data call**.
Every attempt failed, and the reason turned out to be mundane: on a by-hand
`modprobe ipa` the QMI handshake does not redo itself, so `ipa_modem_start()`
never runs, `rmnet_ipa0` does not exist, and ModemManager reports

```
Failed to add link with mux id 1: rmnet_ipa0 interface is not available
```

So the comparison is not yet like for like. LTE was killed at registration,
before any data. GSM survived registration. But GSM was never put through the
part where the modem actually drives IPA's channels for traffic.

That leaves two readings and the evidence does not separate them:

- The fault is specific to LTE, and GSM would survive a data call too.
- The fault is in the data path, and GSM survived only because its data call
  never came up.

**The test that decides it has not been run**: boot with IPA loading first so
the handshake completes and `rmnet_ipa0` exists, mode forced to GSM, then bring
up a GPRS data call and pass traffic. If that survives, LTE-specific is
confirmed. If it resets, the whole framing moves back to the data path and the
LTE finding becomes a coincidence of what GSM never got to do.

## Checked in this round and matching

- The one LTE-specific endpoint mainline declares, `MODEM_DL_NLO_TX`, is
  channel 2 endpoint 8, and downstream's `Q6_DL_NLO_DATA_PROD` for IPA_4_11 is
  `{ 8, 2, ... }`. Identical.
- The filter endpoint bitmap. Mainline marks three endpoints `filter_support`
  -- 2, 5 and 8 -- and downstream marks exactly the same three `support_flt`.
- Mainline describes three modem endpoints where downstream has eight. The
  extra five sit at endpoints 10 to 13 on channels 3 to 6 of the Q6 execution
  environment and collide with nothing mainline uses.

Band-by-band testing is not available: the modem answers a single-band
restriction with QMI error 25, DeviceUnsupported, through either qmicli or
ModemManager.

For the record, the firmware in play is
`MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`, and the modem carries an
initial EPS bearer for `datos.personal.com` with `ue mode of operation:
csps-1`.

## What this rules out, which is most of this document

The AP side was never the problem. Everything below that was checked and found
correct -- the SRAM layout, the IMEM address, the endpoint and channel
numbering, the register windows, the SMEM size, the QMI handshake completing,
the absence of IPA and GSI errors, the watchdog not barking -- was correct
because it *is* correct. The same driver, the same tables, the same handshake
and the same hardware configuration carry a working GSM attach.

Whatever breaks is specific to what the modem does when it attaches on LTE, and
the AP's IPA configuration is a precondition rather than a cause: without IPA
the modem will not register at all, on any technology.

## Where to look now

LTE attach differs from GSM attach in what the modem sets up on its side. The
obvious candidates, none yet examined:

- The LTE data path uses IPA's accelerated pipes and GSI channels in a way
  GPRS does not. GSI channel and event ring configuration is still the one part
  of the IPA description never compared field by field against downstream.
- LTE bands. Personal Argentina runs LTE on bands this port may not have
  described correctly, and RF front-end configuration is entirely unexamined.
- Carrier aggregation and the higher power classes LTE uses.

## A usable workaround, with its cost

With `mode preference = gsm` the modem is stable with IPA loaded, which means
ModemManager creates the modem and **voice and SMS work**. That is a real
change to what the port can do: the README's "no dialer, no SMS UI" limitation
came from ModemManager refusing a modem with no net port, and there is a net
port here.

The cost is that there is no usable data -- GPRS only, and the data call did
not complete in testing, failing with `call-already-present` from the modem.

The risk is that anything setting the mode back to LTE brings the reset back,
and the setting lives in the modem's own NV rather than anywhere this port
controls. It survives reboots on its own, which cuts both ways.

Making this the default would mean dropping `rhodep-ipa-hold.conf` and forcing
GSM instead, and that trade has not been made here.

(The status line above supersedes this one, kept for the history of how the
search went.) This is the bug that keeps mobile data switched off, via
`/etc/modprobe.d/rhodep-ipa-hold.conf`.

Everything here was gathered after `0067` made crash logs survive. Before that
the console came back with 1-4% of its bytes altered and every compressed dmesg
record died on `zlib_inflate() failed`, so there was nothing to read. Captures
below are marker-verified and came back with `ECC: No errors detected`.

## Capturing the boot path: how not to do it

The by-hand path is the only one captured. The boot path -- where udev
autoloads ipa at around 15 s, before the modem is up -- resets too, and its
final messages are still unknown. One attempt cost a reset loop and produced
nothing. What went wrong is worth writing down.

`rhodep-holds-enforce.service` puts the hold back on every boot, which is why
the boot after a crash comes up with ipa unloaded and stable. To let ipa load
at boot it has to be neutralised, and `systemctl mask` **fails** on it: a real
unit file already exists at `/etc/systemd/system/rhodep-holds-enforce.service`
and masking wants to put a symlink there. The error was printed and not read
before rebooting.

The other half of the failure is ramoops itself. The console zone holds one
boot, and systemd-pstore moves it aside on the next. The crashed boot's record
was overwritten by the boots that followed while the phone was being rescued,
so by the time anything was read it was a clean shutdown from the recovery
reboot.

One more thing that attempt got wrong, on the reading side rather than the
setup. The phone was declared to be in a reset loop and rescued with a
blacklist image, and it was not: it reset twice, then ran 8, 12, 7 and 19
minutes across the next four boots, accepting ssh logins throughout. It had
stabilised on its own for the reason above -- ModemManager was down, so nothing
brought the radio online. Two failed reset boots and a handful of unrelated ssh
timeouts were read as a loop. Check `journalctl --list-boots` before concluding
a phone is looping; the boot durations say it plainly.

For the next attempt:

- Neutralise the enforcer by moving its unit file aside
  (`mv /etc/systemd/system/rhodep-holds-enforce.service /root/` plus
  `systemctl daemon-reload`), not by masking it, and verify it is gone before
  rebooting.
- Arrange the restore to happen on the *second* boot, and confirm the restoring
  unit actually runs before udev autoloads ipa -- ordering `Before=
  systemd-udev-trigger.service` was written but never verified to win that
  race.
- Read `/var/lib/systemd/pstore/console-ramoops-0` on the **first** boot after
  the crash, before anything else reboots the phone. There is exactly one
  chance, and it was missed twice: once to the rescue reboots, once because the
  boot in between never crashed and its clean record overwrote the interesting
  one.
- Boot from an image carrying `modprobe.blacklist=ipa` whenever ipa is not the
  thing being tested. It makes the safe state a property of the kernel command
  line instead of a race between udev and a userspace service, which is what
  went wrong. `kali-boot-v130-RESCATE-sin-ipa.img` is exactly v128 plus that
  one word.

## Reproducing it

Takes under a minute with a SIM that registers:

```
chattr -i /etc/modprobe.d/rhodep-ipa-hold.conf
mv /etc/modprobe.d/rhodep-ipa-hold.conf /root/ipa-hold.off
modprobe ipa
qmicli -d qrtr://0 --dms-set-operating-mode=online
```

ModemManager then attaches LTE on its own and the SoC resets. Observed twice at
roughly 30 and 45 seconds after `modprobe`, against the 3 to 10 minutes the
README quotes for the boot-time path.

`protect-files` restores the hold on the next boot, which is the safety net
working as intended -- and is also why an early attempt looked stable for
fourteen minutes: IPA had never loaded.

Confirmed with `androidboot.bootreason=watchdog` on the following boot.

## What the kernel says before it dies

```
[  138.770081] REPRO3-1787527920
[  138.902359] ipa 5840000.ipa: IPA driver initialized
[  138.940974] ipa 5840000.ipa: IPA driver setup completed successfully
[  138.948344] qcom_q6v5_pas 6080000.remoteproc: Handover signaled, but it
               already happened
[  138.949212] ipa 5840000.ipa: unexpected init_completed response

ECC: No errors detected
```

That is the end of the log. No exception, no backtrace, no watchdog bark,
nothing from the softlockup detector, which is enabled with a five second
threshold. The SoC simply stops.

Worth stating plainly because it shapes the search: **this does not look like a
hung CPU.** A hung CPU would trip the softlockup detector or leave an RCU
stall, and neither appears. Linux is also not managing the APSS watchdog at all
here -- `CONFIG_QCOM_WDT` is not enabled, `/sys/class/watchdog` is empty and
there is no `/dev/watchdog` -- so nothing in the kernel is petting or reporting
it.

## The warning is a symptom, not the cause

`ipa_uc.c:164` prints it when the microcontroller reports INIT_COMPLETED while
`ipa->uc_powered` is false:

```c
case IPA_UC_RESPONSE_INIT_COMPLETED:
	if (ipa->uc_powered) {
		ipa->uc_loaded = true;
		ipa_power_retention(ipa, true);
		(void)pm_runtime_put_autosuspend(dev);
		ipa->uc_powered = false;
	} else {
		dev_warn(dev, "unexpected init_completed response\n");
	}
```

`ipa_uc_power()` guards its body with a file-scope `static bool already`, so it
runs once per module load. Load IPA after the modem is already up -- which is
what `modprobe` by hand does, and what the preceding "Handover signaled, but it
already happened" line reports -- and the response arrives with nobody having
armed `uc_powered`.

Three things do not happen as a result. `uc_loaded` stays false. The runtime PM
reference taken in `ipa_uc_power()` is never dropped. And
`ipa_power_retention()` is never called.

That last one looked promising and is not: it sends
`{ class: bcm, res: ipa_pc, val: 1 }` over QMP and returns immediately when
`power->qmp` is NULL. The rhodep IPA node has no `qcom,qmp` property, so on
this platform the call is a no-op whether it happens or not.

Which leaves the warning as evidence of ordering, not a fault in itself. It is
also absent from the boot-time path, where IPA loads at ~15 s and the modem
comes up after it; that path resets too, and its final message has not been
captured yet.

## Already fixed, and still broken: read 0042 and 0048 first

Before theorising, read `0042-net-ipa-sm6375-fix-imem-address.patch` and
`0048-net-ipa-sm6375-own-sram-layout.patch`. Both were written against this
exact bug and both are already in the build.

0042 found that `ipa_data_v4_11_sm6375` was reusing SC7280's `imem_addr` of
0x146a8000, which is not IMEM on this SoC, and repointed it at 0x0c123000 --
taken from the region downstream identity maps into IPA's AP context bank in
blair.dtsi. It has the SMMU fault that used to be produced:

```
arm-smmu c600000.iommu: Unhandled context fault: fsr=0x402,
    iova=0x0c123080, fsynr=0x370002, cbfrsynra=0x4a0, cb=7
androidboot.bootreason=watchdog
```

0048 gave the SoC its own `ipa_mem_local_data_sm6375` rather than SC7280's
SRAM layout.

Two things follow, and both were established the slow way in this session by
rediscovering the above from scratch:

- The IMEM address and the SRAM layout are **not** the remaining problem. Both
  were already checked against the vendor and corrected.
- **The failure has changed shape.** The 0042-era reset announced itself with
  an unhandled SMMU context fault. The reset today produces no fault, from any
  context bank, in any capture. Whatever is left is not IPA reading through its
  AP context bank at an unmapped address.

Checked again this session and confirmed still correct, so nobody has to redo
it: the endpoint and channel numbering matches the vendor for all seven
endpoints that matter (`APPS_CMD_PROD` ep 7 ch 5, `APPS_WAN_PROD` ep 2 ch 2,
`APPS_LAN_CONS` ep 9 ch 14, `APPS_WAN_CONS` ep 16 ch 7, and the Q6 side), and
`qcom,ipa-q6-smem-size` in holi.dtsi is 36864, the 0x9000 the driver already
uses.

## The AP watchdog did not do it

Patch 0068 describes the APSS watchdog so qcom-wdt can bind to it, and
`scripts/rhodep-wdt-arm` puts it to work: timeout 30 s, pretimeout 15 s, petted
every 5 s, with the panic governor selected so a bark becomes a panic carrying
every CPU's backtrace.

Reproduced with that running. **No bark, no panic, no backtrace.** The console
record is byte-identical to the ones taken without it, ECC reporting no errors,
ending at the same `clearing disabled IPA interrupts` line.

That is a real elimination rather than another silence. The watchdog was being
fed up to the instant of the reset, so nothing on the AP side timed out: no CPU
was wedged, no interrupt storm starved the pet, the kernel was alive and
scheduling. Every hypothesis of the form "something hangs and the watchdog
bites" is out, and `androidboot.bootreason=watchdog` is the bootloader's label
for an abnormal reset rather than a description of what happened.

Neither side signals anything. `qcom_q6v5_pas` never reports a fatal error, and
the modem node has the full set wired -- wdog, fatal, ready, handover,
stop-ack, shutdown-ack over smp2p, plus the stop state -- so the AP would hear
about a modem crash if there were one to hear.

What is left is a hardware-level abort: an access the interconnect or TrustZone
refuses, escalated straight to a system reset with no chance for anyone to
write a line. Which is exactly what 0026 describes running into.

## Registration is the trigger, not traffic

Confirmed twice, from both directions.

Stopped ModemManager so no bearer could exist, loaded ipa by hand, switched the
radio on. The modem reached `Registration state: 'registered'` and the SoC went
down within ten seconds of it. No data session, no packets.

The other direction fell out of a boot that was not meant to be an experiment.
udev autoloaded ipa at 14 s while ModemManager happened to still be stopped
from an earlier run, so the radio never went online and the modem never
registered. **That boot ran for eighteen minutes with ipa loaded and nothing
happened.** IPA being loaded is harmless on its own; what it takes is the modem
reaching the network, which is when the modem programs its own IPA pipes
through its own execution environment.

Stopped ModemManager so no bearer could be created, loaded ipa by hand and
switched the radio on. The modem reached `Registration state: 'registered'`
and the SoC went down within ten seconds of it.

So a data session is not needed and neither is a single packet. What is needed
is IPA loaded and the modem reaching the network, which is the point the modem
programs its own IPA pipes through its own execution environment. The fault is
on that side of the hardware, not in anything the AP's data path does.

## TrustZone leaves nothing behind

0067 reserves the three regions the vendor's memory map says firmware writes.
With `iomem=relaxed` on the cmdline they can be read through /dev/mem -- by
mmap, not read(), since they are `no-map` and `xlate_dev_mem_ptr()` returns
NULL for those, which is an EFAULT.

Dumped before a reset and after, all three came back **byte for byte
identical**, and the content is uninitialised DRAM noise in both:

| region | bytes | identical across the reset |
| ------ | ----: | -------------------------- |
| `tzlog_dump@aefa2000` | 196608 | 100% |
| `wdog_cpuctx@aefd2000` | 188416 | 100% |
| `mmi_annotate@aefa1800` | 2048 | 100% |

Nothing writes them. TrustZone keeps no log backup here and no CPU context is
dumped, so there is no firmware trace to read. Downstream populates these
through its own drivers and SCM calls, which mainline has no equivalent of; the
buffers alone are not enough.

Incidentally this also shows DRAM survives the reset intact enough to compare
byte for byte, which is consistent with ramoops working at around 1% corruption
before ECC.

A dump has to be synced. The reset is abrupt and a file left in the page cache
does not survive it -- two runs were lost that way before `rhodep-fwdump`
learned to fsync.

## Checked against the vendor and matching, so do not redo these

Everything comparable between mainline's description of this IPA and the vendor
tree has now been diffed:

| what | result |
| ---- | ------ |
| SRAM region table (0048 vs `ipa_4_11_mem_part`) | all 22 regions identical, offsets and sizes |
| IMEM address (0042) | 0x0C123000, matches `ipa_smmu_ap`'s additional-mapping exactly |
| uC context bank 0x4a2 | vendor gives it no additional-mapping at all; nothing missing |
| endpoint and channel numbering | all seven that matter match |
| `qcom,ipa-q6-smem-size` | 36864, the 0x9000 already used |
| register windows | `gsi` identical to vendor's `gsi-base`; `ipa-reg` and `ipa-shared` are `ipa-base` + 0x40000 and + 0x50000 |
| modem SSR wiring | complete |

## The modem cannot register without IPA, so the two cannot be separated

The experiment that should have decomposed this does not exist, and it took a
while to see why.

With ModemManager disabled and IPA loaded at boot, the phone is **stable**: the
handshake completes, `rmnet_ipa0` is created and sits DOWN, and nothing happens
for as long as it is left alone. Switch the radio on by hand and it is gone
within ten seconds, with an empty log. So registration is the trigger and
nothing the AP does on its own matters.

The obvious next question is whether registration alone does it, without IPA.
It cannot be asked. ModemManager refuses a QMI modem with no net port, so
without IPA it never manages the modem and the SIM is never provisioned.
Provisioning the SIM by hand through qmicli works -- `Primary GW: slot '1',
application '1'` -- and the modem still will not register: it sits in
`not-registered-searching` indefinitely and answers a forced search with

```
error: couldn't force network search: QMI protocol error (52): 'DeviceNotReady'
```

Nine minutes of that, perfectly stable. The modem gates registration on its own
data path being ready, which needs the QMI handshake, which needs IPA. So "IPA
loaded" and "modem registered" are not independent variables on this device and
no experiment can hold one while varying the other.

That also reframes the original note. "With the radio on but not attached it
runs for 22 minutes" was never a registered modem; it was a modem that could
not register.

One test was run and thrown away before this was understood: `rmmod ipa` on a
boot where IPA had already loaded and completed the handshake, then radio on.
It died immediately, which proves nothing -- the hardware was configured and the
modem held references to a driver that had just been removed.

## The boot path completes the handshake and dies anyway

With the instrumented module actually installed -- copied to /lib/modules, not
flashed -- the boot path finally showed itself. Two captures, both clean:

```
[   19.988692] ipa 5840000.ipa: received modem starting event
[   24.200582] ipa 5840000.ipa: received modem running event
[   30.747079] ipa-qmi: -> INIT_DRIVER ... modem=[0x27d8 +0x800] ctrl_ep=16
                                            skip_uc=0
[   31.099992] ipa-qmi: <- INDICATION_REGISTER from modem
[   31.112575] ipa-qmi: <- DRIVER_INIT_COMPLETE from modem
[   31.112587] ipa-qmi: ready check: modem=1 uc=0 ...
[   31.113774] ipa-qmi: ready check: modem=1 uc=1 ...
[   31.114594] ipa-qmi: both sides ready, starting modem netdev
[   31.115583] ipa-qmi: both sides ready, starting modem netdev
```

Everything works. The modem answers, both flags go true, `ipa_modem_start()`
runs. Then seventeen seconds later the SoC is gone, with nothing in between.

Three things follow:

- **The handshake is not the bug.** That whole line of investigation is closed.
- `skip_uc` is 0 here too, and the modem answers anyway. The hang documented
  below is specific to loading the module by hand, where the modem has been up
  far longer. Worth fixing, not the cause.
- "both sides ready" prints twice, so `ipa_qmi_ready()` reaches the end twice
  and `ipa_modem_start()` is called twice. The second call returns early on its
  own state check, so it is harmless, but it is not intended either.

## IPA is not reporting an error

`ipa_interrupt_process()` handles UC_0, UC_1 and TX_SUSPEND and sends the rest
to "silently ignore (and clear) any other condition", and nothing ever unmasks
the error conditions. So BAD_SNOC_ACCESS -- which is what an access the
interconnect refuses looks like from inside IPA, and the obvious candidate for
a reset with no other trace -- could not have been seen.

`0072-DIAGNOSTIC-net-ipa-report-error-interrupts.patch` unmasks
BAD_SNOC_ACCESS, RX_ERR, DEAGGR_ERR, TX_ERR, PROC_ERR and TX_HOLB_DROP and
names whichever arrives. Reproduced with it in place: **none of them fire.**
IPA reports nothing at all before the reset.

That closes the other obvious line. Whatever refuses the access, IPA is not the
one being told about it.

## What is left, and it is thin

The only thing that consistently precedes the reset is the ADSP handover storm:

```
q6v5_handover_interrupt: 252 callbacks suppressed
qcom_q6v5_pas a400000.remoteproc: Handover signaled, but it already happened
```

It arrives in the last seconds before every reset, at the same suppressed count
each time, from the ADSP rather than the modem, and 0070 does not stop it even
though it masks the line after the first handover -- which means either the
mask does not stick under IRQF_ONESHOT, or something re-enables it.

Correlation is all this is so far. Counting across the saved captures does not
separate it cleanly, because the "IPA driver initialized" line used to tell
whether ipa was loaded is often scrolled out of the console ring, so several
captures are unclassifiable. It is the strongest remaining thread and it is
still only a thread.

## The modem hangs on INIT_DRIVER, and why

This is the concrete result of the session, and it corrects the section that
follows it.

`ipa_qmi_init_driver()` builds its request with

```c
req.skip_uc_load = ipa->uc_loaded ? 1 : 0;
```

`uc_loaded` is only set in `ipa_uc_response_hdlr()`, and only when the driver
also holds the power reference it took in `ipa_uc_power()`. That function
guards its body with a file-scope `static bool already`, so it runs once per
module load. Bind IPA while the modem is already up -- by hand, or after any
modem restart -- and the microcontroller has been running since long before
this driver existed. Either it answers with nobody holding a reference, which
takes the `else` branch and leaves the flag false, or it does not answer at all,
which also leaves it false.

Either way the AP then tells the modem to load a microcontroller that is
already running, and **the modem never replies**:

```
ipa-qmi: -> INIT_DRIVER hdr=[0x688..0x8c7] v4rt=0x508 v6rt=0x608
         v4flt=0x308 v6flt=0x408 modem=[0x27d8 +0x800] ctrl_ep=16 skip_uc=0
ipa 5840000.ipa: error -110 awaiting init driver response
```

Sixty seconds of timeout, reproduced on three consecutive loads. Every address
in that request is correct for this SoC; `skip_uc` is the one field that is
wrong.

Forcing `skip_uc = 1` changes the outcome immediately:

```
ipa-qmi: -> INIT_DRIVER ... skip_uc=1
ipa-qmi: <- INDICATION_REGISTER from modem
ipa-qmi: ready check: modem=0 uc=0 indication_requested=1 sent=0 initial_boot=1
ipa-qmi: ready check: modem=1 uc=0 indication_requested=1 sent=0 initial_boot=1
```

The modem answers, `modem_ready` goes true and the handshake moves. No timeout.

**It does not stop the reset.** With the handshake unstuck the SoC still goes
down when the radio comes online. So the hang and the reset are two separate
faults, and this one is now understood.

The handshake still stalls at `uc=0`. `uc_ready` is set only in
`ipa_server_driver_init_complete()`, from a DRIVER_INIT_COMPLETE the modem
sends on first boot, so `ipa_qmi_ready()` returns early, `ipa_modem_start()`
never runs and no rmnet netdev is created. That is where to pick this up.

`0071-net-ipa-init-completed-means-uc-loaded.patch`, parked in this directory,
sets `uc_loaded` from the response instead of from the power bookkeeping. It is
right as far as it goes and it is not enough: on a re-load the microcontroller
does not send INIT_COMPLETED at all, so there is no response to learn from. A
real fix has to work out that the uC is up without being told -- reading its
state, or treating "modem already running at probe" as implying it.

## Two hours were spent reading an uninstrumented module

Worth recording as a method failure. Kernel modules live in the rootfs, not in
the boot image. Patch 0069 was built, packaged, and the boot image flashed four
times, while the phone went on loading the `ipa.ko` already installed under
`/lib/modules` -- which had none of the tracing in it.

Every conclusion drawn from "no `ipa-qmi:` lines appear" during that stretch was
worthless: the code was never there. The QMI handshake was assumed dead when it
was simply unobserved. Installing the module from the freshly built apk, with
`depmod -a`, produced the trace above within a minute.

When a change is to a module, copy the `.ko`. Flashing a boot image does
nothing for it.

## The AP does nothing between loading and dying

With patch 0069 tracing every step of the QMI handshake and 0070 clearing the
handover noise out of the way, the log around the reset is finally legible:

```
[  140.350985] ipa 5840000.ipa: IPA driver initialized
[  140.399149] ipa 5840000.ipa: IPA driver setup completed successfully
[  140.406083] ipa 5840000.ipa: unexpected init_completed response
                    ... fourteen seconds of nothing ...
                    reset
```

**Not one `ipa-qmi:` line** -- which turned out to mean nothing at all, because
the module being loaded did not contain the tracing. See the section above. The
handshake does happen; it hangs. Read this section only for the timing, which
still holds: fourteen seconds pass between the last message and the reset.

Which leaves one thing the AP did do: `ipa_probe()` reinitialises IPA while the
modem is actively using it. The modem then reaches the network, touches state
the AP has just reset underneath it, and the SoC goes down. That fits every
observation, including why registration is the trigger and why nothing on the
AP side has anything to report.

It also means **the by-hand reproduction may not be the same bug as the boot
path**, where IPA initialises before the modem exists. The two have been
treated as one throughout. The boot path still has no capture.

## The handover interrupt storm, and what 0060 hid

Tracing turned up something unrelated on the way. Seconds before each reset:

```
q6v5_handover_interrupt: 251 callbacks suppressed
qcom_q6v5_pas a400000.remoteproc: Handover signaled, but it already happened
```

`q6v5_handover_interrupt()` does its work, sets `handover_issued` and returns
with the interrupt still enabled, so every further transition on the line
re-enters a threaded handler that only logs. Patch 0060 ratelimited the message
-- around 26000 of them per boot -- and the storm was left running underneath.

0070 masks the line after the first handover, keeping the enable in
`qcom_q6v5_prepare()` balanced. Bursts drop from 251 to 4 and the boot goes
from thousands of messages to three.

**It does not fix the reset**, which was the honest expectation. And it does not
fully stop the ADSP's messages either: a few still arrive per boot, from
`a400000.remoteproc`, without the ADSP ever being restarted (no "powering up"
in the log). Why that line keeps transitioning is unexplained.

## The strongest lead

From the commit message of `0026-arm64-dts-qcom-rhodep-enable-ipa.patch`,
written when the IPA register windows were first worked out:

> Using the old +0x47000 window makes the very first access to the shared SRAM
> reset the SoC instantly, with no CPU exception and no log output at all.

That is this signature exactly: instant reset, no exception, no log. The port
has already hit this failure mode once, from a wrong offset into IPA's shared
SRAM, and fixed it by moving the window to +0x10000 for IPA v4.5 and later.

So the hypothesis worth testing next is that something else in the IPA or GSI
description is still wrong, and only gets touched once the modem sets up its
own channels -- which is why it needs LTE attach and not merely a loaded
driver. With the AP context bank ruled out by the absence of any fault, the
remaining candidates are:

- **`skip_uc_load` in the QMI handshake.** `ipa_qmi_init_driver()` sends
  `skip_uc_load = ipa->uc_loaded ? 1 : 0`, and `uc_loaded` is left false by the
  "unexpected init_completed response" path described above. So when IPA is
  loaded after the modem is already up, the AP tells the modem to load a
  microcontroller that is already running. That is a plausible way to get an
  abort with no software trace, and it makes the warning a cause rather than a
  symptom -- but only for the by-hand path. The boot path, where IPA loads
  before the modem, resets too, and its last messages have never been captured.
  **Capturing the boot-path crash is the cheapest next step** and it decides
  whether this matters.
- **GSI channel and event ring configuration**, which is the one part of the
  IPA description not yet compared field by field, and the part the modem
  touches when it brings up its channels.
- **Qualcomm NoC error reporting.** An unclaimed or refused bus access is what
  this looks like, and the SoC has error registers for it that mainline does
  not read on this platform.

## What did not explain it

- **Deep cpuidle (0061).** Retention was the obvious link and it is a no-op
  here. Not tested at runtime because the reasoning no longer supports it, and
  the bug predates the patch.
- **The interconnect provider.** It was blacklisted when this bug was first
  written up, and since `0065` it is built in and always running, so that
  variable has already changed without affecting the outcome.

## Where the captures are

Read after any reset with:

```
sudo cat /var/lib/systemd/pstore/console-ramoops-0
```

Always check that the marker written before the run is present. One capture in
this session was read and nearly reported before noticing it came from an
earlier boot: a one-second boot had come and gone in between and overwritten
the zone. Write a marker to `/dev/kmsg`, then grep for it in the record before
believing anything in it.
