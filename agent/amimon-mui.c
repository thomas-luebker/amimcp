/*
 * amimon-mui - the MUI front-end of the amiagent status monitor.
 *
 * Same job as amimon.c: find the named semaphore the agent publishes (see
 * status.h), copy the board out twice a second, show it. The difference is
 * the toolkit - a resizable MUI window that follows the user's MUI settings,
 * for the people who run YAM and IBrowse and want their tools to match.
 *
 * amimon (GadTools) stays the baseline: it runs on every OS 2.04+ machine
 * with nothing installed. This one needs MUI 3.8+ (muimaster.library v19)
 * and politely says so if it is missing. Same arrangement as amipkg's
 * gui.c/mui.c pair, and the same PARITY RULE applies: every row shown here
 * exists in amimon.c and vice versa - neither monitor is the "lesser" one.
 *
 * Headers: vendor/mui/include (MUI 3.8 developer kit, freely distributable;
 * see vendor/mui/README). Link: -lamiga (DoMethod).
 */

#ifndef __amigaos__
#error "amimon-mui targets AmigaOS - build with m68k-amigaos-gcc (see Makefile)"
#endif

#include <exec/types.h>
#include <exec/memory.h>
#include <exec/semaphores.h>
#include <dos/dos.h>
#include <devices/timer.h>
#include <libraries/mui.h>

#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <proto/muimaster.h>
#include <clib/alib_protos.h>

#include <stdio.h>
#include <string.h>

#include "proto.h"
#include "status.h"

/* bebbo's startup auto-opens intuition/graphics (same arrangement as
 * amimon); muimaster and utility we open by hand. */
struct Library *MUIMasterBase = NULL;
struct Library *UtilityBase = NULL;    /* MUI notification tags use utility */

static const char verstag[] __attribute__((used)) =
    "$VER: amimon-mui " AMIAGENT_VERSION " (9.8.2026)";

#ifndef MAKE_ID
#define MAKE_ID(a,b,c,d) ((ULONG)(a)<<24 | (ULONG)(b)<<16 | (ULONG)(c)<<8 | (ULONG)(d))
#endif

/* MUI trees and the MUI settings window want more than a shell's default 4K
 * stack; guaranteed here the same way amipkg-mui does it. */
#define STACK_BYTES (64UL * 1024UL)

enum { ID_ABOUT = 1, ID_MUIPREFS };

/* One value row per board field. Order here is display order - keep the enum,
 * labels and window children in sync (and in PARITY with amimon.c). */
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

/* Updated in place; the compare skips the MUIM_Set (and the repaint) when a
 * row has not changed - the same dedup amimon.c does. */
static char g_shown[ROWS][64];

static Object *app, *win, *txt[ROWS];

static struct MsgPort *g_tport = NULL;
static struct timerequest *g_treq = NULL;

/* ------------------------------------------------------------------ *
 * Reading the board (identical to amimon.c - see status.h)
 * ------------------------------------------------------------------ */

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
    set(txt[row], MUIA_Text_Contents, (ULONG)g_shown[row]);
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
     * the LAN runs commands for anyone, and this is where you notice.
     * %lu, not %u: -lamiga puts RawDoFmt's sprintf in front of the C
     * library's, and RawDoFmt reads a bare %u as a 16-bit WORD while the
     * caller pushed a 32-bit int - the port printed as 0 and the argument
     * list slipped (seen on the A4000). Every format in this file must be
     * l-sized for that reason. */
    sprintf(line, "amiagent %.11s on port %lu - %s", b.agent_version,
            (unsigned long)b.tcp_port,
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
 * Timer heartbeat (identical to amimon.c)
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

/* ------------------------------------------------------------------ *
 * UI
 * ------------------------------------------------------------------ */

/* A value cell. The FIRST contents sets the cell's minimum width at window
 * layout (amipkg learned this the hard way), so the Agent row is born with
 * the widest string any row will show; the first refresh() replaces it. */
#define ValueText(initial) TextObject, TextFrame, \
    MUIA_Background, MUII_TextBack, \
    MUIA_Text_Contents, (ULONG)(initial), \
    End

static int build_app(void)
{
    static const char widest[] = "amiagent 0.6.0 on port 65535 - token required  ";

    app = ApplicationObject,
        MUIA_Application_Title,       (ULONG)"amimon",
        MUIA_Application_Version,     (ULONG)&verstag[1],
        MUIA_Application_Author,      (ULONG)"Thomas Luebker",
        MUIA_Application_Copyright,   (ULONG)"(c) 2026 Thomas Luebker",
        MUIA_Application_Description, (ULONG)"amiagent status monitor",
        MUIA_Application_Base,        (ULONG)"AMIMON",

        MUIA_Application_Menustrip, (ULONG)(MenustripObject,
            MUIA_Family_Child, MenuObjectT("amimon"),
                MUIA_Family_Child, MenuitemObject, MUIA_Menuitem_Title, (ULONG)"About...",
                    MUIA_Menuitem_Shortcut, (ULONG)"?",
                    MUIA_UserData, ID_ABOUT, End,
                MUIA_Family_Child, MenuitemObject, MUIA_Menuitem_Title, (ULONG)"MUI...",
                    MUIA_UserData, ID_MUIPREFS, End,
                MUIA_Family_Child, MenuitemObject, MUIA_Menuitem_Title, (ULONG)"Quit",
                    MUIA_Menuitem_Shortcut, (ULONG)"Q",
                    MUIA_UserData, MUIV_Application_ReturnID_Quit, End,
            End,
        End),

        SubWindow, win = WindowObject,
            MUIA_Window_Title, (ULONG)"amimon - amiagent status",
            MUIA_Window_ID,    MAKE_ID('A','M','O','N'),
            WindowContents, VGroup,
                Child, ColGroup(2),
                    Child, Label((ULONG)g_labels[ROW_AGENT]),
                    Child, txt[ROW_AGENT]   = ValueText(widest),
                    Child, Label((ULONG)g_labels[ROW_STATE]),
                    Child, txt[ROW_STATE]   = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_REQUEST]),
                    Child, txt[ROW_REQUEST] = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_COMMAND]),
                    Child, txt[ROW_COMMAND] = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_COUNTS]),
                    Child, txt[ROW_COUNTS]  = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_CLIENT]),
                    Child, txt[ROW_CLIENT]  = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_DRIVER]),
                    Child, txt[ROW_DRIVER]  = ValueText("-"),
                    Child, Label((ULONG)g_labels[ROW_UPTIME]),
                    Child, txt[ROW_UPTIME]  = ValueText("-"),
                End,
            End,
        End,
    End;
    if (!app) return 0;

    DoMethod(app, MUIM_Notify, MUIA_Application_MenuAction, MUIV_EveryTime,
             (ULONG)app, 2, MUIM_Application_ReturnID, MUIV_TriggerValue);
    DoMethod(win, MUIM_Notify, MUIA_Window_CloseRequest, TRUE,
             (ULONG)app, 2, MUIM_Application_ReturnID, MUIV_Application_ReturnID_Quit);
    return 1;
}

/* ------------------------------------------------------------------ *
 * Main loop
 * ------------------------------------------------------------------ */

static int gui_run(void)
{
    ULONG tsig;
    int i, rc = RETURN_FAIL;
    ULONG opened = 0;

    UtilityBase   = OpenLibrary((STRPTR)"utility.library", 37);
    MUIMasterBase = OpenLibrary((STRPTR)MUIMASTER_NAME, 19);
    if (!MUIMasterBase || !UtilityBase) {
        printf("amimon-mui: MUI 3.8+ is required (muimaster.library v19).\n");
        printf("Use the GadTools amimon on machines without MUI.\n");
        goto out;
    }

    for (i = 0; i < ROWS; i++) strcpy(g_shown[i], "-");
    g_shown[ROW_AGENT][0] = '\0';   /* the widest-string cell: force row 1 */

    if (!timer_open() || !build_app()) {
        printf("amimon-mui: could not create the application.\n");
        goto out;
    }

    set(win, MUIA_Window_Open, TRUE);
    get(win, MUIA_Window_Open, &opened);
    if (!opened) {
        printf("amimon-mui: the window did not open (screen too small,\n");
        printf("broken MUI prefs, or an incomplete MUI install?).\n");
        goto out;
    }

    refresh();
    timer_start();
    tsig = 1UL << g_tport->mp_SigBit;

    {
        int done = 0;
        long zeros = 0;
        while (!done) {
            ULONG sigs = 0;
            LONG rid = DoMethod(app, MUIM_Application_NewInput, (ULONG)&sigs);
            switch (rid) {
            case MUIV_Application_ReturnID_Quit: done = 1; break;
            case ID_ABOUT:
                MUI_Request(app, win, 0, (char *)"About amimon", (char *)"_OK",
                    "\033bamimon-mui " AMIAGENT_VERSION "\033n\n\n"
                    "Watches the amiagent status board over local IPC:\n"
                    "state, the request in flight, the last command,\n"
                    "counters, client, driver, uptime. No network.\n\n"
                    "(c) 2026 Thomas Luebker\n"
                    "https://github.com/thomas-luebker/amimcp");
                break;
            case ID_MUIPREFS:
                DoMethod(app, MUIM_Application_OpenConfigWindow, 0);
                break;
            default: break;
            }
            if (done) break;
            if (sigs) {
                zeros = 0;
                sigs = Wait(sigs | tsig | SIGBREAKF_CTRL_C);
                if (sigs & SIGBREAKF_CTRL_C) done = 1;
                if (sigs & tsig) {
                    while (GetMsg(g_tport)) ;
                    refresh();
                    if (!done) timer_start();
                }
            } else if (++zeros > 100) {
                /* Spin guard, same as amipkg-mui: an empty signal mask should
                 * be transient - yield politely rather than burn the CPU. */
                Delay(1);
            }
        }
    }
    set(win, MUIA_Window_Open, FALSE);
    rc = RETURN_OK;

out:
    timer_close();
    if (app) MUI_DisposeObject(app);
    if (MUIMasterBase) CloseLibrary(MUIMasterBase);
    if (UtilityBase) CloseLibrary(UtilityBase);
    return rc;
}

/* StackSwap wrapper (same idiom as amipkg-mui). */
static struct StackSwapStruct g_sss;
static char *g_stk;
static int   g_rc;

int main(void)
{
    g_stk = (char *)AllocMem(STACK_BYTES, MEMF_ANY);
    if (!g_stk) return gui_run();
    g_sss.stk_Lower   = (APTR)g_stk;
    g_sss.stk_Upper   = (ULONG)g_stk + STACK_BYTES;
    g_sss.stk_Pointer = (APTR)((ULONG)g_stk + STACK_BYTES);
    StackSwap(&g_sss);
    g_rc = gui_run();
    StackSwap(&g_sss);
    FreeMem(g_stk, STACK_BYTES);
    return g_rc;
}
