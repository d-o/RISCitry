#!/usr/bin/env python3
"""Soak the Zephyr tick image (zephyr/samples/tick) on the board: each `tick N` line must be the last + 1, the
rate steady, no stalls or resets.  python3 tools/serial_tick_soak.py HOURS OUTPREFIX  (writes OUTPREFIX.log/.summary)."""
import os, re, select, subprocess, sys, time, datetime
PORT = "/dev/serial/by-id/usb-Alchitry_Alchitry_Cu_V2_FTA4W6HP-if01-port0"
HOURS = float(sys.argv[1]); OUT = sys.argv[2]
end = time.time() + HOURS * 3600
log = open(OUT + ".log", "a", buffering=1)
def say(m): log.write(f"{datetime.datetime.now():%F %T.%f}"[:-3] + " " + m + "\n")
def summary(final=False):
    with open(OUT + ".summary", "w") as f:
        f.write(f"{'FINAL ' if final else ''}{datetime.datetime.now():%F %T}\nlines {lines} bytes {nbytes} resets {resets} gaps {gaps} corrupt {corrupt} stalls {stalls} reopen {reopens}\n"
                f"per-minute period ms min {pmin*1000:.2f} max {pmax*1000:.2f} overall mean {(psum/pn*1000 if pn else 0):.3f}\nlast tick {last}\n")
lines = nbytes = resets = gaps = corrupt = stalls = reopens = pn = 0
pmin, pmax, psum, last, tprev = 9e9, 0, 0.0, None, None
t0 = n0 = None; win = None
buf = b""
say(f"start, {HOURS} h")
next_sum = 0
while time.time() < end:
    if not os.path.exists(PORT):
        say("port missing"); reopens += 1; time.sleep(0.5); continue
    try: fd = os.open(PORT, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError: time.sleep(0.5); continue
    subprocess.run(["stty", "-F", PORT, "1000000", "raw", "-echo", "-hupcl"], stderr=subprocess.DEVNULL)
    lastrx = time.time()
    try:
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 1.0)
            now = time.time()
            if r:
                try: d = os.read(fd, 4096)
                except BlockingIOError: continue
                if not d: break
                lastrx = now; nbytes += len(d); buf += d
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if re.fullmatch(rb"tick up on alchitry_cu_io/riscitry\r", line):
                        resets += 1; last = None; tprev = None; say("banner (reset or first)"); continue
                    m = re.fullmatch(rb"tick (\d+)\r", line)
                    if not m:
                        corrupt += 1; say(f"corrupt {line!r}"); continue
                    n = int(m.group(1)); lines += 1
                    if last is not None and n != last + 1:
                        gaps += 1; say(f"gap {last} -> {n}")
                    if t0 is None: t0, n0 = now, n
                    last, tprev = n, now
            elif now - lastrx > 5:
                stalls += 1; say("stall (5 s without bytes)"); lastrx = now
            if now > next_sum:
                if last is not None and win is not None and last > win[1] and now > win[0]:
                    p = (now - win[0]) / (last - win[1]); pmin, pmax = min(pmin, p), max(pmax, p)
                    say(f"minute: {last - win[1]} ticks, {p*1000:.3f} ms each")
                if last is not None: win = (now, last)
                if t0 is not None and last > n0: psum, pn = now - t0, last - n0
                summary(); next_sum = now + 60
    except OSError as e:
        say(f"read error {e}"); reopens += 1
    os.close(fd)
summary(True); say("end")
