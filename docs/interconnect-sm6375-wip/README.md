# SM6375 (rhodep) — mobile data and audio bring-up notes

**Read `HANDOFF-SESSION4.md` first**: §0 is the current state of the port (which
image to flash, what works, what does not), and the rest is how mobile data was
solved. For audio, read **`AUDIO-SM6375.md`**. The rest of this
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

**None of which the phone does today**, and that is deliberate: `ipa.ko` is held
out of the boot by `/etc/modprobe.d/rhodep-ipa-hold.conf`, because with the
module loaded *and* the modem attached to LTE the SoC watchdog-resets every 3 to
10 minutes. The IPA register-layout fix above is correct and is not the open
bug; §5 sessions 14-15 of `HANDOFF-SESSION4.md` is.

## The interconnect driver was not needed — and it works now anyway

Two separate things, and older revisions of this file ran them together.

**It was not needed for mobile data.** The earlier theory here — "mobile data
needs an SM6375 interconnect provider so the IPA can vote its NoC bandwidth" —
was wrong, and the days spent on it are in `PROGRESS.md` as a cautionary tale.
The RPM keeps bimc/cnoc/snoc voted at INT_MAX on its own: rpmcc hands the icc bus
clocks off at INT_MAX and never registers them with the clock framework, so Linux
never clocks DDR on this SoC at all. `interconnect_count = 0` in the IPA power
data was a correct description of this platform, and fixing the IPA register
layout is what actually delivered data.

**But the driver is no longer broken.** Older revisions said it "resets the SoC
the moment it binds the bimc node, for reasons that were never identified".
Session 15 identified them, all four, by finally loading the module: NULL holes
in the node arrays (the vendor's global numbering used as array indices), a
match table whose compatibles did not match the DT, QoS register writes that
park a CPU, and an `#interconnect-cells = <2>` provider being fed one cell per
specifier. On `kali-boot-v97` six providers bind, there is no oops, and the IPA
probes cleanly with the paths visible in `interconnect_summary`.

So patches **0027, 0028 and 0046 are in the kernel aport's `source=`**, not
reference material. The driver is still held out of the boot by
`/etc/modprobe.d/rhodep-icc-hold.conf` until it has been loaded by hand at least
once on a device tree that has the provider nodes — that file says why. It did
**not** fix the watchdog reset (§5 session 15), but it is real SM6375 support and
is worth upstreaming.

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
| `HANDOFF-SESSION4.md` | **current state of the port**, how the IPA was fixed, the debug method, and the one thing still open: the SoC watchdog-resets when `ipa.ko` is loaded *and* the modem is attached to LTE (§5, sessions 14-15) |
| `AUDIO-SM6375.md` | the audio bring-up. Speaker, earpiece, headphones, the microphone and jack detection all work; §6 is the state, what is ruled out with evidence, and the one rough edge left |
| `GNSS-SM6375.md` | **starting a GNSS session watchdog-resets the SoC in under a second**, with `ipa.ko` not loaded. Same silent signature as the mobile-data reset, and a five-second reproducer for it. Read before assuming GPS is a feature request |
| `REMOVED-MEM-SM6375.md` | mainline's `removed_mem` is 32 MB smaller than the vendor's, because the number came from shima/yupik instead of blair. Untested candidate for the reset, one byte to try, test image already built |
| `PROGRESS.md` | full chronological log, including the dead ends |
| `0031-DIAG-net-ipa-trace-probe-steps.patch` | the instrumentation that found it (KERN_EMERG markers per probe step + a `diag_mode` parameter that probes the shared SRAM). Not in `source=`; re-add when debugging IPA |
| `AUDIT.md`, `VENDOR-BIMC.md` | interconnect research (RPM ids, the bimc reset theory). Historical — the reset was four bugs in the driver, see above |
| `DT-NODES.md`, `gen_sm6375.py`, `qcom,sm6375.h` | the interconnect provider's generator and bindings. `sm6375.c` here is the pre-session-15 copy; the one that works is patch 0027 in `kernel/patches/` |
| `holi_nodes_dump.txt`, `holi_qos_dump.txt`, `icbid_map.txt`, `qcm2290_ports.txt` | parsed vendor NoC/QoS tables |
| `0029`, `0037`-`0041` patches | superseded diagnostics and experiments. **Not** in `source=`, and the numbers were never reused. `0029` was replaced by `0045`, which wires all three IPA paths instead of two |
| `0027`, `0028`, `0032`-`0036` patches | the copies kept here are historical: these are all **in** the kernel aport's `source=` now (interconnect provider + DT nodes, and the audio bring-up). Edit them in `kernel/patches/`, not here |

## Where the vendor source comes from

Partial (`blob:none`) sparse clone of
`github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375`, branch
`fifteen`. Add paths on demand with `git sparse-checkout add <path>` — that is how
`include/dt-bindings` (for `RAMOOPS_BASE_ADDR`) and `techpack/dataipa` (for the
IPA register offsets) were obtained.

Note that rhodep is a **blair** board, not holi: `blair-moto-rhodep-base.dts`
includes `blair.dtsi`, which is the SM6375 and pulls in the holi interconnect
bindings. Always cross-check `blair.dtsi` rather than assuming `holi.dtsi`.
