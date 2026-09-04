#!/bin/bash
# rhodep-offchan-tx-test: probe whether the INTERNAL radio can transmit a
# management frame on an arbitrary channel via the STA vdev's offchannel/ROC
# path (nl80211 mgmt-tx), which — unlike a monitor vdev — the firmware honours
# (association mgmt frames from the STA vdev DO radiate). This is the "inject
# without firmware RE" hypothesis (see README item 13).
#
# Idea: `iw dev wlan0 mgmt-tx <freq> wait <ms> <hexframe>` routes through
# ieee80211_mgmt_tx -> IEEE80211_TX_CTL_TX_OFFCHAN -> ath10k offchannel TX from
# the STA (scan) vdev, carrying the ROC freq in the HTT descriptor. The frame's
# addr2 (SA) is FORCED to wlan0's own MAC by cfg80211 (ath10k has no random-TA),
# so this is a real-source frame, not a spoof.
#
# Runs on the phone. Needs the TP-Link (or another card) as an external witness
# on the target channel to confirm the frame actually hits the air, since a
# self-test can't distinguish "radiated" from "accepted-but-dropped".
#
# Usage:  rhodep-offchan-tx-test.sh <target_freq_mhz> [dest_mac] [bssid]
# Example: rhodep-offchan-tx-test.sh 2462           # ch11
set -u
FREQ="${1:?usage: $0 <freq_mhz> [dest] [bssid]}"
DST="${2:-ffffffffffff}"      # addr1 / DA (hex, no colons)
BSS="${3:-$DST}"              # addr3 / BSSID
WAIT_MS="${WAIT_MS:-500}"

need_root(){ [ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }; }
need_root

IF=wlan0
SELF=$(cat /sys/class/net/$IF/address | tr -d ':')
if [ -z "$SELF" ]; then echo "no $IF"; exit 1; fi

echo "== offchannel mgmt-tx test =="
echo "   iface=$IF self(SA)=$SELF freq=$FREQ dst(DA)=$DST bssid=$BSS wait=${WAIT_MS}ms"

# Build a minimal 802.11 management frame. We use a PROBE REQUEST (subtype 0x4,
# type mgmt) — harmless, sendable from a STA vif, and a clean way to prove the
# radio put a chosen frame on a chosen channel. Frame layout (no colons):
#   fc(2 LE)=40 00  dur(2)=00 00  addr1(DA)  addr2(SA)  addr3(BSSID)  seq(2)=00 00
#   then a minimal SSID IE (00 00 = wildcard) + supported rates IE (01 01 82)
FC="4000"; DUR="0000"; SEQ="0000"
IE="0000010182"
FRAME="${FC}${DUR}${DST}${SELF}${BSS}${SEQ}${IE}"
echo "   frame=$FRAME"

# Send it. `iw` mgmt-tx syntax varies; try the common forms.
echo "== sending via iw mgmt-tx =="
if iw dev "$IF" mgmt-tx "$FREQ" wait-time "$WAIT_MS" "$FRAME" 2>/tmp/offchan.err; then
	echo "iw mgmt-tx accepted"
else
	echo "iw 'wait-time' form failed, trying bare form:"; cat /tmp/offchan.err
	iw dev "$IF" mgmt-tx "$FREQ" "$FRAME" 2>&1 || {
		echo "mgmt-tx not supported by this iw; try: iw dev $IF mgmt-tx --help"; }
fi

echo ""
echo "== WITNESS INSTRUCTIONS =="
echo "On a SEPARATE monitor (e.g. TP-Link wlan1mon) parked on freq $FREQ, capture:"
echo "  tcpdump -i wlan1mon -nne 'wlan addr2 ${SELF:0:2}:${SELF:2:2}:${SELF:4:2}:${SELF:6:2}:${SELF:8:2}:${SELF:10:2}'"
echo "If you see a probe-request from our SA on ch(freq $FREQ), the INTERNAL radio"
echo "radiated on an arbitrary channel via the STA offchannel path => injection is"
echo "viable without firmware RE (subject to addr2 = own MAC)."
echo ""
echo "To watch which driver path was taken, enable ath10k mac debug and look for"
echo "'mac queued offchannel skb' (temp-peer path) vs 'htt tx ... freq $FREQ'."
