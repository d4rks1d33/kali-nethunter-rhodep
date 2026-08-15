/*
 * Publish QMI service 52 (memshare) and log whatever the modem sends to it.
 *
 * This is a diagnostic, not an implementation. The question it answers is
 * whether the modem actually asks the application processor for memory. The
 * stock device tree says it should: qcom,memshare declares a client for the
 * modem with qcom,allocate-boot-time and an 8 MB reserved region, downstream
 * answers on QMI service 0x34, and mainline provides nothing at all. A modem
 * blocked on an allocation that never arrives fits the observed symptoms, which
 * are that it answers queries but never finishes initialising and refuses every
 * operating mode transition.
 *
 * Why C rather than the Python version next to this file: publishing a service
 * by hand over a raw AF_QIPCRTR socket did not work. The service never appeared
 * in qrtr-lookup, which is a silent failure that looks exactly like the modem
 * ignoring you. libqrtr's qrtr_publish() is the path that demonstrably works on
 * this device, since rmtfs, tqftpserv and pd-mapper all register through it.
 *
 * Build on the phone:
 *     sudo apt install libqrtr-dev
 *     gcc -O2 -Wall -o memshare-probe memshare-probe.c -lqrtr
 *
 * Verify it actually registered before trusting any result:
 *     qrtr-lookup | awk '$1==52'
 */

#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <libqrtr.h>

#define MEMSHARE_SERVICE  52
#define MEMSHARE_VERSION  1
#define MEMSHARE_INSTANCE 0

static volatile sig_atomic_t stop;

static void on_signal(int signo)
{
	(void)signo;
	stop = 1;
}

static const char *ctrl_name(unsigned int cmd)
{
	switch (cmd) {
	case QRTR_TYPE_HELLO:      return "HELLO";
	case QRTR_TYPE_BYE:        return "BYE";
	case QRTR_TYPE_NEW_SERVER: return "NEW_SERVER";
	case QRTR_TYPE_DEL_SERVER: return "DEL_SERVER";
	case QRTR_TYPE_DEL_CLIENT: return "DEL_CLIENT";
	case QRTR_TYPE_RESUME_TX:  return "RESUME_TX";
	case QRTR_TYPE_EXIT:       return "EXIT";
	case QRTR_TYPE_PING:       return "PING";
	case QRTR_TYPE_NEW_LOOKUP: return "NEW_LOOKUP";
	case QRTR_TYPE_DEL_LOOKUP: return "DEL_LOOKUP";
	default:                   return "unknown";
	}
}

static void hexdump(const unsigned char *buf, unsigned int len)
{
	unsigned int i;

	for (i = 0; i < len; i++) {
		if (i && i % 16 == 0)
			printf("\n        ");
		printf("%02x", buf[i]);
	}
	printf("\n");
}

int main(int argc, char **argv)
{
	unsigned int seconds = argc > 1 ? (unsigned int)atoi(argv[1]) : 120;
	unsigned char buf[4096];
	time_t deadline;
	uint32_t node;
	uint32_t port;
	unsigned int packets = 0;
	int sock;
	int ret;

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	/* 0 means "let the kernel pick a port", the same as every other service
	 * on this system that is not rmtfs. */
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

	printf("published QMI service %d (memshare) v%d instance %d\n",
	       MEMSHARE_SERVICE, MEMSHARE_VERSION, MEMSHARE_INSTANCE);
	printf("check it is real with: qrtr-lookup | awk '$1==%d'\n", MEMSHARE_SERVICE);
	fflush(stdout);

	deadline = time(NULL) + seconds;

	while (!stop && time(NULL) < deadline) {
		ret = qrtr_poll(sock, 1000);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "poll failed: %s\n", strerror(errno));
			break;
		}
		if (ret == 0)
			continue;

		ret = qrtr_recvfrom(sock, buf, sizeof(buf), &node, &port);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "recv failed: %s\n", strerror(errno));
			break;
		}

		packets++;
		printf("\n--- packet %u from node=%u port=%u, %d bytes\n",
		       packets, node, port, ret);

		if (port == QRTR_PORT_CTRL && ret >= 4) {
			uint32_t cmd;

			memcpy(&cmd, buf, sizeof(cmd));
			printf("    control: %s\n", ctrl_name(cmd));
		} else if (ret >= 7) {
			uint16_t txn;
			uint16_t msg_id;
			uint16_t msg_len;

			memcpy(&txn, buf + 1, sizeof(txn));
			memcpy(&msg_id, buf + 3, sizeof(msg_id));
			memcpy(&msg_len, buf + 5, sizeof(msg_len));
			printf("    QMI type=0x%02x txn=%u msg_id=0x%04x len=%u\n",
			       buf[0], txn, msg_id, msg_len);
			printf("    *** the modem is talking to memshare ***\n");
		}

		printf("        ");
		hexdump(buf, ret > 64 ? 64 : (unsigned int)ret);
		fflush(stdout);
	}

	printf("\ndone: %u packets\n", packets);
	if (!packets)
		printf("nothing arrived. Only meaningful if qrtr-lookup showed"
		       " service %d while this was running\n", MEMSHARE_SERVICE);

	qrtr_close(sock);
	return 0;
}
