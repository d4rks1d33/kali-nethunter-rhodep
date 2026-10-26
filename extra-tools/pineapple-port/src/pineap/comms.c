/*
 * comms.c — UNIX socket client for pineap-ng.
 *
 * Matches the original openCliComms exactly:
 *   - socket(AF_UNIX, SOCK_STREAM, 0)
 *   - sockaddr_un with sun_path = /var/run/pineapd.sock, connect len = 110
 *   - send(fd, buf, 4097, 0) — always 4097 bytes
 *   - if the command prints a response: recv 1 byte at a time until NUL,
 *     stream raw to stdout (no transformation).  Then close.
 *   - same stderr error strings on failure, exit(1).
 */
#include "pineap.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/un.h>

const char *pineap_sock_path(void)
{
    const char *e = getenv("PINEAP_SOCK");
    return (e && *e) ? e : PINEAP_SOCK_PATH;
}

const char *pineap_iface(void)
{
    const char *e = getenv("PINEAP_IFACE");
    return (e && *e) ? e : PINEAP_HOSTAPD_IFACE;
}

const char *pineap_hostapd_cli(void)
{
    const char *e = getenv("PINEAP_HOSTAPD_CLI");
    return (e && *e) ? e : PINEAP_HOSTAPD_CLI;
}

int pineap_send(const uint8_t *req, bool read_response)
{
    const char *path = pineap_sock_path();

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        /* original: "Failed to create socket: <strerror>" -> cerr, exit(1) */
        fprintf(stderr, "Failed to create socket: %s\n", strerror(errno));
        return 1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof addr.sun_path - 1);

    /* connect with len 110 (= sizeof(struct sockaddr_un)) to match original */
    if (connect(fd, (struct sockaddr *)&addr, sizeof(struct sockaddr_un)) < 0) {
        fprintf(stderr, "Failed to connect to socket at path: %s: %s\n",
                path, strerror(errno));
        close(fd);
        return 1;
    }

    /* always send exactly 4097 bytes */
    size_t off = 0;
    while (off < PINEAP_CMD_LEN) {
        ssize_t w = send(fd, req + off, PINEAP_CMD_LEN - off, 0);
        if (w <= 0) {
            if (w < 0 && (errno == EINTR)) continue;
            fprintf(stderr, "Failed to send command: %s\n", strerror(errno));
            close(fd);
            return 1;
        }
        off += (size_t)w;
    }

    if (read_response) {
        /* read 1 byte at a time; stop at NUL; dump raw to stdout */
        char c;
        ssize_t n;
        while ((n = recv(fd, &c, 1, 0)) > 0) {
            if (c == '\0') break;
            fwrite(&c, 1, 1, stdout);
        }
        fflush(stdout);
    }

    close(fd);
    return 0;
}
