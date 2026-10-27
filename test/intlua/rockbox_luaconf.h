
/* ---- appended by test/intlua/build.sh: Rockbox "Rocklua integer patch" ----
 * Adapted from Rockbox apps/plugins/lua/luaconf.h (tag v4.0-final).
 * SPDX-License-Identifier: GPL-2.0-or-later
 * (The rest of RockGenius is MIT; see LICENSE.) */
#undef LUA_NUMBER_DOUBLE
#undef LUA_NUMBER
#define LUA_NUMBER int
#undef LUAI_UACNUMBER
#define LUAI_UACNUMBER int
#undef LUA_NUMBER_SCAN
#define LUA_NUMBER_SCAN "%d"
#undef LUA_NUMBER_FMT
#define LUA_NUMBER_FMT "%d"
#undef lua_number2str
#define lua_number2str(s,n) snprintf((s), 32, LUA_NUMBER_FMT, (n))
#undef lua_str2number
#define lua_str2number(s,p) rg_str2number((s), (p))
#undef luai_nummod
#define luai_nummod(a,b) ((a)%(b))
#undef luai_numpow
#define luai_numpow(a,b) ((int)pow((a),(b)))
#undef LUAI_MAXVARS
#define LUAI_MAXVARS 100
#undef LUAI_MAXUPVALUES
#define LUAI_MAXUPVALUES 30
#undef LUAL_BUFFERSIZE
#define LUAL_BUFFERSIZE 512
#include <stdlib.h>
#include <limits.h>
/* strtol on a 32-bit long clamps out-of-range literals */
static inline int rg_str2number(const char *s, char **p) {
  long v = strtol(s, p, 10);
  if (v > INT_MAX) v = INT_MAX;
  if (v < INT_MIN) v = INT_MIN;
  return (int)v;
}
