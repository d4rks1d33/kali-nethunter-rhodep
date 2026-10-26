#!/usr/bin/env python3
"""pager_viewer.py - live viewer + touch/keyboard input for the WiFi Pineapple
Pager firmware running under the patched qemu-mipsel on the Moto G82.

TASK A (display): mmaps the firmware's live framebuffer (rootfs/dev/fb0), decodes
it as RGB565 laid out 222(wide) x 480(tall) (panel physically rotated 90deg),
rotates -90 to the intended 480x222 landscape, and blits it scaled
(nearest-neighbor, aspect preserved) to the phone screen, refreshing ~20 fps.

TASK B (input): draws an on-screen virtual D-pad + A/B/PWR, and maps keyboard
keys. Presses are written as struct input_event records to the host FIFO that
the patched qemu forwards to the firmware's evdev reader.

Run on the phone's Wayland session:
    WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 \
        python3 pager_viewer.py

Env:
    PAGER_FB        framebuffer file     (default rootfs/dev/fb0)
    PAGER_INPUT_FIFO input FIFO          (default /tmp/pager_input.fifo)
    PAGER_FULLSCREEN 1 to go fullscreen  (default 1)
"""
import os, sys, time, struct, mmap

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import numpy as np
import pygame

# ----- config -----
FB_PATH   = os.environ.get("PAGER_FB", "/home/kali/pager-qemu/rootfs/dev/fb0")
FIFO_PATH = os.environ.get("PAGER_INPUT_FIFO", "/tmp/pager_input.fifo")
FULLSCREEN = os.environ.get("PAGER_FULLSCREEN", "1") == "1"

PW, PH = 222, 480            # stored panel buffer (wide x tall)
DW, DH = PH, PW             # landscape display 480 x 222
FB_BYTES = 426240            # file size; active data = PW*PH*2 = 213120
ACTIVE   = PW * PH * 2

# ----- input codes (match pager_input.py / the firmware) -----
EV_SYN, EV_KEY, SYN_REPORT = 0x00, 0x01, 0x00
CODES = {
    "up": 0x220, "down": 0x221, "left": 0x222, "right": 0x223,
    "a": 0x130, "b": 0x131, "power": 116,
}
_PACK = struct.Struct("<IIHHi")


class Injector:
    def __init__(self, path):
        self.path = path
        self.fd = -1

    def _ensure(self):
        if self.fd < 0:
            try:
                # non-blocking so a missing reader never freezes the UI
                self.fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                self.fd = -1
        return self.fd >= 0

    def _ev(self, etype, code, value):
        if not self._ensure():
            return
        t = time.time(); sec = int(t); usec = int((t - sec) * 1e6)
        try:
            os.write(self.fd, _PACK.pack(sec, usec, etype, code, value))
        except OSError:
            try: os.close(self.fd)
            except OSError: pass
            self.fd = -1

    def tap(self, name):
        code = CODES.get(name)
        if code is None:
            return
        self._ev(EV_KEY, code, 1); self._ev(EV_SYN, SYN_REPORT, 0)
        self._ev(EV_KEY, code, 0); self._ev(EV_SYN, SYN_REPORT, 0)
        print("[input] tap", name, flush=True)


class Framebuffer:
    """mmap the live fb file; decode RGB565 222x480 -> landscape 480x222 RGB."""
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        sz = os.fstat(self.fd).st_size
        self.map = mmap.mmap(self.fd, max(sz, ACTIVE), mmap.MAP_SHARED,
                             mmap.PROT_READ)

    def frame_rgb(self):
        """return a (DW, DH, 3) uint8 array ready for pygame (surfarray order)."""
        self.map.seek(0)
        raw = self.map.read(ACTIVE)
        v = np.frombuffer(raw, dtype="<u2").reshape(PH, PW)  # (480 tall,222 wide)
        r = ((v >> 11) & 0x1f).astype(np.uint8)
        g = ((v >> 5) & 0x3f).astype(np.uint8)
        b = (v & 0x1f).astype(np.uint8)
        r = (r << 3) | (r >> 2)
        g = (g << 2) | (g >> 4)
        b = (b << 3) | (b >> 2)
        panel = np.dstack((r, g, b))               # (480,222,3) = (PH,PW,3)
        # rotate -90 (CCW) to get landscape: out[y,x] = panel[x, PW-1-y]
        # fbrender.py mapping: pr=x(0..479), pc=PW-1-y(0..221); img indexed [y,x]
        # Build (DH=222, DW=480, 3) then transpose to pygame surfarray (DW,DH,3).
        # out_landscape[y, x] = panel[x, PW-1-y]
        land = panel[:, ::-1, :].transpose(1, 0, 2)   # (PW,PH,3)=(222,480,3)
        # land[y, x] = panel[x, PW-1-y]  -> that's (DH,DW,3)
        # pygame surfarray wants (W,H,3): transpose axes 0,1
        return np.ascontiguousarray(land.transpose(1, 0, 2))  # (480,222,3)


def build_buttons(sw, sh, disp_rect):
    """Return list of (name, pygame.Rect, label). Place a control strip at the
    bottom (or right) of the screen, outside the display image."""
    btns = []
    # area below the display image
    pad = 8
    top = disp_rect.bottom + pad
    avail_h = sh - top - pad
    if avail_h < 80:
        # not enough room below; overlay a semi strip at the bottom of screen
        top = sh - 150
        avail_h = 150 - pad
    # D-pad on the left, A/B/PWR on the right
    bh = max(44, min(70, avail_h // 3))
    bw = bh
    # dpad cluster
    cx = pad + bw + pad
    cy = top + bh
    btns.append(("up",    pygame.Rect(cx, cy - bh, bw, bh), "UP"))
    btns.append(("down",  pygame.Rect(cx, cy + bh, bw, bh), "DN"))
    btns.append(("left",  pygame.Rect(cx - bw, cy, bw, bh), "LF"))
    btns.append(("right", pygame.Rect(cx + bw, cy, bw, bh), "RT"))
    # action buttons on the right side
    rx = sw - (bw + pad) * 3
    btns.append(("a",     pygame.Rect(rx,           cy, bw, bh), "A"))
    btns.append(("b",     pygame.Rect(rx + bw + pad, cy, bw, bh), "B"))
    btns.append(("power", pygame.Rect(rx + 2*(bw+pad), cy, bw, bh), "PWR"))
    return btns


def main():
    inj = Injector(FIFO_PATH)
    try:
        fb = Framebuffer(FB_PATH)
    except OSError as e:
        print("cannot open framebuffer", FB_PATH, ":", e, file=sys.stderr)
        return 1

    pygame.init()
    pygame.display.set_caption("WiFi Pineapple Pager")
    flags = pygame.FULLSCREEN if FULLSCREEN else 0
    screen = pygame.display.set_mode((0, 0) if FULLSCREEN else (960, 540), flags)
    sw, sh = screen.get_size()
    font = pygame.font.SysFont(None, 22)

    # compute scaled display rect (preserve aspect, leave room at bottom)
    reserved = 170  # space for the control strip
    max_w, max_h = sw, sh - reserved
    scale = min(max_w / DW, max_h / DH)
    scale = max(1.0, scale)
    tw, th = int(DW * scale), int(DH * scale)
    dx = (sw - tw) // 2
    dy = max(0, (max_h - th) // 2)
    disp_rect = pygame.Rect(dx, dy, tw, th)

    buttons = build_buttons(sw, sh, disp_rect)

    key_map = {
        pygame.K_UP: "up", pygame.K_DOWN: "down",
        pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
        pygame.K_RETURN: "a", pygame.K_KP_ENTER: "a", pygame.K_a: "a",
        pygame.K_ESCAPE: "b", pygame.K_BACKSPACE: "b", pygame.K_b: "b",
        pygame.K_p: "power",
    }
    quit_keys = {pygame.K_q}

    small = pygame.Surface((DW, DH))
    clock = pygame.time.Clock()
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in quit_keys:
                    running = False
                elif ev.key in key_map:
                    inj.tap(key_map[ev.key])
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                if ev.type == pygame.FINGERDOWN:
                    px, py = int(ev.x * sw), int(ev.y * sh)
                else:
                    px, py = ev.pos
                for name, rect, _ in buttons:
                    if rect.collidepoint(px, py):
                        inj.tap(name)
                        break

        # --- draw live framebuffer ---
        try:
            arr = fb.frame_rgb()
            pygame.surfarray.blit_array(small, arr)
        except Exception as e:
            pass
        scaled = pygame.transform.scale(small, (tw, th))
        screen.fill((0, 0, 0))
        screen.blit(scaled, (dx, dy))

        # --- draw buttons ---
        for name, rect, label in buttons:
            pygame.draw.rect(screen, (40, 40, 48), rect, border_radius=8)
            pygame.draw.rect(screen, (120, 120, 140), rect, 2, border_radius=8)
            t = font.render(label, True, (230, 230, 240))
            screen.blit(t, t.get_rect(center=rect.center))

        pygame.display.flip()
        clock.tick(20)

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
