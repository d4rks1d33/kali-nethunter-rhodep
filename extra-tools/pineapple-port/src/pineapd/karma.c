/*
 * karma.c — SSID pool beaconing.
 *
 * KARMA NOTE (INFORME §F.1): the REAL karma probe-responses are done by hostapd
 * (the pineap CLI calls `hostapd_cli -i <iface> karma_*`), NOT by pineapd. So
 * pineapd-ng does NOT forge probe-responses. What pineapd does is:
 *   - periodic beaconing of the SSID pool (broadcast_ssid_pool)
 *   - SSID harvesting (capture_ssids) -> handled in parser.c
 *   - filters / recon / handshakes / deauth
 *
 * This file implements beacon_ssid_pool(): enqueue one beacon per pooled SSID,
 * using pineap_mac as BSSID/source and ap_channel as the TX channel, at the
 * configured beacon_interval cadence (InjectInterval).
 */
#include "pineapd.h"
#include <stdlib.h>
#include <unistd.h>

void beacon_ssid_pool(void)
{
    pineap_config_t *c = config_get();
    char **ssids = NULL;
    int n = ssid_get_all(&ssids);
    if (n <= 0) { ssid_free_all(ssids, n); return; }

    uint8_t channel = (uint8_t)(c->ap_channel > 0 && c->ap_channel < 200
                                ? c->ap_channel : 1);
    uint32_t interval_us = c->beacon_interval ? c->beacon_interval : INJECT_NORMAL;
    /* beacon interval field in TU (1 TU = 1024us); use 100 TU standard */
    uint16_t bi_tu = 100;

    mac_t src = c->pineap_mac;
    if (mac_is_zero(&src)) {
        /* default a locally-administered random-ish BSSID if unset */
        src.b[0] = 0x02; src.b[1] = 0x1a; src.b[2] = 0x11;
        src.b[3] = 0x00; src.b[4] = 0x00; src.b[5] = 0x01;
    }

    for (int i = 0; i < n; i++) {
        size_t len = 0;
        uint8_t *frame = build_beacon(&src, &src, ssids[i], channel, bi_tu, &len);
        if (frame && len) {
            /* multiplier 1, inter-SSID delay from InjectInterval */
            inject_queue_push(channel, 0, 1, frame, len);
        }
        free(frame);
        usleep(interval_us);
    }
    ssid_free_all(ssids, n);
}
