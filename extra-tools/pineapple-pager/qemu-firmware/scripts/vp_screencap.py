#!/usr/bin/env python3
"""vp_screencap.py - Virtual Pager native screen capture over WebSocket.

Connects to the WiFi Pineapple Pager firmware's native Virtual Pager
screen stream (/api/pager/display/screen.ws) at ws://HOST:1471, authenticates
with the AUTH_<ServerId> session cookie obtained from POST /api/login, receives
one (or more) raw RGBA frames (480x222x4 = 426240 bytes each, Go image.RGBA
Pix layout), and saves a PNG.

Usage:
  vp_screencap.py --host 127.0.0.1:1471 --password PW --out frame.png [--frames N]
"""
import argparse, base64, json, sys, time
import urllib.request
import websocket  # websocket-client
from PIL import Image

SCREEN_W, SCREEN_H = 480, 222
FRAME_BYTES = SCREEN_W * SCREEN_H * 4


def login(host, password):
    body = json.dumps({"username": "root", "password": password}).encode()
    req = urllib.request.Request(
        f"http://{host}/api/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=6) as r:
        tok = json.loads(r.read())["token"]
    hdr = tok.split(".")[0]
    hdr += "=" * (-len(hdr) % 4)
    sid = json.loads(base64.b64decode(hdr))["ServerId"]
    return tok, sid


def capture(host, token, sid, out, frames=1, settle=0.0):
    cookie = f"AUTH_{sid}={token}"
    url = f"ws://{host}/api/pager/display/screen.ws"
    ws = websocket.create_connection(
        url, header=[f"Cookie: {cookie}"], timeout=10)
    print(f"[screen.ws] connected to {url}", flush=True)
    saved = None
    got = 0
    try:
        if settle > 0:
            time.sleep(settle)
        deadline = time.time() + 15
        while got < frames and time.time() < deadline:
            msg = ws.recv()
            if isinstance(msg, str):
                print(f"[screen.ws] text msg: {msg[:120]}", flush=True)
                continue
            n = len(msg)
            print(f"[screen.ws] binary frame: {n} bytes "
                  f"(expected {FRAME_BYTES})", flush=True)
            if n < FRAME_BYTES:
                print("[screen.ws]   frame too small, skipping", flush=True)
                continue
            img = Image.frombytes("RGBA", (SCREEN_W, SCREEN_H), msg[:FRAME_BYTES])
            img.convert("RGB").save(out)
            saved = out
            got += 1
            print(f"[screen.ws]   saved {out}", flush=True)
    finally:
        ws.close()
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:1471")
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="vp_frame.png")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--settle", type=float, default=0.0)
    a = ap.parse_args()
    tok, sid = login(a.host, a.password)
    print(f"[login] ok ServerId={sid}", flush=True)
    out = capture(a.host, tok, sid, a.out, a.frames, a.settle)
    if not out:
        print("[FAIL] no frame captured", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
