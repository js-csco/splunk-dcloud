#!/usr/bin/env python3
# ===========================================================================
# make_app_icons.py - generate Splunk app icons (no external deps).
#
# Writes appIcon.png (36), appIcon_2x.png (72), appIconAlt.png, appIconAlt_2x.png
# into each app's appserver/static/. Each icon is the app's nav colour with its
# initial in white. Re-run after changing an app's colour/letter.
#   python3 scripts/make_app_icons.py
# ===========================================================================
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
APPS = os.path.join(HERE, "..", "splunk", "apps")

# app -> (hex colour, initial)
ICONS = {
    "dcloud_lab":       ("#65a637", "L"),
    "get_data_in":      ("#e67e22", "G"),
    "correlation":      ("#9b59b6", "C"),
    "infra_monitoring": ("#4a90d9", "I"),
    "metrics":          ("#3fb950", "M"),
    "alerts":           ("#d93f3c", "A"),
}

# 5x7 bitmap font for the initials we use.
FONT = {
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
}


def hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def png_bytes(w, h, pixels):
    # pixels: RGBA bytes (4 per pixel), colortype 6.
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter type 0
        raw.extend(pixels[y * stride:(y + 1) * stride])
    comp = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def render(size, hexcolor, letter):
    bg = hex_rgb(hexcolor) + (255,)
    fg = (255, 255, 255, 255)
    px = bytearray()
    for _ in range(size * size):
        px.extend(bg)
    glyph = FONT[letter]
    scale = max(1, int(size * 0.62) // 7)
    gw, gh = 5 * scale, 7 * scale
    ox, oy = (size - gw) // 2, (size - gh) // 2
    for gy, row in enumerate(glyph):
        for gx, bit in enumerate(row):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        x, y = ox + gx * scale + dx, oy + gy * scale + dy
                        i = (y * size + x) * 4
                        px[i:i + 4] = bytes(fg)
    return png_bytes(size, size, px)


def main():
    for app, (color, letter) in ICONS.items():
        small, big = render(36, color, letter), render(72, color, letter)
        files = (("appIcon.png", small), ("appIcon_2x.png", big),
                 ("appIconAlt.png", small), ("appIconAlt_2x.png", big))
        # Write to BOTH appserver/static (classic) and static (some Splunk
        # versions' app bar/sidebar read the icon from here).
        for sub in (("appserver", "static"), ("static",)):
            d = os.path.join(APPS, app, *sub)
            os.makedirs(d, exist_ok=True)
            for name, data in files:
                with open(os.path.join(d, name), "wb") as fh:
                    fh.write(data)
        print("icons: %s (%s %s)" % (app, color, letter))


if __name__ == "__main__":
    main()
