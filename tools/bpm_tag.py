#!/usr/bin/env python3
"""
bpm_tag.py -- detect BPM for every song in a folder and write it into the
file's own tags, so RockGenius (and anything else) can read it.

  MP3 / WAV / AIFF -> ID3 TBPM      FLAC / OGG / Opus -> Vorbis "BPM"
  M4A / MP4 / AAC  -> iTunes "tmpo"

Files that already have a BPM tag are skipped unless you pass --force.
Nothing is renamed or moved; only the BPM tag is added.

Setup (once): Python 3.12 and ffmpeg, then
    python3 -m pip install librosa mutagen numpy        (Windows: py -m pip ...)
docs/GUIDE.md has the step-by-step for macOS, Windows and Linux.

Run it on the player's music folder (preview first):
    macOS:    python3 bpm_tag.py /Volumes/IPOD/Music --dry-run
    Windows:  py bpm_tag.py E:\\Music --dry-run
    Linux:    python3 bpm_tag.py /media/$USER/IPOD/Music --dry-run
then the same without --dry-run to write the tags. Or run it on your
computer's music folder and copy the files over afterwards.

Afterwards on the player: Database > Update Now, then run bpm_export.py.
"""
import argparse, os, re, subprocess, sys, time, csv, warnings, unicodedata
from pathlib import Path
warnings.filterwarnings("ignore")

if os.name == "nt":
    os.system("")                # turns on ANSI escapes in the Windows console


def install_hint(what):
    if what == "ffmpeg":
        if sys.platform == "darwin":
            return "brew install ffmpeg"
        if os.name == "nt":
            return "winget install Gyan.FFmpeg   (then open a new PowerShell window)"
        return "sudo apt install ffmpeg   (Fedora: sudo dnf install ffmpeg-free)"
    py = "py" if os.name == "nt" else "python3"
    return f"{py} -m pip install librosa mutagen numpy"

EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus",
        ".wav", ".aif", ".aiff"}
SR = 22050


# ---------------------------------------------------------------- tags
def read_bpm(path):
    import mutagen
    from mutagen.id3 import ID3
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":                        # ID3 directly: works even when the
            try:                                 # audio frames confuse mutagen.File
                tags = ID3(path)
            except Exception:
                return None
            return _num(str(tags["TBPM"].text[0])) if "TBPM" in tags else None
        if ext in (".wav", ".aif", ".aiff"):
            f = mutagen.File(path)
            tags = getattr(f, "tags", None)
            if tags is not None and "TBPM" in tags:
                return _num(str(tags["TBPM"].text[0]))
            return None
        f = mutagen.File(path)
        if f is None or f.tags is None:
            return None
        if ext in (".m4a", ".mp4", ".aac", ".alac"):
            v = f.tags.get("tmpo")
            return int(v[0]) if v else None
        for k in ("bpm", "BPM", "tbpm", "TBPM"):
            if k in f.tags:
                return _num(f.tags[k][0])
    except Exception:
        return None
    return None


def write_bpm(path, bpm):
    import mutagen
    from mutagen.id3 import ID3, TBPM, ID3NoHeaderError
    ext = os.path.splitext(path)[1].lower()
    bpm = int(round(bpm))
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.setall("TBPM", [TBPM(encoding=3, text=str(bpm))])
        tags.save(path, v2_version=3)            # v2.3: widest player support
    elif ext in (".wav", ".aif", ".aiff"):
        f = mutagen.File(path)
        if f.tags is None:
            f.add_tags()
        f.tags.setall("TBPM", [TBPM(encoding=3, text=str(bpm))])
        f.save()
    elif ext in (".m4a", ".mp4", ".aac", ".alac"):
        f = mutagen.File(path)
        if f.tags is None:
            f.add_tags()
        f.tags["tmpo"] = [bpm]
        f.save()
    else:                                        # FLAC / Ogg / Opus
        f = mutagen.File(path)
        if f.tags is None:
            f.add_tags()
        f.tags["BPM"] = [str(bpm)]
        f.save()


def _num(s):
    try:
        return int(round(float(str(s).strip().split()[0])))
    except Exception:
        return None


# ---------------------------------------------------------------- analysis
def decode(path, offset, dur):
    import numpy as np
    cmd = ["ffmpeg", "-v", "quiet", "-ss", str(offset), "-t", str(dur), "-i", path,
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             check=False, timeout=120).stdout
    except subprocess.TimeoutExpired:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32)


def duration(path):
    try:
        import mutagen
        f = mutagen.File(path)
        return float(f.info.length) if f and f.info else 0.0
    except Exception:
        return 0.0


def detect_bpm(path, lo, hi):
    import numpy as np, librosa
    length = duration(path)
    # analyse up to 90 s from the body of the song, skipping intros
    start = 30.0 if length > 150 else (length * 0.15 if length > 30 else 0.0)
    y = decode(path, start, 90)
    if y.size < SR * 8:
        y = decode(path, 0, 90)
    if y.size < SR * 8:
        return None, 0.0
    onset = librosa.onset.onset_strength(y=y, sr=SR, aggregate=np.median)
    tempo = librosa.feature.tempo(onset_envelope=onset, sr=SR, aggregate=None)
    tempo = float(np.median(tempo)) if np.size(tempo) else 0.0
    if tempo <= 0:
        return None, 0.0
    # refine to sub-frame precision: parabolic fit around the autocorrelation
    # peak of the onset envelope at the lag librosa chose
    fps = SR / 512.0
    ac = librosa.autocorrelate(onset - onset.mean())
    lag = 60.0 * fps / tempo
    i = int(round(lag))
    if 2 <= i < ac.size - 2:
        i = i - 1 + int(np.argmax(ac[i - 1:i + 2]))     # nearest true peak
        a0, a1, a2 = ac[i - 1], ac[i], ac[i + 1]
        den = a0 - 2 * a1 + a2
        shift = 0.5 * (a0 - a2) / den if den != 0 else 0.0
        if abs(shift) < 1:
            fine = 60.0 * fps / (i + shift)
            if abs(fine - tempo) / tempo < 0.08:
                tempo = fine
    # fold half/double-time into the preferred range
    while tempo < lo:
        tempo *= 2
    while tempo > hi:
        tempo /= 2
    # confidence: how peaky the onset autocorrelation is at the chosen lag
    ac = librosa.autocorrelate(onset - onset.mean())
    lag = int(round(60.0 * SR / 512 / tempo))
    conf = float(ac[lag] / ac[0]) if 0 < lag < ac.size and ac[0] > 0 else 0.0
    return int(round(tempo)), conf


JOURNAL = "bpm_journal.txt"   # on the computer, next to the report: tracks writes in progress


def journal(line):
    with open(JOURNAL, "a", encoding="utf-8") as fh:   # short appends are atomic
        fh.write(line + "\n")


def work(path, root, lo, hi, dry, force):
    if not os.path.isdir(root):
        return path, "gone", None, 0.0
    try:
        if not force:
            have = read_bpm(path)
            if have:
                return path, "skip", have, 0.0
        bpm, conf = detect_bpm(path, lo, hi)
        if not os.path.isdir(root):
            return path, "gone", None, 0.0
        if not bpm:
            return path, "fail", None, 0.0
        if not dry:
            journal("START\t" + path)
            write_bpm(path, bpm)
            journal("DONE\t" + path)
        return path, "tagged" if not dry else "would-tag", bpm, conf
    except Exception as e:
        if not os.path.isdir(root):
            return path, "gone", None, 0.0
        return path, "error: %s" % e, None, 0.0


def unfinished_writes():
    """Files whose tag write started but never finished (e.g. player unplugged)."""
    if not os.path.exists(JOURNAL):
        return []
    state = {}
    with open(JOURNAL, encoding="utf-8") as fh:
        for line in fh:
            k, _, p = line.rstrip("\n").partition("\t")
            if p:
                state[p] = k
    return sorted(p for p, k in state.items() if k == "START")


def check_file(path):
    """True if the file's tags parse and the first seconds of audio decode cleanly."""
    import mutagen
    try:
        if mutagen.File(path) is None:
            return False, "tags unreadable"
    except Exception as e:
        return False, "tags unreadable: %s" % e
    r = subprocess.run(["ffmpeg", "-v", "error", "-t", "20", "-i", path, "-f", "null", "-"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0 or r.stderr.strip():
        return False, (r.stderr.strip().splitlines() or ["decode failed"])[0]
    return True, ""


DONE_LIST = "bpm_done.txt"      # files finished (tagged or already had BPM)


def mark_done(path):
    with open(DONE_LIST, "a", encoding="utf-8") as fh:
        fh.write(unicodedata.normalize("NFC", os.path.abspath(path)) + "\n")


def load_done(report):
    norm = lambda p: unicodedata.normalize("NFC", os.path.abspath(p)).casefold()
    done = set()
    if os.path.exists(DONE_LIST):
        with open(DONE_LIST, encoding="utf-8") as fh:
            done.update(norm(l.rstrip("\n")) for l in fh if l.strip())
    if os.path.exists(report):                  # results of earlier runs, even cut-short ones
        with open(report, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("status") in ("tagged", "skip") and row.get("path"):
                    done.add(norm(row["path"]))
    return done


SKIPLIST = "bpm_skiplist.txt"   # files that crashed or hung the analyser


def load_skiplist():
    if not os.path.exists(SKIPLIST):
        return set()
    with open(SKIPLIST, encoding="utf-8") as fh:
        return {l.rstrip("\n").split("\t", 1)[-1] for l in fh if l.strip()}


def add_skiplist(path, why):
    with open(SKIPLIST, "a", encoding="utf-8") as fh:
        fh.write(f"{why}\t{path}\n")


def _worker(wid, inq, conn, stop, root, lo, hi, dry, force):
    import warnings
    warnings.filterwarnings("ignore")
    while not stop.is_set():
        try:
            path = inq.get(timeout=0.5)
        except Exception:
            continue
        if path is None:
            break
        conn.send(("start", path))                  # pipe send is synchronous: the parent
        conn.send(("done", work(path, root, lo, hi, dry, force)))   # sees it even if we crash
    conn.send(("exit", None))


def supervise(files, root, lo, hi, dry, force, jobs, timeout, show):
    """Run work() over files in separate worker processes. A worker that crashes
    (segfault in a decoder) or hangs is replaced, and only that one file is marked
    bad -- the rest of the run carries on. Returns True if the run was cut short."""
    import multiprocessing as mp
    from multiprocessing.connection import wait
    ctx = mp.get_context("spawn")
    inq, stop = ctx.Queue(), ctx.Event()
    for p in files:
        inq.put(p)
    for _ in range(jobs):
        inq.put(None)
    procs, conns, current, started = {}, {}, {}, {}

    def spawn(wid):
        parent, child = ctx.Pipe(duplex=False)
        pr = ctx.Process(target=_worker,
                         args=(wid, inq, child, stop, root, lo, hi, dry, force), daemon=True)
        pr.start()
        child.close()
        procs[wid], conns[wid], current[wid] = pr, parent, None

    for w in range(jobs):
        spawn(w)
    alive, cut_short, finished = set(procs), False, 0
    by_conn = lambda: {conns[w]: w for w in alive}

    def handle(w, msg):
        nonlocal cut_short, finished
        kind, val = msg
        if kind == "start":
            current[w], started[w] = val, time.time()
        elif kind == "done":
            current[w] = None
            if val[1] == "gone":
                if not cut_short:
                    print(f"\n{root} disappeared (player disconnected?) -- stopping.")
                cut_short = True
                stop.set()
            else:
                show(*val)
                finished += 1
        elif kind == "exit":
            alive.discard(w)

    def reap():
        nonlocal finished
        for w in list(alive):
            pr, path = procs[w], current.get(w)
            hung = path and timeout and time.time() - started.get(w, 0) > timeout
            if pr.is_alive() and not hung:
                continue
            # drain anything it managed to send before dying
            try:
                while conns[w].poll():
                    handle(w, conns[w].recv())
            except (EOFError, OSError):
                pass
            if w not in alive:
                continue
            path = current.get(w)
            if hung:
                pr.kill()
            pr.join(1)
            if path:
                why = "timeout" if hung else "crashed"
                add_skiplist(path, why)
                show(path, why, None, 0.0)
                finished += 1
            if stop.is_set():
                alive.discard(w)
            else:
                spawn(w)                          # replacement keeps pulling from the queue

    try:
        while alive:
            m = by_conn()
            for c in wait(list(m), timeout=1.0):
                w = m[c]
                try:
                    handle(w, c.recv())
                except (EOFError, OSError):
                    pass                          # worker died; reap() deals with it
            reap()
    except KeyboardInterrupt:
        print("\nInterrupted -- letting in-flight files finish...")
        stop.set()
        cut_short = True
        deadline = time.time() + 60
        while alive and time.time() < deadline:
            try:
                m = by_conn()
                for c in wait(list(m), timeout=1.0):
                    try:
                        handle(m[c], c.recv())
                    except (EOFError, OSError):
                        alive.discard(m[c])
            except KeyboardInterrupt:
                break
    for pr in procs.values():
        pr.join(2)
        if pr.is_alive():
            pr.kill()
    return cut_short or finished < len(files)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Detect and write BPM tags.")
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true", help="analyse but don't write")
    ap.add_argument("--force", action="store_true", help="re-analyse files that already have BPM")
    ap.add_argument("--range", default="70-180",
                    help="fold half/double time into this range (default 70-180)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--report", default="bpm_report.csv")
    ap.add_argument("--timeout", type=int, default=240,
                    help="give up on a single file after this many seconds (default 240)")
    ap.add_argument("--only", metavar="ARTIST", action="append",
                    help='only these artist folders (repeat: --only "Jelly Roll" --only HARDY)')
    ap.add_argument("--start-from", metavar="TEXT",
                    help='skip every file that sorts before this (e.g. "Luke Combs/Gettin")')
    ap.add_argument("--recheck", action="store_true",
                    help="ignore the done-list and look at every file again")
    ap.add_argument("--check", action="store_true",
                    help="verify files whose tag write was interrupted, then exit")
    a = ap.parse_args()
    if re.fullmatch(r"[A-Za-z]:", a.folder):        # "E:" means "E:\\" here
        a.folder += "\\"
    root = os.path.abspath(a.folder)
    lo, hi = (float(x) for x in a.range.split("-"))

    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, check=True)
    except Exception:
        sys.exit("ffmpeg not found. Install it with:  " + install_hint("ffmpeg"))
    try:
        import librosa, mutagen  # noqa
    except ImportError:
        sys.exit("Missing packages. Install with:  " + install_hint("pip"))

    if not Path(root).is_dir():
        sys.exit(f"{root} not found. Is the player connected, and is that its music folder?")

    pending = unfinished_writes()
    if a.check or pending:
        if pending:
            print(f"{len(pending)} file(s) were being written when the last run stopped. Checking them:")
        bad = []
        for p in pending:
            if not os.path.exists(p):
                print("  missing  ", p); continue
            ok, why = check_file(p)
            print(("  ok       " if ok else "  DAMAGED  ") + os.path.relpath(p, root) + ("" if ok else "  -- " + why))
            if ok:
                journal("DONE\t" + p)
            else:
                bad.append(p)
        if bad:
            sys.exit(f"\n{len(bad)} damaged file(s) above: re-copy them from your computer's library, "
                     "then run again.")
        if a.check:
            print("All clear."); return
        print()

    walk_roots = [a.folder]
    if a.only:
        nf = lambda x: unicodedata.normalize("NFC", x).casefold()
        top = {nf(d): d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))}
        walk_roots = []
        for name in a.only:
            d = top.get(nf(name))
            if d:
                walk_roots.append(os.path.join(root, d))
            else:
                sq = lambda x: "".join(ch for ch in nf(x) if ch.isalnum())
                close = sorted(v for k, v in top.items() if sq(name) in sq(k) or sq(k) in sq(name))[:5]
                print(f'!! no folder "{name}" under {root}' + (f" (did you mean: {', '.join(close)}?)" if close else ""))
        if not walk_roots:
            sys.exit(1)
        print("Only: " + ", ".join(os.path.basename(w) for w in walk_roots))

    files = []
    for dpath, dirs, names in (x for w in walk_roots for x in os.walk(w)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if n.startswith("._"):                 # macOS resource-fork junk on FAT
                continue
            if os.path.splitext(n)[1].lower() in EXTS:
                files.append(os.path.abspath(os.path.join(dpath, n)))
    nfc = lambda p: unicodedata.normalize("NFC", p).casefold()
    files.sort(key=nfc)

    # Resume without touching the player for files already handled. The done-list
    # lives on the computer (bpm_done.txt), seeded from any earlier bpm_report.csv.
    done = set() if a.recheck else load_done(a.report)
    if done:
        before = len(files)
        files = [p for p in files if nfc(p) not in done]
        print(f"Resuming: {before - len(files)} file(s) already handled in earlier runs "
              f"(from {DONE_LIST}/{os.path.basename(a.report)}; --recheck to redo).")
    if a.start_from:
        key = nfc(os.path.join(root, a.start_from))
        before = len(files)
        files = [p for p in files if nfc(os.path.abspath(p)) >= key]
        print(f"--start-from: skipping {before - len(files)} file(s) before \"{a.start_from}\".")
    print(f"{len(files)} audio files under {a.folder}  (jobs={a.jobs}, range {lo:g}-{hi:g})")

    skiplist = load_skiplist()
    if skiplist:
        before = len(files)
        files = [p for p in files if p not in skiplist]
        print(f"Skipping {before - len(files)} file(s) that crashed the analyser before "
              f"(listed in {SKIPLIST}; delete that file to retry them).")

    print("Starting... (the first results take ~30 s while the audio libraries load; "
          "files that already have a BPM are skipped quickly)", flush=True)
    counts, t0, rows = {}, time.time(), []
    total = len(files)
    state = {"i": 0}

    def show(path, status, bpm, conf):
        state["i"] += 1
        i = state["i"]
        key = status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        rows.append((path, status, bpm or "", round(conf, 2)))
        if status in ("tagged", "skip"):
            mark_done(path)
        if status != "skip":
            flag = "  (low confidence)" if status in ("tagged", "would-tag") and conf < 0.2 else ""
            line = f"[{i}/{total}] {status:9s} {str(bpm or ''):>4} {os.path.relpath(path, a.folder)}{flag}"
            sys.stdout.write("\r\033[K" + line + "\n")
        el = time.time() - t0
        eta = ""
        if i > 20 and el > 5:
            left = (total - i) * el / i
            eta = "  ~%dm%02ds left" % (left // 60, left % 60)
        sys.stdout.write("\r\033[K  %d/%d done (%d%%)  tagged %d  skipped %d  failed %d%s" % (
            i, total, i * 100 // max(1, total),
            counts.get("tagged", 0) + counts.get("would-tag", 0), counts.get("skip", 0),
            counts.get("fail", 0) + counts.get("error", 0) + counts.get("crashed", 0)
            + counts.get("timeout", 0), eta))
        sys.stdout.flush()

    gone = supervise(files, root, lo, hi, a.dry_run, a.force, a.jobs, a.timeout, show)

    print()
    old = []
    if os.path.exists(a.report):                # keep earlier runs' rows, newest wins
        seen = {r[0] for r in rows}
        with open(a.report, newline="", encoding="utf-8") as fh:
            old = [r for r in csv.reader(fh)][1:]
        old = [r for r in old if r and r[0] not in seen]
    with open(a.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "status", "bpm", "confidence"])
        w.writerows(sorted(old + [list(r) for r in rows]))
    took = time.time() - t0
    print("\nDone in %dm%02ds: %s" % (took // 60, took % 60,
          ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))))
    print(f"Report: {os.path.abspath(a.report)}")
    if gone:
        print("Run the same command again to continue (reconnect the player first if it dropped): "
              "it checks any file that was mid-write, then carries on where it stopped.")
    elif not a.dry_run:
        if os.path.exists(JOURNAL):
            os.remove(JOURNAL)
        print("On the player: Database > Update Now. Then run bpm_export.py with the player connected.")


if __name__ == "__main__":
    main()
