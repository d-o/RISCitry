#!/usr/bin/env python3
"""Receive-side soak for the real board: send random bytes, check the echo comes back unchanged.

Board setup (boot menu 2: Larson, direction buttons and the serial receiver interrupt, which
echoes every byte it receives): middle DIP bank = switch 7 up (switch register 0x02), left bank
= fast (io_dip[2][6]: switch 2 up), stop and clock mode down; then power-cycle. Do not press
the direction buttons: they print "buttons N" into the echo stream.

Each round sends a random chunk and waits for exactly that many bytes back. Chunks of one byte are
ping-pong (the core always has time); larger chunks leave the host back to back at line rate, 10 us a byte,
which tests the one-byte receive buffer: the core must pop each byte before the next completes.
Results are kept per chunk size, because the two kinds of failure mean different things:

  chunk 1       a fault in the receiver or the transmitter (nothing else is going on)
  chunk > 1     the same, or the core not keeping up (a dropped byte = overrun; that is a throughput
                limit of the design, not a corrupt byte, so it is reported separately)

Per round the echo is compared with what was sent:
  ok        identical
  corrupt   same length, different bytes
  dropped   shorter (the missing bytes are listed)
  extra     longer (doubled or unsolicited bytes)

    python3 tools/serial_echo_soak.py --hours 8 --log ~/soak
"""
import argparse
import collections
import json
import os
import random
import signal
import sys
import time

import serial

SIZES = [1, 1, 1, 2, 4, 8, 16, 32, 64, 128]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--timeout", type=float, default=0.25, help="seconds to wait for the rest of an echo")
    ap.add_argument("--sizes", default=",".join(map(str, SIZES)), help="chunk sizes to cycle through")
    ap.add_argument("--status", type=float, default=60.0)
    ap.add_argument("--log", default=".")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    sizes = [int(x) for x in a.sizes.split(",")]
    rng = random.Random(a.seed)
    os.makedirs(os.path.expanduser(a.log), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(os.path.expanduser(a.log), f"echo-{stamp}.log")
    json_path = log_path[:-4] + ".json"
    log = open(log_path, "a", buffering=1)

    def say(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # per chunk size: rounds, bytes sent, ok, corrupt, dropped, extra
    keys = sorted(set(sizes))
    stats = {n: collections.Counter() for n in keys}
    total_sent = 0
    started = time.time()
    deadline = started + a.hours * 3600
    last_status = started
    port = None

    def faults(n):
        s = stats[n]
        return s["corrupt"] + s["dropped"] + s["extra"]

    def summary(final=False):
        d = {"port": a.port, "baud": a.baud, "started": stamp, "seconds": round(time.time() - started),
             "final": final, "bytes_sent": total_sent, "by_chunk": {}}
        for n in keys:
            s = stats[n]
            d["by_chunk"][n] = {"rounds": s["rounds"], "bytes": s["bytes"], "ok": s["ok"],
                                "corrupt": s["corrupt"], "dropped": s["dropped"], "extra": s["extra"]}
        d["ping_pong_faults"] = faults(1) if 1 in stats else None
        pp = stats[1]["bytes"] if 1 in stats else 0
        d["ping_pong_upper_bound_95pct_per_byte"] = (3.0 / pp) if pp and not faults(1) else None
        with open(json_path, "w") as f:
            json.dump(d, f, indent=2)
        return d

    def drain(quiet=0.05, limit=2.0):
        """Read until the line has been quiet for `quiet` s; returns what came."""
        got = b""
        end = time.time() + limit
        last = time.time()
        while time.time() < end:
            d = port.read(max(1, port.in_waiting))
            if d:
                got += d
                last = time.time()
            elif time.time() - last > quiet:
                break
        return got

    say(f"echo soak start: {a.port} {a.baud} baud, {a.hours} h, chunk sizes {sizes}, log {log_path}")
    i = 0
    while not stop and time.time() < deadline:
        if port is None:
            try:
                port = serial.Serial(a.port, a.baud, timeout=0.003, write_timeout=2)
                time.sleep(0.2)
                port.reset_input_buffer()
                stray = drain()
                if stray:
                    say(f"note: {len(stray)} bytes on the line before starting (banner or noise): {stray[:60]!r}")
                say("port open")
            except (serial.SerialException, OSError) as e:
                say(f"port open failed ({e}); retrying in 5 s")
                time.sleep(5)
                continue

        n = sizes[i % len(sizes)]
        i += 1
        sent = bytes(rng.getrandbits(8) for _ in range(n))
        try:
            port.write(sent)
            got = b""
            end = time.time() + a.timeout
            while len(got) < n and time.time() < end:
                got += port.read(n - len(got))
            late = drain(quiet=0.004, limit=0.2) if len(got) >= n else b""    # anything extra after the echo
        except (serial.SerialException, OSError) as e:
            say(f"port error ({e}); reopening")
            try:
                port.close()
            except Exception:
                pass
            port = None
            time.sleep(2)
            continue

        got += late
        s = stats[n]
        s["rounds"] += 1
        s["bytes"] += n
        total_sent += n
        if got == sent:
            s["ok"] += 1
        else:
            if len(got) == len(sent):
                kind = "corrupt"
            elif len(got) < len(sent):
                kind = "dropped"
            else:
                kind = "extra"
            s[kind] += 1
            say(f"FAULT {kind} chunk={n} round={s['rounds']}")
            say(f"      sent {sent.hex(' ')}")
            say(f"      got  {got.hex(' ')}")
            # let any straggler bytes arrive so the next round starts clean
            tail = drain(quiet=0.1, limit=1.0)
            if tail:
                say(f"      then  {tail.hex(' ')}")

        now = time.time()
        if now - last_status >= a.status:
            last_status = now
            el = now - started
            per = "  ".join(f"{k}:{faults(k)}/{stats[k]['rounds']}" for k in keys)
            say(f"{el / 3600:6.2f} h  {total_sent:>12,} bytes sent  {total_sent / max(el, 1) / 1000:6.1f} kB/s  "
                f"faults/rounds by chunk {per}")
            summary()

    d = summary(final=True)
    say("echo soak end: " + json.dumps(d))
    for k in keys:
        s = stats[k]
        if s["rounds"]:
            say(f"chunk {k:>3}: {s['rounds']:,} rounds, {s['bytes']:,} bytes, ok {s['ok']:,}, "
                f"corrupt {s['corrupt']}, dropped {s['dropped']}, extra {s['extra']}")
    pp = d["ping_pong_upper_bound_95pct_per_byte"]
    if pp:
        say(f"ping-pong (chunk 1): ZERO faults in {stats[1]['bytes']:,} bytes: error rate < {pp:.2e} per byte (95%)")
    return 1 if any(faults(k) for k in keys) else 0


if __name__ == "__main__":
    sys.exit(main())
