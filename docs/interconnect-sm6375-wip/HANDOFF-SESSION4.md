# SM6375 mobile-data (IPA) — SESSION 4: SOLVED

Device: Motorola Moto G82 5G (motorola-rhodep, SM6375 / Snapdragon 695).
Kernel 7.2.0-rc5. Read this INSTEAD of HANDOFF-SESSION3.md; sessions 1-3 were
chasing the wrong thing.

--------------------------------------------------------------------------------
## 0. CURRENT STATE (read this first)
--------------------------------------------------------------------------------
### The image to use

**`kali-boot-v80-STABLE.img`** in /opt/postmarket/kali-nethunter/img/. This is
the complete working port and the one flashed on the device: display, touch,
GPU, wifi/BT, the three remoteprocs, battery, charger, USB/OTG, thermal
throttling, ramoops in the region the bootloader really preserves, and the IPA
with the mobile data path up. Kernel patches 0001-0026, no audio, no
interconnect, no diagnostics.

Rollback: `kali-boot-v47.img` (the pre-everything image). Everything in between
(v48..v76) was diagnostic; do not ship any of it.

The device is currently left flashed with **`kali-boot-v88-smmu-a1.img`**, an
audio experiment (see AUDIO-SM6375.md, session 7) whose kernel also carries the
APR/ASM diagnostic patches. It is fine for testing but it is not the image to
ship: go back to v80 for daily use.

**Flashing is done by the user.** From now on the fastboot commands
(`fastboot flash boot_a ...`, `--set-active`, `reboot`) are run by hand on the
user's side; do not try to drive fastboot over USB from the build host. Build
the image, hand over the filename, and wait to be told it booted.

### What works

**IPA / mobile data path: yes.** It loads at boot and sets up:

	ipa 5840000.ipa: IPA driver initialized
	ipa 5840000.ipa: IPA driver setup completed successfully
	ipa 5840000.ipa: received modem starting event
	ipa 5840000.ipa: received modem running event

`rmnet_ipa0` is created and ModemManager reports the modem with
`drivers: qrtr, ipa` and `ports: qrtr0 (qmi), rmnet_ipa0 (net)`, i.e. it finally
has a data port. **No interconnect provider is involved**, see §1.

### What does not work

1. **The modem never leaves offline state**, so no data and no calls. Separate
   and pre-existing problem, full evidence and leads in §5, including what was
   checked and ruled out (rmtfs and the EFS are fine) and one thing that was
   actually broken and is now fixed: ModemManager was racing the modem and never
   saw it at all, so the phone never even asked for the SIM PIN.
   Note a newer, known-good SIM is not detected on this modem at all
   (no ATR) while the older one is; see §5, it looks like a consequence of the
   modem not initialising rather than a separate problem.
2. **Audio.** The device tree is complete and the card registers, but a stream
   takes the SoC down. See **AUDIO-SM6375.md**, which has the state, everything
   ruled out with its evidence, and the next step. The patches (0032-0036) are
   in the kernel aport directory and in this directory but are deliberately
   **out of `source=`**, so the shipped kernel has no audio nodes.
3. Loudspeaker and earpiece are Awinic i2c smart amplifiers with a proprietary
   ADSP topology; mainline has no driver for that family (AUDIO-SM6375.md §5).

### On the device

- ssh `kali` / `1234` at 192.168.1.53, sudo with the same password, no root
  login.
- The module tree matches the v80 kernel and `ipa.ko` autoloads by modalias.
- `ipa_fws.mdt` + `.b0[0-4]`, extracted from the modem partition, are in
  /lib/firmware/qcom/sm6375/motorola/rhodep/ and are also packaged in
  firmware-motorola-rhodep (pkgver 3), which is NOT installed as a package yet.

### Reading crash logs

`/var/lib/systemd/pstore/`, **not** `/sys/fs/pstore/`, which systemd-pstore
empties on boot. See §2.

--------------------------------------------------------------------------------
## 1. ROOT CAUSE of the "IPA resets the SoC" bootloops (v48..v62)
--------------------------------------------------------------------------------
The IPA node had the **sdm845/sc7180 register layout**. From **IPA v4.5**
onwards the shared-SRAM direct-access window moved from `reg_base + 0x7000` to
`reg_base + 0x10000`. Two independent confirmations:

- Downstream `techpack/dataipa/.../ipahal/ipahal_reg.c`:

	[IPA_HW_v3_0][IPA_SW_AREA_RAM_DIRECT_ACCESS_n] = { ... 0x00007000 ... }
	[IPA_HW_v4_5][IPA_SW_AREA_RAM_DIRECT_ACCESS_n] = { ... 0x00010000 ... }

  with `ipahal_get_reg_base() == 0x40000`. Later versions (v4.7/v4.9/v4.11)
  inherit the v4.5 value (ipahal_reg_init() copies forward).
- Mainline itself: `sm6350.dtsi` (IPA v4.7) and `sm8350.dtsi` (IPA v4.9) use
  `ipa-reg` size 0x8000, **`ipa-shared` at base+0x50000**, `gsi` size 0x23000.
  Only sdm845/sc7180 (v3.5.1/v4.2) use the +0x47000 window.

So `ipa-shared` pointed at 0x5847000, which on this SoC is a hole:
**any access to it, even a single 32-bit read, resets the SoC instantly**, with
no CPU exception, no panic and not one character of output. That is what killed
v48 and every "IPA okay" image after it, and it is why it looked like "IPA needs
an interconnect vote".

Correct node (patch 0026):

	reg = <0x0 0x05840000 0x0 0x8000>,     /* ipa-reg    = base + 0x40000 */
	      <0x0 0x05850000 0x0 0x3000>,     /* ipa-shared = base + 0x50000 */
	      <0x0 0x05804000 0x0 0x23000>;    /* gsi        = base + 0x04000 */

(base = 0x5800000 = downstream "ipa-base"; the 0x23000 gsi size matches the
downstream "gsi-base" region exactly.)

Two more fixes in the same node:
- `iommus = <&apps_smmu 0x4a0 0x0>, <&apps_smmu 0x4a2 0x0>` — the AP and the
  microcontroller context banks (downstream `ipa_smmu_ap` 0x4A0 and
  `ipa_smmu_uc` 0x4A2), mirroring sm6350's 0x440/0x442. Only 0x4a0 was there.
- `qcom,gsi-loader = "self"` + `memory-region = <&pil_ipa_fw_mem>` +
  `firmware-name = "qcom/sm6375/motorola/rhodep/ipa_fws.mdt"`.
  It was `"modem"`, which is wrong for this SoC: downstream describes the GSI
  firmware as an **AP-loaded PIL peripheral**:

	qcom,ipa_fws { compatible = "qcom,pil-tz-generic";
	               qcom,pas-id = <0xf>;          /* 15 == IPA_PAS_ID */
	               qcom,firmware-name = "ipa_fws";
	               memory-region = <&pil_ipa_fw_mem>; };

  PAS id 15 and pil_ipa_fw_mem are exactly what mainline `ipa_firmware_load()`
  uses. The signed image lives in the **modem (NON-HLOS) partition** as
  `image/ipa_fws.mdt` + `ipa_fws.b0[0-4]`; it is now shipped by
  firmware-motorola-rhodep (pkgver 3). To re-extract:

	mount -o ro /dev/disk/by-partlabel/modem_a /mnt && ls /mnt/image/ipa_fws.*

The `interconnect_count = 0` variant from patch 0025 is kept and is correct:
the RPM keeps bimc/cnoc/snoc voted at INT_MAX by itself (rpmcc hands the icc bus
clocks off at INT_MAX and never registers them with CCF), so Linux never needs
to clock DDR on this SoC. **The whole interconnect driver effort (patches
0027-0030, VENDOR-BIMC.md, AUDIT.md) was not needed and is not in source=.**

--------------------------------------------------------------------------------
## 2. THE OTHER ROOT CAUSE: ramoops was at the wrong address (we were blind)
--------------------------------------------------------------------------------
Sessions 1-3 never got a single log. Reason: patch 0008 put ramoops at
**0xd0000000**, taken from the generic `holi.dtsi`/`blair.dtsi` node — but that
node is **explicitly deleted on Motorola boards** (`blair-moto-base.dts:
/delete-node/ ramoops`) and the bootloader does scribble over it (the running
v47 printed "ramoops: error in header" on every boot: pure garbage).

The real region is in `include/dt-bindings/moto/moto-mem-reserve.h`:

	RAMOOPS_BASE_ADDR	0xaf000000
	RAMOOPS_CONSOLE_SIZE	0x40000
	RAMOOPS_PMSG_SIZE	0x40000
	RAMOOPS_RECORD_SIZE	0x3f800	  -> RAMOOPS_SIZE = 0xbf800

instantiated by `blair-pm6125-moto-common-overlay.dtsi` (rhodep's base DT is
`blair-moto-rhodep-base.dts`, and blair *is* the SM6375; it includes
`dt-bindings/interconnect/qcom,holi.h`).

Patch 0008 now uses 0xaf000000/0xbf800, `no-map`, console 0x80000, record
0x3f800, no pmsg (CONFIG_PSTORE_PMSG is off), `mem-type = <1>` (unbuffered, so
console bytes hit DRAM instead of sitting in a WC buffer when the SoC dies) and
`max-reason = <5>` so even an orderly reboot writes a dump record — which makes
the black box self-testable.

It works: the captured log runs from `postcore_initcall(ramoops_init)` (pstore's
console has CON_PRINTBUFFER, so the earlier printk buffer is replayed too) all
the way to `reboot: Restarting system`. Expect **occasional single-byte
corruption** (DRAM bit rot across the reset); `ecc-size = <16>` would fix it and
is worth adding.

### !!! WHERE TO ACTUALLY READ IT
`/sys/fs/pstore/` will look EMPTY, because **systemd-pstore.service moves the
files** on boot to:

	/var/lib/systemd/pstore/{console-ramoops-0,dmesg-ramoops-0.enc.z}

--------------------------------------------------------------------------------
## 3. THE DEBUG LOOP THAT FOUND IT (reuse this, it is cheap)
--------------------------------------------------------------------------------
`CONFIG_QCOM_IPA=m`, so IPA does not have to be part of the boot. That turns a
one-flash-per-guess bootloop hunt into an interactive session:

1. Keep the DT node enabled but move the module out of the way so udev cannot
   autoload it:
	mv /lib/modules/7.2.0-rc5/kernel/drivers/net/ipa/ipa.ko /root/ipa-hold/
	depmod -a 7.2.0-rc5
   The device then boots normally with the IPA node present.
2. `insmod /root/ipa-hold/ipa.ko` over ssh. If the SoC dies, reboot and read
   /var/lib/systemd/pstore/.
3. Iterate on the *module only* — build with pmbootstrap, extract just ipa.ko
   from the apk, scp it. **No reflash**, ~6 min per iteration:
	tar xzf .../linux-motorola-rhodep-7.2_rc5-r0.apk \
	    usr/lib/modules/7.2.0-rc5/kernel/drivers/net/ipa/ipa.ko

`0031-DIAG-net-ipa-trace-probe-steps.patch` (kept on disk, NOT in source=) does
this instrumentation: a KERN_EMERG marker before every step of ipa_probe(),
ipa_config() and ipa_hardware_config(), plus a `diag_mode` module parameter that
probes the shared SRAM (1 = read only then abort, 2 = read + one 32-bit write,
3 = same through a private ioremap instead of memremap-WC).

How the answer fell out:
- markers -> died in `ipa_mem_config()`, everything before (all of
  ipa_hardware_config, dozens of MMIO writes) fine.
- values -> `SHARED_MEM_SIZE = 0x600` => mem_offset 0, size 0x3000, i.e. the
  register block at 0x5840000 is right and sane.
- died on the first canary write, `mem_virt + 0x280`.
- `diag_mode=1` (a plain read) also reset => not write protection, the window
  simply is not there => wrong address.

--------------------------------------------------------------------------------
## 4. NOTES / GOTCHAS
--------------------------------------------------------------------------------
- SSH: `kali` / `1234` at 192.168.1.53, sudo with the same password. There is no
  root login. No ssh client on the build host by default:
  `sudo apt-get install -y openssh-client sshpass`.
- The vendor source is a partial (blob:none) sparse clone in
  `/tmp/opencode/dl/moto`, remote
  `github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375`, branch
  `fifteen`. Add paths on demand with `git sparse-checkout add <path>`; that is
  how `include/dt-bindings` and `techpack/dataipa` got fetched.
- rhodep is a **blair** board, not holi: `blair-moto-rhodep-base.dts` ->
  `blair.dtsi`. blair.dtsi uses the holi interconnect bindings, so holi.c was
  the right reference for that, but always cross-check blair.dtsi.
- `pmbootstrap build` must be started detached (`setsid nohup ... &`), otherwise
  it is killed when the calling shell times out.
- Keep the hunk line counts of the patches honest: `@@ -a,N +b,M @@`. Some of
  the existing patches have wrong counts and only apply thanks to fuzz.
- ModemManager 1.24.2 logs
  `couldn't reset QMI port 'qrtr0' with data interface 'rmnet_ipa0': ...
  /dev/qrtr0: No such file or directory`
  every time. It is a **warning** and harmless (MM tries to build a /dev path
  for what is a QRTR node), not the reason anything fails. Probably worth an
  upstream MM bug report.

--------------------------------------------------------------------------------
## 5. NEXT TASK: the modem never goes online (blocks data AND voice)
--------------------------------------------------------------------------------
This is independent of IPA and predates it. Evidence collected with qmicli
(`qmicli -d qrtr://0 ...`, stop ModemManager first):

- `--dms-get-operating-mode` -> `Mode: 'offline'`
- `--dms-set-operating-mode=online` -> QMI error 52 `DeviceNotReady`
- `--dms-set-fcc-authentication` -> QMI error 57 `WmsInvalidMessageId`
- `--nas-get-serving-system` -> not-registered, CS/PS detached, radio 'none'
- `--uim-get-card-status` -> `Card state: 'present'`,
  `PIN1 state: 'enabled-verified'` (the PIN does get accepted), but the usim
  application is stuck at `Application state: 'check-personalization-state'`
  with `Personalization state: 'unknown'`, forever.
- Consequently ModemManager gives up with "Couldn't check unlock status:
  Couldn't get SIM lock status after 30 retries" -> state `failed: sim-missing`.
- The modem itself boots fine (remoteproc0 up, 69 QRTR services registered
  including "IPA control service"), and rmtfs / pd-mapper / tqftpserv /
  qrtr-ns are all running (rmtfs with `-r -P -s`).

### Session 4c: what was checked on the modem, and corrected

**ModemManager was never seeing the modem at all.** It probes qrtr0 exactly once
at startup and never retries, and the modem is a remoteproc that needs ~15 s to
boot and register its QMI services, so ModemManager always lost the race:
"No modems were found", no SIM, and therefore the phone never asked for the SIM
PIN. Fixed in rhodep-modem-support >= 2 with a drop-in that waits for the QMI
User Identity Module service (id 11) before starting, capped at 90 s. Verified
at a cold boot: the modem now appears on its own and reports
`lock: sim-pin / state: locked`.

This also invalidates part of the diagnosis below: the
"check-personalization-state" the USIM appeared stuck in was almost certainly a
side effect of restarting ModemManager without restarting the modem, not a modem
fault. With a clean boot the card reports `pin1-or-upin-pin-required` and
`PIN1 state: enabled-not-verified`, which is exactly right.

**rmtfs and the EFS are healthy.** Ruled out as a cause: with `-v` it opens both
EFS files and serves reads with no errors, using the shared buffer at the
rmtfs_mem region:

	[RMTFS] alloc 0, 2621440 => 0xf3900000 (0:0)
	[RMTFS] open /boot/modem_fs1 => 0 (0:0)
	[RMTFS] open /boot/modem_fs2 => 1 (0:0)

Note `/boot/modem_fs1` is the path the **modem** asks for, not a local file;
rmtfs maps it to /dev/disk/by-partlabel/modemst1. A cold boot shows the modem
only ever requesting those two, never modem_fsg or modem_fsc.

**A partition alias that was missing anyway.** rmtfs resolves those requests
under /dev/disk/by-partlabel with bare names, but on this device `fsg` is A/B
suffixed (`fsg_a`) while modemst1/modemst2/fsc are not, so a request for the
factory NV backup would have failed. rhodep-modem-support >= 3 ships a udev rule
that creates the unsuffixed alias. Preventive, since the modem does not ask for
it.

**The modem cannot be restarted from the AP** to retry things: writing "stop" to
/sys/class/remoteproc/remoteproc0/state returns success but the modem stays
running, because the WLAN protection domain lives inside the modem and holds a
reference (stopping it would also drop wifi).

### A SIM that works elsewhere gets no ATR

Testing with a SIM that is known good (daily driver in another phone, no PIN) it
is not detected at all:

	Card state: 'error: no-atr-received (3)'
	Physical slot 1: Card status: absent, Slot status: active

ATR is the card's very first answer after the slot is powered and reset, before
any PIN, file or network activity, so this is electrical/protocol level and
nothing the kernel or the device tree controls. It is **not** placement or a
broken reader: putting the original (prepaid, older) SIM back makes it read
again immediately, `Card state: 'present'`. Power cycling the slot with
`qmicli --uim-sim-power-off/on` does not change it.

So one card gets an ATR on this modem and another, newer one does not. The usual
cause is the SIM voltage class (modern cards are often 1.8 V only, older ones
tolerate 3 V) and which classes the modem's UIM tries. Since the same modem
firmware and the same EFS work with any SIM under Android, the most likely
explanation is that this is **downstream of the modem never initialising
properly**: a modem stuck offline may not run the full voltage class retry
sequence. Chasing it separately is probably wasted effort; get the modem online
first.

So: the card is readable and the PIN verifies, but the modem stays offline and
the USIM application never reaches `ready`. Leads worth chasing, roughly in
order:
1. rmtfs EFS access: confirm it really opens modemst1/modemst2 (check
   /proc/<rmtfs pid>/fd) and that writes succeed. A modem that cannot persist
   its EFS is a classic "stays offline / DeviceNotReady".
2. Whether the modem wants something else we do not provide yet: `memshare`
   (blair.dtsi qcom,memshare client_1/client_2 with allocate-boot-time),
   a `qcom,mss` region, or the `fsg` partition.
3. Compare the downstream boot: which QMI/QRTR services the stock ROM has that
   we do not (diff `qrtr-lookup` against an Android boot).
4. Try a different SIM with actual service before spending too long: some of
   this may just be the prepaid SIM without a plan.

--------------------------------------------------------------------------------
## 6. BUILD, FLASH AND WHERE EVERYTHING IS
--------------------------------------------------------------------------------
This replaces HANDOFF-SESSION3 §5, which is stale.

### Persistent locations (safe)

| what | where |
| --- | --- |
| kernel aport (the one that is built) | `~/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep/` |
| device and firmware aports | same directory, `device-motorola-rhodep/`, `firmware-motorola-rhodep/` |
| project repo (commit here) | `/opt/postmarket/nethunter-rhodep-repo`, remote `github.com/d4rks1d33/kali-nethunter-rhodep` |
| aport mirror inside the repo | `postmarketos/` (keep in sync when committing) |
| these notes | `/opt/postmarket/_common/interconnect-sm6375-wip/`, mirrored in the repo under `docs/` |
| boot images | `/opt/postmarket/kali-nethunter/img/` |
| support .deb packages | `/opt/postmarket/kali-nethunter/img/` and `packages/` in the repo |
| **ramdisk and cmdline needed to build any boot image** | `/opt/postmarket/_common/boot-artifacts/` |
| backups | `/opt/postmarket/_common/backups-historicos/` |

### Volatile locations (in /tmp, WILL be lost, all recreatable)

- `/tmp/opencode/dl/moto` — vendor source, partial sparse clone. Recreate with:

	git clone --filter=blob:none --sparse -b fifteen \
	    https://github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375 moto
	cd moto && git sparse-checkout add arch/arm64/boot/dts/vendor/qcom \
	    include/dt-bindings drivers/interconnect techpack/audio techpack/dataipa

- `/tmp/opencode/kernel/linux-7.2-rc5` — pristine reference tree, used to write
  patches against. Same tarball the APKBUILD fetches:
  `https://git.kernel.org/torvalds/t/linux-7.2-rc5.tar.gz`
- `/tmp/gpuwork/linux-7.2-rc5/scripts/dtc/dtc` — the dtc used to decompile DTBs.
  Any dtc will do, or build it from the reference tree.
- `/tmp/opencode/lore.py` — solves the Anubis proof-of-work so lore.kernel.org
  can be queried from the shell. Copied to
  `/opt/postmarket/_common/scripts/lore-fetch.py`. Use it to search upstream
  patches (`https://lore.kernel.org/all/?q=s:sm6375&x=t`) and fetch whole threads
  as `.../t.mbox.gz`, which is how the sm6115 audio series was found.

### Build the kernel

	cd /home/pmos
	setsid nohup pmbootstrap build --force linux-motorola-rhodep > /tmp/b.log 2>&1 < /dev/null &

**It must be detached**: a plain foreground run dies when the calling shell times
out. Poll for the end instead of sleeping blindly:

	tail -4 ~/.local/var/pmbootstrap/log.txt | grep -qE "DONE!|ERROR: Couldn't build"

A patch-only change takes ~5 minutes; touching the `.config` forces a full
rebuild, ~10 to 35 minutes. Output:
`~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk`

Whenever a patch or the config changes, refresh its sha512sum in the APKBUILD or
the build fails with "computed checksums did NOT match".

### Make a boot image

	cd /tmp && rm -rf out && mkdir out && cd out
	tar xzf ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk

	# ALWAYS verify the DTB before flashing
	dtc -I dtb -O dts boot/dtbs/qcom/sm6375-motorola-rhodep.dtb | grep <the property you changed>

	python3 /opt/postmarket/_common/scripts/mkbootv2b.py \
	    boot/vmlinuz boot/dtbs/qcom/sm6375-motorola-rhodep.dtb \
	    /opt/postmarket/_common/boot-artifacts/v47_ramdisk \
	    "$(cat /opt/postmarket/_common/boot-artifacts/v47_cmdline.txt)" \
	    /opt/postmarket/kali-nethunter/img/kali-boot-vNN.img

The Motorola bootloader needs the **flat `Image`** (starts with `MZ`, which is
why CONFIG_EFI_ZBOOT is off) with the **DTB appended**, which is what
mkbootv2b.py does. An Image.gz-dtb resets the device.

Verifying the DTB is not optional: two patches touch `sm6375.dtsi` in sequence,
so editing only the first one's base tree silently leaves the old value in the
final DTB. That happened once and was only caught by decompiling.

### Flash

	fastboot flash boot_a <img> && fastboot reboot

Rollback is `kali-boot-v47.img`. `fastboot flash userdata` is always permission
denied on this device (no fastbootd); userdata is written with dd from a running
system.

### The shortcut that saves most of the time

If the change is **driver only** (no device tree), there is no need to reflash.
Rebuild, take just the modules out of the apk and copy them over ssh:

	tar xzf ...apk usr/lib/modules/7.2.0-rc5/kernel/<path>/<module>.ko
	scp <module>.ko kali@192.168.1.53:/tmp/
	ssh ... 'cp /tmp/<module>.ko /lib/modules/7.2.0-rc5/kernel/<path>/ && depmod -a 7.2.0-rc5'

Only device tree changes need a new boot image. The kernel version string does
not change, so modules stay loadable across these rebuilds.

### Debugging technique that actually worked

Both the IPA bug and the audio bug were found the same way, and it is worth
reusing:

1. Make sure the black box works: ramoops at 0xaf000000, read from
   `/var/lib/systemd/pstore/` (see §2 for why not /sys/fs/pstore).
2. If the driver is a module, keep it **out** of `/lib/modules` (move it aside
   and run depmod) so the device boots with the new device tree but nothing
   touches the hardware. Then load it by hand over ssh and watch.
3. If it dies with no output, add `printk(KERN_EMERG ...)` markers around each
   step and bisect. `CONFIG_DYNAMIC_DEBUG` is enabled in the shipped kernel, so
   the drivers' own `dev_dbg` messages can be turned on at runtime with
   `echo 'module <name> +p' > /sys/kernel/debug/dynamic_debug/control`
   (module patterns work, file globs do not).
4. Check `androidboot.bootreason` in /proc/cmdline after a reset: `watchdog`
   means a hang, not a bus abort, which changes what to look for entirely.
