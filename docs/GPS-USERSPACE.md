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

Package: `rhodep-gnss`, version 3, installed and verified. Sources in
`userspace/gnss/`, packaging in `packages/rhodep-gnss/`, built by
`scripts/build-support-debs.sh`. `userspace/gnss/README.md` is the operator
manual; this file is the write-up of what was found and why the design is what
it is.

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

	sudo apt-get install ./rhodep-gnss_3_arm64.deb
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

	rhodep-gnss-daemon --self-test        # 90 checks, no hardware, no network
	rhodep-cell-db     self-test          # 29 checks

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
