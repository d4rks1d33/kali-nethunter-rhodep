#!/bin/sh
# pineapple-net.sh -- bring the Pineapple LAN + NAT up/down (native Kali, nftables).
#
# Part of: extra-tools/pineapple-port/net/   (FASE 4)
#
# WHAT IT REPLACES (reverse-engineered from the original OpenWrt firmware):
#   docs/INFORME-* + /tmp/opencode/pineapple-re/wifi-pineapple-cloner/files/common/etc/
#     95-network.sh : LAN = bridge br-lan, static 172.16.42.1/24 (ORIGINAL).
#                     gateway 172.16.42.42, dns 1.1.1.1 1.0.0.1
#   PORT CHANGE: the port moves the LAN to 172.16.84.1/24 so it does NOT clash
#   with usb0 (NetHunter USB tether = 172.16.42.1/24, the SSH lifeline).
#     90-firewall.sh: masquerade towards the uplink (usb/wan/wwan) + forward
#                     lan<->uplink (both directions) + ip_forward
#   On OpenWrt netifd/fw3 built br-lan and fw3 installed the NAT. Kali has
#   neither; this script does the SAME with iproute2 + nftables, fully
#   contained and fully reversible.
#
# DESIGN:
#   - Creates a dedicated bridge  br-lan  with IP 172.16.84.1/24.
#     (The AP interface -- e.g. the TP-Link Archer USB running hostapd -- is
#     enslaved to br-lan separately by the AP/hostapd layer; this script only
#     needs the bridge + addressing + NAT so DHCP victims on 172.16.84.0/24 get
#     routed out through the phone's uplink.)
#   - Enables ip_forward (saving the previous value to restore on down).
#   - Installs an isolated nftables table `inet pineapple` with:
#       * masquerade on the uplink (postrouting)
#       * forward br-lan <-> uplink (both directions) + established/related
#     Using a DEDICATED nft table means `down` just deletes that one table and
#     never touches the phone's own firewall rules.
#   - down: reverses everything (flush+delete our nft table, restore ip_forward,
#     drop the IP, delete br-lan). Leaves zero residue.
#
# UPLINK DETECTION (G82 specific):
#   The phone's internet-facing interface can be rmnet* (mobile data), a wlan
#   client (wlanX in station mode), or usb0 tethering. Order of resolution:
#     1. $UPLINK env / /opt/pineapple/etc/uplink file  (explicit override)
#     2. the interface of the current default route (ip route get 1.1.1.1)
#   The uplink is recorded to /opt/pineapple/run/uplink so `down` reverts the
#   exact same interface even if routing changed meanwhile.
#
# USAGE:  pineapple-net.sh up | down | status
# ENV:
#   BRIDGE   bridge name            (default br-lan)
#   LAN_IP   LAN address            (default 172.16.84.1)
#   LAN_CIDR prefix length          (default 24)
#   UPLINK   force uplink iface     (default: autodetect)
#   NFT_TABLE  nft table name       (default pineapple)
#   RUN_DIR  runtime state dir      (default /opt/pineapple/run)

set -eu

# NOTE: The LAN subnet MUST NOT collide with usb0 (the NetHunter USB-tether /
# SSH lifeline, fixed at 172.16.42.1/24) nor with common home networks
# (192.168.0/1.0/24). We use 172.16.84.0/24. Using the SAME 172.16.42.1 as usb0
# previously stole the route and killed SSH-over-USB. DO NOT revert to .42.x.
BRIDGE="${BRIDGE:-br-lan}"
LAN_IP="${LAN_IP:-172.16.84.1}"
LAN_CIDR="${LAN_CIDR:-24}"
NFT_TABLE="${NFT_TABLE:-pineapple}"
RUN_DIR="${RUN_DIR:-/opt/pineapple/run}"
UPLINK_FILE="${RUN_DIR}/uplink"
IPFWD_FILE="${RUN_DIR}/ip_forward.orig"
UPLINK_CFG="/opt/pineapple/etc/uplink"

log() { echo "[pineapple-net] $*"; }

detect_uplink() {
    # 1) explicit override
    if [ -n "${UPLINK:-}" ]; then
        printf '%s' "$UPLINK"; return 0
    fi
    if [ -r "$UPLINK_CFG" ]; then
        _u="$(head -n1 "$UPLINK_CFG" 2>/dev/null | tr -d ' \t\r\n')"
        if [ -n "$_u" ]; then printf '%s' "$_u"; return 0; fi
    fi
    # 2) default route towards the Internet
    _u="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1)"
    if [ -n "$_u" ]; then printf '%s' "$_u"; return 0; fi
    # last resort: whatever has the default route
    _u="$(ip route show default 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1)"
    printf '%s' "$_u"
}

up() {
    mkdir -p "$RUN_DIR"

    # --- bridge ---
    if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
        log "creating bridge $BRIDGE"
        ip link add name "$BRIDGE" type bridge
    fi
    ip link set "$BRIDGE" up

    # --- address (idempotent) ---
    if ! ip -4 addr show dev "$BRIDGE" | grep -q "inet ${LAN_IP}/"; then
        log "assigning ${LAN_IP}/${LAN_CIDR} to $BRIDGE"
        ip addr add "${LAN_IP}/${LAN_CIDR}" dev "$BRIDGE"
    fi

    # --- uplink ---
    UP="$(detect_uplink)"
    if [ -z "$UP" ]; then
        log "WARNING: no uplink detected; NAT will be installed once an uplink exists."
        log "         set UPLINK=<iface> or write it to $UPLINK_CFG"
    fi
    printf '%s\n' "$UP" > "$UPLINK_FILE"
    log "uplink = ${UP:-<none>}"

    # --- ip_forward (save original) ---
    if [ ! -f "$IPFWD_FILE" ]; then
        cat /proc/sys/net/ipv4/ip_forward > "$IPFWD_FILE" 2>/dev/null || echo 0 > "$IPFWD_FILE"
    fi
    sysctl -w net.ipv4.ip_forward=1 >/dev/null

    # --- nftables: dedicated table so we never touch the phone's ruleset ---
    # Delete a stale copy first (idempotent re-up).
    nft delete table inet "$NFT_TABLE" 2>/dev/null || true

    # Build the ruleset. If there is no uplink yet, still create the table with
    # the bridge forward rules; masquerade is added only when we know the uplink.
    if [ -n "$UP" ]; then
        nft -f - <<EOF
table inet ${NFT_TABLE} {
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "${BRIDGE}" oifname "${UP}" accept
        iifname "${UP}" oifname "${BRIDGE}" ct state established,related accept
    }
    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
        oifname "${UP}" ip saddr ${LAN_IP%.*}.0/${LAN_CIDR} masquerade
    }
}
EOF
    else
        nft -f - <<EOF
table inet ${NFT_TABLE} {
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "${BRIDGE}" accept
    }
}
EOF
    fi

    log "up: $BRIDGE=${LAN_IP}/${LAN_CIDR}  uplink=${UP:-<none>}  nft table inet/${NFT_TABLE}"
}

down() {
    # --- nft table ---
    if nft list table inet "$NFT_TABLE" >/dev/null 2>&1; then
        log "removing nft table inet/$NFT_TABLE"
        nft delete table inet "$NFT_TABLE" 2>/dev/null || true
    fi

    # --- ip_forward restore ---
    if [ -f "$IPFWD_FILE" ]; then
        _orig="$(cat "$IPFWD_FILE" 2>/dev/null || echo 0)"
        sysctl -w net.ipv4.ip_forward="${_orig:-0}" >/dev/null 2>&1 || true
        rm -f "$IPFWD_FILE"
    fi

    # --- address + bridge ---
    if ip link show "$BRIDGE" >/dev/null 2>&1; then
        ip addr flush dev "$BRIDGE" 2>/dev/null || true
        ip link set "$BRIDGE" down 2>/dev/null || true
        log "deleting bridge $BRIDGE"
        ip link delete "$BRIDGE" type bridge 2>/dev/null || true
    fi

    rm -f "$UPLINK_FILE"
    log "down: reverted (bridge removed, ip_forward restored, nft table removed)"
}

status() {
    echo "bridge:"
    ip -4 addr show "$BRIDGE" 2>/dev/null || echo "  $BRIDGE: absent"
    echo "uplink (recorded): $(cat "$UPLINK_FILE" 2>/dev/null || echo '<none>')"
    echo "ip_forward: $(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)"
    echo "nft table inet/$NFT_TABLE:"
    nft list table inet "$NFT_TABLE" 2>/dev/null || echo "  absent"
}

case "${1:-}" in
    up)     up ;;
    down)   down ;;
    status) status ;;
    *) echo "usage: $0 {up|down|status}" >&2; exit 1 ;;
esac
