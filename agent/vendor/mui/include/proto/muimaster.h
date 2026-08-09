#ifndef PROTO_MUIMASTER_H
#define PROTO_MUIMASTER_H
#include <exec/types.h>
extern struct Library *MUIMasterBase;
#ifdef __GNUC__
#include <inline/muimaster.h>
#else
#include <clib/muimaster_protos.h>
#include <pragmas/muimaster_pragmas.h>
#endif
#endif
