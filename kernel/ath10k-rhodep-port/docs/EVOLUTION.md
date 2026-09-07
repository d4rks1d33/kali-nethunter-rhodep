# Evolution: sessions 1..9 timeline

Nine sessions of increasingly-informed reverse-engineering, from
"maybe monitor mode works" to a fully functional aircrack-ng suite on
2.4 GHz AND 5 GHz on the internal WCN3990 radio.

## Session 1-3 (2026-03..05): scoping and dead ends

Discovered patch 0117 (`RX_FLAG_SKIP_MONITOR` clear) makes passive RX
work. Tried enabling injection via `NL80211_CMD_FRAME` on the STA vdev
with offchannel — worked partially, but the STA vdev has to be up
which conflicts with monitor. Deauth-swap was the workaround. Item
13 in the main repo README documents this era.

## Session 4-6 (2026-06..08): fw reverse engineering

Mapped `_wlan_mgmt_tx_send` at fw offset `0x53660`. Confirmed opmode
allow-list `{STA=0, AP=1, IBSS=2, NDI=6}` blocks monitor. First
attempts at direct fw patch didn't reach OTA because the driver's
monitor mgmt-tx takes the HTT-raw path, not the WMI path that was
patched.

## Session 7 (2026-09-02): Loukious discovered

Someone found Wael Hasnaoui's Medium article and v1 commit for
WCN3998. Attempted a straight port to WCN3990. Result: hidden STA vdev
created, `status=0` on first tx, but sustained bursts got `status=1
DISCARD`. Tried extending `wmi_tlv_mgmt_tx_cmd` with the qcacld v7
fields — crashed fw immediately. Reverted. Parked as
`wip-rhodep-loukious-hidden-sta/`.

## Session 8 (2026-09-05): first working version, session-oops era

Restored the port from the parked snapshot. Made it work end-to-end
with the aircrack-ng suite on 2.4 GHz. Documented in
`wip-rhodep-0119-loukious-monitor-inject/README.md` as commit item 16
in the main repo.

But: introduced a subtle kernel oops in `wmi_process_mgmt_tx_comp`
(UAF via `idr_find`/`idr_remove` split — see item 16.3 for the
diagnosis) that manifested during aireplay-ng bursts. Debugged with
multi-agent RE.

## Session 9 (2026-09-06): the great cleanup

Item 16.3: **Fix the oops.** Three bugs in the mgmt-tx completion
path:
- Split `idr_find` / `idr_remove` with `kfree(pkt_addr)` in between →
  duplicate completion events UAF'd the freed pkt_addr. Fix: atomic
  `idr_remove()`.
- Status normalization `raw & 0x3` promoted retry-only events to fake
  COMPLETE_OK. Fix: drop `(raw & 0x3) == 0 && (raw & ~0x3U)` events.
- Spinlock held across sleeping WMI calls in `ensure_inject_vdev`. Fix:
  drop the spinlock, use `conf_mutex` only.

Item 16.4: **Restore per-DA peer_create + LRU cache** that session 8
mistakenly removed. Turned out to be pointing at the wrong fix
direction (documented in item 16.5).

Item 16.5: **The AP-vdev breakthrough.** Multi-agent RE of Loukious's
v1 + Onyx v7 revealed:
- WCN3998 fw uses STA self-peer as wildcard tx-peer for any addr1.
- WCN3990 fw does NOT — it DISCARDs unicast whose addr1 has no peer.
- Per-DA peer_create on peerless STA vdev asserts
  `cmnos_thread.c:4005:A`.
- **AP vdev** (WMI_VDEV_TYPE_AP + VDEV_UP with self-BSSID) uses AP
  self-peer as wildcard for ALL unicast, no per-DA needed.

Also Fix A: drop non-mgmt frames on monitor vif in `ath10k_mac_op_tx`
to avoid the `WLAN BE:0x4708a` fw crash from the HTT raw path.

Result: `aireplay-ng -9 wlan0mon` prints "Injection is working!" with
100% response rate on 2.4 GHz.

Item 16.6: **Chan-scan race fix.** User reported `aireplay --deauth 0`
without `-D` (default channel scan mode) failed with "Network is
down". Diagnosed as: each `iw set channel` teardown+recreates the
hidden AP vdev = 10 WMI cmds × 13 channels = credit exhaustion →
`start_recovery` → `-108` avalanche. Fix: three defense layers (A) fw-
dead early bailout, (B) rate-limit 1/sec, (C) in-place `vdev_restart`
instead of delete+recreate.

Item 16.7: **5 GHz TX/RX unlock via `ath.country=` param.** Motorola
Moto G82 5G ships with EEPROM regdomain 0x406c → world regd →
NL80211_RRF_NO_IR on all 5 GHz channels. Fix in
`drivers/net/wireless/ath/regd.c`: new `ath.country=US` module param
that (a) rewrites current_rd, (b) makes runtime `iw reg set` also work,
(c) applies a permissive custom regdom covering all 25 common 5 GHz
channels (UNII-1/2/2e/3). Verified: `aireplay-ng -9` on ch 149 gets
"Injection is working!" with 7.9 ms ping RTT.

Also verified `WiFi Mateo 5G` (target BSSID `8a:c2:27:a1:19:d0`)
capturable on ch 157 at -63 dBm.

Item 16.8: **rhodep_inject_retune chanctx hook.** Multi-agent RE
identified: WCN3990 has ONE radio, and the hidden AP vdev pins the
pdev on its old freq when the monitor vif moves to a different band.
Fix: hook `ath10k_mac_update_vif_chan` to also retune the hidden AP
vdev to the new freq via `vdev_down → vdev_restart → vdev_up`.

Then: tried to fix the LAST 5 GHz issue (airodump `--band abg` doesn't
find all 5 GHz APs consistently). Attempted TWO different fixes:

- **DOWN+UP bounce on monitor vdev** in `assign_vif_chanctx` — crashes
  fw at `cmnos_thread.c:4005 RT:0x9e087`. Monitor vdevs have no peer
  to anchor the transition.
- **Dummy STA vdev create+delete+barrier** — same idiom as mainline's
  `ath10k_core_reset_rx_filter` for QCA9880 — crashes fw at
  `cmnos_thread.c:4005 RT:0xa8xxx` cascade. WCN3990 fw is stricter
  than QCA9880.

Both reverted. Documented as a known limitation in
[5ghz-mgmt-forwarder-bug.md](5ghz-mgmt-forwarder-bug.md). Future work:
RE downstream qcacld-3.0's `wma_set_monitor_mode` sequence to find
what WMI cmd it uses to keep the fw mgmt-forwarder alive.

## Session 9 commits (main repo)

- `37abe03` wifi item 16.3: fix ath10k oops in Loukious port
- `0f73d16` wifi item 16.4: restore per-DA peer_create + LRU cache
- `4a30446` wifi item 16.5: SOLVED aireplay -9 100% via AP vdev
- `ba77de8` wifi item 16.6: fix chan-scan race in --deauth without -D
- `4bf7484` wifi item 16.7: unlock 5 GHz via ath.country= param
- `b1d8b4f` wifi item 16.8: add inject_retune hook + document 5G limit

Total lines added across session 9: ~800 in mac.c + core.h/c +
regd.c + wmi.c + wmi-tlv.c/h + mac.h + docs.

## Key insights (for future WCN3990 hackers)

1. **WCN3998 vs WCN3990 are NOT the same fw.** Loukious's v1 has bugs
   that only manifest on WCN3990. Test everything.
2. **Per-DA peer_create on a peerless STA vdev asserts fw.** Only do
   this on VDEV_UP'd vdevs with a valid peer chain.
3. **AP vdev is the right choice on WCN3990**, even for
   mgmt-injection-only use cases, because AP self-peer is wildcard.
4. **Beacon offload is defused by `bcn_intval=0` + `hidden_ssid=1` +
   dummy SSID + `disable_hw_ack=1`.**
5. **HTT raw path from monitor vif is toxic** when a sibling AP vdev
   is UP. Drop non-mgmt frames early in `ath10k_mac_op_tx`.
6. **WCN3990 has ONE HTC WMI credit.** Fw dropped completions leak it
   forever unless GC'd. This is what caused the `-108` avalanche
   before item 16.4.
7. **Regdomain 0x6c is baked into the BDF and cannot be changed via
   `iw reg set`.** The `ath.country=` module param on the parent
   `ath` driver is the workaround.
8. **WCN3990 fw's phymode-class transitions (5G ↔ 2.4G) leave the
   mgmt-forwarder in a stuck state.** No known safe WMI cmd to
   re-arm. This is the last remaining limitation.
</content></function_results>

Ahora actualizo el README principal del port para que refleje el estado final del ath10k. Miro el header actual del repo main README:

<function_calls>
<invoke name="bash">
<parameter name="command">grep -n "Monitor mode\|monitor mode\|item 16\|WCN3990.*monitor\|WCN3990.*inject" /opt/postmarket/nethunter-rhodep-repo/README.md | head