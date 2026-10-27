#!/usr/bin/env python3
"""
bpm_export.py -- build RockGenius's caches on your computer and copy them to the
player, so the player never has to open thousands of audio files itself and
never has to show "Building mood cache".

It reads Rockbox's database straight off the player (.rockbox/database_idx.tcd
and database_N.tcd), looks up each track's BPM, and writes

  .rockbox/rockgenius_bpm.bin    BPM per track (format RGB2)
  .rockbox/rockgenius_mood.bin   mood scores per track (format RGM3), scored with
                                 the same rules and word lists as rockgenius.lua

BPMs come from bpm_report.csv (written by bpm_tag.py). Any track missing from
the report has its tag read directly (header only, no audio decoding).

Order of operations:
  1. bpm_tag.py has finished tagging
  2. on the player: Database > Update Now   (so the database matches the files)
  3. plug the player in and run, with the player's drive as the argument:
        macOS:    python3 bpm_export.py /Volumes/IPOD
        Windows:  py bpm_export.py E:\\
        Linux:    python3 bpm_export.py /media/$USER/IPOD
  4. eject, open RockGenius
"""
import argparse, csv, os, re, struct, sys, unicodedata
from pathlib import Path, PurePath

TC_MAGIC = 0x54434810
MASTER_HDR = 24
HDR_SIZE = 12
M24 = 16777213
UNTAGGED = b"<Untagged>"
MOOD_MAGIC = b"RGM3"
BPM_MAGIC = b"RGB2"


def norm(p):
    """Comparable form of a local path: absolute, OS case rules, NFC, casefolded."""
    p = os.path.normcase(os.path.abspath(str(p)))
    return unicodedata.normalize("NFC", p).casefold()


def player_root(arg, cls=Path):
    """'E:' -> 'E:\\' (otherwise Windows reads it as 'current dir on E:')."""
    if re.fullmatch(r"[A-Za-z]:", arg):
        arg += "\\"
    return cls(arg)


def device_to_local(dev_path, mount):
    """'/<HDD0>/Music/x.mp3' or '/Music/x.mp3' -> <mount>/Music/x.mp3"""
    p = dev_path
    if p.startswith("/<"):                      # multi-volume prefix like /<HDD0>
        end = p.find(">")
        if end > 0:
            p = p[end + 1:]
    parts = [x for x in p.split("/") if x]
    base = mount if isinstance(mount, PurePath) else Path(mount)
    return base.joinpath(*parts)


# --- must match phash() / path_key() in rockgenius.lua exactly ----------------
def phash(path_bytes):
    n = len(path_bytes)
    h = n
    for b in path_bytes[max(0, n - 24):]:
        h = (h * 31 + b) % M24
    if h == 0:
        h = 1
    return h


def path_key(path_bytes, mtime):
    h = (phash(path_bytes) + mtime % M24) % M24
    return h if h else 1


def idiv(a, b):
    """rockgenius.lua's idiv(): integer division truncating toward zero."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


# --- reading the Rockbox database ---------------------------------------------
class Database:
    """The parts of the tagcache rockgenius.lua uses, read the same way."""

    def __init__(self, rbdir):
        self.rbdir = Path(rbdir)
        idx = (self.rbdir / "database_idx.tcd").read_bytes()
        if len(idx) < MASTER_HDR:
            raise ValueError("database_idx.tcd is too short")
        self.end = "<"
        if struct.unpack_from("<i", idx, 0)[0] != TC_MAGIC:
            if struct.unpack_from(">i", idx, 0)[0] == TC_MAGIC:
                self.end = ">"
            else:
                raise ValueError("Unrecognised database_idx.tcd (wrong version?)")
        self.idx = idx
        (self.datasize, self.entries, self.serial,
         self.commit) = struct.unpack_from(self.end + "iiii", idx, 4)
        # field numbers, adjusted like open_master() in rockgenius.lua
        self.T = dict(artist=0, album=1, genre=2, title=3, filename=4, composer=5,
                      comment=6, albumartist=7, grouping=8, year=9, disc=10, track=11,
                      canon=12, bitrate=13, length=14, playcount=15, rating=16,
                      playtime=17, lastplayed=18, commitid=19, mtime=20)
        self.tag_count, self.recsize = 23, 96
        if self.entries > 0 and self.datasize > 0:
            isz = idiv(self.datasize, self.entries)
            if 80 <= isz <= 160 and isz % 4 == 0 and isz != self.recsize:
                old = self.tag_count
                self.recsize, self.tag_count = isz, (isz - 4) // 4
                if self.tag_count == old - 1:          # older db: no canonical artist
                    self.T = {k: (v - 1 if v >= 12 else v) for k, v in self.T.items()}
        self._tags = {}

    def tagfile(self, tag):
        if tag not in self._tags:
            p = self.rbdir / f"database_{tag}.tcd"
            self._tags[tag] = p.read_bytes() if p.exists() else None
        return self._tags[tag]

    def rd32(self, buf, off):
        return struct.unpack_from(self.end + "i", buf, off)[0]

    def record(self, r):
        base = MASTER_HDR + r * self.recsize
        rec = self.idx[base: base + self.recsize]
        return rec if len(rec) == self.recsize else None

    def fld(self, rec, name):
        return self.rd32(rec, self.T[name] * 4)

    def deleted(self, rec):
        return self.rd32(rec, self.tag_count * 4) & 1 == 1

    def tag_at(self, tag, off):
        """tag_at() in rockgenius.lua: the string at `off`, or None."""
        data = self.tagfile(tag)
        if data is None or off < HDR_SIZE or off + 8 > len(data):
            return None
        ln = self.rd32(data, off)
        if ln <= 0 or ln > 2048:
            return None
        return data[off + 8: off + 8 + ln].split(b"\0", 1)[0]

    def stream_tagfile(self, tag):
        """stream_tagfile() in rockgenius.lua: yields (offset, string)."""
        data = self.tagfile(tag)
        if data is None or len(data) < HDR_SIZE:
            return
        n, pos = self.rd32(data, 8), HDR_SIZE
        for _ in range(max(0, n)):
            if pos + 8 > len(data):
                break
            ln = self.rd32(data, pos)
            if ln < 0 or ln > 4096:
                break
            yield pos, data[pos + 8: pos + 8 + ln].split(b"\0", 1)[0]
            pos += 8 + ln


# --- the lexicon, read from rockgenius.lua ---------------------------------------
def _lua_tokens(src):
    """Tiny Lua tokenizer: strings, numbers, names, punctuation; skips comments."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith("--", i):
            if src.startswith("--[[", i):
                j = src.find("]]", i)
                i = n if j < 0 else j + 2
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j + 1
        elif c in "\"'":
            j, out = i + 1, bytearray()
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                    m = re.match(r"\d{1,3}", src[j:])
                    if m:
                        out.append(int(m.group()))
                        j += len(m.group())
                        continue
                    out += {"n": b"\n", "t": b"\t", "\\": b"\\", '"': b'"', "'": b"'"}.get(
                        src[j], src[j].encode("utf-8"))
                else:
                    out += src[j].encode("utf-8")
                j += 1
            yield ("str", bytes(out))
            i = j + 1
        elif c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit()):
            m = re.match(r"-?\d+", src[i:])
            yield ("num", int(m.group()))
            i += len(m.group())
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", src[i:])
            yield ("name", m.group())
            i += len(m.group())
        else:
            yield ("op", c)
            i += 1


def _table_tokens(src, name):
    m = re.search(r"^local\s+" + name + r"\s*=\s*\{", src, re.M)
    if not m:
        raise ValueError(f"no 'local {name} = {{' in rockgenius.lua")
    depth, out = 1, []
    for tok in _lua_tokens(src[m.end():]):
        if tok == ("op", "{"):
            depth += 1
        elif tok == ("op", "}"):
            depth -= 1
            if depth == 0:
                return out
        out.append(tok)
    raise ValueError(f"unterminated {name} table")


def load_lexicon(lua_path):
    """Returns (score_version, mood_magic, GENRE list, WORDS dict) from rockgenius.lua."""
    src = Path(lua_path).read_text(encoding="utf-8")
    sv = re.search(r"^local\s+SCORE_VERSION\s*=\s*(\d+)", src, re.M)
    mm = re.search(r'^local\s+MOOD_MAGIC\s*=\s*"(\w+)"', src, re.M)
    if not sv or not mm:
        raise ValueError("SCORE_VERSION / MOOD_MAGIC not found")
    genre, toks = [], [t for t in _table_tokens(src, "GENRE") if t not in (("op", ","), ("op", ";"))]
    i = 0
    while i < len(toks):
        if (toks[i] != ("op", "{") or toks[i + 1][0] != "str" or
                any(t[0] != "num" for t in toks[i + 2:i + 5]) or toks[i + 5] != ("op", "}")):
            raise ValueError(f"GENRE: can't read entry near {toks[i:i + 6]}")
        genre.append((toks[i + 1][1], toks[i + 2][1], toks[i + 3][1], toks[i + 4][1]))
        i += 6
    words, toks = {}, [t for t in _table_tokens(src, "WORDS") if t not in (("op", ","), ("op", ";"))]
    i = 0
    while i < len(toks):
        if toks[i][0] == "name":
            key, i = toks[i][1].encode(), i + 1
        elif toks[i] == ("op", "[") and toks[i + 1][0] == "str" and toks[i + 2] == ("op", "]"):
            key, i = toks[i + 1][1], i + 3
        else:
            raise ValueError(f"WORDS: can't read key near {toks[i:i + 3]}")
        vals = toks[i:i + 6]
        if (vals[0] != ("op", "=") or vals[1] != ("op", "{") or
                any(t[0] != "num" for t in vals[2:5]) or vals[5] != ("op", "}")):
            raise ValueError(f"WORDS: can't read value for {key!r}")
        words[key] = (vals[2][1], vals[3][1], vals[4][1])     # last one wins, like Lua
        i += 6
    return int(sv.group(1)), mm.group(1).encode(), genre, words


def _strhash(b, h):
    for x in b:
        h = (h * 31 + x) % M24
    return h


def lexicon_fp(score_version, genre, words):
    """lexicon_fp() in rockgenius.lua."""
    fp = score_version
    for g in genre:
        fp = (fp + _strhash(g[0] + b"=%d,%d,%d" % g[1:], 7)) % M24
    for w, x in words.items():
        fp = (fp + _strhash(w + b":%d,%d,%d" % x, 11)) % M24
    return fp


# --- mood scoring: a line-by-line port of build_mood() --------------------------
WORD_RE = re.compile(rb"[A-Za-z\x80-\xff']+")


def tagtext(s):
    return b"" if s is None or s == UNTAGGED else s


def genre_vec(gname, genre):
    gname = tagtext(gname).lower()                  # bytes.lower(): ASCII only, like Lua
    e = v = a = hits = 0
    for key, ge, gv, ga in genre:
        if key in gname:
            e, v, a, hits = e + ge, v + gv, a + ga, hits + 1
    if hits == 0:
        return 0, 0, 0, False
    return idiv(e, hits), idiv(v, hits), idiv(a, hits), True


def word_vec(text, words):
    text = tagtext(text)
    if not text:
        return 0, 0, 0
    e = v = a = 0
    for m in WORD_RE.finditer(text.lower()):
        x = words.get(m.group().replace(b"'", b""))
        if x:
            e, v, a = e + x[0], v + x[1], a + x[2]
    return clamp(e, -50, 50), clamp(v, -50, 50), clamp(a, -50, 50)


def build_mood(db, bpm_entries, lexicon):
    """Bytes of rockgenius_mood.bin. bpm_entries[r] = (bpm, key) as in the BPM file."""
    score_version, _magic, genre, words = lexicon
    T = db.T
    G = {off: genre_vec(s, genre) for off, s in db.stream_tagfile(T["genre"])}
    A, C = {}, {}
    for off, s in db.stream_tagfile(T["album"]):
        x = word_vec(s, words)
        if x != (0, 0, 0):
            A[off] = x
    for off, s in db.stream_tagfile(T["comment"]):
        x = word_vec(s, words)
        if x != (0, 0, 0):
            C[off] = x
    fp = lexicon_fp(score_version, genre, words)
    out = bytearray(MOOD_MAGIC)
    out += struct.pack("<IIII", db.entries & 0xFFFFFFFF, db.commit & 0xFFFFFFFF,
                       db.datasize & 0xFFFFFFFF, fp)
    for r in range(db.entries):
        rec = db.record(r)
        if rec is None or db.deleted(rec):
            out += b"\0" * 5
            continue
        ge, gv, ga, known = G.get(db.fld(rec, "genre"), (0, 0, 0, False))
        we, wv, wa = word_vec(db.tag_at(T["title"], db.fld(rec, "title")), words)
        for x in (A.get(db.fld(rec, "album")), C.get(db.fld(rec, "comment"))):
            if x:
                we, wv, wa = we + x[0], wv + x[1], wa + x[2]
        we, wv, wa = clamp(we, -50, 50), clamp(wv, -50, 50), clamp(wa, -50, 50)
        ln = idiv(db.fld(rec, "length"), 1000)
        le = 0
        if ln > 0:
            if ln < 150: le = 20
            elif ln < 210: le = 8
            elif ln > 420: le = -20
            elif ln > 330: le = -8
        bpm = 0
        bb, bh = bpm_entries[r] if r < len(bpm_entries) else (0, 0)
        if bb > 0:
            p = db.tag_at(T["filename"], db.fld(rec, "filename"))
            if p is not None and path_key(p, db.fld(rec, "mtime")) == bh:
                bpm = bb
        e = ge + we + le
        if bpm > 0:
            e = idiv(e + clamp((bpm - 115) * 2, -100, 100) * 2, 3)
        a = ga + wa
        if bpm > 150:
            a += 10
        e, a = clamp(e, -100, 100), clamp(a, -100, 100)
        v = clamp(gv + wv, -100, 100)
        out += bytes((e + 128, v + 128, a + 128, bpm, 3 if known else 2))
    return bytes(out)


# --- BPM ------------------------------------------------------------------------
def read_tag_bpm(path):
    try:                                        # plain ID3 first: cheapest, and works
        from mutagen.id3 import ID3             # even if the audio frames are odd
        t = ID3(path)
        if "TBPM" in t:
            return int(round(float(str(t["TBPM"].text[0]).split()[0])))
    except Exception:
        pass
    try:
        import mutagen
        f = mutagen.File(path)
        if f is None or f.tags is None:
            return 0
        t = f.tags
        for k in ("TBPM", "tmpo", "BPM", "bpm"):
            if k in t:
                v = t[k]
                v = v.text[0] if hasattr(v, "text") else (v[0] if isinstance(v, list) else v)
                return int(round(float(str(v).split()[0])))
    except Exception:
        pass
    return 0


def build_bpm(db, mount, bpm_by_path, tag_read=True, progress=True):
    """Returns (file bytes, entries [(bpm, key)], stats)."""
    out = bytearray(BPM_MAGIC + struct.pack("<I", db.entries))
    entries = []
    st = dict(report=0, tags=0, missing=0, deleted=0)
    for r in range(db.entries):
        rec = db.record(r)
        ent = (0, 0)
        if rec is not None and not db.deleted(rec):
            raw = db.tag_at(db.T["filename"], db.fld(rec, "filename"))
            if raw is not None:
                h = path_key(raw, db.fld(rec, "mtime"))
                local = device_to_local(raw.decode("utf-8", "replace"), mount)
                bpm = bpm_by_path.get(norm(local), 0)
                if bpm:
                    st["report"] += 1
                elif tag_read and local.exists():
                    bpm = read_tag_bpm(str(local))
                    if bpm:
                        st["tags"] += 1
                if not bpm:
                    st["missing"] += 1
                ent = (clamp(bpm, 0, 255), h)
        elif rec is not None:
            st["deleted"] += 1
        entries.append(ent)
        b, h = ent
        out += bytes((b, h & 255, (h >> 8) & 255, (h >> 16) & 255))
        if progress and r % 500 == 0:
            sys.stdout.write(f"\r  {r}/{db.entries} tracks")
            sys.stdout.flush()
    return bytes(out), entries, st


def write_atomic(dest, data):
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def find_lua(mount, explicit):
    if explicit:
        return Path(explicit)
    for p in (Path(mount) / ".rockbox" / "rocks" / "apps" / "rockgenius.lua",
              Path(__file__).resolve().parent.parent / "rockgenius.lua"):
        if p.exists():
            return p
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write RockGenius's BPM and mood caches for the player.")
    ap.add_argument("mount", help=r"the player's drive: /Volumes/IPOD, E:\, /media/you/IPOD")
    ap.add_argument("--report", default="bpm_report.csv",
                    help="bpm_tag.py's report (default ./bpm_report.csv)")
    ap.add_argument("--no-tag-read", action="store_true",
                    help="don't open files missing from the report; leave them unknown")
    ap.add_argument("--no-mood", action="store_true",
                    help="only write the BPM file; the player rebuilds the mood cache itself")
    ap.add_argument("--lua", help="rockgenius.lua to take the word lists from "
                    "(default: the copy on the player, else the one next to tools/)")
    ap.add_argument("--out", help="write the BPM file here instead of the player")
    ap.add_argument("--mood-out", help="write the mood file here instead of the player")
    a = ap.parse_args(argv)

    mount = player_root(a.mount)
    rbdir = mount / ".rockbox"
    if not (rbdir / "database_idx.tcd").exists() or not (rbdir / "database_4.tcd").exists():
        sys.exit(f"No Rockbox database under {rbdir}. Is the player connected, and has it "
                 "run Settings > General > Database > Initialize Now?")

    bpm_by_path = {}
    if os.path.exists(a.report):
        with open(a.report, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    b = int(row.get("bpm") or 0)
                except ValueError:
                    b = 0
                if b > 0 and row.get("path"):
                    bpm_by_path[norm(row["path"])] = b
        print(f"{len(bpm_by_path)} BPMs from {a.report}")
    else:
        print(f"No {a.report} found, so BPM tags are read from the files.")

    try:
        db = Database(rbdir)
    except ValueError as e:
        sys.exit(str(e))
    if db.entries <= 0:
        sys.exit("The player's database is empty. Run Database > Initialize Now on the player first.")

    data, entries, st = build_bpm(db, mount, bpm_by_path, not a.no_tag_read)
    dest = Path(a.out) if a.out else rbdir / "rockgenius_bpm.bin"
    write_atomic(dest, data)
    print(f"\r{db.entries} tracks in the player's database: {st['report']} BPM from report, "
          f"{st['tags']} from tags, {st['missing']} without BPM, {st['deleted']} deleted entries.")
    print(f"Wrote {dest}")

    mood_dest = Path(a.mood_out) if a.mood_out else rbdir / "rockgenius_mood.bin"
    lexicon, why = None, ""
    if not a.no_mood:
        lua = find_lua(mount, a.lua)
        if lua is None:
            why = "rockgenius.lua not found (use --lua)"
        else:
            try:
                lexicon = load_lexicon(lua)
                if lexicon[1] != MOOD_MAGIC:
                    why = f"{lua} uses cache format {lexicon[1].decode()}, this script writes {MOOD_MAGIC.decode()}"
                    lexicon = None
            except (OSError, ValueError) as e:
                why = f"couldn't read the word lists from {lua}: {e}"
    if lexicon:
        write_atomic(mood_dest, build_mood(db, entries, lexicon))
        print(f"Wrote {mood_dest} (word lists from {lua})")
        print("Eject the player and open RockGenius: it goes straight to the menu.")
    else:
        if why:
            print(f"Mood cache not written: {why}.")
        if not a.mood_out and mood_dest.exists():
            mood_dest.unlink()                  # forces RockGenius to re-score with BPMs
        if not a.out:
            print("Eject the player and open RockGenius: it rebuilds its mood cache once with these BPMs.")


if __name__ == "__main__":
    main()
