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
- **Bluetooth audio** (A2DP), with the controller's real address so pairings
  survive a reboot — `userspace/bluetooth/`.
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
| **GPS** | **Starting a GNSS session watchdog-resets the SoC in under a second**, reproducibly, with `ipa.ko` not loaded. The location service (QMI 16) is there and answers every query — mode, NMEA types, XTRA servers, start, stop — but the moment a session runs with indications registered, the phone reboots. It was never an indoors problem. [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md) |
| **Camera** | **Does not capture** — no photos, no video, no viewfinder. Only groundwork is done: the FAN53870 camera PMIC driver is written and running (all 7 LDOs registered, voltages verified against the chip's registers), and the rest is feasible because the ISP pipeline (`csid530` + `tfe530` + `tpg101`) is the *same silicon* mainline already drives on qcm2290, the 52 `gcc_camss_*` clocks are in mainline, and the 50 MP main sensor (Samsung S5KJN1) has a mainline driver. Still needed before any image: a `camss` entry for SM6375, the CSIPHY/CSID/TFE nodes and the sensor node. [`CAMERA-SENSORS-FEASIBILITY.md`](docs/CAMERA-SENSORS-FEASIBILITY.md) |
| **Monitor mode on the internal WiFi** | Infeasible: the WCN3990 firmware reports `raw 0`. Use the external adapter, which does work. |

## Where to pick this up

1. **The SoC reset, with GNSS as the trigger.** A live GNSS session kills the
   SoC in **under 100 ms**, with no SIM, no IPA, no ModemManager, no network and
   the WWAN radio switched off — `sudo scripts/rhodep-gnss-test.py 40 min` and
   the phone reboots, every time. The LTE reset needs `ipa.ko` plus an LTE
   attach plus 3 to 10 minutes of waiting, so if they share a cause the
   expensive bug now has an instant reproducer.

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
- **Mobile data**: the modem itself is finished. It registers on LTE, data was
  measured at ~24 Mbit/s, calls connect and SMS arrives. What blocks day-to-day
  use is a separate bug reachable only now that the radio works: with `ipa.ko`
  loaded **and** the modem attached to LTE, the SoC watchdog-resets every 3 to
  10 minutes, silently. Either condition alone is stable (22 and 16.5 minutes
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
- **GPS is worse than "not done": using it reboots the phone.** The location
  service answers every query, but a GNSS session with indications registered
  watchdog-resets the SoC in under a second, `ipa.ko` or no `ipa.ko`. Same
  silent signature as the mobile-data reset, which is why it is now the best
  lead on that bug rather than a feature request.
  [`GNSS-SM6375.md`](docs/interconnect-sm6375-wip/GNSS-SM6375.md)
- Single USB-C port + no mainline Type-C driver → charge vs OTG is not automatic
  (manual `otg on|off`, defaults to charging). See `packages/rhodep-usb-otg`.

---

# Repository layout
```
kernel/
  patches/            49 kernel patches (DTS + shared-driver fixes), applied in order
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
  usb-net/            SSH over the USB cable (172.16.42.1), the only link that
                      survives a modem restart (install.sh)
  bluetooth/          unmasks bluetooth.service and programs the real
                      controller address from androidboot.btmacaddr, instead
                      of the random one the stack invents (install.sh)
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
docs/                       extra notes
```

---

# The kernel (shared with the pmOS port)

## The 49 patches (`kernel/patches/`, applied in this order)

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
```

**The numbering has gaps and they are not omissions.** 0029-0031 and 0037-0041
were interconnect and audio diagnostics that were dropped, 0050 and 0058 were
never used, and the ones worth reading survive under
`docs/interconnect-sm6375-wip/` (see
[`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md)). Every
`.patch` file that *is* in `kernel/patches/` is in the build — all 49 of them,
and both aports list the same 49.

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

**The 49 patches under `kernel/patches/` and `postmarketos/linux-motorola-rhodep/`
are identical**, same filenames and same `source=` order — the only difference
between the two trees is the `.config`.
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

1. **Reapply the out-of-tree patches / config.** The 49 patches under
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

   **The same applies to `snd-q6dsp-common.ko`**, registered as the
   `kernel-suspendfix` component. That one is a file *inside* the held
   `linux-image-<KVER>` package rather than a DKMS build, so the immutable bit
   is doing something slightly different: it is what stops
   `apt-get install --reinstall linux-image-<KVER>` from silently putting the
   unpatched module back and taking suspend/resume down with it. A hold does
   not stop a reinstall. Release it before any deliberate kernel package
   operation, and re-register afterwards:
   ```sh
   rhodep-protect-files release /usr/lib/modules/<KVER>/kernel/sound/soc/qcom/qdsp6/snd-q6dsp-common.ko
   # ... install the new kernel package ...
   rhodep-protect-files register kernel-suspendfix 0644 /usr/lib/modules/<KVER>/kernel/sound/soc/qcom/qdsp6/snd-q6dsp-common.ko
   ```
   The durable answer is to stop carrying it as a loose file at all: rebuild
   the `linux-image` .deb from an apk that already has patch 0059 in `source=`
   (see "Turning the kernel apk into a Debian `linux-image` .deb"), and the
   packaged module *is* the fixed one. Until then, `dpkg -V linux-image-<KVER>`
   reporting `??5??????` on that path is expected and is the fix, not damage.
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
modified**. It is up to **27 components** — `apt`, `apt-conf`, `modem`,
`modem-conf`, `audio`, `audio-conf`, `bluetooth`, `bluetooth-conf`,
`usb-net-conf`, the sensor ones (`sensors`, `sensors-lib`, `sensors-conf`,
`adsp-suspend`), `kernel-suspendfix`, `gpu-opencl`, `rtl8821au`, `cleanup` and
the `extra-tools` ones (`keyboard`, `keyboard-conf`, `keyboard-layouts`,
`clipboard`, `claude-free`, `claude-free-conf`, `claude-local`). The list grows
as installers are added, so read it from the device rather than from here:

	rhodep-protect-files status

**One of them is not like the others.** Every component above protects a file
that belongs to no package, which is why nothing would ever put it back.
`kernel-suspendfix` is the exception: `snd-q6dsp-common.ko` *is* owned by
`linux-image-7.2.0-rc5`, and the copy in the package is the unpatched one. The
hold stops an upgrade and dpkg's `Protected` stops a removal, but neither stops
`apt-get install --reinstall`, which would put the broken module back and take
suspend/resume with it, silently. The immutable bit turns that into a loud
failure instead. See "Updating the kernel" for how to release it on purpose.

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

- **`bluez`** owns `btmgmt`, which `rhodep-bt-address` uses to program the
  controller's real address at every boot; without it the phone invents a
  random one per boot and every pairing dies. It is also the one hold here most
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

One note for whoever measures this again. `deep` suspend works, but it
occasionally aborts in ~0.6 s and systemd falls back to `s2idle`, which also
sleeps properly (119.6 s against a 120 s RTC alarm); and after a successful
`deep` sleep systemd tries `s2idle` once more anyway and it returns in
milliseconds. Both are noise in the log rather than failures — see the open-work
list.
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
   - **Keeping the codec awake should not be necessary.** Jack detection relies
     on a udev rule pinning the soundwire slave on, because MBHC detects nothing
     while it is suspended. The real fix is for the codec to keep that block
     alive across runtime suspend, which is a kernel change and would also stop
     the port from burning power to hold the slave up.

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
8. **The ADSP's handover interrupt storm**, which is cheap to fix and pays for
   itself immediately in debuggability. From ~30 ms after the ADSP boots, on
   every boot, forever:

	qcom_q6v5_pas a400000.remoteproc: Handover signaled, but it already happened

   at **4.2 times a second** — 26 355 of them in one 1h44 boot. `smp2p-adsp`
   IRQ 16 and the `q6v5 ready` / `q6v5 handover` bits fire together each time,
   so the ADSP is re-signalling both, without ever crashing (`q6v5 fatal` stays
   at 0). Two costs: it wraps the kernel ring buffer every six minutes, so
   `dmesg` is useless for anything older than that and every early-boot message
   is gone; and the ADSP is a wakeup source, which is a plausible reason `deep`
   suspend aborts and the port falls back to `s2idle`. Start at
   `q6v5_handover_interrupt()` in `drivers/remoteproc/qcom_q6v5.c` and work out
   why the remote keeps re-asserting; even a `dev_err_ratelimited()` would give
   `dmesg` back while the real cause is found.
9. **`deep` suspend sometimes aborts, and systemd always tries `s2idle`
   afterwards.** Both halves are cosmetic as far as anyone can tell, but they
   are confusing to read in a log, so: `deep` **does** work on this device — a
   real overnight-style idle slept 1229.7 s (20.5 min) in `deep` and woke on
   the power button, with the display, the sound card and the sensors all
   intact. Sometimes it instead returns in ~0.6 s right after the freeze,
   which is what a pending wakeup at `pm_wakeup_pending()` looks like, and
   systemd falls back to `freeze`, which also sleeps properly (119.6 s against
   a 120 s RTC alarm). And even after a *successful* `deep` sleep, systemd
   still tries `s2idle` once and it returns in ~3 ms, which means the write to
   `/sys/power/state` reports an error even when the sleep worked. None of this
   has been chased down; the ADSP interrupt storm in item 8 is the obvious
   suspect for the spurious aborts, since the ADSP is a wakeup source.

# License
Kernel patches: GPL-2.0. Packaging/glue: MIT. Vendor firmware blobs: proprietary,
not included (extract from your own device). See `LICENSE`.

# Credits
Community port. Built on the postmarketOS mainline SM6375 work and the Kali
NetHunter Pro (debos) build system.
