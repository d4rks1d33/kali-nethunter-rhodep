# The watchdog reset with IPA loaded and the modem on LTE

Status: **reproducible on demand, last kernel message now known, cause not
found.** This is the bug that keeps mobile data switched off, via
`/etc/modprobe.d/rhodep-ipa-hold.conf`.

Everything here was gathered after `0067` made crash logs survive. Before that
the console came back with 1-4% of its bytes altered and every compressed dmesg
record died on `zlib_inflate() failed`, so there was nothing to read. Captures
below are marker-verified and came back with `ECC: No errors detected`.

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

- **The TrustZone log.** 0067 reserves `tzlog_dump@aefa2000`, 192 KiB that TZ
  writes on its own. If TZ is the one pulling the reset, its reason is
  plausibly in there. Reading it needs `iomem=relaxed` on the cmdline, since
  CONFIG_STRICT_DEVMEM refuses reserved ranges, and then somebody has to work
  out the format. This is the most direct route left to a cause.
- **`wdog_cpuctx@aefd2000`**, also reserved by 0067: the CPU context the
  firmware dumps at watchdog time. If it is populated after one of these
  resets, it says what each core was doing. Same access problem.
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
