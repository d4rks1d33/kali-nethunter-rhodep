/*
 * db.c — SQLite databases with the EXACT schema reverse-engineered from the
 *        original pineapd (INFORME §G). The PHP panel reads these tables, so
 *        the CREATE TABLE statements must match column-for-column.
 *
 * Databases:
 *   recon.db           : aps, clients, scan_ids
 *   log.db (hostapd)   : log, tracking
 *   pineapple.db (ssid): ssids
 */
#include "pager.h"
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>

/* ---- EXACT schema literals from the original binary (INFORME §G) -------- */
static const char *SQL_CREATE_APS =
    "CREATE TABLE IF NOT EXISTS aps ( scan_id INT, ssid TEXT, bssid TEXT, "
    "encryption INT, hidden INT, channel INT, signal INT, wps INT, "
    "last_seen INT NOT NULL, UNIQUE(scan_id, ssid, bssid) ON CONFLICT REPLACE )";

static const char *SQL_CREATE_CLIENTS =
    "CREATE TABLE IF NOT EXISTS clients ( scan_id INT, mac TEXT, bssid TEXT, "
    "last_seen INT NOT NULL, UNIQUE(scan_id, mac) ON CONFLICT REPLACE )";

static const char *SQL_CREATE_SCAN_IDS =
    "CREATE TABLE IF NOT EXISTS scan_ids ( scan_id INT UNIQUE NOT NULL, "
    "date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP )";

static const char *SQL_CREATE_LOG =
    "CREATE TABLE IF NOT EXISTS log( log_type INT NOT NULL, mac TEXT NOT NULL, "
    "ssid TEXT, dups INT NOT NULL DEFAULT 0, "
    "created_at INT NOT NULL, updated_at INT NOT NULL )";

static const char *SQL_CREATE_TRACKING =
    "CREATE TABLE IF NOT EXISTS tracking ( id INT PRIMARY KEY, "
    "mac TEXT UNIQUE NOT NULL )";

static const char *SQL_CREATE_SSIDS =
    "CREATE TABLE IF NOT EXISTS ssids ( ssid TEXT UNIQUE NOT NULL, "
    "new_ssid INTEGER NOT NULL DEFAULT 1, "
    "created_at TIMESTAMP NOT NULL, "
    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "user_created INT NOT NULL DEFAULT 0 )";

static sqlite3 *g_recon = NULL;   /* aps, clients, scan_ids */
static sqlite3 *g_log   = NULL;   /* log, tracking          */
static sqlite3 *g_ssid  = NULL;   /* ssids                  */
static pthread_mutex_t g_db_lock = PTHREAD_MUTEX_INITIALIZER;

static sqlite3 *open_db(const char *path)
{
    sqlite3 *db = NULL;
    int rc = sqlite3_open_v2(path, &db,
                             SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL);
    if (rc != SQLITE_OK) {
        pineap_log("DB", "Failed to open sqlite3 database at %s: %s",
                   path, db ? sqlite3_errmsg(db) : "?");
        if (db) sqlite3_close(db);
        return NULL;
    }
    sqlite3_busy_timeout(db, 2000);
    /* WAL: readers (the recon list handler on the socket thread) do NOT block on
     * the writer (the capture thread inserting APs per packet). Without this, a
     * busy capture holds the DB and the firmware's "recon stable list" request
     * times out (i/o timeout) -> the AP list screen never populates. WAL lets
     * the list read proceed concurrently with capture inserts. */
    sqlite3_exec(db, "PRAGMA journal_mode=WAL", NULL, NULL, NULL);
    sqlite3_exec(db, "PRAGMA synchronous=NORMAL", NULL, NULL, NULL);
    sqlite3_exec(db, "PRAGMA wal_autocheckpoint=256", NULL, NULL, NULL);
    return db;
}

static int exec_sql(sqlite3 *db, const char *sql)
{
    char *err = NULL;
    int rc = sqlite3_exec(db, sql, NULL, NULL, &err);
    if (rc != SQLITE_OK) {
        pineap_log("DB", "sql error: %s", err ? err : "?");
        sqlite3_free(err);
        return -1;
    }
    return 0;
}

/* Does table `tbl` have a column named `col`?  Uses PRAGMA table_info. */
static bool table_has_column(sqlite3 *db, const char *tbl, const char *col)
{
    bool found = false;
    char sql[128];
    snprintf(sql, sizeof sql, "PRAGMA table_info(%s)", tbl);
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db, sql, -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *name = sqlite3_column_text(st, 1); /* col 1 = name */
            if (name && !strcmp((const char *)name, col)) { found = true; break; }
        }
        sqlite3_finalize(st);
    }
    return found;
}

/* Does table `tbl` exist at all? */
static bool table_exists(sqlite3 *db, const char *tbl)
{
    bool found = false;
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, tbl, -1, SQLITE_TRANSIENT);
        found = (sqlite3_step(st) == SQLITE_ROW);
        sqlite3_finalize(st);
    }
    return found;
}

/*
 * Migrate a legacy `ssids` table to the RE-confirmed schema.
 *
 * Old (stale) pineapple.db seeds shipped `CREATE TABLE ssids (ssid VARCHAR)`,
 * which is MISSING the new_ssid/created_at/updated_at/user_created columns the
 * daemon's addSSID INSERT writes.  On such a DB `CREATE TABLE IF NOT EXISTS`
 * is a no-op (the table already exists), so every add_ssid INSERT fails and
 * list_ssids returns empty (the exact G82 symptom).  Detect the old layout
 * (ssids present but no created_at column) and rebuild it, preserving any SSIDs
 * already stored.  Idempotent: on a correct schema this does nothing.
 */
static void migrate_ssids(sqlite3 *db)
{
    if (!table_exists(db, "ssids")) return;        /* CREATE will make it fresh */
    if (table_has_column(db, "ssids", "created_at")) return;  /* already new */

    pineap_log("DB", "migrating legacy 'ssids' table to current schema");
    /* Rebuild inside a transaction, carrying over existing ssid values. */
    exec_sql(db, "BEGIN");
    exec_sql(db, "ALTER TABLE ssids RENAME TO ssids_legacy");
    exec_sql(db, SQL_CREATE_SSIDS);
    /* Copy distinct, non-empty SSIDs; new columns take their DEFAULTs. */
    exec_sql(db,
        "INSERT OR IGNORE INTO ssids (ssid, created_at, updated_at) "
        "SELECT ssid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM ssids_legacy WHERE ssid IS NOT NULL AND ssid <> ''");
    exec_sql(db, "DROP TABLE ssids_legacy");
    exec_sql(db, "COMMIT");
}

int db_init_all(void)
{
    pager_config_t *c = config_get();

    g_recon = open_db(c->recon_db_path);
    g_log   = open_db(c->hostapd_db_path);
    g_ssid  = open_db(c->ssid_db_path);
    if (!g_recon || !g_log || !g_ssid) return -1;

    /* recon.db */
    exec_sql(g_recon, SQL_CREATE_APS);
    exec_sql(g_recon, SQL_CREATE_CLIENTS);
    exec_sql(g_recon, SQL_CREATE_SCAN_IDS);
    /* log.db (+tracking lives alongside the log db per RE §G.3) */
    exec_sql(g_log, SQL_CREATE_LOG);
    exec_sql(g_log, SQL_CREATE_TRACKING);
    /* pineapple.db */
    migrate_ssids(g_ssid);          /* upgrade a legacy ssids table in place */
    exec_sql(g_ssid, SQL_CREATE_SSIDS);
    return 0;
}

void db_close_all(void)
{
    pthread_mutex_lock(&g_db_lock);
    if (g_recon) { sqlite3_close(g_recon); g_recon = NULL; }
    if (g_log)   { sqlite3_close(g_log);   g_log   = NULL; }
    if (g_ssid)  { sqlite3_close(g_ssid);  g_ssid  = NULL; }
    pthread_mutex_unlock(&g_db_lock);
}

/* ------------------------------- recon.db ------------------------------- */
int recon_next_scan_id(void)
{
    if (!g_recon) return 0;
    pthread_mutex_lock(&g_db_lock);
    int next = 0;
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_recon,
            "SELECT scan_id FROM scan_ids ORDER BY scan_id DESC LIMIT 1",
            -1, &st, NULL) == SQLITE_OK) {
        if (sqlite3_step(st) == SQLITE_ROW)
            next = sqlite3_column_int(st, 0) + 1;
        sqlite3_finalize(st);
    }
    char sql[128];
    snprintf(sql, sizeof sql, "INSERT INTO scan_ids (scan_id) VALUES (%d)", next);
    exec_sql(g_recon, sql);
    pthread_mutex_unlock(&g_db_lock);
    return next;
}

void recon_write_ap(int scan_id, const char *ssid, const char *bssid,
                    int encryption, int hidden, int channel, int signal_,
                    int wps, long last_seen)
{
    if (!g_recon) return;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    const char *sql = "INSERT OR REPLACE INTO aps ( scan_id, ssid, bssid, "
        "encryption, hidden, channel, signal, wps, last_seen ) "
        "VALUES (?,?,?,?,?,?,?,?,?)";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_int (st, 1, scan_id);
        sqlite3_bind_text(st, 2, ssid ? ssid : "", -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, 3, bssid ? bssid : "", -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (st, 4, encryption);
        sqlite3_bind_int (st, 5, hidden);
        sqlite3_bind_int (st, 6, channel);
        sqlite3_bind_int (st, 7, signal_);
        sqlite3_bind_int (st, 8, wps);
        sqlite3_bind_int64(st, 9, last_seen);
        sqlite3_step(st);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
}

void recon_write_client(int scan_id, const char *mac, const char *bssid,
                        long last_seen)
{
    if (!g_recon) return;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    const char *sql = "INSERT OR REPLACE INTO clients ( scan_id, mac, bssid, "
                      "last_seen) VALUES (?,?,?,?)";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_int (st, 1, scan_id);
        sqlite3_bind_text(st, 2, mac ? mac : "", -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, 3, bssid ? bssid : "", -1, SQLITE_TRANSIENT);
        sqlite3_bind_int64(st, 4, last_seen);
        sqlite3_step(st);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
}

/* Count current-scan APs (scan_id<0 => all scans). */
int recon_count_aps(int scan_id)
{
    if (!g_recon) return 0;
    pthread_mutex_lock(&g_db_lock);
    int n = 0;
    sqlite3_stmt *st = NULL;
    const char *sql = (scan_id < 0)
        ? "SELECT count(*) FROM aps"
        : "SELECT count(*) FROM aps WHERE scan_id=?";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        if (scan_id >= 0) sqlite3_bind_int(st, 1, scan_id);
        if (sqlite3_step(st) == SQLITE_ROW) n = sqlite3_column_int(st, 0);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return n;
}

int recon_count_clients(int scan_id)
{
    if (!g_recon) return 0;
    pthread_mutex_lock(&g_db_lock);
    int n = 0;
    sqlite3_stmt *st = NULL;
    const char *sql = (scan_id < 0)
        ? "SELECT count(*) FROM clients"
        : "SELECT count(*) FROM clients WHERE scan_id=?";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        if (scan_id >= 0) sqlite3_bind_int(st, 1, scan_id);
        if (sqlite3_step(st) == SQLITE_ROW) n = sqlite3_column_int(st, 0);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return n;
}

/* Iterate AP rows (scan_id<0 => all), newest-seen first. Invokes cb per row;
 * stops early if cb returns false. Returns number of rows visited.
 *
 * NOTE (BUG A — recon list scroll crash) — FIXED, see handlers.c h_send_devices.
 * The firmware panic
 *     ui_recon_list.go:238 -> models_reconlist.go:142 (SelectNext)
 *       -> models_reconlist.go:204 (CurrentSelected)
 *     panic: index out of range [6] with length 6
 * is a FIRMWARE-side off-by-one: each recon page is a fixed 6-slot page (1
 * autoupdate control + 5 rows); (*ReconMenu).SelectNext indexes the current 6-slot
 * page with a running selectedIndex, so once the daemon hands it a list LONGER
 * than one page it chunks it internally and overflows pages[0].items[6] on the
 * DOWN that crosses a page boundary.
 *
 * An earlier note here concluded the daemon "cannot avoid it except by sending
 * <=5 APs".  That was WRONG: live wire capture of cmd=216 showed the firmware
 * ALSO issues its own paged fetches {page:N, size:5, rstable:false} as the user
 * scrolls.  The fix (handlers.c) is to answer the stable poll with ONE page
 * (size rows) + the true total and honour page*size on the rstable:false fetches,
 * so the firmware pages the whole list itself, one safe 6-slot page at a time —
 * no device is hidden and the crash cannot occur.  The clihash is also made
 * order-independent so the list FREEZES during scroll (no background rebuild).
 * Validated live on the G82 qemu firmware: scrolling the full multi-page list up
 * and down no longer panics/reboots the Pager.
 *
 * This iterator keeps the newest-first (last_seen DESC) order; the firmware
 * re-sorts by signal anyway, and the paging/offset is applied by dev_cb. */
int recon_each_ap(int scan_id, recon_dev_cb cb, void *ud)
{
    if (!g_recon || !cb) return 0;
    pthread_mutex_lock(&g_db_lock);
    int n = 0;
    sqlite3_stmt *st = NULL;
    const char *sql = (scan_id < 0)
        ? "SELECT bssid, ssid, channel, signal, encryption, hidden, wps, "
          "last_seen FROM aps ORDER BY last_seen DESC"
        : "SELECT bssid, ssid, channel, signal, encryption, hidden, wps, "
          "last_seen FROM aps WHERE scan_id=? ORDER BY last_seen DESC";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        if (scan_id >= 0) sqlite3_bind_int(st, 1, scan_id);
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *bssid = sqlite3_column_text(st, 0);
            const unsigned char *ssid  = sqlite3_column_text(st, 1);
            int channel = sqlite3_column_int(st, 2);
            int sig     = sqlite3_column_int(st, 3);
            int enc     = sqlite3_column_int(st, 4);
            int hidden  = sqlite3_column_int(st, 5);
            int wps     = sqlite3_column_int(st, 6);
            long last   = (long)sqlite3_column_int64(st, 7);
            n++;
            if (!cb(ud, bssid ? (const char *)bssid : "",
                        ssid  ? (const char *)ssid  : "",
                        channel, sig, enc, hidden, wps, last))
                break;
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return n;
}

/* Build a snapshot of per-AP client counts: one entry per bssid that currently
 * has >=1 associated client, as {bssid, count}.  Used by BUG B (per-AP client
 * count icon in the recon list).
 *
 * WHY A SNAPSHOT (not a per-device lookup): dev_cb runs INSIDE recon_each_ap(),
 * which holds g_db_lock for the whole AP iteration.  Querying the clients table
 * from dev_cb would re-lock the (non-recursive) g_db_lock -> self-deadlock ->
 * the cmd 216 frame never completes -> the firmware's recon stable-list read
 * times out and the list vanishes (this exact regression was seen and reverted
 * before).  So we PRE-COMPUTE all counts here, ONCE, with a single grouped
 * query, BEFORE recon_each_ap is called, and dev_cb then looks up its bssid in
 * this in-memory snapshot without touching the DB.
 *
 * Returns the number of entries written into *out (a malloc'd array the caller
 * must free with recon_client_counts_free); 0 and *out=NULL on empty/no-db. */
int recon_client_counts_by_bssid(int scan_id, recon_cli_count_t **out)
{
    *out = NULL;
    if (!g_recon) return 0;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    const char *sql = (scan_id < 0)
        ? "SELECT bssid, count(*) FROM clients "
          "WHERE bssid IS NOT NULL AND bssid<>'' GROUP BY bssid"
        : "SELECT bssid, count(*) FROM clients "
          "WHERE scan_id=? AND bssid IS NOT NULL AND bssid<>'' GROUP BY bssid";
    int n = 0, cap = 16;
    recon_cli_count_t *arr = malloc(sizeof(*arr) * cap);
    if (arr && sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        if (scan_id >= 0) sqlite3_bind_int(st, 1, scan_id);
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *bssid = sqlite3_column_text(st, 0);
            int cnt = sqlite3_column_int(st, 1);
            if (!bssid) continue;
            if (n == cap) { cap *= 2; arr = realloc(arr, sizeof(*arr) * cap); if (!arr) break; }
            strncpy(arr[n].bssid, (const char *)bssid, sizeof arr[n].bssid - 1);
            arr[n].bssid[sizeof arr[n].bssid - 1] = '\0';
            arr[n].count = cnt;
            n++;
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    *out = arr;
    return n;
}

void recon_client_counts_free(recon_cli_count_t *arr)
{
    free(arr);
}

/* Iterate client rows (scan_id<0 => all), newest-seen first. If `ap_bssid` is
 * non-NULL and non-empty, only clients associated with that AP are visited.
 * Invokes cb per row; stops early if cb returns false. Returns #rows visited. */
int recon_each_client(int scan_id, const char *ap_bssid,
                      recon_client_cb cb, void *ud)
{
    if (!g_recon || !cb) return 0;
    bool filter = (ap_bssid && ap_bssid[0]);
    pthread_mutex_lock(&g_db_lock);
    int n = 0;
    sqlite3_stmt *st = NULL;
    const char *sql;
    if (scan_id < 0 && !filter)
        sql = "SELECT mac, bssid, last_seen FROM clients ORDER BY last_seen DESC";
    else if (scan_id < 0 && filter)
        sql = "SELECT mac, bssid, last_seen FROM clients WHERE bssid=? "
              "ORDER BY last_seen DESC";
    else if (scan_id >= 0 && !filter)
        sql = "SELECT mac, bssid, last_seen FROM clients WHERE scan_id=? "
              "ORDER BY last_seen DESC";
    else
        sql = "SELECT mac, bssid, last_seen FROM clients WHERE scan_id=? "
              "AND bssid=? ORDER BY last_seen DESC";
    if (sqlite3_prepare_v2(g_recon, sql, -1, &st, NULL) == SQLITE_OK) {
        int idx = 1;
        if (scan_id >= 0) sqlite3_bind_int(st, idx++, scan_id);
        if (filter) sqlite3_bind_text(st, idx++, ap_bssid, -1, SQLITE_TRANSIENT);
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *mac   = sqlite3_column_text(st, 0);
            const unsigned char *bssid = sqlite3_column_text(st, 1);
            long last = (long)sqlite3_column_int64(st, 2);
            n++;
            if (!cb(ud, mac ? (const char *)mac : "",
                        bssid ? (const char *)bssid : "", last))
                break;
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return n;
}

/* -------------------------------- log.db -------------------------------- */
void log_write_entry(int log_type, const char *mac, const char *ssid,
                     long created_at, long updated_at)
{
    if (!g_log) return;
    pthread_mutex_lock(&g_db_lock);
    /* dedup by mac+ssid (INFORME §G.2) */
    sqlite3_stmt *st = NULL;
    int count = 0;
    if (sqlite3_prepare_v2(g_log,
            "SELECT count(*) FROM log WHERE mac=? AND ssid=?",
            -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, mac ? mac : "", -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, 2, ssid ? ssid : "", -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW) count = sqlite3_column_int(st, 0);
        sqlite3_finalize(st);
    }
    if (count > 0) {
        if (sqlite3_prepare_v2(g_log,
                "UPDATE log SET dups=dups+1, updated_at=? WHERE mac=? AND ssid=?",
                -1, &st, NULL) == SQLITE_OK) {
            sqlite3_bind_int64(st, 1, updated_at);
            sqlite3_bind_text (st, 2, mac ? mac : "", -1, SQLITE_TRANSIENT);
            sqlite3_bind_text (st, 3, ssid ? ssid : "", -1, SQLITE_TRANSIENT);
            sqlite3_step(st);
            sqlite3_finalize(st);
        }
    } else {
        if (sqlite3_prepare_v2(g_log,
                "INSERT INTO log(log_type, mac, ssid, dups, created_at, updated_at) "
                "VALUES (?,?,?,0,?,?)", -1, &st, NULL) == SQLITE_OK) {
            sqlite3_bind_int  (st, 1, log_type);
            sqlite3_bind_text (st, 2, mac ? mac : "", -1, SQLITE_TRANSIENT);
            sqlite3_bind_text (st, 3, ssid ? ssid : "", -1, SQLITE_TRANSIENT);
            sqlite3_bind_int64(st, 4, created_at);
            sqlite3_bind_int64(st, 5, updated_at);
            sqlite3_step(st);
            sqlite3_finalize(st);
        }
    }
    pthread_mutex_unlock(&g_db_lock);
}

/* append s + '\n' to a growing heap buffer */
static char *sb_append(char *buf, size_t *len, size_t *cap, const char *s)
{
    size_t sl = strlen(s);
    if (*len + sl + 2 > *cap) {
        *cap = (*len + sl + 2) * 2;
        buf = realloc(buf, *cap);
    }
    memcpy(buf + *len, s, sl);
    *len += sl;
    buf[(*len)++] = '\n';
    buf[*len] = '\0';
    return buf;
}

char *log_get_probes_all(void)
{
    char *buf = calloc(1, 64);
    size_t len = 0, cap = 64;
    if (!g_log) return buf;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    /* original: SELECT DISTINCT ssid,mac FROM log WHERE ssid != "" AND log_type = 0 */
    if (sqlite3_prepare_v2(g_log,
            "SELECT DISTINCT ssid,mac FROM log WHERE ssid != '' AND log_type = 0",
            -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *ssid = sqlite3_column_text(st, 0);
            const unsigned char *mac  = sqlite3_column_text(st, 1);
            char row[320];
            snprintf(row, sizeof row, "%s,%s", ssid ? (const char*)ssid : "",
                     mac ? (const char*)mac : "");
            buf = sb_append(buf, &len, &cap, row);
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return buf;
}

char *log_get_probes_mac(const char *mac)
{
    char *buf = calloc(1, 64);
    size_t len = 0, cap = 64;
    if (!g_log) return buf;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_log,
            "SELECT DISTINCT ssid FROM log WHERE mac=? AND ssid != '' AND log_type = 0",
            -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, mac ? mac : "", -1, SQLITE_TRANSIENT);
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *ssid = sqlite3_column_text(st, 0);
            buf = sb_append(buf, &len, &cap, ssid ? (const char*)ssid : "");
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return buf;
}

/* ------------------------------ pineapple.db ---------------------------- */
bool ssid_has(const char *ssid)
{
    if (!g_ssid || !ssid) return false;
    pthread_mutex_lock(&g_db_lock);
    bool found = false;
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_ssid, "SELECT ssid FROM ssids WHERE ssid=?",
                           -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, ssid, -1, SQLITE_TRANSIENT);
        found = (sqlite3_step(st) == SQLITE_ROW);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return found;
}

void ssid_add(const char *ssid)
{
    if (!g_ssid || !ssid || !*ssid) return;
    bool exists = ssid_has(ssid);
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    if (exists) {
        if (sqlite3_prepare_v2(g_ssid,
                "UPDATE ssids SET updated_at=CURRENT_TIMESTAMP, new_ssid=1 WHERE ssid=?",
                -1, &st, NULL) == SQLITE_OK) {
            sqlite3_bind_text(st, 1, ssid, -1, SQLITE_TRANSIENT);
            if (sqlite3_step(st) != SQLITE_DONE)
                pineap_log("DB", "ssid_add UPDATE failed: %s", sqlite3_errmsg(g_ssid));
            sqlite3_finalize(st);
        } else {
            pineap_log("DB", "ssid_add UPDATE prepare failed: %s", sqlite3_errmsg(g_ssid));
        }
    } else {
        if (sqlite3_prepare_v2(g_ssid,
                "INSERT INTO ssids(ssid, created_at, updated_at) "
                "VALUES(?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                -1, &st, NULL) == SQLITE_OK) {
            sqlite3_bind_text(st, 1, ssid, -1, SQLITE_TRANSIENT);
            if (sqlite3_step(st) != SQLITE_DONE)
                pineap_log("DB", "ssid_add INSERT failed: %s", sqlite3_errmsg(g_ssid));
            sqlite3_finalize(st);
        } else {
            pineap_log("DB", "ssid_add INSERT prepare failed: %s", sqlite3_errmsg(g_ssid));
        }
    }
    pthread_mutex_unlock(&g_db_lock);
}

void ssid_del(const char *ssid)
{
    if (!g_ssid || !ssid) return;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_ssid, "DELETE FROM ssids WHERE ssid=?",
                           -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, ssid, -1, SQLITE_TRANSIENT);
        sqlite3_step(st); sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
}

void ssid_clear(void)
{
    if (!g_ssid) return;
    pthread_mutex_lock(&g_db_lock);
    exec_sql(g_ssid, "DELETE FROM ssids;");
    pthread_mutex_unlock(&g_db_lock);
}

char *ssid_list(void)
{
    char *buf = calloc(1, 64);
    size_t len = 0, cap = 64;
    if (!g_ssid) return buf;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_ssid, "SELECT ssid FROM ssids",
                           -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *s = sqlite3_column_text(st, 0);
            buf = sb_append(buf, &len, &cap, s ? (const char*)s : "");
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return buf;
}

int ssid_get_all(char ***out)
{
    *out = NULL;
    if (!g_ssid) return 0;
    pthread_mutex_lock(&g_db_lock);
    sqlite3_stmt *st = NULL;
    int n = 0, cap = 8;
    char **arr = malloc(sizeof(char*) * cap);
    if (sqlite3_prepare_v2(g_ssid, "SELECT ssid FROM ssids",
                           -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *s = sqlite3_column_text(st, 0);
            if (n == cap) { cap *= 2; arr = realloc(arr, sizeof(char*) * cap); }
            arr[n++] = strdup(s ? (const char*)s : "");
        }
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    *out = arr;
    return n;
}

void ssid_free_all(char **arr, int n)
{
    if (!arr) return;
    for (int i = 0; i < n; i++) free(arr[i]);
    free(arr);
}

/* ------------------------------- tracking ------------------------------- */
bool tracking_is_tracked(const char *mac)
{
    if (!g_log || !mac) return false;
    pthread_mutex_lock(&g_db_lock);
    bool found = false;
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(g_log, "SELECT mac FROM tracking WHERE mac=?",
                           -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, mac, -1, SQLITE_TRANSIENT);
        found = (sqlite3_step(st) == SQLITE_ROW);
        sqlite3_finalize(st);
    }
    pthread_mutex_unlock(&g_db_lock);
    return found;
}
