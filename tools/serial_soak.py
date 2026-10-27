#!/usr/bin/env python3
"""Serial soak test for the real board: does a byte ever arrive doubled or dropped?

Board setup (boot menu 1, the hello demo, at full speed): middle DIP bank = switch 8 up
(switch register 0x01), left bank = fast (io_dip[2][6]: bit 6, i.e. switch 2 up), stop and
clock mode down; then power-cycle or press the Cu reset. The core prints a banner, then
forever one line per Larson sweep:

    sweep XXXXXXXX\\r\\n        (XXXXXXXX = x8, a 32-bit counter, +1 per line)

Every byte of that stream is predictable, so this checks each line against the pattern and the
counter against the previous one. It never trusts the board's own idea of what it sent.

Anomalies (each logged with the bytes around it):
  doubled   line is longer than 17 bytes and has an adjacent repeated byte
  dropped   line is shorter than 17 bytes
  corrupt   17 bytes but not `sweep ` + 8 hex digits + CRLF
  gap       a valid line whose counter is not the previous + 1 (whole lines lost/repeated)
  stall     no bytes for --stall seconds (and the recovery)
  reset     the banner again (the board restarted; the counter is resynchronised, not an error)

Zero anomalies in N bytes bounds the real error rate at about 3/N (95% confidence).

    python3 tools/serial_soak.py --hours 8 --log ~/soak
"""
import argparse
import collections
import json
import os
import re
import signal
import sys
import time

import serial

LINE = re.compile(rb"^sweep ([0-9A-F]{8})\r\n$")
BANNER = b"Hello from an RV32E soft core on the Alchitry Cu"
LINE_LEN = len(b"sweep 00000000\r\n")        # 16 + CRLF


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--stall", type=float, default=5.0, help="seconds of silence that count as a stall")
    ap.add_argument("--status", type=float, default=60.0, help="seconds between status lines")
    ap.add_argument("--log", default=".", help="directory for soak-<time>.log and .json")
    a = ap.parse_args()

    os.makedirs(os.path.expanduser(a.log), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(os.path.expanduser(a.log), f"soak-{stamp}.log")
    json_path = log_path[:-4] + ".json"
    log = open(log_path, "a", buffering=1)

    def say(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")

    stats = collections.Counter()
    total_bytes = 0
    started = time.time()
    deadline = started + a.hours * 3600
    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    prev = None                 # last counter seen (None = unsynchronised)
    synced = False              # the first line after (re)start is partial: skip it
    history = collections.deque(maxlen=96)      # last bytes, for context
    buf = bytearray()
    last_rx = time.time()
    stalled = False
    last_status = time.time()
    port = None

    def write_json(final=False):
        done = {
            "port": a.port, "baud": a.baud, "started": stamp, "seconds": round(time.time() - started),
            "bytes": total_bytes, "final": final, "counts": dict(stats),
            "anomalies": sum(v for k, v in stats.items() if k in ("doubled", "dropped", "corrupt", "gap")),
            "upper_bound_95pct_per_byte": (3.0 / total_bytes) if total_bytes else None,
        }
        with open(json_path, "w") as f:
            json.dump(done, f, indent=2)
        return done

    def anomaly(kind, line):
        stats[kind] += 1
        ctx = bytes(history)
        say(f"ANOMALY {kind} at byte {total_bytes}, line {stats['lines']}: line={line!r}")
        say(f"        last {len(ctx)} bytes: {ctx!r}")
        say(f"        hex: {ctx.hex(' ')}")

    def check(line):
        nonlocal prev, synced
        if not synced:                          # drop the partial first line
            synced = True
            return
        m = LINE.match(line)
        if m:
            stats["lines"] += 1
            n = int(m.group(1), 16)
            if prev is not None and n != (prev + 1) & 0xFFFFFFFF:
                anomaly("gap", line)
                say(f"        counter {prev:08X} -> {n:08X} (expected {(prev + 1) & 0xFFFFFFFF:08X})")
            prev = n
            return
        text = line.rstrip(b"\r\n")
        if text == BANNER:
            stats["reset"] += 1
            prev = None
            say(f"reset: banner seen at byte {total_bytes}")
            return
        if text == b"" or text == b"\r":        # the blank line either side of the banner
            return
        stats["lines"] += 1
        if prev is not None:
            prev = (prev + 1) & 0xFFFFFFFF      # this line was a counter value too: stay in step
        if len(line) > LINE_LEN and any(line[i] == line[i + 1] for i in range(len(line) - 1)):
            anomaly("doubled", line)
        elif len(line) < LINE_LEN:
            anomaly("dropped", line)
        else:
            anomaly("corrupt", line)
        # a damaged line can hide a counter change: resynchronise on the next valid one
        prev = None

    say(f"soak start: {a.port} {a.baud} baud, {a.hours} h, log {log_path}")

    while not stop and time.time() < deadline:
        if port is None:
            try:
                port = serial.Serial(a.port, a.baud, timeout=0.5)
                port.reset_input_buffer()
                synced = False
                buf.clear()
                last_rx = time.time()
                say("port open")
            except (serial.SerialException, OSError) as e:
                say(f"port open failed ({e}); retrying in 5 s")
                time.sleep(5)
                continue
        try:
            data = port.read(max(1, port.in_waiting))
        except (serial.SerialException, OSError) as e:
            stats["port_error"] += 1
            say(f"port error ({e}); reopening")
            try:
                port.close()
            except Exception:
                pass
            port = None
            time.sleep(2)
            continue

        now = time.time()
        if data:
            if stalled:
                say(f"stall over after {now - last_rx:.1f} s")
                stalled = False
                synced = False
                buf.clear()
                prev = None
            last_rx = now
            total_bytes += len(data)
            for b in data:
                history.append(b)
                buf.append(b)
                if b == 0x0A:
                    check(bytes(buf))
                    buf.clear()
                elif len(buf) > 256:            # no newline for far too long
                    stats["lines"] += 1
                    anomaly("corrupt", bytes(buf))
                    buf.clear()
                    prev = None
        elif now - last_rx > a.stall and not stalled:
            stalled = True
            stats["stall"] += 1
            say(f"STALL: no bytes for {a.stall:.0f} s (byte {total_bytes}); is the board still running menu 1?")

        if now - last_status >= a.status:
            last_status = now
            el = now - started
            say(f"{el / 3600:6.2f} h  {total_bytes:>14,} bytes  {total_bytes / max(el, 1) / 1000:7.1f} kB/s  "
                f"lines {stats['lines']:,}  anomalies d/dr/c/g "
                f"{stats['doubled']}/{stats['dropped']}/{stats['corrupt']}/{stats['gap']}  "
                f"resets {stats['reset']}  stalls {stats['stall']}")
            write_json()

    done = write_json(final=True)
    say("soak end: " + json.dumps(done))
    if done["anomalies"] == 0 and total_bytes:
        say(f"ZERO anomalies in {total_bytes:,} bytes: error rate < {3 / total_bytes:.2e} per byte (95%)")
    return 1 if done["anomalies"] else 0


if __name__ == "__main__":
    sys.exit(main())
