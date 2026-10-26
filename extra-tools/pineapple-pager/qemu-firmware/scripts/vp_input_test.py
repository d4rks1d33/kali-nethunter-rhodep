#!/usr/bin/env python3
"""vp_input_test.py - prove the WiFi Pineapple Pager native Virtual Pager INPUT
navigates the display.

Protocol (reverse-engineered from the firmware's embedded SPA served at :1471):
  - Auth:  POST /api/login {"username":"root","password":PW} -> {"token": JWTish}
           cookie AUTH_<ServerId>=<token>  (ServerId = base64url(token.split('.')[0]).ServerId)
  - Screen: ws://HOST/api/pager/display/screen.ws  -> binary frames,
            raw Go image.RGBA Pix, 480x222x4 = 426240 bytes each.
  - Input:  ws://HOST/api/pager/input/keys.ws  <- send plain-text JS KeyboardEvent.key:
            "ArrowUp" "ArrowDown" "ArrowLeft" "ArrowRight" "Enter"(A) "Escape"(B)

This script: connects both WS, grabs a BEFORE frame, sends a key on keys.ws,
grabs an AFTER frame, saves both PNGs and reports how many bytes differ.
"""
import argparse, base64, json, sys, time
import urllib.request
import websocket
from PIL import Image

SCREEN_W, SCREEN_H = 480, 222
FRAME_BYTES = SCREEN_W * SCREEN_H * 4


def login(host, password):
    body = json.dumps({"username": "root", "password": password}).encode()
    req = urllib.request.Request(f"http://{host}/api/login", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=6) as r:
        tok = json.loads(r.read())["token"]
    hdr = tok.split(".")[0]; hdr += "=" * (-len(hdr) % 4)
    sid = json.loads(base64.b64decode(hdr))["ServerId"]
    return tok, sid


def open_screen(host, cookie):
    ws = websocket.create_connection(
        f"ws://{host}/api/pager/display/screen.ws",
        header=[f"Cookie: {cookie}"], timeout=10)
    return ws


def open_keys(host, cookie):
    ws = websocket.create_connection(
        f"ws://{host}/api/pager/input/keys.ws",
        header=[f"Cookie: {cookie}"], timeout=10)
    return ws


def grab_frame(ws, deadline=12):
    end = time.time() + deadline
    while time.time() < end:
        msg = ws.recv()
        if isinstance(msg, (bytes, bytearray)) and len(msg) >= FRAME_BYTES:
            return bytes(msg[:FRAME_BYTES])
    raise RuntimeError("no full frame received")


def save_png(raw, path):
    Image.frombytes("RGBA", (SCREEN_W, SCREEN_H), raw).convert("RGB").save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:1471")
    ap.add_argument("--password", required=True)
    ap.add_argument("--key", default="ArrowDown",
                    help="JS key name to send, e.g. ArrowDown Enter Escape")
    ap.add_argument("--before", default="vp_before.png")
    ap.add_argument("--after", default="vp_after.png")
    ap.add_argument("--repeat", type=int, default=1,
                    help="how many times to send the key")
    ap.add_argument("--settle", type=float, default=1.2,
                    help="seconds to wait after key before grabbing after frame")
    a = ap.parse_args()

    tok, sid = login(a.host, a.password)
    cookie = f"AUTH_{sid}={tok}"
    print(f"[login] ok ServerId={sid}", flush=True)

    scr = open_screen(a.host, cookie)
    keys = open_keys(a.host, cookie)
    print("[ws] screen.ws + keys.ws connected", flush=True)

    before = grab_frame(scr)
    save_png(before, a.before)
    print(f"[before] saved {a.before}", flush=True)

    for i in range(a.repeat):
        keys.send(a.key)
        print(f"[input] sent key '{a.key}' ({i+1}/{a.repeat})", flush=True)
        time.sleep(0.25)

    # let the firmware re-render; drain stale frames then grab a fresh one
    time.sleep(a.settle)
    # drain any queued frames so we get the newest post-input render
    scr.settimeout(0.3)
    drained = 0
    try:
        while True:
            m = scr.recv()
            if isinstance(m, (bytes, bytearray)) and len(m) >= FRAME_BYTES:
                last = bytes(m[:FRAME_BYTES]); drained += 1
    except Exception:
        pass
    scr.settimeout(10)
    if drained:
        after = last
    else:
        after = grab_frame(scr)
    save_png(after, a.after)
    print(f"[after] saved {a.after} (drained {drained} queued frames)", flush=True)

    diff = sum(1 for x, y in zip(before, after) if x != y)
    pct = 100.0 * diff / FRAME_BYTES
    print(f"[RESULT] differing bytes: {diff}/{FRAME_BYTES} ({pct:.2f}%)", flush=True)
    if diff > 0:
        print("[RESULT] DISPLAY CHANGED -> input navigated the Virtual Pager", flush=True)
    else:
        print("[RESULT] no change (display identical)", flush=True)

    keys.close(); scr.close()
    return 0 if diff > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
