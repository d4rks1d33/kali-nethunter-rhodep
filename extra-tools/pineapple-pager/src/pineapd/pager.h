/*
 * pager.h — native aarch64 reimplementation of the WiFi Pineapple **Pager**
 *           PineAP daemon (pineapd), command-protocol compatible with the
 *           original MIPS C++ binary (firmware 1.1.3).
 *
 * This is the central header: configurable paths, the socket wire protocol,
 * the command-code table, the config/scan-state singletons, and the module
 * prototypes (msg, sock, handlers, config, db, frames, scan_sta, iface).
 *
 * Evidence basis (see ../../docs/):
 *   - PROTOCOLO-SOCKET.md   : framing [u32 cmd BE][u32 len BE][msgpack body],
 *                             DEFINITIVE table of 60 code->handler mappings.
 *   - ARQUITECTURA-PORT.md  : msgpack key names (section 2), radio mapping,
 *                             config keys, graceful-degradation rules.
 *
 * DESIGN NOTES (differences vs the NANO pineapd-ng port this is based on):
 *   - The Pager's socket body is **MessagePack** (lib mpack), NOT a fixed 4097
 *     byte binary struct. We hand-write only the 8-byte BE frame header and let
 *     mpack own the body. See msg.c.
 *   - Dispatch is a table of {uint32 code -> handler}, not switch(buf[0]-1).
 *   - Radio rule (unchanged from NANO): the internal wlan0 (WCN3990) is the
 *     STA/AP radio and is NEVER put in monitor mode. Monitor / deauth / recon
 *     capture go over the Archer USB (wlan1 -> wlan1mon). With no Archer
 *     plugged in, radio handlers degrade gracefully (basic error, no crash).
 */
#ifndef PINEAPD_PAGER_H
#define PINEAPD_PAGER_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <pthread.h>
#include <sys/types.h>

/* ------------------------------------------------------------------------- *
 *  Configurable paths — the Pager lives under /opt/pineapple-pager (separate
 *  from the NANO at /opt/pineapple so both can coexist). Override at build time
 *  with -DPINEAPD_SOCK_PATH=... etc.
 * ------------------------------------------------------------------------- */
#ifndef PINEAPD_SOCK_PATH
#define PINEAPD_SOCK_PATH   "/opt/pineapple-pager/run/pineap_sock"
#endif
#ifndef PINEAPD_LOCK_PATH
#define PINEAPD_LOCK_PATH   "/opt/pineapple-pager/run/pineapd.lock"
#endif
#ifndef PINEAPD_CONF_PATH
#define PINEAPD_CONF_PATH   "/opt/pineapple-pager/etc/pineapd.conf"
#endif
#ifndef PINEAPD_RECON_DB
#define PINEAPD_RECON_DB    "/opt/pineapple-pager/run/recon.db"
#endif
#ifndef PINEAPD_LOG_DB
#define PINEAPD_LOG_DB      "/opt/pineapple-pager/run/log.db"
#endif
#ifndef PINEAPD_SSID_DB
#define PINEAPD_SSID_DB     "/opt/pineapple-pager/run/pineapple.db"
#endif
#ifndef PINEAPD_HANDSHAKE_DIR
#define PINEAPD_HANDSHAKE_DIR "/opt/pineapple-pager/run/handshakes"
#endif
#ifndef PINEAPD_PCAP_DIR
#define PINEAPD_PCAP_DIR    "/opt/pineapple-pager/run/pcaps"
#endif

/* Internal STA/AP radio (WCN3990) — NEVER monitor. */
#ifndef PINEAPD_INTERNAL_IFACE
#define PINEAPD_INTERNAL_IFACE "wlan0"
#endif
/* Primary monitor/inject radio = Archer USB. Created from wlan1 by airmon-ng or
 * `iw`. If absent, radio handlers degrade gracefully. */
#ifndef PINEAPD_MONITOR_IFACE
#define PINEAPD_MONITOR_IFACE "wlan1mon"
#endif
/* Base device the monitor iface rides on (for presence/type checks). */
#ifndef PINEAPD_MONITOR_BASE
#define PINEAPD_MONITOR_BASE "wlan1"
#endif

/* ------------------------------------------------------------------------- *
 *  Wire protocol — EXACT match of the Pager (PROTOCOLO-SOCKET.md)
 *
 *      [ u32 cmd  BE ][ u32 len BE ][ body: MessagePack (len bytes) ]
 *
 *  Both request and response use this framing. The response body is always a
 *  MessagePack MAP. The "basic" response is {error:uint, message:str}.
 * ------------------------------------------------------------------------- */
#define PINEAP_HDR_LEN      8          /* 2x u32 BE                           */
#define PINEAP_MAX_BODY     (4u << 20) /* 4 MiB body sanity cap               */

/* recon "stable list" handshake: how many EXTRA clients frames to push after
 * the AP devicelist frame for an rstable send_devices (the firmware reads
 * several frames off one cmd=216; see sock.c). Tunable during bring-up. */
#ifndef PINEAP_RSTABLE_EXTRA_FRAMES
#define PINEAP_RSTABLE_EXTRA_FRAMES 1
#endif

/* Command codes — DEFINITIVE table from PROTOCOLO-SOCKET.md. */
enum pineap_cmd {
    /* interface_tracker */
    CMD_LIST_INTERFACES        = 100,
    CMD_MOD_INTERFACE          = 101,
    CMD_TOGGLE_INTERFACE_A     = 102,
    CMD_TOGGLE_INTERFACE_B     = 103,
    CMD_MOD_INTERFACE_2        = 104,
    CMD_SET_INTERFACE_TYPE     = 105,
    CMD_SET_INTERFACE_TYPE_2   = 106,
    CMD_IFACE_RESET_CHANNELS   = 107,
    CMD_SEND_DEAUTH            = 400,
    CMD_SEND_DEAUTH_ALL        = 401,
    CMD_SEND_DEAUTH_CSA        = 402,
    CMD_EXAMINE_BSSID          = 500,
    CMD_EXAMINE_CHANNEL        = 501,
    CMD_EXAMINE_RESET          = 502,
    CMD_PCAP_START             = 610,
    CMD_PCAP_STOP              = 611,
    CMD_PCAP_CONTROL           = 612,
    /* recon_engine */
    CMD_COUNT_DEVICES          = 200,
    CMD_SEND_DEVICES_201       = 201,
    CMD_COUNT_DEVICES_2        = 202,
    CMD_SEND_DEVICES_203       = 203,
    CMD_NEW_RECON              = 204,
    CMD_SEND_DEVICES_205       = 205,
    CMD_MONITOR_DEVICE         = 206,
    CMD_RECON_FREQSTATS        = 207,
    CMD_RECON_STATS            = 208,
    CMD_RECON_DEVCOUNTS        = 209,
    CMD_RECON_CRYPTSTATS       = 210,
    CMD_RECON_PPS              = 211,
    CMD_RECONDB_CONTROL        = 212,
    CMD_WAITFOR                = 213,
    CMD_SET_HANDSHAKE          = 214,
    CMD_SEND_DEVICES_215       = 215,
    CMD_SEND_DEVICES_216       = 216,
    CMD_SEND_DEVICES_217       = 217,
    CMD_SET_SSIDCOLLECT        = 255,
    CMD_WIGLE_CONTROL_A        = 600,
    CMD_WIGLE_CONTROL_B        = 601,
    CMD_WIGLE_CONTROL_C        = 602,
    CMD_HOSTAP_HANDSHAKE       = 901,
    CMD_HOSTAP_CHALLENGE       = 902,
    CMD_HOSTAP_BASIC           = 903,
    CMD_HOSTAP_CLIENT_CONNECT  = 904,
    CMD_HOSTAP_CLIENT_DISCONNECT = 905,
    /* ssid_pool */
    CMD_FETCH_SSID_POOL        = 300,
    CMD_CLEAR_SSID_POOL        = 301,
    CMD_MOD_SSID_POOL_A        = 302,
    CMD_MOD_SSID_POOL_B        = 303,
    CMD_CONFIG_SSID_POOL_304   = 304,
    CMD_CONFIG_SSID_POOL_305   = 305,
    /* 306 = intentional gap */
    CMD_CONFIG_SSID_POOL_307   = 307,
    CMD_CONFIG_SSID_POOL_308   = 308,
    CMD_CONFIG_SSID_POOL_309   = 309,
    CMD_CONFIG_SSID_POOL_310   = 310,
    CMD_LOADUCI_CONFIG         = 311,
    CMD_CONFBCAST_SSID_POOL    = 312,
    CMD_LOADUCI_SSID_POOL      = 313,
    CMD_FETCH_NEW_SSID_POOL    = 314,
    /* gpsd */
    CMD_GPSD_REPORT            = 10
};

/* 802.11 deauth reason code we emit (class-3 frame from nonassociated STA). */
#define PINEAP_DEAUTH_REASON   7

/* ------------------------------------------------------------------------- *
 *  MACAddress
 * ------------------------------------------------------------------------- */
typedef struct { uint8_t b[6]; } mac_t;
void   mac_to_str(const mac_t *m, char out[18]);
bool   mac_from_str(const char *s, mac_t *out);
bool   mac_is_zero(const mac_t *m);
bool   mac_is_broadcast(const mac_t *m);

/* ------------------------------------------------------------------------- *
 *  Config singleton (replaces libuci). Keys mirror the Pager init/UCI config.
 * ------------------------------------------------------------------------- */
typedef struct {
    /* radios */
    char      internal_iface[64];   /* wlan0: STA/AP, never monitor         */
    char      monitor_iface[64];    /* wlan1mon: primary monitor/inject     */
    char      sta_scan_interface[64]; /* managed STA for native recon scan  */

    /* recon / paths (from init flags: --recon, --reconpath, --reconname, ...) */
    bool      recon_enabled;
    char      recon_path[256];      /* recon.db path (--reconpath)          */
    char      recon_name[128];      /* scan name label (--reconname)        */
    char      handshake_path[256];  /* --handshakepath                      */
    char      payloads_path[256];   /* --payloads                           */
    char      wigle_path[256];      /* --wiglepath                          */
    char      pcap_path[256];       /* --pcappath                           */
    char      recon_db_path[256];   /* sqlite recon.db (== recon_path)      */
    char      hostapd_db_path[256]; /* log.db                               */
    char      ssid_db_path[256];    /* pineapple.db (ssid pool)             */

    /* PineAP / behaviour */
    bool      inject;               /* --inject: injection enabled          */
    bool      hop;                  /* --hop: channel hopping               */
    int       channel;             /* --channel: fixed channel (0=hop)      */
    char      bands[32];           /* --band if:bands string (e.g. "2.4,5") */
    bool      ssid_collect;         /* harvest probed SSIDs                  */
    bool      broadcast_ssid_pool;  /* beacon the SSID pool                  */
    bool      karma;

    /* gpsd */
    char      gpsd_host[64];
    int       gpsd_port;

    char      config_path[256];
    pthread_mutex_t lock;
} pager_config_t;

pager_config_t *config_get(void);
void config_set_path(const char *path);
int  config_reload(void);        /* parse config_path into the singleton    */
int  config_save(void);          /* re-serialize to config_path             */

/* ------------------------------------------------------------------------- *
 *  Scan / recon state singleton.
 * ------------------------------------------------------------------------- */
typedef struct {
    bool     running;            /* daemon main loop should keep running     */
    bool     recon_running;      /* a recon scan is active                   */
    bool     handshake_running;  /* handshake capture mode                   */
    bool     pcap_running;       /* raw pcap capture running                 */
    bool     ssid_collect;       /* live SSID harvesting                     */
    int      recon_scan_id;      /* current recon.db scan_id                 */
    int      scan_percent;
    int      channel;
    int      freq_band;          /* 0=2.4, 1=5, 2=both                       */
    time_t   start_time;
    int      duration_sec;
    pthread_mutex_t lock;
} scan_state_t;

scan_state_t *scan_state_get(void);

/* ------------------------------------------------------------------------- *
 *  Logger
 * ------------------------------------------------------------------------- */
void logger_set_enabled(bool e);
void pineap_log(const char *tag, const char *fmt, ...);

/* ------------------------------------------------------------------------- *
 *  Databases (sqlite3) — recon.db (aps,clients,scan_ids), log.db (log,
 *  tracking), pineapple.db (ssids). Schema matches the original.
 * ------------------------------------------------------------------------- */
int  db_init_all(void);
void db_close_all(void);

int  recon_next_scan_id(void);
void recon_write_ap(int scan_id, const char *ssid, const char *bssid,
                    int encryption, int hidden, int channel, int signal_,
                    int wps, long last_seen);
void recon_write_client(int scan_id, const char *mac, const char *bssid,
                        long last_seen);
int  recon_count_aps(int scan_id);
int  recon_count_clients(int scan_id);

/* Device-row callback used by send_devices handlers. Return false to stop. */
typedef bool (*recon_dev_cb)(void *ud, const char *bssid, const char *ssid,
                             int channel, int signal_, int encryption,
                             int hidden, int wps, long last_seen);
int  recon_each_ap(int scan_id, recon_dev_cb cb, void *ud); /* returns #rows */

/* Per-AP client-count snapshot (BUG B: recon list "clients" count icon). The
 * count MUST be precomputed before recon_each_ap() to avoid re-locking the DB
 * mutex inside dev_cb (self-deadlock).  See db.c for the full rationale. */
typedef struct { char bssid[20]; int count; } recon_cli_count_t;
int  recon_client_counts_by_bssid(int scan_id, recon_cli_count_t **out);
void recon_client_counts_free(recon_cli_count_t *arr);

/* Client-row callback used by the recon "stable clients" response. Return
 * false to stop. `ap_bssid` (nullable) filters clients to a single AP. */
typedef bool (*recon_client_cb)(void *ud, const char *mac, const char *bssid,
                                long last_seen);
int  recon_each_client(int scan_id, const char *ap_bssid,
                       recon_client_cb cb, void *ud);

void log_write_entry(int log_type, const char *mac, const char *ssid,
                     long created_at, long updated_at);

/* ssid pool (pineapple.db) */
void ssid_add(const char *ssid);
void ssid_del(const char *ssid);
void ssid_clear(void);
bool ssid_has(const char *ssid);
int  ssid_get_all(char ***out);       /* array; caller frees via ssid_free_all */
void ssid_free_all(char **arr, int n);

bool tracking_is_tracked(const char *mac);

#define LOGTYPE_PROBE       0
#define LOGTYPE_ASSOCIATION 1
#define LOGTYPE_DEAUTH      2

/* ------------------------------------------------------------------------- *
 *  802.11 frame building (frames.c) — beacon / deauth / disassoc + radiotap.
 * ------------------------------------------------------------------------- */
uint8_t *build_beacon(const mac_t *src, const mac_t *bssid, const char *ssid,
                      uint8_t channel, uint16_t beacon_interval_tu, size_t *len);
uint8_t *build_deauth(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                      uint16_t reason, size_t *len);
uint8_t *build_disassoc(const mac_t *dst, const mac_t *src, const mac_t *bssid,
                        uint16_t reason, size_t *len);

/* ------------------------------------------------------------------------- *
 *  Interface / radio control (iface.c) — nl80211 + iw, uplink-radio safety.
 * ------------------------------------------------------------------------- */
/* True if ifname (or its base, stripping a trailing "mon") is the phone's
 * internet-carrying STA. We must NEVER flip that radio into monitor mode. */
int  iface_is_uplink_radio(const char *ifname);
/* True if ifname currently exists (present in `iw dev`). */
bool iface_exists(const char *ifname);
/* Set channel on an interface via nl80211 (fallback `iw`). 0 on success. */
int  iface_set_channel_on(const char *ifname, int channel);
/* Open a monitor pcap handle on the monitor iface; inject frames; close.
 * These degrade gracefully if the monitor iface is absent. */
int  iface_tx_open(void);          /* 0 on success, -1 if no monitor iface  */
void iface_tx_close(void);
int  iface_tx_inject(const uint8_t *buf, size_t len);  /* bytes sent or -1  */
/* Fill `out` (cap bytes) with a short status for the monitor iface. */
void iface_monitor_status(char *out, size_t cap);

/* ------------------------------------------------------------------------- *
 *  Native STA scan for AP recon (scan_sta.c) — off-channel scan via the
 *  associated managed STA (wlan0), writes rows into recon.db `aps`.
 * ------------------------------------------------------------------------- */
void scan_sta_start(int scan_id);
void scan_sta_stop(void);
int  scan_sta_once(const char *staif, int scan_id);
int  scan_sta_parse_buffer(const char *text, int scan_id, long now);
void scan_sta_pick_interface(char *out, size_t cap);

/* ------------------------------------------------------------------------- *
 *  REAL over-the-air recon on the Archer monitor (recon_mon.c) — creates
 *  wlan1mon, sniffs beacons/data/probes for APs+clients, counts PPS with a
 *  45s history, channel-hops 2.4+5GHz, and tears wlan1mon down cleanly.
 *  NEVER touches wlan0.
 * ------------------------------------------------------------------------- */
int      recon_mon_start(int scan_id);
void     recon_mon_stop(void);
bool     recon_mon_running(void);
unsigned recon_mon_pps(void);                 /* last full-second PPS         */
unsigned recon_mon_pps_avg(void);             /* running average PPS          */
unsigned long long recon_mon_pkt_total(void); /* total frames captured        */
int      recon_mon_pps_history(unsigned *out, int cap); /* oldest..newest     */
void     recon_mon_set_channel_lock(int channel);       /* 0 => hop           */
void     recon_note_poll(void);               /* firmware polled recon_stats  */
long     recon_seconds_since_poll(void);      /* -1 if never polled           */

/* ------------------------------------------------------------------------- *
 *  MessagePack frame helpers (msg.c)
 * ------------------------------------------------------------------------- */
#include "msg.h"

/* ------------------------------------------------------------------------- *
 *  Socket server + dispatch (sock.c)
 * ------------------------------------------------------------------------- */
int  sock_init(void);          /* create + bind + listen the UNIX socket    */
void sock_shutdown(void);
void sock_handle_next(void);   /* accept + read frame + dispatch one client */

/* Handler signature: given the request (cmd + parsed body root node), build a
 * response body into `rw`. Handlers live in handlers.c. mpack_node_t /
 * mpack_writer_t come from the mpack header pulled in via msg.h above. */
typedef void (*cmd_handler_fn)(uint32_t cmd, mpack_node_t root,
                               mpack_writer_t *rw);

/* Look up a handler for a command code (NULL if unknown). */
cmd_handler_fn sock_lookup_handler(uint32_t cmd);

/* recon "stable list" two-frame handshake (see handlers.c / sock.c).
 * The firmware's recon AP-list screen sends cmd=216 with rstable:true and then
 * reads TWO frames: the AP devicelist, then the clients list. */
bool send_devices_wants_rstable(mpack_node_t root);
void send_devices_build_clients(mpack_node_t root, mpack_writer_t *w);

#endif /* PINEAPD_PAGER_H */
