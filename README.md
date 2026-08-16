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

> **The modem works.** It registers on LTE, mobile data reached ~24 Mbit/s,
> calls connect and SMS arrives. The cause of two sessions of "the modem never
> leaves offline" turned out to be files, not silicon: the modem fetches
> run-time images from the AP over TFTP and every request was being answered
> "file not found". Install with `userspace/modem/install.sh` and read
> [`userspace/modem/README.md`](userspace/modem/README.md).
>
> **But it is shipped switched off**, because of a separate, newly exposed bug:
> with `ipa.ko` loaded *and* the modem registered, the SoC watchdog-resets every
> few minutes. So `install.sh` also drops
> `/etc/modprobe.d/rhodep-ipa-hold.conf`, which costs mobile data and
> ModemManager but keeps the phone stable. Deleting that one file and rebooting
> gives the whole modem back. Bisection and leads: HANDOFF-SESSION4.md session
> 14, "OPEN BUG". No kernel or device tree change is needed for any of the modem
> work — `ipa.ko` is a module and the two IPA patches (0042, 0043) ship in it —
> so `kali-boot-v94-STABLE.img` is still the image to flash.

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
- **Display** (Novatek NT37701 AMOLED 1080x2400, DSI + DSC command mode)
- **Internal WiFi + Bluetooth** (WCN3990)
- **Modem / ADSP / CDSP** remoteprocs
- **GPU** Adreno 619 (accelerated), touchscreen (Goodix GT9916S), buttons
- **Battery** (CellWise CW2217 gauge) + **charging** (SGM41542) with full thermal
  protection (kernel hot-side + userspace cold-side JEITA)
- **USB host / OTG** with VBUS from the SGM41542 charger → **external USB WiFi
  adapters** (tested: TP-Link TL-WN722N v2/v3 / RTL8188EUS → `wlan1` with
  **monitor mode + packet injection**, which the internal WCN3990 cannot do).
  Injection uses the `rtl8188eus` (`8188eu`) DKMS driver, not the generic
  `rtl8xxxu` — see `packages/rhodep-rtl8188eus-fix`.
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
- **IPA** (the data path for mobile data) is up: the register layout the driver
  needed is in the DTS and the microcode loads from the stock `modem_a`

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

## Known limitations
- **Mobile data**: the IPA blocker described in older revisions of this file is
  solved and the modem now enumerates on its own, but it does not get as far as
  registering on the network: ModemManager reports `DeviceNotReady`, and a
  known-good SIM gets no ATR while an older prepaid SIM reads fine. That is a
  SIM/UIM problem downstream of everything else and is still being looked at.
- **Internal WiFi monitor/injection**: impossible in mainline — the WCN3990
  firmware reports `raw 0` (no raw mode). Use an external USB adapter instead.
- **Audio**: speaker, earpiece, headphones and both microphones work. What is
  left is call audio, which needs the modem first, and one rough edge: changing
  output **while a stream is already playing** works towards the headphones but
  leaves the speaker silent until that stream is reopened. The cause is an ASoC
  ordering problem, not a configuration one — the amplifiers are powered up
  ~300 ms before the I2S clock they need to lock onto — and it is written up in
  [`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md) §6.
  Jack detection needs the codec kept awake (a udev rule), because a suspended
  soundwire slave reports nothing.
- **Only one microphone is real.** AMIC3 works. AMIC1, AMIC2, the four VA-macro
  DMICs and the TX-macro DMICs all return digital silence, so the device tree
  describes inputs this board does not have and those nodes can be dropped.
  Note when testing: enabling a capture path emits a settling DC transient, a
  one-sided ramp, before going quiet, so peak and RMS look like signal on a dead
  input. Count sign changes instead.
- Sensors (SSC/ADSP), GPS, NFC (Samsung `sec-nfc`, no mainline driver),
  camera: not done.
- Single USB-C port + no mainline Type-C driver → charge vs OTG is not automatic
  (manual `otg on|off`, defaults to charging). See `packages/rhodep-usb-otg`.

---

# Repository layout
```
kernel/
  patches/            31 kernel patches (DTS + shared-driver fixes), applied in order
  config/
    config-motorola-rhodep.aarch64   full kernel .config (Kali variant = pmOS config + NetHunter + BTF fix)
    nethunter-config.fragment        the NetHunter symbols merged onto the pmOS config
  APKBUILD            pmaports APKBUILD used by pmbootstrap to build the kernel
postmarketos/         FULL postmarketOS port source (the base this Kali port builds on)
  linux-motorola-rhodep/     kernel aport (31 patches + APKBUILD + pmOS config, no NetHunter)
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
  firmware-motorola-rhodep/ .deb template (blobs NOT included, see its README)
userspace/
  usb-net/            SSH over the USB cable (172.16.42.1), the only link that
                      survives a modem restart (install.sh)
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
  apt/                the 41 apt holds this port depends on, the hook that
                      refuses to purge them, and the enforcer that puts a hold
                      back if anything removes it (apply-holds.sh)
  login/              GDM login screen instead of the phosh.service autologin (install.sh)
scripts/
  mkbootv2b.py             build an Android boot.img v2 (flat Image + appended DTB)
  make-boot-from-apk.sh    build a boot.img reusing an initramfs from a base image
  build-support-debs.sh    build the three support .deb packages
  build-kernel-headers.sh  build linux-headers-<KVER>.deb (for DKMS / rtl8188eus)
docs/                       extra notes
```

---

# The kernel (shared with the pmOS port)

## The 31 patches (`kernel/patches/`, applied in this order)
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
```

Patches 0027-0031 and 0037-0041 were interconnect and audio diagnostics and are
not part of the build; the ones worth keeping are described in
[`AUDIO-SM6375.md`](docs/interconnect-sm6375-wip/AUDIO-SM6375.md).

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

**The 31 patches under `kernel/patches/` and `postmarketos/linux-motorola-rhodep/`
are identical** — the only difference between the two trees is the `.config`.
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
for the full TP-Link RTL8188EUS monitor+injection workflow.

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

Verified by attacking it on the device, not by reading it:

| attack | result |
| --- | --- |
| `apt-mark unhold rmtfs` | ALERT logged, hold back on the next enforce |
| `apt-mark unhold $(apt-mark showhold)` — all 41 | all 41 back |
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

All 41 carry it. The deliberate way past is dpkg's own
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

Two traps when checking this by hand. `apt -s` does **not** run dpkg hooks, so a
simulated purge proves nothing about the guard. And a held package shows as
`hi`, not `ii`, in `dpkg -l`, with dpkg status `hold ok installed` rather than
`install ok installed` — so a survival check written as `grep '^ii'` or
`grep 'install ok installed'` reports the package as gone when it is fine. Both
of those produced convincing false negatives here. Use:

	dpkg-query -W -f='${Status}' <package> | grep -q 'ok installed'

To update any of them on purpose: `sudo apt-mark unhold <package>`. Note that if
the "modem never goes online" problem (see
`docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md` §5) is ever attacked, a newer
ModemManager is one of the things worth trying, so that particular hold is meant
to be lifted deliberately rather than kept forever.

`apt full-upgrade` does NOT re-flash `boot_a` (the running initramfs lives in
the flashed boot.img), so updates are safe for the boot path.

**Do not unhold these casually.** Each one is load-bearing: the kernel holds
stop a generic Debian kernel from replacing one that boots, `firmware-atheros`
silently overwrites the WiFi board file, the modem transport set is what brings
up both the modem and the internal WiFi, and the audio set keeps PipeWire
starting at all. Unholding one to chase a bug is fine; doing it by reflex
during an upgrade is how this port breaks.

Check what is protected at any time with `apt-mark showhold`; it should list 39
packages. The full list lives in `userspace/apt/apt-holds.txt` and
`userspace/apt/apply-holds.sh` puts it back after a rootfs reinstall.

<details>
<summary>The 39 held packages, by purpose</summary>

| Purpose | Packages |
|---|---|
| Custom kernel | `linux-image-7.2.0-rc5`, `linux-headers-7.2.0-rc5` |
| Device firmware | `firmware-motorola-rhodep`, `firmware-atheros` |
| Port packages | `rhodep-modem-support`, `rhodep-usb-otg`, `rhodep-battery-jeita` |
| USB WiFi injection | `realtek-rtl8188eus-dkms` |
| Modem transport | `rmtfs`, `tqftpserv`, `protection-domain-mapper`, `qrtr-tools`, `libqrtr1` |
| Modem and SIM | `modemmanager`, `libmm-glib0`, `libqmi-utils`, `libqmi-glib5`, `libqmi-proxy` |
| ALSA | `alsa-ucm-conf`, `alsa-utils`, `alsa-topology-conf`, `libasound2-data`, `libasound2t64` |
| PipeWire | `pipewire`, `pipewire-alsa`, `pipewire-audio`, `pipewire-bin`, `pipewire-pulse`, `libpipewire-0.3-0t64`, `libpipewire-0.3-common`, `libpipewire-0.3-modules`, `libspa-0.2-modules` |
| WirePlumber | `wireplumber`, `libwireplumber-0.5-0` |
| GStreamer bridge | `gstreamer1.0-pipewire` |
| On screen keyboard | `squeekboard`, `plasma-keyboard` |
| Modem tooling | `libqrtr-dev` |
| Kali menu launchers | `kali-menu` |

</details>

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
role (ModemManager auto-config); `nautilus` is the file manager. The Phone app
may stay limited until the modem works (mobile data is still blocked on the
interconnect driver — see open work), but the apps install and appear now.

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

The two highest-value items are the modem (blocks data and calls) and the audio
userspace (everything but the speaker). The rest is either not started or a
research dead-end kept for reference.

1. **Modem registration**: the modem boots, answers QMI and reports its IMEI,
   but its state machine never finishes initialising, so it blocks mobile data
   *and* calls. The sharpest evidence is that it rejects **every** mode
   transition, not just going online (`online` → `DeviceNotReady`, `low-power` →
   `InvalidTransition`), and the SIM PIN verifying changes nothing.

   **memshare was genuinely missing, is now implemented, and is not the
   cause.** The modem really does ask the application processor for memory
   during bring-up, which mainline never answered; `userspace/modem/` does now,
   and `kali-boot-v93-memshare.img` reserves the region it hands over. With the
   allocation fully satisfied the modem is unchanged.

   **The SIM is not the cause either**, which is worth stating because it is the
   natural suspicion when the card is an old prepaid one with no service. Power
   the card off so the modem sees no SIM at all and it *still* refuses to go
   online, and a modem with no SIM must be able to power its radio. The APN
   warning Plasma Mobile shows is downstream of all this and equally irrelevant
   until the radio comes on.

   **It is not a missing QMI service either**, and that was settled by sweeping
   rather than guessing: since the modem connects to whatever the name service
   announces, every service id from 1 to 235 that nobody provides was offered
   across cold boots. The modem reaches out to exactly two, LOWI (location) and
   an unnamed service 60 carrying `"msm_mpss"` and a 5 s timeout, and answering
   both with success changes nothing.

   Ruled out, with reasons, so nobody repeats them: the SIM (including with the
   card powered off entirely), the SIM lock, the carrier configuration (PDC has
   the right one active), rmtfs read-only mode, memshare, and any missing QMI
   service. What is left is below QMI, and the honest next step is asking
   upstream rather than another reflash. See
   `docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md` §5, sessions 13 to 13d.
2. **Audio**: speaker, earpiece, headphones, both microphones and jack detection
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

   **Call audio** is blocked on the modem (item 1), not on audio.
3. **Sensors, GPS, NFC, camera** — not started. The detailed technical notes
   (I2C addresses, GPIOs, which subsystem each goes through, feasibility) live
   in `docs/KERNEL-TECHNICAL.md` §7 (that doc is otherwise historical, but §7
   is still valid for these unstarted areas): §7.2 sensors (SSC/ADSP),
   §7.4 GPS (QMI/QRTR GNSS), §7.5 NFC (Samsung `sec-nfc`, no mainline driver —
   would need writing), §7.8 camera (CAMSS, effectively infeasible).
4. **Interconnect driver** (`docs/interconnect-sm6375-wip/`): not needed for
   mobile data (that was solved via the IPA register layout instead), but a
   real SM6375 interconnect driver would still benefit both ports. It was
   written and compiles, but resets the SoC at bus bind; next step is to
   register the buses one at a time and cross-check every `mas_rpm_id`/
   `slv_rpm_id` against a known-good SM6375/holi list. See
   `docs/interconnect-sm6375-wip/PROGRESS.md`.

# License
Kernel patches: GPL-2.0. Packaging/glue: MIT. Vendor firmware blobs: proprietary,
not included (extract from your own device). See `LICENSE`.

# Credits
Community port. Built on the postmarketOS mainline SM6375 work and the Kali
NetHunter Pro (debos) build system.
