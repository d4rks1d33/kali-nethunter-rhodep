/*
 * iface.c — radio control, monitor TX, uplink-radio safety gate.
 *
 * KALI/AARCH64 ADAPTATION (same approach as the NANO port):
 *   - Channel set via nl80211 (NL80211_CMD_SET_CHANNEL) with an `iw` fallback.
 *   - Monitor TX (deauth/inject) via libpcap (pcap_inject) on the Archer USB
 *     monitor iface (wlan1mon). DLT_IEEE802_11_RADIO.
 *
 * THE RULE OF GOLD (ARQUITECTURA-PORT.md §3):
 *   The internal wlan0 (WCN3990) carries the phone's internet and the AP; it is
 *   NEVER placed into monitor mode (that tears the LAN down). Every TX/monitor
 *   path here runs over the DEDICATED Archer (wlan1mon). If the Archer is not
 *   plugged in, iface_tx_open() fails gracefully and radio handlers respond with
 *   a basic error instead of crashing.
 */
#include "pager.h"
#include <pcap/pcap.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <errno.h>

#include <netlink/netlink.h>
#include <netlink/genl/genl.h>
#include <netlink/genl/ctrl.h>
#include <linux/nl80211.h>
#include <net/if.h>

static pcap_t *g_tx_pcap = NULL;   /* TX handle on the monitor iface */

/* -------------------- helpers ------------------------------------------- */
static int chan_to_freq(int ch)
{
    if (ch >= 1 && ch <= 13) return 2407 + ch * 5;
    if (ch == 14)            return 2484;
    if (ch >= 36)            return 5000 + ch * 5;
    return 0;
}

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

int iface_set_channel_on(const char *ifname, int ch)
{
    int freq = chan_to_freq(ch);
    if (freq == 0 || !ifname || !*ifname) return -1;

    struct nl_ctx c;
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
    char cmd[128];
    snprintf(cmd, sizeof cmd, "iw dev %s set channel %d >/dev/null 2>&1", ifname, ch);
    return system(cmd);
}

/* -------------------- uplink-radio safety gate -------------------------- */
int iface_is_uplink_radio(const char *ifname)
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
        if (fgets(up, sizeof up, p)) up[strcspn(up, "\r\n")] = '\0';
        pclose(p);
    }
    if (up[0] && strcmp(up, base) == 0) return 1;

    /* (b) managed + associated STA */
    char cmd[160];
    snprintf(cmd, sizeof cmd,
             "iw dev %s info 2>/dev/null | grep -q 'type managed' && "
             "iw dev %s link 2>/dev/null | grep -qi 'Connected to'", base, base);
    if (system(cmd) == 0) return 1;

    return 0;
}

bool iface_exists(const char *ifname)
{
    if (!ifname || !*ifname) return false;
    if (if_nametoindex(ifname) != 0) return true;
    /* also accept a monitor iface that `iw dev` lists but isn't up */
    char cmd[128];
    snprintf(cmd, sizeof cmd, "iw dev 2>/dev/null | grep -qw 'Interface %s'", ifname);
    return system(cmd) == 0;
}

void iface_monitor_status(char *out, size_t cap)
{
    pager_config_t *c = config_get();
    if (iface_exists(c->monitor_iface))
        snprintf(out, cap, "present (%s)", c->monitor_iface);
    else if (iface_exists(PINEAPD_MONITOR_BASE))
        snprintf(out, cap, "base %s up, monitor %s absent",
                 PINEAPD_MONITOR_BASE, c->monitor_iface);
    else
        snprintf(out, cap, "absent (no Archer USB)");
}

/* -------------------- monitor TX (pcap_inject) -------------------------- */
int iface_tx_open(void)
{
    if (g_tx_pcap) return 0;
    pager_config_t *c = config_get();
    const char *mon = c->monitor_iface;

    if (iface_is_uplink_radio(mon)) {
        pineap_log("IFACE",
                   "monitor iface %s maps onto the uplink radio; refusing TX "
                   "(connect the Archer USB on wlan1)", mon);
        return -1;
    }
    if (!iface_exists(mon)) {
        pineap_log("IFACE", "monitor iface %s not present; TX unavailable", mon);
        return -1;
    }
    char errbuf[PCAP_ERRBUF_SIZE] = {0};
    g_tx_pcap = pcap_open_live(mon, 2048, 1, 300, errbuf);
    if (!g_tx_pcap) {
        pineap_log("IFACE", "failed to open TX handle on %s: %s", mon, errbuf);
        return -1;
    }
    pcap_set_datalink(g_tx_pcap, DLT_IEEE802_11_RADIO);
    pineap_log("IFACE", "monitor TX ready on %s", mon);
    return 0;
}

void iface_tx_close(void)
{
    if (g_tx_pcap) { pcap_close(g_tx_pcap); g_tx_pcap = NULL; }
}

int iface_tx_inject(const uint8_t *buf, size_t len)
{
    if (!g_tx_pcap) {
        if (iface_tx_open() != 0) return -1;
    }
    return pcap_inject(g_tx_pcap, buf, len);
}
