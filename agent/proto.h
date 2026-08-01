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
#define CMD_AUTH 0x10

/* CMD_INPUT payload: one op byte, then the op's own fields. Keeping several
 * events in one frame matters — a click is move+press+release, and splitting
 * that across three connections would let the pointer drift in between. */
#define IN_MOVE   1   /* u16 x, u16 y                          */
#define IN_BUTTON 2   /* u8 button, u8 down                    */
#define IN_KEY    3   /* u8 rawcode, u8 down, u16 qualifier    */
#define IN_TEXT   4   /* latin-1 text, mapped via the Amiga's own keymap */
#define IN_CLICK  5   /* u16 x, u16 y, u8 button, u8 count     */

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

#define AMIAGENT_VERSION "0.3.2"

#endif /* AMIMCP_PROTO_H */
