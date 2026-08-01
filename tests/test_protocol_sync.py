#!/usr/bin/env python3
"""Guard against the two halves of amimcp drifting apart.

agent/proto.h and server/amiga.py hand-maintain the same eleven constants.
That is the right call — a code generator would cost more than it saves — but
it needs a test, because a mismatch here fails silently and confusingly: the
Amiga would happily run the wrong command.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import amiga  # noqa: E402

PROTO_H = os.path.join(HERE, "..", "agent", "proto.h")


def read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def parse_defines(src: str) -> dict[str, int]:
    out = {}
    for line in src.splitlines():
        m = re.match(r"\s*#define\s+(\w+)\s+((?:0x)?[0-9A-Fa-f]+)\s*(?:/\*.*)?$", line)
        if m:
            out[m.group(1)] = int(m.group(2), 0)
    return out


class TestProtocolSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(PROTO_H)
        cls.c = parse_defines(cls.src)

    def test_header_length_matches(self):
        self.assertEqual(self.c["AMI_HDRLEN"], amiga.HDRLEN)

    def test_magic_matches(self):
        # proto.h spells the magic as char literals, which parse_defines skips.
        chars = re.findall(r"#define AMI_MAGIC\d '(.)'", self.src)
        self.assertEqual(len(chars), 4, "expected four AMI_MAGIC bytes")
        self.assertEqual("".join(chars).encode(), amiga.MAGIC)

    def test_input_ops_match(self):
        for name in ("MOVE", "BUTTON", "KEY", "TEXT", "CLICK"):
            self.assertEqual(
                self.c[f"IN_{name}"],
                getattr(amiga, f"IN_{name}"),
                f"IN_{name} differs between proto.h and amiga.py",
            )

    def test_mouse_buttons_match(self):
        for name, py in (("LEFT", "left"), ("RIGHT", "right"), ("MIDDLE", "middle")):
            self.assertEqual(self.c[f"IN_BTN_{name}"], amiga.BUTTONS[py])

    def test_command_codes_match(self):
        for name in ("PING", "EXEC", "GET", "PUT", "LIST", "INFO", "SHOT", "INPUT", "AUTH"):
            self.assertEqual(
                self.c[f"CMD_{name}"],
                getattr(amiga, f"CMD_{name}"),
                f"CMD_{name} differs between proto.h and amiga.py",
            )

    def test_status_codes_match(self):
        for name in ("OK", "ERR", "AUTH"):
            self.assertEqual(
                self.c[f"ST_{name}"],
                getattr(amiga, f"ST_{name}"),
                f"ST_{name} differs between proto.h and amiga.py",
            )

    def test_screenshot_formats_match(self):
        self.assertEqual(self.c["SHOT_CHUNKY"], amiga.SHOT_CHUNKY)
        self.assertEqual(self.c["SHOT_RGB24"], amiga.SHOT_RGB24)

    def test_frame_cap_matches(self):
        m = re.search(r"#define AMI_MAXFRAME\s+\((\d+)UL \* (\d+)UL \* (\d+)UL\)", self.src)
        self.assertIsNotNone(m, "AMI_MAXFRAME is not in the expected form")
        a, b, c = (int(g) for g in m.groups())
        self.assertEqual(a * b * c, amiga.MAXFRAME)

    def test_every_command_constant_is_handled_by_the_daemon(self):
        src = read(os.path.join(HERE, "..", "agent", "amiagent.c"))
        for name in ("PING", "EXEC", "GET", "PUT", "LIST", "INFO", "SHOT", "INPUT"):
            self.assertIn(f"case CMD_{name}:", src, f"amiagent.c never handles CMD_{name}")
        self.assertIn("code == CMD_AUTH", src, "amiagent.c never handles CMD_AUTH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
