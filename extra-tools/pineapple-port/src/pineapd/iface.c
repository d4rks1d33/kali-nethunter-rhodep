/*
 * iface.c — capture / injection / channel control.
 *
 * KALI/AARCH64 ADAPTATION (vs original OpenWrt MIPS):
 *   The original used WEXT ioctls (SIOCSIWFREQ via iwreq) to set the channel
 *   and assumed the interface was already in monitor mode (wlan1mon via
 *   airmon-ng). On modern Kali/mac80211 WEXT is deprecated; we use nl80211 via
 *   libnl to set channel and (best-effort) monitor mode. We fall back to the
 *   `iw` command if the nl80211 calls fail, which is always present on Kali.
 *
 *   Capture + injection still use libpcap (pcap_open_live / pcap_inject) on the
 *   monitor interface, identical to the original (DLT_IEEE802_11_RADIO, no BPF).
 *
 * Threads: this module owns the capture thread (interface_run) which also
 * drains the inject queue — matching the original architecture (INFORME §H.1).
 */
#include "pineapd.h"
#include <pcap/pcap.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>

#include <netlink/netlink.h>
#include <netlink/genl/genl.h>
#include <netlink/genl/ctrl.h>
#include <linux/nl80211.h>
#include <net/if.h>

static pcap_t          *g_pcap = NULL;
static pthread_t        g_cap_thread;
static volatile bool    g_cap_run = false;
static char             g_ifname[64];

/* 2.4GHz channel list for hopping (recon). 5GHz added when band allows. */
static const int CHANS_24[]  = {1,6,11,2,7,3,8,4,9,5,10};
static const int CHANS_5[]   = {36,40,44,48,149,153,157,161,165};

/* -------------------- nl80211 channel control --------------------------- */
struct nl_ctx { struct nl_sock *sk; int id; };

static int nl_init(struct nl_ctx *c)
{
    c->sk = nl_socket_alloc();
    if (!c->sk) return -1;
    if (genl_connect(c->sk)) { nl_socket_free(c->sk); c->sk = NULL; return -1; }
    c->id = genl_ctrl_resolve(c->sk, "nl80211");
    if (c->id < 0) { nl_socket_free(c->sk); c->sk = NULL; return -1; }
    return 0;
}

static void nl_fini(struct nl_ctx *c)
{
    if (c->sk) { nl_socket_free(c->sk); c->sk = NULL; }
}

static int chan_to_freq(int ch)
{
    if (ch >= 1 && ch <= 13) return 2407 + ch * 5;
    if (ch == 14)            return 2484;
    if (ch >= 36)            return 5000 + ch * 5;
    return 0;
}

/* set channel via nl80211 NL80211_CMD_SET_CHANNEL; fallback to `iw` */
static int nl_set_channel(const char *ifname, int ch)
{
    struct nl_ctx c;
    int freq = chan_to_freq(ch);
    if (freq == 0) return -1;
    if (nl_init(&c) == 0) {
        unsigned ifidx = if_nametoindex(ifname);
        struct nl_msg *msg = nlmsg_alloc();
        int ret = -1;
        if (msg && ifidx) {
            genlmsg_put(msg, 0, 0, c.id, 0, 0, NL80211_CMD_SET_CHANNEL, 0);
            nla_put_u32(msg, NL80211_ATTR_IFINDEX, ifidx);
            nla_put_u32(msg, NL80211_ATTR_WIPHY_FREQ, freq);
            nla_put_u32(msg, NL80211_ATTR_WIPHY_CHANNEL_TYPE, NL80211_CHAN_NO_HT);
            ret = nl_send_auto(c.sk, msg);
            if (ret >= 0) ret = nl_wait_for_ack(c.sk);
        }
        if (msg) nlmsg_free(msg);
        nl_fini(&c);
        if (ret == 0) return 0;
    }
    /* fallback: iw (always available on Kali) */
    char cmd[128];
    snprintf(cmd, sizeof cmd, "iw dev %s set channel %d >/dev/null 2>&1", ifname, ch);
    return system(cmd);
}

int iface_set_channel(int channel)
{
    scan_state_t *s = scan_state_get();
    pthread_mutex_lock(&s->lock);
    s->channel = channel;
    pthread_mutex_unlock(&s->lock);
    return nl_set_channel(g_ifname, channel);
}

/*
 * is_uplink_radio() -- true if `ifname` (or its base, if it ends in "mon") is
 * the phone's internet-carrying STA. We must NEVER flip that radio to monitor:
 * doing so kills the phone's WiFi and (because the phone is a client of the
 * home router) looks like a network-wide outage. See net/pineapd-iface.sh for
 * the matching shell-level guard.
 *
 * Detection (best-effort, via the `iw`/`ip` CLIs which are always on Kali):
 *   (a) the iface carries the default route to the Internet, OR
 *   (b) it is a managed wlan that is currently associated ("Connected to").
 */
static int is_uplink_radio(const char *ifname)
{
    char base[64];
    strncpy(base, ifname, sizeof base - 1);
    base[sizeof base - 1] = '\0';
    size_t n = strlen(base);
    if (n > 3 && strcmp(base + n - 3, "mon") == 0) base[n - 3] = '\0';

    /* (a) default-route iface to the Internet */
    char up[64] = {0};
    FILE *p = popen("ip route get 1.1.1.1 2>/dev/null | "
                    "sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -n1", "r");
    if (p) {
        if (fgets(up, sizeof up, p)) {
            up[strcspn(up, "\r\n")] = '\0';
        }
        pclose(p);
    }
    if (up[0] && strcmp(up, base) == 0) return 1;

    /* (b) managed + associated STA */
    char cmd[128];
    snprintf(cmd, sizeof cmd,
             "iw dev %s info 2>/dev/null | grep -q 'type managed' && "
             "iw dev %s link 2>/dev/null | grep -qi 'Connected to'", base, base);
    if (system(cmd) == 0) return 1;

    return 0;
}

/*
 * Best-effort monitor mode via `iw` (Kali-friendly) -- but REFUSES to touch the
 * phone's uplink STA. Returns 0 if the iface is a usable dedicated monitor
 * device (or already monitor), -1 if it is the uplink radio (caller must then
 * skip pcap capture and rely on scan_sta for recon).
 */
static int ensure_monitor(const char *ifname)
{
    if (is_uplink_radio(ifname)) {
        pineap_log("INTERFACE",
                   "capture iface %s maps onto the phone's uplink radio; "
                   "refusing monitor mode (recon uses STA scan). Plug in a "
                   "dedicated adapter (wlan1 Archer) for capture/deauth.",
                   ifname);
        return -1;
    }
    char cmd[256];
    snprintf(cmd, sizeof cmd,
             "iw dev %s set type monitor >/dev/null 2>&1; "
             "ip link set %s up >/dev/null 2>&1", ifname, ifname);
    if (system(cmd) == -1) { /* best-effort monitor setup */ }
    return 0;
}

/* -------------------- capture / inject thread --------------------------- */
int iface_inject(const uint8_t *buf, size_t len)
{
    if (!g_pcap) return -1;
    return pcap_inject(g_pcap, buf, len);
}

static void drain_inject_queue(void)
{
    inject_frame_t *f;
    while ((f = inject_queue_pop()) != NULL) {
        if (f->channel) iface_set_channel(f->channel);
        uint32_t reps = f->multiplier ? f->multiplier : 1;
        for (uint32_t i = 0; i < reps; i++) {
            iface_inject(f->payload, f->len);
            if (f->delay_us) usleep(f->delay_us);
        }
        inject_frame_free(f);
    }
}

static void *interface_run(void *arg)
{
    (void)arg;
    scan_state_t *s = scan_state_get();
    int ci24 = 0, ci5 = 0;
    struct timespec last_hop; clock_gettime(CLOCK_MONOTONIC, &last_hop);

    while (g_cap_run) {
        /* channel hopping during recon (if not paused) */
        pthread_mutex_lock(&s->lock);
        bool hopping = s->hopping && !s->paused;
        bool handshake = s->handshake_running;
        int band = s->freq_band;
        pthread_mutex_unlock(&s->lock);

        if (hopping && !handshake) {
            struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
            long dms = (now.tv_sec - last_hop.tv_sec) * 1000 +
                       (now.tv_nsec - last_hop.tv_nsec) / 1000000;
            if (dms >= 250) {
                int ch;
                if (band == 1) { ch = CHANS_5[ci5++ % (int)(sizeof CHANS_5/sizeof CHANS_5[0])]; }
                else if (band == 2) {
                    if ((ci24 + ci5) & 1) ch = CHANS_5[ci5++ % (int)(sizeof CHANS_5/sizeof CHANS_5[0])];
                    else ch = CHANS_24[ci24++ % (int)(sizeof CHANS_24/sizeof CHANS_24[0])];
                } else ch = CHANS_24[ci24++ % (int)(sizeof CHANS_24/sizeof CHANS_24[0])];
                iface_set_channel(ch);
                last_hop = now;
            }
        }

        /* capture one frame (non-blocking-ish via to_ms=300 in open_live) */
        struct pcap_pkthdr *hdr = NULL;
        const u_char *data = NULL;
        int rc = pcap_next_ex(g_pcap, &hdr, &data);
        if (rc == 1 && hdr && data) {
            long ts_us = (long)hdr->ts.tv_sec * 1000000 + hdr->ts.tv_usec;
            parser_submit(data, hdr->caplen, ts_us, 0);
        } else if (rc < 0) {
            /* capture error — brief backoff */
            usleep(1000);
        }

        /* drain pending injections (beacons/deauth) */
        drain_inject_queue();
        usleep(500);
    }
    return NULL;
}

int iface_start(const char *ifname)
{
    strncpy(g_ifname, ifname, sizeof g_ifname - 1);
    g_ifname[sizeof g_ifname - 1] = '\0';

    /* NEVER put the phone's uplink STA into monitor mode. If this iface is the
     * phone's internet radio, skip pcap capture entirely: recon still works via
     * the native STA scan (scan_sta), and the daemon keeps serving its socket/DB.
     * Monitor capture (handshake/deauth) requires a DEDICATED adapter. */
    if (ensure_monitor(g_ifname) != 0) {
        pineap_log("INTERFACE",
                   "monitor capture disabled on %s (uplink radio protected); "
                   "recon via STA scan, no handshake/deauth until a dedicated "
                   "capture adapter is configured", g_ifname);
        return -1;  /* main.c tolerates this: socket/DB still served */
    }

    char errbuf[PCAP_ERRBUF_SIZE] = {0};
    /* snaplen 2048, promisc, to_ms=300 — matches original (INFORME §E.1) */
    g_pcap = pcap_open_live(g_ifname, 2048, 1, 300, errbuf);
    if (!g_pcap) {
        pineap_log("INTERFACE", "Failed to create capture handle for %s: %s",
                   g_ifname, errbuf);
        return -1;
    }
    /* ensure monitor DLT if available */
    pcap_set_datalink(g_pcap, DLT_IEEE802_11_RADIO);

    g_cap_run = true;
    if (pthread_create(&g_cap_thread, NULL, interface_run, NULL) != 0) {
        pineap_log("INTERFACE", "failed to start capture thread");
        g_cap_run = false;
        pcap_close(g_pcap); g_pcap = NULL;
        return -1;
    }
    pineap_log("INTERFACE", "capture started on %s", g_ifname);
    return 0;
}

void iface_stop(void)
{
    if (!g_cap_run) return;
    g_cap_run = false;
    pthread_join(g_cap_thread, NULL);
    if (g_pcap) { pcap_close(g_pcap); g_pcap = NULL; }
}
