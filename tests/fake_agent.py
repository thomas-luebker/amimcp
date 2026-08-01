#!/usr/bin/env python3
"""A host-side stand-in for amiagent, speaking the same wire protocol.

Two jobs:

  * lets the test suite exercise the whole stack — framing, auth, streaming,
    PNG encoding, MCP plumbing — with no Amiga and no cross-compiler;
  * lets you develop and debug amimcp on the Mac, then point it at real
    hardware by changing one environment variable.

It is a test double, not an emulator: EXEC runs a handful of AmigaDOS-shaped
commands against a sandbox directory rather than actually running AmigaDOS.

    python3 tests/fake_agent.py --root /tmp/fakeamiga --port 7846
"""

from __future__ import annotations

import argparse
import os
import socketserver
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from amiga import (  # noqa: E402
    CMD_AUTH, CMD_BREAK, CMD_EXEC, CMD_GET, CMD_INFO, CMD_INPUT, CMD_LIST,
    CMD_PING, CMD_PUT, CMD_SHOT, HDRLEN, IN_BUTTON, IN_CLICK, IN_KEY, IN_MOVE,
    IN_TEXT, MAGIC, SHOT_CHUNKY, SHOT_RGB24, ST_AUTH, ST_ERR, ST_OK,
)

# Every input event the fake agent is asked to inject, so tests can assert on
# what would have reached input.device.
INPUT_LOG: list[tuple] = []
TRUECOLOR = False

ROOT = "/tmp/fakeamiga"
TOKEN = ""


def frame(code: int, payload: bytes) -> bytes:
    return MAGIC + bytes([code, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


def to_host(path: str) -> str:
    """Map an AmigaDOS path onto the sandbox directory.

    'SYS:S/Startup-Sequence' and 'S:Startup-Sequence' both land under ROOT.
    Enough fidelity to exercise the client; not a real AmigaDOS namespace.
    """
    p = path.replace("\\", "/")
    if ":" in p:
        vol, rest = p.split(":", 1)
        vol = vol.upper()
        if vol in ("SYS", "RAM", "WORK", ""):
            p = rest
        else:
            p = os.path.join(vol.lower(), rest)
    p = p.strip("/")
    full = os.path.realpath(os.path.join(ROOT, p))
    if not full.startswith(os.path.realpath(ROOT)):
        raise ValueError("path escapes the sandbox")
    return full


def fake_shell(command: str) -> tuple[int, str]:
    parts = command.split()
    if not parts:
        return 0, ""
    verb = parts[0].lower()
    try:
        if verb in ("list", "dir"):
            target = parts[1] if len(parts) > 1 else "SYS:"
            names = sorted(os.listdir(to_host(target)))
            return 0, "\n".join(names) + "\n"
        if verb == "type":
            with open(to_host(parts[1]), "rb") as fh:
                return 0, fh.read().decode("latin-1")
        if verb == "makedir":
            os.makedirs(to_host(parts[1]), exist_ok=True)
            return 0, ""
        if verb == "delete":
            os.remove(to_host(parts[1]))
            return 0, f"{parts[1]}  deleted\n"
        if verb == "version":
            return 0, "Kickstart 40.68, Workbench 40.42\n"
        if verb == "avail":
            return 0, ("Type  Available    In-Use   Maximum   Largest\n"
                       "chip    1015232    033856   1049088    999424\n"
                       "fast    7340032    048576   7388608   7000000\n")
        if verb == "echo":
            return 0, command[5:].strip().strip('"') + "\n"
        if verb == "failme":
            return 10, "fake_agent: deliberate failure\n"
    except Exception as e:  # noqa: BLE001
        return 20, f"fake_agent: {e}\n"
    return 205, f"{parts[0]}: unknown command (fake_agent implements a subset)\n"


def fake_screenshot() -> bytes:
    """A 320x200 16-colour test pattern, in the SHOT_CHUNKY payload layout."""
    w, h, ncolors = 320, 200, 16
    palette = bytearray()
    for i in range(ncolors):
        palette += bytes(((i * 17) & 0xFF, (i * 11) & 0xFF, (i * 23) & 0xFF))
    pixels = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            pixels[y * w + x] = ((x // 20) + (y // 25)) % ncolors
    hdr = struct.pack(">BBHHH", SHOT_CHUNKY, 0, w, h, ncolors)
    return hdr + bytes(palette) + bytes(pixels)


def fake_truecolor_shot() -> bytes:
    """A 64x32 RGB24 payload, in the SHOT_RGB24 layout."""
    w, h = 64, 32
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes((x * 4 & 0xFF, y * 8 & 0xFF, (x + y) & 0xFF))
    return struct.pack(">BBHHH", SHOT_RGB24, 0, w, h, 0) + bytes(px)


def fake_input(body: bytes) -> None:
    op = body[0]
    p = body[1:]
    if op == IN_MOVE:
        INPUT_LOG.append(("move", *struct.unpack(">HH", p[:4])))
    elif op == IN_BUTTON:
        INPUT_LOG.append(("button", p[0], p[1]))
    elif op == IN_KEY:
        INPUT_LOG.append(("key", p[0], p[1], struct.unpack(">H", p[2:4])[0]))
    elif op == IN_TEXT:
        INPUT_LOG.append(("text", p.decode("latin-1")))
    elif op == IN_CLICK:
        x, y, button, count = struct.unpack(">HHBB", p[:6])
        INPUT_LOG.append(("click", x, y, button, count))
    else:
        raise ValueError(f"unknown input op {op}")


def fake_info() -> bytes:
    return (
        "agent=fake_agent 0.1.0\n"
        "kickstart=40.68\n"
        "dos.library=40\n"
        "cpu=68030\n"
        "fpu=yes\n"
        "chipram_free=1015232\n"
        "fastram_free=7340032\n"
        "largest_free=7000000\n"
        "volumes=SYS,Work,RAM\n"
        "cwd=SYS:\n"
    ).encode("latin-1")


class Job:
    """Mirrors the real agent's one-command-at-a-time model.

    The agent runs commands in a child process and stops waiting at the
    deadline; a stuck command blocks later EXECs until BREAK. Modelling that
    here is what lets the timeout, busy and break paths be tested without an
    Amiga attached.
    """

    lock = threading.Lock()
    active = None          # the command line, while one is outstanding
    finished = False
    rc = 0
    out = ""
    stop = None            # set by BREAK to release a sleeping command


JOB = Job()


def _run_job(cmd: str, seconds: float) -> None:
    broke = JOB.stop.wait(seconds)
    with JOB.lock:
        JOB.finished = True
        JOB.rc = 20 if broke else 0
        JOB.out = "*** Break\n" if broke else f"slept {seconds:g}s\n"


def fake_exec(body: bytes) -> bytes:
    """EXEC payload is a u16 deadline followed by the command line."""
    (deadline,) = struct.unpack(">H", body[:2])
    cmd = body[2:].decode("latin-1")
    deadline = deadline or 120

    with JOB.lock:
        if JOB.active and JOB.finished:
            JOB.active = None          # reap
        busy = JOB.active

    if busy:
        raise Busy(f'a command is still running: "{busy}". Send BREAK to try to stop it.')

    if cmd.lower().startswith("sleep "):
        secs = float(cmd.split()[1])
        with JOB.lock:
            JOB.active, JOB.finished, JOB.stop = cmd, False, threading.Event()
        threading.Thread(target=_run_job, args=(cmd, secs), daemon=True).start()
        JOB.stop.wait(0)  # let the thread start
        for _ in range(int(deadline * 100)):
            with JOB.lock:
                if JOB.finished:
                    JOB.active = None
                    return struct.pack(">I", JOB.rc & 0xFFFFFFFF) + JOB.out.encode("latin-1")
            time.sleep(0.01)
        raise StillRunning(f"still running after {deadline}s. The agent is NOT stuck.")

    rc, out = fake_shell(cmd)
    return struct.pack(">I", rc & 0xFFFFFFFF) + out.encode("latin-1")


def fake_break() -> bytes:
    with JOB.lock:
        if not JOB.active:
            raise Busy("no command is running")
        ev = JOB.stop
    if ev:
        ev.set()
    return b"sent Ctrl-C to 1 process(es)"


class Busy(Exception):
    pass


class StillRunning(Exception):
    pass


class Handler(socketserver.BaseRequestHandler):
    def recv_exact(self, n: int) -> bytes:
        chunks, got = [], 0
        while got < n:
            b = self.request.recv(min(65536, n - got))
            if not b:
                raise ConnectionError("closed")
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)

    def reply(self, status: int, payload: bytes = b"") -> None:
        self.request.sendall(frame(status, payload))

    def handle(self) -> None:
        authed = not TOKEN
        try:
            while True:
                hdr = self.recv_exact(HDRLEN)
                if hdr[:4] != MAGIC:
                    return
                code = hdr[4]
                (length,) = struct.unpack(">I", hdr[8:12])
                body = self.recv_exact(length) if length else b""

                if code == CMD_AUTH:
                    if TOKEN and body.decode("latin-1") == TOKEN:
                        authed = True
                        self.reply(ST_OK)
                        continue
                    self.reply(ST_ERR, b"bad token")
                    return
                if not authed:
                    self.reply(ST_AUTH)
                    return

                self.dispatch(code, body)
                return  # one command per connection, like the real agent
        except (ConnectionError, OSError):
            pass

    def dispatch(self, code: int, body: bytes) -> None:
        try:
            if code == CMD_PING:
                self.reply(ST_OK, b"fake_agent 0.1.0  AmigaOS dos.library 40\n")
            elif code == CMD_INFO:
                self.reply(ST_OK, fake_info())
            elif code == CMD_SHOT:
                self.reply(ST_OK, fake_truecolor_shot() if TRUECOLOR else fake_screenshot())
            elif code == CMD_INPUT:
                fake_input(body)
                self.reply(ST_OK)
            elif code == CMD_EXEC:
                self.reply(ST_OK, fake_exec(body))
            elif code == CMD_BREAK:
                self.reply(ST_OK, fake_break())
            elif code == CMD_GET:
                with open(to_host(body.decode("latin-1")), "rb") as fh:
                    self.reply(ST_OK, fh.read())
            elif code == CMD_PUT:
                (plen,) = struct.unpack(">H", body[:2])
                path = body[2 : 2 + plen].decode("latin-1")
                host = to_host(path)
                os.makedirs(os.path.dirname(host), exist_ok=True)
                with open(host, "wb") as fh:
                    fh.write(body[2 + plen :])
                self.reply(ST_OK)
            elif code == CMD_LIST:
                self.reply(ST_OK, self.listing(body.decode("latin-1")))
            else:
                self.reply(ST_ERR, b"unknown command")
        except (Busy, StillRunning) as e:
            self.reply(ST_ERR, str(e).encode("latin-1"))
        except FileNotFoundError as e:
            self.reply(ST_ERR, f"cannot open \"{e.filename}\" (DOS error 205)".encode("latin-1"))
        except Exception as e:  # noqa: BLE001
            self.reply(ST_ERR, str(e).encode("latin-1"))

    def listing(self, path: str) -> bytes:
        host = to_host(path)
        if not os.path.isdir(host):
            raise NotADirectoryError("path is a file, not a directory")
        out = []
        for name in sorted(os.listdir(host)):
            full = os.path.join(host, name)
            is_dir = os.path.isdir(full)
            size = 0 if is_dir else os.path.getsize(full)
            out.append(
                f"{'D' if is_dir else 'F'}\t{size}\t----rwed\t01-Jan-99 00:00:00\t{name}"
            )
        return ("\n".join(out) + "\n").encode("latin-1") if out else b""


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def seed(root: str) -> None:
    os.makedirs(os.path.join(root, "S"), exist_ok=True)
    os.makedirs(os.path.join(root, "C"), exist_ok=True)
    with open(os.path.join(root, "S", "Startup-Sequence"), "w") as fh:
        fh.write("C:SetPatch QUIET\nC:Version >NIL:\nC:AddBuffers DF0: 15\nLoadWB\nEndCLI >NIL:\n")
    with open(os.path.join(root, "C", "Shell"), "wb") as fh:
        fh.write(b"\x00\x00\x03\xf3\x00\x00\x00\x00fake hunk binary\x00")


def build(host: str = "127.0.0.1", port: int = 7846, root: str = ROOT,
          token: str = "") -> Server:
    """Create a fake agent bound to host:port, not yet serving."""
    global ROOT, TOKEN
    ROOT, TOKEN = root, token
    os.makedirs(root, exist_ok=True)
    seed(root)
    return Server((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 7846, root: str = ROOT,
          token: str = "") -> tuple[Server, threading.Thread]:
    """Start a fake agent in a background thread. Returns (server, thread)."""
    srv = build(host, port, root, token)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


def main() -> int:
    ap = argparse.ArgumentParser(description="Fake amiagent for testing amimcp.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7846)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--token", default="")
    a = ap.parse_args()

    srv = build(a.host, a.port, a.root, a.token)
    print(f"fake_agent listening on {a.host}:{a.port}, sandbox {a.root}")
    print("Point amimcp at it:")
    print(f"  AMIGA_HOST={a.host} AMIGA_PORT={a.port} python3 server/amimcp.py --probe")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
