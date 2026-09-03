# discord-web

Discord's **web client** (`discord.com/app`) as a native, fullscreen
**QtWebEngine** app — no browser chrome, no URL bar, just Discord.

## Why a webview, and why QtWebEngine

Discord ships **only x86_64** Linux binaries. Running the official client under
box64 was tried extensively and does not work on this phone: box64 v0.4.3 cannot
bring up Discord's current Chromium (Chrome 150 / Electron 42) multiprocess
startup — the main process spins without ever forking a renderer, so no window
appears (buried with evidence in the session notes; the top-level README's
"Nice to have" #20-area covers the KVM/box64 limits).

So instead this runs the **web** client in a native aarch64 embedded browser.
The engine matters: **QtWebEngine is Chromium**, so `discord.com/app` behaves
exactly as in Chrome — login, DMs, servers, text all work. (WebKitGTK is
Safari-family and Discord degrades on it; only its voice path is truly blocked,
but it has periodic text/login regressions too — so QtWebEngine, not WebKitGTK.)
The device already runs Plasma/KDE, so the Qt stack is mostly present.

## What the wrapper does

- **Hardware GL via freedreno** (`--use-gl=egl`): the Adreno 619 renders and
  composites. The first attempt used SwiftShader (software GL), which pegged the
  CPU and made the UI lag; hardware GL drops the load from ~15 to ~1.6 and the
  lag with it.
- **Phone layout**: an Android user-agent + `--force-device-scale-factor` give
  Discord's mobile web UI (single panel: list -> tap channel -> chat -> back),
  at native render resolution rather than a Qt zoom (which blew everything up).
  Tune the width with `DISCORD_SCALE` (higher = narrower/more "phone";
  default 2.5). Lower it to ~2.0 for bigger elements.
- **No auto keyboard** (`QT_IM_MODULE=none`): a focused text field does not pop
  the on-screen keyboard; raise it yourself from the navigation bar.
- **Wayland-native**, **maximised** (not fullscreen) so the mobile shell keeps
  its navigation bar (minimise/close/keyboard).
- **Persistent session**: a named `QWebEngineProfile` stores cookies +
  localStorage in `~/.local/share/discord-web/`, so you do not re-login every
  launch.

## Install

```sh
cd extra-tools/discord-web
sudo ./install.sh
```

Deps (auto-installed live): `python3-pyqt6`, `python3-pyqt6.qtwebengine`,
`qt6-wayland` — all in Kali arm64. Then open **Discord** from the app drawer.

**First login:** use email/password. Discord hides the QR-code panel on a narrow
(phone) screen — it is served but CSS-collapsed — so the QR is not shown; the
email/password form is. The session then persists.

## Privacy note

The Discord session (your login token, cookies, localStorage) lives **only** in
`~/.local/share/discord-web/` on the device. It is per-user runtime data and is
**never** part of this repo (`.gitignore` guards against it). The repo contains
only the code: the app, the wrapper, the `.desktop`, the icon, and this installer.

## Files

| repo file | installed to |
| --- | --- |
| `discord-web.py` | `/usr/local/share/rhodep/discord-web/discord-web.py` |
| `discord-web` (wrapper) | `/usr/local/bin/discord-web` |
| `discord-web.desktop` | `/usr/share/applications/discord-web.desktop` |
| `discord.svg` | `/usr/share/icons/hicolor/scalable/apps/discord.svg` |

The built files are registered with `rhodep-protect-files` (they live in
`/usr/local` + `/usr/share`, owned by no `.deb`). The icon is vendored here
(GPL-3.0, from Flat-Remix) rather than pulled from a theme at install time, so it
resolves regardless of the active icon theme.
