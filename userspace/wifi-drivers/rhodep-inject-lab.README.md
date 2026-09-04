# rhodep-inject-lab

Interactive lab CLI to test injection on an arbitrary channel from the
**internal** WiFi (ath10k/WCN3990), using an external card (TP-Link) as a
witness. For your own networks / lab only.

## Why

We proved the internal radio's monitor/AP TX does not radiate, but the STA vdev
does transmit. This tool tries the remaining lead: transmit a management frame
on a chosen channel through the STA vdev's **offchannel/ROC** path
(`iw dev wlan0 mgmt-tx`) and use the TP-Link to confirm whether it actually hit
the air — all without touching the firmware. See README item 13.

## How to use (dead simple)

1. Plug in the TP-Link (the USB WiFi card). If it doesn't show up, run `otg on`
   as root first.
2. Run:

   ```sh
   sudo rhodep-inject-lab
   ```

3. Pick a channel from the menu (e.g. `11` for 2.4GHz ch11). Optionally type the
   AP MAC you want to aim at (or leave blank for broadcast).
4. Press ENTER when it warns it will drop WiFi for ~30s. It runs the test, then
   **restores your WiFi automatically**.
5. At the end it prints a `RESULT ...` line and a log path. Copy the `RESULT`
   line back, and/or send the log file it names (e.g.
   `/tmp/rhodep-inject-lab.YYYYMMDD-HHMMSS.log`) and the witness pcap
   (`/tmp/rhodep-inject-witness.pcap`).

## What the result means

- `witness_heard > 0` → the internal radio **radiated** on that channel via the
  STA offchannel path. Injection-on-arbitrary-channel works without firmware RE
  (the frame's source MAC is forced to wlan0's own MAC — that's a hardware/driver
  limit, not a bug).
- `witness_heard = 0` but `sanity_beacons > 0` → the witness works (it heard
  other APs) but our frame didn't radiate — same firmware behaviour as monitor/AP
  TX; the log's `mgmt-tx-compl status=` line tells us if the firmware dropped it.
- `sanity_beacons = 0` → the witness heard nothing at all: wrong channel, card
  not in monitor, or card not plugged. Re-check the TP-Link.

## Notes

- It never leaves your WiFi down: a restore step runs even if something fails or
  you Ctrl-C.
- Tip: aim at a BSSID **different** from the AP wlan0 is associated to (or on a
  different channel), otherwise mac80211 sends on the home channel instead of
  going offchannel — the tool warns you if it detects this.
