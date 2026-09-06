#!/bin/sh
# build-kernel-headers.sh
#
# Build a linux-headers-<KVER>_*.deb (aarch64) that matches the installed
# rhodep kernel, so out-of-tree / DKMS modules (e.g. realtek rtl8188eus for
# the TP-Link RTL8188EUS USB Wi-Fi) can be compiled on-device in Kali.
#
# The pmbootstrap apk ships vmlinuz + dtbs + modules but NOT the headers.
# This produces the equivalent of /usr/src/linux-headers-<KVER> with:
#   - headers built from the SAME .config as the installed kernel,
#   - a full Module.symvers (incl. mac80211/cfg80211 symbols),
#   - aarch64-native scripts/ (modpost, fixdep),
#   - /lib/modules/<KVER>/build -> that tree.
#
# ---------------------------------------------------------------------------
# READ THIS BEFORE CHANGING THE CONFIG HANDLING BELOW.
#
# The .config this script produces determines `struct module`'s layout via
# include/generated/autoconf.h. If it differs from the running kernel's config
# in ANY member-bearing #ifdef, every module built against this tree writes its
# `struct module` members at offsets the running kernel does not read them
# from. The failure is silent and asymmetric: `init` is early in the struct so
# modules still load and run, while `exit` and `refcnt` are late, so
# delete_module() reads NULL for mod->exit, takes its `mod->init && !mod->exit`
# branch and returns EBUSY. Nothing can ever be rmmod'ed.
#
# That happened. Two earlier defects caused it, both fixed here:
#
#   1. This script used to `sed` CONFIG_DEBUG_INFO_BTF=y out of the config
#      before configuring. CONFIG_DEBUG_INFO_BTF_MODULES is `def_bool y;
#      depends on DEBUG_INFO_BTF && MODULES`, so olddefconfig then silently
#      dropped it too -- and that option contributes 24 bytes to struct module
#      (btf_data_size, btf_base_data_size, btf_data, btf_base_data) sitting
#      *between* `init` and `exit`.
#
#   2. This script configured with aarch64-linux-gnu-gcc while the kernel apk
#      is built with `LLVM=1` (Alpine clang + lld + pahole). kconfig
#      re-evaluates every `$(cc-option ...)` / `$(success ...)` macro on each
#      run, so a gcc host recomputes CC_IS_CLANG, LD_IS_LLD, AS_IS_LLVM,
#      PAHOLE_VERSION, ... and cascades those into real option changes
#      (ARM64_BTI_KERNEL, RELR, DEBUG_INFO_BTF, DYNAMIC_DEBUG, ...).
#
# So: configure with the SAME toolchain the kernel was built with (LLVM=1, the
# pmbootstrap chroot has clang 22.1.8 / lld / pahole 1.31), and then verify.
# The verification is not optional and is not advisory -- if the resulting
# .config is not byte-identical to the authoritative one, this script exits
# non-zero and produces no .deb.
#
# The authoritative config is the RUNNING kernel's own config. This kernel sets
# CONFIG_IKCONFIG_PROC=y, so `zcat /proc/config.gz` off the device IS that
# config and nothing else needs to be trusted. Fetch it with:
#
#   ssh kali@<phone> 'zcat /proc/config.gz' > config-running.aarch64
#
# ---------------------------------------------------------------------------
#
# Requirements (build host): the linux source tarball patched with the same
# patch series as the running kernel, and the kernel's own toolchain. The
# supported way is to run this inside the pmbootstrap native chroot:
#
#   pmbootstrap chroot -- sh -c 'cd /tmp/hdr && \
#       KSRC=/tmp/hdr/linux-7.2-rc5 CONFIG=/tmp/hdr/config-running.aarch64 \
#       /tmp/hdr/build-kernel-headers.sh'
#
# Usage:
#   KSRC=/path/to/linux-7.2-rc5 CONFIG=/path/to/config-running.aarch64 \
#       ./build-kernel-headers.sh
#
# Env:
#   KVER    kernel release string (default: 7.2.0-rc5)
#   KSRC    kernel source tree (already patched with the rhodep patches)
#   CONFIG  the kernel .config to use; MUST be the running kernel's config
#   LLVM    1 (default) to build with clang/lld, as the kernel apk does.
#           Set LLVM=0 to fall back to CROSS gcc -- see the warning above,
#           this will almost certainly fail the verification gate.
#   CROSS   cross-compile prefix used only when LLVM=0 (aarch64-linux-gnu-)
#   DEBVER  .deb version (default: 7.2~rc5-rhodep3)
#   OUTDIR  where to write the .deb (default: current directory)
#   JOBS    parallelism (default: nproc)

set -e

KVER="${KVER:-7.2.0-rc5}"
CROSS="${CROSS:-aarch64-linux-gnu-}"
DEBVER="${DEBVER:-7.2~rc5-rhodep3}"
LLVM="${LLVM:-1}"
JOBS="${JOBS:-$(nproc)}"
OUTDIR="${OUTDIR:-$(pwd)}"
: "${KSRC:?set KSRC to the (patched) kernel source tree}"
: "${CONFIG:?set CONFIG to the running kernel's config (zcat /proc/config.gz)}"

[ -f "$CONFIG" ] || { echo "!! CONFIG $CONFIG does not exist" >&2; exit 1; }
[ -d "$KSRC" ]   || { echo "!! KSRC $KSRC does not exist" >&2; exit 1; }

CONFIG=$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")
OUTDIR=$(cd "$OUTDIR" && pwd)

if [ "$LLVM" = "1" ]; then
    MAKEFLAGS_TC="LLVM=1"
else
    # kbuild validates LLVM out of the environment too, and rejects anything
    # that is not 1 or a version suffix, so it has to be removed entirely
    # rather than passed as LLVM=0.
    unset LLVM
    MAKEFLAGS_TC="CROSS_COMPILE=$CROSS"
    echo "!! WARNING: LLVM=0. The rhodep kernel apk is built with LLVM=1."
    echo "!! kconfig will recompute the toolchain-derived symbols and the"
    echo "!! verification gate below will very likely reject the result."
fi

echo ">> Kernel:  $KVER"
echo ">> Source:  $KSRC"
echo ">> Config:  $CONFIG"
echo ">> Toolchain: make ARCH=arm64 $MAKEFLAGS_TC"

cd "$KSRC"

# ---------------------------------------------------------------------------
# Provenance check, before anything else.
#
# The byte-diff gate further down only proves "kconfig did not change the
# config you gave it". It cannot tell that you gave it the WRONG config -- a
# config produced by a gcc host is perfectly self-consistent under gcc and
# would sail through. What actually pins the config to the running kernel is
# the compiler recorded inside it: CONFIG_CC_VERSION_TEXT is written by kconfig
# from the compiler it was last run with. If that does not match the compiler
# we are about to build with, the config did not come from this toolchain, and
# every $(cc-option)-derived symbol in it is suspect.
#
# This is the check that would have caught the original bug on day one: the
# shipped headers carried CC_VERSION_TEXT="aarch64-linux-gnu-gcc ... 13.3.0"
# while the kernel it claimed to match carried "Alpine clang version 22.1.8".
# ---------------------------------------------------------------------------
cfg_cc=$(sed -n 's/^CONFIG_CC_VERSION_TEXT="\(.*\)"$/\1/p' "$CONFIG")
if [ "$LLVM" = "1" ]; then
    real_cc=$(clang --version 2>/dev/null | head -n1)
else
    real_cc=$(${CROSS}gcc --version 2>/dev/null | head -n1)
fi
echo ">> Config was generated by: ${cfg_cc:-<unset>}"
echo ">> Building with:           ${real_cc:-<unknown>}"
if [ -z "$cfg_cc" ]; then
    echo "!! CONFIG has no CONFIG_CC_VERSION_TEXT; it is not a kconfig output."
    exit 1
fi
if [ "$cfg_cc" != "$real_cc" ]; then
    echo "!! TOOLCHAIN MISMATCH."
    echo "!! The config you passed was generated by a different compiler than"
    echo "!! the one this build would use. kconfig re-evaluates every"
    echo "!! \$(cc-option ...) macro on each run, so configuring with the wrong"
    echo "!! compiler silently changes real options -- including"
    echo "!! CONFIG_DEBUG_INFO_BTF_MODULES, which moves struct module's exit"
    echo "!! and refcnt by 24 bytes and makes every module unloadable."
    echo "!!"
    echo "!! Use the kernel's own toolchain. For rhodep that is the pmbootstrap"
    echo "!! native chroot: pmbootstrap chroot -- ... with LLVM=1."
    [ "${ALLOW_TOOLCHAIN_MISMATCH:-0}" = "1" ] || exit 1
    echo "!! ALLOW_TOOLCHAIN_MISMATCH=1 set; continuing anyway."
fi

# The authoritative config goes in verbatim. Nothing is sed'ed out of it --
# in particular CONFIG_DEBUG_INFO_BTF stays, because dropping it drops
# CONFIG_DEBUG_INFO_BTF_MODULES and changes struct module. If pahole is
# missing, olddefconfig will turn DEBUG_INFO_BTF off and verify_config() will
# catch it and stop, which is the intended behaviour: install pahole rather
# than shipping a mismatched tree.
cp "$CONFIG" .config

# olddefconfig only fills in symbols the source config does not mention. With
# the correct toolchain it is a no-op on this config; verify_config() proves
# that rather than assuming it.
make ARCH=arm64 $MAKEFLAGS_TC olddefconfig

# ---------------------------------------------------------------------------
# Verification gate.
# ---------------------------------------------------------------------------

# The set of CONFIG symbols that add or remove members of `struct module`, read
# out of the source tree itself rather than hardcoded, so this keeps working
# when the struct changes upstream.
struct_module_symbols() {
    awk '
        /^struct module \{/       { in_struct = 1 }
        in_struct && /^\};/       { exit }
        in_struct && /^#(if|elif)/ {
            while (match($0, /CONFIG_[A-Za-z0-9_]+/)) {
                print substr($0, RSTART, RLENGTH)
                $0 = substr($0, RSTART + RLENGTH)
            }
        }
    ' include/linux/module.h | sort -u
}

verify_config() {
    rc=0

    echo ">> Verifying .config against $CONFIG ..."
    if diff -u "$CONFIG" .config > /tmp/khdr-config.diff 2>&1; then
        echo "   OK: .config is byte-identical to the authoritative config."
    else
        rc=1
        echo "!! MISMATCH: kconfig changed the authoritative config."
        echo "!! $(grep -c '^[+-][^+-]' /tmp/khdr-config.diff) line(s) differ. Full diff:"
        sed -n '3,$p' /tmp/khdr-config.diff | sed 's/^/!!   /'
    fi

    # Even if the byte diff somehow passes, spell out the layout-critical
    # subset explicitly, because this is the failure that cost us a week.
    echo ">> Checking struct module layout symbols ..."
    bad=0
    for sym in $(struct_module_symbols); do
        want=$(grep -E "^($sym=|# $sym is not set)" "$CONFIG" || true)
        have=$(grep -E "^($sym=|# $sym is not set)" .config    || true)
        if [ "$want" != "$have" ]; then
            bad=1
            rc=1
            printf '!!   %-40s want[%s] got[%s]\n' \
                "$sym" "${want:-<absent>}" "${have:-<absent>}"
        fi
    done
    if [ "$bad" = 0 ]; then
        echo "   OK: all $(struct_module_symbols | wc -l) struct module" \
             "#ifdef symbols match."
    else
        echo "!! struct module would be laid out differently from the running"
        echo "!! kernel. Modules built against this tree would load but could"
        echo "!! never be rmmod'ed (delete_module returns EBUSY, refcnt is"
        echo "!! read from the wrong offset). Refusing to build."
    fi

    return $rc
}

if ! verify_config; then
    echo
    echo "!! ABORTING. No headers package was produced."
    echo "!! Most likely cause: the configuring toolchain differs from the one"
    echo "!! that built the kernel. Run this inside the pmbootstrap native"
    echo "!! chroot with LLVM=1 (clang 22.1.8, lld, pahole 1.31)."
    exit 1
fi

echo ">> Full build (generates Module.symvers)..."
make -j"$JOBS" ARCH=arm64 $MAKEFLAGS_TC \
    KBUILD_BUILD_VERSION="1-motorola-rhodep" Image modules

# A completed build re-runs syncconfig; make sure it still did not drift.
if ! verify_config; then
    echo "!! .config drifted during the build. Refusing to package."
    exit 1
fi

DEST="/tmp/khdr/usr/src/linux-headers-$KVER"
rm -rf /tmp/khdr
mkdir -p "$DEST"

echo ">> Collecting headers..."
# Makefiles, Kconfigs and shell scripts across the whole tree
find . \( -name Makefile\* -o -name Kconfig\* -o -name "*.sh" \) \
    -exec cp --parents {} "$DEST/" \; 2>/dev/null || true
cp -a include "$DEST/"
mkdir -p "$DEST/arch/arm64"
cp -a arch/arm64/include "$DEST/arch/arm64/"
cp -a arch/arm64/Makefile "$DEST/arch/arm64/" 2>/dev/null || true
cp -a scripts "$DEST/"

# ---------------------------------------------------------------------------
# Host tools have to run on the DEVICE, not on the build host.
#
# Configuring correctly requires the kernel's own toolchain, which for rhodep
# lives in the pmbootstrap Alpine chroot -- and Alpine is musl. The scripts/
# helpers built there (fixdep, modpost, ...) are linked against
# /lib/ld-musl-aarch64.so.1, which does not exist on Kali, so an on-device
# module build dies with the very unhelpful
#
#     /usr/src/linux-headers-<KVER>/scripts/basic/fixdep: not found
#
# These are plain host programs; nothing about them depends on the kernel
# config, so they can be built anywhere as long as the libc matches the device.
# Build them on a glibc aarch64 host from the same source tree and point
# HOST_TOOLS_TREE at it:
#
#   tar xzf linux-motorola-rhodep-7.2_rc5.tar.gz && cd linux-7.2-rc5
#   cp <running config> .config
#   make ARCH=arm64 olddefconfig          # drift here is irrelevant, throwaway
#   make -j8 ARCH=arm64 scripts_basic scripts scripts/mod/
#
# TARGET_INTERP is checked against every ELF actually shipped, so a forgotten
# HOST_TOOLS_TREE is a build failure rather than a confusing one on the phone.
# ---------------------------------------------------------------------------
TARGET_INTERP="${TARGET_INTERP:-/lib/ld-linux-aarch64.so.1}"

if [ -n "${HOST_TOOLS_TREE:-}" ]; then
    echo ">> Grafting device-libc host tools from $HOST_TOOLS_TREE ..."
    [ -d "$HOST_TOOLS_TREE/scripts" ] || {
        echo "!! HOST_TOOLS_TREE has no scripts/ directory"; exit 1; }
    (cd "$HOST_TOOLS_TREE/scripts" && find . -type f -perm -u+x) | while read -r f; do
        if file -b "$HOST_TOOLS_TREE/scripts/$f" 2>/dev/null | grep -q ELF; then
            cp -a "$HOST_TOOLS_TREE/scripts/$f" "$DEST/scripts/$f"
            echo "   grafted scripts/${f#./}"
        fi
    done
fi

echo ">> Checking every shipped host tool runs on the device ($TARGET_INTERP)..."
bad_interp=0
for f in $(find "$DEST/scripts" -type f -perm -u+x); do
    file -b "$f" 2>/dev/null | grep -q ELF || continue
    interp=$(readelf -l "$f" 2>/dev/null \
        | sed -n 's/.*Requesting program interpreter: \([^]]*\)\]/\1/p')
    # statically linked helpers have no interpreter and are fine
    [ -z "$interp" ] && continue
    if [ "$interp" != "$TARGET_INTERP" ]; then
        bad_interp=1
        printf '!!   %-46s needs %s\n' "scripts/${f#"$DEST"/scripts/}" "$interp"
    fi
done
if [ "$bad_interp" != 0 ]; then
    echo "!! The host tools above cannot execute on the target."
    echo "!! Build them on a $TARGET_INTERP host and pass HOST_TOOLS_TREE=."
    exit 1
fi
echo "   OK: all shipped host tools link against $TARGET_INTERP."

cp Module.symvers "$DEST/"
cp .config "$DEST/"
cp System.map "$DEST/" 2>/dev/null || true
# Keep the authoritative config next to the tree so the installed package can
# be audited later:  diff .config .config.authoritative  must be empty, and
# on the device it must also match `zcat /proc/config.gz`.
cp "$CONFIG" "$DEST/.config.authoritative"
# trim intermediates but keep the built helper binaries (modpost/fixdep)
find "$DEST/scripts" -name "*.o" -delete 2>/dev/null || true
find "$DEST" -name "*.cmd" -delete 2>/dev/null || true

# /lib/modules/<KVER>/build -> headers tree
mkdir -p "/tmp/khdr/lib/modules/$KVER"
ln -sf "/usr/src/linux-headers-$KVER" "/tmp/khdr/lib/modules/$KVER/build"
ln -sf "/usr/src/linux-headers-$KVER" "/tmp/khdr/lib/modules/$KVER/source"

# Debian metadata
mkdir -p /tmp/khdr/DEBIAN
ISIZE=$(du -sk /tmp/khdr/usr | cut -f1)
cat > /tmp/khdr/DEBIAN/control <<EOF
Package: linux-headers-$KVER
Source: linux-motorola-rhodep
Version: $DEBVER
Architecture: arm64
Maintainer: rhodep port <nethunter@localhost>
Installed-Size: $ISIZE
Depends: gcc, make, libc6-dev, bc, flex, bison, libssl-dev, libelf-dev
Provides: linux-headers, linux-headers-2.6
Section: kernel
Priority: optional
Description: Header files for Linux $KVER (Motorola rhodep, NetHunter)
 Kernel headers for $KVER aarch64, configured from the running kernel's own
 config (/proc/config.gz) and verified byte-identical to it, plus
 Module.symvers (mac80211/cfg80211). Enables DKMS modules such as realtek
 rtl8188eus. Creates /lib/modules/$KVER/build.
 .
 The config match is load-bearing, not cosmetic: a mismatched
 CONFIG_DEBUG_INFO_BTF_MODULES shifts struct module's exit/refcnt by 24 bytes
 and makes every module built against the tree impossible to rmmod.
EOF
cat > /tmp/khdr/DEBIAN/postinst <<EOF
#!/bin/sh
set -e
KVER=$KVER
SRC=/usr/src/linux-headers-\$KVER
# Create /lib/modules/<KVER>/build (and source) unconditionally. This is what
# makes a bare \`make\` and DKMS find the kernel without anyone passing -C/KDIR:
# a canonical out-of-tree Makefile defaults KDIR to /lib/modules/\$(uname -r)/build.
# mkdir -p so the symlink exists even if the headers land before the modules
# tree; a later linux-image install adds real files alongside it.
mkdir -p /lib/modules/\$KVER
ln -sf "\$SRC" /lib/modules/\$KVER/build
ln -sf "\$SRC" /lib/modules/\$KVER/source
# touch generated files so kbuild does not try to rebuild scripts on-device.
# This also stops kbuild from re-running syncconfig with the DEVICE's compiler,
# which would recompute the toolchain-derived symbols and reintroduce exactly
# the struct module mismatch this package exists to avoid.
if [ -d "\$SRC" ]; then
    find "\$SRC/include/config" "\$SRC/include/generated" -type f -exec touch {} + 2>/dev/null || true
    find "\$SRC/scripts" -type f -exec touch {} + 2>/dev/null || true
    touch "\$SRC/Module.symvers" 2>/dev/null || true
    touch "\$SRC/.config" 2>/dev/null || true
fi
# Fail loudly at install time if this package does not match the kernel it is
# being installed next to. Cheap, and the alternative is a silent EBUSY.
if [ -r /proc/config.gz ] && [ "\$(uname -r)" = "\$KVER" ]; then
    if ! zcat /proc/config.gz | diff -q - "\$SRC/.config.authoritative" >/dev/null 2>&1; then
        echo "linux-headers-\$KVER: WARNING: /proc/config.gz does not match the" >&2
        echo "  config these headers were built from. Modules built against this" >&2
        echo "  tree may be unloadable. Rebuild with scripts/build-kernel-headers.sh." >&2
    fi
fi
exit 0
EOF
chmod 0755 /tmp/khdr/DEBIAN/postinst

OUT="$OUTDIR/linux-headers-${KVER}_${DEBVER}_arm64.deb"
fakeroot dpkg-deb --build -Zxz /tmp/khdr "$OUT"
echo ">> Built $OUT"
