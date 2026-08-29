# NetHunter Pro App (Phosh)

A native **GTK4 / libadwaita** control panel for **Kali NetHunter Pro** running
on a mainline Linux kernel with Phosh, built for the Motorola Moto G82 5G
(rhodep) port but not tied to it.

It is a **reimplementation**, not a port, of the Android
[kali-nethunter-app](https://github.com/kimocoder/kali-nethunter-app). That app
is Java/Android and most of it exists to manage the Kali *chroot* inside
Android. On NetHunter Pro the tools are native and there is no chroot, so the
UI is rewritten in GTK and only the useful logic — which command each screen
runs — is carried over. Privilege comes from `pkexec` (the Phosh polkit
prompt), not a persistent root shell.

## Why a reimplementation

The original is ~29 Android `Fragment`s over Activities, XML layouts and
Android APIs that do not exist on Phosh. What is reusable is the shell logic:
`new ShellExecuter().RunAsRootOutput("setprop ...")` becomes an async `pkexec`
call here. The Android-only, chroot-heavy screens (ChrootManager, the
Bluetooth/VNC/Wifipumpkin service wrappers) are dropped; native systemd
services replace them.

## Screens

| Screen | What it does | Source it maps to |
|---|---|---|
| Kali Services | start/stop ssh, postgresql, bettercap, ... via systemd | KaliServicesFragment |
| MAC Changer | randomise or reset an interface MAC | MacchangerFragment |
| Nmap | build a scan from options and run it | NmapFragment |
| USB and Radio | OTG charge/host switch, monitor mode (rhodep) | new, port-specific |
| Pwnagotchi | start/stop pwnagotchi on wlan1, pick manual/auto, open the web UI, download wpa-sec cracked keys and export a wordlist | new, port-specific |
| HID / BadUSB | type keystrokes into a connected host | HidFragment |
| Run Command | one-off shell command, optionally as root | TerminalFragment |

A module greys itself out and says which tool is missing if the command it
needs is not on `PATH`, so screens degrade cleanly rather than failing at
click time.

## Wifipumpkin3 screen notes

The rogue-AP screen builds a wifipumpkin3 startup script from the chosen options
and opens it in a terminal. What each field maps to:

  * **AP interface** — `set interface` (the adapter that becomes the AP, e.g.
    `wlan1`, the external dongle).
  * **Internet interface** — `set interface_net` (wifipumpkin3's `-iNet`): the
    adapter with a working connection, shared to the AP's clients, so they get
    real internet through the phone. `wlan0` by default (the internal Wi-Fi).
    Leave empty for a captive portal that does not pass traffic on.
  * **Proxy** — `captiveflask` is the captive portal; `pumpkinproxy` is the
    transparent MITM proxy; `sniffkin3` captures credentials. (Not
    `pumpkinproxy` for the portal — that was a bug once.)
  * **Captive-portal template** — enabled with `set captiveflask.<Template> true`,
    which is what writes e.g. `DarkLogin=true` into `captive-portal.ini`. This is
    not the `templates.custom` install command, which installs *new* templates
    rather than selecting one.

**Captive-portal templates live in** `/usr/share/wifipumpkin3/config/templates`
(also mirrored under the dist-packages path). Drop a new template folder in
there and press **Refresh templates** to pick it up. The stock ones are
`DarkLogin`, `FlaskDemo`, `Login_v4`, `evilqr3` and `loginPage`.

Note the NAT/forwarding wifipumpkin3 sets up needs iptables working in the
kernel; on a kernel without the `ip_tables` module, sharing internet fails with
"table does not exist".

## Architecture

- `executor.py` — async command runner. Everything runs off the GTK thread and
  delivers results back on it; `root=True` wraps in `pkexec`.
- `module.py` — `NHModule` base class and a registry. Each screen is one
  subclass that declares a title, an icon and its required tools.
- `widgets.py` — shared pieces (`CommandButton`, `OutputView`).
- `window.py` — adaptive `NavigationSplitView` that collapses to one pane on a
  phone-width screen, so the same UI works on the desktop and in the hand.
- `modules/` — one file per screen. Adding a screen is one `@register` class.

## Install

```
pip install --break-system-packages .
install -Dm644 data/org.kali.NetHunterPro.desktop \
    /usr/share/applications/org.kali.NetHunterPro.desktop
install -Dm644 data/org.kali.NetHunterPro.svg \
    /usr/share/icons/hicolor/scalable/apps/org.kali.NetHunterPro.svg
install -Dm644 data/org.kali.NetHunterPro.policy \
    /usr/share/polkit-1/actions/org.kali.NetHunterPro.policy
```

Then it appears in the Phosh app drawer as **NetHunter Pro**, or run
`nethunter-pro` from a terminal.

## Requirements

GTK4 and libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`), Python 3.10+, and
whatever tool a given screen drives (nmap, macchanger, aircrack-ng, ...).

## License

GPL-2.0-or-later, matching the app it reimplements.
