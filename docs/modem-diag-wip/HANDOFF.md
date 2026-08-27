# Getting the modem's DIAG working on rhodep

Qualcomm's DIAG is the diagnostic protocol every Qualcomm modem speaks: the one
QXDM and QPST use, and the one that carries the modem's own logs, its message
masks and its command interface. Mainline Linux has no diag driver at all --
the downstream one lives outside the kernel tree -- so on this port nothing has
ever asked the modem for it.

This document is what is known after one session of trying. It exists so the
next attempt starts from measurements instead of from the same three wrong
assumptions.

Related reading, in this order:

  * `docs/watchdog-ipa-lte-wip/HANDOFF.md` -- the main investigation. Its
    traps section is required reading for anyone touching this phone: how to
    detect a reset, why `/tmp` and unsynced files vanish, and why reading an
    address that does not answer hangs the machine.
  * `extra-tools/modem-at/README.md` -- the AT console, which is the working
    example of talking to the modem over a glink channel from userspace.
  * `docs/KERNEL-TECHNICAL.md` -- the port as a whole.

## Three things I was wrong about, so nobody repeats them

**"There is no DIAG channel."** Said after reading the list of channels the
modem advertises and finding none. Wrong: glink channels are advertised by
whichever side opens them, and since mainline has no diag driver, nothing ever
asks, so the modem never announces one. Absence from that list means nothing.

**"AT$QCDMG returns ERROR, so diag is absent."** Wrong for a different reason.
That command switches an AT port into diagnostic mode; it is not implemented in
this firmware and its string is not in the image. That says something about the
AT command, not about diag. `AT$QCDMR?` answers `115200`, so the modem does
have a diagnostic port with a rate.

**"The five second open timeout is why it fails."** Half wrong, and the half
that was right mattered: see patch 0083 below.

## What is established

**DIAG is present in this firmware.** Strings from
`/readonly/firmware/image/modem.b*` include the channel names and the component:

    DIAG   DIAG_2   DIAG_CMD   DIAG_CNTL   DIAG_CNTL_2   glink_diag

`DIAG_DATA`, the name downstream uses on some platforms, is *not* in the image,
so the data channel here is plain `DIAG`.

**The modem recognises the names, and treats them differently from nonsense.**
This is the control that makes everything else meaningful:

    DIAG, DIAG_CNTL, DIAG_DATA   endpoint appears, open never completes,
                                 and the modem later takes a fatal error
    NOTACHANNEL_XYZ              open never completes, and nothing happens
                                 at all -- no assert, modem carries on

The fatal error, delivered to the AP and logged by remoteproc:

    qcom_q6v5_pas 6080000.remoteproc: fatal error received:
      glink_channel_migration.c:602:Assertion status == GLINK_STATUS_SUCCESS failed

So the modem does real work on these names -- it starts a glink channel
migration -- and asserts when that work fails.

**The channels are never advertised, before or after.** Watched for 30 seconds
following an attempt: the list stays at the same twelve.

## Patch 0083: the open timeout, which was crashing the modem

`qcom_glink_create_local()` waits five seconds for the remote to acknowledge a
channel opened from this side. The modem's migration takes longer than that. We
give up, close the channel, and about twenty-one seconds later the modem
asserts against a channel that is gone.

0083 makes that wait a writable module parameter, default unchanged:

    cat /sys/module/qcom_glink/parameters/open_timeout_ms          # 5000
    echo 45000 > /sys/module/qcom_glink/parameters/open_timeout_ms

Note the module is `qcom_glink`, not `qcom_glink_native`: the file is built
into that module.

Measured:

    5000 ms    open fails, modem asserts ~21 s later
    20000 ms   open fails, modem asserts
    45000 ms   open fails after 46 s, and DIAG_CNTL produced no assert at all

So waiting avoids crashing the modem, and does not make the channel open. Both
halves matter. The crash is worth fixing for anyone opening an on-demand glink
channel from mainline, diag or not.

## Patch 0084: sm6375 was missing from the PD mapper entirely

The firmware says what its diag needs, right next to the config filename:

    /nv/item_files/conf/diag_bootup.conf
    Diag_LSM.c:servreg_get_local_domain() returned null

The modem's diag asks the service registry for its protection domain. It was
getting nothing, because `qcom_pd_mapper` had no entry for sm6375 and printed
on every boot:

    PDM: no support for the platform, userspace daemon might be required.

0084 adds it, reusing sm6115's domain set since both SoCs have mpss, adsp and
cdsp. `qcom_pd_mapper` is a module, so this can be tested by swapping the
module and rebooting rather than flashing.

It works, confirmed two ways: the notice is gone, and the AP now publishes the
registry, which it never did before:

    64  1  1  1  16390  Service registry locator service     <- node 1 is the AP

**And diag still does not open its channels.** Verified after a modem restart
with the registry up. So servreg was necessary-looking but not sufficient, and
something else is also missing.

This patch is worth having regardless of diag. PDR is how protection domain
lifetimes are announced between subsystems, and this port had none of it.

## QMI service 21, the modem's embedded file system

`diag_bootup.conf` lives in the modem's own filesystem, which the AP serves
through rmtfs. rmtfs is running and healthy here (service 14, published by node
1) and the modem publishes service 21, "modem embedded file system".

`scripts/modem/rhodep-qmi-probe.py` talks to any QMI service by number. The
method for mapping an undocumented service is to read which error comes back:

    error 57   the message id does not exist
    error 17   the message exists, a required TLV is missing
    error 1    a TLV is present but the wrong size or shape
    error 19   the TLV is the right shape and the value is refused

What that found:

    0x1E   get supported messages: result 0, tlv 0x10 = 0500000000c003
    0x20   exists.  0x01 u16, 0x02 u16, 0x03 u32 all accepted,
           0x04 refused, and with 0x01+0x02+0x03 it still answers 17,
           so at least one required TLV is unfound.
           No string field anywhere: this identifies a file by number.
    0x21   exists.  0x01 takes a string in some form -- 1, 2 and 4-byte
           values are malformed, any string is parsed and then refused
           with 19. Not a length problem: "/nv" and a 34-character path
           are refused identically.
    0x22, 0x23, 0x24, 0x01, 0x02, 0x03   do not exist

## What I would do next, in order

**1. Read the downstream diag driver.** This is the biggest lever and it was
not done. Qualcomm's diag driver is open source, in a separate repository from
the kernel -- CodeLinaro's `platform/vendor/qcom-opensource/dataipa`-style
repos, or Motorola's published kernel sources for this device. It will state
exactly which channels it opens, in which order, with what intents, and what
handshake it performs on DIAG_CNTL. Every question below is answered there
rather than guessed:

  * does the AP open the channels, or wait for the modem to advertise them?
  * what does the modem need before its diag opens anything?
  * what is the control channel handshake, and does it have to happen before
    the data channel is opened?

The vendor kernel clone used by this port does **not** contain it:
`drivers/char/diag` is absent from the tree, checked with `git ls-tree`.

**2. Find out whether the modem is querying servreg and what it receives.**
0084 makes the AP answer, but nothing has confirmed the modem asks, or that it
likes the answer. A QMI trace on service 64 during modem boot would settle it.
If the modem asks for a domain this port does not declare, the domain list in
0084 is the thing to fix.

**3. Only then the EFS.** Getting `diag_bootup.conf` out needs either the last
required TLV of message 0x20 or the accepted path form of 0x21. It is a real
possibility but it is guessing, and step 1 may make it unnecessary.

And keep questioning the premise: that `diag_bootup.conf` is the gate is a good
lead from a firmware string, not a fact.

## Tools, and how to run them

    userspace/debug-tools/rhodep-diag-probe.py
        opens one glink channel and listens without sending. One channel per
        run. Logs to /var/log/rhodep-diag-probe.log with an fsync per line.

    scripts/modem/rhodep-qmi-probe.py
        talks to any QMI service by number.
          --service 21 --msg 0x20 --tlv 0x01:hex=0000 --tlv 0x02:hex=0000
          --service 21 --msg 0x21 --tlv "0x01=/nv/item_files/conf/diag.conf"

    extra-tools/modem-at/rhodep-modem-at
        the AT console. `-l` lists the channels the modem advertises and finds
        the modem's rpmsg control device, whose number changes between boots.

## Practical notes that cost time to learn

**Module unload does not work on this kernel.** `rmmod` returns EBUSY at
refcount zero despite `CONFIG_MODULE_UNLOAD=y`, and `rmmod -f` leaves ioremaps
behind so the next `insmod` fails too. Changing any diagnostic module means
rebooting.

**A failed diag probe used to crash the modem, and a modem crash leaves IPA
un-setup until reboot**, which takes ModemManager's modem with it. With 0083
and a long timeout this is much less frequent, but one probe per boot is still
the honest way to run these.

**The phone drops WiFi if nobody unlocks the screen after a reboot.** ssh
silence in that situation is not a crash; ask for the screen to be unlocked.

**Find the rpmsg control device by matching `6080000.remoteproc` in the sysfs
path.** It has been rpmsg_ctrl0, 2 and 3 across a single afternoon. Guessing it
is how two tools in this repo broke, one of them twice.
