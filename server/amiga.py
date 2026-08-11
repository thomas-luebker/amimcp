"""Client for the amiagent wire protocol (see ../PROTOCOL.md).

Standard library only — this is meant to be pointed at with `python3` and run,
with no venv to create and no packages to install before you can reach the
Amiga.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

MAGIC = b"AMI0"
HDRLEN = 12
MAXFRAME = 16 * 1024 * 1024

CMD_PING = 0x01
CMD_EXEC = 0x02
CMD_GET = 0x03
CMD_PUT = 0x04
CMD_LIST = 0x05
CMD_INFO = 0x06
CMD_SHOT = 0x07
CMD_INPUT = 0x08
CMD_BREAK = 0x09
CMD_SCREENS = 0x0A
CMD_POINTER = 0x0B
CMD_HASH = 0x0C
CMD_UITREE = 0x0D
CMD_AREXX = 0x12
CMD_UIACT = 0x0E
CMD_MENUS = 0x0F
CMD_AUTH = 0x10

# CMD_INPUT ops (see agent/proto.h)
IN_MOVE = 1
IN_BUTTON = 2
IN_KEY = 3
IN_TEXT = 4
IN_CLICK = 5
IN_RMOVE = 6
IN_HOME = 7
IN_SCRIPT = 8
INS_WAIT = 9

BUTTONS = {"left": 0, "right": 1, "middle": 2}

# Qualifier bits, straight from devices/inputevent.h.
QUALIFIERS = {
    "shift": 0x0001, "lshift": 0x0001, "rshift": 0x0002,
    "capslock": 0x0004, "ctrl": 0x0008, "control": 0x0008,
    "alt": 0x0010, "lalt": 0x0010, "ralt": 0x0020,
    "amiga": 0x0040, "lamiga": 0x0040, "ramiga": 0x0080,
    "lcommand": 0x0040, "rcommand": 0x0080,
    "numpad": 0x0100,
}

# Amiga raw key codes for keys that have no printable character, so callers can
# say "return" instead of 0x44. Printable text goes through input_text(), which
# uses the Amiga's own keymap.
RAWKEYS = {
    "backspace": 0x41, "tab": 0x42, "enter": 0x43, "return": 0x44,
    "esc": 0x45, "escape": 0x45, "delete": 0x46, "del": 0x46,
    "help": 0x5F, "space": 0x40,
    "up": 0x4C, "down": 0x4D, "right": 0x4E, "left": 0x4F,
    "f1": 0x50, "f2": 0x51, "f3": 0x52, "f4": 0x53, "f5": 0x54,
    "f6": 0x55, "f7": 0x56, "f8": 0x57, "f9": 0x58, "f10": 0x59,
}

ST_OK = 0x00
ST_ERR = 0x01
ST_AUTH = 0x02

SHOT_CHUNKY = 1
SHOT_RGB24 = 2


class AmigaError(Exception):
    """The agent answered, and the answer was a refusal."""


class AmigaUnreachable(Exception):
    """We could not get a well-formed answer out of the agent at all."""


@dataclass
class DirEntry:
    is_dir: bool
    size: int
    protection: str
    date: str
    name: str


@dataclass
class Screenshot:
    width: int
    height: int
    palette: list[tuple[int, int, int]] | None
    pixels: bytes


def _button(name: str) -> int:
    try:
        return BUTTONS[str(name).lower()]
    except KeyError:
        raise AmigaError(f"unknown mouse button {name!r} — use left, right or middle") from None


def _rawkey(key: str | int) -> int:
    if isinstance(key, int):
        return key
    k = str(key).lower()
    if k in RAWKEYS:
        return RAWKEYS[k]
    raise AmigaError(
        f"unknown key {key!r}. Named keys: {', '.join(sorted(RAWKEYS))}. "
        "For printable characters use input_text(), or pass a raw code as an int."
    )


def _frame(code: int, payload: bytes) -> bytes:
    return MAGIC + bytes([code, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


class Amiga:
    """One connection per request.

    The agent serves a single command per connection and then closes, which
    keeps its side a plain accept loop with no half-consumed socket state to
    recover from. Reconnecting costs one round trip on a LAN and buys us a
    daemon that cannot get wedged into an ambiguous state.
    """

    def __init__(self, host: str, port: int = 7846, token: str = "", timeout: float = 30.0):
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        chunks = []
        got = 0
        while got < n:
            b = sock.recv(min(65536, n - got))
            if not b:
                raise AmigaUnreachable(
                    f"connection closed after {got} of {n} expected bytes "
                    "(agent crashed, or a command is still running)"
                )
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)

    def _read_response(self, sock: socket.socket) -> bytes:
        hdr = self._recv_exact(sock, HDRLEN)
        if hdr[:4] != MAGIC:
            raise AmigaUnreachable(
                f"bad frame magic {hdr[:4]!r} — is something other than amiagent "
                f"listening on {self.host}:{self.port}?"
            )
        status = hdr[4]
        (length,) = struct.unpack(">I", hdr[8:12])
        if length > MAXFRAME:
            raise AmigaUnreachable(f"agent announced an absurd frame length ({length})")
        body = self._recv_exact(sock, length) if length else b""

        if status == ST_OK:
            return body
        if status == ST_AUTH:
            raise AmigaError(
                "the agent requires a token — set AMIGA_TOKEN to the same value "
                "amiagent was started with"
            )
        raise AmigaError(body.decode("latin-1", "replace") or "unspecified agent error")

    def _request(self, code: int, payload: bytes = b"", timeout: float | None = None) -> bytes:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise AmigaUnreachable(
                f"cannot reach amiagent at {self.host}:{self.port} ({e}). "
                "Is the Amiga on, is its TCP stack up, and is amiagent running?"
            ) from e

        with sock:
            sock.settimeout(timeout if timeout is not None else self.timeout)
            try:
                if self.token:
                    sock.sendall(_frame(CMD_AUTH, self.token.encode("latin-1")))
                    self._read_response(sock)
                sock.sendall(_frame(code, payload))
                return self._read_response(sock)
            except socket.timeout as e:
                raise AmigaUnreachable(
                    f"the Amiga did not answer within {timeout or self.timeout:.0f}s. "
                    "A long-running command blocks amiagent until it finishes."
                ) from e

    # -- commands ---------------------------------------------------------

    def ping(self) -> str:
        return self._request(CMD_PING).decode("latin-1", "replace")

    def exec_command(self, command: str, timeout: float = 120.0) -> tuple[int, str]:
        """Run a command. `timeout` is the agent's deadline, not just ours.

        The agent runs the command in a child process and gives up waiting
        after this many seconds, answering "still running" rather than hanging.
        Our own socket timeout is deliberately longer so that answer arrives
        instead of us timing out first and losing the explanation.
        """
        deadline = max(1, min(int(timeout), 65535))
        body = self._request(
            CMD_EXEC,
            struct.pack(">H", deadline) + command.encode("latin-1"),
            timeout=deadline + 15,
        )
        if len(body) < 4:
            raise AmigaUnreachable("truncated EXEC response")
        (rc,) = struct.unpack(">I", body[:4])
        # AmigaDOS return codes are small positives, but sign-extend anyway so
        # a -1 from a failed launch reads as -1 rather than 4294967295.
        if rc > 0x7FFFFFFF:
            rc -= 0x100000000
        return rc, body[4:].decode("latin-1", "replace")

    def break_command(self) -> str:
        """Ask the agent to Ctrl-C whatever command is stuck."""
        return self._request(CMD_BREAK).decode("latin-1", "replace")

    def arexx(self, source: str, timeout: float = 60.0) -> tuple[int, str]:
        """Run an ARexx program string on the Amiga; return (rc, RESULT).

        The `source` is the program itself (not a script filename), so a
        one-liner like `address 'DOPUS.1'; command` drives any ARexx-aware app.
        Needs ARexx running (RexxMast); rc is the ARexx primary return code.
        """
        body = self._request(
            CMD_AREXX, source.encode("latin-1"), timeout=max(self.timeout, timeout)
        )
        if len(body) < 4:
            raise AmigaUnreachable("truncated AREXX response")
        (rc,) = struct.unpack(">I", body[:4])
        if rc > 0x7FFFFFFF:
            rc -= 0x100000000
        return rc, body[4:].decode("latin-1", "replace")

    def read_file(self, path: str) -> bytes:
        return self._request(CMD_GET, path.encode("latin-1"))

    def write_file(self, path: str, data: bytes) -> None:
        p = path.encode("latin-1")
        self._request(CMD_PUT, struct.pack(">H", len(p)) + p + data, timeout=max(self.timeout, 120.0))

    def list_dir(self, path: str) -> list[DirEntry]:
        text = self._request(CMD_LIST, path.encode("latin-1")).decode("latin-1", "replace")
        entries = []
        for line in text.split("\n"):
            if not line:
                continue
            # Name is last and tab-separated because AmigaDOS names may contain
            # spaces but never tabs.
            parts = line.split("\t", 4)
            if len(parts) != 5:
                continue
            kind, size, prot, date, name = parts
            entries.append(
                DirEntry(
                    is_dir=(kind == "D"),
                    size=int(size) if size.isdigit() else 0,
                    protection=prot,
                    date=date,
                    name=name,
                )
            )
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def system_info(self) -> dict[str, str]:
        text = self._request(CMD_INFO).decode("latin-1", "replace")
        info = {}
        for line in text.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v
        return info

    def ui_tree(self) -> str:
        """SPIKE: the frontmost screen's Intuition window/gadget tree as text.

        The semantic complement to a screenshot — windows and gadgets with
        roles, labels and absolute click-ready bounds. Standard Intuition/
        GadTools GUIs only; custom-rendered screens are invisible here.
        """
        return self._request(CMD_UITREE).decode("latin-1", "replace")

    def _uiact(self, verb: str, gadget: str, window: str = "", text: str = "") -> str:
        parts = [verb, window, str(gadget)]
        if verb == "settext":
            parts.append(text)
        payload = "\t".join(parts).encode("latin-1", "replace")
        return self._request(CMD_UIACT, payload).decode("latin-1", "replace")

    def ui_click(self, gadget: str, window: str = "", double: bool = False) -> str:
        """SPIKE: click a gadget by identity, not coordinates.

        `gadget` is a GadgetID (numeric) or a case-insensitive label substring;
        `window` optionally scopes it by title substring or index. The agent
        resolves the gadget to its centre and clicks it. Raises AmigaError if
        nothing matches. Standard Intuition/GadTools GUIs only.
        """
        return self._uiact("dclick" if double else "click", gadget, window)

    def ui_set(self, gadget: str, text: str, window: str = "") -> str:
        """SPIKE: focus a string gadget by identity, clear it, and type `text`."""
        return self._uiact("settext", gadget, window, text)

    def menus(self, window: str = "") -> str:
        """SPIKE: a window's menu strip as text (default: the active window).

        Lines: 'W "title"', 'M m enabled "name"', and per item
        'I m i s code flags "key" "text"' where `key` is the keyboard shortcut
        and `code` the packed FULLMENUNUM. Standard Intuition menus only.
        """
        payload = window.encode("latin-1", "replace")
        return self._request(CMD_MENUS, payload).decode("latin-1", "replace")

    def menu_key(self, shortcut: str) -> str:
        """SPIKE: invoke a menu item by its keyboard shortcut char (right-Amiga+char)."""
        return self._uiact("menushort", shortcut[:1])

    def menu_select(self, item: str, window: str = "") -> str:
        """SPIKE: invoke a menu item by label. Reads the menu strip, finds the
        item whose text contains `item`, and triggers its keyboard shortcut.

        Raises AmigaError if there is no match, or the match has no shortcut
        (menu items without a Command key can't be invoked this way yet).
        """
        needle = item.lower()
        for line in self.menus(window).splitlines():
            if not line.startswith("I "):
                continue
            # I m i s code flags "key" "text"
            try:
                key = line.split('"')[1]
                text = line.split('"')[3]
            except IndexError:
                continue
            if needle in text.lower():
                if not key:
                    raise AmigaError(f"menu item {text!r} has no keyboard shortcut")
                return self.menu_key(key)
        raise AmigaError(f"no menu item matching {item!r}")

    # -- input injection --------------------------------------------------

    def input_move(self, x: int, y: int) -> None:
        self._request(CMD_INPUT, struct.pack(">BHH", IN_MOVE, x, y))

    def input_button(self, button: str = "left", down: bool = True) -> None:
        self._request(CMD_INPUT, struct.pack(">BBB", IN_BUTTON, _button(button), 1 if down else 0))

    def input_key(self, key: str | int, down: bool | None = None,
                  qualifiers: int = 0) -> None:
        """Send a keystroke — by default a complete press *and* release.

        Pass `down=True` or `down=False` only when half an event is genuinely
        wanted, such as holding a modifier across several other keys.

        The default used to be press-only, which is a quiet trap: nothing fails
        and nothing reports an error, the Amiga simply behaves as though a
        finger is resting on the key. A single unreleased Escape put ScummVM
        into permanent skip-cutscene state, where it ignores every mouse click —
        which reads as "mouse injection is broken", not "a key is stuck".
        """
        code = _rawkey(key)
        if down is None:
            self._request(CMD_INPUT, struct.pack(">BBBH", IN_KEY, code, 1, qualifiers))
            self._request(CMD_INPUT, struct.pack(">BBBH", IN_KEY, code, 0, qualifiers))
        else:
            self._request(
                CMD_INPUT,
                struct.pack(">BBBH", IN_KEY, code, 1 if down else 0, qualifiers),
            )

    def input_text(self, text: str) -> None:
        # A tick per character on the Amiga side, so allow generous time.
        self._request(CMD_INPUT, bytes([IN_TEXT]) + text.encode("latin-1", "replace"),
                      timeout=max(self.timeout, 5 + len(text) * 0.2))

    def input_click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self._request(CMD_INPUT,
                      struct.pack(">BHHBB", IN_CLICK, x, y, _button(button), count))

    # Intuition programs follow the pointer warp that input_move() sends. SDL
    # and other programs that read raw mouse deltas do not: they keep their own
    # cursor and never see the warp, so a warp-then-click lands wherever that
    # program last believed the pointer was. For those, move relatively.

    def input_move_rel(self, dx: int, dy: int) -> None:
        """Move the pointer by a delta. Works for SDL/game programs."""
        self._request(CMD_INPUT, struct.pack(">Bhh", IN_RMOVE, dx, dy),
                      timeout=max(self.timeout, 5 + (abs(dx) + abs(dy)) * 0.05))

    def input_home(self) -> None:
        """Pin the pointer in the top-left corner, establishing a known origin."""
        self._request(CMD_INPUT, bytes([IN_HOME]),
                      timeout=max(self.timeout, 15))

    def input_point(self, x: int, y: int) -> None:
        """Put an SDL program's cursor at (x, y) — home, then move by that much.

        Absolute positioning built out of relative moves. Slower than
        input_move() and only needed for programs that ignore pointer warps;
        prefer input_move() for Intuition applications.
        """
        self.input_home()
        self.input_move_rel(x, y)

    def input_script(self, events: list[tuple]) -> None:
        """Run a sequence of input events in ONE request.

        Each event is a tuple: ("move", x, y), ("rmove", dx, dy),
        ("button", name, down), ("key", key, down, quals), ("home",),
        ("wait", ticks) — one tick is 1/50 s.

        Every other input call opens its own connection, so a press and a
        release land hundreds of milliseconds apart and programs read that as a
        held button rather than a click. Here the gaps are the Amiga's own
        ticks, which is what drags and double-clicks actually need.
        """
        out = bytearray([IN_SCRIPT, len(events)])
        for ev in events:
            kind = ev[0]
            if kind == "move":
                out += struct.pack(">BHH", IN_MOVE, ev[1], ev[2])
            elif kind == "rmove":
                out += struct.pack(">Bhh", IN_RMOVE, ev[1], ev[2])
            elif kind == "button":
                out += struct.pack(">BBB", IN_BUTTON, _button(ev[1]), 1 if ev[2] else 0)
            elif kind == "key":
                out += struct.pack(">BBBH", IN_KEY, _rawkey(ev[1]),
                                   1 if ev[2] else 0, ev[3] if len(ev) > 3 else 0)
            elif kind == "home":
                out += bytes([IN_HOME])
            elif kind == "wait":
                out += struct.pack(">BH", INS_WAIT, ev[1])
            else:
                raise AmigaError(f"unknown script event {kind!r}")
        ticks = sum(e[1] for e in events if e[0] == "wait")
        self._request(CMD_INPUT, bytes(out),
                      timeout=max(self.timeout, 10 + ticks / 50.0 + len(events)))

    def click_at(self, x: int, y: int, button: str = "left",
                 relative: bool = False) -> None:
        """Position and click as one atomic sequence.

        `relative=True` homes the pointer and moves by a delta instead of
        warping, for programs that ignore pointer warps (SDL).
        """
        move = [("home",), ("rmove", x, y)] if relative else [("move", x, y)]
        self.input_script(move + [("wait", 4), ("button", button, True),
                                  ("wait", 2), ("button", button, False)])

    # -- looking at the machine --------------------------------------------

    def screens(self) -> list[dict]:
        """Every open screen, front to back. Index 0 is frontmost.

        Worth having for two reasons: the client can derive its coordinate
        mapping from real dimensions instead of guessing, and a crashed program
        that leaves a blank screen in front no longer hides a healthy Workbench
        behind it — capture it by index.
        """
        body = self._request(CMD_SCREENS)
        if not body:
            raise AmigaUnreachable("empty screen list")
        out, at = [], 1
        for _ in range(body[0]):
            index, w, h, depth, modeid, tlen = struct.unpack(">BHHBIB", body[at:at + 11])
            at += 11
            title = body[at:at + tlen].decode("latin-1"); at += tlen
            out.append({"index": index, "width": w, "height": h,
                        "depth": depth, "modeid": modeid, "title": title})
        return out

    def pointer(self) -> dict:
        """Where Intuition's pointer is, and the screen it is on.

        Cheap enough to ask before every click. Note a program tracking raw
        mouse deltas keeps its own cursor that this cannot see.
        """
        body = self._request(CMD_POINTER)
        x, y, w, h = struct.unpack(">HHHH", body[:8])
        return {"x": x, "y": y, "screen_width": w, "screen_height": h}

    def region_hash(self, x: int = 0, y: int = 0, w: int = 0, h: int = 0,
                    screen: int = 0) -> int:
        """Checksum of a screen region — "has this changed?" without the pixels.

        Polling for a cutscene to finish or a status line to update otherwise
        means dragging whole frames across the wire to compare a few hundred
        bytes.
        """
        return struct.unpack(">I", self._request(
            CMD_HASH, struct.pack(">HHHHB", x, y, w, h, screen)))[0]

    def screenshot(self, timeout: float = 60.0, x: int = 0, y: int = 0,
                   w: int = 0, h: int = 0, screen: int = 0) -> Screenshot:
        """Capture a screen, or just a rectangle of one.

        A region is not a micro-optimisation. Reading a 320x14 status line out
        of a 1920x1080 truecolour frame moves ~6 MB over a ~1 MB/s link to
        answer a 4 KB question, and that ratio is the difference between
        watching a program and keeping up with it.
        """
        payload = b"" if not (x or y or w or h or screen) else \
            struct.pack(">HHHHB", x, y, w, h, screen)
        body = self._request(CMD_SHOT, payload, timeout=timeout)
        if len(body) < 8:
            raise AmigaUnreachable("truncated screenshot response")
        fmt, _rsvd, width, height, ncolors = struct.unpack(">BBHHH", body[:8])
        off = 8

        if fmt == SHOT_CHUNKY:
            palbytes = ncolors * 3
            raw = body[off : off + palbytes]
            off += palbytes
            palette = [tuple(raw[i * 3 : i * 3 + 3]) for i in range(ncolors)]
            expected = width * height
        elif fmt == SHOT_RGB24:
            palette = None
            expected = width * height * 3
        else:
            raise AmigaUnreachable(f"unknown screenshot format {fmt}")

        pixels = body[off : off + expected]
        if len(pixels) != expected:
            raise AmigaUnreachable(
                f"screenshot truncated: got {len(pixels)} of {expected} pixel bytes"
            )
        return Screenshot(width=width, height=height, palette=palette, pixels=pixels)
