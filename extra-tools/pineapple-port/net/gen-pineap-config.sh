#!/bin/sh
# gen-pineap-config.sh -- render /tmp/pineap.conf from the UCI store.
#
# Part of: extra-tools/pineapple-port/net/   (FASE 4)
#
# WHY:
#   On OpenWrt the pineapd init script's gen_config() reads the UCI datamodel
#   (pineap.@config[0].*) and writes /tmp/pineap.conf before launching pineapd.
#   This is the port's equivalent, invoked by pineapd.service ExecStartPre. It
#   reads the SAME UCI store the panel writes (via the compat/uci shim, stored
#   at /opt/pineapple/etc/uci/pineap) and emits /tmp/pineap.conf in the EXACT
#   "key = value" format pineapd-ng (config.c) parses and the panel's
#   PineAPHelper reads/writes (getSetting/setSetting).
#
#   Reference for fields & format:
#     - docs/INFORME_pineapd_RE.md  §B (config file + fields)
#     - src/pineapd/config.c        config_save()/config_apply_line()
#     - docs/INFORME-panel-PORT.md  §2 (UCI datamodel, pineap.@config[0].*)
#
#   The pineapd-ng parser is tolerant (trims spaces, ignores unknown keys,
#   accepts on/off/0/1/true/yes for bools) but we emit the canonical form
#   produced by config_save() so the file is byte-compatible with both the
#   daemon and the panel.
#
# USAGE:
#   gen-pineap-config.sh [OUTFILE]      (default OUTFILE=/tmp/pineap.conf)
# ENV:
#   UCI            path to the uci shim (default: /opt/pineapple/compat/uci)
#   PINEAPPLE_UCI_DIR  store dir (consumed by the uci shim; default
#                      /opt/pineapple/etc/uci)
#
# Idempotent: writes the full file atomically every time.

set -eu

OUT="${1:-/tmp/pineap.conf}"
UCI="${UCI:-/opt/pineapple/compat/uci}"
export PINEAPPLE_UCI_DIR="${PINEAPPLE_UCI_DIR:-/opt/pineapple/etc/uci}"

# uci get that tolerates a missing key (returns the provided default).
# The uci shim returns rc=1 + empty stdout for a missing key (like real uci).
uget() {
    # $1 = pineap.@config[0].<opt>  ; $2 = default
    _v="$("$UCI" get "$1" 2>/dev/null || true)"
    if [ -z "$_v" ]; then
        printf '%s' "$2"
    else
        printf '%s' "$_v"
    fi
}

# Normalise a boolean coming from the store (0/1/on/off/true/false) to on|off.
# The panel writes "on"/"off" via uciSet; the seed uses 0/1. Emit "on"/"off"
# which is what config_save() produces and what PineAPHelper expects.
bool_onoff() {
    case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" in
        1|on|true|yes)  printf 'on'  ;;
        *)              printf 'off' ;;
    esac
}

# Normalise an interval to LOW|NORMAL|AGGRESSIVE (uppercase; default NORMAL).
norm_interval() {
    case "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" in
        LOW)        printf 'LOW' ;;
        AGGRESSIVE) printf 'AGGRESSIVE' ;;
        *)          printf 'NORMAL' ;;
    esac
}

# Normalise a filter mode to white|black (config.c parse_filter accepts both
# white/allow and black/deny; the UCI datamodel uses white/black).
norm_filter() {
    case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" in
        white|allow) printf 'white' ;;
        *)           printf 'black' ;;
    esac
}

P='pineap.@config[0]'

autostart="$(uget "$P.autostart" 0)"
karma="$(bool_onoff "$(uget "$P.karma" 0)")"
beacon_interval="$(norm_interval "$(uget "$P.beacon_interval" NORMAL)")"
beacon_response_interval="$(norm_interval "$(uget "$P.beacon_response_interval" NORMAL)")"
beacon_responses="$(bool_onoff "$(uget "$P.beacon_responses" off)")"
capture_ssids="$(bool_onoff "$(uget "$P.capture_ssids" off)")"
broadcast_ssid_pool="$(bool_onoff "$(uget "$P.broadcast_ssid_pool" off)")"
logging="$(bool_onoff "$(uget "$P.logging" 0)")"
mac_filter="$(norm_filter "$(uget "$P.mac_filter" white)")"
ssid_filter="$(norm_filter "$(uget "$P.ssid_filter" white)")"
ap_channel="$(uget "$P.ap_channel" 1)"
pineap_mac="$(uget "$P.pineap_mac" 00:00:00:00:00:00)"
target_mac="$(uget "$P.target_mac" 00:00:00:00:00:00)"
# Default capture iface is the DEDICATED USB adapter (Archer wlan1), NEVER the
# internal radio (wlan0): monitor on the internal WCN3990 tears the LAN down.
# "No Archer plugged in" => no monitor by design (recon still works via STA scan).
pineap_interface="$(uget "$P.pineap_interface" wlan1mon)"
# Managed STA interface used for the native off-channel recon scan (iw scan).
# Empty => pineapd auto-detects the connected managed iface (falls back to wlan0).
# This is distinct from pineap_interface (the monitor VIF for handshake/inject):
# on single-radio hardware the monitor cannot channel-hop, so recon uses the STA.
sta_scan_interface="$(uget "$P.sta_scan_interface" "")"
recon_db_path="$(uget "$P.recon_db_path" /opt/pineapple/etc/pineapple.db)"
hostapd_db_path="$(uget "$P.hostapd_db_path" /opt/pineapple/etc/pineapple.db)"
ssid_db_path="$(uget "$P.ssid_db_path" /opt/pineapple/etc/pineapple.db)"
connect_notifications="$(bool_onoff "$(uget "$P.connect_notifications" off)")"
disconnect_notifications="$(bool_onoff "$(uget "$P.disconnect_notifications" off)")"
rts_enabled="$(bool_onoff "$(uget "$P.rts_enabled" off)")"
rts_scan_id="$(uget "$P.rts_scan_id" -1)"

TMP="${OUT}.tmp.$$"
{
    # Header is identical to config_save() / the original (INFORME §B.1).
    echo "# PineAP Config File"
    echo "# -- DO NOT EDIT MANUALLY --"
    echo "autostart = $autostart"
    echo "karma = $karma"
    echo "beacon_interval = $beacon_interval"
    echo "beacon_response_interval = $beacon_response_interval"
    echo "beacon_responses = $beacon_responses"
    echo "capture_ssids = $capture_ssids"
    echo "broadcast_ssid_pool = $broadcast_ssid_pool"
    echo "logging = $logging"
    echo "mac_filter = $mac_filter"
    echo "ssid_filter = $ssid_filter"
    echo "ap_channel = $ap_channel"
    echo "pineap_mac = $pineap_mac"
    echo "target_mac = $target_mac"
    echo "pineap_interface = $pineap_interface"
    echo "sta_scan_interface = $sta_scan_interface"
    echo "recon_db_path = $recon_db_path"
    echo "hostapd_db_path = $hostapd_db_path"
    echo "ssid_db_path = $ssid_db_path"
    echo "connect_notifications = $connect_notifications"
    echo "disconnect_notifications = $disconnect_notifications"
    echo "rts_enabled = $rts_enabled"
    echo "rts_scan_id = $rts_scan_id"
} > "$TMP"
mv -f "$TMP" "$OUT"

exit 0
