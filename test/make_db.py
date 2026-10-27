"""Builds a fake Rockbox tagcache (database_idx.tcd + database_N.tcd) plus stub
MP3 files with ID3v2.3 TBPM frames, in ROOT/.rockbox and ROOT/Music.

The layout follows apps/tagcache.c (tag v4.0-final): 24-byte master header,
23 fields + flag per record, string entries padded to 8 bytes with 'X' after the
NUL, and "<Untagged>" stored for every missing string tag.

usage: make_db.py ROOT [N] [--layout tree|flat|mixed] [--untagged-every K]
                        [--prefix /<HDD0>] [--no-audio]
(RBPREFIX in the environment still works as the prefix, as before.)
"""
import argparse, os, random, struct

MAGIC = 0x54434810
TAG_COUNT = 23
UNTAGGED = "<Untagged>"

GENRES = ["Ambient", "Hip-Hop", "Thrash Metal", "Indie Pop", "Classic Rock", "Drum & Bass",
          "Jazz", "Doom Metal", "Country", "House", "Singer/Songwriter", "Punk Rock", "Lo-Fi",
          "Trap", "Post-Punk", "Bossa Nova", "Grunge", "Soul"]
WORDS = ["Rain", "Fire", "Sleep", "Run", "Midnight", "Sunshine", "Goodbye", "Riot", "Dream",
         "Love", "Skull", "Golden", "Storm", "Quiet", "Party", "Lost", "Alive", "Scream", "Home",
         "Thunder", "Don't", "Corazón", "Ghost"]
YEARS = [0, 1968, 1975, 1983, 1991, 1997, 2004, 2008, 2013, 2019, 2023]


def _path(i, t, layout, rng):
    if layout == "tree":
        return f"/Music/{t['a']}/{t['al']}/{i:05d} {t['t']}.mp3"
    if layout == "flat":                       # one big folder, no track numbers
        return f"/Music/{t['t']}.mp3"
    k = rng.randint(0, 3)                      # mixed
    if k == 0:
        return f"/Music/{t['a']}/{t['al']}/{i % 20 + 1:02d} {t['t']}.mp3"
    if k == 1:
        return f"/Music/{t['t']}.mp3"
    if k == 2:
        return f"/{t['t']}.mp3"                # loose at the root of the drive
    return f"/Music/Misc/Song {i}.mp3"


def build(root, n=300, prefix="", layout="tree", untagged_every=0, seed=7,
          deleted=(5, 10), audio=True, mtime_negative=False):
    """Write the database. Returns the list of track dicts (index == record)."""
    rng = random.Random(seed)
    rb = os.path.join(root, ".rockbox")
    os.makedirs(rb, exist_ok=True)
    artists = [f"Artist {chr(65 + i % 26)}{i}" for i in range(max(20, n // 12))]

    tracks = []
    for i in range(n):
        t = dict(g=rng.choice(GENRES), a=rng.choice(artists),
                 t=" ".join(rng.sample(WORDS, 2)) + f" {i}",
                 al=f"Album {rng.randint(1, 40)}", len=rng.randint(60, 480) * 1000,
                 pc=rng.choice([0, 0, 0, 1, 2, 5, 12, 30]), rt=rng.choice([0, 0, 5, 8, 10]),
                 bpm=rng.choice([0, 0, 85, 90, 120, 128, 145, 160, 170, 175, 180]),
                 lp=rng.randint(0, 500), year=rng.choice(YEARS), track=i % 20 + 1,
                 comment=("Normalized %08X" % rng.getrandbits(32)) if i % 3 else UNTAGGED,
                 untagged=False)
        if untagged_every and i % untagged_every == 1:
            t.update(g=UNTAGGED, a=UNTAGGED, t=UNTAGGED, al=UNTAGGED, comment=UNTAGGED,
                     year=0, track=-1, untagged=True)
        name_src = dict(t)
        if t["untagged"]:                      # the file still has a name on disk
            name_src.update(t=f"Track {i}", a="Unknown", al="Unknown")
        t["p"] = _path(i, name_src, layout, rng)
        mt = rng.randint(0x30000000, 0x5FFFFFFF)       # FAT date/time, 2004..2027
        if mtime_negative and i % 2:
            mt = -rng.randint(1, 2**31 - 1)
        t["mtime"] = mt
        t["deleted"] = i in deleted
        tracks.append(t)

    seen_paths = {}
    for t in tracks:                            # keep paths unique (flat layout)
        p = t["p"]
        if p in seen_paths:
            seen_paths[p] += 1
            base, ext = os.path.splitext(p)
            p = f"{base} ({seen_paths[p]}){ext}"
            t["p"] = p
        seen_paths.setdefault(p, 0)

    def write_tag(tag, strings, unique):
        entries, seen = [], set()
        for idx, s in enumerate(strings):
            if unique:
                if s in seen:
                    continue
                seen.add(s)
                entries.append((s, -1))
            else:
                entries.append((s, idx))
        if unique:
            entries.sort(key=lambda e: e[0].casefold())
        body, offs, pos = bytearray(), {}, 12
        for s, idx in entries:
            raw = s.encode("utf-8")
            length = len(raw) + 1
            if (length + 8) % 8:
                length += 8 - (length + 8) % 8
            offs[s if unique else idx] = pos
            body += struct.pack("<ii", length, idx) + raw + b"\0"
            body += b"X" * (length - len(raw) - 1)
            pos += 8 + length
        with open(os.path.join(rb, f"database_{tag}.tcd"), "wb") as f:
            f.write(struct.pack("<iii", MAGIC, len(body), len(entries)) + body)
        return offs

    o_artist = write_tag(0, [t["a"] for t in tracks], True)
    o_album = write_tag(1, [t["al"] for t in tracks], True)
    o_genre = write_tag(2, [t["g"] for t in tracks], True)
    o_title = write_tag(3, [t["t"] for t in tracks], False)
    o_file = write_tag(4, [prefix + t["p"] for t in tracks], False)
    o_comp = write_tag(5, [UNTAGGED] * len(tracks), True)
    o_comm = write_tag(6, [t["comment"] for t in tracks], True)
    o_aa = write_tag(7, [t["a"] for t in tracks], True)
    o_grp = write_tag(8, [UNTAGGED] * len(tracks), True)
    o_canon = write_tag(12, [t["a"] for t in tracks], True)

    idx = bytearray()
    for i, t in enumerate(tracks):
        seek = [0] * TAG_COUNT
        seek[0] = o_artist[t["a"]]; seek[1] = o_album[t["al"]]; seek[2] = o_genre[t["g"]]
        seek[3] = o_title[i]; seek[4] = o_file[i]; seek[5] = o_comp[UNTAGGED]
        seek[6] = o_comm[t["comment"]]; seek[7] = o_aa[t["a"]]; seek[8] = o_grp[UNTAGGED]
        seek[9] = t["year"]; seek[10] = 1; seek[11] = t["track"]; seek[12] = o_canon[t["a"]]
        seek[13] = 320; seek[14] = t["len"]; seek[15] = t["pc"]; seek[16] = t["rt"]
        seek[17] = t["pc"] * t["len"]; seek[18] = t["lp"]; seek[19] = 1; seek[20] = t["mtime"]
        flag = 1 if t["deleted"] else 0
        if i % 7 == 3:
            flag |= 0x80010000                  # attr bits set: the int is negative
        idx += struct.pack("<23i", *seek) + struct.pack("<I", flag)
    # like tagcache.c: master header + all index entries + every tag file's data
    # except the filename one. Readers must not use it as the record size.
    tagdata = sum(os.path.getsize(os.path.join(rb, f"database_{k}.tcd")) - 12
                  for k in range(13) if k != 4 and os.path.exists(os.path.join(rb, f"database_{k}.tcd")))
    with open(os.path.join(rb, "database_idx.tcd"), "wb") as f:
        f.write(struct.pack("<iii", MAGIC, 24 + len(idx) + tagdata, len(tracks)))
        f.write(struct.pack("<iii", 600, 3, 0))
        f.write(idx)

    if audio:
        def ss(n):
            return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])
        for t in tracks:
            fp = os.path.join(root, t["p"].lstrip("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            frames = b""
            if t["bpm"]:
                txt = b"\x00" + str(t["bpm"]).encode()
                frames += b"TIT2" + struct.pack(">i", 5) + b"\0\0" + b"\x00abcd"
                frames += b"TBPM" + struct.pack(">i", len(txt)) + b"\0\0" + txt
            with open(fp, "wb") as f:
                f.write(b"ID3\x03\x00\x00" + ss(len(frames)) + frames + b"\xff\xfb" + b"\0" * 64)
    return tracks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("n", nargs="?", type=int, default=300)
    ap.add_argument("--layout", default="tree", choices=["tree", "flat", "mixed"])
    ap.add_argument("--untagged-every", type=int, default=0)
    ap.add_argument("--prefix", default=os.environ.get("RBPREFIX", ""))
    ap.add_argument("--no-audio", action="store_true")
    a = ap.parse_args()
    tracks = build(a.root, a.n, a.prefix, a.layout, a.untagged_every, audio=not a.no_audio)
    print("ok", len(tracks))


if __name__ == "__main__":
    main()
