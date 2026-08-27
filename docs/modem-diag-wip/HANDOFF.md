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
with the registry up. So servreg was necessary-looking but not sufficient --
and the next section pins down exactly what else is missing: the AP publishes
the registry, but not the one *service* the modem's diag actually looks up
(`tms/pdr_enabled`). 0084 was the right direction with the wrong domain set.

This patch is worth having regardless of diag. PDR is how protection domain
lifetimes are announced between subsystems, and this port had none of it.

## MEASURED: what the modem actually asks servreg for at boot, and it is not served

This is the strongest new result and it is a direct measurement, not an
inference. `qcom_pdm_get_domain_list()` in `qcom_pd_mapper.c` carries a
`pr_debug` (line ~200) that logs every incoming domain-list query with the
service_name the requester asked for. Enable it live -- the module is loaded,
so no rebuild:

    echo 'file drivers/soc/qcom/qcom_pd_mapper.c +p' \
        > /sys/kernel/debug/dynamic_debug/control

Then restart the modem *with ModemManager stopped* so LTE data does not
re-attach and trigger the ipa-hold-OFF reset (it did not reset -- uptime
climbed monotonically across the whole run):

    systemctl stop ModemManager
    echo 1 > /sys/kernel/debug/remoteproc/remoteproc0/crash   # SSR, works
    # note: writing 'stop' to remoteproc0/state returns 0 but is a no-op here;
    # the crash file is the working restart trigger (rhodep-modem-restart-watch)

What the modem asked for, during its boot, verbatim from dmesg:

    PDM: service 'tms/pdr_enabled'     offset -1 returning 0 domains (of 0)
    PDM: service 'tms/pddump_disabled' offset -1 returning 0 domains (of 0)
    PDM: found msm/adsp/audio_pd / 74   -> service 'avs/audio'        1 domain
    PDM: found msm/modem/wlan_pd / 180  -> service 'kernel/elf_loader' 1 domain

So, established:

  * **The modem does query servreg at boot** -- the open question from step 2
    is answered, measured. It asks by *service* name, not domain name.
  * **It asks for `tms/pdr_enabled` and the AP returns zero domains.** This is
    the service that advertises "PDR is enabled on this PD". In `qcom_pd_mapper`
    it is declared only by the `mpss_root_pd_gps_pdr` domain variant, which
    lists `"gps/gps_service"` **and** `"tms/pdr_enabled"` on `msm/modem/root_pd`
    (instance 180). Only sc7180 and sc7280 use that variant upstream.
  * **0084 reused `sm6115_domains`, which uses the plain `mpss_root_pd_gps`** --
    it has `gps/gps_service` but **not** `tms/pdr_enabled`. So the modem's query
    for `tms/pdr_enabled` finds no domain, exactly as logged.
  * It also asks `tms/pddump_disabled`, also unserved. Unknown whether that one
    matters; noting it because it was in the same burst.

**Inference (flagged as such, not yet proven):** `tms/pdr_enabled` is very
plausibly the gate. It is the flag by which a PD-aware modem subsystem decides
the AP supports PDR and it is safe to bring its user-PD services up -- diag
among them. The firmware string `servreg_get_local_domain() returned null` is
consistent with this: the modem's diag looks up its PD, the PD is not marked
pdr-enabled, the lookup yields null, diag does not start. This is a lead, a
strong one, but "diag starts once `tms/pdr_enabled` is served" is the next
thing to *test*, not yet a fact.

**Next action (queued):** add `tms/pdr_enabled` to sm6375's modem root PD --
i.e. point sm6375 at a domain set that uses `mpss_root_pd_gps_pdr` instead of
`mpss_root_pd_gps`, or add a dedicated sm6375 domain table. `qcom_pd_mapper` is
a module, so this is a rebuild-the-module-and-reboot test, no flash. Then
repeat the trace: the query for `tms/pdr_enabled` should return 1 domain, and
the observable for success is the modem advertising a `DIAG`/`DIAG_CNTL`
channel on its glink edge (see the passive-rpmsg-peer section -- we watch for
the modem to advertise, we do not open).

Raw trace saved on the phone at `/root/servreg-trace-*.log` and
`/home/kali/servreg-trace-*.log` (md5-verified after drop_caches).

## Patch 0085: serving tms/pdr_enabled -- it resolves, and diag still does not start

The queued action was done. 0085 gives sm6375 its own domain table, a copy of
sm6115's set but with `mpss_root_pd_gps_pdr` (which carries `tms/pdr_enabled`)
in place of `mpss_root_pd_gps`. Built as a module, swapped, rebooted.

**The fix does exactly what it was designed to, measured.** With the same
pr_debug enabled and the same modem-restart-with-ModemManager-stopped method
(no SoC reset, uptime monotonic), the modem's boot-time query now resolves:

    before 0085:  PDM: service 'tms/pdr_enabled' offset -1 returning 0 domains (of 0)
    after  0085:  PDM: found msm/modem/root_pd / 180
                  PDM: service 'tms/pdr_enabled' offset -1 returning 1 domains (of 1)

So the causal chain from the measurement holds: the previously-null lookup the
modem makes at boot now returns its PD. This is real progress and 0085 is worth
keeping -- it is the correct domain set for this SoC regardless of diag.

**But the modem still does not advertise a diag channel.** Checked right after,
the mpss edge is the same twelve channels, none of them `DIAG`/`DIAG_CNTL`/
`DIAG_2`:

    DATA1 DATA2 DATA3 DATA4 DATA11 DS IPCRTR LOOPBACK_CTL_MPSS
    SSM_RTR_MODEM_APPS apr_voice_svc glink_ssr rpmsg_ctrl

So **`tms/pdr_enabled` was necessary-looking and is now served, and it is still
not sufficient.** The strong inference from the previous section -- that
serving `tms/pdr_enabled` would make diag start -- is now *disproven as a
complete explanation*. Serving it changed the servreg answer and nothing
observable about diag. The gate, or the next gate, is elsewhere.

What this does buy: the servreg PD path is no longer a suspect. The modem asks,
the AP now answers correctly for `tms/pdr_enabled`, and the modem's diag still
does not come up -- so whatever stops it is past the PD lookup. That removes a
whole branch of the search.

**Still open from the same trace burst:** `tms/pddump_disabled` is still queried
and still returns 0 domains. It was not addressed by 0085 (there is no
`pddump` domain in `qcom_pd_mapper` at all). Unknown if it matters; it is now
the only unserved servreg query left in the modem's boot burst, which makes it
the next cheap thing to look at -- but note it may be a red herring the same
way `tms/pdr_enabled` turned out to be only a partial answer. Do not assume.

Raw trace saved at `/root/pdr-fix-trace-*.log` and `/home/kali/pdr-fix-trace-*.log`.

## What the modem's diag bootup gate actually is, from the image strings

After 0085 cleared the PD lookup and diag still did not start, the next look
was at the modem image itself. `strings` on `/readonly/firmware/image/modem.b21`
around the diag names gives, verbatim:

    diag_mode.c
    tms/pdr_enabled
    tms/pddump_disabled
    /nv/item_files/conf/diag_bootup.conf
    /nv/item_files/services/diag/diag_bootup_flag
    /nv/item_files/services/diag/diag_bootup_flag

So there are **two** bootup items, not one. The previous session only knew
`diag_bootup.conf`. The second, `.../services/diag/diag_bootup_flag`, is the
stronger lead: a `_flag` under `services/diag` is exactly the shape of an
on/off switch, and a production/OEM build setting it to 0 to keep diag off is
the textbook way Qualcomm modems ship diag disabled. That would explain the
whole picture cleanly: PD lookup fixed (0085), and diag still down because its
own bootup flag says do not start.

This is now more than a string, but it is still not proven -- what is unknown
is the flag's **value**. Confirming it needs reading that NV item, which lives
in the modem's EFS.

**Where the EFS physically is.** rmtfs runs as `rmtfs -P -s` and backs the
modem EFS with the raw partitions `modemst1` (sde12) and `modemst2` (sde13),
plus `fsg`/`fsc`. These are Qualcomm EFS2 images, an opaque on-flash format;
rmtfs relays block access, it does not expose files. So `diag_bootup_flag`
cannot be `cat`'d. Reaching it means one of:

  * QMI service 21 (below) -- read the item by its path or id. Still guessing
    TLVs, but now with a concrete target path worth the guessing.
  * parsing EFS2 out of modemst1/2 offline -- possible but heavy, and writing
    back is risky.
  * an NV/EFS write path that flips the flag to 1. If that is doable and safe,
    it is the direct test of the whole premise.

`tms/pddump_disabled` is also in the image (returns 0 from servreg, i.e. not
served). It is probably benign -- "PD dump collection is not disabled" -- and
is not the same kind of gate as a diag bootup flag. Noted, not chased.

## QMI service 21, the modem's embedded file system

`diag_bootup.conf` and `diag_bootup_flag` live in the modem's own filesystem,
which the AP serves through rmtfs. rmtfs is running and healthy here (service
14, published by node 1) and the modem publishes service 21, "modem embedded
file system". The `diag_bootup_flag` path above is the concrete thing to try
to read here.

**Service 21 is QMI_MFS (Modem File System), and the previous error map was
wrong.** The IDL was found: `modem_filesystem_v01` (libqmi's enum agrees,
`QMI_SERVICE_MFS = 0x15 = 21`, "Modem embedded file system service"; libqmi
ships the enum but no message definitions, so qmicli cannot drive it). The IDL
sits at `/tmp/opencode/qmi21/` (re-fetch if gone). It is a **single-shot
path-based get/put**, no open/read/close handles:

    0x20  MFS_PUT  write a whole item file.  Mandatory TLVs 0x01 path,
          0x02 data, 0x03 oflag(u32), 0x04 mode(i32).
    0x21  MFS_GET  read a whole item file.   Mandatory TLV 0x01 path;
          optional 0x10 data_length(u32).

The errors seen are **QMI framework errors, not EFS errors** -- this is the
correction that matters, the old table below was a misread:

    error 17  MISSING_ARGUMENT     a mandatory TLV is absent
    error  1  MALFORMED_MSG        the message structure does not decode
    error 19  ARGUMENT_TOO_LONG    a var-length TLV's inner count is too big
    (a real EFS errno comes back as result 0 with efs_err_num in TLV 0x10)

So the previous session's reading -- "0x20 identifies a file by number", "0x21
parses a string then refuses the value with 19" -- was wrong. 0x20's TLV 0x01
is the same path array as 0x21; error 17 on 0x20 was the missing mandatory
0x04 mode, not "a required TLV is unfound". And 0x21's error 19 was **not** a
refused value: the path is a QMI variable-length array with a **2-byte count
prefix inside the TLV value** (`SZ_IS_16`), and sending a bare string made the
decoder read the first two path bytes as the count.

**What this session measured on 0x21 (GET), and where it is stuck:**

    path as bare string (old)          -> error 19
    path with u16 count prefix         -> error 1   (progress: 19 -> 1)
    path with 1-byte count prefix      -> error 19
    u16 count + optional data_length   -> error 1
    "/nv" alone with u16 count         -> error 1   (same class as long path)

So the u16 count prefix is right (it moved the error off 19), and the leftover
**error 1 is structural, not about the path contents** (a 3-char and a 45-char
path give the same error 1). Adding the optional data_length TLV does not help.
What is still wrong about the message frame is not yet known -- this is the open
end. Tried against both `/nv/item_files/services/diag/diag_bootup_flag` and
`/nv/item_files/conf/diag_bootup.conf`.

**0x1F (GET_SUPPORTED_FIELDS) was used and it disagrees with the stock IDL.**
Asked per message id, it returns `<count:1><tlv ids...>`:

    msg 0x1E  req: (none)      resp: 0x01
    msg 0x20  req: (none, 00)  resp: 0x01     <- PUT reports ZERO req fields
    msg 0x21  req: 0x01        resp: 0x03     <- GET resp data is 0x03, not 0x11

Two things to carry forward: this build's GET puts the file **data in response
TLV 0x03**, where the stock IDL uses 0x11 -- so when a GET finally succeeds,
read 0x03. And PUT advertising zero request fields is odd; it may mean writes
are locked down on this build. Both are measured, not inferred.

**The error-1 wall on GET was chased hard and is now well characterised, but
not broken.** What was ruled out, all measured:

  * **Not the modem being in a bad state.** Rebooted the whole phone for a
    clean modem boot (uptime confirmed the reboot, zero crashes that boot,
    service 21 published) and the GET gives the identical error 1. So it is not
    a degraded modem after the SSR restarts -- it reproduces on a pristine boot.
  * **Not the path or the file.** Known-good item files
    (`/nv/item_files/conf/data_profile.conf`,
    `.../Thin_UI/enable_thin_ui_cfg`) give error 1 too, so it is framing, not
    a missing/locked target.
  * **Not the message header.** 0x21 with no TLV returns error 17
    (MISSING_ARGUMENT), so the service logic runs and reaches the mandatory-TLV
    check -- the QMI header, txn and routing are fine; the same header works for
    0x1E and 0x1F.
  * **Not any path encoding I could construct.** Bare string, u8/u16/u32 count
    prefix, aggregate `u32 path_len + path`, each with and without a trailing
    NUL -- **every** shape of TLV 0x01 returns error 1. The u16-count form is
    provably correct against Qualcomm's own serializer (`qmi_idl_lib.c`,
    `SZ_IS_16` -> 2-byte LE count): msg_len, tlv_len and count all reconcile,
    and it is still rejected.

**RESOLVED: the error-1 wall was a mandatory data_length TLV, and it is now
broken. But MFS read is EPERM-gated by policy.** A later sweep (I had wrongly
concluded above that error 1 was an unreadable OEM path schema -- that was
wrong, kept here as the mistake it was) tried the u16-count path in TLV 0x01
*together with* a second TLV, and found the accepting form:

    0x21 GET:  TLV 0x01 = path (u16-count array)   [mandatory]
               TLV 0x10 = data_length (u32)        [MANDATORY on this build,
                                                     optional in the stock IDL]

    data_length <= 512  -> result 0
    data_length >= 4096 -> error 1 (MALFORMED, exceeds the msg buffer)
    path alone, no 0x10 -> error 1   <- THIS is what the whole wall was

So the OEM difference is small and now known: this build makes the otherwise-
optional `data_length` mandatory. The `0x1F` GET-response-field 0x03 reading
stands, but it was a red herring for the request side. `rhodep-qmi-probe.py`
and its `0x01:path=` form were correct all along; the missing piece was the
second TLV.

**And now the real answer: MFS read is gated with EPERM.** With the accepting
form, every GET returns `result 0` (QMI happy) and then `efs_err_num = 1` in
TLV 0x10 -- EFS errno 1 = EPERM. Measured across many paths:

    diag_bootup_flag, diag_bootup.conf, lte_disable_duration, qmi_cat_mode,
    enable_thin_ui_cfg, qp_ims_enabled, rfnv/00000505 ...   all efs_err_num=1
    a path that DOES NOT EXIST                              also efs_err_num=1

The last line is the proof it is policy, not a filesystem result: a real EFS
would answer ENOENT (2) for a missing file, not EPERM (1) for everything. The
read is refused in the MFS handler before it touches the filesystem. This
firmware simply does not serve EFS item reads over MFS to an AP client -- the
same production lockdown shape as diag itself being off.

**So the QMI route to `diag_bootup_flag` is closed too, but for a concrete,
understood reason (EPERM policy), not a mystery.** The instrument is correct and
fully speaks MFS now; it is the firmware refusing. If a way to satisfy MFS's
permission check surfaces (a privileged QMI client identity, or the read coming
from an on-modem context), the exact request form to use is the one above.

**Two ways forward that do not need the MFS schema:**

  * **Read EFS2 offline from the raw partitions.** TRIED -- and it is a dead
    end, because **the modem EFS is encrypted.** Dumped `modemst1` and
    `modemst2` (3 MB each, sde12/sde13) read-only. Both carry a small plaintext
    `IMGEFS2`/`IMGEFS1` wrapper header (entropy 0.57) and then a body at
    **entropy 7.29 bits/byte** -- that is ciphertext, not a filesystem. The
    ~12000 "strings" a naive scan finds are random 5-char runs, not names. The
    EFS2 `EFSSuper` superblock magic and the EFS-info magic `0xA7B93EA0` are
    **absent** from both dumps, confirming there is no plaintext EFS2 to parse.
    `4z0x/efs2_extractor` and the EFS2 on-flash spec are irrelevant against an
    encrypted image; the key lives in the modem/TrustZone, not on the AP.
  * **The plaintext factory EFS (`fsg`) does not contain the diag items.**
    `fsg` (128 MB, `sde…`) *is* plaintext and holds ~32000 `/nv/item_files/...`
    paths -- but grep for `diag` in it returns **zero**. No `diag_bootup_flag`,
    no `diag_bootup.conf`, nothing under `services/diag/`. So the diag bootup
    items are not in factory defaults; they live only in the encrypted modemst
    EFS, or the modem falls back to a compiled-in default. Either way the value
    is not readable from the AP side offline.
  * **Net on reading the flag:** both offline routes are closed by encryption,
    and the live route (QMI MFS) is now driven correctly but closed by an EPERM
    policy in the modem's MFS handler (see the resolved section above). Every
    AP-side route to the flag's value is closed. Stated plainly so nobody
    re-dumps modemst expecting to grep it, or re-fights the MFS framing.
  * **The TrustZone key idea is a dead end, and it was already established
    why.** The modem EFS key lives in hardware (secure key slots / TZ), never in
    AP-readable memory; and the watchdog-ipa HANDOFF already measured that this
    board's secure memory cannot be reached from the AP -- `CONFIG_STRICT_DEVMEM`
    is set, the production debug fuses are closed (they disable CoreSight and
    SDI), and reading the modem carveout or past the IMEM window *hangs the SoC
    and needs the power button*. Extracting a hardware key would need a TZ/PBL
    exploit or physical glitching, which is out of scope for a software port.

**Where that leaves the premise.** The diag-bootup-flag lead got *stronger* on
evidence this session, not weaker: besides `/nv/item_files/services/diag/
diag_bootup_flag` and `diag_bootup.conf`, the modem image also carries
`BWP Init: ARD EFS params: Enable diag=%d, ...` and
`/nv/item_files/services/diag/DCI_disable` -- a consistent picture of an EFS
switch that gates whether the modem's diag comes up. It is no longer "a lead
from one string". But it is still **not proven**, because the one thing that
would prove it -- the flag's value -- is behind EFS encryption offline and an
EPERM policy over QMI MFS, both closed here. Do not present "diag_bootup_flag is
the gate" as fact; present it as the strongest surviving hypothesis with the
servreg/PD branch eliminated beneath it.

A note on the shape of the whole answer: three independent AP-side lockdowns
now point the same way -- diag's channels are never advertised, MFS refuses
every EFS read with EPERM, and the EFS partition is encrypted. This is a
production handset with diagnostics deliberately fused/flagged off in the modem
firmware. Nothing on the AP side (which is all this port controls) has been
found that flips that, and the servreg/PD lever that looked most promising was
served correctly by 0085 and did not. The realistic reading is that turning
diag on requires changing something *inside the modem* -- the bootup flag or an
equivalent -- which needs either a privileged/on-modem path this port does not
have, or a different (engineering/unlocked) modem image. That is worth stating
plainly so the next session weighs it against more AP-side probing.

Old error map, kept because the numbers are still a useful cross-check but the
*labels* were wrong (see corrected table above):

    0x1E   get supported messages: result 0, tlv 0x10 = 0500000000c003
    0x22, 0x23, 0x24   do not exist (only 0x1E,0x1F,0x20,0x21 are implemented)

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

## Three gaps closed by measurement, and none of them opens diag

Picking this up after the session above, three things it left as inference or
as a queued action have now been measured. All three are negative, and each
removes a way the picture could still have been wrong.

**MFS PUT is EPERM too, not just GET.** The section above characterises GET
thoroughly and then says of PUT only that reporting zero request fields "may
mean writes are locked down". That was never tested. It is now, with the
framing the same section established:

    0x20 PUT  0x01 path (u16-count)  /nv/item_files/services/diag/zz_probe
              0x02 data (u16-count)
              0x03 oflag (u32)  0x0301
              0x04 mode  (i32)  0644
    -> result 0, efs_err_num = 1

Same EPERM as every read. So the lockdown is on the MFS handler as a whole, not
on reads, and writing the flag was never going to be the way in. Worth knowing
before someone plans around "maybe we can write it even if we cannot read it" --
that was my first idea on reading this file.

**DIAG is never advertised, not even for an instant.** The reframed observable
from step 2 was checked only as a snapshot after the modem settled. A channel
advertised during bring-up and immediately withdrawn would not survive that.
`userspace/debug-tools/rhodep-glink-advert-watch.py` polls the rpmsg bus
directly at 50 ms, opens nothing, creates no endpoint, and so cannot assert the
modem. Across a full crash-and-recover, with ModemManager stopped:

    2123.35  withdrawn: all twelve
    2127.50  APPEARED: IPCRTR SSM_RTR_MODEM_APPS glink_ssr rpmsg_ctrl
    2127.60  APPEARED: LOOPBACK_CTL_MPSS apr_voice_svc
    2128.65  APPEARED: DATA1 DATA11 DATA2 DATA3 DATA4 DS
    ...100 s of sampling...
    DIAG ever advertised: NO

Three distinct waves of bring-up, sampled at 50 ms for a hundred seconds, and
no DIAG name appears in any of them. The modem's diag does not come up, briefly
or otherwise. That is now measured at resolution rather than assumed from a
snapshot, and it is the primary test the AP-passive model asks for.

**The unidentified OEM QMI services are not a way in either.** Nine services on
the modem are unnamed by qrtr-lookup -- 5004, 1067, 228, 227, 74, 73, 78, 68,
4099 -- and any of them could in principle have carried a diag control. None
implements the standard capability message: 1067, 228, 74, 68, 73 and 78 answer
error 57 to 0x1E, and 5004, 227 and 4099 do not answer at all. Mapping them
would mean brute-forcing message ids service by service, which is a large and
unpromising search given everything else here.

**Where that leaves it.** I set out to find a hole in the reasoning above and
did not find one; I closed three of my own candidate holes instead. Four
independent AP-side facts now agree: the modem never advertises a diag channel
at any point in its boot, MFS refuses both reads and writes with EPERM
including for paths that do not exist, the EFS partitions are encrypted, and
the servreg PD lever was served correctly and changed nothing.

Stated as carefully as the evidence allows: nothing reachable from the
application processor has been found that turns this modem's diag on, and the
remaining candidates are all inside the modem -- its bootup flag, or a
privileged context this port does not have. That is not the same as proving it
impossible, and the honest gap is still the same one: no one has read the
flag's value. But every route to it from this side is now closed with a
measured reason rather than a suspicion.

# BREAKTHROUGH: the modem's diag is alive. It was waiting for us to publish.

Everything above concluded that diag is off inside the modem and unreachable
from the AP. That was wrong, and the reason it was wrong is instructive: every
attempt looked at the **glink** transport, where diag is a set of named
channels. `diagfwd_socket.c` is a second transport, and there the direction is
reversed.

    the AP is the SERVER for CNTL, DATA and DCI; the modem connects to it
    the AP is a client of the modem's CMD and DCI_CMD services

    DIAG_SVC_ID      0x1001 (4097)
    MODEM_INST_BASE  0
    INST_ID          CNTL 0   CMD 1   DATA 2   DCI_CMD 3   DCI 4

So a stock AP advertises QRTR service 4097, instances 0, 2 and 4, before the
modem has anywhere to connect. This port never did, and `qrtr-lookup` showed no
4097 from anyone -- measured, before the experiment.

`userspace/debug-tools/rhodep-diag-server.py` publishes those three services
and waits. It creates no glink endpoint and opens no channel, so it cannot
assert the modem the way rhodep-diag-probe.py does.

**The modem answered immediately.** Within the same second:

    4097  instance 1  node 0  port 138   DIAG service (MODEM:CMD)   <- the modem
    4097  instance 0  node 1  16412      DIAG service (MODEM:CNTL)  <- us
    4097  instance 2  node 1  16413      DIAG service (MODEM:DATA)
    4097  instance 4  node 1  16414      DIAG service (MODEM:DCI)

and it sent diag control packets to our CNTL socket:

    RX on CNTL from (0, 137): 08000000 07000000 03000000 f7fe1b
    RX on CNTL from (0, 137): 0c000000 01000000 02

Control type 8 is `DIAG_CTRL_MSG_FEATURE`: that is the modem's feature mask,
which the downstream driver describes as step 2 of the handshake. Type 12 is a
log mask message. This is the diag control protocol running.

**The modem's diag service persists** after our process exits: once brought up
it stays up. Before the experiment there was no 4097 at all.

So every conclusion above about diag being fused off is withdrawn. Diag was
never disabled -- the modem was waiting for an AP that never advertised itself,
because mainline has no diag driver and nothing on this port had ever published
the service. The EFS lockdown, the encrypted modemst, the missing
`diag_bootup_flag` -- all real, all measured, and none of them were the reason.

## What that does not yet do, and the next step

Sending a raw DIAG request to the modem's CMD service (node 0, port 138) times
out: 0x00 version, 0x7C extended build id, 0x0C verno all get nothing. That is
expected from the driver's own description: the AP has to complete the control
handshake first. `diag_send_updates_peripheral()` only lets the AP reply after
`process_incoming_feature_mask()` has run, and then
`diag_send_feature_mask_update()` sends the AP's own feature mask, followed by
the log, event and F3 masks.

So the next step is concrete and small: reply on the CNTL socket with the AP's
feature mask in the same DIAG_CTRL framing the modem just used, then retry the
command. The framing is visible in the two packets above -- u32 type, u32
length, payload -- and `diagfwd_cntl.c` has the exact structures.

After that, a DIAG console is the same shape as the AT one: send a request to
the modem's CMD port, read the response, with the DATA socket carrying the log
stream.

## The control handshake completes, and what is left

`rhodep-diag-server.py` now answers the modem. On receiving control packet
type 8 it replies with the AP's feature mask, built exactly as
`diag_send_feature_mask_update()` builds it:

    bits 0 FEATURE_MASK_SUPPORT   2 LOG_ON_DEMAND_APPS   4 REQ_RSP_SUPPORT
         9 STM                   11 MASK_CENTRALIZATION 13 SOCKETS_ENABLED
        14 DCI_EXTENDED_HEADER   15 DIAGID_SUPPORT      20 MULTI_SIM_SUPPORT

    -> 08000000 08000000 04000000 15ea1000

APPS_HDLC_ENCODE (6) is deliberately left clear: over sockets the packets are
unframed, and downstream only sets it for the USB path. FEATURE_MASK_LEN is 4,
not 2 -- the `{0, 0}` initialiser in the driver is partial and misleads.

With that, a full session from a modem restart looks like:

    modem feature mask   08000000 07000000 03000000 f7fe1b
    our reply            08000000 08000000 04000000 15ea1000
    modem log mask       0c000000 01000000 02
    modem diag id 0x21   ... "msm/modem/root_pd"
    modem diag id 0x21   ... "msm/modem/wlan_pd"

238 packets in one run. Control type 0x21 is DIAG_CTRL_MSG_DIAGID and the modem
is registering its protection domains by name, which is a later stage of the
handshake than the feature mask -- so the modem is progressing through the
protocol, not just answering once.

**Sending a command still gets nothing back.** The modem's CMD service is found
(node 0, instance 1; its port moves across restarts, so look it up) and a raw
`0x00` version request is accepted by the socket, but no response arrives on
any of the three channels.

That is expected at this stage rather than a wall. `diag_send_updates_peripheral()`
sends more than the feature mask: after it come the msg (F3), log and event mask
updates, the real-time mode and the buffering mode. A peripheral with no masks
set has been told to report nothing, so silence is the correct behaviour. The
next work is to send those, in `diag_masks.c` order, and then retry.

Two practical notes for whoever continues:

  * **The modem's diag stays up once it has come up**, and it stays bound to
    whichever sockets it first connected to. Starting a second server without
    restarting the modem gets no handshake and no traffic. Use
    `--restart-modem` for a clean session.
  * **The CMD service port changes** across modem restarts (138, then 26). Look
    it up by service 0x1001 instance 1 on node 0 rather than hardcoding.
