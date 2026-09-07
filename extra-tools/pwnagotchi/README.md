# pwnagotchi on rhodep

	git clone https://github.com/jayofelony/pwnagotchi /opt/pwnagotchi   # if not already there
	# install the aarch64 pwngrid from jayofelony/pwngrid releases to /usr/bin/pwngrid
	sudo ./install.sh
	otg on
	sudo systemctl start rhodep-pwnagotchi

Then the web UI is at `http://<phone>:8080` (or `172.16.42.1:8080` over the USB
cable). It captures WPA handshakes into `/etc/pwnagotchi/handshakes/`.

Verified working on the device: bettercap on `wlan1mon` sees dozens of APs, the
agent sends association/deauth frames and **captured 11 handshakes** in a short
auto-mode run, e.g.

	!!! captured new handshake on channel 1: b2:4e:26:df:2b:2f -> Claro Fibra !!!

**Two radios supported, one at a time**: the shipped default is the **external
TP-Link (`wlan1mon`)** exactly as described below. There is now an **opt-in
"internal radio" mode** that runs pwnagotchi on the phone's own WCN3990 radio
(`wlan0` gone, `wlan0mon` doing channel-hopping capture, deauth/associate routed
through the STA offchannel injector). It's opt-in via a systemd drop-in — see
"Internal-radio (wlan0/wlan0mon) mode" below.

## Default (external): it uses wlan1, never wlan0

pwnagotchi is built for a Raspberry Pi whose only WiFi does monitor mode. On
this phone we have two radios; by default we keep them separate:

- **wlan0** is the internal ath10k / WCN3990. In the default (external) mode
  it stays a managed client (that is your actual network) and is never touched.
  Whether wlan0 itself can do monitor was long "no" and is now "partially yes"
  — see the internal-radio section for what changed and item 13 in the top-level
  README for the details (patches 0117/0118).
- **wlan1** is the external TP-Link TL-WN722N (RTL8188EUS) or Archer T2U Nano
  (RTL8811AU), which does full raw monitor + injection. All capture happens here
  in the default mode.

In the default (external) mode, everything is pinned to wlan1 and wlan0 is
never touched:

- `main.iface = "wlan1mon"` in the config — a distinct name, impossible to
  confuse with wlan0.
- `rhodep-pwn-monstart` operates on **wlan1 by name only**, refuses to run if
  pointed at wlan0, and refuses if wlan1 turns out to share a phy with wlan0.
- It **never runs `airmon-ng check kill`**. Upstream's monstart does, which
  stops NetworkManager and wpa_supplicant for the whole system — on this phone
  that would drop wlan0 and gain nothing, since NetworkManager already ignores
  wlan1. Instead it hands only wlan1 to `nmcli device set wlan1 managed no`,
  with no daemon restart.
- It removes the driver's stray P2P-device vif, but only ones on wlan1's phy,
  and explicitly skips wlan0.

Confirmed after a full run: `wlan0` stayed `type managed` and connected
throughout, `nmcli` still showed `wlan0:connected`.

## Pieces

	bin/rhodep-pwn-monstart            wlan1 -> monitor as wlan1mon (wlan1 only)
	bin/rhodep-pwn-monstop             wlan1mon -> managed
	bin/rhodep-pwn-bettercap-launcher  bettercap on wlan1mon (main.mon_start_cmd path)
	bin/rhodep-pwn-launcher            the agent, from the venv
	config/config.toml                 the user override
	apply-ui-fixes.sh                  web-UI patches to /opt/pwnagotchi (idempotent)
	systemd/rhodep-pwn-bettercap.service   bettercap + api.rest on :8081
	systemd/rhodep-pwngrid-peer.service    pwngrid peer on wlan1mon
	systemd/rhodep-pwnagotchi.service      the agent (pulls in the other two)

## How it runs

Three services, same split as upstream but retargeted:

1. **bettercap** brings up `wlan1mon` (via monstart) and serves its REST API on
   `127.0.0.1:8081`. This is what actually drives the radio.
2. **pwngrid-peer** is the identity/mesh peer, on `wlan1mon`, API on `:8666`.
   `grid.report = false` in the config keeps it from phoning home.
3. **pwnagotchi** is the agent: it talks to bettercap's API, sets
   `wifi.interface wlan1mon`, and runs the recon/associate/deauth/capture loop.

Stopping is the important direction: bettercap's `ExecStopPost` runs
`rhodep-pwn-monstop`, which renames `wlan1mon` back to `wlan1`, sets it managed
and hands it to NetworkManager — so after an off there is a normal `wlan1`
again, by name and mode, and wlan0 was never touched. Verified: start -> stop
leaves `wlan1 type managed`, no `wlan1mon`, `wlan0` still connected.

It runs from a venv at `/opt/pwnagotchi/.venv` built `--system-site-packages`,
because Kali is EXTERNALLY-MANAGED: scapy, flask, dbus, prctl and the rest come
from apt, and only `pycryptodome` (the `Crypto` module) is added into the venv.

**The venv is pinned to `python3.13`, on purpose.** Two things broke it when it
followed the system Python instead:

  * pwnagotchi calls `asyncio.get_event_loop()` with no running loop
    (`agent.py`, `start_event_polling`), which Python 3.12+ made a fatal
    `RuntimeError` instead of a warning. On 3.14 the agent dies a few seconds
    after start with *"There is no current event loop in thread 'MainThread'"* --
    which from the UI looks like Enable turning itself back off. 3.13 is the last
    version it runs on.
  * A venv whose `python3` is a plain link to `/usr/bin/python3` follows the
    system across a minor-version bump, leaving its own `pycryptodome` in
    `lib/python3.13/site-packages` where the new interpreter never looks:
    *"ModuleNotFoundError: No module named 'Crypto'"*, the same failure with a
    different message. This is the same trap that hit the NetHunter Pro app.

`install.sh` builds the venv with `python3.13` explicitly and rebuilds it if it
finds one on another version. If 3.13 is ever removed, pwnagotchi's asyncio use
has to be fixed before it will run at all.

Default mode is **manual**: it listens and serves the UI but never transmits.
For the full loop change `launcher manual` to `launcher auto` in
`rhodep-pwnagotchi.service`. The NetHunter Pro app does this with a systemd
drop-in (`.../rhodep-pwnagotchi.service.d/10-mode.conf`) rather than editing the
unit, so a package upgrade does not fight it.

## The trap that will reboot your phone

pwnagotchi's `set_name()` rewrites `/etc/hostname` and **reboots the whole
machine** whenever `main.name` differs from the current hostname — and
`main.name` is read from `default.toml`, where it is `"pwnagotchi"`, so simply
leaving it out of your config does not help: it reboots towards "pwnagotchi".

`install.sh` pins `main.name` to the device's current hostname so the check is a
no-op and nothing is ever renamed or rebooted. During bring-up here it did fire
once and changed the hostname from `kali` to `pwnagotchi` before this was
understood; the installer sets it correctly, but if you edit the config by hand,
keep `main.name` equal to `hostname` or expect a reboot.

## Adapters: single- and dual-band

Either TP-Link works, and monstart is driver-agnostic (it drives `wlan1` by name
and phy, not by chipset), so nothing here changes between them:

- **RTL8188EUS** (single-band 2.4 GHz) — the `rtl8188eus` driver, see
  `packages/rhodep-rtl8188eus-fix`.
- **RTL8811AU / Archer T2U Nano** (dual-band 2.4 + 5 GHz) — the `rtw88` driver
  (`rtw_8821au`), see `packages/rhodep-rtl8821au`. With this one, add the 5 GHz
  channels to `personality.channels` in the config to actually hop them; the
  non-DFS channels (36-48, 149-165) are the useful ones, since DFS channels are
  `no-IR` and cannot transmit assoc/deauth. Confirmed hopping 5 GHz and seeing
  APs on channels 40/149/153.

## Web UI feel (apply-ui-fixes.sh)

The web frame is a PNG pwnagotchi re-saves per update; with the stock
`ui.fps = 0` it is written only on major state changes, so the "waiting Ns"
countdown never ticks and the whole thing feels static. Setting `ui.fps = 2` in
the config (device-side) starts the periodic refresh so it animates. To match
it, `apply-ui-fixes.sh` lowers the browser poll in `index.html` from 1000 ms to
500 ms, and moves the `APS` widget from x=28 to x=38 so three-digit 5 GHz
channel numbers do not overlap the label. Those two touch `/opt/pwnagotchi`
(third-party source), so `install.sh` reapplies them; re-run after any
pwnagotchi update.

## Notes

- Needs the TP-Link powered, which means USB-host + VBUS: `otg on` first
  (`rhodep-usb-otg`). monstart calls `otg on` itself if wlan1 is absent, but the
  adapter has to be physically plugged in.
- The internal wlan0 keeps working as your normal WiFi the entire time, so you
  can SSH in over it (or over the USB cable) while pwnagotchi runs.
- This is not held or protected like the core port packages — it is an
  extra-tool, optional, and lives entirely under /opt/pwnagotchi,
  /usr/local/sbin and /etc/pwnagotchi.

## Internal-radio (wlan0/wlan0mon) mode — opt-in

Runs pwnagotchi on the phone's own WCN3990 radio, no external adapter needed.

**Trade-off you must accept:** wlan0 STA is deleted for the duration of the run,
so you lose WiFi. The chanctx model of ath10k requires monitors-only for
channel-hopping to actually retune the radio; if wlan0 STA is up, wlan0mon is
locked to the STA's channel (silently). We tear wlan0 down completely at start
and put it back at stop. `systemctl stop rhodep-pwn-bettercap.service` (or the
NetHunter Pro toggle off) restores wlan0 and NetworkManager reassociates.

**What the internal mode can and can't do vs the external one:**

|                        | external (wlan1) | internal (wlan0/wlan0mon)              |
| ---                    | ---              | ---                                    |
| Passive capture        | full             | full                                   |
| Channel hopping        | full             | full (monitors-only, real retune)      |
| Deauth injection       | bettercap raw    | bettercap raw (rhodep ath10k patches)  |
| Assoc injection        | bettercap raw    | bettercap raw (rhodep ath10k patches)  |
| WiFi stays up          | yes              | **no** (wlan0 deleted while running)   |
| Adapter required       | TP-Link          | none                                   |

Both radios run the vanilla evilsocket/pwnagotchi agent path. There is no
wrapper plugin, no userland injector, and no monkey-patching of
`agent.deauth`/`agent.associate` -- the rhodep ath10k patches make raw
radiotap TX from the monitor vdev work natively on the WCN3990, so
`bettercap` handles both capture and injection the way upstream expects.

### How to enable

Install the drop-in on both units and edit the config:

```sh
sudo install -Dm0644 /path/to/repo/extra-tools/pwnagotchi/systemd/dropins/20-radio-internal.conf \
    /etc/systemd/system/rhodep-pwn-bettercap.service.d/20-radio.conf
sudo install -Dm0644 /path/to/repo/extra-tools/pwnagotchi/systemd/dropins/20-radio-internal.conf \
    /etc/systemd/system/rhodep-pwngrid-peer.service.d/20-radio.conf
sudo systemctl daemon-reload
sudoedit /etc/pwnagotchi/config.toml    # under [main]: iface = "wlan0mon"
                                        # and: mon_start_cmd = "/usr/local/sbin/rhodep-pwn-monstart-dispatch"
                                        # and: mon_stop_cmd = "/usr/local/sbin/rhodep-pwn-monstop-dispatch"
sudo systemctl start rhodep-pwnagotchi.service
```

**Go back to external:** remove both `20-radio.conf` drop-ins, revert the three
config keys, `daemon-reload`, re-`start`.

The NetHunter Pro app writes/removes these drop-ins for you (same pattern it
already uses for the manual/auto mode `10-mode.conf`).

### Requirements

- The running kernel must expose `/sys/module/ath10k_core/parameters/mon_mgmt`
  (the rhodep ath10k patch that delivers WMI mgmt frames to the monitor
  vdev). `rhodep-pwn-monstart-internal` refuses to start if it is missing.

### Extra pieces (installed by install.sh)

```
bin/rhodep-pwn-monstart-internal      wlan0 STA -> gone, wlan0mon up on the ath10k phy
bin/rhodep-pwn-monstop-internal       wlan0mon -> gone, wlan0 STA recreated + reconnected
bin/rhodep-pwn-monstart-dispatch      picks -monstart or -monstart-internal by RHODEP_PWN_RADIO
bin/rhodep-pwn-monstop-dispatch       symmetric dispatch on stop
bin/rhodep-pwn-pwngrid-launcher       pwngrid on the right iface (wlan1mon or wlan0mon)
systemd/dropins/20-radio-internal.conf  drop-in template that sets RHODEP_PWN_RADIO=internal
```
