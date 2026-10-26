/*
 * test_scan_sta.c — unit test for the native STA scan parser.
 *
 * Feeds a captured `iw dev wlan0 scan` buffer to scan_sta_parse_buffer() and
 * verifies the resulting rows in a throwaway recon.db (same schema/write path
 * the daemon uses). Build:
 *
 *   cc -O2 -std=gnu11 -o test_scan_sta test_scan_sta.c scan_sta.c db.c config.c \
 *      -lsqlite3 -lpthread
 *
 * Usage: ./test_scan_sta <iwscan.txt>
 */
#include "pineapd.h"
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static char *slurp(const char *path, size_t *outlen)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror("open"); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(n + 1);
    if (fread(buf, 1, n, f) != (size_t)n) { perror("read"); exit(2); }
    buf[n] = '\0';
    fclose(f);
    if (outlen) *outlen = (size_t)n;
    return buf;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s <iwscan.txt>\n", argv[0]); return 1; }

    /* point the recon db at a fresh temp file */
    const char *dbpath = "/tmp/opencode/test_recon.db";
    remove(dbpath);
    pineap_config_t *c = config_get();
    strncpy(c->recon_db_path, dbpath, sizeof c->recon_db_path - 1);
    /* db_init_all also opens log/ssid dbs; point them at temp too */
    strncpy(c->hostapd_db_path, "/tmp/opencode/test_log.db", sizeof c->hostapd_db_path - 1);
    strncpy(c->ssid_db_path, "/tmp/opencode/test_ssid.db", sizeof c->ssid_db_path - 1);
    remove(c->hostapd_db_path); remove(c->ssid_db_path);

    if (db_init_all() != 0) { fprintf(stderr, "db_init_all failed\n"); return 1; }

    char *text = slurp(argv[1], NULL);
    int scan_id = 42;
    long now = time(NULL);
    int written = scan_sta_parse_buffer(text, scan_id, now);
    printf("parser wrote %d APs\n", written);
    free(text);
    db_close_all();

    /* reopen the recon db read-only and dump what we stored */
    sqlite3 *db = NULL;
    if (sqlite3_open(dbpath, &db) != SQLITE_OK) { fprintf(stderr, "reopen fail\n"); return 1; }

    const char *q = "SELECT bssid, ssid, channel, signal, encryption, hidden, wps "
                    "FROM aps WHERE scan_id=? ORDER BY channel, bssid";
    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db, q, -1, &st, NULL);
    sqlite3_bind_int(st, 1, scan_id);

    int rows = 0;
    int enc_nonzero = 0, wps_cnt = 0, hidden_cnt = 0;
    printf("%-17s  %-20s  %3s  %5s  %3s  %3s  %3s\n",
           "BSSID", "SSID", "CH", "SIG", "ENC", "HID", "WPS");
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char *bssid = (const char*)sqlite3_column_text(st, 0);
        const char *ssid  = (const char*)sqlite3_column_text(st, 1);
        int ch   = sqlite3_column_int(st, 2);
        int sig  = sqlite3_column_int(st, 3);
        int enc  = sqlite3_column_int(st, 4);
        int hid  = sqlite3_column_int(st, 5);
        int wps  = sqlite3_column_int(st, 6);
        printf("%-17s  %-20s  %3d  %5d  %3d  %3d  %3d\n",
               bssid, ssid ? ssid : "", ch, sig, enc, hid, wps);
        rows++;
        if (enc) enc_nonzero++;
        if (wps) wps_cnt++;
        if (hid) hidden_cnt++;
    }
    sqlite3_finalize(st);

    /* count truly distinct channels (the ORDER BY groups them) */
    sqlite3_prepare_v2(db, "SELECT COUNT(DISTINCT channel) FROM aps WHERE scan_id=?", -1, &st, NULL);
    sqlite3_bind_int(st, 1, scan_id);
    int chans = 0;
    if (sqlite3_step(st) == SQLITE_ROW) chans = sqlite3_column_int(st, 0);
    sqlite3_finalize(st);
    sqlite3_close(db);

    printf("\n--- summary ---\n");
    printf("rows in aps       : %d\n", rows);
    printf("distinct channels : %d\n", chans);
    printf("secured (enc!=0)  : %d\n", enc_nonzero);
    printf("wps APs           : %d\n", wps_cnt);
    printf("hidden APs        : %d\n", hidden_cnt);

    int ok = 1;
    if (rows != written) { printf("FAIL: rows(%d) != written(%d)\n", rows, written); ok = 0; }
    if (chans < 2)       { printf("FAIL: expected APs across multiple channels\n"); ok = 0; }
    if (rows < 5)        { printf("FAIL: suspiciously few rows\n"); ok = 0; }
    printf(ok ? "\nTEST PASS\n" : "\nTEST FAIL\n");
    return ok ? 0 : 1;
}
