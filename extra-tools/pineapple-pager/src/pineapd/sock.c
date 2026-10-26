/*
 * sock.c — AF_UNIX / SOCK_STREAM command server for the Pager protocol.
 *
 * Flow per client connection:
 *   1. msg_read_frame(): read [u32 cmd BE][u32 len BE][msgpack body]
 *   2. parse the body into an mpack tree (msg_body_parse)
 *   3. look up the handler for `cmd` (sock_lookup_handler, handlers.c)
 *   4. handler writes a response map into a growable msgpack writer
 *   5. msg_write_frame(): same framing back (cmd echoed, body = response map)
 *
 * Unknown command codes get a basic error response (error!=0). Any body-build
 * failure falls back to a basic error so a client always gets a valid frame.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>

static int g_listen_fd = -1;

int sock_init(void)
{
    unlink(PINEAPD_SOCK_PATH);
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (g_listen_fd < 0) {
        pineap_log("SOCK", "socket() failed: %s", strerror(errno));
        return -1;
    }
    int one = 1;
    setsockopt(g_listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, PINEAPD_SOCK_PATH, sizeof addr.sun_path - 1);

    if (bind(g_listen_fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        pineap_log("SOCK", "bind(%s) failed: %s", PINEAPD_SOCK_PATH, strerror(errno));
        close(g_listen_fd); g_listen_fd = -1;
        return -1;
    }
    if (listen(g_listen_fd, 16) < 0) {
        pineap_log("SOCK", "listen() failed: %s", strerror(errno));
        close(g_listen_fd); g_listen_fd = -1;
        return -1;
    }
    pineap_log("SOCK", "listening on %s", PINEAPD_SOCK_PATH);
    return 0;
}

void sock_shutdown(void)
{
    if (g_listen_fd >= 0) { close(g_listen_fd); g_listen_fd = -1; }
    unlink(PINEAPD_SOCK_PATH);
}

/* Build the response body for a request. Returns a malloc'd buffer (*len set),
 * or NULL on hard failure. */
static char *build_response(uint32_t cmd, const char *body, size_t body_len,
                            size_t *len)
{
    cmd_handler_fn fn = sock_lookup_handler(cmd);
    if (!fn) {
        char msg[48];
        snprintf(msg, sizeof msg, "unknown command %u", cmd);
        return msg_make_basic(1, msg, len);
    }

    /* parse request body (tolerant: nil/empty is fine) */
    mpack_tree_t tree;
    mpack_node_t root;
    msg_body_parse(&tree, body, body_len, &root);

    /* prepare growable response writer */
    char *data = NULL;
    size_t size = 0;
    mpack_writer_t w;
    msg_body_begin(&w, &data, &size);

    fn(cmd, root, &w);

    char *out = msg_body_finish(&w, &data, &size, len);
    mpack_tree_destroy(&tree);

    if (!out) {
        /* handler produced an invalid map; return a safe basic error */
        return msg_make_basic(1, "internal error building response", len);
    }
    return out;
}

/* Serve ONE accepted client connection to EOF. Runs on its own detached thread
 * (see sock_handle_next) so a long-lived connection (e.g. the firmware's
 * persistent dashboard poll connection) does NOT block other connections.
 *
 * WHY THREADED: the firmware opens a SEPARATE connection for the recon
 * stable-list (cmd=216) while already holding a persistent connection for the
 * dashboard polls (cmd 10/208/314).  A single-threaded accept+serve loop blocks
 * inside the dashboard connection's read() between polls and cannot accept() the
 * stable-list connection until the dashboard one closes -> the 216 request sits
 * unserved until the firmware's read deadline fires ("failed to get recon stable
 * list from pineap: i/o timeout") and the list never refreshes.  Serving each
 * connection concurrently lets the 216 request be answered immediately.
 * DB access underneath is serialised by g_db_lock, so concurrency is safe. */
static void *serve_client(void *arg)
{
    int cli = (int)(intptr_t)arg;

    /* The accepted socket inherits O_NONBLOCK from the SOCK_NONBLOCK listener;
     * clear it so msg_read_frame() can block waiting for the full frame. */
    int fl = fcntl(cli, F_GETFL, 0);
    if (fl >= 0) fcntl(cli, F_SETFL, fl & ~O_NONBLOCK);

    /* The real firmware keeps the connection OPEN and pipelines many commands
     * over the same socket. Serve frames in a loop until the client hangs up
     * (msg_read_frame() returns != 0 on a clean EOF or error). Closing after a
     * single command makes the firmware's next write hit a Broken pipe and see
     * the connection as EOF ("failed to get recon stable list from pineap: EOF"). */
    for (;;) {
        msg_frame_t req;
        if (msg_read_frame(cli, &req) != 0)
            break;  /* client closed the connection or framing error */

        size_t rlen = 0;
        char *resp = build_response(req.cmd, req.body, req.body_len, &rlen);
        if (resp) {
            if (msg_write_frame(cli, req.cmd, resp, rlen) != 0) {
                free(resp);
                msg_frame_free(&req);
                break;  /* peer gone */
            }

            /* recon "stable list" handshake (RE'd via strace of the firmware's
             * socket reads on one cmd=216): the firmware's AP-list screen sends
             * ONE send_devices (216) with rstable:true, then on this same
             * connection READS devicelist frames in a loop while the response
             * field "more" is true, and finally reads one CLIENTS frame.  The AP
             * handler above now answers the whole AP set in a single more=false
             * frame (loop terminates after one read); we then push exactly ONE
             * stable CLIENTS frame (also more=false) for the second read.  No
             * second request is ever sent by the firmware, so without this
             * clients frame its follow-up read hits the deadline ("failed to get
             * recon stable list from pineap: i/o timeout"). */
            if (req.cmd >= 201 && req.cmd <= 217) {
                mpack_tree_t t;
                mpack_node_t r;
                msg_body_parse(&t, req.body, req.body_len, &r);
                int ok = 1;
                if (send_devices_wants_rstable(r)) {
                    char *cdata = NULL; size_t csize = 0, clen = 0;
                    mpack_writer_t cw;
                    msg_body_begin(&cw, &cdata, &csize);
                    send_devices_build_clients(r, &cw);
                    char *cout = msg_body_finish(&cw, &cdata, &csize, &clen);
                    if (cout) {
                        if (msg_write_frame(cli, req.cmd, cout, clen) != 0) ok = 0;
                        free(cout);
                    }
                }
                mpack_tree_destroy(&t);
                if (!ok) { free(resp); msg_frame_free(&req); break; }
            }
            free(resp);
        } else {
            /* last-ditch empty basic error */
            size_t bl = 0;
            char *b = msg_make_basic(1, "error", &bl);
            if (b) { msg_write_frame(cli, req.cmd, b, bl); free(b); }
        }

        msg_frame_free(&req);
    }

    close(cli);
    return NULL;
}

void sock_handle_next(void)
{
    if (g_listen_fd < 0) return;
    int cli = accept(g_listen_fd, NULL, NULL);
    if (cli < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK)
            usleep(1000);
        return;
    }

    /* Serve this connection on a detached thread so concurrent connections
     * (persistent dashboard poll + recon stable-list) don't block each other. */
    pthread_t th;
    if (pthread_create(&th, NULL, serve_client, (void *)(intptr_t)cli) != 0) {
        /* thread spawn failed: fall back to serving inline so we never drop the
         * connection (correctness over concurrency). */
        serve_client((void *)(intptr_t)cli);
    } else {
        pthread_detach(th);
    }
}
