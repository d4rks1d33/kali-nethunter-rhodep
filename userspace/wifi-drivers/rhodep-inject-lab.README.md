# rhodep-inject-lab

Inject 802.11 management frames on any channel from the **internal** WiFi radio
(ath10k/WCN3990). Works, verified over the air. For your own networks / lab.

## What works

- Transmit probe-req / deauth / auth / etc. on any 2.4GHz or 5GHz channel from
  wlan0's STA vdev via the mac80211 offchannel/ROC path.
- **Does not drop your WiFi**: wlan0 stays associated while it briefly hops
  offchannel to send.
- **Deauth is effective** (spoofed SA = the AP's BSSID, patch 0118). Verified
  with an external TP-Link witness: our deauths appear on the air with SA = the
  AP's MAC, and target clients disconnect.

## What does not work (hardware/firmware limits)

- Monitor-vdev raw injection: the firmware discards it (see README item 13).
  This tool uses the STA offchannel path instead.
- Arbitrary data-frame injection: same limit; only management frames are
  transmit-capable via this path.
- addr2 spoofing on non-deauth/auth frames: cfg80211 allows random-TA only for
  deauth + auth (via patch 0118). Probe/action still use our own MAC as SA.

## Usage

Menu:

```
sudo rhodep-inject-lab
```

Direct commands:

```
sudo rhodep-inject-lab probe   <ch|freq> [dest] [bssid] [count]
sudo rhodep-inject-lab deauth  <ch|freq>  <dest>  <bssid> [count] [reason]
sudo rhodep-inject-lab raw     <ch|freq> <hexframe> [count]
sudo rhodep-inject-lab witness <ch|freq>          # set up TP-Link as OTA witness
```

`<ch|freq>` accepts a channel number (11, 36, 149, …) or a raw MHz value
(2462, 5745, …). The tool converts channel → freq automatically.

## Example: deauth all clients of `WiFi Mateo 2.4G` (ch 11, BSSID 8a:c2:27:a1:19:cc)

```
sudo rhodep-inject-lab deauth 11 ff:ff:ff:ff:ff:ff 8a:c2:27:a1:19:cc 40
```

The frames go out with SA = `8a:c2:27:a1:19:cc` (the AP's MAC), which is what
clients honour. Confirmed on-air with a TP-Link in monitor: 130 deauths captured
at -23 dBm, target client disconnected.

## Requires

- Kernel patches **0117 + 0118** applied (mgmt capture + AUTH/DEAUTH random-TA).
- `nl80211-mgmt-tx.py` installed alongside (same dir), which issues
  `NL80211_CMD_FRAME` via raw generic-netlink (no `iw mgmt-tx`, since `iw` 6.17
  does not have that subcommand).
- Optional but recommended: TP-Link (or another external adapter with monitor +
  injection) plugged in, for the `witness` mode which verifies OTA independently.
