#!/usr/bin/env python3
"""
rhodep-gnss-test - drive the QMI location service from a single QMI client.

*** WARNING: ON motorola-rhodep THIS RESETS THE SoC. ***

That is the finding, not a bug in this script. Starting a GNSS session from a
client that has registered for indications watchdog-resets the SoC in under a
second, reproducibly (3/3), with ipa.ko not loaded. The console stops mid-line
with no panic and no oops - the same signature as the IPA/LTE reset. Run it
knowing the phone will reboot. See docs/interconnect-sm6375-wip/GNSS-SM6375.md.

Why it has to exist: qmicli cannot do what this does. The LOC session belongs
to the QMI client that created it, qmicli refuses more than one --loc-* action
per invocation ("too many LOC actions requested"), and two processes cannot
share one CID through qmi-proxy (both time out). So "register events, then
start a session, then listen" - the only order that makes the engine actually
run - is unreachable from the shell. Doing it from one client is what turned
"GPS emits nothing" into "GPS resets the SoC".

Every step is written to /dev/kmsg as well as stdout, because on a watchdog
reset stdout dies with the ssh session while the kernel console survives in
/var/lib/systemd/pstore/. The last line there tells you which step died.

Usage:  sudo ./rhodep-gnss-test.py [seconds] [mask-groups] [opmode=MODE]

  seconds       how long to listen after the session starts (default 120)
  mask-groups   comma separated, from: min,engine,inject,wifi (default: all)
                'min' is NMEA + position report + satellite info, and is
                enough on its own to trigger the reset.
  opmode=MODE   default|msb|msa|standalone|cellid|wwan, set from this client

*** opmode=cellid is the one configuration that SURVIVES. *** It runs a real
session -- position reports and NMEA -- without ever bringing up the GNSS
measurement engine, which is what the reset actually needs. Run it back to back
against opmode=standalone on one boot and the mode is the only variable.

Requires gir1.2-qmi-1.0 (not installed by default; it pulls in no held
packages). The modem has to be out of low-power first:

  qmicli -d qrtr://0 --dms-set-operating-mode=online
"""

import sys
import gi

gi.require_version("Qmi", "1.0")
from gi.repository import Qmi, Gio, GLib  # noqa: E402

DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 120
MASK_GROUPS = (sys.argv[2].split(",") if len(sys.argv) > 2
               else ["min", "engine", "inject", "wifi"])
# Extra flags, as bare words anywhere in argv:
#   bare      QMI_LOC_START with the session id only, no optional TLVs
#   nostart   register events and never start a session
BARE_START = "bare" in sys.argv
NO_START = "nostart" in sys.argv
NO_REGISTER = "noreg" in sys.argv
STOP_NOW = "stopnow" in sys.argv
#   opmode=NAME  set the operation mode from THIS client before starting.
#                One of: default msb msa standalone cellid wwan.
#                It has to happen here rather than through
#                `qmicli --loc-set-operation-mode`, because qmicli releases its
#                LOC client on exit and the setting goes with it: qmicli reports
#                "Successfully set operation mode" and a following
#                --loc-get-operation-mode still answers 'standalone'.
#                'cellid' is the interesting one: the session runs to completion
#                but the GNSS measurement engine is never brought up, which
#                separates "the engine" from "the session state machine". It is
#                what turned this from a trigger into a bisection.
#                Always check the "operation mode set to ..." line in the
#                output: a failed set falls through to standalone, and a
#                survival that came from an unapplied mode means nothing.
OPMODE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("opmode=")),
              None)
SESSION_ID = 11

loop = GLib.MainLoop()
state = {"device": None, "client": None, "count": 0, "started": False, "secs": 0}


def log(msg):
    """Print, and also write into the kernel ring buffer.

    The point of /dev/kmsg here is ramoops: if the SoC watchdog-resets, stdout
    is lost with the ssh session but the kernel console survives in
    /var/lib/systemd/pstore/, so the last line printed says which step died.
    """
    print(msg, flush=True)
    try:
        with open("/dev/kmsg", "w") as k:
            k.write("rhodep-gnss: %s\n" % msg)
    except Exception:
        pass


def bail(msg):
    log("FAIL: %s" % msg)
    loop.quit()


# ---------------------------------------------------------------- indications

def show_nmea(_client, output):
    state["count"] += 1
    try:
        log("NMEA   %s" % output.get_nmea_string().strip())
    except Exception as e:
        log("NMEA   (unreadable: %s)" % e)


def show_position(_client, output):
    state["count"] += 1
    bits = []
    for name, getter in (("lat", "get_latitude"), ("lon", "get_longitude"),
                         ("alt", "get_altitude_from_ellipsoid")):
        try:
            bits.append("%s=%s" % (name, getattr(output, getter)()))
        except Exception:
            pass
    try:
        bits.append("session=%s" % (output.get_session_status(),))
    except Exception:
        pass
    log("POS    %s" % (" ".join(bits) if bits else "(no TLVs)"))


def show_sv(_client, output):
    state["count"] += 1
    try:
        svs = output.get_list()
        used = sum(1 for s in svs if getattr(s, "sv_status", None) == 3)
        log("SV     %d satellites reported (%d used)" % (len(svs), used))
        for s in svs[:6]:
            log("       sv %s sys=%s snr=%s elev=%s"
                % (getattr(s, "sv_prn", "?"), getattr(s, "system", "?"),
                   getattr(s, "signal_to_noise_ratio", "?"),
                   getattr(s, "elevation", "?")))
    except Exception as e:
        log("SV     (unreadable: %s)" % e)


def show_engine(_client, output):
    state["count"] += 1
    try:
        log("ENGINE state=%s" % (output.get_engine_state(),))
    except Exception as e:
        log("ENGINE (unreadable: %s)" % e)


def show_generic(name):
    def cb(_client, _output):
        state["count"] += 1
        log("EVENT  %s" % name)
    return cb


SIGNALS = [
    ("nmea", show_nmea),
    ("position-report", show_position),
    ("gnss-sv-info", show_sv),
    ("engine-state", show_engine),
    ("fix-recurrence-type", show_generic("fix-recurrence-type")),
    ("inject-time-request", show_generic("inject-time-request")),
    ("inject-position-request", show_generic("inject-position-request")),
    ("inject-predicted-orbits-request", show_generic("inject-predicted-orbits")),
    ("wifi-request", show_generic("wifi-request (LOWI)")),
]


# ------------------------------------------------------------------ call chain

def on_start(client, res, _u):
    try:
        out = client.start_finish(res)
        out.get_result()
    except Exception as e:
        return bail("start: %s" % e)
    state["started"] = True
    if STOP_NOW:
        log("stopnow: stopping the session immediately")
        i = Qmi.MessageLocStopInput()
        i.set_session_id(SESSION_ID)
        client.stop(i, 5, None,
                    lambda c, r, u: (log("stopped; idling %d s" % DURATION),
                                     GLib.timeout_add_seconds(DURATION, finish)),
                    None)
        return
    log("session %d started; listening %d s" % (SESSION_ID, DURATION))
    log("-" * 60)

    # Heartbeat into the kernel log. If the SoC watchdog-resets, the last
    # "alive N" that survives in ramoops is how long the session lasted.
    def beat():
        state["secs"] += 1
        log("alive %d ms (indications so far: %d)"
            % (state["secs"] * 100, state["count"]))
        return state["secs"] < DURATION * 10
    GLib.timeout_add(100, beat)
    GLib.timeout_add_seconds(DURATION, finish)


def do_start(client):
    """Build and send QMI_LOC_START."""
    i = Qmi.MessageLocStartInput()
    i.set_session_id(SESSION_ID)
    if not BARE_START:
        for setter, value in (("set_fix_recurrence_type", 1),
                              ("set_intermediate_report_state", 1),
                              ("set_minimum_interval_between_position_reports",
                               1000)):
            try:
                getattr(i, setter)(value)
            except Exception:
                pass
    else:
        log("bare start: session id only, no optional TLVs")
    client.start(i, 10, None, on_start, None)


def on_opmode(client, res, _u):
    try:
        out = client.set_operation_mode_finish(res)
        out.get_result()
        log("operation mode set to %s" % OPMODE)
    except Exception as e:
        log("set_operation_mode(%s) failed: %s -- starting anyway" % (OPMODE, e))
    do_start(client)


def on_register(client, res, _u):
    if res is not None:
        try:
            out = client.register_events_finish(res)
            out.get_result()
        except Exception as e:
            return bail("register_events: %s" % e)
        log("events registered")

    if NO_START:
        log("nostart: not starting a session, just listening %d s" % DURATION)
        log("-" * 60)
        GLib.timeout_add_seconds(DURATION, finish)
        return

    if OPMODE:
        try:
            mode = getattr(Qmi.LocOperationMode, OPMODE.upper())
        except AttributeError:
            log("unknown opmode '%s'; known: %s" % (
                OPMODE, [n for n in dir(Qmi.LocOperationMode)
                         if n.isupper()]))
            return bail("bad opmode")
        i = Qmi.MessageLocSetOperationModeInput()
        i.set_operation_mode(mode)
        log("setting operation mode %s from this client" % OPMODE)
        client.set_operation_mode(i, 10, None, on_opmode, None)
        return

    do_start(client)


def on_client(device, res, _u):
    try:
        client = device.allocate_client_finish(res)
    except Exception as e:
        return bail("allocate_client: %s" % e)
    state["client"] = client
    log("LOC client allocated (cid %s)" % client.get_cid())

    for name, handler in SIGNALS:
        try:
            client.connect(name, handler)
        except Exception:
            log("  (no signal '%s' in this libqmi)" % name)

    f = Qmi.LocEventRegistrationFlag
    groups = {
        "min": f.POSITION_REPORT | f.GNSS_SATELLITE_INFO | f.NMEA,
        "engine": f.ENGINE_STATE | f.FIX_SESSION_STATE,
        "inject": (f.INJECT_TIME_REQUEST | f.INJECT_POSITION_REQUEST
                   | f.INJECT_PREDICTED_ORBITS_REQUEST),
        "wifi": f.WIFI_REQUEST,
        # Individually, for bisecting which indication is fatal.
        "nmea": f.NMEA,
        "pos": f.POSITION_REPORT,
        "sv": f.GNSS_SATELLITE_INFO,
        "none": 0,
    }
    mask = 0
    for name in MASK_GROUPS:
        mask |= groups[name]
    log("event mask: %s (0x%x)" % (",".join(MASK_GROUPS), mask))
    if NO_REGISTER:
        log("noreg: skipping register_events entirely")
        return on_register(client, None, None)

    i = Qmi.MessageLocRegisterEventsInput()
    i.set_event_registration_mask(mask)
    client.register_events(i, 10, None, on_register, None)


def on_open(device, res, _u):
    try:
        device.open_finish(res)
    except Exception as e:
        return bail("open: %s" % e)
    log("device open")
    device.allocate_client(Qmi.Service.LOC, Qmi.CID_NONE, 10, None, on_client, None)


def on_device(_src, res, _u):
    try:
        device = Qmi.Device.new_finish(res)
    except Exception as e:
        return bail("Device.new: %s" % e)
    state["device"] = device
    device.open(Qmi.DeviceOpenFlags.PROXY, 15, None, on_open, None)


def finish():
    log("-" * 60)
    log("indications received: %d" % state["count"])
    if state["count"] == 0:
        log("VERDICT: the location engine produced nothing. A running GNSS")
        log("         engine emits NMEA and satellite lists even with no fix,")
        log("         so this is not an 'indoors' result.")
    if state["client"] and state["started"]:
        i = Qmi.MessageLocStopInput()
        i.set_session_id(SESSION_ID)
        state["client"].stop(i, 5, None, lambda c, r, u: loop.quit(), None)
    else:
        loop.quit()
    return False


Qmi.Device.new(Gio.File.new_for_commandline_arg("qrtr://0"), None, on_device, None)
GLib.timeout_add_seconds(DURATION + 60, lambda: (bail("overall timeout"), False)[1])
loop.run()
