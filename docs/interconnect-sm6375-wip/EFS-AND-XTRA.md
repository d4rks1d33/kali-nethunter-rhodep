# Modem EFS persistence, and the missing XTRA path

Investigation, 2026-09-06, Motorola rhodep (SM6375), kernel 7.2.0-rc5, Kali
ARM64. Everything below was measured on the live device unless it says
otherwise.

The lead being tested: *the modem's non-volatile storage may be a throwaway RAM
shadow, so the positioning engine's calibration and assistance data never
persist, and it starts every time from a state stock Android never sees.*

**The lead is false.** Modem EFS writes persist on this port, and they are
proven to persist by direct measurement across a power cycle. The
`rmtfs -r` hazard is real, but it is not, and never was, active on this device.

A separate and genuinely missing piece *was* found and has been fixed: this
port had no equivalent of stock Android's `xtra-daemon`, so the predicted-orbit
almanac had never been loaded. `rhodep-xtra` now does that job.

---

## 1. rmtfs: what is actually running

`-r` really does mean read-**only**. From the rmtfs sources:

```c
rmtfs.c:526    case 'r':  read_only = true;              /* "-r to avoid writing to storage" */
rmtfs.c:575    ret = storage_init(storage_root, read_only, use_partitions);

storage.c:89   if (!storage_read_only) {
storage.c:90       fd = open(fspath, O_RDWR);            /* the real partition */
storage.c:99   } else {
storage.c:100      ret = storage_populate_shadow_buf(rmtfd, fspath);   /* opens O_RDONLY */
               }

storage.c:246  if (!storage_read_only)
storage.c:247      return pwrite(rmtfd->fd, buf, nbyte, offset);
               ...                                       /* else memcpy into realloc'd RAM */

storage.c:273  int storage_sync(struct rmtfd *rmtfd) {
storage.c:274      if (storage_read_only) return 0;       /* fdatasync becomes a lie */
```

So under `-r` the modem's writes are acknowledged, kept in a `realloc`'d shadow
buffer, and `fdatasync` reports success without touching anything. The modem
cannot tell. That is what makes it a nasty failure mode — and it is why the
comment claiming `-r` meant "read/write" was worth correcting.

### What the live process runs

```
$ systemctl show rmtfs -p ExecStart
ExecStart={ path=/usr/bin/rmtfs ; argv[]=/usr/bin/rmtfs -P -s ; ... }

$ ps -o args= -p $(pidof rmtfs)
/usr/bin/rmtfs -P -s
```

**No `-r`.** Confirmed independently from `/proc/<pid>/cmdline`.

### Which drop-in is authoritative, and why

There are three copies of this drop-in in the tree, and on the phone there are
two files:

| file | content | in effect? |
|---|---|---|
| `/usr/lib/systemd/system/rmtfs.service` (base unit, distro) | `-r -P -s` | no — `ExecStart=` reset below |
| `/usr/lib/systemd/system/rmtfs.service.d/10-rhodep.conf` (from `rhodep-modem-support`) | `-r -P -s` on the installed v3 package | **no** |
| `/etc/systemd/system/rmtfs.service.d/10-rhodep.conf` (from `userspace/modem/install.sh`) | `-P -s` | **yes** |

The reason is not drop-in merge order in the usual sense. **Both drop-ins have
the same filename**, `10-rhodep.conf`. systemd resolves drop-ins by *basename*
across the search path and the highest-precedence directory wins outright: the
`/etc` copy does not merge with the `/usr/lib` copy, it **replaces** it, and the
`/usr/lib` one is never parsed at all. That is visible directly:

```
$ systemctl cat rmtfs
# /usr/lib/systemd/system/rmtfs.service
...
# /etc/systemd/system/rmtfs.service.d/10-rhodep.conf     <- only this one

$ systemctl show rmtfs -p DropInPaths
DropInPaths=/etc/systemd/system/rmtfs.service.d/10-rhodep.conf
```

`userspace/modem/install.sh` has always written the `/etc` copy without `-r`,
with a correct comment explaining why. So the packaged copy's wrong flag was
latent on this device, never active.

It was **not** latent everywhere:
`postmarketos/device-motorola-rhodep/rmtfs-rhodep.conf` is the only drop-in on a
postmarketOS install — there is no `/etc` override there — and it still carried
`-r`. That one was really taking effect on pmOS. Both stale copies are now
corrected.

### Direct proof from the running process

The cleanest evidence, needing no reboot: in read-only mode `fd_open()` never
takes the `open(fspath, O_RDWR)` branch, it calls `storage_populate_shadow_buf()`
which opens `O_RDONLY` into a malloc buffer. So the open fds decide it.

```
$ ls -l /proc/$(pidof rmtfs)/fd
 8 -> /dev/sde12        (modemst1)
 9 -> /dev/sde13        (modemst2)
$ grep flags /proc/$(pidof rmtfs)/fdinfo/8
flags:  0400002         <- O_RDWR | O_LARGEFILE
$ grep flags /proc/$(pidof rmtfs)/fdinfo/9
flags:  0400002
```

Persistent `O_RDWR` file descriptors on the real block devices. That is
structurally impossible under `-r`.

### The partitions rmtfs is backing

```
modemst1 -> /dev/sde12    3145728 bytes   (3 MiB)   held open O_RDWR by rmtfs
modemst2 -> /dev/sde13    3145728 bytes   (3 MiB)   held open O_RDWR by rmtfs
fsc      -> /dev/sde14     131072 bytes   (128 KiB) not opened
fsg      -> /dev/sdd13  134217728 bytes   (128 MiB) not opened
fsg_a    -> /dev/sdd13  134217728 bytes
fsg_b    -> /dev/sdf13  134217728 bytes
```

Only modemst1/modemst2 are actually served, which matches the note in the udev
rule that a cold boot only shows `/boot/modem_fs1` and `/boot/modem_fs2` being
opened.

### The fsg udev alias

`packages/rhodep-modem-support/usr/lib/udev/rules.d/99-rhodep-fsg-alias.rules`
does what it claims. Verified:

```
/dev/disk/by-partlabel/fsg -> ../../sdd13
$ udevadm info -q property -n /dev/sdd13 | grep DEVLINKS
... /dev/disk/by-partlabel/fsg ... /dev/disk/by-partlabel/fsg_a ...
```

The `fsg` symlink is created by the rule (its mtime is boot time, while the
firmware-created partlabel links carry the pre-NTP 1973 timestamp). It is still
preventive — nothing has been observed asking for it.

One real caveat, now documented in the rule rather than silently fixed: it
hardcodes `fsg_a`. This phone boots slot `_a` (`androidboot.slot_suffix=_a`) so
it is correct today, but on a `_b` boot it would alias the wrong slot's factory
NV. rmtfs already has the right mechanism — `-S b` — and that is the fix if it
ever matters.

---

## 2. Do EFS writes actually persist? Measured: yes

Three independent observations, none of which required writing to a block
device from userspace.

**(a) A modem NV write reached the disk.** Hashing the partitions across the
session, `modemst2` changed in a window whose only activity was a
`--loc-set-engine-lock` sequence:

```
01:02:27Z  sde13  0fdd1e4f8d0672348a8fda715ed82887607b3e1a6cef056136cf142cb12cc90d
   ... qmicli --loc-set-engine-lock=mi / =all / =none ...
01:07:00Z  sde13  fd6855adc19b33a88f644daa230802f4c1df61499d06d68ec0832c28a60572d5
```

**(b) The XTRA injection reached the disk.** `modemst1` changed a few seconds
after the almanac was injected — an asynchronous EFS flush by the modem:

```
01:09:11Z  sde12  a12ad8f3b46b926f04ed8ba116195d99cf86f302adcfd4a95f9ef0d6e17be284
01:09:26Z  sde12  05a442e4a8796d73b9d9c067683a9a7dee58eb7b64211d3523598bb85ddb39a6
```

**(c) It all survived a power cycle.** Byte-identical across `reboot`:

| partition | before reboot | after reboot |
|---|---|---|
| modemst1 `sde12` | `05a442e4…b39a6` | `05a442e4…b39a6` |
| modemst2 `sde13` | `fd6855ad…572d5` | `fd6855ad…572d5` |
| fsc `sde14` | `fa43239b…8e471` | `fa43239b…8e471` |

And the engine agrees. The predicted-orbit validity window, injected before the
reboot, is intact after it:

```
after reboot:  valid from 2026-09-05T23:00:00Z, 168 hours, 165.8 hours remaining
```

**(d) It also survived two unclean power cycles.** While this investigation was
finishing, another agent's `BISECT-R6plus-standalone-*` runs reset the phone
twice (22:14:29 and 22:18:17). Those are watchdog power-cycles, not orderly
shutdowns — no `sync`, no service stop, no `rmtfs -s` flush on exit. The
almanac came back intact both times:

```
after two hard resets:  valid from 2026-09-05T23:00:00Z, 168 hours, 165.6 remaining
```

That is stronger than the clean-reboot result. It means the modem's EFS writes
are reaching the flash at write time, not being flushed at shutdown, so they
survive exactly the failure mode this port actually suffers.

**Conclusion: modem EFS writes persist on this port. Positioning assistance
data persists across a clean reboot and across a watchdog reset. The
"throwaway RAM shadow" hypothesis is disproven for this device.**

A further corroboration that the engine's NV is real and stock-provisioned:
`QMI_LOC_GET_SERVER` returns `supl.google.com:7275` for the UMTS SLP server.
Nothing in this port ever set that. It is Motorola's factory value, still in
the EFS, read back over QMI.

(EFS images were snapshotted to `/root/efs-backup/*.img` before the reboot.
Those are files, read out of the partitions; nothing was written to any block
device at any point.)

---

## 3. The `get-engine-lock returns no TLV` anomaly: a libqmi bug, not engine state

`qmicli --loc-get-engine-lock` prints `error: couldn't get engine lock: missing`.
This was noted as "a small hint that the engine's state is not actually
populated". **It is not. It is a libqmi TLV mismatch and carries no information
about the engine at all.**

qmicli only reaches that message when the indication *arrived* and its
mandatory status TLV *parsed* (`qmicli-loc.c:1222`); it means "the optional
lock-type TLV was absent", not "the modem said nothing". The raw traffic:

```
<<<<<< QMI:      flags = "indication"   message = "Get Engine Lock" (0x003B)
<<<<<< TLV:      type = "Indication Status" (0x01)  length = 4
                 value = 00:00:00:00   translated = success
<<<<<< TLV:      type = 0x12                        length = 8
                 value = 00:00:00:00:00:00:00:00
<<<<<< QMI:      flags = "response"     TLV Result = SUCCESS
```

libqmi's `data/qmi-service-loc.json` declares the lock in **TLV `0x10`, 4 bytes,
`QmiLocLockType`**. This firmware answers in **TLV `0x12`, 8 bytes**. libqmi
looks for `0x10`, does not find it, and qmicli prints `missing`.

That the `0x12` payload is genuinely live lock state was confirmed by
perturbing it and reading the raw bytes back:

| set to | TLV 0x12 |
|---|---|
| *(baseline)* | `00 00 00 00 00 00 00 00` |
| `mi` | `01 00 00 00 00 00 00 00` |
| `all` | `01 00 00 00 00 00 00 00` |
| `none` **(restored)** | `00 00 00 00 00 00 00 00` |

The first word tracks locked/unlocked. The engine is answering correctly and its
state *is* populated. Note also that libqmi reads the indication status into a
variable and then never checks it (`qmicli-loc.c:1216-1224`), so a genuine
non-success status would be reported with the same misleading text — worth
knowing if this message is ever trusted for diagnosis.

The engine lock is left at `none`.

---

## 4. Full read-only LOC inventory

`qmicli --help-loc` on libqmi 1.38.0 offers exactly these, and only five are
read-only queries of stored state:

```
--loc-get-position-report            needs a running session
--loc-get-gnss-sv-info               needs a running session
--loc-get-nmea-types                 *
--loc-get-operation-mode             *
--loc-get-engine-lock                *
--loc-get-predicted-orbits-data-source     *
--loc-get-predicted-orbits-data-validity   *
```

There is **no** `--loc-get-server` and **no** fix-criteria or constellation
query in qmicli, although libqmi *does* define `0x0043 Get Server`. `rhodep-xtra
status` fills that gap. Full state, after injection:

```
== predicted orbits (XTRA) ==
  valid from : 2026-09-05T23:00:00Z (raw 1788649200)
  valid for  : 168 hours
  server     : https://path1.xtracloud.net/xtra3grej.bin
  server     : https://path2.xtracloud.net/xtra3grej.bin
  server     : https://path3.xtracloud.net/xtra3grej.bin

== engine ==
  operation mode : standalone (0x4)
  NMEA types     : gga, rmc, gsv, gsa, vtg, pqxfi, pstis (0x7fff)
  engine lock    : unlocked  (TLV 0x12 = 0; see section 3)

== assistance servers ==
  UMTS SLP (SUPL) : supl.google.com:7275
  CDMA PDE        : <unset>
  CDMA MPC        : <unset>
  Custom PDE      : <unset>
```

`operation mode: standalone` is the engine's *configured default mode*, not a
running session. No session is running and none was started.

The `Allowed Sizes` TLV of the predicted-orbits-source indication is absent on
this firmware, so the modem does not advertise a maximum almanac size.

LOC is service 16 version 2 on QRTR node 0, port 108, described by
`qrtr-lookup` as `Location service (~ PDS v2)`.

---

## 5. The XTRA path

### The servers are alive in 2026

All three CDN endpoints the modem itself names respond, and all three serve
byte-identical content:

```
https://path1.xtracloud.net/xtra3grej.bin   200   40949 bytes   md5 ad2943c1…
https://path2.xtracloud.net/xtra3grej.bin   200   40949 bytes   md5 ad2943c1…
https://path3.xtracloud.net/xtra3grej.bin   200   40949 bytes   md5 ad2943c1…
```

(`xtra3grc.bin` 33298 B and `xtra2.bin` 60758 B are also served, but
`xtra3grej.bin` is what this modem asks for, so that is what is fetched.)
Ordinary public CA chain — the stock `XTRA_CA_PATH=/usr/lib/ssl-1.1/certs` is
Android's bundle and is not needed; the system trust store works.

### qmicli cannot inject; libqmi can

`qmicli --help-loc` has **no** `--loc-inject-predicted-orbits-data`. libqmi
1.38 nevertheless defines the messages, so the injection is done in-process
through `gir1.2-qmi-1.0`.

### Which message this firmware implements

libqmi defines two, with identical part-wise payloads. Measured on rhodep:

| message | result |
|---|---|
| `0x0035 QMI_LOC_INJECT_PREDICTED_ORBITS_DATA` | **`NotSupported` (94)** |
| `0x00A7 QMI_LOC_INJECT_XTRA_DATA` | **success** |

`rhodep-xtra` defaults to `--message auto`: it probes `0x0035` with one part and
falls back to `0x00A7`, so the tool stays correct on firmware that does support
the documented message.

Chunking is required: the Part Data TLV is capped at 1024 bytes, parts are
1-based and sent in order. 40949 bytes → 40 parts, the last 1013 bytes. Only
the final part produces the summarising indication:

```
  part 40/40 (40949 bytes)
  final indication: status=0 part=40
```

### `--loc-inject-time` and the Time Source TLV

Hand-rolling `0x0038 Inject UTC Time` with only UTC Time and Time Uncertainty
returns **`MalformedMessage` (1)**. Adding TLV `0x10` Time Source makes it
succeed. `qmicli` always sends all three (`qmicli-loc.c:1549-1551`), which is
why `qmicli --loc-inject-time` works and a two-TLV message does not. On this
firmware the Time Source TLV is **mandatory**, despite libqmi marking it
optional.

`0x0039 Inject Position` works, and is fed the daemon's real WiFi fix.

### Why injection cannot start the engine

Checked before running anything, from libqmi's own message definitions:

* The only message that starts the measurement engine is **`0x0022
  QMI_LOC_START`**, whose input is `Session ID` + `Fix Recurrence Type`
  (+ intermediate-report and interval options).
* `0x0035`, `0x00A7`, `0x0038` and `0x0039` carry no session id, no recurrence
  and no start semantics. Their outputs are an Operation Result plus an
  indication carrying a status (and, for the almanac, a part number). Nothing
  in them can transition the engine into a fix attempt.
* Injection is precisely what A-GPS does *before* a fix, which is the whole
  reason these messages exist separately from `START`.
* `rhodep-xtra` additionally never sends `0x0021 QMI_LOC_REGISTER_EVENTS`. It
  wants no position-report or SV-info indications, and asking for them is a
  needless way to interact with the engine.

This is enforced in code, not just by intent: every send goes through
`Loc.call()`, which asserts the message is in `ALLOWED_MESSAGES`. `start`,
`stop`, `register_events` and `delete_assistance_data` are not in that set, and
`rhodep-xtra self-test` checks that they are not.

Observed in practice: injection ran repeatedly, including once through the
systemd unit, with no reset and no session.

### What was added

```
userspace/gnss/rhodep-xtra                                  the tool
packages/rhodep-gnss/usr/local/sbin/rhodep-xtra             packaged copy
packages/rhodep-gnss/usr/lib/systemd/system/rhodep-xtra.service
packages/rhodep-gnss/usr/lib/systemd/system/rhodep-xtra.timer
```

`rhodep-xtra status | fetch | inject | self-test`. The timer runs 3 min after
boot and every 6 h, `Persistent=true`; `inject` reuses a cached almanac younger
than 12 h so a reboot costs one QMI transfer and no download. Enabled by the
postinst — unlike `rhodep-cell-db.timer`, this is 40 kB, not 1.1 GB.

---

## 6. Assessment: does any of this explain the reset?

**Honestly: no, and the XTRA half almost certainly does not.**

The recorded failure is that starting the measurement engine power-cycles the
SoC **within 100 ms, reproducibly**. Nothing about a cold start explains that:

* A cold start with no almanac makes a fix *slow*. The receiver has to
  demodulate ephemeris off the satellites at 50 bit/s, which takes tens of
  seconds at best. It is a time-to-first-fix problem, not a crash.
* 100 ms is far too early for any of it. At 100 ms the engine has not acquired
  anything, has not demodulated anything, and has not reached the point where
  the presence or absence of predicted orbits changes its behaviour. Whatever
  kills the SoC happens during bring-up — clocks, regulators, interconnect
  votes, the RF front end being powered — long before the almanac is consulted.
* **This has now been observed directly, by accident.** No session was started
  from this investigation. But while it was finishing, another agent's
  standalone bisect ran on this phone and reset it twice — at 22:14:29 and
  22:18:17 — and on both occasions the engine had a **valid XTRA almanac,
  injected time and an injected coarse position**, because this work had put
  them there minutes earlier and they persisted across the intervening reset.

  ```
  Sep 05 22:18:12  unknown: BISECT-R6plus-standalone-powermode1-1788657492
  Sep 05 22:18:13  rhodep-gnss: bisect run: 12 opmode=standalone powermode=1,1000
  Sep 05 22:18:17  rhodep-gnss: SET_OPERATION_MODE: standalone (4) from this client
  Sep 05 22:18:17  rhodep-gnss: REG_EVENTS mask 0x187 (min,engine)
  <log ends: hard reset>
  ```

  So the hypothesis has an answer: **a fully assisted engine still resets the
  SoC in standalone mode.** XTRA does not explain the failure. (The
  `rhodep-xtra` run in the earlier window had completed and released its LOC
  client two seconds before the 22:14:29 reset — `del_client for 1:16426` is in
  the log above the reset — so the injection is not implicated in either
  event.)

The counter-argument worth keeping is the one about code paths: an engine with
no assistance may take a *different* bring-up path than one with it — for
instance, deciding it needs a full RF cold search rather than a narrow one,
which is a different sequence of hardware votes. That is testable now and was
not before, so it is worth one careful run. It is a long shot.

**The sharper half of the original lead was the calibration/persistence
question, and that half is now closed: the EFS does persist.** So the engine is
*not* starting from a state stock Android never sees, at least not for the
reasons proposed. Whatever calibration the engine keeps in `gps_nv_efs.c` /
`gps_fs_task` is being written to modemst1/modemst2 and is being read back
after a reboot, exactly as on stock.

That removes the most attractive explanation and points the investigation back
at the bring-up hardware path — which is consistent with the 100 ms timing, with
the IPA/watchdog interaction already documented in
`docs/watchdog-ipa-lte-wip/`, and with the fact that `cellid` mode (which uses
the LOC service but not the RF measurement engine) is safe while `standalone`
is not.

The one genuinely new capability from this work is that the port now has the
XTRA path stock has, which is worth having on its own merits for
time-to-first-fix if the engine is ever made safe to start.

---

## 7. Hand-off

### What was NOT done

**No positioning session was started by this work.** No `QMI_LOC_START`, no
`--loc-start`, no `standalone` session, no `cellid` session. Nothing was
written to any block device or partition. The boot image was not touched.

The two resets that occurred during this session (22:14:29 and 22:18:17) were
another agent's `BISECT-R6plus-standalone-*` runs, identified from their own
`/dev/kmsg` markers and their `rhodep-gnss: bisect run: N opmode=standalone`
log lines.

### State the engine is now in

| item | value | how it got there |
|---|---|---|
| predicted orbits | loaded, valid from `2026-09-05T23:00:00Z` for 168 h | injected by `rhodep-xtra` |
| UTC time | injected, NTP-synced, uncertainty 1000 ms | `rhodep-xtra` |
| coarse position | `-32.9542, -60.6443`, ±50 m | `rhodep-xtra`, from the wifi source |
| engine lock | **`none`** (unlocked) — verified by raw TLV `0x12 = 0` | restored after the lock-TLV experiment |
| operation mode | `standalone` — **unchanged**, this is the pre-existing configured default, not a session | not touched |
| NMEA types | `0x7fff` — unchanged | not touched |
| SUPL server | `supl.google.com:7275` — unchanged, factory value | not touched |

The almanac is persistent and self-maintaining: `rhodep-xtra.timer` is enabled
and refreshes it 3 min after boot and every 6 h. It will still be valid until
**2026-09-12T23:00:00Z** even if the timer never runs again.

### Confirming the state before a test

Read-only, safe to run at any time, cannot start the engine:

```sh
sudo /usr/local/sbin/rhodep-xtra status
```

Expect `valid for : 168 hours` and a non-zero "hours remaining". If it reads
`1980-01-06T00:00:00Z` / `0 hours`, the almanac has been deleted (something ran
`--loc-delete-assistance-data`) — put it back with:

```sh
sudo /usr/local/sbin/rhodep-xtra inject
```

which is also safe and starts nothing. Re-run it immediately before any test if
you want time and coarse position to be fresh as well as the almanac; all three
are injected together.

### The test another agent should run

The open question this leaves is narrow: *does a fully assisted engine take a
different bring-up path than an unassisted one?* Based on section 6 the answer
is very likely no — it has in fact already been observed resetting while fully
assisted — but if it is to be tested deliberately, the run is:

```sh
# 1. assert the assistance is present (read-only, starts nothing)
sudo /usr/local/sbin/rhodep-xtra status

# 2. refresh time + position so they are seconds old, not minutes
sudo /usr/local/sbin/rhodep-xtra inject

# 3. mark the attempt so it can be recovered from ramoops after the reset
sudo sh -c 'echo XTRA-ASSISTED-standalone-$(date +%s) > /dev/kmsg'

# 4. THE PART THIS INVESTIGATION DID NOT RUN, and which resets the phone:
#    a standalone session. Either the daemon, which has the three
#    operation-mode gates (--operation-mode is hidden and refuses anything but
#    'cellid' without the second flag; SAFE_MODE = "cellid"):
sudo /usr/local/sbin/rhodep-gnss-daemon --source qmi-cellid \
     --operation-mode standalone --i-know-this-reboots-the-phone
#    ...or the bare bisect harness, which takes positional args
#    (duration, event-mask groups, opmode=...):
sudo scripts/rhodep-gnss-test.py 120 min,engine opmode=standalone

# 5. after the phone comes back
sudo grep -ah 'XTRA-ASSISTED\|rhodep-gnss' /var/lib/systemd/pstore/console-ramoops-* \
    | grep -vE 'alive [0-9]+ ms' | tail -30
```

The prediction: it resets in ~100 ms exactly as before, and the almanac is
still valid afterwards (it survived two watchdog resets already). If it does
**not** reset, that is a genuinely important result and the assistance state is
the variable.

Note that step 4 is what the standing instruction "do not run a standalone
session" prohibits, and it is left to whoever owns that.

### Files added or changed

```
userspace/gnss/rhodep-xtra                                       new, the tool
userspace/gnss/install.sh                                        installs + self-tests it
userspace/gnss/README.md                                         new XTRA section
packages/rhodep-gnss/usr/local/sbin/rhodep-xtra                  packaged copy
packages/rhodep-gnss/usr/lib/systemd/system/rhodep-xtra.service  new
packages/rhodep-gnss/usr/lib/systemd/system/rhodep-xtra.timer    new
packages/rhodep-gnss/DEBIAN/{control,postinst,prerm,postrm}      v5, enable the timer
packages/rhodep-gnss/usr/share/doc/rhodep-gnss/README            new XTRA section
rhodep-gnss_5_arm64.deb                                          built, installed, verified
postmarketos/device-motorola-rhodep/rmtfs-rhodep.conf            CORRECTED: dropped -r
packages/rhodep-modem-support/.../99-rhodep-fsg-alias.rules      verified; slot caveat documented
docs/interconnect-sm6375-wip/EFS-AND-XTRA.md                     this file
```

`rhodep-gnss` v5 is installed on the phone and `dpkg -l` shows `ii`.
`rhodep-xtra self-test` is 33 offline checks and is run by `install.sh` and
by hand; `systemd-analyze verify` passes on both units.

### Loose end worth fixing upstream

libqmi's `data/qmi-service-loc.json` has the wrong TLV for
`QMI_LOC_GET_ENGINE_LOCK` for this firmware generation (`0x10`/4 bytes vs the
`0x12`/8 bytes actually sent), and `qmicli-loc.c` reads the indication status
without checking it, so any failure is reported as the same misleading
`missing`. Both are worth a bug report; neither affects this port beyond the
cosmetic.
