/*
 * proto.c — pure request builders + value maps for pineap-ng.
 *
 * Every builder produces the EXACT 4097-byte, zero-filled, big-endian request
 * that the original `pineap` sends (validated against /tmp/opencode/proto-re/
 * recv_*.bin).  No sockets here so the builders are oracle-testable.
 */
#include "pineap.h"
#include <string.h>
#include <ctype.h>
#include <stdlib.h>

/* ----------------------------- MAC parsing ------------------------------ */
/* Accept "aa:bb:cc:dd:ee:ff" or "aabbccddeeff"; separators : or - ignored. */
bool mac_from_str(const char *s, mac_t *out)
{
    if (!s || !out) return false;
    int n = 0;
    uint8_t cur = 0;
    int nib = 0;
    for (const char *p = s; *p; ++p) {
        char c = *p;
        if (c == ':' || c == '-') continue;
        int v;
        if (c >= '0' && c <= '9') v = c - '0';
        else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
        else return false;
        cur = (uint8_t)((cur << 4) | v);
        if (++nib == 2) {
            if (n >= 6) return false;
            out->b[n++] = cur;
            cur = 0; nib = 0;
        }
    }
    return (n == 6 && nib == 0);
}

/* ----------------------------- big-endian writers ----------------------- */
static void put_be32(uint8_t *b, int off, uint32_t v)
{
    b[off]   = (uint8_t)(v >> 24);
    b[off+1] = (uint8_t)(v >> 16);
    b[off+2] = (uint8_t)(v >> 8);
    b[off+3] = (uint8_t)(v);
}

/* ----------------------------- request builders ------------------------- */
void req_zero(uint8_t *out)
{
    memset(out, 0, PINEAP_CMD_LEN);
}

void req_simple(uint8_t *out, uint8_t cmd)
{
    req_zero(out);
    out[0] = cmd;
}

/* run_scan: [0]=4, [1]=freq, [2..5]=dur u32BE  (oracle: 04 02 0000001e) */
void req_run_scan(uint8_t *out, uint8_t freq, uint32_t dur)
{
    req_zero(out);
    out[0] = CMD_RUN_SCAN;
    out[1] = freq;
    put_be32(out, 2, dur);
}

/* deauth: [0]=8, [1..6]=target, [7..12]=source, [13]=ch, [14..17]=mult u32BE
 * (oracle: 08 bb*6 aa*6 06 00000003 -> target then source on the wire) */
void req_deauth(uint8_t *out, const mac_t *source, const mac_t *target,
                uint8_t channel, uint32_t multiplier)
{
    req_zero(out);
    out[0] = CMD_DEAUTH;
    memcpy(out + 1, target->b, 6);
    memcpy(out + 7, source->b, 6);
    out[13] = channel;
    put_be32(out, 14, multiplier);
}

/* handshake_capture_start: [0]=9, [1..6]=bssid, [7]=ch (oracle: 09 .. 06) */
void req_handshake_start(uint8_t *out, const mac_t *bssid, uint8_t channel)
{
    req_zero(out);
    out[0] = CMD_HANDSHAKE_CAPTURE_START;
    memcpy(out + 1, bssid->b, 6);
    out[7] = channel;
}

/* add_ssid / del_ssid: [0]=cmd, [1..]=SSID ascii + NUL, rest zero
 * (oracle addssid: 0e 54657374 4e6574 00 -> "TestNet"+NUL, NOT fixed-padded) */
void req_ssid(uint8_t *out, uint8_t cmd, const char *ssid)
{
    req_zero(out);
    out[0] = cmd;
    size_t n = strlen(ssid);
    if (n > PINEAP_CMD_LEN - 2) n = PINEAP_CMD_LEN - 2;  /* leave trailing NUL */
    memcpy(out + 1, ssid, n);
    /* out[1+n] already 0 from req_zero */
}

/* list_probes: [0]=18, [1..6]=MAC (00..=all) (oracle: 12 aabbccddeeff) */
void req_list_probes(uint8_t *out, const mac_t *mac)
{
    req_zero(out);
    out[0] = CMD_LIST_PROBES;
    memcpy(out + 1, mac->b, 6);
}

/* inject: [0]=19, [1]=ch, [2..5]=count u32BE, [6..9]=len u32BE, [10..]=frame
 * (oracle: 13 06 00000003 0000000e <14 frame bytes>; delay NOT on the wire) */
void req_inject(uint8_t *out, uint8_t channel, uint32_t count,
                const uint8_t *frame, uint32_t frame_len)
{
    req_zero(out);
    out[0] = CMD_INJECT;
    out[1] = channel;
    put_be32(out, 2, count);
    put_be32(out, 6, frame_len);
    uint32_t n = frame_len;
    if (n > PINEAP_INJECT_MAX) n = PINEAP_INJECT_MAX;
    if ((size_t)(10 + n) > PINEAP_CMD_LEN) n = PINEAP_CMD_LEN - 10;
    if (frame && n) memcpy(out + 10, frame, n);
}

/* setOpt bool: [0]=21, [1]=opt, [2..5]=0, [6]=0/1 (oracle: 15 02 00000000 01) */
void req_opt_bool(uint8_t *out, uint8_t opt, bool v)
{
    req_zero(out);
    out[0] = CMD_SETOPT;
    out[1] = opt;
    out[6] = v ? 1 : 0;
}

/* setOpt u32: [0]=21, [1]=opt, [2..5]=0, [6..9]=u32BE
 * (oracle bi_low: 15 0c 00000000 00002328) */
void req_opt_u32(uint8_t *out, uint8_t opt, uint32_t v)
{
    req_zero(out);
    out[0] = CMD_SETOPT;
    out[1] = opt;
    put_be32(out, 6, v);
}

/* setOpt ap_channel QUIRK: channel as a SINGLE byte at offset 6, NOT a u32.
 * (oracle apchannel: 15 09 00000000 0b 00...; server reads it as 0x0b000000) */
void req_opt_ap_channel(uint8_t *out, uint8_t channel)
{
    req_zero(out);
    out[0] = CMD_SETOPT;
    out[1] = OPT_AP_CHANNEL;
    out[6] = channel;
}

/* setOpt MAC: [0]=21, [1]=opt, [2..5]=0, [6..11]=MAC
 * (oracle setsource: 15 0f 00000000 112233445566) */
void req_opt_mac(uint8_t *out, uint8_t opt, const mac_t *mac)
{
    req_zero(out);
    out[0] = CMD_SETOPT;
    out[1] = opt;
    memcpy(out + 6, mac->b, 6);
}

/* ----------------------------- value maps ------------------------------- */
static void lower(const char *s, char *d, size_t cap)
{
    size_t i = 0;
    for (; s[i] && i + 1 < cap; ++i) d[i] = (char)tolower((unsigned char)s[i]);
    d[i] = '\0';
}

/* strToInjectInterval (§B.3): low=9000, high/aggressive=3500, else 5712 */
uint32_t inject_interval_from_str(const char *s)
{
    char l[32];
    lower(s ? s : "", l, sizeof l);
    if (!strcmp(l, "low"))                               return INJECT_LOW;
    if (!strcmp(l, "high") || !strcmp(l, "aggressive"))  return INJECT_AGGRESSIVE;
    return INJECT_NORMAL;  /* "normal" or any unknown string */
}

/* isOptionOn: "on"/"1"/"true"/"yes"/"enable"(d) case-insensitive */
bool is_option_on(const char *s)
{
    char l[32];
    lower(s ? s : "", l, sizeof l);
    return !strcmp(l, "on") || !strcmp(l, "1") || !strcmp(l, "true") ||
           !strcmp(l, "yes") || !strcmp(l, "enable") || !strcmp(l, "enabled");
}

/* strToFilterMode: "deny"/"black" -> DENY (black); otherwise ALLOW (white) */
filter_mode_t filter_mode_from_str(const char *s)
{
    char l[32];
    lower(s ? s : "", l, sizeof l);
    if (!strcmp(l, "deny") || !strcmp(l, "black")) return FILTER_DENY;
    return FILTER_ALLOW;
}

/* hex_decode: "ffaa.." -> bytes.  Returns #bytes or -1 on invalid hex.
 * Ignores whitespace so a frame file may contain spaces/newlines. */
int hex_decode(const char *in, uint8_t *out, size_t out_cap)
{
    size_t n = 0;
    int nib = 0;
    uint8_t cur = 0;
    for (const char *p = in; *p; ++p) {
        char c = *p;
        if (isspace((unsigned char)c) || c == ':') continue;
        int v;
        if (c >= '0' && c <= '9') v = c - '0';
        else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
        else return -1;
        cur = (uint8_t)((cur << 4) | v);
        if (++nib == 2) {
            if (n >= out_cap) return (int)n;   /* truncate at cap */
            out[n++] = cur;
            cur = 0; nib = 0;
        }
    }
    if (nib != 0) return -1;   /* odd number of hex digits */
    return (int)n;
}
