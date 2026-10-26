/*
 * msg.h — MessagePack wire-frame helpers for the Pager socket protocol.
 *
 * Frame = [u32 cmd BE][u32 len BE][msgpack body]. This module owns:
 *   - reading a full frame off a socket fd (header hand-decoded BE, body raw)
 *   - writing a full frame to a socket fd (header hand-encoded BE)
 *   - parsing the body into an mpack tree (read side)
 *   - building the body into a growable mpack writer (write side)
 *   - small typed accessors for the exact msgpack keys used by the protocol
 *     (see ARQUITECTURA-PORT.md section 2).
 *
 * The 8-byte header is encoded/decoded by hand with ntohl/htonl; everything in
 * the body goes through the vendored mpack library.
 */
#ifndef PINEAPD_MSG_H
#define PINEAPD_MSG_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "../../vendor/mpack/mpack.h"

/* A received request frame: the command code plus the raw msgpack body. */
typedef struct {
    uint32_t cmd;
    char    *body;     /* malloc'd; may be NULL/0-length for empty body */
    size_t   body_len;
} msg_frame_t;

/* ---- framing on a socket fd -------------------------------------------- */
/* Read one full frame. Returns 0 on success (fills *out; caller frees
 * out->body), -1 on error/EOF. */
int  msg_read_frame(int fd, msg_frame_t *out);
void msg_frame_free(msg_frame_t *f);

/* Write one full frame (cmd + body bytes). Returns 0 on success. */
int  msg_write_frame(int fd, uint32_t cmd, const char *body, size_t body_len);

/* ---- body writer convenience ------------------------------------------- *
 * Build a response body into a growable buffer:
 *   char *data; size_t size;
 *   mpack_writer_t w;
 *   msg_body_begin(&w, &data, &size);
 *   ... write a map into w ...
 *   char *body = msg_body_finish(&w, &data, &size, &len);
 * msg_body_finish destroys the writer and, on success, returns `data` (the
 * malloc'd body; caller frees) with *len set. On error returns NULL, *len=0,
 * and frees any partial buffer.
 */
void  msg_body_begin(mpack_writer_t *w, char **data, size_t *size);
char *msg_body_finish(mpack_writer_t *w, char **data, size_t *size, size_t *len);

/* Write a "basic" response map {error:uint, message:str} into w (which must be
 * a fresh writer from msg_body_begin). Does NOT finish the writer. */
void  msg_write_basic(mpack_writer_t *w, uint64_t error, const char *message);

/* Convenience: build a complete basic response body in one call. Returns a
 * malloc'd buffer (caller frees), sets *len. */
char *msg_make_basic(uint64_t error, const char *message, size_t *len);

/* ---- body reader convenience ------------------------------------------- *
 * Parse a body into an mpack tree. Caller must mpack_tree_destroy() it. The
 * root node is returned via *root. Returns true on success (valid map), false
 * otherwise (root still valid-but-nil so accessors are safe to call).
 */
bool  msg_body_parse(mpack_tree_t *tree, const char *body, size_t len,
                     mpack_node_t *root);

/* Typed, NULL/absent-safe map accessors. Each returns the fallback when the key
 * is missing or the wrong type (mpack's optional + type guards). */
uint32_t msg_get_u32(mpack_node_t map, const char *key, uint32_t dflt);
int64_t  msg_get_i64(mpack_node_t map, const char *key, int64_t dflt);
bool     msg_get_bool(mpack_node_t map, const char *key, bool dflt);
double   msg_get_double(mpack_node_t map, const char *key, double dflt);
/* Copy a string value into out[cap] (always NUL-terminated). Returns true if
 * the key existed and was a string. */
bool     msg_get_str(mpack_node_t map, const char *key, char *out, size_t cap);
bool     msg_get_mac6(mpack_node_t map, const char *key, uint8_t out[6]);

#endif /* PINEAPD_MSG_H */
