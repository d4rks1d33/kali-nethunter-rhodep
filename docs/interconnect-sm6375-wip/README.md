# Mobile data (IPA) on SM6375 — research, and how it was actually solved

**Read `HANDOFF-SESSION4.md` first.** It has the answer. The rest of this
directory is the (long) road that led there, kept because the raw vendor data in
it is useful and because the dead ends are worth knowing about.

## TL;DR

Mobile data was blocked because the IPA device tree node used the **sdm845 /
sc7180 register layout**. From IPA v4.5 onwards the shared SRAM direct access
window moved from `reg_base + 0x7000` to `reg_base + 0x10000`, so `ipa-shared`
pointed at `0x5847000`, a hole on this SoC where **any access, even a single
32-bit read, resets the SoC instantly** with no CPU exception and no log output.

Fixing the node (see `0026-arm64-dts-qcom-rhodep-enable-ipa.patch` in
`postmarketos/linux-motorola-rhodep/`) gets:

	ipa 5840000.ipa: IPA driver initialized
	ipa 5840000.ipa: IPA driver setup completed successfully

`rmnet_ipa0` is created and ModemManager gets a data port.

## The interconnect driver was NOT needed

The earlier theory in this directory — "mobile data needs an SM6375 interconnect
provider so the IPA can vote its NoC bandwidth" — **was wrong**, and the days
spent on it are documented in `PROGRESS.md` as a cautionary tale.

The RPM keeps bimc/cnoc/snoc voted at INT_MAX on its own: rpmcc hands the icc bus
clocks off at INT_MAX and never registers them with the clock framework, so Linux
never clocks DDR on this SoC at all (`/sys/class/interconnect` does not even
exist on a working boot). `interconnect_count = 0` in the IPA power data is the
correct description of this platform.

Worse, the hand-written provider in here **resets the SoC** the moment it binds
the bimc node, for reasons that were never identified (see `PROGRESS.md` and
`VENDOR-BIMC.md`); since it turned out to be unnecessary, that was never chased
down. Patches 0027-0030 are kept for reference only and are **not** in the
kernel aport's `source=`.

## Why it took so long: there was no log

`ramoops` was at `0xd0000000`, an address the Motorola bootloader overwrites (the
generic holi/blair node is `/delete-node/`d on Motorola boards). Every early
reset was therefore invisible, and three sessions were spent guessing. The real
region is `RAMOOPS_BASE_ADDR 0xaf000000`. Details in `HANDOFF-SESSION4.md` §2,
including the fact that **systemd-pstore moves the files** to
`/var/lib/systemd/pstore/`, so `/sys/fs/pstore/` looks empty.

`HANDOFF-SESSION4.md` §3 describes the debug loop that finally cracked it:
because `CONFIG_QCOM_IPA=m`, the module can be held back at boot and loaded by
hand over ssh, so each hypothesis costs a module rebuild (~6 min) instead of a
flash cycle.

## Files

| file | what it is |
| --- | --- |
| `HANDOFF-SESSION4.md` | **the solution**, the debug method, and the next task |
| `PROGRESS.md` | full chronological log, sessions 1-4, including the dead ends |
| `0031-DIAG-net-ipa-trace-probe-steps.patch` | the instrumentation that found it (KERN_EMERG markers per probe step + a `diag_mode` parameter that probes the shared SRAM). Not in `source=`; re-add when debugging IPA |
| `AUDIT.md`, `VENDOR-BIMC.md` | interconnect research (RPM ids, the bimc reset theory). Historical |
| `DT-NODES.md`, `gen_sm6375.py`, `sm6375.c`, `qcom,sm6375.h` | the unused interconnect provider and its generator |
| `holi_nodes_dump.txt`, `holi_qos_dump.txt`, `icbid_map.txt`, `qcm2290_ports.txt` | parsed vendor NoC/QoS tables |
| `0027`-`0029` patches | the unused interconnect driver + DT nodes |

## Where the vendor source comes from

Partial (`blob:none`) sparse clone of
`github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375`, branch
`fifteen`. Add paths on demand with `git sparse-checkout add <path>` — that is how
`include/dt-bindings` (for `RAMOOPS_BASE_ADDR`) and `techpack/dataipa` (for the
IPA register offsets) were obtained.

Note that rhodep is a **blair** board, not holi: `blair-moto-rhodep-base.dts`
includes `blair.dtsi`, which is the SM6375 and pulls in the holi interconnect
bindings. Always cross-check `blair.dtsi` rather than assuming `holi.dtsi`.
