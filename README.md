# amimcp — let Claude work on your Amiga

An MCP server that gives Claude hands on a real Amiga: run AmigaDOS commands,
read and write files, list drawers, inspect the machine, and look at the
screen. You describe the problem; Claude pokes at the actual hardware.

```
┌──────────────┐   MCP over stdio    ┌──────────┐   framed TCP   ┌───────────┐
│ Claude Code  │ ◄─────────────────► │  amimcp  │ ◄────────────► │ amiagent  │
│  (your Mac)  │  JSON-RPC 2.0       │ (python) │  port 7846     │ (the Amiga)│
└──────────────┘                     └──────────┘                └───────────┘
```

Two halves:

- **`server/amimcp.py`** — the MCP server, on the Mac. Pure Python standard
  library: no venv, no pip, no lockfile to rot between the day you set this up
  and the evening two years later when the Amiga won't boot.
- **`agent/amiagent`** — a ~830-line C daemon on the Amiga. AmigaOS 2.04 and up,
  bare 68000, no TCP stack requirements beyond `bsdsocket.library`.

## Tools Claude gets

| Tool | What it does |
|---|---|
| `amiga_shell` | Run an AmigaDOS command, get its return code and stdout |
| `amiga_read_file` | Read a file (text inline, binaries base64) |
| `amiga_write_file` | Write or overwrite a file, including binaries |
| `amiga_list_dir` | List a drawer with sizes, protection bits, datestamps |
| `amiga_screenshot` | Capture the frontmost screen (planar natively, RTG via `C:sgrab`) |
| `amiga_system_info` | Kickstart, CPU/FPU, free RAM, volumes, cwd |

## Setup

### 1. Build and start the agent on the Amiga

Needs [bebbo's amiga-gcc](https://github.com/bebbo/amiga-gcc) — the same
toolchain amipkg uses. Cross-compile on whatever machine has it:

```sh
cd agent && make                # 68000 baseline, runs on everything
cd agent && make CPU=68020      # 68020+ build
```

Copy `amiagent` to the Amiga, bring up your TCP/IP stack (Roadshow, AmiTCP,
or the emulator's `bsdsocket.library` emulation), then:

```
amiagent TOKEN=pickasecret
```

Options: `PORT/N` (default 7846), `TOKEN/K`, `QUIET/S`. Ctrl-C stops it.

To start it at boot, add that line to `S:User-Startup` **after** your TCP stack
comes up — `run >NIL: amiagent TOKEN=pickasecret QUIET` if you don't want it
holding a Shell.

> If the link fails with an undefined `IntuitionBase`, your toolchain doesn't
> auto-open intuition/graphics. Rebuild with `make OWNBASES=1` and `amiagent`
> opens them itself.

### 2. Point Claude Code at it

```sh
./install.sh 192.168.1.42 pickasecret
```

That registers the server in `~/.claude.json` for the current project. Or do it
by hand:

```sh
claude mcp add amiga \
  --env AMIGA_HOST=192.168.1.42 \
  --env AMIGA_TOKEN=pickasecret \
  -- python3 /Users/loki/Development/amimcp/server/amimcp.py
```

### 3. Check the link before you rely on it

```sh
AMIGA_HOST=192.168.1.42 AMIGA_TOKEN=pickasecret python3 server/amimcp.py --probe
```

This skips MCP entirely and just talks to the agent, so a failure here is a
network or agent problem, not a Claude one.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AMIGA_HOST` | *(required)* | The Amiga's IP address or hostname |
| `AMIGA_PORT` | `7846` | Port `amiagent` listens on |
| `AMIGA_TOKEN` | *(none)* | Must match the agent's `TOKEN=` |
| `AMIGA_TIMEOUT` | `30` | Default per-request timeout, seconds |

## Developing without an Amiga

`tests/fake_agent.py` speaks the same wire protocol against a sandbox
directory, so you can build and debug the whole stack on the Mac and then
change one environment variable to hit real hardware:

```sh
python3 tests/fake_agent.py --port 7846 --root /tmp/fakeamiga
AMIGA_HOST=127.0.0.1 python3 server/amimcp.py --probe
```

It is a test double, not an emulator: `EXEC` understands a handful of
AmigaDOS-shaped commands (`List`, `Type`, `MakeDir`, `Version`, `Avail`, …)
rather than actually running AmigaDOS.

```sh
./run_tests.sh                # 44 tests, no hardware and no cross-compiler needed
```

## Security — read this before exposing the port

`amiagent` runs arbitrary commands for anyone who can reach its port, and the
protocol is unencrypted. That is a deliberate trade: classic Amigas have no TLS
without AmiSSL, and requiring AmiSSL just to reach the machine you are trying
to repair defeats the purpose.

So: **set a `TOKEN`, keep it on a LAN you trust, and never forward the port.**
Without a token, anything on your network segment can run `Format` on your
system partition. The agent prints a warning when started without one.

The token is checked per connection and travels in cleartext. It stops a stray
port scan, not a determined attacker with a packet sniffer on your LAN.

## Known limits

- **One command at a time.** The agent serves a single connection and blocks
  until each command finishes. A program that waits for input wedges it until
  you Ctrl-C the agent on the Amiga itself.
- **stderr needs OS 3.2+.** `SYS_Error` arrived in `dos.library` v47, so on
  3.2 and later stderr is captured and appended after a `--- stderr ---`
  marker. Older systems get stdout only.
- **Screenshots take one of two routes.** The agent's built-in capture needs
  OS 3.0+ and a palette screen of 256 colours or fewer; on RTG or truecolor it
  refuses rather than sending a wrong picture, and the server falls back to
  running `C:sgrab` on the Amiga and fetching the file it writes. That covers
  every screen a classic Amiga can put up, at the cost of a dependency —
  [SGrab](https://aminet.net) must be installed. JPEG output additionally
  needs `jpeg.library`; PNG does not. Since 0.2.0 the agent also captures
  truecolor natively via `cybergraphics.library`, so SGrab is a fallback rather
  than a requirement — though it still produces a much smaller transfer,
  because it compresses on the Amiga.
- **16 MiB frame cap** in both directions.
- **The token is compared byte-for-byte**, so `A4000` and `a4000` are different
  tokens. A mismatch reports "bad token"; no token at all reports that one is
  required.

## Verified on

An A4000/060 running Kickstart 47.115 / Workbench 47.5 (AmigaOS 3.2.3) with
Roadshow and a 1920x1080 Picasso96 RTG screen. Confirmed against that machine:
shell with stdout+stderr and return codes, file read/write, directory listing,
system info, native truecolor screen capture through `cybergraphics.library`,
SGrab capture as the fallback, mouse move/click, and typing — including
`äöü ÄÖÜ ß` correctly produced through the machine's German keymap.

Transfer runs at roughly 830 KiB/s, so a full 1920x1080 truecolor grab is
6.2 MB and about 7 seconds on the wire; the PNG that reaches the client is
around 850 KiB.

## Layout

```
PROTOCOL.md          the wire format, and why it looks like that
agent/amiagent.c     the Amiga daemon
agent/proto.h        shared constants
agent/Makefile       bebbo amiga-gcc build
server/amimcp.py     MCP server (stdio JSON-RPC)
server/amiga.py      wire protocol client
server/png.py        chunky/RGB → PNG, stdlib zlib
tests/fake_agent.py  host-side stand-in for the Amiga
tests/test_e2e.py    end-to-end tests
tests/test_protocol_sync.py  keeps proto.h and amiga.py in step
run_tests.sh         run everything
install.sh           register with Claude Code
```
