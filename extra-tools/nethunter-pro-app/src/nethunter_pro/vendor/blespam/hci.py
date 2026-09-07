"""Raw HCI (Bluetooth controller) access for LE advertising.

This module talks directly to a Bluetooth controller through a raw
``AF_BLUETOOTH`` / ``BTPROTO_HCI`` socket in *user channel* mode, which gives
full control over the advertising payload (something BlueZ/DBus cannot do).

Requirements
------------
* Running as root.
* The ``bluetooth`` service must be stopped and ``hci0`` brought down before
  opening the user channel (the kernel refuses the bind while the device is
  busy).  ``run.sh`` does this for you.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import struct
import time

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
SOCK_RAW = 3

HCI_CHANNEL_USER = 1

HCI_COMMAND_PKT = 0x01
HCI_EVENT_PKT = 0x04

EVT_COMMAND_COMPLETE = 0x0E
EVT_COMMAND_STATUS = 0x0F
# 0x10 is the Hardware Error event -- the chip's way of saying "I have
# lost my mind, please close and reopen me". On the QCA WCN399x this is
# what gets fired when the LE controller queue overflows after too many
# rapid payload swaps. See engine.py:_recover_heavy for what to do when
# we see one of these.
EVT_HARDWARE_ERROR = 0x10
EVT_NUM_COMPLETED_PACKETS = 0x13
EVT_LE_META_EVENT = 0x3E

# HCI status codes we care about. See Bluetooth Core spec Vol 4, Part E
# §1.3 for the full table.
HCI_STATUS_OK = 0x00
HCI_STATUS_COMMAND_DISALLOWED = 0x0C   # radio busy, LE state wrong, ...
HCI_STATUS_INVALID_PARAMETERS = 0x12   # bad length/opcode, or QCA
                                       # "redundant reset" flavour

# OGF (opcode group field) -- see Bluetooth Core spec Vol 4 Part E §5.4.1.
# The OGF sits in the top 6 bits of the 16-bit opcode. The upstream port
# had OGF_LINK_CTL = 0x01<<10 and CMD_RESET = 0x0003 | OGF_LINK_CTL,
# claiming the result was 0x0C03 -- but 0x01<<10 is 0x0400 and 0x0403 is
# *Periodic Inquiry Mode*, not Reset. Under HCI_CHANNEL_USER on this
# QCA chip that command comes back with Invalid HCI Command Parameters
# (0x12), which is exactly the '0x12 on reset' behaviour our reset()
# was accidentally excusing. The correct HCI Reset lives in the Host
# Controller & Baseband group (OGF=0x03), opcode 0x0C03.
OGF_LINK_CTL = 0x01 << 10
OGF_HOST_CTL = 0x03 << 10
OGF_LE_CTL = 0x08 << 10

# Common opcodes -- Legacy Advertising (BT 4.0+)
CMD_RESET = 0x0003 | OGF_HOST_CTL                        # 0x0C03
CMD_LE_SET_ADVERTISING_PARAMETERS = 0x0006 | OGF_LE_CTL  # 0x2006
CMD_LE_SET_ADVERTISING_DATA = 0x0008 | OGF_LE_CTL        # 0x2008
CMD_LE_SET_SCAN_RESPONSE_DATA = 0x0009 | OGF_LE_CTL      # 0x2009
CMD_LE_SET_ADVERTISE_ENABLE = 0x000A | OGF_LE_CTL        # 0x200A
CMD_LE_READ_LOCAL_SUPPORTED_FEATURES = 0x0003 | OGF_LE_CTL   # 0x2003
CMD_LE_SET_RANDOM_ADDRESS = 0x0005 | OGF_LE_CTL          # 0x2005

# Extended Advertising (BT 5.0+). The QCA WCN6750 on SM6375 only
# implements the Extended path -- Legacy commands come back with
# Status: Success but the radio is silent on-air. Discovered by
# comparing btmon captures against `hciconfig hciX leadv` which
# also returns "status 12" (Invalid HCI Command Parameters) on
# this chip. Any host must go through the Extended API here.
CMD_LE_SET_EXT_ADV_SET_RANDOM_ADDR = 0x0035 | OGF_LE_CTL # 0x2035
CMD_LE_SET_EXT_ADV_PARAMETERS = 0x0036 | OGF_LE_CTL      # 0x2036
CMD_LE_SET_EXT_ADV_DATA = 0x0037 | OGF_LE_CTL            # 0x2037
CMD_LE_SET_EXT_SCAN_RSP_DATA = 0x0038 | OGF_LE_CTL       # 0x2038
CMD_LE_SET_EXT_ADV_ENABLE = 0x0039 | OGF_LE_CTL          # 0x2039
CMD_LE_CLEAR_ADV_SETS = 0x003D | OGF_LE_CTL              # 0x203D

# Legacy advertising types (Set_Advertising_Parameters)
ADV_TYPE_IND = 0x00        # connectable + scannable
ADV_TYPE_DIRECT = 0x01
ADV_TYPE_SCAN_IND = 0x02   # scannable, not connectable
ADV_TYPE_NONCONN_IND = 0x03  # not scannable, not connectable

# Extended advertising event properties (Set_Extended_Advertising_Parameters).
# These map the classic ADV_TYPE_* values into the Extended world by
# combining the Connectable/Scannable/Directed/Legacy_PDUs bits per
# Bluetooth Core spec Vol 4 Part E §7.8.53. "Legacy" is essential: it
# tells the controller to emit the same-shape PDUs the old legacy scan
# used to look for (ADV_IND / ADV_SCAN_IND / ADV_NONCONN_IND), so the
# advertisement is visible to every BLE scanner in existence, not just
# the ones that speak Extended.
EXT_ADV_PROPS_LEGACY = 0x0010
EXT_ADV_PROPS_CONNECTABLE = 0x0001
EXT_ADV_PROPS_SCANNABLE = 0x0002
EXT_ADV_PROPS_DIRECTED = 0x0004
# Ready-made shapes that match the classic advertising types.
EXT_ADV_PROPS_IND = (EXT_ADV_PROPS_LEGACY
                     | EXT_ADV_PROPS_CONNECTABLE
                     | EXT_ADV_PROPS_SCANNABLE)          # 0x0013
EXT_ADV_PROPS_SCAN_IND = EXT_ADV_PROPS_LEGACY | EXT_ADV_PROPS_SCANNABLE  # 0x0012
EXT_ADV_PROPS_NONCONN_IND = EXT_ADV_PROPS_LEGACY         # 0x0010

# Data operation for Set_Extended_Advertising_Data / Scan_Response_Data
EXT_ADV_DATA_OP_COMPLETE = 0x03

# Own_Address_Type
OWN_ADDR_PUBLIC = 0x00
OWN_ADDR_RANDOM = 0x01

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _pack_command(opcode: int, params: bytes) -> bytes:
    return bytes([HCI_COMMAND_PKT]) + struct.pack("<HB", opcode, len(params)) + params


class HciError(Exception):
    """Raised when a HCI command fails or the controller is unreachable."""


class HciHardwareError(HciError):
    """The controller emitted a Hardware Error event (HCI event 0x10).

    On the QCA WCN399x this is the signal that the firmware's internal
    LE controller state is corrupted -- typically because the host has
    been swapping the advertising payload faster than the firmware can
    reconcile with the radio's TX schedule. The only in-band recovery
    is to close the HCI user channel and reopen it, which makes the
    kernel driver run `qca_regulator_disable` / `qca_regulator_enable`
    and re-download the firmware -- see engine.py:_recover_heavy.

    The ``code`` attribute holds the vendor-specific Hardware_Code byte.
    On the QCA firmware the codes we've seen are 0x0D (LE controller
    queue overflow) and 0x0F (UART sync lost), both meaning the same
    thing from the caller's point of view.
    """
    def __init__(self, code: int):
        super().__init__(
            "HCI Hardware Error event 0x%02X -- chip needs restart" % code)
        self.code = code


class HciDevice:
    """Minimal raw HCI user-channel interface for LE advertising."""

    def __init__(self, dev_id: int = 0):
        self.dev_id = dev_id
        self._fd = None

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self, retries: int = 12, delay: float = 0.5) -> None:
        if self._fd is not None:
            return
        fd = _libc.socket(AF_BLUETOOTH, SOCK_RAW, BTPROTO_HCI)
        if fd < 0:
            raise HciError(f"cannot open HCI socket: {os.strerror(ctypes.get_errno())}")
        sockaddr = struct.pack("HHH", AF_BLUETOOTH, self.dev_id, HCI_CHANNEL_USER).ljust(8, b"\x00")
        last = None
        for _ in range(retries):
            ret = _libc.bind(fd, sockaddr, 8)
            if ret == 0:
                self._fd = fd
                return
            last = os.strerror(ctypes.get_errno())
            # EBUSY: the driver is still releasing the radio - try again.
            time.sleep(delay)
        _libc.close(fd)
        raise HciError(
            f"cannot bind HCI user channel on hci{self.dev_id}: {last} "
            "(is bluetooth running / hci0 still up?)"
        )

    def close(self) -> None:
        if self._fd is not None:
            _libc.close(self._fd)
            self._fd = None

    # -- low level ----------------------------------------------------------

    def _send(self, data: bytes) -> None:
        buf = ctypes.create_string_buffer(data, len(data))
        sent = _libc.send(self._fd, buf, len(data), 0)
        if sent < 0:
            raise HciError(f"HCI send failed: {os.strerror(ctypes.get_errno())}")

    def _recv(self, timeout: float) -> bytes:
        readable, _, _ = select.select([self._fd], [], [], timeout)
        if not readable:
            raise HciError("timeout waiting for HCI event (controller unresponsive?)")
        buf = ctypes.create_string_buffer(512)
        n = _libc.recv(self._fd, buf, 512, 0)
        if n < 0:
            raise HciError(f"HCI recv failed: {os.strerror(ctypes.get_errno())}")
        return buf.raw[:n]

    def command(self, opcode: int, params: bytes = b"", timeout: float = 2.0) -> int:
        """Send a HCI command and wait for its Command Complete event.

        Returns the status byte of the command (0 == success).

        Along the way we watch for other events on the socket:

          * Hardware Error (0x10) always raises :class:`HciHardwareError`
            no matter which command we were waiting for. That event is
            unconditional -- the chip is dead, keep going and the next
            command times out too.
          * Num_Completed_Packets (0x13) is drained silently. It is
            informational (broadcast advertising doesn't really need
            back-pressure) but leaving it in the kernel recv queue means
            our next `_recv` picks it up instead of the event we want.
          * LE Meta events and any other unrelated notifications are
            drained silently for the same reason.
        """
        if self._fd is None:
            raise HciError("HCI device is not open")
        self._send(_pack_command(opcode, params))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HciError(f"HCI command 0x{opcode:04X} timed out")
            data = self._recv(min(remaining, 2.0))
            if not data or data[0] != HCI_EVENT_PKT:
                continue
            evt = data[1]
            if evt == EVT_HARDWARE_ERROR:
                # Hardware_Error_Code is the byte right after the event
                # length. Some controllers omit the byte and just fire
                # the event as a two-byte packet -- default to 0x00 in
                # that case.
                code = data[3] if len(data) >= 4 else 0
                raise HciHardwareError(code)
            if evt == EVT_COMMAND_COMPLETE and len(data) >= 6:
                # data[2] = length, data[3] = num packets, data[4:6] = opcode LE
                (cmd_op,) = struct.unpack("<H", data[4:6])
                if cmd_op == opcode:
                    status = data[6] if len(data) > 6 else 0
                    return status
            elif evt == EVT_COMMAND_STATUS and len(data) >= 6:
                status = data[3]
                (cmd_op,) = struct.unpack("<H", data[4:6])
                if cmd_op == opcode:
                    return status
            # else: unrelated event, keep draining

    def _command_ok(self, opcode: int, params: bytes = b"") -> None:
        status = self.command(opcode, params)
        if status != 0:
            raise HciError(f"HCI command 0x{opcode:04X} failed with status 0x{status:02X}")

    def command_with_data(
        self, opcode: int, params: bytes = b"", timeout: float = 2.0
    ) -> bytes:
        """Same as :meth:`command` but returns the trailing payload of the
        Command Complete event instead of just the status.

        Some Extended Advertising commands return useful state -- e.g.
        ``LE Set Extended Advertising Parameters`` (0x2036) returns the
        actual TX power the controller decided to use. Callers can use
        this to log what the radio really picked when they asked for
        "no preference" (0x7F). Raises :class:`HciError` on non-zero
        status, same as :meth:`_command_ok`.
        """
        if self._fd is None:
            raise HciError("HCI device is not open")
        self._send(_pack_command(opcode, params))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HciError(f"HCI command 0x{opcode:04X} timed out")
            data = self._recv(min(remaining, 2.0))
            if not data or data[0] != HCI_EVENT_PKT:
                continue
            evt = data[1]
            if evt == EVT_HARDWARE_ERROR:
                code = data[3] if len(data) >= 4 else 0
                raise HciHardwareError(code)
            if evt == EVT_COMMAND_COMPLETE and len(data) >= 6:
                (cmd_op,) = struct.unpack("<H", data[4:6])
                if cmd_op == opcode:
                    status = data[6] if len(data) > 6 else 0
                    if status != 0:
                        raise HciError(
                            f"HCI command 0x{opcode:04X} failed with "
                            f"status 0x{status:02X}")
                    return bytes(data[7:])
            # ignore unrelated events

    # -- controller commands -------------------------------------------------

    def reset(self, retries: int = 5, delay: float = 0.3) -> None:
        """Reset the controller.

        Right after the radio is released the controller can be mid-
        transition and return COMMAND_DISALLOWED (0x0C), so we retry.
        We used to also silently accept 0x12 "Invalid HCI Command
        Parameters" because the upstream port had OGF_LINK_CTL where
        OGF_HOST_CTL should have been, and the resulting opcode 0x0403
        (Periodic Inquiry Mode) predictably came back with 0x12. That
        is fixed now -- opcode is 0x0C03 and a real 0x12 means a real
        problem.
        """
        last = None
        for _ in range(retries):
            try:
                status = self.command(CMD_RESET)
            except HciError as exc:  # noqa: PERF203
                last = exc
                time.sleep(delay)
                continue
            if status == 0:
                return
            last = HciError(
                "HCI Reset returned status 0x%02X" % status)
            time.sleep(delay)
        raise last  # type: ignore[misc]

    def set_advertising_parameters(
        self,
        interval_min: int = 0x0020,   # 20 ms (Android INTERVAL_MIN)
        interval_max: int = 0x0020,
        adv_type: int = ADV_TYPE_NONCONN_IND,
        own_addr_type: int = 0,
        peer_addr_type: int = 0,
        peer_addr: bytes = b"\x00" * 6,
        channel_map: int = 7,
        filter_policy: int = 0,
    ) -> None:
        params = struct.pack(
            "<HHBBB6sBB",
            interval_min & 0xFFFF,
            interval_max & 0xFFFF,
            adv_type & 0xFF,
            own_addr_type & 0xFF,
            peer_addr_type & 0xFF,
            peer_addr[:6].ljust(6, b"\x00"),
            channel_map & 0xFF,
            filter_policy & 0xFF,
        )
        self._command_ok(CMD_LE_SET_ADVERTISING_PARAMETERS, params)

    def set_advertising_data(self, data: bytes) -> None:
        # HCI spec: length byte followed by a *fixed* 31-octet data
        # field. Some chips accept a shorter tail, but the QCA wcn399x
        # returns 0x12 "Invalid HCI Command Parameters" unless the
        # buffer is exactly 32 bytes. Pad with zeros to match the spec.
        data = data[:31]
        payload = struct.pack("<B", len(data)) + data.ljust(31, b"\x00")
        self._command_ok(CMD_LE_SET_ADVERTISING_DATA, payload)

    def set_scan_response_data(self, data: bytes) -> None:
        # Same shape as set_advertising_data: length + fixed 31-octet
        # field. The scan response is optional (data may be empty) but
        # the length + padding are not.
        data = data[:31]
        payload = struct.pack("<B", len(data)) + data.ljust(31, b"\x00")
        self._command_ok(CMD_LE_SET_SCAN_RESPONSE_DATA, payload)

    def set_advertise_enable(self, enabled: bool) -> None:
        self._command_ok(CMD_LE_SET_ADVERTISE_ENABLE, struct.pack("<B", 1 if enabled else 0))

    # -- Extended Advertising (BT 5.0+) --------------------------------------
    # These are the *only* advertising commands the QCA WCN6750 firmware
    # on the Moto G82 5G actually honours. See the module docstring at the
    # top of engine.py for the discovery story.

    def clear_advertising_sets(self) -> None:
        """Delete every advertising set the controller currently holds.

        Cheap belt-and-suspenders before configuring a new set: if a
        previous session (bluetoothd, an earlier crash, another opencode
        run) left a set behind, ``LE Set Extended Advertising Parameters``
        will happily overwrite handle 0 but a stale enable on handle >0
        can keep transmitting garbage. Clearing is safe when there is
        nothing to clear (returns 0x00).
        """
        self._command_ok(CMD_LE_CLEAR_ADV_SETS)

    def set_advertising_set_random_address(
        self, handle: int, addr: bytes
    ) -> None:
        """Bind a random address to an advertising set.

        Required whenever the set uses ``OWN_ADDR_RANDOM``. Address is
        transmitted in little-endian byte order on the wire (LSB first),
        so we accept ``addr`` in big-endian human order and reverse it
        here to keep call sites readable ("C0:11:22:33:44:55" -> b"\\xc0\\x11...").
        The Extended-set variant takes a handle (0..EA), unlike the
        classic 0x2005 ``LE Set Random Address`` which is per-controller.
        """
        if len(addr) != 6:
            raise ValueError("random address must be 6 bytes, got %d" % len(addr))
        params = struct.pack("<B", handle & 0xFF) + addr[::-1]
        self._command_ok(CMD_LE_SET_EXT_ADV_SET_RANDOM_ADDR, params)

    def set_extended_advertising_parameters(
        self,
        handle: int = 0,
        adv_event_props: int = EXT_ADV_PROPS_SCAN_IND,
        prim_interval_min: int = 0x000020,   # 20 ms (0.625 ms units)
        prim_interval_max: int = 0x000020,
        prim_channel_map: int = 0x07,        # channels 37, 38, 39
        own_addr_type: int = OWN_ADDR_RANDOM,
        peer_addr_type: int = 0,
        peer_addr: bytes = b"\x00" * 6,
        adv_filter_policy: int = 0,
        adv_tx_power: int = 0x7F,            # 0x7F = host has no preference
        prim_adv_phy: int = 0x01,            # 1M PHY
        sec_adv_max_skip: int = 0,
        sec_adv_phy: int = 0x01,             # 1M PHY
        adv_sid: int = 0,
        scan_req_notify_enable: int = 0,
    ) -> int:
        """Configure an advertising set. Returns the selected TX power (dBm).

        Layout is Bluetooth Core spec Vol 4 Part E §7.8.53. All the
        multi-byte fields are little-endian on the wire. The controller
        returns the actual selected TX power in the command-complete
        event -- that's the only useful piece of state and we hand it
        back so the caller can log it.

        Primary advertising interval is in 0.625 ms units and is a
        24-bit field packed as three little-endian bytes; we compute it
        from the fixed-size ``<I`` int and slice the top byte off.
        """
        if len(peer_addr) != 6:
            raise ValueError("peer_addr must be 6 bytes")
        params = struct.pack(
            "<BH",
            handle & 0xFF,
            adv_event_props & 0xFFFF,
        )
        # Primary advertising interval min/max are 3-byte little-endian.
        params += (prim_interval_min & 0xFFFFFF).to_bytes(3, "little")
        params += (prim_interval_max & 0xFFFFFF).to_bytes(3, "little")
        params += struct.pack(
            "<BBB6sBbBBBBB",
            prim_channel_map & 0xFF,
            own_addr_type & 0xFF,
            peer_addr_type & 0xFF,
            peer_addr[:6].ljust(6, b"\x00"),
            adv_filter_policy & 0xFF,
            adv_tx_power & 0xFF if adv_tx_power >= 0 else adv_tx_power,
            prim_adv_phy & 0xFF,
            sec_adv_max_skip & 0xFF,
            sec_adv_phy & 0xFF,
            adv_sid & 0xFF,
            scan_req_notify_enable & 0xFF,
        )
        # We need the payload back to read the returned tx power byte.
        payload = self.command_with_data(CMD_LE_SET_EXT_ADV_PARAMETERS, params)
        return payload[0] if payload else 0

    def set_extended_advertising_data(
        self,
        data: bytes,
        handle: int = 0,
        operation: int = EXT_ADV_DATA_OP_COMPLETE,
        fragment_pref: int = 0x01,
    ) -> None:
        """Set the advertising payload for a set. Data <= 31 bytes for
        legacy PDUs, up to 251 bytes for extended-only PDUs. We stay
        within 31 because we emit legacy-shaped PDUs for compatibility
        with every BLE scanner.
        """
        data = data[:31]
        params = struct.pack("<BBBB",
                             handle & 0xFF,
                             operation & 0xFF,
                             fragment_pref & 0xFF,
                             len(data)) + data
        self._command_ok(CMD_LE_SET_EXT_ADV_DATA, params)

    def set_extended_scan_response_data(
        self,
        data: bytes,
        handle: int = 0,
        operation: int = EXT_ADV_DATA_OP_COMPLETE,
        fragment_pref: int = 0x01,
    ) -> None:
        """Set the scan response payload. Empty (``b''``) is allowed and
        common when the advertising type is ADV_NONCONN_IND -- but the
        controller still wants the length byte, so we pass len=0.
        """
        data = data[:31]
        params = struct.pack("<BBBB",
                             handle & 0xFF,
                             operation & 0xFF,
                             fragment_pref & 0xFF,
                             len(data)) + data
        self._command_ok(CMD_LE_SET_EXT_SCAN_RSP_DATA, params)

    def set_extended_advertise_enable(
        self,
        enabled: bool,
        handle: int = 0,
        duration_10ms: int = 0,   # 0 = advertise forever
        max_events: int = 0,       # 0 = no limit
    ) -> None:
        """Start / stop advertising for a single set.

        Multi-set enable/disable is possible per spec but we only ever
        use handle 0, so the wire layout is fixed: enable(1) + numsets(1=1)
        + handle(1) + duration(2 LE) + max_events(1).
        Disable (``enabled=False``) with num_sets=0 stops every set.
        """
        if enabled:
            params = struct.pack("<BBBHB",
                                 0x01,
                                 0x01,           # num_sets
                                 handle & 0xFF,
                                 duration_10ms & 0xFFFF,
                                 max_events & 0xFF)
        else:
            # A zero-length "disable all" body is only legal when
            # enable=0 AND num_sets=0. Everything else must specify sets.
            params = struct.pack("<BB", 0x00, 0x00)
        self._command_ok(CMD_LE_SET_EXT_ADV_ENABLE, params)
