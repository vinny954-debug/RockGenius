# RockGenius guide

This takes you from a fresh download to a playlist playing on your player. You don't need to know how to use a terminal. Where you have to type a command, the guide gives it to you for each operating system. Copy it, change the drive or folder name if yours is different, and paste.

- [What you need](#what-you-need)
- [Step 1: prepare the library](#step-1-prepare-the-library)
- [Step 2: tidy file names (optional)](#step-2-tidy-file-names-optional)
- [Step 3: install the plugin](#step-3-install-the-plugin)
- [Step 4: BPM (optional)](#step-4-bpm-optional)
- [Step 5: using it](#step-5-using-it)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Customizing](#customizing)
- [How it works](#how-it-works)

## What you need

- A player running Rockbox with the Lua plugin. Official Rockbox builds include it. If your player has a `.rockbox/rocks/viewers/lua.rock` file, you're set.
- A computer (macOS, Windows or Linux) and the USB cable for the player.
- Music with decent tags.

RockGenius reads tags, not filenames. It gets everything from the database Rockbox builds out of your tags: title, artist, album, genre, year, length and play count. Folders don't matter. A thousand files called `Song 1.mp3` to `Song 1000.mp3` in one folder work fine, as long as the title, artist and genre tags inside them are right. A perfectly organized folder tree with empty tags doesn't.

## Step 1: prepare the library

Genre tags matter most. Genre is the biggest single input to the mood score, and the title words only nudge it.

Check a few albums from different corners of your library. You want the title, artist, album, genre and year filled in. Year is used by Era match, Throwback and the era step of the Mood dial. If most files look right, skip ahead. You can fix tags later and update the database.

What Rockbox stores for a missing tag: I checked this in `apps/tagcache.c` at tag `v4.0-final` (`check_if_empty()`, around line 2185). When a file has no title, artist, album, genre, composer, comment or album artist tag, the database stores the literal text `<Untagged>` for it. A missing title does not fall back to the filename. A missing track number is stored as -1, and a missing year as 0. RockGenius treats `<Untagged>` as empty, so it never counts as a word or a genre. Untagged files still get picked. They show up in playlists under their file name. They don't count as one artist, so the three-per-artist limit doesn't apply to them.

You can fix tags on the player itself (connect it and open its `Music` folder) or on your computer before copying. Work on a copy if you're nervous. All three tools below only change the files you tell them to.

### MusicBrainz Picard (macOS, Windows, Linux)

Picard looks your files up in the MusicBrainz database and fills in tags, including genre.

1. Install it from [picard.musicbrainz.org](https://picard.musicbrainz.org/). On Linux it's also in most package managers (`sudo apt install picard`, `sudo dnf install picard`) and on Flathub.
2. Open Options > Options > Metadata > Genres. Tick "Use genres from MusicBrainz". Click Make It So.
3. Click Add Folder and pick your music folder.
4. Select the files in the left pane, then click Cluster, then Lookup. Picard matches each cluster to an album and moves it to the right pane.
5. Files that didn't match stay on the left. Select them and click Scan. That fingerprints the audio, which is slower but finds singles and oddly named files.
6. Check the matches on the right. A green icon is a good match; red or orange means take a look.
7. Select everything on the right and click Save.

### Mp3tag (Windows; there's also a paid Mac version)

Mp3tag is the fastest way to fix a lot of files by hand.

1. Install it from [mp3tag.de](https://www.mp3tag.de/en/).
2. File > Change Directory, and pick your music folder. Every file shows up as a row with Title, Artist, Album, Genre and Year columns.
3. Click the Genre column header to sort by it. Blank genres float to the top.
4. Select the files you want to change (Shift-click or Ctrl-click), type into the Genre box on the left, and press Ctrl+S to save.
5. For files whose names have the information but whose tags don't, select them and use Convert > Filename - Tag. A format string like `%artist% - %title%` fills the tags from names like `Artist - Title.mp3`.
6. Tag Sources > MusicBrainz or Discogs can fill whole albums.

### Kid3 (Linux; also runs on Windows and macOS)

1. Install it: `sudo apt install kid3` (or `kid3-qt` outside KDE), `sudo dnf install kid3`, or from Flathub.
2. File > Open Directory, and pick your music folder.
3. Select one or more files. The right side has a Tag 2 section with Title, Artist, Album, Genre and Date fields. Edit them there. With several files selected, a field you change is applied to all of them.
4. To fill tags from file names, set the Format field in the File section (for example `%{artist} - %{title}`). Then click From Filename in the Tag 2 section.
5. File > Save.

When your tags are done, update the database on the player: Settings > General Settings > Database > Update Now.

## Step 2: tidy file names (optional)

This is cosmetic. RockGenius doesn't care about file names. Some stores and rippers produce names like

```
00 Morgan Wallen - 3 Songs at a Time Sampler - 01 - Last Night.mp3
```

and you might prefer `01 Last Night.mp3`. Every method below only touches files that follow that "number Artist - Album - track - Title" pattern. Everything else is left alone. Always preview first.

### With fix_names.py

You need Python for this (see [Step 4](#installing-python-and-ffmpeg) for installing it). Open a terminal in the `tools` folder of the RockGenius download. Name the artist folders to fix, or use `--all` for everything under the music folder.

macOS (Terminal):

```sh
cd ~/Downloads/rockgenius-main/tools
python3 fix_names.py /Volumes/IPOD/Music "Jelly Roll" "Morgan Wallen"
python3 fix_names.py /Volumes/IPOD/Music "Jelly Roll" "Morgan Wallen" --apply
```

Windows (PowerShell):

```powershell
cd $HOME\Downloads\rockgenius-main\tools
py fix_names.py E:\Music "Jelly Roll" "Morgan Wallen"
py fix_names.py E:\Music "Jelly Roll" "Morgan Wallen" --apply
```

Linux:

```sh
cd ~/Downloads/rockgenius-main/tools
python3 fix_names.py /media/$USER/IPOD/Music --all
python3 fix_names.py /media/$USER/IPOD/Music --all --apply
```

The first line of each pair only prints what it would rename. Run the `--apply` line once the list looks right. It also renames the matching macOS `._` sidecar files.

### Without installing anything

Windows (PowerShell). The first block previews; `-WhatIf` makes PowerShell print what it would do without doing it.

```powershell
Get-ChildItem -LiteralPath 'E:\Music' -Recurse -File | Where-Object { $_.Name -notlike '._*' } | ForEach-Object {
  if ($_.Name -match '^\d+ .+ - .+ - (?:\d+-)?(\d+) - (.+)$') {
    Rename-Item -LiteralPath $_.FullName -NewName ('{0:D2} {1}' -f [int]$Matches[1], $Matches[2]) -WhatIf
  }
}
```

When the preview looks right, run the same block again with `-WhatIf` deleted from the end of the `Rename-Item` line.

macOS and Linux (bash). macOS's Terminal starts in zsh, so type `bash` and press Enter first. Then paste this to preview:

```sh
find "/Volumes/IPOD/Music" -type f -name '* - * - * - *' ! -name '._*' | while IFS= read -r f; do
  b=${f##*/}
  if [[ $b =~ ^[0-9]+\ .+\ -\ .+\ -\ ([0-9]+-)?([0-9]+)\ -\ (.+)$ ]]; then
    new=$(printf '%02d %s' "$((10#${BASH_REMATCH[2]}))" "${BASH_REMATCH[3]}")
    echo "$b  ->  $new"
  fi
done
```

To rename for real, paste it again with this line added under the `echo` line:

```sh
    mv -n -- "$f" "${f%/*}/$new"
```

On Linux, replace `/Volumes/IPOD/Music` with `/media/$USER/IPOD/Music` (or `/run/media/$USER/IPOD/Music` on Fedora). `mv -n` never overwrites an existing file. Unlike fix_names.py, the shell version leaves old `._` sidecar files behind. They're macOS junk and safe to delete.

After renaming, update the database on the player: Settings > General Settings > Database > Update Now. The database stores file names, so playlists made before the rename point at files that no longer exist.

## Step 3: install the plugin

1. Download `rockgenius.lua` from the [releases page](https://github.com/vinny954-debug/rockgenius/releases) or the top of the repository.
2. Connect the player. It shows up as a drive (IPOD, or whatever yours is called).
3. Make hidden folders visible, because `.rockbox` starts with a dot:
   - macOS Finder: press Cmd+Shift+. (period). Press it again to hide them later.
   - Windows 11 File Explorer: View > Show > Hidden items. Windows 10: the View tab, tick Hidden items.
   - Linux (GNOME Files, Dolphin and most others): press Ctrl+H.
4. Copy `rockgenius.lua` into `.rockbox/rocks/apps/` on the player. The full path is `/.rockbox/rocks/apps/rockgenius.lua`.
5. Eject the player properly (Finder's eject button, "Safely Remove Hardware" on Windows, the eject icon in your Linux file manager), then unplug it.
6. Build the database if you haven't: Settings > General Settings > Database > Initialize Now. It reads every music file, so a big library takes a while. Turning on Auto Update in the same menu is optional. It keeps the database current when you add music.
7. While you're there, turn on Gather Runtime Data. Rediscover, Deep Cuts, Throwback and Deep cuts like this all use play counts, and Rockbox only records them with this on.

To open RockGenius, go to the main menu and choose Plugins > Applications > rockgenius.lua.

From the Files menu it's in `/.rockbox/rocks/apps/`. The Files browser hides dot-folders until you set Settings > General Settings > File View > Show Files to All.

For one-press access, add it to Shortcuts:

1. Highlight `rockgenius.lua` in Plugins > Applications (or in Files).
2. Open the context menu. On an iPod, hold the Select (center) button.
3. Choose Add to Shortcuts.

It then sits in Shortcuts on the main menu. You can open Shortcuts with music playing, which is the point: Genius uses whatever is playing as its seed.

The first run builds the mood cache, which reads the whole database once. On a big library that takes a while. Every launch after that goes straight to the menu, until the database changes. Step 4 shows how to have your computer build the cache instead.

## Step 4: BPM (optional)

BPM makes the workout lists sharper, and the Running lists need it. Rockbox's database doesn't store BPM. RockGenius gets it two ways:

- On the player: Tools > Scan BPM tags reads the BPM tag from each file's header. It's slow the first time, then only opens new or changed files. This only helps if your files already have BPM tags.
- On a computer: `bpm_tag.py` listens to each song, works out the tempo and writes the tag into the file. Then `bpm_export.py` writes RockGenius's two cache files straight onto the player, so the player never has to scan anything.

The rest of this section is the computer route.

### Installing Python and ffmpeg

`bpm_tag.py` needs Python 3.12, ffmpeg and three Python packages (librosa, mutagen, numpy). `bpm_export.py` only needs Python and mutagen. Use 3.12 rather than the newest Python: librosa depends on numba, which tends to lag new Python releases.

Download the repository first. On the [repository page](https://github.com/vinny954-debug/rockgenius), click Code > Download ZIP and unzip it into Downloads. The commands below assume it ended up at `Downloads/rockgenius-main`.

macOS: install [Homebrew](https://brew.sh) (the site has a one-line installer), then:

```sh
brew install ffmpeg uv
```

`uv` downloads Python 3.12 and the packages the first time you run a script with it. There's nothing else to install.

Windows: open PowerShell and run:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

Close PowerShell and open a new window, so it picks up the new programs. Then:

```powershell
py -3.12 -m pip install librosa mutagen numpy
```

Linux (Debian, Ubuntu, Mint):

```sh
sudo apt install python3 python3-venv ffmpeg
python3 -m venv ~/rockgenius-venv
~/rockgenius-venv/bin/pip install librosa mutagen numpy
```

Linux (Fedora):

```sh
sudo dnf install python3 ffmpeg-free
python3 -m venv ~/rockgenius-venv
~/rockgenius-venv/bin/pip install librosa mutagen numpy
```

If your distribution's Python is newer than 3.12 and pip fails on numba, install `uv` (`pipx install uv`, or see [docs.astral.sh/uv](https://docs.astral.sh/uv/)) and use the macOS commands below. On Fedora, if some files won't decode, install the full ffmpeg from RPM Fusion.

### Tagging BPM

Run it straight on the player's music folder, or on your computer's music folder and copy the files across afterwards. Do a dry run first: it analyses and prints, but writes nothing.

macOS:

```sh
cd ~/Downloads/rockgenius-main/tools
caffeinate -i uv run --python 3.12 --with librosa --with mutagen bpm_tag.py /Volumes/IPOD/Music --dry-run
caffeinate -i uv run --python 3.12 --with librosa --with mutagen bpm_tag.py /Volumes/IPOD/Music --jobs 2
```

`caffeinate -i` keeps the Mac awake until the command finishes.

Windows (PowerShell):

```powershell
cd $HOME\Downloads\rockgenius-main\tools
py -3.12 bpm_tag.py E:\Music --dry-run
py -3.12 bpm_tag.py E:\Music --jobs 2
```

The player's drive letter is in File Explorer under This PC. Windows may still go to sleep during a long run, so set the sleep timer to Never while it runs: Settings > System > Power & battery on Windows 11, or Power & sleep on Windows 10.

Linux:

```sh
cd ~/Downloads/rockgenius-main/tools
~/rockgenius-venv/bin/python bpm_tag.py /media/$USER/IPOD/Music --dry-run
~/rockgenius-venv/bin/python bpm_tag.py /media/$USER/IPOD/Music --jobs 2
```

Each analysed file prints a line like

```
[812/9640] tagged     128 Daft Punk/Discovery/03 Digital Love.mp3
```

What the numbers mean:

- `[812/9640]` is progress.
- `tagged` means a BPM was written; `skip` means the file already had one (use `--force` to redo those); `fail` means the audio was too short or silent to measure.
- `128` is the BPM written into the file.
- `(low confidence)` after a line means the beat was hard to find. Ambient, rubato classical and spoken word often get this. The number may be off.
- Tempos are folded into 70-180 BPM by halving or doubling. A 200 BPM punk song is stored as 100. Change the range with `--range 60-200`. The Running lists match on half and double tempo anyway.

At the end it prints a summary and writes `bpm_report.csv` in the folder you ran it from.

`--jobs 2` analyses two files at a time. More is faster on the computer side, but over USB the player is the slow part. Two is gentler on an old iPod hard drive and on a flaky USB connection.

Other useful options:

- `--only "Artist Name"` (repeatable) does just those artist folders.
- `--start-from "Artist/Album"` skips everything that sorts before that.
- `--timeout 240` gives up on a single file after that many seconds.

To resume after a disconnect: if the player drops off USB, the computer sleeps, or you press Ctrl+C, run the exact same command again. It remembers finished files in `bpm_done.txt` (in the folder you ran it from), so it doesn't touch them again. Before carrying on, it checks any file that was mid-write when the run stopped, and tells you if one got damaged. If one did, copy that file back from your computer's library. Files that crashed or hung the analyser are listed in `bpm_skiplist.txt` and skipped next time. Delete that file to retry them.

### Sending BPM to the player

After tagging, update the player's database so it matches the files: Settings > General Settings > Database > Update Now. Then connect it again and run `bpm_export.py` from the same folder you ran `bpm_tag.py` in, so it finds `bpm_report.csv`:

macOS:

```sh
uv run --python 3.12 --with mutagen bpm_export.py /Volumes/IPOD
```

Windows (PowerShell):

```powershell
py -3.12 bpm_export.py E:\
```

Linux:

```sh
~/rockgenius-venv/bin/python bpm_export.py /media/$USER/IPOD
```

It prints how many tracks got a BPM from the report, how many from reading the tags, and how many have none. It writes two files into the player's `.rockbox` folder:

- `rockgenius_bpm.bin`: one BPM per database entry.
- `rockgenius_mood.bin`: the mood scores, computed with the same rules and word lists as the plugin. It reads them from the `rockgenius.lua` on the player, so your own edits carry over.

Eject the player and open RockGenius. It goes straight to the menu, without "Building mood cache". If the database changed since the export, or the word lists on the player don't match, the plugin notices and rebuilds the cache itself. Nothing breaks either way.

`bpm_export.py` works without `bpm_tag.py`, too. Point it at a player whose files already have BPM tags and it reads them directly, which is much faster than the on-player scan.

## Step 5: using it

The main menu:

- Genius (from now playing): playlists built around the song that's playing. If nothing is playing, it says "Genius (nothing playing)".
- Moods: preset lists for a feeling or a time of day.
- Workouts: lists for exercise, including running at a set cadence.
- Discovery: lists built from your listening history.
- Mood dial: pick energy, mood, edge, era and length yourself.
- Tools: BPM scanning, rebuilding the cache, playlist length.
- Quit.

Every list is saved as `/Playlists/Genius - <name>.m3u8`, so it shows up in Rockbox's Playlists menu later. After it's built you get three choices:

- Play now starts it straight away and closes RockGenius.
- Back keeps it saved and returns to the menu.
- The rest of the menu previews the first eight tracks.

Play now uses Rockbox's own playlist functions. It creates the playlist from the saved file, inserts the tracks one by one if that comes back empty, then starts playback. If none of that works on your build, it writes `/.rockbox/rockgenius_play.txt` and tells you to open the list from Playlists yourself. That file is only for Rockbox builds patched to pick it up on exit. A stock build ignores it.

No list repeats a track, takes more than three songs from one artist, or plays the same artist twice in a row unless there's nothing left to swap in. Intros and skits under 45 seconds are left out.

### Genius

Start a song, open RockGenius, choose Genius. It briefly shows how it reads the song (for example "Seed: fast / bright / mid 124bpm"), then offers:

- More like this: the closest songs on all three mood axes. It leans a little toward the same artist.
- Same vibe, other artists: the same, but never the playing artist.
- Same vibe, same genre: only songs with the same genre tag as the playing song.
- Take it up a notch: the same mood, but more energetic.
- Bring it down: calmer and softer than what's playing.
- Era match: like More like this, but strongly prefers songs released within five years of it.
- Deep cuts like this: the same vibe, only songs you've never played.
- Playlist length: 25, 50 or 100 songs. Select it to cycle through the options. The choice is remembered.

Same genre needs a genre tag on the playing song, and Era match needs a year. If either tag is missing, RockGenius tells you so.

### Moods

- Golden Hour: warm, mid-tempo, feel-good.
- Sunday Morning: slow, warm, nothing loud.
- Sunday Drive: easy, bright, unhurried.
- Road Trip: upbeat and bright, 2.5 to 6.5 minutes.
- Late Night Drive: steady, a bit dark, smooth, 3 minutes or longer.
- Focus: low energy, no edge, prefers long tracks.
- Rainy Window: quiet and melancholy, 3 minutes 20 or longer.
- Blue Hour: sad songs, low light.
- Breakup: sad, with some bite.
- Wind Down: slow and soft, for the end of the day.

### Workouts

- Cardio Push: fast and bright, 2:20 to 5:30 long, 135-185 BPM when BPM is known.
- Running 160 / 170 / 180 spm: songs whose BPM matches your steps per minute, or half or double it, within 6. Only songs with a known BPM qualify, so this needs Step 4 or a BPM scan.
- Heavy Lifting: hard, dark, driving.
- Pregame: loud, confident, under 5.5 minutes.
- Fury Room: maximum aggression, under 4 minutes.
- Short & Sharp: under 3 minutes, high energy.
- Cool-Down Walk: mid energy, warm, no edge.

### Discovery

- Rediscover: songs you played at least three times but haven't played in a long while.
- Deep Cuts: songs you've never played, by artists you play a lot.
- Throwback: songs at least 15 years old, the ones you've played and rated most first. It uses the player's clock for "now". If the clock was never set, it uses the newest year in your library.
- Long Haul: a shuffle of songs over 6 minutes.

The first three only work with Gather Runtime Data turned on (Step 3), and after you've been listening for a while.

### Mood dial

Five quick menus, one after another:

1. Energy: Comatose, Very calm, Calm, Medium, Lively, Pumped, Full tilt.
2. Mood: Bleak, Dark, Moody, Neutral, Warm, Bright, Euphoric.
3. Edge: Feather-light, Gentle, Soft, Normal, Gritty, Hard, Brutal.
4. Era: Any, 60s-70s, 80s, 90s, 2000s, 2010s, 2020s. Anything but Any skips songs without a year tag.
5. Length: 25, 50 or 100 songs.

Press Back at any step to cancel. The list is named after what you picked, leaving out the middle settings. For example "Dial - Calm, Warm, Soft, 90s", or "Dial - Middle of the road" if every setting is in the middle.

### Tools

- Scan BPM tags / Update BPM tags: reads BPM tags on the player (see Step 4). After the first scan it only opens new or changed files. You can cancel it, and the next scan carries on where it stopped.
- Rebuild mood cache: scores everything again. You rarely need this. The cache rebuilds by itself when the database or the word lists change.
- Playlist length: the same setting as in the Genius menu.
- About: version, track count, and whether a BPM file exists.

## Troubleshooting

**"No database" or "Database is empty".** Run Settings > General Settings > Database > Initialize Now and wait for it to finish. If it still comes up empty, check two things:

- Connect the player and open `/.rockbox/config.cfg` in a text editor. Look for a line starting `database scan paths:`. Rockbox only scans the folders listed there, separated by `:`. The default is `/`, the whole drive. If the line is blank or names a folder that doesn't exist, fix it or delete the line. Then run Initialize Now again.
- Search the drive for a file called `database.ignore`. Rockbox skips any folder containing one, including everything below it. Delete it, unless you put it there on purpose. A `database.unignore` file inside an ignored folder brings that folder back.

**The menu says "Genius (nothing playing)".** Start a song first, then open RockGenius from Shortcuts. If a song is playing and Genius says "Now playing track isn't in the database", the database is older than the file. Run Update Now.

**Music stops or skips when RockGenius opens.** It shouldn't: RockGenius keeps itself inside the plugin's own memory, so it never takes the audio buffer. If it happens, connect the player and look at the last line of `/.rockbox/rockgenius.log`. If it ends with `(audio buffer was borrowed!)`, RockGenius used more memory than it should. Open an issue with the whole log attached, plus your player model and Rockbox version.

**Running says "No tracks with a matching BPM".** Running only uses songs with a known BPM. Do Step 4, or run Tools > Scan BPM tags if your files already have BPM tags.

**The player disconnects during a long USB job.** Old iPod hard drives and worn cables don't like hours of sustained reads.

- Use `--jobs 2`, or `--jobs 1` if it keeps happening.
- On a Mac, run the command under `caffeinate -i` (shown in Step 4) so the Mac doesn't sleep.
- Try another cable and another USB port, ideally one on the computer itself rather than a hub.
- Then run the same command again. It resumes where it stopped.

**A playlist plays the wrong songs or "file not found".** The database is out of date: files were renamed, moved or deleted since the last update. Run Update Now and make the playlist again.

**Where the log lives.** Every run writes `/.rockbox/rockgenius.log`, replacing the previous one. Each line shows the time, the Lua memory in use, and what it was doing. It's the first thing to attach to a bug report.

## Limitations

- There's no audio analysis on the player. The mood score comes from the genre tag, a list of keywords matched against titles, album names and comments, the track length, and the BPM when known. It's a good guess, not a measurement.
- Songs with no genre tag fall back to the word list and length. They also rank lower in mood-based lists, because their score is less certain.
- The word list is English. Titles in other languages barely register unless you add words to the WORDS table (see [Customizing](#customizing)).
- A song called "Sunshine" by a doom band lands somewhere in the middle: the genre says dark and heavy, the title says bright. Genius is the most reliable mode, because it starts from a real song rather than from the word list.
- BPM detection can be off by half or double on some tracks, especially ones with half-time drums or a lot of swing.
- The mood cache is rebuilt after every database change (Update Now, or Auto Update finding new files). On a big library that takes a while on the player, and much less time with `bpm_export.py` on a computer.
- Play counts only exist if Gather Runtime Data is on.
- I tested it on my own iPod Video 5.5G running Rockbox 4.0. Other Rockbox players with the Lua plugin should work, but I haven't tried them. If you get it running on something else, open an issue and say so.

## Customizing

Everything is plain Lua near the top of `rockgenius.lua`. Edit it in any text editor and copy it back to the player. The mood cache notices the change and rebuilds itself on the next launch. Each axis runs from -100 to 100: energy is calm to wild, mood (valence) is dark to bright, edge (aggression) is soft to hard. Keep everything as whole numbers. Rockbox's Lua has no decimals.

### Genres

GENRE matches keywords anywhere in the lowercased genre tag. A genre that matches several keywords gets their average.

```lua
local GENRE = {
  {"ambient",     -80,   0, -90},
  -- add yours: keyword, energy, mood, edge
  {"hyperpop",     85,  40,  20},
  {"chamber",     -30,  10, -60},
```

### Words

WORDS matches whole words in titles, album names and comments, case-insensitively. Each word nudges energy, mood and edge, and the total from words is capped at ±50 per axis. Plain words go in as is. Words with accents or other non-ASCII letters need quotes and square brackets, written in lowercase:

```lua
local WORDS = {
  -- energy up
  run=  {25,10,5},  running={25,10,5}, fire={20,0,15},
  -- add yours
  fuego={20,0,15}, noche={-10,-10,-5}, ["corazón"]={0,25,-10},
  ["schön"]={0,25,-10},
```

Lowercasing only covers A to Z. A title in capitals like "CORAZÓN" doesn't match `corazón`, because the Ó stays uppercase.

### Recipes

Each recipe is a target point on the three axes plus weights (how much each axis matters) and optional filters. `group` decides which menu it appears in: `moods`, `workouts` or `discovery`.

```lua
local RECIPES = {
  { group="moods", name="Dishes",      desc="upbeat, cheerful, nothing too long",
    e=40, v=60, a=-20, we=1, wv=3, wa=1, len_hi=300 },
  { group="workouts", name="Running 165 spm", desc="my pace",
    e=60, v=30, a=0, we=1, wv=1, wa=0, cadence=165, len_lo=120 },
```

The filters are optional; leave out any you don't need:

- `len_lo` / `len_hi`: track length in seconds.
- `bpm_lo` / `bpm_hi`: a BPM range, applied only to songs with a known BPM.
- `cadence`: known BPM only, matched to that number or half or double it.
- `long_bonus=true`: prefer long tracks.
- `special`: one of `"rediscover"`, `"deepcuts"`, `"throwback"` or `"shuffle"`.

Other settings at the top of the file: `MAX_PER_ARTIST` (default 3), and `LENGTHS`, the playlist lengths offered (25, 50, 100).

## How it works

You don't need this section to use RockGenius.

RockGenius reads Rockbox's database files directly: `/.rockbox/database_idx.tcd` holds one record per track, and `database_0.tcd` to `database_12.tcd` hold the strings. The first run scores every track and saves the scores in `/.rockbox/rockgenius_mood.bin`, 5 bytes per track. After that, every list is built by streaming the index from disk and keeping only the best few hundred candidates. Titles and paths are read only for the songs that end up in the list.

This matters because a Lua plugin has 512 KB of memory to work in. Holding a whole library in memory makes Lua borrow the audio buffer, and that stops the music. Measured with the test harness on a synthetic 10,000-track library, the peak live Lua memory stays around 260-330 KB whichever menu you use, and it's the same at 1,000 tracks except for Deep Cuts.

Files RockGenius writes on the player:

| File | What it is |
|---|---|
| `/.rockbox/rockgenius_mood.bin` | mood cache (format RGM3), rebuilt when the database or word lists change |
| `/.rockbox/rockgenius_bpm.bin` | BPM per track (format RGB2), from the on-player scan or `bpm_export.py` |
| `/.rockbox/rockgenius.cfg` | the playlist length you picked |
| `/.rockbox/rockgenius_np.txt` | where Rockbox keeps the now-playing path, cached to start faster |
| `/.rockbox/rockgenius.log` | log of the last run |
| `/.rockbox/rockgenius_play.txt` | only written if Play now couldn't start playback |
| `/Playlists/Genius - *.m3u8` | the playlists |

Deleting any of the `.bin` files is safe. RockGenius rebuilds what it needs.
