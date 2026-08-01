#!/usr/bin/env python3
"""amimcp — an MCP server that gives Claude hands on a real Amiga.

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0) to the client, and the
framed TCP protocol in ../PROTOCOL.md to `amiagent` running on the Amiga.

Standard library only, deliberately: no venv, no pip, no lockfile drifting out
of date between the day you set this up and the evening two years later when
the Amiga won't boot and you want help.

    AMIGA_HOST=192.168.1.42 AMIGA_TOKEN=secret python3 amimcp.py

    python3 amimcp.py --probe        # connectivity check, no MCP involved
"""

from __future__ import annotations

import base64
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import png
from amiga import QUALIFIERS, Amiga, AmigaError, AmigaUnreachable

SERVER_NAME = "amimcp"
SERVER_VERSION = "0.1.0"

# Newest first. We echo back whatever the client asks for if we know it, so a
# client pinned to an older revision keeps working.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = SUPPORTED_PROTOCOLS[0]

# A screenshot is worth sending in full; a log file is not. Reads above this
# get truncated with a note rather than blowing out the context window.
MAX_TEXT_BYTES = 256 * 1024


def log(msg: str) -> None:
    """Diagnostics go to stderr. stdout carries JSON-RPC and nothing else."""
    print(f"[amimcp] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "amiga_shell",
        "description": (
            "Run an AmigaDOS command on the Amiga and return its return code and "
            "stdout. This is the workhorse: use it for Dir, List, Type, Copy, "
            "Delete, MakeDir, Assign, Version, Avail, Status, running programs, "
            "and anything else you would type at a Shell prompt. Only stdout is "
            "captured — if a command writes to stderr, append a redirect. "
            "The agent runs one command at a time and blocks until it finishes, "
            "so avoid interactive programs and anything that waits for input."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The AmigaDOS command line, e.g. 'List SYS:C' or 'Version FULL'.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait for the command to finish. Default 120.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "amiga_read_file",
        "description": (
            "Read a file from the Amiga. Use this for Startup-Sequence, "
            "User-Startup, .info files, logs, source, or any binary you want to "
            "inspect. Text is returned inline; binary is returned base64-encoded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "AmigaDOS path, e.g. 'S:Startup-Sequence' or 'Work:src/main.c'.",
                },
                "encoding": {
                    "type": "string",
                    "enum": ["auto", "text", "base64"],
                    "description": "How to return the bytes. 'auto' (default) picks text unless the file looks binary.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "amiga_write_file",
        "description": (
            "Write a file to the Amiga, creating or overwriting it. Use this to "
            "push a fixed Startup-Sequence, a config file, a script, or a "
            "cross-compiled binary. The parent drawer must already exist — "
            "create it with amiga_shell 'MakeDir' first if needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination AmigaDOS path."},
                "content": {"type": "string", "description": "File contents."},
                "encoding": {
                    "type": "string",
                    "enum": ["text", "base64"],
                    "description": "How 'content' is encoded. Default 'text'. Use 'base64' for binaries.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "amiga_list_dir",
        "description": (
            "List a drawer on the Amiga with each entry's type, size, protection "
            "bits and datestamp. Structured and easier to read than parsing the "
            "output of 'List'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Drawer to list, e.g. 'SYS:' , 'DEVS:' or 'Work:projects'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "amiga_screenshot",
        "description": (
            "Capture the frontmost Amiga screen as an image. Use this to see GUI "
            "state, a Guru Meditation, a rendering bug, or simply what the machine "
            "is currently showing. Works on both planar and RTG/truecolor screens: "
            "it uses the agent's built-in capture where it can, and falls back to "
            "C:sgrab on the Amiga otherwise. A full 1080p screen is around 1.4 MB "
            "as PNG — pass format 'jpeg' if you only need to read the layout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["auto", "native", "sgrab"],
                    "description": "'auto' (default) tries the agent's own capture then falls back to SGrab.",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                    "description": "SGrab output format. Default 'png' — lossless, best for reading text.",
                },
                "quality": {
                    "type": "number",
                    "description": "JPEG quality 1-100 when format is 'jpeg'. Default 85. JPEG needs jpeg.library installed on the Amiga.",
                },
            },
        },
    },
    {
        "name": "amiga_click",
        "description": (
            "Move the Amiga's mouse pointer to a screen coordinate and click. "
            "Coordinates are absolute pixels on the frontmost screen, so take a "
            "screenshot first and read them off it. Use count 2 for a "
            "double-click (opening an icon), and button 'right' to reach the "
            "Intuition menu bar. This drives the real machine — the pointer "
            "physically moves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "Absolute screen X in pixels."},
                "y": {"type": "number", "description": "Absolute screen Y in pixels."},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "Default 'left'."},
                "count": {"type": "number", "description": "1 for a single click, 2 to double-click. Default 1."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "amiga_move_mouse",
        "description": (
            "Move the Amiga's mouse pointer without clicking. Useful to hover, "
            "or to park the pointer somewhere harmless before a screenshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "Absolute screen X in pixels."},
                "y": {"type": "number", "description": "Absolute screen Y in pixels."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "amiga_type",
        "description": (
            "Type text on the Amiga as if at the keyboard. Characters are mapped "
            "through the Amiga's own keymap, so accented and national characters "
            "come out right on a German or other non-US layout. Goes to whatever "
            "window currently has focus — click there first. For Return, Escape, "
            "function keys and the like, use amiga_key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "amiga_key",
        "description": (
            "Press a single non-printable key, optionally with qualifiers. Named "
            "keys: return, enter, esc, tab, backspace, delete, help, space, "
            "up/down/left/right, f1-f10. Qualifiers: shift, ctrl, alt, amiga "
            "(and l/r variants). For example key 'return' to confirm a requester, "
            "or key 'q' is not valid — printable characters go through amiga_type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name, e.g. 'return' or 'f1'."},
                "qualifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modifiers held during the press, e.g. ['ctrl','lamiga'].",
                },
            },
            "required": ["key"],
        },
    },
    {
        "name": "amiga_system_info",
        "description": (
            "Report what this Amiga actually is: Kickstart and dos.library "
            "versions, CPU and FPU, free Chip/Fast RAM, largest free block, "
            "mounted volumes, and the agent's current directory. Call this first "
            "when you do not yet know the target."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    # Amiga text is latin-1 with LF line ends; a sprinkling of control bytes
    # beyond tab/newline/CR/formfeed/escape means it isn't text.
    allowed = {0x09, 0x0A, 0x0C, 0x0D, 0x1B}
    ctrl = sum(1 for b in data[:4096] if b < 0x20 and b not in allowed)
    return ctrl > len(data[:4096]) * 0.02


def tool_shell(ami: Amiga, args: dict) -> list[dict]:
    command = args["command"]
    timeout = float(args.get("timeout", 120))
    rc, out = ami.exec_command(command, timeout=timeout)
    header = f"$ {command}\nreturn code: {rc}"
    if rc != 0:
        header += "  (non-zero — the command reported a failure)"
    body = out.rstrip("\n")
    text = f"{header}\n\n{body}" if body else f"{header}\n\n(no output)"
    return [{"type": "text", "text": text}]


def tool_read_file(ami: Amiga, args: dict) -> list[dict]:
    path = args["path"]
    encoding = args.get("encoding", "auto")
    data = ami.read_file(path)

    if encoding == "auto":
        encoding = "base64" if _looks_binary(data) else "text"

    if encoding == "base64":
        return [{
            "type": "text",
            "text": f"{path} — {len(data)} bytes, base64:\n\n"
                    + base64.b64encode(data).decode("ascii"),
        }]

    truncated = ""
    if len(data) > MAX_TEXT_BYTES:
        truncated = (
            f"\n\n[truncated: showing the first {MAX_TEXT_BYTES} of {len(data)} bytes]"
        )
        data = data[:MAX_TEXT_BYTES]
    return [{
        "type": "text",
        "text": f"{path}:\n\n" + data.decode("latin-1") + truncated,
    }]


def tool_write_file(ami: Amiga, args: dict) -> list[dict]:
    path = args["path"]
    encoding = args.get("encoding", "text")
    if encoding == "base64":
        data = base64.b64decode(args["content"])
    else:
        data = args["content"].encode("latin-1", "replace")
    ami.write_file(path, data)
    return [{"type": "text", "text": f"Wrote {len(data)} bytes to {path}."}]


def tool_list_dir(ami: Amiga, args: dict) -> list[dict]:
    path = args["path"]
    entries = ami.list_dir(path)
    if not entries:
        return [{"type": "text", "text": f"{path} is empty."}]

    lines = [f"{path} — {len(entries)} entries", ""]
    width = max(len(e.name) for e in entries)
    for e in entries:
        size = "(dir)" if e.is_dir else str(e.size)
        lines.append(f"{e.name:<{width}}  {size:>10}  {e.protection}  {e.date}")
    return [{"type": "text", "text": "\n".join(lines)}]


GRAB_TMP = "RAM:amimcp-grab"


def _grab_with_sgrab(ami: Amiga, fmt: str, quality: int) -> tuple[bytes, str, str]:
    """Capture via SGrab on the Amiga, then fetch the file.

    SGrab writes real PNG/JPEG itself, which is how RTG and truecolor screens
    get captured at all — the agent's own SHOT command only handles planar
    screens of 256 colours or fewer. It grabs and exits, so this runs
    synchronously and the return code tells us whether it worked.
    """
    ext = "png" if fmt == "png" else "jpg"
    path = f"{GRAB_TMP}.{ext}"
    opts = "PNG" if fmt == "png" else f"JPG Q={quality}"

    ami.exec_command(f"Delete {path} QUIET", timeout=30)
    rc, out = ami.exec_command(f"C:sgrab {opts} {path}", timeout=180)
    if rc != 0:
        raise AmigaError(
            f"SGrab failed (rc={rc}): {out.strip() or 'no output'}. "
            "Is C:sgrab installed?"
        )

    # SGrab exits 0 even when it refuses to do the job — a missing
    # jpeg.library, for instance. The only reliable signal is whether the file
    # exists, so surface what SGrab actually said rather than a bare DOS 205.
    try:
        data = ami.read_file(path)
    except AmigaError as e:
        said = out.strip()
        raise AmigaError(
            f"SGrab wrote no file. It said: {said or '(nothing)'}"
            if said else f"SGrab wrote no file ({e})"
        ) from e
    try:
        ami.exec_command(f"Delete {path} QUIET", timeout=30)
    except (AmigaError, AmigaUnreachable):
        pass  # the grab is what matters; a stray temp file in RAM: is not

    if fmt == "png" and not data.startswith(b"\x89PNG"):
        raise AmigaError(f"SGrab did not produce a PNG ({len(data)} bytes)")
    if fmt == "jpeg" and not data.startswith(b"\xff\xd8"):
        raise AmigaError(f"SGrab did not produce a JPEG ({len(data)} bytes)")

    mime = "image/png" if fmt == "png" else "image/jpeg"
    return data, mime, f"via C:sgrab, {len(data) // 1024} KiB"


def tool_screenshot(ami: Amiga, args: dict) -> list[dict]:
    method = args.get("method", "auto")
    fmt = args.get("format", "png")
    quality = int(args.get("quality", 85))
    native_err = None

    # The agent's built-in capture is faster and needs nothing installed, but
    # only understands planar screens. Try it first, fall back to SGrab.
    if method in ("auto", "native"):
        try:
            shot = ami.screenshot()
            data = png.screenshot_png(shot)
            kind = "truecolor" if shot.palette is None else f"{len(shot.palette)}-colour"
            return [
                {"type": "text",
                 "text": f"Frontmost screen: {shot.width}x{shot.height}, {kind} (native capture)."},
                {"type": "image",
                 "data": base64.b64encode(data).decode("ascii"),
                 "mimeType": "image/png"},
            ]
        except AmigaError as e:
            if method == "native":
                raise
            native_err = str(e)

    data, mime, note = _grab_with_sgrab(ami, fmt, quality)
    lead = "Frontmost screen"
    if native_err:
        lead += " (native capture unavailable, so used SGrab)"
    return [
        {"type": "text", "text": f"{lead} — {note}."},
        {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime},
    ]


def tool_system_info(ami: Amiga, args: dict) -> list[dict]:
    info = ami.system_info()
    if not info:
        return [{"type": "text", "text": "The agent returned no system info."}]
    width = max(len(k) for k in info)
    lines = [f"{k:<{width}}  {v}" for k, v in info.items()]
    return [{"type": "text", "text": "\n".join(lines)}]


def _qualifier_bits(names) -> int:
    bits = 0
    for n in names or []:
        key = str(n).lower()
        if key not in QUALIFIERS:
            raise AmigaError(
                f"unknown qualifier {n!r} — known: {', '.join(sorted(set(QUALIFIERS)))}"
            )
        bits |= QUALIFIERS[key]
    return bits


def tool_click(ami: Amiga, args: dict) -> list[dict]:
    x, y = int(args["x"]), int(args["y"])
    button = args.get("button", "left")
    count = int(args.get("count", 1))
    ami.input_click(x, y, button, count)
    what = "double-clicked" if count == 2 else "clicked"
    return [{"type": "text", "text": f"{what} {button} at ({x}, {y})."}]


def tool_move_mouse(ami: Amiga, args: dict) -> list[dict]:
    x, y = int(args["x"]), int(args["y"])
    ami.input_move(x, y)
    return [{"type": "text", "text": f"Pointer moved to ({x}, {y})."}]


def tool_type(ami: Amiga, args: dict) -> list[dict]:
    text = args["text"]
    ami.input_text(text)
    return [{"type": "text", "text": f"Typed {len(text)} characters."}]


def tool_key(ami: Amiga, args: dict) -> list[dict]:
    key = args["key"]
    quals = _qualifier_bits(args.get("qualifiers"))
    # Press and release, so the key does not stay stuck down if anything later
    # in the turn fails.
    ami.input_key(key, down=True, qualifiers=quals)
    ami.input_key(key, down=False, qualifiers=quals)
    held = "+".join(args.get("qualifiers") or [])
    return [{"type": "text", "text": f"Pressed {held + '+' if held else ''}{key}."}]


HANDLERS = {
    "amiga_shell": tool_shell,
    "amiga_click": tool_click,
    "amiga_move_mouse": tool_move_mouse,
    "amiga_type": tool_type,
    "amiga_key": tool_key,
    "amiga_read_file": tool_read_file,
    "amiga_write_file": tool_write_file,
    "amiga_list_dir": tool_list_dir,
    "amiga_screenshot": tool_screenshot,
    "amiga_system_info": tool_system_info,
}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

class Server:
    def __init__(self, ami: Amiga):
        self.ami = ami
        self.protocol = DEFAULT_PROTOCOL

    def handle(self, msg: dict) -> dict | None:
        """Return a JSON-RPC response, or None for a notification."""
        method = msg.get("method")
        msg_id = msg.get("id")
        is_notification = msg_id is None

        try:
            if method == "initialize":
                result = self.on_initialize(msg.get("params") or {})
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.on_tools_call(msg.get("params") or {})
            elif method == "ping":
                result = {}
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None
            else:
                if is_notification:
                    return None
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
        except Exception as e:  # noqa: BLE001 — a crash here would kill the session
            log(f"error handling {method}: {e}\n{traceback.format_exc()}")
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)},
            }

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def on_initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOLS:
            self.protocol = requested
        return {
            "protocolVersion": self.protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                f"Tools for working directly on a real Amiga at {self.ami.host}. "
                "Call amiga_system_info first if you do not know what machine "
                "this is — Kickstart version and free RAM change what advice is "
                "correct. AmigaDOS paths use a colon after the volume or assign "
                "(SYS:, S:, Work:src/main.c) and are case-insensitive."
            ),
        }

    def on_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"No such tool: {name}"}],
                "isError": True,
            }

        # Tool failures come back as isError results rather than JSON-RPC
        # errors, so Claude sees the message and can adapt — an unreachable
        # Amiga is information, not a protocol fault.
        try:
            return {"content": handler(self.ami, args), "isError": False}
        except (AmigaError, AmigaUnreachable) as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # noqa: BLE001
            log(f"tool {name} failed: {traceback.format_exc()}")
            return {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            }

    def run(self) -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    log(f"ignoring unparseable line: {e}")
                    continue

                response = self.handle(msg)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
        except (BrokenPipeError, KeyboardInterrupt):
            # The client went away. That is a normal shutdown, not a crash —
            # exiting quietly keeps it out of the user's error log.
            log("client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_client() -> Amiga:
    host = os.environ.get("AMIGA_HOST", "")
    if not host:
        log("AMIGA_HOST is not set — every tool call will fail until it is.")
    return Amiga(
        host=host or "0.0.0.0",
        port=int(os.environ.get("AMIGA_PORT", "7846")),
        token=os.environ.get("AMIGA_TOKEN", ""),
        timeout=float(os.environ.get("AMIGA_TIMEOUT", "30")),
    )


def probe(ami: Amiga) -> int:
    """Connectivity check that bypasses MCP entirely."""
    print(f"Probing amiagent at {ami.host}:{ami.port} ...")
    try:
        print("  ping:", ami.ping().strip())
        info = ami.system_info()
        for k, v in info.items():
            print(f"  {k}: {v}")
    except (AmigaError, AmigaUnreachable) as e:
        print(f"  FAILED: {e}")
        return 1
    print("OK.")
    return 0


def main() -> int:
    ami = build_client()
    if "--probe" in sys.argv:
        return probe(ami)
    log(f"serving MCP over stdio; Amiga at {ami.host}:{ami.port}")
    Server(ami).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
