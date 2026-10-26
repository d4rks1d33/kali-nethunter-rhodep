#!/usr/bin/env python3
"""Render the Pager framebuffer (RGB565) to PNG, correcting rotation.

The firmware renders into a 213120-byte buffer that decodes as RGB565 with
the pixel data laid out as 222 wide x 480 tall (the panel is physically
mounted rotated 90deg). We decode that then rotate -90 to present the
intended 480x222 landscape image.
"""
import struct, zlib, sys

data = open(sys.argv[1], "rb").read()
out  = sys.argv[2] if len(sys.argv) > 2 else "/tmp/opencode/pager-qemu/fb.png"

# raw panel layout
PW, PH = 222, 480  # panel width, height in stored buffer

def px565(i):
    b0, b1 = data[i*2], data[i*2+1]
    v = (b1 << 8) | b0
    r = (v >> 11) & 0x1f; g = (v >> 5) & 0x3f; b = v & 0x1f
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))

# decode panel buffer
panel = [[px565(y*PW + x) for x in range(PW)] for y in range(PH)]

# rotate -90 (counter-clockwise) to get landscape 480x222
W, H = PH, PW  # 480 x 222
img = bytearray(W*H*3)
for y in range(H):          # 0..221
    for x in range(W):      # 0..479
        # inverse map: landscape (x,y) <- panel (py=x? ) choose rotation
        # try rotate so text reads left-to-right
        pr = x            # panel row 0..479
        pc = (PW-1) - y   # panel col
        r,g,b = panel[pr][pc]
        o=(y*W+x)*3; img[o]=r; img[o+1]=g; img[o+2]=b

def write_png(fn,w,h,rgb):
    raw=bytearray()
    for yy in range(h):
        raw.append(0); raw += rgb[yy*w*3:(yy+1)*w*3]
    def ch(t,d): return struct.pack(">I",len(d))+t+d+struct.pack(">I",zlib.crc32(t+d)&0xffffffff)
    png=b"\x89PNG\r\n\x1a\n"+ch(b"IHDR",struct.pack(">IIBBBBB",w,h,8,2,0,0,0))
    png+=ch(b"IDAT",zlib.compress(bytes(raw),9))+ch(b"IEND",b"")
    open(fn,"wb").write(png)

write_png(out, W, H, img)
print("wrote", out, f"{W}x{H}")
