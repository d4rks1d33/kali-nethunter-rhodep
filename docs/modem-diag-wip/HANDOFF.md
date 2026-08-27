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

## The downstream diag driver, read at last -- and it changes the model

Step 1 below was finally done. The driver was located, read, and it overturns
the approach every probe on this port has taken so far. Sourcing it took some
digging, recorded here so nobody re-treads it:

  * `drivers/char/diag` does **not** exist in CLO's `kernel/msm-5.4`
    (`LA.UM.9.16.c25..c29`, this SoC's train). Verified via the GitLab tree
    API, 404 on every branch. Diag is not in the msm-5.4 kernel.
  * `clo/la/platform/vendor/qcom-opensource/diag` exists but carries only
    ancient `caf_migration/*` (eclair..ics) branches. A stale mirror, useless.
  * Motorola's `kernel-msm` GKI branches ship a diag **stub** techpack and set
    no `CONFIG_DIAG*`; the real module is pulled from a vendor manifest that is
    not public. Dead end for the exact holi build.
  * What answers the questions is the **rpmsg-transport** diag driver, present
    in CLO `kernel/msm-4.19` (checked `LA.UM.9.15.c29`). Its channel table
    matches this modem's firmware strings *exactly*, which the older glink
    driver (msm-4.9) does not. That match is the evidence it is the right one:

        msm-4.19 rpmsg driver, PERIPHERAL_MODEM (edge "mpss")
          data  "DIAG"        <- matches firmware string DIAG
          cntl  "DIAG_CNTL"   <- matches DIAG_CNTL
          dci   "DIAG_2"      <- matches DIAG_2
          cmd   "DIAG_CMD"    <- matches DIAG_CMD
          dcicmd"DIAG_2_CMD"  <- (DIAG_CNTL_2 in the image is DIAG_2's cntl)

    The msm-4.9 glink driver instead names them `DIAG_DATA`/`DIAG_CTRL`, which
    are **not** in this image. So this firmware speaks the rpmsg-era protocol.
    Clone kept at `/tmp/opencode/diag-rpmsg/drivers/char/diag/` (msm-4.19,
    `LA.UM.9.15.c29`); `/tmp` does not survive a reset, re-clone if gone.

**The AP is a passive rpmsg peer. It does not open diag channels.** This is the
finding. `diagfwd_rpmsg.c` registers a plain `rpmsg_driver` with an id_table of
the channel names and a `.probe`. `diag_rpmsg_probe()` runs only when the
**modem advertises** a channel; the AP never calls anything like
`glink_open`/`CREATE_EPT` for a diag channel. `diag_rpmsg_init()` registers the
control side and then *waits*. Nothing is opened from this side, ever.

That is the opposite of what this port has been doing. `rhodep-diag-probe.py`
and the AT console's DIAG note both **create an endpoint from the AP**, which
forces a local-initiated glink channel open, which is what trips
`glink_channel_migration.c:602` and asserts the modem. Downstream would never
issue that open. The right observable is not "can the AP open DIAG" but "does
the modem ever advertise DIAG on its rpmsg edge" -- and the established fact
that the twelve advertised channels never include a DIAG one now reads as *the
modem's diag has not been brought up*, not as a channel we have failed to open.

**Two concrete errors in `rhodep-diag-probe.py`, now provable, not guessed:**

  * It uses `CREATE_EPT` to open the channel actively. The AP-side model is
    wrong; the endpoint should be created by the rpmsg bus when the modem
    probes it. Actively opening is exactly the assert trigger.
  * Its `KNOWN` list leads with `DIAG_DATA`. For the modem (mpss) the data
    channel is `DIAG`; `DIAG_DATA` is the sensors/cdsp/wdsp name and is not a
    modem channel at all. The comment "downstream opens the control channel
    before the data one" is also wrong: downstream opens neither.

**The DIAG_CNTL handshake, for when a cntl channel does appear.** It is
modem-first, and the sequence is:

  1. modem advertises `DIAG_CNTL`; AP's `.probe` binds it, marks it open.
  2. modem sends its own feature mask as a `DIAG_CTRL_MSG_FEATURE` (0x0000..)
     control packet. `process_incoming_feature_mask()` sets `rcvd_feature_mask`.
  3. only *after* receiving that, `diag_send_updates_peripheral()` lets the AP
     reply: `diag_send_feature_mask_update()` writes the AP's feature mask
     (SUPPORT, LOG_ON_DEMAND, STM, DIAGID, REQ_RSP, HDLC_ENCODE, ...), then
     the log/event/F3 masks follow.

So the control channel is entirely reactive on the AP: it answers the modem, it
never initiates. There is nothing for us to *send* first to make diag start;
the modem has to bring up `DIAG_CNTL` on its own, and it only does that once
its diag is enabled internally. Which puts the whole problem back on the modem
side -- the gate is in the modem, and `diag_bootup.conf` / the servreg PD
lookup remain the only leads for why the modem's diag never starts.

## What I would do next, in order

**1. Read the downstream diag driver.** DONE -- see the section above. The
answers: the AP does **not** open channels (it is a passive rpmsg peer); the
control handshake is modem-first and the AP only replies; and there is no
data-before-control ordering to get right on our side because we open nothing.
The remaining unknown moves entirely to the modem: what makes *it* advertise.

The vendor kernel clone used by this port does **not** contain it:
`drivers/char/diag` is absent from the tree, checked with `git ls-tree`. It is
a techpack module, and for this SoC's train it is not published; the msm-4.19
rpmsg driver above is the faithful stand-in and its channel names prove it.

**2. Watch for the modem ADVERTISING a diag channel, not for the AP opening
one.** This is the reframed observable and it is now the primary test. With the
AP-passive model, success looks like a `DIAG_CNTL`/`DIAG` name appearing in the
modem's advertised channel list -- the same list `rhodep-modem-at -l` prints
and that `rhodep-diag-probe.py` was fighting against. Poll that list across a
modem boot and across the servreg/EFS experiments; the twelve-channel list not
growing is the negative result, its growth by a DIAG name is the win. This
needs *no* endpoint creation and so cannot assert the modem -- it is the safe
experiment the port never ran.

**3. Find out whether the modem is querying servreg and what it receives.**
0084 makes the AP answer, but nothing has confirmed the modem asks, or that it
likes the answer. A QMI trace on service 64 during modem boot would settle it.
If the modem asks for a domain this port does not declare, the domain list in
0084 is the thing to fix. Still the leading lead for why the modem's diag never
starts, now that step 1 has shown the gate is on the modem side.

**4. Only then the EFS.** Getting `diag_bootup.conf` out needs either the last
required TLV of message 0x20 or the accepted path form of 0x21. It is a real
possibility but it is guessing.

And keep questioning the premise: that `diag_bootup.conf` is the gate is a good
lead from a firmware string, not a fact. Step 1 did not confirm it -- the
downstream driver never reads that file; it is the modem's own diag that does,
before it would advertise anything.

## Tools, and how to run them

    userspace/debug-tools/rhodep-diag-probe.py
        opens one glink channel and listens without sending. One channel per
        run. Logs to /var/log/rhodep-diag-probe.log with an fsync per line.
        NOTE: its whole model (CREATE_EPT from the AP) is now known to be wrong
        -- see "The downstream diag driver" above. Actively opening is what
        asserts the modem; downstream is a passive rpmsg peer. Do not run this
        expecting a channel to open. Kept only for the record.

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
