# amifleet

An Apple-Remote-Desktop-style console for every Amiga on your LAN. A native
macOS/SwiftUI app that speaks the amimcp wire protocol
([`PROTOCOL.md`](../../PROTOCOL.md)) **directly** — no MCP server, no Python,
no LLM in the loop. Dressed like a Workbench 3.x window because of course it is.

![the fleet board](docs/board.png)

## What it does

- **Fleet board** — one beveled tile per machine, polled every 5 s: online
  light, latency, agent version, CPU, Kickstart, free Chip/Fast RAM. A machine
  that drops offline keeps showing its last good report, greyed.
- **Scan LAN** — sweeps the /24 of your first configured machine for port 7846
  and adds every agent it finds.
- **Report & Shell** — the full `INFO` report, plus an AmigaDOS shell that runs
  commands through `EXEC` and shows the return code and captured output.
- **Live screen** ("VNC") — the machine's frontmost screen, refreshed the way
  the protocol intends: poll the cheap `HASH` twice a second (no pixels move)
  and pull a full `SHOT` only when the checksum changes. Mouse clicks and the
  keyboard are forwarded through `INPUT`, mapped from the fitted image back to
  real Amiga screen pixels. Chunky pixels are shown unscaled — no smoothing.

Both palette (`fmt 1`) and RGB24/RTG (`fmt 2`) captures render. A full 1080p
RTG frame is a multi-megabyte, multi-second fetch on a real 68060; the header
shows the fetch time so slowness is visible, not mysterious.

## Run it

```
swift run          # from tools/amifleet
```

It starts with the three machines from the amimcp fleet (A4000, PiStorm,
FS-UAE), all on token `a4000`; edit or add your own with **Add…**, and the
list persists in `UserDefaults`. Everything is a plain TCP request/response, so
it works against any `amiagent` 0.1.1+ — the live screen needs 0.5.0+ for
`HASH`, and RTG capture needs a `cybergraphics.library` screen.

## How it's built

- `AmigaWire.swift` — the protocol, on blocking BSD sockets (one request per
  connection, AUTH-then-command, big-endian framing) with async wrappers.
- `Models.swift` — the `Fleet` store: polling, discovery, persistence.
- `FleetView.swift` / `DetailView.swift` / `ScreenView.swift` — the three
  window kinds. `ScreenView` drops to an AppKit `NSView` to catch raw mouse and
  key events and translate macOS keycodes to Amiga rawkeys.

This is a client, not a server: it holds no state on the Amiga and can't do
anything a shell couldn't. Same trust model as the rest of amimcp — keep it on
a LAN you trust and give every agent a token.
