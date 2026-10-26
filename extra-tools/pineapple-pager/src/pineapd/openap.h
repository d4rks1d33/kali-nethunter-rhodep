/*
 * openap.h — faithful bridge for the WiFi Pineapple Pager "Open AP" (and the
 *            other radio0 AP VAPs: Evil WPA / Management AP).
 *
 * WHY THIS EXISTS
 * ---------------
 * The Pager firmware (Go, MIPS) does NOT signal "enable Open AP" over the
 * pineap socket. Captured by strace + UCI diff (see docs/OPENAP-INTENT.md):
 *
 *   When the user toggles  PineAP -> Open AP  ON, the firmware writes
 *   /etc/config/wireless, flipping the `wifi-iface 'wlan0open'` section's
 *       option disabled '1'  ->  option disabled '0'
 *   (and sets `option hidden '0'`), then expects an OpenWRT `wifi reload` /
 *   netifd to actually bring the AP up. Under qemu-user there is no netifd /
 *   hostapd / ubus, so nothing happens on the real radio.
 *
 * The firmware's Open AP intent, verbatim from the UCI it persists:
 *     iface  = wlan0open   (a VAP on radio0 = internal 2.4GHz radio)
 *     mode   = ap
 *     ssid   = <$_OPEN_AP_SSID>   (default "pager-open")
 *     hidden = 0|1
 *     disabled = 0 (enabled) | 1 (disabled)
 *     radio0: band=2g channel=1 htmode=HT20
 *
 * OUR JOB (pure bridge, no policy of our own): watch that file, and when the
 * firmware enables/disables a radio0 AP VAP, bring up / tear down a NATIVE
 * host hostapd that mirrors exactly what the firmware asked for — but on the
 * phone's REAL hardware.
 *
 * HARDWARE MAPPING (see docs/OPENAP-INTENT.md): the firmware's radio0 is the
 * internal 2.4GHz radio. On the G82, wlan0 (ath10k) is the SSH uplink STA on
 * 5GHz and must NOT be disturbed; wlan1 (RTL8821AU) is a free 2.4GHz-capable
 * radio. So the faithful target for the firmware's 2.4GHz Open AP is wlan1.
 * This is overridable (OPENAP_TARGET_IFACE / pineapd.conf), and we refuse to
 * build the AP on the uplink/SSH radio unless explicitly forced.
 */
#ifndef PINEAPD_OPENAP_H
#define PINEAPD_OPENAP_H

#include <stdbool.h>

/* Path to the firmware's UCI wireless config that we watch for AP intent.
 * Defaults to the rootfs copy the qemu firmware reads/writes. */
#ifndef OPENAP_WIRELESS_PATH
#define OPENAP_WIRELESS_PATH "/opt/pineapple-pager/qemu/rootfs/etc/config/wireless"
#endif

/* Native host hostapd binary. */
#ifndef OPENAP_HOSTAPD_BIN
#define OPENAP_HOSTAPD_BIN "/usr/sbin/hostapd"
#endif

/* Where we drop generated hostapd.conf + pid + ctrl sockets. */
#ifndef OPENAP_RUN_DIR
#define OPENAP_RUN_DIR "/opt/pineapple-pager/run"
#endif

/* Real host radio for the firmware's AP VAPs = the INTERNAL wlan0 (ath10k).
 * POLICY (user's real use case):
 *   - AP (Open AP / Evil Portal / Evil WPA) runs on wlan0 ONLY when wlan0 is
 *     FREE (not associated to any WiFi). Then: AP on wlan0 + Archer 100% for
 *     recon, both at once. On teardown, wlan0 returns to managed and can
 *     reconnect to WiFi.
 *   - If wlan0 HAS an uplink (connected to WiFi, carrying SSH), the AP does NOT
 *     start — it never touches wlan0 (would reset the ath10k and drop the
 *     uplink/SSH) and never falls back to the Archer (that would break recon).
 *     It simply waits until wlan0 is free.
 * The Archer (wlan1) is ALWAYS reserved for recon/monitor; the AP never uses it. */
#ifndef OPENAP_TARGET_IFACE
#define OPENAP_TARGET_IFACE "wlan0"
#endif

/* The firmware's AP network (its `lan` interface + `dhcp lan` section). Clients
 * associating to the AP expect an IP from here or they hang on "obtaining IP".
 * We read these live from the firmware's own UCI (network/dhcp) so we stay
 * faithful; these are only fallbacks if the config can't be read. From the
 * firmware's /etc/config/network `lan` + /etc/config/dhcp `lan`:
 *     ipaddr 172.16.52.1  netmask 255.255.255.0  start 100  limit 150. */
#ifndef OPENAP_NET_PATH
#define OPENAP_NET_PATH "/opt/pineapple-pager/qemu/rootfs/etc/config/network"
#endif
#ifndef OPENAP_DHCP_PATH
#define OPENAP_DHCP_PATH "/opt/pineapple-pager/qemu/rootfs/etc/config/dhcp"
#endif
#ifndef OPENAP_AP_IPADDR
#define OPENAP_AP_IPADDR "172.16.52.1"
#endif
#ifndef OPENAP_AP_NETMASK
#define OPENAP_AP_NETMASK "255.255.255.0"
#endif
#ifndef OPENAP_DHCP_START
#define OPENAP_DHCP_START 100
#endif
#ifndef OPENAP_DHCP_LIMIT
#define OPENAP_DHCP_LIMIT 150
#endif
#ifndef OPENAP_DNSMASQ_BIN
#define OPENAP_DNSMASQ_BIN "/usr/sbin/dnsmasq"
#endif

/* Start the background watcher thread (polls OPENAP_WIRELESS_PATH). Idempotent.
 * Returns 0 on success. */
int  openap_start_watcher(void);

/* Stop the watcher thread AND tear down any running AP (hostapd killed, target
 * iface returned to managed). Call on daemon shutdown so "powering off" the
 * Pager frees the radio + battery. Safe to call if never started. */
void openap_shutdown(void);

/* Force a single synchronous reconcile against the current config file (used at
 * startup so state matches the firmware's persisted intent immediately). */
void openap_reconcile_now(void);

#endif /* PINEAPD_OPENAP_H */
