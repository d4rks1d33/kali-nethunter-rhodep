# WiFi Pineapple Pager - Native Virtual Pager activation & validation

Target: Moto G82 (Kali NetHunter aarch64), firmware under patched qemu-mipsel.
Firmware: rootfs/pineapple/pineapple (Go, MIPSel, stripped; Go pclntab intact).

## TL;DR - what works

- The firmware's **native Virtual Pager** (visor + input over HTTP/WebSocket) is
  ACTIVE and fully validated.
- **Screen (visor):** `ws://HOST:1471/api/pager/display/screen.ws` streams the real
  display as raw RGBA frames (480x222x4 = 426240 bytes/frame). Captured the live
  setup wizard.
- **Input (navigation):** `ws://HOST:1471/api/pager/input/keys.ws` accepts plain-text
  JS `KeyboardEvent.key` strings ("ArrowDown", "Enter", etc.). Verified the display
  NAVIGATES (before/after frames differ, wizard advanced Welcome -> Notices -> ...).
- **TCP API on :1471** is up (bind `*:1471`, LAN-reachable). Controllable via the
  unix socket: `POST /api/system/enable_tcp` / `.../disable_tcp`.
- Auth: `POST /api/login {"username":"root","password":PW}` -> token -> cookie.

## 1. How the Virtual Pager is activated

### 1a. $_VIRTUAL_PAGER_ENABLED / .cc_unlock_ui — NOT needed in this build/state

Honest finding: I did **NOT** have to set `$_VIRTUAL_PAGER_ENABLED`, call
`SetVirtualPagerEnabled`, or create `/tmp/.cc_unlock_ui`. In this firmware build the
Virtual Pager HTTP/WS endpoints and the TCP API are served by default once the
server boots ("[CORE] TCP API service starting", "[UI] UI Startup complete").
`/tmp/.cc_unlock_ui` does not exist on disk and the screen/input WS still work.

The binary does contain `GetVirtualPagerEnabled` / `SetVirtualPagerEnabled`
(package `pineapplepager`), the var `$_VIRTUAL_PAGER_ENABLED`, the gate string
`virtual pager not enabled`, and `/tmp/.cc_unlock_ui`. These gate the *device-side*
UI entry ("Virtual Pager" menu item) and possibly a stricter production policy, but
in the emulated boot the WS endpoints are reachable directly after auth. If a build
does gate them, the toggle is `pineapplepager.SetVirtualPagerEnabled` (settable from
Settings > System on-device, persisted to the pager prefs), and `/tmp/.cc_unlock_ui`
is the developer/unlock marker — create it in the rootfs before boot to unlock.

### 1b. Enabling the TCP API on :1471 (the documented mechanism)

The local unix socket `/tmp/api.sock` exposes the SAME `/api/` router but is
**trusted (no auth)**. Enable/disable the TCP listener there:

    # enable (creates the *:1471 listener)
    curl --unix-socket /tmp/api.sock -X POST http://localhost/api/system/enable_tcp
    # -> {"success":true}
    # disable (tears the listener down)
    curl --unix-socket /tmp/api.sock -X POST http://localhost/api/system/disable_tcp

Verified: after `disable_tcp` the `*:1471` listener disappears from `ss -lntp`; after
`enable_tcp` it reappears (qemu-mipsel, `*:1471`). qemu-user shares the host netns so
`:1471` is reachable from the G82 host and over the LAN (tested via 127.0.0.1 and
192.168.1.53).

## 2. Auth model (required for the TCP :1471 endpoints)

Reverse-engineered from the embedded SPA (served at `/`, title "Virtual Pager") and
confirmed live:

1. `POST /api/login`  body `{"username":"root","password":"<pw>"}`
   -> `200 {"token":"<b64url(json{User,Expiry,ServerId})>.<b64sig>"}`
2. First token segment base64url-decodes to JSON with `ServerId`.
3. Client stores cookie: `AUTH_<ServerId>=<token>` (path=/, 12h).
4. Every API/WS call carries that cookie. `GET /api/api_ping` validates it (200/401).

Password source: `utils.CheckPassword` -> `/etc/shadow` (root entry).
On this rootfs root was locked (`root:*`), so login always failed. I set a known
SHA-512 hash so the firmware's own auth accepts it (pw used: `pineapplepager`):

    openssl passwd -6 pineapplepager   # -> $6$... ; written into rootfs/etc/shadow root line
    # then restart firmware so it re-reads shadow

NOTE: the unix socket `/api/api_ping` returns 200 with NO cookie — the local socket
is pre-trusted. The screen/input WS, however, are only exposed over TCP :1471, which
DOES require the cookie.

## 3. Screen WS protocol (the visor)

- URL: `ws://HOST:1471/api/pager/display/screen.ws` (cookie auth).
- Server pushes **binary** messages, each a full frame:
  raw Go `image.RGBA` Pix buffer, width=480 height=222, 4 bytes/pixel (R,G,B,A),
  stride=480*4, total **426240 bytes**. Decode directly to a PIL RGBA image.
- (The binary also has a `screenAsJpegWs` path — "[screenAsJpegWs] write frame" etc. —
  but the shipped SPA and this build use the RGBA `screen.ws` path, 426240-byte frames,
  decoded client-side via createImageData. The screen WS also reads inbound control
  msgs `recv What=%s` for keepalive.)
- Validated: captured a frame = live setup wizard "WELCOME, HACKER".

## 4. Input endpoint (navigation) — the key proof

- URL: `ws://HOST:1471/api/pager/input/keys.ws` (cookie auth).
- Client SENDS plain-text strings = JS `KeyboardEvent.key`:
  `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Enter` (A button),
  `Escape` (B button). (From SPA: `keyws.send(e.key)` and the on-screen D-pad
  `onclick="keyws.send('ArrowDown')"` etc.)
- Also present: `POST /api/pager/debug/input/emulatekeys` returns `"OK"` (debug path).
- NOT registered in this build (404): `/api/pager/display/wake|sleep|dpad`,
  `/api/pager/input/keys` (non-.ws), `/api/pager/preferences/get|set`.

### Before/after proof (critical)

Sent one `ArrowDown` on keys.ws; captured screen.ws frame before and after:

- BEFORE: "WELCOME, HACKER" wizard, selection on **Welcome**.
- AFTER : "NOTICE: Firmware" wizard page, selection on **Notices**.
- Diff: 26092/426240 bytes (6.12%). Repeat x2 -> advanced to "NOTICE: Charging".
  Final re-run after an enable_tcp cycle: 33687 bytes (7.90%) changed.

=> The firmware NAVIGATES via the native Virtual Pager input. This is the behavior
   the old evdev-FIFO approach could not achieve (fb_differing_bytes=0 there).

## 5. Scripts (on the G82, /home/kali/pager-qemu/qemu-firmware/scripts/)

- `fw_start.sh`       - start firmware under patched qemu-mipsel, detached, logs to
                        /home/kali/pager-qemu/fw_boot.log (run as root).
- `vp_screencap.py`   - login + connect screen.ws, save one RGBA frame as PNG.
      python3 vp_screencap.py --host 127.0.0.1:1471 --password pineapplepager --out f.png
- `vp_input_test.py`  - login + screen.ws + keys.ws; before frame, send key, after
                        frame, report differing bytes (the navigation proof).
      python3 vp_input_test.py --host 127.0.0.1:1471 --password pineapplepager \
              --key ArrowDown --before b.png --after a.png [--repeat N]

Captured artifacts in /home/kali/pager-qemu/:
  vp_frame1.png (wizard), vp_before.png/vp_after.png (Welcome->Notices),
  vp_before2.png/vp_after2.png (->Charging), final_before.png/final_after.png.

## 6. What did NOT work / caveats (honest)

- `/api/pager/power/get` -> 500 (reads /sys/class/power_supply/bq27546-0/uevent,
  absent in the emulated sysfs; not an auth problem). The prepare.sh fakes a
  `battery` supply, not `bq27546-0`; harmless for the visor/input goal.
- `enable_tcp`/`disable_tcp`, `preferences/*`, `display/wake|sleep|dpad` are only
  on the unix `/api/` router or simply not registered over TCP in this build; the
  screen+input WS are the supported Virtual Pager surface and they work.
- Login requires a real `/etc/shadow` root password; I injected one. Without setup
  completed on a real device you'd set it through the setup wizard (which the visor
  can now drive) or via the password change API.
- Passwords < 8 chars are rejected before auth (connection dropped / HTTP 000),
  matching the SPA's `minlength=8`.
