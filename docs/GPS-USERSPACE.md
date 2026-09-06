# Location without satellites: what works on this phone today

**The phone reports where it is. Nothing in this document involves a satellite.**

The satellite path is unusable here and is not going to be fixed from userspace:
starting a QMI LOC session in `standalone` brings up the modem's GNSS
measurement engine and the SoC watchdog-resets inside 100 ms, reproducibly,
with no panic and no oops. `standalone` is the only whole-session operation
mode that has actually been run to a reset; `msa`, `msb`, `wwan` and `default`
were left alone and remain untested. That is
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
1 Hz — `--rate`, in Hz, default 1 — from whatever position it holds, and
publishes them twice:

	/run/gnss-share.sock      UNIX, mode 0660, group geoclue, multi-client fan-out
	tcp://127.0.0.1:2948

The tick emits nothing at all while no client is attached, so "at 1 Hz" means
"at 1 Hz whenever someone is listening". The TCP port is a property of the
shipped systemd unit rather than a daemon default: `--tcp` defaults to 0, which
is off, and the unit passes `--tcp 2948`.

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

	[wifi]          enable=false
	[network-nmea]  enable=true, nmea-socket=/run/gnss-share.sock
	[modem-gps]     enable=false
	[ip]            enable=false

`[wifi]` is off, and that is the important line in the file. MEASURED
2026-09-05 with it enabled and pointed at BeaconDB exactly as the file
documents, geoclue answered `accuracy=26000m desc='ipf fallback (from WiFi
data)'` — 4 km from where the phone actually was, at the same moment the daemon
was producing 22 m from the same neighbourhood. geoclue's own WiFi source
cannot send `"considerIp": false`, so when the service fails to match the
BSSIDs it geolocates the ISP instead and answers `HTTP 200` with a real-looking
position, and geoclue passes that on; see "The considerIp trap". It was also
*winning*, because a client that has not asked for EXACT accuracy never gets
`[network-nmea]` at all and this was then the only source left to it.

Nothing is lost by turning it off. The daemon scans the same neighbourhood and
asks the same services, but with `considerIp: false`, with a second provider to
fall back on where BeaconDB has no coverage, with `_nomap` access points
excluded and with `ipf`/`lacf` answers refused — a fallback chain geoclue
cannot express, since its `[wifi]` source takes exactly one URL and cannot fail
over. Turning it off also stops a second copy of the BSSID list leaving the
phone every minute. The URL the source should use if anyone re-enables it is
kept in the file as a comment.

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
marks `gpspipe`, `cgps`, `where-am-i`, `rhodep-locate` and the demo agent as
`system=true`; without that geoclue waits for an agent authorization that never
arrives and the client hangs until it times out. `rhodep-locate` is a stanza
for a client name this port does not currently ship — there is no such program
in the package or in this repo — so it authorizes nothing that exists.

---

## A. The safety design: three gates before `QMI_LOC_START`

This is the part that matters most, because the failure mode is a reboot.

The daemon will not send `QMI_LOC_START` unless the modem has confirmed the
operation mode is `cellid` **three separate times**:

1. `QMI_LOC_SET_OPERATION_MODE` is **acked**;
2. the **set-operation-mode indication** comes back `SUCCESS`;
3. `QMI_LOC_GET_OPERATION_MODE` **reads the mode back** as `cellid`.

Gates 1 and 2 are a conjunction and not a sequence, and the code deliberately
does not enforce an order between them. MEASURED on this modem, 2026-09-05: the
set-operation-mode **indication arrives before the `set_operation_mode`
response**, every time. This used to be written as a chain — ack callback, then
indication, then readback — and the chain therefore advanced past gate 2 before
gate 1 had returned a verdict at all, so it could have reached `QMI_LOC_START`
with a late ack failure still in flight. Each gate now records its own verdict,
`_after_set_mode()` advances only once both 1 and 2 have landed in whichever
order they arrive, gate 3 is strictly last, and all three verdicts are
re-asserted at the single point where `QMI_LOC_START` is sent. Verified live:
the journal prints `gate 2/3` before the ack.

If any gate fails it logs `REFUSING TO START A GNSS SESSION` and exits **3**,
with `QMI_LOC_START` never sent. Three rather than one because it is a decision
and not a fault: the modem will answer the same question the same way next time,
so restarting into it only asks it again. `rhodep-gnss.service` carries
`RestartPreventExitStatus=3` precisely so systemd can tell the two apart and
leave the unit down and visibly failed rather than looping. Exiting is the
default and not the only behaviour — `--keep-running-on-refusal` turns the
refusal into a retry loop that keeps publishing whatever the network sources can
find. All three failure paths are testable without a misbehaving modem:

	rhodep-gnss-daemon --source qmi-cellid --socket '' --tcp 2960 \
	                   --test-gate-failure=ack
	rhodep-gnss-daemon --source qmi-cellid --socket '' --tcp 2961 \
	                   --test-gate-failure=indication
	rhodep-gnss-daemon --source qmi-cellid --socket '' --tcp 2962 \
	                   --test-gate-failure=readback

**The `--source` matters and is not optional there.** The default is `auto`, and
`auto` on an unregistered modem never opens the LOC service, so the injected
fault is never reached: verified live, all three exit 0 having proved nothing.
With `--source qmi-cellid` all three take the real QMI path and exit 3.

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
allocated.

That gate is only as good as its reading of `MMModem3gppRegistrationState`, and
for a while it was not good enough. The allow-list accepted state 8
(emergency-only) and state 11 (attached-RLOS), because the comment above it
mis-mapped the enum. Both mean the network has not accepted the subscriber —
they are exactly where an unprovisioned or rejected SIM lands — so the one
configuration this phone is actually in was the one that could have slipped
through. That is fixed: the tuple is now `(1, 5, 6, 7, 9, 10)`, home and
roaming plus the SMS-only and CSFB-not-preferred variants of each, and the
states that do not qualify are logged by name, so `not registered: state 8
(emergency-only)` is the line that makes the regression obvious if it ever comes
back. Nothing is given up by excluding them: a modem in either state has no
serving cell it can resolve on its own, so `qmi-cellid` would have produced
nothing from it, while the `cell` source keeps working because it reads
identities the radio decoded while searching.

`--no-cell` drops the QMI NAS cell reader and nothing else. It does **not** stop
`--source=auto` from opening LOC and starting a `cellid` session once the modem
registers — an earlier version of this document claimed that it did, and that
was wrong. `--no-qmi` is the switch that does: it forces the LOC service never
to be opened whatever `--source` says, so no LOC client is allocated,
`QMI_LOC_START` is never built, and the three gates above are unreachable rather
than merely trusted. `--source=auto` degrades to wifi and cell;
`--source=qmi-cellid` is refused at argument parsing rather than silently
neutered.

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

**The read is safe.** `--nas-get-cell-location-info` is the only NAS message the
daemon ever sends, and it reports measurements the radio already made while
looking for a network. It is a different QMI service from LOC, it starts
nothing, and several hundred of them during this work left `uptime` untouched.
This is the whole basis on which the `cell` source is claimed not to be able to
trip the reset.

---

## Provider findings

### BeaconDB has no coverage in this region

BeaconDB is the right default and the daemon queries it first among the network
services: public domain, opt-in collection, no API key, and what geoclue 2.8 now
compiles in since the Mozilla Location Service shut down for good in 2024. For a
cell lookup it is second overall rather than first, because the offline
OpenCelliD/MLS database is asked ahead of it — free, instant, private, and it
works with the WAN down — as the table below shows.

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

By default every request the daemon sends carries `"considerIp": false`. The
payload is literally `{"considerIp": bool(self.allow_fallback)}`, so
`--allow-fallback` is the one thing that flips it to true. **Leave it out and
a BeaconDB request that has no data returns `HTTP 200` with real-looking
coordinates and one extra field:**

	"fallback": "ipf"

That is DB-IP geolocation of the **querying host's IP address**. It is not
device data of any kind, it was about 25 km wrong here, and behind a VPN it
would be in a different country. Nothing else in the reply distinguishes it from
a genuine fix — a 200 with plausible coordinates looks exactly like success, and
once it has been laundered into a `$GPGGA` sentence nothing downstream can
contradict it.

So the daemon sends `considerIp: false`, and additionally refuses any reply
flagged `ipf` or `lacf` (LAC-centroid). Both of those are defaults and not laws:
the refusal is guarded by `and not self.allow_fallback`, so `--allow-fallback`
sets `considerIp` true and drops the `ipf`/`lacf` refusal in the same move. A
`fallback` value that is neither `ipf` nor `lacf` is accepted and merely
annotated into the provider name, so it survives into the log rather than being
silently swallowed or silently trusted.

`--max-accuracy` (5000 m) refuses anything that coarse or worse — the test is
`>=`, so exactly 5000 m is refused too — but its scope is narrower than the flag
name suggests, in two ways worth stating. A reply that omits `accuracy`
altogether bypasses the limit entirely, because there is no number to compare.
And the check lives in the network client only: the offline cell database sits
ahead of it in the same chain and returns a range of its own, capped at 20 km,
which `--max-accuracy` never sees. Neither hole reaches a consumer, because the
300 m `--max-nmea-accuracy` publish gate described below catches both — but it
is the publish gate doing that work, not this one.

The same trap is why geoclue's `[ip]` source is disabled.

### OpenCelliD's API needs a human, and two keyless mirrors were used instead

`https://opencellid.org/cell/get` with no key returns **HTTP 200** and

	{"error":"API Key not known: ","code":2}

and the same with an invalid one. There is no anonymous mode; registration is a
web form behind reCAPTCHA wanting a name and an e-mail address. **No token is
shipped and none was invented.**

If you have one, `/etc/default/rhodep-cell-db` switches `rhodep-cell-db` to the
official current per-country export, which is far smaller than the mirror and
current rather than two years old. The variable is `TOKEN_ARG` and it holds the
**whole flag**, not the bare token:

	TOKEN_ARG="--token 0123456789abcdef0123456789abcdef01234567"

`rhodep-cell-db.service` appends it to `ExecStart` as an unquoted `$TOKEN_ARG`,
which systemd splits at whitespace and which expands to *zero* arguments when
the variable is empty or unset — so the default command line is byte-for-byte
what the unit ran before the variable existed. `${TOKEN_ARG}` would be wrong:
the braced form always yields exactly one argument, so an unset token would pass
an empty string and a set one would arrive as a single argv entry that argparse
rejects. An earlier version of this document said to set `TOKEN=`; nothing has
ever read that name, here or in any earlier version of the file.

Two things the token path requires, both recorded in that file. `MCC` must name
exactly one country, because the endpoint serves one MCC per request. And
`SOURCE` must be `ocid` alone, because a token overrides the source name
entirely, so `ocid,mls` with a token downloads the same official export twice
and merges it with itself. The token also ends up in the journal and in
`/proc/PID/cmdline` while the build runs, because `rhodep-cell-db` prints the
URL it is fetching.

Without a token, two keyless Internet Archive mirrors were used:

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
reported** — up to the ceiling, and only up to it. Above 300 m the HDOP mapping
cannot hold that and does not pretend to: `dop_for_accuracy` falls through to
HDOP 25.0 for any accuracy larger than the last band, and 25.0 implies 300 m, so
a 3000 m fix would be understated tenfold. The self-test says so in as many
words, in the check named "and cannot, above it". This is the same saturation as
two paragraphs above, seen from the other end: what rescues the invariant is not
the HDOP mapping but the separate `--max-nmea-accuracy` publish gate, which
refuses to emit a fix coarser than the ceiling at all. `--max-nmea-accuracy 0`
takes the invariant with it. Measured here:

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

"Exact" in the `$GPGST` row is true of four of its fields — RMS, semi-major,
semi-minor and the altitude error — which all carry the radius as the provider
reported it. The latitude- and longitude-error fields carry `r/sqrt(2)`, 16.3 m
and 176.8 m for those two fixes, because a radius describes a circle and those
two fields are its 1-sigma components rather than the radius again. And the
radius itself is passed straight through and labelled 1-sigma: no
confidence-level conversion is applied anywhere, so that label is an assumption
about what these services mean by "accuracy" and not a computation. `$GPGST` is
also only emitted for a fix that carries an accuracy radius at all, so it never
appears on the `qmi-cellid` path — QMI position reports carry DOP and a
satellite list but no accuracy in metres.

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
	sudo socat -u UNIX-CONNECT:/run/gnss-share.sock -
	nc 127.0.0.1 2948

Two of those need a word. `where-am-i` is Debian's `geoclue-2-demo`, which this
package neither depends on nor recommends and which is not installed here —
`apt install geoclue-2-demo` if you want it, and it is worth having, because it
is the only easy geoclue client. And the UNIX socket is mode 0660 root:geoclue,
so an ordinary login gets `Permission denied` on it: use `sudo` as above, add
yourself to the `geoclue` group, or read the TCP listener instead, which has no
such restriction and is what the `nc` line does.

One-shot queries, which touch nothing persistent:

	rhodep-gnss-daemon --locate-once      # wifi
	sudo /usr/local/sbin/rhodep-gnss-daemon --cells-once   # cell

	rhodep-gnss-daemon --self-test        # 114 checks, no hardware, no network
	rhodep-cell-db     self-test          # 29 checks
	rhodep-xtra        self-test          # 33 checks, incl. the QMI allow-list

114 is the count on this phone, which has `gir1.2-qmi-1.0`. On a host without
the bindings it is 110, and the four checks that build a real
`Qmi.MessageLocStartInput` are announced as skipped rather than silently
dropped — they used to fail there, which made the one hardware-free proof of
the gate logic look broken on any machine that was not this one.

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
the WiFi source. Its `systemd` timer **is** enabled, because injection is what
A-GPS does before a fix and it cannot start the measurement engine:

	sudo /usr/local/sbin/rhodep-xtra status    # read-only inventory of stored LOC state
	sudo /usr/local/sbin/rhodep-xtra inject    # fetch + inject orbits, time, position

The cost is **40 kB every twelve hours**, not every six. `inject` reuses a
cached almanac younger than `MAX_AGE`, which is 12 h, so the intervening
six-hourly run is a QMI re-injection with no network traffic at all; the unit
file says as much. "Every six hours" is nominal in any case — the timer carries
`OnBootSec=3min` and `RandomizedDelaySec=20min`, the latter so that not every
phone hits the CDN on the same tick.

`rhodep-xtra status` is the only way to read the modem's stored **SUPL server**
on this port — still Motorola's factory `supl.google.com:7275`, out of the
modem's EFS — because libqmi 1.38 defines the message but `qmicli` exposes no
`--loc-get-server` flag. That is the whole of the claim: it is not the only way
to read stored LOC state generally, since `qmicli` does have
`--loc-get-nmea-types`, `--loc-get-operation-mode`, `--loc-get-engine-lock`,
`--loc-get-predicted-orbits-data-source` and
`--loc-get-predicted-orbits-data-validity`, all of which work here. What
`rhodep-xtra status` adds is the SUPL server, and one read-only inventory of the
rest in a single command.

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
* **immutable + snapshot** — 13 of the package's 14 shipped files are `chattr
  +i` and snapshotted, and `rhodep-holds-enforce` restores anything that goes
  missing. `/etc/default/rhodep-cell-db` was added to that set for a quieter
  version of the same reason as the rest: it names the MCCs to index, the source
  to fetch and, now that the unit passes `$TOKEN_ARG` through, part of a command
  line that runs as root, so anything that can rewrite it can redirect a
  gigabyte-scale download. The one file left out on purpose is
  `/usr/share/doc/rhodep-gnss/README` — it is documentation, it changes on every
  release, and losing it costs nothing that a reinstall or the copy in this repo
  does not replace, so protecting it would buy upgrade friction and no safety.
  `systemctl disable` still works on an undeletable file, so the enabled state
  of `rhodep-gnss.service`, `rhodep-qmi-proxy.service` and `rhodep-xtra.timer` is
  registered too.

The file most worth this treatment is `zz-rhodep-no-modem-gnss.conf`: without
it, an upgrade or an `rm` re-opens the ModemManager path that resets the phone.

Left out of the *enforced* unit list: `rhodep-cell-db.timer`, which the package
ships disabled because a database refresh streams over a gigabyte, and
`gpsd.service` / `gpsd.socket`, which belong to the gpsd package and can be
re-enabled from it.

Not enforced is not the same as not touched, and gpsd is where the difference
matters. The postinst does `systemctl enable gpsd.socket gpsd.service` and
`systemctl restart gpsd.service`, and it edits `/etc/default/gpsd` in place,
rewriting `DEVICES=""` to `DEVICES="tcp://127.0.0.1:2948"` and
`GPSD_OPTIONS=""` to `GPSD_OPTIONS="-n"`. That is one package modifying another
package's conffile, which is exactly the sort of thing worth writing down rather
than leaving to be discovered. It is guarded at both ends: the edit happens only
while the file is still at the pristine Debian default of `DEVICES=""` and does
not already name the port, so nobody's real receiver is stamped on; a copy is
kept as `gpsd.rhodep-gnss.bak`; and the postrm puts it back on purge. The
authoritative setting is still the drop-in
`gpsd.service.d/10-rhodep-gnss.conf`, which is merged after gpsd's own
`EnvironmentFile=` and therefore wins — the `/etc/default/gpsd` edit exists so
that the file a reader inspects agrees with the behaviour they observe.

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
  no SIM here registers. `--self-test` covers the gate logic itself, with no
  hardware and no modem; `--test-gate-failure` exercises the real QMI path, but
  only when it is given `--source qmi-cellid`, since `auto` on an unregistered
  modem never reaches the gates. The success path with a real registered modem
  is untested.
* **Only `standalone` was measured.** The reset was reproduced in `standalone`,
  and within it the `powerMode` and time-injection levers were bisected. `msa`,
  `msb`, `wwan` and `default` were left alone once `powerMode` turned out to be
  a live lever inside `standalone`, because each would have cost one more reset
  with no read-back to distinguish "this mode is safe" from "this mode failed to
  apply". They remain open and cheap for whoever picks this up. Anything said
  anywhere about those four modes is extrapolation from the one that was run.
* The accuracy table above is one location on one day. The band mapping is a
  property of geoclue 2.8.1 and will need re-measuring if that changes.
* Nothing is ever submitted back to any database. Submission would need a
  trustworthy fix to tie the observations to, and the only fix this phone can
  produce is itself derived from a geolocation service — feeding that back would
  launder a guess into the database as ground truth. `submit-data=false`, and
  SSIDs ending in `_nomap` are excluded from queries as well.

## Why the satellite fix resets the phone

Asking the modem for a real satellite fix — a QMI LOC session in `standalone` —
brings up the modem's GNSS measurement engine, and the whole SoC deasserts
PS_HOLD and power-cycles about 100 ms after `QMI_LOC_START`. Next boot reports
`androidboot.bootreason=watchdog`. `standalone` is the mode that was actually
run; `msa`, `msb`, `wwan` and `default` have never been tried and are assumed
rather than known to behave the same way. There is no panic, no oops, no kernel
log line: an instrumented kernel showed all eight CPU cores sitting idle at the
instant it happened, so the application processor is not the one crashing. Stock
Android runs the same receiver on this same phone with byte-identical modem
firmware, so whatever is missing is on the Linux side and, in principle,
findable.

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

1. **Point DIAG at the GNSS reset. DIAG works now; nobody has aimed it at this
   bug yet.** This item used to read "get DIAG working", and that part is done.
   `userspace/debug-tools/rhodep-diag-server.py` is pure userspace over
   AF_QIPCRTR — no kernel driver, no `/dev/diag`, nothing flashed — and
   [`modem-diag-wip/HANDOFF.md`](modem-diag-wip/HANDOFF.md) records the closing
   state: services published, the control handshake complete including the
   DIAGID acknowledgement that was the missing step, masks enabled, commands
   answered, log stream flowing, in a standard format that SCAT, QCSuper,
   MobileInsight and Wireshark all read. It survives the death, which is the
   whole point: `--dump FILE` fsyncs per packet, and 2 MB over 534 packets came
   through an LTE watchdog reset intact. The modem's logs turned out to be plain
   detailed text rather than something to reverse-engineer.

   **This has now been done once, and the result moves the item rather than
   closing it.** 2026-09-06: one server covering a surviving `powerMode 3` run
   and a resetting `powerMode 2` run on the same boot, 7.4 MB, 1835 records,
   uptime 70.91 to 195.92, `bootreason=watchdog` afterwards. The write-up is
   [`modem-diag-wip/GNSS-CAPTURE-20260906.md`](modem-diag-wip/GNSS-CAPTURE-20260906.md).

   The modem streams complete 4096-byte packets at a steady 60 ms cadence, at a
   slightly *rising* rate, up to the instant power goes — no error, no
   assertion, no timeout, and the final record is not truncated, so the rail
   drops *between* packets rather than interrupting a write. Nothing in the
   capture distinguishes the resetting run from the surviving one. That is the
   same signature DIAG found on the LTE reset, from a different trigger.

   But the capture carries **no F3 messages at all** — 19 707 frames of log
   (`0x10`) and 134 events (`0x60`), and zero of `0x79` or `0x92`. F3 is where
   the modem's plain text lives, and every string in `NAV-CORE-RESET.md` that
   would say why the NAV core does not come up is an F3 message out of
   `modem.b21`, the diagnostic message table. So the honest reading of "the
   modem said nothing" is "we did not ask it in the language it answers in".
   One hypothesis for that — `msg_mask_all()` sending an ssid range of [0, 0]
   rather than the `ALL_SSID` sentinel — was fixed and measured and **did not
   help**; three further candidates are listed in that document. The item is
   therefore no longer "point DIAG at the bug" but "make DIAG carry messages,
   then point it at the bug", and it still needs no flash.

2. **Register a memory-dump table with TrustZone — but read the constraint
   first, because it undercuts this.** Stock's `msm-imem` node has a
   `mem_dump_table@10` child that this port does not declare, whose driver
   publishes a table of DRAM regions to the secure firmware so that firmware can
   record what happened before a reset. The reason this now ranks below (1)
   rather than beside it is in
   [`watchdog-ipa-lte-wip/HANDOFF.md`](watchdog-ipa-lte-wip/HANDOFF.md): the PON
   registers read `POFF sequence ran, status 0002 -> PS_HOLD` and the following
   boot calls itself a Hard Reset, so this is a power cycle and **DDR loses its
   contents**. ramoops cannot survive it, the Motorola carveouts cannot survive
   it, and no amount of driver work will change that — the memory holding the
   evidence is unpowered, not overwritten. A memory-dump table therefore only
   helps if TrustZone writes the regions to flash rather than to DRAM, which is
   unestablished. Anything meant to be read after this reset has to be on flash
   before it, which is exactly what `--dump` in (1) and `rhodep-kmsg-tail`
   already do.

   Three attempts to build the module were blocked by "an unrelated tooling
   issue", and that is the whole of what was written down: the actual blocker is
   not recorded anywhere in this repo. The two on-phone module hazards that
   *are* documented both live in `kernel/diag-modules/README.md` — the
   gcc-versus-clang mismatch, since the headers package now carries the kernel's
   real clang-generated `.config` and gcc stops on
   `-fexperimental-late-parse-attributes`, and the bug that made every module on
   this kernel un-unloadable, fixed on 2026-09-05 by the `rhodep3` headers
   package. Either would fit the description; neither is confirmed to be it.

3. **The `pmr735a_l1` operating mode.** Holding this rail on at 600 mV was tested
   and did not help — but the 62 mA *load* vote that would move the LDO out of
   low-power mode almost certainly never reached RPM, because mainline needs
   `regulator-allow-set-load` in the device tree and this port does not have it.
   So the rail's *mode*, as opposed to its voltage, is genuinely untested.
   `kernel/patches/0121` adds exactly that. It needs a flashed boot image to
   test, which needs working fastboot over USB — this port's only recovery path,
   and not available from where the reproducer has been run.

4. **The seven other `msm-imem` children** mainline does not declare
   (`restart_reason`, `dload_type`, `boot_stats`, and so on). Newly visible from
   the recovered stock tree, completely untested, and with nothing yet tying any
   of them to this bug — listed for completeness, not because there is a reason
   to expect it.

**QDSS/CoreSight used to be item 4 in this list and it is now eliminated, not
merely unpromising.** It was the leading candidate on paper — about 110
CoreSight nodes downstream against none at all in mainline, and a hardware trace
source coming up and pushing onto an ATB nobody has configured is a bus
transaction that never completes, which is this signature exactly. Three
independent measurements in
[`watchdog-ipa-lte-wip/HANDOFF.md`](watchdog-ipa-lte-wip/HANDOFF.md) close it.
All six remote-ETM instances — three on the modem, two on the ADSP, one on the
CDSP — report `DISABLED`, and enabling two of the modem's and leaving them
running changed nothing: the phone stayed up and the data call stayed attached.
Five CoreSight blocks including the two carrying the modem's own trace read
cleanly over `/dev/mem` with the standard component id, so the fabric is
powered, clocked and addressable. And every funnel reads `FUNCTL = 0x300` with
all eight input port enables clear, with TMC-ETF and STM `CTL = 0` — nothing
routing, nothing capturing. The fuses settle it independently: `AUTHSTATUS`
reads all-disabled for the modem's trace domain and for the CPUs' own ETMs, so
no hardware trace source on this silicon can emit anything at all, and the
unconfigured-ATB hypothesis was never physically possible. The 110 missing nodes
are a real gap in the port and worth filling one day for tracing. They are not
what resets the SoC.

The recovered stock device tree that made items 2 and 4 visible is kept in
`backups/vendor-dt-rhodep-20260906/` (git-ignored — it is Motorola's), and its
own `README.md` records what it settled and what it opened.

**So: location works today, and it works well enough that the phone is genuinely
useful without a satellite. The satellite fix is a hardware/firmware
investigation that was blocked on the ability to see inside the modem; that
ability now exists and has not yet been pointed at this bug. Until somebody
does, the four sources above are the answer.**
