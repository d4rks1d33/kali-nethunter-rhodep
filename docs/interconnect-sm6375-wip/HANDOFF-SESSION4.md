# SM6375 mobile-data (IPA) — SESSION 4: SOLVED

Device: Motorola Moto G82 5G (motorola-rhodep, SM6375 / Snapdragon 695).
Kernel 7.2.0-rc5. Read this INSTEAD of HANDOFF-SESSION3.md; sessions 1-3 were
chasing the wrong thing.

--------------------------------------------------------------------------------
## NEXT SESSION, START HERE
--------------------------------------------------------------------------------
The modem is **done**: cold boot to LTE registration with nothing typed by hand,
~24 Mbit/s of data, calls that connect, SMS that arrives. §5 session 14 is the
full log; `userspace/modem/README.md` is the short version.

**One bug is in the way, and it is not a modem bug.** With `ipa.ko` loaded *and*
the modem registered, the SoC watchdog-resets every 3-10 minutes, silently.
Either one alone is stable. The phone therefore ships with
`/etc/modprobe.d/rhodep-ipa-hold.conf`, which keeps `ipa.ko` out of the boot and
costs mobile data and ModemManager.

Your job, if you are picking this up, is that bug. Concretely:

1. Read §5 "OPEN BUG" for the bisection table and what is already ruled out
   (suspend, thermal, the IMEM address, IMEM cacheability). Do not redo those.
2. **Read §5 session 15 as well, because it buried the two leads this list used
   to open with.** Both were built, flashed and measured, and neither is the
   cause. Do not re-test them:
   - the unvoted NoC paths (`interconnect_count = 0`): the SM6375 interconnect
     driver now works, six providers bind, the IPA is a consumer on all three
     vendor paths — and it still resets. Patches 0027/0028/0044/0045/0046/0047.
   - the IPA SRAM layout inherited from qcm2290: transcribed from the vendor's
     `ipa_4_11_mem_part`, genuinely different, and it still resets. Patch 0048.

   All of those patches are **kept** — each is a correction that is right on its
   own merits — but none of them is the bug.
3. **The one experiment that has never run**, and the thing to do first:
   *attach to LTE with `ipa.ko` never loaded*. It answers the question outright
   — if it still resets, the IPA is innocent and three sessions of looking at it
   were misdirected. It needs the LTE attach APN set by hand over `qmicli`,
   because ModemManager normally configures it and MM will not touch a QMI modem
   with no net port. The end of §5 session 15 has the exact commands and the
   reason the obvious version of this experiment fails.
4. Reproduce like this, so the causal window is seconds and not minutes.
   `ipa.ko` is a module, so no reflash is needed for any of it:

	# boot with rhodep-ipa-hold.conf in place, then:
	/usr/local/sbin/rhodep-uim-provision
	qmicli -d qrtr://0 --dms-set-operating-mode=online
	qmicli -d qrtr://0 --nas-get-serving-system      # wait for 'registered'
	insmod /lib/modules/7.2.0-rc5/kernel/drivers/net/ipa/ipa.ko dyndbg==p

5. **Do not trust ssh dropping as proof of a reset.** The screen locking takes
   WiFi down and looks identical; that cost real time here. Log uptime to a file
   from a systemd unit, and get the truth per boot from the journal:
   `journalctl -b -N -k | grep -m1 "Kernel command line"` -> `bootreason=`.
6. **Never read the smem region through /dev/mem.** A 4 KiB read of 0x80900000
   wedges the SoC and needs a hard reboot. `dd` returning `EFAULT` is the
   warning, not a permissions problem.

After that, the next thing on the modem is in-call audio, which is a new driver
(q6voice / MVM-CVS-CVP), not a bug. Scoped at the end of session 14.

--------------------------------------------------------------------------------
## 0. CURRENT STATE (read this first)
--------------------------------------------------------------------------------
### The image to use

> **UPDATE (post-audio).** This §0 was written at session 4, before audio was
> solved. The current image is **`kali-boot-v92-STABLE-audio.img`** — the same
> working port plus working speaker audio (kernel patches 0001-0026 **and**
> 0032-0036, the q6asm stream-id fix `iommus = <&apps_smmu 0xa1 0x0>`). It
> supersedes v80 for daily use. See the root `README.md` ("Current image") and
> `AUDIO-SM6375.md` sessions 10-12. The rest of this §0 remains accurate for
> everything other than audio.
>
> **UPDATE 2 (sessions 14-15), and this one changes what "works" means below.**
> - The image to flash is **`kali-boot-v94-STABLE.img`**, not v92: it is v92
>   plus one device tree node reserving 8 MB for the modem's memshare region,
>   and nothing else. Kernel, ramdisk and cmdline are byte for byte identical,
>   so falling back is just reflashing v92.
> - **The "IPA / mobile data path: yes, it loads at boot" paragraph below is no
>   longer true of the shipped phone.** The IPA still works exactly as
>   described, but `ipa.ko` is deliberately kept out of the boot by
>   `/etc/modprobe.d/rhodep-ipa-hold.conf`, because with it loaded *and* the
>   modem attached to LTE the SoC watchdog-resets every 3-10 minutes. So on a
>   stock install there is no `rmnet_ipa0`, and ModemManager creates no modem at
>   all. That is the open bug, §5 sessions 14-15.
> - The modem itself is **done** and the "modem never leaves offline" item below
>   is solved.
> - `kali-boot-v97-ipa-interconnect.img` exists for working on the reset: it
>   adds the interconnect provider nodes and the IPA's three NoC paths to the
>   device tree and boots identically to v94, because both drivers are held out.
>   It is **not** the daily image, and v94 specifically is the recovery one —
>   with no provider nodes in its DT there is no modalias, so `qnoc-sm6375`
>   cannot be autoloaded whatever state it is in.

**`kali-boot-v92-STABLE-audio.img`** is the complete working port and the one to
flash: display, touch, GPU, wifi/BT, the three remoteprocs, battery, charger,
USB/OTG, thermal throttling, ramoops in the region the bootloader really
preserves, the IPA with the mobile data path up, **and speaker audio**.

Rollback: `kali-boot-v80-STABLE.img` (working port without audio), or
`kali-boot-v47.img` (the pre-everything image). The intermediate images
(v48..v91) were diagnostic or audio experiments; do not ship any of them.

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

1. ~~**The modem never leaves offline state**~~ — **SOLVED in session 14**, see
   the end of §5. The modem registers on LTE and mobile data works at ~24 Mbit/s.
   It was not a QMI service, a regulator or a memory region: the modem fetches
   run-time files from the AP over TFTP (QMI 4096) and every one of those
   requests was being answered "file not found". `userspace/modem/README.md` is
   the short version; §5 session 14 is the full log. Sessions 4c-13d below are
   kept because everything they ruled out stayed ruled out.
   Voice calls connect and SMS arrives; **in-call audio does not exist yet**
   because mainline has no q6voice (MVM/CVS/CVP), which is a separate project
   scoped at the end of session 14.
2. **OPEN AND IMPORTANT: with ipa.ko loaded AND the modem registered the SoC
   watchdog-resets every 3-10 minutes.** Either one alone is stable for as long
   as it was watched. Nothing could hit this before, because the modem was
   never on a network. Full bisection and leads at the end of §5. Until it is
   fixed the phone is not daily-usable with the modem enabled.
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

> **Superseded by session 15, below.** The first half stands: the interconnect
> driver was not needed for mobile data, and `interconnect_count = 0` was a
> correct description of the platform. The second half no longer is. Session 15
> loaded the driver, found and fixed the four bugs that had made it look
> unusable, and patches **0027, 0028 and 0046 are now in `source=`** along with
> 0044/0045/0047 which give the IPA real icc data. It still did not fix the
> watchdog reset. Read §5 session 15 before acting on this paragraph.

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
## 5. THE MODEM: from "never goes online" to registered (SOLVED, session 14)

> **Read the last subsection first** ("Session 14"). It has the answer. The
> sessions before it are the investigation that led there and, more usefully,
> the list of things that were eliminated with evidence and stayed eliminated:
> the SIM, the SIM PIN, the carrier configuration, rmtfs/EFS as the blocker,
> memshare, and any missing AP-side QMI service.
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

### Session 13: the PIN is a red herring, and memshare is the strongest lead

Re-tested end to end on a clean boot. Correcting two things this document got
wrong, and adding one concrete finding.

**The SIM PIN verifies, and it changes nothing.** ModemManager now finds the
modem on its own (the >= 2 drop-in works), reports `lock: sim-pin`, and the PIN
is accepted: `PIN1 state: 'enabled-verified'` with all 3 retries intact. The
USIM application still sits at `check-personalization-state` /
`Personalization state: 'unknown'` afterwards, and the modem still refuses to go
online. So the lock was never the blocker.

**The carrier configuration is fine, contrary to what a partial listing
suggests.** `--pdc-list-configs=software` lists 25 configs and the *tail* of that
list is all `Inactive`, which is easy to misread as "nothing is active".
Configuration 1, `PERSONAL_ARG`, is `Active`, and it matches the card in the
device (ICCID 8954 34..., Personal Argentina). PDC is not the problem, and
activating a config would only have reset the modem for nothing.

**rmtfs read-only is not the problem either.** `-r` really does mean read-only,
confirmed in rmtfs.c, but storage.c shows what that means in practice: writes go
to a per-open shadow buffer in RAM and are answered *successfully*. The modem
never sees a write error, so it cannot be stalling on one. Worth knowing before
spending a reflash on it, as nearly happened here. (`-s` is also not what it
looks like: it is not write-sync, it ties rmtfs to the mss remoteproc lifecycle.)

**The new evidence: the modem refuses every mode transition, not just online.**

	--dms-set-operating-mode=online     -> QMI error 52 DeviceNotReady
	--dms-set-operating-mode=low-power  -> QMI error 60 InvalidTransition

That second one reframes the problem. This is not "the modem declines to come
up"; the modem's own state machine never finishes initialising, so *no* mode
change is legal. It answers DMS, NAS and UIM queries, reports its IMEI and
firmware (MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133), and registers 48 QMI
services, while never becoming ready.

**What the AP does not provide.** The application processor publishes exactly
four QMI services:

	 14  Remote file system service        (rmtfs)
	 49  IPA control service
	4096 TFTP                              (tqftpserv)
	 64  Service registry locator service  (pd-mapper)

The stock device tree, recovered from `vendor_boot_b`, asks for one more thing
that mainline has no implementation of at all:

	qcom,memshare
	  qcom,client_1  client-id 0, label "modem", qcom,allocate-boot-time
	  qcom,client_2  client-id 2, label "modem"
	  qcom,client_3  client-id 1, 5 MB, qcom,allocate-on-request
	memshare_region  shared-dma-pool, no-map, 8 MB, 1 MB aligned

`qcom,allocate-boot-time` is the interesting part: the modem asks the AP for
memory *during* its bring-up. Downstream answers that over QMI service 0x34
(52). Nothing on this system does, and 52 is absent from the AP's service list.
A modem waiting for a boot-time allocation that never arrives fits every symptom
observed: services up, queries answered, state machine frozen short of ready.

This is a lead, not a proven cause, and the obvious way to settle it has been
attempted and **does not work yet**. `scripts/modem/memshare-probe.py` publishes
QMI service 52 over a raw AF_QIPCRTR socket and logs anything the modem sends to
it. Run across a cold boot, the modem stayed silent, but that result is
worthless: service 52 never actually appears in `qrtr-lookup` while the probe is
running, while rmtfs (14) does, so the announcement is being dropped and the
modem was never offered anything.

One cause of that was found and fixed, and is worth knowing about because it
fails silently in a very convincing way: the kernel fills in the node and port
of a NEW_SERVER control message from the sending socket's own address, so
publishing from a socket that has not been bound registers a server on port 0.
Everything appears to succeed. Binding to a fixed port first is necessary but
was not sufficient here.

Next move: stop hand rolling the control protocol and call libqrtr's
`qrtr_publish()` from a small C program. That path demonstrably works on this
device, since rmtfs, tqftpserv and pd-mapper all register through it. Once the
service really is visible in `qrtr-lookup` during boot, repeat the cold boot
test. If the modem then talks, the work is to answer it properly: either a
userspace daemon handing out memory from a reserved region, or a kernel driver
equivalent to downstream's msm_memshare.

### Session 13b: memshare confirmed, answered, and still not the whole story

The probe works now and the answer is unambiguous. **The modem does ask the
application processor for memory**, once, during bring-up:

	from node=0 port=7, MEM_QUERY_SIZE (0x0024) txn=1
	00 0100 2400 0e00 | 01 0400 01000000 | 10 0400 00000000

Publishing the service by hand over a raw socket never worked; using libqrtr's
`qrtr_publish()` does, and the service then shows up properly:

	52  1  0  1  16389  Dynamic Heap Memory Sharing

`userspace/modem/memshare-daemon.c` answers it, and is installed by
`userspace/modem/install.sh`. Results, in order:

1. **Answer MEM_QUERY_SIZE with size 0.** The modem accepts it, does not retry,
   and does not ask for an allocation. It also stays exactly as broken: still
   `offline`, the USIM still stops at `check-personalization-state` after the
   PIN, `--dms-set-operating-mode=online` still returns DeviceNotReady.
2. **Answer MEM_QUERY_SIZE with 5 MB.** The modem immediately follows up with an
   allocation request, which is the useful part, because it proves it genuinely
   wants the memory rather than merely a reply:

		from node=0 port=7, MEM_ALLOC_GENERIC (0x0022) txn=2
		00 0200 2200 2000
		  TLV 01 len 4: 00005000  num_bytes   = 0x500000, 5 MB
		  TLV 02 len 4: 01000000  client_id   = 1
		  TLV 03 len 4: 00000000  proc_id     = 0
		  TLV 04 len 4: 00000000  sequence_id = 0
		  TLV 10 len 1: 01        alloc_contiguous

   Answering that with a failure, which is all the daemon can do today, leaves
   the modem in the same state as before.

So memshare is real, it is now answered, and **answering it is not sufficient**.
Whether actually satisfying the allocation is sufficient is the open question,
and it is the obvious next thing to try, since it is the only part of this
exchange still missing.

**What satisfying it requires.** A physical address for 5 MB that nothing else
owns, which means a reserved region, which means a device tree change and a new
boot image. The stock tree reserves exactly that:

	memshare_region: shared-dma-pool, no-map, 8 MB, 1 MB aligned

Give the region a fixed address in `reserved-memory` so userspace can simply
hand out the number, then reply to MEM_ALLOC_GENERIC with, per downstream's
`mem_alloc_generic_resp_msg_v01`:

	TLV 0x02  result   uint16 result, uint16 error   (0,0 for success)
	TLV 0x10  sequence_id                            uint32, echo the request
	TLV 0x11  addr info array: uint8 count, then per entry
	            uint64 phy_addr, uint32 num_bytes

The daemon already builds the result TLV and has the request decoded, so this is
a contained piece of work once the region exists.

### The image that adds the region: kali-boot-v93-memshare.img

Built from `kali-boot-v92-STABLE-audio.img` by changing **only** the device
tree, so it carries no other risk: the kernel Image, the ramdisk and the cmdline
are byte for byte identical to v92, verified after repacking. The single
difference is one added node:

	memshare@8ab00000 {
		reg = <0x00 0x8ab00000 0x00 0x800000>;
		no-map;
	};

How the address was chosen: this tree has no dynamically placed reserved regions
at all, every one has a fixed address, so the free holes are stable. 0x8ab00000
sits in the 13 MB hole between `pil-gpu-ucode` (ends 0x8aa1c000) and
`pil-mpss-wlan` (starts 0x8b800000), is 1 MB aligned, and ends at 0x8b300000,
well clear of the next region.

Rebuild recipe, for the next time the device tree needs a small change without a
full kernel build: split the boot image (header at page 0, then kernel with the
DTB appended, then ramdisk, then the DTB again), `dtc -I dtb -O dts`, edit,
`dtc -I dts -O dtb`, then repack with `scripts/mkbootv2b.py`. Diff the decompiled
old and new trees before repacking; the only difference should be the change
intended.

**Do not advertise a size on an image whose device tree lacks the region.** The
daemon would hand out 0x8ab00000 to the modem while Linux is still using it as
ordinary RAM, and the modem would corrupt whatever is living there. The default
of zero exists for exactly this reason.

**Do not leave the daemon advertising a size it cannot deliver.** The shipped
unit reports zero deliberately: promising memory and then failing the allocation
is worse than saying up front there is none. Pass a size only for experiments.

Two things that made this session possible and should be done first by anyone
continuing: bring up **SSH over USB** (`userspace/usb-net/install.sh`), because
it is the only link that survives a modem restart, and remember that the SIM PIN
dialog timing out on the phone ("Error unlocking modem: Did not receive a
reply") is a *symptom* of this same bug, not a separate problem: the PIN is
accepted at the QMI level and the reply never comes because the USIM never
leaves check-personalization.

Two smaller notes for whoever picks this up:

- **The modem cannot be restarted from the AP**, so every experiment costs a
  reboot. Worse, do not try it over WiFi: the WLAN protection domain lives
  inside the modem, so stopping it drops the network connection you are working
  over.
- The ADSP (node 5) publishes **Snapdragon Sensor Core service** (400). Sensors
  are behind the SSC as expected, but the service is at least alive and
  reachable, which is more than the sensor section of KERNEL-TECHNICAL.md
  assumes.

### Session 13c: memshare satisfied in full, and it is not the blocker

`kali-boot-v93-memshare.img` was flashed, the region is real:

	8ab00000-8b2fffff : reserved        <- the 8 MB
	8b300000-8b7fffff : System RAM

and the daemon handed it over on a cold boot:

	MEM_QUERY_SIZE  -> reporting size 5242880
	MEM_ALLOC_GENERIC -> handing out 0x8ab00000 (5242880 bytes)

**The modem is completely unchanged by this.** Still `offline`, still
`DeviceNotReady`, USIM still stops at `check-personalization-state` after the
PIN. So memshare is now correctly implemented, was genuinely missing, and is
**not the cause**. Answering it is still the right thing to do, and the daemon
is kept for that reason, but it should not be expected to fix anything.

### The SIM is not the cause either, and this is worth being definite about

The obvious suspicion, that the years-old prepaid SIM with no service is to
blame, is wrong, and there is a one command proof. Power the card down so the
modem sees no SIM at all:

	--uim-sim-power-off=1   -> Card state: 'error: power-down (1)'
	--dms-set-operating-mode=online -> QMI error 52 DeviceNotReady

A modem with no SIM must still be able to power its radio; that is how any phone
shows "no service" or emergency calls. Refusing with no card present shows the
refusal has nothing to do with the card, its subscription, or its personalisation
state. The `check-personalization-state` the USIM sits in is a *consequence* of
the modem never finishing initialisation, not the cause.

**APN is a red herring at this stage too.** Plasma Mobile's "APN needs to be
configured" in the quick settings is just its UI noticing there is no data
profile; an APN only matters once the modem registers on a network, and this one
never enables its radio (`power state: off`).

### How service discovery actually works here, which invalidates one plan

There is no point trying to log "what the modem asks for". The name service is
the **kernel's** (`net/qrtr/ns.c` inside qrtr.ko); the userspace `qrtr-ns` starts
and immediately goes dormant with "nameserver already running", so stracing it
shows nothing. More importantly, `ctrl_cmd_new_lookup()` accepts lookups only
from the local node:

	/* Accept only local observers */
	if (from->sq_node != qrtr_ns.local_node)
		return -EINVAL;

The modem does not look services up. It is *told* about them as they are
announced, which is exactly why it started talking to memshare the instant the
service was published.

That suggests the systematic next experiment: rather than guessing which service
is missing, publish a range of plausible QMI service ids at boot and log which
ones the modem engages with. Anything it talks to is something it wants. The
tooling for this already exists in `scripts/modem/memshare-probe.c`; it needs to
publish a list rather than one id.

Current AP side services, for reference:

	 14  Remote file system service        (rmtfs)
	 49  IPA control service
	 52  Dynamic Heap Memory Sharing       (this port's daemon)
	 64  Service registry locator service  (pd-mapper)
	4096 TFTP                              (tqftpserv)

### Session 13d: the missing service theory is dead, systematically

Since the modem connects to whatever is announced, the way to find a missing
service is to offer everything and watch. `userspace/modem/service-probe.c`
publishes a list of ids and logs what the modem talks to, optionally answering
with a bare success.

Every id from 1 to 235 that nobody already provides was offered across two cold
boots, plus 256, 271, 312, 770, 771, 4097 and 4098. **The modem reaches out to
exactly two**, both immediately after it boots:

	service 60, msg_id 0x0020
	  TLV 0x10 (8 bytes): "msm_mpss"   the subsystem name, in ASCII
	  TLV 0x11 (4 bytes): 5000         a timeout, in ms

	service 56, msg_id 0x0024          "LOWI (Location) service"
	  TLV 0x01 (8): 1   TLV 0x02: 1   TLV 0x03: 1   TLV 0x04: 5

56 is LOWI, WiFi assisted location, which cannot plausibly gate bring-up. 60 is
not in qrtr's name table; its payload is a subsystem name and a timeout, so it
is something in the subsystem control or restart family.

**Answering both with a generic QMI success changes nothing.** Still `offline`,
still DeviceNotReady. Combined with the memshare result, that is the important
conclusion:

> The blocker is **not** a missing QMI service on the application processor
> side. That whole class of cause is eliminated, not by argument but by
> offering every id in the range and watching what happens.

### Where that leaves it

Everything reachable from the AP over QMI has now been ruled out with evidence:

- the SIM, including with the card powered off entirely
- the SIM PIN, which verifies
- the carrier configuration, PERSONAL_ARG is active and matches the card
- rmtfs and the EFS, which work, and whose read-only mode never errors
- memshare, genuinely missing, now implemented, and not the cause
- any other missing QMI service, by exhaustive sweep

What is left is below or beside QMI: the modem's own NV or calibration state,
something in the glink/smem or power sequencing it expects, or something the
secure world provides downstream. None of those are reachable with the tools
used so far, and none can be guessed at cheaply, since each attempt costs a
reflash.

The realistic next move is **not** another experiment but asking upstream. The
evidence is now specific enough to be worth someone's attention on
linux-arm-msm: a modem that boots, registers 48 QMI services, answers DMS, NAS
and UIM, reports its IMEI and firmware version, and yet rejects *every*
operating mode transition, with `InvalidTransition` even towards low-power,
which says its internal state machine never completes rather than that it
declines to come up. That last detail is the useful one and was not in this
document before.


### Session 14: SOLVED. The modem was waiting for a file, not for a service

Everything §5 ruled out really was innocent. The blocker was neither below nor
beside QMI: it was **on top of it**. `modem.mbn` is not the whole modem. At run
time the modem fetches more files from the application processor over QMI
service 4096 (TFTP), which `tqftpserv` answers, and on this port **every single
one of those requests was answered "file not found"** — silently, because
nothing was logging them.

`tqftpserv` has a `-d` flag. One boot with it, and two sessions of hunting were
over:

	 12  /readonly/firmware/image/modem_pr/so/205_0_0.mbn
	  2  /readonly/vendor/fsg/mcfg_sw/mbn_sw.dig    "invalid path, rejecting"
	  2  /readonly/vendor/fsg/mcfg_hw/mbn_hw.dig    "invalid path, rejecting"
	  4  /readwrite/datablock/id_00
	  2  /readwrite/shob.bin
	  2  /readwrite/dhob.bin
	  2  /readwrite/datablock/slid_00
	  2  /readwrite/ota_firewall/ruleset
	  1  /readwrite/mot_rfs/imei_sv

**Twelve retries for one file.** `205_0_0.mbn` is a Motorola-signed Hexagon ELF
(`e_machine` 0x00a4, 764 KiB, OEM_ID 02E8, HW_ID 02E80000) that the modem loads
into itself at run time, and it was on the device the whole time:

	mount -o ro /dev/disk/by-partlabel/modem_a /mnt
	ls /mnt/image/modem_pr/so/     # 25 signed Hexagon objects

`tqftpserv`'s `translate_readonly()` strips `/readonly/firmware/image/` and
looks under the remoteproc firmware directory, so the fix was one copy:

	cp -r /mnt/image/modem_pr /lib/firmware/qcom/sm6375/motorola/rhodep/

and a reboot. Immediately afterwards, with nothing else changed:

	--dms-get-operating-mode        -> Mode: 'shutting-down'   (was 'offline')
	--dms-set-operating-mode=online -> Operating mode set successfully
	--nas-get-system-info           -> GSM 'limited', MCC 722 MNC 34,
	                                   LAC 420, Cell ID 60858, -94 dBm

The radio was on and scanning. `DeviceNotReady` for **every** transition,
including `low-power`, was exactly what it looked like — a state machine that
never finished init — and what it was waiting for was a file.

#### Why nobody found this before

Because the three things that would have shown it were each invisible for a
different reason. `tqftpserv` logs nothing without `-d`. The one failure it
*does* print by default, `invalid path /readonly/vendor/fsg/..., rejecting`,
goes to the journal of a service nobody was reading. And the modem does not
complain: it retries, gives up, and carries on answering QMI queries perfectly,
which is what made it look half-alive rather than blocked.

The lesson worth keeping: **the modem's own file requests are a log**. Before
theorising about regulators, glink or the secure world, read what it is asking
for.

#### The rest of the vendor RFS tree

`modem_pr` alone got the radio up; two more sources were needed to get from
"radio on" to "registered". On Android the RFS tree under
`/mnt/vendor/persist/rfs/msm/mpss` and `/vendor/rfs/msm/mpss` is assembled from
three places, and all three are still on this device:

| partition | what it holds | where it has to go |
| --- | --- | --- |
| `modem_a` | `image/modem_pr/so/*.mbn`, run-time Hexagon objects | `/lib/firmware/qcom/sm6375/motorola/rhodep/modem_pr/` |
| `fsg_a` | ext4, 281 entries: carrier MCFG `.mbn`s, `mcfg_summary.xml`, `mcfg_sw/mbn_sw.dig` | `.../rhodep/fsg/` |
| `persist` | `rfs/msm/mpss/`: `cal_rfs/rf000*.bin` (RF calibration), `datablock/id_00` + `slid_00` (Motorola SIM-lock records), `shob.bin`, `dhob.bin`, `mot_rfs/imei_sv`, `ticf.bin` | `/var/lib/tqftpserv/` |

`userspace/modem/rhodep-rfs-populate` does this at first boot and is a no-op
afterwards. The persist tree is **copied, not bind mounted**: the modem writes
into it and the stock partition should stay untouched.

`/readonly/vendor/fsg/` needed a three-line patch to `tqftpserv`'s
`translate.c`, because upstream rejects that prefix before it ever touches the
filesystem. On Android the path is a symlink into the fsg partition, created by
the AOSP target package `rfs_msm_mpss_readonly_vendor_fsg_symlink`, which is
referenced from `sm6375-common/common.mk:285`. Upstream tqftpserv is vendored
under `userspace/modem/tqftpserv/` with the patch; it is built as
`tqftpserv-rhodep` in `/usr/local/bin` and selected with a drop-in so the apt
hold on the distro package stays valid.

#### A free modem-side log, which no earlier session knew existed

Two of the files the modem *writes* through RFS are its own diagnostics, plain
text, with source file and line number:

	simlock_report.txt   mot_simlock_mirror.c, 292: HMAC missmatch
	                     mot_simlock_mirror.c, 608: ERROR: failed reading data from NV
	datablock_report.txt mot_datablock.c, 148: Datablock path to validate:
	                     /readwrite/datablock/id_00
	                     mot_datablock.c, 165: ERR: Primary datablk doesn't exist
	hob_report.txt       mot_s_hob_stg.c, 229: HOB not yet created at the factory

This is the closest thing to a modem console this port has. **Caveat, learned
the hard way:** the copies taken from `persist` already contain years of history
written under Android, tagged with the firmware version of the day. The HMAC
mismatch above looks alarming and is not ours — the file is byte-identical to
the one on the persist partition, mtime 26 March. Always `diff` against persist
and check the mtime before believing an entry.

#### `rmtfs -r` really is harmful, just not where session 13 looked for it

Session 13 was right that reads and writes are consistent within one boot (the
shadow buffer), and therefore right that `-r` was not what kept the modem
offline. It is still wrong to ship it: `-r` means read-only, writes are lost at
reboot, and **the modem keeps its UIM provisioning session in NV**. The packaged
drop-in even documented `-r` as "read/write the modem's EFS partitions", which
is the opposite of what `rmtfs.c:526` does.

`install.sh` now overrides it to `-P -s`. `modemst1`, `modemst2` and `fsc` are
backed up to `_common/backups-historicos/modem-efs-20260816/` first.

#### The last blocker: nobody opened a provisioning session

With the RFS tree served and the radio on, the SIM still did not come up:

	Provisioning applications:
		Primary GW:   session doesn't exist
	Application [1]:
		Application state: 'detected'
		Personalization state: 'unknown'

and ModemManager reported `failed: sim-missing`. **This is the same symptom the
whole port read as a SIM fault for two sessions**, and it is not one. One QMI
call:

	qmicli -d qrtr://0 --uim-change-provisioning-session=\
	  "activate=yes,session-type=primary-gw-provisioning,slot=1,aid=A0000000871002FF47F00189000001FF"

	-> Application state: 'ready'
	   Personalization state: 'ready'

and 20 seconds later:

	Registration state: 'registered'   CS: 'attached'   PS: 'attached'
	MCC 722 MNC 34 'PERSONAL'          Roaming status: 'off'

This is not a workaround. On Android qcrild issues
`QMI_UIM_CHANGE_PROVISIONING_SESSION` during UIM bring-up; ModemManager 1.24
does not, because most QMI modems auto-provision the first card and this one
does not. `userspace/modem/rhodep-uim-provision` runs it before ModemManager so
MM's single probe sees a ready card. With a read/write EFS the session then
survives reboots (verified: second boot logs "already exists"), but it is still
needed for every new SIM.

The old prepaid card, for the record, behaved *better* than the daily one here,
because the modem still had an Android-era session for it in NV. That is why it
looked like a card-specific problem.

#### One real kernel bug, found by the SoC dying at the moment it worked

Setting `--set-allowed-modes="2g|3g|4g|5g"` reset the device. ramoops caught it:

	arm-smmu c600000.iommu: Unhandled context fault: fsr=0x402,
		iova=0x0c123080, fsynr=0x370002, cbfrsynra=0x4a0, cb=7
	arm-smmu c600000.iommu: FSR = 00000402 [Format=2 TF], SID=0x4a0
	androidboot.bootreason=watchdog

SID 0x4a0 is the IPA AP context bank. `ipa_data_v4_11_sm6375` reuses the
`ipa_mem_data` of the other IPA v4.11 SoCs (qcm2290/sm6115), which puts the
modem's route and filter tables in IMEM at **0x146a8000** — an address that does
not exist here. `ipa_imem_init()` therefore mapped a region the hardware never
touches and left unmapped the one it does. The correct value is in the vendor
tree, spelled out with a comment:

	blair.dtsi:3241, ipa_smmu_ap {
		iommus = <&apps_smmu 0x04A0 0x0>;
		qcom,additional-mapping =
		/* modem tables in IMEM */
		<0x0C123000 0x0C123000 0x2000>;
		qcom,ipa-q6-smem-size = <36864>;   /* 0x9000 == mainline smem_size */
	};

consistent with mainline's own `sram@c125000` ("qcom,sm6375-imem") 8 KiB above.
Fixed by `kernel/patches/0042-net-ipa-sm6375-fix-imem-address.patch`. Nothing
notices this bug until the modem actually registers, which is why it had never
been hit before. **`ipa.ko` is a module, so this needed no reflash**: rebuild,
scp the module, `depmod -a 7.2.0-rc5`, reboot.

#### Result

Cold boot, nothing typed by hand:

	mmcli -m any
	  state: registered      registration: home
	  operator name: PERSONAL   access tech: lte
	  packet service state: attached

	nmcli con add type gsm con-name personal apn datos.personal.com \
	    gsm.username datos gsm.password datos ipv4.route-metric 700

	IPv4: 100.79.53.217/30 gw 100.79.53.218 dns 181.8.8.8 mtu 1430
	IPv6: 2800:2504:6a:7643:... /64
	curl --interface qmapmux0.0 https://speed.cloudflare.com/__down?bytes=5000000
	  -> 5000000 B at 2957804 B/s (~24 Mbit/s), ping 8.8.8.8 22-63 ms

The bearer comes up multiplexed as `qmapmux0.0` over `rmnet_ipa0`. The gsm
connection is given `route-metric 700` so WiFi (600) stays the default route
and an ssh session over WLAN is not stolen by the modem.

#### Voice and SMS: calls connect, SMS arrives, in-call audio does not exist

Tested on the live network, not inferred:

	mmcli -m any --voice-create-call=number=111 -> call0 dialing
	[modem0/call0] call state changed: unknown -> dialing (outgoing-started)
	[modem0/call0] call state changed: dialing -> active
	[modem0/call0] call state changed: active -> held -> terminated

	mmcli -m any --3gpp-ussd-initiate="*111#"
	  -> "USSD terminated by network"   (a real answer, the SS path works)

A call to Personal's `*2447` was placed from the phone's dialer, **answered by
the network, and the SMS it sends back arrived** (from 811). So CS signalling,
call setup, call answer and SMS delivery all work.

**There is no audio in the call, and there cannot be yet.** The audio of a
Qualcomm voice call never touches the application processor: it goes
modem <-> ADSP <-> codec, and the AP's job is to set up the MVM/CVS/CVP sessions
over APR. Mainline has `q6afe`, `q6asm`, `q6adm`, `q6routing` and **no q6voice
at all**. It is visible in one line of this port's own boot log:

	qcom,apr ...apr_audio_svc: Adding APR/GPR dev: aprsvc:service:4:3   CORE
	qcom,apr ...apr_audio_svc: Adding APR/GPR dev: aprsvc:service:4:4   AFE
	qcom,apr ...apr_audio_svc: Adding APR/GPR dev: aprsvc:service:4:7   ASM
	qcom,apr ...apr_audio_svc: Adding APR/GPR dev: aprsvc:service:4:8   ADM

Services 3, 4, 7, 8. The voice ones are missing:

	moto techpack/audio/include/ipc/apr.h
		#define APR_SVC_ADSP_MVM  0x09
		#define APR_SVC_ADSP_CVS  0x0A
		#define APR_SVC_ADSP_CVP  0x0B

	moto techpack/audio/dsp/q6voice.c   10565 lines
		common.apr_q6_mvm = apr_register("ADSP", "MVM", ...);
		common.apr_q6_cvs = apr_register("ADSP", "CVS", ...);
		common.apr_q6_cvp = apr_register("ADSP", "CVP", ...);

So in-call audio is its own project, roughly: apr service nodes 9/10/11 in the
device tree (patch 0032 declares only 3/4/7/8), a q6voice driver speaking
MVM/CVS/CVP, VoiceMMode1/2 back-end DAIs, the routing entries in q6routing, and
the machine-driver wiring. Do not treat it as a modem bug; the modem side is
done. The `apr_voice_svc` glink channel the modem opens on its own edge is the
modem-domain half of the same thing.

#### OPEN BUG: IPA + a registered modem watchdog-resets the SoC

This is the one thing left and it is important, because it makes the phone
unusable with the modem on. It is **new only in the sense that nothing could
ever hit it before**: it needs the modem to actually be on a network, which
until session 14 it never was.

Characterised by bisection, each configuration held for as long as stated:

| ipa.ko | modem radio | result |
| --- | --- | --- |
| loaded | off (`shutting-down`) | stable, 23 min, no reset |
| **not loaded** (blacklisted) | **registered on LTE** | stable, 16.5 min, no reset |
| loaded | registered on LTE | **watchdog reset after 3-10 min**, repeatedly |

A data bearer is *not* required: it resets with the modem merely registered and
no connection up. The reset is silent — `androidboot.bootreason=watchdog` and
the ramoops console simply stops mid-line, with no fault, no panic and no stack
trace. That is the signature this port already documented in §1 for an access
to an address the SoC does not decode.

Ruled out, each with evidence:

- **System suspend.** Masking `sleep.target suspend.target hibernate.target
  hybrid-sleep.target` and confirming `/sys/power/suspend_stats` stayed at
  `success=0 fail=0` did not stop it. (It is still worth knowing that the
  device *does* suspend on screen lock, and that console output is dead during
  suspend, which is why an early theory looked so convincing.)
- **Thermal / battery.** No thermal zone above 45 C at the time, battery
  `Full` at 28 C. Resets are 185 s / 332 s / 618 s apart, not periodic.
- **The IMEM address** from patch 0042. The *original* ipa.ko, with the wrong
  address, also ends in a watchdog — it just prints an SMMU fault first. So
  0042 fixes a genuine bug and is not the cause of this one.
- **IMEM cacheability.** Patch 0043 maps IMEM with `IOMMU_MMIO`, matching
  downstream `ipa3_iommu_map(..., IOMMU_READ | IOMMU_WRITE | IOMMU_MMIO)`.
  Correct, kept, and it does not stop the resets either.

Leads, in the order they deserve:

1. **The IPA's interconnect path to IMEM. This is now the strong one**, and
   session 15 built the case for it out of three independent pieces:

   a. The reset is silent — no fault, no panic, console stops mid-line. §1 of
      this document already established that this SoC does exactly that when
      something touches an address it does not decode. It is a bus hang, not a
      software bug.

   b. The IPA demonstrably reaches into IMEM here. That is not a guess: before
      patch 0042 the SMMU caught it red-handed at `iova=0x0c123080`,
      `cbfrsynra=0x4a0` (the IPA AP context bank). And mainline only ever *maps*
      IMEM (`ipa_imem_init()`); it never tells the modem about it, so the modem
      firmware already knows the tables live there and the AP's only job is to
      make the path work.

   c. **Mainline never votes for that path on this IPA version, and the vendor
      does.** The vendor asks for three:

	blair.dtsi:3196, ipa node:
		interconnects = <&system_noc MASTER_IPA &bimc SLAVE_EBI>,
				<&system_noc MASTER_IPA &system_noc SLAVE_OCIMEM>,
				<&bimc MASTER_AMPSS_M0 &config_noc SLAVE_IPA_CFG>;
		interconnect-names = "ipa_to_ebi1", "ipa_to_imem", "appss_to_ipa";

      while `ipa_data-v4.11.c`, which SM6375 borrows from qcm2290/sm6115,
      defines only two:

	.name = "memory"   600000 / 150000
	.name = "config"    74000 /      0

      There is no "imem" entry at all. sdm845 (`ipa_data-v3.5.1.c`) has one, so
      the driver supports the path — this IPA version's data simply does not
      declare it, presumably because qcm2290 does not need it. SM6375 does.

   So: an on-chip SRAM slave that nobody has voted a path to, touched by a DMA
   master, on a SoC that resets without a word when a slave does not answer.

   The work is bounded and the pieces exist. `sm6375.c` in this directory is
   **1675 lines and complete enough**: all six providers (bimc, clk_virt,
   config_noc, mmrt_virt, mmnrt_virt, sys_noc) and it already has the nodes
   involved — `MASTER_IPA` is `qxm_ipa` at line 672, and `SLAVE_OCIMEM`,
   `SLAVE_EBI` and `SLAVE_IPA_CFG` are all present. Patches 0027 (driver), 0028
   (DT nodes) and 0029 (wire the IPA) are on disk. **0029 as written wires only
   `memory` and `config` and would have to grow the `imem` path**, and
   `ipa_data_v4_11_sm6375` needs its own `ipa_interconnect_data` with the third
   entry plus `interconnect_count = 3` instead of the current 0.

   Cost: the device tree changes need a new boot image and a flash by the user,
   unlike everything else in session 14.

   **Session 15 acted on this. Everything is built and waiting to be tested:**

   - patch 0044 gives `ipa_data_v4_11_sm6375` its own `ipa_interconnect_data`
     with three entries including "imem" (350 MBps, sdm845's figure, the only
     one mainline offers for this path) and `interconnect_count = 3`
   - patch 0045 wires the three paths into the rhodep IPA node, copied from the
     vendor and renamed to memory/imem/config. It supersedes the old 0029,
     which was written while the IPA node was still disabled and wired only two
     of the three, missing the one that matters
   - patches 0027 and 0028 (the provider and its DT nodes) moved from this
     directory into `kernel/patches/` and into `source=`
   - `CONFIG_INTERCONNECT_QCOM_SM6375=m`
   - **`kali-boot-v95-ipa-interconnect.img`**, with `README-v95.txt` next to it

   The image is built so that a wrong guess costs a reboot rather than a
   reflash: both drivers are modules and neither loads at boot (`ipa.ko` is
   held out by `rhodep-ipa-hold.conf`, `qnoc-sm6375.ko` is new and nothing
   autoloads it), and the device tree changes are inert without them. If it
   boots at all it boots exactly like v94.

   **Testing it needs the SIM that actually registers.** The dead prepaid card
   never attaches, and attaching is the trigger — see the measurement below.

#### Session 15 result: the interconnect driver works now, and it was NOT enough

Four real bugs were found and fixed by finally running the SM6375 interconnect
driver that had sat unloaded in this directory for two sessions. Every one of
them was invisible until the module was actually loaded:

1. **NULL holes in the node arrays.** The vendor numbers all 94 nodes in one
   global sequence and the driver used those macros as array indices, so
   bimc_nodes was 514 entries for 9 real nodes. `qnoc_probe()` walks every slot
   and died on the first hole. mainline keeps two numberings for exactly this
   reason - dense per bus for the binding, because the DT id doubles as the
   array index in `of_icc_xlate_onecell()`, and globally unique for `.id`,
   because `icc_node_create()` uses one IDR. Fixed the qcm2290 way.
2. **Mismatched compatibles.** DT said `qcom,sm6375-snoc` / `-cnoc`, the match
   table said `-sys-noc` / `-config-noc`. Only bimc bound, which looked like
   success.
3. **QoS writes park a CPU.** With the compatibles fixed the driver reached the
   QoS programming and a core stopped answering, NMI included:
   `After 10 seconds, these CPUS still haven't responded to the NMI: 3`.
   Patch 0046 leaves QoS as the bootloader set it. That is not a workaround:
   bandwidth votes go to the RPM and never touch those registers.
4. **Malformed device tree.** Providers declare `#interconnect-cells = <2>` and
   the IPA node wrote one cell per specifier, missing the RPM tag. fw_devlink
   walked the list with the wrong stride, read node id 20 as a phandle, and
   parked the IPA on whatever it pointed at:
   `5840000.ipa platform: wait for supplier /opp-table-cpu6`. sm6115.dtsi is
   the reference for the right shape.

After all four, on kali-boot-v97: six providers bound, no oops, IPA probes,
`IPA driver setup completed successfully`, no deferrals, and the IPA appears in
`interconnect_summary` as a consumer with tag 3 on `qxm_ipa` and the rest.

**And the SoC still resets.** Silently, console stopping mid-line, when the
modem is pushed onto LTE. So the hypothesis is not confirmed.

The most likely reason it was not enough is visible in the same summary:

	qxm_ipa                                    0            0
	  5840000.ipa               3              0            0

The IPA is registered on the path but votes **avg 0, peak 0**. mainline only
asks for bandwidth while the IPA is runtime-resumed, and drops to zero when it
suspends - but the modem reads its tables out of IMEM on its own schedule,
regardless of what the AP's runtime PM thinks. A path that exists but is voted
at zero may be no better than no path at all.

So the next thing to try is a **floor** on that path rather than a
demand-driven vote: either the provider's `keep_alive` (already true in the
descs - check whether icc-rpm actually applies it to a node with no consumer
demand), or a permanent minimum vote from the IPA on the imem path
specifically. Neither needs a device tree change, so neither needs a reflash.

If that fails too, the second lead from session 14 is untouched: the IPA local
SRAM layout in `ipa_mem_local_data`, inherited from qcm2290.

#### Session 15, the rest: three hypotheses tested, three negatives

All three were tested on the device, with the SIM that attaches, and all three
failed to stop the reset. They are kept because each is a correction verified
against the vendor and wrong to leave as it was, but **none of them is the
cause** and nobody should re-test them.

1. **The unvoted path to IMEM** (patches 0044/0045 + the interconnect driver).
   The provider works, the IPA probes, the three paths appear in
   `interconnect_summary`. Still resets.
2. **The vote being demand-driven** (patch 0047). mainline only asks for
   bandwidth while the IPA is runtime-resumed, and the modem reads IMEM on its
   own schedule, so the path sat at 0 most of the time. With the vote held
   permanently the summary shows real numbers - memory 150000/600000, imem
   0/350000, config 0/74000 - and it still resets.
3. **The IPA SRAM layout** (patch 0048). This one looked like the answer.
   `ipa_data_v4_11_sm6375` used qcm2290's `ipa_mem_local_data`, and this SoC's
   is genuinely different from `modem_hdr_proc_ctx` onwards - the modem's
   processing context is 0xac0 here and 0x200 there, there is a whole
   `stats_fnr` region qcm2290 does not have, and the modem's own region is at
   0x27d8/0x800 instead of 0x1f18/0x100c. `ipa_qmi.c:323` and `:336` hand
   IPA_MEM_MODEM and IPA_MEM_MODEM_PROC_CTX to the modem over QMI, so those
   numbers are not bookkeeping. Transcribed from the vendor's
   `ipa_4_11_mem_part`. Still resets.

#### What is actually known, after all of it

	ipa.ko loaded + modem ATTACHED to LTE      -> reset in 3-10 min, silent
	ipa.ko loaded + radio on, NOT attached     -> 22 min, no reset
	ipa.ko not loaded + modem registered       -> 16.5 min, no reset

The middle line is the sharpest tool available and it is worth restating: the
trigger is the modem **attaching**, not the radio being powered. Swapping to a
dead prepaid SIM that never gets past `not-registered-searching` turns the bug
off completely, which makes it switchable at will.

**The experiment that has never been run**, and the one to run first next time,
is the third line with LTE specifically: *modem attached to LTE with ipa.ko
never loaded*. If it still resets, the IPA is innocent and three sessions of
looking at it were misdirected. It could not be done this session because
ModemManager is what drives LTE attach and MM refuses a QMI modem with no net
port, which is the IPA's. The way around it is qmicli:
`--nas-set-system-selection-preference=lte` persists in NV now that rmtfs is
read/write, so set it, reboot with the IPA still blacklisted, then
`rhodep-uim-provision` + `--dms-set-operating-mode=online` and watch.

That was attempted at the end of session 15 and **it does not work as
described**, so here is the missing piece for whoever tries next. Setting the
selection preference to LTE and rebooting is not enough: with ipa.ko out of the
way and no ModemManager, the modem sits at `not-registered-searching` with
radio `none` indefinitely - watched for 7 minutes, and it never attaches.

The reason is that **LTE attach needs an attach APN**, which is one of the
things ModemManager configures and nothing else here does. MM reports it as
`initial bearer apn: datos.personal.com`, taken from the carrier config. So
the experiment needs the APN set by hand first:

	qmicli -d qrtr://0 --wds-get-lte-attach-parameters
	qmicli -d qrtr://0 --wds-set-lte-attach-pdn-list=...

and only then online + preference + wait. Get that working and the question
finally gets an answer: does it reset with the modem on LTE and the IPA never
loaded?

If that comes back "resets without the IPA", the next place to look is the
modem's own view of the world - the mpss memory regions, or what the vendor's
`qcom,ipa_smmu_*` context banks do that mainline's two `iommus` entries do not.
If it comes back "no reset", then it is the IPA after all and the remaining
suspects are the endpoint/GSI configuration in `ipa_gsi_endpoint_data`, which
is the last big block still inherited from qcm2290 untouched.

#### A module that autoloads by device tree is not "held out of the boot"

Worth writing down because it cost a reflash and the reasoning looked sound at
the time. v95 was built so that "a wrong guess costs a reboot, not a reflash":
both drivers are modules, neither is loaded at boot, the device tree changes
are inert without them. The first half was wrong.

`qnoc-sm6375` has `MODULE_DEVICE_TABLE(of, ...)`. **udev autoloads it the
moment it sees a device tree containing the provider nodes.** It was only
harmless on v95 because the module was copied into /lib/modules *after* that
boot. On the next boot it was already there, udev loaded it, and the version in
place still had the NULL-hole bug:

	kernel boots -> systemd at 7 s -> console stops mid-line
	no display, no WiFi, no ssh

Not a silent SoC reset this time. `qnoc_probe()` crashes inside
`icc_node_create()`, which runs holding `icc_lock`, so the dying thread never
releases it and every interconnect consumer blocks behind it — the eight CPU
nodes have `interconnects` too. The deferred probe queue stops moving and
nothing else ever probes.

Two rules follow:

- A module is only out of the boot if `/etc/modprobe.d` says so. There is now a
  `rhodep-icc-hold.conf` next to `rhodep-ipa-hold.conf`, and both explain
  themselves.
- Never leave an untested module in `/lib/modules` when the device tree that
  matches it is about to be flashed. Keep it somewhere else and `insmod` it by
  path — which is exactly what §3 of this document recommends and what was not
  done here.

Recovery is `kali-boot-v94-STABLE.img` specifically, not v95: v94's device tree
has no provider nodes at all, so there is no modalias to match and the module
cannot be autoloaded whatever state it is in.

#### Session 15: the trigger needs the modem to ATTACH, not just to be on

Measured before touching anything, on the boot that was already up: `ipa.ko`
loaded by hand, radio on via `--dms-set-operating-mode=online`, prepaid SIM
that never gets past `not-registered-searching`.

	22 minutes, no reset

against 185 s, 332 s and 618 s when the modem was registered. So the earlier
bisection line "modem registered" was the right one and can be sharpened: it is
not the radio being powered, it is the modem actually attaching to a network —
which is exactly when it installs its filter and route tables in IMEM and gives
the IPA something to read there. That is one more brick in the same wall.
2. **The IPA local SRAM layout.** `ipa_data_v4_11`'s `ipa_mem_local_data` was
   written for qcm2290/sm6115. If SM6375 partitions its IPA SRAM differently,
   the AP and the modem disagree about offsets and the modem writes outside
   the region as soon as it installs real tables. Diff it against the
   downstream `IPA_MEM_PART` table for IPA v4.11.
3. At load time the driver reports interrupts firing that it has disabled:
   `clearing disabled IPA interrupts 0x01810080` = bits 7, 19, 20, 24 =
   `UC_RX_CMD_Q_NOT_FULL`, `PIPE_YELLOW_ABOVE`, `PIPE_RED_ABOVE`,
   `GSI_IPA_IF_TLV_RCVD`. The two PIPE_*_ABOVE ones are fill-level thresholds.

The debug loop that made this tractable, and that the next session should
reuse: `ipa.ko` is a module, so the two halves can be combined at will without
a reflash. Blacklist it with `/etc/modprobe.d`, boot, bring the radio up by
hand with `qmicli --dms-set-operating-mode=online` plus
`/usr/local/sbin/rhodep-uim-provision`, confirm `registered`, and only then
`insmod ipa.ko dyndbg==p`. And log uptime to a file from a systemd unit
(`rhodep-uplog`): ssh dropping is **not** proof of a reset — the screen locking
takes WiFi down and looks identical. Check
`journalctl -b -N -k | grep -m1 "Kernel command line"` for the real
`bootreason` of each past boot.

#### One unexplained hang, not reproduced

Between the LTE fix and the voice tests the device once hung and reset with
`androidboot.bootreason=watchdog` and **no output at all** at the end of the
ramoops console — it simply stops, unlike the IPA fault which printed its SMMU
error first. It has not recurred: the calls, the USSD, the SMS and several cold
boots since have all been clean, and the last journal line before it was an
ordinary ssh logout while `drkonqi-coredump-processor` was chewing through 60
old coredumps. Recorded here so it is not forgotten, but there is nothing to
bisect yet. If it comes back, the console tail plus what was on screen at the
time is the evidence to collect.

#### Two things not to repeat

- **Do not read the smem region through /dev/mem.** `/dev/mem` on a nomap
  reserved region wedges the SoC on arm64: a 4 KiB read of 0x80900000 hung the
  process and then the whole device, needing a hard reboot. `dd` returns
  `EFAULT` first, which is a warning, not permission trouble.
- The modem's DIAG glink channels (`DIAG_DATA`, `DIAG_CMD`, ...) are **not**
  present on this edge; only `DATA1-4`, `DATA11`, `DS`, `IPCRTR`,
  `LOOPBACK_CTL_MPSS`, `SSM_RTR_MODEM_APPS`, `apr_voice_svc` and `glink_ssr`
  are. `/dev/rpmsg_ctrl2` is the modem edge and `RPMSG_CREATE_EPT_IOCTL` does
  work there (`DS` opens), so the mechanism is fine — the channels are simply
  not offered. `SSM_RTR_MODEM_APPS` has no responder in the vendor kernel
  either, so it is not a missing piece.

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
| boot images | `/opt/postmarket/out/` |
| support .deb packages | `/opt/postmarket/out/` and `packages/` in the repo |
| **ramdisk and cmdline needed to build any boot image** | `/opt/postmarket/_common/boot-artifacts/` |
| backups | `/opt/postmarket/_common/backups-historicos/` |

**Corrected 2026-09-05.** The two `img` rows said
`/opt/postmarket/kali-nethunter/img/`. **That directory does not exist**, and
following it wastes a few minutes at exactly the moment somebody is trying to
flash a recovery image. Boot images and the support `.deb`s are in
`/opt/postmarket/out/`.

### Volatile locations (in /tmp, WILL be lost, all recreatable)

- `/tmp/opencode/dl/moto` — vendor source, partial sparse clone. Recreate with:

	git clone --filter=blob:none --sparse -b fifteen \
	    https://github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375 moto
	cd moto && git sparse-checkout add arch/arm64/boot/dts/vendor/qcom \
	    include/dt-bindings drivers/interconnect techpack/audio techpack/dataipa \
	    drivers/soc/qcom drivers/remoteproc drivers/rpmsg include/soc/qcom \
	    include/linux/soc/qcom

  The last five paths were added in session 14. `techpack/dataipa` is the one
  that matters for the open IPA bug (`ipa_v3/ipa.c`, the `qcom,additional-mapping`
  handling around line 8929). Note this tree has **no** QC diag driver and no
  `ssm.c`, which is why two plausible leads died quickly.

- `/tmp/opencode/dl/{rhodep-dt,sm6375-common}` — the LineageOS device trees.
  Small, plain `--depth 1` clones, and worth having: this is where the RFS
  layout was found.

	git clone --depth 1 https://github.com/Motorola-SM6375-Devs/android_device_motorola_rhodep     rhodep-dt
	git clone --depth 1 https://github.com/Motorola-SM6375-Devs/android_device_motorola_sm6375-common sm6375-common

  Useful files: `sm6375-common/common.mk:285`
  (`rfs_msm_mpss_readonly_vendor_fsg_symlink`, which is what says
  `/readonly/vendor/fsg` is the fsg partition),
  `sm6375-common/rootdir/etc/init/hw/init.mmi.rc:103`
  (`/mnt/vendor/persist/rfs/msm/mpss/mot_rfs`, the persist RFS tree), and
  `init.qcom.rc` for the daemon list.

- `/tmp/opencode/dl/{rmtfs,tqftpserv}` — upstream of the two daemons, read to
  settle what their flags actually do. `github.com/andersson/rmtfs` (`-r` is
  read-only, `rmtfs.c:526`) and `github.com/andersson/tqftpserv` (path
  translation in `translate.c`; the repo vendors this at commit b6bb92d under
  `userspace/modem/tqftpserv/`).

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
	    /opt/postmarket/out/kali-boot-vNN.img

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
