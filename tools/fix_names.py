#!/usr/bin/env python3
"""
fix_names.py -- tidy track filenames like

    00 Morgan Wallen - 3 Songs at a Time Sampler - 01 - Last Night.mp3
 -> 01 Last Night.mp3

Purely cosmetic: RockGenius reads tags, not filenames. The artist and album
are dropped from the name because the tags (and usually the folders) already
say them. Files that don't follow the long pattern are left alone, and so are
the macOS "._" sidecar files (they're renamed along with their track).

It previews by default; add --apply to actually rename.

    macOS:    python3 fix_names.py /Volumes/IPOD/Music "Jelly Roll" "Morgan Wallen"
    Windows:  py fix_names.py E:\\Music "Jelly Roll" "Morgan Wallen"
    Linux:    python3 fix_names.py /media/$USER/IPOD/Music --all
    (add --apply once the preview looks right)

Afterwards on the player: Database > Update Now (the database stores filenames).
"""
import argparse, os, re, sys, unicodedata
from pathlib import Path

EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus",
        ".wav", ".aif", ".aiff"}
SEP = re.compile(r"\s+-\s+")


def norm(s):
    return unicodedata.normalize("NFC", s).casefold()


def new_name(fname):
    """Return the tidy name, or None if the file doesn't follow the long pattern."""
    stem, ext = os.path.splitext(fname)
    if ext.lower() not in EXTS:
        return None
    s = re.sub(r"^\d{1,3}[\s._-]+", "", stem, count=1)       # leading "00 "
    parts = SEP.split(s)
    if len(parts) < 3:
        return None
    # the track number is the last all-digits part that has a title after it
    for k in range(len(parts) - 2, 0, -1):
        m = re.fullmatch(r"(?:\d{1,2}[-.])?(\d{1,3})", parts[k].strip())
        if m:
            title = " - ".join(parts[k + 1:]).strip()
            if not title:
                return None
            title = re.sub(r'[/:\\\\*?"<>|]', "_", title)     # FAT-safe
            return f"{int(m.group(1)):02d} {title}{ext}"
    return None


def music_root(arg):
    if re.fullmatch(r"[A-Za-z]:", arg):             # "E:" means "E:\\" here
        arg += "\\"
    return Path(arg)


def rename(src, dst):
    if norm(src.name) == norm(dst.name):            # case-only change on FAT / NTFS / APFS
        tmp = dst.with_name(dst.name + ".tmp_rename")
        src.rename(tmp)
        tmp.rename(dst)
    else:
        src.rename(dst)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shorten 'NN Artist - Album - NN - Title' filenames.")
    ap.add_argument("music", help=r"music root, e.g. /Volumes/IPOD/Music or E:\Music")
    ap.add_argument("artists", nargs="*", help="artist folder names to fix")
    ap.add_argument("--all", action="store_true", help="fix every file under the music root")
    ap.add_argument("--apply", action="store_true", help="actually rename (default: preview)")
    a = ap.parse_args(argv)

    music = music_root(a.music)
    if not music.is_dir():
        sys.exit(f"{music} not found. Is the player connected, and is that its music folder?")
    if not a.artists and not a.all:
        sys.exit("Name one or more artist folders, or pass --all.")
    if a.all:
        targets = [music]
    else:
        folders = {norm(d.name): d for d in music.iterdir() if d.is_dir()}
        targets = []
        for name in a.artists:
            d = folders.get(norm(name))
            if d:
                targets.append(d)
            else:
                squash = lambda x: re.sub(r"[\W_]+", "", norm(x))
                close = [v.name for k, v in folders.items()
                         if squash(name) in squash(k) or squash(k) in squash(name)]
                hint = f" (did you mean: {', '.join(sorted(close)[:5])}?)" if close else ""
                print(f"!! no folder named \"{name}\" in {music}{hint}")
        if not targets:
            sys.exit(1)

    renamed = skipped = clash = 0
    for top in targets:
        print(f"\n== {top.name or top}")
        for dpath, dirs, names in os.walk(top):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            here = Path(dpath)
            taken = {norm(n) for n in names}
            for n in sorted(names):
                if n.startswith("."):                 # includes macOS "._" junk
                    continue
                new = new_name(n)
                if not new or new == n:
                    skipped += 1
                    continue
                rel = here.relative_to(music)
                if norm(new) in taken and norm(new) != norm(n):
                    print(f"   CLASH  {rel / n}  ->  {new}  (already exists, left alone)")
                    clash += 1
                    continue
                print(f"   {rel}{os.sep}  {n}\n      -> {new}")
                if a.apply:
                    rename(here / n, here / new)
                    side = here / ("._" + n)          # macOS sidecar on FAT
                    if side.exists():
                        side.rename(here / ("._" + new))
                taken.discard(norm(n)); taken.add(norm(new))
                renamed += 1

    verb = "Renamed" if a.apply else "Would rename"
    print(f"\n{verb} {renamed} file(s); {skipped} already fine; {clash} clash(es).")
    if not a.apply and renamed:
        print("Looks right? Run again with --apply.")
    elif a.apply and renamed:
        print("On the player: Database > Update Now.")


if __name__ == "__main__":
    main()
