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
   and pre-existing problem, full evidence and leads in §5. The test SIM is a
   prepaid one with no service, so this could not be validated end to end
   either.
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
