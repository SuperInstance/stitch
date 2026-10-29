# stitch-c — pure C99 keyless core. No dependencies beyond libc/libm.
CC      ?= gcc
CFLAGS  ?= -std=c99 -Wall -Wextra -Werror -O2
CPPFLAGS += -Isrc
LDLIBS  += -lm

SRC = src/sha256.c src/json.c src/stitch.c src/main.c
OBJ = $(SRC:.c=.o)

stitchc: $(OBJ)
	$(CC) $(CFLAGS) -o $@ $(OBJ) $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) $(CPPFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJ) stitchc

.PHONY: clean
