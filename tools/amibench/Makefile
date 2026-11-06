# amibench - one 68020 binary for every Amiga from a 68020 up, FPU or not.
#
# Only fpu.c is compiled with -m68881. The link must NOT get -m68881: that
# selects the FPU C library, whose startup runs FPU instructions before main()
# and kills the program on a 020/030 without a coprocessor (see fpu.c).

# make predefines CC=cc, so `?=` would never apply; only replace the default.
ifeq ($(origin CC),default)
CC      = $(HOME)/opt/amiga/bin/m68k-amigaos-gcc
endif
CFLAGS  = -O2 -m68020 -Wall

amibench: amibench.o fpu.o
	$(CC) $(CFLAGS) -o $@ amibench.o fpu.o

amibench.o: amibench.c
	$(CC) $(CFLAGS) -c -o $@ amibench.c

fpu.o: fpu.c
	$(CC) $(CFLAGS) -m68881 -c -o $@ fpu.c

clean:
	rm -f amibench amibench.o fpu.o

.PHONY: clean
