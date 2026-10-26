#!/bin/sh
# pineapd-iface.sh -- prepare the pineapd capture interface (monitor mode) WITHOUT
# ever destroying the phone's own WiFi uplink.
#
# Part of: extra-tools/pineapple-port/net/   (FASE 4)
# Invoked by: pineapd.service ExecStartPre (best-effort, prefixed with "-").
#
# ---------------------------------------------------------------------------
# WHY THIS SCRIPT WAS REWRITTEN (red-takedown / lost-internet bug)
# ---------------------------------------------------------------------------
# The previous version did, for the configured capture iface wlan0mon:
#     ip link set wlan0 down ; iw dev wlan0 set type monitor
# On the G82 there is a SINGLE internal radio (phy#0) whose netdev is wlan0 --
# and wlan0 is ALSO the phone's internet STA (connected to the home router).
# Flipping wlan0 to monitor:
#   * instantly drops the phone's WiFi internet, and
#   * because the phone was a *client* of the home router, its sudden
#     disappearance looked like a network-wide outage ("deauth to everyone").
#
# That monitor VIF is NO LONGER needed for recon: recon now uses scan_sta.c,
# which runs a native `iw dev wlan0 scan` on the CONNECTED STA and sees every AP
# on every channel WITHOUT monitor mode and WITHOUT dropping the link. The
# monitor interface is only required for EXPLICIT handshake capture / deauth /
# injection, which the user performs on demand with a DEDICATED adapter (the
# TP-Link Archer USB = wlan1, or a second USB NIC) or with airmon-ng manually.
#
# RULE enforced here:
#   - If the capture iface resolves onto the phone's uplink radio (wlan0, the
#     STA that carries the phone's internet), DO NOTHING. pineapd starts fine
#     without a monitor VIF (main.c keeps serving its socket/DB and recon runs
#     via scan_sta). We log that monitor was skipped on purpose.
#   - Only if the capture iface is a SEPARATE adapter (e.g. wlan1/wlan2 that is
#     NOT the uplink STA) do we create/enable the monitor VIF on THAT device.
#
# This script NEVER touches wlan0 (phone internet), usb0 (SSH lifeline) or
# rmnet* (cellular).

set -u

UCI="${UCI:-/opt/pineapple/compat/uci}"
export PINEAPPLE_UCI_DIR="${PINEAPPLE_UCI_DIR:-/opt/pineapple/etc/uci}"

log() { echo "[pineapd-iface] $*"; }

# The capture/monitor iface the daemon is configured to use.
IFACE="$("$UCI" get pineap.@config[0].pineap_interface 2>/dev/null || true)"
# Default to the DEDICATED adapter (Archer wlan1), NEVER the internal radio.
[ -z "$IFACE" ] && IFACE="wlan1mon"

# Derive the base iface (strip a trailing "mon"): wlan0mon -> wlan0, wlan1 -> wlan1.
BASE="${IFACE%mon}"

# ---------------------------------------------------------------------------
# Identify the phone's uplink STA radio -- the netdev we must NEVER monitor.
# ---------------------------------------------------------------------------
# 1) the interface currently carrying the default route to the Internet, and
# 2) any managed (station) wlan that is associated to an SSID.
# Either match means "this is the phone's internet; keep hands off".
UPLINK_IF="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1)"

is_internal_radio() {
    # $1 = base iface name. True if the netdev belongs to the phone's INTERNAL
    # radio (the SoC/platform WCN3990, phy#0), regardless of whether it is
    # associated right now. This is STRUCTURAL (by device bus path), so it holds
    # even if the STA link flapped for an instant.
    #
    # The internal radio hangs off a platform/SoC bus (…/platform/…/…snoc… or
    # …/platform/soc/…), NOT USB. A dedicated adapter (TP-Link Archer RTL8811AU)
    # hangs off …/usb…  We resolve the real device path and classify it.
    _b="$1"
    _dev="/sys/class/net/$_b/device"
    [ -e "$_dev" ] || return 1          # no backing device -> not a real radio
    _path="$(readlink -f "$_dev" 2>/dev/null || true)"
    case "$_path" in
        *"/usb"*) return 1 ;;           # USB NIC => dedicated adapter, OK to monitor
    esac
    # Anything that is NOT usb (platform/soc/snoc/pci-on-SoC) is the internal radio.
    case "$_path" in
        *platform*|*soc*|*snoc*|*ahb*) return 0 ;;
    esac
    # Unknown bus: fall through to behavioural check below (be conservative).
    return 2
}

is_uplink_radio() {
    # $1 = base iface name. True if it is the INTERNAL radio (structural), OR the
    # uplink route iface, OR a managed wlan currently associated. Any match means
    # "this is (or is on) the phone's internet radio; keep hands off".
    _b="$1"
    # 1) STRUCTURAL: internal SoC radio is NEVER allowed into monitor.
    is_internal_radio "$_b"
    case $? in
        0) return 0 ;;   # definitely internal -> protect
        1) ;;            # definitely USB/dedicated -> continue (allowed)
        *) ;;            # unknown bus -> fall through to behavioural checks
    esac
    # 2) BEHAVIOURAL: current default-route iface.
    [ -n "$UPLINK_IF" ] && [ "$_b" = "$UPLINK_IF" ] && return 0
    # 3) BEHAVIOURAL: managed + associated (the live internet STA).
    if command -v iw >/dev/null 2>&1; then
        _info="$(iw dev "$_b" info 2>/dev/null)"
        case "$_info" in
            *"type managed"*)
                if iw dev "$_b" link 2>/dev/null | grep -qi 'Connected to'; then
                    return 0
                fi
                ;;
        esac
    fi
    return 1
}

if ! command -v iw >/dev/null 2>&1; then
    log "WARNING: iw not installed; cannot manage monitor mode (recon still works via STA scan)"
    exit 0
fi

# If the monitor VIF already exists (created by the user via airmon-ng or a
# previous pre-fix run), we must NOT blindly bring it up: a leftover wlan0mon
# sitting on the INTERNAL radio is exactly what took the home network down.
if ip link show "$IFACE" >/dev/null 2>&1 && [ "$IFACE" != "$BASE" ]; then
    # Find the real parent radio of this monitor VIF (its phy), then a sibling
    # netdev on the same phy to classify the bus. Simplest robust check: the
    # VIF's own /sys/class/net/<IFACE>/phy80211 maps to a phy; the internal
    # radio is phy#0 on the SoC. We reuse is_internal_radio on the VIF itself
    # (its device link points at the same backing radio device).
    if is_internal_radio "$IFACE" || is_uplink_radio "$BASE"; then
        log "DANGER: leftover monitor VIF '$IFACE' sits on the INTERNAL radio '$BASE'."
        log "  Destroying it and restoring '$BASE' to managed (this is what tore the LAN down)."
        iw dev "$IFACE" del 2>/dev/null || ip link delete "$IFACE" 2>/dev/null || true
        iw dev "$BASE" set type managed 2>/dev/null || true
        ip link set "$BASE" up 2>/dev/null || true
        # nudge NetworkManager to reconnect the uplink STA.
        (nmcli device connect "$BASE" >/dev/null 2>&1 || true) &
        log "  pineapd starts without monitor; recon via STA scan. Use the Archer for capture/deauth."
        exit 0
    fi
    log "monitor iface $IFACE already present on a dedicated adapter; bringing up (uplink untouched)"
    ip link set "$IFACE" up 2>/dev/null || true
    exit 0
fi

# SAFETY GATE: never put the phone's internet radio into monitor mode.
if is_uplink_radio "$BASE"; then
    log "capture iface '$IFACE' maps onto the phone's uplink radio '$BASE' -- SKIPPING monitor setup."
    log "  Recon uses the native STA scan (scan_sta); pineapd starts without a monitor VIF."
    log "  For handshake capture / deauth, use a DEDICATED adapter (wlan1 Archer) or 'airmon-ng' manually."
    exit 0
fi

# The base iface exists, is NOT the uplink, so it is a dedicated capture adapter.
if ip link show "$BASE" >/dev/null 2>&1; then
    log "dedicated capture adapter '$BASE' detected; enabling monitor mode"
    ip link set "$BASE" down 2>/dev/null || true
    if iw dev "$BASE" set type monitor 2>/dev/null; then
        ip link set "$BASE" up 2>/dev/null || true
        if [ "$IFACE" != "$BASE" ]; then
            iw dev "$BASE" interface add "$IFACE" type monitor 2>/dev/null || true
            ip link set "$IFACE" up 2>/dev/null || true
        fi
        log "monitor mode set on ${IFACE} (dedicated adapter; uplink untouched)"
        exit 0
    fi
    # Restore the adapter to managed if the monitor switch failed.
    iw dev "$BASE" set type managed 2>/dev/null || true
    ip link set "$BASE" up 2>/dev/null || true
    log "WARNING: could not set monitor mode on $BASE (driver may not support it); left as managed"
    exit 0
fi

log "capture iface '$BASE'/'$IFACE' not present (no dedicated adapter plugged in)."
log "  pineapd starts anyway; recon uses the STA scan. Plug in the Archer/USB NIC for capture."
# Never block pineapd from starting (it serves socket/DB and runs scan_sta).
exit 0
