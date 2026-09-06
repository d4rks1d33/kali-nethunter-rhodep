# Location without satellites: what works on this phone today

**The phone reports where it is. Nothing in this document involves a satellite.**

The satellite path is unusable here and is not going to be fixed from userspace:
starting a QMI LOC session in `standalone`, `default`, `msa` or `msb` brings up
the modem's GNSS measurement engine and the SoC watchdog-resets inside 100 ms,
reproducibly, with no panic and no oops. That is
[`interconnect-sm6375-wip/GNSS-SM6375.md`](interconnect-sm6375-wip/GNSS-SM6375.md)
and it is a hardware/firmware bug in the modem's positioning path.

What this document covers is the other half: a daemon that obtains a position
from sources that do not need the measurement engine, and republishes it as
ordinary NMEA so that gpsd, geoclue and everything above them work unmodified.

Verified live on 2026-09-05 in Rosario, Argentina, on
`kali-boot-rhodep-clean-20260903.img`, with an **unprovisioned SIM** in a modem
that never registers:

	$ gpspipe -w | grep TPV
	{"class":"TPV","mode":2,"lat":-32.954171667,"lon":-60.644348333,"eph":142.5}

	$ cgps -s
	2D FIX

	geoclue reports the same location over D-Bus.

`mode:2` is a 2D fix. `eph` is gpsd's estimated horizontal position error, in
metres, derived from the HDOP the daemon emits — see "Not over-claiming
accuracy", which is the part of this that took the most care.

Package: `rhodep-gnss`, version 5, installed and verified. Sources in
`userspace/gnss/`, packaging in `packages/rhodep-gnss/`, built by
`scripts/build-support-debs.sh`. `userspace/gnss/README.md` is the operator
manual; this file is the write-up of what was found and why the design is what
it is.

This document has three parts. The first, and longest, is the location that
works today — the four sources, how they are wired into gpsd and geoclue, the
safety design, the provider findings, and the accuracy work. The second is the
supporting machinery the package ships around it: the predicted-orbit
downloader, the D-Bus deny that keeps ModemManager from resetting the phone, and
the apt/dpkg/immutable protection that keeps the whole package from being
undone by an upgrade. The third is the satellite fix itself — why it does not
work, exactly how far the investigation got, and what would, in theory, fix it,
kept honest about the fact that none of those has been tried.

---

## The four sources

`rhodep-gnss-daemon --source=` selects where the position comes from. The
shipped unit runs `auto`.

| `--source` | needs a SIM | QMI service opened | measured here |
| --- | --- | --- | --- |
| `wifi` | no | **none at all** | **22-23 m** |
| `cell` | no | NAS (read-only) | **250 m** |
| `qmi-cellid` | yes, provisioned | LOC, pinned to `cellid` | unavailable — see below |
| `auto` | no | NAS, and LOC only once registered | 22 m via wifi |

**`wifi`** scans the access points in range and asks an Ichnaea-compatible
geolocation service where those BSSIDs are. It touches no modem, no QMI service
and no GNSS engine, so it cannot trip the reset even in principle.

**`cell`** reads the identities of the cells the modem can hear over QMI **NAS**
— a read-only service, see §B below — and resolves them, first against a local
OpenCelliD/MLS extract and then against the same geolocation services.

**`qmi-cellid`** is the modem's own LOC service, pinned to operation mode
`cellid`. This is the mode the bisection in `GNSS-SM6375.md` found survives: it
runs the session state machine without ever starting the measurement engine. It
only yields a position once the modem has a **serving cell it can resolve
itself**, which in practice needs a provisioned SIM. On this device it therefore
has nothing to say — see §B.

**`auto`** chains them: `qmi-cellid` while the modem is registered, otherwise
`wifi`, otherwise `cell`. The ordering is by how directly the answer is tied to
where the phone physically is — the modem's own fix, then several access points
tens of metres away, then one tower up to kilometres away whose surveyed
position is not yours.

## How it is wired in

The daemon synthesizes checksummed `$GPGGA`, `$GPRMC`, `$GPGSA` and `$GPGST` at
1 Hz from whatever position it holds, and publishes them twice:

	/run/gnss-share.sock      UNIX, mode 0660, group geoclue, multi-client fan-out
	tcp://127.0.0.1:2948

**The TCP listener exists because gpsd cannot read a UNIX socket.** geoclue's
`[network-nmea]` source can and wants one; gpsd's `DEVICES=` takes `tcp://` but
not a UNIX path. Rather than pick one consumer, both are served from the same
fix, with fan-out so several clients can attach at once.

gpsd, through a systemd drop-in
(`usr/lib/systemd/system/gpsd.service.d/10-rhodep-gnss.conf`):

	DEVICES="tcp://127.0.0.1:2948"
	GPSD_OPTIONS="-n"

`-n` makes gpsd poll the source without waiting for a client to connect, which
matters because nothing here is a real serial GPS that would otherwise be left
powered up for nothing.

geoclue, through `/etc/geoclue/conf.d/20-rhodep-wifi.conf`:

	[wifi]    enable=true
	[network-nmea]  enable=true, nmea-socket=/run/gnss-share.sock
	[modem-gps]     enable=false
	[ip]            enable=false

`[modem-gps]` is off **on purpose and it is not cosmetic**: it asks
ModemManager to enable location, ModemManager enables GNSS unassisted, and an
unassisted GNSS session on this SoC power-cycles the phone. Leaving that source
enabled is a loaded gun pointed at anything that asks geoclue for a position.

`[ip]` is off because it resolves to whatever city the ISP peers in — about
25 km of error here — and geoclue would hand it to an application as a
plausible-looking answer whenever the better sources are unavailable, which is
exactly the failure this whole exercise is meant to remove. See "The considerIp
trap".

Command-line tools have no `.desktop` file and no agent, so the config also
marks `rhodep-locate`, `gpspipe`, `cgps`, `where-am-i` and the demo agent as
`system=true`; without that geoclue waits for an agent authorization that never
arrives and the client hangs until it times out.

---

## A. The safety design: three gates before `QMI_LOC_START`

This is the part that matters most, because the failure mode is a reboot.

The daemon will not send `QMI_LOC_START` unless the modem has confirmed the
operation mode is `cellid` **three separate times**:

1. `QMI_LOC_SET_OPERATION_MODE` is **acked**;
2. the **set-operation-mode indication** comes back `SUCCESS`;
3. `QMI_LOC_GET_OPERATION_MODE` **reads the mode back** as `cellid`.

If any gate fails it logs `REFUSING TO START A GNSS SESSION` and exits 1, with
`QMI_LOC_START` never sent. All three failure paths are testable without a
misbehaving modem:

	rhodep-gnss-daemon --test-gate-failure=ack        --socket '' --tcp 2960
	rhodep-gnss-daemon --test-gate-failure=indication --socket '' --tcp 2961
	rhodep-gnss-daemon --test-gate-failure=readback   --socket '' --tcp 2962

**Gate 2 is the one that is easy to miss, and its absence is what turns a failed
set into a reboot.** QMI LOC acknowledges the *request* and reports the actual
outcome in a separate indication. "The call returned without an error" therefore
means only that the request was accepted for processing. A mode that is acked
and never applied leaves the engine in `standalone`.

**The reference bisection script does not have gate 2**, and this is recorded
rather than fixed because the two programs want opposite things.
`scripts/rhodep-gnss-test.py` handles a failed set like this:

	log("set_operation_mode(%s) failed: %s -- starting anyway" % (OPMODE, e))

It starts the session anyway — which is exactly the path that leaves the engine
in `standalone` and takes the phone down. For a tool whose purpose is to cause
the reset on demand that is defensible; for a daemon that runs at boot it is
not. Anyone reusing that script as a template for anything long-running must add
the indication gate first.

There is a fourth, coarser gate above all of these, and it is the one doing most
of the work: **`auto` does not open the LOC service at all unless ModemManager
reports the modem as registered**, which is a plain D-Bus property read that goes
nowhere near the location engine. With an unprovisioned SIM no LOC client is ever
allocated, so the reset is out of reach rather than merely guarded against.
`--no-cell` additionally drops the NAS client, for a daemon that provably speaks
no QMI at all outside `--source=qmi-cellid`.

`--i-know-this-reboots-the-phone` unlocks `--operation-mode` for the day the
underlying bug is fixed. It is not something to run on hardware you want to keep
up.

---

## B. Cell identities are available with no network registration

This is a useful fact that was not recorded anywhere in this repo before, and it
is what makes the `cell` source work on a phone with a dead SIM.

State of the modem while all of the below was measured:

	registration: searching
	network rejection error: imsi-unknown-in-hlr
	Detailed status: Status: 'limited'

That is a modem that is not on any network and will not be. It still knows which
cells are around it:

	$ sudo qmicli -p -d qrtr://0 --nas-get-cell-location-info
	GERAN Info
		Cell ID: '60858'
		PLMN: '72234'
		Location Area Code: '420'
		GERAN Absolute RF Channel Number: '179'
		Base Station Identity Code: '19'
		RX Level: -97 dBm > level > -96 dBm ('14')

**Why this works.** The modem camps for limited service and decodes the BCCH
without attaching. Cell identity is *broadcast* — it is what the tower tells
everyone in range — and is not subscriber-specific, so nothing about it requires
authentication, an IMSI the HLR recognises, or an attach.

**Why ModemManager shows nothing.** `mmcli --location-get` returns no 3GPP
location here, and that is correct behaviour rather than a bug: MM only
populates LAC/CI from a **registered serving cell**. Reading NAS directly with
`qmicli` bypasses that policy. There is nothing to fix in MM; it is answering a
different question.

**It is intermittent, and the reason is benign.** The modem is in a search loop,
so ten polls in a row return anything from a full identity to `NoNetworkFound`,
to PLMN `00-00`, to a neighbour whose ARFCN and signal are known but whose Cell
ID reads `unavailable` — a neighbour is measured seconds before its BCCH is
read. Hit rates observed here ranged from **2 of 10** while the modem was cycling
bands up to 10 of 10 once it settled. The daemon therefore polls on its own
timer (`--cell-poll`, 15 s), caches the last good answer *with its age*, and the
provider query uses whatever is newer than `--cell-max-age`. Anything with cell
id 0, LAC 0 or an all-ones field is discarded.

**Both reads are safe.** `--nas-get-cell-location-info` and
`--nas-get-serving-system` report measurements the radio already made while
looking for a network. They are a different QMI service from LOC, they start
nothing, and several hundred of them during this work left `uptime` untouched.
This is the whole basis on which the `cell` source is claimed not to be able to
trip the reset.

---

## Provider findings

### BeaconDB has no coverage in this region

BeaconDB is the right default and the daemon queries it first: public domain,
opt-in collection, no API key, and what geoclue 2.8 now compiles in since the
Mozilla Location Service shut down for good in 2024.

**Measured 2026-09-05 in Rosario/AR: it answers `HTTP 404, "no location could be
estimated"` for every WiFi query and every cell query made here.** That is not a
misconfiguration and the URL is not wrong — it is a young database and this area
simply has not been surveyed into it. It is the entire reason the daemon has a
fallback chain at all, and the reason the fallback lives in the daemon rather
than in geoclue: geoclue's `[wifi]` source takes exactly one URL and cannot fail
over.

`api.positon.xyz` resolves both WiFi scans and cell identities here.

| provider | key | WiFi | GSM 722-34 LAC 420 CID 60858 |
| --- | --- | --- | --- |
| offline OpenCelliD + MLS | no | n/a | **miss** — not in either dump |
| `api.beacondb.net` | no | **404** | **404** |
| `api.positon.xyz` | shipped | **22 m** | **-32.952961, -60.643772, 250 m** |
| OpenCelliD API | **yes, human signup** | n/a | *not queried* |

### The `considerIp` trap

Every request the daemon sends carries `"considerIp": false`. **Leave it out and
a BeaconDB request that has no data returns `HTTP 200` with real-looking
coordinates and one extra field:**

	"fallback": "ipf"

That is DB-IP geolocation of the **querying host's IP address**. It is not
device data of any kind, it was about 25 km wrong here, and behind a VPN it
would be in a different country. Nothing else in the reply distinguishes it from
a genuine fix — a 200 with plausible coordinates looks exactly like success, and
once it has been laundered into a `$GPGGA` sentence nothing downstream can
contradict it.

The daemon sends `considerIp: false` always, and additionally refuses any reply
flagged `ipf` or `lacf` (LAC-centroid) regardless, and anything coarser than
`--max-accuracy` (5000 m). The same trap is why geoclue's `[ip]` source is
disabled.

### OpenCelliD's API needs a human, and two keyless mirrors were used instead

`https://opencellid.org/cell/get` with no key returns **HTTP 200** and

	{"error":"API Key not known: ","code":2}

and the same with an invalid one. There is no anonymous mode; registration is a
web form behind reCAPTCHA wanting a name and an e-mail address. **No token is
shipped and none was invented.** Setting `TOKEN=` in
`/etc/default/rhodep-cell-db` switches `rhodep-cell-db` to the official current
per-country export.

Without one, two keyless Internet Archive mirrors were used:

| source | date | licence | cells for MCC 722 |
| --- | --- | --- | --- |
| OpenCelliD full export | 2024-05-19 | CC-BY-SA 4.0 | 312 146 |
| Mozilla Location Service final export | 2024-03-27 | public domain | +340 759 |

Merged: **652 905 cells, 29.2 MiB of SQLite.**

### Why the offline database misses this phone's own cell

The 2024 export has **4572 cells within 3 km of the test location** — the area
is very well surveyed. It still does not contain the serving cell, and the
reason is not staleness in the usual sense: **the operator has renumbered its
GERAN location areas.** The local GSM cells for 722-34 sit under **LAC 400** in
both dumps, while the modem today reports **LAC 420 CID 60858** (and a neighbour
at LAC 421 CID 40012).

CID 60858 appears nowhere under MCC 722 in either dump. The only 60858 in
Argentina belongs to operator 722-**07**, about 800 km away in Neuquén — which is
the whole argument for the rule the lookup enforces: **match the full
(MCC, MNC, LAC, CID) tuple, never the cell id alone.** There is deliberately no
"nearest cell in this LAC" mode either; that is the `lacf` answer the daemon
refuses from the network providers, and it is no more honest from a local file.

A stale dump does not degrade into a worse fix. It simply does not contain the
key, and the chain moves on. No prebuilt database is shipped.

---

## Not over-claiming accuracy

A network fix has no satellite geometry, so whatever goes into the HDOP field is
a fabrication. The only question is which fabrication is least dishonest.

geoclue 2.8.1 (Kali's 2.8.1-1) does not read an accuracy in metres from the
stream at all. It buckets HDOP, measured by feeding it synthetic GGA at fixed
HDOP:

| HDOP | ≤1 | ≤2 | ≤5 | ≤10 | ≤20 | >20 |
| --- | --- | --- | --- | --- | --- | --- |
| geoclue reports | 0 m | 1 m | 3 m | 50 m | 100 m | **300 m** |

Two consequences, both of which change the design:

**The conventional "5 m per DOP" conversion over-claims by about 7x here.** A
22 m fix becomes HDOP 4.4, which lands in the *good* band, and geoclue announces
**3 m**. So the daemon does the opposite: it picks the **lowest band whose
implied error is still at least the true accuracy**, and emits an HDOP from
inside that band. A 22 m fix is published as a 50 m claim, not a 3 m one.

**The table saturates at 300 m.** There is no HDOP — no value at all — that
tells geoclue "1200 m". `$GPGST` carries the real accuracy in metres and gpsd
reads it, but **geoclue never reads `$GPGST`**: `strings /usr/libexec/geoclue`
contains `$GPGGA` and nothing else. So a coarse cell fix would reach geoclue as
a 300 m claim with nothing in the stream able to contradict it. The daemon
therefore **refuses to publish any fix coarser than `--max-nmea-accuracy`**,
which defaults to exactly that 300 m ceiling, and logs why once.
`--max-nmea-accuracy 0` disables the check for anyone who would rather have a
coarse position than none.

The invariant, checked directly by `--self-test`: **for every published fix, the
accuracy a consumer infers from our HDOP is at least the accuracy the source
reported.** Measured here:

(One WiFi scan; the provider's claimed accuracy moves by a metre or two between
scans, which is why the daemon derives the HDOP per fix rather than pinning it.)

| | WiFi fix | cell fix |
| --- | --- | --- |
| truth (Positon) | 23 m | 250 m |
| `$GPGST` | 23 m — exact | 250 m — exact |
| geoclue | 50 m | 300 m |
| gpsd `TPV.eph` | 142 m | 475 m |
| `cgps` 2D Err | 467 ft | 1558 ft |

Every number in every column is ≥ the truth. Nothing here reports a position as
better than it is; several things report it as worse, which is the correct
direction to be wrong in.

### Why the sentences are synthesized at all

In `cellid` mode the modem's own NMEA output is a bare `$PSTIS,*61` once a
second, and the network sources produce no NMEA whatsoever. Neither gpsd nor
geoclue can do anything with that.

Two details that cost time and are easy to get wrong:

* **GGA fix quality is 1 when there is a position.** NMEA 0183 has no code for a
  network fix, and gpsd reads quality 0 as "no fix at all". The satellite count
  stays 0, which is honest and which nothing rejects.
* **Sentences carry the time they are sent, not when the fix was obtained** —
  except for QMI positions under two seconds old, which keep the modem's own
  UTC. Rebroadcasting a network fix at 1 Hz under its original timestamp freezes
  the clock in the stream, and gpsd reads that as one endlessly repeated epoch
  and stops propagating the fix.

With no position at all the sentences are still emitted, RMC status `V` and GGA
quality 0, so gpsd and geoclue stay attached to a live source instead of a
silent socket.

---

## Using it

	sudo apt-get install ./rhodep-gnss_5_arm64.deb
	sudo systemctl enable --now rhodep-gnss
	sudo systemctl restart gpsd

	gpspipe -w                 # gpsd JSON
	cgps -s                    # curses view
	where-am-i                 # geoclue, over D-Bus
	socat -u UNIX-CONNECT:/run/gnss-share.sock -
	nc 127.0.0.1 2948

One-shot queries, which touch nothing persistent:

	rhodep-gnss-daemon --locate-once      # wifi
	sudo /usr/local/sbin/rhodep-gnss-daemon --cells-once   # cell

	rhodep-gnss-daemon --self-test        # 112 checks, no hardware, no network
	rhodep-cell-db     self-test          # 29 checks
	rhodep-xtra        self-test          # 33 checks, incl. the QMI allow-list

The offline cell database is optional and is built by hand — the timer ships
**disabled**, because a refresh streams over a gigabyte and the archived sources
are static snapshots anyway:

	sudo /usr/local/sbin/rhodep-cell-db build --mcc 722 --source ocid,mls

## What you actually get here

WiFi, no SIM, no GNSS:

	$ rhodep-gnss-daemon --locate-once
	latitude   -32.9541930
	longitude  -60.6442380
	accuracy   22 m
	provider   api.positon.xyz

	$ gpspipe -w | grep TPV
	{"class":"TPV","mode":2,"lat":-32.954171667,"lon":-60.644348333,"eph":142.5}

	$ cgps -s
	2D FIX

Cell tower, same phone, with the WiFi path disabled:

	$ rhodep-gnss-daemon --cells-once
	1 cell(s) with a decoded identity after 2 poll(s)
	  gsm 722-34 LAC 420 CID 60858 arfcn 179 bsic 19 -93 dBm (serving)
	provider chain:
	  - opencellid-offline: none of the 1 cell(s) are in the dump
	  - api.beacondb.net: HTTP 404 (no coverage for these 1 cell(s))
	  - api.positon.xyz: hit
	latitude   -32.9529610
	longitude  -60.6437720
	accuracy   250 m

The cell answer is 137 m from the WiFi fix taken at the same moment, well inside
its own 250 m claim.

`qmi-cellid` has nothing to say on this device and `auto` never asks it: the SIM
is unprovisioned, so the modem reports `registration: searching` with
`imsi-unknown-in-hlr` and every LOC position report would come back
`session status = general-failure`. Put in a SIM the network accepts and the
daemon switches over on its own.

---

# Part two: the machinery around it

## `rhodep-xtra` — predicted orbits, the job stock Android gives xtra-daemon

The modem's positioning stack expects predicted-orbit assistance (Qualcomm's
gpsOneXTRA): a small almanac of where the satellites will be, fetched from a CDN,
so a cold start does not have to decode the whole thing off the air. On stock
Android a `xtra-daemon` process fetches and injects it. This port never had one,
so the modem's almanac read the GPS epoch, meaning it had never been loaded:

	$ qmicli -d qrtr://0 --loc-get-predicted-orbits-data-validity
	1980-01-06T00:00:00Z, valid for 0 hours

`rhodep-xtra` is that daemon. It fetches the 40 kB `xtra3grej.bin` from the
three CDN endpoints the modem names itself (`path1..3.xtracloud.net`), and
injects it over QMI LOC together with UTC time and a coarse position taken from
the WiFi source. Its `systemd` timer **is** enabled — 40 kB every six hours —
because injection is what A-GPS does before a fix and it cannot start the
measurement engine:

	sudo /usr/local/sbin/rhodep-xtra status    # read-only inventory of stored LOC state
	sudo /usr/local/sbin/rhodep-xtra inject    # fetch + inject orbits, time, position

`rhodep-xtra status` is also the only way to read this port's stored LOC state
— including the SUPL server — because `qmicli` cannot print it.

Two firmware quirks were found building this and are worth knowing:

* `InjectPredictedOrbitsData` (`0x0035`) returns `NotSupported` on this modem;
  the working message is `InjectXtraData` (`0x00A7`). `rhodep-xtra` probes and
  falls back.
* `InjectUtcTime` (`0x0038`) **requires** the Time Source TLV `0x10` on this
  firmware, even though libqmi marks it optional. That is why
  `qmicli --loc-inject-time` works and a hand-rolled two-TLV message does not.

**One hazard this creates, and it is important.** Injecting UTC time is one of
the two things that arm the satellite reset (see part three). `rhodep-xtra`
injecting time is harmless *only* because `rhodep-gnss.service` runs in `auto`,
which stays in `cellid`/WiFi/cell and never brings the measurement engine up.
Anyone enabling a `standalone` path must stop `rhodep-xtra.timer` first, or the
next `QMI_LOC_START` after a time injection resets the phone. This is why every
reproducer run in `GNSS-SM6375.md` disables that timer before starting.

## The D-Bus deny: ModemManager is the other way into the reset

The daemon's own gates (§A) only guard the path *it* takes. ModemManager is a
second, independent way into the same measurement engine, and it has no such
guard: on this modem its Location interface advertises `gps-raw`, `gps-nmea`,
`agps-msa` and `agps-msb`, any of which resets the phone, and any of which is one
unprivileged `mmcli` or D-Bus call away — a settings toggle, a desktop location
switch, geoclue's own `modem-gps` source.

So the package ships `zz-rhodep-no-modem-gnss.conf`, a **mandatory** D-Bus deny
of `Location.Setup`, `Location.InjectAssistanceData` and
`Location.SetSuplServer`. Mandatory because D-Bus applies
`default < user < mandatory` and ModemManager's own policy allows root
everything — the accident being prevented is a `sudo mmcli` one. `GetLocation`
and `SetGpsRefreshRate` stay allowed; nothing else in ModemManager is touched.

	$ mmcli -m any --location-enable-gps-nmea
	error: ... AccessDenied: Rejected send message ... member="Setup"

There is one more piece of ModemManager hygiene here. `libqmi` forks a shared
`qmi-proxy` on demand, and it lands in the cgroup of whichever client started it
first. That used to be `rhodep-gnss.service`, so restarting the location daemon
killed the proxy ModemManager was talking through, which re-enumerated the modem
and made ModemManager re-probe the LOC service for SUPL and XTRA every time — a
latent way for the phone to reset itself with no user action. The package ships
`rhodep-qmi-proxy.service` to own the proxy so that no longer happens.

## Keeping it from being undone: holds, dpkg protection, immutability

`rhodep-gnss` is a `.deb` built in this repo. It exists only in
`/var/lib/dpkg/status` — there is no repository and no cached archive, so apt
cannot re-fetch a file that gets removed. That is the same case as
`rhodep-battery-jeita`, `rhodep-modem-support` and `rhodep-usb-otg`, and it gets
the same three protection layers, applied by `userspace/apt/apply-holds.sh`:

* **apt hold** — `rhodep-gnss` is in `userspace/apt/apt-holds.txt`, so an upgrade
  cannot silently replace it.
* **dpkg `Protected`** — set from that same list, so a raw `dpkg -P` cannot walk
  past the hold and remove the package.
* **immutable + snapshot** — every binary and config file is `chattr +i` and
  snapshotted, and `rhodep-holds-enforce` restores anything that goes missing.
  `systemctl disable` still works on an undeletable file, so the enabled state
  of `rhodep-gnss.service`, `rhodep-qmi-proxy.service` and `rhodep-xtra.timer` is
  registered too.

The file most worth this treatment is `zz-rhodep-no-modem-gnss.conf`: without
it, an upgrade or an `rm` re-opens the ModemManager path that resets the phone.

Deliberately left out: `rhodep-cell-db.timer`, which the package ships disabled
because a database refresh streams over a gigabyte, and `gpsd.service` /
`gpsd.socket`, which belong to the gpsd package and can be re-enabled from it.

---

# Part three: the satellite fix

## What this does not do, and what is not proven

* **It is not a GPS.** There is no satellite involved anywhere. A fix indoors is
  as good as a fix outdoors, and both are as good as the survey data for the
  access points around you, which is not under anyone's control here.
* **It depends on a third-party service.** With no WiFi coverage in the
  geolocation database and no cell in the offline dump, it produces nothing.
  That is the honest failure and it is preferred over the `ipf` answer, which is
  the dishonest one.
* `qmi-cellid` has **never been exercised end to end on this device**, because
  no SIM here registers. The three gates are unit-tested through
  `--test-gate-failure`; the success path with a real registered modem is
  untested.
* The accuracy table above is one location on one day. The band mapping is a
  property of geoclue 2.8.1 and will need re-measuring if that changes.
* Nothing is ever submitted back to any database. Submission would need a
  trustworthy fix to tie the observations to, and the only fix this phone can
  produce is itself derived from a geolocation service — feeding that back would
  launder a guess into the database as ground truth. `submit-data=false`, and
  SSIDs ending in `_nomap` are excluded from queries as well.

## Why the satellite fix resets the phone

Asking the modem for a real satellite fix — a QMI LOC session in `standalone`,
`default`, `msa` or `msb` — brings up the modem's GNSS measurement engine, and
the whole SoC deasserts PS_HOLD and power-cycles about 100 ms after
`QMI_LOC_START`. Next boot reports `androidboot.bootreason=watchdog`. There is no
panic, no oops, no kernel log line: an instrumented kernel showed all eight CPU
cores sitting idle at the instant it happened, so the application processor is
not the one crashing. Stock Android runs the same receiver on this same phone
with byte-identical modem firmware, so whatever is missing is on the Linux side
and, in principle, findable.

The investigation is in
[`interconnect-sm6375-wip/GNSS-SM6375.md`](interconnect-sm6375-wip/GNSS-SM6375.md)
and it is long. The short version of where it got to:

* The failure is not steady-state operation. It is a single firmware step: the
  measurement engine being brought up. `cellid`, which runs the whole session
  state machine but never starts that engine, survives indefinitely. This is
  what makes the `qmi-cellid` source above safe.
* There are exactly two ways to put the engine into the state where
  `QMI_LOC_START` is fatal: a non-background `powerMode` (0, 1 or 2), or giving
  the modem valid UTC time. `powerMode 3` (background) with no time injected runs
  309 fix cycles and never resets — and never tracks a satellite either, because
  without time the firmware skips the NAV-core reset that both starts the
  receiver and kills the SoC. Two runs one word apart, `preinject=time` versus
  `preinject=position`, land on opposite sides of the reset.
* Roughly twenty application-processor hypotheses have been tested on the device
  and eliminated: power-supply capacity, an external low-noise amplifier,
  dynamic power optimisation, memory sharing with the modem, the NAV
  virtual-machine memory identifier, the event registration mask, constellation
  and band restriction, assistance data injected in advance, specific clocks and
  rails. Each is written up with how it was ruled out.
* The NAV-core reset path itself
  ([`NAV-CORE-RESET.md`](interconnect-sm6375-wip/NAV-CORE-RESET.md)) turns out to
  be entirely modem-owned: every register the sequence touches is behind the
  TrustZone boundary, the NAV clock controller lives in the modem, and the bus
  identifiers that looked promising are from a generic Qualcomm header, not this
  chip. The application-processor surface for this bug is essentially exhausted.

## What would, in theory, fix it — none of it tried

Everything below is a hypothesis. The application-processor side has come back
negative enough times that the honest position is "the answer is probably in the
secure world or the modem image, where this port cannot currently see", not "try
X and it will work". These are the leads that remain, ranked by how much they
would teach, not by how likely they are:

1. **Modem-side visibility, and this is the real one.** DIAG/QXDM, or a modem
   ramdump taken after the reset. Every attempt so far to see *why* the modem
   pulls PS_HOLD down has come back empty because nothing on this port records
   modem state across the power cycle. Without this, everything else is guessing
   at a processor nobody can observe — and the last session's four fresh
   application-processor hypotheses were exactly that guess, and all four were
   negative. Getting DIAG working is the highest-value item and it is not a
   position fix, it is instrumentation.

2. **Register a memory-dump table with TrustZone.** Stock's `msm-imem` node has a
   `mem_dump_table@10` child that this port does not declare. Its driver
   publishes a table of DRAM regions to the secure firmware so that firmware can
   record what happened before a reset — which is exactly why every post-mortem
   capture on this port has been empty. Standing this up would not fix the reset,
   but it might make the reset *explain itself*, which is the same value as (1)
   by a different route. Three separate attempts to build the module for this
   were blocked by an unrelated tooling issue and it has never actually run.

3. **The `pmr735a_l1` operating mode.** Holding this rail on at 600 mV was tested
   and did not help — but the 62 mA *load* vote that would move the LDO out of
   low-power mode almost certainly never reached RPM, because mainline needs
   `regulator-allow-set-load` in the device tree and this port does not have it.
   So the rail's *mode*, as opposed to its voltage, is genuinely untested.
   `kernel/patches/0121` adds exactly that. It needs a flashed boot image to
   test, which needs working fastboot over USB — this port's only recovery path,
   and not available from where the reproducer has been run.

4. **QDSS/CoreSight as a subsystem.** 110 device-tree nodes downstream, zero in
   mainline. The RPM-clock half of this was already covered by an earlier
   elimination, so what is left is the nodes themselves. Low confidence, high
   effort.

5. **The seven other `msm-imem` children** mainline does not declare
   (`restart_reason`, `dload_type`, `boot_stats`, and so on). Newly visible from
   the recovered stock tree, completely untested, and with nothing yet tying any
   of them to this bug — listed for completeness, not because there is a reason
   to expect it.

The recovered stock device tree that made items 2 and 5 visible is kept in
`backups/vendor-dt-rhodep-20260906/` (git-ignored — it is Motorola's), and its
own `README.md` records what it settled and what it opened.

**So: location works today, and it works well enough that the phone is genuinely
useful without a satellite. The satellite fix is a hardware/firmware
investigation that is not blocked on ideas so much as on the ability to see
inside the modem, and until that changes the four sources above are the answer.**
