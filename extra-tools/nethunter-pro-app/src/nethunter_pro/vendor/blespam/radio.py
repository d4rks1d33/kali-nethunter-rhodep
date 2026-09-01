"""Radio preparation / restoration helpers.

A raw HCI *user channel* can only be opened while ``bluetoothd`` is out of the
way and the target controller is down.

Internal QCA wcn399x (``hci0``)
    This chip needs a specific dance: unblock rfkill, bring ``hci0`` up so it
    finishes its firmware setup, pause briefly, then take it down.  The
    sequence must stay *quick* - if the chip sits idle after boot it drops to a
    low-power state and answers HCI commands with "command disallowed" (0x12).

External USB adapter (``hci1`` / ``hciN``)
    No firmware dance is needed - just stop ``bluetoothd`` and bring ``hciN``
    down before binding.

In both cases ``bluetoothd`` is masked (the unit has ``Restart=on-failure``,
so it would otherwise restart and re-initialise the controller mid-session) and
killed.  ``restore_radio()`` unmasks it, brings the controller up and starts
the service again.

All ``systemctl`` calls are wrapped in ``timeout`` so a wedged service job can
never hang the app.
"""

from __future__ import annotations

import subprocess
import time


class RadioError(Exception):
    pass


def _sh(cmd: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def _sysctl(*args: str) -> None:
    _sh(f"timeout 15 systemctl {' '.join(args)} 2>/dev/null || true")


def _dev_name(dev_id: int) -> str:
    return f"hci{dev_id}"


def _hci_state(dev_id: int) -> str:
    out = _sh(f"hciconfig {_dev_name(dev_id)}").stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(("UP", "DOWN")):
            return line
    return out.strip()


def _hci_exists(dev_id: int) -> bool:
    return _dev_name(dev_id) in _sh("hciconfig").stdout


def _wait_until_up(dev_id: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "UP RUNNING" in _hci_state(dev_id):
            return
        time.sleep(0.4)
    raise RadioError(
        f"{_dev_name(dev_id)} did not come up (state: {_hci_state(dev_id)!r}). "
        "If the Bluetooth chip is unresponsive, reboot the device and try again."
    )


def _lock_bluetoothd() -> None:
    """Mask the bluetooth unit and kill bluetoothd so nothing can re-take the
    radio mid-session."""
    _sysctl("mask", "bluetooth")
    _sh("pkill -9 bluetoothd 2>/dev/null || true")
    time.sleep(1)


def prepare_radio(dev_id: int = 0) -> None:
    """Get the radio into a state where a raw HCI user channel can be opened."""
    _sh("rfkill unblock bluetooth")

    if not _hci_exists(dev_id):
        raise RadioError(f"no {_dev_name(dev_id)} adapter found")

    # Only the internal QCA chip needs the full firmware-init dance.
    if dev_id == 0:
        _sh("hciconfig hci0 up")
        try:
            _wait_until_up(0)
        except RadioError:
            # Sometimes the chip soft-blocks itself; try once more.
            _sh("rfkill unblock bluetooth")
            _sh("hciconfig hci0 up")
            _wait_until_up(0)
        time.sleep(2)

    _lock_bluetoothd()
    _sh(f"hciconfig {_dev_name(dev_id)} down")
    time.sleep(1)


def restore_radio(dev_id: int = 0) -> None:
    """Give the radio back to the system.  The final bluetoothd start is fired
    in the background so a wedged controller can never block the app close."""
    _sysctl("unmask", "bluetooth")
    _sysctl("stop", "bluetooth")
    _sh(f"hciconfig {_dev_name(dev_id)} up")
    time.sleep(0.5)
    _sh(
        "nohup timeout 20 systemctl start bluetooth "
        "> /dev/null 2>&1 & || true"
    )