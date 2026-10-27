#!/bin/sh
# Build a Lua 5.1.5 interpreter configured like Rockbox's plugin Lua
# (apps/plugins/lua/luaconf.h, tag v4.0-final): integer numbers, C's
# truncating % and /, at most 30 upvalues and 100 locals per function.
# The players use a 32-bit long, so this build uses int with wrap-around.
# Output: test/.build/lua-int and test/.build/luac-int
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/../.build"
SHA=2640fc56a795f29d28ef15e13c34a47e223960b0240e8cb0a82d9b0738695333
mkdir -p "$OUT"
cd "$OUT"
if [ -x lua-int ] && [ -x luac-int ]; then echo "$OUT/lua-int"; exit 0; fi
[ -f lua-5.1.5.tar.gz ] || curl -sSfL -o lua-5.1.5.tar.gz https://www.lua.org/ftp/lua-5.1.5.tar.gz
echo "$SHA  lua-5.1.5.tar.gz" | sha256sum -c - >/dev/null
rm -rf lua-5.1.5 && tar xzf lua-5.1.5.tar.gz
cat "$HERE/rockbox_luaconf.h" >> lua-5.1.5/src/luaconf.h
cd lua-5.1.5/src
${CC:-cc} -O1 -fwrapv -w -DLUA_USE_POSIX -o "$OUT/lua-int" \
  lapi.c lcode.c ldebug.c ldo.c ldump.c lfunc.c lgc.c llex.c lmem.c lobject.c \
  lopcodes.c lparser.c lstate.c lstring.c ltable.c ltm.c lundump.c lvm.c lzio.c \
  lauxlib.c lbaselib.c ldblib.c liolib.c lmathlib.c loslib.c ltablib.c lstrlib.c \
  loadlib.c linit.c lua.c -lm
${CC:-cc} -O1 -fwrapv -w -DLUA_USE_POSIX -o "$OUT/luac-int" \
  lapi.c lcode.c ldebug.c ldo.c ldump.c lfunc.c lgc.c llex.c lmem.c lobject.c \
  lopcodes.c lparser.c lstate.c lstring.c ltable.c ltm.c lundump.c lvm.c lzio.c \
  lauxlib.c luac.c print.c -lm
echo "$OUT/lua-int"
