# Changelog

## 3.0.0

- Licensed under MIT.
- Play now starts the playlist itself with Rockbox's playlist API (`playlist_create`, then `playlist_start`). It no longer needs a patched Rockbox. It only falls back to writing `/.rockbox/rockgenius_play.txt` if that fails.
- Genius from now playing has a submenu:
  - More like this
  - Same vibe, other artists
  - Same vibe, same genre
  - Take it up a notch
  - Bring it down
  - Era match
  - Deep cuts like this
  - Playlist length: 25, 50 or 100, remembered between runs
- Mood dial: seven steps per axis, plus Era and Length steps. Playlists get readable names like "Dial - Calm, Warm, Soft, 90s".
- New presets: Sunday Morning, Road Trip, Late Night Drive, Focus, Breakup, Pregame, Throwback, and Running at 160, 170 and 180 spm.
- The main menu is grouped into Genius, Moods, Workouts, Discovery, Mood dial and Tools. The version shows in the menu title.
- `<Untagged>`, what Rockbox stores for a missing tag, is treated as empty. It's never scored as a word or a genre. Untagged songs appear under their file name, and they don't count as one artist.
- Integer maths made explicit with `idiv()` and `umod()`, so results are the same on the player (C's truncating `%` and `/`) and on a desktop Lua. Fixes:
  - deleted database entries were not detected when the flag word had its top bit set;
  - the BPM key was negative for negative mtimes.
- The BPM file is read through a small window instead of being loaded whole. A BPM scan or cache build no longer grows with the library.
- Mood cache format RGM3: the header adds a fingerprint of the scoring rules and the GENRE and WORDS tables. Editing them, or a cache written by another copy of the plugin, triggers a rebuild. RGM2 caches are rebuilt once.
- Word matching keeps non-ASCII letters inside a word, so WORDS can hold words like `["corazón"]`.
- Running lists say what to do when no BPM is known. The no-back-to-back-artists pass now also fixes the last song in the list.
- Tools: `mac/` renamed to `tools/`. All three scripts work on macOS, Windows and Linux, including drive letters like `E:\`.
  - `bpm_export.py` also writes `rockgenius_mood.bin`, byte-identical to what the player builds, so the player never shows "Building mood cache".
  - `fix_names.py` has `--all`.
- Tests:
  - every menu path;
  - Play now on both paths;
  - odd libraries: flat folders, files at the root, untagged files, the `/<HDD0>` prefix, deleted entries;
  - a 10,000-track library with a heap check;
  - Python-to-Lua cache compatibility for RGB2 and RGM3.

  Everything runs under lupa and under a Lua built with Rockbox's integer configuration and its 30-upvalue and 100-local limits. GitHub Actions workflow added.

## 2.0

- Mood scores cached in `/.rockbox/rockgenius_mood.bin`. The library is streamed from disk instead of held in memory, so Lua stays inside the plugin buffer and playback keeps going.
- Incremental BPM scan, desktop BPM tools, run log.

## 1.0

- First version.
