"""Advertising engine: drives the radio in a background thread."""

from __future__ import annotations

import random
import threading

from .hci import (
    ADV_TYPE_SCAN_IND,
    HciDevice,
    HciError,
)

# TX power bytes embedded in the TX Power AD (dBm-ish).  Only used for
# payloads that carry a TX power level (Fast Pair).
TX_POWER_BYTES = {
    "Ultra Low": 0x00,
    "Low": 0x02,
    "Medium": 0x04,
    "High": 0x06,
}

DEFAULT_TX_POWER = "High"

MODE_SEQUENTIAL = "Sequential"
MODE_RANDOM = "Random"


def interval_to_hci_units(ms: int) -> int:
    """Milliseconds -> HCI advertising interval units (0.625 ms each)."""
    units = round(ms / 0.625)
    return max(0x20, min(units, 0x4000))


class SpamEngine:
    """Cycles checked :class:`blespam.payloads.Packet` objects on the radio.

    ``on_state`` / ``on_packet`` are called from the worker thread; the GUI is
    responsible for marshalling them to the main thread (``GLib.idle_add``).
    """

    def __init__(self, on_state=None, on_error=None, on_packet=None):
        self.on_state = on_state or (lambda s: None)
        self.on_error = on_error or (lambda e: None)
        self.on_packet = on_packet or (lambda title, adv: None)
        self._stop = threading.Event()
        self._thread = None
        self._hci = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        packets,
        interval_ms=20,
        mode=MODE_SEQUENTIAL,
        tx_power=DEFAULT_TX_POWER,
        dev_id=0,
    ) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(list(packets), interval_ms, mode, tx_power, dev_id),
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    # -- worker ---------------------------------------------------------------

    def _run(self, packets, interval_ms, mode, tx_power, dev_id) -> None:
        hci = None
        tx_byte = TX_POWER_BYTES.get(tx_power, 0x06)
        try:
            hci = HciDevice(dev_id)
            hci.open()
            self._hci = hci
            hci.reset(retries=5, delay=0.5)
            # If a previous session (or another tool like BlueDucky) left
            # the chip with LE advertising still enabled, the next
            # `set_advertising_parameters` returns 0x12 "Command Disallowed"
            # -- the spec forbids changing parameters mid-advertise. So we
            # tell the chip to stop advertising first, ignoring any error
            # (0x0C "Command Disallowed" means it was already off, which
            # is the state we want anyway). HCI Reset above is supposed to
            # clear this too, but on the QCA wcn399x it does not always
            # clear the LE controller state -- issuing an explicit
            # Set_Advertise_Enable(false) after the reset is the reliable
            # form.
            try:
                hci.set_advertise_enable(False)
            except HciError:
                pass
            hci.set_advertising_parameters(
                interval_min=interval_to_hci_units(interval_ms),
                interval_max=interval_to_hci_units(interval_ms),
                adv_type=ADV_TYPE_SCAN_IND,
            )
            self.on_state("advertising")
            hci.set_advertise_enable(True)

            idx = 0
            while not self._stop.is_set():
                if not packets:
                    break
                if mode == MODE_RANDOM:
                    packet = random.choice(packets)
                else:
                    packet = packets[idx % len(packets)]
                    idx += 1
                adv, scan = packet.render(tx_byte)
                hci.set_advertising_data(adv)
                if scan is not None:
                    hci.set_scan_response_data(scan)
                else:
                    hci.set_scan_response_data(b"")
                self.on_packet(packet.title, adv)
            self.on_state("stopping")
            hci.set_advertise_enable(False)
        except HciError as exc:
            self.on_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.on_error(f"unexpected error: {exc}")
        finally:
            if hci is not None:
                try:
                    hci.set_advertise_enable(False)
                except Exception:
                    pass
                hci.close()
                self._hci = None
            self.on_state("stopped")
