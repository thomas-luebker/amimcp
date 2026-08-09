/*
 * muistubs.c - out-of-line varargs stubs for muimaster.library.
 *
 * These MUST live in their own translation unit. As static __inline
 * functions in the header, GCC 6 (-Os) saw that the callee never uses
 * va_arg and legally DROPPED the variadic arguments at every call site -
 * every MUI object was created with a garbage tag list (window title,
 * list format etc. never even made it into the binary). Symptoms varied
 * with whatever lay on the stack: "works but rough" under UAE, window
 * refuses to open on RTG machines, Guru 8000 0008 elsewhere.
 *
 * In a separate TU the caller cannot know the args are unread and must
 * pass them all per the m68k stack ABI; (&tag1) then really is the start
 * of a contiguous tag list - the classic Amiga stub idiom.
 */
#ifdef __amigaos__

#include <exec/types.h>
#include <utility/tagitem.h>
#include <libraries/mui.h>
#include <proto/muimaster.h>

Object *MUI_NewObject(char *classname, ULONG tag1, ...)
{
    return MUI_NewObjectA(classname, (struct TagItem *)&tag1);
}

Object *MUI_MakeObject(LONG type, ...)
{
    return MUI_MakeObjectA(type, (ULONG *)((&type) + 1));
}

LONG MUI_Request(APTR app, APTR win, LONG flags, char *title,
                 char *gadgets, char *format, ...)
{
    return MUI_RequestA(app, win, flags, title, gadgets, format,
                        (APTR)((&format) + 1));
}

APTR MUI_AllocAslRequestTags(unsigned long type, ULONG tag1, ...)
{
    return MUI_AllocAslRequest(type, (struct TagItem *)&tag1);
}

BOOL MUI_AslRequestTags(APTR req, ULONG tag1, ...)
{
    return MUI_AslRequest(req, (struct TagItem *)&tag1);
}

#endif
