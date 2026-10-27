"""The desktop tools, and the caches they hand to rockgenius.lua."""
import csv, shutil, struct
from pathlib import Path, PureWindowsPath, PurePosixPath

import pytest

import bpm_export
import fix_names
from conftest import ENGINES, LUA, build

QUIT, TOOLS, WORKOUTS = 6, 5, 2


def export(lib, *extra):
    bpm_export.main([str(lib.root), "--report", str(lib.root / "no-report.csv"), *extra])


# --- RGB2 + RGM3: Python writes, Lua reads ---------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kw", [dict(), dict(layout="mixed", untagged_every=5),
                                dict(prefix="/<HDD0>"), dict(mtime_negative=True)],
                         ids=["tree", "mixed-untagged", "hdd0-prefix", "negative-mtime"])
def test_python_caches_match_lua(tmp_path, engine, kw):
    lib = build(tmp_path / "lib", 1500, **kw)
    export(lib)
    py_mood = (lib.rb / "rockgenius_mood.bin").read_bytes()
    py_bpm = (lib.rb / "rockgenius_bpm.bin").read_bytes()
    assert py_mood[:4] == b"RGM3" and py_bpm[:4] == b"RGB2"

    # 1. Lua accepts Python's mood file as is: no rebuild
    res = lib.run([QUIT], engine=engine)
    assert not any(s.startswith("Building mood cache") for s in res.splashes)
    assert (lib.rb / "rockgenius_mood.bin").read_bytes() == py_mood

    # 2. Lua's own build from Python's BPM file is byte-identical
    (lib.rb / "rockgenius_mood.bin").unlink()
    res = lib.run([QUIT], engine=engine)
    assert any(s.startswith("Building mood cache") for s in res.splashes)
    assert (lib.rb / "rockgenius_mood.bin").read_bytes() == py_mood
    expect = sum(1 for t in lib.tracks if t["bpm"] and not t["deleted"])
    assert f"build: done, {expect} tracks with BPM" in lib.log()

    # 3. Lua's own BPM scan finds nothing new and writes the same RGB2 file
    lib.run([TOOLS, 0, QUIT], engine=engine)
    assert "bpm scan: opened 0 files" in lib.log()
    assert (lib.rb / "rockgenius_bpm.bin").read_bytes() == py_bpm
    assert (lib.rb / "rockgenius_mood.bin").read_bytes() == py_mood


def test_running_works_from_exported_bpm(tmp_path):
    lib = build(tmp_path / "lib", 3000)
    export(lib)
    res = lib.run([WORKOUTS, 2, 1])
    name, picks = res.playlist()
    assert name == "Genius - Running 170 spm.m3u8" and len(picks) == 50
    assert all(p["bpm"] in (85, 170) for p in picks)


def test_mood_rejected_when_lexicon_differs(tmp_path):
    lib = build(tmp_path / "lib", 300)
    other = tmp_path / "other.lua"
    other.write_text(LUA.read_text().replace("run=  {25,10,5}", "run=  {26,10,5}", 1))
    export(lib, "--lua", str(other))
    res = lib.run([QUIT])
    assert any(s.startswith("Building mood cache") for s in res.splashes)


def test_mood_rejected_when_database_changes(tmp_path):
    lib = build(tmp_path / "lib", 300)
    export(lib)
    idx = bytearray((lib.rb / "database_idx.tcd").read_bytes())
    struct.pack_into("<i", idx, 16, 4)                      # new commit id
    (lib.rb / "database_idx.tcd").write_bytes(bytes(idx))
    res = lib.run([QUIT])
    assert any(s.startswith("Building mood cache") for s in res.splashes)


def test_report_beats_tags(tmp_path):
    lib = build(tmp_path / "lib", 200)
    rpt = tmp_path / "bpm_report.csv"
    with open(rpt, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "status", "bpm", "confidence"])
        for t in lib.tracks[:50]:
            w.writerow([str(lib.root / t["p"].lstrip("/")), "tagged", 99, 0.5])
    bpm_export.main([str(lib.root), "--report", str(rpt)])
    data = (lib.rb / "rockgenius_bpm.bin").read_bytes()
    for r, t in enumerate(lib.tracks[:50]):
        if not t["deleted"]:
            assert data[8 + r * 4] == 99


def test_no_mood_flag_removes_stale_cache(tmp_path):
    lib = build(tmp_path / "lib", 100)
    lib.run([QUIT])
    assert (lib.rb / "rockgenius_mood.bin").exists()
    export(lib, "--no-mood")
    assert not (lib.rb / "rockgenius_mood.bin").exists()


def test_lexicon_parse_matches_lua_counts():
    sv, magic, genre, words = bpm_export.load_lexicon(LUA)
    src = LUA.read_text()
    assert magic == b"RGM3" and sv >= 3
    body = src[src.index("local GENRE = {"):src.index("local WORDS = {")]
    assert len(genre) == body.count('{"')
    assert words[b"run"] == (25, 10, 5) and words[b"destroy"] == (15, -30, 35)


def test_lexicon_parse_handles_quoted_keys(tmp_path):
    p = tmp_path / "x.lua"
    p.write_text(LUA.read_text().replace("local WORDS = {", 'local WORDS = {\n  ["coraz\\195\\179n"]={0,30,-10}, ["fuego"]={20,0,10}, -- note\n', 1))
    words = bpm_export.load_lexicon(p)[3]
    assert words["corazón".encode()] == (0, 30, -10) and words[b"fuego"] == (20, 0, 10)


# --- paths -------------------------------------------------------------------
def test_device_paths():
    f = bpm_export.device_to_local
    assert f("/<HDD0>/Music/A/x.mp3", PurePosixPath("/Volumes/IPOD")) == PurePosixPath("/Volumes/IPOD/Music/A/x.mp3")
    assert f("/Music/x.mp3", PurePosixPath("/media/me/IPOD")) == PurePosixPath("/media/me/IPOD/Music/x.mp3")
    assert f("/<HDD0>/Song.mp3", PureWindowsPath("E:\\")) == PureWindowsPath("E:\\Song.mp3")
    assert str(f("/Music/A B/x.mp3", PureWindowsPath("E:\\"))) == "E:\\Music\\A B\\x.mp3"
    assert str(bpm_export.player_root("E:", PureWindowsPath)) == "E:\\"
    assert str(bpm_export.player_root("E:\\", PureWindowsPath)) == "E:\\"
    assert str(bpm_export.player_root("/Volumes/IPOD", PurePosixPath)) == "/Volumes/IPOD"


def test_path_key_matches_lua_hash():
    p = "/<HDD0>/Music/Some Artist/Some Album/01 Song.mp3".encode()
    assert 0 < bpm_export.path_key(p, 0x5A3B1C2D) < 16777213
    assert bpm_export.path_key(p, -5) == bpm_export.path_key(p, 16777213 - 5)


def test_idiv_truncates():
    assert [bpm_export.idiv(a, b) for a, b in ((7, 2), (-7, 2), (7, -2), (-7, -2), (-6, 3))] == [3, -3, -3, 3, -2]


@pytest.mark.parametrize("name,new", [
    ("00 Morgan Wallen - 3 Songs at a Time Sampler - 01 - Last Night.mp3", "01 Last Night.mp3"),
    ("00 Jelly Roll - Whitsitt Chapel - 1-03 - Need A Favor.flac", "03 Need A Favor.flac"),
    ("A - B - 7 - What: Now?.mp3", "07 What_ Now_.mp3"),
    ("Song.mp3", None),
    ("01 Last Night.mp3", None),
    ("notes.txt", None),
])
def test_fix_names_pattern(name, new):
    assert fix_names.new_name(name) == new


def test_fix_names_preview_then_apply(tmp_path):
    d = tmp_path / "Music" / "Jelly Roll" / "Album"
    d.mkdir(parents=True)
    long = "00 Jelly Roll - Whitsitt Chapel - 03 - Need A Favor.mp3"
    (d / long).write_bytes(b"x")
    (d / ("._" + long)).write_bytes(b"x")
    (tmp_path / "Music" / "Loose Song.mp3").write_bytes(b"x")
    fix_names.main([str(tmp_path / "Music"), "--all"])
    assert (d / long).exists()                                   # preview changes nothing
    fix_names.main([str(tmp_path / "Music"), "jelly roll", "--apply"])
    assert sorted(p.name for p in d.iterdir()) == ["._03 Need A Favor.mp3", "03 Need A Favor.mp3"]
    assert (tmp_path / "Music" / "Loose Song.mp3").exists()


def test_bpm_tag_roundtrip_and_journal(tmp_path, monkeypatch):
    import bpm_tag
    monkeypatch.chdir(tmp_path)
    lib = build(tmp_path / "lib", 30)
    t = next(t for t in lib.tracks if not t["bpm"])
    f = str(lib.root / t["p"].lstrip("/"))
    assert bpm_tag.read_bpm(f) is None
    bpm_tag.write_bpm(f, 127.6)
    assert bpm_tag.read_bpm(f) == 128
    bpm_tag.journal("START\t" + f)                # a write that never finished
    assert bpm_tag.unfinished_writes() == [f]
    bpm_tag.journal("DONE\t" + f)
    assert bpm_tag.unfinished_writes() == []
    bpm_tag.mark_done(f)
    assert bpm_export.norm(f) in {bpm_export.norm(p) for p in bpm_tag.load_done("none.csv")}


@pytest.mark.parametrize("engine", ENGINES)
def test_record_size_ignores_header_datasize(tmp_path, engine):
    """tagcache.c's master datasize also counts the tag files (it sizes the RAM
    cache), so the record size has to come from the file length."""
    lib = build(tmp_path / "lib", 500)
    idx = (lib.rb / "database_idx.tcd").read_bytes()
    datasize, entries = struct.unpack_from("<ii", idx, 4)
    assert datasize != 24 + entries * 96                      # realistic header
    assert bpm_export.Database(lib.rb).recsize == 96
    lib.run([QUIT], engine=engine)
    assert "recsize=96" in lib.log()


def test_broken_filename_offsets_are_reported(tmp_path, capsys):
    lib = build(tmp_path / "lib", 100)
    idx = bytearray((lib.rb / "database_idx.tcd").read_bytes())
    struct.pack_into("<i", idx, 24 + 7 * 96 + 4 * 4, 10_000_000)   # record 7's filename offset
    (lib.rb / "database_idx.tcd").write_bytes(bytes(idx))
    export(lib)
    out = capsys.readouterr().out
    assert "1 of 100 database entries point outside database_4.tcd" in out
    assert (lib.rb / "rockgenius_bpm.bin").read_bytes()[8 + 7 * 4: 12 + 7 * 4] == b"\0\0\0\0"
