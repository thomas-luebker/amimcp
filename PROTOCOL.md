# amimcp wire protocol

A deliberately small framed request/response protocol spoken over TCP between
`amimcp` (the MCP server, on the Mac) and `amiagent` (the daemon, on the Amiga).

Design constraints, in priority order:

1. **Implementable in plain C on a 68000** — no JSON parser, no TLS, no
   allocation games. Every field is a fixed-width big-endian integer or a raw
   byte run.
2. **Streamable** — payload length is known before the body, so the agent can
   write a file to disk as it arrives instead of buffering it in Chip/Fast RAM.
3. **Debuggable** — every text payload is plain text you can read in a hex dump.

Big-endian throughout, which is the m68k's natural order and network order, so
neither side byte-swaps.

## Framing

Every request and every response is one frame:

```
offset  size  field
0       4     magic  "AMI0"
4       1     code   command (request) or status (response)
5       1     flags  reserved, must be 0
6       2     rsvd   reserved, must be 0
8       4     length payload length in bytes (big-endian)
12      ...   payload
```

Header is 12 bytes. `length` is capped at 16 MiB by both sides; a larger value
is a protocol error and the connection is dropped.

**One request per connection.** The client connects, sends one request, reads
one response, closes. This keeps the agent a trivial single-threaded accept
loop with no multiplexing, no keepalive timers, and no partially-consumed
socket state to recover from. If a token is configured, the `AUTH` frame is the
exception: it precedes the real request on the same connection.

## Commands (request `code`)

| Code | Name   | Payload                              | Response payload |
|------|--------|--------------------------------------|------------------|
| 0x01 | `PING` | *(empty)*                            | Agent banner, text |
| 0x02 | `EXEC` | `deadline` (u16, seconds) + command line | `rc` (u32) + captured output |
| 0x03 | `GET`  | Path, text                           | Raw file bytes |
| 0x04 | `PUT`  | `pathlen` (u16) + path + file bytes  | *(empty)* |
| 0x05 | `LIST` | Path, text                           | TSV directory listing |
| 0x06 | `INFO` | *(empty)*                            | `key=value` lines |
| 0x07 | `SHOT` | *(empty)*                            | Screen capture (see below) |
| 0x08 | `INPUT`| Op byte + op fields (see below)      | *(empty)* |
| 0x09 | `BREAK`| *(empty)*                            | What was signalled, text |
| 0x10 | `AUTH` | Shared token, text                   | *(empty)* |

Text payloads are **not** NUL-terminated; the frame length delimits them.

## Statuses (response `code`)

| Code | Meaning |
|------|---------|
| 0x00 | `OK` — payload is the result |
| 0x01 | `ERR` — payload is a human-readable error message |
| 0x02 | `AUTH_REQUIRED` — send `AUTH` first on this connection |

## Authentication

`amiagent` is an arbitrary-code-execution service. When started with a `TOKEN`,
it rejects every command with `AUTH_REQUIRED` until an `AUTH` frame carrying a
byte-identical token arrives on that connection. A wrong token closes the
connection immediately.

The token travels in cleartext and the payloads are unencrypted — classic
Amigas have no TLS without AmiSSL, and requiring AmiSSL just to reach the
machine you are trying to repair is the wrong trade. Treat this as a trusted-LAN
protocol: bind to the LAN interface, keep it off the open internet, and set a
token so a stray port scan cannot run `Format`.

## `EXEC`

The payload is a `u16` deadline in seconds followed by the command line. `0`
means the agent's default of 120.

```
offset  size  field                       (response)
0       4     rc      the command's AmigaDOS return code (u32)
4       ...   output  bytes the command wrote to stdout
```

**The command does not run on the accept loop's process.** A child process runs
it while the agent waits with the deadline. If the deadline passes, the agent
answers `ERR` saying the command is still running, goes back to accepting
connections, and leaves it running.

That structure is the whole point: up to 0.2.0 the command ran inline, so a
single program waiting for input nobody would give it parked the agent forever,
recoverable only by Ctrl-C at the physical machine. Now `PING`, `INFO`, `GET`,
`LIST`, `SHOT` and `INPUT` all keep working while a command is stuck. Only a
second `EXEC` is refused, with a message naming the command that is blocking it.

A finished-but-unreported job is reaped on the next `EXEC`, so a client that
gave up and reconnected does not leak the temp files.

Captured with `SystemTags(SYS_Input, …, SYS_Output, …)`, plus `SYS_Error` where
the OS has it — that tag arrived in `dos.library` v47 (AmigaOS 3.2), so on 3.2
and later stderr is captured too and appended after a `\n--- stderr ---\n`
marker. Below 3.2 you get stdout only; append your own redirect if a command
writes somewhere else.

**The handles are the caller's to close.** The `dos.library` autodoc is explicit:
*"[they] will not be closed by System, you must close them (if needed) after
System returns"* (unless `SYS_Asynch` is set, which we do not use). Assuming the
opposite is a quiet, confusing failure rather than a loud one — the output file
is never flushed, so every command reports success with empty output, and the
file stays locked, so the *next* command fails to create it. That was a real bug
in 0.1.0, found within a minute of first touching real hardware.

## `BREAK`

Sends `SIGBREAKF_CTRL_C` to the stuck command. Best effort, and honest about it:
the response says how many processes were signalled, and a program that ignores
Ctrl-C still needs attention at the machine.

Finding the right process takes a small trick. `System()` runs the command in
its own CLI process that the agent never gets a pointer to, so before starting a
job the agent snapshots which CLI numbers exist (`MaxCli()` / `FindCliProc()`).
On `BREAK` it takes the snapshot again and signals anything that appeared since,
plus the child process it did create.


## `LIST` response

One line per entry, `\n`-terminated, fields separated by tabs:

```
type <TAB> size <TAB> protection <TAB> date <TAB> name
```

`type` is `D` for a directory or `F` for a file. `protection` is the AmigaDOS
bit string (`hsparwed`). `date` is `DD-MMM-YY HH:MM:SS`. Names may contain
spaces — that is why `name` is last and why the separator is a tab, which
AmigaDOS filenames cannot contain.

## `SHOT` response

```
offset  size  field
0       1     fmt      1 = palette + chunky, 2 = packed RGB24
1       1     rsvd
2       2     width
4       2     height
6       2     ncolors  (0 when fmt = 2)
8       ...   palette  ncolors * 3 bytes, R,G,B  (absent when fmt = 2)
...     ...   pixels
```

`fmt 1` pixels are one byte per pixel, each an index into the palette, packed
at exactly `width` bytes per row (the agent repacks away the 16-pixel row
padding `ReadPixelArray8` requires). `fmt 2` pixels are three bytes per pixel,
`width * 3` per row, and are produced from `cybergraphics.library` on RTG or
truecolor screens.

The Amiga sends raw pixels and the Mac encodes the PNG. Doing it this way keeps
zlib off the 68000 entirely, and the raw form is small enough not to matter — a
full 640×256 8-bit screen is 160 KiB.

Capture targets the **frontmost** screen, so whatever you are looking at is
what you get, including a Guru.

`fmt 2` comes from `cybergraphics.library`, which both Picasso96 and CyberGraphX
provide. That library is not part of NDK 3.2, so `agent/vendor/cgx` carries its
`.fd` file and a `fd2pragma`-generated inline header — the library offsets are
the CyberGraphX SDK's own, not reverse-engineered guesses. A wrong offset would
not fail to compile; it would jump into the middle of some other function on
real hardware.

## `INPUT` payload

One op byte, then that op's fields:

| Op | Name     | Fields                              |
|----|----------|-------------------------------------|
| 1  | `MOVE`   | `x` (u16), `y` (u16)                |
| 2  | `BUTTON` | `button` (u8), `down` (u8)          |
| 3  | `KEY`    | `rawcode` (u8), `down` (u8), `qualifier` (u16) |
| 4  | `TEXT`   | latin-1 text                        |
| 5  | `CLICK`  | `x` (u16), `y` (u16), `button` (u8), `count` (u8) |
| 6  | `RMOVE`  | `dx` (**s16**), `dy` (**s16**)      |
| 7  | `HOME`   | *(no fields)*                       |

`button` is 0 left, 1 right, 2 middle.

Events are posted to `input.device` with `IND_WRITEEVENT`, which puts them on
the same queue the real mouse and keyboard feed — every application sees them
as genuine input, with no cooperation required from the program being driven.

`MOVE` and the move inside `CLICK` use `IECLASS_POINTERPOS`, which takes
absolute screen coordinates. Button events use `IECLASS_RAWMOUSE` with a zero
relative delta, so they change button state and nothing else.

### Absolute versus relative pointer motion

**`MOVE` does not work for every program.** `IECLASS_POINTERPOS` warps the
Intuition pointer, and Intuition applications follow it — Workbench, SysInfo,
system requesters. Programs that read raw mouse *deltas* instead of asking
Intuition where the pointer is never see the warp and keep their own cursor
exactly where it was. SDL is the common case; ScummVM is the one that forced
these ops into existence. The failure is quiet and misleading: the click is
delivered, but at whatever position the program still believes the pointer
holds, so the same wrong thing is clicked no matter where `MOVE` aims.

`RMOVE` posts `IECLASS_RAWMOUSE` with `IEQUALIFIER_RELATIVEMOUSE` and a nonzero
delta, which those programs do track. The delta is emitted in steps of at most
32 pixels rather than one jump — `ie_X` would carry the whole distance, but
programs that accumulate motion per event follow a run of modest steps far more
predictably.

`HOME` drives the pointer hard into the top-left corner (24 oversized negative
steps). Overshooting is the point: both Intuition and SDL clamp at the edge, so
a deliberate excess of travel makes the resulting position certain. `HOME`
followed by `RMOVE dx,dy` is therefore absolute positioning built out of
relative motion — which is what the client exposes as `input_point(x, y)`.

Verified against ScummVM 2.0.1 on a 320×240 screen: deltas map **1:1** to that
program's own cursor coordinates, no scaling needed.

`dx`/`dy` are **signed**. Packing them unsigned sends 65216 where −320 was
meant, and the pointer travels the wrong way across the screen.

`CLICK` exists as its own op rather than three round trips because a click is
move-then-press-then-release, and splitting that across three connections would
let the pointer drift between them.

`TEXT` is translated per character by `keymap.library/MapANSI()` using whatever
keymap the Amiga is currently running. That is what makes umlauts and other
national characters come out right on a German layout without the Mac side
knowing anything about it. Characters the active keymap cannot produce are
skipped rather than typed wrongly.
