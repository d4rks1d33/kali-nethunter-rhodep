/*
 * scan_sta.c — native STA (station) scan for AP recon.
 *
 * WHY THIS EXISTS (hardware constraint, confirmed on a Moto G82 / WCN3990):
 *   The WCN3990 has a SINGLE radio. When wlan0 (managed/STA) is associated to
 *   an AP (e.g. channel 60), the monitor VIF (wlan0mon) created alongside it is
 *   pinned to the STA's operating channel and CANNOT channel-hop. The recon
 *   monitor therefore only ever sees APs on the connection's channel, so the
 *   Dashboard shows (almost) no nearby APs.
 *
 *   An associated STA, however, can still perform an OFF-CHANNEL scan on its own
 *   (the firmware briefly leaves the home channel and returns) WITHOUT dropping
 *   the connection. `iw dev wlan0 scan` run against the connected managed iface
 *   returns APs from ALL channels while internet stays up. Measured on the real
 *   G82: 15-25 APs across 2.4GHz (1-11) + 5GHz (56,60) with the link intact.
 *
 * WHAT THIS DOES:
 *   A background thread runs `iw dev <sta_iface> scan` every ~5s while a recon
 *   scan is active, parses each BSS block, and writes rows into the SAME `aps`
 *   table (via recon_write_ap) that the monitor path uses — so the PHP panel /
 *   Dashboard reads them identically. The monitor (wlan0mon) is left untouched
 *   for handshake capture / deauth / injection.
 *
 * It never touches the driver, firmware, or the STA's association state.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <ctype.h>
#include <time.h>
#include <pthread.h>

/* encryption INT values. The monitor path writes 0 ("open/unknown") always; we
 * fill the real value. These map to the panel's expectation: 0=open, 1=WEP,
 * 2=WPA, 3=WPA2, 4=WPA3. The panel treats any non-zero as "secured" and shows
 * the label; we provide the finer-grained value where iw gives us the evidence. */
enum { ENC_OPEN = 0, ENC_WEP = 1, ENC_WPA = 2, ENC_WPA2 = 3, ENC_WPA3 = 4 };

/* ------------------------------------------------------------------------- *
 *  Per-BSS accumulator — reset on each "BSS xx:.." line, flushed on the next
 *  BSS line (or EOF).
 * ------------------------------------------------------------------------- */
typedef struct {
    bool  valid;          /* a BSS line has been seen            */
    char  bssid[18];
    char  ssid[64];
    bool  have_ssid;      /* "SSID:" line present                */
    bool  ssid_hidden;    /* SSID empty or all-NUL               */
    int   channel;        /* from DS Parameter set (authoritative) */
    int   freq_channel;   /* derived from freq: (fallback)       */
    int   signal;         /* dBm, rounded from "signal: -NN.NN"  */
    bool  have_signal;
    bool  privacy;        /* "Privacy" in capability             */
    bool  rsn;            /* RSN IE present                      */
    bool  wpa;            /* WPA (vendor) IE present             */
    bool  rsn_sae;        /* RSN with SAE auth => WPA3           */
    bool  wps;            /* WPS IE present                      */
} bss_acc_t;

/* freq(MHz) -> channel. Covers 2.4GHz and 5GHz; good enough as a fallback when
 * a BSS omits the DS Parameter set element (common on some 5GHz APs). */
static int freq_to_channel(int mhz)
{
    if (mhz == 2484) return 14;
    if (mhz >= 2412 && mhz <= 2472) return (mhz - 2407) / 5;   /* ch 1..13 */
    if (mhz >= 5160 && mhz <= 5885) return (mhz - 5000) / 5;   /* ch 32..177 */
    if (mhz >= 5955 && mhz <= 7115) return (mhz - 5950) / 5;   /* 6GHz */
    return 0;
}

/* Decide the encryption INT from the IEs/capability seen for this BSS. */
static int bss_encryption(const bss_acc_t *a)
{
    if (a->rsn)      return a->rsn_sae ? ENC_WPA3 : ENC_WPA2;
    if (a->wpa)      return ENC_WPA;
    if (a->privacy)  return ENC_WEP;   /* Privacy bit but no RSN/WPA => WEP */
    return ENC_OPEN;
}

/* Flush the accumulated BSS into the aps table (if it holds a valid BSSID). */
static int bss_flush(const bss_acc_t *a, int scan_id, long now)
{
    if (!a->valid || !a->bssid[0]) return 0;

    int channel = a->channel > 0 ? a->channel : a->freq_channel;
    int signal  = a->have_signal ? a->signal : 0;
    int enc     = bss_encryption(a);

    /* hidden: no SSID element, or SSID was empty / all-NUL */
    int hidden  = (!a->have_ssid || a->ssid_hidden) ? 1 : 0;
    const char *ssid = hidden ? "" : a->ssid;

    recon_write_ap(scan_id, ssid, a->bssid, enc, hidden, channel,
                   signal, a->wps ? 1 : 0, now);
    return 1;
}

/* Is an SSID string "hidden"? iw prints hidden SSIDs either as an empty value or
 * as a run of "\x00" escape sequences. */
static bool ssid_is_hidden(const char *s)
{
    if (!*s) return true;
    /* all "\x00" groups => hidden */
    const char *p = s;
    bool only_nul = true;
    while (*p) {
        if (p[0] == '\\' && p[1] == 'x' && p[2] == '0' && p[3] == '0') {
            p += 4; continue;
        }
        only_nul = false;
        break;
    }
    return only_nul;
}

/* Parse one full `iw scan` text buffer and write each BSS to the aps table.
 * Returns the number of APs written. Exposed (non-static) so the unit test can
 * feed it a captured scan and assert the resulting rows. */
int scan_sta_parse_buffer(const char *text, int scan_id, long now)
{
    bss_acc_t a;
    memset(&a, 0, sizeof a);
    int written = 0;

    const char *line = text;
    while (line && *line) {
        const char *nl = strchr(line, '\n');
        size_t linelen = nl ? (size_t)(nl - line) : strlen(line);
        char buf[1024];
        if (linelen >= sizeof buf) linelen = sizeof buf - 1;
        memcpy(buf, line, linelen);
        buf[linelen] = '\0';

        /* advance for next iteration */
        line = nl ? nl + 1 : NULL;

        /* "BSS aa:bb:cc:dd:ee:ff(on wlan0)" — new record */
        if (!strncmp(buf, "BSS ", 4)) {
            written += bss_flush(&a, scan_id, now);   /* flush previous */
            memset(&a, 0, sizeof a);
            /* copy 17 chars of MAC right after "BSS " */
            const char *m = buf + 4;
            int i = 0;
            while (i < 17 && m[i] &&
                   (isxdigit((unsigned char)m[i]) || m[i] == ':'))
                { a.bssid[i] = (char)tolower((unsigned char)m[i]); i++; }
            a.bssid[i] = '\0';
            a.valid = (i == 17);
            continue;
        }
        if (!a.valid) continue;

        /* lines inside a BSS block are indented with tabs/spaces; trim them */
        char *p = buf;
        while (*p == '\t' || *p == ' ') p++;

        if (!strncmp(p, "freq:", 5)) {
            int mhz = atoi(p + 5);
            a.freq_channel = freq_to_channel(mhz);
        } else if (!strncmp(p, "signal:", 7)) {
            /* "signal: -88.00 dBm" -> -88 (round toward zero is fine) */
            double v = atof(p + 7);
            a.signal = (int)(v < 0 ? v - 0.5 : v + 0.5);
            a.have_signal = true;
        } else if (!strncmp(p, "SSID:", 5)) {
            const char *s = p + 5;
            while (*s == ' ') s++;
            strncpy(a.ssid, s, sizeof a.ssid - 1);
            a.ssid[sizeof a.ssid - 1] = '\0';
            a.have_ssid = true;
            a.ssid_hidden = ssid_is_hidden(a.ssid);
        } else if (!strncmp(p, "DS Parameter set: channel", 25)) {
            a.channel = atoi(p + 25);
        } else if (!strncmp(p, "capability:", 11)) {
            if (strstr(p, "Privacy")) a.privacy = true;
        } else if (!strncmp(p, "RSN:", 4)) {
            a.rsn = true;
        } else if (!strncmp(p, "WPA:", 4)) {
            a.wpa = true;
        } else if (!strncmp(p, "WPS:", 4)) {
            a.wps = true;
        } else if (a.rsn && strstr(p, "Authentication suites")) {
            /* inside an RSN block: "* Authentication suites: SAE" => WPA3 */
            if (strstr(p, "SAE")) a.rsn_sae = true;
        }
    }

    /* flush the last BSS */
    written += bss_flush(&a, scan_id, now);
    return written;
}

/* ------------------------------------------------------------------------- *
 *  STA interface detection
 * ------------------------------------------------------------------------- */
/* Bounded string copy that always NUL-terminates (no strncpy truncation warn). */
static void safe_copy(char *dst, size_t cap, const char *src)
{
    if (!cap) return;
    size_t i = 0;
    for (; i + 1 < cap && src[i]; i++) dst[i] = src[i];
    dst[i] = '\0';
}

/* Read a whole command's stdout into a malloc'd NUL-terminated buffer.
 * Returns NULL on failure. Caller frees. */
static char *run_capture(const char *cmd)
{
    FILE *fp = popen(cmd, "r");
    if (!fp) return NULL;
    size_t cap = 8192, len = 0;
    char *out = malloc(cap);
    if (!out) { pclose(fp); return NULL; }
    size_t r;
    char tmp[4096];
    while ((r = fread(tmp, 1, sizeof tmp, fp)) > 0) {
        if (len + r + 1 > cap) {
            cap = (len + r + 1) * 2;
            char *n = realloc(out, cap);
            if (!n) { free(out); pclose(fp); return NULL; }
            out = n;
        }
        memcpy(out + len, tmp, r);
        len += r;
    }
    out[len] = '\0';
    pclose(fp);
    return out;
}

/* Determine the managed STA interface to scan.
 *   1. config "sta_scan_interface" if set
 *   2. first "type managed" interface from `iw dev` that has an active link
 *   3. fall back to "wlan0"
 * Writes the chosen name into out (cap bytes). */
void scan_sta_pick_interface(char *out, size_t cap)
{
    pager_config_t *c = config_get();
    if (c->sta_scan_interface[0]) {
        safe_copy(out, cap, c->sta_scan_interface);
        return;
    }

    char *dev = run_capture("iw dev 2>/dev/null");
    char candidate[64] = {0};
    char managed_any[64] = {0};
    if (dev) {
        /* walk the `iw dev` output: track the current Interface name, remember
         * managed ones; prefer the one whose `iw dev X link` is associated. */
        char ifname[64] = {0};
        char *save = NULL;
        for (char *ln = strtok_r(dev, "\n", &save); ln; ln = strtok_r(NULL, "\n", &save)) {
            char *p = ln;
            while (*p == '\t' || *p == ' ') p++;
            if (!strncmp(p, "Interface ", 10)) {
                safe_copy(ifname, sizeof ifname, p + 10);
            } else if (!strncmp(p, "type managed", 12) && ifname[0]) {
                if (!managed_any[0])
                    safe_copy(managed_any, sizeof managed_any, ifname);
                /* check link state */
                char cmd[128];
                snprintf(cmd, sizeof cmd, "iw dev %s link 2>/dev/null", ifname);
                char *link = run_capture(cmd);
                if (link) {
                    if (strstr(link, "Connected to"))
                        safe_copy(candidate, sizeof candidate, ifname);
                    free(link);
                }
                if (candidate[0]) break;
            }
        }
        free(dev);
    }

    const char *chosen = candidate[0] ? candidate :
                         (managed_any[0] ? managed_any : "wlan0");
    safe_copy(out, cap, chosen);
}

/* ------------------------------------------------------------------------- *
 *  One scan pass: run `iw dev <staif> scan`, parse, write rows.
 * ------------------------------------------------------------------------- */
int scan_sta_once(const char *staif, int scan_id)
{
    char iface[64];
    if (staif && *staif) {
        safe_copy(iface, sizeof iface, staif);
    } else {
        scan_sta_pick_interface(iface, sizeof iface);
    }

    /* basic sanity: interface name must be plausible to avoid shell issues */
    for (const char *p = iface; *p; p++) {
        if (!(isalnum((unsigned char)*p) || *p == '-' || *p == '_' || *p == '.')) {
            pineap_log("STASCAN", "refusing suspicious interface name '%s'", iface);
            return -1;
        }
    }

    char cmd[160];
    snprintf(cmd, sizeof cmd, "iw dev %s scan 2>/dev/null", iface);
    char *out = run_capture(cmd);
    if (!out) {
        pineap_log("STASCAN", "iw scan on %s failed to run", iface);
        return -1;
    }
    long now = time(NULL);
    int n = scan_sta_parse_buffer(out, scan_id, now);
    free(out);
    pineap_log("STASCAN", "scan on %s wrote %d APs (scan_id=%d)", iface, n, scan_id);
    return n;
}

/* ------------------------------------------------------------------------- *
 *  Background thread: scan every ~5s while active.
 * ------------------------------------------------------------------------- */
#define STA_SCAN_INTERVAL_SEC 5

static pthread_t     g_sta_thread;
static volatile bool g_sta_run = false;
static int           g_sta_scan_id = -1;
static char          g_sta_iface[64] = {0};

static void *sta_scan_run(void *arg)
{
    (void)arg;
    /* resolve the STA interface once at start (link state is stable for a scan) */
    scan_sta_pick_interface(g_sta_iface, sizeof g_sta_iface);
    pineap_log("STASCAN", "STA scan thread started on %s", g_sta_iface);

    /* do a first pass immediately so the Dashboard populates fast */
    while (g_sta_run) {
        scan_sta_once(g_sta_iface, g_sta_scan_id);
        /* sleep in 1s slices so stop() is responsive */
        for (int i = 0; i < STA_SCAN_INTERVAL_SEC && g_sta_run; i++)
            sleep(1);
    }
    pineap_log("STASCAN", "STA scan thread stopped");
    return NULL;
}

void scan_sta_start(int scan_id)
{
    if (g_sta_run) {
        /* already running: just update the scan_id so new rows are tagged right */
        g_sta_scan_id = scan_id;
        return;
    }
    g_sta_scan_id = scan_id;
    g_sta_run = true;
    if (pthread_create(&g_sta_thread, NULL, sta_scan_run, NULL) != 0) {
        g_sta_run = false;
        pineap_log("STASCAN", "failed to start STA scan thread");
    }
}

void scan_sta_stop(void)
{
    if (!g_sta_run) return;
    g_sta_run = false;
    pthread_join(g_sta_thread, NULL);
}
