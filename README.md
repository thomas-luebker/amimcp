# amimcp — let Claude work on your Amiga

An [MCP](https://modelcontextprotocol.io) server that gives Claude hands on a
real Amiga. It can run AmigaDOS commands, read and write files, list drawers,
inspect the machine, capture the screen, and move the mouse and type on it.

You describe the problem; Claude pokes at the actual hardware.

![Claude typing into an AmigaShell, umlauts and all](docs/typing-demo.png)

*That is Claude clicking into a Shell on an A4000/060, typing a command, and
pressing Return — the umlauts routed through the Amiga's own German keymap.*

```
┌──────────────┐   MCP over stdio    ┌──────────┐   framed TCP   ┌────────────┐
│ Claude Code  │ ◄─────────────────► │  amimcp  │ ◄────────────► │  amiagent  │
│ (your Mac/PC)│  JSON-RPC 2.0       │ (python) │  port 7846     │ (the Amiga)│
└──────────────┘                     └──────────┘                └────────────┘
```

Two halves:

- **`amimcp`** — the MCP server, on the machine running Claude. Pure Python
  standard library: no venv, no pip, no lockfile to rot between the day you set
  this up and the evening two years later when the Amiga won't boot.
- **`amiagent`** — a small C daemon on the Amiga. AmigaOS 2.04 and up, bare
  68000, ~85 KB, needs nothing but `bsdsocket.library`.

## What Claude gets

| Tool | What it does |
|---|---|
| `amiga_shell` | Run an AmigaDOS command — stdout, stderr, and return code |
| `amiga_read_file` | Read a file; text inline, binaries base64 |
| `amiga_write_file` | Write or overwrite a file, binaries included |
| `amiga_list_dir` | List a drawer with sizes, protection bits, datestamps |
| `amiga_system_info` | Kickstart, CPU/FPU, free Chip/Fast, volumes, assigns |
| `amiga_screenshot` | Capture the frontmost screen as PNG — planar or RTG |
| `amiga_click` | Move the pointer and click — single, double, any button |
| `amiga_move_mouse` | Move the pointer without clicking |
| `amiga_type` | Type text, mapped through the Amiga's own keymap |
| `amiga_key` | Press Return, Esc, F-keys and so on, with qualifiers |

Together those are enough to actually work: read a Startup-Sequence and fix it,
cross-compile a binary and push it over and run it, launch a GUI program and
drive it by clicking, or just look at what the machine is showing when it has
gone wrong.

## 1. Install the agent on the Amiga

### Download the release

Grab [`amiagent-0.2.0.lha`](https://github.com/thomas-luebker/amimcp/releases/latest)
and unpack it on the Amiga. It contains `amiagent` (68000, runs on everything)
and `amiagent.020` (68020+).

### With [amipkg](https://github.com/thomas-luebker/amipkg)

```
amipkg install amiagent
```

*Pending — the catalog entry is submitted but not yet merged and signed.
Use the release download until then.*

### Or build it yourself

Needs [bebbo's amiga-gcc](https://github.com/bebbo/amiga-gcc). Cross-compile on
any machine that has it:

```sh
cd agent && make                # 68000 baseline, runs on every Amiga
cd agent && make CPU=68020      # 68020+ build
```

Copy the resulting `amiagent` to the Amiga.

> If the link fails with an undefined `IntuitionBase`, your toolchain doesn't
> auto-open intuition/graphics. Rebuild with `make OWNBASES=1`.

## 2. Start it

Bring up your TCP/IP stack (Roadshow, AmiTCP, or the emulator's `bsdsocket`
emulation), then from a Shell:

```
amiagent TOKEN=pickasecret
```

It prints that it is listening on port 7846 and waits. **Ctrl-C** stops it; if
you backgrounded it, find it with `Status` and `Break <n> C`.

Options: `PORT/N` (default 7846), `TOKEN/K`, `QUIET/S`.

To start it at boot, add this to `S:User-Startup`, **after** your TCP stack
comes up:

```
run >NIL: amiagent TOKEN=pickasecret QUIET
```

## 3. Point Claude at it

```sh
./install.sh 192.168.1.42 pickasecret
```

That probes the Amiga first, then registers the server with Claude Code. Or do
it by hand:

```sh
claude mcp add amiga \
  --env AMIGA_HOST=192.168.1.42 \
  --env AMIGA_TOKEN=pickasecret \
  -- python3 /path/to/amimcp/server/amimcp.py
```

For **Claude Desktop**, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "amiga": {
      "command": "python3",
      "args": ["/path/to/amimcp/server/amimcp.py"],
      "env": {
        "AMIGA_HOST": "192.168.1.42",
        "AMIGA_TOKEN": "pickasecret"
      }
    }
  }
}
```

Start a new session and ask Claude to check the Amiga.

### Check the link without involving Claude

```sh
AMIGA_HOST=192.168.1.42 AMIGA_TOKEN=pickasecret python3 server/amimcp.py --probe
```

This skips MCP entirely, so a failure here is a network or agent problem rather
than a Claude one.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AMIGA_HOST` | *(required)* | The Amiga's IP address or hostname |
| `AMIGA_PORT` | `7846` | Port `amiagent` listens on |
| `AMIGA_TOKEN` | *(none)* | Must match the agent's `TOKEN=` |
| `AMIGA_TIMEOUT` | `30` | Default per-request timeout, seconds |

## Security — read this before opening the port

`amiagent` runs arbitrary commands for anyone who can reach its port, and the
protocol is unencrypted. That is a deliberate trade: classic Amigas have no TLS
without AmiSSL, and requiring AmiSSL just to reach the machine you are trying to
repair defeats the purpose.

So: **set a `TOKEN`, keep it on a LAN you trust, and never forward the port.**
Without a token, anything on your network segment can run `Format` on your
system partition. The agent warns loudly when started without one.

The token is compared byte-for-byte (so `A4000` and `a4000` are different
tokens) and travels in cleartext. It stops a stray port scan, not someone with a
packet sniffer on your LAN.

## Verified on

An A4000/060 running Kickstart 47.115 / Workbench 47.5 (AmigaOS 3.2.3) with
Roadshow and a 1920×1080 Picasso96 RTG screen. Confirmed against that machine:
shell with stdout+stderr and return codes, file read/write, directory listing,
system info, native truecolor screen capture through `cybergraphics.library`,
SGrab capture as the fallback, mouse move/click, and typing — including
`äöü ÄÖÜ ß` correctly produced through the machine's German keymap.

Transfer runs at roughly 830 KiB/s, so a full 1920×1080 truecolor grab is 6.2 MB
and about 7 seconds on the wire; the PNG that reaches Claude is around 850 KiB.

## Known limits

- **Interactive programs block `amiga_shell`.** The agent runs one command at a
  time and waits for it to exit, so anything that sits waiting for keyboard
  input wedges it until you Ctrl-C the agent on the Amiga itself. This applies
  to *shell* commands — a GUI program launched with `Run` can then be driven
  perfectly well with `amiga_click` and `amiga_type`.
- **stderr needs OS 3.2+.** `SYS_Error` arrived in `dos.library` v47, so on 3.2
  and later stderr is captured and appended after a `--- stderr ---` marker.
  Older systems get stdout only.
- **Screenshots take one of two routes.** The agent captures planar screens
  itself (OS 3.0+, ≤256 colours) and truecolor/RTG screens through
  `cybergraphics.library`, which Picasso96 and CyberGraphX both provide. If
  neither fits, it falls back to running `C:sgrab` on the Amiga and fetching the
  file — SGrab compresses on the Amiga, so it transfers far less. SGrab's JPEG
  mode additionally needs `jpeg.library`; PNG does not.
- **One command at a time**, one connection at a time.
- **16 MiB frame cap** in both directions.

## How it works

[`PROTOCOL.md`](PROTOCOL.md) documents the wire format and, more usefully, why
each part looks the way it does — the framing, why there is one command per
connection, how input events are injected, and the `System()` file-handle
ownership rule that caused the first real bug found on hardware.

## Developing without an Amiga

`tests/fake_agent.py` speaks the same wire protocol against a sandbox directory,
so you can build and debug the whole stack on your desktop and then change one
environment variable to hit real hardware:

```sh
python3 tests/fake_agent.py --port 7846 --root /tmp/fakeamiga
AMIGA_HOST=127.0.0.1 python3 server/amimcp.py --probe
```

It is a test double, not an emulator: `EXEC` understands a handful of
AmigaDOS-shaped commands rather than actually running AmigaDOS.

```sh
./run_tests.sh     # 61 tests, no hardware and no cross-compiler needed
```

## Layout

```
PROTOCOL.md          the wire format, and why it looks like that
agent/amiagent.c     the Amiga daemon
agent/proto.h        constants shared with the Python side
agent/Makefile       bebbo amiga-gcc build
agent/vendor/cgx/    CyberGraphX interface files (see its README)
server/amimcp.py     MCP server (stdio JSON-RPC)
server/amiga.py      wire protocol client
server/png.py        chunky/RGB → PNG, stdlib zlib
tests/fake_agent.py  host-side stand-in for the Amiga
tests/               end-to-end and protocol-drift tests
install.sh           register with Claude Code
```

## Licence

Apache 2.0 — see [LICENSE](LICENSE). Vendored CyberGraphX interface files keep
phase5's terms; see [NOTICE](NOTICE) and
[`agent/vendor/cgx/README.md`](agent/vendor/cgx/README.md).
