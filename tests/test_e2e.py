#!/usr/bin/env python3
"""End-to-end tests for amimcp against the fake agent.

Covers the wire protocol, the MCP layer, and the PNG encoder. Run with:

    python3 tests/test_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, HERE)

import amimcp  # noqa: E402
import fake_agent  # noqa: E402
import png  # noqa: E402
from amiga import Amiga, AmigaError, AmigaUnreachable, DirEntry  # noqa: E402


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    token = ""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="amimcp-test-")
        cls.port = free_port()
        cls.srv, _ = fake_agent.serve("127.0.0.1", cls.port, cls.root, cls.token)
        cls.ami = Amiga("127.0.0.1", cls.port, token=cls.token, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)


class TestWireProtocol(Base):
    def test_ping(self):
        self.assertIn("fake_agent", self.ami.ping())

    def test_system_info(self):
        info = self.ami.system_info()
        self.assertEqual(info["cpu"], "68030")
        self.assertEqual(info["kickstart"], "40.68")
        self.assertIn("Work", info["volumes"])

    def test_exec_success(self):
        rc, out = self.ami.exec_command("Version")
        self.assertEqual(rc, 0)
        self.assertIn("Kickstart", out)

    def test_exec_nonzero_return_code(self):
        rc, out = self.ami.exec_command("failme")
        self.assertEqual(rc, 10)
        self.assertIn("deliberate", out)

    def test_read_file(self):
        data = self.ami.read_file("S:Startup-Sequence")
        self.assertIn(b"SetPatch", data)

    def test_read_missing_file_raises_amiga_error(self):
        with self.assertRaises(AmigaError) as ctx:
            self.ami.read_file("S:NoSuchFile")
        self.assertIn("cannot open", str(ctx.exception))

    def test_write_then_read_roundtrip(self):
        payload = b"hello from the Mac\n\xc4\x00\xff binary bytes\n"
        self.ami.write_file("Work:test/roundtrip.dat", payload)
        self.assertEqual(self.ami.read_file("Work:test/roundtrip.dat"), payload)

    def test_write_large_file_streams(self):
        # Bigger than the agent's 8 KiB IO buffer, so this exercises the
        # streaming path rather than the prefetch.
        payload = bytes(range(256)) * 200  # 51200 bytes
        self.ami.write_file("Work:big.bin", payload)
        self.assertEqual(self.ami.read_file("Work:big.bin"), payload)

    def test_list_dir_sorts_dirs_first(self):
        entries = self.ami.list_dir("SYS:")
        names = [e.name for e in entries]
        self.assertIn("S", names)
        self.assertIn("C", names)
        self.assertTrue(entries[0].is_dir)

    def test_list_file_raises(self):
        with self.assertRaises(AmigaError):
            self.ami.list_dir("S:Startup-Sequence")

    def test_screenshot_shape(self):
        shot = self.ami.screenshot()
        self.assertEqual((shot.width, shot.height), (320, 200))
        self.assertEqual(len(shot.palette), 16)
        self.assertEqual(len(shot.pixels), 320 * 200)

    def test_unreachable_host_message_is_actionable(self):
        dead = Amiga("127.0.0.1", free_port(), timeout=2)
        with self.assertRaises(AmigaUnreachable) as ctx:
            dead.ping()
        self.assertIn("amiagent", str(ctx.exception))


class TestAuth(Base):
    token = "s3cret"

    def test_correct_token_works(self):
        self.assertIn("fake_agent", self.ami.ping())

    def test_missing_token_is_rejected(self):
        anon = Amiga("127.0.0.1", self.port, token="", timeout=5)
        with self.assertRaises(AmigaError) as ctx:
            anon.ping()
        self.assertIn("token", str(ctx.exception))

    def test_wrong_token_is_rejected(self):
        wrong = Amiga("127.0.0.1", self.port, token="nope", timeout=5)
        with self.assertRaises(AmigaError):
            wrong.ping()


class TestHungCommand(Base):
    """The 0.2.0 wedge: one stuck command parked the agent forever."""

    def tearDown(self):
        try:
            self.ami.break_command()
        except AmigaError:
            pass
        fake_agent.JOB.active = None

    def test_short_command_still_returns_normally(self):
        rc, out = self.ami.exec_command("sleep 0.1", timeout=10)
        self.assertEqual(rc, 0)
        self.assertIn("slept", out)

    def test_command_past_the_deadline_reports_instead_of_hanging(self):
        with self.assertRaises(AmigaError) as ctx:
            self.ami.exec_command("sleep 30", timeout=1)
        self.assertIn("still running", str(ctx.exception))
        self.assertIn("NOT stuck", str(ctx.exception))

    def test_agent_stays_responsive_while_a_command_is_stuck(self):
        with self.assertRaises(AmigaError):
            self.ami.exec_command("sleep 30", timeout=1)
        # The whole point: everything else keeps working.
        self.assertIn("fake_agent", self.ami.ping())
        self.assertEqual(self.ami.system_info()["cpu"], "68030")
        self.assertTrue(self.ami.list_dir("SYS:"))

    def test_second_command_is_refused_with_an_explanation(self):
        with self.assertRaises(AmigaError):
            self.ami.exec_command("sleep 30", timeout=1)
        with self.assertRaises(AmigaError) as ctx:
            self.ami.exec_command("Version", timeout=5)
        self.assertIn("still running", str(ctx.exception))
        self.assertIn("BREAK", str(ctx.exception))

    def test_break_frees_the_agent(self):
        with self.assertRaises(AmigaError):
            self.ami.exec_command("sleep 30", timeout=1)
        self.assertIn("Ctrl-C", self.ami.break_command())
        # Give the fake job a moment to notice, then commands work again.
        import time as _t
        for _ in range(200):
            try:
                rc, out = self.ami.exec_command("Version", timeout=5)
                break
            except AmigaError:
                _t.sleep(0.01)
        else:
            self.fail("agent never recovered after BREAK")
        self.assertEqual(rc, 0)
        self.assertIn("Kickstart", out)

    def test_break_with_nothing_running_is_a_clear_error(self):
        with self.assertRaises(AmigaError) as ctx:
            self.ami.break_command()
        self.assertIn("no command is running", str(ctx.exception))


class TestInput(Base):
    def setUp(self):
        fake_agent.INPUT_LOG.clear()

    def test_move(self):
        self.ami.input_move(320, 200)
        self.assertEqual(fake_agent.INPUT_LOG, [("move", 320, 200)])

    def test_click_defaults_to_single_left(self):
        self.ami.input_click(100, 50)
        self.assertEqual(fake_agent.INPUT_LOG, [("click", 100, 50, 0, 1)])

    def test_double_right_click(self):
        self.ami.input_click(10, 20, "right", 2)
        self.assertEqual(fake_agent.INPUT_LOG, [("click", 10, 20, 1, 2)])

    def test_unknown_button_rejected_client_side(self):
        with self.assertRaises(AmigaError):
            self.ami.input_click(0, 0, "elbow")
        self.assertEqual(fake_agent.INPUT_LOG, [])

    def test_relative_move(self):
        self.ami.input_move_rel(40, -25)
        self.assertEqual(fake_agent.INPUT_LOG, [("rmove", 40, -25)])

    def test_relative_move_carries_negatives(self):
        # The wire field is signed; an unsigned pack would send 65216 here and
        # the pointer would shoot off to the right instead of to the left.
        self.ami.input_move_rel(-320, -240)
        self.assertEqual(fake_agent.INPUT_LOG, [("rmove", -320, -240)])

    def test_home(self):
        self.ami.input_home()
        self.assertEqual(fake_agent.INPUT_LOG, [("home",)])

    def test_script_runs_events_in_order_in_one_request(self):
        # The whole point: press and release inside one request, so the gap
        # between them is the Amiga's own ticks rather than a TCP round trip.
        self.ami.input_script([("move", 10, 20), ("wait", 4),
                               ("button", "left", True), ("wait", 2),
                               ("button", "left", False)])
        self.assertEqual(fake_agent.INPUT_LOG, [
            ("script", 5), ("move", 10, 20), ("wait", 4),
            ("button", 0, 1), ("wait", 2), ("button", 0, 0),
        ])

    def test_click_at_is_atomic(self):
        self.ami.click_at(64, 32)
        self.assertEqual(fake_agent.INPUT_LOG[0], ("script", 5))
        self.assertIn(("move", 64, 32), fake_agent.INPUT_LOG)
        self.assertIn(("button", 0, 1), fake_agent.INPUT_LOG)
        self.assertIn(("button", 0, 0), fake_agent.INPUT_LOG)

    def test_click_at_relative_homes_first(self):
        # For programs that ignore pointer warps, the delta must be measured
        # from a known corner — so HOME has to come before the move.
        self.ami.click_at(30, 40, relative=True)
        kinds = [e[0] for e in fake_agent.INPUT_LOG]
        self.assertLess(kinds.index("home"), kinds.index("rmove"))

    def test_script_rejects_unknown_event_client_side(self):
        with self.assertRaises(AmigaError):
            self.ami.input_script([("teleport", 1, 2)])
        self.assertEqual(fake_agent.INPUT_LOG, [])

    def test_point_is_home_then_relative(self):
        # Absolute positioning for programs that ignore pointer warps: the
        # order matters, since the delta is measured from the corner.
        self.ami.input_point(160, 120)
        self.assertEqual(fake_agent.INPUT_LOG, [("home",), ("rmove", 160, 120)])

    def test_text(self):
        self.ami.input_text("Hallo Amiga")
        self.assertEqual(fake_agent.INPUT_LOG, [("text", "Hallo Amiga")])

    def test_named_key_maps_to_rawcode(self):
        self.ami.input_key("return", down=True, qualifiers=0x0008)
        self.assertEqual(fake_agent.INPUT_LOG, [("key", 0x44, 1, 0x0008)])

    def test_key_defaults_to_press_and_release(self):
        # A press with no release leaves the key held on the Amiga, which fails
        # silently and looks like something else entirely — a stuck Escape made
        # ScummVM ignore every mouse click for an entire session.
        self.ami.input_key("escape")
        self.assertEqual(
            fake_agent.INPUT_LOG,
            [("key", 0x45, 1, 0), ("key", 0x45, 0, 0)],
        )

    def test_explicit_down_still_sends_half_an_event(self):
        # Holding a modifier across other keys is a legitimate need.
        self.ami.input_key("f1", down=True)
        self.assertEqual(fake_agent.INPUT_LOG, [("key", 0x50, 1, 0)])

    def test_unknown_key_rejected_with_helpful_message(self):
        with self.assertRaises(AmigaError) as ctx:
            self.ami.input_key("banana")
        self.assertIn("Named keys", str(ctx.exception))

    def test_raw_int_keycode_passes_through(self):
        self.ami.input_key(0x50, down=False)
        self.assertEqual(fake_agent.INPUT_LOG, [("key", 0x50, 0, 0)])


class TestTruecolorScreenshot(Base):
    def setUp(self):
        fake_agent.TRUECOLOR = True

    def tearDown(self):
        fake_agent.TRUECOLOR = False

    def test_rgb24_decodes(self):
        shot = self.ami.screenshot()
        self.assertIsNone(shot.palette)
        self.assertEqual((shot.width, shot.height), (64, 32))
        self.assertEqual(len(shot.pixels), 64 * 32 * 3)

    def test_rgb24_encodes_to_truecolor_png(self):
        data = png.screenshot_png(self.ami.screenshot())
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (64, 32))
        self.assertEqual(data[25], 2)  # colour type 2 = truecolour


class TestPNG(unittest.TestCase):
    def _chunks(self, data: bytes) -> dict[bytes, bytes]:
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        out, off = {}, 8
        while off < len(data):
            (length,) = struct.unpack(">I", data[off : off + 4])
            tag = data[off + 4 : off + 8]
            body = data[off + 8 : off + 8 + length]
            crc = data[off + 8 + length : off + 12 + length]
            self.assertEqual(struct.unpack(">I", crc)[0], zlib.crc32(tag + body) & 0xFFFFFFFF,
                             f"bad CRC on {tag!r}")
            out[tag] = body
            off += 12 + length
        return out

    def test_indexed_png_structure(self):
        pal = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        pixels = bytes([0, 1, 2, 3, 3, 2, 1, 0])
        data = png.indexed_png(4, 2, pal, pixels)
        chunks = self._chunks(data)
        w, h, depth, ctype = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        self.assertEqual((w, h, depth, ctype), (4, 2, 8, 3))
        self.assertEqual(chunks[b"PLTE"], b"\x00\x00\x00\xff\x00\x00\x00\xff\x00\x00\x00\xff")
        # Each scanline is a 0 filter byte followed by the row's pixels.
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]),
                         b"\x00" + pixels[:4] + b"\x00" + pixels[4:])

    def test_rgb_png_structure(self):
        pixels = bytes([255, 0, 0, 0, 255, 0])
        data = png.rgb_png(2, 1, pixels)
        chunks = self._chunks(data)
        w, h, depth, ctype = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        self.assertEqual((w, h, depth, ctype), (2, 1, 8, 2))
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\x00" + pixels)

    def test_wrong_pixel_count_rejected(self):
        with self.assertRaises(ValueError):
            png.indexed_png(4, 2, [(0, 0, 0)], b"\x00")


class TestMCP(Base):
    def setUp(self):
        self.server = amimcp.Server(self.ami)

    def call(self, method, params=None, msg_id=1):
        return self.server.handle(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )

    def tool(self, name, args=None):
        r = self.call("tools/call", {"name": name, "arguments": args or {}})
        return r["result"]

    def test_initialize_echoes_known_protocol(self):
        r = self.call("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(r["result"]["serverInfo"]["name"], "amimcp")

    def test_initialize_falls_back_for_unknown_protocol(self):
        r = self.call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r["result"]["protocolVersion"], amimcp.DEFAULT_PROTOCOL)

    def test_tools_list_schemas_are_valid(self):
        tools = self.call("tools/list")["result"]["tools"]
        self.assertGreaterEqual(len(tools), 10)
        for t in tools:
            self.assertTrue(t["name"] and t["description"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            for req in t["inputSchema"].get("required", []):
                self.assertIn(req, t["inputSchema"]["properties"])
        # Every advertised tool must actually be dispatchable.
        self.assertEqual({t["name"] for t in tools}, set(amimcp.HANDLERS))

    def test_notification_gets_no_response(self):
        self.assertIsNone(self.server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method_is_jsonrpc_error(self):
        r = self.call("nope/nope")
        self.assertEqual(r["error"]["code"], -32601)

    def test_ping(self):
        self.assertEqual(self.call("ping")["result"], {})

    def test_shell_tool(self):
        res = self.tool("amiga_shell", {"command": "Version"})
        self.assertFalse(res["isError"])
        self.assertIn("Kickstart", res["content"][0]["text"])

    def test_shell_tool_flags_nonzero_rc(self):
        res = self.tool("amiga_shell", {"command": "failme"})
        self.assertFalse(res["isError"])  # the tool worked; the command didn't
        self.assertIn("non-zero", res["content"][0]["text"])

    def test_read_file_tool_auto_detects_text(self):
        res = self.tool("amiga_read_file", {"path": "S:Startup-Sequence"})
        self.assertIn("SetPatch", res["content"][0]["text"])
        self.assertNotIn("base64", res["content"][0]["text"])

    def test_read_file_tool_auto_detects_binary(self):
        res = self.tool("amiga_read_file", {"path": "C:Shell"})
        self.assertIn("base64", res["content"][0]["text"])

    def test_write_file_tool_base64(self):
        import base64 as b64

        payload = b"\x00\x01\x02binary\xff"
        res = self.tool("amiga_write_file", {
            "path": "Work:bin.dat",
            "content": b64.b64encode(payload).decode(),
            "encoding": "base64",
        })
        self.assertFalse(res["isError"])
        self.assertEqual(self.ami.read_file("Work:bin.dat"), payload)

    def test_list_dir_tool(self):
        res = self.tool("amiga_list_dir", {"path": "SYS:"})
        self.assertIn("S", res["content"][0]["text"])

    def test_system_info_tool(self):
        res = self.tool("amiga_system_info")
        self.assertIn("68030", res["content"][0]["text"])

    def test_break_tool(self):
        with self.assertRaises(AmigaError):
            self.ami.exec_command("sleep 30", timeout=1)
        res = self.tool("amiga_break")
        self.assertFalse(res["isError"])
        self.assertIn("Ctrl-C", res["content"][0]["text"])
        fake_agent.JOB.active = None

    def test_shell_tool_surfaces_a_stuck_command_as_is_error(self):
        with self.assertRaises(AmigaError):
            self.ami.exec_command("sleep 30", timeout=1)
        res = self.tool("amiga_shell", {"command": "Version", "timeout": 2})
        self.assertTrue(res["isError"])
        self.assertIn("still running", res["content"][0]["text"])
        self.ami.break_command(); fake_agent.JOB.active = None

    def test_click_tool(self):
        fake_agent.INPUT_LOG.clear()
        res = self.tool("amiga_click", {"x": 12, "y": 34, "button": "right", "count": 2})
        self.assertFalse(res["isError"])
        self.assertEqual(fake_agent.INPUT_LOG, [("click", 12, 34, 1, 2)])

    def test_type_tool(self):
        fake_agent.INPUT_LOG.clear()
        res = self.tool("amiga_type", {"text": "grussgott"})
        self.assertFalse(res["isError"])
        self.assertEqual(fake_agent.INPUT_LOG, [("text", "grussgott")])

    def test_key_tool_presses_and_releases(self):
        fake_agent.INPUT_LOG.clear()
        res = self.tool("amiga_key", {"key": "return", "qualifiers": ["ctrl", "lamiga"]})
        self.assertFalse(res["isError"])
        # down then up, both carrying the combined qualifier bits
        self.assertEqual(fake_agent.INPUT_LOG,
                         [("key", 0x44, 1, 0x48), ("key", 0x44, 0, 0x48)])

    def test_key_tool_rejects_unknown_qualifier(self):
        res = self.tool("amiga_key", {"key": "return", "qualifiers": ["hyper"]})
        self.assertTrue(res["isError"])
        self.assertIn("unknown qualifier", res["content"][0]["text"])

    def test_move_mouse_tool(self):
        fake_agent.INPUT_LOG.clear()
        self.tool("amiga_move_mouse", {"x": 5, "y": 6})
        self.assertEqual(fake_agent.INPUT_LOG, [("move", 5, 6)])

    def test_screenshot_tool_returns_valid_png(self):
        import base64 as b64

        res = self.tool("amiga_screenshot")
        self.assertFalse(res["isError"])
        image = next(c for c in res["content"] if c["type"] == "image")
        self.assertEqual(image["mimeType"], "image/png")
        data = b64.b64decode(image["data"])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (320, 200))

    def test_agent_error_becomes_is_error_result(self):
        res = self.tool("amiga_read_file", {"path": "S:Missing"})
        self.assertTrue(res["isError"])
        self.assertIn("cannot open", res["content"][0]["text"])

    def test_unreachable_amiga_becomes_is_error_result(self):
        server = amimcp.Server(Amiga("127.0.0.1", free_port(), timeout=2))
        res = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "amiga_system_info", "arguments": {}},
        })["result"]
        self.assertTrue(res["isError"])
        self.assertIn("cannot reach", res["content"][0]["text"])

    def test_unknown_tool_is_error_result_not_crash(self):
        res = self.tool("amiga_selfdestruct")
        self.assertTrue(res["isError"])

    def test_missing_required_argument_is_error_result(self):
        res = self.tool("amiga_shell", {})
        self.assertTrue(res["isError"])

    def test_responses_are_json_serialisable(self):
        for name in ("amiga_system_info", "amiga_screenshot"):
            json.dumps(self.tool(name))


class TestRegionCaptureAndProbes(Base):
    """The cheap-observation ops: region capture, hashing, screens, pointer."""

    def test_region_capture_returns_only_that_rectangle(self):
        s = self.ami.screenshot(x=40, y=50, w=64, h=32)
        self.assertEqual((s.width, s.height), (64, 32))
        self.assertEqual(len(s.pixels), 64 * 32)

    def test_region_is_a_genuine_crop_not_a_resize(self):
        # The same pixel must read the same whether captured whole or cropped —
        # otherwise a region is a different picture, not a cheaper one.
        full = self.ami.screenshot()
        crop = self.ami.screenshot(x=40, y=50, w=64, h=32)
        self.assertEqual(crop.pixels[0], full.pixels[50 * full.width + 40])
        self.assertEqual(crop.pixels[10 * 64 + 5],
                         full.pixels[(50 + 10) * full.width + (40 + 5)])

    def test_zero_width_means_out_to_the_edge(self):
        full = self.ami.screenshot()
        s = self.ami.screenshot(x=100, y=0, w=0, h=0)
        self.assertEqual(s.width, full.width - 100)

    def test_region_hash_is_stable_and_position_dependent(self):
        a = self.ami.region_hash(0, 0, 32, 16)
        self.assertEqual(a, self.ami.region_hash(0, 0, 32, 16))
        self.assertNotEqual(a, self.ami.region_hash(64, 48, 32, 16))

    def test_screens_lists_front_to_back(self):
        rows = self.ami.screens()
        self.assertEqual([r["index"] for r in rows], [0, 1])
        self.assertEqual(rows[0]["title"], "Game")
        self.assertEqual((rows[1]["width"], rows[1]["height"]), (1920, 1080))

    def test_pointer_reports_position_and_its_screen(self):
        p = self.ami.pointer()
        self.assertEqual((p["x"], p["y"]), (100, 50))
        self.assertEqual((p["screen_width"], p["screen_height"]), (640, 256))


class TestDragAndHold(Base):
    """Holding a button down — Intuition menus need it, and a click cannot."""

    def setUp(self):
        fake_agent.INPUT_LOG.clear()

    def test_drag_is_one_atomic_script(self):
        amimcp.tool_drag(self.ami, {"from_x": 10, "from_y": 20,
                                    "to_x": 100, "to_y": 60})
        kinds = [e[0] for e in fake_agent.INPUT_LOG]
        self.assertEqual(kinds[0], "script")          # one request, not three
        self.assertEqual(fake_agent.INPUT_LOG[1], ("move", 10, 20))
        # button goes down BEFORE the second move and up after it
        down = kinds.index("button")
        move2 = [i for i, e in enumerate(fake_agent.INPUT_LOG)
                 if e == ("move", 100, 60)][0]
        up = len(kinds) - 1 - kinds[::-1].index("button")
        self.assertLess(down, move2)
        self.assertLess(move2, up)

    def test_drag_right_button_for_menus(self):
        amimcp.tool_drag(self.ami, {"from_x": 5, "from_y": 5, "to_x": 5, "to_y": 40,
                                    "button": "right"})
        presses = [e for e in fake_agent.INPUT_LOG if e[0] == "button"]
        self.assertEqual([p[1] for p in presses], [1, 1])   # 1 == right
        self.assertEqual([p[2] for p in presses], [1, 0])   # down then up

    def test_drag_relative_homes_before_each_move(self):
        amimcp.tool_drag(self.ami, {"from_x": 30, "from_y": 40, "to_x": 80,
                                    "to_y": 90, "relative": True})
        kinds = [e[0] for e in fake_agent.INPUT_LOG]
        self.assertEqual(kinds.count("home"), 2)
        self.assertEqual(kinds.count("rmove"), 2)

    def test_button_hold_and_release(self):
        amimcp.tool_button(self.ami, {"down": True, "button": "right"})
        self.assertEqual(fake_agent.INPUT_LOG, [("button", 1, 1)])
        fake_agent.INPUT_LOG.clear()
        out = amimcp.tool_button(self.ami, {"down": False, "button": "right"})
        self.assertEqual(fake_agent.INPUT_LOG, [("button", 1, 0)])
        self.assertIn("released", out[0]["text"])

    def test_holding_warns_about_releasing(self):
        out = amimcp.tool_button(self.ami, {"down": True})
        self.assertIn("HELD DOWN", out[0]["text"])
        self.assertIn("amiga_button down=false", out[0]["text"])


class FakeSgrabAmiga:
    """Just enough Amiga for the SGrab fallback: a C: listing, EXEC and GET."""

    def __init__(self, has_exe: bool, payload: bytes = b"\x89PNG fake"):
        self.names = ["Dir", "List", "sgrab"] + (["SGrab.exe"] if has_exe else [])
        self.payload = payload
        self.commands: list[str] = []
        self.listed: list[str] = []

    def list_dir(self, path):
        self.listed.append(path)
        return [DirEntry(is_dir=False, size=1, protection="----rwed",
                         date="20-Aug-26", name=n) for n in self.names]

    def exec_command(self, command, timeout=120.0):
        self.commands.append(command)
        return 0, ""

    def read_file(self, path):
        return self.payload


class TestSgrabFallback(unittest.TestCase):
    """Which SGrab we shell out to, given yelworC's wrapper may own the name."""

    def test_plain_name_on_a_stock_install(self):
        ami = FakeSgrabAmiga(has_exe=False)
        self.assertEqual(amimcp._sgrab_command(ami), "C:sgrab")
        self.assertEqual(ami.listed, ["C:"])

    def test_a_c_that_will_not_list_leaves_the_old_behaviour(self):
        ami = FakeSgrabAmiga(has_exe=True)
        ami.list_dir = lambda path: (_ for _ in ()).throw(AmigaError("no lock"))
        self.assertEqual(amimcp._sgrab_command(ami), "C:sgrab")

    def test_binary_wins_where_the_arexx_wrapper_installed_it(self):
        ami = FakeSgrabAmiga(has_exe=True)
        self.assertEqual(amimcp._sgrab_command(ami), "C:sgrab.exe")

    def test_grab_runs_the_binary_not_the_wrapper(self):
        ami = FakeSgrabAmiga(has_exe=True)
        data, mime, note = amimcp._grab_with_sgrab(ami, "png", 85)
        grab = [c for c in ami.commands if "sgrab" in c.lower() and "Delete" not in c]
        self.assertEqual(grab, ["C:sgrab.exe PNG RAM:amimcp-grab.png"])
        self.assertEqual(mime, "image/png")
        self.assertIn("C:sgrab.exe", note)
        self.assertEqual(data, ami.payload)

    def test_grab_still_runs_plain_sgrab_where_there_is_no_binary(self):
        ami = FakeSgrabAmiga(has_exe=False)
        amimcp._grab_with_sgrab(ami, "png", 85)
        grab = [c for c in ami.commands if "sgrab" in c.lower() and "Delete" not in c]
        self.assertEqual(grab, ["C:sgrab PNG RAM:amimcp-grab.png"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
