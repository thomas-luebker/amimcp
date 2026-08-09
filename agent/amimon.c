/*
 * amimon - a GadTools window watching amiagent's status board.
 *
 * Runs on the Amiga itself, next to the agent. Finds the named semaphore the
 * agent publishes (see status.h), copies the board out twice a second, and
 * shows it in a row of TEXT_KIND gadgets: state, the request in flight, the
 * last EXEC, counters, client, uptime. No network, no configuration - if the
 * agent is running on this machine, the window fills in; if not, it says so
 * and keeps watching, so starting the two in either order works.
 *
 * GadTools rather than MUI or ReAction on purpose: it is in ROM/Workbench on
 * every OS 2.04+ machine this project targets, and a status panel does not
 * need a layout engine. The window uses the screen's own font and sizes
 * itself from it, so it looks native at 640x256 topaz and on a 1080p RTG
 * screen alike.
 */

#ifndef __amigaos__
#error "amimon targets AmigaOS - build with m68k-amigaos-gcc (see Makefile)"
#endif

#include <exec/types.h>
#include <exec/memory.h>
#include <exec/semaphores.h>
#include <dos/dos.h>
#include <devices/timer.h>
#include <intuition/intuition.h>
#include <libraries/gadtools.h>

#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <proto/graphics.h>
#include <proto/gadtools.h>

#include <stdio.h>
#include <string.h>

#include "proto.h"
#include "status.h"

/* bebbo's startup auto-opens intuition/graphics (same arrangement as
 * amiagent); gadtools we open by hand - defining the base here keeps libauto
 * out of it. */
struct Library *GadToolsBase = NULL;

/* One TEXT_KIND gadget per row. Order here is display order. */
enum {
    ROW_AGENT,      /* version + port, or "not found" */
    ROW_STATE,
    ROW_REQUEST,    /* current request while busy, last one while idle */
    ROW_COMMAND,    /* last EXEC command line */
    ROW_COUNTS,
    ROW_CLIENT,
    ROW_DRIVER,     /* what the client SAYS is driving it - self-reported */
    ROW_UPTIME,
    ROWS
};

static const char *g_labels[ROWS] = {
    "Agent", "State", "Request", "Command", "Requests", "Client", "Driver", "Up"
};

/* GadTools keeps the pointer a TEXT_KIND gadget was given, so what it is
 * given must live as long as the gadget: one static buffer per row, updated
 * in place and re-set to force the redraw. */
static char g_shown[ROWS][64];

static struct Screen *g_scr = NULL;
static APTR g_vi = NULL;
static struct Gadget *g_glist = NULL;
static struct Gadget *g_rows[ROWS];
static struct Window *g_win = NULL;

static struct MsgPort *g_tport = NULL;
static struct timerequest *g_treq = NULL;

/* ------------------------------------------------------------------ *
 * Reading the board
 * ------------------------------------------------------------------ */

/* Copy the agent's board out under its semaphore. Returns 1 with *out filled,
 * or 0 if no agent (or an incompatible one) is publishing. The Forbid()
 * bridges FindSemaphore and Obtain - see status.h for the full protocol. */
static int board_read(struct agent_board *out)
{
    struct SignalSemaphore *ss;

    Forbid();
    ss = FindSemaphore((STRPTR)AGENT_BOARD_NAME);
    if (ss) ObtainSemaphoreShared(ss);
    Permit();
    if (!ss) return 0;

    CopyMem(ss, out, sizeof *out);
    ReleaseSemaphore(ss);

    return out->board_version == AGENT_BOARD_VERSION;
}

/* ------------------------------------------------------------------ *
 * Display
 * ------------------------------------------------------------------ */

static void row_set(int row, const char *text)
{
    if (strcmp(g_shown[row], text) == 0) return;   /* unchanged: no repaint */
    strncpy(g_shown[row], text, sizeof g_shown[row] - 1);
    g_shown[row][sizeof g_shown[row] - 1] = '\0';
    GT_SetGadgetAttrs(g_rows[row], g_win, NULL,
                      GTTX_Text, (ULONG)g_shown[row],
                      TAG_DONE);
}

static void fmt_uptime(char *out, const struct DateStamp *started)
{
    struct DateStamp now;
    LONG secs;

    DateStamp(&now);
    secs = (now.ds_Days - started->ds_Days) * 86400L
         + (now.ds_Minute - started->ds_Minute) * 60L
         + (now.ds_Tick - started->ds_Tick) / 50L;
    if (secs < 0) secs = 0;

    sprintf(out, "%ldd %02ld:%02ld:%02ld",
            (long)(secs / 86400L), (long)(secs / 3600L % 24L),
            (long)(secs / 60L % 60L), (long)(secs % 60L));
}

static void refresh(void)
{
    struct agent_board b;
    char line[96];

    if (!board_read(&b)) {
        row_set(ROW_AGENT, "not found - is amiagent 0.6.0+ running?");
        row_set(ROW_STATE, "-");
        row_set(ROW_REQUEST, "-");
        row_set(ROW_COMMAND, "-");
        row_set(ROW_COUNTS, "-");
        row_set(ROW_CLIENT, "-");
        row_set(ROW_DRIVER, "-");
        row_set(ROW_UPTIME, "-");
        return;
    }

    /* Whether a TOKEN is required matters at a glance: an agent left open on
     * the LAN runs commands for anyone, and this is where you notice. */
    sprintf(line, "amiagent %.11s on port %u - %s", b.agent_version, b.tcp_port,
            b.have_token ? "token required" : "OPEN, no token");
    row_set(ROW_AGENT, line);

    row_set(ROW_STATE, b.state == AGS_BUSY   ? "answering a request" :
                       b.state == AGS_CMDRUN ? "command running" : "idle");

    /* %.56s: the board's strings are longer than a row; clip, don't wrap. */
    sprintf(line, "%.56s", b.activity);
    row_set(ROW_REQUEST, line);
    sprintf(line, "%.56s", b.lastcmd[0] ? b.lastcmd : "-");
    row_set(ROW_COMMAND, line);

    sprintf(line, "%lu answered, %lu failed, %lu commands",
            (unsigned long)b.requests, (unsigned long)b.failures,
            (unsigned long)b.cmds);
    row_set(ROW_COUNTS, line);

    row_set(ROW_CLIENT, b.client[0] ? b.client : "-");

    /* Self-reported: this is whatever the client claimed via CMD_HELLO, not
     * anything the Amiga can check. A dash means nothing has announced itself. */
    row_set(ROW_DRIVER, b.driver[0] ? b.driver : "-");

    fmt_uptime(line, &b.started);
    row_set(ROW_UPTIME, line);
}

/* ------------------------------------------------------------------ *
 * Setup / teardown
 * ------------------------------------------------------------------ */

static int timer_open(void)
{
    g_tport = CreateMsgPort();
    if (!g_tport) return 0;
    g_treq = (struct timerequest *)
        CreateIORequest(g_tport, sizeof(struct timerequest));
    if (!g_treq) return 0;
    if (OpenDevice((STRPTR)TIMERNAME, UNIT_VBLANK,
                   (struct IORequest *)g_treq, 0)) {
        DeleteIORequest((struct IORequest *)g_treq);
        g_treq = NULL;
        return 0;
    }
    return 1;
}

static void timer_start(void)
{
    g_treq->tr_node.io_Command = TR_ADDREQUEST;
    g_treq->tr_time.tv_secs = 0;
    g_treq->tr_time.tv_micro = 500000;   /* 2 Hz: humans, not oscilloscopes */
    SendIO((struct IORequest *)g_treq);
}

static void timer_close(void)
{
    if (g_treq && g_treq->tr_node.io_Device) {
        AbortIO((struct IORequest *)g_treq);
        WaitIO((struct IORequest *)g_treq);
        CloseDevice((struct IORequest *)g_treq);
    }
    if (g_treq) DeleteIORequest((struct IORequest *)g_treq);
    if (g_tport) DeleteMsgPort(g_tport);
    g_treq = NULL;
    g_tport = NULL;
}

static int ui_open(void)
{
    struct NewGadget ng;
    struct Gadget *gad;
    struct RastPort *rp;
    UWORD fh, gh, labelw, valuew, left, top, i;
    /* Sizes the value column: must be at least as wide as the longest string
     * any row will show, which is the Agent line with a token note. */
    static const char widest[] = "amiagent 0.6.0 on port 65535 - token required  ";

    g_scr = LockPubScreen(NULL);
    if (!g_scr) return 0;
    g_vi = GetVisualInfo(g_scr, TAG_DONE);
    if (!g_vi) return 0;

    /* All geometry hangs off the screen's font: the screen's own RastPort
     * already has it set, so it can measure the labels for us. */
    rp = &g_scr->RastPort;
    fh = g_scr->Font->ta_YSize;
    gh = (UWORD)(fh + 6);

    labelw = 0;
    for (i = 0; i < ROWS; i++) {
        UWORD w = (UWORD)TextLength(rp, (STRPTR)g_labels[i],
                                    strlen(g_labels[i]));
        if (w > labelw) labelw = w;
    }
    valuew = (UWORD)(TextLength(rp, (STRPTR)widest, sizeof widest - 1) + 16);

    left = (UWORD)(g_scr->WBorLeft + 8 + labelw + 8);
    top = (UWORD)(g_scr->WBorTop + fh + 1 + 6);

    gad = CreateContext(&g_glist);
    for (i = 0; i < ROWS; i++) {
        memset(&ng, 0, sizeof ng);
        ng.ng_LeftEdge = left;
        ng.ng_TopEdge = (WORD)(top + i * (gh + 4));
        ng.ng_Width = valuew;
        ng.ng_Height = gh;
        ng.ng_GadgetText = (STRPTR)g_labels[i];
        ng.ng_TextAttr = g_scr->Font;
        ng.ng_GadgetID = i;
        ng.ng_Flags = PLACETEXT_LEFT;
        ng.ng_VisualInfo = g_vi;
        strcpy(g_shown[i], "-");
        gad = CreateGadget(TEXT_KIND, gad, &ng,
                           GTTX_Text, (ULONG)g_shown[i],
                           GTTX_Border, TRUE,
                           TAG_DONE);
        g_rows[i] = gad;
    }
    if (!gad) return 0;

    g_win = OpenWindowTags(NULL,
        WA_PubScreen, (ULONG)g_scr,
        WA_Title, (ULONG)"amimon - amiagent status",
        WA_Left, 40,
        WA_Top, 40,
        WA_InnerWidth, 8 + labelw + 8 + valuew + 8,
        WA_InnerHeight, 6 + ROWS * (gh + 4) + 4,
        WA_Gadgets, (ULONG)g_glist,
        WA_IDCMP, IDCMP_CLOSEWINDOW | IDCMP_REFRESHWINDOW,
        WA_DragBar, TRUE,
        WA_DepthGadget, TRUE,
        WA_CloseGadget, TRUE,
        WA_Activate, TRUE,
        WA_SimpleRefresh, TRUE,
        TAG_DONE);
    if (!g_win) return 0;

    GT_RefreshWindow(g_win, NULL);
    return 1;
}

static void ui_close(void)
{
    if (g_win) CloseWindow(g_win);
    if (g_glist) FreeGadgets(g_glist);
    if (g_vi) FreeVisualInfo(g_vi);
    if (g_scr) UnlockPubScreen(NULL, g_scr);
    g_win = NULL;
    g_glist = NULL;
    g_vi = NULL;
    g_scr = NULL;
}

/* ------------------------------------------------------------------ *
 * Entry point
 * ------------------------------------------------------------------ */

int main(void)
{
    ULONG winsig, tsig;
    int running = 1;

    GadToolsBase = OpenLibrary((STRPTR)"gadtools.library", 37);
    if (!GadToolsBase) {
        printf("amimon: gadtools.library v37+ needed (OS 2.04)\n");
        return RETURN_FAIL;
    }

    if (!timer_open() || !ui_open()) {
        printf("amimon: could not open the window\n");
        ui_close();
        timer_close();
        CloseLibrary(GadToolsBase);
        return RETURN_FAIL;
    }

    refresh();
    timer_start();

    winsig = 1UL << g_win->UserPort->mp_SigBit;
    tsig = 1UL << g_tport->mp_SigBit;

    while (running) {
        ULONG got = Wait(winsig | tsig | SIGBREAKF_CTRL_C);

        if (got & SIGBREAKF_CTRL_C) running = 0;

        if (got & winsig) {
            struct IntuiMessage *im;
            while ((im = GT_GetIMsg(g_win->UserPort))) {
                ULONG cls = im->Class;
                GT_ReplyIMsg(im);
                if (cls == IDCMP_CLOSEWINDOW) running = 0;
                else if (cls == IDCMP_REFRESHWINDOW) {
                    GT_BeginRefresh(g_win);
                    GT_EndRefresh(g_win, TRUE);
                }
            }
        }

        if (got & tsig) {
            if (GetMsg(g_tport)) {
                refresh();
                if (running) timer_start();
            }
        }
    }

    ui_close();
    timer_close();
    CloseLibrary(GadToolsBase);
    return RETURN_OK;
}
