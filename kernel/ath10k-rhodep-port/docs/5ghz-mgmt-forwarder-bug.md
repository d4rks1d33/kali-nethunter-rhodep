# The 5 GHz mgmt-forwarder bug

## TL;DR

`airodump-ng --band abg` (auto-hopping both bands) or `wifite -5` on
the internal WCN3990 sometimes miss 5 GHz APs that ARE within range.
The same APs are found reliably when the channel is set manually with
`iw dev wlan0mon set channel <N>`. Two attempted driver-side fixes
both crashed fw. The bug is inside the fw and there's no known safe
WMI cmd to re-arm it.

## Symptom

```
airmon-ng start wlan0

# Manual test (works, always):
iw dev wlan0mon set channel 157
sleep 3
tcpdump -i wlan0mon -e -n 'type mgt subtype beacon'
  5785 MHz 11a -63dBm  BSSID:8a:c2:27:a1:19:d0  Beacon (WiFi Mateo 5G)
  5785 MHz 11a -65dBm  BSSID:8a:c2:27:a1:19:d0  Beacon (WiFi Mateo 5G)
  ...

# Auto-hopping test (finds some 5 GHz APs but misses others):
airodump-ng --band abg wlan0mon
  # 40 s later:
  #   22 APs on 2.4 GHz  (consistent)
  #    2 APs on 5 GHz    (inconsistent, sometimes 0, sometimes 5)
```

## Diagnosis

Two multi-agent RE sessions on 2026-09-06 (documented in
[EVOLUTION.md](EVOLUTION.md) session 9) converged on this analysis.

WCN3990 fw's WMI mgmt-forwarder is the ONLY path by which monitor
frames reach userspace on this chip. Patch 0117's hijack makes it
work by clearing `RX_FLAG_SKIP_MONITOR` on the fw-forwarded events.
The fw-side forwarder is armed by `WMI_VDEV_START` on the monitor
vdev.

However, on WCN3990 the vdev channel-change path is `unassign →
remove → add → assign`, which does a **full teardown** (`VDEV_DOWN
→ VDEV_STOP`) followed by a **fresh start** (`VDEV_START →
VDEV_UP`), *not* an in-place `VDEV_RESTART`. Every `iw set channel`
is a full teardown.

For channel changes WITHIN a single phymode class (2.4 GHz → 2.4 GHz
or 5 GHz → 5 GHz), the fw's mgmt-forwarder re-arms correctly on the
new channel.

For phymode-CLASS transitions (5 GHz `MODE_11A` → 2.4 GHz
`MODE_11G`, or vice versa), the fw's mgmt-forwarder enters an
inconsistent state: it de-registered the RX callback on the OLD band
but sometimes fails to re-register on the NEW band. Symptom: after

```
iw set channel 157  (works)
iw set channel 6    (silent, 0 beacons on any band)
iw set channel 157  (works again, on-band re-tune "unfreezes" the fw)
```

This makes `airodump-ng --band abg` unreliable: it hops rapidly
between bands, and every phymode-CLASS transition has a chance of
temporarily disabling RX.

## Attempted fix 1: DOWN+UP bounce on monitor vdev

**Rationale**: after `VDEV_UP` in `assign_vif_chanctx`, issue a
`VDEV_DOWN → VDEV_UP` pair. This should force the fw's RT thread to
re-run its mgmt-forwarder registration logic on the new channel.

**Result**: **Fw crash on the very first channel change**:

```
[    ] failed to set cts protection for vdev 0: -108
[    ] ieee80211 phy30: Hardware restart was requested
[    ] rhodep vdev-start id=0 type=4 freq=2462 cf1=2462 mode=1 restart=0
[    ] PDM: service 'wlan_process' crash: 'EF:wlan_process:0x1:WLAN RT:0x9e087:cmnos_thread.c:4005:'
[    ] ath10k_snoc c800000.wifi: firmware crashed! (guid a0357e94-...)
```

**Root cause**: the `RT:` prefix identifies the RT (real-time) thread
of the fw, distinct from the `BE:` (back-end) thread we saw in
earlier peer_create crashes. The RT thread owns the on-air state
machine (TSF, BSSID programming, beacon tracking) and enforces an
`ASSERT(bss_entry->state == IDLE_CLEAN)` before allowing `VDEV_UP`.

For STA/AP vdevs, the intervening `peer_create` transitions the
`bss_entry` state to `IDLE_CLEAN`. For MONITOR vdevs there is no
peer → no state transition → the second `VDEV_UP` fires the assert.

**Reverted.**

## Attempted fix 2: Dummy STA vdev create+delete+barrier

**Rationale**: mainline ath10k already handles a similar bug class
on QCA9880 via `ath10k_core_reset_rx_filter()` at boot: create a
dummy `WMI_VDEV_TYPE_STA` vdev, delete it, ping fw with
`ath10k_wmi_barrier()`. This "pokes" the fw into re-arming its
internal RX admission filter without ever putting the vdev UP, so the
RT thread's `bss_entry` list is never touched.

We tried the same idiom in `assign_vif_chanctx` for monitor vifs.

**Result**: **Fw crash cascade**:

```
[    ] PDM: service 'wlan_process' crash: 'EF:wlan_process:0x1:WLAN RT:0xa808b:cmnos_thread.c:4005:'
[    ] ath10k_snoc c800000.wifi: firmware crashed!
[    ] PDM: service 'wlan_process' crash: 'EF:wlan_process:0x1:WLAN RT:0xaa075:cmnos_thread.c:4005:'
[    ] ath10k_snoc c800000.wifi: firmware crashed!
[    ] PDM: service 'wlan_process' crash: 'EF:wlan_process:0x1:WLAN RT:0xac089:cmnos_thread.c:4005:'
[    ] ath10k_snoc c800000.wifi: firmware crashed!
... (24 crashes in a 40 s airodump run, ~1 crash per phymode-class hop)
```

**Root cause**: WCN3990 fw is stricter than QCA9880. `wlan_vdev_create`
on the RT thread allocates and initializes a per-vdev structure that
includes an entry in the RT thread's global bss list. Immediate
`wlan_vdev_delete` on the same tick doesn't fully unwind that
allocation before the next channel-change context switch happens,
leaving the RT thread's iterator with a dangling pointer. Next
allocation collides, `cmnos_thread.c:4005` asserts.

Note that `ath10k_core_reset_rx_filter()` runs ONCE at boot when the
fw is in a quiescent init state; it never re-runs at runtime. Our
attempt to re-run it on every channel change (dozens of times per
airodump run) exercises a code path the fw was not designed to
handle.

**Reverted.**

## What we know but haven't tried

The downstream qcacld-3.0 driver runs `wma_set_monitor_mode()` on
every channel change while in monitor mode, which explicitly re-issues
a WMI command to keep the fw mgmt-forwarder alive. That command is
`wmi_vdev_param_rx_filter_promisc` in qcacld-3.0's WMI dictionary.
In ath10k mainline's WMI-TLV table, the corresponding entry is
`.rx_filter = WMI_PDEV_PARAM_UNSUPPORTED` (see wmi-tlv.c:4492), meaning
mainline ath10k has NEVER exposed this command.

## Roadmap to fix

Three possible avenues, in increasing effort:

1. **Reverse-engineer downstream qcacld-3.0's WMA layer** to find
   the exact WMI cmd + parameters used by `wma_set_monitor_mode`.
   Add the cmd to ath10k's WMI-TLV table. Call it from
   `assign_vif_chanctx` for monitor vifs. Expected result: fw
   mgmt-forwarder stays armed across band changes; `airodump-ng
   --band abg` becomes reliable.

2. **Patch the WCN3990 fw directly.** We already know the fw is
   patchable (the earlier session 5 experiment loaded a modified
   `wlanmdsp.mbn`). Find the RT thread's `bss_entry` reset logic
   and add an unconditional re-arm on the phymode-class transition
   path.

3. **Accept the limitation.** The manual `iw set channel` +
   airodump-ng `--channel` workflow works reliably. Wifite users
   can pass `-c <ch>` to fix a channel. This is the current
   documented state.

Option 1 is the right long-term fix. It requires access to the
qcacld-3.0 source tree (public on Google's Codelinaro), a couple of
days of RE, and would benefit every WCN3990 device using ath10k
mainline.

## Wider than "5 GHz airodump abg": also affects aireplay after ANY channel switch

Update 2026-09-07: this bug also fires on 2.4 GHz-only workflows, not just
5G↔2.4G. The trigger is any `iw set channel` (or airodump's hopping) that
leaves the fw mid-retune when the userspace command tool starts consuming
frames. Reproducer with the phone already connected as a client:

```
airmon-ng start wlan0
airodump-ng wlan0mon      # hops channels 1..14, sees WiFi Mateo 2.4G ch 11
                          # PWR -63dBm, 3+ beacons captured -- proof AP is nearby
                          # then quit with Ctrl-C
aireplay-ng --deauth 20 -a 8A:C2:27:A1:19:CC wlan0mon
  15:12:09  Waiting for beacon frame (BSSID: 8A:C2:27:A1:19:CC) on channel 11
  # <-- hangs forever. -D flag works around it.
```

Because airodump leaves wlan0mon on the last channel it hopped through
(here e.g. ch 4 or ch 10), aireplay does its own `iw set channel 11` and
IMMEDIATELY starts polling for beacons. Fw is still in the middle of the
retune -- the mgmt-forwarder needs ~1-2 s to re-arm on the new channel.

**Workaround**: separate the `iw set channel` from the aireplay start:
```sh
sudo iw dev wlan0mon set channel 11
sleep 2                                    # <-- KEY: let fw settle
sudo aireplay-ng --deauth 5 -a <BSSID> wlan0mon
# no -D needed, "Waiting for beacon frame" resolves in <1 s, deauth flows.
```

Verified 2026-09-07 on device (v132 kernel, user's Motorola actively
connected to `WiFi Mateo 2.4G` on ch 11):
- Without `sleep 2`: hangs at "Waiting for beacon frame".
- With `sleep 2`: 174 beacons captured in a 4 s tcpdump on ch 11
  right after the sleep, aireplay finds the beacon instantly, deauth
  frames go out immediately.

`-D` continues to be the "just make it work" answer for users who don't
want to think about it. Both approaches produce equivalent OTA effect.

## Workarounds for end users

### Aireplay after any channel switch: settle first
```sh
sudo iw dev wlan0mon set channel <N>
sleep 2
sudo aireplay-ng --deauth <N> -a <BSSID> wlan0mon
```
Or use `-D` to skip the beacon-scan phase entirely.

### Airodump on a specific channel

```sh
iw dev wlan0mon set channel 157
airodump-ng --channel 157 wlan0mon
```

### Airodump on the whole 5 GHz band with per-channel dwell

```sh
airodump-ng -C 5180,5200,5220,5240,5745,5765,5785,5805,5825 -f 1500 wlan0mon
```

### Wifite on a specific channel

```sh
wifite -c 157 -i wlan0mon
```

### Wifite scanning both bands (may miss some 5 GHz APs)

```sh
wifite -5 -i wlan0mon
```

### Full-band scan via `iw scan` (uses cfg80211 NL80211 scan, not
### monitor-mode passive)

```sh
iw dev wlan0 scan | grep -i 'ssid\|freq'
# note: use wlan0 in STA mode (before airmon-ng), not wlan0mon.
```

### After a failed 5 GHz scan, reset the mgmt-forwarder

If `airodump-ng --band abg` returned 0 APs on 5 GHz, force a channel
change back to the last known-working band:

```sh
iw dev wlan0mon set channel 6    # back to 2.4 GHz (usually works)
sleep 1
iw dev wlan0mon set channel 157  # back to 5 GHz (works again, fresh state)
```

This does NOT reload the driver; it just kicks the fw into re-arming
the forwarder on 5 GHz. It's the same trick that "unfreezes" the fw
when you observed the bug.

## Test evidence

Manual 5 GHz capture works reliably (last verified 2026-09-06):

```
=== iw info ===
    channel 157 (5785 MHz), width: 20 MHz (no HT), center1: 5785 MHz
=== 5s tcpdump ===
5785 MHz 11a -63dBm signal  BSSID:8a:c2:27:a1:19:d0  Beacon (WiFi Mateo 5G)
5785 MHz 11a -68dBm signal  BSSID:8a:c2:27:a1:19:d0  Beacon (WiFi Mateo 5G)
5785 MHz 11a -68dBm signal  BSSID:8a:c2:27:a1:19:d0  Beacon (WiFi Mateo 5G)
```

Aireplay -9 on 5 GHz works reliably (last verified 2026-09-06):

```
aireplay-ng -9 wlan0mon
  22:02:04  Trying broadcast probe requests...
  22:02:04  Found 31 APs
  22:02:10  Ping (min/avg/max): 7.9ms/7.9ms/7.9ms
  22:02:10  Injection is working!
```

`airodump-ng --band abg` finds SOME 5 GHz APs but inconsistently
(last verified 2026-09-06 with all fixes reverted):

```
airodump-ng --band abg -f 800 wlan0mon
  # after 40 s:
  #   22 APs on 2.4 GHz (all consistently found)
  #    2 APs on 5 GHz   (redprivada on ch 40, WiFi Mateo 5G sometimes)
```

Zero firmware crashes across all these tests.

## Contact

Discovered by the rhodep port maintainers during session 9
(2026-09-06). If you know a safe way to keep the fw mgmt-forwarder
alive across band changes, please open an issue at
<https://github.com/anomalyco/nethunter-rhodep> or reach out on the
ath10k mailing list.
</content></function_results>

Ahora agrego el port como directorio principal referenciado desde el kernel README. Miro el kernel README primero:

<function_calls>
<invoke name="bash">
<parameter name="command">grep -n "wip-rhodep\|0117\|0118\|0119\|ath10k\|WCN3990\|monitor" /opt/postmarket/nethunter-rhodep-repo/_common/README-rhodep-KERNEL.md 2>/dev/null | head -15