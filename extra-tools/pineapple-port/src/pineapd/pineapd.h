/*
 * pineapd-ng — native aarch64 reimplementation of the WiFi Pineapple PineAP
 * daemon (pineapd), functionally compatible with the original MIPS binary.
 *
 * Central header: configurable paths, protocol constants, shared types.
 *
 * Evidence basis (see docs/):
 *   - PROTOCOLO-DEFINITIVO.md : socket protocol (4097 bytes, big-endian, cmd table)
 *   - INFORME_pineapd_RE.md   : config, SQLite schema, threads, karma, deauth
 */
#ifndef PINEAPD_NG_H
#define PINEAPD_NG_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <pthread.h>
#include <sys/types.h>

/* ------------------------------------------------------------------------- *
 *  Configurable paths — default to the ORIGINAL paths so the PHP panel and
 *  the pineap CLI talk to pineapd-ng exactly as to the original.  Override at
 *  build time with -DPINEAPD_SOCK_PATH=... etc. for a Kali relocation to
 *  /opt/pineapple/run/ if required.
 * ------------------------------------------------------------------------- */
#ifndef PINEAPD_SOCK_PATH
#define PINEAPD_SOCK_PATH   "/var/run/pineapd.sock"
#endif
#ifndef PINEAPD_LOCK_PATH
#define PINEAPD_LOCK_PATH   "/var/run/pineapd.lock"
#endif
#ifndef PINEAPD_CONF_PATH
#define PINEAPD_CONF_PATH   "/tmp/pineap.conf"
#endif
#ifndef PINEAPD_RECON_DB
#define PINEAPD_RECON_DB    "/tmp/recon.db"
#endif
#ifndef PINEAPD_LOG_DB
#define PINEAPD_LOG_DB      "/tmp/log.db"
#endif
#ifndef PINEAPD_SSID_DB
#define PINEAPD_SSID_DB     "/etc/pineapple/pineapple.db"
#endif
#ifndef PINEAPD_HANDSHAKE_DIR
#define PINEAPD_HANDSHAKE_DIR "/tmp/handshakes"
#endif
/* Default capture iface = dedicated USB adapter (Archer wlan1), NEVER the
 * internal radio (wlan0mon): monitor on the WCN3990 tears the home LAN down.
 * With no Archer plugged in, the safety gate skips monitor and recon runs via
 * the native STA scan (scan_sta). */
#ifndef PINEAPD_DEFAULT_IFACE
#define PINEAPD_DEFAULT_IFACE "wlan1mon"
#endif

/* ------------------------------------------------------------------------- *
 *  Protocol — EXACT match of the original (PROTOCOLO-DEFINITIVO.md)
 * ------------------------------------------------------------------------- */
#define PINEAP_CMD_LEN      4097   /* request buffer: fixed, zero-filled    */
#define PINEAP_RECV_RETRIES 25001  /* max EAGAIN retries (matches 0x3589+..) */
#define PINEAP_RECV_USLEEP  20     /* usleep between EAGAIN retries          */

/* Command IDs (buf[0]) — server dispatch is switch(buf[0]-1) */
enum pineap_command {
    CMD_RUN_SCAN                = 4,
    CMD_PAUSE_SCAN             = 5,
    CMD_UNPAUSE_SCAN           = 6,
    CMD_STOP_SCAN              = 7,
    CMD_DEAUTH                 = 8,
    CMD_HANDSHAKE_CAPTURE_START = 9,
    CMD_HANDSHAKE_CAPTURE_STOP  = 10,
    CMD_RELOAD_CONFIG          = 11,
    CMD_PAUSE                  = 12,  /* channel-hop pause  */
    CMD_UNPAUSE                = 13,  /* channel-hop resume */
    CMD_ADD_SSID               = 14,
    CMD_DEL_SSID               = 15,
    CMD_CLEAR_SSIDS            = 16,
    CMD_LIST_SSIDS             = 17,  /* has response */
    CMD_LIST_PROBES            = 18,  /* has response */
    CMD_INJECT                 = 19,
    CMD_GET_STATUS             = 20,  /* has response (JSON) */
    CMD_SETOPT                 = 21
};

/* setOpt sub-options (buf[1]) — §5 of PROTOCOLO-DEFINITIVO.md */
enum pineap_opt {
    OPT_KARMA                     = 0,  /* bool  [6]      */
    OPT_BEACON_RESPONSES          = 1,  /* bool  [6]      */
    OPT_CAPTURE_SSIDS             = 2,  /* bool  [6]      */
    OPT_BROADCAST_POOL            = 3,  /* bool  [6]      */
    OPT_LOGGING                   = 4,  /* bool  [6]      */
    OPT_AP_CHANNEL                = 9,  /* QUIRK: byte@6  */
    OPT_CONNECT_NOTIFICATIONS     = 10, /* bool  [6]      */
    OPT_DISCONNECT_NOTIFICATIONS  = 11, /* bool  [6]      */
    OPT_BEACON_INTERVAL           = 12, /* u32BE [6..9]   */
    OPT_BEACON_RESPONSE_INTERVAL  = 13, /* u32BE [6..9]   */
    OPT_SET_SOURCE                = 15, /* MAC   [6..11]  */
    OPT_SET_TARGET                = 16  /* MAC   [6..11]  */
};

/* InjectInterval (beacon cadence) — values are microseconds on the wire */
enum inject_interval {
    INJECT_LOW        = 9000,  /* 0x2328 */
    INJECT_NORMAL     = 5712,  /* 0x1650 */
    INJECT_AGGRESSIVE = 3500   /* 0x0dac */
};

/* ------------------------------------------------------------------------- *
 *  MACAddress
 * ------------------------------------------------------------------------- */
typedef struct { uint8_t b[6]; } mac_t;
void   mac_to_str(const mac_t *m, char out[18]);
bool   mac_from_str(const char *s, mac_t *out);
bool   mac_is_zero(const mac_t *m);
bool   mac_is_broadcast(const mac_t *m);

/* ------------------------------------------------------------------------- *
 *  PineAPConfig (singleton) — ~24 fields (INFORME §B.2)
 * ------------------------------------------------------------------------- */
typedef enum { FILTER_DENY = 0, FILTER_ALLOW = 1, FILTER_INVALID = 2 } filter_mode_t;

typedef struct {
    int           autostart;
    bool          karma;
    uint32_t      beacon_interval;            /* us (InjectInterval value) */
    uint32_t      beacon_response_interval;   /* us */
    bool          beacon_responses;
    bool          capture_ssids;
    bool          broadcast_ssid_pool;
    bool          logging;
    filter_mode_t mac_filter;
    filter_mode_t ssid_filter;
    int           ap_channel;                 /* note the ap_channel quirk */
    mac_t         pineap_mac;                 /* BSSID of forged beacons   */
    mac_t         target_mac;                 /* destination for beacons/deauth */
    char          pineap_interface[64];
    char          sta_scan_interface[64];     /* managed STA iface for native recon scan (default: auto / wlan0) */
    char          recon_db_path[256];
    char          hostapd_db_path[256];       /* == log db */
    char          ssid_db_path[256];
    bool          connect_notifications;
    bool          disconnect_notifications;
    bool          rts_enabled;
    int           rts_scan_id;
    char          config_path[256];
    pthread_mutex_t lock;
} pineap_config_t;

pineap_config_t *config_get(void);
void config_set_path(const char *path);
int  config_reload(void);         /* parse config_path into the singleton  */
int  config_save(void);           /* re-serialize to config_path           */
const char *inject_interval_to_str(uint32_t us);
uint32_t    inject_interval_from_str(const char *s);

/* ------------------------------------------------------------------------- *
 *  ScanState (singleton) — H.2 / C.5
 * ------------------------------------------------------------------------- */
typedef struct {
    bool     running;            /* daemon main loop should run              */
    bool     paused;             /* scan paused                             */
    bool     hopping;            /* channel-hopping active                  */
    bool     recon_running;      /* scanRunning in JSON                     */
    bool     handshake_running;  /* captureRunning in JSON                  */
    bool     continuous;
    int      recon_scan_id;      /* scanID                                  */
    int      scan_percent;       /* scanPercent                             */
    mac_t    bssid;              /* bssid (handshake target)                */
    int      channel;
    time_t   start_time;
    int      duration_sec;
    int      freq_band;          /* 0=2.4, 1=5, 2=both                      */
    pthread_mutex_t lock;
} scan_state_t;

scan_state_t *scan_state_get(void);
/* Serialize to JSON the exact way the original ScanState::toJSON does.
 * Returns length written (not counting the trailing NUL). */
size_t scan_state_to_json(char *out, size_t cap);

/* ------------------------------------------------------------------------- *
 *  Logger
 * ------------------------------------------------------------------------- */
void logger_set_enabled(bool e);
void pineap_log(const char *tag, const char *fmt, ...);

/* ------------------------------------------------------------------------- *
 *  Databases (sqlite3) — EXACT schema (INFORME §G)
 * ------------------------------------------------------------------------- */
int  db_init_all(void);     /* open all DBs, creating schema (used by --initdbs) */
void db_close_all(void);

/* recon.db */
int  recon_next_scan_id(void);
void recon_write_ap(int scan_id, const char *ssid, const char *bssid,
                    int encryption, int hidden, int channel, int signal_,
                    int wps, long last_seen);
void recon_write_client(int scan_id, const char *mac, const char *bssid,
                        long last_seen);
/* log.db */
void log_write_entry(int log_type, const char *mac, const char *ssid,
                     long created_at, long updated_at);
/* list_probes responses: builds a '\n'-separated buffer. Caller frees. */
char *log_get_probes_all(void);
char *log_get_probes_mac(const char *mac);
/* ssid pineapple.db */
void ssid_add(const char *ssid);
void ssid_del(const char *ssid);
void ssid_clear(void);
bool ssid_has(const char *ssid);
char *ssid_list(void);                 /* '\n'-separated, caller frees      */
int   ssid_get_all(char ***out);       /* array for beaconing; caller frees */
void  ssid_free_all(char **arr, int n);
/* tracking */
bool tracking_is_tracked(const char *mac);

/* log types */
#define LOGTYPE_PROBE       0
#define LOGTYPE_ASSOCIATION 1
#define LOGTYPE_DEAUTH      2

/* ------------------------------------------------------------------------- *
 *  Inject queue / frames (I)
 * ------------------------------------------------------------------------- */
typedef struct inject_frame {
    uint8_t  channel;
    uint32_t delay_us;
    uint32_t multiplier;
    uint8_t *payload;
    size_t   len;
    struct inject_frame *next;
} inject_frame_t;

void inject_queue_push(uint8_t channel, uint32_t delay_us, uint32_t multiplier,
                       const uint8_t *payload, size_t len);
inject_frame_t *inject_queue_pop(void);  /* NULL if empty; caller frees via inject_frame_free */
void inject_frame_free(inject_frame_t *f);

/* ------------------------------------------------------------------------- *
 *  802.11 frame building (replaces the Hak5 libwifi fork) — frames.c
 * ------------------------------------------------------------------------- */
/* Build a radiotap+802.11 beacon for `ssid`. Returns malloc'd buffer, sets *len. */
uint8_t *build_beacon(const mac_t *src, const mac_t *bssid, const char *ssid,
                      uint8_t channel, uint16_t beacon_interval_tu, size_t *len);
uint8_t *build_deauth(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                      uint16_t reason, size_t *len);
uint8_t *build_disassoc(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                        uint16_t reason, size_t *len);

/* ------------------------------------------------------------------------- *
 *  802.11 frame parsing (replaces libwifi parse side) — parse.c
 * ------------------------------------------------------------------------- */
typedef struct {
    bool     has_radiotap;
    const uint8_t *dot11;   /* points into packet after radiotap */
    size_t   dot11_len;
    int      signal_dbm;
} sniff_view_t;

/* Returns true if it decoded a valid 802.11 frame into v. */
bool wifi_parse(const uint8_t *pkt, size_t len, sniff_view_t *v);
int  wifi_subtype(const sniff_view_t *v);
int  wifi_type(const sniff_view_t *v);
/* Extract AP info from a beacon/probe-resp (subtype 8/5). */
bool wifi_get_ap(const sniff_view_t *v, mac_t *bssid, char *ssid, size_t ssid_cap,
                 int *channel, int *hidden);
/* Extract client (probe-req subtype 4) info. */
bool wifi_get_probe(const sniff_view_t *v, mac_t *client, char *ssid, size_t ssid_cap);
/* WPA handshake detection: returns message 1..4 or 0 if not EAPOL-key. */
int  wifi_wpa_handshake_part(const sniff_view_t *v, mac_t *ap, mac_t *client);

/* ------------------------------------------------------------------------- *
 *  Interface / capture (iface.c) — nl80211/iw adaptation for Kali
 * ------------------------------------------------------------------------- */
int  iface_start(const char *ifname);     /* open pcap, start capture+inject threads */
void iface_stop(void);
int  iface_set_channel(int channel);
int  iface_inject(const uint8_t *buf, size_t len);

/* ------------------------------------------------------------------------- *
 *  Parser + result handler threads (parser.c)
 * ------------------------------------------------------------------------- */
int  parser_start(void);
void parser_stop(void);
void parser_submit(const uint8_t *pkt, size_t len, long ts_us, int signal_dbm);
/* free the in-memory handshake working set (pcaps on disk are kept); call when
 * handshake capture stops so memory is not held across capture sessions. */
void parser_handshake_reset(void);

/* ------------------------------------------------------------------------- *
 *  Beaconing (karma.c)
 * ------------------------------------------------------------------------- */
void beacon_ssid_pool(void);  /* enqueue one beacon per pooled SSID */

/* ------------------------------------------------------------------------- *
 *  Native STA scan for AP recon (scan_sta.c)
 *
 *  On single-radio hardware (WCN3990/G82) the monitor VIF is pinned to the
 *  STA's channel and cannot hop, so monitor-based recon only sees the
 *  connection's channel. The associated STA can still off-channel scan on its
 *  own; `iw dev <sta> scan` returns APs from every channel without dropping the
 *  link. These functions run that scan in the background and write the APs to
 *  the SAME `aps` table (recon_write_ap) the monitor path uses.
 * ------------------------------------------------------------------------- */
void scan_sta_start(int scan_id);   /* start background scan thread           */
void scan_sta_stop(void);           /* stop + join the background scan thread */
int  scan_sta_once(const char *staif, int scan_id); /* one pass; -1 on error, else #APs */
/* Parse a captured `iw scan` text buffer into aps rows (used by scan + tests). */
int  scan_sta_parse_buffer(const char *text, int scan_id, long now);
/* Resolve the managed STA interface (config -> connected managed -> wlan0). */
void scan_sta_pick_interface(char *out, size_t cap);

/* ------------------------------------------------------------------------- *
 *  Command server (comms.c)
 * ------------------------------------------------------------------------- */
int  comms_init(void);        /* create + bind + listen the UNIX socket */
void comms_shutdown(void);
void comms_handle_next(void); /* accept + recv 4097 + dispatch (one client) */

#endif /* PINEAPD_NG_H */
