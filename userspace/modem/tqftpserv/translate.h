#ifndef __TRANSLATE_H__
#define __TRANSLATE_H__

/*
 * rhodep: the persistent state directory. tqftpserv.c derives the per-instance
 * read/write roots from it, so it lives here rather than privately in
 * translate.c.
 */
#ifndef ANDROID
#define TQFTPSERV_STATE_DIR	"/var/lib/tqftpserv"
#else
#define TQFTPSERV_STATE_DIR	"/data/vendor/tmp/tqftpserv"
#endif

int translate_open(const char *path, int flags);

/*
 * rhodep: select which read/write root the next translate_open() resolves
 * /readwrite/ and /ramdumps/ against. Stock's tftp_server keeps one root per
 * QMI service instance (see tqftpserv.c, rfs_instances[]); this is how the
 * single-threaded server switches between them. Passing NULL restores the
 * default root.
 */
void translate_set_rw_root(const char *root);

#endif
