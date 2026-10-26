/*
 * pineap-ng — native aarch64 reimplementation of the WiFi Pineapple CLI
 * (`pineap`), byte-for-byte compatible with the original MIPS binary's socket
 * protocol so it is a drop-in replacement talking to pineapd / pineapd-ng.
 *
 * Central header: protocol constants, configurable paths, shared helpers.
 *
 * Evidence basis (see ../../docs/):
 *   - PROTOCOLO-DEFINITIVO.md : socket protocol (4097 bytes, big-endian, table)
 *   - INFORME-pineap-RE.md    : argv parsing, command list, stdout, side effects
 *
 * Oracle: /tmp/opencode/proto-re/recv_*.bin are the EXACT bytes the original
 * pineap sends per command (captured under qemu-mips). validate_bytes.sh / the
 * built-in --dump-request mode compare against them byte-for-byte.
 */
#ifndef PINEAP_NG_H
#define PINEAP_NG_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* ------------------------------------------------------------------------- *
 *  Configurable paths / interface.
 *
 *  Defaults match the ORIGINAL so the PHP panel and pineapd talk to pineap-ng
 *  exactly as to the original.  Override at build time for a Kali relocation:
 *    make EXTRA_DEFS='-DPINEAP_SOCK_PATH=\"/opt/pineapple/run/pineapd.sock\" \
 *                     -DPINEAP_HOSTAPD_IFACE=\"wlan1\"'
 *  or at run time:  PINEAP_SOCK / PINEAP_IFACE environment variables.
 * ------------------------------------------------------------------------- */
#ifndef PINEAP_SOCK_PATH
#define PINEAP_SOCK_PATH   "/var/run/pineapd.sock"
#endif
/* The original hardcodes `hostapd_cli -i wlan0 ...`.  On the G82/Kali the AP
 * interface differs; override via -DPINEAP_HOSTAPD_IFACE or $PINEAP_IFACE. */
#ifndef PINEAP_HOSTAPD_IFACE
#define PINEAP_HOSTAPD_IFACE "wlan1"
#endif

/* The hostapd_cli to invoke for karma/filter side effects.  The original ran
 * bare `hostapd_cli` (PATH lookup).  On the port this MUST resolve to the
 * Hak5->mana translating wrapper (compat/hostapd_cli), NOT the stock
 * /usr/sbin/hostapd_cli that the hostapd-mana apt package installs -- otherwise
 * `karma_enable` reaches mana untranslated and it replies "Unknown command
 * 'karma_enable'".  Relying on PATH is fragile (interactive shells and the
 * php-fpm pool can put /usr/sbin before /usr/local/sbin).  So we call the
 * wrapper by ABSOLUTE path, overridable at build time (-DPINEAP_HOSTAPD_CLI) or
 * runtime ($PINEAP_HOSTAPD_CLI) for relocation/testing. */
#ifndef PINEAP_HOSTAPD_CLI
#define PINEAP_HOSTAPD_CLI "/usr/local/sbin/hostapd_cli"
#endif

/* ------------------------------------------------------------------------- *
 *  Protocol — EXACT match of the original (PROTOCOLO-DEFINITIVO.md)
 * ------------------------------------------------------------------------- */
#define PINEAP_CMD_LEN   4097   /* request buffer: fixed, zero-filled */
#define PINEAP_INJECT_MAX 2048  /* max frame bytes carried in inject  */

/* Command IDs (buf[0]).  Server dispatch keys on this value. */
enum pineap_command {
    CMD_RUN_SCAN                = 4,
    CMD_PAUSE_SCAN              = 5,
    CMD_UNPAUSE_SCAN            = 6,
    CMD_STOP_SCAN               = 7,
    CMD_DEAUTH                  = 8,
    CMD_HANDSHAKE_CAPTURE_START = 9,
    CMD_HANDSHAKE_CAPTURE_STOP  = 10,
    CMD_RELOAD_CONFIG           = 11,
    CMD_PAUSE                   = 12,  /* channel-hop pause  */
    CMD_UNPAUSE                 = 13,  /* channel-hop resume */
    CMD_ADD_SSID                = 14,
    CMD_DEL_SSID                = 15,
    CMD_CLEAR_SSIDS             = 16,
    CMD_LIST_SSIDS              = 17,  /* has response            */
    CMD_LIST_PROBES             = 18,  /* has response            */
    CMD_INJECT                  = 19,
    CMD_GET_STATUS              = 20,  /* has response (JSON)     */
    CMD_SETOPT                  = 21
};

/* setOpt sub-options (buf[1]) — §5 of PROTOCOLO-DEFINITIVO.md */
enum pineap_opt {
    OPT_KARMA                    = 0,  /* bool  [6]      */
    OPT_BEACON_RESPONSES         = 1,  /* bool  [6]      */
    OPT_CAPTURE_SSIDS            = 2,  /* bool  [6]      */
    OPT_BROADCAST_POOL           = 3,  /* bool  [6]      */
    OPT_LOGGING                  = 4,  /* bool  [6]      */
    OPT_AP_CHANNEL               = 9,  /* QUIRK: single byte @6 */
    OPT_CONNECT_NOTIFICATIONS    = 10, /* bool  [6]      */
    OPT_DISCONNECT_NOTIFICATIONS = 11, /* bool  [6]      */
    OPT_BEACON_INTERVAL          = 12, /* u32BE [6..9]   */
    OPT_BEACON_RESPONSE_INTERVAL = 13, /* u32BE [6..9]   */
    OPT_SET_SOURCE               = 15, /* MAC   [6..11]  */
    OPT_SET_TARGET               = 16  /* MAC   [6..11]  */
};

/* InjectInterval (beacon cadence), big-endian u32 on the wire (§B.3) */
enum inject_interval {
    INJECT_LOW        = 9000,  /* 0x2328 */
    INJECT_NORMAL     = 5712,  /* 0x1650  <- default for unknown strings */
    INJECT_AGGRESSIVE = 3500   /* 0x0dac */
};

/* FilterMode (ssid_filter / mac_filter), drives hostapd_cli only */
typedef enum { FILTER_DENY = 0, FILTER_ALLOW = 1 } filter_mode_t;

/* ------------------------------------------------------------------------- *
 *  MAC helpers (proto.c)
 * ------------------------------------------------------------------------- */
typedef struct { uint8_t b[6]; } mac_t;
bool mac_from_str(const char *s, mac_t *out);   /* "aa:bb:cc:dd:ee:ff" */

/* ------------------------------------------------------------------------- *
 *  Request builders (proto.c) — fill a 4097-byte zero-filled buffer.
 *  Each returns the buffer via `out` (caller provides PINEAP_CMD_LEN bytes).
 *  These are pure: no sockets, so they can be unit/oracle-tested.
 * ------------------------------------------------------------------------- */
void req_zero(uint8_t *out);
void req_simple(uint8_t *out, uint8_t cmd);
void req_run_scan(uint8_t *out, uint8_t freq, uint32_t dur);
void req_deauth(uint8_t *out, const mac_t *source, const mac_t *target,
                uint8_t channel, uint32_t multiplier);
void req_handshake_start(uint8_t *out, const mac_t *bssid, uint8_t channel);
void req_ssid(uint8_t *out, uint8_t cmd, const char *ssid);  /* add/del */
void req_list_probes(uint8_t *out, const mac_t *mac);
void req_inject(uint8_t *out, uint8_t channel, uint32_t count,
                const uint8_t *frame, uint32_t frame_len);
/* setOpt variants */
void req_opt_bool(uint8_t *out, uint8_t opt, bool v);
void req_opt_u32(uint8_t *out, uint8_t opt, uint32_t v);      /* BE @6..9 */
void req_opt_ap_channel(uint8_t *out, uint8_t channel);       /* QUIRK byte@6 */
void req_opt_mac(uint8_t *out, uint8_t opt, const mac_t *mac);

/* value maps */
uint32_t inject_interval_from_str(const char *s);
bool     is_option_on(const char *s);      /* "on"/"1"/"true" (ci) */
filter_mode_t filter_mode_from_str(const char *s);

/* hex decoding for inject frames (returns #bytes, -1 on bad hex) */
int hex_decode(const char *in, uint8_t *out, size_t out_cap);

/* ------------------------------------------------------------------------- *
 *  Socket client (comms.c)
 * ------------------------------------------------------------------------- */
const char *pineap_sock_path(void);     /* $PINEAP_SOCK or PINEAP_SOCK_PATH */
const char *pineap_iface(void);         /* $PINEAP_IFACE or PINEAP_HOSTAPD_IFACE */
const char *pineap_hostapd_cli(void);   /* $PINEAP_HOSTAPD_CLI or PINEAP_HOSTAPD_CLI */
/* connect, send the 4097 request, if read_response!=0 stream server reply
 * (until NUL) to stdout.  Returns 0 on success, 1 on socket error (and prints
 * the original's error text to stderr). */
int pineap_send(const uint8_t *req, bool read_response);

#endif /* PINEAP_NG_H */
