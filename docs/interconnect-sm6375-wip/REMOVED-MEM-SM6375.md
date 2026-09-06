# mainline reserves 32 MB less than the vendor does, and took the number from the wrong SoC

**Status: TESTED ON THE DEVICE. The discrepancy is real and the patch is
correct, but it does NOT fix the watchdog reset.**

`kali-boot-v98-removed-mem.img` was flashed and the reservation confirmed live:

	c0000000-c70fffff : reserved      (was c0000000-c50fffff)
	c7100000-f38fffff : System RAM

The GNSS reproducer still resets the SoC in under 100 ms. So this was not the
cause of that reset and, by extension, most likely not the cause of the LTE one
either. Keep the patch anyway: the vendor value is the authority on what the
secure firmware owns, handing 32 MB of it to Linux is wrong on its own terms,
and a bug that only shows up under memory pressure is exactly the kind that
would waste a future session. It is a correctness fix, not a fix for the reset.

## The discrepancy

	arch/arm64/boot/dts/qcom/sm6375.dtsi          (mainline, what this port runs)
	removed_mem: removed@c0000000 {
		reg = <0 0xc0000000 0 0x5100000>;        <-- 81 MB
		no-map;
	};

	arch/arm64/boot/dts/vendor/qcom/blair.dtsi    (vendor, Android 15 / LineageOS)
	removed_mem: removed_region@c0000000 {
		no-map;
		reg = <0x0 0xc0000000 0x0 0x7100000>;    <-- 118 MB
	};

Checked across the vendor tree
(`Motorola-SM6375-Devs/android_kernel_motorola_sm6375`, branch `fifteen`):

| SoC dtsi | removed_region size |
| --- | --- |
| **blair.dtsi — SM6375, this SoC** | **`0x7100000`** |
| holi.dtsi — the sibling | `0x7100000` |
| shima.dtsi | `0x5100000` |
| yupik.dtsi | `0x5100000` |

rhodep is a blair board: `blair-moto-rhodep-base.dts` includes `blair.dtsi`, and
no rhodep overlay overrides `removed_mem`. Mainline's value is shima's and
yupik's, not this SoC's.

**32 MB — 0xc5100000 to 0xc7100000 — that the secure firmware owns and that
Linux is currently handing out as ordinary RAM.**

## Why this is a candidate for the watchdog reset

Nothing in either device tree references `removed_mem`. It is a pure `no-map`
reservation for TrustZone, so getting it wrong produces **no probe failure, no
bus abort, no deferred probe and no log line**. There is no driver to complain.
Linux simply gets 32 MB it should not have, and whatever it puts there is
written under it by firmware that never asked.

That failure mode matches what is actually observed, point for point:

| observation | explained by |
| --- | --- |
| watchdog, not a bus abort or an SMMU fault | corrupted kernel structures hang; they do not fault |
| console stops mid-line, no panic, no oops | corruption, not an exception |
| minutes with LTE, under a second with GNSS | depends entirely on what Linux had placed in those pages |
| patches 0044-0048 each correct, none of them helped | the IPA was never the problem |
| stable when idle for an hour | those pages only get used under memory pressure |

**The argument against it:** the port has run for hours at a time, and the
modem, ADSP and CDSP all work. If 32 MB of live kernel memory were being
overwritten constantly, nothing would work at all. So if this is the cause, the
firmware must only touch that range under specific conditions — which is
consistent with a reset that needs the modem to be doing real work, but is an
assumption, not a measurement.

## How to test it, cheaply

The fix is one field in the device tree, so **no kernel rebuild is needed**.
`kali-boot-v98-removed-mem.img` was built by editing the DTB inside the running
`boot_a` and repacking it. Verified byte for byte against the image it came
from:

	header identico: True
	ramdisk identico: True (13289298 vs 13289298 bytes)
	kernel mismo tamano: True
	bytes distintos en kernel+dtb: 1

**One byte.** `0x51` -> `0x71` at offset 50908844, inside the appended DTB.
Anything that changes is caused by that byte and nothing else.

	fastboot flash boot_a kali-boot-v98-removed-mem.img
	fastboot reboot

Then run the GNSS reproducer, which is the fastest trigger available:

	sudo qmicli -d qrtr://0 --dms-set-operating-mode=online
	sudo ./scripts/rhodep-gnss-test.py 40 min

- **If the phone survives 40 seconds**, this was it, and the LTE reset is very
  likely the same bug. Confirm with `ipa.ko` and an LTE attach.
- **If it still resets in under a second**, the hypothesis is dead and the 32 MB
  are merely a correctness fix. Say so here and move on to the next steps in
  `GNSS-SM6375.md`.

Rollback is `kali-boot-CURRENT-backup.img`, which is the exact `boot_a` this was
derived from.

Verify the change took effect after booting:

	# should print 7100000
	od -An -tx1 /sys/firmware/devicetree/base/reserved-memory/removed@c0000000/reg

	# 8aa1c000-8b7fffff should no longer end at 8b7fffff as System RAM
	grep -A2 8aa1c000 /proc/iomem

## The permanent fix

`kernel/patches/0049-arm64-dts-qcom-sm6375-fix-removed-region-size.patch`,
which applies cleanly to mainline as of this writing. It is a candidate to send
upstream regardless of whether it fixes the reset: the vendor value is the
authority on what the secure firmware owns, and shima's value has no business
being in an SM6375 device tree.

## Everything else in the reservation map matches

Checked one by one against `blair.dtsi`, so that nobody has to do it twice:

	hyp_mem                 0x80000000 0x600000     ok
	xbl_aop_mem             0x80700000 0x100000     ok
	reserved_xbl_uefi       0x80880000 0x14000      ok
	smem_mem                0x80900000 0x200000     ok
	fw_mem                  0x80b00000 0x100000     ok
	splash_memory           0x85200000 0xc00000     ok
	dfps_data_memory        0x85e00000 0x100000     ok
	pil_wlan_mem            0x86500000 0x200000     ok
	pil_adsp_mem            0x86700000 0x2000000    ok
	pil_cdsp_mem            0x88700000 0x1e00000    ok
	pil_video_mem           0x8a500000 0x500000     ok
	pil_ipa_fw_mem          0x8aa00000 0x10000      ok
	pil_ipa_gsi_mem         0x8aa10000 0xa000       ok
	pil_gpu_micro_code_mem  0x8aa1a000 0x2000       ok
	pil_mpss_wlan_mem       0x8b800000 0x10000000   ok
	removed_mem             0xc0000000 0x5100000    WRONG, vendor says 0x7100000

`ramoops` differs deliberately and correctly: the vendor node at `0xd0000000` is
`/delete-node/`d on Motorola boards and the port uses `0xaf000000`, see
`HANDOFF-SESSION4.md` §2.

## A separate thing found on the way: memshare has no reserved region

Not the cause of anything today, but it is a loaded gun.
`userspace/modem/memshare-daemon.c` says, in its own comment:

	 * The region handed to the modem. It must be reserved in the device tree,
	 * or Linux will happily use it as ordinary RAM and the modem will corrupt it:
	 *     memshare@8ab00000 { reg = <0x00 0x8ab00000 0x00 0x800000>; no-map; };
	#define REGION_BASE   0x8ab00000ULL

That region **is not reserved**, on the device or in any patch in this repo:

	$ grep -A2 8aa1c000 /proc/iomem
	8aa1c000-8b7fffff : System RAM

0x8ab00000 sits squarely inside it. Today this is harmless because the daemon
runs in its minimal mode — `offer` defaults to 0, the unit starts it with no
arguments, it answers `MEM_QUERY_SIZE` with size 0 and *refuses* `MEM_ALLOC`.
Confirmed across every boot in the journal: only `MEM_QUERY_SIZE`, never an
allocation. **So memshare is not what resets the SoC.**

But the moment anyone starts it with a size argument to test real allocation —
which is exactly what the daemon's own comments describe as the next step — it
will hand the modem 8 MB of live kernel memory. Reserve the region first.

**Also worth correcting:** the root README says v94 is "`v92-STABLE-audio` plus
one device tree node reserving 8 MB for the modem's memshare region". There is
no such node in the DTB the phone is running, and no patch in `kernel/patches/`
adds one. Either v94 was built from a tree that was never committed, or the
claim is wrong. The running DTB is the authority and it has no memshare
reservation.

### Update 2026-09-05: the correction has been made, and the region was tested anyway

Three things closed here.

**The README claim is retracted at the source.** The root `README.md` banner and
`userspace/modem/README.md` both said an image reserves the memshare region;
neither does. The flashed image is `kali-boot-rhodep-clean-20260903.img`
(sha256 `64398775143f8f701af883ee6b3317c44dd7231a6dd57752f05d79d53d6c3141`,
verified byte for byte against the live `boot_a`) and it has no such node. The
reservation exists only as `kernel/patches/0120`, which has never been flashed.

**The experiment was run without one.** `rhodep_memassign.ko alloc=1` takes
5 MiB of CMA at run time and holds it for the module's lifetime, which makes the
reservation unnecessary *for the test* — nothing else can be using pages the
module owns. `qcom_scm_assign_mem()` HLOS → `QCOM_SCM_VMID_MSS_MSA` returned 0,
the daemon offered `5242880`, and the modem sent `MEM_ALLOC_GENERIC` and took
the address. The GNSS reset was unchanged. Details in
[`GNSS-SM6375.md`](GNSS-SM6375.md) and
[`kernel/diag-modules/README.md`](../../kernel/diag-modules/README.md).

The dts reservation is still the right end state: CMA keeps a cacheable linear
alias that `no-map` memory would not, and speculation through that alias cannot
be fenced from a module.

**And the vendor numbers are now first-hand.** Everything in the table above was
checked against `blair.dtsi` as published on GitHub. The **stock DTB this board
actually boots** has since been extracted from the untouched A/B slot `_b`
(`vendor_boot_b`, a 323 677-byte FDT, merged with the four `dtbo_b` overlays),
and it confirms the reservation map from the device's own firmware:

	removed_region@c0000000   reg = <0x00 0xc0000000 0x00 0x7100000>   no-map

— `0x7100000`, exactly as this document argued, from the shipped firmware rather
than from a source tree that might not correspond to it. The full stock
`reserved-memory` list matches this document's table item for item, and the
memshare region is there as expected:

	memshare_region {
		compatible = "shared-dma-pool";
		no-map;
		alloc-ranges = <0x00 0x00 0x00 0xffffffff>;
		alignment = <0x00 0x100000>;
		size = <0x00 0x800000>;
	};

Note it is **dynamically placed** — no fixed `reg`, `alloc-ranges` spanning the
whole 32-bit space, 1 MiB aligned. Patch 0120's fixed `0x8ab00000` is therefore a
*choice this port makes*, not the vendor's address, which is worth knowing before
anyone treats 0x8ab00000 as authoritative.

The rhodep overlays touch nothing in `reserved-memory`, so the base DTB is
authoritative for all of it on its own.

**These artefacts are outside the repo and `/tmp` will not survive them.** They
should be committed — suggested `docs/vendor-dt/` — while slot `_b` is still
untouched and re-extraction is still possible.
