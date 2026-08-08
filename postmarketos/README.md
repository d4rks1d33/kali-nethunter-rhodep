# postmarketOS port source (motorola-rhodep)

Complete pmaports source for the **postmarketOS** port of the Motorola Moto G82
5G. This is the *other* userspace on top of the same mainline kernel; the Kali
NetHunter Pro port reuses this kernel (plus a `.config` diff — see below).

These directories are **pmaports packages**: drop them into a pmbootstrap
`pmaports` checkout under `device/testing/` and build with `pmbootstrap`.

```
postmarketos/
  linux-motorola-rhodep/     kernel aport: 26 patches + APKBUILD + config (NO NetHunter)
  device-motorola-rhodep/    device package: deviceinfo, systemd units, JEITA, modem glue
  firmware-motorola-rhodep/  firmware aport (APKBUILD only; blobs NOT included, see below)
```

## Kernel: pmOS vs Kali (.config diff)
The **kernel sources (26 patches) and DTB are identical** between the pmOS and
Kali ports. Only the `.config` differs:
- This `linux-motorola-rhodep/config-motorola-rhodep.aarch64` is the **pmOS**
  config (no NetHunter symbols).
- The Kali config = this config + `../kernel/config/nethunter-config.fragment`
  + **`CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`** (mandatory on Kali/Debian; harmless
  on pmOS, so the configs could be unified).
See `../kernel/` for the Kali variant and the fragment.

## Building with pmbootstrap

### One-time setup
```
# install pmbootstrap (see https://wiki.postmarketos.org/wiki/Pmbootstrap)
pmbootstrap init          # pick device: motorola-rhodep, UI: phosh (or none)
```

### Put these aports in place
Copy the three directories into your pmaports checkout:
```
PMAPORTS=~/.local/var/pmbootstrap/cache_git/pmaports
cp -r postmarketos/linux-motorola-rhodep    $PMAPORTS/device/testing/
cp -r postmarketos/device-motorola-rhodep   $PMAPORTS/device/testing/
cp -r postmarketos/firmware-motorola-rhodep $PMAPORTS/device/testing/
# add the vendor firmware blobs (see firmware-motorola-rhodep/ note below)
```

### Build the kernel
```
cd $PMAPORTS/device/testing/linux-motorola-rhodep
pmbootstrap checksum linux-motorola-rhodep      # ALWAYS after editing patches/config
pmbootstrap build --force linux-motorola-rhodep
# apk -> ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk
```
Verify: no empty `.ko`, and **no `.ko.zst`** in the apk (compressed modules hang
this device at boot).

### Build the device + firmware packages
```
pmbootstrap checksum device-motorola-rhodep && pmbootstrap build --force device-motorola-rhodep
pmbootstrap checksum firmware-motorola-rhodep && pmbootstrap build --force firmware-motorola-rhodep
```

### Build a bootable image / install
```
pmbootstrap install               # builds the rootfs image
pmbootstrap flasher flash_kernel  # flashes boot_a
# NOTE: this device sets deviceinfo_flash_kernel_on_update="true": the running
# DTB comes from the kernel apk in the rootfs; `apk add` of the kernel re-flashes
# boot_a via boot-deploy. So updating = `apk add` the kernel, NOT fastboot flash.
```
The rootfs image is a **sector-4096 GPT disk** written to `userdata` (same scheme
Kali uses). `fastboot flash userdata` is blocked on this bootloader — pmbootstrap
writes it during the guided install; to re-flash later, dd the image to userdata
from a running system or the rescue shell (see the top-level README).

## GOTCHAS (shared with Kali — read the top-level docs too)
- Flat `Image`, **not** `Image.gz`/EFI-zboot (Motorola bootloader resets otherwise).
- **`# CONFIG_MODULE_COMPRESS is not set`** — must stay off.
- Don't mix `.config` and logic changes in one build; bisect one change at a time.
- Full technical notes: `../docs/KERNEL-TECHNICAL.md`.

## Firmware blobs (NOT included)
`firmware-motorola-rhodep/` ships only the APKBUILD. The proprietary vendor blobs
are not redistributable — extract them from your own device's stock `vendor`
partition and place them where the APKBUILD expects (`fw/` subdir). The generic
ath10k WCN3990 firmware comes from Alpine's `linux-firmware`; only device-specific
GPU/BT/wlanmdsp blobs are packaged here. The large modem/adsp/cdsp blobs are read
at runtime from the phone's `modem_a` partition (see the Kali
`rhodep-modem-support` package / top-level README).

## Port status (pmOS)
Works: boot, display, touch, GPU (accelerated), WiFi+BT, battery+charging with
thermal protection, thermal throttling, USB host/gadget+SSH, buttons, vibrator,
Docker. Mobile data: IPA present but `status=disabled` (needs the SM6375
interconnect driver). See `../docs/KERNEL-TECHNICAL.md` §7 for the full list and
per-feature starting points.

## MR to upstream pmaports
Target: `postmarketOS/pmaports!9234`. The pmOS config here (no NetHunter) is the
clean baseline for that MR.
