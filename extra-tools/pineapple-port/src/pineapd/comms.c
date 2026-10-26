/*
 * comms.c — UNIX socket command server. BYTE-FOR-BYTE compatible with the
 *           original pineapd protocol (PROTOCOLO-DEFINITIVO.md).
 *
 *   - AF_UNIX / SOCK_STREAM at /var/run/pineapd.sock, bind len = 110
 *   - request = 4097 bytes FIXED, zero-filled, big-endian integers, buf[0]=cmd
 *   - server reads 4097 in a loop, dispatches switch(buf[0]-1)
 *   - responses terminated by a NUL byte; only get_status/list_ssids/list_probes
 *     send a payload.
 */
#include "pineapd.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>

static int g_listen_fd = -1;

/* big-endian readers at absolute offsets in the 4097 buffer */
static uint32_t be32(const uint8_t *b, int off)
{
    return ((uint32_t)b[off] << 24) | ((uint32_t)b[off+1] << 16) |
           ((uint32_t)b[off+2] << 8) | (uint32_t)b[off+3];
}

int comms_init(void)
{
    unlink(PINEAPD_SOCK_PATH);
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (g_listen_fd < 0) {
        pineap_log("MAIN", "Failed top open socket: %s", strerror(errno));
        return -1;
    }
    int one = 1;
    setsockopt(g_listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, PINEAPD_SOCK_PATH, sizeof addr.sun_path - 1);

    /* bind with len 110 (= sizeof(struct sockaddr_un)) to match the original */
    if (bind(g_listen_fd, (struct sockaddr *)&addr, sizeof(struct sockaddr_un)) < 0) {
        pineap_log("MAIN", "Failed to bind sock fd %d to path %s: %s",
                   g_listen_fd, PINEAPD_SOCK_PATH, strerror(errno));
        close(g_listen_fd); g_listen_fd = -1;
        return -1;
    }
    if (listen(g_listen_fd, 10) < 0) {
        pineap_log("MAIN", "Failed to listen on sock fd %d at path %s: %s",
                   g_listen_fd, PINEAPD_SOCK_PATH, strerror(errno));
        close(g_listen_fd); g_listen_fd = -1;
        return -1;
    }
    pineap_log("MAIN", "listening on %s", PINEAPD_SOCK_PATH);
    return 0;
}

void comms_shutdown(void)
{
    if (g_listen_fd >= 0) { close(g_listen_fd); g_listen_fd = -1; }
    unlink(PINEAPD_SOCK_PATH);
}

/* read exactly 4097 bytes with the original's retry semantics */
static int recv_command(int fd, uint8_t *buf)
{
    memset(buf, 0, PINEAP_CMD_LEN);
    int got = 0, tries = 0;
    while (got < PINEAP_CMD_LEN) {
        ssize_t n = recv(fd, buf + got, PINEAP_CMD_LEN - got, 0);
        if (n > 0) { got += (int)n; continue; }
        if (n == 0) break;                      /* peer closed */
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            usleep(PINEAP_RECV_USLEEP);
            if (++tries < PINEAP_RECV_RETRIES) continue;
            break;
        }
        break;
    }
    return got;
}

/* send a response payload and terminate with a NUL (matches original) */
static void send_response(int fd, const char *data, size_t len)
{
    if (data && len) {
        size_t off = 0;
        while (off < len) {
            ssize_t w = send(fd, data + off, len - off, MSG_NOSIGNAL);
            if (w <= 0) break;
            off += (size_t)w;
        }
    }
    char nul = '\0';
    send(fd, &nul, 1, MSG_NOSIGNAL);
}

/* -------------------------- setOpt (cmd 21) ----------------------------- */
static void handle_setopt(const uint8_t *buf)
{
    pineap_config_t *c = config_get();
    uint8_t opt = buf[1];
    bool bval = buf[6] != 0;
    uint32_t ival = be32(buf, 6);
    char iface[160];

    pthread_mutex_lock(&c->lock);
    switch (opt) {
        case OPT_KARMA:                    c->karma = bval; break;
        case OPT_BEACON_RESPONSES:         c->beacon_responses = bval; break;
        case OPT_CAPTURE_SSIDS:            c->capture_ssids = bval; break;
        case OPT_BROADCAST_POOL:           c->broadcast_ssid_pool = bval; break;
        case OPT_LOGGING:                  c->logging = bval; break;
        case OPT_AP_CHANNEL:
            /*
             * QUIRK (PROTOCOLO §2h): the CLI sends the channel as a SINGLE byte
             * at offset 6; the original server reads lhu[6]<<16 | lhu[8], so a
             * channel of 11 (0x0b) becomes 0x0b000000 = 184549376 in the conf.
             * We reproduce this EXACTLY for byte-for-byte compat with the panel.
             */
            c->ap_channel = (int)(((uint32_t)buf[6] << 24) |
                                  ((uint32_t)buf[7] << 16) |
                                  ((uint32_t)buf[8] << 8)  |
                                  (uint32_t)buf[9]);
            break;
        case OPT_CONNECT_NOTIFICATIONS:    c->connect_notifications = bval; break;
        case OPT_DISCONNECT_NOTIFICATIONS: c->disconnect_notifications = bval; break;
        case OPT_BEACON_INTERVAL:          c->beacon_interval = ival; break;
        case OPT_BEACON_RESPONSE_INTERVAL: c->beacon_response_interval = ival; break;
        case OPT_SET_SOURCE:               memcpy(c->pineap_mac.b, buf + 6, 6); break;
        case OPT_SET_TARGET:               memcpy(c->target_mac.b, buf + 6, 6); break;
        default: break;
    }
    pthread_mutex_unlock(&c->lock);

    /* karma on/off also drives hostapd (INFORME §F.1) — best-effort, like CLI */
    if (opt == OPT_KARMA) {
        snprintf(iface, sizeof iface, "hostapd_cli -i %s karma_%s >/dev/null 2>&1",
                 c->pineap_interface, bval ? "enable" : "disable");
        if (system(iface) == -1) { /* hostapd_cli may be absent */ }
    }
    config_save();
}

/* ---------------------------- main dispatch ----------------------------- */
static void dispatch(int cli, const uint8_t *buf)
{
    scan_state_t *s = scan_state_get();
    uint8_t cmd = buf[0];
    long now = time(NULL);

    switch (cmd) {
    case CMD_RUN_SCAN: {
        /* [1]=freq, [2..5]=dur u32BE */
        int freq = buf[1];
        uint32_t dur = be32(buf, 2);
        pthread_mutex_lock(&s->lock);
        s->freq_band = freq;
        s->duration_sec = (int)dur;
        s->recon_running = true;
        s->hopping = true;
        s->paused = false;
        s->start_time = now;
        s->scan_percent = 0;
        pthread_mutex_unlock(&s->lock);
        s->recon_scan_id = recon_next_scan_id();
        /*
         * Native STA scan for recon. On single-radio hardware (WCN3990/G82) the
         * monitor VIF stays pinned to the STA's channel and cannot hop, so the
         * monitor path only ever sees APs on the connection's channel. The
         * associated STA can off-channel scan on its own without dropping the
         * link, so this is what actually populates the `aps` table across all
         * channels. The monitor (wlan0mon) is left running for handshake /
         * deauth / inject — we do NOT stop or retune it here.
         */
        scan_sta_start(s->recon_scan_id);
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_PAUSE_SCAN:
        pthread_mutex_lock(&s->lock); s->paused = true; pthread_mutex_unlock(&s->lock);
        scan_sta_stop();
        send_response(cli, NULL, 0); break;
    case CMD_UNPAUSE_SCAN:
        pthread_mutex_lock(&s->lock); s->paused = false; pthread_mutex_unlock(&s->lock);
        /* resume native STA scanning with the current scan id */
        scan_sta_start(s->recon_scan_id);
        send_response(cli, NULL, 0); break;
    case CMD_STOP_SCAN:
        pthread_mutex_lock(&s->lock);
        s->recon_running = false; s->hopping = false; s->scan_percent = 0;
        pthread_mutex_unlock(&s->lock);
        scan_sta_stop();
        send_response(cli, NULL, 0); break;

    case CMD_DEAUTH: {
        /* [1..6]=target, [7..12]=source, [13]=ch, [14..17]=mult u32BE */
        mac_t tgt, src;
        memcpy(tgt.b, buf + 1, 6);
        memcpy(src.b, buf + 7, 6);
        uint8_t ch = buf[13];
        uint32_t mult = be32(buf, 14);
        if (mult == 0) mult = 1;
        size_t dl = 0, ul = 0;
        /* bssid = target (AP) for deauth of its clients */
        uint8_t *dframe = build_deauth(&src, &tgt, &tgt, 7, &dl);
        uint8_t *uframe = build_disassoc(&src, &tgt, &tgt, 7, &ul);
        if (dframe && dl) inject_queue_push(ch, 51280, mult, dframe, dl);
        if (uframe && ul) inject_queue_push(ch, 51280, mult, uframe, ul);
        free(dframe); free(uframe);
        if (config_get()->logging) {
            char ms[18]; mac_to_str(&tgt, ms);
            log_write_entry(LOGTYPE_DEAUTH, ms, "", now, now);
        }
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_HANDSHAKE_CAPTURE_START: {
        /* [1..6]=bssid, [7]=ch */
        mac_t bssid; memcpy(bssid.b, buf + 1, 6);
        uint8_t ch = buf[7];
        pthread_mutex_lock(&s->lock);
        s->bssid = bssid;
        s->handshake_running = true;
        s->hopping = false;
        s->channel = ch;
        pthread_mutex_unlock(&s->lock);
        iface_set_channel(ch);
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_HANDSHAKE_CAPTURE_STOP:
        pthread_mutex_lock(&s->lock);
        s->handshake_running = false;
        pthread_mutex_unlock(&s->lock);
        /* release the in-memory EAPOL working set (pcaps are already on disk) so
         * it does not accumulate across capture sessions. */
        parser_handshake_reset();
        send_response(cli, NULL, 0); break;

    case CMD_RELOAD_CONFIG:
        config_reload();
        send_response(cli, NULL, 0); break;

    case CMD_PAUSE:   /* channel hop pause */
        pthread_mutex_lock(&s->lock); s->hopping = false; pthread_mutex_unlock(&s->lock);
        send_response(cli, NULL, 0); break;
    case CMD_UNPAUSE: /* channel hop resume */
        pthread_mutex_lock(&s->lock); s->hopping = true; pthread_mutex_unlock(&s->lock);
        send_response(cli, NULL, 0); break;

    case CMD_ADD_SSID: {
        char ssid[256];
        size_t n = 0;
        while (n < sizeof ssid - 1 && n < PINEAP_CMD_LEN - 1 && buf[1 + n]) {
            ssid[n] = (char)buf[1 + n]; n++;
        }
        ssid[n] = '\0';
        if (n) ssid_add(ssid);
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_DEL_SSID: {
        char ssid[256];
        size_t n = 0;
        while (n < sizeof ssid - 1 && n < PINEAP_CMD_LEN - 1 && buf[1 + n]) {
            ssid[n] = (char)buf[1 + n]; n++;
        }
        ssid[n] = '\0';
        if (n) ssid_del(ssid);
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_CLEAR_SSIDS:
        ssid_clear();
        send_response(cli, NULL, 0); break;

    case CMD_LIST_SSIDS: {
        char *list = ssid_list();   /* '\n'-separated, maybe empty */
        send_response(cli, list, list ? strlen(list) : 0);
        free(list);
        break;
    }
    case CMD_LIST_PROBES: {
        mac_t mac; memcpy(mac.b, buf + 1, 6);
        char *list;
        if (mac_is_zero(&mac)) list = log_get_probes_all();
        else { char ms[18]; mac_to_str(&mac, ms); list = log_get_probes_mac(ms); }
        send_response(cli, list, list ? strlen(list) : 0);
        free(list);
        break;
    }
    case CMD_INJECT: {
        /* [1]=ch, [2..5]=count u32BE, [6..9]=len u32BE, [10..]=frame */
        uint8_t ch = buf[1];
        uint32_t count = be32(buf, 2);
        uint32_t flen = be32(buf, 6);
        if (count == 0) count = 1;
        if (flen > 0 && flen <= 2048 && (size_t)(10 + flen) <= PINEAP_CMD_LEN) {
            inject_queue_push(ch, 0, count, buf + 10, flen);
        }
        send_response(cli, NULL, 0);
        break;
    }
    case CMD_GET_STATUS: {
        char json[512];
        size_t n = scan_state_to_json(json, sizeof json);
        send_response(cli, json, n);
        break;
    }
    case CMD_SETOPT:
        handle_setopt(buf);
        send_response(cli, NULL, 0);
        break;

    default:
        /* invalid selector — close without payload (matches original) */
        break;
    }
}

void comms_handle_next(void)
{
    if (g_listen_fd < 0) return;
    int cli = accept(g_listen_fd, NULL, NULL);
    if (cli < 0) {
        /* non-fatal; main loop will retry */
        usleep(1000);
        return;
    }
    uint8_t *buf = malloc(PINEAP_CMD_LEN);
    if (!buf) { close(cli); return; }
    int got = recv_command(cli, buf);
    if (got >= 1) dispatch(cli, buf);
    free(buf);
    close(cli);
}
