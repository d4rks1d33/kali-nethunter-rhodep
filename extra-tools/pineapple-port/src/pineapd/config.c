/*
 * config.c — PineAPConfig singleton, ScanState singleton, MACAddress, Logger.
 *
 * Config file format (INFORME §B): "key = value", one per line, header:
 *   # PineAP Config File
 *   # -- DO NOT EDIT MANUALLY --
 * The PHP panel reads/writes this file, so the key names MUST match.
 */
#include "pineapd.h"
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
    /* accept aa:bb:.. or aabb.. (12 hex) */
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

/* ------------------------- InjectInterval ------------------------------- */
const char *inject_interval_to_str(uint32_t us)
{
    switch (us) {
        case INJECT_LOW:        return "LOW";
        case INJECT_AGGRESSIVE: return "AGGRESSIVE";
        case INJECT_NORMAL:     default: return "NORMAL";
    }
}

uint32_t inject_interval_from_str(const char *s)
{
    if (!s) return INJECT_NORMAL;
    if (!strcasecmp(s, "LOW"))        return INJECT_LOW;
    if (!strcasecmp(s, "AGGRESSIVE")) return INJECT_AGGRESSIVE;
    return INJECT_NORMAL;
}

/* --------------------------- PineAPConfig ------------------------------- */
static pineap_config_t g_config;
static bool g_config_init = false;

static void config_defaults(pineap_config_t *c)
{
    memset(c, 0, sizeof *c);
    c->autostart = 0;
    c->karma = false;
    c->beacon_interval = INJECT_NORMAL;
    c->beacon_response_interval = INJECT_NORMAL;
    c->beacon_responses = false;
    c->capture_ssids = false;
    c->broadcast_ssid_pool = false;
    c->logging = false;
    c->mac_filter = FILTER_DENY;
    c->ssid_filter = FILTER_DENY;
    c->ap_channel = 1;
    c->connect_notifications = false;
    c->disconnect_notifications = false;
    c->rts_enabled = false;
    c->rts_scan_id = -1;
    strncpy(c->pineap_interface, PINEAPD_DEFAULT_IFACE, sizeof c->pineap_interface - 1);
    /* native STA recon scan: empty => auto-detect (connected managed iface),
     * falling back to "wlan0". Override with sta_scan_interface in pineap.conf. */
    c->sta_scan_interface[0] = '\0';
    strncpy(c->recon_db_path, PINEAPD_RECON_DB, sizeof c->recon_db_path - 1);
    strncpy(c->hostapd_db_path, PINEAPD_LOG_DB, sizeof c->hostapd_db_path - 1);
    strncpy(c->ssid_db_path, PINEAPD_SSID_DB, sizeof c->ssid_db_path - 1);
    pthread_mutex_init(&c->lock, NULL);
}

pineap_config_t *config_get(void)
{
    if (!g_config_init) {
        config_defaults(&g_config);
        g_config_init = true;
    }
    return &g_config;
}

void config_set_path(const char *path)
{
    pineap_config_t *c = config_get();
    strncpy(c->config_path, path, sizeof c->config_path - 1);
    c->config_path[sizeof c->config_path - 1] = '\0';
}

static bool parse_bool(const char *v)
{
    return (!strcasecmp(v, "on") || !strcasecmp(v, "1") ||
            !strcasecmp(v, "true") || !strcasecmp(v, "yes"));
}

static filter_mode_t parse_filter(const char *v)
{
    if (!strcasecmp(v, "white") || !strcasecmp(v, "allow")) return FILTER_ALLOW;
    if (!strcasecmp(v, "black") || !strcasecmp(v, "deny"))  return FILTER_DENY;
    return FILTER_INVALID;
}

/* tolerant line parser: "key = value" (any amount of whitespace) */
static void config_apply_line(pineap_config_t *c, char *line)
{
    char *hash = strchr(line, '#');
    if (hash == line) return;           /* full-line comment */
    char *eq = strchr(line, '=');
    if (!eq) return;
    *eq = '\0';
    char *key = line, *val = eq + 1;
    /* trim key */
    while (*key && isspace((unsigned char)*key)) key++;
    char *ke = key + strlen(key);
    while (ke > key && isspace((unsigned char)ke[-1])) *--ke = '\0';
    /* trim val */
    while (*val && isspace((unsigned char)*val)) val++;
    char *ve = val + strlen(val);
    while (ve > val && isspace((unsigned char)ve[-1])) *--ve = '\0';
    if (!*key) return;

    if (!strcmp(key, "autostart"))                 c->autostart = atoi(val);
    else if (!strcmp(key, "karma"))                c->karma = parse_bool(val);
    else if (!strcmp(key, "beacon_interval"))      c->beacon_interval = inject_interval_from_str(val);
    else if (!strcmp(key, "beacon_response_interval")) c->beacon_response_interval = inject_interval_from_str(val);
    else if (!strcmp(key, "beacon_responses"))     c->beacon_responses = parse_bool(val);
    else if (!strcmp(key, "capture_ssids"))        c->capture_ssids = parse_bool(val);
    else if (!strcmp(key, "broadcast_ssid_pool"))  c->broadcast_ssid_pool = parse_bool(val);
    else if (!strcmp(key, "logging"))              c->logging = parse_bool(val);
    else if (!strcmp(key, "mac_filter"))           c->mac_filter = parse_filter(val);
    else if (!strcmp(key, "ssid_filter"))          c->ssid_filter = parse_filter(val);
    else if (!strcmp(key, "ap_channel"))           c->ap_channel = atoi(val);
    else if (!strcmp(key, "pineap_mac"))           mac_from_str(val, &c->pineap_mac);
    else if (!strcmp(key, "target_mac"))           mac_from_str(val, &c->target_mac);
    else if (!strcmp(key, "pineap_interface"))     { strncpy(c->pineap_interface, val, sizeof c->pineap_interface - 1); c->pineap_interface[sizeof c->pineap_interface-1]=0; }
    else if (!strcmp(key, "sta_scan_interface"))   { strncpy(c->sta_scan_interface, val, sizeof c->sta_scan_interface - 1); c->sta_scan_interface[sizeof c->sta_scan_interface-1]=0; }
    else if (!strcmp(key, "recon_db_path"))        { strncpy(c->recon_db_path, val, sizeof c->recon_db_path - 1); c->recon_db_path[sizeof c->recon_db_path-1]=0; }
    else if (!strcmp(key, "hostapd_db_path"))      { strncpy(c->hostapd_db_path, val, sizeof c->hostapd_db_path - 1); c->hostapd_db_path[sizeof c->hostapd_db_path-1]=0; }
    else if (!strcmp(key, "ssid_db_path"))         { strncpy(c->ssid_db_path, val, sizeof c->ssid_db_path - 1); c->ssid_db_path[sizeof c->ssid_db_path-1]=0; }
    else if (!strcmp(key, "connect_notifications"))     c->connect_notifications = parse_bool(val);
    else if (!strcmp(key, "disconnect_notifications"))  c->disconnect_notifications = parse_bool(val);
    else if (!strcmp(key, "rts_enabled"))          c->rts_enabled = parse_bool(val);
    else if (!strcmp(key, "rts_scan_id"))          c->rts_scan_id = atoi(val);
    /* unknown keys are tolerated and ignored */
}

int config_reload(void)
{
    pineap_config_t *c = config_get();
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
    return 0;
}

static const char *filter_to_str(filter_mode_t m)
{
    switch (m) { case FILTER_ALLOW: return "white"; case FILTER_DENY: return "black"; default: return "black"; }
}

int config_save(void)
{
    pineap_config_t *c = config_get();
    if (!c->config_path[0]) return -1;
    FILE *f = fopen(c->config_path, "w");
    if (!f) { pineap_log("CONFIG", "could not write %s", c->config_path); return -1; }
    pthread_mutex_lock(&c->lock);
    char smac[18], tmac[18];
    mac_to_str(&c->pineap_mac, smac);
    mac_to_str(&c->target_mac, tmac);
    fprintf(f, "# PineAP Config File\n");
    fprintf(f, "# -- DO NOT EDIT MANUALLY --\n");
    fprintf(f, "autostart = %d\n", c->autostart);
    fprintf(f, "karma = %s\n", c->karma ? "on" : "off");
    fprintf(f, "beacon_interval = %s\n", inject_interval_to_str(c->beacon_interval));
    fprintf(f, "beacon_response_interval = %s\n", inject_interval_to_str(c->beacon_response_interval));
    fprintf(f, "beacon_responses = %s\n", c->beacon_responses ? "on" : "off");
    fprintf(f, "capture_ssids = %s\n", c->capture_ssids ? "on" : "off");
    fprintf(f, "broadcast_ssid_pool = %s\n", c->broadcast_ssid_pool ? "on" : "off");
    fprintf(f, "logging = %s\n", c->logging ? "on" : "off");
    fprintf(f, "mac_filter = %s\n", filter_to_str(c->mac_filter));
    fprintf(f, "ssid_filter = %s\n", filter_to_str(c->ssid_filter));
    fprintf(f, "ap_channel = %d\n", c->ap_channel);
    fprintf(f, "pineap_mac = %s\n", smac);
    fprintf(f, "target_mac = %s\n", tmac);
    fprintf(f, "pineap_interface = %s\n", c->pineap_interface);
    fprintf(f, "sta_scan_interface = %s\n", c->sta_scan_interface);
    fprintf(f, "recon_db_path = %s\n", c->recon_db_path);
    fprintf(f, "hostapd_db_path = %s\n", c->hostapd_db_path);
    fprintf(f, "ssid_db_path = %s\n", c->ssid_db_path);
    fprintf(f, "connect_notifications = %s\n", c->connect_notifications ? "on" : "off");
    fprintf(f, "disconnect_notifications = %s\n", c->disconnect_notifications ? "on" : "off");
    fprintf(f, "rts_enabled = %s\n", c->rts_enabled ? "on" : "off");
    fprintf(f, "rts_scan_id = %d\n", c->rts_scan_id);
    pthread_mutex_unlock(&c->lock);
    fclose(f);
    return 0;
}

/* ---------------------------- ScanState --------------------------------- */
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

/*
 * scan_state_to_json — byte-identical to the original ScanState::toJSON.
 *
 * Reference capture from the real daemon (idle):
 *   {\n\t"captureRunning": false,\n\t"scanRunning": false\n}\n
 *
 * Full branches reconstructed from binary string fragments @0x1be4c:
 *   capture on  -> \t"captureRunning": true,\n  then  \t"bssid": "<mac>"
 *   scan on     -> \t"scanRunning": true,\n \t"continuous": <b> \t"scanID": <n>
 *                  \t"scanPercent": <n> }\n
 *   scan off    -> \t"scanRunning": false\n}\n
 */
size_t scan_state_to_json(char *out, size_t cap)
{
    scan_state_t *s = scan_state_get();
    pthread_mutex_lock(&s->lock);
    char bssid[18];
    mac_to_str(&s->bssid, bssid);
    bool cap_on  = s->handshake_running;
    bool scan_on = s->recon_running;
    bool cont    = s->continuous;
    int  sid     = s->recon_scan_id;
    int  pct     = s->scan_percent;
    pthread_mutex_unlock(&s->lock);

    size_t n = 0;
    #define APPEND(...) do { int _w = snprintf(out + n, (cap > n) ? cap - n : 0, __VA_ARGS__); if (_w > 0) n += (size_t)_w; } while (0)
    APPEND("{\n");
    if (cap_on) {
        APPEND("\t\"captureRunning\": true,\n");
        APPEND("\t\"bssid\": \"%s\"\n", bssid);
    } else {
        APPEND("\t\"captureRunning\": false,\n");
    }
    if (scan_on) {
        APPEND("\t\"scanRunning\": true,\n");
        APPEND("\t\"continuous\": %s\n", cont ? "true" : "false");
        APPEND("\t\"scanID\": %d\n", sid);
        APPEND("\t\"scanPercent\": %d\n", pct);
        APPEND("}\n");
    } else {
        APPEND("\t\"scanRunning\": false\n}\n");
    }
    #undef APPEND
    return n;
}
