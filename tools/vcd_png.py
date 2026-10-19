"""Render the 1-bit signals of one or more VCD files (vsim --trace writes them) as stacked
waveforms in a PNG, no GTKWave needed.

Usage: python3 tools/vcd_png.py OUT.png [--zoom CLOCKS] FILE.vcd[=LABEL] ...
Every signal of every file gets a row (labelled LABEL, or the file name and signal name). The
picture has two panels over a shared axis in clocks: the whole trace, then (with --zoom N, default
400) its first N clocks blown up, where single-tick pulses show.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, ROW, GAP, LEFT, RIGHT = 1700, 46, 14, 170, 30
BG, FG, GRID, TRACE, DIM = (250, 250, 247), (30, 30, 30), (220, 220, 215), (20, 90, 160), (120, 120, 120)


def load(path):
    """{signal name: [(time, value)]}, total length in clocks."""
    names, ids, series, t, last = {}, {}, {}, 0, 0
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line.startswith("$var"):
            parts = line.split()
            ids[parts[3]] = parts[4]
            series[parts[4]] = []
        elif line.startswith("#"):
            t = int(line[1:])
            last = max(last, t)
        elif line and line[0] in "01" and line[1:] in ids:
            series[ids[line[1:]]].append((t, int(line[0])))
    return series, last


def font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_panel(d, y, rows, t0, t1, title, small):
    d.text((LEFT, y), title, fill=FG, font=font(15))
    y += 24
    x = lambda t: LEFT + (t - t0) / (t1 - t0) * (W - LEFT - RIGHT)
    top = y
    for label, events in rows:
        high, low = y + 6, y + ROW - 6
        d.text((10, y + ROW // 2 - 8), label, fill=FG, font=small)
        d.line([(LEFT, low), (W - RIGHT, low)], fill=GRID)
        value, prev_t = 0, t0
        pts = []
        for t, v in events:
            if t <= t0:
                value = v
                continue
            if t >= t1:
                break
            pts += [(x(t), low if value == 0 else high), (x(t), low if v == 0 else high)]
            value = v
        pts = [(x(t0), low if (events and [v for t, v in events if t <= t0] or [0])[-1] == 0 else high)] + pts
        pts.append((x(t1), low if value == 0 else high))
        d.line(pts, fill=TRACE, width=2)
        y += ROW + GAP
    span = t1 - t0
    step = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000) if span / s <= 10)
    for t in range((t0 // step) * step, t1 + 1, step):
        if t < t0:
            continue
        d.line([(x(t), top - 4), (x(t), y - GAP + 2)], fill=GRID)
        d.text((x(t) - 12, y - GAP + 4), str(t), fill=DIM, font=small)
    return y + 26


def main():
    args = sys.argv[1:]
    out, zoom = args[0], 400
    files = []
    i = 1
    while i < len(args):
        if args[i] == "--zoom":
            zoom = int(args[i + 1])
            i += 2
        else:
            files.append(args[i])
            i += 1
    rows, total = [], 0
    for f in files:
        path, _, label = f.partition("=")
        series, last = load(path)
        total = max(total, last + 1)
        for name, events in series.items():
            rows.append((label or f"{Path(path).stem}:{name}", events))
    height = 2 * (24 + len(rows) * (ROW + GAP) + 26) + 50
    img = Image.new("RGB", (W, height), BG)
    d = ImageDraw.Draw(img)
    small = font(13)
    y = draw_panel(d, 10, rows, 0, total, f"whole trace: {total} clocks (x axis in clocks)", small)
    draw_panel(d, y + 10, rows, 0, min(zoom, total), f"first {min(zoom, total)} clocks, blown up", small)
    img.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
