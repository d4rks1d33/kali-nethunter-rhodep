# Diagnostic modules built on the phone

Small out-of-tree modules that exist to test "does the modem need a clock or
a rail corner the application processor never asks for?" **without** a device
tree change, a kernel build or a flash.

That constraint is the point. Both questions are otherwise unaskable: no
mainline node on sm6375 references the QDSS clock, and nothing at all
references `SM6375_VDDMX` after the modem's proxy vote is released, so there is
no consumer to hang a phandle on. Adding one means a new DTB, which means a new
boot image, which means the experiment costs twenty minutes instead of two.

They build on the device, against the installed headers package:

```sh
scp -r kernel/diag-modules kali@192.168.1.53:/tmp/
ssh kali@192.168.1.53 'cd /tmp/diag-modules && make'
```

`clang`, `make` and `/lib/modules/7.2.0-rc5/build` are all present on the phone
(the headers `.deb` is there for DKMS). The Makefile passes `CC=clang` for you.
**gcc will not work any more** and that is deliberate — see the next section;
the headers package now carries the kernel's real clang-generated `.config`, so
`KBUILD_CFLAGS` contains clang-only options and gcc stops at

```
gcc: error: unrecognized command-line option '-fexperimental-late-parse-attributes'
```

Only the *compiler* is clang. GNU binutils still do the `ld -r` module link, so
`lld` and the `llvm-*` binutils do not need to be installed on the phone.

Three messages during the build are expected and harmless:

```
warning: the compiler differs from the one used to build the kernel
  The kernel was built by: Alpine clang version 22.1.8
  You are using:           Debian clang version 21.1.8 (7+b3)
warning: pahole version differs from the one used to build the kernel
  The kernel was built with: 131 / You are using: 0
Skipping BTF generation for rhodep_foo.ko due to unavailability of vmlinux
```

The config carries `CONFIG_MODULE_ALLOW_BTF_MISMATCH=y` exactly so the first
two are survivable. The third is `scripts/Makefile.modfinal` noticing the
headers package ships no `vmlinux` and skipping `pahole -J`; **pahole is not
needed on the phone.** `CONFIG_DEBUG_INFO_BTF_MODULES=y` still has to be set in
the headers `.config`, but only for its effect on `struct module`'s layout, not
to actually emit BTF.

## Why nothing could be `rmmod`ed, and what fixed it

**Fixed on 2026-09-05 by `linux-headers-7.2.0-rc5_7.2~rc5-rhodep3`. Kept here
because the failure mode is silent, generic, and will come back the moment
someone rebuilds the headers package on the wrong host.**

### The symptom

Every module on this kernel used to be un-unloadable, including a six-line
hello-world built in this same directory:

```
# insmod rhodep_hello.ko && rmmod rhodep_hello
rmmod: ERROR: could not remove 'rhodep_hello': Device or resource busy
```

with `refcnt` 0, `initstate` `live`, no holders, `CONFIG_MODULE_UNLOAD=y` in the
headers package, and `.exit = cleanup_module` present in the generated
`.mod.c`. The relocation was present and correct in the `.ko` (`readelf -r`:
`R_AARCH64_ABS64 cleanup_module + 0` at offset `0x440` into
`.gnu.linkonce.this_module`) and `.exit.text` was a normal `AX` section.
`delete_module()` was taking its `mod->init && !mod->exit` branch: the running
kernel was reading NULL out of a `__this_module` whose layout its own
`struct module` did not agree with. Marking the exit function `__exit` or not
made no difference.

### The root cause, measured

The headers package at `/lib/modules/7.2.0-rc5/build` had been configured
differently from the running image. `struct module` gains four members from
`CONFIG_DEBUG_INFO_BTF_MODULES` (`include/linux/module.h`):

```c
#ifdef CONFIG_DEBUG_INFO_BTF_MODULES
	unsigned int btf_data_size;
	unsigned int btf_base_data_size;
	void *btf_data;
	void *btf_base_data;		/* 24 bytes */
#endif
```

and they sit **between `init` and `exit`** — after the `CONFIG_BPF_EVENTS`
block, before `CONFIG_JUMP_LABEL`, with `exit` and `refcnt` further down under
`CONFIG_MODULE_UNLOAD`.

The running kernel sets `CONFIG_IKCONFIG_PROC=y` and `CONFIG_DEBUG_INFO_BTF=y`,
which gives two independent ground truths: `/proc/config.gz` is the config the
image was built from, and `/sys/kernel/btf/vmlinux` is the image's own view of
its structs. Reading the second with `pahole -C module`, against a module built
against the old headers printing its own `offsetof()`s from `init`:

| member | module (old headers) | running kernel (BTF) | delta |
| --- | --- | --- | --- |
| `init` | 304 | 304 | 0 |
| `btf_data_size` | *absent* | 1008 | — |
| `jump_entries` | 1008 | 1032 | **−24** |
| `num_jump_entries` | 1016 | 1040 | **−24** |
| `exit` | **1088** | **1112** | **−24** |
| `refcnt` | 1096 | 1120 | **−24** |
| `sizeof(struct module)` | 1152 | 1152 | 0 |

`init` is early in the struct, so modules loaded and their init functions ran
normally; `exit` is late, so `mod->exit` read as NULL and `delete_module()`
returned `EBUSY`. `mod->refcnt` was read 24 bytes low too, so module reference
counting on that headers package was meaningless.

`sizeof` matching exactly is a coincidence and is why nothing caught this
earlier: the old headers dropped **two** 24-byte blocks — the BTF block and, at
the very end of the struct, `struct _ddebug_info dyndbg_info`
(`CONFIG_DYNAMIC_DEBUG_CORE`) — and `1152 - 48 = 1104` rounds straight back up
to 1152 under `____cacheline_aligned`. Only the middle block moves `exit`.

### Why the headers were configured differently

`scripts/build-kernel-headers.sh` had two defects, both now fixed:

1. It ran `sed -i 's/CONFIG_DEBUG_INFO_BTF=y/# ... is not set/'` on the config
   before configuring, on the theory that BTF is irrelevant to module symbols.
   `CONFIG_DEBUG_INFO_BTF_MODULES` is `def_bool y; depends on DEBUG_INFO_BTF &&
   MODULES`, so `olddefconfig` then silently dropped it as well — and that is
   the option that moves `exit`.

2. More fundamentally, it configured with `aarch64-linux-gnu-gcc` while the
   kernel apk is built with `LLVM=1`. kconfig re-evaluates every
   `$(cc-option ...)` / `$(success ...)` macro on **every** run, so a gcc host
   recomputes `CC_IS_CLANG`, `LD_IS_LLD`, `AS_IS_LLVM`, `PAHOLE_VERSION` … and
   cascades those into real option changes. `diff -u /proc/config.gz` against
   the old package's `.config` is **136 changed lines**, with 61 distinct
   `CONFIG_` symbols present on only one side — not one option;
   besides the BTF pair it also dropped `CONFIG_ARM64_BTI_KERNEL`,
   `CONFIG_RELR`, `CONFIG_COMPAT_VDSO`, `CONFIG_DYNAMIC_DEBUG`,
   `CONFIG_UCLAMP_TASK`, `CONFIG_MODULE_ALLOW_BTF_MISMATCH`,
   `CONFIG_INTERCONNECT_QCOM_SM6375` and the whole `CONFIG_IP_NF_*` /
   `CONFIG_NF_CONNTRACK` group, and flipped `ZRAM_DEF_COMP` from `zstd` to
   `lzo-rle`. Only `DEBUG_INFO_BTF_MODULES` broke `rmmod`, but nothing about
   that build was trustworthy.

The decisive evidence that (2) is the whole story: with the source tree the
image was built from (the 7.2-rc5 tarball plus the 97 patches listed in the
Sep-3 `APKBUILD`'s `source=`), `/proc/config.gz` copied in verbatim, and
`make ARCH=arm64 LLVM=1 olddefconfig` run inside the pmbootstrap native chroot
(Alpine clang 22.1.8, LLD 22.1.8, pahole 1.31 — exactly the
`CONFIG_CC_VERSION_TEXT` / `CONFIG_LLD_VERSION` / `CONFIG_PAHOLE_VERSION` the
image records), `olddefconfig` is a **complete no-op**: the resulting `.config`
is byte-identical to `/proc/config.gz`. `pahole -C module` on the vmlinux built
from it gives 53 members at offsets identical to the device's own BTF.

### The fix

`scripts/build-kernel-headers.sh` now:

* takes the authoritative config verbatim and `sed`s nothing out of it;
* defaults to `LLVM=1`, and refuses to start if `CONFIG_CC_VERSION_TEXT` in the
  supplied config does not match the compiler it is about to build with — that
  single check would have caught the original bug on day one, since the old
  package carried `"aarch64-linux-gnu-gcc … 13.3.0"` while the kernel carried
  `"Alpine clang version 22.1.8"`;
* diffs the post-`olddefconfig` `.config` against the source config and **exits
  non-zero on any difference**, before and again after the build;
* separately re-checks the 26 `CONFIG_` symbols that `#ifdef` members of
  `struct module`, extracted from `include/linux/module.h` at build time rather
  than hardcoded, so the guard survives upstream changes to the struct;
* ships the authoritative config alongside as `.config.authoritative`, and the
  `postinst` warns if it does not match the `/proc/config.gz` of the kernel it
  is being installed under.

If `pahole` is missing, `olddefconfig` turns `DEBUG_INFO_BTF` off and the gate
stops the build rather than shipping a mismatched tree. That is the intended
behaviour: install pahole. (Setting `CONFIG_DEBUG_INFO_BTF_MODULES=y` without
pahole present on the *module* build host is fine and is what happens on the
phone — modules only need the matching struct layout, not real BTF data, and
`Makefile.modfinal` skips `pahole -J` by itself when there is no `vmlinux`.)

One consequence of a correct config: the headers now describe a clang-built
kernel, so on-device module builds need `CC=clang`. The Makefile does that.
Debian's clang 21.1.8 is installed on the phone and is what it uses; it is not
the Alpine clang 22.1.8 the kernel was built with, which is why
`CONFIG_MODULE_ALLOW_BTF_MISMATCH=y` matters (see the top of this file).

The old, broken tree is kept at
`/root/linux-headers-7.2.0-rc5.OLD-broken.tar.gz` on the device, so the exact
`struct module` offsets that produced the `EBUSY` can be re-measured rather than
taken on trust from this document.

**One consequence that reaches beyond `rmmod`, and is worth carrying forward:**
`mod->refcnt` was read 24 bytes low as well, so **module reference counting on
that image was untrustworthy** for the whole period the old headers were in use.
Any earlier measurement in this repo that inferred something from a refcount, or
from a resource coming back to baseline after an unload, should be re-read with
that in mind.

### Proof

Same hello-world, rebuilt against the fixed tree:

```
rhodep_hello: offsetof init = 304
rhodep_hello: CONFIG_DEBUG_INFO_BTF_MODULES=y, btf_data_size @ 1008
rhodep_hello: offsetof jump_entries = 1032
rhodep_hello: offsetof exit = 1112, refcnt = 1120      <- matches BTF exactly
# rmmod rhodep_hello ; echo $?
0
rhodep_hello: EXIT ran -- cleanup executed, rmmod worked
```

and a full take/release cycle through `rhodep_cehold`, which is the module that
did the leaking:

```
                    baseline   insmod    rmmod
    ce1_clk           ec=0      ec=1      ec=0
    ce1_a_clk         ec=0      ec=1      ec=0
    hwkm_clk          ec=0      ec=1      ec=0
    hwkm_a_clk        ec=0      ec=1      ec=0
    pka_clk           ec=0      ec=1      ec=0
    pka_a_clk         ec=0      ec=1      ec=0

    rhodep_cehold: ce1_clk     released    enable 1->0 prepare 1->0
```

and through `rhodep_reghold`, `pmr735a_l1` use/open count `0,0 -> 1,1 -> 0,0`:

```
    l1   0  0  0  unknown  576mV   0mA  576mV  648mV   <- baseline
    l1   1  1  0  unknown  600mV   0mA  576mV  648mV   <- held
    l1   0  0  0  unknown  600mV   0mA  576mV  648mV   <- after rmmod
    rhodep_reghold: released l1: enabled=0 voltage=600000 uV
```

### What this means for the modules here

`rmmod` works, exit functions run, and resources come back. The old advice —
"treat every `insmod` as permanent", use `parameters/hold` because you can
never unload — no longer applies.

`rmmod -f` should still never be used on `clkhold`, `cehold`, `pdhold` or
`reghold`: it skips the exit function by design, so the clock votes, the genpd
performance state and the regulator handle are **leaked**, `enable_count` never
comes back down, and the next measurement's baseline is silently wrong until
the phone is rebooted. That is what force-unloading did to the six CE/HWKM/PKA
clocks (2 leaked references each) and to `pmr735a_l1` (open count 2) before this
was fixed; a reboot cleared them, and as of 2026-09-05 16:35 UTC all six read
`enable_count 0 prepare_count 0` and `l1` reads `0 users, 0 open` again.

Runtime release via `parameters/hold` is still worth having — `rhodep_cehold`
and `rhodep_reghold` offer it, `rhodep_clkhold` and `rhodep_pdhold` do not — but
it is now a convenience for taking and giving back a resource inside one load,
not the only way to give it back at all.

### If it regresses

The check is two commands on the phone and needs nothing else:

```sh
zcat /proc/config.gz | diff - /lib/modules/$(uname -r)/build/.config && echo OK
grep CONFIG_DEBUG_INFO_BTF_MODULES /lib/modules/$(uname -r)/build/.config
```

An empty diff is the whole invariant. If it is not empty, rebuild the headers
with `scripts/build-kernel-headers.sh` inside the pmbootstrap chroot; do not
patch the tree by hand.

## `rhodep_clkhold` — hold RPM clocks enabled by index

```sh
sudo insmod rhodep_clkhold.ko idx=8,9      # RPM_SMD_QDSS_CLK, QDSS_A
sudo insmod rhodep_clkhold.ko compat="qcom,rpmcc-sm6375" idx=4
```

Indices are the provider's own, from
`include/dt-bindings/clock/qcom,rpmcc.h`.

**Read this before drawing a conclusion from it.** On this platform the module
is very often a no-op, and that cost a wasted test run. `clk_smd_rpm_handoff()`
in `drivers/clk/qcom/clk-smd-rpm.c` already writes an enable to RPM for
*every* clock in the SoC's table, in both the active and the sleep set, at
probe:

```c
struct clk_smd_rpm_req req = {
	.key = cpu_to_le32(r->rpm_key),
	.value = cpu_to_le32(r->branch ? 1 : INT_MAX),
};
qcom_rpm_smd_write(..., QCOM_SMD_RPM_ACTIVE_STATE, ...);
qcom_rpm_smd_write(..., QCOM_SMD_RPM_SLEEP_STATE, ...);
```

So `qdss_clk` already reads `enable Y` at 19.2 MHz in `clk_summary` with a
prepare count of zero, before this module is loaded. Voting it again changes
nothing. **Check `clk_summary` first**; if the clock is already `Y`, a null
result from this module says nothing about the hypothesis.

## `rhodep_cehold` — hold the RPM crypto-engine clocks enabled

```sh
sudo insmod rhodep_cehold.ko                 # all six
sudo insmod rhodep_cehold.ko sel=ce          # ce1_clk, ce1_a_clk only
sudo insmod rhodep_cehold.ko sel=ce,hwkm     # leave PKA alone

echo 0 | sudo tee /sys/module/rhodep_cehold/parameters/hold   # release
echo 1 | sudo tee /sys/module/rhodep_cehold/parameters/hold   # take again
```

Unlike `clkhold`, **this one is not a no-op** — check the caveat above, it does
not apply here. `CE1_CLK`, `CE1_A_CLK`, `HWKM_CLK`, `HWKM_A_CLK`, `PKA_CLK` and
`PKA_A_CLK` all sit at `enable_count 0` on a normally booted device, because
mainline sm6375 describes none of the vendor's crypto nodes
(`qcedev@1b20000`, `qcrypto@1b20000`, `hwkm@4440000`, `qrng@4453000`) and so
nothing claims them. `clk_smd_rpm_handoff()` votes them at probe and
`clk_disable_unused()` takes the vote straight back off at
`late_initcall_sync`, because `clk_smd_rpm` implements `.prepare`/`.unprepare`
and the prepare count is zero.

Measured, before and after `insmod`:

```
    ce1_clk      0  0  0  2147483647 ... firmware:scm  core
    ce1_a_clk    0  0  0  ...
    hwkm_clk     0  0  0  ...
    pka_clk      0  0  0  ...

    ce1_clk      1  1  0  2147483647 ... of_clk_get_from_provider
    ce1_a_clk    1  1  0  ...
    hwkm_clk     1  1  0  ...
    pka_clk      1  1  0  ...
```

**Do not read the "hardware enable" column for these.** It says `Y` whether they
are voted or not: `clk_smd_rpm` has no `.is_enabled` op, so `clk_core_is_enabled()`
returns true by default. The same goes for the `rate` column, which shows the
cached `INT_MAX` the driver would request, not what the RPM is doing. Only
`enable_count`/`prepare_count` mean anything here.

The single consumer `clk_summary` shows for `ce1_clk`, `firmware:scm` with
connection id `core`, is `qcom_scm` doing a `clk_get` for its optional core
clock and never a `clk_prepare_enable` — on the SMC calling convention it does
not need one.

## `rhodep_pdhold` — pin rpmpd domains at a performance state

```sh
sudo insmod rhodep_pdhold.ko idx=0,3            # SM6375_VDDCX, SM6375_VDDMX
sudo insmod rhodep_pdhold.ko idx=0,3 state=384  # TURBO instead of the maximum
```

Indices are from `include/dt-bindings/power/qcom-rpmpd.h`. `state` defaults to
`INT_MAX`, which `rpmpd_set_performance()` clamps to the provider's
`max_state` (`RPM_SMD_LEVEL_TURBO_NO_CPR` for sm6375).

It works by registering a bare platform device per domain and attaching it with
`of_genpd_add_device()`, then `pm_runtime_resume_and_get()` and
`dev_pm_genpd_set_performance_state()`. The runtime-PM get matters: rpmpd only
sends the corner to RPM if the domain is enabled --

```c
if (!pd->enabled && pd->key != KEY_FLOOR_CORNER && pd->key != KEY_FLOOR_LEVEL)
	return 0;
```

Verify it took with

```sh
cat /sys/kernel/debug/pm_genpd/cx/perf_state
cat /sys/kernel/debug/pm_genpd/mx/perf_state
```

Unlike `clkhold`, this one really does change something: CX and MX both sit at
`256` (NOMINAL) in normal operation, because mainline drops the modem's proxy
vote at handover and the only remaining votes are the AP's own consumers.

## `rhodep_reghold` — hold one regulator on, at a voltage, with a load

```sh
sudo insmod rhodep_reghold.ko name=l1              # pmr735a_l1, 600000 uV, 62000 uA
sudo insmod rhodep_reghold.ko name=l1 hold=0       # attach and report, vote nothing

echo 0 | sudo tee /sys/module/rhodep_reghold/parameters/hold   # release
echo 1 | sudo tee /sys/module/rhodep_reghold/parameters/hold   # take again
```

The rail it was written for is `pmr735a_l1`, the one rail the stock tree holds
and this port does not. Stock declares it under RPM resource `ldoe`/1 with
`qcom,init-voltage = <600000>`, `qcom,proxy-consumer-enable` and
`qcom,proxy-consumer-current = <62000>`, against an LDO whose
`qcom,hpm-min-load` is 10 mA. Mainline declares the same rail and references it
from nowhere, so it reads zero users, off, and parked at its floor.

**It needs no device tree change, and that is the interesting part.**
`regulator_dev_lookup()` falls back to `regulator_lookup_by_name()` after the DT
and board-map lookups miss, and that is a plain `class_find_device()` over the
regulator class comparing `rdev_get_name()`. With no `regulator-name` in the
dts, the name is the one in `qcom_smd-regulator.c`'s `rpm_pmr735a_regulators[]`
table. So `regulator_get_optional(NULL, "l1")` reaches `pmr735a_l1` directly.
`_optional_` matters: the plain `regulator_get()` hands back
`dummy_regulator_rdev` with only a `dev_warn` when the lookup misses, which
would look exactly like success.

The names are the PMIC driver's and are **not unique across PMICs** — this board
has a `pm6125` and a `pmr735a` and both have an `l2`..`l7`, and
`class_find_device()` returns whichever registered first. `l1` is unambiguous
because mainline declares no `pm6125_l1`. For anything else, count first:

```sh
for d in /sys/class/regulator/regulator.[0-9]*; do cat $d/name; done | sort | uniq -c
```

and lean on `guard_lo`/`guard_hi` (default 400000..900000 uV), which refuse a
rail whose present voltage is nowhere near the one requested.

Measured on the device, `hold` 0 -> 1 -> 0:

```
    l1     0  0  0  unknown  576mV   0mA  576mV  648mV     <- as found
    l1     1  2  0  unknown  600mV   0mA  576mV  648mV     <- held
       l1  1                        62mA  600mV  600mV
    l1     0  2  0  unknown  600mV   0mA  576mV  648mV     <- released
```

**`set_load` is a no-op here and the module says so.** It returned 0 and the
62 mA shows in the consumer row, but `drms_uA_update()` bails out unless
`REGULATOR_CHANGE_DRMS` is in the rail's `valid_ops_mask`, and
`of_get_regulation_constraints()` sets that only for
`regulator-allow-set-load`. So the `ma` key never reaches RPM and the LDO keeps
whatever mode RPM last gave it, instead of the high power mode the stock 62 mA
proxy forces. If the LDO's *mode* is what the modem needs, this module cannot
test it — that needs the dts, which is what `kernel/patches/0121` is for.

## `rhodep_memassign` — give the memshare region to the modem's VMID

```sh
sudo insmod rhodep_memassign.ko                 # 0x8ab00000 + 0x800000 -> MSS_MSA
sudo insmod rhodep_memassign.ko size=0x500000   # only what client 1 asks for
sudo insmod rhodep_memassign.ko alloc=1         # allocate 5 MiB here; no dts needed
sudo rmmod rhodep_memassign                     # back to HLOS
```

Unlike the two above, this one is not a substitute for a device tree change —
it is a substitute for a **driver** this port does not have. Handing the modem a
shared buffer is two steps, and only one of them can be done from userspace:

1. tell it an address (QMI service 52, `userspace/modem/memshare-daemon.c`), and
2. move the pages from `VMID_HLOS` to `VMID_MSS_MSA` so the XPU lets the modem
   write to them.

The vendor driver does the second in `shared_hyp_mapping()` →
`hyp_assign_phys(phys, size, {VMID_HLOS} → {VMID_MSS_MSA}, RW)`. There is no
ioctl, no sysfs and no debugfs for that on mainline, so a daemon cannot do it.
This module is `qcom_scm_assign_mem()`, which is the mainline spelling of the
same SMC, wrapped in module params.

`qcom_scm_assign_mem` is `EXPORT_SYMBOL_GPL` (`drivers/firmware/qcom/qcom_scm.c`
line 1424), so the module has to be `MODULE_LICENSE("GPL")` or it will not
link. The header is `<linux/firmware/qcom/qcom_scm.h>` — not
`<linux/qcom_scm.h>`, which moved — and the VMIDs come from
`<dt-bindings/firmware/qcom,scm.h>` (`QCOM_SCM_VMID_HLOS` 0x3,
`QCOM_SCM_VMID_MSS_MSA` 0xf).

**It refuses to load unless the region is really reserved.** `insmod` walks
`/reserved-memory` in the live tree and requires a `no-map` node covering
`[base, base+size)`; without one it returns `-EPERM` and says so. That check is
the whole safety of the thing: assigning memory Linux still owns hands the modem
a live kernel page, and nothing reports it. `force=1` skips the check and should
not be used.

| param | default | what it is |
| --- | --- | --- |
| `alloc` | `0` | allocate the region here instead of trusting a dts reservation |
| `base` | `0x8ab00000` | physical base, matches `kernel/patches/0120`; ignored when `alloc=1` |
| `size` | `0` | 0 means `0x500000` with `alloc=1`, `0x800000` without |
| `dma_bits` | `32` | DMA width of the allocation; 32 keeps it below 4 GiB, where stock's region is |
| `dest_vmid` | `0xf` | `QCOM_SCM_VMID_MSS_MSA` |
| `dest_perm` | `6` | `QCOM_SCM_PERM_RW`, what the vendor driver asks for |
| `hlos_perm` | `6` | permission HLOS gets back on unload |
| `revert` | `1` | give the region back to HLOS on `rmmod` |
| `force` | `0` | assign even if unreserved — do not |

### `alloc=1`: running this without a new boot image

`alloc=1` is the mode that makes the whole memshare experiment reachable on a
stock-flashed boot image. Instead of trusting a reservation that is only in the
dts, the module takes the memory out of the allocator itself and never gives it
back while loaded, which makes the reservation guard unnecessary — nothing else
can be using pages we hold.

It has to come from CMA, and the reason is worth writing down because the
obvious alternative does not work here:

```
CONFIG_ARCH_FORCE_MAX_ORDER=10   -> alloc_pages() tops out at order 10 = 4 MiB.
```

There is no order 11 on this kernel, so "round 5 MiB up to 8 MiB with
`alloc_pages`" is not available. `CONFIG_CMA=y`, `CONFIG_DMA_CMA=y` and a 40 MiB
default CMA area are, so the module registers a bare platform device and calls
`dma_alloc_pages()`, which routes through `dma_alloc_contiguous()` to
`cma_alloc()` and has no order limit. `alloc_contig_pages()` and `cma_alloc()`
could be called more directly but `dma_alloc_pages()` picks the right CMA area
by itself. Watch `CmaFree` in `/proc/meminfo`: 5 MiB out, 5 MiB back.

**What `alloc=1` cannot do, and why the dts reservation is still the right end
state.** CMA pages are ordinary low memory and the kernel's cacheable linear map
still covers them after the SCM call; a `no-map` reservation would not be mapped
at all. Two consequences:

* *Dirty lines* — handled. `dma_direct_alloc_pages()` zeroes the buffer through
  that cacheable alias, so dirty lines for those pages exist at the moment of
  handover, and a write-back landing after the ownership change is itself an XPU
  violation. The module cleans the range to the point of coherency
  (`dma_sync_single_for_device`, `DMA_TO_DEVICE`) immediately before the
  assignment.
* *Speculation* — not handled, and not handleable from a module.
  `set_memory_valid()` and `set_direct_map_invalid_noflush()` are not exported,
  so the linear alias survives and a speculative read through it could in
  principle trip the XPU. Not observed in practice across three boots, but it is
  the residual risk of doing this without a dts change.

### Ordering

Order matters and is easy to get wrong: the assignment has to be in place
**before** the modem writes, and it writes only after the daemon has told it the
address, so load this before `rhodep-memshare.service` starts.

Use `ExecStartPre=`, not `/etc/modules-load.d/`. `modules-load.d` is handled by
`systemd-modules-load.service`, which is only ordered against `sysinit.target`;
nothing guarantees it has finished before the daemon reads
`/sys/kernel/rhodep_memshare`. `ExecStartPre` is synchronous — `modprobe`
returns before `ExecStart` begins — so the region is always published first.

```ini
# /etc/systemd/system/rhodep-memshare.service.d/20-memassign-5mib.conf
[Service]
ExecStartPre=/sbin/modprobe rhodep_memassign
ExecStart=
ExecStart=/usr/local/bin/rhodep-memshare 0x500000
```

```ini
# /etc/modprobe.d/rhodep-memassign.conf
options rhodep_memassign alloc=1 size=0x500000
```

Measured on a boot with that in place — the modem's query lands at 16.2 s, the
module is up at 12.9 s:

```
[   11.662] Starting rhodep-modem-fw.service...
[   12.875] Starting rhodep-memshare.service...
[   12.926] rhodep_memassign: allocated 0xfd300000 + 0x500000 and holding it
[   12.948] rhodep_memassign: qcom_scm_assign_mem(...) returned 0
[   12.949] rhodep_memassign: published /sys/kernel/rhodep_memshare
[   12.977] memshare: published QMI service 52
[   16.040] Finished rhodep-modem-fw.service.
[   16.205] memshare: reporting size 5242880
```

A non-zero return from the SCM call is TrustZone refusing, and is a result, not
a bug in the module: it means this firmware does not accept that transition for
that region. **On this device it returns 0** — see below.

### Do not `rmmod` it while the modem is holding the buffer

Found the hard way on 2026-09-05. Unloading the module after the modem has been
given the address and has started using it wedges:

```
watchdog: BUG: soft lockup - CPU#0 stuck for 113s! [kworker/0:3]
Modules linked in: ... rhodep_memassign(O-) ...
Call trace:
  smp_call_function_many_cond -> kick_all_cpus_sync -> __text_poke
  -> bpf_arch_text_invalidate -> bpf_prog_pack_free
```

The `(O-)` is the module stuck in `delete_module`. The reverse
`qcom_scm_assign_mem()` — taking the region back from a VMID that is actively
using it — does not return, so that CPU never answers an IPI, and the first
unrelated thing needing `smp_call_function()` (here BPF JIT text poking) stalls
forever. RCU stalls, escalating soft lockups, then `bootreason=hardware_reset`
after ~6 minutes. Nothing was corrupted, but the session was lost.

`rmmod` is fine before the address has ever been handed out (take/release was
verified cleanly that way). To undo the experiment afterwards, **remove the
modprobe.d and drop-in files and reboot** — do not unload it live.

## What they have found so far

Both were written for the silent whole-SoC reset (`docs/watchdog-ipa-lte-wip/`,
`docs/interconnect-sm6375-wip/GNSS-SM6375.md`) and both came back negative:

* CX **and** MX pinned at `TURBO_NO_CPR` for the whole run: the GNSS reproducer
  still resets the SoC. Rail corners that the AP can vote for are excluded.
* QDSS clock: no change, and for the reason above the run proves nothing. QDSS
  is **not** excluded.
* **`pmr735a_l1` held on for the whole run — negative.** 2026-09-05. The rail
  went `l1 0 0 0 unknown 576mV 0mA` -> `l1 1 2 0 unknown 600mV` with `62mA` in
  the consumer row, i.e. stock's enable and stock's voltage, for the whole
  `standalone` window. **The SoC reset unchanged**, same sub-100 ms latency,
  `bootreason=watchdog`. Two caveats travel with this result and neither is
  cosmetic: the `set_load` vote almost certainly never reached RPM (see the
  module's own section above), so the LDO's **operating mode** is *not* tested
  and `kernel/patches/0121` is still the experiment that would test it; and
  stock asserts this rail at *boot*, whereas the module took it on a running
  system roughly 2300 s in, so anything that latched during modem bring-up while
  the rail was off is not undone by holding it later.
* CE1/HWKM/PKA: `rhodep_cehold` demonstrably moves them 0 -> 1, so unlike
  `clkhold` it is a real intervention. But before spending a reset on it, read
  the `clk_ignore_unused` row of the table in
  `docs/interconnect-sm6375-wip/GNSS-SM6375.md`: that cmdline makes
  `clk_disable_unused()` return before it reaches
  `clk_unprepare_unused_subtree()`, which leaves *every* `clk_smd_rpm` clock
  holding its handoff vote — these six included. The v99 run therefore already
  had CE1/HWKM/PKA voted at `INT_MAX` kHz in both the active and sleep sets, and
  still reset. The "only moved 3 clocks" caveat recorded against that run was
  measured from the hardware-enable column, which as noted above cannot see
  these clocks change at all.

* **memshare, the full stock answer, against the GNSS path — negative.**
  2026-09-05. This is the first run in which the modem was given a real buffer
  rather than being told "zero", and the first against the positioning path
  rather than LTE attach.

Negative results, but each is an elimination with an instrument rather than an
argument, which is the only kind this bug has responded to.

## The 5 MiB memshare run, 2026-09-05

The question was whether answering the modem's startup memshare query with a
real 5 MiB buffer — the number stock's `qcom,client_3` produces — changes the
reset that ends every GNSS activation. Every gate passed except the one that
mattered.

| gate | result |
| --- | --- |
| allocation | `0xfd300000 + 0x500000`, from CMA, held for the module's lifetime |
| `qcom_scm_assign_mem()` HLOS -> MSS_MSA RW | **returned 0.** `srcvm` came back `0x8000` = `BIT(0xf)` |
| offer | `memshare: reporting size 5242880`, not 0 |
| `MEM_ALLOC_GENERIC (0x0022)` | **the modem sent it**, same millisecond, and took the address |
| ordering | module at 12.9 s, daemon at 12.98 s, query at 16.2 s |
| device health | qrtr service 16 present, WiFi associated, no new errors, no SError/XPU fault in 100 s of uptime with the region assigned |
| GNSS run | `operation mode set to standalone`, `session 11 started`, **SoC reset within milliseconds** |

The modem's allocation request decodes exactly as stock's client 3:
`01` num_bytes `0x00500000`, `02` client_id `1`, `03` proc_id `0`,
`04` sequence_id `0`, `10` alloc_contiguous `1`. It never sent `MEM_FREE`.

The persistent console stops dead one line after the session starts:

```
[  102.531] MEMSHARE-5MIB-1788646360
[  115.223] rhodep-gnss: setting operation mode standalone from this client
[  115.227] rhodep-gnss: operation mode set to standalone
[  115.232] rhodep-gnss: session 11 started; listening 40 s
[  115.232] rhodep-gnss: ---------------------------------------------
```

`androidboot.bootreason=watchdog` / `powerup_reason=0x00008000`, versus
`reboot` / `0x00004000` on the clean `systemctl reboot` immediately before it —
so the indicator is calibrated, not assumed. Zero of the 40 seconds ran. No
`dmesg-ramoops` was produced, so it was not a kernel panic: the SoC went down
underneath Linux, which is the same signature as every previous GNSS run.

Reproduced across two boots (`0xfd300000` and `0xfd200000`); the second boot
after the reset came up and answered 5 MiB again with no intervention.

**Conclusion: the missing memshare heap is not the cause of the GNSS reset.**
This is a strong elimination rather than a weak one, because unlike the earlier
"answer 0" experiments the modem visibly accepted the buffer — it asked for it,
was given a real physical address it legally owns, and the SoC then died at
exactly the same point and just as fast as it does with the offer at 0. Nothing
about the timing changed. A control run with the module unloaded was therefore
not spent; the pre-existing 0-offer behaviour is the control, and it is
identical.

What this does *not* eliminate: the buffer was CMA-backed rather than a `no-map`
reservation, so a cacheable linear alias survived (see above). If a future run
with `kernel/patches/0120` flashed behaves differently, that difference would be
about the alias, not about the size of the answer.
