/*
 * status.h - the amiagent status board.
 *
 * A single struct behind a named public semaphore, so any program on the same
 * Amiga (amimon, a dock plugin, an ARexx hack) can see what the agent is doing
 * without touching the network stack. This is deliberately NOT part of the
 * wire protocol: it is local IPC in the classic AmigaOS idiom, and the wire
 * stays as small as it was.
 *
 * Protocol for readers:
 *
 *     Forbid();
 *     ss = FindSemaphore(AGENT_BOARD_NAME);
 *     if (ss) ObtainSemaphoreShared(ss);
 *     Permit();
 *     ... copy the struct out, check board_version ...
 *     ReleaseSemaphore(ss);
 *
 * The Forbid() covers the gap between finding the semaphore and obtaining it;
 * once obtained, the agent's teardown (RemSemaphore, then an exclusive obtain)
 * waits for every reader to finish before the memory goes away. Copy the
 * struct out and release quickly - a reader that sits on the semaphore holds
 * up the agent's next request.
 */

#ifndef AMIMCP_STATUS_H
#define AMIMCP_STATUS_H

#include <exec/semaphores.h>
#include <dos/dos.h>

#define AGENT_BOARD_NAME "amiagent.status"

/* Bump when the struct layout changes. A reader that sees a version it does
 * not know must display "incompatible", not garbage. */
#define AGENT_BOARD_VERSION 3

#define AGS_IDLE   0   /* waiting for a connection */
#define AGS_BUSY   1   /* answering a request right now */
#define AGS_CMDRUN 2   /* an EXEC outlived its deadline and is still running */

struct agent_board {
    struct SignalSemaphore sem;  /* named AGENT_BOARD_NAME; must stay first */

    UWORD board_version;         /* AGENT_BOARD_VERSION */
    UWORD state;                 /* AGS_* */
    UWORD tcp_port;
    UWORD have_token;            /* nonzero if a TOKEN is required to connect */

    ULONG requests;              /* frames answered since startup */
    ULONG failures;              /* ST_ERR responses among them */
    ULONG cmds;                  /* EXEC commands started */

    struct DateStamp started;    /* for uptime */

    char agent_version[12];      /* "0.6.0" */
    char activity[96];           /* current (BUSY) or most recent request */
    char lastcmd[96];            /* most recent EXEC command line */
    char client[20];             /* dotted-quad of the most recent client */

    /* What the client SAYS is driving it, set by CMD_HELLO. Self-reported and
     * unverifiable - any client can claim anything - so a reader should present
     * it as a claim, not a fact. Empty until a HELLO arrives. */
    char driver[40];
};

#endif /* AMIMCP_STATUS_H */
