#!/usr/bin/env python3
"""
rhodep-gnss-bisect - bisect *what the GNSS measurement engine switches on*.

*** ON motorola-rhodep A standalone SESSION RESETS THE SoC. Read
    docs/interconnect-sm6375-wip/GNSS-SM6375.md before running this. ***

`scripts/rhodep-gnss-test.py` established the trigger and the first cut:
`opmode=standalone` kills the SoC about 100 ms after QMI_LOC_START, `cellid`
runs the identical session state machine forever because it never brings up the
measurement engine. This tool exists to cut *inside* the engine - per
constellation, per band, per power mode, per fix criterion - which the libqmi
API physically cannot do.

Why it is not a copy of rhodep-gnss-test.py
-------------------------------------------
libqmi 1.38 models eleven LOC messages. `QMI_LOC_START` is exposed with three
of its nine optional TLVs, and there is no binding at all for constellation
control, SV blacklisting, multiband configuration or the fix power mode. There
is also no raw-message escape hatch: `QmiMessage` is a GByteArray typedef and
does not appear in the GObject introspection data at all, so
`gi.repository.Qmi` has no `Message` attribute and a program using python-gi
cannot hand-build a QMI PDU.

So this speaks QMI to the modem directly over AF_QIPCRTR, the way
`scripts/pdr-tool.py` and `scripts/ssc-*.py` already do on this port: one
socket, bound once, is one QMI LOC client for its whole lifetime, which is
exactly the property the session needs (the session belongs to the client that
created it). Node 0 port 108 is the LOC service - `qrtr-lookup` prints it as
"Location service (~ PDS v2)".

Wire format: {ctl_flags u8, txn u16, msg_id u16, len u16, TLVs}. Message and
TLV numbering are taken from `location_service_v02.h` (the QMI LOC v02 IDL,
LA.VENDOR.15.4.0.r1), not guessed, and every one used here was first confirmed
against this modem with QMI_LOC_GET_SUPPORTED_MSGS (0x001E) and a read-only
GET.

The validity rule
-----------------
From this repo's own notes: a *failed* `QMI_LOC_SET_OPERATION_MODE` falls
through to `standalone`, so a survival that came from an unapplied setting
means nothing. Every configuration step here is read back from the modem
before the next one is sent, and `start()` refuses to run if any step the
caller asked for did not take. A run whose log does not contain
"config verified" is void.

Everything is written to /dev/kmsg as well as stdout, because on a watchdog
reset stdout dies with the ssh session while the console survives in
/var/lib/systemd/pstore/console-ramoops-*.

Usage
-----
  sudo ./rhodep-gnss-bisect.py [seconds] [key=value ...] [flags]

  seconds              listen time after the session starts (default 30)

  opmode=NAME          default|msb|msa|standalone|cellid|wwan
  constell=SPEC        gpsonly | reset | +glo,-gal,... | keep   (default keep)
                       'gpsonly' disables GLO, BDS, QZSS, GAL and NavIC.
                       GPS itself is ENABLED_MANDATORY and cannot be turned off.
                       MEASURED ON rhodep: this modem is expansion-only. It
                       accepts an *enable* (QZSS went DISABLED_INTERNALLY ->
                       ENABLED_BY_CLIENT) and silently ignores a *disable*,
                       answering SUCCESS at both the QMI and the LOC layer while
                       leaving the constellation ENABLED_INTERNALLY. So a
                       restricting constell= will VOID the run, on purpose. The
                       encoding is not the problem: 32-bit masks are rejected
                       with QMI error 0x0001, 64-bit masks are accepted.
  multiband=MASK       secondary-band enable mask, hex or decimal, or 'keep'
                       bits: gps 0x01 glo 0x02 bds 0x04 gal 0x08 qzss 0x10
                             navic 0x20   (0 = no secondary bands at all)
                       Same story: reads back, will not take a write. On rhodep
                       it already reads 0, i.e. the engine is L1/G1/E1 only and
                       GPS L2/L5, GAL E5a and BDS B2A are off to begin with.
  accuracy=LEVEL       low|med|high      QMI_LOC_START TLV 0x11
  recurrence=TYPE      periodic|single   QMI_LOC_START TLV 0x10
  mininterval=MS       QMI_LOC_START TLV 0x13
  powermode=N[,MS]     1 improved-accuracy (full power, DPO OFF)
                       2 normal (engine duty-cycles itself, DPO ON)
                       3 background-defined-power  4 background-defined-time
                       5 background-keep-warm      QMI_LOC_START TLV 0x1A
                       THIS IS THE LEVER THAT FLIPS THE RESET. In standalone,
                       3 and 5 survive and 1 (and no TLV at all) reset the SoC.
                       It reads back exactly through GET_FIX_CRITERIA TLV 0x18,
                       which is how it is verified - note that the firmware's own
                       GET_SUPPORTED_FIELDS does *not* list 0x1A as supported and
                       is wrong about that, the same way GET_SUPPORTED_MSGS is
                       wrong about 0x00B8/0x00E0. Do not trust either bitmap;
                       send the message and read the value back.
                        mininterval= is NOT a lever: asked 60000, reads back 1000.
                        The second field is the only companion parameter the
                        struct has - GET_FIX_CRITERIA TLV 0x18 is 8 bytes, two
                        uint32 - so powermode=3,MS is the duty-cycle knob and
                        the thing to walk when looking for the threshold.
  events=a,b,c         min,engine,inject,wifi,nmea,pos,sv,meas,svpoly,hi,none
                        or a literal mask (0x... or decimal), OR-ed together.
                        (default min,engine)
                        meas = QMI_LOC_EVENT_MASK_GNSS_MEASUREMENT_REPORT, the
                        one instrument that can tell "the receiver is duty-
                        cycling safely" from "the receiver never came up":
                        only a correlator that is actually tracking emits it.
                        hi = bits 20..31 as slack around it, because the exact
                        bit index is from the IDL and the IDL has already been
                        caught being wrong about this service twice.

  port=N               LOC service qrtr port. Default: looked up at runtime.
                       QRTR ports are assigned per boot and MOVE - this service
                       has been observed at 108 on one boot and 107 on the next.
                       A hardcoded port sends the session into a void and the
                       run comes back as "no response from the LOC service",
                       which is a Void, not a survival - but do not rely on
                       that: look it up.

  respond=a,b,c        answer the engine's own in-session assistance requests,
                       from THIS client, inside the fix cycle that asked.
                        time     EVENT_INJECT_TIME_REQ (0x0028)
                                   -> QMI_LOC_INJECT_UTC_TIME (0x0038)
                        position EVENT_INJECT_POSITION_REQ (0x002A)
                                   -> QMI_LOC_INJECT_POSITION (0x0039)
                        xtra     EVENT_INJECT_PREDICTED_ORBITS_REQ (0x0029)
                                   -> QMI_LOC_INJECT_XTRA_DATA (0x00A7)
                       Default: answer nothing, i.e. every earlier run in this
                       file. Every injection's RESP *and* its indication status
                       are checked and counted; none of them is assumed.
  lat= lon= acc=       the coarse position to inject (acc in metres, default 50)
  possrc=NAME          gnss|cellid|enh_cellid|wifi|terrestrial|hybrid|other
  timesrc=NAME         unknown|ap|ntp|nts    (0x0038 TLV 0x10, see below)
  xtrafile=PATH        default /var/lib/rhodep-gnss/xtra.bin (rhodep-xtra fetch)

  respondafter=SEC     do not answer anything for the first SEC seconds of the
                       session. Without this the first answers go out inside the
                       replayed backlog of queued indications, five of them in
                       eight milliseconds, which is a different experiment from
                       "one answer per fix cycle".
  injectevery=SEC      inject unconditionally on a timer as well, whether or not
                       the engine asked. This is the control for opmodes that
                       never ask: cellid never emits INJECT_TIME_REQ, so it is
                       the only way to send an injection into a live cellid
                       session and ask whether the reset needs the measurement
                       engine or just needs LOC traffic.
  preinject=a,b,c      inject assistance BEFORE QMI_LOC_START, with no session
                       running, and Void the run unless the modem acknowledged
                       every part. Separate from respond=: a run can preinject
                       time and then answer nothing at all for the whole
                       session, which is how "does the engine merely need to
                       HAVE time" is separated from "does it need to be
                       answered while it is asking".
  injecttest           send one of each injection with NO session running and
                       print the RESP and the indication status. Free, safe,
                       and the thing to run before trusting a session result.
  nostart              configure and read back, never start a session
  probe                dump the current engine configuration and exit
  norestore            do not put the constellation config back afterwards

Answering the engine
--------------------
The wire formats for the three injections were not taken from an IDL. They
were captured off this device from libqmi 1.38.0 with tracing enabled, so they
are byte-identical to what `qmicli --loc-inject-time`, `qmicli
--loc-inject-position-*` and `rhodep-xtra` put on the wire and are known to be
accepted here:

  0x0038 INJECT_UTC_TIME   0x10 u32 time source, 0x02 u32 unc ms, 0x01 u64 ms
                           TLV 0x10 is optional in libqmi's model and MANDATORY
                           on this firmware.
  0x0039 INJECT_POSITION   0x1D u32 source, 0x1B u64 utc ms, 0x13 u8 confidence,
                           0x12 f32 hor unc, 0x11 f64 lon, 0x10 f64 lat
  0x00A7 INJECT_XTRA_DATA  0x01 u32 total size, 0x02 u16 total parts,
                           0x03 u16 part number, 0x04 part data as a
                           LENGTH-PREFIXED array: u16 count then the bytes, so
                           a 1024-byte part is a 1026-byte TLV.
                           0x0035 InjectPredictedOrbitsData is NotSupported.

Examples
  ./rhodep-gnss-bisect.py probe
  ./rhodep-gnss-bisect.py 25 opmode=cellid                 # known survivor
  ./rhodep-gnss-bisect.py 25 opmode=standalone constell=gpsonly
  ./rhodep-gnss-bisect.py 25 opmode=standalone powermode=1 accuracy=low
"""

import socket
import struct
import subprocess
import sys
import time

LOC_SERVICE = 16                      # QMI LOC, "Location service (~ PDS v2)"
LOC_NODE, LOC_PORT = 0, 108           # PORT is a fallback only; see find_loc()

# ---- message ids, location_service_v02.h -----------------------------------
INFORM_CLIENT_REVISION = 0x0020
REG_EVENTS = 0x0021
START = 0x0022
STOP = 0x0023
EVENT_POSITION_REPORT = 0x0024
EVENT_GNSS_SV_INFO = 0x0025
EVENT_NMEA = 0x0026
EVENT_NI_NOTIFY_VERIFY_REQ = 0x0027
EVENT_INJECT_TIME_REQ = 0x0028
EVENT_INJECT_PREDICTED_ORBITS_REQ = 0x0029
EVENT_INJECT_POSITION_REQ = 0x002A
EVENT_ENGINE_STATE = 0x002B
EVENT_FIX_SESSION_STATE = 0x002C
EVENT_WIFI_REQ = 0x002D                    # IDL numbering, NOT confirmed here
EVENT_SENSOR_STREAMING_READY_STATUS = 0x002E   # IDL numbering, NOT confirmed
EVENT_TIME_SYNC_REQ = 0x002F               # IDL numbering, NOT confirmed here
EVENT_SET_SPI_STREAMING_REPORT = 0x0030    # IDL numbering, NOT confirmed here
EVENT_LOCATION_SERVER_CONNECTION_REQ = 0x0031  # IDL numbering, NOT confirmed
EVENT_GNSS_MEASUREMENT_REPORT = 0x0086
EVENT_SV_POLYNOMIAL_REPORT = 0x0087
GET_FIX_CRITERIA = 0x0033
SET_OPERATION_MODE = 0x004A
GET_OPERATION_MODE = 0x004B
GET_SERVER = 0x0043
GET_REGISTERED_EVENTS = 0x0049
SET_CONSTELLATION_CONTROL = 0x00B8
GET_CONSTELLATION_CONTROL = 0x00BF
SET_MULTIBAND_CONFIG = 0x00E0
GET_MULTIBAND_CONFIG = 0x00E1

# ---- assistance injection, the AP->modem direction --------------------------
# Wire formats below are not guessed and not taken from an IDL. They were
# captured off this device on 2026-09-06 from libqmi 1.38.0 with its own
# tracing on (`qmicli --verbose` for 0x0038/0x0039, a python-gi one-shot with
# Qmi.utils_set_traces_enabled(True) for 0x00A7), so they are byte-for-byte
# what the working `qmicli --loc-inject-time` and `rhodep-xtra` send.
INJECT_PREDICTED_ORBITS_DATA = 0x0035      # answers NotSupported on this modem
INJECT_UTC_TIME = 0x0038
INJECT_POSITION = 0x0039
INJECT_XTRA_DATA = 0x00A7

MSG_NAME = {
    EVENT_POSITION_REPORT: "POSITION_REPORT",
    EVENT_GNSS_SV_INFO: "GNSS_SV_INFO",
    EVENT_NMEA: "NMEA",
    EVENT_NI_NOTIFY_VERIFY_REQ: "NI_NOTIFY_VERIFY_REQ",
    EVENT_INJECT_TIME_REQ: "INJECT_TIME_REQ",
    EVENT_INJECT_PREDICTED_ORBITS_REQ: "INJECT_PREDICTED_ORBITS_REQ",
    EVENT_INJECT_POSITION_REQ: "INJECT_POSITION_REQ",
    EVENT_ENGINE_STATE: "ENGINE_STATE",
    EVENT_FIX_SESSION_STATE: "FIX_SESSION_STATE",
    # Everything below this line is inferred from message-id ordering in the
    # QMI LOC v02 IDL and has NOT been confirmed against this firmware. The
    # trailing '?' is deliberate and is printed: do not quote these as facts.
    EVENT_WIFI_REQ: "WIFI_REQ?",
    EVENT_SENSOR_STREAMING_READY_STATUS: "SENSOR_STREAMING_READY?",
    EVENT_TIME_SYNC_REQ: "TIME_SYNC_REQ?",
    EVENT_SET_SPI_STREAMING_REPORT: "SET_SPI_STREAMING_REPORT?",
    EVENT_LOCATION_SERVER_CONNECTION_REQ: "LOC_SERVER_CONN_REQ?",
    EVENT_GNSS_MEASUREMENT_REPORT: "GNSS_MEASUREMENT_REPORT",
    EVENT_SV_POLYNOMIAL_REPORT: "SV_POLYNOMIAL_REPORT",
    INJECT_PREDICTED_ORBITS_DATA: "ack:INJECT_PREDICTED_ORBITS_DATA",
    INJECT_UTC_TIME: "ack:INJECT_UTC_TIME",
    INJECT_POSITION: "ack:INJECT_POSITION",
    INJECT_XTRA_DATA: "ack:INJECT_XTRA_DATA",
}

# Every indication this tool considers "the engine asking the AP for something".
# Membership is by name, per the brief; the ones with a '?' are id-inferred.
REQUEST_IDS = frozenset((
    EVENT_NI_NOTIFY_VERIFY_REQ, EVENT_INJECT_TIME_REQ,
    EVENT_INJECT_PREDICTED_ORBITS_REQ, EVENT_INJECT_POSITION_REQ,
    EVENT_WIFI_REQ, EVENT_TIME_SYNC_REQ, EVENT_SET_SPI_STREAMING_REPORT,
    EVENT_LOCATION_SERVER_CONNECTION_REQ,
))

# Indications that are acknowledgements of something *we* sent, not requests.
INJECT_ACK_IDS = frozenset((INJECT_PREDICTED_ORBITS_DATA, INJECT_UTC_TIME,
                            INJECT_POSITION, INJECT_XTRA_DATA))

# qmiLocPositionSrcEnumT / Qmi.LocPositionSource, read out of the gir on the
# phone rather than assumed.
POS_SOURCE = {"gnss": 0, "cellid": 1, "enh_cellid": 2, "wifi": 3,
              "terrestrial": 4, "hybrid": 5, "other": 6}
# Qmi.LocInjectedTimeSource
TIME_SOURCE = {"unknown": 0, "ap": 1, "ntp": 2, "nts": 3}

OPMODES = {"default": 1, "msb": 2, "msa": 3, "standalone": 4,
           "cellid": 5, "wwan": 6}
OPMODE_NAME = {v: k for k, v in OPMODES.items()}

ACCURACY = {"low": 1, "med": 2, "high": 3}
RECURRENCE = {"periodic": 1, "single": 2}

# qmiLocConstellationMaskT_v02 (uint64)
CONSTELL = {"glo": 0x01, "bds": 0x02, "qzss": 0x04,
            "gal": 0x08, "navic": 0x10, "gps": 0x20}
# QMI_LOC_GET_CONSTELLATION_CONTROL indication TLV -> constellation
CONSTELL_TLV = {0x10: "gps", 0x11: "glo", 0x12: "bds",
                0x13: "qzss", 0x14: "gal", 0x15: "navic"}
CONSTELL_STATUS = {
    0: "ENABLED_MANDATORY", 1: "ENABLED_INTERNALLY", 2: "ENABLED_BY_CLIENT",
    100: "DISABLED_NOT_SUPPORTED", 101: "DISABLED_INTERNALLY",
    102: "DISABLED_BY_CLIENT", 103: "DISABLED_NO_MEMORY",
}

LOC_STATUS = {0: "SUCCESS", 1: "GENERAL_FAILURE", 2: "UNSUPPORTED",
              3: "INVALID_PARAMETER", 4: "ENGINE_BUSY", 5: "PHONE_OFFLINE",
              6: "TIMEOUT", 7: "CONFIG_NOT_SUPPORTED", 8: "INSUFFICIENT_MEMORY",
              9: "MAX_GEOFENCE_PROGRAMMED", 10: "XTRA_VERSION_CHECK_FAILURE",
              11: "GNSS_DISABLED"}

# qmiLocEventRegMaskT_v02 (uint64)
EVENTS = {
    "min": 0x001 | 0x002 | 0x004,        # position report | sv info | nmea
    "engine": 0x080 | 0x100,             # engine state | fix session state
    "inject": 0x010 | 0x020 | 0x040,
    "wifi": 0x200,
    "nmea": 0x004,
    "pos": 0x001,
    "sv": 0x002,
    # Every *request-shaped* event bit, and nothing else: NI notify verify,
    # wifi, sensor streaming ready, time sync, SPI streaming, location server
    # connection, inject-wifi-AP-data. Deliberately NOT the geofence, batching,
    # pedometer, vehicle-data or GDT bits, and not the top 32 bits: the one run
    # that registered all 64 reset the SoC after 253 s (n=1, unexplained), so
    # widening the mask is not free and this group is the narrow version.
    # Bit indices are from the IDL and are NOT confirmed on this firmware.
    "req": 0x00000008 | 0x00000200 | 0x00000400 | 0x00000800 |
           0x00001000 | 0x00002000 | 0x00200000,
    "meas": 0x01000000,                  # GNSS_MEASUREMENT_REPORT (bit 24)
    "svpoly": 0x02000000,                # SV_POLYNOMIAL_REPORT   (bit 25)
    "hi": 0x0FF00000,                    # bits 20..27, slack around those two
    "none": 0,
}

SESSION_ID = 11


# ------------------------------------------------------------------- logging

def log(msg):
    """Print, and also write into the kernel ring buffer.

    The point of /dev/kmsg is ramoops: on a watchdog reset stdout is lost with
    the ssh session but the console survives in /var/lib/systemd/pstore/, so
    the last line there says which step died.
    """
    print(msg, flush=True)
    try:
        with open("/dev/kmsg", "w") as k:
            k.write("rhodep-gnss: %s\n" % msg)
    except Exception:
        pass


class Void(Exception):
    """A configuration step did not apply. The run proves nothing."""


# ------------------------------------------------------- service localisation

def find_loc(override=None):
    """Return (node, port) of the QMI LOC service on this boot.

    QRTR service ports are handed out by the router as services register, so
    they are stable within a boot and NOT across boots: measured 0:108 on the
    2026-09-05 boot and 0:107 on the next one. Sending a session to the wrong
    port is not dangerous - nothing answers, INFORM_CLIENT_REVISION times out
    and the run Voids - but it silently costs a run, and worse, a Void that
    looks like a timeout is easy to misread as "the modem ignored us".

    Parsed out of qrtr-lookup, whose columns are
        Service Version Instance Node Port  Name
    """
    if override:
        n, _, p = override.partition(":")
        return (0, int(n)) if not p else (int(n), int(p))
    try:
        out = subprocess.run(["qrtr-lookup"], capture_output=True, text=True,
                             timeout=15).stdout
    except Exception as e:
        log("qrtr-lookup failed (%s); falling back to %d:%d"
            % (e, LOC_NODE, LOC_PORT))
        return LOC_NODE, LOC_PORT
    hits = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == str(LOC_SERVICE) and f[3].isdigit() \
                and f[4].isdigit():
            hits.append((int(f[3]), int(f[4])))
    if not hits:
        raise Void("qrtr-lookup lists no service %d (LOC); the location "
                   "service is not running on this boot" % LOC_SERVICE)
    if len(hits) > 1:
        log("qrtr-lookup lists %d instances of service %d: %s; using the first"
            % (len(hits), LOC_SERVICE, hits))
    return hits[0]


# ---------------------------------------------------------------- QMI client

class Loc:
    """One AF_QIPCRTR socket == one QMI LOC client, for its whole lifetime."""

    def __init__(self, node=LOC_NODE, port=LOC_PORT):
        self.node, self.port = node, port
        self.s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
        self.s.bind((1, 0))
        self.s.settimeout(0.2)
        self.txn = 0
        self.pending_ind = []
        log("LOC client bound to %s (qrtr %d:%d)"
            % (self.s.getsockname(), self.node, self.port))

    # -- framing ------------------------------------------------------------
    @staticmethod
    def _encode(txn, msg_id, tlvs):
        body = b""
        for t, v in tlvs:
            body += bytes([t]) + struct.pack("<H", len(v)) + v
        return (b"\x00" + struct.pack("<H", txn) + struct.pack("<H", msg_id)
                + struct.pack("<H", len(body)) + body)

    @staticmethod
    def _decode(pkt):
        flags, txn, mid, ln = struct.unpack("<BHHH", pkt[:7])
        out, body, i = {}, pkt[7:7 + ln], 0
        while i + 3 <= len(body):
            t = body[i]
            n = struct.unpack("<H", body[i + 1:i + 3])[0]
            out[t] = body[i + 3:i + 3 + n]
            i += 3 + n
        return flags, txn, mid, out

    def _pump(self, deadline):
        while time.time() < deadline:
            try:
                pkt, _ = self.s.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                return None
            if len(pkt) < 7:
                continue
            yield self._decode(pkt)

    # -- request/response ---------------------------------------------------
    def call(self, msg_id, tlvs=(), timeout=6.0, want_ind=True):
        """Send a request; return (resp_tlvs, ind_tlvs).

        LOC answers a request with a RESP carrying only the QMI result, and
        then an indication with the same message id carrying the real status.
        Both are waited for; unrelated indications are queued for the session
        listener rather than dropped.
        """
        self.txn = (self.txn + 1) & 0xFFFF
        txn = self.txn
        self.s.sendto(self._encode(txn, msg_id, tlvs), (self.node, self.port))
        resp = ind = None
        for flags, t, m, tl in self._pump(time.time() + timeout):
            if flags == 0x02 and m == msg_id:
                resp = tl
            elif flags == 0x04 and m == msg_id:
                ind = tl
            else:
                self.pending_ind.append((flags, m, tl))
            if resp is not None and (ind is not None or not want_ind):
                break
        return resp, ind

    @staticmethod
    def qmi_result(resp):
        if not resp or 0x02 not in resp:
            return None
        result, error = struct.unpack("<HH", resp[0x02][:4])
        return result, error

    @staticmethod
    def loc_status(ind):
        if not ind or 0x01 not in ind:
            return None
        return struct.unpack("<I", ind[0x01][:4])[0]

    def checked(self, name, msg_id, tlvs=(), timeout=6.0):
        """Send, and raise Void unless both layers reported success."""
        resp, ind = self.call(msg_id, tlvs, timeout)
        qr = self.qmi_result(resp)
        st = self.loc_status(ind)
        log("  %s: qmi_result=%s loc_status=%s"
            % (name, qr, "%s(%s)" % (LOC_STATUS.get(st, "?"), st)
               if st is not None else None))
        if qr is None:
            raise Void("%s: no response from the LOC service" % name)
        if qr[0] != 0:
            raise Void("%s: QMI error %d" % (name, qr[1]))
        if st is not None and st != 0:
            raise Void("%s: LOC status %s" % (name, LOC_STATUS.get(st, st)))
        return ind


# ------------------------------------------------------------- read-back GETs

def read_constellations(loc):
    _, ind = loc.call(GET_CONSTELLATION_CONTROL)
    if not ind:
        raise Void("GET_CONSTELLATION_CONTROL gave no indication")
    st = loc.loc_status(ind)
    if st != 0:
        raise Void("GET_CONSTELLATION_CONTROL status %s"
                   % LOC_STATUS.get(st, st))
    out = {}
    for tlv, name in CONSTELL_TLV.items():
        if tlv in ind:
            out[name] = struct.unpack("<I", ind[tlv][:4])[0]
    return out


def read_multiband(loc):
    _, ind = loc.call(GET_MULTIBAND_CONFIG)
    if not ind:
        raise Void("GET_MULTIBAND_CONFIG gave no indication")
    st = loc.loc_status(ind)
    if st != 0:
        raise Void("GET_MULTIBAND_CONFIG status %s" % LOC_STATUS.get(st, st))
    if 0x10 not in ind:
        return None
    return struct.unpack("<Q", ind[0x10][:8])[0]


def read_opmode(loc):
    _, ind = loc.call(GET_OPERATION_MODE)
    if not ind or 0x10 not in ind:
        raise Void("GET_OPERATION_MODE gave no mode")
    return struct.unpack("<I", ind[0x10][:4])[0]


def read_fix_criteria(loc):
    _, ind = loc.call(GET_FIX_CRITERIA, timeout=4.0)
    if not ind:
        return None
    out = {}
    names = {0x10: "accuracy", 0x11: "intermediate", 0x12: "minInterval",
             0x14: "configAltitudeAssumed", 0x15: "minIntermediateInterval",
             0x16: "positionReportTimeout", 0x17: "sharePosition",
             0x18: "powerMode"}
    for tlv, name in names.items():
        if tlv not in ind:
            continue
        v = ind[tlv]
        if name == "powerMode" and len(v) >= 8:
            out[name] = struct.unpack("<II", v[:8])
        elif len(v) >= 4:
            out[name] = struct.unpack("<I", v[:4])[0]
        else:
            out[name] = v[0]
    return out


def show_config(loc, tag):
    cons = read_constellations(loc)
    log("%s constellations: %s" % (tag, " ".join(
        "%s=%s(%d)" % (k, CONSTELL_STATUS.get(v, "?"), v)
        for k, v in cons.items())))
    mb = read_multiband(loc)
    log("%s multiband secondaryGnssConfig = %s"
        % (tag, "0x%016x" % mb if mb is not None else "(absent)"))
    log("%s operation mode = %s" % (tag, OPMODE_NAME.get(read_opmode(loc), "?")))
    fc = read_fix_criteria(loc)
    log("%s fix criteria = %s" % (tag, fc))
    return cons, mb


# --------------------------------------------------------------- indications

SESS_STATUS = {0: "SUCCESS", 1: "IN_PROGRESS", 2: "GENERAL_FAILURE",
               3: "TIMEOUT", 4: "USER_END", 5: "BAD_PARAMETER",
               6: "PHONE_OFFLINE", 7: "ENGINE_LOCKED"}


def raw_tlvs(tl, limit=48):
    """Every TLV as id/len/hex, truncated.

    The point is that a run with indications this decoder does not recognise
    must not look the same as a silent run. Anything not decoded below still
    shows up here as bytes.
    """
    out = []
    for k in sorted(tl):
        v = tl[k]
        h = v[:limit].hex()
        out.append("0x%02x[%d]=%s%s" % (k, len(v), h,
                                        ".." if len(v) > limit else ""))
    return " ".join(out)


def decode_position(tl):
    bits = []
    if 0x01 in tl:
        s = struct.unpack("<I", tl[0x01][:4])[0]
        bits.append("session=%d(%s)" % (s, SESS_STATUS.get(s, "?")))
    for tlv, nm in ((0x10, "lat"), (0x11, "lon")):
        if tlv in tl and len(tl[tlv]) >= 8:
            bits.append("%s=%.6f" % (nm, struct.unpack("<d", tl[tlv][:8])[0]))
    for tlv, nm in ((0x12, "horUnc"), (0x1A, "altEll"), (0x1B, "altMSL")):
        if tlv in tl and len(tl[tlv]) >= 4:
            bits.append("%s=%.1f" % (nm, struct.unpack("<f", tl[tlv][:4])[0]))
    if 0x27 in tl and len(tl[0x27]) >= 6:
        wk, tow = struct.unpack("<HI", tl[0x27][:6])
        bits.append("gpsWeek=%d tow=%d" % (wk, tow))
    if 0x2B in tl and len(tl[0x2B]) >= 4:
        bits.append("fixId=%d" % struct.unpack("<I", tl[0x2B][:4])[0])
    if 0x2C in tl and len(tl[0x2C]) >= 1:
        bits.append("svUsed=%d" % tl[0x2C][0])
    return " ".join(bits)


def decode_svlist(tl):
    """svList is a length-prefixed array; the prefix width is not assumed."""
    if 0x10 not in tl:
        return "no svList TLV"
    b = tl[0x10]
    n = b[0]
    rest = len(b) - 1
    if n and rest % n == 0:
        esz = rest // n
        sats = []
        for i in range(min(n, 24)):
            e = b[1 + i * esz:1 + (i + 1) * esz]
            if esz >= 28:
                sysid, svid = struct.unpack("<IH", e[4:10])
                snr = struct.unpack("<f", e[esz - 12:esz - 8])[0]
                sats.append("%d/%d:%.0f" % (sysid, svid, snr))
        return "n=%d entrysize=%d %s" % (n, esz, " ".join(sats))
    return "n=%d payload=%d bytes (not divisible)" % (n, rest)


def decode_time_req(tl):
    """qmiLocEventInjectTimeReqInd: TLV 0x10 is the XTRA-T server list.

    Measured payload on rhodep, every fix cycle:
        0x10[62] = e8030000 03 12 "time.xtracloud.net" ...
    i.e. a uint32 (1000) then a length-prefixed string. Decoded loosely: pull
    every printable run of >=4 characters out, and report the leading uint32.
    """
    bits = []
    if 0x10 in tl:
        b = tl[0x10]
        if len(b) >= 4:
            bits.append("u32=%d" % struct.unpack("<I", b[:4])[0])
        run, out = b"", []
        for ch in b:
            if 32 <= ch < 127:
                run += bytes([ch])
            else:
                if len(run) >= 4:
                    out.append(run.decode("ascii"))
                run = b""
        if len(run) >= 4:
            out.append(run.decode("ascii"))
        if out:
            bits.append("strings=%s" % ",".join(out))
    return " ".join(bits)


def show_indication(state, msg_id, tl):
    state["count"] += 1
    per = state["per"]
    per[msg_id] = per.get(msg_id, 0) + 1
    seen = per[msg_id]
    name = MSG_NAME.get(msg_id, "0x%04x" % msg_id)

    # Every request-shaped indication is logged with its raw TLVs the FIRST
    # time each distinct payload is seen, however late in the run that is.
    # Rate limiting by count alone would hide a request whose contents change
    # -- e.g. an INJECT_TIME_REQ that stops naming a server once it is
    # answered -- behind "we have already printed 5 of those".
    if msg_id in REQUEST_IDS:
        sig = raw_tlvs(tl, 64)
        last = state.setdefault("reqsig", {})
        if last.get(msg_id) != sig:
            last[msg_id] = sig
            extra = decode_time_req(tl) if msg_id == EVENT_INJECT_TIME_REQ else ""
            log("REQUEST %s #%d %s%s"
                % (name, seen, sig, ("  [%s]" % extra) if extra else ""))
            return

    # Rate limit: the first 5 of every kind in full, then every 20th. A
    # measurement report is large and can arrive at several Hz, and the console
    # ramoops buffer is 256 KB total.
    if seen > 5 and seen % 20 != 0:
        return

    if msg_id == EVENT_NMEA and 0x01 in tl:
        log("NMEA   %s" % tl[0x01].decode("ascii", "replace").strip())
        return
    if msg_id == EVENT_ENGINE_STATE and 0x01 in tl:
        s = struct.unpack("<I", tl[0x01][:4])[0]
        log("ENGINE state=%s  raw %s" % ({1: "ON", 2: "OFF"}.get(s, s),
                                         raw_tlvs(tl)))
        return
    if msg_id == EVENT_FIX_SESSION_STATE:
        s = struct.unpack("<I", tl[0x01][:4])[0] if 0x01 in tl else None
        log("FIXSESS state=%s  raw %s"
            % ({1: "STARTED", 2: "FINISHED"}.get(s, s), raw_tlvs(tl)))
        return
    if msg_id == EVENT_POSITION_REPORT:
        log("POS    %s" % decode_position(tl))
        log("POS    #%d raw %s" % (seen, raw_tlvs(tl, 16)))
        return
    if msg_id == EVENT_GNSS_SV_INFO:
        log("SV     #%d %s" % (seen, decode_svlist(tl)))
        log("SV     #%d raw %s" % (seen, raw_tlvs(tl, 24)))
        return
    if msg_id == EVENT_GNSS_MEASUREMENT_REPORT:
        log("*** MEASUREMENT REPORT #%d - the correlator is running ***" % seen)
        log("MEAS   %s" % raw_tlvs(tl, 32))
        return
    if msg_id == EVENT_SV_POLYNOMIAL_REPORT:
        log("SVPOLY #%d %s" % (seen, raw_tlvs(tl, 16)))
        return
    log("EVENT  %s #%d %s" % (name, seen, raw_tlvs(tl, 24)))


# ----------------------------------------------------------------- responder

class Responder:
    """Answer the engine's in-session assistance requests, same QMI client.

    Everything prior to this answered the engine only *before* a session, from
    a different client, with libqmi. The engine asks for time on every single
    fix cycle and had never once been answered. This class answers on the same
    AF_QIPCRTR socket that owns the session, inside the fix cycle that asked.

    It never waits: an injection is one sendto, and the RESP/IND that come back
    are picked up by the session listener and checked there. Blocking on each
    answer would put a 6 s stall inside a 1 Hz loop.

    NOTHING HERE CAN START THE ENGINE. Only QMI_LOC_START does that, and it is
    not in this class.
    """

    # Do not answer the same request more often than this. The time request
    # arrives once per fix cycle (1 Hz) so it is effectively unlimited; the
    # orbit request must not turn into a 40 kB re-upload every second.
    MIN_GAP = {"time": 0.0, "position": 0.0, "xtra": 120.0}

    def __init__(self, loc, kinds, lat, lon, acc, xtra_path,
                 possrc="wifi", timesrc="ntp", answer=None):
        self.loc = loc
        self.kinds = kinds
        # What the responder answers *in session*. preinject= can send a
        # message that is deliberately NOT answered later, which is how a run
        # can put assistance into the engine before QMI_LOC_START and then
        # keep its hands off for the whole session.
        self.answer = set(answer if answer is not None else kinds)
        self.lat, self.lon, self.acc = lat, lon, acc
        self.possrc = POS_SOURCE[possrc]
        self.timesrc = TIME_SOURCE[timesrc]
        self.xtra = None
        self.stats = {}
        self.pending = {}
        self.last = {}
        self.first_answer_at = {}
        # Answers are ignored until this wall-clock time. Used by
        # respondafter=, so a run can show the session cycling healthily for N
        # seconds first and only then start answering: without it the answers
        # land in the replayed backlog of queued indications and go out as a
        # burst of five in eight milliseconds, which is a different experiment.
        self.enable_at = 0.0
        # Unconditional timer injection, for opmodes that never ask. This is
        # the control for "is it answering, or is it any LOC traffic at all".
        self.every = 0.0
        self._next_tick = 0.0
        if "xtra" in kinds:
            try:
                with open(xtra_path, "rb") as f:
                    self.xtra = f.read()
                log("responder: XTRA almanac %s, %d bytes"
                    % (xtra_path, len(self.xtra)))
            except OSError as e:
                log("responder: no XTRA almanac (%s); orbit answers disabled" % e)
                self.kinds = [k for k in kinds if k != "xtra"]
        log("responder: can send %s / answers in-session %s  "
            "pos=%.6f,%.6f +/-%.0fm src=%d timesrc=%d"
            % (",".join(sorted(self.kinds)) or "(nothing)",
               ",".join(sorted(self.answer)) or "(nothing)", self.lat, self.lon,
               self.acc, self.possrc, self.timesrc))

    # -- bookkeeping --------------------------------------------------------
    def _stat(self, name):
        return self.stats.setdefault(
            name, {"sent": 0, "resp_ok": 0, "resp_err": 0,
                   "ind_ok": 0, "ind_err": 0, "errors": []})

    def _send(self, kind, msg_id, tlvs, quiet=False):
        loc = self.loc
        loc.txn = (loc.txn + 1) & 0xFFFF
        txn = loc.txn
        try:
            loc.s.sendto(loc._encode(txn, msg_id, tlvs), (loc.node, loc.port))
        except OSError as e:
            log("RESPOND %s: sendto failed: %s" % (kind, e))
            return None
        st = self._stat(kind)
        st["sent"] += 1
        self.pending[txn] = (kind, msg_id, time.time())
        if not quiet and st["sent"] <= 3:
            log("RESPOND %s -> 0x%04x txn=%d" % (kind, msg_id, txn))
        return txn

    def _due(self, kind):
        gap = self.MIN_GAP.get(kind, 1.0)
        now = time.time()
        if now - self.last.get(kind, 0.0) < gap:
            return False
        self.last[kind] = now
        return True

    # -- the three answers --------------------------------------------------
    def inject_time(self):
        """QMI_LOC_INJECT_UTC_TIME (0x0038).

        TLV order and contents copied from what qmicli --loc-inject-time puts
        on the wire on this device. TLV 0x10 (Time Source) is optional in
        libqmi's model and MANDATORY on this firmware: without it the modem
        answers MalformedMessage.
        """
        return self._send("time", INJECT_UTC_TIME, [
            (0x10, struct.pack("<I", self.timesrc)),
            (0x02, struct.pack("<I", 1000)),
            (0x01, struct.pack("<Q", int(time.time() * 1000))),
        ])

    def inject_position(self):
        """QMI_LOC_INJECT_POSITION (0x0039), qmicli's TLV set and order."""
        return self._send("position", INJECT_POSITION, [
            (0x1D, struct.pack("<I", self.possrc)),
            (0x1B, struct.pack("<Q", int(time.time() * 1000))),
            (0x13, bytes([68])),
            (0x12, struct.pack("<f", float(self.acc))),
            (0x11, struct.pack("<d", float(self.lon))),
            (0x10, struct.pack("<d", float(self.lat))),
        ])

    def inject_xtra(self):
        """QMI_LOC_INJECT_XTRA_DATA (0x00A7), chunked 1024 bytes per part.

        Part Data (TLV 0x04) is a length-prefixed array: uint16 count, then the
        bytes. That prefix is why the TLV is 1026 bytes long for a 1024-byte
        part, and getting it wrong is the difference between an accepted
        almanac and a silent one.
        0x0035 InjectPredictedOrbitsData is NotSupported on this modem.
        """
        data = self.xtra
        if not data:
            return
        t0 = time.time()
        total = (len(data) + 1023) // 1024
        for n in range(total):
            blob = data[n * 1024:(n + 1) * 1024]
            self._send("xtra", INJECT_XTRA_DATA, [
                (0x04, struct.pack("<H", len(blob)) + blob),
                (0x03, struct.pack("<H", n + 1)),
                (0x02, struct.pack("<H", total)),
                (0x01, struct.pack("<I", len(data))),
            ], quiet=True)
            # The socket is a datagram socket to a modem; give the router a
            # moment rather than dumping 40 KB into it in one go, and drain
            # anything that came back so the receive queue cannot overflow.
            time.sleep(0.002)
            self.drain()
        log("RESPOND xtra: %d parts, %d bytes sent in %.0f ms"
            % (total, len(data), (time.time() - t0) * 1000))

    def drain(self):
        """Non-blocking sweep of the socket, used mid-XTRA-upload.

        Genuinely non-blocking: with the session socket's 50 ms timeout this
        would cost 50 ms per call, i.e. two seconds across a 40-part upload,
        which inside a 1 Hz fix loop would be its own experiment.
        """
        old = self.loc.s.gettimeout()
        self.loc.s.settimeout(0)
        try:
            while True:
                try:
                    pkt, _ = self.loc.s.recvfrom(65536)
                except (socket.timeout, BlockingIOError, OSError):
                    return
                if len(pkt) >= 7:
                    self.loc.pending_ind.append(self.loc._decode(pkt))
        finally:
            self.loc.s.settimeout(old)

    # -- dispatch -----------------------------------------------------------
    def tick(self, now):
        """Timer-driven injection, independent of any request. `injectevery=`.

        cellid never emits INJECT_TIME_REQ, so the only way to ask "does
        sending QMI_LOC_INJECT_UTC_TIME into a live session kill the SoC even
        when the measurement engine was never brought up" is to send it on a
        clock rather than in reply to something.
        """
        if self.every <= 0 or now < self.enable_at:
            return
        if now < self._next_tick:
            return
        self._next_tick = now + self.every
        if "time" in self.answer:
            self.inject_time()
            self.first_answer_at.setdefault("time", now)
        if "position" in self.answer:
            self.inject_position()
            self.first_answer_at.setdefault("position", now)

    def on_request(self, msg_id, tl):
        """Called for every indication the listener sees. Returns True if an
        answer was sent."""
        if time.time() < self.enable_at:
            return False
        if msg_id == EVENT_INJECT_TIME_REQ and "time" in self.answer:
            if self._due("time"):
                self.inject_time()
                self.first_answer_at.setdefault("time", time.time())
                return True
        elif msg_id == EVENT_INJECT_POSITION_REQ and "position" in self.answer:
            if self._due("position"):
                self.inject_position()
                self.first_answer_at.setdefault("position", time.time())
                return True
        elif msg_id == EVENT_INJECT_PREDICTED_ORBITS_REQ and "xtra" in self.answer:
            if self._due("xtra"):
                self.inject_xtra()
                self.first_answer_at.setdefault("xtra", time.time())
                return True
        return False

    def on_response(self, txn, msg_id, tl):
        """A QMI RESP came back. Returns True if it was ours."""
        got = self.pending.pop(txn, None)
        if got is None:
            return False
        kind, mid, t0 = got
        st = self._stat(kind)
        qr = Loc.qmi_result(tl)
        if qr is None or qr[0] != 0:
            st["resp_err"] += 1
            if len(st["errors"]) < 6:
                st["errors"].append("RESP %s" % (qr,))
            log("RESPOND %s: RESP qmi_result=%s  (%.0f ms)"
                % (kind, qr, (time.time() - t0) * 1000))
        else:
            st["resp_ok"] += 1
            if st["resp_ok"] <= 3:
                log("RESPOND %s: RESP ok (%.0f ms)"
                    % (kind, (time.time() - t0) * 1000))
        return True

    def on_ack(self, msg_id, tl):
        """An indication that is an ack for an injection we sent."""
        kind = {INJECT_UTC_TIME: "time", INJECT_POSITION: "position",
                INJECT_XTRA_DATA: "xtra",
                INJECT_PREDICTED_ORBITS_DATA: "orbits"}.get(msg_id, "?")
        st = self._stat(kind)
        s = Loc.loc_status(tl)
        if s == 0:
            st["ind_ok"] += 1
            if st["ind_ok"] <= 3:
                log("RESPOND %s: IND status=SUCCESS %s" % (kind, raw_tlvs(tl, 8)))
        else:
            st["ind_err"] += 1
            if len(st["errors"]) < 6:
                st["errors"].append("IND %s" % LOC_STATUS.get(s, s))
            log("RESPOND %s: IND status=%s  %s"
                % (kind, LOC_STATUS.get(s, s), raw_tlvs(tl, 16)))

    def report(self):
        log("-" * 60)
        if not self.stats:
            log("RESPONDER: nothing was answered")
            return
        for kind in sorted(self.stats):
            s = self.stats[kind]
            log("RESPONDER %-9s sent=%d  RESP ok=%d err=%d  IND ok=%d err=%d%s"
                % (kind, s["sent"], s["resp_ok"], s["resp_err"],
                   s["ind_ok"], s["ind_err"],
                   ("  errors: " + "; ".join(s["errors"])) if s["errors"] else ""))
        if self.pending:
            log("RESPONDER: %d injection(s) never got a RESP" % len(self.pending))


# ---------------------------------------------------------------------- main

def parse_args(argv):
    cfg = {
        "seconds": 30, "opmode": None, "constell": "keep", "multiband": "keep",
        "accuracy": None, "recurrence": None, "mininterval": None,
        "powermode": None, "events": "min,engine", "port": None,
        "nostart": False, "probe": False, "norestore": False,
        "respond": "", "lat": None, "lon": None, "acc": "50",
        "xtrafile": "/var/lib/rhodep-gnss/xtra.bin",
        "possrc": "wifi", "timesrc": "ntp", "injecttest": False,
        "respondafter": "0", "injectevery": "0", "preinject": "",
    }
    for a in argv[1:]:
        if a in ("nostart", "probe", "norestore", "injecttest"):
            cfg[a] = True
        elif "=" in a:
            k, v = a.split("=", 1)
            if k not in cfg:
                sys.exit("unknown option %r" % k)
            cfg[k] = v
        elif a.isdigit():
            cfg["seconds"] = int(a)
        else:
            sys.exit("unknown argument %r" % a)
    cfg["seconds"] = int(cfg["seconds"])
    return cfg


def build_constell_masks(spec, current):
    """Return (enable, disable) uint64 masks, or None to leave it alone."""
    if spec in ("keep", ""):
        return None
    if spec == "reset":
        return "reset"
    want = {}
    if spec == "gpsonly":
        want = {"glo": False, "bds": False, "qzss": False,
                "gal": False, "navic": False, "gps": True}
    else:
        for tok in spec.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok[0] not in "+-":
                sys.exit("constell tokens need a leading + or -: %r" % tok)
            nm = tok[1:]
            if nm not in CONSTELL:
                sys.exit("unknown constellation %r" % nm)
            want[nm] = tok[0] == "+"
    enable = disable = 0
    # every bit has to appear in exactly one of the two masks
    for nm, bit in CONSTELL.items():
        on = want.get(nm)
        if on is None:
            # not mentioned: keep whatever it is now
            on = current.get(nm, 1) < 100
        if on:
            enable |= bit
        else:
            disable |= bit
    return enable, disable


def main():
    cfg = parse_args(sys.argv)
    log("=" * 60)
    log("bisect run: %s" % " ".join(sys.argv[1:]))
    node, port = find_loc(cfg["port"])
    loc = Loc(node, port)

    loc.checked("INFORM_CLIENT_REVISION", INFORM_CLIENT_REVISION,
                [(0x01, struct.pack("<I", 1))], timeout=4.0)

    cons0, mb0 = show_config(loc, "before:")

    if cfg["probe"]:
        log("probe only, nothing changed")
        return

    # ---- responder --------------------------------------------------------
    responder = None
    kinds = [k.strip() for k in cfg["respond"].split(",")
             if k.strip() and k.strip() != "none"]
    for k in kinds:
        if k not in ("time", "position", "xtra"):
            sys.exit("unknown respond kind %r; known: time,position,xtra" % k)
    pre = [k.strip() for k in cfg["preinject"].split(",")
           if k.strip() and k.strip() != "none"]
    for k in pre:
        if k not in ("time", "position", "xtra"):
            sys.exit("unknown preinject kind %r; known: time,position,xtra" % k)
    if kinds or pre or cfg["injecttest"]:
        if cfg["lat"] is None or cfg["lon"] is None:
            if "position" in kinds or "position" in pre or cfg["injecttest"]:
                sys.exit("position injection needs lat= and lon=")
        responder = Responder(
            loc, sorted(set(kinds) | set(pre)) or ["time", "position", "xtra"],
            float(cfg["lat"] or 0.0), float(cfg["lon"] or 0.0),
            float(cfg["acc"]), cfg["xtrafile"],
            cfg["possrc"], cfg["timesrc"],
            answer=kinds if (kinds or pre) else None)

    if cfg["injecttest"]:
        # Fire one of each injection with NO session running, and read the
        # RESP and the indication back. This proves the hand-rolled TLVs are
        # accepted before a session run depends on them. It cannot start the
        # engine: there is no QMI_LOC_START anywhere in this branch.
        log("-" * 60)
        log("INJECTTEST: one of each, no session")

        def itest(flags, txn, m, tl):
            if flags == 0x02:
                if not responder.on_response(txn, m, tl):
                    log("INJECTTEST unmatched RESP txn=%d msg=0x%04x %s"
                        % (txn, m, raw_tlvs(tl, 16)))
            elif flags == 0x04 and m in INJECT_ACK_IDS:
                responder.on_ack(m, tl)
            else:
                log("INJECTTEST stray flags=0x%02x msg=0x%04x %s"
                    % (flags, m, raw_tlvs(tl, 16)))

        loc.s.settimeout(0.05)
        responder.inject_time()
        responder.inject_position()
        responder.inject_xtra()
        t0 = time.time()
        while time.time() - t0 < 10.0:
            # Responder.drain() parks packets in loc.pending_ind while an
            # XTRA upload is in flight. Replaying them here is not optional:
            # without it every answer to the first two injections is lost and
            # the test reports "never got a RESP" for messages the modem in
            # fact acknowledged.
            if loc.pending_ind:
                queued, loc.pending_ind = loc.pending_ind, []
                for q in queued:
                    itest(*q)
                continue
            try:
                pkt, _ = loc.s.recvfrom(65536)
            except (socket.timeout, OSError):
                continue
            if len(pkt) < 7:
                continue
            itest(*loc._decode(pkt))
        responder.report()
        return

    applied = []

    # ---- constellations ---------------------------------------------------
    want_cons = build_constell_masks(cfg["constell"], cons0)
    if want_cons is not None:
        if want_cons == "reset":
            log("SET_CONSTELLATION_CONTROL: reset to NV default")
            loc.checked("SET_CONSTELLATION_CONTROL", SET_CONSTELLATION_CONTROL,
                        [(0x01, b"\x01")])
        else:
            en, dis = want_cons
            log("SET_CONSTELLATION_CONTROL: enable=0x%02x disable=0x%02x"
                % (en, dis))
            loc.checked("SET_CONSTELLATION_CONTROL", SET_CONSTELLATION_CONTROL,
                        [(0x01, b"\x00"),
                         (0x10, struct.pack("<Q", en)),
                         (0x11, struct.pack("<Q", dis))])
            got = read_constellations(loc)
            log("  read back: %s" % " ".join(
                "%s=%s" % (k, CONSTELL_STATUS.get(v, v)) for k, v in got.items()))
            for nm, bit in CONSTELL.items():
                if nm not in got:
                    continue
                is_on = got[nm] < 100
                asked_on = bool(en & bit)
                if is_on != asked_on and not (nm == "gps" and is_on):
                    raise Void("constellation %s asked %s, modem says %s"
                               % (nm, "on" if asked_on else "off",
                                  CONSTELL_STATUS.get(got[nm], got[nm])))
            applied.append("constell=%s" % cfg["constell"])

    # ---- multiband --------------------------------------------------------
    if cfg["multiband"] != "keep":
        want = int(cfg["multiband"], 0)
        log("SET_MULTIBAND_CONFIG: secondaryGnssConfig=0x%x" % want)
        loc.checked("SET_MULTIBAND_CONFIG", SET_MULTIBAND_CONFIG,
                    [(0x10, struct.pack("<Q", want))])
        got = read_multiband(loc)
        log("  read back: %s" % ("0x%016x" % got if got is not None else None))
        if got != want:
            raise Void("multiband asked 0x%x, modem says %s" % (want, got))
        applied.append("multiband=0x%x" % want)

    # ---- operation mode ---------------------------------------------------
    if cfg["opmode"]:
        if cfg["opmode"] not in OPMODES:
            sys.exit("unknown opmode %r; known: %s"
                     % (cfg["opmode"], sorted(OPMODES)))
        want = OPMODES[cfg["opmode"]]
        log("SET_OPERATION_MODE: %s (%d) from this client" % (cfg["opmode"], want))
        loc.checked("SET_OPERATION_MODE", SET_OPERATION_MODE,
                    [(0x01, struct.pack("<I", want))])
        got = read_opmode(loc)
        log("  read back: operation mode = %s (%d)"
            % (OPMODE_NAME.get(got, "?"), got))
        if got != want:
            raise Void("operation mode asked %s, modem says %s -- a failed set "
                       "falls through to standalone, so this run is void"
                       % (cfg["opmode"], OPMODE_NAME.get(got, got)))
        applied.append("opmode=%s" % cfg["opmode"])

    # ---- events -----------------------------------------------------------
    mask = 0
    for g in cfg["events"].split(","):
        g = g.strip()
        if g in EVENTS:
            mask |= EVENTS[g]
        elif g[:2].lower() == "0x" or g.isdigit():
            mask |= int(g, 0)          # literal mask, for bits the IDL misnames
        else:
            sys.exit("unknown event group %r" % g)
    log("REG_EVENTS mask 0x%x (%s)" % (mask, cfg["events"]))
    loc.checked("REG_EVENTS", REG_EVENTS, [(0x01, struct.pack("<Q", mask))],
                timeout=4.0)
    applied.append("events=%s" % cfg["events"])

    log("config verified: %s" % (", ".join(applied) if applied else "(defaults)"))

    # ---- pre-session assistance injection ---------------------------------
    # Assistance injected here goes in with NO session running: no
    # QMI_LOC_START has been sent and nothing below this point can be blamed
    # on in-session traffic. Every part is verified before the session starts,
    # and the run Voids if the modem did not take it -- otherwise a reset
    # would be attributed to assistance the engine never received.
    if pre:
        log("-" * 60)
        log("PREINJECT (no session running): %s" % ",".join(pre))

        def preinj(flags, txn, m, tl):
            if flags == 0x02:
                if not responder.on_response(txn, m, tl):
                    log("PREINJECT unmatched RESP txn=%d msg=0x%04x" % (txn, m))
            elif flags == 0x04 and m in INJECT_ACK_IDS:
                responder.on_ack(m, tl)
            else:
                log("PREINJECT stray flags=0x%02x msg=0x%04x %s"
                    % (flags, m, raw_tlvs(tl, 16)))

        loc.s.settimeout(0.05)
        for k in pre:
            {"time": responder.inject_time,
             "position": responder.inject_position,
             "xtra": responder.inject_xtra}[k]()
        t0 = time.time()
        while time.time() - t0 < 8.0:
            if loc.pending_ind:
                queued, loc.pending_ind = loc.pending_ind, []
                for q in queued:
                    preinj(*q)
                continue
            try:
                pkt, _ = loc.s.recvfrom(65536)
            except (socket.timeout, OSError):
                continue
            if len(pkt) >= 7:
                preinj(*loc._decode(pkt))
        responder.report()
        bad = [k for k, v in responder.stats.items()
               if v["resp_err"] or v["ind_err"] or v["ind_ok"] == 0]
        if bad:
            raise Void("preinjection not confirmed accepted for %s; a session "
                       "started now would prove nothing" % ",".join(bad))
        log("PREINJECT: all accepted, RESP and indication status checked")
        applied.append("preinject=%s" % ",".join(pre))
        loc.s.settimeout(0.2)

    if cfg["nostart"]:
        log("nostart: not starting a session")
        show_config(loc, "after: ")
        return

    # ---- start ------------------------------------------------------------
    tlvs = [(0x01, bytes([SESSION_ID]))]
    desc = ["session=%d" % SESSION_ID]
    if cfg["recurrence"]:
        v = RECURRENCE[cfg["recurrence"]]
        tlvs.append((0x10, struct.pack("<I", v)))
        desc.append("recurrence=%s" % cfg["recurrence"])
    if cfg["accuracy"]:
        v = ACCURACY[cfg["accuracy"]]
        tlvs.append((0x11, struct.pack("<I", v)))
        desc.append("accuracy=%s" % cfg["accuracy"])
    tlvs.append((0x12, struct.pack("<I", 1)))          # intermediate reports on
    if cfg["mininterval"]:
        tlvs.append((0x13, struct.pack("<I", int(cfg["mininterval"]))))
        desc.append("minInterval=%s" % cfg["mininterval"])
    if cfg["powermode"]:
        parts = cfg["powermode"].split(",")
        pm = int(parts[0])
        tbm = int(parts[1]) if len(parts) > 1 else 1000
        tlvs.append((0x1A, struct.pack("<II", pm, tbm)))
        desc.append("powerMode=%d/%dms" % (pm, tbm))

    log("-" * 60)
    log("QMI_LOC_START  %s" % "  ".join(desc))
    resp, ind = loc.call(START, tlvs, timeout=8.0)
    qr = loc.qmi_result(resp)
    log("start: qmi_result=%s" % (qr,))
    if qr is None:
        raise Void("QMI_LOC_START got no response")
    if qr[0] != 0:
        raise Void("QMI_LOC_START QMI error %d" % qr[1])
    log("session %d started; listening %d s" % (SESSION_ID, cfg["seconds"]))

    fc = read_fix_criteria(loc)
    log("fix criteria read back after start: %s" % fc)

    # ---- post-start verification -----------------------------------------
    # A START TLV can only be read back once the session exists, so this cannot
    # void the run before the dangerous operation the way the SET/GET pairs
    # above do. It is logged instead, loudly, and repeated in the last line.
    unverified = []
    if fc:
        if cfg["powermode"]:
            _p = cfg["powermode"].split(",")
            want_pm = int(_p[0])
            want_tbm = int(_p[1]) if len(_p) > 1 else 1000
            got = fc.get("powerMode")
            if got is None:
                unverified.append("powerMode asked %d, GET_FIX_CRITERIA has no "
                                  "TLV 0x18" % want_pm)
            elif got[0] != want_pm:
                unverified.append("powerMode asked %d, reads back %s"
                                  % (want_pm, got))
            elif got[1] != want_tbm:
                # The second uint32 of TLV 0x18 is the duty-cycle knob. If it
                # does not read back, a threshold walk along it is measuring
                # nothing, so say so rather than plot a curve through noise.
                unverified.append("powerMode enum %d applied but tBetween asked "
                                  "%d, reads back %d -- the duty-cycle knob did "
                                  "NOT take" % (want_pm, want_tbm, got[1]))
            else:
                log("POSTSTART-VERIFY: powerMode = %s  APPLIED" % (got,))
        if cfg["mininterval"]:
            want_mi = int(cfg["mininterval"])
            got_mi = fc.get("minInterval")
            if got_mi != want_mi:
                unverified.append("minInterval asked %d, reads back %s"
                                  % (want_mi, got_mi))
            else:
                log("POSTSTART-VERIFY: minInterval = %s  APPLIED" % got_mi)
    for u in unverified:
        log("POSTSTART-UNVERIFIED: %s" % u)
    log("-" * 60)

    # ---- listen -----------------------------------------------------------
    state = {"count": 0, "per": {}}
    if responder is not None:
        responder.every = float(cfg["injectevery"])
        delay = float(cfg["respondafter"])
        responder.enable_at = time.time() + delay
        log("responder armed: after %.0f s, timer every %.1f s"
            % (delay, responder.every))

    def handle(flags, txn, m, tl):
        if flags == 0x02:
            if responder is not None and responder.on_response(txn, m, tl):
                return
            return
        if flags != 0x04:
            return
        if m in INJECT_ACK_IDS:
            # an acknowledgement of something we injected, not engine output
            if responder is not None:
                responder.on_ack(m, tl)
                return
        show_indication(state, m, tl)
        if responder is not None:
            responder.on_request(m, tl)

    for item in loc.pending_ind:
        if len(item) == 3:
            f, m, tl = item
            handle(f, 0, m, tl)
        else:
            handle(item[0], item[1], item[2], item[3])
    loc.pending_ind = []

    t0 = time.time()
    last_beat = 0.0
    loc.s.settimeout(0.05)
    while True:
        now = time.time() - t0
        if now >= cfg["seconds"]:
            break
        try:
            pkt, _ = loc.s.recvfrom(65536)
            flags, txn, m, tl = loc._decode(pkt)
            handle(flags, txn, m, tl)
        except socket.timeout:
            pass
        # Responder.drain() parks packets here while an XTRA upload is in
        # flight; replay them so nothing is lost.
        if loc.pending_ind:
            queued, loc.pending_ind = loc.pending_ind, []
            for flags, txn, m, tl in queued:
                handle(flags, txn, m, tl)
        # The reset lands ~100 ms after START, so the first seconds need 100 ms
        # resolution; after that the heartbeat is only proving liveness and a
        # 300 s run at 10 Hz would push the whole session out of the 256 KB
        # ramoops console.
        if responder is not None:
            responder.tick(time.time())
        beat = 0.1 if now < 5 else 5.0
        if now - last_beat >= beat:
            last_beat = now
            log("alive %d ms (indications so far: %d)"
                % (int(now * 1000), state["count"]))

    # ---- finish -----------------------------------------------------------
    log("-" * 60)
    log("SURVIVED %d s; indications received: %d" % (cfg["seconds"], state["count"]))
    for m in sorted(state["per"]):
        log("  indication %-26s x%d"
            % (MSG_NAME.get(m, "0x%04x" % m), state["per"][m]))
    log("MEASUREMENT REPORTS: %d   (0 = the correlator was never demonstrated "
        "to run)" % state["per"].get(EVENT_GNSS_MEASUREMENT_REPORT, 0))
    log("SATELLITE INFO INDICATIONS: %d"
        % state["per"].get(EVENT_GNSS_SV_INFO, 0))
    reqs = [(m, n) for m, n in state["per"].items() if m in REQUEST_IDS]
    log("REQUEST INVENTORY: %d kind(s)" % len(reqs))
    for m, n in sorted(reqs):
        log("  request %-28s x%d" % (MSG_NAME.get(m, "0x%04x" % m), n))
    if responder is not None:
        responder.report()
    if unverified:
        log("RESULT-CAVEAT: %s" % "; ".join(unverified))
    loc.call(STOP, [(0x01, bytes([SESSION_ID]))], timeout=5.0)
    log("session stopped")
    show_config(loc, "after: ")

    if not cfg["norestore"] and want_cons not in (None, "reset"):
        log("restoring constellation config to NV default")
        try:
            loc.checked("SET_CONSTELLATION_CONTROL(reset)",
                        SET_CONSTELLATION_CONTROL, [(0x01, b"\x01")])
            log("restored: %s" % read_constellations(loc))
        except Void as e:
            log("restore failed: %s" % e)


if __name__ == "__main__":
    if not hasattr(socket, "AF_QIPCRTR"):
        sys.exit("this kernel/python has no AF_QIPCRTR")
    try:
        main()
    except Void as e:
        log("VOID: %s" % e)
        log("VOID: the run proves nothing; fix the step and repeat it")
        sys.exit(2)
