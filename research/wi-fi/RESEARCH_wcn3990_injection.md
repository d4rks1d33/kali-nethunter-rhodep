# WCN3990 Monitor-Mode Injection - Research Report

Date: 2026-09-06
Scope: Loukious (Wael Hasnaoui) follow-up + all public WCN3990 injection work

---

## 1. Executive summary

The "hidden STA + vdev_id swap" trick you're trying to port IS the correct
approach, and Loukious has quietly evolved it a lot since the 4.14 commit
you're basing on. The most important discovery in this research is:

1. **The Medium article's decompilation description is technically imprecise**
   about what the firmware check actually is. The `sVar10 != 2 && sVar10 != 6`
   is described as "VDEV Mode", but Loukious's own working code creates a vdev
   of type `WMI_VDEV_TYPE_STA` (mode = 0), NOT AP (2) or MONITOR (6). The check
   that actually matters is **peer existence at fw offset `vdev + 0xc`**, i.e.
   the self-peer created by `WMI_PEER_CREATE_CMDID`. Vdev *type* is secondary.

2. **Loukious ships a 5-part patch, not a 3-part one**. The 4.14 commit you
   have (`65c6a05`) is 44 files / 11 800 lines and does much more than just
   swap `vdev_id` on WMI mgmt-tx. In particular, it also patches:
   - The DP (data-path) TX pool globals so mgmt frames get a descriptor pool
     even when `is_mgmt_over_wmi_enabled == 1` (this is the fix at
     `ol_txrx_flow_control.c`).
   - HDD monitor-mode session management (`wlan_hdd_frame_inject.c` -
     1 824 lines of glue).
   - `is_mgmt_over_wmi_enabled` default reset in `ol_txrx_pdev_attach`.
   - Global `pdev->mgmt_pool` allocation regardless of the WMI-mgmt-tx service
     bit (`ol_tx_register_flow_control`).
   - Legacy fallback path if the WMI cmd send fails (dropped in current
     revision - see item 4 below).

3. **There is a newer 2026 GKI 6.6 revision** for WCN7750 (Poco F7) that
   differs in real ways from the 4.14 revision, and reveals what turned out
   to matter in later firmware families:
   `Loukious/vendor_qcom_opensource_wlan @ 25875eb1a65e94fae404c6e9188c7a1fe679e0f5`
   - Routes injection through the **DP TX** path (nbuf marker
     `DP_TX_INJECTION_DESC_PREFIX = 0xf000`) and hooks `dp_tx_comp_free_buf` /
     `dp_tx_comp_process_tx_status` to call back into
     `wma_injection_dp_complete`. In the 4.14 revision he goes through WMI
     mgmt-tx only.
   - Adds `dp_refresh_monitor_mode` and forces
     `pfilter->tlv_filter.fp_mgmt_filter = FILTER_MGMT_ALL` on WCN7750 so
     RX mgmt frames aren't dropped by the monitor filter (RX-side fix, not
     TX, but confirms filter tweaks are chip-family specific).
   - Forces `wlan_mlme_is_sta_mon_conc_supported() = true` unconditionally
     so a real standalone monitor VIF gets the full "STA + monitor" concurrent
     lifecycle.

4. **The 4.14 tree got a subtle post-write-up cleanup**. On the `nethunter`
   branch of `Loukious/android_kernel_xiaomi_sm8150`, sha `29da85f7d95` (Aug
   2026) is nearly identical to the `65c6a05` commit dated 2024, but includes
   small revisions:
   - `wma_frame_inject.c:1497`: forces `tx_type = GENERIC_NODOWLOAD_ACK_COMP_INDEX`
     for consistency ("Several firmware builds are stricter with probe-request
     tx_type handling").
   - `wma_frame_inject.c:1471`: for monitor + probe-req + broadcast DA,
     it **overwrites SA in place** to match the vdev MAC (both nbuf and req
     buffers), because "firmware discards when SA doesn't match transmitting
     vdev MAC".
   - `wma_frame_inject.c:1443`: hard-refuses to inject on a monitor vdev
     whose `interfaces[vdev_id].mhz` is 0 (i.e. channel unset - DISCARD hint).
   - Patch banner: `"Injection patch tag: monitor_sta_vdev_tx_v7"` - it's
     up to v7 revisions internally.

5. **hcxdumptool's author (ZerBea) still lists qcacld-3.0 as unsupported**
   (Nov 2025 to Aug 2026), so nobody outside the Loukious cluster has this
   working. This means our best signal is Loukious's own patches (both
   revisions) + brokestar233's OnePlus 13 (WCN7750-era) work, which
   independently confirms that deauth/disassoc will hit a stricter fw path.

---

## 2. Loukious follow-up work - full inventory of URLs

### 2.1 Kernel repos owned by Loukious that touch injection

- Main 4.14 kernel (POCO X3 Pro / vayu, sm8150, **WCN3998** - NOT WCN3990 but
  same firmware family): https://github.com/Loukious/android_kernel_xiaomi_sm8150
  - Branches: `ci` (default, no commit history), `nethunter` (has the code),
    `16.0`, and `patches` (holds standalone `.patch` files)
  - Original commit (referenced in article):
    https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1
  - Latest on nethunter (Aug 2026): sha `29da85f7d95064cc7ba28d5e2e37b7a13a5b8ba4`
  - Standalone patch (what he tells outsiders to use):
    https://github.com/Loukious/android_kernel_xiaomi_sm8150/blob/patches/patches/nethunter/07_packet_injection.patch

- Newer 6.6 GKI patch for Poco F7 / Redmi Turbo 4 Pro (onyx, SM8750,
  **WCN7750** - different chip, but same "hidden vdev" mechanism):
  - Kernel: https://github.com/Loukious/konoha-kernel-gki
    - Branch: `nethunter-ksunext-upstream`
    - Commits with tag `wlan:` and `wifi:` are the injection ones
  - Actual WLAN module changes:
    https://github.com/Loukious/vendor_qcom_opensource_wlan
    - Key commit for injection: `25875eb1a65e94fae404c6e9188c7a1fe679e0f5`
      ("wlan: add monitor mode and direct packet injection")
    - Parent 0b310fa is a `MiCode/vendor_qcom_opensource_wlan` fork base for
      the `onyx-v-oss` branch

### 2.2 Public Q&A where Loukious posted after the article

- hcxdumptool discussion #531 (this is where he links the newer patch):
  https://github.com/ZerBea/hcxdumptool/discussions/531
  - His June-16 2026 reply: `#discussioncomment-17324397`
  - His Aug-4 2026 reply pointing to `konoha-kernel-gki` and
    `vendor_qcom_opensource_wlan @ 25875eb1`: `#discussioncomment-17893205`

### 2.3 Loukious's LinkedIn / socials (if you want to contact him)

- GitHub: https://github.com/Loukious (196 followers, active daily)
- Twitch: https://www.twitch.tv/LoukiousTN
- LinkedIn: https://www.linkedin.com/in/wael-hasnaoui/
- Location: Tunisia
- No Twitter/X handle visible on his profile

### 2.4 Nothing else from Loukious matters

Everything else in his 85 repos is unrelated (Discord mods, TikTok stream key
generators, Elden Ring Nightreign mods, Stremio patches). He has forks of
`rtl8812au`, `rtl8814au`, `nethunter_kernel_oneplus_sdm845`, `bluebinder` and
`badbt` but they're all vanilla upstream mirrors with no injection work.

---

## 3. Other researchers working on qcacld / WCN3990 injection

### 3.1 kimocoder (Christian Bjerre)

Author of aircrack-ng's Android/Kali NetHunter port and de-facto QCACLD
monitor-mode maintainer.

- https://github.com/kimocoder/qualcomm_android_monitor_mode - the canonical
  "how to enable QCACLD monitor mode on Android" repo (356 stars). NO
  injection work - just monitor-mode enable trick
  (`echo 4 > /sys/module/wlan/parameters/con_mode`).
- https://github.com/kimocoder/ath10k-firmware - fork of the QCA firmware
  drop. WCN3990 tree only has:
  - `WCN3990/hw1.0/HL2.0/WLAN.HL.2.0-01387-QCAHLSWMTPLZ-1/`
  - `WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-00784-QCAHLSWMTPLZ-1/`
  - `WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-00959-QCAHLSWMTPLZ-1/`
  - `WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-01040-QCAHLSWMTPLZ-1/`
  - **NO HL.3.3.x builds are public**. Ours is WLAN.HL.3.3.2 - Loukious's
    was HL.3.1 or HL.2.0-era. This is a real difference.
- His OnePlus Nord monitor patch (irrelevant to injection but referenced in
  the hcxdumptool discussion):
  https://github.com/kimocoder/android_kernel_oneplus_avicii/commit/a8ac9d836544cbaa4e1a58a9cfc0f1e3548dcb9e
  (one-liner adds `case QDF_MONITOR_MODE: return true` in `hdd_is_client_mode`).
- Twitter: https://twitter.com/kimocoder
- **Most valuable person to contact** - he has hardware to try things on and
  runs the community list of "who has monitor mode working".

### 3.2 brokestar233 (OnePlus 13, WCN7750-era)

- Independent reverse-engineered fw report (Aug 2026), most relevant quote:
  https://github.com/ZerBea/hcxdumptool/discussions/531#discussioncomment-17893098
  > "7 out of 67 frame types failed to transmit and were dropped by the NIC-
  > specifically the deauth and disassoc management frames. Firmware
  > reverse-engineering revealed that deauth and disassoc frames are routed
  > to a separate firmware execution path. This path strictly requires valid,
  > active peer, vdev, and session objects to dispatch management frames."
- His port attempt:
  https://github.com/brokestar233/android_kernel_modules_and_devicetree_oneplus_sm8750/commit/be87f121e5c53b1693662248da8a28a37f6b19db
- **Kernel-panic reproduction** for WCN7750 with wrong TLV assumptions:
  https://github.com/ZerBea/hcxdumptool/discussions/531#discussioncomment-16031148
  (`cnss_pci_device_crashed` -> `cmnos_assert_patched.c:604`)

### 3.3 dr1408 (mystery user reporting "it worked")

- Reported hcxdumptool captures 6 handshakes on Loukious's port
  (Mar 2026). Named as "referenced by brokestar for porting".
- https://github.com/dr1408
- His repo trail is thin; likely just applied Loukious's patch.

### 3.4 ZerBea (Michael Woelfel)

Author of hcxdumptool/hcxtools. Has consistently said qcacld-3.0 is broken
for injection. His last words on it (Aug 2026):
> "Unfortunately frame injection is still unsupported by the driver."
Blames driver + firmware combo. He is the reference for "what a working
injection driver has to look like" via hcxdumptool's radiotap checks.
- Twitter/mail unknown but active on GitHub daily.
- Relevant kernel wireless docs he cites:
  - https://wireless.docs.kernel.org/en/latest/en/users/drivers/ath10k.html
  - https://wireless.docs.kernel.org/en/latest/en/users/drivers/ath10k/monitor.html

### 3.5 MOHAMMAD RASIM

Only public poster on the ath10k mailing list asking about WCN3990 monitor
mode (Aug 2024): https://www.mail-archive.com/ath10k@lists.infradead.org/msg16830.html
- No follow-up work published. Kalle Valo replied "I doubt that WCN3990
  firmware supports monitor mode, though just guessing here."

### 3.6 postmarketOS motorola-rhodep porter

- https://github.com/d4rks1d33/postmarketos-motorola-rhodep - active as of
  Sep 2 2026. This is likely your parallel or your own workspace.

### 3.7 Nobody else

Extensive searching (Google, DDG, Reddit r/kalilinux/r/wireshark/r/AsahiLinux,
kismet-forum, hackaday, hackster, wigle, github code search for
`WCN3990 monitor`, `qcacld-3.0 inject`, `ath10k mgmt-tx`) returns basically
only:
- Loukious cluster
- kimocoder + brokestar233
- The ath10k mailing-list thread above
- One openembedded/yocto changelog reference to the a618e2069783 commit
  (`ath10k: skip resetting rx filter for WCN3990`) which is an RX-side fix,
  not TX.

No DEFCON / BlackHat / USENIX / ACSAC papers on this. This is genuinely a
niche corner.

---

## 4. Alternative firmware builds to consider

### 4.1 What's in the public firmware repos

**kimocoder/ath10k-firmware master (HEAD 2024-04):**
```
WCN3990/hw1.0/HL2.0/WLAN.HL.2.0-01387-QCAHLSWMTPLZ-1/
WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-00784-QCAHLSWMTPLZ-1/
WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-00959-QCAHLSWMTPLZ-1/
WCN3990/hw1.0/HL3.1/WLAN.HL.3.1-01040-QCAHLSWMTPLZ-1/
```

**gitlab kernel-firmware/linux-firmware ath10k/WCN3990/hw1.0 (current):**
```
board-2.bin
firmware-5.bin          <- generic HL.3.1
notice.txt_wlanmdsp
wlanmdsp.mbn
qcm2290/               <- SoC-specific override
   firmware-5.bin
   wlanmdsp.mbn
```

Ours (rhodep, SM6375) uses HL.3.3.2. There is **no HL.3.3 published to
linux-firmware or kimocoder**. Only sources of HL.3.3.x builds:
- The Android vendor blob for the exact device (Motorola rhodep OTA image
  under `/vendor/firmware/wlan/` or `/vendor/firmware_mnt/image/`).
- Cross-device blobs from other WCN3990-in-SM6xxx phones: try
  - Xiaomi curtana/gram (SM6350, Redmi Note 9 Pro)
  - Xiaomi lisa (SM7325 Lahaina but same WCN3990 family)
  - Realme narzo 30 (SM6375 exactly)
  - Motorola cebu / capri (SM6350)
- Extract via android_dumps or dumpyara mirrors on github.com/dumpyara-project.

### 4.2 Options worth trying (ordered by risk)

1. **Downgrade to HL.3.1-01040 from kimocoder/ath10k-firmware.**
   Loukious tested against this exact family. Risk: rhodep-specific BDF (board
   data file) mismatch means WiFi may not init at all. Mitigate by keeping the
   stock board-2.bin.
2. **Try HL.2.0-01387.** Older, may be looser about vdev-type checks (older
   fw builds were before the `sVar10 != 2 && sVar10 != 6` firewall was
   tightened - some 2018-era HL.2.0 might not check at all).
3. **Sideload another OEM's HL.3.3.x wlanmdsp.mbn** (Motorola cebu, or Xiaomi
   curtana). Same major family as ours; the vdev/peer TLV layout will match.
4. **Do NOT** try HL.3.5+ or WLAN.HL.4.x - those are for WCN6750/WCN6855,
   won't load on hw1.0 SoC.

### 4.3 WMI service bit differences to check between fw variants

When we load a new firmware we should log the WMI service bitmap and diff
against ours. Key bits Loukious's code checks (in his `wma_frame_inject.c`
around line 1555):
- `wmi_service_mgmt_tx_wmi` - if 0 he warns but still attempts
- `WMI_SERVICE_MONITOR_MODE_ENABLED` (0x?)
- `WMI_SERVICE_PACKET_CAPTURE_SUPPORT` (0x?)
- `WMI_SERVICE_FRAME_INJECTION` if any firmware advertises it (unclear)

We should dump ours and compare. If any HL.3.3.x build advertises a service
bit that our current one doesn't, we've likely found "hidden" fw features.

---

## 5. Code / approaches you likely have NOT tried yet

### 5.1 The three most important things missing from a 4.14 mechanical port

Looking at diffs between Loukious's 4.14 commit and his newer 25875eb:

a. **Force the mgmt tx pool to always exist**
   `ol_txrx_flow_control.c` change - `ol_tx_register_global_mgmt_pool` must
   run **regardless** of the `is_mgmt_over_wmi_enabled` service bit.
   ```c
   /* Loukious 4.14: */
   ol_tx_register_global_mgmt_pool(pdev);   // unconditional
   ol_txrx_info("flowctl init: global mgmt pool ptr=%pK", pdev->mgmt_pool);
   ```
   If your mainline ath10k doesn't have this pool at all, the fallback path
   Loukious added would silently fail. ath10k-mainline uses a very different
   TX-desc allocator - you can't 1-to-1 port; you need the equivalent guarantee
   that mgmt-frame descriptors are always available on the monitor vdev.

b. **SA rewrite for broadcast probe requests on monitor vdev**
   ```c
   if (monitor_vdev && is_probe_req && is_bcast_da && req->frame_len >= 24) {
       vdev_mac = wlan_vdev_mlme_get_macaddr(interfaces[vdev_id].vdev);
       if (memcmp(frame + 10, vdev_mac, 6) != 0)
           memcpy(frame + 10, vdev_mac, 6);
   }
   ```
   Firmware discards the frame if SA (addr2) doesn't match the vdev MAC. This
   is why some injected probe-reqs go through and others don't. **This is the
   most likely explanation for our status=1 DISCARD** if we're injecting
   frames with arbitrary SA (as most aireplay/hcxdumptool tools do).

c. **Explicit `tx_type = GENERIC_NODOWLOAD_ACK_COMP_INDEX`**
   His code hardcodes tx_type=GENERIC_NODOWLOAD_ACK_COMP_INDEX (not the
   default). Mainline ath10k passes tx_type differently through htt - we may
   be passing a tx_type that firmware treats as "beacon-only" which triggers
   the DISCARD path.

### 5.2 Things Loukious does NOT need but mainline might need

Loukious uses `wmi_mgmt_unified_cmd_send()` which is qcacld-3.0's helper
that builds the WMI TLV block for him. On mainline ath10k **we build TLVs
ourselves** in `ath10k_wmi_op_gen_mgmt_tx_send()` (10.x) or
`ath10k_wmi_tlv_op_gen_mgmt_send()`. Differences to double-check against a
wireshark of Loukious's traffic (if we can get him to share one) or against
qcacld's `wmi_unified_tlv.c`:

- The `WMI_TLV_TAG_STRUC_wmi_mgmt_tx_send_cmd_fixed_param` layout
- Whether `chanfreq` is set to 0 for probe-req (his article says YES, mainline
  ath10k may set it always)
- Whether `desc_id` uses the same allocation range as normal mgmt tx
- Whether `tx_params_valid` is 0 (his default) - mainline may be setting rate
  bits from radiotap header

### 5.3 A completely different angle: the DP-TX path (his newer approach)

His WCN7750/6.6 code routes injection **not through WMI mgmt-tx** but through
the data-path with a magic descriptor ID prefix (`0xf000`). This is closer to
what real APs do internally. Mainline ath10k does have a monitor TX ndo, but
it does NOT flag the frame as mgmt. If the mgmt-tx (WMI) path is
fundamentally broken on our firmware, we might have better luck:

1. Send the frame as a DATA frame via the normal HTT TX path on the hidden STA
   vdev
2. Bit-flip the 802.11 header FrameControl field between what the driver sees
   and what the firmware DMAs
3. Ideally never let the frame reach the "is this a mgmt frame" check.

This is speculative but is what his 25875eb commit does.

### 5.4 The "peer at vdev + 0xc" trap more explicitly

The article says firmware peeks `vdev + 0xc`. In practice this means the
`WMI_PEER_CREATE_CMDID` for the hidden STA must have completed and the
firmware must have populated the peer object BEFORE the first mgmt-tx.
Loukious's code does `msleep(150)` after CREATE, `msleep(150)` after START,
`msleep(100)` after PEER_CREATE. **If our port lacks those sleeps or fires
mgmt-tx before the peer-create completion event, firmware will happily
return status=1 DISCARD** because vdev+0xc will still be a null/stale peer.

Test in dmesg: look for `WMI_PEER_CREATE_COMPLETE_EVENTID` before the mgmt
tx. If the peer-create event isn't there, we're racing.

### 5.5 The `!VDEV_UP` detail

Loukious explicitly **skips** `WMI_VDEV_UP_CMDID` for the hidden STA vdev.
Comment in his code:
```
/* Skip VDEV_UP.  For STA vdevs, firmware's wlan_vdev_up asserts unless
 * a BSS peer (the AP) exists — we only have a self-peer.  The mgmt TX
 * handler only needs the vdev in STARTED state with a valid peer at
 * vdev+0xc. */
```
If our port issues VDEV_UP on the hidden STA (e.g. because mainline's
vdev-start helper does it automatically), firmware will assert and the
whole card will reset. Check `dmesg` after hidden-STA creation for
`WMI_VDEV_UP` before mgmt-tx.

### 5.6 vdev-slot ID selection

His code walks the vdev slots **top-down** from `CFG_TGT_NUM_VDEV - 2` and
picks the first free one that isn't the monitor vdev:
```c
int fw_max_vid = CFG_TGT_NUM_VDEV - 2;   /* typically 4 - 2 = 2 */
for (i = fw_max_vid; i >= 0; i--) {
    if ((uint8_t)i == mon_vdev_id) continue;
    if (!wma->interfaces[i].vdev) { vid = i; break; }
}
```
Note the -2 (not -1): "num_vdevs may be decremented by 1 for NAN, use
CFG_TGT_NUM_VDEV - 2 as safe ceiling". If we're picking `vdev_id = num_vdevs - 1`
we may be trampling the NAN slot and firmware silently discards mgmt tx from it.

### 5.7 Explicit channel handling for probe-req vs. action

Loukious's code sets `mgmt_params.chanfreq = is_probe_req ? 0 : tx_chanfreq`
in the general case, but then overrides to `tx_chanfreq` again if it's a
monitor probe-req. Firmware seems to expect chanfreq=0 for host-driven probe
requests but non-zero for monitor-driven ones. If we hardcode either way we
get DISCARD.

---

## 6. Hypotheses for why our port doesn't radiate

Ranked by likelihood, based on what we now know Loukious does that a naive
port omits:

1. **We're not creating a hidden self-peer** or it's not completed when we
   fire mgmt-tx. Firmware reads `vdev + 0xc`, sees stale/null peer, returns
   status=1 DISCARD. FIX: verify PEER_CREATE_COMPLETE_EVENTID arrived and
   add explicit wait/msleep.

2. **We're not swapping vdev_id** in the outgoing WMI-mgmt-tx TLV. The
   monitor vdev_id survives into the WMI cmd, firmware sees a MONITOR-type
   vdev (mode 6? or actually the `_wlan_send_mgmt_to_host` beacon-only path
   Loukious's comment mentions), and DISCARDs. FIX: after building the WMI
   TLV block, patch `wmi_mgmt_tx_send_cmd_fixed_param.vdev_id`.

3. **SA in the frame doesn't match the hidden STA MAC**. Loukious rewrites
   SA for broadcast probe requests specifically. If our test frames all
   have random SAs (aireplay style), firmware DISCARDs. FIX: rewrite SA to
   the hidden STA's locally-administered MAC before submitting.

4. **We inherit VDEV_UP on the hidden STA** (mainline vdev-start helper is
   auto-doing it). Firmware asserts internally and the mgmt-tx path is dead
   silent afterwards. FIX: manually build vdev-create/start WMI cmds without
   going through the helpful `ath10k_mac_vif_up` path.

5. **Mainline TLV framing differs from qcacld's**. E.g. missing or extra
   TLV-header padding in `WMI_MGMT_TX_SEND_CMDID`. FIX: capture the exact
   bytes Loukious's driver puts on the wire (write a small ftrace on his
   kernel), compare to our TLV output byte-for-byte.

6. **Our HL.3.3.2 fw has stricter checks than Loukious's HL.3.1**. Some
   fields (retry count, tx_power, rate mask) that HL.3.1 tolerated as 0 now
   trigger DISCARD if unset. FIX: verify `mgmt_params.tx_params_valid = false`
   equivalent on our side, or set explicit defaults.

7. **We didn't allocate a global mgmt TX descriptor pool.** ath10k mainline
   doesn't have `pdev->mgmt_pool` per se, but the equivalent (`ath10k_htt`
   mgmt-tx pending list) needs enough slots or firmware silently drops. FIX:
   check `htt->pending_tx` capacity.

---

## 7. Recent QCA CLD-3.0 upstream (2024-2026)

CodeLinaro `clo/la/platform/vendor/qcom-opensource/wlan/qcacld-3.0`:
- Full branch list (100+, all with tag `LA.UM.` for legacy or `LA.VENDOR.` for
  Waipio/Kailua). Nothing named `*inject*`, nothing named `*monitor*`, no
  branch mentioning WCN3990 specifically.
- The r60-era branch kimocoder points at is `LA.UM.8.9.r1-04400-SM6xx.0-1`-era
  (Snapdragon 6-series, matches ours). Worth diffing our qcacld against
  https://git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/wlan/qcacld-3.0/-/tree/LA.UM.9.14.1.c30
  for the "packet capture" component changes:
  - `components/pkt_capture/core/inc/wlan_pkt_capture_mon_thread.h` L124
  - `components/pkt_capture/core/inc/wlan_pkt_capture_data_txrx.h` L36
  - `components/pkt_capture/core/src/wlan_pkt_capture_main.c` L255
- No new "WCN3990 firmware fix" landed in 2024-2026 that I could find on
  codelinaro. Qualcomm has effectively frozen WCN3990 in the CLD tree.

---

## 8. Postmarket / Alpine / GrapheneOS / CalyxOS notes

- **postmarketOS**: `d4rks1d33/postmarketos-motorola-rhodep` (Sep 2 2026)
  is the only rhodep-specific pmOS repo. No injection patches present.
- **AsahiLinux**: N/A (M1/M2 target, uses Broadcom fullmac).
- **GrapheneOS/CalyxOS**: no injection patches. Both use vendor-provided
  qcacld unchanged.
- **msm8916-mainline** and **z3ntu** (Luca Weiss): work on WCN36x0 (older
  chip, ath9k-family) - not applicable to WCN3990.

---

## 9. Ordered action list

1. Fetch Loukious's `wma_frame_inject.c` and `wlan_hdd_frame_inject.c` from
   `nethunter` branch (already in /tmp/opencode/loukious/) and re-read
   sections 300-400 (hidden vdev create) and 1400-1620 (WMI mgmt-tx path)
   with fresh eyes. These are the two functions that actually matter.
2. In our port, add `pr_info` at every step of hidden-vdev create/start/peer
   and dump WMI service bitmap. Confirm PEER_CREATE_COMPLETE arrives before
   we fire the first inject.
3. Wireshark the exact bytes we put on WMI-mgmt-tx and compare to a diff of
   qcacld's tlv builder (`wmi_unified_tlv.c :: send_mgmt_cmd_tlv`).
4. Verify SA-rewrite for broadcast probe-req.
5. Verify we're NOT doing VDEV_UP on the hidden STA.
6. If all above are correct and we still get DISCARD: try swapping
   `firmware-5.bin` for kimocoder's HL.3.1-01040 build.
7. If still DISCARD: try HL.2.0-01387 (older, looser).
8. If still DISCARD: try approach from Loukious's newer 25875eb - DP-TX path
   with a marker prefix (mainline ath10k has an equivalent via
   `ieee80211_ops.tx()` with `IEEE80211_TX_CTL_INJECTED` flag).
9. Reach out to Loukious via GitHub issue on `konoha-kernel-gki` (he's active
   there) - specifically ask for a wireshark dump of a working WMI mgmt-tx
   on WCN3998 so we can byte-diff.
10. Reach out to kimocoder on Twitter/GitHub. He has the hardware inventory
    and might have unpublished notes.

---

## 10. People to contact

Rank ordered by likelihood of useful response:

1. **Wael Hasnaoui / Loukious** - has the exact code working, has been active
   in the hcxdumptool discussion as recently as Aug 2026, and has offered
   help ("Get yourself Codex, give it the article + patches"). Open an issue
   in https://github.com/Loukious/konoha-kernel-gki or ping via
   https://www.linkedin.com/in/wael-hasnaoui/. Ask specifically for the wire
   bytes.
2. **kimocoder (Christian Bjerre)** - maintains monitor-mode community,
   probably has hardware to test. https://github.com/kimocoder ,
   https://twitter.com/kimocoder.
3. **brokestar233** - has recent (Aug 2026) fw reverse-engineering notes
   specifically on which frame types make it through. https://github.com/brokestar233.
4. **ZerBea (hcxdumptool)** - won't debug your code but WILL tell you
   authoritatively if hcxdumptool's radiotap checks match what we're
   producing. https://github.com/ZerBea .
5. **Kalle Valo** - upstream ath10k maintainer. He said (Aug 2024) "I doubt
   WCN3990 firmware supports monitor mode" - if we can prove otherwise we
   have a very good story to pitch. https://lore.kernel.org/ath10k/ .
6. **Luca Weiss (z3ntu)** - not directly relevant but has broadest experience
   with mainline-porting Qualcomm phones and knows people. https://github.com/z3ntu.

---

## 11. Key URLs summary (bookmark these)

Loukious code (canonical):
- https://github.com/Loukious/android_kernel_xiaomi_sm8150/blob/nethunter/drivers/staging/qcacld-3.0/core/wma/src/wma_frame_inject.c
- https://github.com/Loukious/android_kernel_xiaomi_sm8150/blob/nethunter/drivers/staging/qcacld-3.0/core/wma/inc/wma_frame_inject.h
- https://github.com/Loukious/android_kernel_xiaomi_sm8150/blob/nethunter/drivers/staging/qcacld-3.0/core/hdd/src/wlan_hdd_frame_inject.c
- https://github.com/Loukious/android_kernel_xiaomi_sm8150/blob/patches/patches/nethunter/07_packet_injection.patch
- https://github.com/Loukious/vendor_qcom_opensource_wlan/commit/25875eb1a65e94fae404c6e9188c7a1fe679e0f5

Community discussions:
- https://github.com/ZerBea/hcxdumptool/discussions/531 (57 replies, very active)
- https://github.com/kimocoder/qualcomm_android_monitor_mode
- https://github.com/ZerBea/hcxdumptool/issues/332
- https://www.mail-archive.com/ath10k@lists.infradead.org/msg16830.html

Firmware:
- https://github.com/kimocoder/ath10k-firmware/tree/master/WCN3990/hw1.0
- https://gitlab.com/kernel-firmware/linux-firmware/-/tree/main/ath10k/WCN3990/hw1.0
- https://git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/wlan/qcacld-3.0 (source only, no firmware)

Original article (mirror via jina.ai to bypass Medium paywall):
- https://r.jina.ai/https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702
