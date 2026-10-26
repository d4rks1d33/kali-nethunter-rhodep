/*
 * test_proto.c — protocol compatibility tester for pineapd-ng.
 *
 * Connects to the pineapd socket and sends commands using the EXACT original
 * wire format (4097 bytes, zero-filled, big-endian, buf[0]=cmd) as captured
 * from the real pineap CLI under qemu (see /tmp/opencode/proto-re/recv_*.bin).
 *
 * Usage: test_proto <socket_path> <cmd> [args...]
 *   get_status
 *   add_ssid <ssid>
 *   del_ssid <ssid>
 *   list_ssids
 *   clear_ssids
 *   list_probes [mac]
 *   setopt_bool <opt> <0|1>
 *   ap_channel <ch>            (reproduces the single-byte quirk)
 *   beacon_interval <us>
 *   run_scan <freq> <dur>
 *   deauth <tgt> <src> <ch> <mult>
 *   raw <hexstring>           (first bytes; rest zero-filled)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/un.h>

#define LEN 4097

static void put_be32(uint8_t *b, int off, uint32_t v)
{
    b[off]   = (v >> 24) & 0xff;
    b[off+1] = (v >> 16) & 0xff;
    b[off+2] = (v >> 8)  & 0xff;
    b[off+3] = v & 0xff;
}

static int hexpair(const char *s)
{
    int hi, lo;
    #define H(c) ((c)>='0'&&(c)<='9'?(c)-'0':((c)|0x20)>='a'&&((c)|0x20)<='f'?((c)|0x20)-'a'+10:-1)
    hi = H(s[0]); lo = H(s[1]);
    #undef H
    if (hi < 0 || lo < 0) return -1;
    return (hi << 4) | lo;
}

static int parse_mac(const char *s, uint8_t out[6])
{
    unsigned v[6];
    if (sscanf(s, "%2x:%2x:%2x:%2x:%2x:%2x",
               &v[0],&v[1],&v[2],&v[3],&v[4],&v[5]) == 6 ||
        sscanf(s, "%2x%2x%2x%2x%2x%2x",
               &v[0],&v[1],&v[2],&v[3],&v[4],&v[5]) == 6) {
        for (int i=0;i<6;i++) out[i]=(uint8_t)v[i];
        return 0;
    }
    return -1;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <sockpath> <cmd> [args...]\n", argv[0]);
        return 2;
    }
    const char *sockpath = argv[1];
    const char *cmd = argv[2];

    uint8_t buf[LEN];
    memset(buf, 0, LEN);
    int want_response = 0;

    if (!strcmp(cmd, "get_status")) {
        buf[0] = 20; want_response = 1;
    } else if (!strcmp(cmd, "add_ssid")) {
        buf[0] = 14;
        if (argc > 3) strncpy((char*)buf+1, argv[3], LEN-2);
    } else if (!strcmp(cmd, "del_ssid")) {
        buf[0] = 15;
        if (argc > 3) strncpy((char*)buf+1, argv[3], LEN-2);
    } else if (!strcmp(cmd, "list_ssids")) {
        buf[0] = 17; want_response = 1;
    } else if (!strcmp(cmd, "clear_ssids")) {
        buf[0] = 16;
    } else if (!strcmp(cmd, "list_probes")) {
        buf[0] = 18; want_response = 1;
        if (argc > 3) parse_mac(argv[3], buf+1);
    } else if (!strcmp(cmd, "setopt_bool")) {
        buf[0] = 21; buf[1] = (uint8_t)atoi(argv[3]); buf[6] = (uint8_t)atoi(argv[4]);
    } else if (!strcmp(cmd, "ap_channel")) {
        /* QUIRK: single byte at offset 6 */
        buf[0] = 21; buf[1] = 9; buf[6] = (uint8_t)atoi(argv[3]);
    } else if (!strcmp(cmd, "beacon_interval")) {
        buf[0] = 21; buf[1] = 12; put_be32(buf, 6, (uint32_t)atoi(argv[3]));
    } else if (!strcmp(cmd, "set_source")) {
        buf[0] = 21; buf[1] = 15; parse_mac(argv[3], buf+6);
    } else if (!strcmp(cmd, "set_target")) {
        buf[0] = 21; buf[1] = 16; parse_mac(argv[3], buf+6);
    } else if (!strcmp(cmd, "run_scan")) {
        buf[0] = 4; buf[1] = (uint8_t)atoi(argv[3]); put_be32(buf, 2, (uint32_t)atoi(argv[4]));
    } else if (!strcmp(cmd, "deauth")) {
        buf[0] = 8; parse_mac(argv[3], buf+1); parse_mac(argv[4], buf+7);
        buf[13] = (uint8_t)atoi(argv[5]); put_be32(buf, 14, (uint32_t)atoi(argv[6]));
    } else if (!strcmp(cmd, "raw")) {
        const char *hx = argv[3];
        int o = 0;
        for (size_t i = 0; hx[i] && hx[i+1] && o < LEN; i += 2) {
            int v = hexpair(hx + i);
            if (v < 0) break;
            buf[o++] = (uint8_t)v;
        }
        want_response = (argc > 4 && !strcmp(argv[4], "resp"));
    } else {
        fprintf(stderr, "unknown cmd %s\n", cmd);
        return 2;
    }

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sockpath, sizeof addr.sun_path - 1);
    if (connect(fd, (struct sockaddr*)&addr, sizeof(struct sockaddr_un)) < 0) {
        perror("connect"); return 1;
    }

    /* always send 4097 bytes */
    size_t off = 0;
    while (off < LEN) {
        ssize_t w = send(fd, buf + off, LEN - off, 0);
        if (w <= 0) { perror("send"); return 1; }
        off += (size_t)w;
    }

    if (want_response) {
        /* read until NUL */
        char c;
        while (recv(fd, &c, 1, 0) > 0) {
            if (c == '\0') break;
            fputc(c, stdout);
        }
    }
    close(fd);
    return 0;
}
