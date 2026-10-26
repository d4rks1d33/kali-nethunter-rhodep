#!/bin/bash
# Title: Bridge Net Test
# Author: port-validation
# Description: Proof that a NETWORK payload reaches the real hardware/network
#              through the qemu-user bridge (ping + curl run NATIVELY on the G82).
# Version: 1.0

LOG green "Net payload: pinging 8.8.8.8 over real wlan0..."

if ping -c 2 -W 3 8.8.8.8 >/tmp/bridge_ping.out 2>&1; then
    RTT=$(grep -oE 'min/avg/max[^=]*= [0-9./]+' /tmp/bridge_ping.out | head -1)
    LOG green "PING OK: $RTT"
else
    LOG red "PING FAILED (no route?)"
fi

LOG blue "HTTP HEAD to example.com over real network..."
CODE=$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://example.com 2>/tmp/bridge_curl.err)
if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
    LOG green "HTTP reachable, status $CODE"
else
    LOG red "HTTP unreachable"
fi

LOG "Net payload done."
