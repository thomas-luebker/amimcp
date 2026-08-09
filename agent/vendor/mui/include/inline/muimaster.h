/* Modern GCC inline header for muimaster.library — REPLACES the 1997 dev-kit
 * file (whose multi-line asm strings GCC 6 rejects). Written against
 * FD/muimaster_lib.fd (bias 30) using the NDK's LP* macros — the same
 * mechanism the toolchain's own inlines (and our vendored AmiSSL ones) use.
 * Varargs wrappers use the classic stack-tags idiom (m68k args are pushed
 * contiguously). */
#ifndef _INLINE_MUIMASTER_H
#define _INLINE_MUIMASTER_H

#ifndef __INLINE_MACROS_H
#include <inline/macros.h>
#endif

#ifndef MUIMASTER_BASE_NAME
#define MUIMASTER_BASE_NAME MUIMasterBase
#endif

extern struct Library *MUIMasterBase;

#define MUI_NewObjectA(___class, ___tags) \
    LP2(0x1e, Object *, MUI_NewObjectA, char *, ___class, a0, struct TagItem *, ___tags, a1, \
    , MUIMASTER_BASE_NAME)

#define MUI_DisposeObject(___obj) \
    LP1NR(0x24, MUI_DisposeObject, Object *, ___obj, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_RequestA(___app, ___win, ___flags, ___title, ___gadgets, ___format, ___params) \
    LP7(0x2a, LONG, MUI_RequestA, APTR, ___app, d0, APTR, ___win, d1, LONG, ___flags, d2, \
        char *, ___title, a0, char *, ___gadgets, a1, char *, ___format, a2, APTR, ___params, a3, \
    , MUIMASTER_BASE_NAME)

#define MUI_AllocAslRequest(___type, ___tags) \
    LP2(0x30, APTR, MUI_AllocAslRequest, unsigned long, ___type, d0, struct TagItem *, ___tags, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_AslRequest(___req, ___tags) \
    LP2(0x36, BOOL, MUI_AslRequest, APTR, ___req, a0, struct TagItem *, ___tags, a1, \
    , MUIMASTER_BASE_NAME)

#define MUI_FreeAslRequest(___req) \
    LP1NR(0x3c, MUI_FreeAslRequest, APTR, ___req, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_Error() \
    LP0(0x42, LONG, MUI_Error, \
    , MUIMASTER_BASE_NAME)

#define MUI_SetError(___errnum) \
    LP1(0x48, LONG, MUI_SetError, LONG, ___errnum, d0, \
    , MUIMASTER_BASE_NAME)

#define MUI_GetClass(___name) \
    LP1(0x4e, struct IClass *, MUI_GetClass, char *, ___name, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_FreeClass(___cl) \
    LP1NR(0x54, MUI_FreeClass, struct IClass *, ___cl, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_RequestIDCMP(___obj, ___flags) \
    LP2NR(0x5a, MUI_RequestIDCMP, Object *, ___obj, a0, ULONG, ___flags, d0, \
    , MUIMASTER_BASE_NAME)

#define MUI_RejectIDCMP(___obj, ___flags) \
    LP2NR(0x60, MUI_RejectIDCMP, Object *, ___obj, a0, ULONG, ___flags, d0, \
    , MUIMASTER_BASE_NAME)

#define MUI_Redraw(___obj, ___flags) \
    LP2NR(0x66, MUI_Redraw, Object *, ___obj, a0, ULONG, ___flags, d0, \
    , MUIMASTER_BASE_NAME)

#define MUI_CreateCustomClass(___base, ___supername, ___supermcc, ___datasize, ___dispatcher) \
    LP5(0x6c, struct MUI_CustomClass *, MUI_CreateCustomClass, struct Library *, ___base, a0, \
        char *, ___supername, a1, struct MUI_CustomClass *, ___supermcc, a2, \
        int, ___datasize, d0, APTR, ___dispatcher, a3, \
    , MUIMASTER_BASE_NAME)

#define MUI_DeleteCustomClass(___mcc) \
    LP1(0x72, BOOL, MUI_DeleteCustomClass, struct MUI_CustomClass *, ___mcc, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_MakeObjectA(___type, ___params) \
    LP2(0x78, Object *, MUI_MakeObjectA, LONG, ___type, d0, ULONG *, ___params, a0, \
    , MUIMASTER_BASE_NAME)

#define MUI_Layout(___obj, ___l, ___t, ___w, ___h, ___flags) \
    LP6(0x7e, BOOL, MUI_Layout, Object *, ___obj, a0, LONG, ___l, d0, LONG, ___t, d1, \
        LONG, ___w, d2, LONG, ___h, d3, ULONG, ___flags, d4, \
    , MUIMASTER_BASE_NAME)

/* ---- varargs wrappers ---------------------------------------------------- */
/* OUT-OF-LINE in src/amiga/muistubs.c. As static __inline HERE, GCC 6
 * proved the varargs unread (no va_arg in the body) and DROPPED them at
 * every call site - garbage tag lists; the great amipkg-mui crash hunt
 * of July 2026. Do NOT move them back into this header. */
#ifndef NO_INLINE_STDARG
Object *MUI_NewObject(char *classname, ULONG tag1, ...);
Object *MUI_MakeObject(LONG type, ...);
LONG MUI_Request(APTR app, APTR win, LONG flags, char *title,
                 char *gadgets, char *format, ...);
APTR MUI_AllocAslRequestTags(unsigned long type, ULONG tag1, ...);
BOOL MUI_AslRequestTags(APTR req, ULONG tag1, ...);
#endif /* NO_INLINE_STDARG */

#endif /* _INLINE_MUIMASTER_H */
