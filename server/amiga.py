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
CMD_AUTH = 0x10

# CMD_INPUT ops (see agent/proto.h)
IN_MOVE = 1
IN_BUTTON = 2
IN_KEY = 3
IN_TEXT = 4
IN_CLICK = 5
IN_RMOVE = 6
IN_HOME = 7

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

    def screenshot(self, timeout: float = 60.0) -> Screenshot:
        body = self._request(CMD_SHOT, timeout=timeout)
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
