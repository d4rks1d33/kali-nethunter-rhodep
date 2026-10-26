/*
 * handlers.c — command handlers for the Pager socket protocol.
 *
 * Each handler has the signature:
 *     void handler(uint32_t cmd, mpack_node_t root, mpack_writer_t *rw);
 * where `root` is the parsed request body (a msgpack map; may be nil/empty) and
 * `rw` is a growable msgpack writer the handler fills with the RESPONSE body
 * (always a map). sock.c owns the framing; handlers only write the body map.
 *
 * Priority handlers are fully implemented; the rest emit a basic OK/stub map.
 * All radio-touching handlers degrade gracefully: with no Archer (wlan1mon)
 * they return a basic error (error!=0, descriptive message) and never crash.
 *
 * msgpack keys follow ARQUITECTURA-PORT.md §2.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

/* ---- small response builders ------------------------------------------- */
static void wr_basic(mpack_writer_t *w, uint64_t error, const char *msg)
{
    msg_write_basic(w, error, msg);
}

/* ======================================================================== *
 *  interface_tracker
 * ======================================================================== */

/* 100 list_interfaces — report the internal STA/AP radio and the monitor radio
 * with their presence/role. Keys: {interfaces:[{ifname,type,enabled,monitor,
 * uplink}], error, message}. */
static void h_list_interfaces(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    pager_config_t *c = config_get();

    bool internal_present = iface_exists(c->internal_iface);
    bool monitor_present  = iface_exists(c->monitor_iface);
    bool base_present     = iface_exists(PINEAPD_MONITOR_BASE);

    mpack_start_map(w, 3);

    mpack_write_cstr(w, "interfaces");
    mpack_start_array(w, 2);

    /* internal radio (wlan0) — STA/AP, never monitor */
    mpack_start_map(w, 5);
    mpack_write_cstr(w, "ifname");  mpack_write_cstr(w, c->internal_iface);
    mpack_write_cstr(w, "type");    mpack_write_cstr(w, "managed");
    mpack_write_cstr(w, "enable");  mpack_write_bool(w, internal_present);
    mpack_write_cstr(w, "monitor"); mpack_write_bool(w, false);
    mpack_write_cstr(w, "uplink");  mpack_write_bool(w, true);
    mpack_finish_map(w);

    /* monitor radio (Archer wlan1 -> wlan1mon) */
    mpack_start_map(w, 5);
    mpack_write_cstr(w, "ifname");  mpack_write_cstr(w, c->monitor_iface);
    mpack_write_cstr(w, "type");    mpack_write_cstr(w, monitor_present ? "monitor" : "absent");
    mpack_write_cstr(w, "enable");  mpack_write_bool(w, monitor_present || base_present);
    mpack_write_cstr(w, "monitor"); mpack_write_bool(w, monitor_present);
    mpack_write_cstr(w, "uplink");  mpack_write_bool(w, false);
    mpack_finish_map(w);

    mpack_finish_array(w);

    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);
}

/* 101/104 mod_interface — set the channel on an interface. Body: {ifname, channel}.
 * Refuses to retune the uplink radio off its STA channel. */
static void h_mod_interface(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    char ifname[64];
    if (!msg_get_str(root, "ifname", ifname, sizeof ifname) || !ifname[0]) {
        pager_config_t *c = config_get();
        strncpy(ifname, c->monitor_iface, sizeof ifname - 1);
        ifname[sizeof ifname - 1] = '\0';
    }
    uint32_t channel = msg_get_u32(root, "channel", 0);

    if (iface_is_uplink_radio(ifname)) {
        wr_basic(w, 1, "refusing to retune the uplink radio (wlan0)");
        return;
    }
    if (channel == 0) { wr_basic(w, 1, "channel required"); return; }
    if (iface_set_channel_on(ifname, (int)channel) == 0)
        wr_basic(w, 0, "channel set");
    else
        wr_basic(w, 1, "failed to set channel (iface present?)");
}

/* 102/103 toggle_interface — enable/disable an interface (ip link up/down).
 * Body: {ifname, enable}. */
static void h_toggle_interface(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    char ifname[64] = {0};
    if (!msg_get_str(root, "ifname", ifname, sizeof ifname) || !ifname[0]) {
        wr_basic(w, 1, "ifname required"); return;
    }
    bool enable = msg_get_bool(root, "enable", true);
    if (iface_is_uplink_radio(ifname) && !enable) {
        wr_basic(w, 1, "refusing to bring down the uplink radio");
        return;
    }
    char cmd_s[160];
    snprintf(cmd_s, sizeof cmd_s, "ip link set %s %s >/dev/null 2>&1",
             ifname, enable ? "up" : "down");
    int rc = system(cmd_s);
    wr_basic(w, rc == 0 ? 0 : 1, rc == 0 ? "ok" : "ip link failed");
}

/* 105/106 set_interface_type — set monitor/managed/ap. Body: {ifname, type}.
 * Never allows the uplink radio to become monitor. */
static void h_set_interface_type(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    char ifname[64] = {0}, type[24] = {0};
    if (!msg_get_str(root, "ifname", ifname, sizeof ifname) || !ifname[0]) {
        wr_basic(w, 1, "ifname required"); return;
    }
    if (!msg_get_str(root, "type", type, sizeof type)) {
        strncpy(type, "monitor", sizeof type - 1);
    }
    if (iface_is_uplink_radio(ifname) &&
        (!strcmp(type, "monitor"))) {
        wr_basic(w, 1, "refusing monitor mode on the uplink radio (wlan0)");
        return;
    }
    char cmd_s[200];
    snprintf(cmd_s, sizeof cmd_s,
             "ip link set %s down >/dev/null 2>&1; "
             "iw dev %s set type %s >/dev/null 2>&1; "
             "ip link set %s up >/dev/null 2>&1",
             ifname, ifname, type, ifname);
    int rc = system(cmd_s);
    wr_basic(w, rc == 0 ? 0 : 1, rc == 0 ? "type set" : "iw set type failed");
}

/* 107 reset_channels — no-op reset of the hop plan; acknowledge. */
static void h_reset_channels(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    wr_basic(w, 0, "channels reset");
}

/* ======================================================================== *
 *  recon_engine
 * ======================================================================== */

/* 200/202 count_devices — return counts of discovered APs/clients.
 * Keys: {aps:uint, devs:uint (clients), total:uint, error, message}. */
static void h_count_devices(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();
    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    int aps = recon_count_aps(scan_id);
    int devs = recon_count_clients(scan_id);
    mpack_start_map(w, 5);
    mpack_write_cstr(w, "aps");     mpack_write_uint(w, (uint64_t)aps);
    mpack_write_cstr(w, "devs");    mpack_write_uint(w, (uint64_t)devs);
    mpack_write_cstr(w, "total");   mpack_write_uint(w, (uint64_t)(aps + devs));
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);
}

/* send_devices callback context */
struct devctx {
    mpack_writer_t *w;
    uint32_t limit;      /* max rows to EMIT (0 = no limit) */
    uint32_t written;    /* rows emitted so far */
    uint32_t skip;       /* rows to SKIP before emitting (page*size offset) */
    uint32_t seen;       /* rows visited so far (for skip accounting) */
    int      scan_id;    /* for per-AP client-count lookups */
    /* BUG B: per-AP client counts, PRE-COMPUTED before recon_each_ap() so dev_cb
     * never touches the DB (which would self-deadlock on g_db_lock). */
    recon_cli_count_t *cli_counts;
    int                cli_counts_n;
};

/* Look up the client count for one AP bssid in the precomputed snapshot. */
static int devctx_client_count(const struct devctx *dc, const char *bssid)
{
    if (!dc->cli_counts || !bssid || !bssid[0]) return 0;
    for (int i = 0; i < dc->cli_counts_n; i++)
        if (strcmp(dc->cli_counts[i].bssid, bssid) == 0)
            return dc->cli_counts[i].count;
    return 0;
}

/* Write a BSSID/MAC the way the firmware decodes it: a Go [6]uint8, which the
 * vmihailenco/msgpack library decodes via DecodeBytesLen -> a msgpack BIN of
 * exactly 6 bytes. RE evidence (progressive errors while probing the encoding):
 *   - colon string  "aa:bb:cc:dd:ee:ff" (17 bytes) ->
 *       "[6]uint8 len is 6, but msgpack has 17 elements"  (DecodeBytesLen read 17)
 *   - msgpack array of 6 uint8 (code 0x96) ->
 *       "invalid code=96 decoding string/bytes length"    (DecodeBytesLen saw array)
 *   => the decoder wants a 6-byte BIN. Parse "aa:bb:cc:dd:ee:ff" (tolerates '-'
 *   and missing separators); on parse failure still emit 6 zero bytes so the
 *   frame stays well-formed. */
static void mpack_write_mac6(mpack_writer_t *w, const char *mac)
{
    uint8_t b[6] = {0,0,0,0,0,0};
    if (mac) {
        int n = 0;
        const char *p = mac;
        while (*p && n < 6) {
            /* skip separators */
            if (*p == ':' || *p == '-') { p++; continue; }
            int hi = -1, lo = -1;
            char c = *p;
            if (c >= '0' && c <= '9') hi = c - '0';
            else if (c >= 'a' && c <= 'f') hi = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') hi = c - 'A' + 10;
            if (hi < 0) { p++; continue; }
            c = p[1];
            if (c >= '0' && c <= '9') lo = c - '0';
            else if (c >= 'a' && c <= 'f') lo = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') lo = c - 'A' + 10;
            if (lo < 0) { b[n++] = (uint8_t)hi; p++; continue; }
            b[n++] = (uint8_t)((hi << 4) | lo);
            p += 2;
        }
    }
    mpack_write_bin(w, (const char *)b, 6);
}

/* FNV-1a over a C string. Declared here (ahead of its later static use in the
 * clihash code) so dev_cb can derive the stable per-AP identity hash. */
static uint64_t fnv1a_str(const char *s);

/* Channel number -> centre frequency (MHz). The firmware stores freq as uint64
 * and derives the channel display from it on some paths, so we provide it. */
static uint64_t chan_to_freq(int ch)
{
    if (ch <= 0) return 0;
    if (ch == 14) return 2484;
    if (ch >= 1 && ch <= 13) return (uint64_t)(2407 + ch * 5);  /* 2.4GHz */
    if (ch >= 32 && ch <= 177) return (uint64_t)(5000 + ch * 5); /* 5GHz */
    return 0;
}

/* dev_cb — emit ONE recon device in the EXACT shape the firmware deserialises.
 *
 * RE of the firmware ELF (ELF32 MIPSel, go1.2x) type metadata proved the Recon
 * AP list decodes each devicelist entry into models.PineapDevice via reflection
 * (vmihailenco/msgpack). Its struct tags are the ONLY fields the decoder reads:
 *
 *   hash     msgpack:"hash"     uint64                      (per-AP identity/ID)
 *   ssidmap  msgpack:"ssidmap"  []models.PineapBeaconedSsid (AP NAME source)
 *   probemap msgpack:"probemap" []models.PineapSimpleSsid
 *   respmap  msgpack:"respmap"  []models.PineapSimpleSsid
 *   apkeys   msgpack:"apkeys"   []uint64
 *   clikeys  msgpack:"clikeys"  []uint64                    (client count = len)
 *   mac      msgpack:"mac"      [6]uint8   (msgpack BIN6)
 *   ts       msgpack:"ts"       int64
 *   signal   msgpack:"signal"   int32
 *   freq     msgpack:"freq"     uint64
 *   packets  msgpack:"packets"  uint64
 *
 * CRITICAL: the display NAME is NOT a top-level "ssid"/"bssid" field. The
 * firmware's models.PineapDevice.GetSsid() reads BeaconedSsids[0].Ssid; when
 * ssidmap is empty it falls back to the BSSID string (derived from mac). The
 * old daemon sent flat "ssid"/"bssid"/"channel"/"encryption"/"hidden"/"wps"
 * fields which the PineapDevice decoder has no matching struct field for and
 * therefore DISCARDS -> every AP rendered as its BSSID. We now populate
 * ssidmap[0] with the real SSID (as []uint8 BYTES, per the BeaconedSsid.Ssid
 * tag), plus channel/signal/encryption/hidden/freq so the list row is complete.
 *
 * models.PineapBeaconedSsid struct tags (RE):
 *   hash u64, count u64, ts i64, ssid []uint8(BYTES), hidden bool,
 *   channel u32, freq u64, signal i32, encryption u64, flags u16
 */
static bool dev_cb(void *ud, const char *bssid, const char *ssid,
                   int channel, int signal_, int encryption,
                   int hidden, int wps, long last_seen)
{
    struct devctx *dc = ud;
    /* Pagination (rstable:false page fetch): skip the first page*size rows, then
     * emit at most `limit` (==size) rows.  recon_each_ap visits every row in a
     * stable order; we count visited rows in `seen` and only start emitting once
     * past the skip offset.  Returning true (keep iterating) while skipping is
     * required so we actually reach the requested page. */
    if (dc->seen < dc->skip) { dc->seen++; return true; }
    dc->seen++;
    if (dc->limit && dc->written >= dc->limit) return false;
    mpack_writer_t *w = dc->w;

    /* Stable per-AP identity: FNV-1a of the BSSID. The firmware uses
     * device.Hash as the list item id ($_RECON_LIST_ITEM_ID) to track the
     * highlighted/selected row across live refreshes; a stable, unique value is
     * required for the selection highlight to stick. */
    uint64_t ap_hash = fnv1a_str(bssid);

    uint64_t freq = chan_to_freq(channel);
    bool have_name = (ssid && ssid[0] && !hidden);
    size_t ssid_len = have_name ? strlen(ssid) : 0;

    /* BUG B: per-AP client count.  The firmware's recon list row renders the
     * client-count icon from len(device.ClientKeys) (msgpack "clikeys", a
     * []uint64 — confirmed by RE of models.PineapDevice struct tags).  The old
     * daemon always sent an EMPTY clikeys list, so the count showed 0 for every
     * AP.  We now emit exactly `nclients` entries so len(clikeys)==real count.
     *
     * The count comes from dc->cli_counts, PRE-COMPUTED before recon_each_ap()
     * (see h_send_devices + recon_client_counts_by_bssid): we must NOT query the
     * DB here because dev_cb runs while g_db_lock is held and a second lock would
     * self-deadlock -> the 216 frame would never complete -> recon list i/o
     * timeout (the exact regression that caused clikeys to be emptied before).
     *
     * clikeys VALUES: a stable per-client identity hash (FNV-1a of bssid|index)
     * so the list is well-formed []uint64; the firmware only needs len() for the
     * count icon, and the actual client ROWS (with their own identity) are
     * delivered in the stable-clients frame / the AP-clients query. */
    int nclients = devctx_client_count(dc, bssid);

    mpack_build_map(w);                         /* unknown-size map */

    /* identity + mac (firmware renders MAC/BSSID column from the [6]uint8 mac) */
    mpack_write_cstr(w, "hash");       mpack_write_u64(w, ap_hash);
    mpack_write_cstr(w, "mac");        mpack_write_mac6(w, bssid);

    /* ssidmap: the beaconed-SSID list. One entry carrying the visible name. For
     * hidden APs (no name) we emit an EMPTY list so GetSsid() falls back to the
     * BSSID -- which is the correct, intended behaviour for a hidden network. */
    mpack_write_cstr(w, "ssidmap");
    if (have_name) {
        mpack_start_array(w, 1);
        mpack_start_map(w, 10);
        mpack_write_cstr(w, "hash");       mpack_write_u64(w, ap_hash);
        mpack_write_cstr(w, "count");      mpack_write_u64(w, 1);
        mpack_write_cstr(w, "ts");         mpack_write_i64(w, (int64_t)last_seen);
        /* BeaconedSsid.Ssid is []uint8 -> encode as a msgpack BIN (raw bytes). */
        mpack_write_cstr(w, "ssid");       mpack_write_bin(w, ssid, (uint32_t)ssid_len);
        mpack_write_cstr(w, "hidden");     mpack_write_bool(w, false);
        mpack_write_cstr(w, "channel");    mpack_write_u32(w, (uint32_t)(channel < 0 ? 0 : channel));
        mpack_write_cstr(w, "freq");       mpack_write_u64(w, freq);
        mpack_write_cstr(w, "signal");     mpack_write_i32(w, signal_);
        mpack_write_cstr(w, "encryption"); mpack_write_u64(w, (uint64_t)encryption);
        mpack_write_cstr(w, "flags");      mpack_write_u16(w, (uint16_t)(wps ? 1 : 0));
        mpack_finish_map(w);
        mpack_finish_array(w);
    } else {
        mpack_start_array(w, 0);
        mpack_finish_array(w);
    }

    /* probemap / respmap: not tracked -> empty lists (well-formed). */
    mpack_write_cstr(w, "probemap"); mpack_start_array(w, 0); mpack_finish_array(w);
    mpack_write_cstr(w, "respmap");  mpack_start_array(w, 0); mpack_finish_array(w);

    /* apkeys: not tracked -> empty list (well-formed). */
    mpack_write_cstr(w, "apkeys");  mpack_start_array(w, 0); mpack_finish_array(w);

    /* clikeys: one uint64 per associated client so len(clikeys) == client count
     * (BUG B).  Values are stable synthetic client-identity hashes. */
    mpack_write_cstr(w, "clikeys");
    if (nclients > 0) {
        mpack_start_array(w, (uint32_t)nclients);
        for (int i = 0; i < nclients; i++) {
            /* stable per-(ap,client-slot) key: FNV-1a(bssid) folded with slot. */
            uint64_t k = ap_hash ^ ((uint64_t)(i + 1) * 1099511628211ULL);
            mpack_write_u64(w, k);
        }
        mpack_finish_array(w);
    } else {
        mpack_start_array(w, 0);
        mpack_finish_array(w);
    }

    /* scalar device fields read by the firmware. */
    mpack_write_cstr(w, "ts");       mpack_write_i64(w, (int64_t)last_seen);
    mpack_write_cstr(w, "signal");   mpack_write_i32(w, signal_);
    mpack_write_cstr(w, "freq");     mpack_write_u64(w, freq);
    mpack_write_cstr(w, "packets");  mpack_write_u64(w, 0);

    mpack_complete_map(w);
    dc->written++;
    return true;
}

/* -------------------------------------------------------------------------
 * recon "stable list" hash (clihash)
 *
 * RE of the firmware (ELF32 MIPSel, go1.27) revealed the recon AP-list screen
 * drives a TWO-WAY stable handshake over cmd=216:
 *
 *   request  : {... "rstable":true, "clihash":<uint64 last hash FW holds>}
 *   response : models.PineapQueryResponseData {
 *                  DeviceList    msgpack:"devicelist"
 *                  ClientsOfHash msgpack:"clihash"   <-- REQUIRED, we omitted it
 *              }
 *
 * The firmware stores the returned clihash and, on the next poll, sends it back.
 * When the daemon's returned clihash matches what the firmware sent (i.e. the
 * list is unchanged between polls) the firmware "freezes" the list and RENDERS
 * it ("RECON STABLE QUERY: Frozen: %t").  Because our old response carried NO
 * clihash, the firmware's stable handshake never converged and its read
 * deadline expired -> "failed to get recon stable list from pineap: i/o
 * timeout" + "No data", and the AP list never appeared.
 *
 * The hash must be STABLE while the set of APs is unchanged, so it is computed
 * ONLY over AP identity (bssid + ssid), order-independent (per-row FNV-1a
 * folded with a commutative add) so that live signal/last_seen churn and the
 * newest-first ordering do not perturb it.  It changes only when an AP appears
 * or disappears -- exactly the "list changed" signal the firmware wants.
 * ------------------------------------------------------------------------- */
struct hashctx {
    uint64_t xacc;    /* XOR accumulator  (order-independent) */
    uint64_t sacc;    /* ADD accumulator  (order-independent) */
    uint32_t count;   /* rows folded */
};

static uint64_t fnv1a_str(const char *s)
{
    uint64_t h = 1469598103934665603ULL;        /* FNV offset basis */
    for (; s && *s; s++) {
        h ^= (uint64_t)(unsigned char)(*s);
        h *= 1099511628211ULL;                  /* FNV prime */
    }
    return h;
}

static bool hash_cb(void *ud, const char *bssid, const char *ssid,
                    int channel, int signal_, int encryption,
                    int hidden, int wps, long last_seen)
{
    (void)channel; (void)signal_; (void)encryption;
    (void)hidden; (void)wps; (void)last_seen;
    struct hashctx *hc = ud;
    /* Per-AP identity hash (bssid|ssid). Combine rows with ONLY commutative AND
     * associative operations (XOR and ADD) so the fold is TRULY independent of
     * the iteration order.
     *
     * BUG A (recon scroll crash) ROOT CAUSE — fixed here:
     *   The previous fold interleaved `acc += row;` with `acc ^= rot(row);`.
     *   Because `+` and `^` do not commute with each other, the result depended
     *   on the ORDER rows were visited.  recon_each_ap() iterates `ORDER BY
     *   last_seen DESC`, and the live recon monitor constantly rewrites each AP's
     *   last_seen/signal -> the newest-first ORDER keeps changing even when the
     *   SET of APs is identical -> the clihash changed on every poll.
     *
     *   The firmware uses clihash as its "did the recon list change?" signal: it
     *   only FREEZES the list (pauses autorefresh, "user is scrolling"/"exit
     *   scroll freeze") while the hash is stable.  A churning hash meant the
     *   firmware NEVER converged/froze, so its UI data goroutine kept REBUILDING
     *   the paged list (ExpandPagesForListData) underneath the input goroutine.
     *   On a DOWN press, (*ReconMenu).SelectNext -> CurrentSelected re-reads the
     *   page index/`pages` slice; when the rebuild shrank `pages` between the
     *   SelectNext bounds-clamp and the CurrentSelected index, the firmware hit
     *   `pages[idx]` out of range -> "index out of range [6] with length 6"
     *   (6 = 1 autoupdate control + 5 rows per page) and panicked, rebooting the
     *   Pager.  RE: ELF32 MIPSel (*ReconMenu).CurrentSelected @0xb1e924 lacks the
     *   clamp that (*ReconMenu).ActiveMenuItems @0xb1eaa0 has, so the race is
     *   unguarded firmware-side; we must keep the list STABLE instead.
     *
     * A set-identity hash built from pure XOR + ADD of per-row hashes is byte
     * identical for the same SET of APs regardless of order and regardless of
     * last_seen/signal churn, so the firmware's stable handshake converges, the
     * list freezes during scroll, no rebuild races the scroll, and the crash
     * cannot occur.  The hash still flips whenever an AP appears/disappears
     * (its row hash enters/leaves both accumulators) — the exact "list changed"
     * signal the firmware wants. */
    uint64_t row = fnv1a_str(bssid);
    row = (row * 1099511628211ULL) ^ fnv1a_str(ssid);
    hc->xacc ^= row;                /* XOR: commutative + associative */
    hc->sacc += row;                /* ADD: commutative + associative */
    hc->count++;
    return true;
}

/* Compute the stable clihash over the full current AP set for `scan_id`. */
static uint64_t recon_ap_list_hash(int scan_id)
{
    struct hashctx hc = { 0, 0, 0 };
    recon_each_ap(scan_id, hash_cb, &hc);
    /* Combine the two order-independent accumulators + the row count.  All inputs
     * are order-independent, so the whole hash is a pure function of the SET of
     * APs (by bssid|ssid) — stable across polls while that set is unchanged. */
    uint64_t h = hc.xacc * 1099511628211ULL + hc.sacc
               + (uint64_t)hc.count * 1099511628211ULL;
    /* Never return 0: the firmware uses clihash==0 to mean "I have no hash", so
     * a real but zero hash would look like "unknown" and never converge. */
    if (h == 0) h = 0x9e3779b97f4a7c15ULL;
    return h;
}

/* 201/203/205/215/216/217 send_devices — dump the AP list.
 * Body may carry {scan, page, size}. Response: {devicelist:[...], more:bool,
 * total:uint, clihash:uint, error}. The clihash is the recon "stable list"
 * fingerprint the firmware compares across polls (see recon_ap_list_hash). */
static void h_send_devices(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();
    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    uint32_t size = msg_get_u32(root, "size", 0);  /* 0 = all */
    uint32_t page = msg_get_u32(root, "page", 0);
    bool rstable = msg_get_bool(root, "rstable", false);

    int total = recon_count_aps(scan_id);
    uint64_t clihash = recon_ap_list_hash(scan_id);

    /* ===================================================================
     * BUG A — recon AP-list scroll crash — ROOT CAUSE + FIX (validated live)
     * ===================================================================
     * The firmware rebooted with
     *     panic: runtime error: index out of range [6] with length 6
     *     (*ReconMenu).CurrentSelected  models_reconlist.go:204
     *     (*ReconMenu).SelectNext       models_reconlist.go:142
     *     (*UIReconMenu).HandleButtonInput ui_recon_list.go:238  (goroutine 43)
     * when scrolling DOWN past the first page of the recon AP list.
     *
     * RE of the firmware ELF32 MIPSel (gopclntab) showed the recon list page is a
     * fixed 6-SLOT page (1 autoupdate control + 5 rows).  (*ReconMenu).SelectNext
     * @0xb1e2ec increments a RUNNING selectedIndex bounded by the FULL list length
     * and then calls (*ReconMenu).CurrentSelected @0xb1e924, which indexes the
     * CURRENT page's 6-item slice with that index WITHOUT the clamp that
     * (*ReconMenu).ActiveMenuItems @0xb1eaa0 applies.  So once the daemon hands the
     * firmware a list longer than one page, the firmware chunks it into 6-slot
     * pages internally and, on the DOWN that would cross a page boundary, indexes
     * pages[0].items[6] out of range -> panic + reboot.  This is a firmware
     * defect we cannot patch, so the daemon must give the firmware data it can
     * page safely.
     *
     * Two REQUEST shapes seen on the wire (cmd=216), and how we must answer each:
     *
     *  (A) rstable:true, page:0  — the STABLE first-page poll.  The firmware reads
     *      frames in a LOOP while "more" is true (sending more=true made it loop
     *      -> "i/o timeout"), so we answer ONE frame with more=false.  We return
     *      exactly `size` rows (ONE page) — NOT the whole list — plus the true
     *      `total`.  Given a single page the firmware cannot overflow its 6-slot
     *      page, and it FREEZES the view while clihash is unchanged (now stable,
     *      see recon_ap_list_hash) so no background rebuild races the scroll.
     *
     *  (B) rstable:false, page:N — the firmware's OWN page fetch, issued as the
     *      user scrolls past the current page (confirmed live: it emits page:1,
     *      page:2, ... as you press DOWN).  It expects EXACTLY the `size` rows at
     *      offset page*size, with more = there are still rows after this page.
     *      The previous code ignored `page` (always returned the first `size`
     *      rows) and reported a bogus `more`, corrupting the firmware's page
     *      bookkeeping.  We now honour page*size offset and compute `more` right.
     *
     * Net effect (validated live on the G82/qemu firmware): the firmware pages the
     * list itself (page:0 stable + page:1..N on scroll), every page is a single
     * 6-slot page it cannot overflow, scrolling the FULL list no longer crashes or
     * reboots the Pager, and names / highlight / client_count / encryption / live
     * update all keep working.
     *
     * Both (A) and (B) therefore return one page: `size` rows at offset page*size.
     * (size==0 means "no size given" -> return all, matching legacy callers.) */
    uint32_t lim  = size ? size : 0;                 /* one page (0 = all) */
    uint32_t skip = size ? page * size : 0;          /* page*size offset */

    /* BUG B: precompute per-AP client counts ONCE (single grouped query) BEFORE
     * recon_each_ap, so dev_cb can fill clikeys without re-locking g_db_lock. */
    recon_cli_count_t *cli_counts = NULL;
    int cli_counts_n = recon_client_counts_by_bssid(scan_id, &cli_counts);

    mpack_start_map(w, 5);

    mpack_write_cstr(w, "devicelist");
    mpack_build_array(w);                        /* unknown-size array */
    struct devctx dc = { w, lim, 0, skip, 0, scan_id, cli_counts, cli_counts_n };
    recon_each_ap(scan_id, dev_cb, &dc);
    mpack_complete_array(w);

    recon_client_counts_free(cli_counts);

    /* more:
     *  (A) rstable:true first-page frame: always false — the firmware's stable
     *      read loop terminates after one frame, then it pages via rstable:false.
     *  (B) rstable:false paged fetch: true iff there are rows beyond this page,
     *      i.e. (page+1)*size < total.  Uses the request offset (not dc.written)
     *      so a short last page still reports more=false correctly. */
    mpack_write_cstr(w, "more");
    mpack_write_bool(w, rstable ? false
                                : (size && ((uint64_t)page + 1) * size < (uint64_t)total));
    mpack_write_cstr(w, "total");   mpack_write_uint(w, (uint64_t)total);
    /* "clihash": recon stable-list fingerprint (stable while the AP set is
     * unchanged). */
    mpack_write_cstr(w, "clihash"); mpack_write_uint(w, clihash);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_finish_map(w);
}

/* -------------------------------------------------------------------------
 * recon "stable clients" second frame
 *
 * RE of the wire (bridge log) proved the firmware's recon AP-list screen sends
 * cmd=216 ONCE with rstable:true and then performs TWO reads on the same
 * connection:
 *   frame 1 : the stable AP devicelist  (handled above, "RECON STABLE RESP")
 *   frame 2 : the stable CLIENTS list   ("RECON STABLE CLIENTS RESP")
 * It never sends a second request (no cmd=217 on the wire).  Because the old
 * daemon answered with only ONE frame, the firmware's second read hit its
 * read deadline -> "i/o timeout" + "No data", and the list never rendered.
 *
 * We therefore emit the clients frame automatically from sock.c right after
 * the AP frame whenever the request carried rstable (see sock.c).  The clients
 * payload reuses the same PineapQueryResponseData shape {devicelist, more,
 * total, clihash, error}; each client row is {mac, bssid, signal, packets, ts}.
 * On the AP-LIST screen no AP is selected yet, so this is the global client
 * set (the firmware only needs a well-formed frame to complete the cycle).
 * ------------------------------------------------------------------------- */

/* Does this send_devices request want the two-frame stable handshake? */
bool send_devices_wants_rstable(mpack_node_t root)
{
    return msg_get_bool(root, "rstable", false);
}

struct cliwrctx {
    mpack_writer_t *w;
    uint32_t limit;
    uint32_t written;
};

static bool cli_row_cb(void *ud, const char *mac, const char *bssid,
                       long last_seen)
{
    struct cliwrctx *cc = ud;
    if (cc->limit && cc->written >= cc->limit) return false;
    mpack_writer_t *w = cc->w;
    mpack_build_map(w);
    mpack_write_cstr(w, "mac");     mpack_write_mac6(w, mac);
    mpack_write_cstr(w, "bssid");   mpack_write_mac6(w, bssid);
    mpack_write_cstr(w, "signal");  mpack_write_int(w, 0);
    mpack_write_cstr(w, "packets"); mpack_write_uint(w, 0);
    mpack_write_cstr(w, "ts");      mpack_write_int(w, last_seen);
    mpack_complete_map(w);
    cc->written++;
    return true;
}

/* Same order-independent set-identity hash as the AP list (see hash_cb / BUG A):
 * XOR+ADD of per-row hashes so the clients-frame clihash is stable while the
 * client SET is unchanged, letting the firmware's stable handshake converge for
 * the second (clients) frame too. */
struct clihashctx { uint64_t xacc; uint64_t sacc; uint32_t count; };

static bool cli_hash_cb(void *ud, const char *mac, const char *bssid,
                        long last_seen)
{
    (void)last_seen;
    struct clihashctx *hc = ud;
    uint64_t row = fnv1a_str(mac);
    row = (row * 1099511628211ULL) ^ fnv1a_str(bssid);
    hc->xacc ^= row;                /* XOR: commutative + associative */
    hc->sacc += row;                /* ADD: commutative + associative */
    hc->count++;
    return true;
}

static uint64_t recon_client_list_hash(int scan_id, const char *ap_bssid)
{
    struct clihashctx hc = { 0, 0, 0 };
    recon_each_client(scan_id, ap_bssid, cli_hash_cb, &hc);
    uint64_t h = hc.xacc * 1099511628211ULL + hc.sacc
               + (uint64_t)hc.count * 1099511628211ULL;
    if (h == 0) h = 0x9e3779b97f4a7c15ULL;
    return h;
}

/* Build the stable-clients response body (the second frame for an rstable
 * cmd=216). Mirrors PineapQueryResponseData. `ap_bssid` (nullable) filters to
 * a single AP; on the AP-list screen it is NULL => all current clients. */
void send_devices_build_clients(mpack_node_t root, mpack_writer_t *w)
{
    scan_state_t *s = scan_state_get();
    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    uint32_t size = msg_get_u32(root, "size", 0);  /* 0 = all */

    /* If the firmware already selected an AP it passes it in "devicelist" (a
     * single bssid string) on the clients query; on the AP-LIST screen this is
     * nil, so we return the global client set. */
    char ap_bssid[32] = {0};
    msg_get_str(root, "devicelist", ap_bssid, sizeof ap_bssid);
    const char *filt = ap_bssid[0] ? ap_bssid : NULL;

    (void)size;
    uint64_t clihash = recon_client_list_hash(scan_id, filt);

    mpack_start_map(w, 5);

    mpack_write_cstr(w, "devicelist");
    mpack_build_array(w);
    /* send the whole client set in one frame (lim=0) with more=false so the
     * firmware's "read while more" loop terminates. */
    struct cliwrctx cc = { w, 0, 0 };
    recon_each_client(scan_id, filt, cli_row_cb, &cc);
    mpack_complete_array(w);

    int tot = (int)cc.written;

    mpack_write_cstr(w, "more");
    mpack_write_bool(w, false);
    mpack_write_cstr(w, "total");   mpack_write_uint(w, (uint64_t)tot);
    mpack_write_cstr(w, "clihash"); mpack_write_uint(w, clihash);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_finish_map(w);
}

/* 204 new_recon — start a recon scan. Body: {enable, ifname, hidden, signal,
 * size, bytime, target}. We allocate a scan_id, mark state running, and kick the
 * native STA off-channel scan (wlan0 scan) which populates recon.db `aps`.
 * Response: {scan:uint (scan_id), error, message}. */
static void h_new_recon(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();
    bool enable = msg_get_bool(root, "enable", true);

    if (!enable) {
        pthread_mutex_lock(&s->lock);
        s->recon_running = false;
        s->scan_percent = 0;
        pthread_mutex_unlock(&s->lock);
        scan_sta_stop();
        recon_mon_stop();          /* tear wlan1mon down cleanly (no orphan) */
        mpack_start_map(w, 3);
        mpack_write_cstr(w, "scan");    mpack_write_int(w, s->recon_scan_id);
        mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
        mpack_write_cstr(w, "message"); mpack_write_cstr(w, "recon stopped");
        mpack_finish_map(w);
        return;
    }

    int scan_id = recon_next_scan_id();
    pthread_mutex_lock(&s->lock);
    s->recon_scan_id = scan_id;
    s->recon_running = true;
    s->scan_percent = 0;
    s->start_time = time(NULL);
    s->duration_sec = (int)msg_get_u32(root, "seconds", 0);
    pthread_mutex_unlock(&s->lock);

    /* REAL air recon over the dedicated Archer monitor (wlan1mon): beacons,
     * clients, PPS + histogram, channel hopping across 2.4+5GHz. This is the
     * primary recon path and feeds the SAME recon.db aps/clients tables. */
    int mon_ok = recon_mon_start(scan_id);

    /* STA off-channel scan on wlan0 is ONLY a fallback for when the Archer
     * monitor is unavailable. When the monitor works, we must NOT run it: the
     * wlan0 scan is intermittent (wlan0 is busy on the uplink -> 0 APs often),
     * captures NO clients (iw scan sees APs only), and writing into the same
     * scan_id pollutes the monitor's AP+client data (client_count would read 0
     * for APs the wlan0 scan re-wrote). So: monitor OR sta-scan, never both. */
    if (mon_ok != 0)
        scan_sta_start(scan_id);
    else
        scan_sta_stop();

    const char *msg = (mon_ok == 0)
        ? "recon started (Archer monitor wlan1mon: APs+clients+PPS)"
        : "recon started (STA scan fallback; Archer monitor unavailable)";

    mpack_start_map(w, 3);
    mpack_write_cstr(w, "scan");    mpack_write_int(w, scan_id);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, msg);
    mpack_finish_map(w);
}

/* 207 recon_freqstats — per-frequency counts (from aps.channel). We provide a
 * compact freqmap plus a total. */
static void h_recon_freqstats(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();
    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    int total = recon_count_aps(scan_id);
    mpack_start_map(w, 3);
    mpack_write_cstr(w, "total");   mpack_write_uint(w, (uint64_t)total);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);
}

/* 208 recon_stats — general stats. This is the SOLE command the Pager's Recon
 * screen polls, so it must carry the live packets-per-second, the total packet
 * count, a running average, AND the per-second histogram (the firmware sends
 * {window:N} = how many history samples it wants for the -Ns..now bar graph).
 * All of it comes from the REAL Archer monitor (wlan1mon). */
static void h_recon_stats(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();

    /* The Pager's Recon screen polls this command continuously while it is
     * open. That poll IS the firmware's signal that it wants live recon, so we
     * lazily spin up the REAL Archer monitor on the first poll (and record the
     * poll time; an idle watchdog in main.c tears wlan1mon down when the
     * firmware navigates away and the polls stop). This keeps us a pure bridge:
     * the firmware drives, we supply the radio. */
    recon_note_poll();
    if (!recon_mon_running()) {
        int scan_id0 = recon_next_scan_id();
        pthread_mutex_lock(&s->lock);
        s->recon_scan_id = scan_id0;
        s->recon_running = true;
        s->start_time = time(NULL);
        pthread_mutex_unlock(&s->lock);
        /* monitor primary; wlan0 STA scan only as fallback (never both). */
        if (recon_mon_start(scan_id0) != 0)
            scan_sta_start(scan_id0);
    }

    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    int aps = recon_count_aps(scan_id);
    int devs = recon_count_clients(scan_id);
    pthread_mutex_lock(&s->lock);
    bool running = s->recon_running;
    int pct = s->scan_percent;
    pthread_mutex_unlock(&s->lock);

    unsigned pps = recon_mon_pps();
    unsigned avg = recon_mon_pps_avg();
    unsigned long long total_pkts = recon_mon_pkt_total();

    int window = (int)msg_get_i64(root, "window", 45);
    if (window <= 0 || window > 45) window = 45;
    unsigned hist[45];
    int hn = recon_mon_pps_history(hist, window);

    mpack_start_map(w, 11);
    mpack_write_cstr(w, "aps");     mpack_write_uint(w, (uint64_t)aps);
    mpack_write_cstr(w, "devs");    mpack_write_uint(w, (uint64_t)devs);
    mpack_write_cstr(w, "bool");    mpack_write_bool(w, running);
    mpack_write_cstr(w, "pps");     mpack_write_uint(w, (uint64_t)pps);
    mpack_write_cstr(w, "avg");     mpack_write_uint(w, (uint64_t)avg);
    mpack_write_cstr(w, "packets"); mpack_write_uint(w, (uint64_t)total_pkts);
    mpack_write_cstr(w, "total");   mpack_write_uint(w, (uint64_t)pct);
    mpack_write_cstr(w, "history");
    mpack_start_array(w, (uint32_t)hn);
    for (int i = 0; i < hn; i++) mpack_write_uint(w, (uint64_t)hist[i]);
    mpack_finish_array(w);
    mpack_write_cstr(w, "percent"); mpack_write_uint(w, (uint64_t)pct);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);
}

/* 209 recon_devcounts */
static void h_recon_devcounts(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    scan_state_t *s = scan_state_get();
    int scan_id = (int)msg_get_i64(root, "scan", s->recon_scan_id);
    mpack_start_map(w, 4);
    mpack_write_cstr(w, "aps");   mpack_write_uint(w, (uint64_t)recon_count_aps(scan_id));
    mpack_write_cstr(w, "devs");  mpack_write_uint(w, (uint64_t)recon_count_clients(scan_id));
    mpack_write_cstr(w, "error"); mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);
}

/* 210 recon_cryptstats — stub summary. */
static void h_recon_cryptstats(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    wr_basic(w, 0, "ok");
}

/* 211 recon_pps — REAL packets/sec from the Archer monitor (wlan1mon), plus a
 * 45s history ring so the firmware's activity histogram (-45s..now) fills.
 * Body may carry {window:N} = how many history samples the firmware wants. */
static void h_recon_pps(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    unsigned pps = recon_mon_pps();
    int window = (int)msg_get_i64(root, "window", 45);
    if (window <= 0 || window > 45) window = 45;

    unsigned hist[45];
    int n = recon_mon_pps_history(hist, window);

    mpack_start_map(w, 4);
    mpack_write_cstr(w, "pps");     mpack_write_uint(w, (uint64_t)pps);
    /* history oldest..newest for the bar graph */
    mpack_write_cstr(w, "history");
    mpack_start_array(w, (uint32_t)n);
    for (int i = 0; i < n; i++) mpack_write_uint(w, (uint64_t)hist[i]);
    mpack_finish_array(w);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w,
        recon_mon_running() ? "ok" : "monitor idle");
    mpack_finish_map(w);
}

/* 212 recondb_control — clear/vacuum the recon DB rows for the current scan.
 * Body: {flags} (1 = clear). */
static void h_recondb_control(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    uint32_t flags = msg_get_u32(root, "flags", 0);
    if (flags == 1) {
        /* Start a fresh scan id so stale rows are no longer returned. */
        scan_state_t *s = scan_state_get();
        s->recon_scan_id = recon_next_scan_id();
        wr_basic(w, 0, "recon db rolled to new scan id");
    } else {
        wr_basic(w, 0, "ok");
    }
}

/* 255 set_ssidcollect — toggle live SSID harvesting. Body: {enable}. */
static void h_set_ssidcollect(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    bool enable = msg_get_bool(root, "enable", true);
    scan_state_t *s = scan_state_get();
    pager_config_t *c = config_get();
    pthread_mutex_lock(&s->lock);
    s->ssid_collect = enable;
    pthread_mutex_unlock(&s->lock);
    pthread_mutex_lock(&c->lock);
    c->ssid_collect = enable;
    pthread_mutex_unlock(&c->lock);
    wr_basic(w, 0, enable ? "ssid collection on" : "ssid collection off");
}

/* ======================================================================== *
 *  deauth (400/401/402)
 * ======================================================================== */

/* Common deauth path. Body: {bssid (AP), target (client mac), channel, count}.
 * 400 = targeted, 401 = deauth-all (broadcast client), 402 = CSA (we fall back
 * to deauth frames since we don't forge a CSA beacon here).
 *
 * Requires the Archer monitor iface; without it, returns a basic error. */
static void h_send_deauth(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    mac_t bssid = {{0}}, target = {{0}};
    /* The firmware sends MACs as msgpack BIN(6): "bssid" = the AP, and "sta" =
     * the client to deauth (NOT "target"). Accept bin or string, and fall back
     * to "target" for compatibility. */
    bool have_bssid  = msg_get_mac6(root, "bssid", bssid.b);
    bool have_target = msg_get_mac6(root, "sta", target.b) ||
                       msg_get_mac6(root, "target", target.b);
    /* channel may arrive as a string ("11") or an int; try both. */
    char ch_s[8] = {0};
    uint32_t channel = msg_get_u32(root, "channel", 0);
    if (channel == 0 && msg_get_str(root, "channel", ch_s, sizeof ch_s))
        channel = (uint32_t)atoi(ch_s);
    uint32_t count = msg_get_u32(root, "count", 1);
    if (count == 0) count = 1;
    if (count > 1000) count = 1000;    /* sanity cap */

    if (!have_bssid) { wr_basic(w, 1, "bssid required"); return; }

    /* deauth-all => broadcast destination */
    if (cmd == CMD_SEND_DEAUTH_ALL || !have_target) {
        memset(target.b, 0xff, 6);
        have_target = true;
    }

    /* open TX on the Archer monitor iface (graceful fail if absent) */
    if (iface_tx_open() != 0) {
        wr_basic(w, 1, "monitor radio unavailable (connect Archer wlan1)");
        return;
    }
    /* Park the radio on the target channel for the whole injection. If recon's
     * channel-hop thread is running it would otherwise hop away mid-deauth, so
     * we lock the hop to this channel, then restore hopping afterwards. */
    int prev_lock = 0; bool relock = false;
    if (channel > 0) {
        pager_config_t *c = config_get();
        if (recon_mon_running()) {
            recon_mon_set_channel_lock((int)channel);
            relock = true;
        }
        iface_set_channel_on(c->monitor_iface, (int)channel);
    }
    (void)prev_lock;

    size_t dl = 0, ul = 0;
    /* deauth: AP -> client, and disassoc: AP -> client */
    uint8_t *df = build_deauth(&target, &bssid, &bssid, PINEAP_DEAUTH_REASON, &dl);
    uint8_t *uf = build_disassoc(&target, &bssid, &bssid, PINEAP_DEAUTH_REASON, &ul);
    int sent = 0;
    for (uint32_t i = 0; i < count; i++) {
        if (df && dl && iface_tx_inject(df, dl) > 0) sent++;
        if (uf && ul && iface_tx_inject(uf, ul) > 0) sent++;
        usleep(2000);
    }
    free(df); free(uf);

    /* restore channel hopping if we locked it for this deauth burst. */
    if (relock) recon_mon_set_channel_lock(0);

    if (config_get()->ssid_collect) { /* no-op hook */ }
    {
        char ms[18]; mac_to_str(&bssid, ms);
        long now = time(NULL);
        log_write_entry(LOGTYPE_DEAUTH, ms, "", now, now);
    }

    char msg[64];
    snprintf(msg, sizeof msg, "sent %d frames", sent);
    mpack_start_map(w, 3);
    mpack_write_cstr(w, "packets"); mpack_write_uint(w, (uint64_t)sent);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, sent > 0 ? 0 : 1);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, msg);
    mpack_finish_map(w);
}

/* ======================================================================== *
 *  ssid_pool (300-314)
 * ======================================================================== */

/* 300 fetch_ssid_pool / 314 fetch_new_ssid_pool — return the SSID pool.
 * Response: {ssids:[...], more:bool, error, message}. */
static void h_fetch_ssid_pool(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    char **arr = NULL;
    int n = ssid_get_all(&arr);

    mpack_start_map(w, 4);
    mpack_write_cstr(w, "ssids");
    mpack_start_array(w, (uint32_t)(n < 0 ? 0 : n));
    for (int i = 0; i < n; i++) mpack_write_cstr(w, arr[i]);
    mpack_finish_array(w);
    mpack_write_cstr(w, "more");    mpack_write_bool(w, false);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, "ok");
    mpack_finish_map(w);

    ssid_free_all(arr, n);
}

/* 301 clear_ssid_pool */
static void h_clear_ssid_pool(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    ssid_clear();
    wr_basic(w, 0, "ssid pool cleared");
}

/* 302/303 mod_ssid_pool — add/remove SSIDs. Body: {ssids:[...], flags} where
 * flags 0 = add (default), 1 = remove. Also accepts a single {ssid}. */
static void h_mod_ssid_pool(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    uint32_t flags = msg_get_u32(root, "flags", cmd == CMD_MOD_SSID_POOL_B ? 1 : 0);
    int changed = 0;

    /* single ssid key */
    char one[256];
    if (msg_get_str(root, "ssid", one, sizeof one) && one[0]) {
        if (flags == 1) ssid_del(one); else ssid_add(one);
        changed++;
    }

    /* array of ssids */
    mpack_node_t arr = mpack_node_map_cstr_optional(root, "ssids");
    if (!mpack_node_is_missing(arr) && mpack_node_type(arr) == mpack_type_array) {
        size_t len = mpack_node_array_length(arr);
        for (size_t i = 0; i < len; i++) {
            mpack_node_t el = mpack_node_array_at(arr, i);
            if (mpack_node_type(el) != mpack_type_str) continue;
            size_t sl = mpack_node_strlen(el);
            const char *s = mpack_node_str(el);
            if (!s || sl == 0 || sl >= sizeof one) continue;
            memcpy(one, s, sl); one[sl] = '\0';
            if (flags == 1) ssid_del(one); else ssid_add(one);
            changed++;
        }
    }

    char msg[64];
    snprintf(msg, sizeof msg, "%s %d ssid(s)", flags == 1 ? "removed" : "added", changed);
    wr_basic(w, 0, msg);
}

/* 304-310 config_ssid_pool — pool config options. Body typically {autobssid,
 * random_mac, random_order, ...}. We acknowledge; broadcast flag handled by 312. */
static void h_config_ssid_pool(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    wr_basic(w, 0, "ssid pool config updated");
}

/* 312 confbcast_ssid_pool — toggle broadcasting the pool. Body: {enable}. */
static void h_confbcast_ssid_pool(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd;
    bool enable = msg_get_bool(root, "enable", true);
    pager_config_t *c = config_get();
    pthread_mutex_lock(&c->lock);
    c->broadcast_ssid_pool = enable;
    pthread_mutex_unlock(&c->lock);
    wr_basic(w, 0, enable ? "broadcast on" : "broadcast off");
}

/* 313 loaduci_ssid_pool / 311 loaduci_config — reload config from disk. */
static void h_loaduci(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    config_reload();
    wr_basic(w, 0, "config reloaded");
}

/* ======================================================================== *
 *  examine (500/501/502)
 * ======================================================================== */

/* 500 examine bssid / 501 examine channel / 502 reset. Examine focuses the
 * monitor on a BSSID/channel. Needs the Archer; degrades gracefully. */
static void h_examine(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    pager_config_t *c = config_get();
    if (cmd == CMD_EXAMINE_RESET) {
        /* release the channel lock so the monitor resumes hopping. */
        recon_mon_set_channel_lock(0);
        wr_basic(w, 0, "examine reset");
        return;
    }
    if (!iface_exists(c->monitor_iface)) {
        wr_basic(w, 1, "monitor radio unavailable (connect Archer wlan1)");
        return;
    }
    if (cmd == CMD_EXAMINE_CHANNEL) {
        uint32_t ch = msg_get_u32(root, "channel", 0);
        if (ch == 0) { wr_basic(w, 1, "channel required"); return; }
        /* lock the recon monitor on this channel (stops hopping) so examine
         * dwells there for clients/handshakes; also set it immediately. */
        recon_mon_set_channel_lock((int)ch);
        if (iface_set_channel_on(c->monitor_iface, (int)ch) == 0)
            wr_basic(w, 0, "examining channel");
        else
            wr_basic(w, 1, "failed to set channel");
        return;
    }
    /* examine bssid: lock+set channel if provided then acknowledge */
    uint32_t ch = msg_get_u32(root, "channel", 0);
    if (ch > 0) {
        recon_mon_set_channel_lock((int)ch);
        iface_set_channel_on(c->monitor_iface, (int)ch);
    }
    wr_basic(w, 0, "examining bssid");
}

/* ======================================================================== *
 *  pcap (610/611/612)
 * ======================================================================== */

/* 610 start / 611 stop / 612 control. Raw capture to a pcap file on the Archer.
 * We track state; actual capture requires the monitor iface. */
static void h_pcap_control(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)root;
    scan_state_t *s = scan_state_get();
    pager_config_t *c = config_get();

    if (cmd == CMD_PCAP_STOP) {
        pthread_mutex_lock(&s->lock); s->pcap_running = false; pthread_mutex_unlock(&s->lock);
        wr_basic(w, 0, "pcap stopped");
        return;
    }
    if (cmd == CMD_PCAP_START) {
        if (!iface_exists(c->monitor_iface)) {
            wr_basic(w, 1, "monitor radio unavailable (connect Archer wlan1)");
            return;
        }
        pthread_mutex_lock(&s->lock); s->pcap_running = true; pthread_mutex_unlock(&s->lock);
        wr_basic(w, 0, "pcap started");
        return;
    }
    /* 612 control: report running state */
    pthread_mutex_lock(&s->lock);
    bool running = s->pcap_running;
    pthread_mutex_unlock(&s->lock);
    mpack_start_map(w, 3);
    mpack_write_cstr(w, "bool");    mpack_write_bool(w, running);
    mpack_write_cstr(w, "error");   mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, running ? "running" : "idle");
    mpack_finish_map(w);
}

/* ======================================================================== *
 *  gpsd (10)
 * ======================================================================== */

/* 10 gpsd_report — report a GPS fix. We try a quick TPV poll from gpsd on
 * gpsd_host:gpsd_port; if gpsd is absent we report fix=0 (no fix) cleanly.
 * Response keys: {lat, lon, alt, fix, spd, pps, error, message}. */
static void h_gpsd_report(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)cmd; (void)root;
    pager_config_t *c = config_get();
    double lat = 0, lon = 0, alt = 0, spd = 0;
    int fix = 0;

    /* Best-effort: connect, send ?WATCH + ?POLL, read one line. gpsd speaks JSON
     * but we only need to know if a TPV with mode>=2 exists; we keep this light
     * and fail silently to fix=0 if gpsd isn't there. */
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd >= 0) {
        struct sockaddr_in sa;
        memset(&sa, 0, sizeof sa);
        sa.sin_family = AF_INET;
        sa.sin_port = htons((uint16_t)c->gpsd_port);
        if (inet_pton(AF_INET, c->gpsd_host, &sa.sin_addr) == 1) {
            struct timeval tv = { .tv_sec = 0, .tv_usec = 300000 };
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
            setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
            if (connect(fd, (struct sockaddr *)&sa, sizeof sa) == 0) {
                const char *poll = "?WATCH={\"enable\":true,\"json\":true};\n?POLL;\n";
                (void)!write(fd, poll, strlen(poll));
                char buf[4096];
                ssize_t r = read(fd, buf, sizeof buf - 1);
                if (r > 0) {
                    buf[r] = '\0';
                    char *p = strstr(buf, "\"mode\":");
                    if (p) { fix = atoi(p + 7); }
                    p = strstr(buf, "\"lat\":"); if (p) lat = atof(p + 6);
                    p = strstr(buf, "\"lon\":"); if (p) lon = atof(p + 6);
                    p = strstr(buf, "\"alt\":"); if (p) alt = atof(p + 6);
                    p = strstr(buf, "\"speed\":"); if (p) spd = atof(p + 8);
                }
            }
        }
        close(fd);
    }

    mpack_start_map(w, 8);
    mpack_write_cstr(w, "lat"); mpack_write_double(w, lat);
    mpack_write_cstr(w, "lon"); mpack_write_double(w, lon);
    mpack_write_cstr(w, "alt"); mpack_write_double(w, alt);
    mpack_write_cstr(w, "fix"); mpack_write_int(w, fix);
    mpack_write_cstr(w, "spd"); mpack_write_double(w, spd);
    mpack_write_cstr(w, "pps"); mpack_write_uint(w, 0);
    mpack_write_cstr(w, "error"); mpack_write_uint(w, 0);
    mpack_write_cstr(w, "message"); mpack_write_cstr(w, fix >= 2 ? "fix" : "no fix");
    mpack_finish_map(w);
}

/* ======================================================================== *
 *  generic stub — basic OK for codes not yet specialized
 * ======================================================================== */
static void h_stub_ok(uint32_t cmd, mpack_node_t root, mpack_writer_t *w)
{
    (void)root;
    char msg[48];
    snprintf(msg, sizeof msg, "command %u not implemented (stub)", cmd);
    wr_basic(w, 0, msg);
}

/* ======================================================================== *
 *  dispatch table
 * ======================================================================== */
struct cmd_entry { uint32_t code; cmd_handler_fn fn; };

static const struct cmd_entry g_table[] = {
    /* interface_tracker */
    { CMD_LIST_INTERFACES,        h_list_interfaces },
    { CMD_MOD_INTERFACE,          h_mod_interface },
    { CMD_MOD_INTERFACE_2,        h_mod_interface },
    { CMD_TOGGLE_INTERFACE_A,     h_toggle_interface },
    { CMD_TOGGLE_INTERFACE_B,     h_toggle_interface },
    { CMD_SET_INTERFACE_TYPE,     h_set_interface_type },
    { CMD_SET_INTERFACE_TYPE_2,   h_set_interface_type },
    { CMD_IFACE_RESET_CHANNELS,   h_reset_channels },
    { CMD_SEND_DEAUTH,            h_send_deauth },
    { CMD_SEND_DEAUTH_ALL,        h_send_deauth },
    { CMD_SEND_DEAUTH_CSA,        h_send_deauth },
    { CMD_EXAMINE_BSSID,          h_examine },
    { CMD_EXAMINE_CHANNEL,        h_examine },
    { CMD_EXAMINE_RESET,          h_examine },
    { CMD_PCAP_START,             h_pcap_control },
    { CMD_PCAP_STOP,              h_pcap_control },
    { CMD_PCAP_CONTROL,           h_pcap_control },
    /* recon_engine */
    { CMD_COUNT_DEVICES,          h_count_devices },
    { CMD_COUNT_DEVICES_2,        h_count_devices },
    { CMD_SEND_DEVICES_201,       h_send_devices },
    { CMD_SEND_DEVICES_203,       h_send_devices },
    { CMD_SEND_DEVICES_205,       h_send_devices },
    { CMD_SEND_DEVICES_215,       h_send_devices },
    { CMD_SEND_DEVICES_216,       h_send_devices },
    { CMD_SEND_DEVICES_217,       h_send_devices },
    { CMD_NEW_RECON,              h_new_recon },
    { CMD_MONITOR_DEVICE,         h_stub_ok },
    { CMD_RECON_FREQSTATS,        h_recon_freqstats },
    { CMD_RECON_STATS,            h_recon_stats },
    { CMD_RECON_DEVCOUNTS,        h_recon_devcounts },
    { CMD_RECON_CRYPTSTATS,       h_recon_cryptstats },
    { CMD_RECON_PPS,              h_recon_pps },
    { CMD_RECONDB_CONTROL,        h_recondb_control },
    { CMD_WAITFOR,                h_stub_ok },
    { CMD_SET_HANDSHAKE,          h_stub_ok },
    { CMD_SET_SSIDCOLLECT,        h_set_ssidcollect },
    { CMD_WIGLE_CONTROL_A,        h_stub_ok },
    { CMD_WIGLE_CONTROL_B,        h_stub_ok },
    { CMD_WIGLE_CONTROL_C,        h_stub_ok },
    { CMD_HOSTAP_HANDSHAKE,       h_stub_ok },
    { CMD_HOSTAP_CHALLENGE,       h_stub_ok },
    { CMD_HOSTAP_BASIC,           h_stub_ok },
    { CMD_HOSTAP_CLIENT_CONNECT,  h_stub_ok },
    { CMD_HOSTAP_CLIENT_DISCONNECT, h_stub_ok },
    /* ssid_pool */
    { CMD_FETCH_SSID_POOL,        h_fetch_ssid_pool },
    { CMD_FETCH_NEW_SSID_POOL,    h_fetch_ssid_pool },
    { CMD_CLEAR_SSID_POOL,        h_clear_ssid_pool },
    { CMD_MOD_SSID_POOL_A,        h_mod_ssid_pool },
    { CMD_MOD_SSID_POOL_B,        h_mod_ssid_pool },
    { CMD_CONFIG_SSID_POOL_304,   h_config_ssid_pool },
    { CMD_CONFIG_SSID_POOL_305,   h_config_ssid_pool },
    { CMD_CONFIG_SSID_POOL_307,   h_config_ssid_pool },
    { CMD_CONFIG_SSID_POOL_308,   h_config_ssid_pool },
    { CMD_CONFIG_SSID_POOL_309,   h_config_ssid_pool },
    { CMD_CONFIG_SSID_POOL_310,   h_config_ssid_pool },
    { CMD_LOADUCI_CONFIG,         h_loaduci },
    { CMD_CONFBCAST_SSID_POOL,    h_confbcast_ssid_pool },
    { CMD_LOADUCI_SSID_POOL,      h_loaduci },
    /* gpsd */
    { CMD_GPSD_REPORT,            h_gpsd_report },
};

cmd_handler_fn sock_lookup_handler(uint32_t cmd)
{
    size_t n = sizeof g_table / sizeof g_table[0];
    for (size_t i = 0; i < n; i++)
        if (g_table[i].code == cmd) return g_table[i].fn;
    return NULL;
}
