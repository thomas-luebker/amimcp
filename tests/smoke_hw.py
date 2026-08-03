#!/usr/bin/env python3
"""Exercise every amiagent op against a REAL Amiga and print a pass/fail table.

The unit suite runs entirely against `fake_agent`, which is fast and needs no
hardware — and therefore cannot catch anything that only goes wrong on the
machine. A second agent instance that could not open `cybergraphics.library`,
while the first could, went unnoticed for exactly that reason.

    AMIGA_HOST=192.168.178.21 AMIGA_TOKEN=secret python3 tests/smoke_hw.py

Read-only by default. Pass --write to also exercise PUT/GET round-trips and
input injection, which move the pointer and create a file under T:.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from amiga import Amiga, AmigaError, AmigaUnreachable  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    """Run one probe. A failure is recorded, never fatal — the point is the
    whole table, not the first thing that broke."""
    start = time.time()
    try:
        note = fn() or ""
        RESULTS.append((name, True, f"{note}  [{time.time() - start:.1f}s]"))
    except (AmigaError, AmigaUnreachable, Exception) as e:  # noqa: BLE001
        RESULTS.append((name, False, f"{type(e).__name__}: {str(e)[:90]}"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("AMIGA_HOST", "192.168.178.21"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AMIGA_PORT", "7846")))
    ap.add_argument("--token", default=os.environ.get("AMIGA_TOKEN", ""))
    ap.add_argument("--write", action="store_true",
                    help="also test writes and input injection (moves the pointer)")
    args = ap.parse_args()

    a = Amiga(args.host, args.port, token=args.token, timeout=120)

    check("ping", lambda: a.ping().strip())
    check("system_info", lambda: f"cpu={a.system_info().get('cpu')}")
    check("exec", lambda: f"rc={a.exec_command('Version', timeout=30)[0]}")
    check("list_dir", lambda: f"{len(a.list_dir('SYS:'))} entries")

    check("screens", lambda: ", ".join(
        f"[{s['index']}] {s['width']}x{s['height']}d{s['depth']}" for s in a.screens()))
    check("pointer", lambda: (lambda p: f"({p['x']},{p['y']}) on "
                                        f"{p['screen_width']}x{p['screen_height']}")(a.pointer()))

    def full_shot():
        s = a.screenshot(timeout=300)
        return f"{s.width}x{s.height} {'truecolor' if s.palette is None else 'chunky'}"
    check("screenshot (full)", full_shot)

    def region_shot():
        t0 = time.time()
        s = a.screenshot(timeout=120, x=0, y=0, w=160, h=32)
        if (s.width, s.height) != (160, 32):
            raise AssertionError(f"asked for 160x32, got {s.width}x{s.height}")
        return f"160x32 in {time.time() - t0:.1f}s"
    check("screenshot (region)", region_shot)

    def hashing():
        h1 = a.region_hash(0, 0, 160, 32)
        h2 = a.region_hash(0, 0, 160, 32)
        if h1 != h2:
            raise AssertionError("same region hashed differently twice")
        return f"{h1:08x} stable"
    check("region_hash", hashing)

    # Capturing a screen behind the front one is the whole reason screen
    # indexing exists; skip cleanly when only one screen is open.
    def back_screen():
        rows = a.screens()
        if len(rows) < 2:
            return "only one screen open, skipped"
        s = a.screenshot(timeout=300, w=64, h=32, screen=1)
        return f"screen 1 -> {s.width}x{s.height}"
    check("screenshot (screen 1)", back_screen)

    if args.write:
        payload = b"amimcp smoke test\n\x00\xff binary\n"
        check("write+read roundtrip", lambda: (
            a.write_file("T:amimcp-smoke.tmp", payload),
            "identical" if a.read_file("T:amimcp-smoke.tmp") == payload else "MISMATCH",
        )[1])
        check("cleanup", lambda: f"rc={a.exec_command('Delete T:amimcp-smoke.tmp QUIET', timeout=30)[0]}")

        def input_probe():
            before = a.pointer()
            a.input_script([("move", 40, 40), ("wait", 6)])
            after = a.pointer()
            a.input_move(before["x"], before["y"])   # put it back
            return f"pointer {before['x']},{before['y']} -> {after['x']},{after['y']}"
        check("input_script + pointer", input_probe)

    width = max(len(n) for n, _, _ in RESULTS)
    print()
    for name, ok, note in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {note}")
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed"
          + (f" — failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
