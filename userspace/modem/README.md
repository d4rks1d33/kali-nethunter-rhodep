# Modem support

This is what makes the radio work on motorola-rhodep. Install with:

	sudo apt install libqrtr-dev libzstd-dev
	sudo ./install.sh

Before this, the modem booted, registered 48 QMI services, answered DMS/NAS/UIM
and reported its IMEI, but sat in DMS operating mode `offline` and refused
**every** operating mode transition (`online` -> DeviceNotReady, `low-power` ->
InvalidTransition). After it, the phone registers on LTE and mobile data works.

## What was actually wrong: the modem's remote file system

`modem.mbn` is not the whole modem. At run time the modem fetches files from the
application processor over QMI service 4096 (TFTP), which `tqftpserv` answers.
On Android those requests resolve against the vendor image, the `fsg` partition
and `/mnt/vendor/persist`. On this port none of that existed, so every request
was answered "file not found" — silently, because nothing was logging them.

Running `tqftpserv -d` for one boot showed it immediately:

	12  /readonly/firmware/image/modem_pr/so/205_0_0.mbn   <- twelve retries
	 2  /readonly/vendor/fsg/mcfg_sw/mbn_sw.dig            <- "invalid path"
	 4  /readwrite/datablock/id_00
	 2  /readwrite/shob.bin
	 2  /readwrite/dhob.bin
	 1  /readwrite/mot_rfs/imei_sv

`205_0_0.mbn` is a **Motorola-signed Hexagon ELF** (`e_machine` 0xa4, 764 KiB)
that the modem loads into itself at run time, and it exists on the device, in
the `modem` partition under `image/modem_pr/so/`. Copying `modem_pr` into
`/lib/firmware/qcom/sm6375/motorola/rhodep/` and rebooting was enough to take
the modem from `offline` to accepting `--dms-set-operating-mode=online`, and it
then camped on a cell.

That is what `rhodep-rfs-populate` does, for all three sources:

| partition | what it provides | goes to |
| --- | --- | --- |
| `modem_a` | `image/modem_pr/so/*.mbn`, run-time Hexagon objects | `/lib/firmware/qcom/sm6375/motorola/rhodep/modem_pr/` |
| `modem_a` | `image/strait/qdsp6m.qdb`, 5.8 MiB Hexagon symbol database | `.../rhodep/strait/` |
| `fsg_a` | carrier (MCFG) configuration set + `mcfg_sw/mbn_sw.dig` | `.../rhodep/fsg/` |
| `persist` | `rfs/msm/mpss/`: RF calibration (`cal_rfs`), Motorola datablock / SIM-lock records, `shob.bin`, `dhob.bin`, `mot_rfs/imei_sv` | `/var/lib/tqftpserv/` |
| `persist` | `rfs/shared/server_info.txt`, `TFTP_SERVER.1.0` | `/var/lib/tqftpserv/rfs_shared/` |
| `persist` | `hlos_rfs/shared/` (empty at the factory) | `/var/lib/tqftpserv/rfs_hlos/` |
| `persist` | `rfs/apq/gnss/` (**empty at the factory**) | `/var/lib/tqftpserv/rfs_apq_gnss/` |

The persist tree is **copied, not bind mounted**: the modem writes into it
(`mcfg.tmp`, `ticf.bin`, `*_report.txt`) and the stock partition is left alone.

It runs once, at first boot, and is a no-op afterwards.

### A free modem-side log

Two of the files the modem writes into `/var/lib/tqftpserv` are its own
diagnostics, in plain text with source file and line number:

	datablock_report.txt   mot_datablock.c, 148: Datablock path to validate:
	                       /readwrite/datablock/id_00
	simlock_report.txt     mot_simlock_mirror.c, 292: HMAC missmatch
	hob_report.txt         mot_s_hob_stg.c, 229: HOB not yet created at the factory

This is the closest thing to a modem console this port has, and it costs
nothing. Note the files copied from `persist` already contain years of history
written under Android — check the mtime before believing an entry is yours.

## tqftpserv, patched

Upstream `translate.c` maps `/readonly/firmware/image/`,
`/readonly/firmware/modem_pr`, `/readonly/vendor/firmware/`,
`/readonly/vendor/firmware_mnt/image/` and `/readwrite/`, and **rejects
everything else before even looking at the filesystem**. This modem asks for
`/readonly/vendor/fsg/mcfg_{sw,hw}/mbn_{sw,hw}.dig`, which on Android is a
symlink into the `fsg` partition (AOSP target package
`rfs_msm_mpss_readonly_vendor_fsg_symlink`, referenced from
`sm6375-common/common.mk`).

`tqftpserv/` holds upstream (BSD-3-Clause, commit b6bb92d) plus three patches.
It is built as `/usr/local/bin/tqftpserv-rhodep` and selected with a systemd
drop-in, so `/usr/bin/tqftpserv` still belongs to the held distro package.

`0001-translate-serve-readonly-vendor-fsg.patch` — three lines that add a
catch-all for the rest of `/readonly/vendor/`, resolved under the remoteproc
firmware directory.

`0002-translate-serve-shared-hlos-ramdumps.patch` — the other three roots of a
Qualcomm RFS namespace. See "The whole RFS namespace" below.

`0003-tqftpserv-publish-rfs-qmi-instances.patch` — publish QMI service 4096 on
more than one instance. See the same section.

`-d` is left on deliberately. It is a handful of lines per boot and it is the
only visibility there is into what the modem wants.

## The whole RFS namespace

Read `/vendor/rfs/` off the stock vendor image and every root is the same five
entries — this is `msm/mpss`, and `apq/gnss`, `msm/adsp`, `mdm/tn` and the other
eight are identical bar the `readwrite` target and mpss's extra `fsg` symlink:

	hlos      -> /mnt/vendor/persist/hlos_rfs/shared    (same dir for all roots)
	shared    -> /mnt/vendor/persist/rfs/shared         (same dir for all roots)
	ramdumps  -> /data/vendor/tombstones/rfs/modem      (per subsystem)
	readonly/firmware        -> /vendor/firmware_mnt
	readonly/vendor/firmware -> /vendor/firmware
	readonly/vendor/fsg      -> /vendor/fsg             (msm/mpss only)
	readwrite -> /mnt/vendor/persist/rfs/msm/mpss       (per subsystem)

Upstream tqftpserv implements two of the five. All three of the others are named
literally in **this modem's own firmware** — `strings` over the `modem` partition's
`modem.b*`/`modem.mbn` yields, verbatim and exhaustively:

	/hlos/   /hlos/qdma/
	/ramdumps/   /ramdumps/efs_report.txt
	/ramdumps/wlan_minidump   /ramdumps/wlan_minidump.count
	/shared/
	/readonly/firmware/image/modem_pr/mcfg/configs
	/readonly/firmware/image/modem_pr/so/%s
	/readonly/firmware/image/strait/qdsp6m.qdb
	/readonly/firmware/image/testpd.mbn
	/readonly/firmware/image/wlanmdsp.mbn
	/readonly/vendor/firmware/{testpd,wlanmdsp}.mbn
	/readonly/vendor/firmware_mnt/image/{testpd,wlanmdsp}.mbn
	/readwrite{,/}   /readwrite/cal_rfs
	/readwrite/datablock/{id,slid}   /readwrite/datablock_report.txt
	/readwrite/{dhob,shob,ticf}.bin  /readwrite/{dhob,hob,fsg,simlock}_report.txt
	/readwrite/mcfg.tmp   /readwrite/mot_rfs/imei_sv
	/readwrite/ota_firewall/ruleset   /readwrite/server_check.txt

`0002` adds `/shared/`, `/hlos/` and `/ramdumps/`, as directories under the
state directory rather than as symlinks into `/mnt/vendor/persist`, which does
not exist here:

	/shared/    -> /var/lib/tqftpserv/rfs_shared      (global, as on stock)
	/hlos/      -> /var/lib/tqftpserv/rfs_hlos        (global, as on stock)
	/ramdumps/  -> <the instance's root>/rfs_ramdumps (per instance, as on stock)

`strait/qdsp6m.qdb` — 5.8 MiB, `QDB` magic, the Hexagon symbol database;
"strait" is SM6375's internal codename, cf. `nav_ConfigStrait` in the same
firmware — is in the `modem` partition and was not being copied out of it, so
`/readonly/firmware/image/strait/qdsp6m.qdb` was ENOENT. `rhodep-rfs-populate`
now copies it. `testpd.mbn` is **not** on this device's `modem` partition at
all, so stock answers ENOENT to it too; nothing to do there.

### The twelve QMI service instances

Stock does not run one file server per subsystem. `/vendor/bin/tftp_server` is a
single process, started with no arguments from `vendor.qti.tftp.rc`, that
registers QMI service 4096 **twelve times** and picks the root from the instance
the request arrived on. The table is in its `.data` at vaddr `0x1a708`, twelve
`{u32 instance, char *root}` pairs, and the registration loop at `0x17860` walks
`1..12` and puts the instance straight into the QRTR `NEW_SERVER` packet:

	 1 msm/mpss     2 msm/adsp     3 mdm/mpss     4 mdm/adsp
	 5 mdm/tn       6 apq/gnss     7 msm/slpi     8 mdm/slpi
	 9 msm/cdsp    10 mdm/cdsp    11 msm/wpss    12 mdm/wpss

libqrtr encodes `qrtr_publish(fd, service, version, instance)` as
`instance << 8 | version`, so upstream's `qrtr_publish(fd, 4096, 1, 0)` puts a
**1** on the wire — which is exactly stock's `msm/mpss`. That is why the modem
proper has always found it, and it confirms the numbering: the one instance this
port publishes is the one the modem uses.

Every other instance had nothing listening, and `qrtr-ns`'s `server_match()`
(`ns.c:118`) turns a lookup with a non-zero instance into an exact match, so a
client that looked one up got **no server at all**. tqftpserv never saw the
request and never logged it — which is worth knowing before concluding from a
silent log that nothing was asked for.

`0003` makes the instance list a command line option. Instance 1 is always
published and keeps `/var/lib/tqftpserv` as its read/write root, so the modem
path is bit-for-bit what it was. The drop-in adds `-i 6`, `apq/gnss`, which gets
its own root at `/var/lib/tqftpserv/rfs_apq_gnss`. Adding more is `-i 2,6,9`.

	$ qrtr-lookup | grep 4096
	   4096   1   0   1  16394  TFTP      <- msm/mpss
	   4096   6   0   1  16395  TFTP      <- apq/gnss

One socket per instance is required and not one socket published repeatedly:
`qrtr-ns` keys its server map on the *port*, so a second `NEW_SERVER` from the
same port replaces the first instead of adding to it.

### What this does not fix, and the evidence for that

The GNSS reset was the reason for looking. It is **not** an RFS problem:

- `/mnt/vendor/persist/rfs/apq/gnss/` is **empty** on this phone's factory
  `persist` partition — mode 0750, owner 2903:2903, mtime 2022-03-28, zero
  files. Serving instance 6 perfectly serves nothing, because stock has nothing
  there. `hlos_rfs/shared` is empty too.
- The `apq/gnss` root is boilerplate. The same vendor image carries seven
  `rfs/mdm/*` roots for an external modem this phone does not have.
- The positioning engine's files are **EFS**, not TFTP. `gpsoffsets.bin`,
  `GPSX1CorrFile`, `GPSX2CorrFile`, `GPSX3File`, `NAVICX3File`, `EphFile01..31`,
  `IonoFile`, `UtcFile`, `SbasCannedFile`, `GeoAlmFile120..141` all sit in the
  firmware's string table next to `/GNSS/ME/%s`, `/GNSS/PE/%s`, `/GNSS/CFG/`,
  `/GNSS/TLE/nv_items.bin` and `/nv/item_files/gps/%s/%s`. Those are paths in
  the modem's own filesystem, whose backing store is `modemst1`/`modemst2` and
  whose server is **rmtfs**, block by block, with no file names on the wire.
- None of those names exists as a file anywhere on the stock `persist`,
  `vendor`, `modem` or `fsg` partitions. The only `xtra` hit in the whole vendor
  image is `/vendor/bin/xtra-daemon` and its seccomp policy, i.e. the assistance
  data downloader, which is an AP-side network client and not an RFS file.

So `0002` and `0003` close a real gap between this port and stock, and the
`apq/gnss` lead is closed negatively: stock exposes that root, we now expose it
too, and it is empty on both.

## rmtfs must NOT have -r

In rmtfs, `-r` means **read-only** (`rmtfs.c:526 read_only = true`), and
read-only means every write the modem makes goes into a per-open RAM shadow and
is thrown away at reboot. The packaged drop-in shipped `-r` with a comment
claiming it meant read/write; `install.sh` overrides it with `-P -s`.

**Fixed in the package too, 2026-09-05.**
`packages/rhodep-modem-support/usr/lib/systemd/system/rmtfs.service.d/10-rhodep.conf`
now says `-P -s` and carries the correction in its comment. Nothing was broken
on *this* device, because the `/etc` drop-in `install.sh` writes takes
precedence over the `/usr/lib` one from the `.deb` — but on a device where the
`.deb` drop-in is the only one, UIM provisioning would silently stop persisting
and every boot would need the SIM re-provisioned. That is the kind of bug that
looks like a hardware fault.

This matters because the modem keeps the UIM provisioning session in NV. With
`-r`, a SIM the modem has not seen before never gets one.

**Back up `modemst1`, `modemst2` and `fsc` before turning this on.** There is a
copy under `_common/backups-historicos/modem-efs-*/`.

## rhodep-uim-provision

Even with a read/write EFS, this modem does not open a provisioning session by
itself. Without one the UICC application never leaves `detected`:

	Provisioning applications:
		Primary GW:   session doesn't exist
	Application [1]:
		Application state: 'detected'
		Personalization state: 'unknown'

and ModemManager gives up with "Couldn't check unlock status" and reports
`failed: sim-missing` — which is what the earlier sessions of this port were
chasing as a SIM problem. One QMI call fixes it:

	qmicli -d qrtr://0 --uim-change-provisioning-session=\
	    "activate=yes,session-type=primary-gw-provisioning,slot=1,aid=<AID>"

	-> Application state: 'ready'
	   Personalization state: 'ready'

This is not a workaround, it is what the vendor stack does: on Android qcrild
issues `QMI_UIM_CHANGE_PROVISIONING_SESSION` during UIM bring-up. ModemManager
1.24 does not, because most QMI modems auto-provision the first card.

The service runs before ModemManager so MM's single probe sees a ready card. It
is idempotent: once the EFS is writable the session survives reboots and the
service just logs "already exists". It is still needed for any new SIM.

## memshare

The modem asks the application processor for 5 MB over QMI service 52 during
bring-up. `memshare-daemon.c` answers it. This was investigated at length and is
**not** what was blocking the radio (see HANDOFF-SESSION4.md sessions 13b/13c),
but answering it is correct and it is kept.

The shipped answer is **0**, which is not what stock answers. Stock's only
non-zero memshare client is `qcom,client_3` with
`qcom,peripheral-size = <0x500000>` and `qcom,client-id = <0x01>`, and the
vendor driver sets `init_size` from that unconditionally -- `allocate-on-request`
defers the allocation, not the advertised size -- so a stock kernel answers
5 MiB to exactly the one query this modem sends. Answering 0 is a deliberate
divergence and the reason is the next paragraph.

Do not advertise a size on a system where nothing actually owns the region: the
daemon would hand out memory Linux is still using. **The daemon enforces this
itself**, and it will accept a region from exactly two sources:

1. `/proc/device-tree/reserved-memory/` -- a `no-map` `memshare*` node in the
   *live* tree. Preferred, because no-map memory is never mapped by Linux at all.
2. `/sys/kernel/rhodep_memshare/` -- a region that
   `kernel/diag-modules/rhodep_memassign.ko` allocated itself with `alloc=1`,
   holds for its whole lifetime, and has already moved to `VMID_MSS_MSA`. The
   `assigned` attribute is a fact about the return value of
   `qcom_scm_assign_mem()`, not about what the module was asked to do, and
   anything other than `1` is treated as "no region".

The device tree is tried first; the module is the fallback for images whose boot
image predates the reservation, and it is the only route that needs no reflash.
If neither is present the offer is forced to 0 whatever the command line says, so
a boot image and a rootfs that disagree degrade to "reports 0" instead of to
silent corruption. Ask it what it sees:

	rhodep-memshare check
	# reserved region memshare@8ab00000: 0x8ab00000 + 0x800000 (no-map)   -> exit 0
	# module region rhodep_memassign: 0xfd200000 + 0x500000 (...)         -> exit 0
	# no assigned region at /sys/kernel/rhodep_memshare either            -> exit 1

The reservation itself is `kernel/patches/0120`. A one-off hand-repacked
`kali-boot-v93-memshare.img` carried it during session 13c and was never
committed; 0120 is that node as a patch.

One thing the daemon cannot do itself, and it matters if the modem is ever going
to *use* the buffer rather than just be told about it: the vendor driver calls
`shared_hyp_mapping()` -> `hyp_assign_phys(phys, size, {VMID_HLOS} ->
{VMID_MSS_MSA}, RW)` before the modem touches the memory. Userspace has no way
to make that call. `kernel/diag-modules/rhodep_memassign.c` does it with
`qcom_scm_assign_mem()` and has to be loaded before the address is handed over.
Without it a modem that writes to the buffer takes an XPU violation, which on
this SoC is a silent reset. In session 13c the modem was handed the address with
no assignment and nothing happened -- but that modem never finished
initialisation, so it almost certainly never wrote to it. That is not evidence
that the assignment is unnecessary.

### The full 5 MiB answer has now been given, and the modem takes it

Measured 2026-09-05 with `rhodep_memassign.ko alloc=1 size=0x500000` loaded from
an `ExecStartPre=/sbin/modprobe` on `rhodep-memshare.service`, offer `0x500000`:

	[12.926] rhodep_memassign: allocated 0xfd300000 + 0x500000 and holding it
	[12.948] rhodep_memassign: qcom_scm_assign_mem(... HLOS -> vmid 0xf perm 0x6) returned 0
	[12.977] memshare: module region rhodep_memassign: 0xfd300000 + 0x500000
	[16.205] memshare: request from node=0 port=8 MEM_QUERY_SIZE (0x0024) txn=1
	[16.205] memshare: reporting size 5242880
	[16.205] memshare: request from node=0 port=8 MEM_ALLOC_GENERIC (0x0022) txn=2
	[16.205] memshare: handing out 0xfd300000 (5242880 bytes)

So the "if the modem is offered memory it will follow up with an allocation
request" question is answered: **it does**, in the same millisecond, and its
request decodes exactly as stock's client 3 --
`01` num_bytes `0x00500000`, `02` client_id `1`, `03` proc_id `0`,
`04` sequence_id `0`, `10` alloc_contiguous `1`. It never sends `MEM_FREE`.

It changes nothing about the GNSS reset. See
`kernel/diag-modules/README.md`, "What they have found so far".

## Also required, outside this directory

`kernel/patches/0042-net-ipa-sm6375-fix-imem-address.patch`. The IPA data for
SM6375 inherited qcm2290's IMEM address (0x146a8000); on this SoC the modem's
route and filter tables are at **0x0c123000**. Nothing notices until the modem
actually registers, at which point the IPA reads them through its own SMMU
context bank, faults, and the watchdog takes the SoC down:

	arm-smmu c600000.iommu: Unhandled context fault: fsr=0x402,
		iova=0x0c123080, cbfrsynra=0x4a0
	androidboot.bootreason=watchdog

`ipa.ko` is a module, so this one does not need a reflash: rebuild, copy the
module, `depmod -a 7.2.0-rc5`, reboot.

## READ THIS FIRST: the IPA is held out of the boot on purpose

`install.sh` drops `/etc/modprobe.d/rhodep-ipa-hold.conf`, which keeps `ipa.ko`
from loading. **That single file is the only thing between this port and
working mobile data**, and it is there because of a bug that has nothing to do
with the modem work above:

> With `ipa.ko` loaded **and** the modem registered on a network, the SoC
> watchdog-resets every 3 to 10 minutes, silently. Either one alone is stable.

While the file is in place:

- no mobile data (`rmnet_ipa0` / `qmapmux0.0` never appear)
- **no ModemManager**, therefore no dialer and no SMS UI. MM refuses a QMI
  modem that has no net port:
  `couldn't create modem for device 'qcom-soc': Failed to find a net port`
- the QMI layer still works: `qmicli -d qrtr://0 --dms-get-ids`, etc.

Delete the file, reboot, and everything documented below comes back: LTE
registration, ~24 Mbit/s of data, calls and SMS. Do that if you are working on
the bug, and read the comments in the file first — it describes the debug loop
that keeps the causal window a few seconds wide instead of a few minutes.

The bisection and the leads are in HANDOFF-SESSION4.md session 14, "OPEN BUG".
The best one is that patch 0025 gives SM6375 `interconnect_count = 0`, which
was verified for probe and never for operation, while the vendor IPA node asks
for `ipa_to_ebi1`, `ipa_to_imem` and `appss_to_ipa`.

## Verifying

(with `rhodep-ipa-hold.conf` removed and after a reboot)

	mmcli -m any | grep -E "state:|registration|operator|access tech"
	  state: registered   registration: home
	  operator name: PERSONAL   access tech: lte

	nmcli con up personal          # gsm connection, apn datos.personal.com
	curl --interface qmapmux0.0 https://ifconfig.me

If it is not registered, in order: `journalctl -u rhodep-rfs-populate -b`,
`journalctl -u tqftpserv -b | grep -E "reject|invalid"` (there should be nothing
but `datablock/id_01`, which is the second SIM slot, and `ota_firewall/ruleset`,
which the modem creates), then `journalctl -u rhodep-uim-provision -b`.

## Voice and SMS

Calls connect and SMS works. Verified on the network, not inferred:

	mmcli -m any --voice-create-call=number=111
	  call state changed: unknown -> dialing -> active -> terminated
	mmcli -m any --3gpp-ussd-initiate="*111#"
	  -> "USSD terminated by network"          (a real answer)

A call to `*2447` was answered by Personal and the SMS it replies with arrived.

**There is no audio during a call, and that is not a modem problem.** On
Qualcomm the voice audio never reaches the application processor: it goes
modem <-> ADSP <-> codec, and the AP only sets up the MVM/CVS/CVP sessions over
APR. Mainline has q6afe/q6asm/q6adm/q6routing and no q6voice, which shows up in
this port's boot log as APR services 3, 4, 7, 8 and nothing else — the voice
services are 0x09/0x0A/0x0B (`techpack/audio/include/ipc/apr.h`), driven by
`techpack/audio/dsp/q6voice.c`, 10565 lines. See HANDOFF-SESSION4.md session 14
for what implementing it would take.
