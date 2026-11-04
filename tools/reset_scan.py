#!/usr/bin/env python3
"""Reset regression scan: does a program restart cleanly when the Cu reset lands at any moment?

A RAM that is also the program store is not reloaded by a reset, so any state a program keeps in
RAM can break the next start (Zephyr's .data was found this way, notes 2026-11-04: a reset while
main slept in k_msleep left a stale timeout_list and the restart died, but only at some moments).
One reset at one moment proves little; this runs vsim once without a reset (the baseline: what
the program prints), then once per reset time, the times spaced by a step that is not a multiple
of any loop period so that every phase of the program gets hit.

  python3 tools/reset_scan.py [--dips HEX] [--then HEX] [--time 1.0] [--from 0.30] [--step 0.00037] [--n 24]
  --then: the switches change to HEX while reset is held, so the program that starts is a different one
  (the boot menu's selection) and runs on the previous program's leftover RAM.

A run passes when the baseline's first line appears again after the reset (so the program
restarted and printed it complete; a stray byte in front of it is allowed, a reset can leave one), more output follows when the baseline had some after its first
line, and there is no Zephyr/trap dump ("mcause"). Needs a built project (`alchitry build`, vsim
compiles build/source) and a program that prints on the serial port. Exit 1 if any run fails.
Runs vsim from the current directory's checkout. Output is read as bytes: a stray byte makes
GNU/ugrep treat vsim's output as binary, so do not grep it without -a.
"""
import argparse
import subprocess
import sys


def vsim(dips, total, resets, then=None):
    cmd = ["vsim", "--dips", dips, "--headless", f"{total:.6f}s"]
    for t in resets:
        cmd += ["--at", f"{t:.6f}s", "reset"]
        if then:    # the switches change while reset is held (the click is held 100 ms)
            cmd += ["--at", f"{t + 0.02:.6f}s", f"dips={then}"]
    out = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    return [l.strip().decode("latin1") for l in out.split(b"\n")
            if l.strip() and not l.startswith(b"vsim:")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dips", default="400100", help="io_dip[23:0] hex (default: fast clock, middle bank 1)")
    ap.add_argument("--then", default=None, help="DIPs to change to while reset is held: the PREVIOUS program's leftovers meet a different program (boot menu selection)")
    ap.add_argument("--time", type=float, default=1.0, help="board seconds per run")
    ap.add_argument("--from", dest="t0", type=float, default=0.30, help="first reset time (s)")
    ap.add_argument("--step", type=float, default=0.00037, help="spacing of reset times (s)")
    ap.add_argument("--n", type=int, default=24, help="number of reset times")
    a = ap.parse_args()

    base = vsim(a.then or a.dips, a.time, [])      # what the program that runs after the reset prints
    if not base:
        sys.exit("baseline printed nothing: this program does not use the serial port")
    first = base[0]
    after_base = len(base) - 1
    print(f"baseline: {len(base)} lines, first {first!r}")
    bad = 0
    for i in range(a.n):
        t = a.t0 + i * a.step
        lines = vsim(a.dips, a.time, [t], a.then)
        idx = [k for k, l in enumerate(lines) if l.endswith(first)]   # a reset can leave a stray byte in front
        fatal = any("mcause" in l for l in lines)
        need = 1 if a.then else 2     # with --then the first program prints something else, so one hit is the restart
        ok = len(idx) >= need and not fatal and (len(lines) - 1 - idx[need - 1] >= min(2, after_base) if len(idx) >= need else False)
        if not ok:
            bad += 1
        print(f"  reset at {t:.5f}s: first line x{len(idx)}"
              f"{', FATAL DUMP' if fatal else ''}"
              f"{', ' + str(len(lines) - 1 - idx[need - 1]) + ' lines after the restart' if len(idx) >= need else ''}"
              f"  {'ok' if ok else 'FAIL'}")
    print(f"{a.n - bad}/{a.n} restarts clean")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
