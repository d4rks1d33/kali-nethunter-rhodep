/*
 * main.c — pineapd-pager entry point.
 *
 * Mirrors the init flags of the real Pager procd service. Foreground only
 * (systemd supervises; no self-daemonize).
 *
 * Flags (all optional; config file supplies defaults):
 *   --recon                 enable recon at start
 *   --reconpath <path>      recon.db path
 *   --reconname <name>      scan name label
 *   --handshakepath <path>  handshake capture dir
 *   --payloads <path>       payloads dir
 *   --wiglepath <path>      wigle export dir
 *   --pcappath <path>       pcap output dir
 *   --interface <if>        monitor/primary interface (e.g. wlan1mon)
 *   --band <if:bands>       band spec (e.g. wlan1mon:2.4,5) -> bands
 *   --primary <if>          primary monitor interface (alias of --interface)
 *   --inject                enable injection
 *   --hop                   enable channel hopping
 *   --channel <n>           fix a channel (0 => hop)
 *   --config <path>         config file (default /opt/pineapple-pager/etc/pineapd.conf)
 *   --debug                 verbose logging to stderr
 *   --foreground / -f       foreground (default; accepted for compatibility)
 *
 * Startup: parse flags -> load config file -> apply flag overrides -> open DBs
 * -> single-instance lock -> create socket -> main loop (accept + dispatch).
 */
#include "pager.h"
#include "openap.h"
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
    if (g_shutdown) _exit(1);
    g_shutdown = 1;
    scan_state_get()->running = false;
}

static int acquire_lock(void)
{
    g_lock_fd = open(PINEAPD_LOCK_PATH, O_RDWR | O_CREAT, 0644);
    if (g_lock_fd < 0) return -1;
    if (flock(g_lock_fd, LOCK_EX | LOCK_NB) != 0) {
        fprintf(stderr, "Only one instance of pineapd-pager can run at once\n");
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
    fprintf(stderr,
      "Usage: %s [--config <path>] [--recon] [--reconpath <p>] [--reconname <n>]\n"
      "          [--handshakepath <p>] [--payloads <p>] [--wiglepath <p>]\n"
      "          [--pcappath <p>] [--interface <if>] [--band <if:bands>]\n"
      "          [--primary <if>] [--inject] [--hop] [--channel <n>]\n"
      "          [--debug] [--foreground]\n", prog);
}

/* mkdir -p of the parent directory of `path` (best effort). */
static void ensure_parent_dir(const char *path)
{
    char tmp[512];
    strncpy(tmp, path, sizeof tmp - 1);
    tmp[sizeof tmp - 1] = '\0';
    char *slash = strrchr(tmp, '/');
    if (!slash) return;
    *slash = '\0';
    /* walk and create each component */
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') { *p = '\0'; mkdir(tmp, 0755); *p = '/'; }
    }
    mkdir(tmp, 0755);
}

#define CFGCPY(dst, val) do { \
        strncpy((dst), (val), sizeof(dst) - 1); (dst)[sizeof(dst)-1] = '\0'; \
    } while (0)

int main(int argc, char **argv)
{
    const char *conf = PINEAPD_CONF_PATH;
    bool debug = false;
    bool foreground = true; (void)foreground;

    /* flag overrides collected here and applied AFTER config_reload so CLI wins */
    bool  f_recon = false, set_recon = false;
    bool  f_inject = false, set_inject = false;
    bool  f_hop = false, set_hop = false;
    int   f_channel = 0, set_channel = 0;
    const char *f_reconpath = NULL, *f_reconname = NULL, *f_handshakepath = NULL;
    const char *f_payloads = NULL, *f_wiglepath = NULL, *f_pcappath = NULL;
    const char *f_interface = NULL, *f_band = NULL, *f_primary = NULL;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        #define NEXTARG() ((i + 1 < argc) ? argv[++i] : NULL)
        if      (!strcmp(a, "--config"))        conf = NEXTARG();
        else if (!strcmp(a, "--recon"))         { f_recon = true; set_recon = true; }
        else if (!strcmp(a, "--reconpath"))     f_reconpath = NEXTARG();
        else if (!strcmp(a, "--reconname"))     f_reconname = NEXTARG();
        else if (!strcmp(a, "--handshakepath")) f_handshakepath = NEXTARG();
        else if (!strcmp(a, "--payloads"))      f_payloads = NEXTARG();
        else if (!strcmp(a, "--wiglepath"))     f_wiglepath = NEXTARG();
        else if (!strcmp(a, "--pcappath"))      f_pcappath = NEXTARG();
        else if (!strcmp(a, "--interface"))     f_interface = NEXTARG();
        else if (!strcmp(a, "--band"))          f_band = NEXTARG();
        else if (!strcmp(a, "--primary"))       f_primary = NEXTARG();
        else if (!strcmp(a, "--inject"))        { f_inject = true; set_inject = true; }
        else if (!strcmp(a, "--hop"))           { f_hop = true; set_hop = true; }
        else if (!strcmp(a, "--channel"))       { const char *v = NEXTARG(); if (v) { f_channel = atoi(v); set_channel = 1; } }
        else if (!strcmp(a, "--debug"))         debug = true;
        else if (!strcmp(a, "--foreground") || !strcmp(a, "-f")) foreground = true;
        else if (!strcmp(a, "--help") || !strcmp(a, "-h")) { usage(argv[0]); return 0; }
        else { fprintf(stderr, "unknown flag: %s\n", a); usage(argv[0]); return 1; }
        #undef NEXTARG
    }
    if (!conf) conf = PINEAPD_CONF_PATH;

    logger_set_enabled(debug);
    config_set_path(conf);
    config_reload();   /* tolerant: uses defaults if file is missing */

    /* apply CLI overrides on top of the config file */
    pager_config_t *c = config_get();
    if (set_recon)      c->recon_enabled = f_recon;
    if (set_inject)     c->inject = f_inject;
    if (set_hop)        c->hop = f_hop;
    if (set_channel)    c->channel = f_channel;
    if (f_reconpath)    { CFGCPY(c->recon_path, f_reconpath); CFGCPY(c->recon_db_path, f_reconpath); }
    if (f_reconname)    CFGCPY(c->recon_name, f_reconname);
    if (f_handshakepath)CFGCPY(c->handshake_path, f_handshakepath);
    if (f_payloads)     CFGCPY(c->payloads_path, f_payloads);
    if (f_wiglepath)    CFGCPY(c->wigle_path, f_wiglepath);
    if (f_pcappath)     CFGCPY(c->pcap_path, f_pcappath);
    if (f_interface)    CFGCPY(c->monitor_iface, f_interface);
    if (f_primary)      CFGCPY(c->monitor_iface, f_primary);
    if (f_band) {
        /* --band if:bands  -> store the bands part (after the colon if present) */
        const char *colon = strchr(f_band, ':');
        CFGCPY(c->bands, colon ? colon + 1 : f_band);
    }

    /* ensure runtime dirs exist (socket dir, db dir, handshake/pcap dirs) */
    ensure_parent_dir(PINEAPD_SOCK_PATH);
    ensure_parent_dir(c->recon_db_path);
    ensure_parent_dir(c->hostapd_db_path);
    ensure_parent_dir(c->ssid_db_path);
    mkdir(c->handshake_path, 0755);
    mkdir(c->pcap_path, 0755);

    /* root check — radio ops need root, but the socket/DB can still be served.
     * We warn rather than hard-exit so the daemon is testable without root. */
    if (geteuid() != 0)
        pineap_log("MAIN", "not running as root; radio operations will fail");

    if (acquire_lock() != 0) return 1;

    signal(SIGINT,  handle_signal);
    signal(SIGTERM, handle_signal);
    signal(SIGPIPE, SIG_IGN);

    if (db_init_all() != 0) {
        pineap_log("MAIN", "db init failed");
        release_lock();
        return 1;
    }

    scan_state_t *s = scan_state_get();
    s->running = true;
    s->ssid_collect = c->ssid_collect;

    if (sock_init() != 0) {
        pineap_log("MAIN", "socket init failed");
        db_close_all(); release_lock();
        return 1;
    }

    /* auto-start recon if requested (native STA scan; never touches wlan0 link) */
    if (c->recon_enabled) {
        int scan_id = recon_next_scan_id();
        pthread_mutex_lock(&s->lock);
        s->recon_scan_id = scan_id;
        s->recon_running = true;
        s->start_time = time(NULL);
        pthread_mutex_unlock(&s->lock);
        /* Monitor (Archer) is primary; wlan0 STA scan ONLY as fallback when the
         * Archer is absent (never both: wlan0 scan is intermittent + clientless
         * and would pollute the monitor's AP/client data). */
        if (recon_mon_start(scan_id) != 0)
            scan_sta_start(scan_id);
        pineap_log("MAIN", "auto recon started (scan_id=%d)", scan_id);
    }

    /* Open AP bridge: watch the firmware's /etc/config/wireless and mirror its
     * radio0 AP VAPs (Open AP / Evil WPA) onto the real host radio via native
     * hostapd. This is the path the firmware ACTUALLY uses to signal Open AP
     * (it writes wlan0open disabled=0; see openap.c / docs/OPENAP-INTENT.md).
     * It never touches the SSH/uplink radio. */
    openap_start_watcher();

    pineap_log("MAIN", "pineapd-pager running (socket=%s)", PINEAPD_SOCK_PATH);

    /* main loop: accept + dispatch one client per iteration, with pacing. */
    while (s->running && !g_shutdown) {
        sock_handle_next();

        /* Note: recon idle-teardown (firmware left the Recon screen) is handled
         * by recon_mon's own watchdog thread, independent of this accept loop
         * (which can block on the firmware's kept-open connection). */

        /* recon duration enforcement (if a bounded scan was requested) */
        if (s->recon_running && s->duration_sec > 0) {
            int el = (int)(time(NULL) - s->start_time);
            pthread_mutex_lock(&s->lock);
            if (el >= s->duration_sec) {
                s->scan_percent = 100;
                s->recon_running = false;
                pthread_mutex_unlock(&s->lock);
                scan_sta_stop();
            } else {
                s->scan_percent = s->duration_sec ? (el * 100 / s->duration_sec) : 0;
                pthread_mutex_unlock(&s->lock);
            }
        }

        usleep(2000);
    }

    pineap_log("MAIN", "shutting down");
    openap_shutdown();     /* kill hostapd + return AP radio to managed */
    recon_mon_stop();      /* stop capture + tear wlan1mon down (no orphan) */
    scan_sta_stop();
    iface_tx_close();
    sock_shutdown();
    db_close_all();
    release_lock();
    return 0;
}
