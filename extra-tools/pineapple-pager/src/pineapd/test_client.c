/*
 * test_client.c — minimal C client for the pineapd-pager socket.
 *
 * Builds with: make test_client   (links msg.c + mpack for framing/decoding)
 * Usage:       ./test_client [sock_path]
 *
 * Sends cmd 100 (list_interfaces) and cmd 10 (gpsd_report) and prints the
 * decoded "message"/"error" of each basic/response map. Proves the wire framing
 * (8B BE header + msgpack body) round-trips against the running daemon.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

static int connect_sock(const char *path)
{
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_un a;
    memset(&a, 0, sizeof a);
    a.sun_family = AF_UNIX;
    strncpy(a.sun_path, path, sizeof a.sun_path - 1);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) != 0) { close(fd); return -1; }
    return fd;
}

/* send an empty-body command, read the response frame, print a summary */
static int do_cmd(const char *path, uint32_t cmd, const char *label)
{
    int fd = connect_sock(path);
    if (fd < 0) { printf("[ERR] cmd %u (%s): connect failed\n", cmd, label); return 1; }

    /* empty msgpack body */
    if (msg_write_frame(fd, cmd, NULL, 0) != 0) {
        printf("[ERR] cmd %u (%s): write failed\n", cmd, label);
        close(fd); return 1;
    }

    msg_frame_t resp;
    if (msg_read_frame(fd, &resp) != 0) {
        printf("[ERR] cmd %u (%s): read failed\n", cmd, label);
        close(fd); return 1;
    }
    close(fd);

    /* decode the response map and pull out error/message if present */
    mpack_tree_t tree;
    mpack_node_t root;
    bool ok_map = msg_body_parse(&tree, resp.body, resp.body_len, &root);
    long err = -1;
    char message[128] = "(none)";
    if (ok_map) {
        err = (long)msg_get_i64(root, "error", -1);
        msg_get_str(root, "message", message, sizeof message);
    }
    int rc = (resp.cmd == cmd && ok_map) ? 0 : 1;
    printf("[%s] cmd %u (%s) -> rcmd=%u body_len=%zu error=%ld message=\"%s\"\n",
           rc == 0 ? "OK " : "ERR", cmd, label, resp.cmd, resp.body_len, err, message);
    mpack_tree_destroy(&tree);
    msg_frame_free(&resp);
    return rc;
}

int main(int argc, char **argv)
{
    const char *path = (argc > 1) ? argv[1] : PINEAPD_SOCK_PATH;
    int rc = 0;
    rc |= do_cmd(path, 100, "list_interfaces");
    rc |= do_cmd(path, 10,  "gpsd_report");
    rc |= do_cmd(path, 300, "fetch_ssid_pool");
    rc |= do_cmd(path, 99999, "unknown");
    return rc;
}
