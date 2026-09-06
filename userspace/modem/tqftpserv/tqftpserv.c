// SPDX-License-Identifier: BSD-3-Clause
/*
 * Copyright (c) 2018, Linaro Ltd.
 */
#include <arpa/inet.h>
#include <sys/stat.h>
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <libqrtr.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "list.h"
#include "translate.h"

#define MAX(x, y) ((x) > (y) ? (x) : (y))

/* RFC 2348: TFTP Blocksize Option - valid range 8 to 65464 bytes */
#define MIN_BLKSIZE 8
#define MAX_BLKSIZE 65464

/* RFC 7440: TFTP Windowsize Option - valid range 1 to 65535 */
#define MIN_WSIZE 1
#define MAX_WSIZE 65535

/* Reasonable limits for custom options */
#define MAX_RSIZE (100 * 1024 * 1024)  /* 100 MB */
#define MIN_TIMEOUTMS 1
#define MAX_TIMEOUTMS 255000  /* 255 seconds */
#define MIN_SEEK 0

enum {
	OP_RRQ = 1,
	OP_WRQ,
	OP_DATA,
	OP_ACK,
	OP_ERROR,
	OP_OACK,
};

/* RFC 1350: TFTP Error Codes */
enum tftp_error {
	TFTP_ERROR_UNDEF = 0,		/* Not defined, see error message */
	TFTP_ERROR_ENOENT = 1,		/* File not found */
	TFTP_ERROR_EACCESS = 2,		/* Access violation */
	TFTP_ERROR_ENOSPACE = 3,	/* Disk full or allocation exceeded */
	TFTP_ERROR_EBADOP = 4,		/* Illegal TFTP operation */
	TFTP_ERROR_EBADID = 5,		/* Unknown transfer ID */
	TFTP_ERROR_EEXISTS = 6,		/* File already exists */
	TFTP_ERROR_ENOUSER = 7,		/* No such user */
	TFTP_ERROR_EOPTNEG = 8,		/* Option negotiation failed (RFC 2347) */
	ERROR_END_OF_TRANSFER = 9,	/* Custom: End of transfer (not an error) */
};

struct tftp_client {
	struct list_head node;

	struct sockaddr_qrtr sq;

	int sock;
	int fd;

	size_t block;

	size_t blksize;
	size_t rsize;
	size_t wsize;
	unsigned int timeoutms;
	off_t seek;

	uint8_t *blk_buf;
	uint8_t *rw_buf;
	size_t rw_buf_size;
	size_t blk_offset;
	uint16_t blk_expected;
};

static bool tftp_debug;

static struct list_head readers = LIST_INIT(readers);
static struct list_head writers = LIST_INIT(writers);

/*
 * rhodep: the QMI service instances a Qualcomm RFS server publishes.
 *
 * Stock's /vendor/bin/tftp_server registers QMI service 4096 twelve times, once
 * per remote file system root, and picks the root from the instance the request
 * arrived on. The table below is read verbatim out of that binary's .data
 * (rhodep's stock vendor image, table at vaddr 0x1a708, twelve {u32 instance,
 * char *root} pairs; the registration loop at 0x17860 walks 1..12 and passes
 * the instance straight into the QRTR NEW_SERVER packet).
 *
 * libqrtr encodes qrtr_publish(fd, service, version, instance) as
 * "instance << 8 | version", so the wire instance stock uses for
 * /vendor/rfs/msm/mpss -- 1 -- is what upstream tqftpserv already publishes as
 * qrtr_publish(fd, 4096, 1, 0). That is why the modem proper finds it. Every
 * other instance in this table has nothing listening on this port, and
 * qrtr-ns's server_match() requires an exact instance match, so a client that
 * looks one up gets no server at all -- and tqftpserv never sees, and never
 * logs, the request.
 *
 * `name` is the stock root under /vendor/rfs/. It is used to name the state
 * subdirectory, so /var/lib/tqftpserv/rfs_apq_gnss is visibly the same thing
 * as stock's /mnt/vendor/persist/rfs/apq/gnss.
 */
struct rfs_instance {
	unsigned int instance;
	const char *name;
};

static const struct rfs_instance rfs_instances[] = {
	{  1, "msm/mpss" },
	{  2, "msm/adsp" },
	{  3, "mdm/mpss" },
	{  4, "mdm/adsp" },
	{  5, "mdm/tn"   },
	{  6, "apq/gnss" },
	{  7, "msm/slpi" },
	{  8, "mdm/slpi" },
	{  9, "msm/cdsp" },
	{ 10, "mdm/cdsp" },
	{ 11, "msm/wpss" },
	{ 12, "mdm/wpss" },
};

#define RFS_INSTANCE_COUNT (sizeof(rfs_instances) / sizeof(rfs_instances[0]))

/* The instance upstream publishes, and stock's /vendor/rfs/msm/mpss. */
#define RFS_MPSS_INSTANCE 1

struct tftp_server {
	int fd;
	unsigned int instance;
	/* NULL keeps translate.c's default root, which is the mpss one. */
	char *rw_root;
};

static struct tftp_server servers[RFS_INSTANCE_COUNT];
static size_t server_count;

static void log_debug(const char *fmt, ...)
{
	va_list ap;

	if (!tftp_debug)
		return;

	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fflush(stderr);
}

static void log_err(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fflush(stderr);
}

static void log_info(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vfprintf(stdout, fmt, ap);
	va_end(ap);
	fflush(stderr);
}

static int sanitize_path(const char *path)
{
	const char *p;

	/* Check for "../" or "/../" */
	for (p = path; *p; p++) {
		if (p[0] == '.' && p[1] == '.' && p[2] == '/') {
			if (p == path || p[-1] == '/')
				return -1;
		}
	}

	return 0;
}

static int tftp_send_error(int sock, enum tftp_error code, const char *msg)
{
	size_t len;
	char *buf;
	int rc;

	len = 4 + strlen(msg) + 1;

	buf = calloc(1, len);
	if (!buf)
		return -1;

	*(uint16_t *)buf = htons(OP_ERROR);
	*(uint16_t *)(buf + 2) = htons(code);
	memcpy(buf + 4, msg, len - 4);

	rc = send(sock, buf, len, 0);
	free(buf);
	return rc;
}

/**
 * tftp_send_error_to() - send TFTP ERROR packet to a remote address
 * @sq: remote address to send error to
 * @code: TFTP error code
 * @msg: error message string
 *
 * Creates a temporary socket, connects to the remote address, sends an
 * ERROR packet, and closes the socket. This is used when we need to send
 * an error before establishing a client session.
 */
static void tftp_send_error_to(struct sockaddr_qrtr *sq, enum tftp_error code, const char *msg)
{
	int sock;
	int ret;

	sock = qrtr_open(0);
	if (sock < 0)
		return;

	ret = connect(sock, (struct sockaddr *)sq, sizeof(*sq));
	if (ret >= 0)
		tftp_send_error(sock, code, msg);

	close(sock);
}

static ssize_t tftp_send_data(struct tftp_client *client,
			      unsigned int block, size_t offset, size_t response_size)
{
	ssize_t len;
	size_t send_len;
	uint8_t *buf = client->blk_buf;
	uint8_t *p = buf;

	*p++ = 0;
	*p++ = OP_DATA;

	*p++ = (block >> 8) & 0xff;
	*p++ = block & 0xff;

	len = pread(client->fd, p, client->blksize, offset);
	if (len < 0) {
		log_err("failed to read data: %s\n", strerror(errno));
		tftp_send_error(client->sock, TFTP_ERROR_UNDEF, "Read error");
		return len;
	}

	p += len;

	/* If rsize was set, we should limit the data in the response to n bytes */
	if (response_size != 0) {
		/* Header (4 bytes) + data size */
		send_len = 4 + response_size;
		if (send_len > p - buf) {
			log_err("requested data of %ld bytes but only read %ld bytes from file, rejecting\n",
				response_size, len);
			return -EINVAL;
		}
	} else {
		send_len = p - buf;
	}

	log_debug("Sending %zd bytes of DATA to %d:%d\n", send_len, client->sq.sq_node, client->sq.sq_port);
	return send(client->sock, buf, send_len, 0);
}


static int tftp_send_ack(int sock, int block)
{
	struct {
		uint16_t opcode;
		uint16_t block;
	} ack = { htons(OP_ACK), htons(block) };

	return send(sock, &ack, sizeof(ack), 0);
}

static int tftp_send_oack(int sock, size_t *blocksize, size_t *tsize,
			  size_t *wsize, unsigned int *timeoutms, size_t *rsize,
			  off_t *seek)
{
	char buf[512];
	char *end = buf + sizeof(buf);
	char *p = buf;
	int n;

	*p++ = 0;
	*p++ = OP_OACK;

	if (blocksize) {
		if (p + 8 >= end)
			return -1;
		memcpy(p, "blksize", 8);
		p += 8;

		n = snprintf(p, end - p, "%zd", *blocksize);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	if (timeoutms) {
		if (p + 10 >= end)
			return -1;
		memcpy(p, "timeoutms", 10);
		p += 10;

		n = snprintf(p, end - p, "%d", *timeoutms);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	if (tsize && *tsize != -1) {
		if (p + 6 >= end)
			return -1;
		memcpy(p, "tsize", 6);
		p += 6;

		n = snprintf(p, end - p, "%zd", *tsize);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	if (wsize) {
		if (p + 6 >= end)
			return -1;
		memcpy(p, "wsize", 6);
		p += 6;

		n = snprintf(p, end - p, "%zd", *wsize);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	if (rsize) {
		if (p + 6 >= end)
			return -1;
		memcpy(p, "rsize", 6);
		p += 6;

		n = snprintf(p, end - p, "%zd", *rsize);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	if (seek) {
		if (p + 5 >= end)
			return -1;
		memcpy(p, "seek", 5);
		p += 5;

		n = snprintf(p, end - p, "%zd", *seek);
		if (n < 0 || n >= end - p)
			return -1;
		p += n;
		*p++ = '\0';
	}

	return send(sock, buf, p - buf, 0);
}

static int parse_options(const char *buf, size_t len, size_t *blksize,
			 ssize_t *tsize, size_t *wsize, unsigned int *timeoutms,
			 size_t *rsize, off_t *seek)
{
	const char *opt, *value;
	long long parsed_val;
	const char *end = buf + len;
	const char *p = buf;
	size_t value_len;
	size_t opt_len;
	char *endptr;

	while (p < end) {
		/* Ensure option string is null-terminated within buffer */
		opt = p;
		opt_len = strnlen(opt, end - p);
		if (opt_len == (size_t)(end - p)) {
			log_err("Malformed options: option not null-terminated\n");
			return -1;
		}
		p += opt_len + 1;

		/* Ensure we have space for value string */
		if (p >= end) {
			log_err("Malformed options: missing value\n");
			return -1;
		}

		/* Ensure value string is null-terminated within buffer */
		value = p;
		value_len = strnlen(value, end - p);
		if (value_len == (size_t)(end - p)) {
			log_err("Malformed options: value not null-terminated\n");
			return -1;
		}
		p += value_len + 1;

		/*
		 * blksize: block size - how many bytes to send at once
		 * timeoutms: timeout in milliseconds
		 * tsize: total size - request to get file size in bytes
		 * rsize: read size - how many bytes to send, not full file
		 * wsize: window size - how many blocks to send without ACK
		 * seek: offset from beginning of file in bytes to start reading
		 */
		if (!strcmp(opt, "blksize")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < MIN_BLKSIZE || parsed_val > MAX_BLKSIZE) {
				log_err("Invalid blksize value '%s' (must be %d-%d)\n",
					value, MIN_BLKSIZE, MAX_BLKSIZE);
				return -1;
			}
			*blksize = (size_t)parsed_val;
		} else if (!strcmp(opt, "timeoutms")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < MIN_TIMEOUTMS || parsed_val > MAX_TIMEOUTMS) {
				log_err("Invalid timeoutms value '%s' (must be %d-%d)\n",
					value, MIN_TIMEOUTMS, MAX_TIMEOUTMS);
				return -1;
			}
			*timeoutms = (unsigned int)parsed_val;
		} else if (!strcmp(opt, "tsize")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < 0) {
				log_err("Invalid tsize value '%s'\n", value);
				return -1;
			}
			*tsize = (ssize_t)parsed_val;
		} else if (!strcmp(opt, "rsize")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < 1 || parsed_val > MAX_RSIZE) {
				log_err("Invalid rsize value '%s' (must be 1-%d)\n",
					value, MAX_RSIZE);
				return -1;
			}
			*rsize = (size_t)parsed_val;
		} else if (!strcmp(opt, "wsize")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < MIN_WSIZE || parsed_val > MAX_WSIZE) {
				log_err("Invalid wsize value '%s' (must be %d-%d)\n",
					value, MIN_WSIZE, MAX_WSIZE);
				return -1;
			}
			*wsize = (size_t)parsed_val;
		} else if (!strcmp(opt, "seek")) {
			errno = 0;
			parsed_val = strtoll(value, &endptr, 10);
			if (errno != 0 || *endptr != '\0' || parsed_val < MIN_SEEK) {
				log_err("Invalid seek value '%s' (must be >= %d)\n",
					value, MIN_SEEK);
				return -1;
			}
			*seek = (off_t)parsed_val;
		} else {
			log_err("Ignoring unknown option '%s' with value '%s'\n", opt, value);
		}
	}

	return 0;
}

static void handle_rrq(const char *buf, size_t len, struct sockaddr_qrtr *sq)
{
	struct tftp_client *client;
	const char *filename;
	const char *mode;
	const char *p;
	const char *end = buf + len;
	size_t filename_len, mode_len;
	ssize_t tsize = -1;
	size_t blksize = 512;
	unsigned int timeoutms = 1000;
	size_t rsize = 0;
	size_t wsize = 1;
	off_t seek = 0;
	bool do_oack = false;
	int sock;
	int ret;
	int fd;

	p = buf + 2;

	/* Parse filename - ensure it's NUL-terminated within buffer */
	filename = p;
	filename_len = strnlen(filename, end - p);
	if (filename_len == (size_t)(end - p)) {
		log_err("RRQ: filename not NUL-terminated\n");
		return;
	}
	p += filename_len + 1;

	/* Parse mode - ensure it's NUL-terminated within buffer */
	if (p >= end) {
		log_err("RRQ: truncated packet, missing mode\n");
		return;
	}
	mode = p;
	mode_len = strnlen(mode, end - p);
	if (mode_len == (size_t)(end - p)) {
		log_err("RRQ: mode not NUL-terminated\n");
		return;
	}
	p += mode_len + 1;

	if (strcasecmp(mode, "octet")) {
		log_err("RRQ: unsupported mode '%s', rejecting\n", mode);
		tftp_send_error_to(sq, TFTP_ERROR_EBADOP, "Only octet mode supported");
		return;
	}

	/* Validate filename for path traversal attacks */
	if (sanitize_path(filename) < 0) {
		log_err("read with invalid filename requested from %d:%d: %s\n",
			sq->sq_node, sq->sq_port, filename);
		tftp_send_error_to(sq, TFTP_ERROR_EACCESS, "Access violation");
		return;
	}

	if (p < buf + len) {
		do_oack = true;
		ret = parse_options(p, len - (p - buf), &blksize, &tsize, &wsize,
				    &timeoutms, &rsize, &seek);
		if (ret < 0) {
			log_err("Invalid options in RRQ, rejecting\n");
			tftp_send_error_to(sq, TFTP_ERROR_EOPTNEG, "Option negotiation failed");
			return;
		}
	}

	log_debug("read request for %s (mode: %s, rsize: %d, seek: %d) from %d:%d\n",
		  filename, mode, rsize, seek, sq->sq_node, sq->sq_port);

	sock = qrtr_open(0);
	if (sock < 0) {
		log_err("unable to create new qrtr socket, reject\n");
		return;
	}

	ret = connect(sock, (struct sockaddr *)sq, sizeof(*sq));
	if (ret < 0) {
		log_err("unable to connect new qrtr socket to remote\n");
		goto out_close_sock;
		return;
	}

	fd = translate_open(filename, O_RDONLY);
	if (fd < 0) {
		log_err("unable to open %s (%d), reject\n", filename, errno);
		tftp_send_error(sock, TFTP_ERROR_ENOENT, "file not found");
		goto out_close_sock;
		return;
	}

	if (tsize != -1) {
		tsize = lseek(fd, 0, SEEK_END);
		if (tsize < 0) {
			log_err("unable to determine file size for %s: %s\n",
				filename, strerror(errno));
			tftp_send_error(sock, TFTP_ERROR_UNDEF, "Cannot determine file size");
			goto out_close_sock;
			return;
		}
		/* Reset file position to beginning */
		lseek(fd, 0, SEEK_SET);
	}

	client = calloc(1, sizeof(*client));
	client->sq = *sq;
	client->sock = sock;
	client->fd = fd;
	client->blksize = blksize;
	client->rsize = rsize;
	client->wsize = wsize;
	client->timeoutms = timeoutms;
	client->seek = seek;
	client->rw_buf_size = blksize * wsize;

	client->blk_buf = calloc(1, blksize + 4);
	if (!client->blk_buf) {
		log_err("Memory allocation failure\n");
		tftp_send_error(sock, TFTP_ERROR_UNDEF, "Resources temporary unavailable");
		goto out_free_client;
		return;
	}

	client->rw_buf = calloc(1, client->rw_buf_size);
	if (!client->rw_buf) {
		log_err("Memory allocation failure\n");
		tftp_send_error(sock, TFTP_ERROR_UNDEF, "Resources temporary unavailable");
		goto out_free_blk_buf;
		return;
	}

	log_info("%s opened for reading from %d:%d\n", filename, sq->sq_node, sq->sq_port);

	list_add(&readers, &client->node);

	if (do_oack) {
		tftp_send_oack(client->sock, &blksize,
			       tsize ? (size_t*)&tsize : NULL,
			       wsize ? &wsize : NULL,
			       &client->timeoutms,
			       rsize ? &rsize : NULL,
			       seek ? &seek : NULL);
	} else {
		tftp_send_data(client, 1, 0, 0);
	}

	return;

out_free_blk_buf:
	free(client->blk_buf);
out_free_client:
	free(client);
	close(fd);
out_close_sock:
	close(sock);
}

static void handle_wrq(const char *buf, size_t len, struct sockaddr_qrtr *sq)
{
	struct tftp_client *client;
	const char *filename;
	const char *mode;
	const char *p;
	const char *end = buf + len;
	size_t filename_len, mode_len;
	ssize_t tsize = -1;
	size_t blksize = 512;
	unsigned int timeoutms = 1000;
	size_t rsize = 0;
	size_t wsize = 1;
	off_t seek = 0;
	bool do_oack = false;
	int sock;
	int ret;
	int fd;

	p = buf + 2;

	/* Parse filename - ensure it's NUL-terminated within buffer */
	filename = p;
	filename_len = strnlen(filename, end - p);
	if (filename_len == (size_t)(end - p)) {
		log_err("WRQ: filename not NUL-terminated\n");
		return;
	}
	p += filename_len + 1;

	/* Parse mode - ensure it's NUL-terminated within buffer */
	if (p >= end) {
		log_err("WRQ: truncated packet, missing mode\n");
		return;
	}
	mode = p;
	mode_len = strnlen(mode, end - p);
	if (mode_len == (size_t)(end - p)) {
		log_err("WRQ: mode not NUL-terminated\n");
		return;
	}
	p += mode_len + 1;

	if (strcasecmp(mode, "octet")) {
		log_err("WRQ: unsupported mode '%s', rejecting\n", mode);
		tftp_send_error_to(sq, TFTP_ERROR_EBADOP, "Only octet mode supported");
		return;
	}

	log_debug("write request for %s (%s) from %d:%d\n", filename, mode, sq->sq_node, sq->sq_port);

	/* Validate filename for path traversal attacks */
	if (sanitize_path(filename) < 0) {
		log_err("write with invalid filename requested from %d:%d: %s\n",
			sq->sq_node, sq->sq_port, filename);
		tftp_send_error_to(sq, TFTP_ERROR_EACCESS, "Access violation");
		return;
	}

	if (p < buf + len) {
		do_oack = true;
		ret = parse_options(p, len - (p - buf), &blksize, &tsize, &wsize,
				    &timeoutms, &rsize, &seek);
		if (ret < 0) {
			log_err("Invalid options in WRQ, rejecting\n");
			tftp_send_error_to(sq, TFTP_ERROR_EOPTNEG, "Option negotiation failed");
			return;
		}
	}

	fd = translate_open(filename, O_WRONLY | O_CREAT);
	if (fd < 0) {
		log_debug("unable to open %s (%d), reject\n", filename, errno);
		tftp_send_error_to(sq, TFTP_ERROR_EACCESS, "Access violation");
		return;
	}

	sock = qrtr_open(0);
	if (sock < 0) {
		log_err("unable to create new qrtr socket, reject\n");
		goto out_close_fd;
		return;
	}

	ret = connect(sock, (struct sockaddr *)sq, sizeof(*sq));
	if (ret < 0) {
		log_err("unable to connect new qrtr socket to remote\n");
		goto out_close_sock;
		return;
	}

	client = calloc(1, sizeof(*client));
	client->sq = *sq;
	client->sock = sock;
	client->fd = fd;
	client->blksize = blksize;
	client->rsize = rsize;
	client->wsize = wsize;
	client->timeoutms = timeoutms;
	client->seek = seek;
	client->rw_buf_size = blksize * wsize;
	client->blk_expected = 1;

	client->blk_buf = calloc(1, blksize + 4);
	if (!client->blk_buf) {
		log_err("Memory allocation failure\n");
		tftp_send_error(sock, TFTP_ERROR_UNDEF, "Resources temporary unavailable");
		goto out_free_client;
		return;
	}

	client->rw_buf = calloc(1, client->rw_buf_size);
	if (!client->rw_buf) {
		log_err("Memory allocation failure\n");
		tftp_send_error(sock, TFTP_ERROR_UNDEF, "Resources temporary unavailable");
		goto out_free_blk_buf;
		return;
	}

	log_info("%s opened for writing from %d:%d\n", filename, sq->sq_node, sq->sq_port);

	list_add(&writers, &client->node);

	if (do_oack) {
		tftp_send_oack(client->sock, &blksize,
			       tsize ? (size_t*)&tsize : NULL,
			       wsize ? &wsize : NULL,
			       &client->timeoutms,
			       rsize ? &rsize : NULL,
			       seek ? &seek : NULL);
	} else {
		tftp_send_data(client, 1, 0, 0);
	}

	return;

out_free_blk_buf:
	free(client->blk_buf);
out_free_client:
	free(client);
out_close_fd:
	close(fd);
out_close_sock:
	close(sock);
}

static int handle_reader(struct tftp_client *client)
{
	struct sockaddr_qrtr sq;
	uint16_t block;
	uint16_t last;
	char buf[128];
	socklen_t sl;
	ssize_t len;
	ssize_t n = 0;
	int opcode;
	int ret;

	sl = sizeof(sq);
	len = recvfrom(client->sock, buf, sizeof(buf), 0, (void *)&sq, &sl);
	if (len < 0) {
		ret = -errno;
		if (ret != -ENETRESET)
			log_err("recvfrom failed when handling reader: %d\n", ret);
		return -1;
	}

	/* Drop unsolicited messages */
	if (sq.sq_node != client->sq.sq_node ||
	    sq.sq_port != client->sq.sq_port) {
		log_debug("Discarding spoofed message\n");
		return -1;
	}

	opcode = buf[0] << 8 | buf[1];
	if (opcode == OP_ERROR) {
		buf[len] = '\0';
		int err = buf[2] << 8 | buf[3];
		/* "End of Transfer" is not an error, used with stat(2)-like calls */
		if (err == ERROR_END_OF_TRANSFER)
			log_info("Reader received end of transfer from %d:%d\n", sq.sq_node, sq.sq_port);
		else
			log_err("Remote returned an error: %d - %s\n", err, buf + 4);
		return -1;
	} else if (opcode != OP_ACK) {
		log_err("Expected ACK, got %d\n", opcode);
		tftp_send_error(client->sock, TFTP_ERROR_EBADOP, "Expected ACK opcode");
		return -1;
	}

	last = buf[2] << 8 | buf[3];
	log_debug("Got ack for %d from %d:%d\n", last, sq.sq_node, sq.sq_port);

	/* We've sent enough data for rsize already */
	if (last * client->blksize > client->rsize)
		return 0;

	for (block = last; block < last + client->wsize; block++) {
		size_t offset = client->seek + block * client->blksize;
		size_t response_size = 0;
		/* Check if need to limit response size based for requested rsize */
		if ((block + 1) * client->blksize > client->rsize)
			response_size = client->rsize % client->blksize;

		n = tftp_send_data(client, block + 1,
				   offset, response_size);
		if (n < 0) {
			log_err("Sent block %d failed: %zd\n", block + 1, n);
			break;
		}
		log_debug("Sent block %d of %zd to %d:%d\n", block + 1, n, sq.sq_node, sq.sq_port);
		if (n == 0)
			break;
		/* We've sent enough data for rsize already */
		if ((block + 1) * client->blksize > client->rsize)
			break;
	}

	return 1;
}

static int handle_writer(struct tftp_client *client)
{
	struct sockaddr_qrtr sq;
	uint16_t block;
	size_t payload;
	uint8_t *buf = client->blk_buf;
	socklen_t sl;
	ssize_t len;
	int opcode;
	int ret;

	sl = sizeof(sq);
	len = recvfrom(client->sock, buf, client->blksize + 4, 0, (void *)&sq, &sl);
	if (len < 0) {
		ret = -errno;
		if (ret != -ENETRESET)
			log_err("recvfrom failed when handling writer: %d\n", ret);
		return -1;
	}

	/* Drop unsolicited messages */
	if (sq.sq_node != client->sq.sq_node ||
	    sq.sq_port != client->sq.sq_port)
		return -1;

	opcode = buf[0] << 8 | buf[1];
	block = buf[2] << 8 | buf[3];
	if (opcode != OP_DATA) {
		log_err("Expected DATA opcode, got %d\n", opcode);
		tftp_send_error(client->sock, TFTP_ERROR_EBADOP, "Expected DATA opcode");
		return -1;
	}

	payload = len - 4;
	buf += 4;

	/* Check if we recieved expected block */
	if (block != client->blk_expected) {
		uint16_t blk_expected = client->blk_expected;

		log_err("Block number out of sequence: %d (expected %d)\n", block, blk_expected);
		tftp_send_error(client->sock, TFTP_ERROR_EBADOP, "Block number out of sequence");

		/* Set blk_expected to beginning of current window */
		if ((blk_expected % client->wsize) == 0)
			blk_expected -= client->wsize + 1;
		else
			blk_expected -= (blk_expected % client->wsize) - 1;

		client->blk_expected = blk_expected;
		client->blk_offset = 0;

		return -1;
	}

	client->blk_expected++;

	/* Copy the data to the destination buffer */
	memcpy(client->rw_buf + client->blk_offset, buf, payload);
	client->blk_offset += payload;

	/* Write to file if all the wsize blocks are recieved
	 * or received buffer that is less than blksize */
	if ((block % client->wsize == 0) || (payload < client->blksize)) {
		ret = write(client->fd, client->rw_buf, client->blk_offset);
		if (ret < 0) {
			log_err("failed to write data: %s\n", strerror(errno));
			tftp_send_error(client->sock, TFTP_ERROR_ENOSPACE, "Disk full or write error");
			return -1;
		}

		client->blk_offset = 0;
		tftp_send_ack(client->sock, block);
	}

	return payload == client->blksize ? 1 : 0;
}

static void client_close_and_free(struct tftp_client *client)
{
	list_del(&client->node);
	close(client->sock);
	close(client->fd);
	free (client->blk_buf);
	free (client->rw_buf);
	free(client);
}

static void print_usage(void)
{
	extern const char *__progname;
	size_t i;

	fprintf(stderr, "Usage: %s [-d] [-i instance[,instance...]] [-h]\n", __progname);
	fprintf(stderr, " -d\tPrint detailed debug information\n");
	fprintf(stderr, " -i\tAlso publish QMI service 4096 on these RFS instances\n");
	fprintf(stderr, " -h\tPrint this usage info\n");
	fprintf(stderr, "\nRFS instances, as registered by the stock tftp_server:\n");
	for (i = 0; i < RFS_INSTANCE_COUNT; i++)
		fprintf(stderr, "  %2u  /vendor/rfs/%s%s\n",
			rfs_instances[i].instance, rfs_instances[i].name,
			rfs_instances[i].instance == RFS_MPSS_INSTANCE ?
				"  (always published)" : "");
}

/**
 * rfs_instance_name() - stock /vendor/rfs/ root for a QMI service instance
 *
 * Return: the root name, or NULL if the instance is not one stock uses.
 */
static const char *rfs_instance_name(unsigned int instance)
{
	size_t i;

	for (i = 0; i < RFS_INSTANCE_COUNT; i++)
		if (rfs_instances[i].instance == instance)
			return rfs_instances[i].name;

	return NULL;
}

/**
 * server_add() - open a qrtr socket and publish service 4096 on @instance
 *
 * rhodep: one socket per instance is required, not one socket published
 * repeatedly: qrtr-ns keys its server map on the port (ns.c, server_add() ->
 * map_reput(&node->services, hash_u32(port), ...)), so a second NEW_SERVER
 * from the same port replaces the first rather than adding to it.
 *
 * The read/write root is the default one for the mpss instance, so nothing
 * about the working modem path changes, and a per-instance state directory for
 * every other instance, mirroring stock's one-persist-directory-per-root.
 *
 * Return: 0 on success, -1 otherwise
 */
static int server_add(unsigned int instance)
{
	const char *name;
	char *root = NULL;
	char *p;
	int ret;
	int fd;

	if (server_count == RFS_INSTANCE_COUNT) {
		fprintf(stderr, "too many instances requested\n");
		return -1;
	}

	name = rfs_instance_name(instance);
	if (!name) {
		fprintf(stderr, "instance %u is not an RFS instance, refusing\n",
			instance);
		return -1;
	}

	if (instance != RFS_MPSS_INSTANCE) {
		ret = asprintf(&root, "%s/rfs_%s", TQFTPSERV_STATE_DIR, name);
		if (ret < 0) {
			fprintf(stderr, "failed to build state directory name\n");
			return -1;
		}
		/*
		 * "apq/gnss" -> "apq_gnss": one flat directory, not a
		 * hierarchy, so translate_persistent()'s single mkdir() is
		 * enough to create it.
		 */
		for (p = root + strlen(TQFTPSERV_STATE_DIR) + 1; *p; p++)
			if (*p == '/')
				*p = '_';
	}

	fd = qrtr_open(0);
	if (fd < 0) {
		fprintf(stderr, "failed to open qrtr socket for instance %u\n",
			instance);
		free(root);
		return -1;
	}

	ret = qrtr_publish(fd, 4096, instance, 0);
	if (ret < 0) {
		fprintf(stderr, "failed to publish service 4096 instance %u\n",
			instance);
		close(fd);
		free(root);
		return -1;
	}

	servers[server_count].fd = fd;
	servers[server_count].instance = instance;
	servers[server_count].rw_root = root;
	server_count++;

	log_debug("publishing service 4096 instance %u for /vendor/rfs/%s (%s)\n",
		  instance, name, root ? root : TQFTPSERV_STATE_DIR);

	return 0;
}

/**
 * server_add_list() - add every instance in a comma separated list
 *
 * Return: 0 on success, -1 otherwise
 */
static int server_add_list(const char *list)
{
	unsigned long instance;
	const char *p = list;
	char *end;

	while (*p) {
		errno = 0;
		instance = strtoul(p, &end, 10);
		if (end == p || errno || instance > 0xffff) {
			fprintf(stderr, "invalid instance list '%s'\n", list);
			return -1;
		}

		if (instance != RFS_MPSS_INSTANCE &&
		    server_add((unsigned int)instance) < 0)
			return -1;

		p = end;
		if (*p == ',')
			p++;
		else if (*p)  {
			fprintf(stderr, "invalid instance list '%s'\n", list);
			return -1;
		}
	}

	return 0;
}

int main(int argc, char **argv)
{
	struct tftp_client *client;
	struct tftp_client *next;
	struct sockaddr_qrtr sq;
	struct qrtr_packet pkt;
	socklen_t sl;
	ssize_t len;
	char buf[4096];
	fd_set rfds;
	const char *extra_instances = NULL;
	size_t i;
	int nfds;
	int opcode;
	int opt;
	int ret;
	int fd;

	while ((opt = getopt(argc, argv, "dhi:")) != -1) {
		switch (opt) {
		case 'd':
			tftp_debug = true;
			break;
		case 'i':
			extra_instances = optarg;
			break;
		case 'h':
			print_usage();
			return 0;
		default:
			print_usage();
			return 1;
		}
	}

	if (optind != argc) {
		print_usage();
		return 1;
	}

	/*
	 * rhodep: instance 1 is stock's /vendor/rfs/msm/mpss and is what
	 * upstream publishes, so it is unconditional and keeps the default
	 * read/write root. Anything else is opt-in via -i.
	 */
	if (server_add(RFS_MPSS_INSTANCE) < 0)
		exit(1);

	if (extra_instances && server_add_list(extra_instances) < 0)
		exit(1);

	for (;;) {
		FD_ZERO(&rfds);
		nfds = 0;

		for (i = 0; i < server_count; i++) {
			FD_SET(servers[i].fd, &rfds);
			nfds = MAX(nfds, servers[i].fd);
		}

		list_for_each_entry(client, &writers, node) {
			FD_SET(client->sock, &rfds);
			nfds = MAX(nfds, client->sock);
		}

		list_for_each_entry(client, &readers, node) {
			FD_SET(client->sock, &rfds);
			nfds = MAX(nfds, client->sock);
		}

		ret = select(nfds + 1, &rfds, NULL, NULL, NULL);
		if (ret < 0) {
			if (errno == EINTR) {
				continue;
			} else {
				log_err("select failed\n");
				break;
			}
		}

		list_for_each_entry_safe(client, next, &writers, node) {
			if (FD_ISSET(client->sock, &rfds)) {
				ret = handle_writer(client);
				if (ret <= 0)
					client_close_and_free(client);
			}
		}

		list_for_each_entry_safe(client, next, &readers, node) {
			if (FD_ISSET(client->sock, &rfds)) {
				ret = handle_reader(client);
				if (ret <= 0)
					client_close_and_free(client);
			}
		}

		for (i = 0; i < server_count; i++) {
			fd = servers[i].fd;

			if (!FD_ISSET(fd, &rfds))
				continue;

			/*
			 * rhodep: point translate.c at this instance's root
			 * before the request is dispatched. The server is
			 * single-threaded and translate_open() runs to
			 * completion inside handle_rrq()/handle_wrq(), so the
			 * root only has to be right for the duration of the
			 * open; everything after that uses the already-open fd.
			 */
			translate_set_rw_root(servers[i].rw_root);

			sl = sizeof(sq);
			len = recvfrom(fd, buf, sizeof(buf), 0, (void *)&sq, &sl);
			if (len < 0) {
				ret = -errno;
				if (ret != -ENETRESET)
					fprintf(stderr, "recvfrom failed on control socket: %d\n", ret);
				return ret;
			}

			/* Ignore control messages */
			if (sq.sq_port == QRTR_PORT_CTRL) {
				ret = qrtr_decode(&pkt, buf, len, &sq);
				if (ret < 0) {
					fprintf(stderr, "unable to decode qrtr packet\n");
					return ret;
				}

				switch (pkt.type) {
				case QRTR_TYPE_BYE:
					log_debug("got bye for %d\n", pkt.node);
					list_for_each_entry_safe(client, next, &writers, node) {
						if (client->sq.sq_node == sq.sq_node)
							client_close_and_free(client);
					}
					break;
				case QRTR_TYPE_DEL_CLIENT:
					log_debug("got del_client for %d:%d\n", pkt.node, pkt.port);
					list_for_each_entry_safe(client, next, &writers, node) {
						if (!memcmp(&client->sq, &sq, sizeof(sq)))
							client_close_and_free(client);
					}
					break;
				}
			} else {
				if (len < 2)
					continue;

				/*
				 * rhodep: worth a line of its own. Everything
				 * seen on this port so far has arrived on the
				 * mpss instance; a request on any other one is
				 * a client that had no server at all before.
				 */
				if (servers[i].instance != RFS_MPSS_INSTANCE)
					log_debug("request on RFS instance %u (/vendor/rfs/%s) from %d:%d\n",
						  servers[i].instance,
						  rfs_instance_name(servers[i].instance),
						  sq.sq_node, sq.sq_port);

				opcode = buf[0] << 8 | buf[1];
				switch (opcode) {
				case OP_RRQ:
					handle_rrq(buf, len, &sq);
					break;
				case OP_WRQ:
					handle_wrq(buf, len, &sq);
					break;
				case OP_ERROR:
					buf[len] = '\0';
					log_err("received error %d from %d:%d: %s\n",
						buf[2] << 8 | buf[3], sq.sq_node, sq.sq_port, buf + 4);
					break;
				default:
					log_err("unhandled op %d from %d:%d\n",
						opcode, sq.sq_node, sq.sq_port);
					break;
				}
			}
		}
	}

	for (i = 0; i < server_count; i++) {
		close(servers[i].fd);
		free(servers[i].rw_root);
	}

	return 0;
}
