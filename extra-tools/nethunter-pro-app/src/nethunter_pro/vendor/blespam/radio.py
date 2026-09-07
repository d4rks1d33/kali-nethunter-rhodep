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


def _sysctl(*args: str) -> subprocess.CompletedProcess:
    return _sh(f"timeout 15 systemctl {' '.join(args)}")


def _bluetoothd_running() -> bool:
    return _sh("pgrep -x bluetoothd").returncode == 0


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
    """Take bluetoothd out of the picture so nothing can re-open hci0 mid-session.

    On systemd systems bluetoothd can come back via three paths:
      1. bluetooth.service (Restart=on-failure)
      2. dbus-org.bluez.service (D-Bus activation)
      3. bluez.service (some distros ship both names)
    Mask all three and stop them explicitly, then kill any lingering process.
    Verify with pgrep before returning -- if it's still alive, systemd is
    reviving it and we need to know.
    """
    # Stop first (before mask) so a pending job doesn't fight us
    for unit in ("bluetooth", "bluetooth.socket",
                 "dbus-org.bluez", "bluez"):
        _sysctl("stop", unit)
    for unit in ("bluetooth", "bluetooth.socket",
                 "dbus-org.bluez", "bluez"):
        _sysctl("mask", unit)
    _sh("pkill -9 bluetoothd 2>/dev/null || true")
    # Give systemd a moment to notice, then verify.
    for _ in range(20):   # up to 4 s
        time.sleep(0.2)
        if not _bluetoothd_running():
            return
    raise RadioError(
        "bluetoothd will not stay down (D-Bus activation is reviving it). "
        "Try: systemctl mask dbus-org.bluez.service && systemctl daemon-reload"
    )


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
    """Give the radio back to the system.

    Unmask the unit, bring the controller up, then start bluetoothd
    synchronously so the user's phone Bluetooth is actually usable when
    the module exits. The old code fired the systemctl start in the
    background with a bogus ``... & || true`` -- the shell interprets
    ``|| true`` after ``&`` as a separate command, so the start never
    ran. That is why the phone appeared with 'Bluetooth off' after
    every session.

    A wedged controller could in theory make the start job block, so
    we cap it with ``timeout 15`` -- the systemd job either succeeds
    quickly (typical <2 s) or times out and we move on, and the app's
    close path is not held hostage either way.
    """
    for unit in ("bluetooth", "bluetooth.socket",
                 "dbus-org.bluez", "bluez"):
        _sysctl("unmask", unit)
    _sysctl("stop", "bluetooth")
    _sh(f"hciconfig {_dev_name(dev_id)} up")
    time.sleep(0.5)
    _sh("timeout 15 systemctl start bluetooth 2>/dev/null || true")