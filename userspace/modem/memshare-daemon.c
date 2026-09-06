/*
 * Answer the modem's memshare (QMI service 52) requests.
 *
 * Established by scripts/modem/memshare-probe.c, on this device, at boot:
 *
 *     packet from node=0 port=7, 21 bytes
 *     00 0100 2400 0e00 | 01 0400 01000000 | 10 0400 00000000
 *     QMI REQUEST txn=1 msg_id=0x0024 (MEM_QUERY_SIZE) client_id=1 proc=0
 *
 * The modem asks the application processor how much shared memory it can have,
 * exactly once, during bring-up, and on mainline nothing answers because there
 * is no memshare implementation at all. The stock device tree declares
 * qcom,memshare with an 8 MB region and a client marked qcom,allocate-boot-time,
 * so this is a conversation the modem is entitled to expect.
 *
 * This daemon is deliberately the *minimal* answer: it reports success with a
 * size of zero rather than handing out memory. The point is to find out whether
 * the modem is blocked on receiving an answer at all, which is cheap to test,
 * before taking on the much larger job of allocating physical memory from a
 * reserved region and handing over addresses. If the modem completes
 * initialisation after this, the remaining work is well defined. If it does
 * not, memshare is not the blocker and the next lead is somewhere else.
 *
 * Build on the phone:
 *     sudo apt install libqrtr-dev
 *     gcc -O2 -Wall -o memshare-daemon memshare-daemon.c -lqrtr
 *
 * SAFETY: this daemon will not hand out an address unless something on the
 * running system demonstrably owns it -- either a no-map reservation in the live
 * device tree (find_reserved_region()) or a region allocated and assigned to the
 * modem's VMID by rhodep_memassign.ko (find_module_region()). If neither is
 * present it offers zero, whatever it was told on the command line. The reasons
 * are written out at both functions.
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <libqrtr.h>

#define MEMSHARE_SERVICE   52
#define MEMSHARE_VERSION   1
#define MEMSHARE_INSTANCE  0

/* Downstream memshare message ids. */
#define MEM_ALLOC_REQ          0x0020
#define MEM_FREE_REQ           0x0021
#define MEM_ALLOC_GENERIC_REQ  0x0022
#define MEM_FREE_GENERIC_REQ   0x0023
#define MEM_QUERY_SIZE_REQ     0x0024

/* QMI_REQUEST and QMI_RESPONSE come from libqrtr.h. */

#define TLV_RESULT    0x02
#define TLV_SIZE      0x10
#define TLV_SEQUENCE  0x10
#define TLV_ADDR_INFO 0x11

/*
 * The region handed to the modem. It must be reserved in the device tree, or
 * Linux will happily use it as ordinary RAM and the modem will corrupt it:
 *
 *     memshare@8ab00000 {
 *             reg = <0x00 0x8ab00000 0x00 0x800000>;
 *             no-map;
 *     };
 *
 * 8 MB in the hole between pil-gpu-ucode and pil-mpss-wlan. A fixed address is
 * used instead of stock's dynamically placed shared-dma-pool so that this can
 * stay in userspace: there is nothing to allocate, only a number to hand over.
 *
 * These two constants are only a documented default now. The address that is
 * actually handed out is read from the live device tree at start-up, and if the
 * tree does not reserve it nothing is handed out at all -- see
 * find_reserved_region().
 */
#define REGION_BASE   0x8ab00000ULL
#define REGION_SIZE   0x800000U

#define DT_RESERVED   "/proc/device-tree/reserved-memory"

/*
 * The second place a legitimate region can come from: kernel/diag-modules/
 * rhodep_memassign.ko loaded with alloc=1. It allocates the buffer itself,
 * holds it for its whole lifetime so nothing else can use it, moves it from
 * QCOM_SCM_VMID_HLOS to QCOM_SCM_VMID_MSS_MSA with qcom_scm_assign_mem(), and
 * only then publishes it here. That covers both halves of what makes an address
 * safe to hand over -- Linux is not using the pages, and the XPU lets the modem
 * write to them -- without a device tree change, which needs a new boot image.
 */
#define MOD_REGION    "/sys/kernel/rhodep_memshare"

/* The region, filled in by main() from one of the two sources above. */
static uint64_t region_base = REGION_BASE;
static uint64_t region_size;      /* 0 means "no usable region found" */
static char     region_node[256];

static volatile sig_atomic_t stop;

static void on_signal(int signo)
{
	(void)signo;
	stop = 1;
}

static ssize_t slurp(const char *path, unsigned char *buf, size_t max)
{
	ssize_t n;
	int fd = open(path, O_RDONLY);

	if (fd < 0)
		return -1;
	n = read(fd, buf, max);
	close(fd);
	return n;
}

static uint32_t be32(const unsigned char *p)
{
	return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
	       ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static uint64_t be_cells(const unsigned char *p, int cells)
{
	uint64_t v = 0;
	int i;

	for (i = 0; i < cells; i++)
		v = (v << 32) | be32(p + i * 4);
	return v;
}

/*
 * Find the memshare reservation in the LIVE device tree, and refuse to work
 * from anything else.
 *
 * This exists because the failure it prevents is silent and expensive. The
 * address below is a compile-time constant; the reservation that makes it safe
 * lives in the device tree, which is in the boot image, which is flashed
 * separately from this binary. The two can trivially get out of step -- a
 * rootfs update, a rollback to an older boot image, a rescue image -- and when
 * they do, this daemon would hand the modem 0x8ab00000 while Linux is using it
 * as ordinary heap. Nothing reports that. The modem writes, some unrelated
 * allocation is corrupted, and the symptom appears somewhere else entirely.
 *
 * A config flag would not help: the flag would live in the rootfs too, and
 * would be exactly as likely to disagree with the flashed device tree. Reading
 * the tree is the only check that cannot be wrong, because the tree is the
 * thing being asserted about.
 *
 * The node is matched by name prefix rather than by address, so the daemon
 * follows the device tree if the region ever moves.
 */
static bool find_reserved_region(void)
{
	unsigned char cells[4];
	int na = 2, ns = 2;
	DIR *d;
	struct dirent *e;
	bool found = false;

	if (slurp(DT_RESERVED "/#address-cells", cells, 4) == 4)
		na = (int)be32(cells);
	if (slurp(DT_RESERVED "/#size-cells", cells, 4) == 4)
		ns = (int)be32(cells);
	if (na < 1 || na > 2 || ns < 1 || ns > 2) {
		printf("memshare: reserved-memory has #address-cells=%d #size-cells=%d, "
		       "which this does not know how to read\n", na, ns);
		return false;
	}

	d = opendir(DT_RESERVED);
	if (!d) {
		printf("memshare: cannot read %s: %s\n", DT_RESERVED, strerror(errno));
		return false;
	}

	while ((e = readdir(d)) != NULL) {
		unsigned char reg[32];
		char path[512];
		struct stat st;
		ssize_t n;

		if (strncmp(e->d_name, "memshare", 8) != 0)
			continue;

		snprintf(path, sizeof(path), DT_RESERVED "/%s/reg", e->d_name);
		n = slurp(path, reg, sizeof(reg));
		if (n < (ssize_t)((na + ns) * 4)) {
			printf("memshare: %s has no usable reg property\n", e->d_name);
			continue;
		}

		/*
		 * no-map is not optional. A "reusable" or plain reserved region
		 * is still mapped by the kernel and can still be handed to a
		 * driver; only no-map keeps Linux out of it entirely.
		 */
		snprintf(path, sizeof(path), DT_RESERVED "/%s/no-map", e->d_name);
		if (stat(path, &st) != 0) {
			printf("memshare: %s is reserved but not no-map; not usable\n",
			       e->d_name);
			continue;
		}

		region_base = be_cells(reg, na);
		region_size = be_cells(reg + na * 4, ns);
		snprintf(region_node, sizeof(region_node), "%s", e->d_name);
		found = true;
		break;
	}

	closedir(d);
	return found;
}

/* Read one small text file and turn it into a number; base 0 so 0x... works. */
static bool read_u64(const char *path, uint64_t *out)
{
	unsigned char buf[64];
	char *end;
	ssize_t n = slurp(path, buf, sizeof(buf) - 1);

	if (n <= 0)
		return false;
	buf[n] = '\0';
	errno = 0;
	*out = strtoull((char *)buf, &end, 0);
	if (errno || end == (char *)buf)
		return false;
	return true;
}

/*
 * Find a region published by rhodep_memassign.ko.
 *
 * The check that matters is "assigned": the module creates this directory only
 * after qcom_scm_assign_mem() has returned 0, and the attribute is a fact about
 * that call rather than about what the module was asked to do. A region whose
 * ownership has not actually moved to VMID_MSS_MSA is exactly as dangerous as
 * an unreserved one -- the modem writes, the XPU refuses, and on this SoC that
 * is a silent whole-SoC reset -- so anything short of 1 is treated as "no
 * region", not as "probably fine".
 *
 * This does not replace find_reserved_region(). A device tree reservation is
 * still better, because no-map memory is never mapped by Linux at all, whereas
 * the module's buffer keeps a cacheable linear alias. The device tree is tried
 * first for that reason; this is the fallback for images whose boot image
 * predates the reservation.
 */
static bool find_module_region(void)
{
	uint64_t assigned = 0, mbase = 0, msize = 0;

	if (!read_u64(MOD_REGION "/assigned", &assigned))
		return false;              /* module not loaded: normal, quiet */

	if (assigned != 1) {
		printf("memshare: %s exists but assigned=%llu; the region is not "
		       "the modem's, refusing to use it\n",
		       MOD_REGION, (unsigned long long)assigned);
		return false;
	}

	if (!read_u64(MOD_REGION "/base", &mbase) ||
	    !read_u64(MOD_REGION "/size", &msize)) {
		printf("memshare: %s has no readable base/size\n", MOD_REGION);
		return false;
	}
	if (!mbase || !msize) {
		printf("memshare: %s reports base 0x%llx size %llu; unusable\n",
		       MOD_REGION, (unsigned long long)mbase,
		       (unsigned long long)msize);
		return false;
	}

	region_base = mbase;
	region_size = msize;
	snprintf(region_node, sizeof(region_node), "rhodep_memassign");
	return true;
}

static const char *msg_name(uint16_t id)
{
	switch (id) {
	case MEM_ALLOC_REQ:         return "MEM_ALLOC";
	case MEM_FREE_REQ:          return "MEM_FREE";
	case MEM_ALLOC_GENERIC_REQ: return "MEM_ALLOC_GENERIC";
	case MEM_FREE_GENERIC_REQ:  return "MEM_FREE_GENERIC";
	case MEM_QUERY_SIZE_REQ:    return "MEM_QUERY_SIZE";
	default:                    return "unknown";
	}
}

/*
 * Build a QMI response carrying a result TLV, and optionally a size TLV.
 * result 0 / error 0 is success.
 */
static unsigned int build_response(unsigned char *buf, uint16_t txn,
				   uint16_t msg_id, uint16_t result,
				   uint16_t error, bool with_size,
				   uint32_t size)
{
	unsigned int payload = 7 + (with_size ? 7 : 0);
	unsigned int i = 0;

	buf[i++] = QMI_RESPONSE;
	memcpy(buf + i, &txn, 2);      i += 2;
	memcpy(buf + i, &msg_id, 2);   i += 2;
	memcpy(buf + i, &payload, 2);  i += 2;

	buf[i++] = TLV_RESULT;
	buf[i++] = 0x04;
	buf[i++] = 0x00;
	memcpy(buf + i, &result, 2);   i += 2;
	memcpy(buf + i, &error, 2);    i += 2;

	if (with_size) {
		buf[i++] = TLV_SIZE;
		buf[i++] = 0x04;
		buf[i++] = 0x00;
		memcpy(buf + i, &size, 4); i += 4;
	}

	return i;
}

/* The modem echoes a sequence id back in the response; pull it out of TLV 4. */
static uint32_t parse_sequence_id(const unsigned char *buf, int len)
{
	int i = 7;

	while (i + 3 <= len) {
		unsigned char type = buf[i];
		uint16_t tlen;

		memcpy(&tlen, buf + i + 1, 2);
		if (type == 0x04 && tlen == 4 && i + 3 + 4 <= len) {
			uint32_t seq;

			memcpy(&seq, buf + i + 3, 4);
			return seq;
		}
		i += 3 + tlen;
	}

	return 0;
}

/*
 * mem_alloc_generic_resp_msg_v01: result, the echoed sequence id, and an array
 * of address/size pairs. The array is a QMI array, so it carries a one byte
 * count before the entries, and each entry is a 64 bit physical address
 * followed by a 32 bit length.
 */
static unsigned int build_alloc_response(unsigned char *buf, uint16_t txn,
					 uint16_t msg_id, uint32_t sequence_id,
					 uint64_t phys, uint32_t size)
{
	uint16_t payload = 7 + 7 + (3 + 1 + 12);
	uint16_t zero = 0;
	unsigned int i = 0;

	buf[i++] = QMI_RESPONSE;
	memcpy(buf + i, &txn, 2);      i += 2;
	memcpy(buf + i, &msg_id, 2);   i += 2;
	memcpy(buf + i, &payload, 2);  i += 2;

	buf[i++] = TLV_RESULT;
	buf[i++] = 0x04;
	buf[i++] = 0x00;
	memcpy(buf + i, &zero, 2);     i += 2;   /* result: success */
	memcpy(buf + i, &zero, 2);     i += 2;   /* error */

	buf[i++] = TLV_SEQUENCE;
	buf[i++] = 0x04;
	buf[i++] = 0x00;
	memcpy(buf + i, &sequence_id, 4); i += 4;

	buf[i++] = TLV_ADDR_INFO;
	buf[i++] = 0x0d;                         /* 1 + 12 */
	buf[i++] = 0x00;
	buf[i++] = 0x01;                         /* one entry */
	memcpy(buf + i, &phys, 8);     i += 8;
	memcpy(buf + i, &size, 4);     i += 4;

	return i;
}

int main(int argc, char **argv)
{
	/* Size to advertise for MEM_QUERY_SIZE. Zero means "nothing available",
	 * which is the safe default; pass a size to find out whether the modem
	 * follows up with an allocation request when offered memory.
	 *
	 * Whatever is asked for here is capped, or refused outright, by what the
	 * device tree actually reserves. Stock answers 0x500000 for client-id 1
	 * (qcom,peripheral-size in qcom,client_3), so that is the size to pass to
	 * reproduce stock behaviour. */
	uint32_t offer;
	bool check_only = argc > 1 && strcmp(argv[1], "check") == 0;
	unsigned char req[4096];
	unsigned char resp[64];
	uint32_t node;
	uint32_t port;
	int sock;
	int ret;

	offer = (argc > 1 && !check_only) ? (uint32_t)strtoul(argv[1], NULL, 0) : 0;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	setvbuf(stdout, NULL, _IOLBF, 0);

	/*
	 * "check" reports what the guard sees and exits, without touching QRTR.
	 * It is the safe way to ask a running system whether an image is one on
	 * which a non-zero offer would be honoured, and it is safe to run while
	 * the real daemon is up, because it never publishes the service.
	 */
	if (check_only) {
		if (find_reserved_region()) {
			printf("memshare: reserved region %s: 0x%llx + 0x%llx (no-map)\n",
			       region_node,
			       (unsigned long long)region_base,
			       (unsigned long long)region_size);
			return 0;
		}
		printf("memshare: no no-map 'memshare*' node under %s\n", DT_RESERVED);
		if (find_module_region()) {
			printf("memshare: module region %s: 0x%llx + 0x%llx "
			       "(allocated and assigned to the modem VMID)\n",
			       region_node,
			       (unsigned long long)region_base,
			       (unsigned long long)region_size);
			return 0;
		}
		printf("memshare: no assigned region at %s either; "
		       "a non-zero offer would be refused\n", MOD_REGION);
		return 1;
	}

	/*
	 * The guard. Everything this daemon can safely promise is bounded by
	 * what the flashed device tree reserves, so establish that first and
	 * say so in the log, on every boot, whichever way it goes.
	 */
	if (find_reserved_region()) {
		printf("memshare: reserved region %s: 0x%llx + 0x%llx (no-map)\n",
		       region_node,
		       (unsigned long long)region_base,
		       (unsigned long long)region_size);
	} else {
		printf("memshare: no no-map 'memshare*' node under %s\n", DT_RESERVED);

		if (find_module_region())
			printf("memshare: module region %s: 0x%llx + 0x%llx, "
			       "allocated by rhodep_memassign and assigned to the "
			       "modem VMID\n",
			       region_node,
			       (unsigned long long)region_base,
			       (unsigned long long)region_size);
		else
			printf("memshare: no assigned region at %s either\n",
			       MOD_REGION);
	}

	if (region_size) {
		if (offer > region_size) {
			printf("memshare: asked to offer %u but only 0x%llx is "
			       "available; capping\n",
			       offer, (unsigned long long)region_size);
			offer = (uint32_t)region_size;
		}
	} else {
		if (offer) {
			printf("memshare: REFUSING to offer %u bytes: nothing on this "
			       "system owns a region for the modem. The device tree "
			       "reserves none (flash a boot image with kernel patch "
			       "0120) and rhodep_memassign is not loaded with alloc=1. "
			       "Any address handed out now would be memory Linux is "
			       "still using.\n", offer);
			offer = 0;
		}
		printf("memshare: reporting zero bytes available\n");
	}

	sock = qrtr_open(0);
	if (sock < 0) {
		fprintf(stderr, "failed to open qrtr socket: %s\n", strerror(-sock));
		return 1;
	}

	ret = qrtr_publish(sock, MEMSHARE_SERVICE, MEMSHARE_VERSION, MEMSHARE_INSTANCE);
	if (ret < 0) {
		fprintf(stderr, "failed to publish service %d: %s\n",
			MEMSHARE_SERVICE, strerror(-ret));
		return 1;
	}
	printf("memshare: published QMI service %d\n", MEMSHARE_SERVICE);

	while (!stop) {
		uint16_t txn;
		uint16_t msg_id;
		unsigned int len;

		ret = qrtr_poll(sock, 1000);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "poll failed: %s\n", strerror(errno));
			break;
		}
		if (ret == 0)
			continue;

		ret = qrtr_recvfrom(sock, req, sizeof(req), &node, &port);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "recv failed: %s\n", strerror(errno));
			break;
		}

		/* Control traffic is not ours to answer. */
		if (port == QRTR_PORT_CTRL || ret < 7)
			continue;
		if (req[0] != QMI_REQUEST)
			continue;

		memcpy(&txn, req + 1, 2);
		memcpy(&msg_id, req + 3, 2);

		printf("memshare: request from node=%u port=%u %s (0x%04x) txn=%u\n",
		       node, port, msg_name(msg_id), msg_id, txn);
		{
			/* Dump the raw request: implementing a real allocator
			 * needs the exact TLVs the modem sends. */
			int i;

			printf("memshare: raw ");
			for (i = 0; i < ret && i < 96; i++)
				printf("%02x", req[i]);
			printf("\n");
		}

		switch (msg_id) {
		case MEM_QUERY_SIZE_REQ:
			printf("memshare: reporting size %u\n", offer);
			len = build_response(resp, txn, msg_id, 0, 0, true, offer);
			break;
		case MEM_ALLOC_REQ:
		case MEM_ALLOC_GENERIC_REQ:
			if (offer == 0 || region_size == 0) {
				/* Not advertising memory -- either because we
				 * were not asked to, or because the device tree
				 * reserves nothing -- so refuse cleanly rather
				 * than leaving the modem waiting. Handing out an
				 * unreserved address is the one outcome that
				 * must never happen; see find_reserved_region().
				 */
				printf("memshare: refusing allocation (offer=%u, reserved=0x%llx)\n",
				       offer, (unsigned long long)region_size);
				len = build_response(resp, txn, msg_id, 1, 0, false, 0);
				break;
			}
			len = build_alloc_response(resp, txn, msg_id,
						   parse_sequence_id(req, ret),
						   region_base, offer);
			printf("memshare: handing out 0x%llx (%u bytes) from %s\n",
			       (unsigned long long)region_base, offer, region_node);
			break;
		case MEM_FREE_REQ:
		case MEM_FREE_GENERIC_REQ:
			len = build_response(resp, txn, msg_id, 0, 0, false, 0);
			break;
		default:
			printf("memshare: no handler for 0x%04x, ignoring\n", msg_id);
			continue;
		}

		ret = qrtr_sendto(sock, node, port, resp, len);
		if (ret < 0)
			fprintf(stderr, "memshare: failed to answer: %s\n",
				strerror(-ret));
		else
			printf("memshare: answered %s\n", msg_name(msg_id));
	}

	qrtr_close(sock);
	return 0;
}
