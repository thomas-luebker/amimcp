# amiagent — AmigaOS build (bebbo amiga-gcc, https://github.com/bebbo/amiga-gcc)
#
#   make                   → amiagent (68000 baseline; runs on every Amiga)
#   make CPU=68020         → 68020+ build
#   make OWNBASES=1        → open intuition/graphics by hand (see below)
#
# Same toolchain and flag set as amipkg, so a machine that can build that can
# build this.

CC      = m68k-amigaos-gcc
CPU     = 68000
CFLAGS  = -Os -fomit-frame-pointer -m$(CPU) -Wall -Wextra -D__amigaos__ -Ivendor/cgx
# -lgcc after the objects: newlib's printf pulls in libgcc's 64-bit integer
# helpers, which sit after libc in the default link order.
LDFLAGS = -s -lgcc

# If your toolchain does not auto-open intuition.library / graphics.library,
# the link fails on an undefined IntuitionBase. Rebuild with OWNBASES=1 and
# amiagent opens and closes them itself.
ifdef OWNBASES
CFLAGS += -DAMIAGENT_OWN_LIBBASES
endif

all: amiagent

amiagent: amiagent.c proto.h
	$(CC) $(CFLAGS) -o $@ amiagent.c $(LDFLAGS)

clean:
	rm -f amiagent

.PHONY: all clean
