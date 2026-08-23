# amiagent — AmigaOS build (m68k-amigaos-gcc; see
# https://github.com/AmigaPorts/m68k-amigaos-gcc — bebbo's original repo is gone)
#
#   make                   → amiagent + amimon (68000 baseline; runs on every Amiga)
#   make CPU=68020         → 68020+ build
#   make CPU=68080         → Apollo 68080 speed-optimised build
#   make OWNBASES=1        → open intuition/graphics by hand (see below)
#
# Same toolchain and flag set as amipkg, so a machine that can build that can
# build this.

CC      = m68k-amigaos-gcc
CPU     = 68000
ifeq ($(CPU),68080)
OPT     = -O2
else
OPT     = -Os
endif
CFLAGS  = $(OPT) -fomit-frame-pointer -m$(CPU) -Wall -Wextra -D__amigaos__ -Ivendor/cgx
# -lgcc after the objects: newlib's printf pulls in libgcc's 64-bit integer
# helpers, which sit after libc in the default link order.
LDFLAGS = -s -lgcc

# If your toolchain does not auto-open intuition.library / graphics.library,
# the link fails on an undefined IntuitionBase. Rebuild with OWNBASES=1 and
# amiagent opens and closes them itself.
ifdef OWNBASES
CFLAGS += -DAMIAGENT_OWN_LIBBASES
endif

# Optional TLS via AmiSSL. The AmiSSL SDK is an external build dependency (like
# the toolchain) — it is not vendored (its OpenSSL headers are ~11 MB). Point
# AMISSL_SDK at the extracted "AmiSSL/Developer" dir:
#   make SSL=1 AMISSL_SDK=/path/to/AmiSSL/Developer CPU=68020
# The default build has no AmiSSL dependency. `-lamisslstubs` resolves the
# OpenSSL functions passed as function pointers; init is done by hand (not
# -lamisslauto) so the agent reuses its own bsdsocket base.
ifdef SSL
CFLAGS  += -DAMIAGENT_SSL -I$(AMISSL_SDK)/include
SSLLIBS  = -L$(AMISSL_SDK)/lib/AmigaOS3 -lamisslstubs
endif

all: amiagent amimon amimon-mui

amiagent: amiagent.c proto.h status.h
	$(CC) $(CFLAGS) -o $@ amiagent.c $(SSLLIBS) $(LDFLAGS)

amimon: amimon.c proto.h status.h
	$(CC) $(CFLAGS) -o $@ amimon.c $(LDFLAGS)

# The MUI monitor needs the vendored MUI 3.8 headers (compile-time only;
# muimaster.library is opened at runtime) and -lamiga for DoMethod.
# muistubs.c MUST stay a separate translation unit - see its header comment.
amimon-mui: amimon-mui.c muistubs.c proto.h status.h
	$(CC) $(CFLAGS) -Ivendor/mui/include -o $@ amimon-mui.c muistubs.c -lamiga $(LDFLAGS)

clean:
	rm -f amiagent amimon amimon-mui

.PHONY: all clean
