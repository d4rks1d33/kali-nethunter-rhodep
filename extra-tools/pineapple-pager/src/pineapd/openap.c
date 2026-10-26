/*
 * openap.c — see openap.h. Watches the firmware's /etc/config/wireless and
 * mirrors its radio0 AP VAP (Open AP / Evil WPA) onto a REAL host radio using
 * the native host hostapd. Pure bridge: we do exactly what the firmware's
 * persisted UCI says, nothing more.
 *
 * Reconcile model (idempotent):
 *   - Parse wireless config -> desired AP state {want_up, ssid, hidden,
 *     channel, encryption, key}.
 *   - If want_up and not running (or params changed): (re)start hostapd.
 *   - If !want_up and running: stop hostapd, return target iface to managed.
 *
 * The firmware exposes up to three radio0 AP VAPs. We mirror the FIRST enabled
 * one in this priority: wlan0wpa (Evil WPA) > wlan0open (Open AP) > wlan0mgmt
 * (Mgmt AP). The chip can only host one AP on one channel at a time on our free
 * radio, and the Pager UI only lets one of these be active at once in practice;
 * priority keeps behaviour deterministic and matches "most specific wins".
 */
#define _GNU_SOURCE
#include "openap.h"
#include "pager.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <errno.h>
#include <ctype.h>
#include <signal.h>
#include <pthread.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <net/if.h>    /* if_nametoindex: check the AP target iface exists */

/* ------------------------------------------------------------------------- */
#define OA_SSID_MAX   33     /* 32 + NUL */
#define OA_KEY_MAX    65

typedef struct {
    bool  present;           /* section exists in the config                 */
    bool  enabled;           /* !disabled                                    */
    char  ifname[32];        /* UCI ifname (wlan0open/...)                    */
    char  mode[16];          /* ap                                           */
    char  ssid[OA_SSID_MAX];
    char  key[OA_KEY_MAX];
    char  encryption[24];    /* none/psk2/sae/...                            */
    int   hidden;            /* 0/1                                          */
} oa_vap_t;

typedef struct {
    int   channel;           /* radio0 channel (1..13); 0 => "auto" => 1     */
    char  band[8];           /* 2g / 5g                                      */
    char  htmode[12];        /* HT20/...                                     */
} oa_radio_t;

/* desired/applied AP state */
typedef struct {
    bool  up;
    char  ifname[32];        /* firmware VAP name (for logging)              */
    char  ssid[OA_SSID_MAX];
    int   hidden;
    int   channel;
    char  encryption[24];
    char  key[OA_KEY_MAX];
} oa_state_t;

/* ------------------------------------------------------------------------- */
static pthread_t       g_th;
static volatile bool   g_run = false;
static bool            g_started = false;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static pid_t      g_hostapd_pid = -1;
static oa_state_t g_applied;          /* what we currently have running      */
static char       g_target[32];       /* resolved host radio (e.g. wlan1)    */

/* ------------------------------------------------------------------------- */
static const char *target_iface(void)
{
    const char *e = getenv("OPENAP_TARGET_IFACE");
    return (e && *e) ? e : OPENAP_TARGET_IFACE;
}
static const char *wireless_path(void)
{
    const char *e = getenv("OPENAP_WIRELESS_PATH");
    return (e && *e) ? e : OPENAP_WIRELESS_PATH;
}
static const char *hostapd_bin(void)
{
    const char *e = getenv("OPENAP_HOSTAPD_BIN");
    return (e && *e) ? e : OPENAP_HOSTAPD_BIN;
}

/* ------------------------------------------------------------------------- *
 *  Tiny UCI parser — only what we need: wifi-device 'radio0' and the three
 *  radio0 AP wifi-iface sections. Line oriented, robust to tabs/quotes.
 * ------------------------------------------------------------------------- */
static void strip_quotes(char *s)
{
    size_t n = strlen(s);
    if (n >= 2 && (s[0] == '\'' || s[0] == '"') && s[n-1] == s[0]) {
        memmove(s, s + 1, n - 2);
        s[n-2] = '\0';
    }
}

/* Read "option <key> '<val>'" / "list <key> '<val>'"; returns 1 if the line is
 * an option/list and fills key/val. */
static int parse_kv(const char *line, char *key, size_t kc, char *val, size_t vc)
{
    char buf[512];
    strncpy(buf, line, sizeof buf - 1); buf[sizeof buf - 1] = '\0';
    char *p = buf;
    while (*p == ' ' || *p == '\t') p++;
    const char *kw = NULL;
    if (!strncmp(p, "option ", 7)) { kw = p + 7; }
    else if (!strncmp(p, "list ", 5)) { kw = p + 5; }
    else return 0;
    /* kw -> "<key> <val...>" */
    char tmp[512];
    strncpy(tmp, kw, sizeof tmp - 1); tmp[sizeof tmp - 1] = '\0';
    char *sp = tmp;
    while (*sp == ' ' || *sp == '\t') sp++;
    char *k = sp;
    while (*sp && *sp != ' ' && *sp != '\t') sp++;
    if (*sp) { *sp++ = '\0'; }
    while (*sp == ' ' || *sp == '\t') sp++;
    char *v = sp;
    /* trim trailing ws/newline */
    size_t vn = strlen(v);
    while (vn && (v[vn-1] == '\n' || v[vn-1] == '\r' || v[vn-1] == ' ' || v[vn-1] == '\t'))
        v[--vn] = '\0';
    strip_quotes(v);
    strncpy(key, k, kc - 1); key[kc-1] = '\0';
    strncpy(val, v, vc - 1); val[vc-1] = '\0';
    return 1;
}

/* Is this a "config <type> '<name>'" header? Fill type/name. */
static int parse_section(const char *line, char *type, size_t tc, char *name, size_t nc)
{
    char buf[512];
    strncpy(buf, line, sizeof buf - 1); buf[sizeof buf - 1] = '\0';
    char *p = buf;
    while (*p == ' ' || *p == '\t') p++;
    if (strncmp(p, "config ", 7)) return 0;
    p += 7;
    while (*p == ' ' || *p == '\t') p++;
    char *t = p;
    while (*p && *p != ' ' && *p != '\t' && *p != '\n' && *p != '\r') p++;
    if (*p) *p++ = '\0';
    while (*p == ' ' || *p == '\t') p++;
    char *nm = p;
    size_t nn = strlen(nm);
    while (nn && (nm[nn-1]=='\n'||nm[nn-1]=='\r'||nm[nn-1]==' '||nm[nn-1]=='\t'))
        nm[--nn] = '\0';
    strip_quotes(nm);
    strncpy(type, t, tc - 1); type[tc-1] = '\0';
    strncpy(name, nm, nc - 1); name[nc-1] = '\0';
    return 1;
}

/* Parse the whole wireless config, extracting radio0 + the three AP VAPs. */
static int parse_wireless(const char *path, oa_radio_t *radio,
                          oa_vap_t *open_ap, oa_vap_t *wpa_ap, oa_vap_t *mgmt_ap)
{
    FILE *f = fopen(path, "r");
    if (!f) return -1;

    memset(radio, 0, sizeof *radio);
    memset(open_ap, 0, sizeof *open_ap);
    memset(wpa_ap, 0, sizeof *wpa_ap);
    memset(mgmt_ap, 0, sizeof *mgmt_ap);
    radio->channel = 0;

    enum { S_NONE, S_RADIO0, S_VAP } st = S_NONE;
    oa_vap_t *cur = NULL;

    char line[512];
    while (fgets(line, sizeof line, f)) {
        char type[32], name[64];
        if (parse_section(line, type, sizeof type, name, sizeof name)) {
            cur = NULL; st = S_NONE;
            if (!strcmp(type, "wifi-device") && !strcmp(name, "radio0")) {
                st = S_RADIO0;
            } else if (!strcmp(type, "wifi-iface")) {
                if (!strcmp(name, "wlan0open"))      { cur = open_ap; st = S_VAP; }
                else if (!strcmp(name, "wlan0wpa"))  { cur = wpa_ap;  st = S_VAP; }
                else if (!strcmp(name, "wlan0mgmt")) { cur = mgmt_ap; st = S_VAP; }
                if (cur) { cur->present = true; cur->enabled = true; /* default enabled unless disabled '1' */ }
            }
            continue;
        }
        char k[64], v[256];
        if (!parse_kv(line, k, sizeof k, v, sizeof v)) continue;

        if (st == S_RADIO0) {
            if (!strcmp(k, "channel")) {
                if (!strcmp(v, "auto")) radio->channel = 0;
                else radio->channel = atoi(v);
            } else if (!strcmp(k, "band")) {
                strncpy(radio->band, v, sizeof radio->band - 1);
            } else if (!strcmp(k, "htmode")) {
                strncpy(radio->htmode, v, sizeof radio->htmode - 1);
            }
        } else if (st == S_VAP && cur) {
            if (!strcmp(k, "ifname"))         strncpy(cur->ifname, v, sizeof cur->ifname - 1);
            else if (!strcmp(k, "mode"))      strncpy(cur->mode, v, sizeof cur->mode - 1);
            else if (!strcmp(k, "ssid"))      strncpy(cur->ssid, v, sizeof cur->ssid - 1);
            else if (!strcmp(k, "key"))       strncpy(cur->key, v, sizeof cur->key - 1);
            else if (!strcmp(k, "encryption"))strncpy(cur->encryption, v, sizeof cur->encryption - 1);
            else if (!strcmp(k, "hidden"))    cur->hidden = atoi(v);
            else if (!strcmp(k, "disabled"))  cur->enabled = (atoi(v) == 0);
        }
    }
    fclose(f);
    return 0;
}

/* ------------------------------------------------------------------------- *
 *  Desired-state resolution: pick the first ENABLED radio0 AP VAP and build
 *  the state the firmware is asking for.
 * ------------------------------------------------------------------------- */
/* If the target radio (wlan0) is associated to an uplink, return its current
 * channel; else 0. The ath10k does STA+AP only on ONE channel (#channels<=1),
 * so when there is an uplink the AP MUST ride its channel. `iw dev X link`
 * prints "freq: NNNN" when associated. */
static int uplink_channel(const char *iface)
{
    char cmd[128];
    snprintf(cmd, sizeof cmd, "iw dev %s link 2>/dev/null", iface);
    FILE *p = popen(cmd, "r");
    if (!p) return 0;
    char line[256];
    int freq = 0;
    while (fgets(line, sizeof line, p)) {
        char *f = strstr(line, "freq:");
        if (f) { freq = atoi(f + 5); break; }
    }
    pclose(p);
    if (freq <= 0) return 0;                       /* not associated */
    if (freq >= 2412 && freq <= 2484)              /* 2.4GHz */
        return (freq == 2484) ? 14 : (freq - 2407) / 5;
    if (freq >= 5000 && freq <= 5900)              /* 5GHz */
        return (freq - 5000) / 5;
    return 0;
}

static void resolve_desired(oa_state_t *out)
{
    memset(out, 0, sizeof *out);
    oa_radio_t radio;
    oa_vap_t open_ap, wpa_ap, mgmt_ap;
    if (parse_wireless(wireless_path(), &radio, &open_ap, &wpa_ap, &mgmt_ap) != 0) {
        /* no config readable -> leave AP down (teardown) */
        return;
    }

    /* firmware's radio0 channel; "auto" (0) maps to ch1 (the UCI default). */
    int channel = radio.channel > 0 ? radio.channel : 1;

    /* wlan0 (ath10k) does STA+AP on ONE channel only. If wlan0 is associated to
     * an uplink, FORCE the AP onto the uplink's channel (otherwise hostapd and
     * the STA fight over the radio and neither works). If wlan0 has no uplink
     * (the usual field case), honour the firmware's requested channel as-is
     * (2.4 or 5GHz, whatever it asked). */
    int up_ch = uplink_channel(target_iface());
    if (up_ch > 0) {
        if (up_ch != channel)
            pineap_log("OPENAP",
                "wlan0 uplink on ch %d; AP forced to ch %d (ath10k #channels<=1)",
                up_ch, up_ch);
        channel = up_ch;
    }
    /* clamp only impossible values (0/negative); 2.4 and 5GHz are both allowed. */
    if (channel <= 0) channel = 1;

    /* priority: Evil WPA > Open AP > Mgmt AP (most-specific/secured wins). */
    oa_vap_t *pick = NULL;
    if (wpa_ap.present && wpa_ap.enabled && wpa_ap.ssid[0])      pick = &wpa_ap;
    else if (open_ap.present && open_ap.enabled && open_ap.ssid[0]) pick = &open_ap;
    else if (mgmt_ap.present && mgmt_ap.enabled && mgmt_ap.ssid[0]) pick = &mgmt_ap;

    if (!pick) return;   /* nothing enabled -> AP down */

    out->up = true;
    out->channel = channel;
    out->hidden = pick->hidden;
    strncpy(out->ifname, pick->ifname, sizeof out->ifname - 1);
    strncpy(out->ssid, pick->ssid, sizeof out->ssid - 1);
    strncpy(out->encryption, pick->encryption, sizeof out->encryption - 1);
    strncpy(out->key, pick->key, sizeof out->key - 1);
}

/* ------------------------------------------------------------------------- *
 *  AP network (IP + DHCP) — read the firmware's own `lan`/`dhcp lan` intent so
 *  clients get an address instead of hanging on "obtaining IP". Pure bridge:
 *  we apply exactly the ipaddr/netmask/start/limit the firmware persists.
 * ------------------------------------------------------------------------- */
typedef struct {
    char ipaddr[32];
    char netmask[32];
    int  dhcp_start;
    int  dhcp_limit;
} oa_net_t;

/* netmask dotted -> prefix length (e.g. 255.255.255.0 -> 24). */
static int netmask_to_prefix(const char *nm)
{
    unsigned a = 0, b = 0, c = 0, d = 0;
    if (sscanf(nm, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return 24;
    unsigned long m = (a << 24) | (b << 16) | (c << 8) | d;
    int p = 0;
    for (int i = 31; i >= 0; i--) { if (m & (1UL << i)) p++; else break; }
    return p ? p : 24;
}

/* ip + offset in the last octet -> dotted string (host part only, /24 assumed
 * like the firmware's 172.16.52.0/24). */
static void ip_with_host(const char *base, int host, char *out, size_t outn)
{
    unsigned a = 0, b = 0, c = 0, d = 0;
    sscanf(base, "%u.%u.%u.%u", &a, &b, &c, &d);
    snprintf(out, outn, "%u.%u.%u.%d", a, b, c, host);
}

/* Read the firmware's `config interface 'lan'` ipaddr/netmask. */
static void read_ap_ip(oa_net_t *net)
{
    strncpy(net->ipaddr,  OPENAP_AP_IPADDR,  sizeof net->ipaddr - 1);
    strncpy(net->netmask, OPENAP_AP_NETMASK, sizeof net->netmask - 1);

    FILE *f = fopen(OPENAP_NET_PATH, "r");
    if (!f) return;
    char line[512];
    int in_lan = 0;
    while (fgets(line, sizeof line, f)) {
        char type[32], name[64];
        if (parse_section(line, type, sizeof type, name, sizeof name)) {
            in_lan = (!strcmp(type, "interface") && !strcmp(name, "lan"));
            continue;
        }
        if (!in_lan) continue;
        char k[64], v[256];
        if (!parse_kv(line, k, sizeof k, v, sizeof v)) continue;
        if (!strcmp(k, "ipaddr"))  strncpy(net->ipaddr,  v, sizeof net->ipaddr - 1);
        else if (!strcmp(k, "netmask")) strncpy(net->netmask, v, sizeof net->netmask - 1);
    }
    fclose(f);
}

/* Read the firmware's `config dhcp 'lan'` start/limit. */
static void read_ap_dhcp(oa_net_t *net)
{
    net->dhcp_start = OPENAP_DHCP_START;
    net->dhcp_limit = OPENAP_DHCP_LIMIT;

    FILE *f = fopen(OPENAP_DHCP_PATH, "r");
    if (!f) return;
    char line[512];
    int in_lan = 0;
    while (fgets(line, sizeof line, f)) {
        char type[32], name[64];
        if (parse_section(line, type, sizeof type, name, sizeof name)) {
            in_lan = (!strcmp(type, "dhcp") && !strcmp(name, "lan"));
            continue;
        }
        if (!in_lan) continue;
        char k[64], v[256];
        if (!parse_kv(line, k, sizeof k, v, sizeof v)) continue;
        if (!strcmp(k, "start")) net->dhcp_start = atoi(v);
        else if (!strcmp(k, "limit")) net->dhcp_limit = atoi(v);
    }
    fclose(f);
}

static void read_ap_net(oa_net_t *net)
{
    memset(net, 0, sizeof *net);
    read_ap_ip(net);
    read_ap_dhcp(net);
}

static pid_t g_dnsmasq_pid = -1;

/* Assign the firmware's lan IP to the AP iface. */
static void iface_set_ip(const char *iface, const oa_net_t *net)
{
    int prefix = netmask_to_prefix(net->netmask);
    char cmd[320];
    snprintf(cmd, sizeof cmd,
             "ip addr flush dev %s >/dev/null 2>&1; "
             "ip addr add %s/%d dev %s >/dev/null 2>&1; "
             "ip link set %s up >/dev/null 2>&1",
             iface, net->ipaddr, prefix, iface, iface);
    (void)!system(cmd);
}

static void iface_flush_ip(const char *iface)
{
    char cmd[160];
    snprintf(cmd, sizeof cmd, "ip addr flush dev %s >/dev/null 2>&1", iface);
    (void)!system(cmd);
}

static void stop_dnsmasq(void)
{
    if (g_dnsmasq_pid > 0) {
        kill(g_dnsmasq_pid, SIGTERM);
        for (int i = 0; i < 20; i++) {
            if (waitpid(g_dnsmasq_pid, NULL, WNOHANG) == g_dnsmasq_pid) {
                g_dnsmasq_pid = -1; break;
            }
            usleep(100000);
        }
        if (g_dnsmasq_pid > 0) {
            kill(g_dnsmasq_pid, SIGKILL);
            waitpid(g_dnsmasq_pid, NULL, 0);
            g_dnsmasq_pid = -1;
        }
    }
    char cmd[256];
    snprintf(cmd, sizeof cmd,
             "pkill -f 'dnsmasq .*%s/dnsmasq-openap' >/dev/null 2>&1",
             OPENAP_RUN_DIR);
    (void)!system(cmd);
}

/* Start a dedicated dnsmasq serving DHCP on the AP iface, with the firmware's
 * range. Isolated (--conf-file=/dev/null) so it never touches the host DNS. */
static int start_dnsmasq(const char *iface, const oa_net_t *net)
{
    stop_dnsmasq();

    char range_lo[32], range_hi[32];
    ip_with_host(net->ipaddr, net->dhcp_start, range_lo, sizeof range_lo);
    ip_with_host(net->ipaddr, net->dhcp_start + net->dhcp_limit, range_hi, sizeof range_hi);

    char pidfile[256], logpath[256];
    snprintf(pidfile, sizeof pidfile, "%s/dnsmasq-openap.pid", OPENAP_RUN_DIR);
    snprintf(logpath, sizeof logpath, "%s/dnsmasq-openap.log", OPENAP_RUN_DIR);

    /* Build argv for a self-contained dnsmasq bound to our iface only. */
    char a_iface[64], a_listen[64], a_range[128], a_router[96], a_dns[96], a_pid[300];
    snprintf(a_iface,  sizeof a_iface,  "--interface=%s", iface);
    snprintf(a_listen, sizeof a_listen, "--listen-address=%s", net->ipaddr);
    snprintf(a_range,  sizeof a_range,  "--dhcp-range=%s,%s,%s,12h",
             range_lo, range_hi, net->netmask);
    snprintf(a_router, sizeof a_router, "--dhcp-option=3,%s", net->ipaddr); /* gateway */
    snprintf(a_dns,    sizeof a_dns,    "--dhcp-option=6,%s", net->ipaddr); /* dns = us */
    snprintf(a_pid,    sizeof a_pid,    "--pid-file=%s", pidfile);

    pid_t pid = fork();
    if (pid < 0) {
        pineap_log("OPENAP", "dnsmasq fork failed: %s", strerror(errno));
        return -1;
    }
    if (pid == 0) {
        int fd = open(logpath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
        setsid();
        execl(OPENAP_DNSMASQ_BIN, "dnsmasq",
              "--keep-in-foreground",
              "--conf-file=/dev/null",
              "--no-resolv",
              "--no-hosts",
              "--bind-interfaces",
              "--except-interface=lo",
              a_iface, a_listen, a_range, a_router, a_dns, a_pid,
              "--dhcp-authoritative",
              "--dhcp-leasefile=/tmp/dhcp.leases",
              (char *)NULL);
        _exit(127);
    }

    g_dnsmasq_pid = pid;
    usleep(300000);
    if (waitpid(pid, NULL, WNOHANG) == pid) {
        g_dnsmasq_pid = -1;
        pineap_log("OPENAP", "dnsmasq exited immediately (see %s)", logpath);
        return -1;
    }
    pineap_log("OPENAP", "DHCP up on %s: %s range %s-%s pid=%d",
               iface, net->ipaddr, range_lo, range_hi, pid);
    return 0;
}

/* ------------------------------------------------------------------------- *
 *  hostapd config generation + lifecycle
 * ------------------------------------------------------------------------- */
static int enc_is_secured(const char *enc)
{
    if (!enc || !enc[0]) return 0;
    if (!strcasecmp(enc, "none") || !strcasecmp(enc, "open")) return 0;
    return 1;    /* psk/psk2/sae/wpa... */
}

static int write_hostapd_conf(const char *path, const oa_state_t *st, const char *iface)
{
    FILE *f = fopen(path, "w");
    if (!f) return -1;

    fprintf(f, "# generated by pineapd-pager openap bridge (mirrors firmware intent)\n");
    fprintf(f, "interface=%s\n", iface);
    fprintf(f, "driver=nl80211\n");
    fprintf(f, "ctrl_interface=%s/hostapd\n", OPENAP_RUN_DIR);
    fprintf(f, "ssid=%s\n", st->ssid);
    /* hw_mode depends on the band: ch 1-14 => 2.4GHz (g), ch >=36 => 5GHz (a).
     * (Needed now that the AP can land on the uplink's 5GHz channel.) */
    int ch = st->channel > 0 ? st->channel : 1;
    int is5g = (ch >= 36);
    fprintf(f, "hw_mode=%s\n", is5g ? "a" : "g");
    fprintf(f, "channel=%d\n", ch);
    if (is5g) {
        /* 5GHz needs the country + 11n to be accepted by the regdomain. */
        fprintf(f, "ieee80211d=1\n");
        fprintf(f, "ieee80211n=1\n");
    }
    fprintf(f, "ignore_broadcast_ssid=%d\n", st->hidden ? 1 : 0);
    fprintf(f, "wmm_enabled=1\n");

    if (enc_is_secured(st->encryption) && st->key[0]) {
        /* WPA2-PSK (and SAE if requested) — mirror the firmware's Evil WPA. */
        int sae = (strcasestr(st->encryption, "sae") != NULL);
        fprintf(f, "wpa=2\n");
        fprintf(f, "wpa_key_mgmt=%s\n", sae ? "SAE WPA-PSK" : "WPA-PSK");
        fprintf(f, "rsn_pairwise=CCMP\n");
        fprintf(f, "wpa_passphrase=%s\n", st->key);
        if (sae) fprintf(f, "ieee80211w=1\n");
    } else {
        /* Open AP — no encryption (auth_algs=1 = open system). */
        fprintf(f, "auth_algs=1\n");
    }
    fclose(f);
    return 0;
}

/* Put the target iface into a clean managed/down state (teardown). */
static void iface_to_managed(const char *iface)
{
    char cmd[256];
    snprintf(cmd, sizeof cmd,
             "ip link set %s down >/dev/null 2>&1; "
             "iw dev %s set type managed >/dev/null 2>&1; "
             "ip link set %s down >/dev/null 2>&1",
             iface, iface, iface);
    (void)!system(cmd);
}

static void stop_hostapd(void)
{
    if (g_hostapd_pid > 0) {
        kill(g_hostapd_pid, SIGTERM);
        /* reap with a short grace, then SIGKILL */
        for (int i = 0; i < 20; i++) {
            int status;
            pid_t r = waitpid(g_hostapd_pid, &status, WNOHANG);
            if (r == g_hostapd_pid) { g_hostapd_pid = -1; break; }
            usleep(100000);
        }
        if (g_hostapd_pid > 0) {
            kill(g_hostapd_pid, SIGKILL);
            waitpid(g_hostapd_pid, NULL, 0);
            g_hostapd_pid = -1;
        }
    }
    /* belt and suspenders: clear any stray hostapd bound to our iface */
    char cmd[256];
    snprintf(cmd, sizeof cmd,
             "pkill -f 'hostapd .*%s/hostapd-openap.conf' >/dev/null 2>&1",
             OPENAP_RUN_DIR);
    (void)!system(cmd);
}

/* AP VAP helpers (defined below bring_up); forward-declared for bring_down. */
#define OPENAP_VAP "pager-ap0"
static bool g_vap_created;
static void vap_destroy(void);

/* Bring AP down fully (DHCP + hostapd + iface IP/mode). */
static void bring_down(const char *iface)
{
    stop_dnsmasq();
    stop_hostapd();
    if (g_vap_created) {
        /* AP ran on the VAP: flush its IP and destroy it. wlan0 (STA/uplink)
         * was never touched, so the SSH link stays up. */
        iface_flush_ip(OPENAP_VAP);
        vap_destroy();
        pineap_log("OPENAP", "AP torn down; VAP removed, %s uplink preserved", iface);
    } else {
        /* AP ran directly on the base iface (no uplink case): return to managed. */
        iface_flush_ip(iface);
        iface_to_managed(iface);
        pineap_log("OPENAP", "AP torn down; %s returned to managed (DHCP+IP cleared)", iface);
    }
    memset(&g_applied, 0, sizeof g_applied);
}

/* ------------------------------------------------------------------------- *
 *  AP VAP (virtual interface) over the internal radio's phy, so the AP can run
 *  CONCURRENTLY with the wlan0 STA uplink WITHOUT bringing wlan0 down (which
 *  would drop the SSH-over-wifi). hostapd runs on this VAP; wlan0 stays STA.
 * ------------------------------------------------------------------------- */
/* g_vap_created + OPENAP_VAP forward-declared above bring_down. */

/* phy name backing an iface (e.g. wlan0 -> phy0). Returns 0 on success. */
static int iface_phy(const char *iface, char *out, size_t outn)
{
    char path[128];
    snprintf(path, sizeof path, "/sys/class/net/%s/phy80211/name", iface);
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    if (!fgets(out, (int)outn, f)) { fclose(f); return -1; }
    fclose(f);
    size_t n = strlen(out);
    while (n && (out[n-1] == '\n' || out[n-1] == '\r')) out[--n] = '\0';
    return out[0] ? 0 : -1;
}

/* Is iface currently associated to an uplink (has a managed STA link up)? */
static bool iface_has_uplink(const char *iface)
{
    return uplink_channel(iface) > 0;
}

/* Create the AP VAP on the same phy as `base` (wlan0). Leaves base untouched. */
static int vap_create(const char *base)
{
    char phy[64];
    if (iface_phy(base, phy, sizeof phy) != 0) return -1;
    char cmd[256];
    /* delete any stale VAP first, then add a fresh AP-type vif on the phy */
    snprintf(cmd, sizeof cmd, "iw dev %s del >/dev/null 2>&1", OPENAP_VAP);
    (void)!system(cmd);
    snprintf(cmd, sizeof cmd,
             "iw phy %s interface add %s type __ap >/dev/null 2>&1", phy, OPENAP_VAP);
    if (system(cmd) != 0) {
        /* some drivers want "type ap" */
        snprintf(cmd, sizeof cmd,
                 "iw phy %s interface add %s type ap >/dev/null 2>&1", phy, OPENAP_VAP);
        if (system(cmd) != 0) return -1;
    }
    g_vap_created = true;
    return 0;
}

static void vap_destroy(void)
{
    if (!g_vap_created) return;
    char cmd[128];
    snprintf(cmd, sizeof cmd, "ip link set %s down >/dev/null 2>&1", OPENAP_VAP);
    (void)!system(cmd);
    snprintf(cmd, sizeof cmd, "iw dev %s del >/dev/null 2>&1", OPENAP_VAP);
    (void)!system(cmd);
    g_vap_created = false;
}

/* Start hostapd for the desired state on iface. Returns 0 on success. */
static int bring_up(const oa_state_t *st, const char *iface)
{
    /* make sure run dir + ctrl dir exist */
    mkdir(OPENAP_RUN_DIR, 0755);
    char ctrldir[256];
    snprintf(ctrldir, sizeof ctrldir, "%s/hostapd", OPENAP_RUN_DIR);
    mkdir(ctrldir, 0755);

    /* Decide the ACTUAL interface hostapd will bind:
     *  - wlan0 has an uplink STA -> use a separate AP VAP (pager-ap0) on the same
     *    phy so the STA/SSH is NOT disturbed (STA+AP concurrent, same channel).
     *  - no uplink -> bind the base iface directly (down+managed for clean slate). */
    char ap_if[32];
    if (iface_has_uplink(iface)) {
        if (vap_create(iface) != 0) {
            pineap_log("OPENAP",
                "could not create AP VAP on %s's phy; AP not started (uplink preserved)",
                iface);
            return -1;
        }
        strncpy(ap_if, OPENAP_VAP, sizeof ap_if - 1); ap_if[sizeof ap_if-1] = '\0';
        pineap_log("OPENAP", "using AP VAP %s on %s's phy (STA/uplink preserved)",
                   ap_if, iface);
    } else {
        strncpy(ap_if, iface, sizeof ap_if - 1); ap_if[sizeof ap_if-1] = '\0';
        iface_to_managed(ap_if);   /* clean slate; safe (no uplink to lose) */
    }

    char conf[256];
    snprintf(conf, sizeof conf, "%s/hostapd-openap.conf", OPENAP_RUN_DIR);
    if (write_hostapd_conf(conf, st, ap_if) != 0) {
        pineap_log("OPENAP", "failed to write %s: %s", conf, strerror(errno));
        vap_destroy();
        return -1;
    }

    char logpath[256];
    snprintf(logpath, sizeof logpath, "%s/hostapd-openap.log", OPENAP_RUN_DIR);

    pid_t pid = fork();
    if (pid < 0) {
        pineap_log("OPENAP", "fork failed: %s", strerror(errno));
        return -1;
    }
    if (pid == 0) {
        /* child: redirect stdout/stderr to a log, exec hostapd */
        int fd = open(logpath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
        setsid();
        execl(hostapd_bin(), "hostapd", conf, (char *)NULL);
        _exit(127);
    }

    /* parent: wait briefly and confirm hostapd stayed up (didn't exit 127). */
    g_hostapd_pid = pid;
    usleep(400000);
    int status;
    pid_t r = waitpid(pid, &status, WNOHANG);
    if (r == pid) {
        /* hostapd exited already => failure */
        g_hostapd_pid = -1;
        pineap_log("OPENAP", "hostapd exited immediately (see %s)", logpath);
        vap_destroy();
        return -1;
    }

    g_applied = *st;
    pineap_log("OPENAP",
               "AP UP: iface=%s ssid=\"%s\" ch=%d hidden=%d enc=%s (firmware VAP %s) pid=%d",
               ap_if, st->ssid, st->channel, st->hidden,
               st->encryption[0] ? st->encryption : "open",
               st->ifname[0] ? st->ifname : "?", pid);

    /* Give clients an IP: assign the firmware's lan IP to the AP iface and run
     * a dedicated dnsmasq DHCP server with the firmware's range. Without this
     * the client associates (hostapd OK) but hangs on "obtaining IP address".
     * (On the AP VAP, not on wlan0, so the uplink keeps its own IP.) */
    oa_net_t net;
    read_ap_net(&net);
    iface_set_ip(ap_if, &net);
    if (start_dnsmasq(ap_if, &net) != 0)
        pineap_log("OPENAP", "WARNING: DHCP server failed; clients won't get an IP");

    return 0;
}

/* Does the applied state already satisfy the desired one? */
static bool state_equal(const oa_state_t *a, const oa_state_t *b)
{
    if (a->up != b->up) return false;
    if (!a->up) return true;
    return a->channel == b->channel &&
           a->hidden == b->hidden &&
           !strcmp(a->ssid, b->ssid) &&
           !strcasecmp(a->encryption, b->encryption) &&
           !strcmp(a->key, b->key);
}

/* ------------------------------------------------------------------------- *
 *  Reconcile — the only place that mutates radio state.
 * ------------------------------------------------------------------------- */
static void reconcile_locked(void)
{
    oa_state_t want;
    resolve_desired(&want);

    const char *iface = g_target;   /* wlan0 (internal ath10k) */

    /* POLICY GATE: the AP runs on wlan0 ONLY when wlan0 is FREE (no uplink).
     *  - wlan0 present AND not associated -> OK, bring the AP up on wlan0.
     *  - wlan0 associated to a WiFi (uplink/SSH) -> AP stays DOWN. We must NOT
     *    touch wlan0 (resets the ath10k, drops the uplink/SSH) and must NOT fall
     *    back to the Archer (that would break recon). Just wait until wlan0 is
     *    free (user disconnects WiFi / goes mobile). */
    if (want.up) {
        if (if_nametoindex(iface) == 0) {
            static bool warned_missing = false;
            if (!warned_missing) {
                pineap_log("OPENAP", "%s not present; AP stays down", iface);
                warned_missing = true;
            }
            if (g_applied.up) bring_down(iface);
            return;
        }
        if (uplink_channel(iface) > 0) {
            static bool warned_uplink = false;
            if (!warned_uplink) {
                pineap_log("OPENAP",
                    "%s is connected to WiFi (uplink/SSH); AP stays down until "
                    "wlan0 is free (never touches the uplink, never the Archer)",
                    iface);
                warned_uplink = true;
            }
            if (g_applied.up) bring_down(iface);
            return;
        }
    }

    if (!want.up) {
        if (g_applied.up) bring_down(iface);
        return;
    }

    if (g_applied.up && state_equal(&g_applied, &want)) {
        /* already running with the right params; also verify hostapd alive */
        if (g_hostapd_pid > 0) {
            int status;
            pid_t r = waitpid(g_hostapd_pid, &status, WNOHANG);
            if (r == g_hostapd_pid) {
                /* hostapd died unexpectedly -> restart */
                g_hostapd_pid = -1;
                memset(&g_applied, 0, sizeof g_applied);
                pineap_log("OPENAP", "hostapd died; restarting");
                bring_up(&want, iface);
            }
        }
        return;
    }

    /* params changed or not yet up -> (re)start cleanly */
    if (g_applied.up) stop_hostapd();
    if (bring_up(&want, iface) != 0) {
        /* leave iface clean on failure */
        iface_to_managed(iface);
        memset(&g_applied, 0, sizeof g_applied);
    }
}

void openap_reconcile_now(void)
{
    pthread_mutex_lock(&g_lock);
    reconcile_locked();
    pthread_mutex_unlock(&g_lock);
}

/* ------------------------------------------------------------------------- *
 *  Watcher thread — poll the config file mtime; reconcile on change.
 * ------------------------------------------------------------------------- */
static void *watch_thread(void *arg)
{
    (void)arg;
    const char *path = wireless_path();
    struct stat prev;
    memset(&prev, 0, sizeof prev);
    bool have_prev = (stat(path, &prev) == 0);

    /* initial reconcile so we match persisted intent at startup */
    openap_reconcile_now();

    while (g_run) {
        struct stat cur;
        if (stat(path, &cur) == 0) {
            if (!have_prev ||
                cur.st_mtime != prev.st_mtime ||
                cur.st_size  != prev.st_size) {
                /* debounce: wait for the writer (UCI atomic rename) to settle */
                usleep(150000);
                prev = cur; have_prev = true;
                openap_reconcile_now();
            }
        }
        /* poll interval; also re-checks hostapd liveness periodically */
        for (int i = 0; i < 10 && g_run; i++) usleep(100000); /* ~1s */
        if (g_run) openap_reconcile_now();  /* liveness re-check */
    }
    return NULL;
}

/* ------------------------------------------------------------------------- */
int openap_start_watcher(void)
{
    pthread_mutex_lock(&g_lock);
    if (g_started) { pthread_mutex_unlock(&g_lock); return 0; }
    strncpy(g_target, target_iface(), sizeof g_target - 1);
    g_target[sizeof g_target - 1] = '\0';
    memset(&g_applied, 0, sizeof g_applied);
    g_run = true;
    if (pthread_create(&g_th, NULL, watch_thread, NULL) != 0) {
        g_run = false;
        pthread_mutex_unlock(&g_lock);
        pineap_log("OPENAP", "failed to start watcher thread");
        return -1;
    }
    g_started = true;
    pthread_mutex_unlock(&g_lock);
    pineap_log("OPENAP",
               "watcher started: watching %s -> target radio %s (hostapd=%s)",
               wireless_path(), g_target, hostapd_bin());
    return 0;
}

void openap_shutdown(void)
{
    pthread_mutex_lock(&g_lock);
    bool was = g_started;
    g_run = false;
    pthread_mutex_unlock(&g_lock);

    if (was) {
        pthread_join(g_th, NULL);
    }

    pthread_mutex_lock(&g_lock);
    /* unconditional teardown so "powering off" the Pager frees the radio */
    if (g_applied.up || g_hostapd_pid > 0 || g_dnsmasq_pid > 0) {
        bring_down(g_target[0] ? g_target : target_iface());
    } else {
        /* still kill any stray helpers we might own */
        stop_dnsmasq();
        stop_hostapd();
    }
    g_started = false;
    pthread_mutex_unlock(&g_lock);
    pineap_log("OPENAP", "shutdown complete (AP down, radio freed)");
}
