# Building a flashable image from a clean clone

This is the end-to-end map: what the repo gives you, what it does **not** (and
where to get it), and the order of steps. Read it before starting so you know
which parts are automated and which are manual.

The `README.md` at the repo root has the detailed commands for each phase; this
document is the checklist and the honest list of external dependencies.

---

## What a clean clone can and cannot do

A clean clone **cannot** produce a flashable image on its own. It has all the
source (kernel patches, configs, aport, `.deb` sources, userspace config,
scripts, debos device config) but it deliberately does **not** contain
proprietary blobs, base images, or the external rootfs recipe. You have to
supply those. None of this is a bug — it is what cannot legally or practically
be committed — but it means "clone and run one script" is not the workflow.

---

## External material you must supply

Nothing below is in the repo. Gather it first.

| # | What | Where it comes from | Needed for |
|---|------|---------------------|------------|
| 1 | **Vendor firmware** (WCN3990 `board-2.bin`/`firmware-5.bin`, QCA BT, Adreno `a6xx`/`a619_gmu`/`a615_zap`, WLAN `wlanmdsp.mbn` + `.jsn`) | Stock ROM. Exact list in `packages/firmware-motorola-rhodep/FIRMWARE-FILE-LIST.txt` | WiFi, BT, GPU, modem/WLAN PD |
| 2 | **`ipa_fws.mdt` + `.b00..b04`** | Extracted from the `modem_a` partition (see HANDOFF-SESSION4 §1) | Mobile data (IPA/GSI firmware) |
| 3 | **`aw88261_acf.bin`** (audio tuning blob) | `scripts/extract-aw88261-acf.sh`, run on the phone as root; extracts it from the `vendor` logical partition inside `super` | Speaker audio (the AW88261 driver will not probe without it) |
| 4 | **`v47_ramdisk`** (pmOS initramfs, ~13 MB) | A working pmOS boot.img for this device; unpack its ramdisk. `boot-artifacts/` has the matching `v47_cmdline.txt` but NOT the ramdisk | Building *any* boot.img (`mkbootv2b.py` needs it) |
| 5 | **A base pmOS `boot.img`** | The postmarketOS rhodep port (e.g. `boot-v45-DOCKER.img`) | Rescue image, and as a source of the initramfs |
| 6 | **The debos rootfs recipe** | `git clone` the `kali-nethunter-pro` repo (GitLab). `debos/wip.toml` here is only the device config that gets copied into it | Building the Kali rootfs |
| 7 | **`realtek-rtl8188eus-dkms`** | Kali repos; then patch it with `packages/rhodep-rtl8188eus-fix/apply-rtl8188eus-fix.sh` so it builds on 7.2 | External USB WiFi monitor/injection (wlan1) |
| 8 | **Qualcomm's FastRPC userspace** | `git clone` [`quic/fastrpc`](https://github.com/quic/fastrpc) (BSD-3); `userspace/sensors/build-fastrpc.sh` does the clone, the patch and the build | Sensors: it is the FastRPC listener the ADSP's sensor core reads its registry through |
| 9 | **The SSC sensor registry** (`/vendor/etc/sensors/`, `/persist/sensors/`) | The device's own stock `vendor` (inside `super`) and `persist` partitions; `rhodep-ssc-populate` copies them at first boot | Sensors: no registry, no sensor drivers, and the ADSP asserts |

---

## Phase 0 — prerequisites

- A pmbootstrap environment with the aport in place at
  `device/testing/linux-motorola-rhodep/` (copy it from `postmarketos/`).
- Go + debos for the rootfs (`debos/binwrap/` has container-friendly wrappers).
- The kernel source tree is fetched by the APKBUILD; you do not supply it.

---

## Phase 1 — the kernel (pmbootstrap)

The kernel is a postmarketOS package. `postmarketos/linux-motorola-rhodep/` is
the aport; `postmarketos/README.md` has the exact steps.

```
pmbootstrap checksum linux-motorola-rhodep      # after ANY patch/config change
pmbootstrap build --force linux-motorola-rhodep
# -> ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk
```

- **pmOS kernel**: use the aport as-is.
- **Kali kernel**: swap in `kernel/config/config-motorola-rhodep.aarch64` before
  building. The 40 patches are identical either way; only the config differs.

**Verify the apk before trusting it:**
- The audio stream ID is present in the decompiled `sm6375-motorola-rhodep.dtb`:
  `dtc -I dtb -O dts ... | grep 'iommus = <0x[0-9a-f]* 0xa1 0x00>'`. Without it
  the SoC resets on playback.

  Check the **stream ID `0xa1`**, not the phandle. This used to be written as
  `iommus = <0x3a 0xa1 0x00>` and that check now fails on a perfectly good
  build: adding a node renumbers phandles, and patch 0082's `tcsr_regs` pushed
  the SMMU from 0x3a to 0x3b. The stream ID is the part that means something.
- IPA node has `qcom,gsi-loader = "self"`.
- `snd-soc-aw88261.ko`, `q6asm-dai.ko`, the LPASS macros are present.
- **No `.ko.zst`** anywhere (compressed modules hang this device at boot).
- For the Kali build: `mac80211.ko`, `rtl8xxxu.ko`, `ath9k_htc.ko` present.

---

## Phase 2 — support packages and firmware

```
sh scripts/build-support-debs.sh      # builds the 3 rhodep-* .deb (not firmware)
```

Then build `firmware-motorola-rhodep` after dropping the vendor blobs (item 1)
into `packages/firmware-motorola-rhodep/lib/firmware/` per its README.

Kernel headers for DKMS (rtl8188eus): `scripts/build-kernel-headers.sh` (needs
`KSRC` = a patched kernel tree and `CONFIG`).

---

## Phase 3 — the rootfs (debos)

Clone the external `kali-nethunter-pro` recipe (item 6), copy `debos/wip.toml`
into its device configs, and drop the built `.deb` (kernel + firmware +
support) into its packages dir. Then run debos.

**Known manual step:** `image.yaml` cannot partition inside a container
(`max_part=0`). The rootfs tar builds fine; you finish the disk by hand — see
README "Finish the rootfs by hand" and "The userdata disk image (sector-4096
GPT)".

---

## Phase 4 — boot image

```
python3 scripts/mkbootv2b.py vmlinuz DTB initramfs "<cmdline>" kali-boot.img
```

or, reusing an initramfs from an existing boot.img:

```
sh scripts/make-boot-from-apk.sh old-boot.img new-boot.img
```

The initramfs is the pmOS one (item 4/5); it implements the sector-4096
subpartition mount and `pmos.debug-shell`. The cmdline carries
`pmos_root_uuid=<UUID of the Kali root ext4>`.

---

## Phase 5 — install (dd, not fastboot)

The Motorola bootloader refuses `fastboot flash userdata`, so `userdata` is
written by `dd` from a rescue boot. Full steps in README "Installing on the
device": build a rescue image with `scripts/build-rescue-boot.sh`, flash it,
`telnet 172.16.42.1 23`, `dd` the disk to `userdata`, then flash the real
`kali-boot.img`.

---

## Phase 6 — bake into the image, or do on-device after first boot

All three of these run fine inside the build chroot, and doing them there is
preferable: the image then boots with audio working and protected, instead of
needing a session over the network to finish the job. `install.sh` detects that
systemd is not running and installs files only; `systemctl enable` is just
symlinking, so the units still come up on first boot.

```
sudo cp -r userspace/audio       /tmp/rootfs/srv/audio
sudo chroot /tmp/rootfs sh /srv/audio/install.sh
sudo cp -r userspace/plasma-apps /tmp/rootfs/srv/plasma-apps
sudo chroot /tmp/rootfs sh /srv/plasma-apps/install.sh
sudo cp -r userspace/usb-net     /tmp/rootfs/srv/usb-net
sudo chroot /tmp/rootfs sh /srv/usb-net/install.sh
sudo cp -r userspace/apt     /tmp/rootfs/srv/apt
sudo chroot /tmp/rootfs sh /srv/apt/apply-holds.sh
sudo cp -r userspace/login   /tmp/rootfs/srv/login
sudo chroot /tmp/rootfs sh /srv/login/install.sh kali
sudo cp -r userspace/power   /tmp/rootfs/srv/power
sudo chroot /tmp/rootfs sh /srv/power/install.sh
# external USB Wi-Fi drivers: stage the driver packages the orchestrator needs,
# then run it. In the chroot it only stages sources + arms a first-boot service
# (DKMS must build against the booted kernel, not the build host's). Runs after
# apply-holds.sh so rhodep-protect-files exists and it can make its files
# immutable; the linux-headers/linux-image/rtl8188eus holds are already in
# apt-holds.txt and were applied above.
sudo mkdir -p /tmp/rootfs/srv/packages
sudo cp -r packages/rhodep-rtl8188eus-fix packages/rhodep-rtl8821au \
           /tmp/rootfs/srv/packages/
sudo cp -r userspace/wifi-drivers /tmp/rootfs/srv/wifi-drivers
sudo chroot /tmp/rootfs sh /srv/wifi-drivers/install.sh
```

- **Audio userspace** (`userspace/audio/install.sh`): UCM for speaker, earpiece,
  headphones and the microphone, the PipeWire and WirePlumber rules, the boot
  route unit, the udev rule that keeps the codec awake so the headset jack can
  be detected at all, and the jack watcher that switches output on insertion.
- **SSH over USB** (`userspace/usb-net/install.sh`): gives `usb0` the address
  172.16.42.1 and a DHCP server, without advertising itself as router or DNS.
  Worth baking in: it is the only link that survives a modem restart, since the
  WLAN protection domain lives inside the modem, so it is what makes modem
  debugging possible at all.
- **apt holds** (`userspace/apt/apply-holds.sh`, 51 packages): keeps an upgrade
  from replacing the kernel, the WiFi firmware, the modem stack, the audio stack
  or the on-screen keyboard. It also installs `rhodep-apt-guard`, an apt hook
  that refuses to remove a held package, because a hold does **not** stop
  `apt purge`; and `rhodep-holds-enforce`, which puts back any hold or guard
  file that goes missing, because nothing protected the holds themselves. The
  guard's files are made immutable, `rhodep-dpkg-protect` sets dpkg's own
  `Protected` field so that `dpkg -r`/`dpkg -P` are refused as well (dpkg
  ignores apt holds entirely), and releasing a hold on purpose goes through
  `rhodep-hold-override`, which wants a reason and refuses to run unattended. See README "A hold does not stop `apt purge`".

  **Run it last, and note it does three things beyond the holds.** It registers
  the port's own `.deb`s (`rhodep-battery-jeita`, `rhodep-modem-support`,
  `rhodep-usb-otg`) with `rhodep-protect-files` as well, because those exist
  only in `/var/lib/dpkg/status` — apt cannot re-fetch them, so a hold does
  nothing against `rm` of one of their files and there is nowhere to restore
  from. It registers the *enabled state* of the units that matter, which the
  immutable bit cannot cover. And it protects every file the installers above
  laid down.
- **Modem** (`userspace/modem/install.sh`): the whole reason the radio works.
  Populates the modem's remote file system from the stock `modem`/`fsg`/
  `persist` partitions at first boot (without the Hexagon objects under
  `modem_pr/so/` the modem never leaves DMS operating mode 'offline'), opens
  the UIM provisioning session before ModemManager, installs a `tqftpserv`
  patched to serve `/readonly/vendor/fsg/`, answers QMI service 52 (memshare),
  and drops `/etc/modprobe.d/rhodep-ipa-hold.conf` (plus
  `rhodep-icc-hold.conf` if the image carries the interconnect provider nodes,
  as v97 does). Needs `libqrtr-dev`,
  `libzstd-dev` and a compiler; it works in the chroot, where it only lays the
  files down. **Read `userspace/modem/README.md` before shipping an image**:
  the ipa-hold file is deliberate, it is what keeps the phone from
  watchdog-resetting, and it is also what costs mobile data and ModemManager.
- **Bluetooth** (`userspace/bluetooth/install.sh`): unmasks `bluetooth.service`,
  which was found symlinked to `/dev/null`, drops the `--noplugin` restriction
  that had Bluetooth audio and input turned off, and programs the controller's
  real address from `androidboot.btmacaddr` before `bluetoothd` starts. Without
  that last part the stack invents a new random address every boot and no
  pairing survives a reboot. A2DP verified with real earbuds.
- **Power** (`userspace/power/install.sh`): selects s2idle, because `deep`
  suspend returns after two seconds on this SoC and the phone then enters and
  leaves suspend continuously with the screen off, re-enumerating USB each time.
  Also installs the hold that keeps a running job alive across a screen lock,
  and the NetworkManager drop-in that asks the radio to stay wake-capable. This
  directory had no `install.sh` at all until now, so images built before this
  note have none of it.

  It also installs the pair that keeps the WiFi alive across a suspend:
  `/usr/lib/systemd/system-sleep/rhodep-wowlan`, which disarms WoWLAN for the
  instant NetworkManager checks it on resume so NM does not tear the link down
  and rebuild it, and `rhodep-wowlan-check.timer`, which re-arms WoWLAN if it is
  ever found off with `wlan0` associated. The timer is not optional decoration:
  both earlier versions of the hook failed in ways that left WoWLAN disarmed for
  good, and a phone in that state cannot be woken over the network at all. All
  four files are registered with `rhodep-protect-files`, so a stray `apt` or an
  editor cannot quietly undo them.

- **Powersupply app** (`userspace/powersupply/install.sh`): makes the
  Powersupply app read the real battery. It groups the supplies by `type`, sorts
  each group alphabetically and takes `[0]`, so `bq256xx-battery` beat
  `cw2217-battery` and every field showed N/A. That first one is the charger's
  own empty view of a battery and patch 0023 marks it `scope=Device` for exactly
  this reason, which is also why UPower already ignores it. The fix skips
  `scope=Device` rather than naming the gauge, so it survives a hardware change
  and is worth sending upstream. `powersupply-gtk` is held and the file is
  registered with `rhodep-protect-files`.

- **Login screen** (`userspace/login/install.sh`): required as soon as anything
  pulls in `gdm3` (several Kali metapackages do), because `phosh.service` and a
  display manager both claim the display at boot. See README "Login screen
  (GDM)".

- **External USB Wi-Fi drivers** (`userspace/wifi-drivers/install.sh`): the
  rtw88 (Archer T2U Nano) and rtl8188eus (TL-WN722N) drivers that give monitor
  mode + injection on `wlan1`. Unlike the rest of Phase 6, this one **cannot be
  fully baked in**: the drivers are DKMS modules and a `.ko` must match the
  kernel that runs it, which is not the build host's kernel. In the chroot the
  installer only stages the driver sources and arms
  `rhodep-wifi-drivers-firstboot.service`, which compiles both against the
  booted kernel on the device's first boot (it needs the network once, and the
  matching `linux-headers-<KVER>` — stage that `.deb` alongside for an offline
  build). See README "Building out-of-tree drivers" and the directory's README.

- **Sensors** (`userspace/sensors/install.sh`): the SSC sensors -- accelerometer,
  gyroscope, magnetometer, proximity, ambient light, and therefore screen
  auto-rotation. Needs `build-fastrpc.sh --install` first, which clones and
  builds Qualcomm's FastRPC userspace (`quic/fastrpc`, BSD-3) against the
  mainline driver; that step wants network and a compiler, so run it in the
  build chroot before `install.sh` or on the device afterwards. The registry
  itself is copied out of the stock `vendor` and `persist` partitions at first
  boot by `rhodep-ssc-populate`, exactly like the modem's RFS tree, so it cannot
  be baked into an image. **Read `userspace/sensors/README.md` first**: with the
  listener running and the registry missing, the sensors PD asserts and restarts
  the whole ADSP, which takes audio with it.

The one step that **cannot** be baked in, and still has to happen on the device:

- Extract the AW88261 blob: `scripts/extract-aw88261-acf.sh` (item 3). It is not
  redistributable, it lives in the `vendor` logical partition inside `super`,
  and without it the amplifiers do not probe and there is no sound at all.

---

## Current state (what actually works)

See `README.md` "What works / Known limitations" for the authoritative list,
and `docs/interconnect-sm6375-wip/` for the deep history:

- **AUDIO-SM6375.md** — audio bring-up, sessions 1-12 (SOLVED at session 10-12).
- **HANDOFF-SESSION4.md** — IPA/mobile-data root cause, ramoops, modem, and the
  build/flash/where-everything-lives reference.

Short version: display, GPU, touch, WiFi/BT, battery/charging, USB/OTG, the
three remoteprocs, **audio** and **sensors** all work. Audio means speaker,
earpiece, headphones with working jack detection, and the microphone, through
PipeWire. Sensors means accelerometer, gyroscope, magnetometer, proximity and
ambient light through iio-sensor-proxy, so the screen auto-rotates.

**The modem works too** — it registers on LTE, data was measured at ~24 Mbit/s,
calls connect and SMS arrives — but it is **shipped switched off**. With
`ipa.ko` loaded *and* the modem attached to LTE the SoC watchdog-resets every 3
to 10 minutes, so `userspace/modem/install.sh` drops
`/etc/modprobe.d/rhodep-ipa-hold.conf`. The cost is mobile data and
ModemManager, which means no dialer, no SMS UI and no `mmcli` on a stock
install. That is deliberate; see HANDOFF-SESSION4.md §5 sessions 14-15.

**Call audio** is not blocked on the modem: mainline has no q6voice
(MVM/CVS/CVP) at all, so it is a driver that has to be written. One rough edge
remains in audio itself: changing output while a stream is already playing works
towards the headphones but leaves the speaker silent until that stream is
reopened (AUDIO-SM6375.md §6).
