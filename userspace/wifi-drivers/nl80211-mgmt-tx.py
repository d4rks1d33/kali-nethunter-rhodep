#!/usr/bin/env python3
# nl80211-mgmt-tx: send an 802.11 management frame via NL80211_CMD_FRAME
# (offchannel/ROC) using raw generic-netlink — no libnl/iw dependency.
#
# This is the "inject on an arbitrary channel from the STA vdev" path: the
# kernel takes the frame offchannel (temp peer / HTT freq descriptor) and the
# firmware transmits it from the tx-capable STA vdev. addr2/SA is forced by
# cfg80211 to the vif's own MAC, so build the frame with SA = wlan0's MAC.
#
# Usage: nl80211-mgmt-tx.py <ifname> <freq_mhz> <frame_hex> [wait_ms]
import sys, socket, struct, os

# --- generic netlink plumbing ------------------------------------------------
NETLINK_GENERIC = 16
GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2

# nl80211 constants (resolved on this device's headers)
NL80211_CMD_FRAME = 59
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_FRAME = 51
NL80211_ATTR_WIPHY_FREQ = 38
NL80211_ATTR_DURATION = 87
NL80211_ATTR_OFFCHANNEL_TX_OK = 108
NL80211_ATTR_TX_NO_CCK_RATE = 135

NLM_F_REQUEST = 1
NLM_F_ACK = 4

def _align(n): return (n + 3) & ~3

def nla(typ, data):
    hdr = struct.pack("HH", len(data) + 4, typ)
    return hdr + data + b"\x00" * (_align(len(data) + 4) - (len(data) + 4))

def nla_u32(typ, val): return nla(typ, struct.pack("I", val))
def nla_flag(typ):     return nla(typ, b"")

class GeNl:
    def __init__(self):
        self.s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.s.bind((0, 0))
        self.seq = 0
        self.pid = self.s.getsockname()[0]

    def _send(self, family, cmd, attrs, flags=NLM_F_REQUEST | NLM_F_ACK, version=1):
        self.seq += 1
        genl = struct.pack("BBH", cmd, version, 0)
        payload = genl + attrs
        nlmsg = struct.pack("IHHII", 16 + len(payload), family, flags, self.seq, self.pid) + payload
        self.s.send(nlmsg)
        return self.seq

    def _recv(self):
        data = self.s.recv(65535)
        return data

    def resolve_family(self, name):
        attrs = nla(CTRL_ATTR_FAMILY_NAME, name.encode() + b"\x00")
        self._send(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, attrs)
        data = self._recv()
        # parse nlmsghdr
        msglen, mtype, flags, seq, pid = struct.unpack("IHHII", data[:16])
        # genlmsghdr (4 bytes) then attrs
        pos = 16 + 4
        while pos + 4 <= len(data):
            alen, atype = struct.unpack("HH", data[pos:pos+4])
            if alen < 4: break
            val = data[pos+4:pos+alen]
            if atype == CTRL_ATTR_FAMILY_ID:
                return struct.unpack("H", val[:2])[0]
            pos += _align(alen)
        raise RuntimeError("could not resolve nl80211 family id")

    def send_frame(self, family, ifindex, freq, frame_bytes, wait_ms):
        attrs = b""
        attrs += nla_u32(NL80211_ATTR_IFINDEX, ifindex)
        attrs += nla_u32(NL80211_ATTR_WIPHY_FREQ, freq)
        if wait_ms:
            attrs += nla_u32(NL80211_ATTR_DURATION, wait_ms)
        attrs += nla_flag(NL80211_ATTR_OFFCHANNEL_TX_OK)
        attrs += nla_flag(NL80211_ATTR_TX_NO_CCK_RATE)
        attrs += nla(NL80211_ATTR_FRAME, frame_bytes)
        self._send(family, NL80211_CMD_FRAME, attrs)
        data = self._recv()
        msglen, mtype, flags, seq, pid = struct.unpack("IHHII", data[:16])
        if mtype == 2:  # NLMSG_ERROR (also ACK, err==0)
            err = struct.unpack("i", data[16:20])[0]
            return err
        # otherwise a reply with a cookie => success
        return 0

def main():
    if len(sys.argv) < 4:
        print("usage: nl80211-mgmt-tx.py <ifname> <freq_mhz> <frame_hex> [wait_ms]")
        sys.exit(2)
    ifname = sys.argv[1]
    freq = int(sys.argv[2])
    frame = bytes.fromhex(sys.argv[3])
    wait_ms = int(sys.argv[4]) if len(sys.argv) > 4 else 300
    ifindex = socket.if_nametoindex(ifname)
    g = GeNl()
    fam = g.resolve_family("nl80211")
    err = g.send_frame(fam, ifindex, freq, frame, wait_ms)
    if err == 0:
        print("OK: NL80211_CMD_FRAME accepted (freq=%d wait=%d len=%d)" % (freq, wait_ms, len(frame)))
        sys.exit(0)
    else:
        print("ERR: kernel returned %d (%s)" % (err, os.strerror(-err) if err < 0 else "?"))
        sys.exit(1)

if __name__ == "__main__":
    main()
