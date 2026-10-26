/*
 * parse.c — 802.11 frame parsing (replaces the libwifi parse side used by
 *           FrameParser::parseMgmt / parseData in the original).
 *
 * Handles: radiotap strip + signal, mgmt type/subtype, beacon/probe-resp AP
 * info (SSID, channel, hidden), probe-req client SSID, and WPA EAPOL-Key
 * handshake message detection (M1..M4).
 */
#include "pineapd.h"
#include <string.h>
#include <stdbool.h>

/* radiotap present-field bit for DBM_ANTSIGNAL */
#define RT_PRESENT_TSFT          (1u << 0)
#define RT_PRESENT_FLAGS         (1u << 1)
#define RT_PRESENT_RATE          (1u << 2)
#define RT_PRESENT_CHANNEL       (1u << 3)
#define RT_PRESENT_FHSS          (1u << 4)
#define RT_PRESENT_DBM_ANTSIGNAL (1u << 5)

static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
}

/* Parse radiotap header. Returns header length, sets *signal if present. */
static size_t parse_radiotap(const uint8_t *pkt, size_t len, int *signal_dbm)
{
    if (len < 8) return 0;
    uint16_t rtlen = le16(pkt + 2);
    if (rtlen < 8 || rtlen > len) return 0;
    *signal_dbm = 0;

    /* Walk present bitmaps to find antenna signal. Handle chained present words. */
    uint32_t present = le32(pkt + 4);
    size_t off = 8;
    /* skip extended present words */
    while ((present & (1u << 31)) && off + 4 <= rtlen) {
        present = le32(pkt + off);
        off += 4;
    }
    /* First present word fields (minimal alignment-aware walk) */
    uint32_t p0 = le32(pkt + 4);
    size_t fo = off;
    #define ALIGN(o, a) (((o) + ((a)-1)) & ~((size_t)(a)-1))
    if (p0 & RT_PRESENT_TSFT)   { fo = ALIGN(fo, 8); fo += 8; }
    if (p0 & RT_PRESENT_FLAGS)  { fo += 1; }
    if (p0 & RT_PRESENT_RATE)   { fo += 1; }
    if (p0 & RT_PRESENT_CHANNEL){ fo = ALIGN(fo, 2); fo += 4; }
    if (p0 & RT_PRESENT_FHSS)   { fo += 2; }
    if (p0 & RT_PRESENT_DBM_ANTSIGNAL) {
        if (fo < rtlen) *signal_dbm = (int8_t)pkt[fo];
    }
    #undef ALIGN
    return rtlen;
}

bool wifi_parse(const uint8_t *pkt, size_t len, sniff_view_t *v)
{
    if (!pkt || len < 10) return false;
    memset(v, 0, sizeof *v);
    size_t rt = 0;
    /* heuristic: radiotap version byte is 0 */
    if (pkt[0] == 0x00 && len >= 8) {
        rt = parse_radiotap(pkt, len, &v->signal_dbm);
    }
    if (rt && rt < len) {
        v->has_radiotap = true;
        v->dot11 = pkt + rt;
        v->dot11_len = len - rt;
    } else {
        v->dot11 = pkt;
        v->dot11_len = len;
    }
    return v->dot11_len >= 10;
}

int wifi_type(const sniff_view_t *v)
{
    if (v->dot11_len < 1) return -1;
    return (v->dot11[0] >> 2) & 0x3;
}

int wifi_subtype(const sniff_view_t *v)
{
    if (v->dot11_len < 1) return -1;
    return (v->dot11[0] >> 4) & 0xf;
}

/* addr2 (source) at offset 10, addr1 (dst) at 4, addr3 (bssid) at 16 */
static void get_addr(const sniff_view_t *v, int which, mac_t *out)
{
    size_t off = (which == 1) ? 4 : (which == 2) ? 10 : 16;
    if (off + 6 <= v->dot11_len) memcpy(out->b, v->dot11 + off, 6);
    else memset(out->b, 0, 6);
}

/* Iterate tagged parameters starting at `start`, find tag `id`. */
static const uint8_t *find_tag(const uint8_t *tags, size_t taglen, uint8_t id,
                               uint8_t *out_len)
{
    size_t o = 0;
    while (o + 2 <= taglen) {
        uint8_t t = tags[o], l = tags[o+1];
        if (o + 2 + l > taglen) break;
        if (t == id) { *out_len = l; return tags + o + 2; }
        o += 2 + l;
    }
    return NULL;
}

bool wifi_get_ap(const sniff_view_t *v, mac_t *bssid, char *ssid, size_t ssid_cap,
                 int *channel, int *hidden)
{
    /* mgmt hdr 24 + fixed params 12 = 36 before tags for beacon/probe-resp */
    if (v->dot11_len < 36) return false;
    get_addr(v, 3, bssid);
    const uint8_t *tags = v->dot11 + 36;
    size_t taglen = v->dot11_len - 36;
    uint8_t l = 0;
    const uint8_t *s = find_tag(tags, taglen, 0, &l);  /* SSID */
    *hidden = 0;
    ssid[0] = '\0';
    if (s) {
        if (l == 0) *hidden = 1;
        else {
            size_t n = l < ssid_cap - 1 ? l : ssid_cap - 1;
            memcpy(ssid, s, n);
            ssid[n] = '\0';
        }
    }
    const uint8_t *ds = find_tag(tags, taglen, 3, &l);  /* DS param: channel */
    *channel = (ds && l >= 1) ? ds[0] : 0;
    return true;
}

bool wifi_get_probe(const sniff_view_t *v, mac_t *client, char *ssid, size_t ssid_cap)
{
    /* probe-req: mgmt hdr 24 then tags */
    if (v->dot11_len < 24) return false;
    get_addr(v, 2, client);  /* source = client */
    const uint8_t *tags = v->dot11 + 24;
    size_t taglen = v->dot11_len - 24;
    uint8_t l = 0;
    const uint8_t *s = find_tag(tags, taglen, 0, &l);
    ssid[0] = '\0';
    if (s && l) {
        size_t n = l < ssid_cap - 1 ? l : ssid_cap - 1;
        memcpy(ssid, s, n);
        ssid[n] = '\0';
    }
    return true;
}

/*
 * wifi_wpa_handshake_part — detect EAPOL-Key (WPA 4-way handshake) and return
 * the message number 1..4, or 0 if not a handshake frame.
 *
 * Logic mirrors is_wpa_handshake + get_wpa_handshake_part:
 *   - data frame (type 2), LLC/SNAP with EtherType 0x888E (EAPOL)
 *   - EAPOL type 3 = EAPOL-Key
 *   - key info flags distinguish M1..M4.
 */
int wifi_wpa_handshake_part(const sniff_view_t *v, mac_t *ap, mac_t *client)
{
    if (wifi_type(v) != 2) return 0;   /* must be data */
    const uint8_t *d = v->dot11;
    size_t dl = v->dot11_len;
    /* data header is 24 bytes (26 if QoS). Determine QoS from subtype bit 3. */
    int subtype = wifi_subtype(v);
    size_t hdr = 24;
    if (subtype & 0x08) hdr += 2;      /* QoS data */
    /* ToDS/FromDS could add addr4(6) — handle the common AP<->STA case */
    uint16_t fc = le16(d);
    int tods = (fc >> 8) & 0x1, fromds = (fc >> 8) & 0x2 ? 1 : 0;
    if (tods && fromds) hdr += 6;      /* WDS: addr4 present */
    if (dl < hdr + 8) return 0;

    /* LLC/SNAP: AA AA 03 00 00 00 <ethertype> */
    const uint8_t *llc = d + hdr;
    if (!(llc[0] == 0xAA && llc[1] == 0xAA && llc[2] == 0x03)) return 0;
    uint16_t ethertype = (llc[6] << 8) | llc[7];
    if (ethertype != 0x888E) return 0; /* not EAPOL */

    const uint8_t *eapol = llc + 8;
    size_t eapol_len = dl - (hdr + 8);
    if (eapol_len < 4) return 0;
    if (eapol[1] != 0x03) return 0;    /* EAPOL-Key */
    if (eapol_len < 7) return 0;
    uint16_t key_info = (eapol[5] << 8) | eapol[6];

    /* figure out addresses */
    /* fromds => AP is addr2(source), client addr1(dst); tods => reverse */
    if (fromds) { get_addr(v, 2, ap); get_addr(v, 1, client); }
    else        { get_addr(v, 1, ap); get_addr(v, 2, client); }

    /* key_info bits: 0x0008=pairwise, 0x0040=install, 0x0080=ack,
       0x0100=mic, 0x0200=secure */
    bool mic    = key_info & 0x0100;
    bool ack    = key_info & 0x0080;
    bool install= key_info & 0x0040;
    bool secure = key_info & 0x0200;

    if (ack && !mic && !install) return 1;             /* M1 */
    if (mic && !ack && !install && !secure) return 2;  /* M2 */
    if (mic && ack && install) return 3;               /* M3 */
    if (mic && !ack && secure) return 4;               /* M4 */
    /* fall back: M2 vs M4 by secure bit */
    if (mic && !ack) return secure ? 4 : 2;
    return 0;
}
