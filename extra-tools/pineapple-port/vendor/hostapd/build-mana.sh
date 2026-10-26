#!/bin/sh
# build-mana.sh -- install/build hostapd-mana (karma + WPE EAP) for the port.
#
# Part of: extra-tools/pineapple-port/vendor/hostapd/
#
# Provides the patched hostapd the rogue-AP core needs:
#   - karma / MANA  : evil-twin that answers probes and associates clients
#   - WPE EAP       : WPA2-Enterprise credential harvesting (PineAPE Enterprise)
#
# TWO install strategies, auto-selected:
#   (1) APT (preferred on Kali/NetHunter arm64): `apt install hostapd-mana`.
#       hostapd-mana is in Kali main, arch "any" (pkg.kali.org), so it installs
#       directly on the G82. We just point our names at the packaged binaries.
#   (2) SOURCE fallback (any host; cross or native): clone sensepost/hostapd-mana
#       and build with vendor/hostapd/mana.config. Cross-compiles to aarch64 when
#       CROSS_COMPILE / a *-gcc is given.
#
# OUTPUT (both strategies): two binaries on the target
#   /usr/sbin/hostapd-mana        the mana hostapd
#   /usr/sbin/hostapd_cli-mana    the mana hostapd_cli (REAL one; the Hak5-named
#                                 wrapper compat/hostapd_cli calls this)
#
# USAGE:
#   build-mana.sh install            # try apt, else build from source, install
#   build-mana.sh build   [OUTDIR]   # build from source only (no install)
#   build-mana.sh apt                # apt path only
#   build-mana.sh --check            # report what strategy would be used
#
# ENV:
#   MANA_REPO     git url     (default https://github.com/sensepost/hostapd-mana)
#   MANA_REF      git ref     (default master)
#   CROSS_COMPILE e.g. aarch64-linux-gnu-   (for from-source cross build)
#   CC            compiler    (default ${CROSS_COMPILE}gcc or gcc)
#   DESTDIR_SBIN  install dir (default /usr/sbin)
#   JOBS          make -j     (default: nproc)
#
# This script NEVER touches the host Wi-Fi; it only installs/builds software.

set -eu

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MANA_REPO="${MANA_REPO:-https://github.com/sensepost/hostapd-mana}"
MANA_REF="${MANA_REF:-master}"
DESTDIR_SBIN="${DESTDIR_SBIN:-/usr/sbin}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"
CC="${CC:-${CROSS_COMPILE:-}gcc}"

log()  { echo "[build-mana] $*"; }
warn() { echo "[build-mana] WARNING: $*" >&2; }
die()  { echo "[build-mana] ERROR: $*" >&2; exit 1; }

# --- PREBUILT strategy -----------------------------------------------------
# A validated aarch64 build of hostapd-mana (karma + EAP + WPE) is shipped under
# prebuilt/<arch>/. If the target arch matches and the runtime libs are present,
# install those directly (no apt, no compiler needed on the phone).
prebuilt_dir() {
    a="$(uname -m 2>/dev/null || echo unknown)"
    case "$a" in
        aarch64|arm64) echo "$SELF_DIR/prebuilt/aarch64" ;;
        *) echo "" ;;
    esac
}

prebuilt_available() {
    d="$(prebuilt_dir)"
    [ -n "$d" ] && [ -x "$d/hostapd-mana" ] && [ -x "$d/hostapd_cli-mana" ]
}

prebuilt_install() {
    d="$(prebuilt_dir)"
    log "installing shipped prebuilt hostapd-mana from $d"
    install -D -m 0755 "$d/hostapd-mana"     "$DESTDIR_SBIN/hostapd-mana"
    install -D -m 0755 "$d/hostapd_cli-mana" "$DESTDIR_SBIN/hostapd_cli-mana"
    log "installed prebuilt mana binaries into $DESTDIR_SBIN"
}

# --- APT strategy ----------------------------------------------------------
apt_available() {
    command -v apt-get >/dev/null 2>&1 || return 1
    # hostapd-mana is a Kali package; probe the candidate.
    apt-cache policy hostapd-mana 2>/dev/null | grep -q 'Candidate:' &&
        ! apt-cache policy hostapd-mana 2>/dev/null | grep -q 'Candidate: (none)'
}

apt_install() {
    log "installing hostapd-mana via apt"
    apt-get update || warn "apt-get update failed (continuing)"
    apt-get install -y hostapd-mana || die "apt install hostapd-mana failed"
    # Kali's package installs /usr/sbin/hostapd-mana and /usr/sbin/hostapd_cli-mana
    # (it renames to avoid clashing with stock hostapd). Normalise our names.
    _link_packaged
}

# Resolve a symlink chain to its final real file WITHOUT following it through our
# own translating wrapper.  Returns the canonical path (or "" if it cannot be
# resolved).  Used to make sure the CLI we adopt is the genuine mana ELF and not
# (directly or transitively) compat/hostapd_cli.
_canon() { readlink -f "$1" 2>/dev/null || true; }

# True if $1 is (or resolves to) our Hak5->mana translating wrapper. The wrapper
# must NEVER become hostapd_cli-mana, or the wrapper would call itself -> an
# infinite fork loop ("Cannot fork"). Detect it by canonical path tail and by a
# signature line only the wrapper contains.
_is_our_wrapper() {
    _t="$(_canon "$1")"
    [ -n "$_t" ] || _t="$1"
    case "$_t" in
        */compat/hostapd_cli) return 0 ;;
    esac
    # content probe (covers copies/renames): the wrapper carries this marker.
    if [ -f "$_t" ] && grep -q 'Hak5-vocabulary shim over hostapd-mana' "$_t" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Find the mana binaries the package dropped and expose our canonical names.
_link_packaged() {
    hp=""
    for c in /usr/sbin/hostapd-mana /usr/bin/hostapd-mana \
             /usr/lib/hostapd-mana/hostapd /usr/sbin/hostapd; do
        [ -x "$c" ] && { hp="$c"; break; }
    done
    [ -n "$hp" ] || die "hostapd-mana binary not found after apt install"

    # Locate the REAL mana hostapd_cli. CRITICAL: skip any candidate that is (or
    # resolves to) our own wrapper, otherwise hostapd_cli-mana would point back at
    # compat/hostapd_cli and the wrapper would recurse into itself -> "Cannot
    # fork".  Also skip our already-published hostapd_cli-mana unless it is a
    # genuine ELF (so re-runs stay idempotent and self-healing).
    hc=""
    for c in "$DESTDIR_SBIN/hostapd_cli.stock-mana" \
             /usr/sbin/hostapd_cli-mana /usr/bin/hostapd_cli-mana \
             /usr/sbin/hostapd_cli /usr/bin/hostapd_cli; do
        [ -x "$c" ] || continue
        _is_our_wrapper "$c" && { log "skipping $c (it is our wrapper, not mana cli)"; continue; }
        hc="$c"; break
    done
    [ -n "$hc" ] || die "hostapd_cli (mana) binary not found after apt install"

    # hostapd-mana: a symlink is fine (it is never invoked by bare name from the
    # wrapper, and nothing claims /usr/sbin/hostapd-mana later).
    [ "$hp" = "$DESTDIR_SBIN/hostapd-mana" ] || ln -sfn "$hp" "$DESTDIR_SBIN/hostapd-mana"

    # hostapd_cli-mana: install a REAL REGULAR FILE (copy of the resolved ELF),
    # NOT a symlink.  A symlink here is dangerous: install.sh step 4 claims
    # /usr/sbin/hostapd_cli for the wrapper, so any symlink hostapd_cli-mana ->
    # .../hostapd_cli would silently start resolving to the wrapper and loop.
    # Copying the resolved target pins it to the mana ELF for good.
    hc_real="$(_canon "$hc")"; [ -n "$hc_real" ] || hc_real="$hc"
    _is_our_wrapper "$hc_real" && die "refusing: resolved mana cli '$hc_real' is our wrapper (would loop)"
    if [ "$hc_real" != "$DESTDIR_SBIN/hostapd_cli-mana" ]; then
        # remove any stale symlink first, then copy the real binary in place.
        rm -f "$DESTDIR_SBIN/hostapd_cli-mana"
        cp -f "$hc_real" "$DESTDIR_SBIN/hostapd_cli-mana"
        chmod 0755 "$DESTDIR_SBIN/hostapd_cli-mana"
    fi
    log "mana hostapd    -> $DESTDIR_SBIN/hostapd-mana  ($hp)"
    log "mana hostapd_cli-> $DESTDIR_SBIN/hostapd_cli-mana (copied from $hc_real)"

    # The apt package (and a stock hostapd) install a REAL /usr/sbin/hostapd_cli
    # that speaks ONLY mana verbs (MANA_*/WPE_*), not the Hak5 verbs pineap-ng /
    # the panel send (karma_*/pineape_*).  If such a binary is left on $PATH it
    # can shadow our translating wrapper for any caller that resolves
    # `hostapd_cli` by bare name with /usr/sbin ahead of the wrapper -> mana then
    # replies "Unknown command 'karma_enable'".  We have already copied the real
    # cli to hostapd_cli-mana, so retire the stock name (only the wrapper, which
    # install.sh symlinks here, should answer /usr/sbin/hostapd_cli).
    if [ -e "$DESTDIR_SBIN/hostapd_cli" ] && [ ! -L "$DESTDIR_SBIN/hostapd_cli" ]; then
        mv -f "$DESTDIR_SBIN/hostapd_cli" "$DESTDIR_SBIN/hostapd_cli.stock-mana" 2>/dev/null \
            && log "moved stock $DESTDIR_SBIN/hostapd_cli -> hostapd_cli.stock-mana (shadow-safe)"
    fi
}

# --- SOURCE strategy -------------------------------------------------------
build_from_source() {
    OUT="${1:-$SELF_DIR/build}"
    command -v git  >/dev/null 2>&1 || die "git required for source build"
    command -v make >/dev/null 2>&1 || die "make required for source build"
    command -v "$CC" >/dev/null 2>&1 || die "compiler '$CC' not found (set CC/CROSS_COMPILE)"

    mkdir -p "$OUT"
    if [ ! -d "$OUT/hostapd-mana/.git" ]; then
        log "cloning $MANA_REPO ($MANA_REF)"
        git clone --depth 1 --branch "$MANA_REF" "$MANA_REPO" "$OUT/hostapd-mana" \
            || git clone "$MANA_REPO" "$OUT/hostapd-mana"
    fi
    src="$OUT/hostapd-mana/hostapd"
    [ -d "$src" ] || die "unexpected tree: $src missing"

    cp "$SELF_DIR/mana.config" "$src/.config"
    log "building hostapd + hostapd_cli with CC=$CC (jobs=$JOBS)"
    # libnl / openssl headers must be present for the target. On a cross build
    # point PKG_CONFIG / CFLAGS at the sysroot as needed (documented in README).
    make -C "$src" clean >/dev/null 2>&1 || true
    make -C "$src" -j"$JOBS" CC="$CC" hostapd hostapd_cli

    install -D -m 0755 "$src/hostapd"     "$OUT/hostapd-mana"
    install -D -m 0755 "$src/hostapd_cli" "$OUT/hostapd_cli-mana"
    log "built: $OUT/hostapd-mana  $OUT/hostapd_cli-mana"
    echo "$OUT"
}

install_from_source() {
    OUT="$(build_from_source "$SELF_DIR/build")"
    install -D -m 0755 "$OUT/hostapd-mana"     "$DESTDIR_SBIN/hostapd-mana"
    install -D -m 0755 "$OUT/hostapd_cli-mana" "$DESTDIR_SBIN/hostapd_cli-mana"
    log "installed mana binaries into $DESTDIR_SBIN"
}

cmd="${1:-install}"
case "$cmd" in
    --check)
        if apt_available; then echo "strategy: apt (hostapd-mana available)";
        elif prebuilt_available; then echo "strategy: prebuilt ($(prebuilt_dir))";
        else echo "strategy: source (apt+prebuilt unavailable)"; fi
        ;;
    apt)
        apt_available || die "hostapd-mana not available via apt on this host"
        apt_install
        ;;
    prebuilt)
        prebuilt_available || die "no shipped prebuilt for this arch"
        prebuilt_install
        ;;
    build)
        build_from_source "${2:-$SELF_DIR/build}"
        ;;
    install)
        # Strategy order:
        #   1. apt       -> Kali/NetHunter: maintained, signed, correct deps.
        #   2. prebuilt  -> shipped, validated aarch64 build (no compiler needed).
        #   3. source    -> last resort (any arch, needs toolchain + dev libs).
        if apt_available; then
            apt_install
        elif prebuilt_available; then
            log "hostapd-mana not in apt here; using shipped prebuilt"
            prebuilt_install
        else
            warn "hostapd-mana not in apt and no prebuilt for this arch; building from source"
            install_from_source
        fi
        ;;
    *) die "unknown command: $cmd (use install|apt|prebuilt|build|--check)" ;;
esac
