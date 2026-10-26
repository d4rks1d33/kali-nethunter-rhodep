/*
 * config.c — pager_config_t singleton, scan_state_t singleton, MACAddress
 *            helpers and the Logger. Replaces libuci with a tiny "key = value"
 *            parser over /opt/pineapple-pager/etc/pineapd.conf.
 *
 * Config file format: one "key = value" per line, '#' comments. The keys mirror
 * the init flags used by the real Pager procd service (ARQUITECTURA-PORT.md §5):
 *   internal_iface, monitor_iface, sta_scan_interface,
 *   recon, reconpath, reconname, handshakepath, payloads, wiglepath, pcappath,
 *   bands, primary (monitor), inject, hop, channel,
 *   recon_db_path, hostapd_db_path, ssid_db_path,
 *   ssid_collect, broadcast_ssid_pool, karma, gpsd_host, gpsd_port.
 *
 * Defaults are sensible for the G82 (wlan0 internal, wlan1mon Archer), so the
 * daemon boots and serves the socket even with no config file present.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdarg.h>
#include <ctype.h>
#include <time.h>

/* ----------------------------- Logger ----------------------------------- */
static bool g_log_enabled = false;
void logger_set_enabled(bool e) { g_log_enabled = e; }

void pineap_log(const char *tag, const char *fmt, ...)
{
    if (!g_log_enabled) return;
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    char ts[32];
    strftime(ts, sizeof ts, "%d-%m-%Y %H:%M:%S", &tm);
    fprintf(stderr, "[!! %s] [%s] ", ts, tag ? tag : "MAIN");
    va_list ap; va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

/* --------------------------- MACAddress --------------------------------- */
void mac_to_str(const mac_t *m, char out[18])
{
    snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
             m->b[0], m->b[1], m->b[2], m->b[3], m->b[4], m->b[5]);
}

bool mac_from_str(const char *s, mac_t *out)
{
    if (!s) return false;
    unsigned v[6];
    if (sscanf(s, "%2x:%2x:%2x:%2x:%2x:%2x",
               &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6) {
        for (int i = 0; i < 6; i++) out->b[i] = (uint8_t)v[i];
        return true;
    }
    if (strlen(s) >= 12 &&
        sscanf(s, "%2x%2x%2x%2x%2x%2x",
               &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6) {
        for (int i = 0; i < 6; i++) out->b[i] = (uint8_t)v[i];
        return true;
    }
    return false;
}

bool mac_is_zero(const mac_t *m)
{
    for (int i = 0; i < 6; i++) if (m->b[i]) return false;
    return true;
}

bool mac_is_broadcast(const mac_t *m)
{
    for (int i = 0; i < 6; i++) if (m->b[i] != 0xff) return false;
    return true;
}

/* --------------------------- pager_config_t ----------------------------- */
static pager_config_t g_config;
static bool g_config_init = false;

static void config_defaults(pager_config_t *c)
{
    memset(c, 0, sizeof *c);
    strncpy(c->internal_iface, PINEAPD_INTERNAL_IFACE, sizeof c->internal_iface - 1);
    strncpy(c->monitor_iface,  PINEAPD_MONITOR_IFACE,  sizeof c->monitor_iface - 1);
    c->sta_scan_interface[0] = '\0';   /* empty => auto-detect connected STA  */

    c->recon_enabled = false;
    strncpy(c->recon_path,     PINEAPD_RECON_DB,       sizeof c->recon_path - 1);
    strncpy(c->recon_name,     "pager",                sizeof c->recon_name - 1);
    strncpy(c->handshake_path, PINEAPD_HANDSHAKE_DIR,  sizeof c->handshake_path - 1);
    c->payloads_path[0] = '\0';
    c->wigle_path[0]    = '\0';
    strncpy(c->pcap_path,      PINEAPD_PCAP_DIR,       sizeof c->pcap_path - 1);
    strncpy(c->recon_db_path,  PINEAPD_RECON_DB,       sizeof c->recon_db_path - 1);
    strncpy(c->hostapd_db_path,PINEAPD_LOG_DB,         sizeof c->hostapd_db_path - 1);
    strncpy(c->ssid_db_path,   PINEAPD_SSID_DB,        sizeof c->ssid_db_path - 1);

    c->inject = true;
    c->hop = true;
    c->channel = 0;                    /* 0 => hop                            */
    strncpy(c->bands, "2.4", sizeof c->bands - 1);
    c->ssid_collect = false;
    c->broadcast_ssid_pool = false;
    c->karma = false;

    strncpy(c->gpsd_host, "127.0.0.1", sizeof c->gpsd_host - 1);
    c->gpsd_port = 2947;

    pthread_mutex_init(&c->lock, NULL);
}

pager_config_t *config_get(void)
{
    if (!g_config_init) {
        config_defaults(&g_config);
        g_config_init = true;
    }
    return &g_config;
}

void config_set_path(const char *path)
{
    pager_config_t *c = config_get();
    strncpy(c->config_path, path, sizeof c->config_path - 1);
    c->config_path[sizeof c->config_path - 1] = '\0';
}

static bool parse_bool(const char *v)
{
    return (!strcasecmp(v, "on") || !strcasecmp(v, "1") ||
            !strcasecmp(v, "true") || !strcasecmp(v, "yes"));
}

#define CPYFIELD(dst, val) do { \
        strncpy((dst), (val), sizeof(dst) - 1); (dst)[sizeof(dst)-1] = '\0'; \
    } while (0)

static void config_apply_line(pager_config_t *c, char *line)
{
    char *hash = strchr(line, '#');
    if (hash == line) return;
    char *eq = strchr(line, '=');
    if (!eq) return;
    *eq = '\0';
    char *key = line, *val = eq + 1;
    while (*key && isspace((unsigned char)*key)) key++;
    char *ke = key + strlen(key);
    while (ke > key && isspace((unsigned char)ke[-1])) *--ke = '\0';
    while (*val && isspace((unsigned char)*val)) val++;
    char *ve = val + strlen(val);
    while (ve > val && isspace((unsigned char)ve[-1])) *--ve = '\0';
    if (!*key) return;

    if      (!strcmp(key, "internal_iface"))     CPYFIELD(c->internal_iface, val);
    else if (!strcmp(key, "monitor_iface") ||
             !strcmp(key, "primary"))            CPYFIELD(c->monitor_iface, val);
    else if (!strcmp(key, "sta_scan_interface")) CPYFIELD(c->sta_scan_interface, val);
    else if (!strcmp(key, "recon"))              c->recon_enabled = parse_bool(val);
    else if (!strcmp(key, "reconpath") ||
             !strcmp(key, "recon_path"))       { CPYFIELD(c->recon_path, val);
                                                  CPYFIELD(c->recon_db_path, val); }
    else if (!strcmp(key, "reconname") ||
             !strcmp(key, "recon_name"))         CPYFIELD(c->recon_name, val);
    else if (!strcmp(key, "handshakepath") ||
             !strcmp(key, "handshake_path"))     CPYFIELD(c->handshake_path, val);
    else if (!strcmp(key, "payloads") ||
             !strcmp(key, "payloads_path"))      CPYFIELD(c->payloads_path, val);
    else if (!strcmp(key, "wiglepath") ||
             !strcmp(key, "wigle_path"))         CPYFIELD(c->wigle_path, val);
    else if (!strcmp(key, "pcappath") ||
             !strcmp(key, "pcap_path"))          CPYFIELD(c->pcap_path, val);
    else if (!strcmp(key, "recon_db_path"))      CPYFIELD(c->recon_db_path, val);
    else if (!strcmp(key, "hostapd_db_path"))    CPYFIELD(c->hostapd_db_path, val);
    else if (!strcmp(key, "ssid_db_path"))       CPYFIELD(c->ssid_db_path, val);
    else if (!strcmp(key, "inject"))             c->inject = parse_bool(val);
    else if (!strcmp(key, "hop"))                c->hop = parse_bool(val);
    else if (!strcmp(key, "channel"))            c->channel = atoi(val);
    else if (!strcmp(key, "bands") ||
             !strcmp(key, "band"))               CPYFIELD(c->bands, val);
    else if (!strcmp(key, "ssid_collect"))       c->ssid_collect = parse_bool(val);
    else if (!strcmp(key, "broadcast_ssid_pool"))c->broadcast_ssid_pool = parse_bool(val);
    else if (!strcmp(key, "karma"))              c->karma = parse_bool(val);
    else if (!strcmp(key, "gpsd_host"))          CPYFIELD(c->gpsd_host, val);
    else if (!strcmp(key, "gpsd_port"))          c->gpsd_port = atoi(val);
    /* unknown keys tolerated and ignored */
}

int config_reload(void)
{
    pager_config_t *c = config_get();
    if (!c->config_path[0]) return -1;
    FILE *f = fopen(c->config_path, "r");
    if (!f) {
        pineap_log("CONFIG", "could not open %s (using defaults)", c->config_path);
        return -1;
    }
    pthread_mutex_lock(&c->lock);
    char line[1024];
    while (fgets(line, sizeof line, f)) {
        size_t n = strlen(line);
        while (n && (line[n-1] == '\n' || line[n-1] == '\r')) line[--n] = '\0';
        config_apply_line(c, line);
    }
    pthread_mutex_unlock(&c->lock);
    fclose(f);
    pineap_log("CONFIG", "loaded %s", c->config_path);
    return 0;
}

int config_save(void)
{
    pager_config_t *c = config_get();
    if (!c->config_path[0]) return -1;
    FILE *f = fopen(c->config_path, "w");
    if (!f) { pineap_log("CONFIG", "could not write %s", c->config_path); return -1; }
    pthread_mutex_lock(&c->lock);
    fprintf(f, "# pineapd-pager config\n");
    fprintf(f, "# -- managed by pineapd-pager --\n");
    fprintf(f, "internal_iface = %s\n", c->internal_iface);
    fprintf(f, "monitor_iface = %s\n", c->monitor_iface);
    fprintf(f, "sta_scan_interface = %s\n", c->sta_scan_interface);
    fprintf(f, "recon = %s\n", c->recon_enabled ? "on" : "off");
    fprintf(f, "reconpath = %s\n", c->recon_path);
    fprintf(f, "reconname = %s\n", c->recon_name);
    fprintf(f, "handshakepath = %s\n", c->handshake_path);
    fprintf(f, "payloads = %s\n", c->payloads_path);
    fprintf(f, "wiglepath = %s\n", c->wigle_path);
    fprintf(f, "pcappath = %s\n", c->pcap_path);
    fprintf(f, "recon_db_path = %s\n", c->recon_db_path);
    fprintf(f, "hostapd_db_path = %s\n", c->hostapd_db_path);
    fprintf(f, "ssid_db_path = %s\n", c->ssid_db_path);
    fprintf(f, "inject = %s\n", c->inject ? "on" : "off");
    fprintf(f, "hop = %s\n", c->hop ? "on" : "off");
    fprintf(f, "channel = %d\n", c->channel);
    fprintf(f, "bands = %s\n", c->bands);
    fprintf(f, "ssid_collect = %s\n", c->ssid_collect ? "on" : "off");
    fprintf(f, "broadcast_ssid_pool = %s\n", c->broadcast_ssid_pool ? "on" : "off");
    fprintf(f, "karma = %s\n", c->karma ? "on" : "off");
    fprintf(f, "gpsd_host = %s\n", c->gpsd_host);
    fprintf(f, "gpsd_port = %d\n", c->gpsd_port);
    pthread_mutex_unlock(&c->lock);
    fclose(f);
    return 0;
}

/* ---------------------------- scan_state_t ------------------------------ */
static scan_state_t g_scan;
static bool g_scan_init = false;

scan_state_t *scan_state_get(void)
{
    if (!g_scan_init) {
        memset(&g_scan, 0, sizeof g_scan);
        g_scan.running = true;
        g_scan.recon_scan_id = -1;
        pthread_mutex_init(&g_scan.lock, NULL);
        g_scan_init = true;
    }
    return &g_scan;
}
