/*
 * amiagent - the Amiga half of amimcp.
 *
 * A small TCP daemon that lets a remote MCP server run AmigaDOS commands,
 * move files, list directories, read system state, and grab the screen on
 * this machine's behalf. Speaks the framed protocol in ../PROTOCOL.md.
 *
 * Targets AmigaOS 2.04 and up on a bare 68000. That constraint drives most of
 * the shape of this file: no dynamic string library, no recursion, payloads
 * streamed through a fixed 8 KiB buffer rather than held in RAM, and one
 * connection served at a time.
 *
 * SECURITY: this runs arbitrary commands for anyone who can reach the port.
 * Start it with a TOKEN and keep it on a LAN you trust. See PROTOCOL.md.
 */

#ifndef __amigaos__
#error "amiagent targets AmigaOS - build with m68k-amigaos-gcc (see Makefile)"
#endif

/* The NDK's sys/socket.h uses ssize_t, but newlib gates its typedef behind a
 * feature macro that isn't active here. Define it (with newlib's own guard, so
 * this stays conflict-free) BEFORE proto/bsdsocket.h pulls socket.h in. This
 * is the same dance amipkg's http.c does. */
#include <sys/types.h>
#ifndef _SSIZE_T_DECLARED
typedef long ssize_t;
#define _SSIZE_T_DECLARED
#endif

#include <exec/types.h>
#include <exec/memory.h>
#include <dos/dos.h>
#include <dos/dostags.h>
#include <dos/datetime.h>
#include <dos/dosextens.h>
#include <exec/io.h>
#include <devices/input.h>
#include <devices/inputevent.h>
#include <intuition/intuition.h>
#include <intuition/intuitionbase.h>
#include <graphics/gfxbase.h>
#include <cybergraphx/cybergraphics.h>

#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <proto/graphics.h>
#include <proto/keymap.h>
#include <inline/cybergraphics.h>
#include <proto/bsdsocket.h>
#include <rexx/storage.h>
#include <rexx/rxslib.h>
#include <proto/rexxsyslib.h>

#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "proto.h"
#include "status.h"

struct Library *SocketBase = NULL;
/* Opened on demand: only truecolor screens need it, and plenty of Amigas do
 * not have it at all. */
struct Library *CyberGfxBase = NULL;

/* bebbo's startup auto-opens intuition.library and graphics.library when the
 * linker pulls in a reference to them, so IntuitionBase/GfxBase are already
 * live here. If your toolchain does NOT auto-open them the link will fail with
 * an undefined IntuitionBase (or the screenshot path will find it NULL) —
 * build with -DAMIAGENT_OWN_LIBBASES to open and close them by hand instead. */
#ifdef AMIAGENT_OWN_LIBBASES
struct IntuitionBase *IntuitionBase = NULL;
struct GfxBase *GfxBase = NULL;
#endif

#define IOBUF 8192

/* Stack for the process a command runs in. Workbench icons routinely ask for
 * 100 KB and more; 16 KB was enough for AmigaDOS commands and nothing else,
 * and quietly wrecked anything larger. Memory is not the constraint here — the
 * target machine has hundreds of megabytes — so be generous. */
#define AMIAGENT_CMD_STACK 262144

static UBYTE g_io[IOBUF];
static char g_token[128];
static int g_have_token = 0;
static int g_quiet = 1;   /* silent unless VERBOSE: see say() */

/* Progress output is OFF by default, and that is a safety decision rather than
 * a taste one. An AmigaShell pauses its output the moment someone clicks in
 * the window to mark text; a process writing to it then blocks inside printf
 * until a key is pressed. For a daemon that means the whole agent stops
 * accepting connections because of a window nobody is looking at — which is
 * exactly the wedge this daemon is supposed to not have. VERBOSE opts back in
 * for interactive debugging. */
static void say(const char *fmt, ...)
{
    va_list ap;
    if (g_quiet) return;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    fflush(stdout);
}

/* ------------------------------------------------------------------ *
 * Status board
 *
 * A named public semaphore wrapping one struct (see status.h), so a local
 * program - amimon is the one that exists - can show what the agent is doing
 * without opening a socket. The board is static memory in this binary, which
 * is why board_close() must drain readers before main() returns: a reader
 * still holding the semaphore when the seglist unloads would be reading freed
 * RAM, and on AmigaOS that does not fail politely.
 * ------------------------------------------------------------------ */

static struct agent_board g_board;
static char g_board_name[] = AGENT_BOARD_NAME;

static void board_open(UWORD port)
{
    memset(&g_board, 0, sizeof g_board);
    g_board.sem.ss_Link.ln_Name = g_board_name;
    g_board.sem.ss_Link.ln_Pri = 0;
    g_board.board_version = AGENT_BOARD_VERSION;
    g_board.state = AGS_IDLE;
    g_board.tcp_port = port;
    g_board.have_token = (UWORD)g_have_token;
    strcpy(g_board.agent_version, AMIAGENT_VERSION);
    strcpy(g_board.activity, "waiting for a connection");
    DateStamp(&g_board.started);
    AddSemaphore(&g_board.sem);   /* initializes AND publishes; OS 2.04+ only */
}

static void board_close(void)
{
    /* Unlist first so no new reader can find it, then obtain exclusively,
     * which queues behind every reader already inside. When it returns, the
     * board is ours alone and the memory may go away. */
    Forbid();
    RemSemaphore(&g_board.sem);
    Permit();
    ObtainSemaphore(&g_board.sem);
    ReleaseSemaphore(&g_board.sem);
}

/* ------------------------------------------------------------------ *
 * Socket helpers
 * ------------------------------------------------------------------ */

/* Wait until `sock` is readable. Returns 1 ready, 0 timed out, -1 on Ctrl-C or
 * error. Going through WaitSelect rather than a blocking recv() is what keeps
 * Ctrl-C able to stop the daemon while it is parked waiting for a client. */
static int sock_readable(int sock, long secs)
{
    fd_set rd;
    struct timeval tv;
    ULONG sigs = SIGBREAKF_CTRL_C;
    LONG n;

    FD_ZERO(&rd);
    FD_SET(sock, &rd);
    tv.tv_sec = secs;
    tv.tv_usec = 0;

    n = WaitSelect(sock + 1, &rd, NULL, NULL, secs < 0 ? NULL : &tv, &sigs);
    if (sigs & SIGBREAKF_CTRL_C) return -1;
    if (n < 0) return -1;
    if (n == 0) return 0;
    return FD_ISSET(sock, &rd) ? 1 : 0;
}

/* Read exactly len bytes. Returns 1 on success, 0 on EOF/error/break. */
static int recv_all(int sock, UBYTE *buf, ULONG len)
{
    while (len) {
        long got;
        int r = sock_readable(sock, 120);
        if (r <= 0) return 0;
        got = recv(sock, (char *)buf, (int)(len > 0x7000 ? 0x7000 : len), 0);
        if (got <= 0) return 0;
        buf += got;
        len -= (ULONG)got;
    }
    return 1;
}

static int send_all(int sock, const UBYTE *buf, ULONG len)
{
    while (len) {
        long put = send(sock, (char *)buf, (int)(len > 0x7000 ? 0x7000 : len), 0);
        if (put <= 0) return 0;
        buf += put;
        len -= (ULONG)put;
    }
    return 1;
}

static void put_be32(UBYTE *p, ULONG v)
{
    p[0] = (UBYTE)(v >> 24); p[1] = (UBYTE)(v >> 16);
    p[2] = (UBYTE)(v >> 8);  p[3] = (UBYTE)v;
}

static ULONG get_be32(const UBYTE *p)
{
    return ((ULONG)p[0] << 24) | ((ULONG)p[1] << 16) |
           ((ULONG)p[2] << 8) | (ULONG)p[3];
}

static void put_be16(UBYTE *p, UWORD v)
{
    p[0] = (UBYTE)(v >> 8); p[1] = (UBYTE)v;
}

/* Send a response header. The body follows in as many send_all() calls as the
 * handler likes, which is what lets GET stream a file it never fully holds. */
static int send_hdr(int sock, UBYTE status, ULONG len)
{
    UBYTE h[AMI_HDRLEN];
    h[0] = AMI_MAGIC0; h[1] = AMI_MAGIC1; h[2] = AMI_MAGIC2; h[3] = AMI_MAGIC3;
    h[4] = status;
    h[5] = 0; h[6] = 0; h[7] = 0;
    put_be32(h + 8, len);
    return send_all(sock, h, AMI_HDRLEN);
}

static int send_resp(int sock, UBYTE status, const void *payload, ULONG len)
{
    if (!send_hdr(sock, status, len)) return 0;
    if (len && !send_all(sock, (const UBYTE *)payload, len)) return 0;
    return 1;
}

static int send_err(int sock, const char *msg)
{
    g_board.failures++;   /* bare ULONG bump: only this process writes it */
    return send_resp(sock, ST_ERR, msg, (ULONG)strlen(msg));
}

/* ------------------------------------------------------------------ *
 * Growable byte buffer (directory listings, sysinfo)
 * ------------------------------------------------------------------ */

struct buf {
    UBYTE *p;
    ULONG len;
    ULONG cap;
};

static void buf_init(struct buf *b) { b->p = NULL; b->len = b->cap = 0; }
static void buf_free(struct buf *b) { if (b->p) FreeVec(b->p); buf_init(b); }

static int buf_add(struct buf *b, const void *data, ULONG n)
{
    if (b->len + n > b->cap) {
        ULONG ncap = b->cap ? b->cap * 2 : 4096;
        UBYTE *np;
        while (ncap < b->len + n) ncap *= 2;
        np = (UBYTE *)AllocVec(ncap, MEMF_ANY);
        if (!np) return 0;
        if (b->len) CopyMem(b->p, np, b->len);
        if (b->p) FreeVec(b->p);
        b->p = np;
        b->cap = ncap;
    }
    CopyMem((APTR)data, b->p + b->len, n);
    b->len += n;
    return 1;
}

static int buf_str(struct buf *b, const char *s)
{
    return buf_add(b, s, (ULONG)strlen(s));
}

/* ------------------------------------------------------------------ *
 * Command handlers
 * ------------------------------------------------------------------ */

/* Copy a length-delimited (not NUL-terminated) payload into a C string. */
static char *dup_cstr(const UBYTE *p, ULONG len)
{
    char *s = (char *)AllocVec(len + 1, MEMF_ANY);
    if (!s) return NULL;
    if (len) CopyMem((APTR)p, s, len);
    s[len] = '\0';
    return s;
}

static int do_ping(int sock)
{
    char msg[160];
    sprintf(msg, "amiagent %s  AmigaOS dos.library %ld\n",
            AMIAGENT_VERSION, (long)((struct Library *)DOSBase)->lib_Version);
    return send_resp(sock, ST_OK, msg, (ULONG)strlen(msg));
}

/* Run an AmigaDOS command, capturing stdout to a temp file, then stream the
 * file back after the 4-byte return code.
 *
 * The handles we pass are OURS to close. The dos.library autodoc is explicit:
 * "[they] will not be closed by System, you must close them (if needed) after
 * System returns". Getting that backwards leaves the output file open, so it
 * reads back empty AND stays locked, wedging every later command.
 *
 * stderr is captured too where the OS supports it: SYS_Error arrived in
 * dos.library v47 (AmigaOS 3.2). Older systems silently get stdout only. */

/* Size of an open file, or 0. Seek-to-end returns the OLD position, so the
 * second Seek is what actually reports the length. */
static ULONG file_size(BPTR fh)
{
    LONG end;
    if (!fh) return 0;
    if (Seek(fh, 0, OFFSET_END) < 0) return 0;
    end = Seek(fh, 0, OFFSET_BEGINNING);
    return end > 0 ? (ULONG)end : 0;
}

/* Read through a big temporary buffer when one can be spared. Fewer, larger
 * Read() calls mean fewer packet round-trips to the filesystem.
 *
 * It also sidesteps a storage bug seen on Emu68/PiStorm (FFS on SD): reading a
 * file in 8 KB chunks returned WRONG data — six reads of one file gave three
 * different results and never the right one — while 32 KB+ reads were correct
 * every time. An Amiga-side `copy` of the same bytes corrupted identically, so
 * the fault is under DOS, not here. This does not fix that machine; it only
 * keeps us off the path that breaks on it. */
#define PUMPBUF 65536

static int pump(int sock, BPTR fh, ULONG len)
{
    APTR big  = AllocMem(PUMPBUF, MEMF_ANY);
    UBYTE *buf = big ? (UBYTE *)big : g_io;   /* 2 MB machines keep the old path */
    ULONG cap  = big ? PUMPBUF : IOBUF;
    int ok = 1;

    while (len) {
        LONG n = Read(fh, buf, (LONG)(len > cap ? cap : len));
        if (n <= 0) { ok = 0; break; }
        if (!send_all(sock, buf, (ULONG)n)) { ok = 0; break; }
        len -= (ULONG)n;
    }
    if (big) FreeMem(big, PUMPBUF);
    return ok;
}

static const char ERRMARK[] = "\n--- stderr ---\n";

/* ------------------------------------------------------------------ *
 * Command execution
 *
 * The command does NOT run on the accept loop's process. Up to 0.2.0 it did,
 * and a single command that waited for input — any interactive program, a
 * requester nobody answers — parked the whole agent forever. The only recovery
 * was Ctrl-C at the physical machine, which rather defeats a remote agent.
 *
 * So a child process runs the command while this one waits with a deadline.
 * On timeout we answer the client, go back to accepting connections, and leave
 * the command running. PING, INFO, GET, SHOT and INPUT keep working; only a
 * second EXEC is refused, and BREAK can try to stop the stuck one.
 * ------------------------------------------------------------------ */

struct job {
    volatile LONG busy;      /* a command has been started and not yet reaped */
    volatile LONG done;      /* the child has finished; rc is valid */
    LONG rc;
    char cmd[512];
    char outname[64], errname[64];
    int have_err;
    struct Task *parent;
    BYTE sigbit;
    struct Process *proc;
    ULONG clis[8];           /* CLI numbers that existed before we started */
    ULONG started;           /* Delay ticks elapsed, for the "still running" message */
};

static struct job g_job;

/* Record which CLI processes exist right now. System() runs the command in a
 * new CLI, so anything that appears after this snapshot is very likely ours —
 * which is how BREAK finds something to signal. */
static void cli_snapshot(ULONG *bits)
{
    ULONG n, max;
    memset(bits, 0, sizeof g_job.clis);
    Forbid();
    max = MaxCli();
    if (max > 255) max = 255;
    for (n = 1; n <= max; n++)
        if (FindCliProc(n)) bits[n >> 5] |= 1UL << (n & 31);
    Permit();
}

/* The child. Deliberately free of stdio — printf from a second process while
 * the parent may also be writing is not worth the risk. */
static void job_entry(void)
{
    struct job *j = &g_job;
    BPTR in, out, err = 0;

    j->rc = -1;
    out = Open((STRPTR)j->outname, MODE_NEWFILE);
    in = Open((STRPTR)"NIL:", MODE_OLDFILE);
    if (j->have_err) err = Open((STRPTR)j->errname, MODE_NEWFILE);

    if (in && out)
        /* NP_StackSize is passed straight through to the process SystemTags
         * creates for the command. It matters far more than it looks: a
         * command inherits this stack, and a GUI program given too little
         * does not fail politely on a 68k — it walks off the end of the stack
         * and takes the machine down.
         *
         * ScummVM launched from here crash-rebooted an A4000 repeatedly while
         * the same binary started fine from Workbench, because an icon
         * specifies its own stack and this did not. `ScummVM --version`
         * always worked, which is exactly the tell: it exits before the call
         * depth grows. */
        j->rc = SystemTags((STRPTR)j->cmd,
                           SYS_Input, (ULONG)in,
                           SYS_Output, (ULONG)out,
                           err ? SYS_Error : TAG_IGNORE, (ULONG)err,
                           NP_StackSize, AMIAGENT_CMD_STACK,
                           TAG_DONE);

    /* Ours to close — see the dos.library autodoc note in PROTOCOL.md. */
    if (in) Close(in);
    if (out) Close(out);
    if (err) Close(err);

    j->done = 1;
    Signal(j->parent, 1UL << j->sigbit);
}

/* Finish with a completed job: temp files gone, slot free for the next one. */
static void job_reap(void)
{
    DeleteFile((STRPTR)g_job.outname);
    if (g_job.have_err) DeleteFile((STRPTR)g_job.errname);
    g_job.busy = 0;
    g_job.done = 0;
    g_job.proc = NULL;
}

/* Send rc + captured output for a finished job. */
static int job_reply(int sock)
{
    BPTR fo, fe = 0;
    ULONG outlen, errlen = 0, marklen = 0;
    UBYTE rcbuf[4];
    int ok = 0;

    if (g_job.rc == -1) {
        job_reap();
        return send_err(sock, "could not launch command (not found, or no memory)");
    }

    fo = Open((STRPTR)g_job.outname, MODE_OLDFILE);
    outlen = file_size(fo);
    if (g_job.have_err) {
        fe = Open((STRPTR)g_job.errname, MODE_OLDFILE);
        errlen = file_size(fe);
        if (errlen) marklen = sizeof ERRMARK - 1;
    }

    put_be32(rcbuf, (ULONG)g_job.rc);
    if (send_hdr(sock, ST_OK, 4 + outlen + marklen + errlen) &&
        send_all(sock, rcbuf, 4)) {
        ok = 1;
        if (outlen && !pump(sock, fo, outlen)) ok = 0;
        if (ok && errlen) {
            if (!send_all(sock, (const UBYTE *)ERRMARK, marklen)) ok = 0;
            else if (!pump(sock, fe, errlen)) ok = 0;
        }
    }

    if (fo) Close(fo);
    if (fe) Close(fe);
    job_reap();
    return ok;
}

static int do_exec(int sock, const UBYTE *payload, ULONG len)
{
    UWORD timeout;
    ULONG waited = 0, limit;
    char msg[640];

    if (len < 2) return send_err(sock, "EXEC needs a timeout prefix");
    timeout = (UWORD)((payload[0] << 8) | payload[1]);
    payload += 2;
    len -= 2;
    if (!timeout) timeout = 120;

    /* An earlier command that outlived its deadline may have finished since. */
    if (g_job.busy && g_job.done) job_reap();

    if (g_job.busy) {
        sprintf(msg,
                "a command is still running after %lus and this agent runs one "
                "at a time: \"%s\". Send BREAK to try to stop it, or wait.",
                (unsigned long)(g_job.started / 50), g_job.cmd);
        return send_err(sock, msg);
    }

    if (len >= sizeof g_job.cmd) return send_err(sock, "command line too long");
    if (g_job.sigbit == 0 && (g_job.sigbit = AllocSignal(-1)) == -1)
        return send_err(sock, "no signal available");

    CopyMem((APTR)payload, g_job.cmd, len);
    g_job.cmd[len] = '\0';
    g_job.have_err = (((struct Library *)DOSBase)->lib_Version >= 47);
    sprintf(g_job.outname, "T:amiagent-%08lx.out", (unsigned long)FindTask(NULL));
    sprintf(g_job.errname, "T:amiagent-%08lx.err", (unsigned long)FindTask(NULL));
    g_job.parent = FindTask(NULL);
    g_job.rc = -1;
    g_job.done = 0;
    g_job.started = 0;

    say("exec: %s\n", g_job.cmd);

    ObtainSemaphore(&g_board.sem);
    g_board.cmds++;
    strncpy(g_board.lastcmd, g_job.cmd, sizeof g_board.lastcmd - 1);
    g_board.lastcmd[sizeof g_board.lastcmd - 1] = '\0';
    ReleaseSemaphore(&g_board.sem);

    cli_snapshot(g_job.clis);

    SetSignal(0, 1UL << g_job.sigbit);   /* clear any stale completion signal */
    g_job.busy = 1;
    g_job.proc = CreateNewProcTags(NP_Entry, (ULONG)job_entry,
                                   NP_Name, (ULONG)"amiagent-command",
                                   NP_StackSize, AMIAGENT_CMD_STACK,
                                   TAG_DONE);
    if (!g_job.proc) {
        g_job.busy = 0;
        return send_err(sock, "could not create a process for the command");
    }

    /* Poll rather than Wait() so Ctrl-C on the agent still works and the
     * deadline is honoured. 1/10s costs nothing next to running a command. */
    limit = (ULONG)timeout * 50UL;
    while (!g_job.done) {
        if (SetSignal(0, 0) & SIGBREAKF_CTRL_C) break;
        if (waited >= limit) break;
        Delay(5);
        waited += 5;
        g_job.started = waited;
    }

    if (g_job.done) return job_reply(sock);

    sprintf(msg,
            "still running after %us. The agent is NOT stuck - it is back to "
            "accepting connections, and other commands still work. Send BREAK "
            "to try to stop it, or run EXEC again later to pick up the result.",
            (unsigned)timeout);
    return send_err(sock, msg);
}

/* Best effort: signal the child, and any CLI that appeared after it started —
 * that second part is what actually reaches the command, because System() runs
 * it in its own CLI process that we never get a pointer to. */
static int do_break(int sock)
{
    ULONG now[8];
    ULONG n, max, hit = 0;
    char msg[160];

    if (!g_job.busy) return send_err(sock, "no command is running");
    if (g_job.done) { job_reap(); return send_resp(sock, ST_OK, NULL, 0); }

    Forbid();
    if (g_job.proc) { Signal(&g_job.proc->pr_Task, SIGBREAKF_CTRL_C); hit++; }
    Permit();

    cli_snapshot(now);
    Forbid();
    max = MaxCli();
    if (max > 255) max = 255;
    for (n = 1; n <= max; n++) {
        int was = (g_job.clis[n >> 5] >> (n & 31)) & 1;
        struct Process *p = FindCliProc(n);
        if (!was && p) { Signal(&p->pr_Task, SIGBREAKF_CTRL_C); hit++; }
    }
    Permit();

    say("break: signalled %ld process(es)\n", (long)hit);
    sprintf(msg, "sent Ctrl-C to %lu process(es); the command may take a "
                 "moment to notice, and not every program obeys it.",
            (unsigned long)hit);
    return send_resp(sock, ST_OK, msg, (ULONG)strlen(msg));
}

/* ------------------------------------------------------------------ *
 * ARexx — run an ARexx program string via rexxsyslib and return its RESULT.
 * The payload is the program source (RXFF_STRING), so a one-liner like
 *   address 'DOPUS.1'; command
 * drives any ARexx-aware application. Needs ARexx running (RexxMast, the
 * "REXX" port). Reply: s32 rc + RESULT string, like EXEC's (rc, output).
 * ------------------------------------------------------------------ */

struct RxsLib *RexxSysBase;   /* opened per call; the agent runs one at a time */

static int do_arexx(int sock, const UBYTE *payload, ULONG len)
{
    char *cmd;
    struct MsgPort *reply, *rexxport;
    struct RexxMsg *rm;
    LONG rc;
    char *result = NULL;
    UBYTE rcbuf[4];
    ULONG rlen;

    cmd = dup_cstr(payload, len);
    if (!cmd) return send_err(sock, "out of memory");

    RexxSysBase = (struct RxsLib *)OpenLibrary("rexxsyslib.library", 0);
    if (!RexxSysBase) { FreeVec(cmd); return send_err(sock, "rexxsyslib.library not available"); }

    reply = CreateMsgPort();
    if (!reply) {
        CloseLibrary((struct Library *)RexxSysBase); FreeVec(cmd);
        return send_err(sock, "could not create a reply port");
    }

    rm = CreateRexxMsg(reply, NULL, "AMIAGENT");
    if (!rm) {
        DeleteMsgPort(reply); CloseLibrary((struct Library *)RexxSysBase); FreeVec(cmd);
        return send_err(sock, "could not create the ARexx message");
    }
    rm->rm_Args[0] = CreateArgstring(cmd, (LONG)strlen(cmd));
    rm->rm_Action = RXCOMM | RXFF_STRING | RXFF_RESULT;   /* the string IS the program */

    /* Hand it to RexxMast's port, atomically so the port can't vanish under us. */
    Forbid();
    rexxport = FindPort("REXX");
    if (rexxport) PutMsg(rexxport, (struct Message *)rm);
    Permit();

    if (!rexxport) {
        DeleteArgstring((STRPTR)rm->rm_Args[0]);
        DeleteRexxMsg(rm);
        DeleteMsgPort(reply);
        CloseLibrary((struct Library *)RexxSysBase);
        FreeVec(cmd);
        return send_err(sock, "ARexx is not running (no REXX port - start RexxMast)");
    }

    WaitPort(reply);
    while (GetMsg(reply)) { /* exactly one - our own message comes back */ }

    rc = rm->rm_Result1;
    if (rc == 0 && rm->rm_Result2) result = (char *)rm->rm_Result2;   /* an argstring */

    rcbuf[0] = (UBYTE)(rc >> 24); rcbuf[1] = (UBYTE)(rc >> 16);
    rcbuf[2] = (UBYTE)(rc >> 8);  rcbuf[3] = (UBYTE)rc;
    rlen = result ? (ULONG)strlen(result) : 0;
    if (send_hdr(sock, ST_OK, 4 + rlen)) {
        send_all(sock, rcbuf, 4);
        if (rlen) send_all(sock, (const UBYTE *)result, rlen);
    }

    if (rc == 0 && rm->rm_Result2) DeleteArgstring((STRPTR)rm->rm_Result2);
    DeleteArgstring((STRPTR)rm->rm_Args[0]);
    DeleteRexxMsg(rm);
    DeleteMsgPort(reply);
    CloseLibrary((struct Library *)RexxSysBase);
    FreeVec(cmd);
    return 1;
}

static int do_get(int sock, const UBYTE *payload, ULONG len)
{
    char *path = dup_cstr(payload, len);
    BPTR fh;
    ULONG size = 0;
    int ok = 1;

    if (!path) return send_err(sock, "out of memory");

    fh = Open((STRPTR)path, MODE_OLDFILE);
    if (!fh) {
        char msg[300];
        sprintf(msg, "cannot open \"%s\" for reading (DOS error %ld)",
                path, (long)IoErr());
        FreeVec(path);
        return send_err(sock, msg);
    }
    FreeVec(path);

    size = file_size(fh);
    if (size > AMI_MAXFRAME) {
        Close(fh);
        return send_err(sock, "file is larger than the 16 MiB frame limit");
    }

    if (!send_hdr(sock, ST_OK, size)) { Close(fh); return 0; }
    ok = pump(sock, fh, size);
    Close(fh);
    return ok;
}

/* PUT streams straight to disk: the frame body is consumed IOBUF bytes at a
 * time, so pushing a 4 MB binary at a 2 MB A500 works. */
static int do_put(int sock, const UBYTE *head, ULONG headlen, ULONG total)
{
    UWORD pathlen;
    char *path;
    BPTR fh;
    ULONG remaining;
    int ok = 1;

    if (headlen < 2) return send_err(sock, "PUT payload too short");
    pathlen = (UWORD)((head[0] << 8) | head[1]);
    if ((ULONG)pathlen + 2 > headlen) return send_err(sock, "PUT path exceeds prefetched header");

    path = dup_cstr(head + 2, pathlen);
    if (!path) return send_err(sock, "out of memory");

    fh = Open((STRPTR)path, MODE_NEWFILE);
    if (!fh) {
        char msg[300];
        sprintf(msg, "cannot open \"%s\" for writing (DOS error %ld)",
                path, (long)IoErr());
        FreeVec(path);
        return send_err(sock, msg);
    }
    say("put: %s\n", path);
    FreeVec(path);

    /* Whatever of the body we already have buffered goes out first... */
    if (headlen > (ULONG)pathlen + 2) {
        ULONG have = headlen - (pathlen + 2);
        if (Write(fh, (APTR)(head + 2 + pathlen), (LONG)have) != (LONG)have) ok = 0;
    }
    /* ...then the rest streams from the socket. */
    remaining = total - headlen;
    while (ok && remaining) {
        ULONG want = remaining > IOBUF ? IOBUF : remaining;
        if (!recv_all(sock, g_io, want)) { ok = 0; break; }
        if (Write(fh, g_io, (LONG)want) != (LONG)want) { ok = 0; break; }
        remaining -= want;
    }
    Close(fh);

    if (!ok) return send_err(sock, "write failed (disk full or connection lost)");
    return send_resp(sock, ST_OK, NULL, 0);
}

static void fmt_protection(ULONG prot, char *out)
{
    /* h s p a are set-means-yes; r w e d are set-means-DENIED.
     * Bit 7 is what List renders as 'h'; NDK 3.2 spells the constant
     * FIBF_HOLD (older headers called the same bit FIBF_HIDDEN). */
    out[0] = (prot & FIBF_HOLD)    ? 'h' : '-';
    out[1] = (prot & FIBF_SCRIPT)  ? 's' : '-';
    out[2] = (prot & FIBF_PURE)    ? 'p' : '-';
    out[3] = (prot & FIBF_ARCHIVE) ? 'a' : '-';
    out[4] = (prot & FIBF_READ)    ? '-' : 'r';
    out[5] = (prot & FIBF_WRITE)   ? '-' : 'w';
    out[6] = (prot & FIBF_EXECUTE) ? '-' : 'e';
    out[7] = (prot & FIBF_DELETE)  ? '-' : 'd';
    out[8] = '\0';
}

static int do_list(int sock, const UBYTE *payload, ULONG len)
{
    char *path = dup_cstr(payload, len);
    BPTR lock;
    struct FileInfoBlock *fib;
    struct buf b;
    int ok;

    if (!path) return send_err(sock, "out of memory");

    lock = Lock((STRPTR)path, ACCESS_READ);
    if (!lock) {
        char msg[300];
        sprintf(msg, "cannot lock \"%s\" (DOS error %ld)", path, (long)IoErr());
        FreeVec(path);
        return send_err(sock, msg);
    }
    FreeVec(path);

    fib = (struct FileInfoBlock *)AllocDosObject(DOS_FIB, NULL);
    if (!fib) { UnLock(lock); return send_err(sock, "out of memory"); }

    if (!Examine(lock, fib)) {
        FreeDosObject(DOS_FIB, fib);
        UnLock(lock);
        return send_err(sock, "Examine() failed");
    }
    if (fib->fib_DirEntryType <= 0) {
        FreeDosObject(DOS_FIB, fib);
        UnLock(lock);
        return send_err(sock, "path is a file, not a directory");
    }

    buf_init(&b);
    ok = 1;
    while (ExNext(lock, fib)) {
        char line[512], prot[9], sday[LEN_DATSTRING], sdate[LEN_DATSTRING], stime[LEN_DATSTRING];
        struct DateTime dt;

        fmt_protection((ULONG)fib->fib_Protection, prot);

        dt.dat_Stamp = fib->fib_Date;
        dt.dat_Format = FORMAT_DOS;
        dt.dat_Flags = 0;
        dt.dat_StrDay = (STRPTR)sday;
        dt.dat_StrDate = (STRPTR)sdate;
        dt.dat_StrTime = (STRPTR)stime;
        if (!DateToStr(&dt)) { sdate[0] = '\0'; stime[0] = '\0'; }

        sprintf(line, "%c\t%ld\t%s\t%s %s\t%s\n",
                fib->fib_DirEntryType > 0 ? 'D' : 'F',
                (long)fib->fib_Size, prot, sdate, stime, fib->fib_FileName);
        if (!buf_str(&b, line)) { ok = 0; break; }
    }

    FreeDosObject(DOS_FIB, fib);
    UnLock(lock);

    if (!ok) { buf_free(&b); return send_err(sock, "out of memory building listing"); }
    ok = send_resp(sock, ST_OK, b.p, b.len);
    buf_free(&b);
    return ok;
}

static int do_info(int sock)
{
    struct buf b;
    char line[256];
    struct ExecBase *sb = SysBase;
    int ok = 1;
    BPTR lock;
    struct DosList *dl;

    buf_init(&b);

    sprintf(line, "agent=amiagent %s\n", AMIAGENT_VERSION); ok &= buf_str(&b, line);
    sprintf(line, "kickstart=%ld.%ld\n", (long)sb->LibNode.lib_Version,
            (long)sb->LibNode.lib_Revision); ok &= buf_str(&b, line);
    sprintf(line, "dos.library=%ld\n",
            (long)((struct Library *)DOSBase)->lib_Version); ok &= buf_str(&b, line);

    {
        ULONG af = sb->AttnFlags;
        const char *cpu = "68000";
        if (af & AFF_68060) cpu = "68060";
        else if (af & AFF_68040) cpu = "68040";
        else if (af & AFF_68030) cpu = "68030";
        else if (af & AFF_68020) cpu = "68020";
        else if (af & AFF_68010) cpu = "68010";
        sprintf(line, "cpu=%s\n", cpu); ok &= buf_str(&b, line);
        sprintf(line, "fpu=%s\n", (af & (AFF_68882 | AFF_68881)) ? "yes" : "no");
        ok &= buf_str(&b, line);
    }

    sprintf(line, "chipram_free=%lu\n", (unsigned long)AvailMem(MEMF_CHIP));
    ok &= buf_str(&b, line);
    sprintf(line, "fastram_free=%lu\n", (unsigned long)AvailMem(MEMF_FAST));
    ok &= buf_str(&b, line);
    sprintf(line, "largest_free=%lu\n", (unsigned long)AvailMem(MEMF_ANY | MEMF_LARGEST));
    ok &= buf_str(&b, line);

    /* Mounted volumes, straight off the DOS list. */
    ok &= buf_str(&b, "volumes=");
    dl = LockDosList(LDF_VOLUMES | LDF_READ);
    if (dl) {
        int first = 1;
        while ((dl = NextDosEntry(dl, LDF_VOLUMES | LDF_READ))) {
            UBYTE *bn = (UBYTE *)BADDR(dl->dol_Name);
            if (bn && bn[0]) {
                char name[64];
                int n = bn[0] < 60 ? bn[0] : 60;
                CopyMem(bn + 1, name, n);
                name[n] = '\0';
                if (!first) ok &= buf_str(&b, ",");
                ok &= buf_str(&b, name);
                first = 0;
            }
        }
        UnLockDosList(LDF_VOLUMES | LDF_READ);
    }
    ok &= buf_str(&b, "\n");

    /* Current directory, so a caller knows where relative paths land. */
    lock = Lock((STRPTR)"", ACCESS_READ);
    if (lock) {
        char cwd[256];
        if (NameFromLock(lock, (STRPTR)cwd, sizeof cwd)) {
            sprintf(line, "cwd=%s\n", cwd);
            ok &= buf_str(&b, line);
        }
        UnLock(lock);
    }

#ifndef AMIAGENT_OWN_LIBBASES
    if (GfxBase) {
        sprintf(line, "graphics.library=%ld\n",
                (long)((struct Library *)GfxBase)->lib_Version);
        ok &= buf_str(&b, line);
    }
#endif

    if (!ok) { buf_free(&b); return send_err(sock, "out of memory building sysinfo"); }
    ok = send_resp(sock, ST_OK, b.p, b.len);
    buf_free(&b);
    return ok;
}

/* ------------------------------------------------------------------ *
 * Screenshot
 * ------------------------------------------------------------------ */

/* Truecolor/RTG capture through cybergraphics.library, which Picasso96 and
 * CyberGraphX both provide. The library isn't in NDK 3.2, so the inline header
 * under vendor/cgx is generated by fd2pragma from the CyberGraphX SDK's own
 * .fd file — the offsets are the SDK's, not guesses.
 *
 * RECTFMT_RGB gives packed 24-bit RGB, which is already the wire format, so
 * there is nothing to repack. */
static int screen_still_open(struct Screen *scr);   /* defined below */

static int shot_truecolor(int sock, struct Screen *scr, struct RastPort *rp,
                          UWORD sx, UWORD sy, UWORD w, UWORD h)
{
    ULONG rowbytes = (ULONG)w * 3UL;
    ULONG pixbytes = rowbytes * (ULONG)h;
    UBYTE *pix, hdr[8];
    int ok = 0;

    if (!CyberGfxBase)
        CyberGfxBase = OpenLibrary((STRPTR)"cybergraphics.library", 40);
    if (!CyberGfxBase)
        return send_err(sock,
            "this is a truecolor screen and cybergraphics.library v40+ is not "
            "available, so it cannot be captured");

    if (8 + pixbytes > AMI_MAXFRAME)
        return send_err(sock, "screen is too large for the 16 MiB frame limit");

    pix = (UBYTE *)AllocVec(pixbytes, MEMF_ANY);
    if (!pix) return send_err(sock, "not enough memory for the screen buffer");

    /* Buffer is already allocated, so the read itself can run with the task
     * switcher held off — that is what makes the screen safe to touch. The
     * slow part (pushing it down the socket) happens after Permit(). */
    Forbid();
    if (!screen_still_open(scr)) {
        Permit();
        FreeVec(pix);
        return send_err(sock, "that screen closed while it was being captured");
    }
    ok = (ReadPixelArray(pix, 0, 0, (UWORD)rowbytes, rp, sx, sy, w, h, RECTFMT_RGB) != 0);
    Permit();
    if (!ok) {
        FreeVec(pix);
        return send_err(sock, "cybergraphics ReadPixelArray() failed");
    }
    ok = 0;

    hdr[0] = SHOT_RGB24;
    hdr[1] = 0;
    put_be16(hdr + 2, w);
    put_be16(hdr + 4, h);
    put_be16(hdr + 6, 0);   /* no palette */

    say("shot: %ldx%ld truecolor\n", (long)w, (long)h);
    if (send_hdr(sock, ST_OK, 8 + pixbytes) &&
        send_all(sock, hdr, 8) &&
        send_all(sock, pix, pixbytes))
        ok = 1;

    FreeVec(pix);
    return ok;
}

/* Screens are numbered front to back, so 0 is whatever is on top right now.
 * Anything further back is still capturable, which matters when a program dies
 * and leaves an empty screen in front of a perfectly healthy Workbench. */
static struct Screen *nth_screen(UBYTE index)
{
    struct Screen *s;
    UBYTE i = 0;

    Forbid();
    for (s = IntuitionBase->FirstScreen; s; s = s->NextScreen, i++)
        if (i == index) break;
    Permit();
    return s;
}

/* Is this screen still open? Call with the task switcher held off.
 *
 * A screen pointer taken under Forbid() and dereferenced after Permit() is a
 * use-after-free waiting for its moment, and the moment is precisely when a
 * program is starting up or dying — which is when something is most likely to
 * be capturing the screen to find out what went wrong. Reading a freed Screen
 * on AmigaOS does not fail politely; it takes the machine down. This agent
 * crash-rebooted an A4000 doing exactly that while ScummVM opened its screen. */
static int screen_still_open(struct Screen *scr)
{
    struct Screen *s;
    for (s = IntuitionBase->FirstScreen; s; s = s->NextScreen)
        if (s == scr) return 1;
    return 0;
}

static int gfx_ready(int sock)
{
#ifdef AMIAGENT_OWN_LIBBASES
    if (!IntuitionBase)
        IntuitionBase = (struct IntuitionBase *)OpenLibrary((STRPTR)"intuition.library", 37);
    if (!GfxBase)
        GfxBase = (struct GfxBase *)OpenLibrary((STRPTR)"graphics.library", 39);
#endif
    if (!IntuitionBase || !GfxBase)
        return send_err(sock, "intuition.library/graphics.library unavailable");
    if (((struct Library *)GfxBase)->lib_Version < 39)
        return send_err(sock, "screenshots need graphics.library v39+ (OS 3.0)");
    return -1;   /* -1 means "fine"; 0 means an error was already sent */
}

/* Pull the optional region/screen selector off a CMD_SHOT or CMD_HASH payload
 * and clamp it to the screen. An absent or zero width/height means "out to the
 * edge", so an empty payload still reads as the whole screen. */
static struct Screen *parse_region(int sock, const UBYTE *p, ULONG len,
                                   UWORD *x, UWORD *y, UWORD *w, UWORD *h)
{
    struct Screen *scr;
    UBYTE index = 0;

    if (len >= 9) index = p[8];
    scr = nth_screen(index);
    if (!scr) { send_err(sock, "no such screen"); return NULL; }

    *x = 0; *y = 0; *w = scr->Width; *h = scr->Height;
    if (len >= 8) {
        *x = (UWORD)((p[0] << 8) | p[1]);
        *y = (UWORD)((p[2] << 8) | p[3]);
        *w = (UWORD)((p[4] << 8) | p[5]);
        *h = (UWORD)((p[6] << 8) | p[7]);
    }
    if (*x >= scr->Width || *y >= scr->Height) {
        send_err(sock, "region starts outside the screen");
        return NULL;
    }
    if (!*w || *x + *w > scr->Width)  *w = (UWORD)(scr->Width  - *x);
    if (!*h || *y + *h > scr->Height) *h = (UWORD)(scr->Height - *y);
    return scr;
}

/* Chunky (planar, depth <= 8) capture of one rectangle. Caller FreeVec()s
 * *pix_out. Returns 0 having already sent an error, or 1 on success. */
static int grab_chunky(int sock, struct Screen *scr,
                       UWORD x, UWORD y, UWORD w, UWORD h,
                       UBYTE **pix_out, UBYTE *pal, UWORD *ncolors_out)
{
    struct RastPort *rp = &scr->RastPort, temprp;
    struct BitMap *tempbm;
    UWORD depth = (UWORD)GetBitMapAttr(rp->BitMap, BMA_DEPTH);
    UWORD ncolors, i;
    ULONG stride, rgb[3];
    UBYTE *pix;

    /* ReadPixelArray8 wants rows padded out to a multiple of 16 pixels. */
    stride = ((ULONG)w + 15UL) & ~15UL;
    pix = (UBYTE *)AllocVec(stride * (ULONG)h, MEMF_ANY);
    if (!pix) return send_err(sock, "not enough memory for the screen buffer");

    tempbm = AllocBitMap(stride, 1, 8, 0, NULL);
    if (!tempbm) { FreeVec(pix); return send_err(sock, "not enough memory for the temp bitmap"); }

    temprp = *rp;
    temprp.Layer = NULL;
    temprp.BitMap = tempbm;

    /* Both buffers exist by now, so nothing here needs to allocate — which
     * means the read can run under Forbid() and the screen cannot vanish
     * underneath it. Re-check it is still open first: it may already have
     * closed between being looked up and getting here. */
    Forbid();
    if (!screen_still_open(scr)) {
        Permit();
        FreeBitMap(tempbm);
        FreeVec(pix);
        return send_err(sock, "that screen closed while it was being captured");
    }
    {
        LONG r = ReadPixelArray8(rp, x, y, (UWORD)(x + w - 1), (UWORD)(y + h - 1),
                                 pix, &temprp);
        Permit();
        if (r == -1) {
            FreeBitMap(tempbm);
            FreeVec(pix);
            return send_err(sock, "ReadPixelArray8() failed");
        }
    }
    FreeBitMap(tempbm);

    /* Repack from the padded stride down to exactly `w` bytes per row. The
     * destination offset is always <= the source offset, so a forward copy is
     * safe even though the regions overlap. */
    if (stride != (ULONG)w) {
        UWORD row;
        for (row = 1; row < h; row++)
            memmove(pix + (ULONG)row * w, pix + (ULONG)row * stride, w);
    }

    ncolors = (UWORD)(1U << depth);
    if (ncolors > 256) ncolors = 256;
    for (i = 0; i < ncolors; i++) {
        GetRGB32(scr->ViewPort.ColorMap, i, 1, rgb);
        pal[i * 3 + 0] = (UBYTE)(rgb[0] >> 24);
        pal[i * 3 + 1] = (UBYTE)(rgb[1] >> 24);
        pal[i * 3 + 2] = (UBYTE)(rgb[2] >> 24);
    }

    *pix_out = pix;
    *ncolors_out = ncolors;
    return 1;
}

/* Grab a screen, or a rectangle of one. Planar screens (depth <= 8) come back
 * as palette + chunky via ReadPixelArray8; RTG/truecolor goes through
 * cybergraphics. PNG encoding happens on the Mac — zlib has no business on a
 * 68000. */
static int do_shot(int sock, const UBYTE *body, ULONG len)
{
    struct Screen *scr;
    UWORD x, y, w, h, depth, ncolors = 0;
    ULONG pixbytes, palbytes, total;
    UBYTE *pix = NULL, hdr[8];
    UBYTE pal[256 * 3];
    int ok = 0;

    if (!gfx_ready(sock)) return 0;
    scr = parse_region(sock, body, len, &x, &y, &w, &h);
    if (!scr) return 0;

    depth = (UWORD)GetBitMapAttr(scr->RastPort.BitMap, BMA_DEPTH);
    if (depth > 8)
        return shot_truecolor(sock, scr, &scr->RastPort, x, y, w, h);

    if (!grab_chunky(sock, scr, x, y, w, h, &pix, pal, &ncolors)) return 0;

    pixbytes = (ULONG)w * (ULONG)h;
    palbytes = (ULONG)ncolors * 3UL;

    hdr[0] = SHOT_CHUNKY;
    hdr[1] = 0;
    put_be16(hdr + 2, w);
    put_be16(hdr + 4, h);
    put_be16(hdr + 6, ncolors);

    total = 8 + palbytes + pixbytes;
    say("shot: %ldx%ld at %ld,%ld depth %ld\n",
        (long)w, (long)h, (long)x, (long)y, (long)depth);

    if (send_hdr(sock, ST_OK, total) &&
        send_all(sock, hdr, 8) &&
        send_all(sock, pal, palbytes) &&
        send_all(sock, pix, pixbytes))
        ok = 1;

    FreeVec(pix);
    return ok;
}

/* A checksum over a region, so a caller can poll "has this changed yet?"
 * without paying for the pixels. Watching for a cutscene to end, or for a
 * status line to update, is otherwise a stream of full frames pulled across
 * the wire to compare a few hundred bytes. */
static int do_hash(int sock, const UBYTE *body, ULONG len)
{
    struct Screen *scr;
    UWORD x, y, w, h, depth, ncolors = 0;
    UBYTE *pix = NULL, pal[256 * 3], out[4];
    ULONG i, n, sum = 2166136261UL;   /* FNV-1a */

    if (!gfx_ready(sock)) return 0;
    scr = parse_region(sock, body, len, &x, &y, &w, &h);
    if (!scr) return 0;

    depth = (UWORD)GetBitMapAttr(scr->RastPort.BitMap, BMA_DEPTH);
    if (depth > 8) {
        ULONG rowbytes = (ULONG)w * 3UL;
        if (!CyberGfxBase)
            CyberGfxBase = OpenLibrary((STRPTR)"cybergraphics.library", 40);
        if (!CyberGfxBase)
            return send_err(sock, "truecolor screen needs cybergraphics.library v40+");
        n = rowbytes * (ULONG)h;
        pix = (UBYTE *)AllocVec(n, MEMF_ANY);
        if (!pix) return send_err(sock, "not enough memory for the region buffer");
        if (ReadPixelArray(pix, 0, 0, (UWORD)rowbytes, &scr->RastPort,
                           x, y, w, h, RECTFMT_RGB) == 0) {
            FreeVec(pix);
            return send_err(sock, "cybergraphics ReadPixelArray() failed");
        }
    } else {
        if (!grab_chunky(sock, scr, x, y, w, h, &pix, pal, &ncolors)) return 0;
        n = (ULONG)w * (ULONG)h;
    }

    for (i = 0; i < n; i++) {
        sum ^= pix[i];
        sum *= 16777619UL;
    }
    FreeVec(pix);

    put_be32(out, sum);
    say("hash: %ldx%ld at %ld,%ld\n", (long)w, (long)h, (long)x, (long)y);
    return send_hdr(sock, ST_OK, 4) && send_all(sock, out, 4);
}

/* Every open screen, front to back. Dimensions and mode let the client work
 * out its own coordinate mapping instead of discovering it by trial. */
/* Static, not on the stack. An AmigaOS process gets a few KB of stack and this
 * reply buffer is 800-odd bytes; making it local silently overflowed and hung
 * the agent inside the handler, so it never returned to its accept loop. The
 * daemon handles one connection at a time in this process, so a single shared
 * buffer is safe. */
#define MAX_SCREENS 16
static UBYTE g_screenbuf[1 + MAX_SCREENS * (1 + 2 + 2 + 1 + 4 + 1 + 40)];

static int do_screens(int sock)
{
    struct Screen *s;
    UBYTE *buf = g_screenbuf;
    ULONG at = 1;
    UBYTE count = 0;

    if (!gfx_ready(sock)) return 0;

    Forbid();
    for (s = IntuitionBase->FirstScreen; s && count < MAX_SCREENS; s = s->NextScreen) {
        const char *title = (const char *)s->Title;
        UBYTE tlen = 0;
        if (title) while (tlen < 40 && title[tlen]) tlen++;

        buf[at++] = count;
        put_be16(buf + at, (UWORD)s->Width);  at += 2;
        put_be16(buf + at, (UWORD)s->Height); at += 2;
        buf[at++] = (UBYTE)GetBitMapAttr(s->RastPort.BitMap, BMA_DEPTH);
        put_be32(buf + at, GetVPModeID(&s->ViewPort)); at += 4;
        buf[at++] = tlen;
        if (tlen) { memcpy(buf + at, title, tlen); at += tlen; }
        count++;
    }
    Permit();

    buf[0] = count;
    say("screens: %ld\n", (long)count);
    return send_hdr(sock, ST_OK, at) && send_all(sock, buf, at);
}

/* Where is the pointer? Cheap enough to ask before every click.
 *
 * This is Intuition's pointer. A program that tracks raw mouse deltas keeps a
 * cursor of its own that nothing else can see, so agreement here is necessary
 * but not sufficient when driving one of those. */
static int do_pointer(int sock)
{
    struct Screen *scr;
    UBYTE out[8];

    if (!gfx_ready(sock)) return 0;
    scr = nth_screen(0);
    if (!scr) return send_err(sock, "no screen open");

    Forbid();
    put_be16(out + 0, (UWORD)scr->MouseX);
    put_be16(out + 2, (UWORD)scr->MouseY);
    put_be16(out + 4, (UWORD)scr->Width);
    put_be16(out + 6, (UWORD)scr->Height);
    Permit();

    return send_hdr(sock, ST_OK, 8) && send_all(sock, out, 8);
}

/* ------------------------------------------------------------------ *
 * Intuition object walker (SPIKE)
 * ------------------------------------------------------------------ *
 * A read-only, semantic snapshot of the frontmost screen: its windows and
 * their gadgets, with roles, labels and click-ready absolute bounds. This is
 * the "accessibility tree" complement to CMD_SHOT - the client can reason
 * about the GUI (find the button labelled "OK") instead of hunting pixels.
 * Standard Intuition/GadTools gadgets only; custom screens and bitmap content
 * are invisible here, which is exactly what CMD_SHOT remains for.
 *
 * Walked entirely under Forbid() so the window/gadget lists cannot mutate
 * mid-traversal, into a fixed static buffer (no allocation while forbidden).
 *
 * Line format, one record per line, X/Y/W/H in absolute screen pixels:
 *   S 0 WxH depth=D "title"
 *   W idx X Y WxH state "title"
 *   G id X Y WxH kind state "label" ["value"]
 * A click at X+W/2, Y+H/2 of a G record lands on that gadget.
 */
#define UITREE_MAX 16000
static char g_uitree[UITREE_MAX + 16];

/* Clip src into a fixed field, defanging quotes and control characters so the
 * one-line text format stays parseable (and a hostile window title cannot
 * inject newlines into the reply). */
static void ui_san(char *dst, size_t dstsz, const char *src)
{
    size_t i = 0;
    if (!src) { dst[0] = '\0'; return; }
    for (; src[i] && i < dstsz - 1; i++) {
        unsigned char c = (unsigned char)src[i];
        dst[i] = (c < 32 || c > 126 || c == '"') ? ' ' : (char)c;
    }
    dst[i] = '\0';
}

static const char *ui_kind(UWORD gt)
{
    if (gt & GTYP_SYSGADGET) {
        switch (gt & GTYP_SYSTYPEMASK) {
        case GTYP_SIZING:    return "sys:size";
        case GTYP_WDRAGGING: return "sys:drag";
        case GTYP_WDEPTH:    return "sys:depth";
        case GTYP_WZOOM:     return "sys:zoom";
        case GTYP_CLOSE:     return "sys:close";
        case GTYP_ICONIFY:   return "sys:iconify";
        default:             return "sys";
        }
    }
    switch (gt & GTYP_GTYPEMASK) {
    case GTYP_BOOLGADGET:   return "button";
    case GTYP_STRGADGET:    return "string";
    case GTYP_PROPGADGET:   return "prop";
    case GTYP_CUSTOMGADGET: return "custom";
    default:                return "gadget";
    }
}

static ULONG ui_put(ULONG at, const char *s, int *trunc)
{
    ULONG n = (ULONG)strlen(s);
    if (at + n >= UITREE_MAX) { *trunc = 1; return at; }
    memcpy(g_uitree + at, s, n);
    return at + n;
}

/* Nonempty and all decimal digits? Tells a numeric selector (window index /
 * GadgetID) from a text one (title / label substring). */
static int ui_is_num(const char *s)
{
    if (!s || !*s) return 0;
    for (; *s; s++) if (*s < '0' || *s > '9') return 0;
    return 1;
}

static int ui_num(const char *s)
{
    int v = 0;
    for (; *s >= '0' && *s <= '9'; s++) v = v * 10 + (*s - '0');
    return v;
}

/* Case-insensitive substring test (needle within haystack). Empty needle
 * matches anything; a NULL haystack matches nothing. */
static int ui_ci_contains(const char *hay, const char *needle)
{
    size_t nl, k;
    if (!needle || !needle[0]) return 1;
    if (!hay) return 0;
    nl = strlen(needle);
    for (; *hay; hay++) {
        for (k = 0; k < nl; k++) {
            int a = hay[k], b = needle[k];
            if (a >= 'A' && a <= 'Z') a += 32;
            if (b >= 'A' && b <= 'Z') b += 32;
            if (!hay[k] || a != b) break;
        }
        if (k == nl) return 1;
    }
    return 0;
}

/* A gadget's text label (GadTools/plain Intuition), into dst. Empty if the
 * gadget labels with an image or has no text. */
static void ui_label(struct Gadget *g, char *dst, size_t dstsz)
{
    int labk = g->Flags & GFLG_LABELMASK;
    dst[0] = '\0';
    if (labk == GFLG_LABELITEXT && g->GadgetText && g->GadgetText->IText)
        ui_san(dst, dstsz, (const char *)g->GadgetText->IText);
    else if (labk == 0x1000 /* GFLG_LABELSTRING */ && g->GadgetText)
        ui_san(dst, dstsz, (const char *)g->GadgetText);
}

static int do_uitree(int sock)
{
    struct Screen *scr;
    struct Window *w;
    struct Gadget *g;
    ULONG at = 0;
    int wi = 0, trunc = 0;
    char field[64], val[80], line[300];

    if (!gfx_ready(sock)) return 0;

    Forbid();
    scr = IntuitionBase->FirstScreen;                    /* frontmost screen */
    if (scr) {
        ui_san(field, sizeof field, (const char *)scr->Title);
        sprintf(line, "S 0 %dx%d depth=%d \"%s\"\n",
                (int)scr->Width, (int)scr->Height,
                (int)GetBitMapAttr(scr->RastPort.BitMap, BMA_DEPTH), field);
        at = ui_put(at, line, &trunc);

        for (w = scr->FirstWindow; w && !trunc; w = w->NextWindow, wi++) {
            ui_san(field, sizeof field, (const char *)w->Title);
            sprintf(line, "W %d %d %d %dx%d %s \"%s\"\n", wi,
                    (int)w->LeftEdge, (int)w->TopEdge,
                    (int)w->Width, (int)w->Height,
                    (w->Flags & WFLG_WINDOWACTIVE) ? "active" : "-", field);
            at = ui_put(at, line, &trunc);

            for (g = w->FirstGadget; g && !trunc; g = g->NextGadget) {
                int gx = g->LeftEdge, gy = g->TopEdge;
                int gw = g->Width,    gh = g->Height;
                const char *state;

                /* Resolve the relative-edge flags to an absolute screen box,
                 * so X+W/2,Y+H/2 is directly clickable. */
                if (g->Flags & GFLG_RELRIGHT)  gx += w->Width;
                if (g->Flags & GFLG_RELBOTTOM) gy += w->Height;
                if (g->Flags & GFLG_RELWIDTH)  gw += w->Width;
                if (g->Flags & GFLG_RELHEIGHT) gh += w->Height;
                gx += w->LeftEdge; gy += w->TopEdge;

                ui_label(g, field, sizeof field);

                val[0] = '\0';
                if ((g->GadgetType & GTYP_GTYPEMASK) == GTYP_STRGADGET && g->SpecialInfo) {
                    struct StringInfo *si = (struct StringInfo *)g->SpecialInfo;
                    if (si->Buffer) {
                        char tmp[48];
                        ui_san(tmp, sizeof tmp, (const char *)si->Buffer);
                        sprintf(val, " \"%s\"", tmp);
                    }
                }

                state = (g->Flags & GFLG_DISABLED) ? "disabled"
                      : (g->Flags & GFLG_SELECTED) ? "selected" : "-";

                sprintf(line, "G %d %d %d %dx%d %s %s \"%s\"%s\n",
                        (int)g->GadgetID, gx, gy, gw, gh,
                        ui_kind(g->GadgetType), state, field, val);
                at = ui_put(at, line, &trunc);
            }
        }
    }
    Permit();

    if (trunc) at = ui_put(at, "; truncated\n", &trunc);
    say("uitree: %ld bytes%s\n", (long)at, trunc ? " (truncated)" : "");
    return send_resp(sock, ST_OK, (UBYTE *)g_uitree, at);
}

/* ------------------------------------------------------------------ *
 * Input injection
 * ------------------------------------------------------------------ */

/* Posting events through input.device puts them at the top of the same queue
 * the real mouse and keyboard feed, so every application sees them as genuine
 * input — no per-program cooperation needed.
 *
 * Text goes through keymap.library's MapANSI() rather than a table of our own,
 * which is what makes typing correct on a German (or any other) keymap: the
 * Amiga decides which raw codes and qualifiers produce the character. */

static struct MsgPort *g_inport = NULL;
static struct IOStdReq *g_inreq = NULL;

static int input_open(void)
{
    if (g_inreq) return 1;
    g_inport = CreateMsgPort();
    if (!g_inport) return 0;
    g_inreq = (struct IOStdReq *)CreateIORequest(g_inport, sizeof(struct IOStdReq));
    if (!g_inreq) { DeleteMsgPort(g_inport); g_inport = NULL; return 0; }
    if (OpenDevice((STRPTR)"input.device", 0, (struct IORequest *)g_inreq, 0)) {
        DeleteIORequest((struct IORequest *)g_inreq);
        DeleteMsgPort(g_inport);
        g_inreq = NULL; g_inport = NULL;
        return 0;
    }
    return 1;
}

static void input_close(void)
{
    if (!g_inreq) return;
    CloseDevice((struct IORequest *)g_inreq);
    DeleteIORequest((struct IORequest *)g_inreq);
    DeleteMsgPort(g_inport);
    g_inreq = NULL; g_inport = NULL;
}

static void ie_init(struct InputEvent *ie)
{
    memset(ie, 0, sizeof *ie);
    ie->ie_NextEvent = NULL;
    ie->ie_SubClass = 0;
}

static void input_post(struct InputEvent *ie)
{
    g_inreq->io_Command = IND_WRITEEVENT;
    g_inreq->io_Flags = 0;
    g_inreq->io_Length = sizeof(struct InputEvent);
    g_inreq->io_Data = (APTR)ie;
    DoIO((struct IORequest *)g_inreq);
}

static void input_move(WORD x, WORD y)
{
    struct InputEvent ie;
    ie_init(&ie);
    ie.ie_Class = IECLASS_POINTERPOS;   /* absolute screen coordinates */
    ie.ie_X = x;
    ie.ie_Y = y;
    input_post(&ie);
}

/* Relative motion, for programs that never ask Intuition where the pointer is.
 * SDL is the common case: it tracks its own cursor from RAWMOUSE deltas and
 * ignores IECLASS_POINTERPOS warps entirely, so input_move() above moves the
 * Intuition pointer while the program's cursor stays exactly where it was.
 *
 * The delta is emitted in small steps rather than one jump. ie_X is a WORD and
 * would carry the whole distance, but a program that accumulates motion per
 * event — or any pointer acceleration in the path — follows a run of modest
 * steps far more predictably than a single large one. */
#define RMOVE_STEP 32

static void input_rmove_once(WORD dx, WORD dy)
{
    struct InputEvent ie;
    ie_init(&ie);
    ie.ie_Class = IECLASS_RAWMOUSE;
    ie.ie_Code = IECODE_NOBUTTON;
    ie.ie_Qualifier = IEQUALIFIER_RELATIVEMOUSE;
    ie.ie_X = dx;
    ie.ie_Y = dy;
    input_post(&ie);
}

static void input_rmove(WORD dx, WORD dy)
{
    while (dx || dy) {
        WORD sx = dx, sy = dy;
        if (sx >  RMOVE_STEP) sx =  RMOVE_STEP;
        if (sx < -RMOVE_STEP) sx = -RMOVE_STEP;
        if (sy >  RMOVE_STEP) sy =  RMOVE_STEP;
        if (sy < -RMOVE_STEP) sy = -RMOVE_STEP;
        input_rmove_once(sx, sy);
        dx = (WORD)(dx - sx);
        dy = (WORD)(dy - sy);
        Delay(1);
    }
}

/* Drive the pointer hard into the top-left corner, giving the caller a known
 * origin to measure a relative move from. Overshooting is the point: both
 * Intuition and SDL clamp at the edge, so a deliberate excess of travel is
 * what makes the resulting position certain rather than approximate. */
static void input_home(void)
{
    int i;
    for (i = 0; i < 24; i++) {
        input_rmove_once(-(RMOVE_STEP * 4), -(RMOVE_STEP * 4));
        Delay(1);
    }
}

static UWORD button_code(UBYTE button)
{
    if (button == IN_BTN_RIGHT) return IECODE_RBUTTON;
    if (button == IN_BTN_MIDDLE) return IECODE_MBUTTON;
    return IECODE_LBUTTON;
}

static void input_button(UBYTE button, int down)
{
    struct InputEvent ie;
    ie_init(&ie);
    ie.ie_Class = IECLASS_RAWMOUSE;
    ie.ie_Code = button_code(button) | (down ? 0 : IECODE_UP_PREFIX);
    /* Relative-mouse with a zero delta: a button change and nothing else. */
    ie.ie_Qualifier = IEQUALIFIER_RELATIVEMOUSE;
    ie.ie_X = 0;
    ie.ie_Y = 0;
    input_post(&ie);
}

/* A full click gesture at an absolute screen position: warp, settle, then
 * press/release `count` times. Shared by the IN_CLICK op and CMD_UIACT so the
 * timing lives in one place. Caller must have called input_open() first. */
static void input_click(WORD x, WORD y, UBYTE button, UBYTE count)
{
    UBYTE n;
    if (!count) count = 1;
    input_move(x, y);
    Delay(2);   /* let Intuition settle on the new pointer position */
    for (n = 0; n < count; n++) {
        input_button(button, 1);
        Delay(2);
        input_button(button, 0);
        if (n + 1 < count) Delay(2);
    }
}

static void input_key(UBYTE raw, int down, UWORD qual)
{
    struct InputEvent ie;
    ie_init(&ie);
    ie.ie_Class = IECLASS_RAWKEY;
    ie.ie_Code = raw | (down ? 0 : IECODE_UP_PREFIX);
    ie.ie_Qualifier = qual;
    input_post(&ie);
}

/* One character, via the active keymap. MapANSI hands back up to three
 * code/qualifier pairs; the last is the key itself and any earlier ones are
 * dead-key prefixes that belong in the ie_Prev*Down fields. */
static void input_char(UBYTE ch)
{
    UBYTE rbuf[6];
    LONG actual;
    UBYTE *r = rbuf;
    struct InputEvent ie;

    actual = MapANSI((STRPTR)&ch, 1, (STRPTR)rbuf, 3, NULL);
    if (actual < 1) return;   /* not typeable on this keymap */

    ie_init(&ie);
    ie.ie_Class = IECLASS_RAWKEY;
    if (actual >= 3) { ie.ie_Prev2DownCode = r[0]; ie.ie_Prev2DownQual = r[1]; r += 2; }
    if (actual >= 2) { ie.ie_Prev1DownCode = r[0]; ie.ie_Prev1DownQual = r[1]; r += 2; }

    ie.ie_Code = r[0];
    ie.ie_Qualifier = r[1];
    input_post(&ie);

    ie.ie_Code = r[0] | IECODE_UP_PREFIX;
    input_post(&ie);
}

/* Invoke a menu item by its keyboard shortcut: right-Amiga + the item's
 * Command char. The keymap gives us the raw code for the character; adding
 * IEQUALIFIER_RCOMMAND makes Intuition treat it as the menu shortcut for the
 * active window. Reliable regardless of where the menu box would lay out. */
static void input_menushort(UBYTE ch)
{
    UBYTE rbuf[6];
    LONG actual;
    struct InputEvent ie;

    actual = MapANSI((STRPTR)&ch, 1, (STRPTR)rbuf, 3, NULL);
    if (actual < 1) return;

    ie_init(&ie);
    ie.ie_Class = IECLASS_RAWKEY;
    ie.ie_Code = rbuf[0];
    ie.ie_Qualifier = (UWORD)(rbuf[1] | IEQUALIFIER_RCOMMAND);
    input_post(&ie);
    ie.ie_Code = rbuf[0] | IECODE_UP_PREFIX;
    input_post(&ie);
}

static int do_input(int sock, const UBYTE *p, ULONG len)
{
    UBYTE op;

    if (!len) return send_err(sock, "empty input request");
    if (!input_open()) return send_err(sock, "cannot open input.device");

    op = p[0];
    p++; len--;

    switch (op) {
    case IN_MOVE:
        if (len < 4) return send_err(sock, "MOVE needs x,y");
        input_move((WORD)((p[0] << 8) | p[1]), (WORD)((p[2] << 8) | p[3]));
        break;

    case IN_BUTTON:
        if (len < 2) return send_err(sock, "BUTTON needs button,down");
        input_button(p[0], p[1]);
        break;

    case IN_KEY:
        if (len < 4) return send_err(sock, "KEY needs rawcode,down,qualifier");
        input_key(p[0], p[1], (UWORD)((p[2] << 8) | p[3]));
        break;

    case IN_TEXT: {
        ULONG i;
        for (i = 0; i < len; i++) {
            input_char(p[i]);
            /* A tick between characters: the input queue is shallow, and
             * applications that poll rather than buffer drop anything faster. */
            Delay(1);
        }
        break;
    }

    case IN_RMOVE:
        if (len < 4) return send_err(sock, "RMOVE needs dx,dy");
        input_rmove((WORD)((p[0] << 8) | p[1]), (WORD)((p[2] << 8) | p[3]));
        break;

    case IN_HOME:
        input_home();
        break;

    /* A whole sequence in one request, so the gaps between events are the
     * Amiga's own ticks rather than however long each round trip took. Sending
     * a press and a release as two requests puts hundreds of milliseconds
     * between them, which programs read as a held button rather than a click —
     * that is a real bug, not a theoretical one. */
    case IN_SCRIPT: {
        UBYTE n, i;
        ULONG at = 1;
        if (!len) return send_err(sock, "SCRIPT needs a count");
        n = p[0];
        for (i = 0; i < n; i++) {
            UBYTE op2;
            if (at >= len) return send_err(sock, "SCRIPT ended early");
            op2 = p[at++];
            switch (op2) {
            case IN_MOVE:
                if (at + 4 > len) return send_err(sock, "SCRIPT MOVE needs x,y");
                input_move((WORD)((p[at] << 8) | p[at+1]),
                           (WORD)((p[at+2] << 8) | p[at+3]));
                at += 4;
                break;
            case IN_BUTTON:
                if (at + 2 > len) return send_err(sock, "SCRIPT BUTTON needs button,down");
                input_button(p[at], p[at+1]);
                at += 2;
                break;
            case IN_KEY:
                if (at + 4 > len) return send_err(sock, "SCRIPT KEY needs rawcode,down,qualifier");
                input_key(p[at], p[at+1], (UWORD)((p[at+2] << 8) | p[at+3]));
                at += 4;
                break;
            case IN_RMOVE:
                if (at + 4 > len) return send_err(sock, "SCRIPT RMOVE needs dx,dy");
                input_rmove((WORD)((p[at] << 8) | p[at+1]),
                            (WORD)((p[at+2] << 8) | p[at+3]));
                at += 4;
                break;
            case IN_HOME:
                input_home();
                break;
            case INS_WAIT:
                if (at + 2 > len) return send_err(sock, "SCRIPT WAIT needs ticks");
                Delay((ULONG)((p[at] << 8) | p[at+1]));
                at += 2;
                break;
            default:
                return send_err(sock, "unknown SCRIPT sub-op");
            }
        }
        break;
    }

    case IN_CLICK: {
        if (len < 6) return send_err(sock, "CLICK needs x,y,button,count");
        input_click((WORD)((p[0] << 8) | p[1]), (WORD)((p[2] << 8) | p[3]),
                    p[4], p[5]);
        break;
    }

    default:
        return send_err(sock, "unknown input op");
    }

    say("input: op %ld\n", (long)op);
    return send_resp(sock, ST_OK, NULL, 0);
}

/* ------------------------------------------------------------------ *
 * Act by object (CMD_UIACT) - the semantic complement to raw INPUT
 * ------------------------------------------------------------------ *
 * Resolve a window+gadget selector to a click centre the way do_uitree
 * reports it, then drive the click through the same input path - so a client
 * clicks "the OK button" rather than a pixel it guessed. Resolution runs under
 * its own Forbid(); the click happens after Permit() (posting input events
 * must not be done while forbidden). */
static int ui_find(const char *win, const char *gsel, WORD *cx, WORD *cy)
{
    struct Screen *scr;
    struct Window *w;
    struct Gadget *g;
    int wi = 0, found = 0;
    int want_wnum = ui_is_num(win) ? ui_num(win) : -1;
    int gnum = ui_is_num(gsel) ? ui_num(gsel) : -1;

    Forbid();
    scr = IntuitionBase->FirstScreen;                    /* frontmost screen */
    for (w = scr ? scr->FirstWindow : NULL; w && !found; w = w->NextWindow, wi++) {
        if (want_wnum >= 0) { if (wi != want_wnum) continue; }
        else if (win && win[0] && !ui_ci_contains((const char *)w->Title, win)) continue;

        for (g = w->FirstGadget; g && !found; g = g->NextGadget) {
            char label[64];
            int match;
            if (gnum >= 0) {
                match = ((int)g->GadgetID == gnum);
            } else if (gsel && gsel[0]) {
                /* Match the visible label OR the role name, so "OK" finds a
                 * button and "close" finds the window's close gadget. */
                ui_label(g, label, sizeof label);
                match = ui_ci_contains(label, gsel)
                     || ui_ci_contains(ui_kind(g->GadgetType), gsel);
            } else {
                match = 0;
            }
            if (!match) continue;
            {
                int gx = g->LeftEdge, gy = g->TopEdge, gw = g->Width, gh = g->Height;
                if (g->Flags & GFLG_RELRIGHT)  gx += w->Width;
                if (g->Flags & GFLG_RELBOTTOM) gy += w->Height;
                if (g->Flags & GFLG_RELWIDTH)  gw += w->Width;
                if (g->Flags & GFLG_RELHEIGHT) gh += w->Height;
                *cx = (WORD)(w->LeftEdge + gx + gw / 2);
                *cy = (WORD)(w->TopEdge + gy + gh / 2);
                found = 1;
            }
        }
    }
    Permit();
    return found;
}

static int do_uiact(int sock, const UBYTE *body, ULONG len)
{
    char req[256], msg[80];
    char *verb, *win, *gsel, *text = NULL;
    WORD cx, cy;
    ULONG n = len < sizeof req - 1 ? len : sizeof req - 1;

    memcpy(req, body, n); req[n] = '\0';

    /* tab-separated: verb \t window \t gadget [\t text] */
    verb = req;
    win = strchr(req, '\t');
    if (!win) return send_err(sock, "UIACT: verb\\twindow\\tgadget");
    *win++ = '\0';
    gsel = strchr(win, '\t');
    if (!gsel) return send_err(sock, "UIACT: missing gadget");
    *gsel++ = '\0';
    text = strchr(gsel, '\t');
    if (text) *text++ = '\0';

    if (!input_open()) return send_err(sock, "cannot open input.device");

    /* menushort needs no gadget: the "gadget" field carries the item's
     * shortcut char, delivered as right-Amiga+char to the active window. */
    if (strcmp(verb, "menushort") == 0) {
        if (!gsel[0]) return send_err(sock, "menushort: no shortcut char");
        input_menushort((UBYTE)gsel[0]);
        sprintf(msg, "menushort '%c'", gsel[0]);
        say("uiact: %s\n", msg);
        return send_resp(sock, ST_OK, (UBYTE *)msg, (ULONG)strlen(msg));
    }

    if (!ui_find(win, gsel, &cx, &cy))
        return send_err(sock, "no matching gadget");

    if (strcmp(verb, "click") == 0) {
        input_click(cx, cy, IN_BTN_LEFT, 1);
    } else if (strcmp(verb, "dclick") == 0) {
        input_click(cx, cy, IN_BTN_LEFT, 2);
    } else if (strcmp(verb, "settext") == 0) {
        const char *t;
        input_click(cx, cy, IN_BTN_LEFT, 1);         /* focus the string gadget */
        Delay(3);
        input_key(0x32, 1, IEQUALIFIER_RCOMMAND);    /* right-Amiga+X: clear line */
        input_key(0x32, 0, IEQUALIFIER_RCOMMAND);
        for (t = text ? text : ""; *t; t++) input_char((UBYTE)*t);
        input_key(0x44, 1, 0);                       /* Return: confirm */
        input_key(0x44, 0, 0);
    } else {
        return send_err(sock, "UIACT verb must be click|dclick|settext|menushort");
    }

    sprintf(msg, "%s at %d,%d", verb, (int)cx, (int)cy);
    say("uiact: %s\n", msg);
    return send_resp(sock, ST_OK, (UBYTE *)msg, (ULONG)strlen(msg));
}

/* ------------------------------------------------------------------ *
 * Menu walker (CMD_MENUS)
 * ------------------------------------------------------------------ *
 * The menu-bar analogue of do_uitree: enumerate a window's menu strip so a
 * client sees File/Edit/... and their items - with the keyboard shortcut and
 * a packed FULLMENUNUM selector - and can invoke an item by shortcut
 * (CMD_UIACT verb "menushort"). Payload is an optional window selector (title
 * substring or index); empty means the active window. Reuses g_uitree as the
 * reply buffer (one command per connection). Text format:
 *   W "window title"
 *   M m enabled "menu name"
 *   I m i s code flags "key" "item text"   (s = "-" for a top-level item)
 */
static struct Window *menu_window(const char *sel)
{
    struct Screen *scr;
    struct Window *w;
    int wi = 0, wnum;
    if (!sel || !sel[0]) return IntuitionBase->ActiveWindow;
    wnum = ui_is_num(sel) ? ui_num(sel) : -1;
    scr = IntuitionBase->FirstScreen;
    for (w = scr ? scr->FirstWindow : NULL; w; w = w->NextWindow, wi++) {
        if (wnum >= 0) { if (wi == wnum) return w; }
        else if (ui_ci_contains((const char *)w->Title, sel)) return w;
    }
    return NULL;
}

static void menu_label(struct MenuItem *it, char *dst, size_t sz)
{
    dst[0] = '\0';
    if ((it->Flags & ITEMTEXT) && it->ItemFill) {
        struct IntuiText *t = (struct IntuiText *)it->ItemFill;
        if (t->IText) ui_san(dst, sz, (const char *)t->IText);
    }
}

/* One I-record for a menu item or subitem. sub<0 = top-level item. */
static ULONG menu_emit(ULONG at, int m, int i, int sub, struct MenuItem *it,
                       int *trunc)
{
    char label[64], key[2], flags[40], line[200];
    UWORD code = (UWORD)FULLMENUNUM(m, i, (sub < 0 ? NOSUB : sub));
    int fl = 0;

    menu_label(it, label, sizeof label);
    key[0] = (it->Flags & COMMSEQ) ? (char)it->Command : '\0';
    key[1] = '\0';

    flags[0] = '\0';
    if (!(it->Flags & ITEMENABLED)) { strcat(flags, "disabled"); fl = 1; }
    if (it->Flags & CHECKIT) { if (fl) strcat(flags, ","); strcat(flags,
                              (it->Flags & CHECKED) ? "checked" : "uncheck"); fl = 1; }
    if (it->SubItem) { if (fl) strcat(flags, ","); strcat(flags, "submenu"); fl = 1; }
    if (!fl) strcpy(flags, "-");

    if (sub < 0)
        sprintf(line, "I %d %d - %u %s \"%s\" \"%s\"\n",
                m, i, code, flags, key, label);
    else
        sprintf(line, "I %d %d %d %u %s \"%s\" \"%s\"\n",
                m, i, sub, code, flags, key, label);
    return ui_put(at, line, trunc);
}

static int do_menus(int sock, const UBYTE *body, ULONG len)
{
    char sel[128], field[64], line[200];
    struct Window *w;
    struct Menu *m;
    ULONG at = 0, n = len < sizeof sel - 1 ? len : sizeof sel - 1;
    int trunc = 0, mi;

    if (!gfx_ready(sock)) return 0;
    memcpy(sel, body, n); sel[n] = '\0';

    Forbid();
    w = menu_window(sel);
    if (w && w->MenuStrip) {
        ui_san(field, sizeof field, (const char *)w->Title);
        sprintf(line, "W \"%s\"\n", field);
        at = ui_put(at, line, &trunc);

        for (m = w->MenuStrip, mi = 0; m && !trunc; m = m->NextMenu, mi++) {
            struct MenuItem *it;
            int ii;
            ui_san(field, sizeof field, (const char *)m->MenuName);
            sprintf(line, "M %d %s \"%s\"\n", mi,
                    (m->Flags & MENUENABLED) ? "on" : "off", field);
            at = ui_put(at, line, &trunc);

            for (it = m->FirstItem, ii = 0; it && !trunc; it = it->NextItem, ii++) {
                struct MenuItem *sub;
                int si;
                at = menu_emit(at, mi, ii, -1, it, &trunc);
                for (sub = it->SubItem, si = 0; sub && !trunc;
                     sub = sub->NextItem, si++)
                    at = menu_emit(at, mi, ii, si, sub, &trunc);
            }
        }
    }
    Permit();

    if (trunc) at = ui_put(at, "; truncated\n", &trunc);
    say("menus: %ld bytes%s\n", (long)at, (w && w->MenuStrip) ? "" : " (no menu strip)");
    return send_resp(sock, ST_OK, (UBYTE *)g_uitree, at);
}

/* ------------------------------------------------------------------ *
 * Status board, request half
 * ------------------------------------------------------------------ */

static const char *cmd_name(UBYTE code)
{
    switch (code) {
    case CMD_PING:    return "PING";
    case CMD_EXEC:    return "EXEC";
    case CMD_GET:     return "GET";
    case CMD_PUT:     return "PUT";
    case CMD_LIST:    return "LIST";
    case CMD_INFO:    return "INFO";
    case CMD_SHOT:    return "SHOT";
    case CMD_INPUT:   return "INPUT";
    case CMD_BREAK:   return "BREAK";
    case CMD_SCREENS: return "SCREENS";
    case CMD_POINTER: return "POINTER";
    case CMD_HASH:    return "HASH";
    case CMD_UITREE:  return "UITREE";
    case CMD_UIACT:   return "UIACT";
    case CMD_MENUS:   return "MENUS";
    case CMD_AREXX:   return "AREXX";
    default:          return "?";
    }
}

/* Mark a request as in progress: "EXEC: dir sys:" while it runs, and the same
 * text stays up afterwards as "the last thing that happened". Payload text is
 * clipped and de-fanged - it came off the wire, and a control character would
 * otherwise end up rendered into somebody's window. */
static void board_begin(UBYTE code, const UBYTE *body, ULONG got,
                        const char *client)
{
    const UBYTE *txt = NULL;
    ULONG n = 0, i;
    char snip[64];

    switch (code) {
    case CMD_EXEC:                       /* skip the u16 deadline prefix */
        if (got > 2) { txt = body + 2; n = got - 2; }
        break;
    case CMD_GET:
    case CMD_LIST:
    case CMD_AREXX:
        txt = body; n = got;
        break;
    case CMD_PUT:                        /* u16 pathlen + path + data */
        if (got >= 2) {
            ULONG pl = ((ULONG)body[0] << 8) | body[1];
            if (pl + 2 <= got) { txt = body + 2; n = pl; }
        }
        break;
    }
    if (n > sizeof snip - 1) n = sizeof snip - 1;
    for (i = 0; i < n; i++) {
        UBYTE c = txt[i];
        snip[i] = (c < 32 || c > 126) ? '.' : (char)c;
    }
    snip[n] = '\0';

    ObtainSemaphore(&g_board.sem);
    g_board.state = AGS_BUSY;
    if (n) sprintf(g_board.activity, "%s: %s", cmd_name(code), snip);
    else   strcpy(g_board.activity, cmd_name(code));
    if (client) {
        strncpy(g_board.client, client, sizeof g_board.client - 1);
        g_board.client[sizeof g_board.client - 1] = '\0';
    }
    ReleaseSemaphore(&g_board.sem);
}

static void board_done(void)
{
    ObtainSemaphore(&g_board.sem);
    g_board.requests++;
    if (g_job.busy && !g_job.done) {
        g_board.state = AGS_CMDRUN;
        sprintf(g_board.activity, "running: %.80s", g_job.cmd);
    } else {
        g_board.state = AGS_IDLE;
    }
    ReleaseSemaphore(&g_board.sem);
}

/* Called from the accept loop's timeout tick: an EXEC that outlived its
 * deadline finishes long after any request was around to notice, and without
 * this the board would say "running" until the next client happened by. */
static void board_poll_job(void)
{
    if (g_board.state != AGS_CMDRUN) return;
    if (g_job.busy && !g_job.done) return;
    ObtainSemaphore(&g_board.sem);
    g_board.state = AGS_IDLE;
    sprintf(g_board.activity, "finished: %.80s", g_job.cmd);
    ReleaseSemaphore(&g_board.sem);
}

/* ------------------------------------------------------------------ *
 * Connection loop
 * ------------------------------------------------------------------ */

/* Read one frame header plus up to `prefetch` bytes of its body. PUT needs the
 * prefetch so it can see the path before deciding where to stream the rest. */
static int read_frame(int sock, UBYTE *code, ULONG *len, UBYTE *body, ULONG prefetch, ULONG *got)
{
    UBYTE h[AMI_HDRLEN];

    if (!recv_all(sock, h, AMI_HDRLEN)) return 0;
    if (h[0] != AMI_MAGIC0 || h[1] != AMI_MAGIC1 ||
        h[2] != AMI_MAGIC2 || h[3] != AMI_MAGIC3) return 0;

    *code = h[4];
    *len = get_be32(h + 8);
    if (*len > AMI_MAXFRAME) return 0;

    *got = *len < prefetch ? *len : prefetch;
    if (*got && !recv_all(sock, body, *got)) return 0;
    return 1;
}

static void serve(int sock, const char *client)
{
    UBYTE *body = NULL;
    UBYTE code;
    ULONG len, got;
    int authed = !g_have_token;

    body = (UBYTE *)AllocVec(IOBUF, MEMF_ANY);
    if (!body) { send_err(sock, "out of memory"); return; }

    for (;;) {
        if (!read_frame(sock, &code, &len, body, IOBUF, &got)) break;

        if (code == CMD_AUTH) {
            /* An agent with no token accepts the frame and moves on, rather
             * than refusing it. A client configured with a token should not
             * fail against an open agent — that reads as "wrong password"
             * when the truth is "no password is set". */
            if (!g_have_token ||
                (got == (ULONG)strlen(g_token) && memcmp(body, g_token, got) == 0)) {
                authed = 1;
                send_resp(sock, ST_OK, NULL, 0);
                continue;
            }
            say("auth: rejected\n");
            send_err(sock, "bad token");
            break;
        }

        if (!authed) {
            send_resp(sock, ST_AUTH, NULL, 0);
            break;
        }

        /* Every command except PUT fits its payload in the prefetch. */
        if (code != CMD_PUT && len > got) {
            send_err(sock, "payload larger than the 8 KiB command limit");
            break;
        }

        /* An informational client label for the status board. Handled before
         * board_begin so it does not overwrite the activity line with itself -
         * it is metadata about the connection, not a request to display. */
        if (code == CMD_HELLO) {
            ULONG n = got < sizeof g_board.driver - 1 ? got : sizeof g_board.driver - 1;
            ULONG i;
            ObtainSemaphore(&g_board.sem);
            for (i = 0; i < n; i++) {
                UBYTE c = body[i];
                g_board.driver[i] = (c < 32 || c > 126) ? '.' : (char)c;
            }
            g_board.driver[n] = '\0';
            ReleaseSemaphore(&g_board.sem);
            send_resp(sock, ST_OK, NULL, 0);
            break;
        }

        board_begin(code, body, got, client);

        switch (code) {
        case CMD_PING: do_ping(sock); break;
        case CMD_EXEC: do_exec(sock, body, len); break;
        case CMD_GET:  do_get(sock, body, len); break;
        case CMD_PUT:  do_put(sock, body, got, len); break;
        case CMD_LIST: do_list(sock, body, len); break;
        case CMD_INFO: do_info(sock); break;
        case CMD_SHOT: do_shot(sock, body, len); break;
        case CMD_HASH: do_hash(sock, body, len); break;
        case CMD_SCREENS: do_screens(sock); break;
        case CMD_POINTER: do_pointer(sock); break;
        case CMD_UITREE: do_uitree(sock); break;
        case CMD_UIACT: do_uiact(sock, body, len); break;
        case CMD_MENUS: do_menus(sock, body, len); break;
        case CMD_AREXX: do_arexx(sock, body, len); break;
        case CMD_INPUT: do_input(sock, body, len); break;
        case CMD_BREAK: do_break(sock); break;
        default:       send_err(sock, "unknown command"); break;
        }

        board_done();

        /* One command per connection, so the client always knows exactly what
         * state the socket is in. AUTH is the one exception, handled above. */
        break;
    }

    FreeVec(body);
}

/* ------------------------------------------------------------------ *
 * Entry point
 * ------------------------------------------------------------------ */

static const char *TEMPLATE = "PORT/N,TOKEN/K,QUIET/S,VERBOSE/S";

struct args {
    LONG *port;
    STRPTR token;
    LONG quiet;      /* accepted and ignored: quiet is now the default */
    LONG verbose;
};

int main(void)
{
    struct RDArgs *rda;
    struct args a;
    int listener = -1, port = 7846;
    struct sockaddr_in sa;
    int one = 1;
    int rc = RETURN_OK;

    memset(&a, 0, sizeof a);
    rda = ReadArgs((STRPTR)TEMPLATE, (LONG *)&a, NULL);
    if (!rda) {
        PrintFault(IoErr(), (STRPTR)"amiagent");
        return RETURN_FAIL;
    }
    if (a.port) port = (int)*a.port;
    if (a.verbose) g_quiet = 0;
    if (a.token) {
        strncpy(g_token, (const char *)a.token, sizeof g_token - 1);
        g_token[sizeof g_token - 1] = '\0';
        g_have_token = 1;
    }
    FreeArgs(rda);

    SocketBase = OpenLibrary((STRPTR)"bsdsocket.library", 4);
    if (!SocketBase) {
        printf("amiagent: no bsdsocket.library - start your TCP/IP stack first.\n");
        return RETURN_FAIL;
    }

    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) { printf("amiagent: socket() failed\n"); rc = RETURN_FAIL; goto out; }

    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, (char *)&one, sizeof one);

    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons((unsigned short)port);
    sa.sin_addr.s_addr = INADDR_ANY;

    if (bind(listener, (struct sockaddr *)&sa, sizeof sa) < 0) {
        printf("amiagent: cannot bind port %d (already in use?)\n", port);
        rc = RETURN_FAIL; goto out;
    }
    if (listen(listener, 2) < 0) {
        printf("amiagent: listen() failed\n");
        rc = RETURN_FAIL; goto out;
    }

    if (!g_have_token)
        printf("amiagent %s: listening on port %d - NO TOKEN SET, anyone on this\n"
               "  network can run commands here. Restart with TOKEN=<secret>.\n",
               AMIAGENT_VERSION, port);
    else
        printf("amiagent %s: listening on port %d (token required)\n",
               AMIAGENT_VERSION, port);
    /* The banner always prints: it is one line, written before any client can
     * connect, and without it a quiet-by-default daemon looks like it failed
     * to start. Per-request chatter is what VERBOSE controls. */
    printf("Press Ctrl-C to stop.%s\n", g_quiet ? "  (VERBOSE for per-command output)" : "");
    fflush(stdout);

    board_open((UWORD)port);

    for (;;) {
        struct sockaddr_in ca;
        socklen_t calen = sizeof ca;
        int cs;
        /* A finite wait rather than forever: the tick is what lets the status
         * board notice a background command finishing while no client is
         * around. Two seconds of granularity costs nothing. */
        int r = sock_readable(listener, 2);

        if (r < 0) { say("\namiagent: stopping.\n"); break; }
        if (r == 0) { board_poll_job(); continue; }

        cs = accept(listener, (struct sockaddr *)&ca, &calen);
        if (cs < 0) continue;

        serve(cs, (const char *)Inet_NtoA(ca.sin_addr.s_addr));
        CloseSocket(cs);
    }

    board_close();

out:
    input_close();
    if (CyberGfxBase) CloseLibrary(CyberGfxBase);
    if (listener >= 0) CloseSocket(listener);
    if (SocketBase) CloseLibrary(SocketBase);
#ifdef AMIAGENT_OWN_LIBBASES
    if (GfxBase) CloseLibrary((struct Library *)GfxBase);
    if (IntuitionBase) CloseLibrary((struct Library *)IntuitionBase);
#endif
    return rc;
}
