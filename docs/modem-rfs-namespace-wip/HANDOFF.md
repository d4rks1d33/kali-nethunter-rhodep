# The modem's remote file system namespace, completed

Investigation of the lead "the stock vendor partition has a dedicated remote
filesystem root for the positioning subsystem, `/vendor/rfs/apq/gnss/`, and this
port has no equivalent". Session of 2026-09-06.

**Short answer: the lead is real as a gap and dead as a cause.** Stock does
expose four things this port did not, and they are now exposed. None of them is
where the positioning engine's files live — those are in the modem's own EFS,
behind `rmtfs`, and `/mnt/vendor/persist/rfs/apq/gnss/` is *empty* on this
phone's factory partition.

The engineering is in `userspace/modem/`; `userspace/modem/README.md` sections
"The whole RFS namespace" and "The twelve QMI service instances" are the
reference. This file is the evidence and the handoff.

## 1. What the modem actually asks for

`tqftpserv-rhodep` already runs with `-d`, so a whole boot is in the journal.
Identical across four boots, before and after the change:

	  7  R  /readonly/firmware/image/modem_pr/so/205_0_0.mbn
	  3  R  /readwrite/mot_rfs/imei_sv
	  3  R  /readonly/vendor/firmware_mnt/image/wlanmdsp.mbn
	  2  R  /readwrite/shob.bin
	  2  R  /readwrite/dhob.bin
	  2  R  /readwrite/mcfg.tmp          2  W  /readwrite/mcfg.tmp
	  2  R  /readwrite/datablock/id_00
	  2  R  /readwrite/datablock/slid_00
	  2  R  /readwrite/datablock/id_01        <- 2 rejected, ENOENT
	  2  R  /readwrite/ota_firewall/ruleset   <- 2 rejected, ENOENT
	  2  R  /readonly/vendor/fsg/mcfg_sw/mbn_sw.dig
	  1  R  /readonly/vendor/fsg/mcfg_hw/mbn_hw.dig  <- 1 rejected, ENOENT
	  1  R  /readwrite/ticf.bin          1  W  /readwrite/ticf.bin
	                                     1  W  /readwrite/server_check.txt

	38 request/rejection lines, 3 distinct rejections, 0 "invalid path".

All three rejections are ENOENT on stock too: `fsg` has no `mcfg_hw/`, the
factory `persist` has no `ota_firewall/`, and `datablock/id_01` is the second
SIM slot with no card in it.

**Nothing matching `gps`, `gnss`, `nav`, `xtra`, `offsets`, `Corr`, `X3File` or
any `.bin` other than `shob`/`dhob`/`ticf` is requested. Not once, on any boot.**

`rmtfs` has no per-request logging — there is no verbose flag, `rmtfs.c` only
prints on error (`[storage] failed to open`, `[RMTFS storage] request for
unknown partition '%s', rejecting`). Its journal for a whole boot is one line,
`Started rmtfs.service`, i.e. **zero failures**. It serves four names:
`modemst1`, `modemst2`, `modem_fsc` and `modem_fsg`, as raw block storage with
no file names on the wire. Which files the modem reads out of its EFS cannot be
observed from the AP without instrumenting rmtfs.

## 2. Where the positioning engine's files really are

`strings` over the stock `modem` partition's `modem.b*`, around offset
40877672, puts `gpsoffsets.bin` in this company:

	CGPSGnssGGBiasEsti  CGPSGnssTGPSEsti  CGPSSbasSearchList
	gpsoffsets.bin  CGPSFreqBias  EnergyTimers  /GNSS/ME/%s

and `GPSX1CorrFile`/`GPSX3File`/`NAVICX3File` sit in a contiguous table with
`EphFile01..31`, `IonoFile`, `UtcFile`, `SbasCannedFile`, `GeoAlmFile120..141`,
`GeoEphFile120..141`. The path prefixes that go with them are all EFS:

	/GNSS  /GNSS/  /GNSS/ME  /GNSS/ME/%s  /GNSS/PE  /GNSS/PE/%s  /GNSS/CFG/
	/GNSS/CFG/LBS/  /GNSS/CFG/NF/  /GNSS/CFG/SDP/  /GNSS/SAML/gyrocal
	/GNSS/TLE/nv_items.bin  /GNSS/WWANME/GTS/gtspmic.bin  /GNSS_SM/RLM_Conf
	/nv/item_files/gps/%s/%s

The TFTP prefixes in the same firmware are a disjoint, exhaustive set —
`/hlos/`, `/ramdumps/`, `/readonly/...`, `/readwrite/...`, `/shared/` — and no
GNSS name appears under any of them. This matches the source-file names the
lead quoted: `gps_nv_efs.c`, `gps_fs_task: EFS_PUT: %s %lu bytes written`. EFS,
not TFTP.

Cross-check on the filesystem: `gpsoffsets.bin`, `GPSX*`, `NAVIC*` and anything
`xtra` exist as files on **none** of the stock `persist`, `vendor`, `modem` or
`fsg` partitions. The only `xtra` hit in the whole vendor image is
`/vendor/bin/xtra-daemon` and its seccomp policy, which is the AP-side
assistance-data downloader, not an RFS file.

`modemst1` begins `IMGEFS1` and holds no plaintext names, so the EFS contents
cannot be confirmed by a string scan — but that is what an EFS container looks
like, and rmtfs serves it healthily.

## 3. What stock exposes that this port did not

Read off the stock partitions on 2026-09-06, both mounted read-only through
read-only loop devices and unmounted cleanly afterwards.

`persist` (sde2), `/persist/rfs/`:

	rfs/apq/gnss/          EMPTY   0750 2903:2903  mtime 2022-03-28
	rfs/shared/            server_info.txt, 15 bytes, "TFTP_SERVER.1.0", no NL
	hlos_rfs/shared/       EMPTY
	rfs/mdm/{adsp,cdsp,mpss,slpi,tn,wpss}/   all EMPTY (no external modem here)
	rfs/msm/{adsp,cdsp,slpi,wpss}/           all EMPTY
	rfs/msm/mpss/          cal_rfs/rf000296{18,20,50}.bin, rf000300{06,08,10,16}.bin
	                       datablock/{id_00,slid_00}, datablock_report.txt
	                       dhob.bin 16384, shob.bin 19020, ticf.bin 324
	                       dhob_report.txt, hob_report.txt, simlock_report.txt
	                       mot_rfs/imei_sv, server_check.txt ("hello")

`vendor` (inside `super`, LP offset 3476029440 size 632545280), `/vendor/rfs/`:
twelve roots, `apq/gnss` plus `msm/{adsp,cdsp,mpss,ois,slpi,wpss}` and
`mdm/{adsp,cdsp,mpss,ois,slpi,tn,wpss}`, every one of them the same five
symlinks. `apq/gnss` differs from `msm/mpss` in exactly two ways: `readwrite`
points at `persist/rfs/apq/gnss` instead of `persist/rfs/msm/mpss`, and it has
no `readonly/vendor/fsg` symlink.

`modem` (modem_a): `image/strait/qdsp6m.qdb`, 6042676 bytes, `QDB` magic. Named
literally in the firmware as `/readonly/firmware/image/strait/qdsp6m.qdb`, and
this port was not copying it out. (`image/testpd.mbn`, also named in the
firmware, does not exist on this device at all — stock ENOENTs it too.)

### The instance table

`/vendor/bin/tftp_server` is one process with no arguments. It registers QMI
service 4096 twelve times and picks the root from the instance. Table at vaddr
`0x1a708` in its `.data`, twelve `{u32 instance, char *root}` pairs; the
registration loop at `0x17860` walks `1..12` and puts the instance straight into
the QRTR `NEW_SERVER` packet:

	 1 msm/mpss   2 msm/adsp   3 mdm/mpss   4 mdm/adsp   5 mdm/tn   6 apq/gnss
	 7 msm/slpi   8 mdm/slpi   9 msm/cdsp  10 mdm/cdsp  11 msm/wpss 12 mdm/wpss

libqrtr encodes `qrtr_publish(fd, service, version, instance)` as
`instance << 8 | version`, so upstream tqftpserv's `qrtr_publish(fd, 4096, 1, 0)`
puts a **1** on the wire — stock's `msm/mpss`. That is why the modem finds it,
and it pins the numbering with a working example.

`qrtr-ns`'s `server_match()` (`ns.c:118`) promotes a lookup with a non-zero
instance to an exact match, so any client looking up 2..12 found **no server at
all**. tqftpserv never saw it and never logged it. Worth remembering: a silent
`tqftpserv -d` log is not proof that nothing was asked for, only that nothing
was asked of instance 1.

## 4. What changed

`userspace/modem/tqftpserv/`

- `0002-translate-serve-shared-hlos-ramdumps.patch` — `/shared/`, `/hlos/` and
  `/ramdumps/`, the three roots of a stock RFS namespace upstream rejects with
  "invalid path". All three are named in this modem's firmware. They resolve to
  `/var/lib/tqftpserv/rfs_{shared,hlos}` (global, as on stock, where all twelve
  roots symlink to the same two directories) and `<root>/rfs_ramdumps`
  (per instance, as on stock). `translate_readwrite()` becomes
  `translate_persistent(dir, ...)`, and the read/write root becomes settable.
- `0003-tqftpserv-publish-rfs-qmi-instances.patch` — `-i <list>`, publishing
  service 4096 on extra RFS instances, one socket each (required: `qrtr-ns`
  keys its server map on the port, so republishing on one socket replaces
  rather than adds). Instance 1 is unconditional and keeps
  `/var/lib/tqftpserv`, so the working modem path is unchanged. A request on
  any other instance gets its own `log_debug` line.

`userspace/modem/rhodep-rfs-populate` — copies `image/strait/` from `modem_a`,
`rfs/shared/`, `hlos_rfs/shared/` and `rfs/apq/gnss/` from `persist`; creates
`rfs_shared`, `rfs_hlos/qdma`, `rfs_ramdumps`, `rfs_apq_gnss/rfs_ramdumps`
(tqftpserv's `openat` does not create intermediate directories); falls back to
writing `server_info.txt` itself if persist has none. The persist block's guard
is now per target — it used to be gated on `shob.bin`, which has existed since
this port's first boot, so anything added inside it would never have run.

`userspace/modem/install.sh` — the drop-in becomes
`tqftpserv-rhodep -d -i 6`.

## 5. Verification, 2026-09-05 22:19 on the phone

	qrtr-lookup
	  4096   1   0   1  16410  TFTP        <- msm/mpss
	  4096   6   0   1  16411  TFTP        <- apq/gnss, new
	    16   2   0   0    108  Location service (~ PDS v2)
	    14   1   0   1     14  Remote file system service
	    52   1   0   1  16392  Dynamic Heap Memory Sharing

	journalctl -u tqftpserv -b
	  publishing service 4096 instance 1 for /vendor/rfs/msm/mpss (/var/lib/tqftpserv)
	  publishing service 4096 instance 6 for /vendor/rfs/apq/gnss (/var/lib/tqftpserv/rfs_apq_gnss)

	rejections: unchanged, the same three benign ENOENTs, no "invalid path"
	wlan0 connected, ModemManager active, rmtfs clean, modem boots

Path mapping proved directly with a harness linking `translate.c`
(`/shared/server_info.txt` -> `TFTP_SERVER.1.0`; `/hlos/qdma/x` and
`/ramdumps/x` writable; `strait/qdsp6m.qdb` -> `QDB` magic;
`/readonly/vendor/fsg/mcfg_sw/mbn_sw.dig` still resolves; unknown paths still
rejected; and instance 6's `/readwrite/shob.bin` correctly *not* visible, since
its root is a different directory, exactly as on stock).

The installed `/usr/local/bin/tqftpserv-rhodep` is byte-identical
(sha256 `b2b2294d…65df6`) to a rebuild from the repo sources.

## 6. Caveats

- **The modem does not currently ask for any of the new paths.** A plain boot's
  request list is unchanged. This is capability that stock has and this port did
  not; it is not a fault that was being hit every boot. The one measurable
  before/after difference is that `/readonly/firmware/image/strait/qdsp6m.qdb`
  would now succeed instead of ENOENT, and that instance 6 exists.
- **Not tested with the positioning engine running**, deliberately: a standalone
  session was out of scope for this session. See the note below.
- `install.sh` was **not** run, on purpose. It would recreate
  `/etc/modprobe.d/rhodep-ipa-hold.conf`, which is deliberately absent right now
  (`/etc/modprobe.d/README-rhodep-ipa-hold-is-OFF`), and that would take mobile
  data away from whoever is working on the watchdog bug. Only the three changed
  artefacts were installed, via `rhodep-protect-files release`/`register`.
- The phone watchdog-reset roughly every 2-5 minutes throughout this work. That
  is the documented IPA-plus-registered-modem bug, expected while the IPA hold
  is off, and it also happened in boots that predate this change. Nothing here
  can reset the SoC; it is userspace path resolution and one idle QRTR socket.

## READY TO TEST

Installed and live on 192.168.1.53, survives reboot, nothing regressed.

What to expect: probably nothing. The evidence says the GNSS engine's files are
in EFS and that stock's `apq/gnss` root is empty, so instance 6 now serves an
empty directory just as stock does. The value of the change is that if the
engine *does* try to reach a file server during measurement-engine bring-up, it
now finds one, and `tqftpserv -d` will say so — where before it would have found
nothing and logged nothing, which is indistinguishable from "never asked".

Run the standalone reproducer as usual, then:

	# did anything at all arrive on the GNSS instance?
	sudo journalctl -u tqftpserv -b --no-pager -o cat | grep "RFS instance"

	# and the full request stream, including the new prefixes
	sudo journalctl -u tqftpserv -b --no-pager -o cat \
	  | grep -E "request for|unable to open|invalid path|RFS instance"

A `request on RFS instance 6 (/vendor/rfs/apq/gnss)` line would be the first
direct evidence that the positioning subsystem talks to the AP file server at
all, and would turn this from a completeness fix into a live lead. Its absence
closes the `apq/gnss` lead for good.

To try more instances without rebuilding, edit
`/etc/systemd/system/tqftpserv.service.d/10-rhodep.conf` to `-i 2,6,9`
(`msm/adsp`, `apq/gnss`, `msm/cdsp` — the subsystems that exist on this SoC),
`systemctl daemon-reload`, `systemctl restart tqftpserv`. The file is immutable;
`sudo rhodep-protect-files release <path>` first.
