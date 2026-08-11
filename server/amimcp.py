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
            "Run an AmigaDOS command on the Amiga and return its return code, "
            "stdout and stderr. This is the workhorse: use it for Dir, List, "
            "Type, Copy, Delete, MakeDir, Assign, Version, Avail, Status, "
            "running programs, and anything else you would type at a Shell "
            "prompt. The agent runs the command in a child process and gives up "
            "waiting after `timeout` seconds, reporting that it is still "
            "running — it does not hang. Only one command runs at a time, so a "
            "stuck one blocks later commands until you call amiga_break."
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
                    "description": "Seconds the agent waits before reporting the command is still running. Default 120.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "amiga_arexx",
        "description": (
            "Run an ARexx program on the Amiga and return its return code and "
            "RESULT. The `source` is the ARexx program itself (not a script "
            "filename), so it can be a one-liner or several statements. This is "
            "how you drive ARexx-aware applications: `address 'DOPUS.1'` then a "
            "command talks to Directory Opus; likewise editors, comms programs, "
            "players, and many others expose ARexx ports. Needs ARexx running "
            "(RexxMast) on the Amiga; a non-zero rc is the ARexx error code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The ARexx program, e.g. \"return 6*7\" or \"address 'DOPUS.1'; 'command'\".",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait for the ARexx program. Default 60.",
                },
            },
            "required": ["source"],
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
                "x": {"type": "number", "description": "Left edge of a sub-rectangle to capture."},
                "y": {"type": "number", "description": "Top edge of a sub-rectangle to capture."},
                "w": {"type": "number", "description": "Width of the rectangle; 0 or omitted means out to the screen edge."},
                "h": {"type": "number", "description": "Height of the rectangle; 0 or omitted means out to the screen edge."},
                "screen": {
                    "type": "number",
                    "description": (
                        "Which screen to capture, 0 = frontmost (default). Use amiga_screens "
                        "to list them. Needed when a crashed program leaves a blank screen in "
                        "front of a working Workbench."
                    ),
                },
            },
        },
    },
    {
        "name": "amiga_screens",
        "description": (
            "List the Amiga's open screens, front to back, with dimensions, depth "
            "and title. Use this before capturing or clicking on an unfamiliar "
            "display: it tells you the real geometry instead of guessing, and it "
            "reveals a healthy Workbench hiding behind a crashed program's screen."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "amiga_pointer",
        "description": (
            "Report where the Amiga's mouse pointer currently is, and the size of "
            "the screen it is on. Cheap — no image is transferred. Use it to check "
            "the pointer landed where you aimed before clicking. Note this is "
            "Intuition's pointer: a program that tracks raw mouse deltas (SDL "
            "games, ScummVM) keeps its own cursor that this cannot see."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "amiga_region_changed",
        "description": (
            "Return a checksum of a screen region without transferring the pixels. "
            "Call it twice and compare to answer 'has anything changed yet?' — the "
            "right way to wait for a program to finish drawing, a cutscene to end, "
            "or a status line to update, instead of pulling whole screenshots."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "w": {"type": "number"}, "h": {"type": "number"},
                "screen": {"type": "number", "description": "0 = frontmost (default)."},
            },
        },
    },
    {
        "name": "amiga_break",
        "description": (
            "Send Ctrl-C to a command left running by amiga_shell — the fix for "
            "a program that is waiting for input nobody will give it. Only one "
            "command runs at a time, so use this when amiga_shell reports that "
            "one is still running. Best effort: well-behaved AmigaDOS commands "
            "stop, but a program ignoring Ctrl-C will need attention at the "
            "machine itself."
        ),
        "inputSchema": {"type": "object", "properties": {}},
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
                "relative": {
                    "type": "boolean",
                    "description": (
                        "Position by homing the pointer to the corner and moving by a "
                        "delta, rather than warping it. Needed for SDL programs and "
                        "games (ScummVM), which keep their own cursor and never see a "
                        "warp — a warp-then-click there lands wherever that program "
                        "last thought the pointer was, silently clicking the wrong thing."
                    ),
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "amiga_move_mouse",
        "description": (
            "Move the Amiga's mouse pointer without clicking. Useful to hover, "
            "or to park the pointer somewhere harmless before a screenshot. "
            "Set relative:true for SDL programs and games — see amiga_click."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "Absolute screen X in pixels."},
                "y": {"type": "number", "description": "Absolute screen Y in pixels."},
                "relative": {
                    "type": "boolean",
                    "description": (
                        "Reach (x, y) by homing the pointer to the top-left corner and "
                        "moving by that delta, instead of warping. Required for programs "
                        "that read raw mouse deltas rather than asking Intuition."
                    ),
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "amiga_drag",
        "description": (
            "Press the mouse button at one point, move to another, and release — "
            "all in one atomic sequence with real timing. Use this for drag-and-drop, "
            "dragging a window or scrollbar, rubber-band selection, and anything that "
            "needs the button held down while the pointer moves. "
            "IMPORTANT for Amiga menus: Intuition menus are opened by HOLDING the "
            "RIGHT button at the top of the screen, moving to the item, and releasing. "
            "Drag with button 'right' from the menu bar to the item — a normal click "
            "cannot reach a menu at all."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_x": {"type": "number", "description": "Where to press."},
                "from_y": {"type": "number"},
                "to_x": {"type": "number", "description": "Where to release."},
                "to_y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "Default 'left'. Use 'right' for Intuition menus."},
                "relative": {
                    "type": "boolean",
                    "description": (
                        "Position by homing the pointer and moving by a delta instead of "
                        "warping. Needed for SDL programs and games, which keep their own "
                        "cursor and never see a warp."
                    ),
                },
                "settle": {
                    "type": "number",
                    "description": "Ticks (1/50 s) to pause at each step. Default 5. Raise it if the program is slow to react.",
                },
            },
            "required": ["from_x", "from_y", "to_x", "to_y"],
        },
    },
    {
        "name": "amiga_button",
        "description": (
            "Press or release a mouse button on its own, without moving the pointer. "
            "Use this when you need the button held across several steps — press it, "
            "take a screenshot to see what appeared (a menu, a drag preview), then move "
            "and release. For a simple hold-move-release in one go, prefer amiga_drag. "
            "ALWAYS release afterwards: a button left down leaves the Amiga behaving as "
            "though a finger is resting on it, which blocks other input and is easy to "
            "mistake for the machine having hung."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "Default 'left'."},
                "down": {"type": "boolean",
                         "description": "true to press and hold, false to release."},
            },
            "required": ["down"],
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
    {
        "name": "amiga_ui_tree",
        "description": (
            "The frontmost screen's Intuition window/gadget tree as text — the "
            "semantic complement to a screenshot. Each line: 'S' the screen, "
            "'W idx X Y WxH state \"title\"' a window, 'G id X Y WxH kind state "
            "\"label\" [\"value\"]' a gadget, all in absolute screen pixels. Use "
            "this to see what windows and gadgets exist and drive them by "
            "identity (amiga_ui_click) instead of guessing coordinates. Standard "
            "Intuition/GadTools GUIs only; custom-rendered screens (games, SDL, "
            "MUI internals, a Guru) are invisible here — screenshot those."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "amiga_ui_click",
        "description": (
            "Click a GUI element by identity rather than coordinates. Give a "
            "gadget (a numeric GadgetID, or a substring of its label or role — "
            "'close' finds the window's close gadget, 'OK' an OK button) and "
            "optionally a window (title substring or index) to scope it. The "
            "agent locates it in the UI tree and clicks its centre. Read the "
            "tree first with amiga_ui_tree. Errors if nothing matches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gadget": {"type": "string", "description":
                    "GadgetID (numeric) or a case-insensitive substring of the "
                    "gadget's label or role."},
                "window": {"type": "string", "description":
                    "Optional: window title substring or index to scope the search."},
                "double": {"type": "boolean", "description":
                    "Double-click instead of single. Default false."},
            },
            "required": ["gadget"],
        },
    },
    {
        "name": "amiga_menus",
        "description": (
            "Enumerate a window's menu strip (File/Edit/... and their items) as "
            "text — the menu-bar complement to amiga_ui_tree. Each item line "
            "carries its keyboard shortcut and a packed selector; checkmarks and "
            "disabled state are shown. Default is the active window; pass a "
            "window title substring or index to pick another. Standard Intuition "
            "menus only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description":
                    "Optional: window title substring or index (default: active window)."},
            },
        },
    },
    {
        "name": "amiga_menu_select",
        "description": (
            "Invoke a menu item by name. Reads the menu strip, finds the item "
            "whose text contains your string, and triggers its keyboard shortcut "
            "(right-Amiga+key). Errors if there is no match, or the item has no "
            "shortcut. Read the menus first with amiga_menus."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description":
                    "Substring of the menu item's text, e.g. 'Quit' or 'Execute'."},
                "window": {"type": "string", "description":
                    "Optional: window title substring or index to scope the menu strip."},
            },
            "required": ["item"],
        },
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


def tool_arexx(ami: Amiga, args: dict) -> list[dict]:
    source = args["source"]
    timeout = float(args.get("timeout", 60))
    rc, result = ami.arexx(source, timeout=timeout)
    header = f"ARexx return code: {rc}"
    if rc != 0:
        header += "  (non-zero — ARexx reported an error)"
    text = f"{header}\n\nRESULT: {result}" if result else f"{header}\n\n(no RESULT)"
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
    rx, ry = int(args.get("x", 0)), int(args.get("y", 0))
    rw, rh = int(args.get("w", 0)), int(args.get("h", 0))
    screen = int(args.get("screen", 0))
    region = bool(rx or ry or rw or rh or screen)
    native_err = None

    # The agent's built-in capture is faster and needs nothing installed, but
    # only understands planar screens. Try it first, fall back to SGrab.
    if method in ("auto", "native"):
        try:
            shot = ami.screenshot(x=rx, y=ry, w=rw, h=rh, screen=screen)
            data = png.screenshot_png(shot)
            kind = "truecolor" if shot.palette is None else f"{len(shot.palette)}-colour"
            where = (f"Region {rx},{ry} {shot.width}x{shot.height} of screen {screen}"
                     if region else f"Frontmost screen: {shot.width}x{shot.height}")
            return [
                {"type": "text", "text": f"{where}, {kind} (native capture)."},
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


def tool_ui_tree(ami: Amiga, args: dict) -> list[dict]:
    tree = ami.ui_tree().rstrip("\n")
    return [{"type": "text", "text": tree or "No standard-GUI windows on the frontmost screen."}]


def tool_ui_click(ami: Amiga, args: dict) -> list[dict]:
    gadget = args["gadget"]
    window = args.get("window", "")
    result = ami.ui_click(gadget, window=window, double=bool(args.get("double")))
    # The agent raises (ST_ERR -> AmigaError) when nothing matches, which the
    # dispatcher already renders as an isError text result.
    return [{"type": "text", "text": result}]


def tool_menus(ami: Amiga, args: dict) -> list[dict]:
    text = ami.menus(args.get("window", "")).rstrip("\n")
    return [{"type": "text", "text": text or "The window has no menu strip."}]


def tool_menu_select(ami: Amiga, args: dict) -> list[dict]:
    result = ami.menu_select(args["item"], window=args.get("window", ""))
    return [{"type": "text", "text": result}]


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


def tool_break(ami: Amiga, args: dict) -> list[dict]:
    return [{"type": "text", "text": ami.break_command().strip() or "Break sent."}]


def tool_click(ami: Amiga, args: dict) -> list[dict]:
    x, y = int(args["x"]), int(args["y"])
    button = args.get("button", "left")
    count = int(args.get("count", 1))
    relative = bool(args.get("relative", False))

    if relative:
        # Position by delta from a known corner and click atomically, because
        # the programs that need this also ignore pointer warps entirely.
        for _ in range(count):
            ami.click_at(x, y, button, relative=True)
        how = " (relative positioning)"
    else:
        ami.input_click(x, y, button, count)
        how = ""
    what = "double-clicked" if count == 2 else "clicked"
    return [{"type": "text", "text": f"{what} {button} at ({x}, {y}){how}."}]


def tool_move_mouse(ami: Amiga, args: dict) -> list[dict]:
    x, y = int(args["x"]), int(args["y"])
    if args.get("relative"):
        ami.input_point(x, y)
        return [{"type": "text", "text": f"Pointer moved to ({x}, {y}) by relative motion."}]
    ami.input_move(x, y)
    return [{"type": "text", "text": f"Pointer moved to ({x}, {y})."}]


def tool_drag(ami: Amiga, args: dict) -> list[dict]:
    """Press, move, release — as one request, so the gaps are Amiga ticks.

    Splitting this across calls does not work: each request opens its own
    connection, so press and release land hundreds of milliseconds apart and the
    program sees a held button rather than a drag.
    """
    fx, fy = int(args["from_x"]), int(args["from_y"])
    tx, ty = int(args["to_x"]), int(args["to_y"])
    button = args.get("button", "left")
    settle = int(args.get("settle", 5))
    rel = bool(args.get("relative", False))

    start = [("home",), ("rmove", fx, fy)] if rel else [("move", fx, fy)]
    end = [("home",), ("rmove", tx, ty)] if rel else [("move", tx, ty)]
    ami.input_script(
        start + [("wait", settle), ("button", button, True), ("wait", settle)]
        + end + [("wait", settle), ("button", button, False)]
    )
    how = " (relative positioning)" if rel else ""
    return [{"type": "text",
             "text": f"Dragged {button} from ({fx}, {fy}) to ({tx}, {ty}){how}."}]


def tool_button(ami: Amiga, args: dict) -> list[dict]:
    button = args.get("button", "left")
    down = bool(args["down"])
    ami.input_button(button, down)
    if down:
        return [{"type": "text", "text": (
            f"{button} button is now HELD DOWN. Release it with "
            f"amiga_button down=false — until you do, the Amiga behaves as though "
            f"a finger is resting on it and other input may not get through."
        )}]
    return [{"type": "text", "text": f"{button} button released."}]


def tool_screens(ami: Amiga, args: dict) -> list[dict]:
    rows = ami.screens()
    if not rows:
        return [{"type": "text", "text": "No screens open."}]
    lines = ["Open screens, front to back:"]
    for r in rows:
        lines.append(
            f"  [{r['index']}] {r['width']}x{r['height']} depth {r['depth']} "
            f"mode 0x{r['modeid']:08X}  {r['title'] or '(untitled)'}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def tool_pointer(ami: Amiga, args: dict) -> list[dict]:
    p = ami.pointer()
    return [{"type": "text", "text": (
        f"Pointer at ({p['x']}, {p['y']}) on a "
        f"{p['screen_width']}x{p['screen_height']} screen. "
        "This is Intuition's pointer; SDL programs keep their own cursor."
    )}]


def tool_region_changed(ami: Amiga, args: dict) -> list[dict]:
    h = ami.region_hash(int(args.get("x", 0)), int(args.get("y", 0)),
                        int(args.get("w", 0)), int(args.get("h", 0)),
                        int(args.get("screen", 0)))
    return [{"type": "text", "text": f"region checksum: {h:08x}"}]


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
    "amiga_arexx": tool_arexx,
    "amiga_break": tool_break,
    "amiga_click": tool_click,
    "amiga_drag": tool_drag,
    "amiga_button": tool_button,
    "amiga_move_mouse": tool_move_mouse,
    "amiga_type": tool_type,
    "amiga_key": tool_key,
    "amiga_read_file": tool_read_file,
    "amiga_write_file": tool_write_file,
    "amiga_list_dir": tool_list_dir,
    "amiga_screenshot": tool_screenshot,
    "amiga_screens": tool_screens,
    "amiga_pointer": tool_pointer,
    "amiga_region_changed": tool_region_changed,
    "amiga_system_info": tool_system_info,
    "amiga_ui_tree": tool_ui_tree,
    "amiga_ui_click": tool_ui_click,
    "amiga_menus": tool_menus,
    "amiga_menu_select": tool_menu_select,
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
