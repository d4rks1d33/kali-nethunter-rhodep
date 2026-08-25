# Handoff: the LTE reset on motorola-rhodep

Read this first, then `README.md` in this directory for the full evidence trail.

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

Diagnostic patches, all outside `source=` unless noted:

* `0074` reports every QMI message dropped for want of a handler
* `0075` exposes the modem's GSI channel states and `ipa->mem_offset`
* `0076` exposes the remote's SMEM crash reason on demand
* `0079` is a real fix and is in `source=`: mainline adds `mem_offset` to two
  statistics *sizes* where it belongs only in addresses. Harmless here because
  `mem_offset` reads `0x00000000`, but wrong, and worth sending upstream.

## Where to go next

The AP side is exhausted. Every piece of hardware whose clock, power or bus the
AP votes for has been checked, and IPA answers the AP normally throughout the
window in which the modem is already hung.

1. **Modem-side tracing.** QRTR advertises service 51, CoreSight remote
   tracing, at two instances. Mainline has the CoreSight drivers. If the
   modem's ETM can be routed to a TMC-ETR buffer in DDR and that buffer read
   after the reset, it would say which instruction it stopped on. This is the
   only avenue left that can answer the question directly, and it is a large
   piece of work.
2. **Attach transmit power.** The no-SIM test rules out receiver bring-up but
   not transmit. A way to make the modem attach at low power, or to observe
   rail behaviour during the random access, would close that gap.
3. **Compare against a working LineageOS boot on the same handset.** The modem
   firmware and EFS are identical, so anything the downstream kernel does at
   attach that mainline does not is by definition in the difference. The IPA
   QMI exchange has been compared; the SSR and Trust Zone setup has not, and
   the escalation from modem watchdog to full-chip reset is the part of this
   that is squarely a configuration question.
