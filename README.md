# Kali NetHunter Pro for the Motorola Moto G82 5G (motorola-rhodep)

Port of **Kali Linux (NetHunter Pro / Phosh)** on a **mainline Linux kernel
(7.2-rc5)** to the Motorola Moto G82 5G — codename **rhodep**, SoC **Qualcomm
SM6375** (Snapdragon 695). This device is **not** on Kali's official supported
list; everything here is a from-scratch community port.

> Built on top of a postmarketOS mainline kernel port. The kernel is shared with
> the pmOS port; Kali only changes the kernel `.config` and the userspace.

> **Current image: `kali-boot-v92-STABLE-audio.img`** — the first one with
> working sound. It replaces the older v80 for daily use.

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
- **Audio**: speakers work. Headphones, the microphones and call audio are not
  configured yet; the UCM only describes the speaker, and both amplifiers are
  left on the ACF "Music" profile where the earpiece will want "Receiver" for
  calls. Capture does not work yet.
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
  audio/              UCM, PipeWire/WirePlumber rules and the route unit (install.sh)
  apt/                the 34 apt holds this port depends on (apply-holds.sh)
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

Both amplifiers play together, the bottom loudspeaker and the earpiece, which
is how the phone is meant to sound. The amplifier volume control is
attenuation in 0.25dB steps from 0 to 360, so 360 is full scale.

If you ever reinstall the rootfs, steps 2 and 3 have to be done again, along
with the modules under `/lib/modules/7.2.0-rc5`, none of which live in the
boot image.

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

Check what is protected at any time with `apt-mark showhold`; it should list 34
packages. The full list lives in `userspace/apt/apt-holds.txt` and
`userspace/apt/apply-holds.sh` puts it back after a rootfs reinstall.

<details>
<summary>The 34 held packages, by purpose</summary>

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

</details>

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

1. **Modem registration**: the modem enumerates but never registers
   (`DeviceNotReady`, SIM gets no ATR). This is the main blocker for mobile
   data *and* calls. Full evidence and a prioritised next-step list (EFS access
   to modemst1/2, `memshare`/`qcom,mss`/`fsg`, diffing the QMI/QRTR services
   against an Android boot, trying a SIM with real service) are in
   `docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md` §5.
2. **Audio, remaining paths**: speaker works; headphones, microphone/capture,
   the earpiece "Receiver" profile and call audio do not yet. All of these are
   now **userspace/UCM work** on a kernel that already carries the paths — see
   `docs/interconnect-sm6375-wip/AUDIO-SM6375.md` §6 (a full manual `amixer`
   headphone-routing recipe to base the UCM on) and the "Still to do" notes at
   the end. **Call audio** additionally needs the modem to register (item 1)
   and the earpiece on the Receiver profile before there is anything to route.
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
