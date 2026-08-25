# Reading the modem's memory: what works, what does not, and one hazard

Written after the session that closed CoreSight tracing. Its subject is the
question that was left: **the modem cannot be observed while it runs, so can it
be observed after it dies?**

Short answer so far: the mechanism for reading its DDR across the reset works
and is reusable, the one dump taken came back entirely zero, and the check that
would say whether that zero is real **froze the phone**. Read the hazard section
before repeating any of this.

## Why the modem's DDR is worth reading

Three facts line up.

* DDR survives the silent reset. The firmware regions were compared byte for
  byte across one and came back identical (see `README.md`, "TrustZone leaves
  nothing behind").
* The modem's carveout, `pil-mpss-wlan@8b800000`, is 256 MB of `no-map`
  reserved memory holding its heap, its stacks and its internal F3 log ring.
* The only thing that overwrites it is the application processor loading
  `modem.mdt` into it, at about twelve seconds into the next boot.

So if the load is prevented on the boot after a reset, the carveout should
still hold whatever the modem had in it at the instant the SoC went down. That
is the only remaining route to modem-side visibility on this handset, because
CoreSight is fused off (`HANDOFF.md`) and the firmware advertises no DIAG glink
channel.

## The mechanism, which works

`userspace/debug-tools/rhodep-mpss-dump` plus its unit. It runs before
`qcom_q6v5_pas` is allowed to load, copies 0x8b800000+0x10000000 to
`/root/mpss-dumps/mpss-<timestamp>-<bootreason>.bin`, and only then loads the
driver. Arm it with

```sh
install -m 0755 rhodep-mpss-dump /usr/local/sbin/
install -m 0644 rhodep-mpss-dump.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable rhodep-mpss-dump.service
echo blacklist qcom_q6v5_pas > /etc/modprobe.d/rhodep-nomodem.conf
sync
```

then cause a reset. It is one-shot: the script deletes the blacklist and
disables its own unit in a `finally`, so the boot after is normal again. That
worked exactly as intended -- the file appeared, named `-watchdog`, and the
phone came back with the modem loaded.

**It costs WiFi for that one boot, and this is not optional.** `ath10k_snoc`
probes early and the WLAN protection domain lives inside the modem, so with the
modem held back ath10k fails and does not retry. Loading `qcom_q6v5_pas`
afterwards brings the modem up but not WiFi. Expect one boot with no network
and plan the way back in: the dump unit finishes on its own, so a plain reboot
is enough, and `ssh kali@172.16.42.1` over the USB cable is the link that does
not depend on the modem.

`TimeoutStartSec=600` matters as well. A 256 MB read plus a 256 MB write takes
a while, and if systemd kills the unit the `finally` does not run, which leaves
the blacklist in place and the phone without a network on every subsequent
boot. If that happens, remove `/etc/modprobe.d/rhodep-nomodem.conf` over USB.

## The result, which is not yet interpretable

```
-rw-r--r-- 1 root root 268435456  mpss-20260825-005812-watchdog.bin
MiB con datos: 0 de 256
```

Every one of the 256 MB is zero.

**Do not read that as "the modem's memory is wiped by the reset" yet**, because
the instrument was never validated. Two explanations fit equally well and they
have opposite consequences:

1. Something -- XBL, or TrustZone as part of tearing the subsystem down --
   really does clear the carveout, and there is nothing to recover.
2. `/dev/mem` reads of that region return zeros regardless of content, because
   `qcom_scm_pas_mem_setup()` hands it to the MSS virtual machine and the XPU
   refuses HLOS access.

Explanation 2 is quite likely, and it would make the dump meaningless rather
than informative. Note that during the dump boot the modem had not been loaded,
so the assignment would have had to survive the reset for it to apply --
which is itself unknown.

## The hazard: reading the live carveout froze the phone

The validation was going to be simple. Read one page of the carveout **now**,
with the modem running and its firmware demonstrably loaded into that memory.
Non-zero would mean the region is readable and the dump's zeros are real; zero
would mean the read path lies and the dump proves nothing.

It did neither. The read of `0x8b800000` through `/dev/mem` never returned, and
the phone stopped answering: TCP connections to sshd opened and then timed out
during the banner exchange, which is a wedged kernel rather than a reset. It
had to be recovered with a long press on the power button.

So, stated carefully, because it is one observation:

**Reading the modem's live carveout through `/dev/mem` hung the application
processor.** It did not reset the SoC -- the machine wedged and stayed wedged,
which is a different failure from the one being investigated, and worth keeping
distinct from it.

That is consistent with the region being assigned to the MSS and an HLOS access
to it stalling rather than aborting, and it is also the reason to be careful:
the same page range is what the dump script reads, and the dump script survived
only because at that point in the boot the modem had never been loaded.

**Do not read 0x8b800000..0x9b800000 through /dev/mem while the modem is
running.** The other addresses in that probe -- 0x8ca50000, 0x97000000,
0xaefa2000, 0x80900000 -- were never reached, so nothing is known about them.

## Where to pick this up

1. **Settle whether the dump is meaningful**, without touching the live
   carveout. The safe version of the validation is to dump on a boot after a
   *clean* reboot rather than after a reset, with the modem held back exactly
   the same way. On a clean reboot the previous boot had loaded the firmware
   into that memory, so:
   * if that dump contains the modem image, the read path works, and the zeros
     after a reset are a real finding -- the carveout is cleared on the way
     down, and this whole route is closed;
   * if that dump is also zero, the read path returns zeros for this region and
     the route was never open in the first place.

   Either way it costs one boot and no risk, and it is the first thing to do.

2. **If the read path is the problem**, the region can still be reached from a
   context that is allowed to: an SCM call to reassign it to HLOS
   (`qcom_scm_assign_mem`) before reading, which is what downstream's
   `pil_assign_mem_to_linux()` does on every subsystem restart. That is a small
   module rather than a driver, and it is exactly the operation the vendor
   performs before collecting a modem ramdump.

3. **If the carveout really is cleared**, the remaining route to modem state is
   a full memory dump over EDL/Sahara with download mode enabled, which needs
   USB access to the phone from a host and carries its own recovery risk.
