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

## The strongest lead

From the commit message of `0026-arm64-dts-qcom-rhodep-enable-ipa.patch`,
written when the IPA register windows were first worked out:

> Using the old +0x47000 window makes the very first access to the shared SRAM
> reset the SoC instantly, with no CPU exception and no log output at all.

That is this signature exactly: instant reset, no exception, no log. The port
has already hit this failure mode once, from a wrong offset into IPA's shared
SRAM, and fixed it by moving the window to +0x10000 for IPA v4.5 and later.

So the hypothesis worth testing next is that something else in the IPA or GSI
memory description is still wrong for v4.11, and only gets touched once the
modem sets up its own channels -- which is why it needs LTE attach and not
merely a loaded driver. Candidates:

- The `ipa_mem` region table for this hardware version against downstream's
  `ipa_4_11` layout. A region at the wrong offset would be written the first
  time the modem claims it.
- GSI channel and event ring counts, and the per-channel scratch offsets.
- The two SMMU stream IDs (0x4a0 AP, 0x4a2 uC). A DMA from the wrong context
  bank would fault, and on this SoC an SMMU fault against a TZ-protected range
  is a plausible instant reset.

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
