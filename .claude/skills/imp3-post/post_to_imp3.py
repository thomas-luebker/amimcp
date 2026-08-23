#!/usr/bin/env python3
"""Post lines into the imp3 chat from a fleet Amiga, via amiagent's ARexx port.

Deliberately two-phase, because a chat message cannot be unsent:

    post_to_imp3.py --check                     is imp3 up, and where is its window
    post_to_imp3.py --stage "first line"        type it WITHOUT sending; screenshot it
    post_to_imp3.py --send  "l2" "l3" ...       press enter on the staged line, then
                                                type and send the rest

Look at the screenshot the --stage step writes before you --send. The A4000 runs a
German keymap; ENTERTEXT goes through the Amiga's own keymap so "|" and "/" survive,
but that is a thing to verify while the text is still private, not to assume.

Channel selection is NOT handled here on purpose — imp3 highlights tabs for unread
activity as well as selection, so the active tab cannot be read back. Have a human
select the channel first.
"""

import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "server"))

from amiga import Amiga  # noqa: E402
import png  # noqa: E402


def chat_window(a):
    """Return the (x, y, w, h) of the imp3 chat window, or None.

    Status cannot answer this: imp3 is Workbench-started and has no CLI number.
    The window list is the only proof it is running.
    """
    for line in a.ui_tree().split("\n"):
        if not line.startswith("W ") or "CHAT --" not in line:
            continue
        # W <idx> <x> <y> <w>x<h> <state> "<title>"
        parts = line.split()
        x, y = int(parts[2]), int(parts[3])
        w, h = (int(v) for v in parts[4].split("x"))
        return x, y, w, h
    return None


def rexx(a, command):
    rc, res = a.arexx("address AMIAGENT\n'%s'\n" % command)
    if rc != 0:
        raise SystemExit("ARexx %r failed rc=%d %s" % (command, rc, res))
    return res


def shoot(a, box, path):
    x, y, w, h = box
    shot = a.screenshot(x=x, y=y, w=w, h=h)
    with open(path, "wb") as fh:
        fh.write(png.screenshot_png(shot))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("AMIGA_HOST", "192.168.178.21"))
    ap.add_argument("--token", default=os.environ.get("AMIGA_TOKEN", "a4000"))
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "imp3-input.png"),
                    help="where --stage writes its screenshot")
    ap.add_argument("--verify-out", default=os.path.join(tempfile.gettempdir(), "imp3-posted.png"),
                    help="where --send writes its confirmation screenshot")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between messages")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stage", metavar="LINE")
    ap.add_argument("--send", nargs="*", metavar="LINE")
    args = ap.parse_args()

    a = Amiga(args.host, token=args.token, timeout=60)
    box = chat_window(a)
    if box is None:
        raise SystemExit("no imp3 CHAT window on %s — is imp3 running there?" % args.host)
    x, y, w, h = box
    input_box = (x, y + h - 40, w, 40)     # the input line sits at the window's foot
    pane_box = (x, y + h - 190, w, 190)    # the newest messages

    if args.check or (not args.stage and args.send is None):
        print("imp3 chat window at %d,%d %dx%d on %s" % (x, y, w, h, args.host))
        print("agent:", a.ping().strip())
        print("NOTE: which channel tab is selected cannot be read back — ask a human.")
        return

    if args.stage:
        rexx(a, 'ACTIVATEWINDOW "CHAT --*"')
        rexx(a, 'ENTERTEXT "%s"' % args.stage)
        time.sleep(1)
        print("staged, NOT sent. Check:", shoot(a, input_box, args.out))
        return

    rexx(a, 'ACTIVATEWINDOW "CHAT --*"')
    rexx(a, 'KEY "enter"')                 # sends whatever --stage left in the field
    time.sleep(args.delay)
    for line in args.send:
        rexx(a, 'ENTERTEXT "%s"' % line)
        rexx(a, 'KEY "enter"')
        print("sent:", line)
        time.sleep(args.delay)
    time.sleep(2)
    print("posted. Verify:", shoot(a, pane_box, args.verify_out))


if __name__ == "__main__":
    main()
