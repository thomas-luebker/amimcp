---
name: imp3-post
description: Post a message into the imp3 (Infinity Music Player) chat from one of the fleet Amigas, through amiagent's AMIAGENT ARexx port. Use when asked to post, paste, announce or send something to imp3 / the IMP chat / the scene channel.
---

# Posting to the imp3 chat

imp3 is **"Infinity Music Player v3.472 by Juen"** — an MP3 player with a built-in
community chat used by the Amiga scene (yelworC, juen, FTA, djbase, konsolendoc and
~40 others). Posting into it means typing into a **live public room full of real
people**. Everything below exists to keep that from going wrong.

You do **not** need impsend for this. `amiagent` 0.11.0+ opens a local ARexx port
called `AMIAGENT` whose `ACTIVATEWINDOW` / `ENTERTEXT` / `KEY` commands drive the
chat window directly.

## Before you start

1. **Find a machine that has imp3 running.** Not every fleet machine does, and the
   answer changes between sessions. A chat window is the proof:

   ```python
   wins = [l for l in a.ui_tree().split("\n") if l.startswith("W ")]
   chat = [w for w in wins if "CHAT --" in w]
   ```

   `Status` will **not** show imp3 — it is Workbench-started, so it has no CLI
   number. Only the window list tells you.

2. **All fleet machines share one identity** (`S:imp3.reg`), so they all connect as
   nick `lokiist`. Two clients online at once kick each other off. If imp3 is up on
   more than one machine, post from the one the user named and leave the rest alone.

## The procedure

`post_to_imp3.py` in this directory does it; it is deliberately two-phase.

```
post_to_imp3.py --check                  is imp3 up, and where is its window
post_to_imp3.py --stage "first line"     type it WITHOUT sending; screenshot it
post_to_imp3.py --send "l2" "l3" ...     send the staged line, then the rest
```

By hand it is:

```python
a.arexx("address AMIAGENT\n'ACTIVATEWINDOW \"CHAT --*\"'\n")
a.arexx("address AMIAGENT\n'ENTERTEXT \"your line\"'\n")
a.arexx("address AMIAGENT\n'KEY \"enter\"'\n")
```

One line per message, about 1.5 s apart so the client keeps up.

## The rule that matters: check before it is public

The A4000 runs a **German keymap**. `ENTERTEXT` types through the Amiga's own
keymap, so `|`, `/`, `:` and umlauts do come out right — but verify it rather than
assume, because a mangled line cannot be unsent.

**Type the first line WITHOUT pressing enter, screenshot the input field, and read
it back.** The input line sits at the foot of the chat window; `--stage` does this
and tells you where it wrote the picture. Only once the characters are right, send.

## Channel selection — the weak point

**You cannot reliably read back which tab is selected.** imp3 highlights tabs for
unread activity as well as for selection, and at 1:1 pixel scale the two render
almost identically. There is no log file on disk to check the room afterwards
either.

Clicking a tab *does* work — the content region hash changes and the client
rewinds the backlog — you just cannot confirm *which* tab you landed on:

```python
h0 = a.region_hash(x=620, y=80, w=800, h=420)
a.click_at(698, 71)                 # tab strip is ~11 px below the window top
time.sleep(4)                       # backlog rewind takes a moment
h1 = a.region_hash(x=620, y=80, w=800, h=420)   # different => the tab changed
```

So: **ask the user which channel, and prefer that they select the tab themselves.**
If you must click it, say plainly afterwards that the room is unconfirmed. Do not
quietly assume it worked.

## Afterwards

`--send` screenshots the foot of the chat pane; confirm the lines actually appear
with your nick and a timestamp. A successful post looks like
`[20:10] <@lokiist> your text`.

## Manners

- Keep it short. Five lines is already a lot in a chat room.
- No unattended or repeated posting. One block, then stop.
- If a post lands in the wrong channel, say so and offer to repost — do not paper
  over it.

## Related

- `S:imp3.prefs`, `S:imp3.reg`, `S:impstuff.prefs` — config; `DH1:impsend` on the
  A4000 is the source of truth for the impsend/impdock kit.
- The impdock button bar (`impdock v1.1 for impsend`) posts canned lines — PLAYING,
  WTTR, SYSINFO, UPTIME, PROST, $VER, AWAY — into the current channel, with the same
  channel ambiguity.
