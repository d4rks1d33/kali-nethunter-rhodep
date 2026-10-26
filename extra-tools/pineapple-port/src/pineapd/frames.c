/*
 * frames.c — 802.11 frame construction (replaces the Hak5 libwifi fork's
 *            gen_beacon / gen_deauth / gen_disassoc + radiotap TX header).
 *
 * The original pineapd linked a Hak5 fork of libwifi exposing gen_beacon,
 * beacon_frame_size, dump_beacon_frame, finalize_beacon, set_ssid, gen_deauth,
 * gen_disassoc (INFORME §D.1). We reimplement the subset pineapd actually uses
 * with a self-contained 802.11 + radiotap builder. This is more robust on
 * aarch64/Kali than porting the MIPS .so, and avoids the ABI mismatch.
 *
 * Frames are emitted with a minimal radiotap TX header (same DLT the monitor
 * interface expects, DLT_IEEE802_11_RADIO) so pcap_inject works on mac80211.
 */
#include "pineapd.h"
#include <stdlib.h>
#include <string.h>

/* Minimal radiotap header for injection: just present=0 (no fields). mac80211
 * fills in rate/tx flags from defaults. 8 bytes: version(1) pad(1) len(2) present(4) */
static size_t put_radiotap(uint8_t *buf)
{
    buf[0] = 0x00;            /* version */
    buf[1] = 0x00;            /* pad     */
    buf[2] = 0x08; buf[3] = 0x00; /* length = 8 (LE) */
    buf[4] = 0x00; buf[5] = 0x00; buf[6] = 0x00; buf[7] = 0x00; /* present = 0 */
    return 8;
}

/* 802.11 MAC header for management frames (24 bytes) */
static size_t put_mgmt_hdr(uint8_t *buf, uint16_t fc,
                           const mac_t *dst, const mac_t *src, const mac_t *bssid,
                           uint16_t seq)
{
    size_t o = 0;
    buf[o++] = fc & 0xff;         /* frame control (LE) */
    buf[o++] = (fc >> 8) & 0xff;
    buf[o++] = 0x00; buf[o++] = 0x00;   /* duration */
    memcpy(buf + o, dst->b, 6);   o += 6;   /* addr1 = dst */
    memcpy(buf + o, src->b, 6);   o += 6;   /* addr2 = src */
    memcpy(buf + o, bssid->b, 6); o += 6;   /* addr3 = bssid */
    buf[o++] = (seq << 4) & 0xff; /* seq ctrl (LE): frag=0, seq */
    buf[o++] = (seq >> 4) & 0xff;
    return o;
}

/*
 * build_beacon — radiotap + beacon frame carrying `ssid`.
 * FC: type=0 (mgmt), subtype=8 (beacon) => 0x0080
 */
uint8_t *build_beacon(const mac_t *src, const mac_t *bssid, const char *ssid,
                      uint8_t channel, uint16_t beacon_interval_tu, size_t *len)
{
    static uint16_t seq = 0;
    size_t ssid_len = ssid ? strlen(ssid) : 0;
    if (ssid_len > 32) ssid_len = 32;

    /* radiotap(8) + mgmt hdr(24) + fixed(12) + SSID tag(2+n) +
       rates tag(2+8) + DS tag(2+1) */
    size_t cap = 8 + 24 + 12 + (2 + ssid_len) + (2 + 8) + (2 + 1);
    uint8_t *buf = malloc(cap);
    if (!buf) { *len = 0; return NULL; }
    size_t o = put_radiotap(buf);

    mac_t bcast = {{0xff,0xff,0xff,0xff,0xff,0xff}};
    o += put_mgmt_hdr(buf + o, 0x0080, &bcast, src, bssid, seq++);

    /* fixed params: timestamp(8) beacon interval(2) capabilities(2) */
    memset(buf + o, 0, 8); o += 8;
    buf[o++] = beacon_interval_tu & 0xff;
    buf[o++] = (beacon_interval_tu >> 8) & 0xff;
    buf[o++] = 0x01; buf[o++] = 0x04;   /* capabilities: ESS + short preamble */

    /* SSID element (id 0) */
    buf[o++] = 0x00; buf[o++] = (uint8_t)ssid_len;
    if (ssid_len) { memcpy(buf + o, ssid, ssid_len); o += ssid_len; }

    /* Supported rates (id 1): 1,2,5.5,11,6,9,12,18 Mbps */
    buf[o++] = 0x01; buf[o++] = 0x08;
    buf[o++] = 0x82; buf[o++] = 0x84; buf[o++] = 0x8b; buf[o++] = 0x96;
    buf[o++] = 0x0c; buf[o++] = 0x12; buf[o++] = 0x18; buf[o++] = 0x24;

    /* DS parameter set (id 3): current channel */
    buf[o++] = 0x03; buf[o++] = 0x01; buf[o++] = channel;

    *len = o;
    return buf;
}

static uint8_t *build_dauth_like(uint16_t subtype_fc, const mac_t *dst,
                                 const mac_t *src, const mac_t *bssid,
                                 uint16_t reason, size_t *len)
{
    static uint16_t seq = 0;
    size_t cap = 8 + 24 + 2;  /* radiotap + hdr + reason code */
    uint8_t *buf = malloc(cap);
    if (!buf) { *len = 0; return NULL; }
    size_t o = put_radiotap(buf);
    o += put_mgmt_hdr(buf + o, subtype_fc, dst, src, bssid, seq++);
    buf[o++] = reason & 0xff;         /* reason code (LE) */
    buf[o++] = (reason >> 8) & 0xff;
    *len = o;
    return buf;
}

/* deauth: type=0 subtype=12 => FC 0x00C0 */
uint8_t *build_deauth(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                      uint16_t reason, size_t *len)
{
    return build_dauth_like(0x00C0, dst, src, bssid, reason, len);
}

/* disassoc: type=0 subtype=10 => FC 0x00A0 */
uint8_t *build_disassoc(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                        uint16_t reason, size_t *len)
{
    return build_dauth_like(0x00A0, dst, src, bssid, reason, len);
}
