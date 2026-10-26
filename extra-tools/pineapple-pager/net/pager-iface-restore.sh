#!/bin/sh
# pager-iface-restore.sh -- safety net run on pineapd stop.
#
# Part of: extra-tools/pineapple-port/net/
# Invoked by: pineapd.service ExecStopPost (best-effort, prefixed with "-").
#
# Guarantees that stopping the Pineapple can NEVER leave the phone's internal
# radio stuck in monitor mode. If any monitor VIF sits on the internal SoC
# radio (WCN3990 / phy#0), destroy it and put that radio back to managed so
# NetworkManager can restore the home-WiFi uplink. Dedicated USB adapters
# (Archer wlan1) are left exactly as the user configured them.
#
# This mirrors the classification used in pineapd-iface.sh: a radio on a
# platform/soc/snoc/ahb bus is INTERNAL (protect); a radio on /usb is a
# dedicated adapter (leave alone).

set -u
log() { echo "[pager-iface-restore] $*"; }

command -v iw >/dev/null 2>&1 || { log "iw missing; nothing to do"; exit 0; }

is_internal_radio() {
    _b="$1"
    _dev="/sys/class/net/$_b/device"
    [ -e "$_dev" ] || return 1
    _path="$(readlink -f "$_dev" 2>/dev/null || true)"
    case "$_path" in
        *"/usb"*) return 1 ;;
    esac
    case "$_path" in
        *platform*|*soc*|*snoc*|*ahb*) return 0 ;;
    esac
    return 1
}

# Walk every wlan/monitor netdev; fix any monitor VIF on the internal radio.
for _nd in /sys/class/net/*; do
    _if="$(basename "$_nd")"
    case "$_if" in
        lo|usb*|rmnet*|bridge*|br-*|wwan*|eth*|tun*|tap*) continue ;;
    esac
    _type="$(iw dev "$_if" info 2>/dev/null | sed -n 's/.*type \(.*\)/\1/p' | head -n1)"
    [ "$_type" = "monitor" ] || continue
    if is_internal_radio "$_if"; then
        log "monitor VIF '$_if' is on the INTERNAL radio; destroying and restoring managed."
        # Try to delete the VIF; if it is the base netdev itself, flip it back.
        if iw dev "$_if" del 2>/dev/null; then
            :
        else
            ip link set "$_if" down 2>/dev/null || true
            iw dev "$_if" set type managed 2>/dev/null || true
            ip link set "$_if" up 2>/dev/null || true
        fi
    fi
done

# Make sure the known internal STA (wlan0) is managed + up and reconnecting.
if is_internal_radio wlan0; then
    _t="$(iw dev wlan0 info 2>/dev/null | sed -n 's/.*type \(.*\)/\1/p' | head -n1)"
    if [ "$_t" = "monitor" ]; then
        ip link set wlan0 down 2>/dev/null || true
        iw dev wlan0 set type managed 2>/dev/null || true
    fi
    ip link set wlan0 up 2>/dev/null || true
    (nmcli device connect wlan0 >/dev/null 2>&1 || true) &
fi

exit 0
