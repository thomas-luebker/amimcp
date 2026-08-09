/*
 * proto.h - amimcp wire protocol constants.
 *
 * Kept in sync by hand with server/amiga.py and PROTOCOL.md; there are eleven
 * numbers here and a code generator would cost more than it saves.
 */

#ifndef AMIMCP_PROTO_H
#define AMIMCP_PROTO_H

#define AMI_MAGIC0 'A'
#define AMI_MAGIC1 'M'
#define AMI_MAGIC2 'I'
#define AMI_MAGIC3 '0'

#define AMI_HDRLEN 12

/* Requests. */
#define CMD_PING 0x01
#define CMD_EXEC 0x02
#define CMD_GET  0x03
#define CMD_PUT  0x04
#define CMD_LIST 0x05
#define CMD_INFO 0x06
#define CMD_SHOT 0x07
#define CMD_INPUT 0x08
#define CMD_BREAK 0x09
#define CMD_SCREENS 0x0A
#define CMD_POINTER 0x0B
#define CMD_HASH 0x0C
#define CMD_AUTH 0x10

/* CMD_SHOT payload is optional, and omitting it means "the whole frontmost
 * screen" exactly as before:
 *
 *   (empty)                         whole front screen
 *   u16 x, y, w, h                  that rectangle only; w or h of 0 means
 *                                   "out to the screen edge"
 *   u16 x, y, w, h, u8 screen       ... on screen `screen` (0 = frontmost)
 *
 * A region is worth having because most captures are made to read one small
 * thing. Pulling a 1920x1080 truecolour frame across a ~1 MB/s link to read a
 * 320x14 status line moves about a thousand times more data than the question
 * needs, and that ratio is what makes driving a running program feel slow.
 *
 * CMD_HASH takes the same payload and returns only a u32 checksum, so "has
 * this area changed yet?" costs no pixels at all. */

/* CMD_SCREENS reply: u8 count, then per screen —
 *   u8 index, u16 width, u16 height, u8 depth, u32 modeid, u8 titlelen, title
 * Index 0 is the frontmost. Capturing by index matters when a crashed program
 * leaves a blank screen in front: the machine is fine underneath, and without
 * this the agent is blind to it. */

/* CMD_POINTER reply: u16 x, u16 y, u16 screen_w, u16 screen_h.
 * Answers "where is the pointer?" without moving a frame. Note this is
 * Intuition's pointer — a program tracking raw mouse deltas (SDL) keeps its
 * own cursor which no one but that program can see. */

/* CMD_INPUT payload: one op byte, then the op's own fields. Keeping several
 * events in one frame matters — a click is move+press+release, and splitting
 * that across three connections would let the pointer drift in between. */
#define IN_MOVE   1   /* u16 x, u16 y                          */
#define IN_BUTTON 2   /* u8 button, u8 down                    */
#define IN_KEY    3   /* u8 rawcode, u8 down, u16 qualifier    */
#define IN_TEXT   4   /* latin-1 text, mapped via the Amiga's own keymap */
#define IN_CLICK  5   /* u16 x, u16 y, u8 button, u8 count     */
#define IN_RMOVE  6   /* s16 dx, s16 dy - relative motion      */
#define IN_HOME   7   /* no fields - pin the pointer at the top-left corner */
#define IN_SCRIPT 8   /* u8 count, then that many sub-events (see below) */

/* IN_SCRIPT sub-events, one after another:
 *   1 MOVE    u16 x, u16 y
 *   2 BUTTON  u8 button, u8 down
 *   3 KEY     u8 rawcode, u8 down, u16 qualifier
 *   6 RMOVE   s16 dx, s16 dy
 *   7 HOME    (no fields)
 *   9 WAIT    u16 ticks (1/50 s each)
 *
 * The whole list runs inside one request, so the gaps between events are the
 * Amiga's own ticks rather than however long a TCP round trip happened to
 * take. That distinction is the entire point: a press and a release sent as
 * two requests arrive hundreds of milliseconds apart, which programs read as a
 * held button, not a click. Drags and click-then-click sequences need the
 * timing to be real. */
#define INS_WAIT  9

/* IN_MOVE warps the pointer with IECLASS_POINTERPOS, which is what Intuition
 * programs follow. SDL — and anything else that reads raw mouse deltas rather
 * than asking Intuition where the pointer is — ignores those warps completely
 * and keeps its own cursor, so a warp followed by a click lands wherever that
 * program last thought the pointer was. Driving ScummVM is the case that
 * forced IN_RMOVE and IN_HOME: home to a known corner, then move by a delta. */

#define IN_BTN_LEFT   0
#define IN_BTN_RIGHT  1
#define IN_BTN_MIDDLE 2

/* Responses. */
#define ST_OK   0x00
#define ST_ERR  0x01
#define ST_AUTH 0x02

/* Both sides refuse a frame larger than this. A PUT of a 16 MiB file is
 * already far beyond what a classic Amiga wants in one bite, and the cap is
 * what stops a bad length field from turning into a wild AllocVec. */
#define AMI_MAXFRAME (16UL * 1024UL * 1024UL)

/* Screenshot payload formats. */
#define SHOT_CHUNKY 1   /* palette + 1 byte/pixel */
#define SHOT_RGB24  2   /* 3 bytes/pixel, no palette */

#define AMIAGENT_VERSION "0.5.4"

#endif /* AMIMCP_PROTO_H */
