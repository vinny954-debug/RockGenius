"""Runs rockgenius.lua under Lua 5.1 with a stubbed `rb` (test/harness.lua) and
scripted menu choices.

usage: run.py ROOT SCRIPT "picks" [now_playing_path]
              [--engine lupa|int] [--playlist load|insert|fail|none] [--year 2026]

--engine lupa  Lua 5.1 through the lupa module (pip install lupa); the default.
--engine int   the integer Lua from test/intlua/build.sh, configured like the
               one on the player (integer numbers, C % and /, 30 upvalues).
"""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "harness.lua")
INT_LUA = os.path.join(HERE, ".build", "lua-int")


def lua_str(s):
    """A Lua string literal for s (UTF-8 bytes, anything unusual as \\ddd)."""
    return '"' + "".join(chr(b) if b < 128 and (chr(b).isalnum() or chr(b) in " /._-") else "\\%03d" % b
                         for b in s.encode("utf-8")) + '"'


def config(root, script, choices, np, playlist, year):
    items = ", ".join(str(int(c)) for c in choices)
    return ("HARNESS = { root = %s, script = %s, choices = { %s }, np = %s, playlist = %s, year = %d }"
            % (lua_str(os.path.abspath(root)), lua_str(os.path.abspath(script)), items,
               lua_str(np or ""), lua_str(playlist), year))


def int_lua():
    if not os.path.exists(INT_LUA):
        subprocess.run(["sh", os.path.join(HERE, "intlua", "build.sh")], check=True,
                       stdout=subprocess.DEVNULL)
    return INT_LUA


def run(root, script, choices, np="", playlist="load", engine="lupa", year=2026):
    """Run the plugin once. Returns its stdout (prints, menus, splashes)."""
    root, script = str(root), str(script)
    cfg = config(root, script, choices, np, playlist, year)
    if engine == "int":
        r = subprocess.run([int_lua(), "-e", cfg, HARNESS], capture_output=True, text=True,
                           errors="replace")
        if r.returncode != 0:
            raise RuntimeError("integer Lua failed:\n" + r.stdout + r.stderr)
        return r.stdout
    from lupa import lua51
    L = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
    lines = []

    def lua_print(*args):
        lines.append("\t".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)
                               for x in args))
    L.globals()[b"print"] = lua_print
    L.execute(cfg.encode("utf-8"))
    L.execute(open(HARNESS, "rb").read())
    return "\n".join(lines) + "\n"


def peak_kb(output):
    m = re.search(r"\[peak lua heap\] (\d+)", output)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("script")
    ap.add_argument("picks")
    ap.add_argument("np", nargs="?", default="")
    ap.add_argument("--engine", default="lupa", choices=["lupa", "int"])
    ap.add_argument("--playlist", default="load", choices=["load", "insert", "fail", "none"])
    ap.add_argument("--year", type=int, default=2026)
    a = ap.parse_args()
    choices = [int(x) for x in a.picks.split(",")] if a.picks else []
    sys.stdout.write(run(a.root, a.script, choices, a.np, a.playlist, a.engine, a.year))


if __name__ == "__main__":
    main()
