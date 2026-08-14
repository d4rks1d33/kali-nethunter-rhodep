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
  building. The 31 patches are identical either way; only the config differs.

**Verify the apk before trusting it:**
- `iommus = <0x3a 0xa1 0x00>` in the decompiled `sm6375-motorola-rhodep.dtb`
  (the audio fix — without it the SoC resets on playback).
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

## Phase 6 — on-device, after first boot

These are not in the image (see README "Audio" and userspace/):

- Extract the AW88261 blob: `scripts/extract-aw88261-acf.sh` (item 3).
- Install the audio userspace: `userspace/audio/install.sh`.
- Apply the apt holds: `userspace/apt/apply-holds.sh` (34 packages; keeps an
  upgrade from replacing the kernel, the WiFi firmware, the modem stack or the
  audio stack).
- Swap the autologin for a real login screen: `userspace/login/install.sh`.
  Required as soon as anything pulls in `gdm3` (several Kali metapackages do),
  because `phosh.service` and a display manager both claim the display at boot.
  See README "Login screen (GDM) instead of the phosh.service autologin".

---

## Current state (what actually works)

See `README.md` "What works / Known limitations" for the authoritative list,
and `docs/interconnect-sm6375-wip/` for the deep history:

- **AUDIO-SM6375.md** — audio bring-up, sessions 1-12 (SOLVED at session 10-12).
- **HANDOFF-SESSION4.md** — IPA/mobile-data root cause, ramoops, modem, and the
  build/flash/where-everything-lives reference.

Short version: display, GPU, touch, WiFi/BT, battery/charging, USB/OTG, the
three remoteprocs, IPA (data path up), and **speaker audio** all work. The
modem enumerates but does not register (SIM/UIM issue). Headphones, mic, capture
and call audio are not configured yet.
