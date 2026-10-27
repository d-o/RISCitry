#!/usr/bin/env python3
"""Edge probes for the board's serial receiver and transmitter, host side only (boot menu 7 at full
speed, whose C loop echoes every byte raw; menu 2 echoes too). Nothing here changes the board.

Run after the soak, with nothing else on the port:  python3 tools/serial_edges.py --log ~/soak

Tests, safest first (the break test is last because a wedged receiver would need a Cu reset):
  all256     every byte value, as a burst of 16 and as single bytes
  patterns   0x00 0xFF 0x55 0xAA 0x80 0x01 0xF0 0x0F in 128-byte bursts (long runs of equal bits, the
             worst case for a receiver that re-times on edges)
  longburst  1 KB .. 64 KB random in one write at line rate: the echo leaves at the rate the input
             arrives, so any clock skew between the host's baud and the board's builds a queue in the
             one-byte buffer until a byte is dropped; the index of the first fault measures the skew
  idle       a quiet line for 0.5 .. 120 s, then bytes
  latency    round trip of a single byte, 2000 times (min / median / p99 / max)
  break      a break (line held low) of 1, 10 and 100 ms between good bytes: the receiver should drop
             the framing error and carry on; phantom bytes are reported
"""
import argparse
import json
import os
import random
import statistics
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--log", default=".")
    ap.add_argument("--reps", type=int, default=20, help="repetitions of the long bursts")
    ap.add_argument("--skip", default="", help="comma list of tests to skip")
    ap.add_argument("--idle", default="0.5,5,30,120")
    a = ap.parse_args()
    skip = set(a.skip.split(",")) if a.skip else set()
    rng = random.Random(7)

    os.makedirs(os.path.expanduser(a.log), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    lp = os.path.join(os.path.expanduser(a.log), f"edges-{stamp}.log")
    log = open(lp, "a", buffering=1)
    results = {}

    def say(m):
        line = f"{time.strftime('%H:%M:%S')} {m}"
        print(line, flush=True)
        log.write(line + "\n")

    p = serial.Serial(a.port, a.baud, timeout=0.003, write_timeout=5)
    time.sleep(0.2)
    p.reset_input_buffer()

    def drain(quiet=0.05, limit=3.0):
        got = b""
        end = time.time() + limit
        last = time.time()
        while time.time() < end:
            d = p.read(max(1, p.in_waiting))
            if d:
                got += d
                last = time.time()
            elif time.time() - last > quiet:
                break
        return got

    def exchange(data, timeout=None):
        """Send data and read the echo at the same time (a writer thread), so the echo of a long burst never
        waits behind a blocked write; returns what came back (waits for len(data), then a straggler window)."""
        import threading
        err = []

        def writer():
            try:
                p.write(data)
                p.flush()
            except Exception as ex:
                err.append(ex)
        t = threading.Thread(target=writer)
        t.start()
        timeout = timeout if timeout is not None else 0.5 + len(data) * 20.0 / a.baud
        got = b""
        end = time.time() + timeout
        while len(got) < len(data) and time.time() < end:
            got += p.read(max(1, p.in_waiting))
        t.join(timeout=2.0)
        got += drain(quiet=0.004, limit=0.1)
        if err:
            say(f"write error: {err[0]}")
        return got

    def classify(sent, got):
        if got == sent:
            return None
        if len(got) == len(sent):
            i = next(k for k in range(len(sent)) if sent[k] != got[k])
            return f"corrupt at {i}: sent {sent[i]:02x} got {got[i]:02x}"
        i = next((k for k in range(min(len(sent), len(got))) if sent[k] != got[k]), min(len(sent), len(got)))
        kind = "dropped" if len(got) < len(sent) else "extra"
        return f"{kind} (sent {len(sent)}, got {len(got)}), first difference at index {i}"

    def record(name, ok, detail):
        results[name] = {"ok": ok, "detail": detail}
        say(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    say(f"edges start: {a.port} {a.baud} baud, log {lp}")
    stray = drain(limit=0.5)
    if stray:
        say(f"note: {len(stray)} stray bytes before starting: {stray[:40]!r}")

    if "all256" not in skip:
        bad = []
        for v in range(256):
            for n in (1, 16):
                s = bytes([v]) * n
                e = classify(s, exchange(s))
                if e:
                    bad.append((v, n, e))
        record("all256", not bad, "all 256 values x {1, 16 bytes} echoed intact" if not bad else f"{len(bad)} faults, first {bad[0]}")

    if "patterns" not in skip:
        bad = []
        for v in (0x00, 0xFF, 0x55, 0xAA, 0x80, 0x01, 0xF0, 0x0F):
            for _ in range(200):
                s = bytes([v]) * 128
                e = classify(s, exchange(s))
                if e:
                    bad.append((v, e))
        record("patterns", not bad, "8 patterns x 200 bursts of 128 intact" if not bad else f"{len(bad)} faults, first {bad[0]}")

    if "longburst" not in skip:
        for n in (1024, 4096, 16384, 65536):
            faults = []
            for r in range(a.reps):
                s = bytes(rng.getrandbits(8) for _ in range(n))
                e = classify(s, exchange(s))
                if e:
                    faults.append(e)
                    drain(quiet=0.2, limit=2.0)
            record(f"longburst_{n}", not faults,
                   f"{a.reps} x {n} bytes at line rate intact" if not faults else f"{len(faults)}/{a.reps} faulted, first: {faults[0]}")

    if "idle" not in skip:
        bad = []
        for g in [float(x) for x in a.idle.split(",")]:
            time.sleep(g)
            stray = drain(quiet=0.01, limit=0.2)
            if stray:
                bad.append(f"stray bytes after {g}s idle: {stray[:20]!r}")
            for _ in range(3):
                s = bytes(rng.getrandbits(8) for _ in range(8))
                e = classify(s, exchange(s))
                if e:
                    bad.append(f"after {g}s idle: {e}")
        record("idle", not bad, "quiet for " + a.idle + " s, then 3 x 8 bytes intact each time" if not bad else "; ".join(bad[:3]))

    if "latency" not in skip:
        ts = []
        bad = 0
        for _ in range(2000):
            b = bytes([rng.getrandbits(8)])
            t = time.perf_counter()
            p.write(b)
            got = b""
            while len(got) < 1 and time.perf_counter() - t < 0.2:
                got += p.read(1)
            ts.append((time.perf_counter() - t) * 1000)
            if got != b:
                bad += 1
        ts.sort()
        record("latency", bad == 0,
               f"single-byte round trip ms: min {ts[0]:.2f}, median {statistics.median(ts):.2f}, p99 {ts[int(len(ts) * .99)]:.2f}, max {ts[-1]:.2f}; {bad} wrong of 2000")

    if "break" not in skip:
        notes = []
        ok = True
        for ms in (1, 10, 100):
            drain(limit=0.3)
            try:
                p.send_break(duration=ms / 1000.0)
            except Exception as ex:
                notes.append(f"{ms} ms break not sent ({ex})")
                continue
            time.sleep(0.05)
            ph = drain(quiet=0.05, limit=1.0)
            s = bytes(rng.getrandbits(8) for _ in range(16))
            e = classify(s, exchange(s))
            notes.append(f"{ms} ms break: {len(ph)} phantom byte(s) {ph[:8]!r}; next 16 bytes " + ("intact" if not e else e))
            if e:
                ok = False
        record("break", ok, " | ".join(notes))

    json.dump(results, open(lp[:-4] + ".json", "w"), indent=2)
    say("edges end: " + ", ".join(f"{k}={'PASS' if v['ok'] else 'FAIL'}" for k, v in results.items()))
    return 0 if all(v["ok"] for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
