/*
 * parser.c — parser thread + result handler thread + inject queue.
 *
 * Architecture mirrors the original (INFORME §H.1):
 *   capture thread (iface.c) -> sniff queue -> parser thread -> result queue
 *   -> result handler thread -> DB writes / handshake pcap.
 *
 * Here the capture thread calls parser_submit() which enqueues a copy of the
 * frame; the parser thread decodes it and the same thread performs the DB side
 * effects (we fold parser+result-handler into one worker; the original uses two
 * but the observable behaviour — DB rows, handshake pcaps — is identical).
 */
#include "pineapd.h"
#include <pcap/pcap.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sys/stat.h>
#include <pthread.h>

/* ----------------------------- sniff queue ------------------------------ */
/* Bound the sniff queue. Each node holds a copy of a frame (<=2048 bytes). In a
 * busy RF environment the capture thread can enqueue faster than the parser
 * drains; an unbounded list is an unbounded memory leak (observed: pineapd
 * peaking at 1.3G on the G82). Cap the backlog and drop the OLDEST frame when
 * full (newest data is the most useful for recon/handshakes). 4096 * 2KB ~ 8MB
 * worst case, in line with the original's tiny footprint. */
#define SNIFF_QUEUE_MAX 4096

typedef struct sniff_node {
    uint8_t *data;
    size_t   len;
    long     ts_us;
    int      signal_dbm;
    struct sniff_node *next;
} sniff_node_t;

static sniff_node_t *q_head = NULL, *q_tail = NULL;
static size_t q_len = 0;
static pthread_mutex_t q_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  q_cond = PTHREAD_COND_INITIALIZER;
static pthread_t  g_parser_thread;
static volatile bool g_parser_run = false;

void parser_submit(const uint8_t *pkt, size_t len, long ts_us, int signal_dbm)
{
    if (!g_parser_run || len == 0) return;
    sniff_node_t *n = malloc(sizeof *n);
    if (!n) return;
    n->data = malloc(len);
    if (!n->data) { free(n); return; }
    memcpy(n->data, pkt, len);
    n->len = len; n->ts_us = ts_us; n->signal_dbm = signal_dbm; n->next = NULL;
    pthread_mutex_lock(&q_lock);
    /* backpressure: if the backlog is full, drop the oldest queued frame so the
     * queue cannot grow without bound when the parser falls behind. */
    if (q_len >= SNIFF_QUEUE_MAX && q_head) {
        sniff_node_t *old = q_head;
        q_head = old->next;
        if (!q_head) q_tail = NULL;
        q_len--;
        free(old->data);
        free(old);
    }
    if (q_tail) q_tail->next = n; else q_head = n;
    q_tail = n;
    q_len++;
    pthread_cond_signal(&q_cond);
    pthread_mutex_unlock(&q_lock);
}

static sniff_node_t *q_pop_wait(void)
{
    pthread_mutex_lock(&q_lock);
    while (g_parser_run && !q_head)
        pthread_cond_wait(&q_cond, &q_lock);
    sniff_node_t *n = q_head;
    if (n) { q_head = n->next; if (!q_head) q_tail = NULL; q_len--; }
    pthread_mutex_unlock(&q_lock);
    return n;
}

/* ------------------------- handshake tracking --------------------------- */
/* Cap the number of (ap,client) handshake entries held in memory at once. Each
 * entry can hold up to 4 frame copies (<=2KB each). Without a cap this list grew
 * one entry per unique station pair for the whole capture session and was NEVER
 * freed -> the dominant contributor to the 1.3G peak on the G82. The pcap files
 * are the durable output; in-memory entries are only needed to assemble the 4
 * EAPOL parts, so a bounded working set is sufficient. When full, evict the
 * oldest (tail) entry. */
#define HS_MAX_ENTRIES 256

typedef struct hs_entry {
    mac_t ap, client;
    uint8_t parts_mask;                 /* bits 0..3 = M1..M4 */
    uint8_t *frames[4]; size_t flen[4];
    struct hs_entry *next;
} hs_entry_t;

static hs_entry_t *g_hs = NULL;
static int g_hs_count = 0;
static pthread_mutex_t g_hs_lock = PTHREAD_MUTEX_INITIALIZER;

static void hs_entry_free(hs_entry_t *e)
{
    if (!e) return;
    for (int i = 0; i < 4; i++) free(e->frames[i]);
    free(e);
}

/* Evict the oldest entry (the tail of the singly-linked list). Caller holds
 * g_hs_lock. */
static void hs_evict_oldest(void)
{
    if (!g_hs) return;
    hs_entry_t *prev = NULL, *e = g_hs;
    while (e->next) { prev = e; e = e->next; }
    if (prev) prev->next = NULL; else g_hs = NULL;
    hs_entry_free(e);
    g_hs_count--;
}

/* Called from hs_find() with g_hs_lock held. */
static hs_entry_t *hs_find_locked(const mac_t *ap, const mac_t *cl)
{
    for (hs_entry_t *e = g_hs; e; e = e->next)
        if (!memcmp(e->ap.b, ap->b, 6) && !memcmp(e->client.b, cl->b, 6))
            return e;
    if (g_hs_count >= HS_MAX_ENTRIES)
        hs_evict_oldest();
    hs_entry_t *e = calloc(1, sizeof *e);
    if (!e) return NULL;
    e->ap = *ap; e->client = *cl; e->next = g_hs; g_hs = e;
    g_hs_count++;
    return e;
}

/* Free the whole in-memory handshake working set (does NOT touch the pcap files
 * already written to disk). Called when handshake capture stops and at
 * shutdown, so memory does not accumulate across capture sessions. */
void parser_handshake_reset(void)
{
    pthread_mutex_lock(&g_hs_lock);
    hs_entry_t *e = g_hs;
    while (e) { hs_entry_t *nx = e->next; hs_entry_free(e); e = nx; }
    g_hs = NULL;
    g_hs_count = 0;
    pthread_mutex_unlock(&g_hs_lock);
}

static void hs_write_pcap(hs_entry_t *e)
{
    mkdir(PINEAPD_HANDSHAKE_DIR, 0755);
    bool complete = (e->parts_mask & 0xf) == 0xf;
    char ap[18]; mac_to_str(&e->ap, ap);
    char path[320];
    snprintf(path, sizeof path, "%s/%s_%s.pcap", PINEAPD_HANDSHAKE_DIR,
             ap, complete ? "full" : "partial");
    struct stat stbuf;
    if (stat(path, &stbuf) == 0 && complete) return;   /* don't overwrite full */

    pcap_t *pd = pcap_open_dead(DLT_IEEE802_11_RADIO, 2048);
    if (!pd) return;
    pcap_dumper_t *du = pcap_dump_open(pd, path);
    if (!du) { pcap_close(pd); return; }
    for (int i = 0; i < 4; i++) {
        if (!e->frames[i]) continue;
        struct pcap_pkthdr h;
        memset(&h, 0, sizeof h);
        struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
        h.ts.tv_sec = ts.tv_sec; h.ts.tv_usec = ts.tv_nsec / 1000;
        h.caplen = h.len = e->flen[i];
        pcap_dump((u_char*)du, &h, e->frames[i]);
    }
    pcap_dump_close(du);
    pcap_close(pd);
    pineap_log("RESULTS", "wrote handshake %s (mask=0x%x)", path, e->parts_mask);
}

/* --------------------------- parser worker ------------------------------ */
static void handle_frame(sniff_node_t *n)
{
    sniff_view_t v;
    if (!wifi_parse(n->data, n->len, &v)) return;
    pineap_config_t *c = config_get();
    scan_state_t *s = scan_state_get();
    long now = time(NULL);

    int type = wifi_type(&v), sub = wifi_subtype(&v);

    pthread_mutex_lock(&s->lock);
    int scan_id = s->recon_scan_id;
    bool recon = s->recon_running;
    bool handshake = s->handshake_running;
    pthread_mutex_unlock(&s->lock);

    if (type == 0 && (sub == 8 || sub == 5)) {
        /* beacon / probe-resp => AP */
        mac_t bssid; char ssid[64]; int ch, hidden;
        if (wifi_get_ap(&v, &bssid, ssid, sizeof ssid, &ch, &hidden)) {
            char bs[18]; mac_to_str(&bssid, bs);
            if (recon)
                recon_write_ap(scan_id, ssid, bs, 0, hidden, ch,
                               n->signal_dbm, 0, now);
            /* capture_ssids: harvest SSID into pool */
            if (c->capture_ssids && ssid[0] && !ssid_has(ssid))
                ssid_add(ssid);
        }
    } else if (type == 0 && sub == 4) {
        /* probe-req => client */
        mac_t client; char ssid[64];
        if (wifi_get_probe(&v, &client, ssid, sizeof ssid)) {
            char cs[18]; mac_to_str(&client, cs);
            if (c->logging)
                log_write_entry(LOGTYPE_PROBE, cs, ssid, now, now);
            if (recon)
                recon_write_client(scan_id, cs, "", now);
            if (c->capture_ssids && ssid[0] && !ssid_has(ssid))
                ssid_add(ssid);
        }
    } else if (type == 2 && handshake) {
        /* data => possible EAPOL handshake */
        mac_t ap, client;
        int part = wifi_wpa_handshake_part(&v, &ap, &client);
        if (part >= 1 && part <= 4) {
            pthread_mutex_lock(&g_hs_lock);
            hs_entry_t *e = hs_find_locked(&ap, &client);
            int idx = part - 1;
            if (e && !e->frames[idx]) {
                e->frames[idx] = malloc(n->len);
                if (e->frames[idx]) {
                    memcpy(e->frames[idx], n->data, n->len);
                    e->flen[idx] = n->len;
                    e->parts_mask |= (1 << idx);
                    hs_write_pcap(e);
                }
            }
            pthread_mutex_unlock(&g_hs_lock);
        }
    }
}

static void *parser_run(void *arg)
{
    (void)arg;
    while (g_parser_run) {
        sniff_node_t *n = q_pop_wait();
        if (!n) continue;
        handle_frame(n);
        free(n->data);
        free(n);
    }
    return NULL;
}

int parser_start(void)
{
    g_parser_run = true;
    if (pthread_create(&g_parser_thread, NULL, parser_run, NULL) != 0) {
        g_parser_run = false;
        return -1;
    }
    return 0;
}

void parser_stop(void)
{
    if (!g_parser_run) return;
    g_parser_run = false;
    pthread_mutex_lock(&q_lock);
    pthread_cond_broadcast(&q_cond);
    pthread_mutex_unlock(&q_lock);
    pthread_join(g_parser_thread, NULL);

    /* drain any frames still queued (bounded, but free them so a restart within
     * the same process does not leak the residual backlog) */
    pthread_mutex_lock(&q_lock);
    sniff_node_t *n = q_head;
    while (n) { sniff_node_t *nx = n->next; free(n->data); free(n); n = nx; }
    q_head = q_tail = NULL; q_len = 0;
    pthread_mutex_unlock(&q_lock);

    /* free the in-memory handshake working set */
    parser_handshake_reset();
}

/* ----------------------------- inject queue ----------------------------- */
static inject_frame_t *ij_head = NULL, *ij_tail = NULL;
static pthread_mutex_t ij_lock = PTHREAD_MUTEX_INITIALIZER;

void inject_queue_push(uint8_t channel, uint32_t delay_us, uint32_t multiplier,
                       const uint8_t *payload, size_t len)
{
    inject_frame_t *f = malloc(sizeof *f);
    if (!f) return;
    f->channel = channel; f->delay_us = delay_us; f->multiplier = multiplier;
    f->len = len; f->next = NULL;
    f->payload = malloc(len);
    if (!f->payload) { free(f); return; }
    memcpy(f->payload, payload, len);
    pthread_mutex_lock(&ij_lock);
    if (ij_tail) ij_tail->next = f; else ij_head = f;
    ij_tail = f;
    pthread_mutex_unlock(&ij_lock);
}

inject_frame_t *inject_queue_pop(void)
{
    pthread_mutex_lock(&ij_lock);
    inject_frame_t *f = ij_head;
    if (f) { ij_head = f->next; if (!ij_head) ij_tail = NULL; }
    pthread_mutex_unlock(&ij_lock);
    return f;
}

void inject_frame_free(inject_frame_t *f)
{
    if (!f) return;
    free(f->payload);
    free(f);
}
