/*
 * msg.c — MessagePack wire-frame helpers (see msg.h).
 *
 * Header is 8 bytes, two big-endian u32 (cmd, len), hand-coded with ntohl/htonl.
 * Body is MessagePack via the vendored mpack library.
 */
#include "pager.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>   /* htonl / ntohl */

/* ---- low-level fd I/O --------------------------------------------------- */

/* Read exactly n bytes (blocking). Returns 0 on success, -1 on EOF/error. */
static int read_full(int fd, void *buf, size_t n)
{
    uint8_t *p = buf;
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, p + got, n - got);
        if (r > 0) { got += (size_t)r; continue; }
        if (r == 0) return -1;                      /* peer closed */
        if (errno == EINTR) continue;
        return -1;
    }
    return 0;
}

/* Write exactly n bytes. Returns 0 on success, -1 on error. */
static int write_full(int fd, const void *buf, size_t n)
{
    const uint8_t *p = buf;
    size_t sent = 0;
    while (sent < n) {
        ssize_t w = write(fd, p + sent, n - sent);
        if (w > 0) { sent += (size_t)w; continue; }
        if (w < 0 && errno == EINTR) continue;
        return -1;
    }
    return 0;
}

/* ---- framing ----------------------------------------------------------- */

int msg_read_frame(int fd, msg_frame_t *out)
{
    out->cmd = 0; out->body = NULL; out->body_len = 0;

    uint8_t hdr[PINEAP_HDR_LEN];
    if (read_full(fd, hdr, PINEAP_HDR_LEN) != 0) return -1;

    uint32_t cmd_be, len_be;
    memcpy(&cmd_be, hdr,     4);
    memcpy(&len_be, hdr + 4, 4);
    uint32_t cmd = ntohl(cmd_be);
    uint32_t len = ntohl(len_be);

    if (len > PINEAP_MAX_BODY) {
        pineap_log("MSG", "frame body too large: %u bytes (cap %u)",
                   len, PINEAP_MAX_BODY);
        return -1;
    }

    char *body = NULL;
    if (len > 0) {
        body = malloc(len);
        if (!body) return -1;
        if (read_full(fd, body, len) != 0) { free(body); return -1; }
    }
    out->cmd = cmd;
    out->body = body;
    out->body_len = len;
    return 0;
}

void msg_frame_free(msg_frame_t *f)
{
    if (!f) return;
    free(f->body);
    f->body = NULL;
    f->body_len = 0;
}

int msg_write_frame(int fd, uint32_t cmd, const char *body, size_t body_len)
{
    uint8_t hdr[PINEAP_HDR_LEN];
    uint32_t cmd_be = htonl(cmd);
    uint32_t len_be = htonl((uint32_t)body_len);
    memcpy(hdr,     &cmd_be, 4);
    memcpy(hdr + 4, &len_be, 4);

    if (write_full(fd, hdr, PINEAP_HDR_LEN) != 0) return -1;
    if (body_len > 0 && body) {
        if (write_full(fd, body, body_len) != 0) return -1;
    }
    return 0;
}

/* ---- body writer helpers ----------------------------------------------- */

void msg_body_begin(mpack_writer_t *w, char **data, size_t *size)
{
    mpack_writer_init_growable(w, data, size);
}

char *msg_body_finish(mpack_writer_t *w, char **data, size_t *size, size_t *len)
{
    /* On destroy, mpack writes the final buffer address into *data and its
     * length into *size (the pointers passed to init_growable). */
    mpack_error_t err = mpack_writer_destroy(w);
    if (err != mpack_ok) {
        if (data && *data) { free(*data); *data = NULL; }
        if (len) *len = 0;
        return NULL;
    }
    if (len) *len = size ? *size : 0;
    return data ? *data : NULL;
}

void msg_write_basic(mpack_writer_t *w, uint64_t error, const char *message)
{
    mpack_start_map(w, 2);
    mpack_write_cstr(w, "error");
    mpack_write_uint(w, error);
    mpack_write_cstr(w, "message");
    mpack_write_cstr(w, message ? message : "");
    mpack_finish_map(w);
}

char *msg_make_basic(uint64_t error, const char *message, size_t *len)
{
    char *data = NULL;
    size_t size = 0;
    mpack_writer_t w;
    mpack_writer_init_growable(&w, &data, &size);
    msg_write_basic(&w, error, message);
    if (mpack_writer_destroy(&w) != mpack_ok) {
        free(data);
        if (len) *len = 0;
        return NULL;
    }
    if (len) *len = size;
    return data;
}

/* ---- body reader helpers ----------------------------------------------- */

bool msg_body_parse(mpack_tree_t *tree, const char *body, size_t len,
                    mpack_node_t *root)
{
    /* mpack_tree_init_data tolerates NULL/0 by producing an errored tree; the
     * root node accessors then safely return defaults. */
    mpack_tree_init_data(tree, body ? body : "", len);
    mpack_tree_parse(tree);
    mpack_node_t r = mpack_tree_root(tree);
    if (root) *root = r;
    return (mpack_tree_error(tree) == mpack_ok &&
            mpack_node_type(r) == mpack_type_map);
}

uint32_t msg_get_u32(mpack_node_t map, const char *key, uint32_t dflt)
{
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return dflt;
    mpack_type_t t = mpack_node_type(n);
    if (t == mpack_type_uint || t == mpack_type_int)
        return mpack_node_u32(n);
    return dflt;
}

int64_t msg_get_i64(mpack_node_t map, const char *key, int64_t dflt)
{
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return dflt;
    mpack_type_t t = mpack_node_type(n);
    if (t == mpack_type_uint || t == mpack_type_int)
        return mpack_node_i64(n);
    return dflt;
}

bool msg_get_bool(mpack_node_t map, const char *key, bool dflt)
{
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return dflt;
    if (mpack_node_type(n) == mpack_type_bool) return mpack_node_bool(n);
    /* tolerate ints used as bools */
    if (mpack_node_type(n) == mpack_type_uint || mpack_node_type(n) == mpack_type_int)
        return mpack_node_i64(n) != 0;
    return dflt;
}

double msg_get_double(mpack_node_t map, const char *key, double dflt)
{
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return dflt;
    mpack_type_t t = mpack_node_type(n);
    if (t == mpack_type_double || t == mpack_type_float)
        return mpack_node_double(n);
    if (t == mpack_type_uint || t == mpack_type_int)
        return (double)mpack_node_i64(n);
    return dflt;
}

bool msg_get_str(mpack_node_t map, const char *key, char *out, size_t cap)
{
    if (cap == 0) return false;
    out[0] = '\0';
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return false;
    if (mpack_node_type(n) != mpack_type_str) return false;
    size_t slen = mpack_node_strlen(n);
    const char *s = mpack_node_str(n);
    if (!s) return false;
    if (slen >= cap) slen = cap - 1;
    memcpy(out, s, slen);
    out[slen] = '\0';
    return true;
}

/* Read a 6-byte MAC from `key`. The firmware sends MACs as msgpack BIN (6 raw
 * bytes), but older code / some fields send them as "aa:bb:cc:dd:ee:ff" strings.
 * Accept BOTH. Fills out[6]. Returns true on success. */
bool msg_get_mac6(mpack_node_t map, const char *key, uint8_t out[6])
{
    mpack_node_t n = mpack_node_map_cstr_optional(map, key);
    if (mpack_node_is_missing(n) || mpack_node_is_nil(n)) return false;
    mpack_type_t t = mpack_node_type(n);
    if (t == mpack_type_bin) {
        size_t len = mpack_node_bin_size(n);
        const char *d = mpack_node_bin_data(n);
        if (!d || len != 6) return false;
        memcpy(out, d, 6);
        return true;
    }
    if (t == mpack_type_str) {
        size_t slen = mpack_node_strlen(n);
        const char *s = mpack_node_str(n);
        if (!s) return false;
        /* parse hex pairs separated by :/- */
        int nb = 0;
        for (size_t i = 0; i < slen && nb < 6; ) {
            if (s[i] == ':' || s[i] == '-') { i++; continue; }
            int hi = -1, lo = -1;
            char c = s[i];
            if (c >= '0' && c <= '9') hi = c - '0';
            else if (c >= 'a' && c <= 'f') hi = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') hi = c - 'A' + 10;
            if (hi < 0) { i++; continue; }
            if (i + 1 < slen) {
                c = s[i+1];
                if (c >= '0' && c <= '9') lo = c - '0';
                else if (c >= 'a' && c <= 'f') lo = c - 'a' + 10;
                else if (c >= 'A' && c <= 'F') lo = c - 'A' + 10;
            }
            if (lo < 0) { out[nb++] = (uint8_t)hi; i++; }
            else { out[nb++] = (uint8_t)((hi << 4) | lo); i += 2; }
        }
        return nb == 6;
    }
    return false;
}
