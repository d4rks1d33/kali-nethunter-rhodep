/*
 * main.c — pineap-ng entry point.  Argv parsing + command dispatch, matching
 * the original `pineap` CLI (INFORME-pineap-RE.md §A/B).
 *
 * Usage (both modes, identical to the original):
 *   pineap <command> [args]                 (legacy syntax)
 *   pineap <path/to/pineap.conf> <cmd> [args]   (config syntax)
 * The CLI does NOT read the config file; argv[1] containing "pineap.conf"
 * merely selects config-mode (argv[1]=conf path, argv[2]=cmd, argv[3..]=args).
 *
 * Routing of each command (per RE §B / §F):
 *   - socket (4097-byte request)  : most commands
 *   - hostapd_cli only            : ssid_filter, mac_filter
 *   - hostapd_cli AND socket      : karma on/off
 *   - local only (no socket)      : help, version
 */
#include "pineap.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ------------------------------------------------------------------------- *
 *  Help / version — exact text from the original binary's strings.
 * ------------------------------------------------------------------------- */
static void print_usage(const char *argv0)
{
    /* original: "Usage: <argv0> [command] [options]" + Commands block */
    printf("Usage: %s [command] [options]\n", argv0);
    printf("PineAP V3.2\n");
    printf("Copyright 2019 Hak5 LLC\n");
    printf("Commands:\n");
    printf("\thelp\n");
    printf("\tversion\n");
    printf("\tkarma [on/off]\n");
    printf("\tadd_ssid [ssid]\n");
    printf("\tadd_ssid_file [file with list of ssids]\n");
    printf("\tdel_ssid [ssid]\n");
    printf("\tdel_ssid_file [file with list of ssids]\n");
    printf("\tlist_ssids\n");
    printf("\tlist_probes <MAC Address>\n");
    printf("\tcapture_ssids [on/off]\n");
    printf("\tconnect_notifications [on/off]\n");
    printf("\tdisconnect_notifications [on/off]\n");
    printf("\tbeacon_responses [on/off]\n");
    printf("\tbroadcast_pool [on/off]\n");
    printf("\tssid_filter [black/white]\n");
    printf("\tmac_filter [black/white]\n");
    printf("\tbeacon_interval [LOW/NORMAL/AGGRESSIVE]\n");
    printf("\tbeacon_response_interval [LOW/NORMAL/AGGRESSIVE]\n");
    printf("\tset_source [mac]\n");
    printf("\tset_target [mac]\n");
    printf("\tlogging [on/off]\n");
    printf("\tdeauth [mac] [bssid] [channel] [multiplier]\n");
    printf("\tap_channel [channel]\n");
    printf("\trun_scan [duration in sec] [frequencies (0 for 2GHz, 1 for 5GHz, 2 for both)]\n");
    printf("\tpause_scan\n");
    printf("\tunpause_scan\n");
    printf("\tstop_scan\n");
    printf("\tclear_ssids\n");
    printf("\tinject [file] [channel] [multiplier] [delay]\n");
    printf("\tget_status\n");
    printf("\tpause (channel hopping)\n");
    printf("\tunpause (channel hopping)\n");
    printf("\thandshake_capture_start [bssid] [channel]\n");
    printf("\thandshake_capture_stop\n");
    printf("\trts_attack [on/off]\n");
    printf("\trts_scan_id [scan id]\n");
}

static void print_version(void)
{
    printf("PineAP version 3.2\n");
    printf("Commit id: de3598e095\n");
    printf("Built with debug support disabled\n");
    printf("Built with legacy command syntax enabled\n");
    printf("Built at: Mon Mar  9 13:04:35 UTC 2020\n");
}

/* error helpers — exact strings from the original */
static int err_missing(const char *cmd)
{
    fprintf(stderr, "Missing argument to %s command\n", cmd);
    return 1;
}
static int err_requires(const char *cmd, int need, int given)
{
    fprintf(stderr, "The %s command requires %d arguments (%d given)\n",
            cmd, need, given);
    return 1;
}
static int err_unknown(const char *cmd)
{
    fprintf(stderr, "Unknown command: %s\n", cmd);
    return 1;
}

/* ------------------------------------------------------------------------- *
 *  hostapd_cli side effects (RE §F).  The original hardcodes -i wlan0; we use
 *  the configurable iface (PINEAP_IFACE env or -DPINEAP_HOSTAPD_IFACE).
 * ------------------------------------------------------------------------- */
static void run_hostapd(const char *subcmd)
{
    char line[512];
    /* Call the Hak5->mana wrapper by ABSOLUTE path so it cannot be shadowed by
     * the stock /usr/sbin/hostapd_cli that the hostapd-mana package installs.
     * (Bare `hostapd_cli` relied on PATH and let `karma_enable` reach mana
     * untranslated -> "Unknown command 'karma_enable'".) */
    snprintf(line, sizeof line, "%s -i %s %s",
             pineap_hostapd_cli(), pineap_iface(), subcmd);
    int rc = system(line);
    (void)rc;  /* best-effort, exactly like the original (fails silently on PH) */
}

/* ------------------------------------------------------------------------- *
 *  Optional self-test / oracle mode.
 *    pineap-ng --dump-request <cmd> [args]  -> write the 4097-byte request to
 *    stdout (raw) instead of sending it.  Used by validate_bytes.sh.
 * ------------------------------------------------------------------------- */
static bool g_dump_mode = false;
static int dispatch(int argc, char **argv, const char *cmd);

/* send a prepared request (or dump it in --dump-request mode) */
static int do_send(const uint8_t *req, bool read_response)
{
    if (g_dump_mode) {
        fwrite(req, 1, PINEAP_CMD_LEN, stdout);
        fflush(stdout);
        return 0;
    }
    return pineap_send(req, read_response);
}

/* ------------------------------------------------------------------------- *
 *  Command handlers.  argc/argv here are RELATIVE to the command: argv[0]=cmd,
 *  argv[1..]=its arguments, argc = count including the command itself.
 * ------------------------------------------------------------------------- */

/* bool-opt commands: capture_ssids / beacon_responses / broadcast_pool /
 * logging / connect_notifications / disconnect_notifications */
static int cmd_optbool(int argc, char **argv, uint8_t opt)
{
    if (argc < 2) return err_missing(argv[0]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_bool(buf, opt, is_option_on(argv[1]));
    return do_send(buf, false);
}

static int cmd_karma(int argc, char **argv)
{
    if (argc < 2) return err_missing("karma");
    bool on = is_option_on(argv[1]);
    /* karma is a HYBRID: hostapd_cli AND socket setOpt(0) (RE §F.1).
     * NOTE: karma_enable now enables NORMAL karma only (no LOUD mode); LOUD is
     * opt-in via broadcast_pool below. Plain karma on no longer floods the air
     * with evil-twin responses for the user's real networks. */
    run_hostapd(on ? "karma_enable" : "karma_disable");
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_bool(buf, OPT_KARMA, on);
    return do_send(buf, false);
}

/* broadcast_pool is the EXPLICIT, user-driven switch for emitting the SSID pool.
 * It (a) drives pineapd's own pool beaconing via setOpt(OPT_BROADCAST_POOL) and
 * (b) is the ONLY place that turns mana's LOUD mode on/off. Keeping LOUD gated
 * here (instead of firing it unconditionally from karma on) is what stops the
 * stack from taking down nearby networks when it is merely idle/karma-on. */
static int cmd_broadcast_pool(int argc, char **argv)
{
    if (argc < 2) return err_missing("broadcast_pool");
    bool on = is_option_on(argv[1]);
    run_hostapd(on ? "karma_loud_enable" : "karma_loud_disable");
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_bool(buf, OPT_BROADCAST_POOL, on);
    return do_send(buf, false);
}

/* ssid_filter / mac_filter: hostapd_cli ONLY (no socket) (RE §F) */
static int cmd_ssid_filter(int argc, char **argv)
{
    if (argc < 2) return err_missing("ssid_filter");
    filter_mode_t m = filter_mode_from_str(argv[1]);
    run_hostapd(m == FILTER_DENY ? "karma_black" : "karma_white");
    return 0;
}
static int cmd_mac_filter(int argc, char **argv)
{
    if (argc < 2) return err_missing("mac_filter");
    filter_mode_t m = filter_mode_from_str(argv[1]);
    run_hostapd(m == FILTER_DENY ? "karma_mac_black" : "karma_mac_white");
    return 0;
}

static int cmd_beacon_interval(int argc, char **argv, uint8_t opt)
{
    if (argc < 2) return err_missing(argv[0]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_u32(buf, opt, inject_interval_from_str(argv[1]));
    return do_send(buf, false);
}

static int cmd_set_mac(int argc, char **argv, uint8_t opt)
{
    if (argc < 2) return err_missing(argv[0]);
    mac_t m;
    if (!mac_from_str(argv[1], &m)) {
        fprintf(stderr, "Invalid MAC address: %s\n", argv[1]);
        return 1;
    }
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_mac(buf, opt, &m);
    return do_send(buf, false);
}

static int cmd_ap_channel(int argc, char **argv)
{
    if (argc < 2) return err_missing("ap_channel");
    int ch = atoi(argv[1]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_opt_ap_channel(buf, (uint8_t)ch);   /* QUIRK: byte @ offset 6 */
    return do_send(buf, false);
}

static int cmd_add_del_ssid(int argc, char **argv, uint8_t cmd)
{
    if (argc < 2) return err_missing(argv[0]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_ssid(buf, cmd, argv[1]);
    return do_send(buf, false);
}

/* add_ssid_file / del_ssid_file: read one SSID per line, send one request each */
static int cmd_ssid_file(int argc, char **argv, uint8_t cmd)
{
    if (argc < 2) return err_missing(argv[0]);
    FILE *f = fopen(argv[1], "r");
    if (!f) {
        printf("Could not open file %s for reading.\n", argv[1]);
        return 1;
    }
    char line[512];
    int rc = 0;
    while (fgets(line, sizeof line, f)) {
        size_t n = strlen(line);
        while (n && (line[n-1] == '\n' || line[n-1] == '\r')) line[--n] = '\0';
        if (!n) continue;
        uint8_t buf[PINEAP_CMD_LEN];
        req_ssid(buf, cmd, line);
        int r = do_send(buf, false);
        if (r) rc = r;
    }
    fclose(f);
    return rc;
}

static int cmd_run_scan(int argc, char **argv)
{
    /* run_scan [duration] [frequencies]; needs 2 args */
    if (argc < 3) return err_requires("run_scan", 2, argc - 1);
    uint32_t dur = (uint32_t)strtoul(argv[1], NULL, 10);
    uint8_t freq = (uint8_t)atoi(argv[2]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_run_scan(buf, freq, dur);
    return do_send(buf, false);
}

static int cmd_deauth(int argc, char **argv)
{
    /* deauth [mac/source] [bssid/target] [channel] [multiplier]; 4 args */
    if (argc < 5) return err_requires("deauth", 4, argc - 1);
    mac_t src, tgt;
    if (!mac_from_str(argv[1], &src) || !mac_from_str(argv[2], &tgt)) {
        fprintf(stderr, "Invalid MAC address in deauth\n");
        return 1;
    }
    uint8_t ch = (uint8_t)atoi(argv[3]);
    uint32_t mult = (uint32_t)strtoul(argv[4], NULL, 10);
    uint8_t buf[PINEAP_CMD_LEN];
    req_deauth(buf, &src, &tgt, ch, mult);
    return do_send(buf, false);
}

static int cmd_handshake_start(int argc, char **argv)
{
    if (argc < 3) return err_requires("handshake_capture_start", 2, argc - 1);
    mac_t bssid;
    if (!mac_from_str(argv[1], &bssid)) {
        fprintf(stderr, "Invalid MAC address: %s\n", argv[1]);
        return 1;
    }
    uint8_t ch = (uint8_t)atoi(argv[2]);
    uint8_t buf[PINEAP_CMD_LEN];
    req_handshake_start(buf, &bssid, ch);
    return do_send(buf, false);
}

static int cmd_list_probes(int argc, char **argv)
{
    /* list_probes <mac>; if no mac given, original lists all (00:..:00) */
    mac_t m;
    memset(&m, 0, sizeof m);
    if (argc >= 2 && !mac_from_str(argv[1], &m)) {
        fprintf(stderr, "Invalid MAC address: %s\n", argv[1]);
        return 1;
    }
    uint8_t buf[PINEAP_CMD_LEN];
    req_list_probes(buf, &m);
    return do_send(buf, true);   /* has response */
}

static int cmd_inject(int argc, char **argv)
{
    /* inject [file] [channel] [multiplier] [delay]; 4 args.  delay NOT sent. */
    if (argc < 5) return err_requires("inject", 4, argc - 1);
    const char *file = argv[1];
    uint8_t ch = (uint8_t)atoi(argv[2]);
    uint32_t count = (uint32_t)strtoul(argv[3], NULL, 10);
    /* argv[4] = delay: parsed by original but discarded on the wire */

    /* read frame hex from file (or stdin for "-" / "/dev/stdin") */
    FILE *f;
    bool is_stdin = (!strcmp(file, "-") || !strcmp(file, "/dev/stdin"));
    f = is_stdin ? stdin : fopen(file, "r");
    if (!f) {
        printf("Failed to open: %s\n", file);
        return 1;
    }
    /* slurp */
    static char raw[1 << 20];
    size_t rn = fread(raw, 1, sizeof raw - 1, f);
    raw[rn] = '\0';
    if (!is_stdin) fclose(f);

    uint8_t frame[PINEAP_INJECT_MAX + 64];
    int flen = hex_decode(raw, frame, sizeof frame);
    if (flen < 0) {
        fprintf(stderr, "Invalid hex in frame file: %s\n", file);
        return 1;
    }
    if (flen > PINEAP_INJECT_MAX) {
        printf("Warning: frame of size %d bytes exceeds %d byte max. "
               "It will be truncated.\n", flen, PINEAP_INJECT_MAX);
        flen = PINEAP_INJECT_MAX;
    }

    uint8_t buf[PINEAP_CMD_LEN];
    req_inject(buf, ch, count, frame, (uint32_t)flen);
    return do_send(buf, false);  /* stdout empty on success */
}

/* ------------------------------------------------------------------------- *
 *  Dispatch table — strcmp chain, exactly like the original.
 * ------------------------------------------------------------------------- */
static int dispatch(int argc, char **argv, const char *cmd)
{
    uint8_t buf[PINEAP_CMD_LEN];

    /* local-only */
    if (!strcmp(cmd, "help"))    { print_usage("pineap"); return 0; }
    if (!strcmp(cmd, "version")) { print_version(); return 0; }

    /* simple socket commands (no args, no response) */
    if (!strcmp(cmd, "pause_scan"))   { req_simple(buf, CMD_PAUSE_SCAN);   return do_send(buf, false); }
    if (!strcmp(cmd, "unpause_scan")) { req_simple(buf, CMD_UNPAUSE_SCAN); return do_send(buf, false); }
    if (!strcmp(cmd, "stop_scan"))    { req_simple(buf, CMD_STOP_SCAN);    return do_send(buf, false); }
    if (!strcmp(cmd, "handshake_capture_stop")) { req_simple(buf, CMD_HANDSHAKE_CAPTURE_STOP); return do_send(buf, false); }
    if (!strcmp(cmd, "reload_config")) { req_simple(buf, CMD_RELOAD_CONFIG); return do_send(buf, false); }
    if (!strcmp(cmd, "pause"))   { req_simple(buf, CMD_PAUSE);   return do_send(buf, false); }
    if (!strcmp(cmd, "unpause")) { req_simple(buf, CMD_UNPAUSE); return do_send(buf, false); }
    if (!strcmp(cmd, "clear_ssids")) { req_simple(buf, CMD_CLEAR_SSIDS); return do_send(buf, false); }

    /* response commands */
    if (!strcmp(cmd, "list_ssids")) { req_simple(buf, CMD_LIST_SSIDS); return do_send(buf, true); }
    if (!strcmp(cmd, "get_status")) { req_simple(buf, CMD_GET_STATUS); return do_send(buf, true); }
    if (!strcmp(cmd, "list_probes")) return cmd_list_probes(argc, argv);

    /* ssid pool */
    if (!strcmp(cmd, "add_ssid")) return cmd_add_del_ssid(argc, argv, CMD_ADD_SSID);
    if (!strcmp(cmd, "del_ssid")) return cmd_add_del_ssid(argc, argv, CMD_DEL_SSID);
    if (!strcmp(cmd, "add_ssid_file")) return cmd_ssid_file(argc, argv, CMD_ADD_SSID);
    if (!strcmp(cmd, "del_ssid_file")) return cmd_ssid_file(argc, argv, CMD_DEL_SSID);

    /* scan / attack */
    if (!strcmp(cmd, "run_scan")) return cmd_run_scan(argc, argv);
    if (!strcmp(cmd, "deauth"))   return cmd_deauth(argc, argv);
    if (!strcmp(cmd, "handshake_capture_start")) return cmd_handshake_start(argc, argv);
    if (!strcmp(cmd, "inject"))   return cmd_inject(argc, argv);

    /* config bool options */
    if (!strcmp(cmd, "capture_ssids"))            return cmd_optbool(argc, argv, OPT_CAPTURE_SSIDS);
    if (!strcmp(cmd, "beacon_responses"))         return cmd_optbool(argc, argv, OPT_BEACON_RESPONSES);
    if (!strcmp(cmd, "broadcast_pool"))           return cmd_broadcast_pool(argc, argv);
    if (!strcmp(cmd, "logging"))                  return cmd_optbool(argc, argv, OPT_LOGGING);
    if (!strcmp(cmd, "connect_notifications"))    return cmd_optbool(argc, argv, OPT_CONNECT_NOTIFICATIONS);
    if (!strcmp(cmd, "disconnect_notifications")) return cmd_optbool(argc, argv, OPT_DISCONNECT_NOTIFICATIONS);

    /* config value options */
    if (!strcmp(cmd, "beacon_interval"))          return cmd_beacon_interval(argc, argv, OPT_BEACON_INTERVAL);
    if (!strcmp(cmd, "beacon_response_interval")) return cmd_beacon_interval(argc, argv, OPT_BEACON_RESPONSE_INTERVAL);
    if (!strcmp(cmd, "set_source")) return cmd_set_mac(argc, argv, OPT_SET_SOURCE);
    if (!strcmp(cmd, "set_target")) return cmd_set_mac(argc, argv, OPT_SET_TARGET);
    if (!strcmp(cmd, "ap_channel")) return cmd_ap_channel(argc, argv);

    /* hybrid / hostapd_cli-only */
    if (!strcmp(cmd, "karma"))       return cmd_karma(argc, argv);
    if (!strcmp(cmd, "ssid_filter")) return cmd_ssid_filter(argc, argv);
    if (!strcmp(cmd, "mac_filter"))  return cmd_mac_filter(argc, argv);

    /* rts_attack / rts_scan_id: listed in help but NO handler (RE §B.6) */
    return err_unknown(cmd);
}

int main(int argc, char **argv)
{
    const char *argv0 = argv[0];

    /* optional --dump-request front flag for oracle testing */
    if (argc >= 2 && !strcmp(argv[1], "--dump-request")) {
        g_dump_mode = true;
        argv++; argc--;        /* drop the flag */
        argv[0] = (char *)argv0;
    }

    if (argc < 2) {
        print_usage(argv0);
        return 1;
    }

    /* mode selection (RE §A.2): argv[1] containing "pineap.conf" => config mode */
    int base;               /* index of the command in argv */
    if (strstr(argv[1], "pineap.conf") != NULL) {
        if (argc < 3) { print_usage(argv0); return 1; }
        base = 2;           /* argv[1]=conf, argv[2]=cmd, argv[3..]=args */
    } else {
        base = 1;           /* legacy: argv[1]=cmd, argv[2..]=args */
    }

    const char *cmd = argv[base];
    /* build a command-relative argv for the handlers: cargv[0]=cmd, [1..]=args */
    int   cargc = argc - base;
    char **cargv = &argv[base];

    return dispatch(cargc, cargv, cmd);
}
