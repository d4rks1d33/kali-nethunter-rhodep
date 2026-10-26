/*
 * recon_mon.c — REAL over-the-air recon on the dedicated Archer (wlan1mon).
 *
 * WHY THIS EXISTS
 * ---------------
 * The firmware's Recon screen (Hak5 Pager) shows: packets-per-second, a 45s
 * activity histogram, and a live list of nearby networks + their clients that
 * the user can select to examine / deauth. All of that needs MONITOR mode with
 * channel hopping across the whole band — which the internal wlan0 cannot do
 * (it is the SSH uplink STA, pinned to its 5GHz home channel, and must NEVER be
 * put in monitor; see ARQUITECTURA-PORT.md §3 "rule of gold").
 *
 * The dedicated Archer (RTL8811AU, phy1 / wlan1) is free and supports monitor
 * + 2.4GHz (1..13) + 5GHz. This module drives it:
 *
 *   recon_mon_start(scan_id):
 *     1. create wlan1mon (iw phy phyN interface add wlan1mon type monitor) and
 *        bring it up — ONLY on the Archer, after a hard refusal to touch the
 *        uplink radio.
 *     2. spawn a libpcap capture thread (DLT_IEEE802_11_RADIO) that parses
 *        radiotap + 802.11 and feeds recon.db:
 *          - beacons / probe-responses -> APs  (ssid,bssid,channel,signal,enc)
 *          - data / QoS-data / probe-req frames -> CLIENTS, mapped to their AP
 *            BSSID when the frame's addressing reveals it.
 *        every captured frame bumps a PPS counter.
 *     3. spawn a 1Hz PPS sampler that fills a 45-slot ring buffer (the firmware
 *        histogram is -45s..now).
 *     4. spawn a channel-hop thread: if the firmware locked a channel we stay,
 *        otherwise we iterate 2.4GHz then a set of common 5GHz channels.
 *
 *   recon_mon_stop(): join threads, tear wlan1mon down cleanly (like openap's
 *   teardown) so the Archer is freed and never left orphaned. NEVER wlan0.
 *
 * We DO NOT reimplement the Pager UI or logic. The firmware polls the socket
 * (cmd 208/211/216/...) and we answer with the REAL air it asked for.
 */
#include "pager.h"
#include <pcap/pcap.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <ctype.h>
#include <time.h>
#include <pthread.h>
#include <stdatomic.h>

/* ----- encryption INT values (match scan_sta.c / panel expectation) ------- */
enum { ENC_OPEN = 0, ENC_WEP = 1, ENC_WPA = 2, ENC_WPA2 = 3, ENC_WPA3 = 4 };

/* ----- PPS history ring (the firmware's activity histogram is ~45s) ------- */
#define PPS_HISTORY   45

/* ----- module state ------------------------------------------------------- */
static pcap_t          *g_cap      = NULL;   /* capture handle on wlan1mon    */
static pthread_t        g_cap_th;
static pthread_t        g_pps_th;
static pthread_t        g_hop_th;
static volatile bool    g_run      = false;
static bool             g_cap_started = false;
static bool             g_pps_started = false;
static bool             g_hop_started = false;
static int              g_scan_id  = -1;
static bool             g_created_mon = false;  /* did WE create wlan1mon?    */
static char             g_mon[64]  = {0};

/* PPS: total frames this second (atomic); history ring + current pps. */
static atomic_uint      g_pkt_accum = 0;       /* frames since last sample    */
static atomic_uint      g_pps_now   = 0;       /* last full-second PPS value   */
static atomic_ullong    g_pkt_total = 0;       /* total frames since start     */
static atomic_uint      g_pps_avg   = 0;       /* running average PPS          */
static unsigned         g_pps_ring[PPS_HISTORY];
static int              g_pps_head  = 0;        /* next write slot              */
static pthread_mutex_t  g_pps_lock  = PTHREAD_MUTEX_INITIALIZER;

/* Channel lock: if the firmware fixes a channel (recon lock / examine), we
 * stop hopping and sit there. 0 => hop. */
static atomic_int       g_lock_channel = 0;

/* ----- Write throttle / in-memory dedup -----------------------------------
 * APs beacon ~10x/sec each; with dozens of APs that is hundreds of DB INSERTs
 * per second, each grabbing the global DB mutex. That starves the recon-list
 * reader on the socket thread -> the firmware's "recon stable list" request
 * times out (i/o timeout) and the AP list never shows. We keep a small in-mem
 * cache keyed by BSSID/MAC and only hit the DB when an entry is NEW or hasn't
 * been flushed in WRITE_THROTTLE_SECS. This cuts DB writes by ~20-50x while the
 * data stays fresh. */
#define WRITE_THROTTLE_SECS 3
#define SEEN_MAX 1024
typedef struct { char key[20]; long last_write; } seen_ent_t;
static seen_ent_t       g_seen[SEEN_MAX];
static int              g_seen_n = 0;
static pthread_mutex_t  g_seen_lock = PTHREAD_MUTEX_INITIALIZER;

/* Returns true if we should write this key to the DB now (new or stale). */
static bool should_write(const char *key, long now)
{
    bool do_write = true;
    pthread_mutex_lock(&g_seen_lock);
    for (int i = 0; i < g_seen_n; i++) {
        if (!strcmp(g_seen[i].key, key)) {
            if (now - g_seen[i].last_write < WRITE_THROTTLE_SECS)
                do_write = false;           /* seen recently -> skip DB */
            else
                g_seen[i].last_write = now;  /* stale -> refresh + write */
            pthread_mutex_unlock(&g_seen_lock);
            return do_write;
        }
    }
    /* new key */
    if (g_seen_n < SEEN_MAX) {
        strncpy(g_seen[g_seen_n].key, key, sizeof g_seen[0].key - 1);
        g_seen[g_seen_n].key[sizeof g_seen[0].key - 1] = '\0';
        g_seen[g_seen_n].last_write = now;
        g_seen_n++;
    } else {
        /* table full: recycle the oldest slot */
        int oldest = 0;
        for (int i = 1; i < g_seen_n; i++)
            if (g_seen[i].last_write < g_seen[oldest].last_write) oldest = i;
        strncpy(g_seen[oldest].key, key, sizeof g_seen[0].key - 1);
        g_seen[oldest].key[sizeof g_seen[0].key - 1] = '\0';
        g_seen[oldest].last_write = now;
    }
    pthread_mutex_unlock(&g_seen_lock);
    return true;
}

/* Reset the dedup cache (call on recon start so a fresh scan re-writes all). */
static void seen_reset(void)
{
    pthread_mutex_lock(&g_seen_lock);
    g_seen_n = 0;
    pthread_mutex_unlock(&g_seen_lock);
}

/* Last time the firmware polled recon_stats (cmd 208). The idle watchdog uses
 * this to tear wlan1mon down after the firmware leaves the Recon screen. */
static atomic_llong     g_last_poll = 0;

/* Idle watchdog: a dedicated thread that tears recon down when the firmware
 * stops polling (leaves Recon). It is self-contained so teardown does NOT
 * depend on the single-threaded socket accept loop (which blocks on a kept-open
 * firmware connection). Seconds of no-poll before teardown: */
#define RECON_IDLE_SECS 20
static pthread_t        g_wd_th;
static bool             g_wd_started = false;
static pthread_mutex_t  g_life_lock = PTHREAD_MUTEX_INITIALIZER;
static void stop_workers_locked(void);  /* fwd */

/* ------------------------------------------------------------------------- *
 *  helpers
 * ------------------------------------------------------------------------- */
static void mac_fmt(const uint8_t *m, char out[18])
{
    snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
             m[0], m[1], m[2], m[3], m[4], m[5]);
}

static bool mac_is_bcast(const uint8_t *m)
{
    return m[0]==0xff && m[1]==0xff && m[2]==0xff &&
           m[3]==0xff && m[4]==0xff && m[5]==0xff;
}
static bool mac_is_zero6(const uint8_t *m)
{
    return !(m[0]|m[1]|m[2]|m[3]|m[4]|m[5]);
}
/* multicast/group bit set in the first octet (not a real station) */
static bool mac_is_group(const uint8_t *m) { return m[0] & 0x01; }

static int freq_to_channel(int mhz)
{
    if (mhz == 2484) return 14;
    if (mhz >= 2412 && mhz <= 2472) return (mhz - 2407) / 5;   /* 1..13     */
    if (mhz >= 5160 && mhz <= 5885) return (mhz - 5000) / 5;   /* 32..177   */
    if (mhz >= 5955 && mhz <= 7115) return (mhz - 5950) / 5;   /* 6GHz      */
    return 0;
}

/* Derive the phy index backing a base iface (e.g. wlan1 -> "phy1"). Falls back
 * to the configured value or phy1. Reads /sys so it is robust. */
static void base_phy(const char *base, char *out, size_t cap)
{
    char path[128], buf[64] = {0};
    snprintf(path, sizeof path, "/sys/class/net/%s/phy80211/name", base);
    FILE *f = fopen(path, "r");
    if (f) {
        if (fgets(buf, sizeof buf, f)) buf[strcspn(buf, "\r\n")] = '\0';
        fclose(f);
    }
    if (buf[0]) { snprintf(out, cap, "%s", buf); return; }
    snprintf(out, cap, "phy1");
}

/* ------------------------------------------------------------------------- *
 *  radiotap: find the 802.11 frame start and pull signal(dBm) if present.
 *  Minimal, endian-correct parse of the standard radiotap fields we need.
 * ------------------------------------------------------------------------- */
#define RT_TSFT            0
#define RT_FLAGS           1
#define RT_RATE            2
#define RT_CHANNEL         3
#define RT_FHSS            4
#define RT_DBM_ANTSIGNAL   5

static uint16_t rd16(const uint8_t *p){ return (uint16_t)(p[0] | (p[1]<<8)); }
static uint32_t rd32(const uint8_t *p){
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}

/* Returns offset of the 802.11 header, or -1. Fills *signal (dBm) if the
 * DBM_ANTSIGNAL field is present (else leaves it untouched) and *freq (MHz)
 * from the CHANNEL field if present. */
static int radiotap_parse(const uint8_t *buf, int len, int *signal, int *freq)
{
    if (len < 8) return -1;
    if (buf[0] != 0) return -1;                 /* version 0 only */
    uint16_t rt_len = rd16(buf + 2);
    if (rt_len < 8 || rt_len > len) return -1;

    /* The FIRST present word defines the standard-namespace fields. When bit 31
     * is set, more present words follow (extended/vendor namespaces); the field
     * data begins only AFTER all chained present words. We must parse fields
     * using the first word's flags, not the last. */
    uint32_t present0 = rd32(buf + 4);   /* field flags we actually read */
    uint32_t present  = present0;
    const uint8_t *p = buf + 8;
    const uint8_t *end = buf + rt_len;

    /* skip any extended present bitmaps to find where field data starts */
    while (present & (1u << 31)) {
        if (p + 4 > end) return rt_len;         /* give up on fields, hdr ok */
        present = rd32(p);
        p += 4;
    }
    present = present0;                         /* parse with first word's bits */

    /* field alignment/size table for the bits we read */
    for (int bit = 0; bit <= RT_DBM_ANTSIGNAL; bit++) {
        if (!(present & (1u << bit))) continue;
        switch (bit) {
        case RT_TSFT:   /* u64, align 8 */
            while ((size_t)(p - buf) & 7) p++;
            p += 8; break;
        case RT_FLAGS:  p += 1; break;
        case RT_RATE:   p += 1; break;
        case RT_CHANNEL: /* u16 freq + u16 flags, align 2 */
            while ((size_t)(p - buf) & 1) p++;
            if (p + 2 <= end && freq) *freq = rd16(p);
            p += 4; break;
        case RT_FHSS:   p += 2; break;
        case RT_DBM_ANTSIGNAL: /* s8 */
            if (p + 1 <= end && signal) *signal = (int8_t)*p;
            p += 1; break;
        }
        if (p > end) break;
    }
    return rt_len;
}

/* ------------------------------------------------------------------------- *
 *  802.11 parsing — beacons/probe-resp -> AP; data/probe-req -> client.
 * ------------------------------------------------------------------------- */
/* Parse the tagged IEs of a beacon / probe-response body for SSID + crypto. */
static void parse_beacon_ies(const uint8_t *ie, int ielen,
                             char ssid[64], bool *hidden, int *channel,
                             int *enc, int *wps)
{
    bool privacy_cap = false, have_rsn = false, have_wpa = false, rsn_sae = false;
    ssid[0] = '\0'; *hidden = true;

    const uint8_t *p = ie;
    const uint8_t *end = ie + ielen;
    while (p + 2 <= end) {
        uint8_t id = p[0], l = p[1];
        const uint8_t *v = p + 2;
        if (v + l > end) break;
        switch (id) {
        case 0: /* SSID */
            if (l == 0) { *hidden = true; }
            else {
                int n = l < 63 ? l : 63;
                bool all_nul = true;
                for (int i = 0; i < n; i++) { if (v[i]) all_nul = false; }
                if (all_nul) { *hidden = true; ssid[0] = '\0'; }
                else {
                    memcpy(ssid, v, n); ssid[n] = '\0'; *hidden = false;
                }
            }
            break;
        case 3: /* DS Parameter set: channel */
            if (l >= 1) *channel = v[0];
            break;
        case 48: /* RSN */
            have_rsn = true;
            /* scan AKM suites for SAE (00-0F-AC:8) => WPA3 */
            if (l >= 8) {
                /* version(2) groupcipher(4) pairwiseCount(2) ... */
                int off = 2 + 4;
                if (off + 2 <= l) {
                    int pcnt = v[off] | (v[off+1] << 8); off += 2 + 4 * pcnt;
                    if (off + 2 <= l) {
                        int akm = v[off] | (v[off+1] << 8); off += 2;
                        for (int i = 0; i < akm && off + 4 <= l; i++, off += 4) {
                            if (v[off]==0x00 && v[off+1]==0x0f && v[off+2]==0xac
                                && v[off+3]==8) rsn_sae = true;
                        }
                    }
                }
            }
            break;
        case 221: /* vendor: WPA (00-50-F2:1) or WPS (00-50-F2:4) */
            if (l >= 4 && v[0]==0x00 && v[1]==0x50 && v[2]==0xf2) {
                if (v[3] == 1) have_wpa = true;
                else if (v[3] == 4) *wps = 1;
            }
            break;
        }
        p += 2 + l;
    }

    (void)privacy_cap;
    if (have_rsn)      *enc = rsn_sae ? ENC_WPA3 : ENC_WPA2;
    else if (have_wpa) *enc = ENC_WPA;
    /* WEP vs OPEN is decided by the capability Privacy bit (set by caller). */
}

/* The main per-packet handler called by pcap_loop. */
static void on_packet(u_char *ud, const struct pcap_pkthdr *h, const u_char *bytes)
{
    (void)ud;
    atomic_fetch_add_explicit(&g_pkt_accum, 1, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_pkt_total, 1, memory_order_relaxed);

    int caplen = (int)h->caplen;
    int signal = 0, rt_freq = 0;
    int rt = radiotap_parse(bytes, caplen, &signal, &rt_freq);
    if (rt < 0) return;

    const uint8_t *f = bytes + rt;
    int flen = caplen - rt;
    if (flen < 10) return;

    uint16_t fc = rd16(f);
    uint8_t type    = (fc >> 2) & 0x3;
    uint8_t subtype = (fc >> 4) & 0xf;
    uint8_t tods    = (fc >> 8) & 0x1;
    uint8_t fromds  = (fc >> 9) & 0x1;

    const uint8_t *a1 = f + 4;                  /* addr1 */
    const uint8_t *a2 = (flen >= 16) ? f + 10 : NULL;  /* addr2 */
    const uint8_t *a3 = (flen >= 22) ? f + 16 : NULL;  /* addr3 */

    long now = time(NULL);
    int chan_hint = rt_freq ? freq_to_channel(rt_freq) : atomic_load(&g_lock_channel);

    if (type == 0) {   /* management */
        if (subtype == 8 || subtype == 5) {     /* beacon / probe-response */
            /* fixed params: 12 bytes (timestamp8 + beaconint2 + caps2) then IEs */
            if (flen < 24 + 12) return;
            const uint8_t *bssid = a3 ? a3 : a2;
            if (!bssid || mac_is_zero6(bssid)) return;
            uint16_t caps = rd16(f + 24 + 10);
            bool privacy = (caps & 0x0010) != 0;

            const uint8_t *ies = f + 24 + 12;
            int ielen = flen - (24 + 12);
            char ssid[64]; bool hidden = true; int channel = chan_hint;
            int enc = ENC_OPEN, wps = 0;
            parse_beacon_ies(ies, ielen, ssid, &hidden, &channel, &enc, &wps);
            if (enc == ENC_OPEN && privacy) enc = ENC_WEP;  /* privacy, no RSN/WPA */
            if (channel <= 0) channel = chan_hint;

            char bs[18]; mac_fmt(bssid, bs);
            /* throttle: only hit the DB for new/stale APs (keeps the list
             * reader responsive; signal/last_seen stay fresh enough). */
            if (should_write(bs, now))
                recon_write_ap(g_scan_id, hidden ? "" : ssid, bs, enc,
                               hidden ? 1 : 0, channel, signal, wps, now);
            return;
        }
        if (subtype == 4) {                      /* probe-request => client */
            if (a2 && !mac_is_group(a2) && !mac_is_zero6(a2)) {
                char cs[18]; mac_fmt(a2, cs);
                if (should_write(cs, now))
                    recon_write_client(g_scan_id, cs, "", now);
            }
            return;
        }
        return;
    }

    if (type == 2) {   /* data (incl. QoS data) => associated client */
        /* Determine station MAC and its AP BSSID from the DS bits.
         *   toDS=1,fromDS=0: a1=BSSID, a2=STA(src), a3=dest
         *   toDS=0,fromDS=1: a1=STA(dst), a2=BSSID, a3=src
         *   toDS=0,fromDS=0: IBSS/mgmt-ish; a1=dst a2=src a3=BSSID
         */
        const uint8_t *sta = NULL, *bssid = NULL;
        if (tods && !fromds)      { bssid = a1; sta = a2; }
        else if (!tods && fromds) { bssid = a2; sta = a1; }
        else                      { bssid = a3; sta = a2; }

        if (sta && bssid && !mac_is_group(sta) && !mac_is_zero6(sta)
            && !mac_is_bcast(sta) && !mac_is_zero6(bssid)) {
            char cs[18], bs[18];
            mac_fmt(sta, cs); mac_fmt(bssid, bs);
            /* key on the client MAC (assoc is stable); throttle DB writes. */
            if (should_write(cs, now))
                recon_write_client(g_scan_id, cs, bs, now);
        }
        return;
    }
    /* type==1 (control) carries no useful addressing for recon; ignore. */
}

/* ------------------------------------------------------------------------- *
 *  capture thread
 * ------------------------------------------------------------------------- */
static void *cap_run(void *arg)
{
    (void)arg;
    char errbuf[PCAP_ERRBUF_SIZE] = {0};
    g_cap = pcap_open_live(g_mon, 2048, 1, 200, errbuf);
    if (!g_cap) {
        pineap_log("RECONMON", "pcap open on %s failed: %s", g_mon, errbuf);
        return NULL;
    }
    if (pcap_datalink(g_cap) != DLT_IEEE802_11_RADIO) {
        pcap_set_datalink(g_cap, DLT_IEEE802_11_RADIO);
    }
    pineap_log("RECONMON", "capture running on %s (scan_id=%d)", g_mon, g_scan_id);

    /* pcap_loop with a periodic breakout via pcap_dispatch so stop() is snappy */
    while (g_run) {
        int n = pcap_dispatch(g_cap, 64, on_packet, NULL);
        if (n < 0) break;        /* error */
        if (n == 0) usleep(20000);
    }
    pcap_close(g_cap);
    g_cap = NULL;
    pineap_log("RECONMON", "capture stopped");
    return NULL;
}

/* ------------------------------------------------------------------------- *
 *  PPS sampler: once per second, snapshot accumulator -> pps_now + ring.
 * ------------------------------------------------------------------------- */
static void *pps_run(void *arg)
{
    (void)arg;
    unsigned long long sum = 0; unsigned secs = 0;
    while (g_run) {
        sleep(1);
        unsigned c = atomic_exchange(&g_pkt_accum, 0);
        atomic_store(&g_pps_now, c);
        sum += c; secs++;
        atomic_store(&g_pps_avg, secs ? (unsigned)(sum / secs) : 0);
        pthread_mutex_lock(&g_pps_lock);
        g_pps_ring[g_pps_head] = c;
        g_pps_head = (g_pps_head + 1) % PPS_HISTORY;
        pthread_mutex_unlock(&g_pps_lock);
    }
    return NULL;
}

/* ------------------------------------------------------------------------- *
 *  channel hop thread
 * ------------------------------------------------------------------------- */
/* 2.4GHz 1..13 then common non-DFS 5GHz. DFS/radar channels are passive-only
 * and flaky to hop onto, so we keep the default plan to the reliable set and
 * still dwell on the 2.4GHz band where most nearby APs live. */
static const int HOP_CHANS[] = {
    1, 6, 11, 2, 7, 3, 8, 4, 9, 5, 10, 12, 13,      /* 2.4GHz */
    36, 40, 44, 48, 149, 153, 157, 161, 165          /* 5GHz non-DFS */
};
#define HOP_N ((int)(sizeof HOP_CHANS / sizeof HOP_CHANS[0]))

static void *hop_run(void *arg)
{
    (void)arg;
    pager_config_t *c = config_get();
    int i = 0;
    while (g_run) {
        int lock = atomic_load(&g_lock_channel);
        if (lock > 0) {
            iface_set_channel_on(g_mon, lock);
            /* sit on the locked channel; re-check lock state each ~300ms */
            for (int k = 0; k < 3 && g_run; k++) usleep(100000);
            continue;
        }
        int ch = HOP_CHANS[i % HOP_N];
        i++;
        /* skip 5GHz if the config bands string excludes it */
        if (ch >= 36 && c->bands[0] && !strstr(c->bands, "5")) continue;
        iface_set_channel_on(g_mon, ch);
        /* dwell: 2.4GHz gets a touch longer to catch sparse beacons */
        int dwell_ms = (ch <= 14) ? 250 : 180;
        for (int k = 0; k < dwell_ms/50 && g_run; k++) usleep(50000);
    }
    return NULL;
}

/* ------------------------------------------------------------------------- *
 *  monitor iface lifecycle (create / teardown) — Archer wlan1 ONLY.
 * ------------------------------------------------------------------------- */
static int monitor_up(void)
{
    pager_config_t *c = config_get();
    const char *mon  = c->monitor_iface[0] ? c->monitor_iface : PINEAPD_MONITOR_IFACE;
    const char *base = PINEAPD_MONITOR_BASE;

    /* HARD safety: never operate on the uplink/SSH radio. */
    if (iface_is_uplink_radio(mon) || iface_is_uplink_radio(base)) {
        pineap_log("RECONMON",
                   "refusing monitor: %s/%s maps onto the uplink radio", mon, base);
        return -1;
    }

    snprintf(g_mon, sizeof g_mon, "%s", mon);

    if (iface_exists(mon)) {
        /* already present (maybe left by a previous run); just bring it up. */
        char cmd[128];
        snprintf(cmd, sizeof cmd, "ip link set %s up >/dev/null 2>&1", mon);
        (void)!system(cmd);
        g_created_mon = false;
        pineap_log("RECONMON", "using existing monitor iface %s", mon);
        return 0;
    }

    /* create wlan1mon on the Archer's phy. Prefer not to delete wlan1 (openap
     * may want it) — adding a monitor VIF alongside works on rtw88 drivers. */
    char phy[32]; base_phy(base, phy, sizeof phy);
    char cmd[256];
    snprintf(cmd, sizeof cmd,
             "iw phy %s interface add %s type monitor >/dev/null 2>&1; "
             "ip link set %s up >/dev/null 2>&1",
             phy, mon, mon);
    (void)!system(cmd);

    if (!iface_exists(mon)) {
        /* fallback: some rtl drivers won't add a 2nd VIF; convert base in place */
        snprintf(cmd, sizeof cmd,
                 "ip link set %s down >/dev/null 2>&1; "
                 "iw dev %s set type monitor >/dev/null 2>&1; "
                 "ip link set %s up >/dev/null 2>&1",
                 base, base, base);
        (void)!system(cmd);
        if (iface_exists(base)) {
            snprintf(g_mon, sizeof g_mon, "%s", base);
            g_created_mon = false;  /* we converted base; restore on teardown */
            pineap_log("RECONMON", "monitor via in-place %s (no VIF support)", base);
            return 0;
        }
        pineap_log("RECONMON", "failed to create monitor iface %s", mon);
        return -1;
    }
    g_created_mon = true;
    pineap_log("RECONMON", "created monitor iface %s on %s", mon, phy);
    return 0;
}

static void monitor_down(void)
{
    if (!g_mon[0]) return;
    char cmd[256];
    if (g_created_mon) {
        /* we added a VIF: bring it down and delete it (frees the Archer). */
        snprintf(cmd, sizeof cmd,
                 "ip link set %s down >/dev/null 2>&1; "
                 "iw dev %s del >/dev/null 2>&1",
                 g_mon, g_mon);
        (void)!system(cmd);
        pineap_log("RECONMON", "torn down monitor VIF %s", g_mon);
    } else if (!strcmp(g_mon, PINEAPD_MONITOR_BASE)) {
        /* we converted the base iface in place: return it to managed. */
        snprintf(cmd, sizeof cmd,
                 "ip link set %s down >/dev/null 2>&1; "
                 "iw dev %s set type managed >/dev/null 2>&1",
                 g_mon, g_mon);
        (void)!system(cmd);
        pineap_log("RECONMON", "returned %s to managed", g_mon);
    }
    /* else: pre-existing wlan1mon we didn't create — leave it as found. */
    g_mon[0] = '\0';
    g_created_mon = false;
}

/* ------------------------------------------------------------------------- *
 *  public API
 * ------------------------------------------------------------------------- */
bool recon_mon_running(void) { return g_run; }

/* Record a firmware recon poll (cmd 208). */
void recon_note_poll(void) { atomic_store(&g_last_poll, (long long)time(NULL)); }

/* Seconds since the last firmware recon poll (LLONG for never-polled => huge). */
long recon_seconds_since_poll(void)
{
    long long t = atomic_load(&g_last_poll);
    if (t == 0) return -1;              /* never polled */
    return (long)(time(NULL) - (time_t)t);
}

void recon_mon_set_channel_lock(int channel)
{
    atomic_store(&g_lock_channel, channel > 0 ? channel : 0);
}

unsigned recon_mon_pps(void) { return atomic_load(&g_pps_now); }
unsigned recon_mon_pps_avg(void) { return atomic_load(&g_pps_avg); }
unsigned long long recon_mon_pkt_total(void) { return atomic_load(&g_pkt_total); }

/* Copy up to `cap` history samples (oldest..newest) into out. Returns count. */
int recon_mon_pps_history(unsigned *out, int cap)
{
    if (!out || cap <= 0) return 0;
    int n = cap < PPS_HISTORY ? cap : PPS_HISTORY;
    pthread_mutex_lock(&g_pps_lock);
    /* ring is written at g_pps_head (next slot); oldest is at head. */
    for (int i = 0; i < n; i++) {
        int idx = (g_pps_head - n + i + 2 * PPS_HISTORY) % PPS_HISTORY;
        out[i] = g_pps_ring[idx];
    }
    pthread_mutex_unlock(&g_pps_lock);
    return n;
}

/* Stop capture/pps/hop workers and tear the monitor down. Caller holds
 * g_life_lock. Does NOT touch the watchdog thread. Idempotent. */
static void stop_workers_locked(void)
{
    if (!g_run && !g_cap_started && !g_pps_started && !g_hop_started) {
        monitor_down();
        return;
    }
    g_run = false;
    if (g_cap) pcap_breakloop(g_cap);
    if (g_cap_started) { pthread_join(g_cap_th, NULL); g_cap_started = false; }
    if (g_pps_started) { pthread_join(g_pps_th, NULL); g_pps_started = false; }
    if (g_hop_started) { pthread_join(g_hop_th, NULL); g_hop_started = false; }
    monitor_down();
    atomic_store(&g_pps_now, 0);
    pineap_log("RECONMON", "recon monitor fully stopped");
}

/* Idle watchdog thread: self-contained teardown when the firmware stops polling
 * recon_stats (left the Recon screen). Detached-style: exits after it tears the
 * workers down, independent of the socket accept loop. */
static void *watchdog_run(void *arg)
{
    (void)arg;
    while (g_run) {
        sleep(1);
        if (!g_run) break;
        long idle = recon_seconds_since_poll();
        if (idle >= RECON_IDLE_SECS) {
            pineap_log("RECONMON",
                "firmware idle %lds (left Recon); tearing down wlan1mon", idle);
            pthread_mutex_lock(&g_life_lock);
            stop_workers_locked();
            g_wd_started = false;   /* we are exiting; stop() must not join us */
            pthread_mutex_unlock(&g_life_lock);
            scan_state_get()->recon_running = false;
            scan_sta_stop();
            return NULL;
        }
    }
    return NULL;
}

int recon_mon_start(int scan_id)
{
    pthread_mutex_lock(&g_life_lock);
    if (g_run) { g_scan_id = scan_id; pthread_mutex_unlock(&g_life_lock); return 0; }

    g_scan_id = scan_id;
    atomic_store(&g_pkt_accum, 0);
    atomic_store(&g_pps_now, 0);
    atomic_store(&g_pps_avg, 0);
    atomic_store(&g_pkt_total, 0);
    seen_reset();                      /* fresh scan re-writes all APs/clients */
    recon_note_poll();                 /* seed poll time so we don't insta-idle */
    pthread_mutex_lock(&g_pps_lock);
    memset(g_pps_ring, 0, sizeof g_pps_ring);
    g_pps_head = 0;
    pthread_mutex_unlock(&g_pps_lock);

    /* honor a fixed channel from config (0 => hop). */
    pager_config_t *c = config_get();
    atomic_store(&g_lock_channel, c->channel > 0 ? c->channel : 0);

    if (monitor_up() != 0) { pthread_mutex_unlock(&g_life_lock); return -1; }

    g_run = true;
    if (pthread_create(&g_cap_th, NULL, cap_run, NULL) == 0) g_cap_started = true;
    else { pineap_log("RECONMON", "capture thread failed"); }
    if (pthread_create(&g_pps_th, NULL, pps_run, NULL) == 0) g_pps_started = true;
    if (pthread_create(&g_hop_th, NULL, hop_run, NULL) == 0) g_hop_started = true;
    if (pthread_create(&g_wd_th,  NULL, watchdog_run, NULL) == 0) g_wd_started = true;

    pineap_log("RECONMON", "recon monitor started on %s (scan_id=%d)", g_mon, scan_id);
    pthread_mutex_unlock(&g_life_lock);
    return 0;
}

void recon_mon_stop(void)
{
    /* Join the watchdog first (outside g_life_lock to avoid a deadlock if it is
     * mid-teardown). Snapshot whether it is active under the lock. */
    pthread_t wd; bool join_wd = false;
    pthread_mutex_lock(&g_life_lock);
    if (g_wd_started) { wd = g_wd_th; join_wd = true; g_wd_started = false; }
    /* Signal workers to stop so the watchdog's sleep loop also exits. */
    bool was_run = g_run;
    g_run = false;
    if (g_cap) pcap_breakloop(g_cap);
    pthread_mutex_unlock(&g_life_lock);

    if (join_wd) pthread_join(wd, NULL);

    pthread_mutex_lock(&g_life_lock);
    stop_workers_locked();
    (void)was_run;
    pthread_mutex_unlock(&g_life_lock);
}
