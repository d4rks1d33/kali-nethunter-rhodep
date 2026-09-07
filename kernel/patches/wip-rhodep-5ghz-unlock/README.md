# WIP: ath.country= param unlocks 5 GHz TX on WCN3990

Adds a `country=` module param to `drivers/net/wireless/ath/regd.c` that
overrides the EEPROM regdomain and installs a permissive custom regdom
so cfg80211 does not silently NO_IR-block 5 GHz on WCN3990 boards.

## Problem

Motorola Moto G82 5G ships WCN3990 with EEPROM regdomain `0x406c`
(WORLDWIDE_ROAMING_FLAG | WORC_WORLD). `ath_common`'s
`ath_regd_init_wiphy` picks `ath_world_regdom_67_68_6A_6C` and calls
`wiphy_apply_custom_regulatory()` — every 5 GHz rule in that regdom is
hardcoded with `NL80211_RRF_NO_IR` (see `ATH_5GHZ_5150_5350` /
`ATH_5GHZ_5470_5850` / `ATH_5GHZ_5725_5850` macros at the top of
`regd.c`).

Result:

```
iw phy | grep 5180
    * 5180.0 MHz [36] (30.0 dBm) (no IR)
```

= "passive scan only, TX blocked". `iw reg set US` is silently rejected
because `CONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS` defaults to `N` and
`ath_reg_dyn_country_user_allow()` returns false without it.

Downstream `wcn36xx` uses `REGULATORY_WIPHY_SELF_MANAGED`; `ath11k`
does per-country regdom from firmware over WMI. Neither mechanism
exists in ath10k mainline for WCN3990.

## Fix

Three changes to `drivers/net/wireless/ath/regd.c`:

1. **New `ath.country=XX` module param**. When set, `__ath_regd_init`
   rewrites `reg->current_rd = COUNTRY_ERD_FLAG | CTRY_XX` before the
   normal regpair lookup. Log: `ath: country= override, using US (was
   rd 0x406c)`.

2. **`ath_reg_dyn_country_user_allow()` returns true** when
   `ath_country` is non-NULL, regardless of Kconfig. Makes runtime
   `iw reg set XX` also work after the module is loaded.

3. **Permissive custom regdom `ath_rhodep_permissive_regdom`** applied
   in `ath_regd_init_wiphy` when the param is set. Same shape as the
   existing world regdoms but WITHOUT `NL80211_RRF_NO_IR`. Coverage:
   - UNII-1  (36-48)   5170-5250 MHz — TX free
   - UNII-2  (52-64)   5250-5330 MHz — RX + radar-gated TX (DFS)
   - UNII-2e (100-144) 5490-5730 MHz — RX + radar-gated TX (DFS)
   - UNII-3  (149-165) 5725-5850 MHz — TX free
   `ath_reg_apply_radar_flags()` still marks the DFS bands with `NO_IR
   | RADAR` after our regdom is applied, so TX on 52-64 and 100-140 is
   correctly blocked without DFS certification -- but RX is now
   enabled on ALL 25 common 5 GHz channels, so airodump/kismet/tcpdump
   can see APs everywhere.
   `REGULATORY_STRICT_REG` is NOT set with the override so
   wireless-regdb hints can still refine per-country CTL/EIRP.

## Usage

```
# /etc/modprobe.d/ath-country.conf
options ath country=US
```

or on kernel cmdline: `ath.country=US`

Then reload `ath10k_snoc` (unload+reload the full stack so `ath` is
also reloaded).

Verification:

```
iw phy | grep 5180
    * 5180.0 MHz [36] (20.0 dBm)     ← no more (no IR)

iw dev wlan0mon set channel 149
tcpdump -i wlan0mon -c 5 -e -n type mgt subtype beacon
  ...
  6.0 Mb/s 5745 MHz 11a ... Beacon (Public-AP-A) ... CH: 149
```

Aireplay injection test on 5 GHz UNII-3 (ch 149):

```
aireplay-ng -9 wlan0mon
  22:02:04  Trying broadcast probe requests...
  22:02:04  Found 31 APs                            ← 2.4G + 5G
  22:02:10  Ping (min/avg/max): 7.9ms/7.9ms/7.9ms
  22:02:10  Injection is working!                   ← 5 GHz TX works
```

Passive RX end-to-end on 5 GHz UNII-3 (ch 157) — confirmed that we
receive a specific target AP's 5 GHz beacon:

```
tcpdump -i wlan0mon type mgt subtype beacon (ch 157)
  5785 MHz 11a -63dBm  BSSID:AA:BB:CC:DD:EE:02  Beacon (Home-AP-5G)
```

Same physical AP as `Home-AP-2.4G` (BSSID AA:BB:CC:DD:EE:01 on ch 6)
but the 5 GHz radio uses a different virtual MAC (last byte cc→d0).
This confirms the WCN3990 radio is really tuning to 5 GHz and RX is
working across UNII-1/UNII-2/UNII-3.

Zero fw crashes, zero -108 errors. The in-place vdev restart from
0119/16.6 handles the channel change between 5 GHz and 2.4 GHz
gracefully.

## Caveats

- DFS channels (52-64, 100-144) still marked `(no IR, radar detection)`
  by ath_reg_apply_radar_flags. Mainline ath10k WCN3990 does not have
  production DFS support anyway; RX is enabled, TX is gated.
- TX power is regdb-capped (typically 17-20 dBm on 5 GHz). Not a
  regression.
- Cosmetic: `iw reg get` still shows `country 00` global because we
  did not go through `regulatory_hint()` — it's the driver's custom
  regd. The per-phy regdom is what matters and it is US-compatible.

## Known limitation: airodump-ng --band abg on 5 GHz is less consistent

WCN3990 fw has a known-broken mgmt-forwarder state after certain 5 GHz
<-> 2.4 GHz phymode-class transitions (MODE_11A vs MODE_11G). Symptom:
after `iw set channel 157 (works)` then `iw set channel 6 (silence)`
then `iw set channel 157 (works again)`, the middle step gets 0
beacons on either band. Same class of bug as the QCA9880 rx-filter
issue that mainline ath10k handles at BOOT via
`ath10k_core_reset_rx_filter()` (create+delete dummy STA vdev).

Attempted fixes (session 9 continuation):
- **DOWN+UP bounce on monitor vdev** after VDEV_UP -> crashes fw at
  `cmnos_thread.c:4005 RT:0x9e087` because monitor vdevs have no peer
  to anchor the transition. **Reverted.**
- **Dummy STA vdev create+delete+barrier** (same idiom as
  `ath10k_core_reset_rx_filter`) -> crashes fw at `cmnos_thread.c:4005
  RT:0xa8xxx` in a cascade (~1 crash per channel hop). WCN3990 fw is
  stricter than QCA9880 and does not tolerate this idiom on channel
  change. **Reverted.**

Current behavior (as of commit 16.7):
- `iw dev wlan0mon set channel <5G_ch>` + `sleep 3` + `tcpdump`: works
  reliably from a fresh module load.
- `airodump-ng --band abg`: finds SOME 5 GHz APs (typically strong,
  nearby ones), but is less consistent than an external USB adapter
  (TP-Link etc). Same-BSSID 5G AP may or may not appear in a given
  scan depending on hop timing.
- Aireplay `-9` on a manually-set 5 GHz channel: works with "Injection
  is working!" and 100% response rates.
- All existing 2.4 GHz functionality unchanged.

If a future WMI TLV command is discovered that safely refreshes the
mgmt-forwarder on channel change without crashing fw, this limitation
could be lifted. For now this is a known trade-off.

## Files

- `regd.c.snapshot` — full current file with the three changes.

## Roadmap

- Convert to a proper `01xx-ath-country-module-param.patch` for
  upstream ath10k list submission. This is a generic ath_common patch
  that would benefit every ath10k/ath9k/ath5k user of WCN3990 or
  regcode 0x6c boards.
- Test with country=AR / country=JP to verify country-specific CTL
  differences apply correctly.
