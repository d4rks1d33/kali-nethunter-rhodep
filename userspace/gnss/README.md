# GNSS

	sudo ./install.sh

Or install the package: `packages/rhodep-gnss/`, built by
`scripts/build-support-debs.sh`.

`rhodep-gnss-daemon` gets a position out of a phone whose satellite GNSS cannot
be used, and republishes it as ordinary NMEA — on a UNIX socket for geoclue and
on TCP for gpsd:

	/run/gnss-share.sock     mode 0660, group geoclue
	tcp://127.0.0.1:2948

	socat -u UNIX-CONNECT:/run/gnss-share.sock -
	nc 127.0.0.1 2948
	gpspipe -r ; gpspipe -w ; cgps -s

`rhodep-cell-db` builds the optional offline cell-tower database.

## There is no satellite fix to be had on this phone

Starting a QMI LOC session in operation mode `standalone`, `default`, `msa` or
`msb` power-cycles this SoC within 100 ms, reproducibly, with no panic and no
oops — the modem's GNSS measurement engine coming up is enough. `cellid` runs
the session state machine without ever starting that engine, and survives.

So no amount of gpsd, geoclue or ModemManager configuration produces a
satellite fix here. Every position below is network-derived.
`scripts/rhodep-gnss-test.py` is the bisection;
`docs/interconnect-sm6375-wip/GNSS-SM6375.md` is the write-up.

## Where the position comes from

`--source` picks the location source. The shipped unit runs `auto`.

| | needs a SIM | QMI service opened | measured here |
|---|---|---|---|
| `wifi` | no | **none** | **23 m** |
| `cell` | no | NAS (read-only) | **250 m** |
| `qmi-cellid` | yes | LOC | unavailable — SIM unprovisioned |
| `auto` | no | NAS, and LOC only once registered | 23 m via wifi |

`auto` prefers `qmi-cellid`, then `wifi`, then `cell`. That is an ordering by
how directly each answer is tied to where the phone is — the modem's own fix,
then several access points a few tens of metres away, then one tower up to a
few kilometres away whose surveyed position is not yours. A source only wins if
its fix is current *and* honest (see below), so a stale WiFi fix yields to a
current cell fix on its own.

### The safety story is which QMI service gets opened

The thing that resets this SoC is `QMI_LOC_START` in a mode that brings up the
measurement engine. QMI **NAS** is a different service: `get-cell-location-info`
and `get-serving-system` report what the radio already measured while looking
for a network. They start nothing, and several hundred of them during this work
left `uptime` untouched.

`auto` reads modem registration from ModemManager over D-Bus — a plain property
read — and opens **LOC** only if that passes. With no usable SIM, no LOC client
is ever allocated and no session is ever started, so the reset is out of reach
rather than merely guarded against. `--no-cell` drops the NAS client too, if
you want a daemon that provably speaks no QMI outside `--source=qmi-cellid`.

## The wifi source

	rhodep-gnss-daemon --locate-once

Scanning goes through NetworkManager's D-Bus API by default, asking it to
`RequestScan` when its cache is too thin or too old, so NM schedules the scan
around the live connection instead of fighting it for the radio. `iw` is the
fallback and gives exact dBm. SSIDs ending in `_nomap` are excluded and nothing
is ever submitted back.

Services are tried in order — BeaconDB, then Positon. BeaconDB is the right
default: public domain, opt-in collection, no key, and what geoclue 2.8 now
compiles in since the Mozilla Location Service shut down for good in 2024.

**Measured here on 2026-09-05, in Rosario/AR: BeaconDB has no coverage.** It
answers `404 "no location could be estimated"` for every BSSID in range and for
the serving GSM cell; the only thing it will return is a 25 km IP-address guess.
Positon fixes the same scan to about 23 m. That is not a misconfiguration —
BeaconDB is young and this area simply is not in it — and it is why the fallback
chain exists at all.

## The cell source

	sudo /usr/local/sbin/rhodep-gnss-daemon --cells-once

Reads the identity of the cells the modem can hear and resolves them. It works
with an unprovisioned SIM and a modem that reports `not-registered-searching`,
because a modem decodes the broadcast identity of the cells around it *as part
of searching*:

	$ qmicli -p -d qrtr://0 --nas-get-cell-location-info
	GERAN Info
		Cell ID: '60858'
		PLMN: '72234'
		Location Area Code: '420'
		GERAN Absolute RF Channel Number: '179'
		Base Station Identity Code: '19'
		Timing Advance: 'unavailable'
		RX Level: -97 dBm > level > -96 dBm ('14')
		Cell [0]:
			Cell ID: '40012'
			PLMN: '72234'
			Location Area Code: '421'
			...

ModemManager's `--location-get` reports nothing here because MM only populates
3GPP LAC/CI from a *registered* serving cell. Reading NAS directly bypasses
that.

**It is intermittent.** Ten polls in a row return anything from a full identity
to `NoNetworkFound`, to PLMN `00-00`, to a neighbour whose ARFCN and signal are
known but whose Cell ID reads `unavailable` — a neighbour is measured seconds
before its BCCH is read. Hit rates observed here ranged from 2/10 while the
modem was cycling bands to 10/10 once it settled. So the reader polls on its own
short timer (`--cell-poll`, 15 s), caches the last good answer with its age, and
the provider query (`--cell-interval`, 60 s) uses whatever is newer than
`--cell-max-age`. Anything with cell id 0, LAC 0 or an all-ones field is
discarded rather than submitted.

### Provider chain, and what each one said

| provider | key needed | result for GSM 722-34 LAC 420 CID 60858 |
|---|---|---|
| offline OpenCelliD+MLS, 652 905 cells for MCC 722 | no | **miss** — not in either dump |
| BeaconDB | no | **HTTP 404** |
| Positon | shipped | **-32.952961, -60.643772, 250 m** |
| *OpenCelliD API* | **yes, human signup** | *not queried* |

The Positon answer is 137 m from the WiFi fix taken at the same moment, well
inside its 250 m claim. A separately decoded neighbour (LAC 421 CID 40012) also
resolves, to 159 m.

**OpenCelliD's API cannot be used without a human.** `https://opencellid.org/
cell/get` returns `{"error":"API Key not known: ","code":2}` with HTTP 200 and
no key, and the same with an invalid one; there is no anonymous mode.
Registration is a web form at `https://my.opencellid.org/register` wanting a
name and an e-mail address behind reCAPTCHA. No token is shipped and none was
invented. Put one in `TOKEN=` in `/etc/default/rhodep-cell-db` and
`rhodep-cell-db` uses the official current per-country export instead of the
archived mirror.

`api.mylnikov.org` was also measured: it *does* answer for this cell, with a
`range` of 18.7 km at a position 72 km from the truth. Not self-consistent, not
in the chain.

## The offline database

	sudo /usr/local/sbin/rhodep-cell-db build --mcc 722 --source ocid,mls
	rhodep-cell-db info
	rhodep-cell-db lookup --mcc 722 --mnc 34 --lac 420 --cid 60858 --radio gsm

(`/usr/local/sbin` is not in sudo's `secure_path` on this image, hence the full
path. `info`, `lookup` and `self-test` are read-only and need no root.)

Two keyless sources, both mirrored on the Internet Archive and streamed +
filtered on the fly so nothing is spooled to disk:

| | date | licence | compressed | cells for MCC 722 |
|---|---|---|---|---|
| `ocid` OpenCelliD full export | 2024-05-19 | CC-BY-SA 4.0 | 1.1 GB | 312 146 |
| `mls` Mozilla Location Service final export | 2024-03-27 | public domain | 1.6 GB | +340 759 |

Merged: **652 905 cells, 29.2 MiB of SQLite, 530 s** over a 5 MB/s link. Lookups
are exact on `(mcc, mnc, lac, cid)`. There is deliberately no "nearest cell in
this LAC" mode — that is the `lacf` answer the daemon refuses from the network
providers, and it is no more honest from a local file.

`rhodep-cell-db.timer` refreshes monthly and ships **disabled**; a refresh is
over a gigabyte. With the archived sources it is pure waste anyway — they are
static snapshots — so build once by hand. It becomes worth enabling once
`TOKEN=` is set.

### Why it misses this phone's own cell

The 2024 OpenCelliD export has **4572 cells within 3 km of this flat** — the
area is very well surveyed. But its GSM cells for 722-34 here sit under LAC
**400** (cids 60213, 40213, 40012, 30213, 10012…), while the modem today reports
LAC **420** CID 60858 and LAC **421** CID 40012. Telecom Argentina appears to
have renumbered its GERAN location areas since. CID 60858 appears nowhere in MCC
722 in either dump; the only 60858 in Argentina belongs to operator 722-**07**,
in Neuquén, 800 km away — which is why matching is on the full tuple and never
on cell id alone.

A stale dump does not degrade into a worse fix. It simply does not contain the
key. That is the entire argument for a provider chain.

No prebuilt database is shipped: it would be 29 MiB of Argentina in a package
installed on phones that are not in Argentina, and here it does not even answer.

## The `considerIp` trap

Every request carries `"considerIp": false`. Leave it out and BeaconDB answers
**HTTP 200** with a location and `"fallback": "ipf"` — DB-IP geolocation of the
egress IP, 25 km wrong here and potentially a different country behind a VPN.
Nothing in the reply says "guess" except that one field, and a 200 with
plausible coordinates looks exactly like a success. Replies flagged `ipf` or
`lacf` are refused regardless, as is anything coarser than `--max-accuracy`
(5000 m).

## Not over-claiming accuracy

A network fix has no satellite geometry, so whatever goes in the HDOP field is a
fabrication. The question is which one. Consumers bucket HDOP; geoclue 2.8.1,
measured by feeding it synthetic GGA at fixed HDOP:

| HDOP | ≤1 | ≤2 | ≤5 | ≤10 | ≤20 | >20 |
|---|---|---|---|---|---|---|
| geoclue reports | 0 m | 1 m | 3 m | 50 m | 100 m | 300 m |

So the conventional "5 m per DOP" is actively harmful: a 22 m fix becomes HDOP
4.4, lands in the *good* band, and geoclue announces **3 m**. Instead the daemon
picks the lowest band whose implied accuracy is still at least the real one, and
emits an HDOP from inside it.

**Cell fixes make that table's ceiling matter.** It saturates at 300 m, so there
is no HDOP — no value at all — that tells geoclue "1200 m". `$GPGST` carries the
truth in metres and gpsd reads it, but geoclue does not: `strings
/usr/libexec/geoclue` contains `$GPGGA` and nothing else. So the daemon
**refuses to publish** any fix coarser than `--max-nmea-accuracy`, which
defaults to exactly that 300 m ceiling, and logs why once. `--max-nmea-accuracy
0` disables the check for anyone who would rather have a coarse position than
none.

The invariant, checked directly by `--self-test`: **for every published fix, the
accuracy a consumer infers from our HDOP is at least the accuracy the source
reported.** Measured here:

| | WiFi fix | cell fix |
|---|---|---|
| truth (Positon) | 23 m | 250 m |
| `$GPGST` | 23 m — exact | 250 m — exact |
| geoclue | 50 m | 300 m |
| gpsd `TPV.eph` | 142 m | 475 m |
| `cgps` 2D Err | 467 ft | 1558 ft |

Every number in every column is ≥ the truth.

## Why the sentences are synthesized

In `cellid` mode the modem's own NMEA output is a bare `$PSTIS,*61` once a
second, and the network sources produce no NMEA at all. Neither gpsd nor geoclue
can do anything with that, so `$GPGGA`, `$GPRMC`, `$GPGSA` and `$GPGST` are
built at 1 Hz from whichever position the daemon holds.

GGA fix quality is 1 when there is a position — NMEA 0183 has no code for a
network fix and gpsd reads 0 as "no fix at all" — and the satellite count stays
0. With no position the sentences are still emitted, RMC status `V` and GGA
quality 0, so gpsd and geoclue stay attached to a live source instead of a
silent socket.

Sentences carry the time they are *sent*, not when the fix was obtained (except
for QMI positions under two seconds old, which keep the modem's own UTC). A
network fix rebroadcast at 1 Hz under its original timestamp freezes the clock
in the stream, and gpsd reads that as one endlessly repeated epoch and stops
propagating the fix.

## The only mode that does not reboot the phone

(This governs `--source=qmi-cellid`, and `--source=auto` once a modem registers.
In wifi and cell modes none of it runs.)

The daemon will not start a session until the modem has agreed to `cellid` three
separate times:

1. `QMI_LOC_SET_OPERATION_MODE` is acked,
2. the **set-operation-mode indication** comes back `SUCCESS`, and
3. `QMI_LOC_GET_OPERATION_MODE` reads the mode back as `cellid`.

Gate 2 is the one that matters and the one that is easy to miss. LOC acks the
request and reports the real outcome in an indication, so "the call returned
without an error" says only that the request was accepted. A mode that is never
applied leaves the engine in `standalone` — the case that reboots the phone.

If any gate fails the daemon logs `REFUSING TO START A GNSS SESSION` and exits
1 with `QMI_LOC_START` never sent. That path is testable without a misbehaving
modem:

	rhodep-gnss-daemon --test-gate-failure=ack        --socket '' --tcp 2960
	rhodep-gnss-daemon --test-gate-failure=indication --socket '' --tcp 2961
	rhodep-gnss-daemon --test-gate-failure=readback   --socket '' --tcp 2962

### The gates are recorded state, not a chain

They used to be a chain — ack callback, then indication, then readback, then
start — and running the fault injection above is what showed that to be wrong.
MEASURED, 2026-09-05, `--test-gate-failure=indication`:

	setting LOC operation mode to cellid
	--test-gate-failure=indication: simulating a failed indication gate
	REFUSING TO START A GNSS SESSION: modem refused operation mode cellid
	set_operation_mode acked, waiting for the indication   <- ack arrived LAST

On this modem the **indication is delivered before the response**, every time.
The chain therefore advanced to the readback — and could have reached
`QMI_LOC_START` — before gate 1 had returned a verdict at all; a genuine late
ack failure would have been reported after the session was already running.

Now each gate records `True`/`False`/pending, `_after_set_mode()` advances only
once gates 1 and 2 have *both* landed in whatever order they arrive, and
`start_session()` re-asserts all three plus the operation mode at the single
point where `QMI_LOC_START` is sent. `refuse()` drops the state machine to idle
before anything else, so a reply still in flight for a gate that already failed
cannot find a state it recognises and resume the sequence.

`--i-know-this-reboots-the-phone` unlocks `--operation-mode` for whenever the
underlying reset gets fixed, and is now also required for `--no-mode-readback`,
which removes gate 3. Neither is something to run on hardware you want to stay
up.

### ModemManager can do it too, and it has no gates

	$ mmcli -m any --location-status
	  Location | capabilities: 3gpp-lac-ci, gps-raw, gps-nmea, agps-msa, agps-msb
	           |      enabled: 3gpp-lac-ci

Four of those five reset this SoC, and all of them are one
`Location.Setup()` call away — a settings panel, a shell's location toggle,
geoclue's `modem-gps`, `mmcli --location-enable-gps-nmea`. The package ships a
**mandatory** D-Bus deny (`zz-rhodep-no-modem-gnss.conf`) for `Location.Setup`,
`Location.InjectAssistanceData` and `Location.SetSuplServer`. Mandatory,
because ModemManager's own policy allows root everything and D-Bus applies
default &lt; user &lt; mandatory; the accident being prevented is a `sudo mmcli`
one. `GetLocation` and `SetGpsRefreshRate` stay allowed — neither can start the
engine.

It does **not** stop ModemManager's own LOC probing at modem init (`couldn't
load SUPL server`, `couldn't load supported assistance data types`). Nothing in
MM 1.24 does. What it does stop is that probing being triggered gratuitously:
`rhodep-qmi-proxy.service` now owns the shared `qmi-proxy`, because it used to
be forked by — and killed with — `rhodep-gnss.service`, so every
`systemctl restart rhodep-gnss` took ModemManager's transport down and
re-enumerated the modem.

## What you get today

WiFi, with no SIM and no GNSS:

	$ rhodep-gnss-daemon --locate-once
	latitude   -32.9541930
	longitude  -60.6442380
	accuracy   22 m
	provider   api.positon.xyz

	$ gpspipe -w | grep TPV
	{"class":"TPV","mode":2,"lat":-32.954195,"lon":-60.644223,"eph":142.500}

	geoclue LocationUpdated: lat=-32.9542117 lon=-60.6442733 accuracy=50.0m

Cell tower, from the same phone, same moment, with the WiFi path disabled:

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

	$ gpspipe -w | grep TPV
	{"class":"TPV","mode":2,"lat":-32.952961667,"lon":-60.643771667,"eph":475.000}

	geoclue LocationUpdated: lat=-32.9529617 lon=-60.6437717 accuracy=300.0m

`qmi-cellid` still has nothing to say — it needs a *registered* modem, and

	registration: searching
	network rejection error: imsi-unknown-in-hlr

means it never registers, so every QMI LOC position report would come back
`session status = general-failure`. `--source=auto` therefore never asks it.
Put in a SIM the network accepts and the daemon switches over on its own.

Note that the `cell` source needs none of that: it reads the cell identities the
modem decodes while searching, which is why it works on this phone and
`qmi-cellid` does not.

## Self-tests

	rhodep-gnss-daemon --self-test    # 112 checks
	rhodep-cell-db     self-test      # 29 checks

Neither touches hardware or the network. Between them they cover the NMEA
generator and checksums, the `iw` and `qmicli` output parsers against real
captured output, BCD PLMN decoding, GSM RXLEV conversion, the shape of the
geolocate requests (including that `considerIp` is false), the source priority
chain, the staleness rules, the accuracy-band invariant, and the offline
database builder and its refusal to fall back to an LAC centroid.
