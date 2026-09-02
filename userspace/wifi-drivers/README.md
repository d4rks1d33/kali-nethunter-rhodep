# rhodep-wifi-drivers

Install the **external USB Wi-Fi adapter drivers** so a freshly flashed image
comes up ready for **monitor mode + packet injection** on `wlan1`, instead of
needing the drivers built by hand. The internal WCN3990 (`wlan0`) cannot do raw
mode; these adapters are how the port does WiFi auditing.

Two adapters, two drivers (this is an orchestrator around the two driver
packages, not a third driver):

| Adapter | Chip | Driver | Package |
| --- | --- | --- | --- |
| TP-Link TL-WN722N v2/v3 | RTL8188EUS | `rtl8188eus` (`8188eu`), patched for 7.2 | `packages/rhodep-rtl8188eus-fix` |
| TP-Link Archer T2U Nano | RTL8811AU | `rtw88` (`rtw_8821au`) | `packages/rhodep-rtl8821au` |

## Why this is not baked into the image like the other userspace

Both are **DKMS modules: compiled against the kernel that will run them.** A
loadable `.ko` has to match its kernel exactly (same build, not just the same
`uname -r` -- see "Building out-of-tree drivers" in the top-level README). The
debos build chroot runs the *build host's* kernel, not the phone's, so it cannot
produce a working `.ko` for the phone. Baking a prebuilt module in would give
you a file that refuses to load with `Exec format error` / version-magic
mismatch.

So the build is **deferred to the device**:

- In the **chroot**, `install.sh` stages the driver sources under
  `/usr/local/share/rhodep/wifi-drivers/` and arms
  `rhodep-wifi-drivers-firstboot.service`.
- On the **device's first boot**, that service runs
  `rhodep-wifi-drivers-build`, which compiles both drivers against the booted
  kernel and writes a stamp keyed to a **fingerprint of that kernel build**
  (`uname -r` + a hash of `uname -v`), not just the version. It stays enabled and
  is a cheap no-op on ordinary boots, but rebuilds automatically after any kernel
  change -- **including a rebuild of the same version** (same `uname -r`, new
  build), which a version-only stamp would wrongly skip. It also re-verifies that
  `rtw_8821au` actually loads, so a stamp left by a broken module set is not
  trusted. It needs the network once, to `apt-get` the DKMS package and build
  deps; if the network or the kernel headers are not there yet it exits cleanly
  and retries on the next boot.
- On a **live system**, `install.sh` builds immediately.

## Prerequisite: kernel headers

DKMS needs `/lib/modules/<KVER>/build`. The pmbootstrap apk does not ship it, so
this port builds a matching `linux-headers-<KVER>.deb` with
`scripts/build-kernel-headers.sh`. Put that `.deb` next to `install.sh` (or in
`/srv`, or under the stage dir) and it is installed automatically; otherwise
install it yourself before running. Without headers the first-boot service waits
and retries rather than failing.

## Use

```sh
# in the debos chroot (stage + arm firstboot):
sh install.sh

# on the device, to build now:
sudo sh install.sh
# or, after a kernel change, just:
sudo rhodep-wifi-drivers-build
```

After a build, plug the adapter and power the port (`otg on` -- single USB-C
port, so host mode needs VBUS boost from the charger). It enumerates as `wlan1`;
`wlan0` is never touched. Monitor mode: `rhodep-pwn-monstart` (port way) or
`airmon-ng start wlan1`.

## Held / protected

Nothing here is a distributable `.deb`, so `apt-mark hold` does not apply to the
files this orchestrator lays down. They are protected the way the rest of the
port protects package-less files -- `rhodep-protect-files` (immutable + content
snapshot + auto-restore by `rhodep-holds-enforce`):

- `/usr/local/sbin/rhodep-wifi-drivers-build` (component `wifi-drivers`)
- `/etc/systemd/system/rhodep-wifi-drivers-firstboot.service` (`wifi-drivers-conf`)
- the staged driver sources under `/usr/local/share/rhodep/wifi-drivers/`
  (`wifi-drivers-src`), so a stray `rm` cannot leave the first-boot build with
  nothing to build

The firstboot unit is deliberately **not** `register-units`'d, but for a
different reason than before: the builder no longer self-disables (it stays
enabled to catch a future kernel rebuild), so there is nothing for the enforcer
to fight. The immutable file is enough.

What the apt-hold layer covers, and is already in `userspace/apt/apt-holds.txt`
so a clean build applies it: `linux-headers-7.2.0-rc5`, `linux-image-7.2.0-rc5`
and `realtek-rtl8188eus-dkms`. The rtw88 `.ko` and the 8188eus `.ko` are
protected by the driver packages' own `install.sh` (see `packages/rhodep-rtl8821au`).

Protection needs `rhodep-protect-files`, which `userspace/apt/apply-holds.sh`
installs. If you run this installer **before** apply-holds.sh, it prints a note
and skips protection; re-run it afterwards (or run apply-holds.sh last, as the
build order does) to protect the files.

## After a kernel change (including a same-version rebuild)

A new kernel means new modules. Because the stamp is keyed to the kernel-build
fingerprint (`uname -r` + `uname -v`), the enabled service rebuilds the drivers
on the next boot after **any** kernel change -- a version bump *or* a rebuild of
the same version -- provided the matching `linux-headers-<KVER>` are installed.

Two frictions to know about:

- The built `.ko` files are made immutable by `rhodep-protect-files` (the driver
  packages do this), and DKMS cannot overwrite an immutable `.ko`. The builder
  releases them before `dkms install`; if you rebuild by hand, first
  `rhodep-protect-files release <ko>`.
- **Do not just copy `/lib/modules` across when you flash a same-version
  rebuild.** rtw88 is a *family* (~19 `.ko`); copying only `rtw_8821au.ko` leaves
  it unable to resolve its symbols (`Unknown symbol rtw_usb_probe`) and `wlan1`
  never appears. Let the firstboot service rebuild it, or run `dkms install
  rtw88/0.6 -k $(uname -r) --force` yourself. See "Build & install a clean kernel
  image" step 5 in the top-level README.
