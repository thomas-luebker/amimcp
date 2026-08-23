#!/usr/bin/env python3
"""Run amibench on one or more fleet machines and print rates instead of ticks.

    tools/amibench/benchrun.py 192.168.178.21 127.0.0.1
    tools/amibench/benchrun.py --runs 4 --cycles 3 192.168.178.178

Machines are measured **strictly one at a time**, in a fixed order, repeated for
`--cycles`. That is not fussiness: emulated guests are measurements of their
host's mood as much as their own speed, and running four at once — which is how
this started — makes every number a statement about the other three.

amibench prints reps and ticks, deliberately: doing the division on the Amiga
would drag in soft-float and the byte totals overflow 32 bits on a fast machine.
This does the arithmetic here — 50 ticks to the second, memory phases scaled by
the buffer size the binary reports.

The first run of a phase is often a cold-buffer outlier (the iPad's memset came
in 40% high once, then settled), so `--runs 4` and the median of the rest is the
honest default rather than a best-of.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "server"))

from amiga import Amiga  # noqa: E402

BINARY = os.path.join(HERE, "..", "..", "dist", "stage", "amibench", "amibench")
TICKS_PER_SEC = 50.0


def parse(out: str):
    """amibench's output → (bufsz, {phase: (reps, ticks)})."""
    bufsz, phases = None, {}
    for line in out.strip().split("\n"):
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "amibench" and "bufsz" in tok:
            bufsz = int(tok[tok.index("bufsz") + 1])
        elif len(tok) >= 5 and tok[1] == "reps":
            phases[tok[0]] = (int(tok[2]), int(tok[4]))
    return bufsz, phases


def rates(bufsz: int, phases: dict) -> dict:
    out = {}
    for name, (reps, ticks) in phases.items():
        secs = ticks / TICKS_PER_SEC
        out[name] = reps / secs if name == "int" else reps * bufsz / secs
    return out


def measure(host: str, token: str, runs: int) -> list[dict]:
    a = Amiga(host, token=token, timeout=300)
    # Identify who answered. Two emulators on one host both bind 7846, and the
    # loser exits quietly — leaving the winner to answer every probe. A whole
    # settings sweep was once thrown away for want of this line.
    print("    %s" % a.ping().strip().replace("\n", " "))
    rc, out = a.exec_command("List RAM: PAT amibench", timeout=30)
    if "amibench" not in out:
        data = open(BINARY, "rb").read()
        a.write_file("RAM:amibench", data)
        if a.read_file("RAM:amibench") != data:
            raise SystemExit("%s: amibench did not survive the copy" % host)
    results = []
    for _ in range(runs):
        rc, out = a.exec_command("RAM:amibench", timeout=200)
        bufsz, phases = parse(out)
        if not phases:
            raise SystemExit("%s: unparsable amibench output:\n%s" % (host, out))
        results.append(rates(bufsz, phases))
        time.sleep(2)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hosts", nargs="+")
    ap.add_argument("--token", default=os.environ.get("AMIGA_TOKEN", "a4000"))
    ap.add_argument("--runs", type=int, default=4, help="runs per cycle (first is discarded)")
    ap.add_argument("--cycles", type=int, default=1, help="repeat the whole fixed order N times")
    args = ap.parse_args()

    collected: dict[str, list[dict]] = {h: [] for h in args.hosts}
    for cycle in range(args.cycles):
        for host in args.hosts:
            print("cycle %d/%d  %s" % (cycle + 1, args.cycles, host))
            collected[host] += measure(host, args.token, args.runs)[1:]

    print("\n%-22s %12s %10s %10s %10s %9s" %
          ("host", "int M ops/s", "copy MB/s", "set MB/s", "read MB/s", "copy:int"))
    for host in args.hosts:
        runs = collected[host]
        if not runs:
            continue
        med = {k: statistics.median(r[k] for r in runs) for k in runs[0]}
        spread = (max(r["int"] for r in runs) - min(r["int"] for r in runs)) / med["int"] * 100
        print("%-22s %12.2f %10.0f %10.0f %10.0f %9.1f   (int spread %.1f%%, n=%d)" %
              (host, med["int"] / 1e6, med["copy"] / 1048576, med["set"] / 1048576,
               med["read"] / 1048576, (med["copy"] / 1048576) / (med["int"] / 1e6),
               spread, len(runs)))
    # copy:int is the shape of the machine, not its speed: ~6 on real hardware,
    # ~11 under a JIT, 40-66 interpreted. Data movement stays near host speed
    # while every instruction pays interpretation.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
