# The stock device tree and vendor GNSS configuration

**This directory is git-ignored on purpose and must not be pushed.** It is
Motorola's material, read off this phone's own factory partitions. Decompiled or
not, it is not ours to redistribute. The conclusions drawn from it are quoted in
`docs/` where they matter; those travel, this does not.

Kept on disk because it is the most useful reference artifact this port has.
Every earlier session argued about the vendor tree from hand-written
transcriptions in markdown, and at least twice those were materially wrong:
`HANDOFF.md` quoted `holi.dtsi` (pm6350/pm6150l) for a board that actually
carries pm6125/pmr735a, and the `qcom,memshare` summary omitted that
`memshare_region` is *dynamically* placed -- which makes patch 0120's fixed
address this port's choice rather than the vendor's. Grep this instead of
remembering.

## Where it came from

The phone is A/B. postmarketOS was installed into slot `_a` and **slot `_b` was
never touched**, so the factory Android partitions are still on the device.
Nothing was written; both partitions were read with `dd` over ssh.

	dtbo_a          25165824 bytes, all zero      <- wiped by the pmOS install
	dtbo_b          Android DTBO magic d7b7ab1e   <- STOCK
	boot_a          Android header v2             <- our Linux
	boot_b          Android header v3             <- stock Android
	vendor_boot_a   cmdline 'earlycon pmos_boot_uuid=...'
	vendor_boot_b   cmdline 'androidboot.console=ttyMSM0 ...'   <- STOCK

	vendor_boot_b   header v3, dtb_size 323677 at file offset 0x3a2000
	                model = "Qualcomm Technologies, Inc. Blair"
	                compatible = "qcom,blair"

	dtbo_b          4 entries, all model = "rhodep"
	                compatible = "qcom,blair-rhodep","qcom,blair-moto","qcom,blair"
	                [0] 182878 @ 0xa0     [1] 183686 @ 0x2cafe
	                [2] 182878 @ 0x59884  [3] 182878 @ 0x862e2

The four overlays differ only in board-revision fragments. Applied to the base
with `fdtoverlay`, decompiled with `dtc -I dtb -O dts`.

## Files

| file | what it is |
| --- | --- |
| `merged.dts` / `.dtb` | base + overlay 0, 21894 lines -- **the authoritative stock AP tree** |
| `base_0.dts` / `.dtb` | the SoC base out of `vendor_boot_b`, `qcom,blair` |
| `dtbo_b_0..3.dts` / `.dtb` | the four stock rhodep overlays |
| `live-mainline.dts` | `dtc -f -I fs -O dts /proc/device-tree` on the running port, for diffing |
| `vendor-gnss/etc/*.conf` | GNSS configuration from the stock `/vendor` partition |
| `vendor_boot_b.img` | the raw partition, 96 MiB |
| `dtbo_b.img`, `dtbo_a.img` | the raw DTBO partitions |
| `super_meta.bin` | LP metadata, used to find `vendor_a` inside `super` |
| `boot_a_hdr.bin` | the live boot header, for identifying the flashed image |
| `iomem.txt`, `state/` | live `/proc/iomem` and regulator/clock/genpd snapshots |

`merged.dts` is the one to grep. The rhodep overlays touch **nothing** in
`reserved-memory`, `qcom,memshare`, `qcom,msm-imem` or the mss node, so for all
of those the base is authoritative on its own.

`live-mainline.dts` needed `dtc -f`: the bootloader injects
`/chosen/mmi,bl_sigs::` and `/chosen/mmi,bl_sigs:;`, whose property names
contain `:` and `;`, and `dtc` refuses them otherwise.

`vendor-gnss/` came from the stock `vendor` logical partition, which lives
inside `super` rather than being a partition of its own. Found through the LP
metadata (slot 0, `vendor_a` at offset 3476029440, size 632545280) and mounted
read-only through a loop device, since unmounted and detached.

## What it settled

**There is no GNSS or NAV node in the AP device tree, on either side.** Grepping
`merged.dts` for `gps|gnss|nav|garden|slpi` returns exactly one hit that is not
a thermal sensor or a pinctrl string:

	merged.dts:6248   qcom,rmtfs_sharedmem@0 { ... qcom,vm-nav-path; }

and this port already renders that correctly: the live tree carries
`qcom,vmid = <0x0f 0x2b>` on `rmtfs@f3900000`, which is `QCOM_SCM_VMID_MSS_MSA`
plus `QCOM_SCM_VMID_NAV`. **The NAV VMID is not a gap and never was.**

The measurement engine, the `nav_cc` clock controller, `NAV_CE_MPU_CFG` and the
external LNA control lines (`gnss_l1_elna_ctrl`, `gnss_l2_5_elna_ctrl`) are all
inside the MPSS, named only in the modem firmware's own string table. Stock
Android takes a satellite fix on this phone with a GCC driver that has **zero**
NAV clocks -- verified against Qualcomm's own `gcc-blair.c`, which has the same
166 GCC clocks as mainline's `gcc-sm6375.c`, no more and no fewer. A missing
AP-side NAV clock cannot be the cause of the reset.

The stock mss node, in full, is worth knowing by heart:

	qcom,mss@06000000 {
		compatible = "qcom,pil-tz-generic";
		reg = <0x6000000 0x100>;
		clocks = <0x5f 0x00>;  clock-names = "xo";   /* one clock. XO. */
		vdd_cx-supply = <0x5c>;
		qcom,vdd_cx-uV-uA = <0x180 0x186a0>;         /* TURBO + 100 mA hint */
		qcom,proxy-reg-names = "vdd_cx";             /* no vdd_mx anywhere */
		memory-region = <0x93>;                      /* mpss_wlan, 256 MiB */
		qcom,proxy-timeout-ms = <0x2710>;            /* 10 s */
		qcom,pas-id = <0x04>;
		qcom,ssctl-instance-id = <0x12>;
		qcom,smem-id = <0x1a5>;
	};

One clock and one rail. Everything else is behind the TrustZone PAS boundary,
which is why so many AP-side hypotheses about this bug have come back negative.

## The vendor GNSS configuration

The useful part is what it says the AP has to provide, which is: nothing.

	gps.conf      NMEA_PROVIDER=0       NMEA is generated by the modem, not the AP
	              CAPABILITIES=0x17     SCHEDULING | MSB | MSA | ON_DEMAND_TIME
	              LPP_PROFILE=2   MODEM_TYPE=1   AGPS_CONFIG_INJECT=1
	              all RF_LOSS_* = 0     no antenna corrections
	              PPS_DEVICENAME=/dev/pps0  but DR_SYNC_ENABLED=0, never opened
	izat.conf     lowi-server and xtra-daemon ENABLED, slim_daemon DISABLED
	              SAP=MODEM_DEFAULT
	sap.conf      SENSOR_CONTROL_MODE=2  sensors are not fed to the GNSS engine

	gnss_antenna_info.conf is Qualcomm's template with the example values still
	in it and ANTENNA_INFO_VECTOR_SIZE commented out, so it is inert. Motorola
	never populated real phase-centre data.

No GPIO, LNA enable, regulator, clock or reset is named anywhere in any of them.

One thing here is still an open lead rather than a closed one: the stock vendor
partition has `/vendor/rfs/apq/gnss/`, a remote-filesystem root belonging to the
GNSS subsystem specifically, with `readwrite -> /mnt/vendor/persist/rfs/apq/gnss`
and `readonly/firmware -> /vendor/firmware_mnt`. This port has no `/rfs` tree at
all and `tqftpserv-rhodep` serves none of those prefixes.
