# Kali NetHunter Pro for the Motorola Moto G82 5G (motorola-rhodep)

Port of **Kali Linux (NetHunter Pro / Phosh)** on a **mainline Linux kernel
(7.2-rc5)** to the Motorola Moto G82 5G — codename **rhodep**, SoC **Qualcomm
SM6375** (Snapdragon 695). This device is **not** on Kali's official supported
list; everything here is a from-scratch community port.

> Built on top of a postmarketOS mainline kernel port. The kernel is shared with
> the pmOS port; Kali only changes the kernel `.config` and the userspace.

> **Current image: `kali-boot-v94-STABLE.img`.** Everything below that works,
> works on it: speaker, earpiece, headphones with jack detection, and the
> microphone. It is `v92-STABLE-audio` plus one device tree node reserving 8 MB
> for the modem's memshare region, and nothing else: kernel, ramdisk and cmdline
> are byte for byte identical to v92, so falling back is just reflashing the
> older file.
>
> The image only carries the kernel, the device tree and the initramfs. Audio,
> SSH over USB and the memshare responder live in the rootfs and are installed
> by the scripts under `userspace/`; see [`docs/BUILD.md`](docs/BUILD.md).

> **The modem is functional but shipped off, so mobile data is not usable
> day to day.** It registers on LTE, mobile data reached ~24 Mbit/s, calls
> connect (there is no in-call *audio* yet, see below) and SMS arrives — the
> radio itself is done. The cause of two sessions of "the modem never leaves
> offline" turned out to be files, not silicon: the modem fetches run-time
> images from the AP over TFTP and every request was being answered "file not
> found". Install with `userspace/modem/install.sh` and read
> [`userspace/modem/README.md`](userspace/modem/README.md).
>
> **But it is shipped switched off**, because of a separate bug that only
> became reachable once the modem worked: with `ipa.ko` loaded *and the modem
> attached to LTE*, the SoC watchdog-resets every 3 to 10 minutes, silently.
> With the radio on but not attached it runs for 22 minutes; with `ipa.ko` out,
> the same. So `install.sh` drops `/etc/modprobe.d/rhodep-ipa-hold.conf` (and
> `rhodep-icc-hold.conf` for the interconnect provider), which costs mobile data
> and ModemManager but keeps the phone stable. Delete them and reboot to get the
> whole modem back, resets included.
>
> **Three hypotheses have been tested on the device and buried**: the unvoted
> NoC path to IMEM, that vote being demand-driven instead of a floor, and the
> IPA SRAM layout inherited from qcm2290. Patches 0044-0048 are all kept, every
> one of them a correction checked against the vendor tree, and none of them is
> the cause. Do not re-test them; §5 session 15 of HANDOFF-SESSION4.md says what
> was measured. **The experiment that would settle it has never run**: attach to
> LTE with `ipa.ko` never loaded. It needs the LTE attach APN set by hand over
> `qmicli`, because ModemManager is what normally configures it and MM will not
> touch a QMI modem with no net port.
>
> None of the modem work needs a new boot image — the IPA patches live in
> `ipa.ko`, a module — so **`kali-boot-v94-STABLE.img` is still the image to
> flash**. `kali-boot-v97-ipa-interconnect.img` exists for working on the reset:
> it adds the interconnect provider nodes and the IPA's three NoC paths to the
> device tree, boots identically to v94 because both drivers are held out, and
> has `README-v97.txt` next to it.

> **Bluetooth** was found with its service masked and a random controller
> address; `userspace/bluetooth/install.sh` fixes both. See that directory.

> In-call audio does not exist yet, and is not a modem problem: Qualcomm voice
> audio goes modem <-> ADSP <-> codec and mainline has no q6voice (MVM/CVS/CVP)
> at all. Scoped in HANDOFF-SESSION4.md session 14.

> **Building from a clean clone?** Read
> **[`docs/BUILD.md`](docs/BUILD.md)** first: it is the end-to-end checklist,
> including the external material (firmware, `ipa_fws`, the audio blob, the pmOS
> initramfs, the debos recipe) that a clone does not and cannot contain.

> **Picking this up, or handing it to someone else?** Start at
> **[`docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md`](docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md)**:
> §0 is the current state of the port (which image to flash, what works, what
> does not), §5 is the one thing still blocking mobile data, and §6 is how to
> build, flash and debug, including where every file lives and which of them are
> in `/tmp` and therefore disposable. Audio has its own document,
> [`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md).

## What works
- Boots to **Phosh** (mobile GUI), user `kali` / password `1234`
- **Plasma Mobile**, which is what this port is actually used on day to day —
  it is more comfortable than Phosh here. Installed with `apt install
  plasma-mobile` and picked from the GDM session menu; see "Extra sessions"
  below, and `extra-tools/terminal-keyboard/` for the on-screen keyboard work
  that makes its terminal usable.
- **Suspend and resume**, including waking with the screen intact. This needed
  kernel patch 0059 and a systemd sleep hook; before them the phone came back
  from a long screen-off with a permanently black display. See "Suspend" below.
- **Display** (Novatek NT37701 AMOLED 1080x2400, DSI + DSC command mode)
- **Internal WiFi + Bluetooth** (WCN3990)
- **Modem / ADSP / CDSP** remoteprocs
- **GPU** Adreno 619 (accelerated), touchscreen (Goodix GT9916S), buttons
- **Battery** (CellWise CW2217 gauge) + **charging** (SGM41542) with full thermal
  protection (kernel hot-side + userspace cold-side JEITA)
- **USB host / OTG** with VBUS from the SGM41542 charger → **external USB WiFi
  adapters** with **monitor mode + packet injection**, which the internal
  WCN3990 cannot do. Two are supported:
  - **TP-Link TL-WN722N v2/v3 (RTL8188EUS)** — single-band 2.4 GHz, via the
    `rtl8188eus` (`8188eu`) DKMS driver, see `packages/rhodep-rtl8188eus-fix`.
  - **TP-Link Archer T2U Nano (RTL8811AU)** — dual-band 2.4 + 5 GHz, via the
    `rtw88` mac80211 driver (`rtw_8821au`), see `packages/rhodep-rtl8821au`.

  Either shows up as `wlan1`; the internal `wlan0` is never touched.
- **microSD**, in the tray beside the SIM. Verified with a 16 GB card: it
  enumerates at UHS-I SDR104, 202 MHz on a 4-bit bus, and its vfat partition is
  readable. It needed no work at all -- `&sdhc_2` has had its supplies and
  card-detect GPIO since patch 0001, and nobody had put a card in to find out.
- **Docker**, and all NetHunter kernel features (WiFi USB injection drivers,
  BadUSB HID gadget, CAN, SDR, NFS)
- **Phone apps** (dialer, SMS, Contacts, file manager) via `mobian-phosh-phone`
  — installed by default in the rootfs build; usable once the modem is enabled
- **Audio out through the internal speakers**: both AW88261 amplifiers on
  Secondary MI2S, the bottom loudspeaker and the earpiece together, driven by
  the ADSP. Works from the browser and anything else that goes through
  PipeWire, with no commands to run by hand. **USB audio** works too.
- **Headphones** on the 3.5 mm jack, through the WCD9370 over soundwire, with
  **jack detection**: plugging in switches to the headphones and unplugging
  switches back to the speaker, automatically.
- **Microphone** (AMIC3), exposed as a PipeWire source, so recording works from
  any application, through the PulseAudio compatibility layer as well.
- **Modem** (bring-up done, but **shipped off — not usable day to day**):
  registers on LTE, mobile data measured at ~24 Mbit/s, voice calls connect
  (no in-call audio yet) and SMS arrives. It is held out of the boot because
  the SoC watchdog-resets a few minutes after attaching to LTE — see "What does
  not work" and the note at the top, and `userspace/modem/README.md`.
- **Bluetooth audio** (A2DP). The controller's address is stable across reboots,
  so pairings survive, but it is the firmware's default rather than the
  device's own assigned address — `userspace/bluetooth/`.
- **Sensors**: accelerometer, gyroscope, magnetometer, compass, proximity and
  ambient light, through `iio-sensor-proxy`, so **screen auto-rotation works**.
  They live on the SSC inside the ADSP (the IMU on I3C) and read their registry
  off the application processor over FastRPC; `userspace/sensors/` is the half
  that was missing. Contrary to what this repo said until now, that needed **no
  kernel change at all** — Qualcomm's own libadsprpc (`quic/fastrpc`, BSD-3)
  already speaks mainline's uapi.

## What does not work

| | |
| --- | --- |
| **Mobile data, day to day** | It works, and then the SoC watchdog-resets 3 to 10 minutes after the modem attaches to LTE. `ipa.ko` is therefore held out of the boot. This is the one thing standing between this port and a finished phone. HANDOFF-SESSION4.md §5, sessions 14-15. |
| **In-call audio** | Calls connect but there is no sound. Not a modem problem: Qualcomm voice audio goes modem ↔ ADSP ↔ codec and mainline has no q6voice (MVM/CVS/CVP) at all. A new driver, not a bug. |
| **Fingerprint** | Focaltech with a proprietary HAL (`fingerprint.focaltech.default.so`). No mainline driver. |
| **NFC** | Samsung `sec-nfc` on i2c7. No mainline driver. |
| **GPS** | **A satellite fix watchdog-resets the SoC in under a second**, reproducibly, with `ipa.ko` not loaded. It was never an indoors problem. Narrowed since: the trigger is the GNSS *measurement engine* coming up, so `standalone` mode kills the phone while **`cellid` positioning works** — a live session, fifty indications, position reports and NMEA, no reset (`scripts/rhodep-gnss-test.py 60 min opmode=cellid`). The same reproducer is now the fastest way to work on the LTE reset. [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md) |
| **Camera** | **Does not capture** — no photos, no video, no viewfinder. Only groundwork is done: the FAN53870 camera PMIC driver is written and running (all 7 LDOs registered, voltages verified against the chip's registers), and the rest is feasible because the ISP pipeline (`csid530` + `tfe530` + `tpg101`) is the *same silicon* mainline already drives on qcm2290, the 52 `gcc_camss_*` clocks are in mainline, and the 50 MP main sensor (Samsung S5KJN1) has a mainline driver. Still needed before any image: a `camss` entry for SM6375, the CSIPHY/CSID/TFE nodes and the sensor node. [`CAMERA-SENSORS-FEASIBILITY.md`](docs/CAMERA-SENSORS-FEASIBILITY.md) |
| **Monitor mode on the internal WiFi** | Infeasible: the WCN3990 firmware reports `raw 0`. Use the external adapter, which does work. |

## Bugs

Things that work but sometimes break, as opposed to the table above, which is
things that do not work at all.

**Bluetooth used to stop after a warm reset of the controller — fixed.**
Turning it off from the quick-settings shade and back on failed, took four or
five presses, and often needed a reboot. The cause was not the controller at
all: `icc_sync_state()` drops the INT_MAX floors `icc_node_add()` installs, and
on this SoC almost nothing declares interconnect paths, so every unvoted node —
including the QUP the Bluetooth UART runs on — went to zero the moment it fired.
The tell was the timing: the setups during boot always succeeded, because
`sync_state` had not run yet.

It was dormant until patch 0065 made the interconnect provider built in instead
of a blacklisted module; before that the provider never bound and the RPM's boot
vote stood. Fixed by patch 0088, which does not register `.sync_state` for this
SoC, plus 0086, which gives uart1 the `qup-core`/`qup-config` paths sm6115 has
and sm6375 never did.

Measured: 150 controller resets with zero failures, against failures within one
to six cycles before, and `qup0_core_master` holding INT_MAX instead of 0. The
bisect, the mechanism and the dead ends — link speed, transfer length, the power
pulse, the shared rails, rfkill — are in
[`docs/bluetooth-warm-reset-wip/README.md`](docs/bluetooth-warm-reset-wip/README.md).

**The WiFi is not lost during suspend — NetworkManager throws it away on
resume.** Leave the phone locked and the link is gone when you come back, so
the obvious reading is that the radio dies while the phone sleeps. Measured over
a **1586-second (26 minute) s2idle suspend**, that reading is wrong on both
counts.

At suspend entry NetworkManager unmanaged `usb0`, `qrtr0`, `wlan1` and both
`p2p-dev-*` devices and declared itself `ASLEEP` — and left **`wlan0` activated**.
The link survived the whole 26 minutes. What happened next is the bug:

	22:20:36  PM: suspend exit
	22:20:37  NetworkManager: sleep: wake requested
	22:20:37  device (wlan0): activated -> unmanaged (reason 'unmanaged-nm-disabled')
	22:20:37  wpa_supplicant: CTRL-EVENT-DISCONNECTED locally_generated=1
	22:20:37  wlan0: deauthenticating by local choice (Reason: 3=DEAUTH_LEAVING)
	22:20:45  wlan0: associated          <- eight seconds later, from scratch

Both halves are deliberate, and both are in `do_sleep_wake()` in NetworkManager's
`src/core/nm-manager.c`. On the way down:

	/* Wake-on-LAN devices will be taken down post-suspend rather than pre- */
	if (suspending && device_is_wake_on_lan(priv->platform, device))
		continue;

and on the way back up:

	/* Belatedly take down Wake-on-LAN devices; ideally we wouldn't have to do
	 * this but for now it's the only way to make sure we re-check their
	 * connectivity. */
	if (device_is_wake_on_lan(priv->platform, device))
		nm_device_set_unmanaged_by_flags(device, NM_UNMANAGED_MANAGER_DISABLED, ...);

`device_is_wake_on_lan()` asks the platform, which for a wifi link means WoWLAN,
so `etc/NetworkManager/rhodep-wowlan.conf` is what puts `wlan0` on this path.
**That file is working exactly as intended** and should be kept: it is the reason
the link stays up across the suspend at all, and therefore the reason a magic
packet can wake the phone. Without it NM tears the interface down *before*
suspending instead, which is strictly worse.

There is no setting for the resume teardown. It is unconditional, and upstream's
own comment calls it a workaround. But it does not read configuration either —
`device_is_wake_on_lan()` asks the *hardware* — so it is enough for WoWLAN to
look disarmed at the instant NM asks. **Fixed, without patching NM**, by
`userspace/power/systemd/system-sleep/rhodep-wowlan`: disarm on resume, re-arm
two seconds later.

The ordering this depends on is guaranteed rather than lucky. systemd-sleep runs
these hooks synchronously and logind only emits `PrepareForSleep(false)` once it
exits, so the hook always finishes before NM looks:

	22:55:29.919  PM: suspend exit
	22:55:29.960  rhodep-wowlan: WoWLAN desarmado en phy0
	22:55:32.011  rhodep-wowlan: WoWLAN rearmado en phy0
	(no takedown, no deauth, no re-association)

Measured across a suspend: association time went 752 s → 788 s, continuous, where
before it reset to zero. The phone was woken by a magic packet and `gpio-keys`
never moved, so WoWLAN still works with the hook in place.

Three things this cost, all worth knowing before touching it:

- **The re-arm cannot be a backgrounded child.** The first version used
  `setsid sh -c "sleep 10; ..." &`; `systemd-suspend.service` deactivated 380 ms
  later and systemd killed its cgroup with the child inside. setsid escapes the
  session and the TTY, not the cgroup. It runs under `systemd-run` now, which
  gets a transient unit of its own.
- **A `delay` inhibitor around the window is impossible from here.** logind
  refuses it — *"Failed to inhibit: The operation inhibition has been requested
  for is already running"* — because the hook runs inside the very sleep
  operation it would be inhibiting. Asking for it failed the whole unit in 38 ms
  and left WoWLAN off, which is worse than the race it was meant to close.
- Both failures ended the same way: **WoWLAN disarmed permanently**, so the phone
  could not be woken over the network at all. That is why the safety net is
  `rhodep-wowlan-check.timer`, which reconciles the state every minute instead of
  trusting a moment, skipping the resume window via `/run/rhodep-wowlan.resuming`.

The alternative, for the record, was patching NM or taking `wlan0` away from it
and driving wpa_supplicant directly — which already does the right thing here,
logging *"Do not deauthenticate as part of interface deinit since WoWLAN is
enabled"* just as NM pulls the rug out from under it.

**The USB adapter has a second, unrelated version of this.** Across the same
suspend the dongle was never de-enumerated — VBUS held, `role=host` held, and the
kernel logged `usb 1-1: reset high-speed USB device number 2`, a reset of the
same device on the same port, not a re-enumeration. But `rtw_8821au` reloads
firmware on that reset, so the phy is rebuilt: `phy1` disappears, `phy2` appears,
and NM registers a **new** `wlan1`. Anything pinned to the old interface — a
capture, monitor mode, a MAC — is gone. Checking that `wlan1` still exists after
a resume does not tell you anything; check the phy index.

The practical consequence for auditing work is that a capture must not be allowed
to suspend in the first place, which is what `rhodep-keep-awake` and its
AF_PACKET detection are for.

**Both halves have now been confirmed away from the bench.** A walk with the
screen locked and the TP-Link scanning through OTG: the WiFi stayed up, and the
capture on the dongle ran through without the adapter dropping or coming back as
a new interface. That is the scenario this whole section was written from -- it
had only ever been reproduced while plugged in and sitting on a desk, and the
one thing that could not be tested there was whether it survives being carried
around.

**Waking the phone over WiFi works, and has been demonstrated end to end.** Until
now WoWLAN was only known to be *armed* -- `iw phy0 wowlan show` reports the magic
packet -- which is not the same as it working. Forced a suspend with
`systemctl suspend`, confirmed the phone was unreachable over both ssh paths, sent
a magic packet, and it came back on its own:

	suspend count       2 -> 3     (it really suspended)
	gpio-keys wakeups   2 -> 2     (nobody touched the button)
	"Power key pressed" 0 times in the journal
	22:36:37 suspend entry -> 22:37:25 suspend exit

Two things worth keeping. The packet was **unicast, from a different subnet**
(a 172.18.0.0/16 container routed to the phone's 192.168.1.0/24) -- no broadcast
and no shared L2 segment needed, because the magic pattern is matched in the
payload. So anything with a route to the phone can wake it. And this is the same
reason ordinary traffic does *not* wake it: during the earlier 26-minute suspend
the association was alive and ssh SYNs were reaching the radio, but the firmware
drops everything that is not the magic pattern without ever waking the CPU. A
phone that ignores ssh while suspended is the feature working, not a fault.

Suspend with `systemctl suspend` when testing this. Never `rtcwake -m mem`: it
bypasses `/usr/lib/systemd/system-sleep/rhodep-adsp`, the sensors PD asserts, and
it takes the ADSP and audio down with it until a reboot.

## Where to pick this up

1. **The SoC reset, with GNSS as the trigger.** A live GNSS session kills the
   SoC in **under 100 ms**, with no SIM, no IPA, no ModemManager, no network and
   the WWAN radio switched off — `sudo scripts/rhodep-gnss-test.py 40 min` and
   the phone reboots, every time. The LTE reset needs `ipa.ko` plus an LTE
   attach plus 3 to 10 minutes of waiting, so if they share a cause the
   expensive bug now has an instant reproducer.

   **Bisected one step further, and this is where to start.** Add
   `opmode=cellid` and the identical session **survives** — twenty-five
   seconds, fifty indications, position reports and NMEA. So the trigger is the
   GNSS *measurement engine* being switched on, and not the session, the
   indications, QMI or "the session staying alive" as this file used to say.
   Put next to the LTE reset needing a *real attach* rather than merely LTE
   selected, both look like the same thing: the modem switching on a hardware
   engine, while the Q6 running software is demonstrably fine. Read
   `docs/watchdog-ipa-lte-wip/HANDOFF.md` first — the leading candidate is now
* `docs/modem-diag-wip/HANDOFF.md` — getting the modem's DIAG protocol working: what is
  established, the two patches that came out of it, and what to do next.
   QDSS/CoreSight, which is 110 device tree nodes downstream and zero in
   mainline.

   Already bisected, on the device, one reboot per row: it is not the
   indications (an empty event mask still resets), not the optional start TLVs,
   not `QMI_LOC_START` as a message (`qmicli` sends it and survives, because it
   frees the client and the session dies with it), and not the WWAN radio. **It
   is the session being allowed to stay alive**, and nothing else. Dynamic debug
   on qrtr/glink/q6v5/sysmon produces not one line before the console stops.

   Also excluded, one reboot each: `removed_mem` being 32 MB short (fixed in
   v98, still resets), clocks and power domains Linux switches off as unused
   (`clk_ignore_unused pd_ignore_unused`, v99, still resets), memshare, a
   missing VDD_MX vote, and any AP-side SMMU stream id. **Every CPU wedges at
   once**, which points at a hung bus rather than one stuck core.

   **The AP has been cleared, with evidence.** An instrumented kernel
   (`kali-boot-v100-instrumented.img`, with `HARDLOCKUP_DETECTOR_BUDDY` +
   `PSTORE_FTRACE` and the ramoops region re-split so ftrace has somewhere to
   land) shows all eight cores sitting in `do_idle` at the moment of death, the
   last real work being the modem's own glink reply to the START. No panic is
   ever reached — `kmsg_dump` is never called — and the buddy detector never
   fires, which can only happen if **every CPU stops in the same instant**.
   Loading `qnoc-sm6375` so the NoC provider is bound changes nothing either.

   So this is a hardware-level reset of the whole SoC, started somewhere the AP
   cannot observe: the mpss, TrustZone, or a bus error escalated by hardware.
   More AP-side instrumentation will not help; the next step needs modem-side
   visibility (DIAG/QXDM or a modem ramdump).
   [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md)

2. **`removed_mem` is 32 MB short — tested, real, and not the cause.** Mainline
   takes the size from shima/yupik instead of blair, so Linux gets 32 MB the
   secure firmware owns. Confirmed and fixed in
   `kali-boot-v98-removed-mem.img`; the reset survived it. Keep patch 0049
   anyway and send it upstream, because handing out firmware memory is wrong on
   its own terms and would waste somebody's session later.
   [`REMOVED-MEM-SM6375.md`](docs/interconnect-sm6375-wip/REMOVED-MEM-SM6375.md)
3. **The IPA reset itself**, if items 1 and 2 come back negative. Read §5
   session 15 of HANDOFF-SESSION4.md first: three hypotheses are already buried
   with evidence and should not be retried. The experiment to run is attaching
   to LTE with `ipa.ko` never loaded, which needs the attach APN set by hand
   over `qmicli`.
4. Everything else in the table above is a project rather than a task.

> GPS used to be item 1 here, described as "five minutes, no risk". That was
> wrong, and how it was wrong is worth knowing before trusting any other "it
> almost works" line in this file: `qmicli` cannot start a GNSS session and a
> listener in the same QMI client, and when it fails it fails **quietly** —
> no NMEA, no error. That reads exactly like a GPS with no sky view, which is
> what everyone concluded. Doing it properly, from one client, resets the phone.

## Screenshots

| Phosh desktop (Kali tools) | `otg on` + monitor mode | wifite scanning |
|---|---|---|
| ![Phosh desktop](docs/screenshots/phosh-desktop.png) | ![OTG + airmon-ng](docs/screenshots/otg-airmon-monitor.png) | ![wifite](docs/screenshots/wifite-scan.jpg) |

- **Left**: Phosh app drawer running on the phone — Kali tools (NetHunter,
  Wireshark, Ophcrack, Guymager, ...), a terminal, battery/WiFi in the top bar.
- **Middle**: `otg on` powers the USB port (SGM41542 VBUS boost), then
  `airmon-ng start wlan1` puts the external TP-Link (RTL8188EUS) into
  **monitor mode** — alongside the internal `wlan0` (ath10k). With the
  `rtl8188eus`/`8188eu` DKMS driver, `aireplay-ng --test` injection works too.
- **Right**: `wifite` scanning nearby APs through the USB adapter.

## Extra tools (`extra-tools/`)

Things written after the hardware was working, when the obstacles stopped
being the device and started being software that assumes a desktop. None is needed
to boot or to place a call; each has a detailed README of its own.

**`terminal-keyboard/`** — Plasma's on-screen keyboard has no Esc, no Tab, no Ctrl
and no arrows, which makes a terminal close to unusable. This adds them as a row on
every page, with Ctrl and Alt as modifiers that arm rather than type, so any
combination is reachable.

The detail worth knowing: modifiers cannot be delivered as modifiers at all.
`plasma-keyboard` sends the protocol's modifier field as a hardcoded `0`
(`src/inputlisteneritem.cpp`) and KWin ignores it regardless, deriving modifiers
from the keymap — which is why an early attempt at Ctrl+C arrived as a capital `C`.
So these keys send **control characters as text**: Ctrl+C on a tty is not a
combination, it is the byte `0x03`. All 52 of Qt's layout files are transformed at
install time and each is verified with `qmllint`, keeping Qt's original whenever a
transform cannot be verified — a layout that will not load is a phone you cannot
type on.

**`terminal-clipboard/`** — `Ctrl+V` pastes and `Ctrl+Shift+C` copies in the
terminal, as zsh widgets.

The detail: this cannot live in the keyboard. The keyboard has no keyboard focus,
so KWin never sends it a clipboard offer — a protocol trace contains not one
`data_offer`, and every read came back empty. The shell *can* reach the clipboard,
and the keyboard can send bytes, so the work happens in the shell. `Ctrl+Shift+C`
has to travel as `Esc` + `C` rather than as a control character, because control
characters ignore case: `Ctrl+C` and `Ctrl+Shift+C` are both `0x03`, and `0x03` has
to keep cancelling.

**`claude-free/`** — Claude Code, running on whatever model providers opencode
already holds keys for, including opencode Zen's free models, which need no key at
all.

The detail: Claude Code speaks exactly one API shape and lets you move it with
`ANTHROPIC_BASE_URL`, so a small proxy translates between that and the OpenAI shape
every other provider speaks, routing by `provider/model` and reading opencode's own
`auth.json`. Anthropic is passed through untranslated, since with an Anthropic key
the request is already the right shape. The real work is the streaming direction:
Claude Code expects Anthropic's event sequence with tool calls as
`input_json_delta` fragments, which is a state machine rather than a field rename.

**`pwnagotchi/`** — the WiFi-auditing agent (jayofelony fork) capturing WPA
handshakes on the phone. Verified in auto mode: bettercap on `wlan1mon` sees
dozens of APs and it captured 11 handshakes in a short run.

The detail worth knowing: pwnagotchi is built for a Raspberry Pi whose only WiFi
does monitor mode, and this phone is the opposite. **wlan0** (internal WCN3990)
is the only real WiFi and cannot do monitor mode at all, so it has to stay a
managed client; **wlan1** (external TP-Link RTL8188EUS) is the one that does
monitor and injection. So everything is pinned to `wlan1mon` and wlan0 is never
touched: the monstart helper works on wlan1 by name only, refuses to run if
pointed at wlan0 or if wlan1 shares a phy with it, and never runs
`airmon-ng check kill` (which would drop wlan0 for nothing). The three services
(`rhodep-pwn-bettercap`, `rhodep-pwngrid-peer`, `rhodep-pwnagotchi`) are disabled
at boot and started on demand, because capture needs `otg on` and the external
adapter anyway. Its face and status are on the web UI at `http://<phone>:8080`.

**`nethunter-pro-app/`** — the NetHunter Pro control panel, a GTK4/libadwaita
app for Phosh that drives the port's tools from a touch UI instead of a
terminal: pwnagotchi, wifipumpkin3, Phishkin3 (wp3 + evilginx2), CARsenal (CAN
bus), nmap, HID/BadUSB attacks, an evil twin, VNC, Docker, and the rest. Each
is a module screen.

The Docker screen keeps the engine off until asked. Start and Stop drive
**both** `docker.service` and `docker.socket` -- the socket first on start so the
service can bind it, the service first on stop so nothing socket-activates it
straight back up -- and both are disabled at boot so nothing holds power until
asked. A **Downloaded images** list shows every pulled image with a per-image Run or
Stop button, so several can be started and stopped independently -- start one,
start another, stop one, come back later. Stop removes the container but keeps
the image, so it can be run again; the state is read live from `docker ps`. There
is a **Clean everything** button that wipes all Docker data for the
run-on-demand-then-clean workflow: every container, image, network, the build
cache, and all volumes including named ones (which `system prune --volumes` does
not touch), behind a confirmation. Run is "inteligente" only as far as the
image's own metadata allows: exposed ports come from the pulled image's `EXPOSE`
list (not a README, which is free text), are published with `-p`, and a web port
among them is surfaced as a URL. Tested end to end with `bkimminich/juice-shop`:
pull, EXPOSE 3000 detected and published, container up, HTTP 200 on
`http://127.0.0.1:3000`; and the wipe verified to leave zero containers, images
and volumes.

The **Phishkin3 (evilginx)** screen turns the multi-step wifipumpkin3 +
evilginx2 attack (https://docs.wifipumpkin3.com/blog/tutorials/phishkin3) into
three inputs: phishlet, look-alike domain, interface. The orchestrator lives in
`helper/rhodep-phishkin3-launch`. It configures evilginx by feeding its shell
commands on stdin (evilginx v3 keeps phishlets and lures in a BuntDB store, not
a config file, so pilot-by-stdin is the reliable way), writes the wp3 `.pulp`
with the phishkin3 proxy and DNS spoof, spoofs `/etc/hosts`, and launches both
tools in a `tmux` session named `phishkin3` so they survive and can be attached
for logs. 95 community phishlets ship pre-installed under
`/usr/share/evilginx2/phishlets/` (from Whispergate, jeanlucndato and
hash3liZer), including Instagram, Facebook, Google, GitHub and LinkedIn.

The domain is never the real one -- HSTS preload refuses a spoof of the real
name. The screen suggests look-alikes: a plain one (`instagram-login.com`) and
homoglyphs, Cyrillic and dotless-i characters that read as the original but are
a different domain. The launcher converts these to punycode, which is what
evilginx, the DNS spoof and `/etc/hosts` actually use: `instagrаm.com` with a
Cyrillic a becomes `xn--instgram-46g.com`, verified end to end.

Ten separate bugs had to be worked around, all committed and worth reading
because they name traps the next attacker will hit:

  * The evilginx config directory has to be cleared each run (a stale `data.db`
    accumulates lures and hostnames from previous runs, so `get-url 0` returns
    the wrong lure for the current phishlet -- Instagram tripped over this).
  * The DNS spoof needs every subdomain listed explicitly. `add *.<domain>`
    is written to the zone file but pydns does not match it against a real
    query for `www.<domain>`: the log shows `no local zone found, proxying
    www.<domain>` and the browser gets `ERR_NAME_NOT_RESOLVED`. Every landing
    hostname from the phishlet's `proxy_hosts` gets its own `add` line.
  * phishkin3's gate allows only port 8080 to the AP; the launcher adds an
    `iptables -I FORWARD` for 443 to the AP so the browser can reach the lure
    that `/login` 302s to.
  * evilginx's blacklist runs in `unauth` mode by default. Every victim's
    traffic transits through the AP, so a stray probe or a curl during setup
    lands the AP IP on the list, after which every real visitor gets served
    the `unauth_url` (a Rickroll). The launcher sends `blacklist off` on
    startup and clears `blacklist.txt` alongside the config each run.
  * The stock Instagram phishlet's `sub_filters` are a no-op (search and
    replace are both `https://{hostname}/`), so the HTML served to the
    browser keeps hardcoded links to `www.instagram.com` and the browser
    fetches static assets from the real Instagram through the DNS spoof.
    A fixed phishlet lives under `phishlets/instagram.yaml` and gets copied
    into place.
  * NetworkManager still owns `wlan1` while wp3 puts it in AP mode, so it
    disconnects the interface in a loop -- SSID flashes on and off. `nmcli
    device set <iface> managed no` before start, `managed yes` on Stop.
  * `wifipumpkin3`'s `-iNM` is a *standalone action*, not a flag. Passed with
    `-p`, wp3 ignores the interface and exits before touching the pulp; the
    log shows `The interface wlan1 has been ignored successfully` and then
    nothing, and tmux dies with `no server running`. The nmcli call above is
    enough; `-iNM` must not be added to the wp3 invocation.
  * `pkill -f phishkin3` would match the launcher itself, which is called
    `nethunter-pro-phishkin3-launch`. The reset used narrow patterns instead:
    `pgrep -f 'wifipumpkin3 -p'`, `pgrep -f 'plugins/bin/phishkin3'`, and
    `pkill -x` for evilginx/hostapd/dnsmasq. The `pgrep` variants also filter
    out the launcher's own pid.
  * Half-cleaned state between launches (duplicate FORWARD ACCEPT rules, an
    already-open tmux session) stops wp3 from ever driving phishkin3 to a
    listening state -- the AP comes up but there is no captive portal.
    `full_reset` runs before every Launch, whether Stop was pressed or not.
  * evilginx's `-developer` mode signs certs with a CA called literally
    "Evilginx Super-Evil Root CA". Android will never trust that automatically.
    Installing the CA on the victim (Settings → Security → Install certificate)
    works for browser traffic in a lab, but not for apps: since Android 7,
    Chrome and system apps ignore user-installed CAs by policy, which was
    Google's mitigation for exactly this attack. The realistic path for real
    victim-facing use is a public look-alike domain and Let's Encrypt via
    autocert (without `-developer`) -- the launcher now auto-detects an
    installed cert and switches modes; see below.

Ten more traps came out of getting Instagram's login form to actually render
end to end -- the "stuck on the logo" bug. Each was hunted with tcpdump, tmux
`capture-pane` on the evilginx window, and `tshark` to read SNI off the client's
`ClientHello` frames; every fix is committed and worth reading because the
default configuration fails silently on all of them.

  * The stock Instagram phishlet only proxies `www.instagram.com` and
    `m.instagram.com`. Instagram's login HTML links every JS/CSS bundle that
    renders the form with absolute URLs at `static.cdninstagram.com` (verified:
    488 references in the served HTML), so with the CDN unproxied the browser
    fetches those bundles from the real CDN under the wrong origin and the
    React SPA never mounts the form -- the page stops at the Instagram logo.
    The phishlet under `phishlets/instagram.yaml` now proxies
    `static.cdninstagram.com`, `scontent.cdninstagram.com` and
    `i.instagram.com` as extra `proxy_hosts` with `auto_filter: true`, and adds
    explicit `sub_filters` for the CDN host so the served bundles reference
    `static.<phishdomain>` and stay on the phishing origin.
  * Evilginx v3.3.0 only generates a phishing vhost and a per-host cert for
    `proxy_hosts` marked `session: true`. With `session: false` the CDN hosts
    get no vhost, so the browser's request to `static.<phishdomain>` hangs
    (`http 000`, TCP connects but no TLS response) and the login bundles never
    load -- same "stuck on the logo" symptom. All CDN hosts in the fixed
    Instagram phishlet are `session: true`; the flag does not widen credential
    capture because `auth_tokens` still scopes what is harvested.
  * With the full 96-phishlet directory shipped in `/usr/share/evilginx2/`,
    `phishlets get-hosts instagram` silently omits the extra CDN hosts -- only
    `www.instagram-login.com` and `m.instagram-login.com` appear. `strace` on
    evilginx confirms every YAML is parsed on start; the omission is not a
    parse error but a downstream collision/limit that only triggers past
    ~60-70 loaded phishlets. Isolating the same YAML in a directory with only
    a handful of phishlets makes all five CDN hosts appear in `get-hosts`
    immediately. The port ships only the four phishlets that are actually
    exposed by the app (`instagram`, `facebook`, `outlook`,
    `google-botguard-bypass`); the rest are backed up to
    `phishlets.all.bak/` in case something else is needed later.
  * The USB Realtek RTL8811AU dongle (`rtw_8821au`, e.g. the TP-Link Archer
    T2U Nano) tears itself down mid-attack when WiFi power-save is on: `dmesg`
    shows repeated `wlan1: entered promiscuous mode` / `left promiscuous
    mode`, then `usb 1-1: reset high-speed USB device` /
    `(unregistering)`, and the AP silently dies (`client has left AP`, SSID
    gone, hostapd left orphaned). `iw dev wlan1 set power_save off` before
    hostapd binds fixes it. The launcher runs this in both
    `release_from_networkmanager` and again in `start_in_tmux` after the AP is
    up, because wp3 re-creates the interface when it switches it to AP mode
    which resets `power_save` to `on`. USB autosuspend is also forced off on
    every USB device (`echo on > /sys/bus/usb/*/power/control`) to keep the
    kernel from parking the dongle.
  * `/etc/resolv.conf` on the port ships with `nameserver 127.0.0.11` (a
    ghost from a Docker install that is not running). Nothing listens there,
    so every DNS resolution the host does -- including every upstream lookup
    evilginx makes to reach Instagram's real backend -- times out on the first
    server before falling back to the second. Symptoms are 5+ second first-byte
    times on every proxied request and CDN bundles that never finish loading
    within the browser's own timeouts. The launcher rewrites `/etc/resolv.conf`
    to `1.1.1.1` + `8.8.8.8` at start and disables IPv6 (`sysctl
    net.ipv6.conf.all.disable_ipv6=1`), because Go's default dialer prefers
    the AAAA record when both are present and the port has no working IPv6
    route to Meta's CDN.
  * evilginx v3.3.0 in `-developer` mode refuses `ClientHello`s with SNI it
    does not know: `WARN: Cannot handshake client static.cdninstagram.com
    remote error: tls: unknown certificate`. What actually reaches evilginx
    (verified by `tshark -Y tls.handshake.type==1` on every interface) is only
    the phishing-domain SNIs the browser was told about; the log line
    identifies the *upstream* host evilginx tried and failed to reach, not the
    incoming SNI. It is emitted from the same code path that later, in
    non-developer mode, becomes the real bug: the browser refuses to trust the
    self-signed cert on any subresource -- there is no click-through UI for
    `<script src>` -- and the login form stays a logo. See the Let's Encrypt
    section below for the fix; installing the developer CA on Android does
    NOT help, because Chrome only trusts user-installed CAs for top-level
    documents, not for JS-loaded subresources.

**Real certs, no developer CA.** The launcher auto-detects a real (Let's
Encrypt-issued or otherwise CA-signed) cert under
`crt/sites/<phishdomain>/fullchain.pem` + `privkey.pem` in evilginx's config
directory. If present, it feeds `config autocert off` on stdin during setup and
starts evilginx *without* `-developer`, so evilginx serves the disk cert
instead of the "Super-Evil" self-signed one -- the browser trusts the whole
`*.<phishdomain>` cert chain, top-level *and* every asset subdomain, and the
login form renders. If no cert is present the launcher falls back to
`-developer` (self-signed) so nothing breaks; see `has_real_cert()` in
`rhodep-phishkin3-launch`.

The phishkin3 module in the app surfaces installed-cert domains at the top of
the look-alike domain picker so the default choice is a domain the browser
will actually trust. The GUI runs as the login user (not root) and so it
cannot read the cert store under `/root/.config/nethunter-phishkin3/evilginx/
crt/sites/` directly; instead the launcher mirrors the list of loadable
domains as empty marker files under `/var/lib/nethunter-phishkin3/certs/`
(mode 0644 on a 0755 dir) on every run, via `refresh_cert_index()`. The
picker reads that world-readable directory. Elevating the whole app under
`pkexec` was tried first and rejected: kwin on the Plasma-Mobile port refuses
Wayland connections from a UID that does not own the session, so
`Gdk.Display.get_default()` returns None under root and the app aborts with
`TypeError: Argument 0 does not allow None as a value`. The per-action
`ToolRunner(..., root=True)` path with `allow_active: yes` in the polkit
rule is enough for the privileged work.

The end-to-end flow that actually works (tested with `cdninstagram.dedyn.io`):

  1. Register a free public domain -- **deSEC.io** (`*.dedyn.io`) is the pick:
     wildcard-capable, native DNS API, and gratis. DuckDNS also works and
     avoids deSEC's DNSSEC replication lag but the domain is longer. The
     Let's Encrypt validation is DNS-01, so the port never has to expose any
     port to the internet -- only a TXT record propagates through the
     provider.
  2. Install `acme.sh`:
     `curl https://get.acme.sh | sh -s email=<a-real-email>` (Let's Encrypt
     rejects `example.com` addresses; use `--nocron` if no cron is present).
  3. Issue the wildcard with the deSEC plugin, `--dnssleep 180` or higher --
     deSEC's own devs document their DNSSEC replication lag on their forum
     (`talk.desec.io/t/ns1-desec-io-replication-issues/804`); the default
     `--dnssleep` fails with `DNSSEC: Bogus: validation failure ... covering
     NSEC3 was not opt-out` and the challenge times out. 180-300s consistently
     succeeds:
     ```
     export DEDYN_TOKEN=<token-from-desec.io>
     acme.sh --issue --dns dns_desec \
         -d cdninstagram.dedyn.io -d '*.cdninstagram.dedyn.io' --dnssleep 180
     ```
  4. Install it under evilginx's cert dir with the exact filenames the
     `setUnmanagedSync` loader in `core/certdb.go` looks for (`fullchain.pem`
     + `privkey.pem`; the folder name under `crt/sites/` is a container and
     does not have to match SNI, since certmagic keys off the cert's own
     SANs):
     ```
     acme.sh --install-cert -d cdninstagram.dedyn.io --ecc \
         --fullchain-file <cfg>/crt/sites/cdninstagram.dedyn.io/fullchain.pem \
         --key-file       <cfg>/crt/sites/cdninstagram.dedyn.io/privkey.pem
     ```
  5. Launch: `rhodep-phishkin3-launch --phishlet instagram --domain
     cdninstagram.dedyn.io --interface wlan1 --interface-net wlan0 --start`.
     The launcher logs `cert=letsencrypt/unmanaged` at setup and starts
     evilginx without `-developer`. `openssl s_client -servername
     www.<phishdomain>` should show `issuer=... Let's Encrypt` on every
     subdomain the phishlet uses; that is what the browser gets, no
     click-through warning at any level.

Two things still bite the victim path even with a valid cert, and both are
about how Android treats the AP:

  * **Captive portal WebView.** Android sends `connectivitycheck.gstatic.com`
    over HTTP:80 to sniff for internet on join; the phishkin3 DNAT catches
    that on `--dport 80 -j DNAT --to 172.16.0.1:8080` and the phishkin3 Flask
    app 302s it to the lure URL. Android then opens the lure inside its
    built-in captive-portal WebView, not Chrome, and that WebView is a
    stripped-down runtime: Instagram's SPA loads the HTML+logo and never
    finishes bootstrapping. The victim has to dismiss the captive-portal
    banner ("Use network as is" / "Sin internet") and open the lure in real
    Chrome. This is inherent to how phishkin3 uses the captive-portal
    detection as a delivery vector; documenting it is the fix.
  * **Public A record for the phish domain.** The AP's `dns_spoof` answers
    `www.<phishdomain>` -> `172.16.0.1` for its clients, but Chrome on Android
    routinely does DNS-over-HTTPS to Cloudflare/Google, not to the AP's
    resolver. If the phish domain has no public A record, DoH returns
    `NXDOMAIN` and Chrome shows `DNS_PROBE_FINISHED_NXDOMAIN` -- the AP's own
    resolver is bypassed entirely. The pragmatic fix, since the AP IP is
    private (`172.16.0.1`), is to publish that private IP as the public A
    record for every subdomain the phishlet uses (`apex`, `www`, `static`,
    `scontent`, `i`, `m`). Public resolvers happily serve private IPs; the
    result is the same `172.16.0.1` whether the client asked the AP's spoof
    or Cloudflare's DoH, and Chrome connects to the AP either way. TTL 3600
    (deSEC minimum is 900).

Also worth documenting because it is not obvious: nethunter-pro-app also
carries `rhodep-make-captiveportal`, a generator that turns a git repo of a
static login page into a wifipumpkin3 captive portal (clones, folds
`index.html` into `templates/login.html`, moves css/js/images under `static/`
and rewrites paths, forces the first `<form>` to POST with `name=login` /
`name=password` so captiveflask captures, writes the plugin `.py`, installs
into place and deletes the clone). It is invoked from the wifipumpkin3 screen
and reports honestly when the source has no `<form>` and only submits via
JavaScript, since a portal that silently captures nothing is worse than one
that admits it cannot.

The detail worth knowing: anything needing root goes through a persistent DBus
helper (`org.kali.NetHunterPro.Helper`) so the password is asked once, falling
back to `pkexec` if the helper is absent — the UI thread never runs privileged
code itself. Three module icons are shipped by the app because no installed icon
theme has them (a car for CARsenal, a pumpkin for wifipumpkin3, the pwnagotchi
face), symbolic so they recolour with the theme. The pwnagotchi screen edits the
plugin config live with `tomlkit` to keep comments and formatting, reads the
wpa-sec upload state from the plugin's own SQLite db so its "upload now" and
"free space" buttons agree with the plugin, and only ever deletes captures
wpa-sec has finished with. Note the app is installed with `pip`, so it lands in
the current `python3`'s site-packages: a Python minor-version bump (3.13 → 3.14
happened once) leaves the module behind and the launcher fails with
`ModuleNotFoundError` until it is reinstalled for the new version.

**`cleanup/`** — a weekly disk-space cleanup and a permanent cap on the systemd
journal. The disk had drifted to 50G used, ~2G of it junk that comes straight
back with use, and the journal alone was 633M.

The detail worth knowing: it deletes only regenerable things — caches (GPU
shaders, thumbnails, browser, npm), apt archives, coredumps, fwupd metadata,
rotated logs, `/tmp` — so **no apt hold is affected**, because dpkg owns none of
those files and the holds protect packages. It never touches data (the `/opt`
toolset, libpostal, the databases, the clamav signatures, the AI tools' state).
A journald drop-in sets `SystemMaxUse=100M` so the journal cannot grow back
between runs; the weekly `rhodep-cleanup.timer` reclaims the rest, catching up on
the next boot if the phone was off (`Persistent=true`). Run it by hand with
`sudo rhodep-cleanup`, or `--dry-run` to see what it would free.

## Known limitations
- **Glitched lines while the brightness moves**: a band of corrupted lines
  appears while the brightness ramps and the screen is repainting. Both halves
  are needed — scrolling alone produces nothing, and moving the brightness with
  a still screen produces nothing. Measured with
  `scripts/rhodep-glitch-capture`: twenty errors in ninety seconds of use, all
  of them within 300 ms of a backlight write.
  The cause is characterised: a DCS write lands on top of the pixel stream and
  all four DSI HS lanes underflow. `dsi_ctrl_enable()` already asks the
  controller to hold command DMAs out of a frame
  (`DSI_TRIG_CTRL_BLOCK_DMA_WITHIN_FRAME`) but never programs the window it may
  use instead, because that register is not in the map mainline generates its
  headers from. Four attempts at supplying it are written up in
  `docs/display-cmd-dma-window-wip/`, including two that left a black screen and
  one that made things worse; read that before trying a fifth.
- **Mobile data**: the modem itself is finished. It registers on LTE, data was
  measured at ~24 Mbit/s, calls connect and SMS arrives. What blocks day-to-day
  use is a separate bug reachable only now that the radio works: with `ipa.ko`
  loaded **and** the modem attached to LTE, the SoC resets, silently and within
  seconds. **It is specific to LTE.** With the modem restricted to GSM
  (`qmicli -d qrtr://0 --nas-set-system-selection-preference="gsm"`) the same
  configuration registers, attaches, is managed by ModemManager, receives SMS
  and does not reset. So the AP side -- the driver, the memory layout, the QMI
  handshake, the endpoint configuration -- is right, and so is the data path: on
  GSM a GPRS bearer comes up, real traffic flows through IPA's channels with
  zero errors and zero drops, and it survives reboots. What breaks is specific
  to an LTE attach.

  **Mobile data, voice and SMS therefore work today**, at GPRS speed, by
  installing `userspace/modem-gsm-only/`. It pins the modem to GSM before
  ModemManager can touch it and takes the IPA hold out of the way, which is
  what stopped ModemManager managing the modem at all. The cost is that there
  is no LTE and no 3G -- 3G never registered in testing. `docs/watchdog-ipa-lte-wip/` has the measurements
  and what they rule out.

  That also means voice and SMS are reachable today at the cost of data: the
  "no dialer, no SMS UI" part of this limitation came from ModemManager
  refusing a modem with no net port, and on GSM with IPA loaded there is one.
  It is not the default here because the mode preference lives in the modem's
  own NV, and anything putting it back to LTE brings the reset back. Either condition alone is stable (22 and 16.5 minutes
  observed). So `userspace/modem/install.sh` ships
  `/etc/modprobe.d/rhodep-ipa-hold.conf`, and `rhodep-icc-hold.conf` for the
  interconnect provider, which keeps both drivers out of the boot. The cost is
  that `rmnet_ipa0` never appears and ModemManager, which refuses a QMI modem
  with no net port, does not create the modem at all — so no dialer, no SMS UI
  and no `mmcli`. QMI itself still answers directly
  (`qmicli -d qrtr://0 --dms-get-ids`). Delete the file and reboot to get the
  whole modem back, resets included. HANDOFF-SESSION4.md §5, sessions 14-15.
- **The SIM was never the problem.** Older revisions of this file blamed a
  SIM/UIM fault for the modem not registering (`DeviceNotReady`, no ATR on a
  known-good card). That was wrong: the modem fetches its run-time images from
  the AP over TFTP and every request was being answered "file not found", so it
  never left `offline`. `userspace/modem/` populates the remote file system
  from the stock `modem`/`fsg`/`persist` partitions, serves
  `/readonly/vendor/fsg/` from a patched tqftpserv and opens the UIM
  provisioning session. Nothing about the card had to change.
- **In-call audio does not exist**, and it is not waiting on the modem. Qualcomm
  voice audio goes modem ↔ ADSP ↔ codec and mainline has no q6voice (MVM/CVS/
  CVP) at all, so it is a driver that has to be written, not a bug to fix.
  Scoped in HANDOFF-SESSION4.md session 14.
- **Internal WiFi monitor/injection**: impossible in mainline — the WCN3990
  firmware reports `raw 0` (no raw mode). Use an external USB adapter instead.
- **Audio**: speaker, earpiece, headphones and the microphone all work. One
  rough edge is left: changing output **while a stream is already playing**
  works towards the headphones but leaves the speaker silent until that stream
  is reopened. The cause is an ASoC ordering problem, not a configuration one —
  the amplifiers are powered up ~300 ms before the I2S clock they need to lock
  onto — and it is written up in
  [`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md) §6.
  Jack detection needs the codec kept awake (a udev rule), because a suspended
  soundwire slave reports nothing.
- **Only one microphone is real.** AMIC3 works. AMIC1, AMIC2, the four VA-macro
  DMICs and the TX-macro DMICs all return digital silence, so the device tree
  describes inputs this board does not have and those nodes can be dropped.
  Note when testing: enabling a capture path emits a settling DC transient, a
  one-sided ramp, before going quiet, so peak and RMS look like signal on a dead
  input. Count sign changes instead.
- **Sensors work** (SSC/ADSP over FastRPC) — see the "What works" list and
  `userspace/sensors/`. Still not done, each for want of a driver: NFC (Samsung
  `sec-nfc`), fingerprint (proprietary Focaltech HAL) and the camera (only the
  PMIC is up; it does not capture yet — see below).
- **GPS: a satellite fix reboots the phone, cell-id positioning does not.** The
  location service answers every query, and a session in `cellid` mode runs
  indefinitely and reports positions. Ask for `standalone` — an actual GNSS fix
  — and the SoC watchdog-resets in under a second, `ipa.ko` or no `ipa.ko`.
  The trigger is the GNSS measurement engine being brought up, and nothing
  else: not the session, not the indications, not QMI. Same silent signature as
  the mobile-data reset, and since it needs no SIM and no LTE it is both the
  best lead on that bug and a ninety-second reproducer for it.
  [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md)
- Single USB-C port + no mainline Type-C driver → charge vs OTG is not automatic
  (manual `otg on|off`, defaults to charging). See `packages/rhodep-usb-otg`.

---

# Repository layout
```
kernel/
  patches/            51 kernel patches (DTS + shared-driver fixes), applied in order
  config/
    config-motorola-rhodep.aarch64   full kernel .config (Kali variant = pmOS config + NetHunter + BTF fix)
    nethunter-config.fragment        the NetHunter symbols merged onto the pmOS config
  APKBUILD            pmaports APKBUILD used by pmbootstrap to build the kernel
postmarketos/         FULL postmarketOS port source (the base this Kali port builds on)
  linux-motorola-rhodep/     kernel aport (40 patches + APKBUILD + pmOS config, no NetHunter)
  device-motorola-rhodep/    device package (deviceinfo, systemd units, JEITA, modem glue)
  firmware-motorola-rhodep/  firmware aport (APKBUILD; blobs not included)
  README.md                  how to build the kernel/rootfs with pmbootstrap
debos/
  wip.toml            kali-nethunter-pro device config for rhodep (bootimg offsets)
  binwrap/            wrappers to run debos/systemd-nspawn inside a container
packages/
  rhodep-modem-support/    .deb source: modem/ADSP/CDSP + internal WiFi bring-up
  rhodep-usb-otg/          .deb source: USB-C charge/OTG control (SGM41542 VBUS)
  rhodep-battery-jeita/    .deb source: cold-side (<0C) battery protection
  rhodep-rtl8188eus-fix/   TP-Link RTL8188EUS (wlan1) monitor+inject: patch the
                           realtek-rtl8188eus-dkms driver so it builds on 7.2
  rhodep-phosh-wifi-guard/ .deb source: keep Phosh alive across WiFi-auditing
                           network churn (airmon-ng check kill) + airmon-safe
  rhodep-gpu-opencl/       .deb source: enable rusticl so OpenCL (hashcat) sees
                           the Adreno 619 as FD619 (RUSTICL_ENABLE=freedreno)
  firmware-motorola-rhodep/ .deb template (blobs NOT included, see its README)
userspace/
  debug-tools/             the display and modem diagnostic tools, plus
                           rhodep-pstore-keep, which copies pstore records
                           aside each boot so a crash log survives the next one
  usb-net/            SSH over the USB cable (172.16.42.1), the only link that
                      survives a modem restart (install.sh)
  bluetooth/          unmasks bluetooth.service and drops the --noplugin
                      restriction that kept A2DP off. It no longer programs
                      the controller address: that is stable but is not the
                      device's own, and the fix is local-bd-address in the
                      device tree -- see its README (install.sh)
  modem/              everything that makes the radio work: populates the
                      modem's remote file system from the stock modem/fsg/
                      persist partitions, opens the UIM provisioning session,
                      a tqftpserv patched to serve /readonly/vendor/fsg/, the
                      memshare responder, and rhodep-ipa-hold.conf (install.sh)
  plasma-apps/        makes the Kali menu launchers work under Plasma Mobile:
                      installs kali-menu and points KDE's terminal at kgx
                      (install.sh)
  audio/              UCM (speaker/earpiece/headphones/mics), PipeWire and
                      WirePlumber rules, the boot route unit, the udev rule that
                      keeps the codec awake for jack detection, and the jack
                      watcher that switches output (install.sh)
  apt/                the 51 apt holds this port depends on, the hook that
                      refuses to purge them, and the enforcer that puts a hold
                      back if anything removes it (apply-holds.sh)
  login/              GDM login screen instead of the phosh.service autologin (install.sh)
  powersupply/        makes the Powersupply app skip the charger's empty
                      scope=Device battery and read the real gauge
  power/              s2idle instead of the broken deep suspend, and the hold
                      that keeps a running job alive across a screen lock --
                      including anything started from a terminal. Also the
                      NetworkManager drop-in that asks the radio to stay
                      wake-capable rather than dropping the link (install.sh)
  sensors/            the SSC sensors: Qualcomm's FastRPC userspace built
                      against the mainline driver, the registry copied out of
                      the stock vendor/persist partitions, and the udev bits
                      iio-sensor-proxy needs. Accelerometer, gyroscope,
                      magnetometer, proximity and light (build-fastrpc.sh,
                      install.sh)
extra-tools/          not needed to boot or to make a call: tools that make the
                      device pleasant to work *on* (see extra-tools/README.md)
  terminal-keyboard/  Esc, Tab, Ctrl, Alt and the arrows added to Plasma's
                      on-screen keyboard, in all 52 layout files (install.sh)
  terminal-clipboard/ Ctrl+V / Ctrl+Shift+C in the terminal, as zsh widgets,
                      because the keyboard cannot read the clipboard (install.sh)
  claude-free/        Claude Code routed through the model providers opencode
                      already holds keys for (install.sh)
  pwnagotchi/         pwnagotchi capturing WPA handshakes on the external
                      TP-Link (wlan1mon), never touching the internal wlan0
                      (install.sh; on-demand services, needs otg on)
  nethunter-pro-app/  the NetHunter Pro control panel (GTK4/libadwaita for
                      Phosh): pwnagotchi, wifipumpkin3, Phishkin3 (wp3 +
                      evilginx2, 95 phishlets), CARsenal, Docker, nmap, HID,
                      VNC and more, from a touch UI (install.sh + dbus helper)
  cleanup/            weekly disk-space cleanup (caches, journal, coredumps)
                      + a permanent journal cap; weekly systemd timer (install.sh)
scripts/
  mkbootv2b.py             build an Android boot.img v2 (flat Image + appended DTB)
  make-boot-from-apk.sh    build a boot.img reusing an initramfs from a base image
  build-support-debs.sh    build the three support .deb packages
  build-kernel-headers.sh  build linux-headers-<KVER>.deb (for DKMS / rtl8188eus)
  rhodep-gnss-test.py      drive a GNSS session from ONE QMI client, which qmicli
                           cannot do. WARNING: this reboots the phone; that is
                           the finding. See docs/.../GNSS-SM6375.md
  ssc-probe.py             ask the sensor core for the SUID of one data type
  ssc-enum.py              which sensor data types the sensor core actually has
  ssc-stream.py            stream raw samples out of an SSC sensor (this is how
                           the accelerometer's axes were measured rather than
                           guessed)
  pdr-tool.py              list / query / restart protection domains (SERVREG)
  rhodep-glitch-capture    watch DSI errors while a person drives the screen by
                           hand, sampling the backlight at 20 Hz so each error can
                           be attributed to a moment the brightness was moving or
                           not. Needs patch 0064 applied for the register values
  rhodep-repaint-bench     repeatable repaint load: scrolls a GPU texture at the
                           refresh rate and reports frames, late frames and DSI
                           errors, so two kernels can be compared. Measures jank
                           well; does NOT reproduce the display glitch
  rhodep-repaint-bench.py  the GTK4 load itself, driven by the above
docs/                       extra notes
```

---

# The kernel (shared with the pmOS port)

## The 80 applied patches (`kernel/patches/`, applied in this order)

The order below is the aport's `source=` order, which is what `patch` sees; it
is deliberately not numeric — 0042 and 0043 come before 0027 and 0028.
```
0001 add-Motorola-Moto-G82-5G            device DTS base (+ gpio-vibrator node)
0002 dsi-fix-pclk-for-dsc-without-widebus  [upstream candidate] DSI pclk
0003 drm-panel-add-novatek-nt37701        panel driver
0004 dpu-cmd-mode-tearcheck-intf-config2  [upstream] DPU command mode
0005 dpu-ctl-top-group-id-from-dpu-5      [upstream] CTL group id
0006 dsi-fix-dsc-range-max-qp-8bpp-10bpc  [upstream] DSC range qp
0007 rhodep-enable-adreno-619-gpu         DTS GPU (uses VDDGX, not VDDCX)
0008 rhodep-add-ramoops                   DTS pstore
0009 rhodep-enable-wifi-and-bt            DTS WCN3990 (serial0 alias)
0010 rhodep-enable-usb-host-otg           DTS usb-role-switch
0011 rhodep-enable-charger-i2c-buses      DTS i2c
0012 add-cellwise-cw2217-fuel-gauge       new driver (CW2217 gauge)
0013 rhodep-add-battery                   DTS gauge + simple-battery
0014 a6xx-sm6375-use-gmu-wrapper-funcs    [upstream] a6xx catalog (GMU wrapper)
0015 sm6375-gpu-smmu-adreno               [upstream] adreno-smmu compatible
0016 bq256xx-add-sgm41542                 [upstream] SGM41542 (bq25601 clone)
0017 rhodep-add-charger                   DTS charger
0018 bq256xx-reset-part-before-configuring  [upstream] regmap cache reset
0019 bq256xx-honour-battery-charge-limits [upstream] use bat_info
0020 sm6375-thermal-cooling-maps          DTS CPU throttling
0021 rhodep-throttle-gpu                  DTS GPU throttling
0022 bq256xx-charge-current-cooling-device [upstream] cooling device
0023 bq256xx-report-device-scope          [upstream] scope=DEVICE (UI fix)
0024 rhodep-battery-thermal-zone          DTS battery thermal zone
0025 net-ipa-add-sm6375-without-interconnects  IPA driver: qcom,sm6375-ipa (icc_count=0)
0026 rhodep-enable-ipa                    DTS ipa@5840000
0032 sm6375-add-apr-audio-services        DTS apr/q6 services, q6asm iommus = <&apps_smmu 0xa1 0x0>
0033 sm6375-add-lpass-macros-and-soundwire DTS lpass macros, soundwire, LPI pinctrl (i2s2 pins)
0034 ASoC-add-motorola-rhodep-sndcard     machine driver compatible
0035 rhodep-enable-audio                  DTS sound card, WCD9370, both AW88261 amplifiers
0036 lpass-macro-add-codec-version-2-2    LPASS codec version 2.2
0042 net-ipa-sm6375-fix-imem-address      IPA: the sm6375 IMEM address, from the vendor tree
0043 net-ipa-map-imem-as-device-memory    IPA: map IMEM as device memory, not normal
0027 interconnect-qcom-add-sm6375-provider  new driver: qnoc-sm6375 (module, held out of the boot)
0028 sm6375-add-interconnect-nodes        DTS interconnect provider nodes
0044 net-ipa-sm6375-vote-the-three-noc-paths  IPA: own icc data, 3 paths incl. imem
0045 rhodep-wire-ipa-interconnects        DTS memory/imem/config paths on the IPA node
0046 interconnect-sm6375-do-not-program-qos  qnoc-sm6375: skip QoS programming
0047 net-ipa-sm6375-keep-noc-paths-voted  IPA: hold the votes as a floor, not on demand
0048 net-ipa-sm6375-own-sram-layout       IPA: its own SRAM layout, not qcm2290's
0049 sm6375-fix-removed-region-size        DTS removed_mem is 0x7100000, not shima's
0051 regulator-add-fan53870-camera-pmic    new driver: FAN53870 (7 LDOs), camera rails
0052 rhodep-add-camera-pmic                DTS i2c7 + the FAN53870 node
0053 media-camss-add-sm6375-support        camss: qcom,sm6375-camss (4 CSIPHY/3 CSID/3 TFE)
0054 sm6375-add-camss-and-cci              DTS camss + both CCI masters
0055 rhodep-add-rear-camera                DTS S5KJN1 node (does not capture yet)
0056 media-s5kjn1-vendor-power-order       sensor: vendor rail order (vio,vana,vdig)
0057 sm6375-add-fastrpc                    DTS fastrpc on the ADSP/CDSP glink edges
0059 ASoC-qdsp6-set-drvdata-before-clocks  q6dsp clocks: publish drvdata before
                                          registering, or an ADSP restart oopses
                                          the kernel with prepare_lock held
0060 remoteproc-q6v5-ratelimit-handover  one message a minute, not 4 a second
0061 cpuidle-psci-init-after-deferred-probe  cpuidle never initialised at all
0062 panel-nt37701-brightness-in-lp-mode  brightness DCS in LP; no effect on the glitch
0063 dsi-wait-mdp-idle-before-cmd-xfer   waits for MDP idle; the wait never fires
0065 rhodep-display-interconnect-paths   mdss had no icc paths, so it never voted
0067 rhodep-ramoops-ecc-and-firmware-regions  crash logs came back unreadable
0068 sm6375-add-apss-watchdog            the watchdog was never described
0070 qcom_q6v5-mask-handover-irq         one-shot IRQ left enabled forever
0072 DIAGNOSTIC-net-ipa-report-error-interrupts  IPA error interrupts were
                                       masked, so failures were silent
0073 DIAGNOSTIC-remoteproc-print-leftover-crash-reason  the previous crash
                                       reason survives a reboot; print it
0074 DIAGNOSTIC-soc-qcom-qmi-report-unhandled-messages  unhandled QMI messages
                                       were dropped without a word
0075 DIAGNOSTIC-net-ipa-expose-modem-gsi-channel-state  modem GSI state and
                                       mem_offset, for the LTE reset hunt
0076 DIAGNOSTIC-remoteproc-qcom-read-crash-reason-on-demand  read it from sysfs
                                       rather than only at crash time
0079 net-ipa-do-not-add-the-memory-offset-to-a-size  a size was being computed
                                       as if it were an address
0080 rpmsg-glink-take-a-reference-for-the-rcid-entry  the rcid entry and the
                                       endpoint could be freed while in use
0081 net-ipa-tear-the-setup-down-when-the-modem-crashes  setup survived a modem
                                       crash and pointed at dead memory
0082 sm6375-add-the-tcsr-download-mode-address  needed for download mode to be
                                       armed at all
0083 rpmsg-glink-make-the-channel-open-timeout-adjustable  the fixed timeout was
                                       too short to observe a slow open
0084 soc-qcom-pd_mapper-add-sm6375       the protection-domain map had no entry
                                       for this SoC
0085 soc-qcom-pd_mapper-serve-tms-pdr_enabled-on-sm6375  the modem PD asks for
                                       tms/pdr_enabled and got nothing
0086 sm6375-uart-interconnect-paths      uart1 never voted for QUP bandwidth
0088 interconnect-sm6375-keep-boot-floors  sync_state starved every unvoted
                                       node, which is what broke Bluetooth
0089 regulator-fan53870-declare-low-ldo-supply  LDO1/2 had no supply_name
0090 rhodep-camera-pmic-input-supply     PM6125 S6 feeds the low LDO group
0091 rhodep-drop-absent-microphones      AMIC1/2 and the VA DMICs are not fitted
0092 rhodep-add-the-flash-led-as-a-torch  tlmm 49 as a gpio-led; see nice-to-have
                                       9, the flash also wants a PM6125 PWM
0093 rhodep-expose-gpu-770-840          the speed bin (fuse = 177) allows both;
                                       the table had stopped at NOM
0094 nt37701-selectable-refresh-rate     the panel is 120 Hz and only 60 was
                                       ever offered; refresh= picks the timing
0095 sm6375-soundwire-wake-irq          swr0 had no wakeup interrupt, so the
                                       codec could not signal while suspended
0096 cw2217-no-full-while-discharging    FULL at 100% on battery made UPower
                                       say on-battery: no, so it never slept
0097 nt37701-offer-all-four-refresh-rates  48/60/90/120 selectable at runtime
                                       instead of one picked at power on
```

0062 and 0063 are kept but neither changes the glitched lines they were written
for; they are inert rather than wrong, and `docs/display-cmd-dma-window-wip/`
explains what the artefact actually is. 0064 is a diagnostic that prints the raw
DSI error registers and lives in `kernel/patches/` without being in `source=`,
the same way 0058 does — apply it by hand when measuring the display.

**The numbering has gaps and they are not omissions.** 0029-0031 and 0037-0041
were interconnect and audio diagnostics that were dropped, 0050 and 0058 were
never used, and the ones worth reading survive under
`docs/interconnect-sm6375-wip/` (see
[`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md)). Not every `.patch` file in `kernel/patches/` is in the build: 86 files are
kept and 74 are listed in `source=`. The other twelve are diagnostics and
experiments retained on purpose, and `scripts/check-patch-sync.sh` prints them
by name so the difference is visible rather than assumed.

**0059 is a mainline bug, not a rhodep quirk**, and it is the one to send
upstream first: `q6dsp_clock_dev_probe()` calls `dev_set_drvdata()` *after*
registering its clocks, while the clk ops read that drvdata — so a clock
prepared during its own registration dereferences NULL. It only fires after an
ADSP restart, because that is what leaves prepared orphan clocks behind for the
clk core to reparent. The oops happens inside `__clk_register()`, which holds
the global clk `prepare_lock`, so the real damage is that every
`clk_prepare_enable()` afterwards blocks forever. See "Suspend" below.

**The tree as it stands builds the v97 device tree, not v94.**
`kali-boot-v94-STABLE.img`, the image to flash, predates 0028 and 0045, so its
device tree has no interconnect provider nodes and no NoC paths on the IPA
node. That difference is inert: both are only read by drivers that
`rhodep-ipa-hold.conf` and `rhodep-icc-hold.conf` keep out of the boot. It
matters for one thing only, and §5 of HANDOFF-SESSION4.md says why v94 is the
recovery image: with no provider nodes there is no modalias, so `qnoc-sm6375`
cannot be autoloaded whatever state it is in.

Patches 0044-0048 are corrections checked against the vendor tree and all of
them are kept, but **none of them fixes the watchdog reset** — see §5 session 15
before re-testing any of them.

## Audio

Sound needs three things, and only the first is in the boot image:

1. **Kernel** — patches 0032-0036. The one that mattered most is the q6asm
   stream id, `iommus = <&apps_smmu 0xa1 0x0>`. Getting that wrong does not
   merely break audio: a DSP write to DDR is posted and fails quietly, but a
   DSP read needs a response, so a bad translation hangs the bus and resets the
   SoC with nothing in the logs.
2. **The AW88261 tuning blob**, which the driver refuses to probe without. It
   is not redistributable here, so pull it off the stock ROM with
   `scripts/extract-aw88261-acf.sh`, run on the phone as root. Vendor is a
   logical partition inside `super`, so the script reads the liblp metadata,
   maps the extents with dmsetup and mounts read only rather than copying
   600MB.
3. **Userspace**, `userspace/audio/install.sh`, also on the phone as root.

That last one is not optional, and it is worth understanding why. The card's
PCMs are DPCM front ends: `hw:0,0` cannot be opened until a mixer route is
enabled. ACP, which PipeWire uses to profile cards, probes by opening PCMs, so
it finds nothing, creates no sink, and its probing resets the mixer. So ACP is
disabled for this card, the sink is declared outright, and because PipeWire
opens that sink while starting and exits if it cannot, a systemd unit enables
the route first, waiting for the ADSP to come up rather than assuming it.

That last consequence deserves spelling out, because it bit this port: **if
PipeWire loses that race it does not merely start late, it gives up for good.**
Five failures in a couple of seconds trip systemd's default start limit, and
after that nothing retries. The phone then has no audio at all until someone
logs in over the network and restarts the service by hand, which looks like the
card being broken. A drop-in on the user's `pipewire.service` therefore waits
for the card and a route before starting it, and clears the start limit so a
failure is a delay rather than a permanent one.

Both amplifiers play together, the bottom loudspeaker and the earpiece, which
is how the phone is meant to sound. The amplifier volume control is
attenuation in 0.25dB steps from 0 to 360, so 360 is full scale.

If you ever reinstall the rootfs, steps 2 and 3 have to be done again, along
with the modules under `/lib/modules/7.2.0-rc5`, none of which live in the
boot image.

### Headphones, microphones and the jack

The same `install.sh` sets these up. Two things about them are not obvious and
cost real time to work out, so they are worth stating here:

- The headphone link is bound to `RX_CODEC_DMA_RX_1`, and that port feeds the
  RX macro's **RX2/RX3**, not RX0/RX1. Route the interpolators from the wrong
  pair and the stream starts, runs, and then dies with EIO a second later,
  which looks exactly like the old DSP write bug. `RX INT0/INT1 DEM MUX` must
  also be `CLSH_DSM_OUT` or the path never reaches the headphone amplifier.
- **Jack detection only works while the codec is kept awake.** A suspended
  soundwire slave raises no MBHC interrupt at all, even though the block is
  enabled and its interrupt unmasked, so plugging something in does nothing.
  `userspace/audio/udev/90-rhodep-wcd937x-jack.rules` pins the TX slave on;
  `rhodep-jack-watch` then follows the resulting input events and switches the
  output. `rhodep-audio-out speaker|earpiece|headphones|status` overrides it.

Only **AMIC3** exists on this board, reached through `ADC2 MUX` set to `INP3`
and decimator input `SWR_MIC0`. AMIC1, AMIC2 and every DMIC in the device tree
capture digital silence.

Beware when testing capture: enabling a path produces a settling DC transient,
a one-sided ramp a few thousand samples long, and then silence. Peak and RMS
therefore look like a working microphone on a dead input, which is exactly the
trap this port fell into. Real audio is symmetric around zero and crosses it
constantly; the transient never crosses at all.

Full detail, including the one remaining limitation when switching output
mid-stream, is in
[`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md) §6.

### Keeping apt from breaking it

`userspace/apt/apply-holds.sh` reapplies every hold this port depends on, and
`apt-holds.txt` is the list as it stands.

None of the files this port installs belong to a package, so apt cannot delete
them; dpkg only removes what is in its own manifest. The holds are there for a
different reason. If a PipeWire or WirePlumber upgrade changes the `conf.d`
rule format and our rule stops applying, ACP comes back, and since the PCMs
cannot be opened without a route, PipeWire will not start and the machine ends
up with no audio at all rather than degraded audio. `alsa-ucm-conf` is held for
the UCM lookup path, and `alsa-utils` for `alsa-restore`, which is the fallback
that puts the route back at boot.

## Kernel config notes (CRITICAL)
The config in `kernel/config/config-motorola-rhodep.aarch64` is the **Kali
variant**. Key differences from a plain pmOS config:
- **`CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`** — *the single most important line for
  Kali*. Kali/Debian strictly validates module BTF; without this every module
  fails to load (`failed to validate module [X] BTF: -22`), breaking display
  (refgen regulator) and modem/WiFi (qrtr). Alpine/pmOS does not validate BTF, so
  pmOS works without it. Harmless for pmOS → the config can be unified.
- ~58 NetHunter symbols (USB WiFi injection: rt2800usb / rtl8187 / rtl8192cu /
  rtl8xxxu / ath9k_htc / carl9170 / mt7601u / zd1211rw; USB BT; CAN; SDR
  hackrf/rtl2832; USB gadget HID/ACM/ECM/RNDIS; NFS; binder). See
  `nethunter-config.fragment`.
- **`# CONFIG_MODULE_COMPRESS is not set`** — must stay off. Compressed modules
  hang this device at boot (`modprobe` gives `Exec format error`).
- The kernel is built as a **flat `Image`** (NOT `Image.gz`/EFI-zboot): the
  Motorola bootloader resets on a self-decompressing image.

## Building the kernel (pmbootstrap)
The kernel is a pmaports/postmarketOS package. **The full postmarketOS port
source is included in `postmarketos/`** — that directory has the complete
pmaports aports (kernel + device + firmware) and its own `README.md` with the
exact pmbootstrap build steps. Use it as-is, then apply the Kali `.config` on top.

Quick version — inside a pmbootstrap environment, with the aport in place
(`device/testing/linux-motorola-rhodep/`):
```
pmbootstrap checksum linux-motorola-rhodep      # after ANY patch/config change
pmbootstrap build --force linux-motorola-rhodep
# output: ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk
```
Verify after build: no empty `.ko`, **no `.ko.zst`** in the apk.

**The patches under `kernel/patches/` and `postmarketos/linux-motorola-rhodep/`
are identical**, same filenames and same `source=` order — the only difference
between the two trees is the `.config`. Run `scripts/check-patch-sync.sh` to
confirm it, and do run it: these copies drifted 22 files apart once without
anything complaining, and a stale copy does not look stale, it looks
authoritative.
`postmarketos/` is the build engine (pmbootstrap builds the kernel and produces
the boot.img/initramfs from it); `kernel/` carries the Kali variant of the
config on top. If you change a patch, change it in both places, or copy the set
across so they stay in sync.

- For the **pmOS** kernel: use `postmarketos/linux-motorola-rhodep/` as-is (its
  config has no NetHunter symbols).
- For the **Kali** kernel: use the same aport but swap in
  `kernel/config/config-motorola-rhodep.aarch64`. It is the pmOS config plus the
  NetHunter symbols and `CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`, already merged and
  verified to build. To regenerate it from scratch: merge
  `kernel/config/nethunter-config.fragment` onto the pmOS config with
  `scripts/kconfig/merge_config.sh` + `make olddefconfig`, then confirm the
  BTF-mismatch line is present.

Both configs keep audio working: they carry `CONFIG_SM_LPASSCC_6115=m` (required
for the soundwire/LPASS clocks) and the AW88261/WCD937x/LPASS-macro symbols.

## Turning the kernel apk into a Debian `linux-image` .deb
NetHunter/mobile expects a Debian kernel package. Extract the apk and repackage:
```
KVER=7.2.0-rc5 ; SOC=sm6375 VENDOR=motorola MODEL=rhodep
mkdir -p PKG/DEBIAN PKG/boot PKG/usr/lib/linux-image-$KVER/qcom PKG/usr/lib/modules
tar xzf <apk> -C /tmp/apkx
cp /tmp/apkx/boot/vmlinuz PKG/boot/vmlinuz-$KVER          # flat Image (verify with `file`)
cp /tmp/apkx/boot/dtbs/qcom/sm6375-motorola-rhodep.dtb \
   PKG/usr/lib/linux-image-$KVER/qcom/$SOC-$VENDOR-$MODEL.dtb
cp -r /tmp/apkx/usr/lib/modules/$KVER PKG/usr/lib/modules/$KVER
rm -f PKG/usr/lib/modules/$KVER/build PKG/usr/lib/modules/$KVER/source
# DEBIAN/control: Package: linux-image-7.2.0-rc5 ; Depends: kmod, linux-base
# DEBIAN/postinst: depmod -a $KVER ; update-initramfs -u -k $KVER
fakeroot dpkg-deb --build -Zxz PKG linux-image-${KVER}_7.2~rc5-rhodep2_arm64.deb
```

## Kernel headers (for DKMS / external Wi-Fi drivers)
The apk ships no headers, so DKMS modules (e.g. the TP-Link `rtl8188eus`) cannot
build. Generate a matching `linux-headers-<KVER>.deb` with:
```
KSRC=/path/to/patched/linux-7.2-rc5 \
CONFIG=kernel/config/config-motorola-rhodep.aarch64 \
    scripts/build-kernel-headers.sh
```
It does a full kernel build once to produce a real `Module.symvers` (with
mac80211/cfg80211 symbols), packages the headers + aarch64-native `scripts/`,
and sets up `/lib/modules/<KVER>/build`. Validated by building an out-of-tree
module with the exact installed vermagic. See `packages/rhodep-rtl8188eus-fix`
(single-band RTL8188EUS) and `packages/rhodep-rtl8821au` (dual-band RTL8811AU
via rtw88) for the full USB Wi-Fi monitor+injection workflows.

## Updating the kernel: what you have to redo

This is a hand-maintained mainline port, so a new kernel is not a drop-in — it
is a deliberate re-do of everything that lives **outside** the kernel tree.
Nothing here auto-migrates. After building and flashing a new kernel version:

1. **Reapply the out-of-tree patches / config.** The 51 patches under
   `kernel/patches/` and the Kali `.config` (`CONFIG_MODULE_ALLOW_BTF_MISMATCH`,
   the NetHunter fragment, flat `Image`, no module compression) all have to be
   carried forward and re-verified against the new base. See "The kernel".
2. **Rebuild the headers `.deb`** for the new `<KVER>`
   (`scripts/build-kernel-headers.sh`) and `apt-mark hold` it, or DKMS has
   nothing to build against.
3. **Rebuild every DKMS module** against the new kernel: `rtw88` (dual-band
   Wi-Fi), `realtek-rtl8188eus` (single-band Wi-Fi), plus lime-forensics / xtrx
   if present. DKMS does this automatically on install *unless* a file is held
   immutable — see the next point.
4. **Release, rebuild, re-register the protected driver files.** The dual-band
   driver's built `.ko` is made immutable by `rhodep-protect-files` (so it can't
   be deleted by accident), which also means DKMS cannot overwrite it on a new
   kernel. For `rhodep-rtl8821au`:
   ```sh
   rhodep-protect-files release /lib/modules/<OLD_KVER>/updates/dkms/rtw_8821au.ko
   dkms install rtw88/0.6 -k <NEW_KVER> --force
   packages/rhodep-rtl8821au/install.sh      # re-registers the new .ko + LED rule
   ```
   The udev LED-off rule and the DKMS source in `/usr/src/rtw88-0.6` are not
   kernel-specific and carry over as-is.

   **`snd-q6dsp-common.ko` needs none of this, and the reason is worth
   copying.** It was briefly protected the same way, as a `kernel-suspendfix`
   component, because it is a file *inside* the held `linux-image-<KVER>`
   package and a hold does not stop `apt-get install --reinstall`. That was a
   guard around the real problem rather than a fix for it. The fix was to
   rebuild the `linux-image` .deb from an apk that already carries patch 0059
   (see "Turning the kernel apk into a Debian `linux-image` .deb") so that the
   packaged module *is* the corrected one; the component was then unregistered,
   because immutability on a package-owned file only buys friction once the
   package is right.

   **Do the same for any future module-only fix.** Carrying it as a loose file
   is how the tree drifted in the first place: before this was done,
   `dpkg -V linux-image-7.2.0-rc5` reported **1685 modified files** --
   essentially every module -- and `fan53870.ko` and `lpasscc-sm6115.ko` were
   loaded and working while belonging to no package at all. A `--reinstall`
   would have rolled the whole tree back to the previous build: no camera PMIC,
   no LPASS clock controller, no 0059. Repackaging costs 18 seconds and brought
   that count to 6, all of them depmod's own generated indexes
   (`modules.dep`, `modules.alias`, ...), which differ on any system with DKMS
   and always will.
5. **Reapply the userspace and audio blobs** if you reinstalled the rootfs (the
   AW88261 ACF, the modules under `/lib/modules/<KVER>`, `userspace/*/install.sh`).
6. **Re-run `userspace/apt/apply-holds.sh`** so the holds and the immutable
   protection cover the freshly installed files.

In short: a new kernel means redo the patches, rebuild the headers and DKMS
modules, and re-register the immutable driver files. The `.ko` immutability is
intentional friction — it stops an accidental `rm`, at the cost of one
`release` + re-register per kernel bump, which you are doing anyway.

---

# Building the Kali rootfs (debos)

Kali NetHunter Pro rootfs is built with **debos** using the upstream
[`kali-nethunter-pro`](https://gitlab.com/kalilinux/nethunter/build-scripts/kali-nethunter-pro)
recipes (a fork of mobian-recipes). Environment used here: an **arm64** Ubuntu
24.04 container (native arm64, no qemu), with sudo, **no KVM**.

### Toolchain
```
sudo apt install -y pkg-config libglib2.0-dev libostree-dev debootstrap \
     android-sdk-libsparse-utils mkbootimg bmap-tools jq squashfs-tools-ng \
     fakeroot systemd-container parted gdisk fdisk i2c-tools
# Go + debos:
wget https://go.dev/dl/go1.23.4.linux-arm64.tar.gz && sudo tar -C /usr/local -xzf go*.tar.gz
export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH
go install github.com/go-debos/debos/cmd/debos@latest
sudo pip3 install --break-system-packages yq       # provides tomlq (system-wide)
```

### Recipe + our device config
```
git clone https://gitlab.com/kalilinux/nethunter/build-scripts/kali-nethunter-pro /tmp/knh-pro
cp debos/wip.toml               /tmp/knh-pro/devices/qcom/configs/wip.toml
cp <linux-image .deb>           /tmp/knh-pro/devices/qcom/packages/
cp <firmware .deb>              /tmp/knh-pro/devices/qcom/packages/
```
`wip.toml` sets rhodep: chipset=sm6375, vendor=motorola, model=rhodep, bootimg
offsets (base 0, kernel@0x8000, ramdisk@0x1000000, tags@0x100, pagesize 4096, v2).

### Run debos (container-friendly flags; no KVM)
```
export PATH="$PWD/debos/binwrap:/usr/local/go/bin:/usr/local/bin:$PATH"   # binwrap/debos adds --disable-fakemachine
cd /tmp/knh-pro
sudo mkdir -p /dev/disk                                                   # systemd-nspawn bind-mounts it
sudo env PATH="$PATH" HOME=/root SYSTEMD_NSPAWN_UNIFIED_HIERARCHY=1 ./build.sh -t qcom-wip
#  -> rootfs.yaml completes -> rootfs-arm64-phosh-nonfree.tar.xz (~920 MB)
#  -> image.yaml FAILS to partition (container loop has max_part=0). Finish by hand:
```

### Finish the rootfs by hand (image.yaml can't partition in a container)
```
sudo tar xJf rootfs-arm64-phosh-nonfree.tar.xz -C /tmp/rootfs
# mount proc/sys/dev, fix DNS (echo 'nameserver 127.0.0.11' > etc/resolv.conf  # Docker's DNS)
sudo chroot /tmp/rootfs apt-get install -y --no-install-recommends initramfs-tools qcom-support-common
# Telephony/phone role: dialer (Calls), Chatty/SMS, MMS, ModemManager auto-config.
# The generic Phosh rootfs ships callaudiod/feedbackd/gnome-contacts/modemmanager
# but NOT the UI apps; mobian-phosh-phone adds the dialer + SMS + phone role so the
# Phone/Contacts/Messages apps show up in the Phosh drawer by default. Verified on
# rhodep: installing this made the apps appear. nautilus = file manager.
sudo chroot /tmp/rootfs apt-get install -y mobian-phosh-phone nautilus
sudo chroot /tmp/rootfs dpkg -i /srv/linux-image-*.deb /srv/firmware-*.deb \
      /srv/rhodep-modem-support*.deb /srv/rhodep-usb-otg*.deb /srv/rhodep-battery-jeita*.deb
sudo chroot /tmp/rootfs systemctl enable qrtr-ns rmtfs pd-mapper tqftpserv
sudo chroot /tmp/rootfs systemctl mask droid-juicer systemd-repart
# Login screen instead of the phosh.service autologin. Mandatory if the image
# ends up with gdm3 (kali-linux-default and friends pull it in): otherwise both
# claim the display at boot. See "Login screen (GDM)" above.
sudo cp -r userspace/login /tmp/rootfs/srv/login
sudo chroot /tmp/rootfs sh /srv/login/install.sh kali
# Audio: UCM, the PipeWire and WirePlumber rules, the boot route unit, the udev
# rule that keeps the codec awake for jack detection and the jack watcher.
# install.sh notices there is no running systemd and only lays the files down,
# so the image boots with sound already working. The one thing it cannot do is
# ship the AW88261 blob, which is not redistributable and has to be extracted on
# the device; until it is, the amplifiers do not probe and there is no sound.
sudo cp -r userspace/audio /tmp/rootfs/srv/audio
sudo chroot /tmp/rootfs sh /srv/audio/install.sh
# Modem: the remote file system populator, the UIM provisioning service, the
# patched tqftpserv and the memshare responder. This is what makes the radio
# come up; without it the modem sits in DMS operating mode 'offline' forever.
# Needs libqrtr-dev and libzstd-dev in the chroot to build the two binaries.
# install.sh notices there is no running systemd and only lays the files down.
# It also drops /etc/modprobe.d/rhodep-ipa-hold.conf, which keeps ipa.ko out of
# the boot: read userspace/modem/README.md, that file is deliberate and it is
# what costs mobile data today.
sudo chroot /tmp/rootfs apt-get install -y libqrtr-dev libzstd-dev
sudo cp -r userspace/modem /tmp/rootfs/srv/modem
sudo chroot /tmp/rootfs sh /srv/modem/install.sh
# Kali menu launchers: install kali-menu and point KDE's terminal at kgx.
sudo cp -r userspace/plasma-apps /tmp/rootfs/srv/plasma-apps
sudo chroot /tmp/rootfs sh /srv/plasma-apps/install.sh
# The apt holds, so the first upgrade cannot undo any of the above.
# Bluetooth: unmask the service and program the real controller address.
sudo cp -r userspace/bluetooth /tmp/rootfs/srv/bluetooth
sudo chroot /tmp/rootfs sh /srv/bluetooth/install.sh
# The apt holds, so the first upgrade cannot undo any of the above. Run this
# LAST: it also protects every file the installers above just laid down.
sudo cp -r userspace/apt /tmp/rootfs/srv/apt
sudo chroot /tmp/rootfs sh /srv/apt/apply-holds.sh
# package the dir into an ext4, then wrap it in a sector-4096 GPT disk (see below).
```

### The userdata disk image (sector-4096 GPT, same scheme as pmOS)
pmOS/NetHunter store the OS as a **disk with an internal GPT** inside `userdata`
(2 partitions, boot + root), **logical sector size 4096**. The initramfs does
`losetup -Pf --sector-size 4096 <userdata>` and mounts the root partition by UUID.
Build it with `losetup -b 4096` + `sgdisk` (2 partitions: pmOS_boot vfat +
pmOS_root ext4), then `dd` the ext4 into the root partition offset. Full commands
in `docs/` / the pmOS kernel README.

### The boot image
Use `scripts/mkbootv2b.py` (flat Image + appended DTB, header v2). The boot image
embeds the **pmOS initramfs** (which does the sector-4096 subpartition mount) and
its cmdline uses `pmos_root_uuid=<UUID of the Kali root ext4>`:
```
python3 scripts/mkbootv2b.py vmlinuz DTB initramfs \
  "earlycon pmos_root_uuid=<UUID> pmos_rootfsopts=defaults \
   panel_novatek_nt37701.clk_scale=100 ignore_loglevel watchdog_thresh=5" \
  kali-boot.img
```

---

# Installing on the device

**The Motorola bootloader refuses `fastboot flash userdata`** (permission
denied, no fastbootd). So `userdata` is written by **dd from a running Linux on
the phone** (a rescue boot or an existing OS). This is exactly how pmOS installs.

### 1. Rescue boot (telnet, does not mount root)
The rescue image = **pmOS kernel + pmOS initramfs** (from a working pmOS boot.img,
its kernel+DTB+initramfs are known to boot on this device) + **`pmos.debug-shell`**
on the cmdline. On boot it brings up the USB-gadget network + a **root telnet on
`172.16.42.1:23`** and does **not** mount the root filesystem — so you can safely
`dd` to userdata.

Build it from any working pmOS boot.img (e.g. the pmOS port's `boot-v45-DOCKER.img`)
with the helper script:
```
sh scripts/build-rescue-boot.sh boot-v45-DOCKER.img rescue-boot.img
```
The script splits the source boot.img (kernel+appended-DTB, initramfs, cmdline)
and repacks it as a flat Android v2 image with `pmos.debug-shell` prepended to the
original cmdline. It keeps the flat `Image` (NOT Image.gz) so the Motorola
bootloader accepts it.

> Don't have a pmOS boot.img? Build the pmOS kernel (see "Building the kernel"),
> make a boot.img with `scripts/make-boot-from-apk.sh`, and feed that to
> `build-rescue-boot.sh`. The rescue image only needs the pmOS initramfs, which
> implements `pmos.debug-shell` and the sector-4096 subpartition mounting.

Flash and enter:
```
fastboot flash boot_a rescue-boot.img
fastboot --set-active=a && fastboot reboot
# wait ~30s (black screen / logo, USB gadget comes up, root is NOT mounted)
telnet 172.16.42.1 23        # note: telnet, NOT ssh, in the rescue shell
```
Inside the rescue shell you have full raw access to the partitions, e.g.:
```
losetup -Pf --sector-size 4096 /dev/disk/by-partlabel/userdata   # expose the inner GPT
blkid /dev/loop0p2                                                # inspect the rootfs
```

### 2. Write the Kali disk to userdata (dd, from a running OS or rescue)
From a phone already running pmOS (or via the rescue shell + `nc`):
```
# from your PC, streaming the raw disk image over SSH:
ssh -t user@172.16.42.1 'sudo dd of=/dev/disk/by-partlabel/userdata bs=4M conv=fsync' < kali-userdata.img
```
This overwrites the start of userdata with the Kali GPT disk (replaces pmOS).

### 3. Flash the Kali boot and reboot
```
fastboot flash boot_a kali-boot.img
fastboot --set-active=a && fastboot reboot
```
First boot is slow (resize2fs grows the rootfs, first systemd + Phosh). The phone
vibrates before switch_root. SSH in as `kali` / `1234` over USB (172.16.42.1) or
WiFi.

### 4. First-boot fixes (already applied in the shipped rootfs)
```
sudo systemctl mask droid-juicer.service        # hangs the boot without Android
sudo systemctl mask systemd-repart.service
sudo dpkg -i rhodep-usb-otg_*.deb rhodep-battery-jeita_*.deb rhodep-modem-support_*.deb
```

---

# Day-to-day use

## Update Kali + install the toolset (keep the custom kernel safe)

The three `apt-mark hold` lines below are the historical core and are **not the
whole list** — the canonical one is `userspace/apt/apt-holds.txt`, 51 packages,
and `sudo userspace/apt/apply-holds.sh` applies it along with the guard, the
enforcer and the dpkg `Protected` flags. Prefer that; the commands here are kept
because they explain *why* each group is held.

```
sudo apt-mark hold linux-image-7.2.0-rc5 linux-headers-7.2.0-rc5 \
     realtek-rtl8188eus-dkms firmware-motorola-rhodep \
     rhodep-modem-support rhodep-usb-otg rhodep-battery-jeita
sudo apt-mark hold firmware-atheros \
     rmtfs tqftpserv protection-domain-mapper qrtr-tools libqrtr1 \
     modemmanager libmm-glib0 libqmi-utils libqmi-glib5 libqmi-proxy
sudo apt-mark hold alsa-ucm-conf alsa-utils alsa-topology-conf \
     libasound2-data libasound2t64 \
     pipewire pipewire-alsa pipewire-audio pipewire-bin pipewire-pulse \
     libpipewire-0.3-0t64 libpipewire-0.3-common libpipewire-0.3-modules \
     libspa-0.2-modules wireplumber libwireplumber-0.5-0
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y kali-linux-default python3-requests hcxdumptool hcxtools
sudo systemctl mask droid-juicer.service systemd-repart.service
```
(The second `apt-mark hold` is applied automatically by
`rhodep-modem-support` >= 2, it is repeated here for a from-scratch install.)

The holds are essential, and not only for the kernel:

- **linux-image / linux-headers**: stops apt from pulling a generic Debian
  kernel that would not boot this device.
- **firmware-atheros**: this is the easy one to miss. It ships the very same
  paths as `firmware-motorola-rhodep`,
  `/usr/lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin` and `firmware-5.bin`
  plus the `qca/` bluetooth files, so upgrading it silently replaces the board
  file this device needs with the generic one and the **internal WiFi and
  bluetooth stop working**. Verify who owns them with
  `dpkg -S /usr/lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin`: it must say
  `firmware-motorola-rhodep`.
- **rmtfs, tqftpserv, protection-domain-mapper, qrtr-tools, libqrtr1**: the
  modem transport. Without exactly this set working the modem, and therefore
  the internal WiFi (its protection domain lives inside the modem), do not come
  up.
- **modemmanager, libmm-glib0, libqmi-utils, libqmi-glib5, libqmi-proxy**: the
  modem and SIM handling that the port is validated against.
- **The audio set**: `alsa-ucm-conf`, `alsa-utils`, `alsa-topology-conf`,
  `libasound2-data`, `libasound2t64`, `pipewire`, `pipewire-alsa`,
  `pipewire-audio`, `pipewire-bin`, `pipewire-pulse`, `libpipewire-0.3-0t64`,
  `libpipewire-0.3-common`, `libpipewire-0.3-modules`, `libspa-0.2-modules`,
  `wireplumber`, `libwireplumber-0.5-0`.

  These do not protect against deletion. Every file this port installs, the
  UCM, the PipeWire and WirePlumber rules, the AW88261 blob, the systemd unit,
  belongs to no package at all, and dpkg only removes what is in its own
  manifest; that was checked file by file with `dpkg -S`. What they protect
  against is a change in behaviour. Our WirePlumber rule is what disables ACP
  for this card, and ACP has to stay off: it profiles a card by opening its
  PCMs, and this card's PCMs cannot be opened until a mixer route exists, so
  ACP finds nothing, creates no sink, and resets the mixer while probing. If an
  upgrade changed the `conf.d` rule format and our rule stopped applying, ACP
  would come back and PipeWire would fail to start altogether, so the result is
  no audio at all rather than degraded audio. `alsa-ucm-conf` is held for the
  UCM lookup path our config depends on, and `alsa-utils` for `alsa-restore`,
  the fallback that puts the route back at boot.

### A hold does not stop `apt purge`

This is worth knowing because it is not obvious and it is the one way an upgrade
session can still destroy the port. `apt-mark hold` stops upgrades, stops
autoremove and stops a package being dragged out as a dependency, but

	sudo apt purge alsa-ucm-conf

goes through, and takes half of mobian-phosh with it. Verified, not assumed.

`userspace/apt/apply-holds.sh` therefore also installs a `DPkg::Pre-Install-Pkgs`
hook, `rhodep-apt-guard`, which apt runs **before** handing anything to dpkg and
which aborts the whole transaction if any held package is being removed. Nothing
has been removed at the point it refuses, so it is safe. It fails open: any
error in the guard itself lets the transaction proceed, because a bug in it must
never be able to wedge package management on a phone.

### And nothing protected the holds themselves

The guard above protects packages. Until it was pointed out, **nothing
protected the holds**. One `apt-mark unhold`, by a script, an upgrade helper or
an agent doing something else, and every defence above is gone silently — and
the next upgrade replaces the kernel, the WiFi firmware or the audio stack.

`apply-holds.sh` now installs three more things:

- **`rhodep-holds-enforce`** puts back any hold, and any of the guard's own
  files, that goes missing, and logs it as an ALERT with the package names. It
  runs at boot, **after every apt transaction** (`60rhodep-enforce-holds`, a
  `DPkg::Post-Invoke` hook) and on a 30-minute timer as a backstop. It fails
  open at every step: a phone whose package management is wedged by its own
  guard rail is worse off than one with a missing hold.
- **immutability** (`chattr +i`) on `rhodep-apt-guard`, the `50` hook and the
  canonical hold list. Root can still take them out, but only by deciding to —
  `chattr -i` first. That is the whole point: it separates a deliberate change
  from something removing them in passing.
- **`rhodep-hold-override`**, the authorised way out. A plain `apt-mark unhold`
  no longer sticks, so releasing a hold on purpose goes through:

	sudo rhodep-hold-override release <package> "<reason>"
	sudo rhodep-hold-override restore <package>
	sudo rhodep-hold-override status

  It requires a reason, records it with a timestamp in
  `/etc/rhodep/apt-holds-exceptions.log`, and **refuses to run
  non-interactively without `--yes`**, so an unattended script cannot release a
  hold as a side effect. The exceptions file is what makes the enforcer leave
  that package alone.

Verified by attacking it on the device, not by reading it. The counts below are
what the list held on the day each attack was run — it was 41 packages then and
is 51 now; what was verified is that every one of them came back, not the
number:

| attack | result |
| --- | --- |
| `apt-mark unhold rmtfs` | ALERT logged, hold back on the next enforce |
| `apt-mark unhold $(apt-mark showhold)` — the whole list | every one back |
| `rm` the guard and the `50` hook | `Operation not permitted`, immutable |
| `chattr -i` then `rm` both | ALERT logged, both restored, immutability reapplied |
| unhold, then any ordinary `apt install` | hold back automatically via `Post-Invoke` |
| `rhodep-hold-override` with no tty, no reason, or a package not on the list | refused |
| authorised release, then enforce | stays released, and is in `status` |

### `dpkg -r` and `dpkg -P` are covered too

Everything above is apt-side, and **dpkg does not honour apt holds at all**.
Measured, not assumed, on dpkg 1.23.7:

	apt-mark hold sl && dpkg -P sl
	  Removing sl (5.02-1+b2) ...          <- gone, no warning

dpkg has no pre-removal hook to attach to, but it has a field for exactly this
and it obeys it. `rhodep-dpkg-protect` sets `Protected: yes` on every held
package, and then:

	dpkg -P tqftpserv
	  dpkg: error processing package tqftpserv (--purge):
	   this is a protected package; it should not be removed

	dpkg -r rmtfs
	  dpkg: error processing package rmtfs (--remove):
	   this is a protected package; it should not be removed

All 51 carry it. (`grep -c '^Protected: yes' /var/lib/dpkg/status` says 53, not
51: `libgcc-s1` and `systemd-sysv` ship protected from the Debian archive and
have nothing to do with this port.) The deliberate way past is dpkg's own
`--force-remove-protected`, and the supported way is to release the hold, which
clears the flag as well — otherwise "released" would be a lie.

The field lives in `/var/lib/dpkg/status` and there is no dpkg command to set
it, so this edits that file. It is the most dangerous file on the system, so
the edit refuses to run while dpkg or apt holds the frontend lock, writes a
copy and renames it into place, keeps a backup at
`/var/backups/dpkg.status.rhodep`, and validates by checking that `dpkg-query`
still parses the result and reports the same packages — anything else restores
the backup. It fails open at every step.

dpkg drops the flag whenever it rewrites a package's entry, which is what an
upgrade or reinstall does, so the enforcer reapplies it at boot and on its
timer. Verified: `apt-get install --reinstall tqftpserv` succeeds, the flag is
gone afterwards, and the next enforce puts it back. The edit is **not** done
from the apt `Post-Invoke` hook, because apt still holds the dpkg lock there.

Checked after all of it: `dpkg --audit` clean, 5615 packages still visible,
ordinary `apt install` / `apt purge` of unprotected packages unaffected.

### The port's own files are protected the same way

Holds only cover packages. Everything that makes this phone work that is **not
owned by any package** — the modem's RFS populator, the UIM provisioning
service, the patched tqftpserv, the memshare responder, `rhodep-ipa-hold.conf`,
the audio route and jack-watch scripts, the usb-net profile, the apt guard
itself — lives in `/usr/local` and `/etc`, where dpkg has never heard of it.
Nothing reinstalls those. One `rm -rf` and they are gone with no trace.

`rhodep-protect-files` gives them the same two layers: `chattr +i` on the live
file, and a canonical copy under `/usr/local/share/rhodep/files` that
`rhodep-holds-enforce` restores from if the live one goes missing **or is
modified**. It is up to **32 components**; the list grows as installers are
added, so read it from the device rather than from here:

	rhodep-protect-files status

### Which mechanism a file belongs to

The useful question is **not** "does a package own this file" but **"if it is
deleted, can anything put it back"**. There are three cases and they are easy
to tell apart:

| the file comes from | what protects it | why |
| --- | --- | --- |
| a distro package with a repo (`rmtfs`, `bluez`, `pipewire`) | the apt hold + dpkg `Protected` | `apt-get install --reinstall` can always re-fetch it. `chattr +i` would add nothing and would break the next legitimate upgrade |
| **a locally built `.deb` with no repo** (`rhodep-battery-jeita`, `rhodep-modem-support`, `rhodep-usb-otg`) | **both** layers | dpkg's `Protected` stops the *package* being removed, but nothing stops `rm` of a *file*, and there is no archive to restore it from |
| no package at all (everything under `/usr/local`, the units in `/etc`) | `rhodep-protect-files` | dpkg has never heard of it; a single `rm` and it is gone with no trace |

The middle row is the one that is easy to get wrong, and this port got it wrong
first. Those packages exist only in `/var/lib/dpkg/status` — there is no repo
and no cached `.deb` — so apt says so outright:

	# apt-get install --reinstall rhodep-battery-jeita
	Reinstallation of rhodep-battery-jeita is not possible, it cannot be downloaded.

A hold is no help against `rm`, and `--reinstall` is not available, so a deleted
`/usr/local/sbin/rhodep-battery-jeita` would take the cold-side battery
protection away permanently and silently. They are registered with
`rhodep-protect-files` as well, `battery-jeita`, `modem-support` and `usb-otg`
(plus their `-conf` halves). **The cost is that `dpkg -i` of a newer version of
one of those packages will fail on the immutable files**, so `release` them
first — the same friction as the DKMS drivers, for the same reason.

`snd-q6dsp-common.ko` looked like the middle row and was not: `linux-image` is
locally built too, but the fix could simply be put *into* the package, which is
strictly better than guarding a wrong file. See "Updating the kernel".

### The enabled state, which the immutable bit cannot protect

`chattr +i` on a `.service` stops the file being deleted or edited. It does
nothing about `systemctl disable`, which still works and leaves everything
*looking* present — the file is there, the package is held, `status` says
`protected`. On this port that would mean losing the below-0C battery cutoff,
or the sensors listener that keeps the ADSP from asserting on suspend, with no
error anywhere.

So components can register units as well:

	rhodep-protect-files register-units <component> <unit>...

and the enforcer re-enables any that has gone to `disabled`. It is deliberately
the narrowest thing that closes the gap:

- it only ever runs `systemctl enable`, which writes a symlink. It **never**
  starts, stops or restarts anything — restoring a file must not be able to
  bounce audio or drop the network.
- it acts only on exactly `disabled`. **`masked` is left alone**, because
  masking is always deliberate here (`droid-juicer`, `systemd-repart`,
  `phosh.service` under GDM), and `static`, `indirect` and `enabled` are
  already right.
- system units only; user units live in a session and cannot be enabled from
  here.

Re-running the component's `install.sh` was the other option, and it was
rejected on blast radius: those scripts restart PipeWire, WirePlumber and
`iio-sensor-proxy`, touch udev and query apt. Having that fire unattended every
30 minutes because one file went missing is worse than the missing file, and it
contradicts the rule the enforcer is built on — fail open, always.

Verified by attacking it: `systemctl disable rhodep-battery-jeita` is caught on
the next enforce (`ALERT: rhodep-battery-jeita.service had been disabled,
re-enabling it`), the unit comes back `enabled` and **stays `active`**, because
nothing restarted it. A masked unit is left masked, a run with nothing wrong
logs nothing, and a unit that does not exist exits 0.

**Symlinks are the other deliberate omission.** The store keeps a *content*
snapshot, so restoring a symlink from one would replace it with a copy of its
target. That is why the `multi-user.target.wants/*` links and the FastRPC
soname links are left out; `rhodep-ssc-links` recreates the latter on every
start.

Each installer calls `release` on its files before overwriting and `register`
after, so re-running any of them still works — verified by running them a second
time against already-immutable files, all exit 0, with audio, usb-net, the holds
and the modem services untouched.

Attacked on the device:

| attack | result |
| --- | --- |
| `rm` the modem scripts, `rhodep-ipa-hold.conf`, the guard | `Operation not permitted` |
| `chattr -i` then `rm` four of them | all four restored, each logged as an ALERT |
| edit `rhodep-ipa-hold.conf` to re-enable the IPA | content restored on the next enforce |

That last one matters: silently editing a config is worse than deleting it,
because nothing looks wrong afterwards. The enforcer compares content, not just
existence.

**Where the regress stops, stated plainly:** if you `chattr -i` and delete
`rhodep-holds-enforce` itself, nothing puts it back. There is no way around
that short of a package, and it is the intended deliberate path anyway. Re-run
`userspace/apt/apply-holds.sh` to rebuild the whole thing.

It is a guard rail, not a lock. To remove something deliberately, unhold it
first and the guard steps aside:

	sudo apt-mark unhold <package>

**Verified end to end on the device**, and there are two layers, which is worth
knowing when testing it:

- `apt purge -y <held>` never reaches the guard at all. Modern apt refuses it
  by itself: `E: Held packages were changed and -y was used without
  --allow-change-held-packages`.
- `apt purge -y --allow-change-held-packages <held>` gets past that, reaches
  dpkg, and *then* the guard fires:

	*** refusing to remove packages this port depends on ***
	E: Sub-process /usr/local/sbin/rhodep-apt-guard returned an error code (1)

  and the package is still installed afterwards.

One more thing `--allow-change-held-packages` does, which is easy to miss:
**it drops the hold even when the transaction is then aborted.** Attacking
`libspa-0.2-bluetooth` with it left the package installed (dpkg's `Protected`
refused the removal) but took the hold count down by one — 43 to 42, on a list
that has since grown to 51. The enforcer is what notices and repairs it:

	ALERT: holds had been removed, putting them back: libspa-0.2-bluetooth

which is a good illustration of why the enforcer exists at all: the guard
protects the package, and something still has to protect the hold.

Two traps when checking this by hand. `apt -s` does **not** run dpkg hooks, so a
simulated purge proves nothing about the guard. And a held package shows as
`hi`, not `ii`, in `dpkg -l`, with dpkg status `hold ok installed` rather than
`install ok installed` — so a survival check written as `grep '^ii'` or
`grep 'install ok installed'` reports the package as gone when it is fine. Both
of those produced convincing false negatives here. Use:

	dpkg-query -W -f='${Status}' <package> | grep -q 'ok installed'

To release one on purpose, use `rhodep-hold-override` rather than `apt-mark`,
so that the enforcer does not simply put it back — see above. The
ModemManager hold was originally kept loose because "the modem never goes
online" was the open problem and a newer MM was worth trying; that problem is
solved (HANDOFF-SESSION4.md §5 session 14) and the hold is now just protecting
a validated version.

`apt full-upgrade` does NOT re-flash `boot_a` (the running initramfs lives in
the flashed boot.img), so updates are safe for the boot path.

**Do not unhold these casually.** Each one is load-bearing: the kernel holds
stop a generic Debian kernel from replacing one that boots, `firmware-atheros`
silently overwrites the WiFi board file, the modem transport set is what brings
up both the modem and the internal WiFi, and the audio set keeps PipeWire
starting at all. Unholding one to chase a bug is fine; doing it by reflex
during an upgrade is how this port breaks.

Check what is protected at any time with `apt-mark showhold`; it should list 51
packages, and `apt-mark showhold | wc -l` on `kali-boot-v94-STABLE.img` says 51.
The canonical list lives in `userspace/apt/apt-holds.txt`, is copied to
`/usr/local/share/rhodep/apt-holds.txt` on the device, and
`userspace/apt/apply-holds.sh` puts it back after a rootfs reinstall.

<details>
<summary>The 51 held packages, by purpose</summary>

| Purpose | Packages |
|---|---|
| Custom kernel | `linux-image-7.2.0-rc5`, `linux-headers-7.2.0-rc5` |
| Device firmware | `firmware-motorola-rhodep`, `firmware-atheros` |
| Port packages | `rhodep-modem-support`, `rhodep-usb-otg`, `rhodep-battery-jeita` |
| USB WiFi injection | `realtek-rtl8188eus-dkms` |
| Modem transport | `rmtfs`, `tqftpserv`, `protection-domain-mapper`, `qrtr-tools`, `libqrtr1` |
| Modem and SIM | `modemmanager`, `libmm-glib0`, `libqmi-utils`, `libqmi-glib5`, `libqmi-proxy` |
| Modem tooling | `libqrtr-dev`, `libzstd-dev`, `libzstd1` |
| ALSA | `alsa-ucm-conf`, `alsa-utils`, `alsa-topology-conf`, `libasound2-data`, `libasound2t64` |
| PipeWire | `pipewire`, `pipewire-alsa`, `pipewire-audio`, `pipewire-bin`, `pipewire-pulse`, `libpipewire-0.3-0t64`, `libpipewire-0.3-common`, `libpipewire-0.3-modules`, `libspa-0.2-modules` |
| WirePlumber | `wireplumber`, `libwireplumber-0.5-0` |
| GStreamer bridge | `gstreamer1.0-pipewire` |
| Bluetooth | `bluez`, `libspa-0.2-bluetooth` |
| On screen keyboard | `squeekboard`, `plasma-keyboard`, `qml6-module-qtquick-virtualkeyboard` |
| Terminal clipboard | `wl-clipboard`, `qmlkonsole`, `tmux` |
| Kali menu launchers | `kali-menu` |
| Sensors | `iio-sensor-proxy`, `libssc2`, `libqrtr-glib0` |
| GPU compute | `mesa-opencl-icd` |

</details>

Six of those groups are newer than the rest and are the ones a reader is most
likely to think are stray:

- **`bluez`** owns `btmgmt`, which `rhodep-bt-address` uses to inspect the
  controller at every boot, and `bluetoothctl`/`btmgmt` are how the adapter is
  driven at all. (That service no longer *programs* an address — see
  `userspace/bluetooth/README.md`.) It is also the one hold here most
  worth lifting deliberately when a security update lands, since it is a
  network-facing daemon with a CVE history —
  `rhodep-hold-override release bluez "<reason>"`.
- **`libspa-0.2-bluetooth`** is the plugin that actually carries A2DP, built
  against the exact SPA ABI of the pinned PipeWire. Letting it move on its own
  is the likeliest way to end up with earbuds that pair, connect and play
  nothing.
- **`libzstd-dev` / `libzstd1`** are what the patched tqftpserv links against.
  Losing the runtime library stops the modem's TFTP server from starting at
  all; losing the `-dev` package only stops anyone from rebuilding it, which is
  the same reasoning as `libqrtr-dev`.
- **`qml6-module-qtquick-virtualkeyboard`, `wl-clipboard`, `qmlkonsole`,
  `tmux`** belong to `extra-tools/`. The first is where
  `terminal-keyboard/install.sh` reads Qt's stock layout tree from, so losing it
  means a reinstall has nothing to build on; the other three are what
  `terminal-clipboard` needs to copy and paste. See
  [`extra-tools/terminal-keyboard/README.md`](extra-tools/terminal-keyboard/README.md).
- **`iio-sensor-proxy`, `libssc2`, `libqrtr-glib0`** are the whole userspace
  side of the sensors. `libssc2` is the library that speaks to the sensor core
  and it has exactly one reverse dependency, `iio-sensor-proxy`, so removing
  the latter autoremoves the former and the phone silently stops rotating.
  `iio-sensor-proxy` is held for its behaviour rather than its existence: this
  port depends on the `ssc-accel` and `ssc-proximity` drivers inside it and on
  the `IIO_SENSOR_PROXY_TYPE` tag names its own udev rule uses, which
  `81-rhodep-ssc.rules` appends to. `libqrtr-glib0` is the QRTR transport
  underneath, the sibling of the already-held `libqmi-glib5`.

  Deliberately **not** held: `libyaml-0-2`, `libbsd0`, `libmd0` and
  `libprotobuf-c1`, which the FastRPC and SSC libraries link against. Each has
  between three and eighteen other installed reverse dependencies, so
  `apt autoremove` will not take them, and pinning base libraries to protect
  something that is not at risk costs security updates for nothing — the same
  reasoning that keeps `python3` off this list.
- **`mesa-opencl-icd`** is rusticl, the OpenCL runtime the Adreno 619 shows up
  through as `FD619`, which is what the terminal tools that use the GPU need.
  It is held for the reason `rhodep-gpu-opencl`'s postinst gives: rusticl on
  freedreno is young, this is the version the GPU was validated against, and an
  unattended upgrade is a plausible way to lose working OpenCL.

  It was **already held on the device and not in this list**, which is a
  different kind of bug and the reason it is here now: `rhodep-holds-enforce`
  only puts back holds that are in `apt-holds.txt`, so a hold set by a
  package's postinst is protected by nothing. One `apt-mark unhold` and it was
  gone for good. Verified by doing exactly that, and the enforcer now says
  `ALERT: holds had been removed, putting them back: mesa-opencl-icd`.

  Being honest about what the hold does *not* do: it is not what keeps the
  package installed. `apt autoremove` will not take it today — it is marked
  auto-installed, but `hashcat` and `python3-pyopencl` both depend on it and
  both are installed and manual, and a simulated autoremove with the hold
  released removes nothing. That only becomes the hold's job if those two ever
  go. `ocl-icd-libopencl1` (the ICD loader every OpenCL program links) is
  likewise auto-installed with six other reverse dependencies, so it is not
  held; `clinfo` is a diagnostic, not a dependency.

  The switch that actually turns rusticl on, `/etc/profile.d/rhodep-rusticl.sh`,
  belongs to no package on this image, so it is registered with
  `rhodep-protect-files` as the `gpu-opencl` component. Losing it is silent:
  `clinfo` simply reports zero platforms.

`gstreamer1.0-pipewire` is worth calling out because its absence fails in a
confusing way: the microphone is fine at every level this port controls, ALSA
and PipeWire both capture correctly, and every ordinary application still
records silence, because most of them (the GNOME sound recorder included) go
through GStreamer and that plugin is the only bridge from GStreamer to
PipeWire.

`squeekboard` is held because it is the keyboard once KWin is pointed at it, and
losing text input on a phone is not a recoverable-by-touch situation;
`plasma-keyboard` is held so the fallback stays installed. `python3` is
deliberately **not** held even though `rhodep-jack-watch` needs it: pinning the
interpreter of a rolling distribution to protect one small script would block
security updates and hold back much of the system.

## Login screen (GDM) instead of the phosh.service autologin

Out of the box the shell is started by `phosh.service`: a systemd unit with
`User=1000` and `PAMName=login` that opens a session for the `kali` user on tty7
and then does `chvt 7`. That is an autologin, so the first thing you see after
boot is Phosh's **lock** screen (the PIN keypad), never a chooser.

That is fine until something installs `gdm3` — and several Kali metapackages do
(`kali-linux-default` / `kali-themes-mobile` pulled it in here). gdm3's postinst
creates `/etc/systemd/system/display-manager.service`, which systemd's
`graphical.target` wants, so from then on **both** start at every boot: GDM puts
its greeter on tty1, `phosh.service` takes the display back with `chvt 7`. The
symptoms are exactly that: the keypad instead of a login screen, the user
chooser showing up only when it happens to win the VT, and then
*"a session is already open, force close?"* when you log in from it — because
the session on tty7 was opened by phosh.service's own PAM stack and GDM knows
nothing about it.

There must be one owner of the display. `userspace/login/install.sh` makes it
GDM:

```
sudo userspace/login/install.sh          # defaults to the 'kali' user
sudo reboot
```

- `phosh.service` disabled **and masked** — masking matters, a phosh package
  upgrade re-enables the unit otherwise.
- `display-manager.service` pointed at `gdm3.service`, and any DM that arrives
  later (`lomiri` and `xfce4` recommend lightdm, some plasma sets recommend
  sddm) is masked and pre-answered in debconf, so it cannot take the seat.
- `/var/lib/AccountsService/users/kali` gets `Session=phosh`, so GDM starts the
  same Phosh session after you pick the account, not GNOME.
- A **Switch user** launcher (`/usr/local/bin/switch-user`) calls GDM's
  `CreateTransientDisplay`, which opens a greeter on another VT and leaves the
  running session alone. Ordinary users are allowed to call it:
  `/usr/share/dbus-1/system.d/gdm.conf` permits that one method in the default
  policy context.

Requires **rhodep-phosh-wifi-guard >= 2**. v1's `airmon-safe` restarts
`phosh.service` when it cannot find Phosh, which under a display manager would
recreate the second, DM-unaware session on tty7 that this change exists to
remove; v2 looks for the `phoc` process and restarts `display-manager.service`
instead.

Two things this does **not** change, because they are not what a display manager
does: the screen lock still asks for the current user's password (session
locking is per session on any OS — the chooser belongs to login; use *Log out*
in the power menu, or the Switch user launcher), and Phosh's lock screen keeps
its numeric keypad. The keypad is not configurable — `sm.puri.phosh.lockscreen`
only has `require-unlock` and `shuffle-keypad` — but its bottom-left keyboard
button (`btn_keyboard` in `lockscreen.ui`) opens the full OSK for alphanumeric
passwords.

## Extra sessions (Plasma Mobile, Lomiri, Xfce)

With GDM in charge, other desktops are just packages; GDM lists everything in
`/usr/share/wayland-sessions` and `/usr/share/xsessions` and you pick one before
logging in (the gear/session menu). All three are in the Kali arm64 repos:

```
sudo apt install plasma-mobile      # Wayland, phone UI
sudo apt install lomiri             # Wayland (Mir), phone UI; recommends lightdm
sudo apt install xfce4              # X11, needs xserver-xorg; recommends lightdm
```

Install them **after** `userspace/login/install.sh`, or run that script again
afterwards: lomiri and xfce4 pull `lightdm` in as a Recommends and its postinst
will happily make itself the display manager. The script's `keep_gdm` step
(debconf preseed + masking) is what stops that.

**Plasma Mobile is the session this port is actually driven on.** It is more
comfortable than Phosh on this device, KWin runs accelerated on the Adreno 619,
and rotation, the on-screen keyboard and PowerDevil all behave. Two things about
it are documented elsewhere because they cost real time: its keyboard has no
Esc/Tab/Ctrl/arrows until `extra-tools/terminal-keyboard/` adds them, and
PowerDevil has no ambient-light support at all, so automatic brightness comes
from `rhodep-autobrightness` in `userspace/sensors/`.

## Suspend

Suspend works, and the phone wakes with its display intact. Getting there needed
two changes, and the failure it used to produce is worth describing because it
looks like a display bug and is not one.

**The symptom.** After a short screen-off everything was fine. After a long one
the screen would not come back at all — permanently black, no backlight, no
recovery except a forced reboot — while **SSH kept working normally**. That
combination is the diagnostic: the machine was alive, the display was not.

**The chain.** Three things stacked, and only the third was fatal:

1. A long idle makes PowerDevil actually *suspend*, rather than just blanking
   the screen. A short screen-off is only DPMS, which always worked.
2. Suspend freezes userspace, including `rhodep-sscrpcd`, the FastRPC listener
   the ADSP's sensors PD reads its registry through. The PD gets no answer and
   asserts, which is fatal to the whole ADSP:

	fatal error received: err_qdi.c:1045:EX:sensor_process:0x1:frpc_dsp

3. The ADSP restarts and re-registers its LPASS clocks. The previous crash left
   those clocks unregistered *while still prepared*, so the clk core reparents
   the orphans onto the newly registered clock and prepares it — inside
   `devm_clk_hw_register()`, before `q6dsp_clock_dev_probe()` has published its
   drvdata. `clk_q6dsp_prepare()` reads that drvdata, gets NULL, and oopses on
   `cc->desc` at offset 0x348.

**Why the display specifically.** The oops happens inside `__clk_register()`,
which runs holding the global clk `prepare_lock`. The worker dies with the mutex
held and nothing ever releases it, so every `clk_prepare_enable()` in the system
blocks forever. Turning the panel back on needs the DSI and DPU clocks, so it
hangs; SSH does not ask for a clock at runtime, so it does not. Same shape as the
`icc_lock` deadlock in `docs/interconnect-sm6375-wip/`.

**The two fixes.**

- **Kernel patch 0059** moves `dev_set_drvdata()` before the registration loop.
  This is an upstream mainline bug, not a rhodep quirk. `snd_q6dsp_common` is a
  module, so it needs **no reflash**: rebuild, copy the `.ko`, `depmod`, reboot.
- **`userspace/sensors/systemd/rhodep-adsp-sleep-hook`** stops the listener
  before the freeze and restarts it after, so the ADSP never asserts in the
  first place. Stopping it while awake was measured and does not disturb the
  ADSP.

They are complementary and both are worth having: the hook prevents the crash,
the kernel fix makes the crash survivable if it ever happens for another reason.

Measured on the device, 120-second suspend/resume cycles:

| | without 0059 | with 0059 | with 0059 + hook |
| --- | --- | --- | --- |
| kernel oops | yes | none | none |
| display after resume | black forever | comes back | comes back |
| SSH | dies with the network | survives | survives |
| sound card | gone | gone | **present** |
| sensors | dead | `iio-sensor-proxy` segfaults | **working** |

Confirmed afterwards the way it actually gets used, rather than with a script:
left idle until the screen went off, woken by the **power button** ~20 minutes
later. It had slept 1229.7 s in `deep`, the `pm8941_pwrkey` interrupt is what
woke it, and there was no ADSP crash, no oops, and the sound card, the sensors
and the display were all still there.

### How to measure a suspend, because the obvious way is wrong

**`PM: suspend entry` and `PM: suspend exit` do not tell you how long the phone
slept.** printk timestamps come from `sched_clock`, which is frozen across
suspend, so the gap between those two lines is *awake* time and excludes the
sleep entirely. Measured on the device with `rhodep-clock-test`, one 30-second
RTC alarm:

	BOOTTIME (/proc/uptime)   83.83 s   <- includes the sleep
	MONOTONIC                 55.20 s   <- excludes it
	printk (dmesg)            55.04 s   <- tracks MONOTONIC
	slept = BOOTTIME - MONOTONIC = 28.63 s      (alarm was 30 s)

So the duration is `BOOTTIME - MONOTONIC`, and nothing else. Confirmed twice:
28.63 s against a 30 s alarm and ~59 s against a 60 s alarm.

**Every "it slept N seconds" figure derived from printk deltas is therefore
unverified**, including the 20.5-minute one this section used to quote for the
power-button wake. What that test does establish, and what still stands, is
everything that is not a duration: it suspended, it woke on `pm8941_pwrkey`,
and the display, sound card and sensors were all intact afterwards.

Two more warnings for whoever measures this again. A suspend test on a phone
somebody is holding measures the person, not the kernel — one conclusion here
was wrong for exactly that reason. And log the wake *source* rather than the
duration: `/sys/kernel/debug/wakeup_sources` and the `pm8941_pwrkey` count in
`/proc/interrupts` say who did it.
And **do not trust `dmesg`**: the ADSP emits `Handover signaled, but it already
happened` about 4.2 times a second, for ever, which wraps the kernel ring buffer
every six minutes or so. Use `journalctl -k -b`, which keeps it all.

### On-screen keyboard under Plasma Mobile

Plasma Mobile uses `plasma-keyboard`, which is Qt Virtual Keyboard: its layouts
are `main`, `symbols`, `numbers`, `digits` and `dialpad`, and there is **no
terminal layout**, so no Esc, Ctrl, Tab or arrow keys. That is painful in a
terminal, which on this device is most of the point.

Squeekboard, the Phosh keyboard, does have terminal layouts (`terminal/us`,
`terminal/latam`, ...) and KWin can be pointed at any input method with

	kwriteconfig6 --file kwinrc --group Wayland --key InputMethod <desktop file>

but **do not bother: squeekboard crashes in a loop under KWin.** It was tried
here, leaves a trail of coredumps, and the result is a phone with no on-screen
keyboard at all until the setting is reverted and the session restarted, which
needs SSH. It expects protocols Phosh provides and KWin does not. Note also that
KWin only reads that setting when it starts, so the change needs a full session
restart either way. `squeekboard` and `plasma-keyboard` are both held so that
neither disappears in an upgrade, leaving no way back.

## SSH over the USB cable

```
sudo userspace/usb-net/install.sh     # on the phone, once
ssh kali@172.16.42.1                  # from the machine at the other end
```

This is not just convenience. **The modem cannot be restarted without dropping
WiFi**, because the WLAN protection domain lives inside it, so any modem work
done over WiFi loses the connection at the exact moment something interesting
happens. The USB gadget is the only link that does not depend on the modem.

The gadget is already there and is `ncm.usb0`, which Linux, macOS and Windows
10+ support natively. What is missing on a stock Kali rootfs is an address:
NetworkManager treats `usb0` as an ordinary wired interface and waits forever
for DHCP from a machine that is not serving any, so the interface sits up with
no IPv4 and nothing answers on 172.16.42.1.

The one non-obvious part is that the profile is `ipv4.method shared`, which runs
a DHCP server, **plus** a dnsmasq drop-in that sends DHCP options 3 and 6 empty.
Without that the phone also advertises itself as router and DNS, and the machine
on the other end can silently make a phone that routes nothing its default
route, losing internet. macOS reorders its network services exactly like that.

Note for containers: Docker Desktop on macOS runs containers in a VM, and the
host's USB network is not routed into it, so a container cannot reach
172.16.42.1. Forward a port on the host instead:

```
ssh -N -o GatewayPorts=yes -L 0.0.0.0:2222:127.0.0.1:22 kali@172.16.42.1
```

## Kali menu launchers under Plasma Mobile

```
sudo userspace/plasma-apps/install.sh
```

Out of the box most of the Kali menu is broken on this image, in two ways that
both hit tools which open in a terminal:

- Hundreds of launchers (Caido, bettercap, wfuzz, ...) call
  `/usr/share/kali-menu/exec-in-shell`, which belongs to `kali-menu`, and
  `kali-menu` is not installed. They fail with *"could not find the program
  /usr/share/kali-menu/exec-in-shell"*. 446 launchers were affected here.
  `kali-menu` is a single package that pulls in nothing else, so installing it
  is a clean fix, and it is held so it stays.
- The ones marked `Terminal=true` need a terminal KDE can drive with `-e`.
  Plasma Mobile ships gnome-terminal, whose wrapper dropped `-e`, so KDE cannot
  launch into it and reports *"terminal Konsole not found"*. Installing Konsole
  drags a partial KF6 upgrade onto a rolling system and is not worth the risk;
  gnome-console (`kgx`) is already present and does support `-e`, so KDE's
  terminal is pointed at it in `/etc/xdg/kdeglobals`.

## Phone apps (dialer / SMS / contacts / files)
Images built after this note already include them (see the rootfs build step).
On an older image the generic Phosh rootfs has the daemons (callaudiod,
feedbackd, modemmanager, gnome-contacts) but NOT the UI apps, so the drawer
shows no Phone/Messages. Add them once:
```
sudo apt install mobian-phosh-phone nautilus
sudo update-desktop-database      # refresh the app drawer
```
`mobian-phosh-phone` pulls the dialer (Calls), Chatty/SMS, mmsd-tng and the phone
role (ModemManager auto-config); `nautilus` is the file manager. **They will look
dead on a stock install**, and that is expected rather than a packaging problem:
`rhodep-ipa-hold.conf` keeps `ipa.ko` out of the boot, so there is no net port,
so ModemManager never creates the modem and every app that talks to MM has
nothing to show. Remove the hold file and reboot and they all come to life —
along with the watchdog reset. See "Known limitations".

## USB WiFi adapter (monitor mode / injection)
The single USB-C port defaults to **charging**. To power a USB adapter:
```
sudo otg on          # host + VBUS boost (SGM41542) -> adapter powers up
sudo airmon-ng start wlan1
sudo wifite
sudo otg off         # back to charging
otg status           # role / OTG / charger / battery
```
Injection: the mainline `rtl8xxxu` does monitor but injection is unreliable on
the RTL8188EUS. For monitor **+ injection** use the `rtl8188eus` (`8188eu`) DKMS
driver. It needs kernel headers and a small patch to build on 7.2 — the full
workflow (headers .deb, `apply-rtl8188eus-fix.sh`, DKMS build, autoload,
`apt-mark hold`) is in **`packages/rhodep-rtl8188eus-fix/README.md`**. Once
installed, `8188eu` autoloads at boot and `aireplay-ng --test wlan1mon` passes.

### GOTCHA: `airmon-ng check kill` takes Phosh down (black screen)
`airmon-ng check kill` stops `NetworkManager`/`wpa_supplicant` and kills network
processes. On this Phosh phone port that churn ends up stopping `phosh.service`,
and `phoc` (the Wayland compositor) takes >90s to tear down, so systemd SIGKILLs
it and triggers `OnFailure=getty@tty1` → **black screen with no auto-recovery**.
(The `ath10k ... chan info: invalid frequency 0 (idx 41 out of bounds)` kernel
message that shows up at the same time is a cosmetic WCN3990 firmware quirk and
is NOT related.)

Shipped by the `rhodep-phosh-wifi-guard` package (two reversible layers):

1. **`/etc/systemd/system/phosh.service.d/10-protect.conf`** — drop-in:
   `Restart=always`, `RestartSec=3s`, `TimeoutStopSec=15s`,
   `StartLimitIntervalSec=0`, empty `OnFailure=`. If anything kills or stops
   Phosh, systemd brings it back in ~3s instead of falling to getty. Verified
   with `systemctl kill --signal=SIGKILL phosh.service` → it comes back on its
   own.

2. **`/usr/local/sbin/airmon-safe`** — wrapper that does the "check kill" while
   explicitly shielding the `phoc`/phosh tree and restoring the network on exit:
   ```
   sudo otg on
   sudo airmon-safe start wlan1   # kill interference WITHOUT touching phosh + monitor
   sudo wifite
   sudo airmon-safe stop  wlan1   # managed + restore NetworkManager/wpa_supplicant
   sudo otg off
   ```
   Standalone subcommands: `airmon-safe kill` (the protected check-kill only) and
   `airmon-safe restore` (bring the network back).

**Rule:** for WiFi auditing use `airmon-safe` instead of `airmon-ng check kill`.
With layer 1 in place even a raw `airmon-ng check kill` no longer leaves a
permanent black screen (Phosh returns on its own), but `airmon-safe` avoids the
flicker entirely. Monitor mode is **USB-only** (wlan1); the internal WCN3990
`wlan0` has no raw mode.

---

# Recovery / rollback
- Any bootloop → flash `rescue-boot.img`, `telnet 172.16.42.1`, then dd whatever
  you want to userdata.
- Back to pmOS → dd the pmOS disk image to userdata and flash the pmOS boot.img.

# How to continue the port (open work)

Ordered by value. The single highest-value item is the watchdog reset, because
it is the one thing between this port and a phone with working mobile data —
and as of the GNSS measurement it may not be an IPA bug at all. Everything below
it is either not started or a research project.

1. **The watchdog reset.** The modem is done — it registers on LTE, data was
   measured at ~24 Mbit/s, calls connect and SMS arrives — but with `ipa.ko`
   loaded **and** the modem attached to LTE the SoC watchdog-resets every 3 to
   10 minutes, silently, with the ramoops console stopping mid-line. Either
   condition alone is stable (22 and 16.5 minutes observed), so the port ships
   `rhodep-ipa-hold.conf` and pays for it with mobile data and ModemManager.

   **Read §5 session 15 of HANDOFF-SESSION4.md before touching this.** Three
   hypotheses have already been tested on the device and buried: the unvoted NoC
   path to IMEM, that vote being demand-driven rather than a floor, and the IPA
   SRAM layout inherited from qcm2290. Patches 0044-0048 are each a correction
   checked against the vendor tree and are all kept, and none of them is the
   cause. Do not re-test them.

   **The experiment that would settle it has never been run**: attach to LTE
   with `ipa.ko` never loaded. It needs the LTE attach APN set by hand over
   `qmicli`, because ModemManager is what normally configures it and MM will not
   touch a QMI modem with no net port. Method matters here: do not just delete
   the hold file, because the window is short and a reset costs a reboot — boot
   with the file in place, bring the radio up by hand, confirm `registered`, and
   only then `insmod` the module with `dyndbg==p`, so the causal window is
   seconds wide. Log uptime from a systemd unit rather than trusting ssh: the
   screen locking takes WiFi down and is indistinguishable from a reset.

   Two things worth recording so nobody re-derives them. **memshare was
   genuinely missing and is now implemented** — the modem really does ask the
   application processor for memory during bring-up, `userspace/modem/` answers
   it and v93 onwards reserves the region. And **the SIM was never the cause**,
   which is worth stating because a dead prepaid card is the natural suspect:
   the real fault was that the modem fetches its run-time images from the AP
   over TFTP and every request was answered "file not found". Also ruled out
   with evidence: the SIM lock, the carrier configuration, rmtfs read-only
   mode, and any missing QMI service — every service id from 1 to 235 that
   nobody provides was offered across cold boots, and answering the two the
   modem reaches for changed nothing.

   **And there is now a second way in, which is the reason to reopen this.**
   Starting a GNSS session watchdog-resets the SoC too — in under a second,
   3/3, with `ipa.ko` never loaded, no SIM, no ModemManager and no network. Same
   silence: console stops mid-line, `bootreason=watchdog`, no panic. If it is
   the same bug, the expensive reproducer (SIM + LTE attach + 3 to 10 minutes)
   has been replaced by `sudo scripts/rhodep-gnss-test.py 40 min`. The
   hypothesis that follows — that neither reset is about the IPA, and both are
   mpss moving data against AP memory — would also explain why every IPA-side
   correction in 0044-0048 was individually right and none of them helped.
   Evidence, the argument against it, and the four next steps are in
   [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md).

2. **GPS itself**, once the reset is understood, is downstream of it: there is
   nothing to test until a session can run without rebooting the phone. Note
   for whoever gets there: the XTRA almanac on the device expired 2026-08-04,
   and `qmicli` alone cannot drive a GNSS session at all — see the same
   document for why every shell-only attempt fails silently.

3. **In-call audio.** Calls connect and there is no sound, and this is *not*
   blocked on the modem: Qualcomm voice audio goes modem ↔ ADSP ↔ codec and
   mainline has no q6voice (MVM/CVS/CVP) at all. A driver to write, not a bug to
   fix. Scoped in HANDOFF-SESSION4.md session 14.
4. **Audio**: speaker, earpiece, headphones, the microphone and jack detection
   all work; see `docs/interconnect-sm6375-wip/AUDIO-SM6375.md` §6. Three things
   are left, in order of how cheap they are:
   - **Switching to the speaker mid-stream** leaves it silent until the stream
     is reopened, because ASoC powers the amplifiers ~300 ms before the I2S
     clock they lock onto exists. Needs an ordering fix in ASoC or the machine
     driver; retrying in the codec driver provably cannot work, since the retry
     loop is what delays the backend start.
   - **Drop the dead device tree nodes**: the four VA-macro DMICs and AMIC2 are
     not populated on this board. Cosmetic, so batch it with the next reflash.
   - **Keeping the codec awake should not be necessary — half of it is done.**
     Jack detection relies on a udev rule pinning the soundwire slave on,
     because MBHC detects nothing while it is suspended, and the port burns
     power holding it up. Patch 0095 supplies what was missing underneath: the
     wake interrupt sm6375.dtsi never gave swr0, so the slave had no way to
     signal while the link was stopped.

     **The wake path works, measured.** With the TX slave released to `auto` it
     reaches `suspended` in two seconds, and plugging a jack in wakes it by
     itself — `swr_wake_irq` goes 1 → 2 → 3 on the transitions and
     `rhodep-jack-watch` logged all four switches correctly.

     Two things still block removing the rule. MBHC's impedance measurement
     fails on a freshly woken codec:

	Impedance detect ramp error, c1=2, x1=0x0
	wcd_measure_adc_once: adc complete: 0, adc timeout: 1

     which is what tells a headset from headphones and drives the buttons, so
     insertion working is not enough. And **the saving has never been measured**
     — every attempt so far has been with the phone charging, where the fuel
     gauge reports the charge current and the codec's few mA are invisible.
     Removing the rule to save an unknown amount is not a trade worth making
     blind, especially next to a display now running at 120 Hz.

     Beware of testing this the way it was first tested: flipping
     `power/control` to `auto` on a running system left the speaker silent, with
     `aw88261 ... start failure (-1)` on both amplifiers, which the driver
     raises when the I2S clock is not there. A reboot cleared it. The
     measurement has to start from a cold boot, not from a live edit.

   Call audio is item 3, and it is a missing driver rather than an audio bug.
5. **The camera — started, but it does not capture yet.** Only the power rails
   are up: the FAN53870 camera PMIC driver (patches 0051/0052) is written,
   compiles clean, probes on the device and registers all seven LDOs, with the
   programmed voltages verified against the chip's registers. No image path
   exists yet (no camss/CSID/TFE/sensor nodes), so there is no photo or preview.
   Note it is a FAN53870 at 0x35, *not* the
   `semi,wl2868c` at 0x2f the vendor DT claims — checking the hardware before
   writing the driver is what caught that. Next: a `qcom,sm6375-camss` entry
   (qcm2290's is the template, same CSID and TFE revisions), the
   CSIPHY/CSID/TFE nodes, and the S5KJN1 sensor node. First meaningful test
   after that is a raw capture, not a photo.
   [`CAMERA-SENSORS-FEASIBILITY.md`](docs/CAMERA-SENSORS-FEASIBILITY.md)
6. **NFC and fingerprint** — the two that are left of what used to be the "hard
   three". NFC is a Samsung `sec-nfc` on i2c7 with no mainline driver;
   fingerprint is a proprietary Focaltech HAL. Older technical notes (I2C
   addresses, GPIOs) are in `docs/KERNEL-TECHNICAL.md` §7.

   **Sensors came off this list.** They work — see `userspace/sensors/`. What
   made it tractable is worth carrying to the other two: the blocker was never
   the SSC protocol, which an earlier session had already worked out, but the
   fact that the sensor core had no *registry*, and the registry lives on the
   application processor and is fetched over FastRPC by a listener nobody was
   running. The listener needed no kernel work: it is ordinary forward
   invocations on a static handle, and Qualcomm's own userspace already
   supports the mainline driver. Left over from that, for anyone continuing:
   the gyroscope and the DSP's fusion sensors are enumerated and streaming but
   have no `iio-sensor-proxy` driver, so nothing on D-Bus exposes them yet.
7. **Interconnect driver** (`docs/interconnect-sm6375-wip/`, patches 0027/0028/
   0046): **it works now.** Session 15 found and fixed four bugs that were
   invisible until the module was actually loaded — NULL holes in the node
   arrays, compatibles that did not match the DT, QoS writes that park a CPU,
   and an `#interconnect-cells = <2>` provider being fed one cell per specifier.
   On v97 six providers bind, no oops, and the IPA probes cleanly. It is not
   needed for mobile data and it did not fix the watchdog reset, but it is real
   SM6375 support that benefits both ports and is worth upstreaming. It is still
   held out of the boot by `rhodep-icc-hold.conf` until it has been loaded by
   hand at least once on a device tree that has the provider nodes. See
   `docs/interconnect-sm6375-wip/PROGRESS.md` and §5 session 15.
8. **Why the ADSP re-signals handover 4.2 times a second.** From ~30 ms after
   it boots, on every boot, for as long as it is up, the ADSP re-asserts its
   handover smp2p bit — `smp2p-adsp` IRQ 16 and the `q6v5 ready` / `q6v5
   handover` bits fire together each time, without it ever crashing (`q6v5
   fatal` stays at 0). The interrupt is `IRQF_TRIGGER_RISING`, so each one is a
   genuine 0->1 edge: smp2p and the handover logic are both behaving, and the
   remote really is doing this.

   **The log damage is fixed** by patch 0060, which rate limits the message to
   one a minute per remote; `dmesg` reaches `[0.000000]` again instead of
   holding about six minutes. What is *not* fixed is the behaviour itself,
   which is still unexplained. It costs a wakeup-capable interrupt 4 times a
   second on a device that is trying to idle, so it is worth understanding even
   though it no longer hurts debugging.
9. ~~**cpuidle never initialises, so the cores only ever WFI.**~~ **Fixed by
   patch 0061.** Kept here because the diagnosis is the useful part. It was not
   a missing
   device tree and not a missing config -- `sm6375.dtsi` has the `idle-states`,
   the `psci` node carries all nine CPU power domains, and
   `ARM_PSCI_CPUIDLE`, `ARM_PSCI_CPUIDLE_DOMAIN` and `QCOM_MPM` are all `=y`.
   It is a race between two initcalls, decided by 51 milliseconds:

	[0.109751] CPUidle PSCI: failed to create CPU PM domains ret=-517
	[0.373495] CPUidle PSCI: CPU 0 failed to PSCI idle
	[0.373539] CPUidle PSCI: Failed to create psci-cpuidle device
	[0.424671] CPUidle PSCI: Initialized CPU PM domain topology using OSI mode

   The domain driver defers because the cluster domain's parent is the MPM
   node, which has not probed yet; that resolves fine at 0.4247. But
   `psci_idle_init()` is a `device_initcall` and runs at 0.3735, gets
   `-EPROBE_DEFER` from `dev_pm_domain_attach_by_name()`, and creates its
   device with `faux_device_create()` -- **which has no deferred-probe
   support**, so nothing retries. Upstream bug, not a rhodep one. The fix is in
   generic `drivers/cpuidle/cpuidle-psci.c` and `ARM_PSCI_CPUIDLE` is built in,
   so it needed a new boot image rather than a module swap.

   **Measured after flashing:** `current_driver` went from `none` to
   `psci_idle`, `cpu0` reports `WFI` / `cpu-sleep-0-0` / `cpu-sleep-0-1` (the
   big cores get `cpu-sleep-1-*`), the per-CPU domains show `off-0` in
   `pm_genpd_summary`, and in a 10-second window `cpu0` entered the deepest
   state 430 times for 9734 ms — about 97% of wall time in power collapse,
   against 0% before. No oops, no regressions.

   **What could not be shown is a battery saving.** An A/B/A on the same boot,
   screen off, toggling `state*/disable`: 67.5 mA with the deep states, 71.1 mA
   with only WFI, 72.1 mA with the deep states again. The difference is smaller
   than the drift between the two control runs and far smaller than the ±30-43
   mA spread, so it measures nothing. The likely reason is item 10.
   `docs/interconnect-sm6375-wip/GNSS-SM6375.md` has the full analysis.
10. **The system takes more than 100 interrupts a second while idle**, which is
    both a power cost and the reason the cpuidle A/B above could not measure
    anything. Sampled with the screen off and nothing running:

	IPI1            78.9/s      arch_timer      33.7/s
	arch_mem_timer  58.7/s      ipcc_0          16.1/s
	glink-smem      15.9/s      smp2p-adsp       4.2/s

    `smp2p-adsp` is item 8. `ipcc_0` and `glink-smem` at ~16/s each are the
    unexamined ones. Some of the timer traffic is expected -- a CPU in a deep
    state stops its local timer and the broadcast timer takes over -- so this
    needs separating into "consequence of idling" and "reason we cannot idle"
    before anything is concluded.
11. **After a successful `deep` sleep, systemd tries `s2idle` once more.** It
   returns in about 3 ms, so the write to `/sys/power/state` is reporting an
   error even when the sleep worked — systemd only moves to the next state in
   `SuspendState=` when the previous one fails. Purely cosmetic, but it makes a
   suspend log read as if something went wrong when nothing did. Nobody has
   chased where the error comes from.

   This item used to also claim that `deep` "sometimes aborts spuriously". That
   was wrong twice over and is worth not repeating: both aborts on record have
   ordinary explanations — one ran 40 seconds into a boot, the other happened
   because the phone's owner pressed the power button during the test — and
   blaming the ADSP interrupt storm in item 8 for them was speculation built on
   top of the misreading.

# Nice to have in the future

Small, self-contained things that would make the phone nicer to live with. None
of them blocks anything, and each is written down with the reason it is not
already done.

1. **An "automatic brightness" tile in the quick settings shade.** Plasma Mobile
   6.7.2 ships 21 tiles — `airplanemode audio autohidepanels battery bluetooth
   caffeine docked donotdisturb flashlight hotspot keyboardtoggle kscreenosd
   mobiledata nightcolor powermenu record screenrotation screenshot settingsapp
   waydroid wifi` — and none of them is auto-brightness, so the only way to
   toggle it is Settings → Display Configuration.

   The tile itself would be trivial: they live in
   `/usr/share/plasma/quicksettings/` and the Night Color one is about twenty
   lines of QML around a `QS.QuickSetting` with a `toggle()`. **The blocker is
   that nothing outside KWin can flip the setting.** `kscreen-doctor` has no
   option for it, KWin exposes no D-Bus method, and `libKF6Screen*` exports no
   `autoBrightness` symbol — the KCM writes it through KScreen's internal API.
   So a working tile needs a small C++/QML plugin against libkscreen, which is a
   project rather than an afternoon.

2. **The glitched lines while the brightness ramps down.** Still open, but no
   longer a guess -- the measurements below are the useful part, because the
   obvious explanation has already been tried and is wrong.

   It is not simply "the DCS write lands mid-frame". Patch 0062 added `bl_lpm`,
   which sends the brightness in LP escape mode so the host must leave the HS
   burst first and the write lands between frames by construction. Tested
   against the real workload -- KWin's ambient brightness with a torch on the
   sensor -- **the lines are identical with `bl_lpm=1` and `bl_lpm=0`**.

   What is known, from `scripts/rhodep-bl-capture` sampling the backlight at
   20 Hz while the user drove it with a torch:

   - **It is asymmetric.** KWin ramps *up* in ~13 writes over 0.8 s and *down*
     in 60-180 writes over 3.5-7.2 s, and only the long downward ramp shows it.
   - **Synthetic writes do not reproduce it at all** -- not 2 writes/s with
     3200-step jumps, and not KWin's own 14 writes/s sustained for seconds.
   - Those synthetic writes happen against a **static screen**, and on a command
     mode panel a static screen means there are no frame transfers to collide
     with. So the trigger appears to need brightness writes *concurrent with the
     compositor repainting*, which is also why the artefact only shows up under
     KWin and never under a script.
   - `dsi_err_worker: status=5` does fire during the real workload, but under
     synthetic load it does not track the mode or the rate (0 errors at 14/s, 3
     at 4/s), so it is not yet a usable proxy.

   **Three fixes have been built, flashed and disproven.** Each was plausible,
   each cost a flash, and none changed the artefact at all. They are listed so
   that nobody spends another cycle on them:

   | tried | patch | result |
   | --- | --- | --- |
   | brightness in LP instead of HS | 0062 | lines identical with `bl_lpm` 1 and 0 |
   | DSI clock 100% -> 230% of minimum | cmdline | identical, and `clk_scale=230` costs nothing either way |
   | wait for `CMD_MODE_MDP_BUSY` to clear before a command DMA | 0063 | identical, and the wait never once timed out |
   | DCS *reads* (`actual_brightness`, which needs a BTA) | -- | 2 errors in 200 reads, against 70-79 in one real run. A contributor at most |

   So it is not the link state, not link bandwidth, and not a simple collision
   with an in-flight frame. What is left and untested is the raw content of
   `REG_DSI_TIMEOUT_STATUS` and `REG_DSI_FIFO_STATUS`: `dsi_err_worker()`
   collapses them into one bit each and clears them without ever printing the
   values, so "status=5" is all anyone has. Logging those two registers is the
   obvious next step, and it is a diagnostic rather than another guess.

   `userspace/sensors/rhodep-autobrightness` used to hide the artefact by
   capping how far one command could move the level; KWin does not cap it,
   which is why it became visible.

   **This is now characterised.** The asymmetry above is a red herring: a long
   downward ramp simply means more writes, and it is the writes that matter,
   not the direction. Every occurrence is `DLN0..3_HS_FIFO_UNDERFLOW` with the
   MDP mid-transfer and the command DMA idle, so a DCS write is landing on the
   pixel stream. `dsi_ctrl_enable()` already asks the controller to keep command
   DMAs out of a frame but never programs the window it may use instead, because
   that register is not in the map mainline generates its headers from. Four
   attempts at supplying it, two of which left a black screen, are written up in
   `docs/display-cmd-dma-window-wip/` with the dead ends and where to pick it
   up. Read that before trying a fifth.

3. ~~**Give the FAN53870 node a `vin-supply`.**~~ ~~**Drop the device tree
   nodes for hardware this board does not have.**~~ **Both done**, patches
   0089-0091, in `kali-boot-v114-STABLE.img`.

   The supply needed a driver change as well as the device tree property:
   `fan53870.c` declared no `supply_name` at all, so `vin1-supply` alone would
   have been ignored. LDO1/LDO2 (the low group, `cam_vdig`) now name `vin1` and
   the board wires it to PM6125's S6, which is what the vendor device tree says.
   LDO3-7 are deliberately left with no supply: their input is a real pin too,
   but nothing names it, and inventing a property no device tree sets would only
   make the core substitute a dummy regulator and warn about it five times a
   boot.

   The microphone cleanup dropped the `audio-routing` entries for AMIC1, AMIC2
   and the four VA DMICs. The VA macro's own `dmic01`/`dmic23` pinctrl was
   **left in place on purpose**: that macro provides `fsgen` to the RX and TX
   macros, so nothing audio-related probes without it, and `pinctrl-0` cannot be
   left empty.

5. **Behave like a phone with the screen locked — done, and confirmed in the
   street.** A walk with the screen locked and the TP-Link scanning through OTG:
   the WiFi held, the capture ran through, and the adapter neither dropped nor
   came back as a different interface. That is the scenario this entry was
   opened from, and it had never been tested anywhere but on a desk with a cable
   attached.

   It took three separate fixes, and only the first was where the problem looked
   like it was.

   The immediate cause was not power policy at all: **`deep` suspend does not
   work on this SoC, and it was the default.**

   Asked to sleep for twenty seconds it came back after two, every time, with
   nothing in the log and nothing in `/sys/power/pm_wakeup_irq` -- so not a
   wakeup, but the suspend itself returning:

   | mode | asked | slept |
   | --- | --- | --- |
   | deep | 20 s | 2 s |
   | s2idle | 20 s | 22 s |

   With the screen off the phone was therefore entering and leaving suspend
   every couple of seconds, and every cycle re-enumerates USB. Anything running
   off a USB adapter lost its interface and died, and OTG had to be switched on
   by hand afterwards -- which is how this was found, from a WiFi scan on a
   TP-Link dongle that did not survive locking the screen in the street.

   `rhodep-suspend-mode.service` now selects s2idle at boot. Measured across a
   25 s suspend afterwards: slept 27 s, OTG still on with the charger's boost
   register untouched (`reg01=0x3a` before and after), and a background process
   still running with a single 28-second gap in its heartbeat. A rootfs change,
   so it needs no reflash; `mem_sleep_default=s2idle` on the kernel command line
   would do the same at the cost of a boot image.

   **The link survives too.** Measured across a 27 s suspend with s2idle: same
   BSSID before and after, still associated. So the half of this that looked
   like a driver problem was the broken suspend mode all along.

   **ath10k does advertise WoWLAN here** -- wake on disconnect, on magic packet,
   on pattern match up to 22 patterns, and on network detection up to 16 match
   sets. `/etc/NetworkManager/conf.d/rhodep-wowlan.conf` asks for magic-packet
   wake. **Verified.** `iw phy0 wowlan show` after a normal reconnect reports
   `WoWLAN is enabled: * wake up on magic packet`, from the shipped
   `conf.d` file alone, with the connection profile left at its default
   (`0x1`). It really is only programmed at activation, so a link that has been
   up since boot -- or one brought back by hand after unloading `ath10k_snoc` --
   reads `disabled` and says nothing about the configuration.

   **Jobs left running no longer get frozen**, which was the real complaint --
   start a capture, lock the screen, come back an hour later and find it stopped.

   `rhodep-keep-awake.service` does not match program names, because you do not
   know in advance what you will leave running. It watches for *work*: a process
   burning CPU is doing something, a shell at a prompt is not. Whatever you start
   tomorrow is covered without being listed. Measured: a busy loop started with
   no configuration at all produced `holding the phone awake: trabajo.sh busy`,
   and `letting it sleep: nothing is working` when it ended.

   Worth being clear that **Android is not automatic about this either** -- an
   app declares itself with a foreground service and a notification, and what
   does not declare itself gets frozen. This is the same bargain made without
   the notification.

   **And the last piece was NetworkManager throwing the link away on resume**,
   which is documented under "Bugs" above: NM skips wake-on-lan devices when
   suspending and then takes them down "belatedly" on the way back, so the
   association survived the sleep and was destroyed a second after waking.
   `userspace/power/systemd/system-sleep/rhodep-wowlan` disarms WoWLAN for the
   instant NM looks, and `rhodep-wowlan-check.timer` makes sure it always ends up
   armed again.

   Two things stop it becoming a phone that never sleeps, both in
   `/etc/rhodep/keep-awake.conf`:

   - `cpu_percent`, the threshold. The compositor and shell sit at 5-10% with
     the screen on and are in the default `ignore` list for that reason: with
     the screen on nothing was going to suspend anyway, and if they are still
     busy with the screen off, that is a bug in them and staying awake would
     hide it.
   - `battery_floor`, below which the hold is dropped even with work running.
     A flat phone in your pocket is worse than an interrupted capture, and it is
     the failure you cannot see coming.

   `unit:`, `proc:` and `procf:` entries still exist for jobs that spend their
   time blocked rather than computing -- waiting on the network or a device --
   which the CPU heuristic cannot see. Prefer `proc:` over `procf:`: command-line
   matching also matches a shell that merely *mentions* the name, and it once
   matched the ssh command used to test it.

   The log names whatever it is holding for, so if something keeps the phone
   awake, `journalctl -u rhodep-keep-awake` says what and an `ignore:` line
   settles it.

7. **A keyboard for X11 and Java apps, which needs a KWin patch.** Burp Suite is
   Java on XWayland and shows no on-screen keyboard at all, and neither does any
   other X11 app. This is not configuration: everything reachable from outside
   KWin has been tried and measured, and `userspace/keyboard/README.md` has the
   numbers.

   - `forceActivate` over an X11 window does nothing. With Burp focused and a
     cursor in a text field, `active` and `visible` stayed false for five
     seconds of sampling. KWin will not activate the keyboard when the focused
     client cannot receive text.
   - `mode` on `org.kde.kwin.VirtualKeyboard` is advertised as writable and is
     not. Writing 0, 2 or 3 leaves it at 1.
   - No third-party keyboard can inject keys. KWin offers only
     `zwp_input_method_v1` to ordinary clients and keeps
     `zwp_virtual_keyboard_manager_v1` for the input method it launches, so
     wvkbd and squeekboard cannot place a keystroke anywhere.
   - onboard does work -- an X11 client injecting through XTEST, which reaches
     every X11 app without needing its cooperation -- and was rejected on how it
     feels rather than whether it functions.

   **Measure this first, because it decides which patch to write.** Does
   `plasma-keyboard` deliver keys as keycodes through
   `zwp_virtual_keyboard_v1`, or as committed strings through the text-input
   protocol? Run it under `WAYLAND_DEBUG=1` and watch which interface it binds
   and which requests carry the keystrokes. The answer splits the work in two:

   **If it sends keycodes**, the patch is small. Keycodes go to the seat's
   keyboard focus, which is whatever window is focused -- XWayland included --
   so the keys already have somewhere to land and the only thing missing is
   permission to show the keyboard. In `src/inputmethod.cpp`, activation is
   gated on there being a text-input-enabled surface; `forceActivate()` needs to
   be allowed to proceed without one, and the input method's lifetime needs to
   stop being tied to a text-input client that will never exist. Roughly: an
   "activated by the user rather than by an application" state that show/hide
   respects and that focus changes do not immediately clear.

   **If it commits strings**, there is nothing for an X11 window to receive and
   the patch is bigger: KWin would have to synthesise key events from the
   committed text for surfaces with no text-input, which is the same job
   XTEST does for onboard, done inside the compositor.

   Either way it is a KWin patch and a KWin rebuild rather than a setting, and
   worth checking against upstream first -- an on-screen keyboard that cannot
   type into X11 applications is not a rhodep problem, and someone may already
   be arguing about it.

8. **Automatically keep the phone awake for anything you start, not a fixed
   list.** Today `rhodep-keep-awake` holds the phone up for two things: work
   that burns CPU (caught automatically), and units or processes named in
   `/etc/rhodep/keep-awake.conf` (the pwnagotchi stack is anchored there because
   it waits on packets rather than computing). What is missing is the phone-like
   behaviour of *anything* you start staying alive across a lock without naming
   it first.

   An attempt at fully automatic detection was built and reverted, because it is
   harder than it looks and the failure mode is a phone that silently does not
   sleep and goes flat in your pocket. The two workable directions, and why the
   naive one does not work:

   **What does not work: a photo of the running services at boot.** The idea was
   to snapshot every service running on a fresh boot as "infrastructure", and
   treat anything new as your work. Two things break it, both measured:

   - Services that start late inflate the baseline. docker and containerd spawn
     transient units while bringing containers up, so a baseline taken 20 s in
     held 38 services while the steady state was 35. The live set is then a
     *subset* of the baseline, `now - baseline` is always empty, and nothing is
     ever seen as new.
   - It gets the timing backwards for the real case. To keep pwnagotchi -- which
     you start *before* locking -- alive, the baseline must be "what ran before
     you started working", not "what ran at lock time". Freeze it at lock and
     pwnagotchi is already in it and never counts.

   **Direction A is now implemented** (`terminal_work: yes`, on by default).
   A non-shell process inside a tty or pts login session scope counts as work,
   so anything started from a terminal -- wifite, tshark, a build, as root or
   not -- keeps the phone up without being named first. The graphical session is
   excluded by `Type`, not by name: it is `wayland` here and full of long-lived
   non-shell processes that would pin the phone awake for ever.

   **The trap, which the design above walks straight into: an ssh login is a
   pts session too.** Left as written, `sshd` counts as work and the phone never
   sleeps while anyone is connected -- exactly the flat-battery failure this
   feature has to avoid. Shells and session plumbing are therefore skipped, and
   that has to be a *prefix* match: modern OpenSSH names the per-connection
   process `sshd-session`, not `sshd`, so an exact-name list misses it. That was
   caught by testing rather than by reading, and it is the whole difference
   between this working and quietly draining the battery.

   Verified on the device, five cases: an idle ssh session does not hold;
   `sleep 60` started in it does, and is named in the log; killing it releases;
   `terminal_work: no` disables the whole thing; and the wayland session never
   appears. The service's own inhibitor cannot re-trigger it either, because it
   lives in `/system.slice`, not in a user session scope.

   Still open is Direction B below -- this covers what you start from a
   terminal, not what a graphical app starts.

   **Direction B, the convenient one: dynamic baseline that tracks until you
   work.** Instead of a boot photo, track the running-service set continuously
   while the screen is unlocked and freeze it only when you *start* something --
   detected as the first service to appear that is not in the tracked set --
   rather than at lock time. This is what "just works" would look like, and it
   is where the fragility lives: distinguishing a service you started from one a
   timer or socket activated, without a per-service "who started me", is the
   unsolved part. systemd does not expose that reliably, especially when
   everything runs as root, which on this phone it does.

   Two facts already measured that the next attempt should keep. `loginctl
   show-session -p LockedHint` reports the lock state cleanly. And the inhibitor
   must be killed by process group, not by pid: `systemd-inhibit` spawns a
   `sleep` to hold the lock, and terminating only the parent leaves the sleep
   reparented to init with the lock still held -- a hold that never releases.
   `start_new_session=True` and `os.killpg` fixed it, and that fix is in the
   current script.

6. **Stop needing the codec kept awake for jack detection.** A udev rule pins
   the soundwire TX slave on because MBHC detects nothing while it is
   suspended. The real fix is for the codec driver to keep that block alive
   across runtime suspend, which would also stop the port burning power to hold
   the slave up.

9. **A flashlight toggle** -- and the flash is needed for the camera anyway.

   The good news is that this looks cheap, because the flash on this board is
   **not** an i2c LED driver chip. The vendor describes it as a plain GPIO:

	led_flash_rear: qcom,camera-flash@0 {
		compatible = "qcom,camera-flash";
		flash-type = <CAM_FLASH_TYPE_GPIO>;
		gpios = <&tlmm 49 0>;
		pinctrl-0 = <&flash_active>;
	};

   so in mainline terms it is a `gpio-leds` node on tlmm 49, not a driver to
   write. There is a second one, `led_flash_macro` at cell-index 2, for the
   macro camera.

   And unlike the auto-brightness tile in item 1, **Plasma Mobile already ships
   a `flashlight` quick setting** -- it is in the stock list of 21 tiles. So the
   work is likely to be the device tree node plus whatever name that tile
   expects to find under `/sys/class/leds`. Check that before assuming the tile
   will pick it up: it may want a specific name, or it may go through the V4L2
   flash API rather than the LED class, and those are different amounts of work.

   **Confirmed on the device.** tlmm 49 was free and unclaimed, driving it with
   `gpioset -c gpiochip3 -t 1s 49=1` blinks the rear flash, and the pin shows as
   `GPIO 500000.pinctrl` in pinmux-pins while held. So it is one GPIO and
   nothing else: no rail to hunt for, no i2c part.

   **The gpio-leds node in patch 0092 is not enough, and the reason is the
   interesting part.** It creates `/sys/class/leds/white:torch`, Plasma's tile
   finds it (the plugin matches `*:torch` over udev and reads `max_brightness`),
   and writing 1 does light the LED -- for a few seconds. Then it goes out while
   `brightness` still reads 1. So the LED class thinks it is on and the hardware
   disagrees.

   The vendor explains it. The flash needs **two** pins, not one:

   | pin | role |
   | --- | --- |
   | tlmm 49 | enable, via `qcom,camera-flash` with `flash-type = <CAM_FLASH_TYPE_GPIO>` |
   | PM6125 gpio8, function `func1` | current, as PWM from `&pm6125_pwm 0` |

   and `cam_flash_pm6125_gpio/pm6125_flash_gpio.c` sets the current *as the duty
   cycle*:

	#define PM6125_PWM_PERIOD          50000   /* 50 us */
	#define FLASH_FIRE_LOW_MAXCURRENT    150   /* mA, torch */
	#define FLASH_FIRE_HIGH_MAXCURRENT  1200   /* mA, strobe */

	duty_cycle = PM6125_PWM_PERIOD * real_current / FLASH_FIRE_LOW_MAXCURRENT;

   Holding the enable high with no PWM is therefore "strobe at full current",
   and the driver IC's safety timer cuts it. Android never does that: in torch
   mode it asks for at most 150 mA, about 12% duty. That is why it stays lit
   there and not here.

   What it actually takes:

   1. `qcom,pm6125-pwm` in `drivers/leds/rgb/leds-qcom-lpg.c`. Mainline has
      pm8916, pm8350c, pmi632 and others but not this one, and an entry is six
      lines: the vendor's node is `qcom,pwms@b300`, `reg = <0xb300>`,
      `qcom,num-lpg-channels = <1>`, which maps onto `.num_channels = 1` and
      `.base = 0xb300`. Upstreamable on its own.
   2. The LPG node in the PM6125 device tree, and PM6125 gpio8 muxed to `func1`.
   3. `pwm-leds` instead of `gpio-leds`, period 50 us, brightness scaled into
      the torch range rather than the strobe one.
   4. Somewhere to keep tlmm 49 asserted -- `leds-pwm` has no enable property,
      so this is the part that needs a decision rather than transcription.

   Steps 1 to 3 are mechanical. Patch 0092 should be replaced, not extended.

10. **NFC.** Samsung `sec-nfc` (S3NRN) at 0x27, with ven=tlmm48, firm=tlmm8,
    irq=tlmm9 and clk_req=tlmm7.

    This got cheaper without anyone intending it: the chip sits on
    `qupv3_se7_i2c`, which is mainline's `i2c7`, and **patch 0052 already
    enables that bus** for the camera PMIC. The bus, its pinctrl and its clocks
    are up and working today, so what is missing is only the driver.

    Before writing one, check whether mainline's `s3fwrn5` fits. This port's
    older notes say "mainline has nxp-nci, st-nci, pn544, but not Samsung's",
    and that is not quite right -- `drivers/nfc/s3fwrn5` is a Samsung driver.
    Whether it matches this particular part is unknown and worth ten minutes,
    because it is the difference between adding a device tree node and writing
    a driver from scratch.

    Note the SAR sensor `semtech,sx937x` at 0x2c shares that bus, and has no
    useful mainline driver either.

    **Measured on the device, so nobody repeats it.** The hardware is fitted:
    the bootloader declares `mmi,nfc = samsung` in `/chosen`, which is the
    property the vendor node gates itself on (`mmi,status = "/chosen",
    "mmi,nfc", "samsung"`). The bus works -- the camera PMIC answers as `UU` at
    0x35 on the same i2c-0. The GPIOs are right and reachable: driving tlmm 48
    shows up as `pin 48 (GPIO_48): GPIO 500000.pinctrl` in pinmux-pins.

    And with all of that, **0x27 does not ACK**: VEN low then high, FIRM held
    low for normal mode, probed at 1, 3, 6 and 10 seconds after, with both
    `i2cdetect -r` and `i2cget`. Nothing.

    So this is not "enable the node and it appears". The vendor node declares no
    supplies, so whatever else the part needs -- a rail turned on elsewhere, the
    32 kHz clk_req, or simply not ACKing until something speaks NCI to it -- has
    still to be found. Establish that before writing or porting any driver.

12. **Fingerprint, and unlocking the screen with it.** Focaltech, driven on
    Android by a proprietary HAL (`fingerprint.focaltech.default.so`). No
    mainline driver, and the sensor node was not found in the vendor tree's
    sparse checkout, so even the bus and GPIOs still have to be established.

    **The sensor is fitted**, which is worth establishing before anyone decides
    this variant simply lacks it: the bootloader declares `mmi,fps = true` in
    `/chosen`, the same mechanism it uses for the NFC chip. So this is a driver
    problem, not a missing-hardware one.

    Worth being clear that this is two jobs, not one, and the second is the
    part people forget: a working driver gets you an image from the sensor.
    Turning that into "unlock instead of typing the PIN" needs the matching to
    happen somewhere and a PAM path into the lock screen -- `fprintd` plus
    `pam_fprintd`, with Phosh and KWin's lock screens each having to accept it.
    On this port the lock screen is Phosh's numeric keypad or KWin's, and
    neither has been looked at from that angle.

    It is the least tractable item in this file. It is also the one that would
    change daily use the most, which is why it is here.

13. **Monitor mode on the internal WiFi, as a research project.** The table at
    the top of this file calls this infeasible, and for anything short of a
    project it is -- but "infeasible" is not the same as "impossible", and the
    distinction is worth writing down properly.

    It is **not** a gap in mainline that a patch could close. The capability
    comes from the firmware, and ath10k only reads it:

	if (!test_bit(ATH10K_FW_FEATURE_RAW_MODE_SUPPORT, fw_file->fw_features)) {
		ath10k_err(ar, "rawmode = 1 requires support from firmware");
		return -EINVAL;
	}

    The `raw 0` in dmesg is literally that bit being printed. WCN3990's firmware
    says no, so `ath10k_core frame_mode=1 cryptmode=1` does not force anything:
    it fails the probe and leaves you with no wlan0. A monitor interface can
    still be *created*, because cfg80211 allows it, and it captures nothing.

    Two routes, and only one of them is real:

    - **New firmware is not a route.** The WCN3990's firmware is signed, runs on
      a core with no public toolchain or documentation, and is loaded through the
      WLAN protection domain inside the modem (`wlanmdsp.mbn`). Secure boot
      rejects anything unsigned. This is not "hard", it is closed.

    - **Porting the monitor path from `qcacld-3.0`** is the one that could work.
      That is Qualcomm's own driver, it is published source, and it does monitor
      and spectral scan on this exact chip -- which is why LineageOS has both.
      It does not go through mac80211 at all: it sets up a monitor vdev over WMI
      and takes frames off a path ath10k does not implement.

    So the question worth researching is narrow and answerable: **does the
    firmware expose monitor through WMI independently of the RAW_MODE feature
    bit?** ath10k already knows about `WMI_VDEV_TYPE_MONITOR`. If qcacld gets
    frames by asking the firmware for a monitor vdev and a different rx decap
    mode, rather than by the raw-mode path ath10k gates on, then the missing
    piece is a WMI sequence rather than a firmware capability -- and that is
    something that could be prototyped against the existing ath10k_snoc.

    If it turns out the firmware really does gate it on the same bit, the answer
    is no and this can be closed for good.

    None of this is needed for day-to-day use: the external TP-Link does monitor
    and injection today, and `pwnagotchi` is pinned to `wlan1` for that reason.
    This is worth doing to know the answer, not to get the feature.

14. **The LPASS clock warning on every resume.** Each time the phone comes back
    from suspend, twice per resume:

	LPASS_CLK_ID_TX_CORE_NPL_MCLK already unprepared
	WARNING: drivers/clk/clk.c:1048 at clk_core_unprepare+0xb0/0xe8
	WARNING: drivers/clk/clk.c:1188 at clk_core_disable+0x80/0xa0
	Workqueue: pdr_notifier_wq pdr_notifier_work [pdr_interface]

    The audio codec's clock refcount goes negative when the protection-domain
    notifier runs on resume: something unprepares a clock that is already
    unprepared. Nothing visibly breaks today -- audio, WiFi and OTG all survive
    the resume it happens on.

    It is worth fixing anyway, because this port has already been bitten by
    exactly this class of bug. Patch 0059 exists because an unbalanced LPASS
    clock produced an oops *inside* `__clk_register()`, which runs holding the
    global clk `prepare_lock`; the dying worker never released it, every
    `clk_prepare_enable()` in the system blocked for ever, and the display
    could not be turned back on while SSH kept working. A warning about a
    refcount going negative is the polite version of that.

    Start at `pdr_notifier_work` and which of the LPASS macro drivers
    (`snd_soc_lpass_tx_macro` / `va_macro`) drops the reference twice across a
    PD notification.

15. **Get more out of the GPU — done, 650 → 840 MHz (patch 0093).** The table
    stopped at NOM (650 MHz) because patch 0007 held the turbo steps back on
    purpose, until the GPU was known to be stable. It has been for a long time.

    Nothing was needed from the clock driver: `ftbl_gpu_cc_gx_gfx3d_clk_src` in
    `gpucc-sm6375.c` already lists 770, 840 and 900. Only the OPPs were missing,
    with the vendor's RPMH corners translated to the `rpmpd` (SMD RPM) ones this
    SoC actually uses — NOM_L1 → `rpmpd_opp_nom_plus`, TURBO → `rpmpd_opp_turbo`.

    **The ceiling is 840 rather than 900 because the speed bin is a fuse**, and
    it was read rather than guessed. mainline has no qfprom node for sm6375, so
    it came out of `/dev/mem` at the vendor's address — qfprom `0x1b40000` plus
    the `gpu_speed_bin` cell at `0x6015`, byte `0x1b46015`:

	gpu_speed_bin = 177

    which is `qcom,gpu-pwrlevels-2` in the vendor blair.dtsi, topping out at 840.
    900 MHz is listed only for bins 0 and 190, so on this part it would be
    running the GPU past what the silicon was sorted for.

    Two traps found on the way, both of which produce a confident wrong answer:

    - **blair is not holi.** `blair.dtsi` includes `holi-gpu.dtsi` and then does
      `/delete-node/` on `qcom,gpu-pwrlevel-bins` and declares four bins of its
      own. holi's tables end at 875 and never apply to this SoC. Patch 0007's
      comment says its values came from `qcom,gpu-pwrlevels-0`, and the numbers
      do match blair's — but reading holi's file to extend them gives frequencies
      this clock cannot produce.
    - **The thermal map needed nothing.** The cooling-maps from patch 0021 use
      `THERMAL_NO_LIMIT` for both bounds, so they pick up the new states by
      themselves. An earlier version of this entry claimed they were pinned to a
      table ending at 650; they are not.

    Still worth knowing: this rail is **VDDGX, not VDDCX**, which is what patch
    0007 fixed, and getting the corner wrong under-volts the part and hangs it
    under load. The corners in the built DTB were checked by hand against
    `qcom-rpmpd.h` for that reason (650 → 256 NOM, 770 → 320 NOM_PLUS, 840 → 384
    TURBO).

    **Flashed and measured (v116).** `available_frequencies` lists 770 and 840,
    and `trans_stat` shows the GPU genuinely using them — within two minutes of
    boot, on nothing but Plasma's own animations, it had already spent 9.4 s at
    840 and 4.8 s at 770.

    Stability at the new corner, which was the actual risk, holds: pinned at
    840 MHz (`min_freq` = `max_freq`) under load for 60 s, the clock stayed there
    the whole time, the GPU thermal zones sat at 37-42 °C, and dmesg produced no
    fault, hang, timeout or recovery.

    **The speed-up is real, but only once the panel stops being the bottleneck.**
    At 60 Hz it could not be measured at all: ceiling pinned to 650 and then to
    840 gives 59.7 and 59.5 fps, because both sit on the panel's cap. That said
    more about the display than the GPU, and led to patch 0094 and the 120 Hz
    entry below. Repeating the same A/B once the panel runs at 120:

	120 Hz, GPU ceiling 650   fps=109.8  janks=0  worst=16.8ms
	120 Hz, GPU ceiling 840   fps=116.9  janks=0  worst=16.7ms

    So the turbo steps are worth about 6.5% and the difference between missing
    the 120 Hz target and very nearly hitting it. `trans_stat` during the run
    shows where the load went: 35.8 s at 840 MHz against 7.3 s at 266, where at
    60 Hz the GPU had been mostly idle at the bottom of the table.

    The two changes only make sense together. Raising the GPU ceiling with the
    panel at 60 Hz buys nothing measurable, and raising the panel to 120 Hz
    without the ceiling leaves roughly 7 fps on the table.

16. **Runtime refresh-rate switching (48/60/90/120) — done in patch 0097.** Patch 0094 lifted the
    panel from 60 Hz to 120 and that is measured below, but the rate is still
    chosen once, at power on, from a kernel parameter. DRM is handed a single
    mode, so nothing can change it afterwards: no KDE setting, and no dropping
    back to 60 Hz on a low battery, which is exactly what a phone should do and
    what the stock firmware does.

    The panel already supports it. Register `0x2f` selects the timing and the
    vendor's own map is repeated beside every timing node in
    `dsi-panel-mot-csot-nt37701-655-1080x2400-dsc-cmd`:

	08 = 48 Hz   03 = 60 Hz   07 = 90 Hz   02 = 120 Hz   04 = 144 Hz

    and `nt37701_modes[]` already carries the first four. 144 Hz is in the map
    but its timing node is commented out in the vendor tree, so it is unproven.

    What is missing is the switch itself: `get_modes()` would advertise all four
    and the driver would have to send the vendor's timing-switch command when
    the mode changes, which needs the panel to learn the current mode — drm_panel
    has no `mode_set`, so this usually means tracking it from the DSI host side.
    Worth doing: 120 Hz costs power, and right now it cannot be given back.

    Two things to keep in mind while doing it. The `DSI PLL(0) lock failed`
    messages at boot are **not** related — there are exactly eight of them at
    both 60 and 120 Hz, so they predate all of this. And 120 Hz doubles the
    pixel clock, 168432 → 336864 kHz, on a link this port already runs at the
    theoretical minimum (`clk_scale=100`), so any further rate has to be
    measured rather than assumed to fit.

    **Done.** Patch 0097 advertises all four and takes the register value from
    the mode in use, read out of the CRTC state because `drm_panel` has no
    `mode_set`. KDE lists them (`kscreen-doctor -o` shows
    `120.00*! 90.00 60.00 48.00`) and each was driven from there and measured:

	pedido  48 Hz | kernel 48  | fps=47.1  janks=0  dsi_err=0
	pedido  60 Hz | kernel 60  | fps=58.9  janks=0  dsi_err=0
	pedido  90 Hz | kernel 90  | fps=88.1  janks=0  dsi_err=0
	pedido 120 Hz | kernel 120 | fps=108.2 janks=0  dsi_err=0

    The frame rate follows the request, which is the part worth measuring: that
    the panel is really running at the rate rather than DRM merely claiming it.
    No underruns or DSI errors at any of them, 48 Hz included, which was the one
    furthest from anything previously tested.

    **Seamless switching is left, and deliberately left.** Every switch is a
    full modeset, so the display blanks once. That was judged not worth chasing
    while the feature works: one black flash when you change a setting is not a
    problem anybody has. The vendor sends a timing-switch command and never
    drops the link, which would mean tracking the transition instead of
    re-running the power on sequence. Worth doing if something ever switches
    rate automatically -- dropping to 60 Hz on a low battery would blank the
    screen every time it happened, and that *would* be a problem.

17. **The fuel gauge says `Full` at 100% even on battery — fixed in patch 0096,
    verification pending.** This is the one that quietly cost a day of testing: with
    the charger unplugged and the gauge at 100%, `status` reads `Full` instead
    of `Discharging`, UPower reports `on-battery: no`, and PowerDevil applies the
    plugged-in policy -- so a locked screen never leads to a suspend. Every
    attempt to reproduce the locked-screen behaviour at 100% measured nothing at
    all, and the run that finally worked only did because the battery had drifted
    down to 95%.

    The cause is three lines in `cw2217_get_status()`, in patch 0012:

	if (cw->soc >= 100)
		return POWER_SUPPLY_STATUS_FULL;

	cur = CW2217_CURRENT_TO_UA(cw->current_raw, cw->shunt_mohm);
	...
	return cur > 0 ? POWER_SUPPLY_STATUS_CHARGING :
			 POWER_SUPPLY_STATUS_DISCHARGING;

    The state of charge is checked before the current is looked at, so a battery
    at 100% reports `Full` whether or not it is being drained. `FULL` is supposed
    to mean the battery is full *and* not discharging; a pack sitting at 100%
    with current leaving it is discharging, and it is the only thing userspace
    has to go on.

    The fix is to read the current first and let a discharge win over the
    state of charge, keeping `Full` for 100% with no current leaving:

	cur = ...;
	if (cur < -CW2217_CURRENT_NOISE_UA)
		return POWER_SUPPLY_STATUS_DISCHARGING;
	if (cw->soc >= 100)
		return POWER_SUPPLY_STATUS_FULL;

    Note the sign convention in this driver is that positive current is charging.
    The `!cw->shunt_mohm` path has to keep returning something sensible, since
    without a shunt value the current cannot be read at all -- that is the one
    case where falling back to the state of charge is right.

    Worth fixing before any further power measurement, not only for the
    suspend policy: anything reasoning about "is it on battery" is wrong at 100%,
    which includes the measurement in item 6.

    **Patch 0096 does exactly that**, and since `CONFIG_BATTERY_CW2217=m` it went
    onto the phone as a module over ssh with no reflash -- same vermagic,
    `rmmod`/`modprobe`, and the charging case is unchanged (`Charging` at 55%
    with positive current). **The case it was written for has not been observed
    yet**, because that needs the pack at 100% with the charger out and the
    battery was at 55%. Until someone sees `Discharging` at 100% this is a fix
    by inspection, not a fix by measurement. `rhodep-batwatch` logs capacity,
    status, current and charger presence to `/root/batwatch.log` so the moment
    is captured rather than waited for.

# License
Kernel patches: GPL-2.0. Packaging/glue: MIT. Vendor firmware blobs: proprietary,
not included (extract from your own device). See `LICENSE`.

# Credits
Community port. Built on the postmarketOS mainline SM6375 work and the Kali
NetHunter Pro (debos) build system.
