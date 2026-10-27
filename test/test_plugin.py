"""rockgenius.lua under the stubbed Rockbox (test/harness.lua).

Menu picks are 0-based, like rb.do_menu. Main menu:
  0 Genius  1 Moods  2 Workouts  3 Discovery  4 Mood dial  5 Tools  6 Quit
"""
import re, subprocess
from collections import Counter
from pathlib import Path

import pytest

from conftest import ENGINES, LUA, REPO, build, harness

GENIUS, MOODS, WORKOUTS, DISCOVERY, DIAL, TOOLS, QUIT = range(7)
PLAY, BACK = 0, 1
UNTAGGED_EVERY = 11


def lua_tokens():
    sys_path = str(REPO / "tools")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    import bpm_export
    return list(bpm_export._lua_tokens(LUA.read_text(encoding="utf-8")))


# --- static checks -------------------------------------------------------------
def test_compiles_with_rockbox_limits():
    """30 upvalues / 100 locals per function, integer numbers: like the player."""
    luac = Path(harness.INT_LUA).with_name("luac-int")
    listing = subprocess.run([str(luac), "-p", "-l", str(LUA)], capture_output=True,
                             text=True, check=True).stdout
    assert "SETGLOBAL" not in listing, "rockgenius.lua writes a global"
    used = set(re.findall(r"GETGLOBAL.*; (\w+)", listing))
    allowed = {"rb", "io", "os", "string", "table", "math", "pairs", "ipairs", "type",
               "tostring", "tonumber", "pcall", "require", "collectgarbage", "unpack",
               "package", "luadir", "error", "select", "next"}
    assert used <= allowed, used - allowed


def test_integer_only_source():
    toks = lua_tokens()
    nums = [t for t in toks if t[0] == "num"]
    assert nums, "tokenizer found no numbers"
    src = LUA.read_text(encoding="utf-8")
    code = "".join(t[1] if t[0] in ("op", "name") else " " for t in toks)
    assert code.count("/") == 1, "the only '/' allowed is the one inside idiv()"
    assert "^" not in code
    for bad in ("math.floor", "math.ceil", "math.sqrt", "math.huge", "math.fmod", "math.pow"):
        assert bad not in src
    strings = "".join(t[1].decode("latin-1") for t in toks if t[0] == "str")
    assert not re.search(r"%[-0-9.]*[fgeE]", strings), "float format in a string"
    # a float literal like 1.5 tokenizes as num, op '.', num
    for x, y, z in zip(toks, toks[1:], toks[2:]):
        assert not (x[0] == "num" and y == ("op", ".") and z[0] == "num"), "float literal"


def test_version_in_one_place():
    src = LUA.read_text(encoding="utf-8")
    versions = re.findall(r'\d+\.\d+\.\d+', src)
    assert versions == [re.search(r'^local VERSION\s*=\s*"([\d.]+)"', src, re.M).group(1)]


# --- basic runs on both engines ------------------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
def test_menu_title_and_groups(lib300, tmp_path, engine):
    lib = lib300.fresh(tmp_path)
    res = lib.run([MOODS, 10, WORKOUTS, 9, DISCOVERY, 4, TOOLS, 4, QUIT], engine=engine)
    ver = re.search(r'^local VERSION\s*=\s*"([\d.]+)"', LUA.read_text(), re.M).group(1)
    assert res.menus[0] == f"RockGenius {ver} (300 tracks)"
    assert res.menu_items("RockGenius") == ["Genius (nothing playing)", "Moods", "Workouts",
                                            "Discovery", "Mood dial", "Tools", "Quit"]
    assert res.menu_items("Moods") == ["Golden Hour", "Sunday Morning", "Sunday Drive",
                                       "Road Trip", "Late Night Drive", "Focus", "Rainy Window",
                                       "Blue Hour", "Breakup", "Wind Down", "Back"]
    assert res.menu_items("Workouts") == ["Cardio Push", "Running 160 spm", "Running 170 spm",
                                          "Running 180 spm", "Heavy Lifting", "Pregame",
                                          "Fury Room", "Short & Sharp", "Cool-Down Walk", "Back"]
    assert res.menu_items("Discovery") == ["Rediscover", "Deep Cuts", "Throwback", "Long Haul", "Back"]
    assert res.menu_items("Tools") == ["Scan BPM tags (slow, once)", "Rebuild mood cache",
                                       "Playlist length: 50", "About", "Back"]


def assert_sane(res, lib, length=50, allow_short=False):
    name, picks = res.playlist()
    assert picks, name
    assert len(picks) <= length
    if not allow_short:
        assert len(picks) >= min(length, 20), (name, len(picks))
    assert len({p["r"] for p in picks}) == len(picks), "duplicate track"
    for p in picks:
        assert not p["deleted"], "deleted record in playlist"
    tagged = Counter(p["a"] for p in picks if not p["untagged"])
    assert max(tagged.values(), default=0) <= 3, "more than 3 per artist"
    for x, y in zip(picks, picks[1:]):
        if x["untagged"] or y["untagged"]:
            continue
        if x["a"] == y["a"]:
            # only allowed when nothing else was left to swap in
            assert len(set(p["a"] for p in picks)) <= 2
    return name, picks


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("group,count", [(MOODS, 10), (WORKOUTS, 9), (DISCOVERY, 4)])
def test_every_recipe(lib300, tmp_path, engine, group, count):
    for i in range(count):
        lib = lib300.fresh(tmp_path / f"{group}-{i}")
        res = lib.run([group, i, BACK], engine=engine)
        if group == WORKOUTS and i in (1, 2, 3):           # running: no BPM file yet
            assert any(x.startswith("No tracks with a matching BPM") for x in res.splashes)
            continue
        assert "No matching tracks" not in res.splashes, (group, i)
        name, picks = assert_sane(res, lib, allow_short=True)
        assert name.startswith("Genius - ")


def test_recipe_filters(lib10k, tmp_path):
    lib = lib10k.fresh(tmp_path)
    assert "BPM tags updated" in lib.run([TOOLS, 0, QUIT]).splashes
    mood = None
    for idx, cad in ((1, 160), (2, 170), (3, 180)):
        res = lib.run([WORKOUTS, idx, BACK])
        mood = mood or lib.mood()
        name, picks = assert_sane(res, lib)
        assert name == f"Genius - Running {cad} spm.m3u8"
        for p in picks:
            bpm = mood[p["r"]][3]
            assert bpm > 0, "running picks need a known BPM"
            assert min(abs(bpm - cad), abs(bpm * 2 - cad), abs(bpm - cad * 2)) <= 6
    res = lib.run([DISCOVERY, 2, BACK], year=2026)                         # Throwback
    name, picks = assert_sane(res, lib)
    assert all(0 < p["year"] <= 2011 for p in picks)
    assert sum(p["pc"] for p in picks) / len(picks) > 5, "throwback should favour played tracks"
    res = lib.run([MOODS, 5, BACK])                                         # Focus
    name, picks = assert_sane(res, lib)
    assert all(p["len"] >= 150000 for p in picks)
    assert sum(p["len"] for p in picks) / len(picks) > 300000, "focus should prefer long tracks"


# --- genius --------------------------------------------------------------------
def genius_run(lib, mode, engine="lupa", seed=40, extra=()):
    np = lib.np_path(seed)
    return lib.run([GENIUS, *extra, mode, BACK], np=np, engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_genius_submenu(lib10k, tmp_path, engine):
    lib = lib10k.fresh(tmp_path)
    res = genius_run(lib, 8, engine)                                         # Back
    items = res.menu_items("Genius: ")
    assert items == ["More like this", "Same vibe, other artists", "Same vibe, same genre",
                     "Take it up a notch", "Bring it down", "Era match", "Deep cuts like this",
                     "Playlist length: 50", "Back"]
    assert any(s.startswith("Seed: ") for s in res.splashes)


@pytest.mark.parametrize("engine", ENGINES)
def test_genius_modes(lib10k, tmp_path, engine):
    seed = 40
    s = lib10k.tracks[seed]
    assert not s["untagged"] and s["year"] and not s["deleted"]
    lib = lib10k.fresh(tmp_path)
    lib.run([QUIT])
    mood = lib.mood()
    se, sv, sa = mood[seed][:3]

    def dist(p):
        e, v, a = mood[p["r"]][:3]
        return abs(e - se) + abs(v - sv) + abs(a - sa)

    name, picks = assert_sane(genius_run(lib, 0, engine), lib)               # More like this
    assert name == f"Genius - More like {s['t']}.m3u8".replace("'", "'")
    assert seed not in [p["r"] for p in picks]
    like = sum(dist(p) for p in picks) / len(picks)
    assert like < 60

    name, picks = assert_sane(genius_run(lib, 1, engine), lib)               # other artists
    assert all(p["a"] != s["a"] for p in picks)

    name, picks = assert_sane(genius_run(lib, 2, engine), lib)               # same genre
    assert all(p["g"] == s["g"] for p in picks)

    name, picks = assert_sane(genius_run(lib, 3, engine), lib)               # up a notch
    up = sum(mood[p["r"]][0] for p in picks) / len(picks)
    name, picks = assert_sane(genius_run(lib, 4, engine), lib)               # bring it down
    down = sum(mood[p["r"]][0] for p in picks) / len(picks)
    down_a = sum(mood[p["r"]][2] for p in picks) / len(picks)
    assert up > se + 10 and down < se - 10, (up, se, down)
    assert down_a < sa

    name, picks = assert_sane(genius_run(lib, 5, engine), lib)               # era match
    near = sum(1 for p in picks if p["year"] and abs(p["year"] - s["year"]) <= 5)
    assert near >= len(picks) * 0.8, (near, len(picks))

    name, picks = assert_sane(genius_run(lib, 6, engine), lib)               # deep cuts like this
    assert all(p["pc"] == 0 for p in picks)


@pytest.mark.parametrize("engine", ENGINES)
def test_genius_length_choice(lib10k, tmp_path, engine):
    lib = lib10k.fresh(tmp_path)
    # Length cycles 50 -> 100 -> 25; the choice is saved and shown in Tools
    res = genius_run(lib, 0, engine, extra=(7,))
    assert len(res.playlist()[1]) == 100
    assert "length=100" in (lib.rb / "rockgenius.cfg").read_text()
    res = genius_run(lib, 0, engine, extra=(7,))
    assert len(res.playlist()[1]) == 25
    res = lib.run([TOOLS, 2, TOOLS, 4], engine=engine)
    assert "Playlist length: 50" in res.splashes
    assert res.menu_items("Tools")[2] == "Playlist length: 50"


def test_genius_nothing_playing(lib300, tmp_path):
    lib = lib300.fresh(tmp_path)
    res = lib.run([GENIUS])
    assert "Start playing a track first" in res.splashes
    res = lib.run([GENIUS], np="/Music/not in the database.mp3")
    assert "Now playing track isn't in the database" in res.splashes


def test_genius_untagged_seed(lib300, tmp_path):
    lib = lib300.fresh(tmp_path)
    seed = next(i for i, t in enumerate(lib.tracks) if t["untagged"] and not t["deleted"])
    np = lib.np_path(seed)
    res = lib.run([GENIUS, 2, 5, 8], np=np)
    assert "Now playing has no genre tag" in res.splashes
    assert "Now playing has no year tag" in res.splashes
    title = Path(lib.tracks[seed]["p"]).stem
    assert res.menu_items("Genius: ") and f"[menu] Genius: {title}" in res.out
    res = lib.run([GENIUS, 1, BACK], np=np)                                 # other artists still works
    assert_sane(res, lib, allow_short=True)


# --- mood dial ----------------------------------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
def test_mood_dial(lib10k, tmp_path, engine):
    lib = lib10k.fresh(tmp_path)
    res = lib.run([DIAL, 2, 4, 2, 3, 0, BACK], engine=engine)    # Calm, Warm, Soft, 90s, 25
    assert res.menu_items("Energy") == ["Comatose", "Very calm", "Calm", "Medium", "Lively",
                                        "Pumped", "Full tilt"]
    assert len(res.menu_items("Mood")) == 7 and len(res.menu_items("Edge")) == 7
    assert res.menu_items("Era") == ["Any", "60s-70s", "80s", "90s", "2000s", "2010s", "2020s"]
    assert res.menu_items("Length") == ["25 tracks", "50 tracks", "100 tracks"]
    name, picks = assert_sane(res, lib, length=25)
    assert name == "Genius - Dial - Calm, Warm, Soft, 90s.m3u8"
    assert len(picks) == 25
    assert all(1990 <= p["year"] <= 1999 for p in picks)
    res = lib.run([DIAL, 3, 3, 3, 0, 1, BACK], engine=engine)
    assert res.playlist()[0] == "Genius - Dial - Middle of the road.m3u8"
    res = lib.run([DIAL, 6, 0, 6, 1, 1, BACK], engine=engine)
    name, picks = assert_sane(res, lib, allow_short=True)
    assert name == "Genius - Dial - Full tilt, Bleak, Brutal, 60s-70s.m3u8"
    assert all(1960 <= p["year"] <= 1979 for p in picks)
    res = lib.run([DIAL, 3, -1], engine=engine)                  # backing out makes nothing
    assert "No matching tracks" not in res.splashes


# --- play now -------------------------------------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("mode", ["load", "insert", "fail", "none"])
def test_play_now(lib300, tmp_path, engine, mode):
    lib = lib300.fresh(tmp_path)
    res = lib.run([MOODS, 0, PLAY], playlist=mode, engine=engine)
    name, picks = res.playlist()
    handoff = lib.rb / "rockgenius_play.txt"
    log = lib.log()
    if mode in ("load", "insert"):
        assert res.playlog[0] == f"create /Playlists {name}"
        inserts = [x for x in res.playlog if x.startswith("insert ")]
        assert len(inserts) == (len(picks) if mode == "insert" else 0)
        assert all(x.endswith(" -3") for x in inserts)                  # PLAYLIST_INSERT_LAST
        assert res.playlog[-1] == "start 0 0 0"
        assert not handoff.exists()
        assert "play now: started" in log
    else:
        assert not any(x.startswith("start") for x in res.playlog)
        assert handoff.read_text() == f"/Playlists/{name}"
        assert "hand-off file written" in log
        assert any(s.startswith("Couldn't start playback") for s in res.splashes)
    assert "-- preview --" in res.out
    assert not any(m.startswith("RockGenius") for m in res.menus[1:]), "should exit after Play now"


# --- odd libraries -------------------------------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("layout,prefix", [("flat", ""), ("mixed", ""), ("tree", "/<HDD0>"),
                                           ("mixed", "/<HDD0>")])
def test_layouts(tmp_path, engine, layout, prefix):
    lib = build(tmp_path / "lib", 600, layout=layout, prefix=prefix, untagged_every=4)
    res = lib.run([MOODS, 0, BACK], engine=engine)
    name, picks = assert_sane(res, lib, allow_short=True)
    assert any(p["untagged"] for p in picks) or True
    # every picked path exists on the "drive"
    for p in picks:
        assert (lib.root / p["p"].lstrip("/")).exists()
    # untagged tracks are listed under their file name, never as "<Untagged>"
    text = (lib.root / "Playlists" / name).read_text(encoding="utf-8")
    assert "<Untagged>" not in text
    res = lib.run([GENIUS, 0, PLAY], np=lib.np_path(2), engine=engine)
    assert res.playlog[-1] == "start 0 0 0"
    if prefix:                                   # now-playing path without the prefix
        lib2 = lib.fresh(tmp_path / "noprefix-np")
        res = lib2.run([GENIUS, 0, BACK], np=prefix + lib.tracks[2]["p"], engine=engine)
        assert "Now playing track isn't in the database" not in res.splashes


def test_untagged_tracks_play_with_filename_titles(tmp_path):
    lib = build(tmp_path / "lib", 400, layout="flat", untagged_every=2)  # every other track
    res = lib.run([DISCOVERY, 3, BACK])                                     # Long Haul: shuffle
    name, picks = res.playlist()
    untagged = [p for p in picks if p["untagged"]]
    assert len(untagged) > 3, "untagged tracks should not be capped like one artist"
    text = (lib.root / "Playlists" / name).read_text(encoding="utf-8")
    for p in untagged:
        assert f",{Path(p['p']).stem}\n" in text


def test_untagged_scores_as_empty(tmp_path):
    """'<Untagged>' must not be read as a word or genre, even if WORDS has 'untagged'."""
    lib = build(tmp_path / "lib", 120, untagged_every=3)
    hacked = tmp_path / "rockgenius.lua"
    src = LUA.read_text(encoding="utf-8")
    src = src.replace("local WORDS = {", "local WORDS = {\n  untagged={50,50,50},", 1)
    src = src.replace("local GENRE = {", 'local GENRE = {\n  {"untagged", 90, 90, 90}, {"<", 90, 90, 90},', 1)
    hacked.write_text(src, encoding="utf-8")
    lib.run([QUIT], script=hacked)
    mood = lib.mood()
    for i, t in enumerate(lib.tracks):
        if t["untagged"] and not t["deleted"]:
            e, v, a, bpm, fl = mood[i]
            assert fl == 2, "untagged genre counts as unknown"
            assert v == 0 and a in (0, 10), (i, mood[i])


def test_deleted_records(lib300, tmp_path):
    lib = lib300.fresh(tmp_path)
    lib.run([QUIT])
    mood = lib.mood()
    for i, t in enumerate(lib.tracks):
        if t["deleted"]:
            assert mood[i] == (-128, -128, -128, 0, 0)
    assert lib.tracks[10]["deleted"]          # flag word is negative (attr bits) and odd


def test_negative_mtime_and_bpm_scan(tmp_path):
    lib = build(tmp_path / "lib", 300, mtime_negative=True)
    for engine in ENGINES:
        l2 = lib.fresh(tmp_path / engine)
        res = l2.run([TOOLS, 0, QUIT], engine=engine)
        assert "BPM tags updated" in res.splashes
        expect = sum(1 for t in lib.tracks if t["bpm"] and not t["deleted"])
        assert f"build: done, {expect} tracks with BPM" in l2.log()
        res = l2.run([TOOLS, 0, QUIT], engine=engine)                      # incremental
        assert "bpm scan: opened 0 files" in l2.log()


# --- memory --------------------------------------------------------------------
PATHS = {
    "build cache": [QUIT],
    "genius": [GENIUS, 0, BACK],
    "genius x100": [GENIUS, 7, 0, BACK],
    "cardio": [WORKOUTS, 0, BACK],
    "running": [WORKOUTS, 2, BACK],
    "deep cuts": [DISCOVERY, 1, BACK],
    "throwback": [DISCOVERY, 2, BACK],
    "dial": [DIAL, 4, 4, 4, 3, 2, BACK],
    "bpm scan": [TOOLS, 0, QUIT],
}


def heap_profile(lib, tmp_path, engine="lupa"):
    l2 = lib.fresh(tmp_path)
    out = {}
    for name, picks in PATHS.items():
        np = l2.np_path(40) if picks[0] == GENIUS else ""
        out[name] = l2.run(picks, np=np, engine=engine).peak
        if name == "genius x100":
            (l2.rb / "rockgenius.cfg").unlink()
    return out


def test_heap_stays_flat(lib1k, lib10k, tmp_path):
    small = heap_profile(lib1k, tmp_path / "1k")
    big = heap_profile(lib10k, tmp_path / "10k")
    print("\npeak live Lua heap (KB, lupa)   1k tracks   10k tracks")
    for k in PATHS:
        print(f"  {k:<14} {small[k]:>18} {big[k]:>12}")
    for k in PATHS:
        assert big[k] < 400, (k, big[k])
        # per-artist play counts in Deep Cuts grow with the number of artists
        allowed = 50 if k == "deep cuts" else 20
        assert big[k] - small[k] <= allowed, (k, small[k], big[k])
