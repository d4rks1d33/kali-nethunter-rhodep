/*
 * main.c — pineapd-ng entry point.
 *
 * Usage: pineapd-ng <config> [--initdbs] [--debug] [--foreground]
 *   (the original accepts: Usage: %s [path to config] (--initdbs) (--debug))
 *
 * Startup (INFORME §A.6):
 *   1. parse args, set config path, reload config
 *   2. (optional) root check
 *   3. single-instance lock (/var/run/pineapd.lock)
 *   4. open DBs (schema create). --initdbs => exit after this.
 *   5. start capture / parser threads, init socket
 *   6. main loop: handleNextCommand + channel-hop timing + SSID-pool beaconing
 */
#include "pineapd.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>

static volatile sig_atomic_t g_shutdown = 0;
static int g_lock_fd = -1;

static void handle_signal(int sig)
{
    (void)sig;
    if (g_shutdown) {
        /* "Caught multiple shutdown signals, bailing" */
        _exit(1);
    }
    g_shutdown = 1;
    scan_state_get()->running = false;
}

static int acquire_lock(void)
{
    g_lock_fd = open(PINEAPD_LOCK_PATH, O_RDWR | O_CREAT, 0644);
    if (g_lock_fd < 0) return -1;
    if (flock(g_lock_fd, LOCK_EX | LOCK_NB) != 0) {
        fprintf(stderr, "Only one instance of pineapd can be started at once\n");
        return -1;
    }
    return 0;
}

static void release_lock(void)
{
    if (g_lock_fd >= 0) {
        flock(g_lock_fd, LOCK_UN);
        close(g_lock_fd);
        g_lock_fd = -1;
        unlink(PINEAPD_LOCK_PATH);
    }
}

static void usage(const char *prog)
{
    fprintf(stderr, "Usage: %s [path to config] (--initdbs) (--debug) (--foreground)\n", prog);
}

int main(int argc, char **argv)
{
    if (argc < 2) { usage(argv[0]); return 1; }

    bool initdbs = false, debug = false;
    bool foreground = false; (void)foreground;
    const char *conf = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "--initdbs", 9) || !strcmp(argv[i], "--initdb"))
            initdbs = true;
        else if (!strcmp(argv[i], "--debug"))
            debug = true;
        else if (!strcmp(argv[i], "--foreground") || !strcmp(argv[i], "-f"))
            foreground = true;
        else if (argv[i][0] != '-')
            conf = argv[i];
    }
    if (!conf) { usage(argv[0]); return 1; }

    logger_set_enabled(debug);
    config_set_path(conf);
    config_reload();

    /* --initdbs: create schema and exit (no root/lock needed) */
    if (initdbs) {
        if (db_init_all() != 0) { fprintf(stderr, "initdbs failed\n"); return 1; }
        db_close_all();
        return 0;
    }

    /* root check (original raises SIGSEGV; we just warn+exit cleanly) */
    if (geteuid() != 0) {
        pineap_log("MAIN", "PineAP must be run as root");
        fprintf(stderr, "PineAP must be run as root\n");
        return 1;
    }

    if (acquire_lock() != 0) return 1;

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    signal(SIGPIPE, SIG_IGN);

    if (db_init_all() != 0) { release_lock(); return 1; }

    mkdir(PINEAPD_HANDSHAKE_DIR, 0755);

    pineap_config_t *c = config_get();
    scan_state_t *s = scan_state_get();
    s->running = true;

    /* start worker threads */
    if (parser_start() != 0)
        pineap_log("MAIN", "parser thread failed to start");

    /* capture interface — may fail without WiFi hardware; keep serving socket */
    if (iface_start(c->pineap_interface) != 0)
        pineap_log("MAIN", "interface %s unavailable; socket/DB still served",
                   c->pineap_interface);

    if (comms_init() != 0) {
        pineap_log("MAIN", "comms init failed");
        iface_stop(); parser_stop(); db_close_all(); release_lock();
        return 1;
    }

    pineap_log("MAIN", "pineapd-ng running");

    /* main loop */
    struct timespec last_beacon; clock_gettime(CLOCK_MONOTONIC, &last_beacon);
    while (s->running && !g_shutdown) {
        comms_handle_next();   /* accept + recv + dispatch one client */

        /* SSID pool beaconing */
        if (c->broadcast_ssid_pool) {
            struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
            long dms = (now.tv_sec - last_beacon.tv_sec) * 1000 +
                       (now.tv_nsec - last_beacon.tv_nsec) / 1000000;
            if (dms >= 1000) {
                beacon_ssid_pool();
                last_beacon = now;
            }
        }

        /* scan duration enforcement */
        if (s->recon_running && s->duration_sec > 0) {
            if (time(NULL) - s->start_time >= s->duration_sec) {
                pthread_mutex_lock(&s->lock);
                int elapsed = (int)(time(NULL) - s->start_time);
                s->scan_percent = 100;
                s->recon_running = false;
                s->hopping = false;
                (void)elapsed;
                pthread_mutex_unlock(&s->lock);
                /* duration elapsed: stop the native STA recon scan too */
                scan_sta_stop();
            } else {
                pthread_mutex_lock(&s->lock);
                int el = (int)(time(NULL) - s->start_time);
                s->scan_percent = s->duration_sec ? (el * 100 / s->duration_sec) : 0;
                pthread_mutex_unlock(&s->lock);
            }
        }

        usleep(2000);  /* main-loop pacing (accept is non-blocking) */
    }

    pineap_log("MAIN", "shutting down");
    scan_sta_stop();
    comms_shutdown();
    iface_stop();
    parser_stop();
    db_close_all();
    release_lock();
    return 0;
}
