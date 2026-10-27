import os, re, shutil, struct, sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools"))

import make_db  # noqa: E402
import run as harness  # noqa: E402

LUA = REPO / "rockgenius.lua"
ENGINES = ["lupa", "int"]


class Library:
    """A synthetic player drive built by make_db, plus what went into it."""

    def __init__(self, root, tracks, prefix):
        self.root, self.tracks, self.prefix = Path(root), tracks, prefix
        self.by_path = {t["p"]: (i, t) for i, t in enumerate(tracks)}

    @property
    def rb(self):
        return self.root / ".rockbox"

    def fresh(self, dest):
        """Copy of this library with its own .rockbox (audio files are symlinked)."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.rb, dest / ".rockbox")
        for p in self.root.iterdir():
            if p.name not in (".rockbox", "Playlists"):
                (dest / p.name).symlink_to(p)
        lib = Library(dest, self.tracks, self.prefix)
        return lib

    def run(self, choices, np="", playlist="load", engine="lupa", year=2026, script=LUA):
        out = harness.run(self.root, script, choices, np, playlist, engine, year)
        return Result(out, self)

    def np_path(self, i):
        return self.prefix + self.tracks[i]["p"]

    def mood(self):
        data = (self.rb / "rockgenius_mood.bin").read_bytes()
        assert data[:4] == b"RGM3"
        recs = []
        for r in range(len(self.tracks)):
            e, v, a, bpm, fl = data[20 + r * 5: 25 + r * 5]
            recs.append((e - 128, v - 128, a - 128, bpm, fl))
        return recs

    def log(self):
        return (self.rb / "rockgenius.log").read_text()


class Result:
    def __init__(self, out, lib):
        self.out, self.lib = out, lib
        self.peak = harness.peak_kb(out)
        self.splashes = re.findall(r"^\[splash\] (.*)$", out, re.M)
        self.playlog = re.findall(r"^\[playlist\] (.*)$", out, re.M)
        self.menus = re.findall(r"^\[menu\] (.*)$", out, re.M)
        assert "RockGenius error" not in out, out
        assert "ERROR" not in lib.log(), lib.log()

    def menu_items(self, title_start):
        """Items of the last menu whose title starts with title_start."""
        blocks = re.findall(r"^\[menu\] (.*)\n((?:   \d+: .*\n)*)", self.out, re.M)
        for title, body in reversed(blocks):
            if title.startswith(title_start):
                return re.findall(r"^   \d+: (.*)$", body, re.M)
        raise AssertionError(f"no menu {title_start!r} in output")

    def playlist(self):
        """(name, [track dicts]) of the newest playlist written."""
        pl = sorted((self.lib.root / "Playlists").glob("*.m3u8"), key=lambda p: p.stat().st_mtime_ns)
        assert pl, "no playlist written"
        lines = pl[-1].read_text(encoding="utf-8").splitlines()
        assert lines[0] == "#EXTM3U"
        picks = []
        for ln in lines[1:]:
            if ln.startswith("#EXTINF"):
                assert "<Untagged>" not in ln
                continue
            dev = ln
            if self.lib.prefix:
                assert dev.startswith(self.lib.prefix), dev
                dev = dev[len(self.lib.prefix):]
            i, t = self.lib.by_path[dev]
            picks.append(dict(t, r=i))
        return pl[-1].name, picks


def build(root, n, **kw):
    prefix = kw.get("prefix", "")
    tracks = make_db.build(str(root), n, **kw)
    return Library(root, tracks, prefix)


@pytest.fixture(scope="session")
def lib300(tmp_path_factory):
    return build(tmp_path_factory.mktemp("lib300"), 300, untagged_every=11)


@pytest.fixture(scope="session")
def lib10k(tmp_path_factory):
    return build(tmp_path_factory.mktemp("lib10k"), 10000)


@pytest.fixture(scope="session")
def lib1k(tmp_path_factory):
    return build(tmp_path_factory.mktemp("lib1k"), 1000)


@pytest.fixture(scope="session", autouse=True)
def int_lua():
    return harness.int_lua()
