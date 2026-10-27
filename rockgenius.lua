--[[ RockGenius -- mood-aware playlist generator for Rockbox (pure Lua, on-device)

  Scores every track in the Rockbox database on three mood axes
  (energy, valence, aggression) from metadata only, then builds playlists.

    * Mood scores are computed once and cached in /.rockbox/rockgenius_mood.bin
      (5 bytes per track). The cache is rebuilt only when the database changes
      (new commit), after a BPM scan, or when GENRE / WORDS below are edited.
      tools/bpm_export.py can write the same file on a computer instead.
    * The library is never held in RAM: every recipe streams database_idx.tcd
      from disk and keeps only the best few hundred candidates. Strings (paths,
      titles, artists) are read on demand for the tracks actually picked.
    * This keeps Lua inside the 512 KB plugin buffer, so it never borrows
      the audio buffer (which stops playback).
    * Writes a timestamped log to /.rockbox/rockgenius.log every run.

  Rockbox Lua is integer-only (LUA_NUMBER is long, and % and / are C's
  truncating operators). No floats anywhere; use idiv() / umod().

  Install: copy to /.rockbox/rocks/apps/rockgenius.lua
  Licence: GPL-2.0-or-later. https://github.com/vinny954-debug/rockgenius
]]

local VERSION  = "3.0.0"

local DB_DIR   = rb.ROCKBOX_DIR or "/.rockbox"
local MOOD_F   = DB_DIR .. "/rockgenius_mood.bin"
local BPM_F    = DB_DIR .. "/rockgenius_bpm.bin"
local NP_F     = DB_DIR .. "/rockgenius_np.txt"
local PLAY_F   = DB_DIR .. "/rockgenius_play.txt"
local LOG_F    = DB_DIR .. "/rockgenius.log"
local CFG_F    = DB_DIR .. "/rockgenius.cfg"
local PL_DIR   = "/Playlists"

local LENGTHS        = { 25, 50, 100 }   -- playlist length choices
local MAX_TRACKS     = 50      -- tracks per generated playlist (saved in CFG_F)
local MAX_PER_ARTIST = 3
local KEEP           = 150     -- candidates kept while streaming (set from MAX_TRACKS)
local UNTAGGED       = "<Untagged>"   -- what tagcache.c stores for a missing tag
local SCORE_VERSION  = 3       -- bump when the scoring rules change
local TAG_COUNT      = 23      -- enum tag_type ... TAG_COUNT (auto-detected)
local IDX_SIZE       = TAG_COUNT * 4 + 4
local HDR_SIZE       = 12
local MASTER_HDR     = 24
local TC_MAGIC       = 0x54434810
local MOOD_MAGIC     = "RGM3"
local BPM_MAGIC      = "RGB2"
local HZ             = rb.HZ or 100

local T = { artist=0, album=1, genre=2, title=3, filename=4, composer=5,
            comment=6, albumartist=7, grouping=8, year=9, disc=10, track=11,
            canon=12, bitrate=13, length=14, playcount=15, rating=16,
            playtime=17, lastplayed=18, commitid=19, mtime=20 }

math.randomseed(rb.current_tick())

------------------------------------------------------------------------
-- compatibility (Rockbox 4.0 Lua)
------------------------------------------------------------------------
pcall(require, "actions")              -- defines rb.actions on 4.0

-- Integer division that truncates toward zero on every Lua: on the device %
-- and / are C's (truncating), under a desktop Lua they are floored. Working on
-- magnitudes makes both agree, and tools/bpm_export.py mirrors it exactly.
local function idiv(a, b)
  local neg = (a < 0) ~= (b < 0)
  if a < 0 then a = -a end
  if b < 0 then b = -b end
  local q = (a - a % b) / b
  if neg then return -q end
  return q
end
-- modulo that is never negative (b > 0), same on device and desktop
local function umod(a, b)
  local r = a % b
  if r < 0 then r = r + b end
  return r
end

if not rb.splash_progress then
  local last = -1
  rb.splash_progress = function(cur, tot, msg)
    local pct = 0
    if tot > 0 then pct = idiv(cur * 100, tot) end
    if pct ~= last then last = pct; rb.splash(0, msg .. " " .. pct .. "%") end
  end
end

local function cancel_pressed()
  local act = rb.actions
  if not act then return false end
  local a = rb.get_plugin_action(0)
  return a == act.PLA_CANCEL or a == act.PLA_EXIT
end

------------------------------------------------------------------------
-- log: append + close per line so it survives a hang / hard reset
------------------------------------------------------------------------
local T0 = rb.current_tick()
do local f = io.open(LOG_F, "w"); if f then f:close() end end
local function log(fmt, ...)
  local ok, s = pcall(string.format, fmt, ...)
  if not ok then s = fmt end
  local f = io.open(LOG_F, "a")
  if not f then return end
  f:write(string.format("%6d.%02ds %5dK  ", idiv(rb.current_tick() - T0, HZ),
          (rb.current_tick() - T0) % HZ, collectgarbage("count")), s, "\n")
  f:close()
end

------------------------------------------------------------------------
-- LEXICONS  (all values are fixed-point: 100 == 1.0)
-- Each entry: { energy, valence, aggression }
------------------------------------------------------------------------
-- Genre keyword -> base vector. Matched as substring of lowercase genre.
local GENRE = {
  -- ambient / quiet
  {"ambient",     -80,   0, -90}, {"drone",     -90, -20, -80},
  {"new age",     -70,  30, -90}, {"chillout",  -60,  20, -80},
  {"downtempo",   -50,   0, -60}, {"trip",      -40, -10, -40},
  {"lo-fi",       -50,  10, -70}, {"lofi",      -50,  10, -70},
  {"acoustic",    -40,  20, -70}, {"singer",    -30,  10, -60},
  {"folk",        -30,  20, -60}, {"americana", -20,  20, -50},
  {"country",      10,  30, -40}, {"bluegrass",  40,  40, -30},
  {"blues",       -20, -30, -30}, {"soul",       -10,  30, -50},
  {"r&b",         -10,  20, -50}, {"rnb",        -10,  20, -50},
  {"gospel",       10,  60, -50}, {"jazz",       -20,  20, -60},
  {"bossa",       -40,  40, -80}, {"lounge",     -50,  30, -80},
  {"classical",   -40,  10, -70}, {"orchestral", -20,   0, -40},
  {"soundtrack",  -20,   0, -30}, {"score",      -30,   0, -30},
  {"piano",       -50,   0, -80}, {"instrumental",-20,  10, -50},
  -- pop / dance
  {"pop",          30,  50, -40}, {"synth",      30,  30, -30},
  {"indie",        10,  10, -40}, {"alternative",10,  -10, -10},
  {"disco",        60,  70, -40}, {"funk",       50,  60, -30},
  {"dance",        70,  60, -20}, {"house",      60,  50, -20},
  {"techno",       80,   0,  20}, {"trance",     70,  30, -10},
  {"electro",      60,  20,   0}, {"edm",        80,  50,  10},
  {"drum",         90,   0,  30}, {"dnb",        90,   0,  30},
  {"dubstep",      70, -10,  50}, {"breakbeat",  70,  10,  10},
  {"electronic",   40,  10, -20}, {"reggae",    -10,  40, -50},
  {"ska",          60,  60, -10}, {"latin",      50,  60, -30},
  {"afro",         50,  50, -30}, {"k-pop",      50,  60, -30},
  -- hip hop
  {"hip",          40,  10,  20}, {"rap",        40,   0,  30},
  {"trap",         50, -20,  50}, {"drill",      50, -40,  70},
  {"grime",        60, -20,  60}, {"boom bap",   30,  10,  20},
  -- rock
  {"rock",         50,  10,  20}, {"classic rock",40, 20,  10},
  {"hard rock",    70,   0,  60}, {"punk",       90,   0,  70},
  {"hardcore",     90, -30,  90}, {"post-punk",  40, -30,  20},
  {"grunge",       50, -40,  50}, {"garage",     60,  10,  40},
  {"psych",        20,   0,   0}, {"prog",       20,   0,   0},
  {"shoegaze",    -10, -20, -20}, {"emo",        40, -50,  30},
  {"metal",        80, -30,  90}, {"thrash",     95, -30, 100},
  {"death",        95, -60, 100}, {"black metal",90, -70, 100},
  {"doom",         10, -70,  70}, {"sludge",     20, -60,  80},
  {"industrial",   60, -50,  70}, {"nu metal",   70, -30,  80},
  {"metalcore",    85, -40,  90}, {"stoner",     30, -10,  50},
  -- misc
  {"christmas",    10,  70, -60}, {"holiday",    10,  70, -60},
  {"comedy",       20,  60, -50}, {"spoken",    -60,   0, -90},
  {"podcast",     -60,   0, -90}, {"audiobook", -70,   0, -90},
  {"meditation",  -95,  30, -100},{"sleep",     -95,  10, -100},
  {"world",         0,  20, -40}, {"experimental",0, -20,  10},
  {"noise",        50, -50,  80}, {"vaporwave", -40,   0, -60},
}

-- Word lexicon for titles / album names / comments.
-- Each: energy, valence, aggression nudges (applied once per word hit, capped).
local WORDS = {
  -- energy up
  run=  {25,10,5},  running={25,10,5}, fire={20,0,15}, burn={15,-5,20},
  go=   {15,10,0},  fast={25,5,10},    speed={25,0,10}, jump={25,20,0},
  dance={25,25,-10},party={25,30,-10}, wild={20,5,15},  loud={20,0,20},
  fight={20,-10,30},war={15,-30,35},   kill={15,-40,40},blood={10,-40,35},
  hate= {10,-40,35},rage={25,-40,40},  riot={25,-20,35},power={15,0,20},
  thunder={15,-10,20},storm={15,-20,15},lightning={20,0,15},rock={15,10,10},
  attack={20,-20,35},beast={15,-10,30},machine={10,-10,20},animal={15,0,20},
  move= {20,15,0},  shake={20,20,0},   bounce={20,20,0}, pump={25,10,15},
  hype= {25,20,10}, crazy={20,10,10},  mad={15,-10,20},  wake={15,10,0},
  -- energy down
  sleep={-35,0,-20},dream={-25,10,-20},dreams={-25,10,-20},slow={-30,0,-15},
  quiet={-30,0,-25},still={-25,0,-20}, soft={-25,10,-25}, gentle={-25,15,-25},
  lullaby={-40,20,-30},rest={-25,10,-20},calm={-30,15,-25},peace={-25,20,-25},
  float={-20,10,-20},drift={-20,0,-15},breathe={-20,10,-20},silence={-30,-10,-25},
  night={-10,-10,-5},midnight={-10,-15,-5},moon={-15,0,-15},stars={-15,10,-15},
  whisper={-30,0,-25},lonely={-15,-35,-15},alone={-15,-30,-10},
  -- valence down
  sad=  {-10,-40,-5},cry={-10,-35,-5},tears={-10,-35,-5},goodbye={-5,-30,-5},
  gone= {-5,-25,0}, lost={-5,-30,0},  blue={-10,-25,-10}, rain={-15,-20,-10},
  grey= {-10,-20,-5},gray={-10,-20,-5},cold={-10,-20,0},  dead={0,-35,15},
  death={0,-35,15}, die={0,-35,15},   grave={-5,-35,10},  ghost={-10,-25,0},
  pain= {0,-35,15}, hurt={-5,-30,5},  broken={-5,-35,5},  empty={-15,-30,-5},
  dark= {-5,-25,10},black={0,-20,10}, hell={10,-30,30},   devil={5,-25,25},
  fear= {0,-30,15}, nightmare={5,-35,20},sorrow={-15,-40,-5},miss={-10,-25,-5},
  -- valence up
  love= {0,25,-15}, happy={10,40,-15},sun={5,30,-15},   sunshine={5,35,-15},
  summer={10,30,-10},light={0,20,-10},smile={5,35,-15}, sweet={-5,30,-20},
  baby= {0,20,-10}, heaven={-5,30,-15},beautiful={-5,30,-15},good={5,25,-10},
  home= {-5,20,-10},friend={0,25,-10},free={10,25,-5},  fly={10,25,-5},
  shine={5,30,-10}, gold={0,20,-10},  golden={0,20,-10},glory={10,25,0},
  celebrate={20,35,-10},joy={10,40,-15},dancing={20,30,-10},alive={10,30,0},
  -- aggression
  metal={15,-10,30},steel={10,-10,25},iron={10,-10,25}, bone={5,-20,20},
  skull={5,-20,25}, scream={20,-20,35},chaos={15,-25,30},destroy={15,-30,35},
}

-- Playlist recipes. Each defines a target point + weights on the axes, and
-- optional hard filters. Axes: e (energy) v (valence) a (aggression),
-- targets are -100..100, we/wv/wa are weights. Optional fields:
--   len_lo / len_hi    track length in seconds
--   bpm_lo / bpm_hi    BPM range, applied only to tracks whose BPM is known
--   cadence            running cadence (steps/min): known BPM only, matched on
--                      BPM, half-time or double-time
--   long_bonus         prefer longer tracks
--   special            "rediscover", "deepcuts", "throwback" or "shuffle"
--   group              which menu it shows up in
local RECIPES = {
  -- moods
  { group="moods", name="Golden Hour",      desc="warm, mid-tempo, feel-good",
    e=35, v=70, a=-30, we=1, wv=3, wa=1 },
  { group="moods", name="Sunday Morning",   desc="slow, warm, nothing loud",
    e=-35, v=45, a=-70, we=2, wv=2, wa=2 },
  { group="moods", name="Sunday Drive",     desc="easy, bright, unhurried",
    e=20, v=55, a=-40, we=1, wv=3, wa=2 },
  { group="moods", name="Road Trip",        desc="upbeat, bright, singalong length",
    e=45, v=45, a=-5, we=2, wv=2, wa=1, len_lo=150, len_hi=390 },
  { group="moods", name="Late Night Drive", desc="steady, a bit dark, smooth",
    e=10, v=-20, a=-35, we=2, wv=2, wa=2, len_lo=180 },
  { group="moods", name="Focus",            desc="low energy, no edge, long tracks",
    e=-25, v=10, a=-70, we=2, wv=1, wa=3, len_lo=150, long_bonus=true },
  { group="moods", name="Rainy Window",     desc="quiet and melancholy, long tracks welcome",
    e=-55, v=-30, a=-70, we=2, wv=2, wa=2, len_lo=200 },
  { group="moods", name="Blue Hour",        desc="sad songs, low light",
    e=-40, v=-60, a=-50, we=1, wv=3, wa=2 },
  { group="moods", name="Breakup",          desc="sad, with some bite",
    e=-5, v=-75, a=15, we=1, wv=3, wa=1 },
  { group="moods", name="Wind Down",        desc="slow, soft, for the end of the day",
    e=-70, v=10, a=-80, we=3, wv=1, wa=2 },
  -- workouts
  { group="workouts", name="Cardio Push",   desc="fast, bright, 2.5-5 min, BPM 135-185 if known",
    e=80, v=30, a=20, we=3, wv=1, wa=1,
    bpm_lo=135, bpm_hi=185, len_lo=140, len_hi=330 },
  { group="workouts", name="Running 160 spm", desc="known BPM only, matched to 160 steps/min",
    e=60, v=30, a=0, we=1, wv=1, wa=0, cadence=160, len_lo=120 },
  { group="workouts", name="Running 170 spm", desc="known BPM only, matched to 170 steps/min",
    e=60, v=30, a=0, we=1, wv=1, wa=0, cadence=170, len_lo=120 },
  { group="workouts", name="Running 180 spm", desc="known BPM only, matched to 180 steps/min",
    e=60, v=30, a=0, we=1, wv=1, wa=0, cadence=180, len_lo=120 },
  { group="workouts", name="Heavy Lifting", desc="hard, dark, driving",
    e=70, v=-30, a=85, we=2, wv=1, wa=3, len_lo=120, len_hi=420 },
  { group="workouts", name="Pregame",       desc="loud, confident, short",
    e=85, v=45, a=45, we=3, wv=1, wa=1, len_hi=330 },
  { group="workouts", name="Fury Room",     desc="maximum aggression, short and sharp",
    e=85, v=-50, a=100, we=1, wv=1, wa=3, len_hi=240 },
  { group="workouts", name="Short & Sharp", desc="under 3 minutes, high energy",
    e=70, v=10, a=30, we=3, wv=1, wa=1, len_hi=180 },
  { group="workouts", name="Cool-Down Walk", desc="mid energy, warm, no edge",
    e=10, v=35, a=-50, we=2, wv=2, wa=2 },
  -- discovery
  { group="discovery", name="Rediscover",   desc="old favourites you haven't played in ages",
    e=0, v=0, a=0, we=0, wv=0, wa=0, special="rediscover" },
  { group="discovery", name="Deep Cuts",    desc="never played, by artists you play a lot",
    e=0, v=0, a=0, we=0, wv=0, wa=0, special="deepcuts" },
  { group="discovery", name="Throwback",    desc="15+ years old, the ones you play most",
    e=0, v=0, a=0, we=0, wv=0, wa=0, special="throwback" },
  { group="discovery", name="Long Haul",    desc="epics over 6 minutes",
    e=0, v=0, a=0, we=0, wv=0, wa=0, len_lo=360, special="shuffle" },
}

-- Genius-from-now-playing variations (see genius_recipe()).
local GENIUS_MODES = { "More like this", "Same vibe, other artists", "Same vibe, same genre",
                       "Take it up a notch", "Bring it down", "Era match", "Deep cuts like this" }

-- Mood dial steps: index 4 is the middle; values are (k - 4) * 30.
local DIAL_E = { "Comatose", "Very calm", "Calm", "Medium", "Lively", "Pumped", "Full tilt" }
local DIAL_V = { "Bleak", "Dark", "Moody", "Neutral", "Warm", "Bright", "Euphoric" }
local DIAL_A = { "Feather-light", "Gentle", "Soft", "Normal", "Gritty", "Hard", "Brutal" }
local ERAS   = { { "Any" }, { "60s-70s", 1960, 1979 }, { "80s", 1980, 1989 },
                 { "90s", 1990, 1999 }, { "2000s", 2000, 2009 }, { "2010s", 2010, 2019 },
                 { "2020s", 2020, 2029 } }

------------------------------------------------------------------------
-- byte helpers (Rockbox Lua is INTEGER ONLY: no floats anywhere)
------------------------------------------------------------------------
local function clamp(x, lo, hi) if x < lo then return lo elseif x > hi then return hi end return x end
local function abs(x) if x < 0 then return -x end return x end

local function le32(s, i)
  local b1,b2,b3,b4 = s:byte(i, i+3)
  local n = b1 + b2*256 + b3*65536 + b4*16777216
  if n >= 2147483648 then n = n - 4294967296 end
  return n
end
local function be32(s, i)
  local b1,b2,b3,b4 = s:byte(i, i+3)
  local n = b4 + b3*256 + b2*65536 + b1*16777216
  if n >= 2147483648 then n = n - 4294967296 end
  return n
end
local rd32 = le32
local function u32s(n)
  return string.char(n % 256, idiv(n, 256) % 256, idiv(n, 65536) % 256, idiv(n, 16777216) % 256)
end
local function fld(c, base, t) return rd32(c, base + t * 4) end

-- cheap path fingerprint: length + last 24 bytes (24-bit result, never 0)
local function phash(p)
  local n = #p
  local h = n
  local s = 1
  if n > 24 then s = n - 23 end
  for i = s, n do h = (h * 31 + p:byte(i)) % 16777213 end
  if h == 0 then h = 1 end
  return h
end

-- the key stored next to each BPM in rockgenius_bpm.bin (path + mtime).
-- tools/bpm_export.py computes the same value.
local function path_key(p, mtime)
  local h = (phash(p) + umod(mtime, 16777213)) % 16777213
  if h == 0 then h = 1 end
  return h
end

-- a database string, with tagcache's "<Untagged>" placeholder turned into ""
local function tagtext(s)
  if s == nil or s == UNTAGGED then return "" end
  return s
end

------------------------------------------------------------------------
-- TAGCACHE ACCESS (streaming, nothing held in RAM)
------------------------------------------------------------------------
local function open_master()
  local f = io.open(DB_DIR .. "/database_idx.tcd", "rb")
  if not f then return nil, "No database. Initialize it first\n(Settings > General > Database)" end
  local hdr = f:read(MASTER_HDR)
  if not hdr or #hdr < MASTER_HDR then f:close() return nil, "Bad database header" end
  if le32(hdr, 1) ~= TC_MAGIC then
    if be32(hdr, 1) == TC_MAGIC then rd32 = be32
    else f:close() return nil, string.format("Unknown DB version %08x", le32(hdr, 1)) end
  end
  local m = { f = f, datasize = rd32(hdr, 5), entries = rd32(hdr, 9),
              serial = rd32(hdr, 13), commit = rd32(hdr, 17) }
  if m.entries > 0 and m.datasize > 0 then
    local isz = idiv(m.datasize, m.entries)
    if isz >= 80 and isz <= 160 and isz % 4 == 0 and isz ~= IDX_SIZE then
      local old = TAG_COUNT
      IDX_SIZE = isz
      TAG_COUNT = idiv(isz - 4, 4)
      if TAG_COUNT == old - 1 then          -- older db: no canonical artist
        for k, v in pairs(T) do if v >= 12 then T[k] = v - 1 end end
      end
    end
  end
  return m
end

-- Stream every master record: cb(r, chunk, base, deleted).
-- cb may return false to stop. opts.all = include deleted, opts.nocancel.
local function each_record(m, cb, label, opts)
  opts = opts or {}
  local f, n, r = m.f, m.entries, 0
  local CH = 64
  local tick = 0
  f:seek("set", MASTER_HDR)
  while r < n do
    local want = n - r
    if want > CH then want = CH end
    local chunk = f:read(want * IDX_SIZE)
    if not chunk or #chunk < IDX_SIZE then break end
    local nrec = idiv(#chunk, IDX_SIZE)
    local fo = TAG_COUNT * 4
    for k = 0, nrec - 1 do
      local base = k * IDX_SIZE + 1
      local del = umod(rd32(chunk, base + fo), 2) == 1
      if opts.all or not del then
        if cb(r + k, chunk, base, del) == false then return false end
      end
    end
    r = r + nrec
    tick = tick + 1
    if label and tick % 8 == 0 then rb.splash_progress(r, n, label) end
    rb.yield()
    if not opts.nocancel and cancel_pressed() then return false end
  end
  return true
end

local function read_record(m, r)
  m.f:seek("set", MASTER_HDR + r * IDX_SIZE)
  return m.f:read(IDX_SIZE)
end

local tagfh = {}
local function tag_at(tag, off)
  if not off or off < HDR_SIZE then return nil end
  local f = tagfh[tag]
  if not f then
    f = io.open(string.format("%s/database_%d.tcd", DB_DIR, tag), "rb")
    if not f then return nil end
    tagfh[tag] = f
  end
  f:seek("set", off)
  local h = f:read(8)
  if not h or #h < 8 then return nil end
  local len = rd32(h, 1)
  if len <= 0 or len > 2048 then return nil end
  local d = f:read(len) or ""
  return (d:match("^(%Z*)"))
end
local function close_tags()
  for k, f in pairs(tagfh) do f:close(); tagfh[k] = nil end
end

-- stream a unique-tag file: fn(offset, string)
local function stream_tagfile(tag, fn)
  local f = io.open(string.format("%s/database_%d.tcd", DB_DIR, tag), "rb")
  if not f then return end
  local hdr = f:read(HDR_SIZE)
  if not hdr or #hdr < HDR_SIZE then f:close() return end
  local n, pos = rd32(hdr, 9), HDR_SIZE
  for i = 1, n do
    local e = f:read(8)
    if not e or #e < 8 then break end
    local len = rd32(e, 1)
    if len < 0 or len > 4096 then break end
    local d = f:read(len) or ""
    if fn(pos, (d:match("^(%Z*)"))) == false then break end
    pos = pos + 8 + len
    if i % 200 == 0 then rb.yield() end
  end
  f:close()
end

-- find master record index for a path (fast plain search of database_4.tcd)
local function find_record_by_path(path, entries)
  local f = io.open(string.format("%s/database_%d.tcd", DB_DIR, T.filename), "rb")
  if not f then return nil end
  f:read(HDR_SIZE)
  local needle = path .. "\0"
  local nl = #needle
  local keep = nl + 8
  local buf = ""
  while true do
    local d = f:read(8192)
    if not d then break end
    buf = buf .. d
    local i = buf:find(needle, 1, true)
    while i do
      if i > 8 then
        local len, idx = rd32(buf, i - 8), rd32(buf, i - 4)
        if len >= nl and len <= nl + 8 and idx >= 0 and idx < entries then
          f:close()
          return idx
        end
      end
      i = buf:find(needle, i + 1, true)
    end
    if #buf > keep then buf = buf:sub(-keep) end
    rb.yield()
  end
  f:close()
  return nil
end

------------------------------------------------------------------------
-- BPM tag reader (reads only the tag header of one file)
------------------------------------------------------------------------
local bpm_from_file
do
local function syncsafe(s, i)
  local a,b,c,d = s:byte(i, i+3)
  return ((a*128 + b)*128 + c)*128 + d
end
-- All readers walk the container with seeks and only read small pieces, so a
-- file with large embedded cover art never lands in Lua's heap.
local function num_from(txt)
  if not txt then return nil end
  return tonumber(txt:match("(%d+)"))
end

local function id3_bpm(f, head, base)
  base = base or 0
  local ver, flags = head:byte(4), head:byte(6)
  local size = syncsafe(head, 7)
  local pos, stop = base + 10, base + 10 + size
  if ver == 3 and flags % 128 >= 64 then             -- v2.3 extended header
    local x = f:read(4); if not x or #x < 4 then return nil end
    pos = pos + 4 + be32(x, 1)
  elseif ver == 4 and flags % 128 >= 64 then         -- v2.4 extended header
    local x = f:read(4); if not x or #x < 4 then return nil end
    pos = pos + syncsafe(x, 1)
  end
  local hlen, idlen = 10, 4
  local want = "TBPM"
  if ver == 2 then hlen, idlen, want = 6, 3, "TBP" end
  local n = 0
  while pos + hlen <= stop and n < 400 do
    n = n + 1
    f:seek("set", pos)
    local h = f:read(hlen)
    if not h or #h < hlen or h:byte(1) == 0 then break end
    local id = h:sub(1, idlen)
    local fsz
    if ver == 2 then
      local a, b, c = h:byte(4, 6); fsz = (a * 256 + b) * 256 + c
    elseif ver >= 4 then fsz = syncsafe(h, 5)
    else fsz = be32(h, 5) end
    if fsz <= 0 or pos + hlen + fsz > stop then break end
    if id == want then
      return num_from((f:read(fsz > 32 and 32 or fsz) or ""):sub(2):gsub("%z", ""))
    end
    pos = pos + hlen + fsz
  end
  return nil
end

local function flac_bpm(f)
  local pos = 4
  for _ = 1, 64 do
    f:seek("set", pos)
    local h = f:read(4)
    if not h or #h < 4 then return nil end
    local t = h:byte(1)
    local last = t >= 128
    t = t % 128
    local len = (h:byte(2) * 256 + h:byte(3)) * 256 + h:byte(4)
    if t == 4 then                                   -- VORBIS_COMMENT
      local blob = f:read(len > 16384 and 16384 or len) or ""
      return num_from(blob:match("[Bb][Pp][Mm]=([%d%.]+)"))
    end
    if last then return nil end
    pos = pos + 4 + len
  end
  return nil
end

local function mp4_bpm(f)
  -- walk top-level atoms to moov, then scan moov in chunks for tmpo
  local pos, fsize = 0, f:seek("end")
  for _ = 1, 64 do
    if pos + 8 > fsize then return nil end
    f:seek("set", pos)
    local h = f:read(8)
    if not h or #h < 8 then return nil end
    local size, typ = be32(h, 1), h:sub(5, 8)
    if size == 1 then                                -- 64-bit size (mdat)
      local x = f:read(8); if not x or #x < 8 then return nil end
      if be32(x, 1) ~= 0 then return nil end         -- >4 GB, give up
      size = be32(x, 5)
    elseif size == 0 then size = fsize - pos end
    if size < 8 then return nil end
    if typ == "moov" then
      local p, stop, carry = pos + 8, pos + size, ""
      f:seek("set", p)
      while p < stop do
        local want = stop - p; if want > 16384 then want = 16384 end
        local d = f:read(want)
        if not d or #d == 0 then return nil end
        local blob = carry .. d
        local i = blob:find("tmpo", 1, true)
        if i and i + 21 <= #blob and blob:sub(i + 8, i + 11) == "data" then
          return blob:byte(i + 20) * 256 + blob:byte(i + 21)
        end
        if i and i + 21 > #blob then carry = blob:sub(i) else carry = blob:sub(-24) end
        p = p + #d
        rb.yield()
      end
      return nil
    end
    pos = pos + size
  end
  return nil
end

function bpm_from_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local head = f:read(12) or ""
  local ok, bpm = pcall(function()
    if head:sub(1, 3) == "ID3" then return id3_bpm(f, head)
    elseif head:sub(1, 4) == "fLaC" then return flac_bpm(f)
    elseif head:sub(5, 8) == "ftyp" then return mp4_bpm(f)
    elseif head:sub(1, 4) == "RIFF" and head:sub(9, 12) == "WAVE" then
      local pos = 12                                -- walk RIFF chunks for "id3 "
      for _ = 1, 32 do
        f:seek("set", pos)
        local h = f:read(8)
        if not h or #h < 8 then return nil end
        local sz = le32(h, 5)
        if h:sub(1, 4):lower() == "id3 " then
          local ih = f:read(10) or ""
          if ih:sub(1, 3) == "ID3" then return id3_bpm(f, ih, pos + 8) end
          return nil
        end
        if sz < 0 then return nil end
        pos = pos + 8 + sz + sz % 2
      end
      return nil
    elseif head:sub(1, 4) == "OggS" then
      f:seek("set", 0)
      return num_from((f:read(16384) or ""):match("[Bb][Pp][Mm]=([%d%.]+)"))
    end
    return nil
  end)
  f:close()
  if not ok then bpm = nil end
  if bpm and (bpm < 40 or bpm > 260) then bpm = nil end
  return bpm
end
end -- BPM readers

------------------------------------------------------------------------
-- MOOD SCORING (only runs when building the cache)
------------------------------------------------------------------------
local function pack3(e, v, a) return (e + 128) + (v + 128) * 256 + (a + 128) * 65536 end
local function unpack3(x)
  return x % 256 - 128, idiv(x, 256) % 256 - 128, idiv(x, 65536) % 256 - 128
end

local function genre_vec(gname)
  gname = tagtext(gname):lower()
  local e, v, a, hits = 0, 0, 0, 0
  for i = 1, #GENRE do
    local g = GENRE[i]
    if gname:find(g[1], 1, true) then
      e, v, a, hits = e + g[2], v + g[3], a + g[4], hits + 1
    end
  end
  if hits == 0 then return 0, 0, 0, false end
  return idiv(e, hits), idiv(v, hits), idiv(a, hits), true
end

local function word_vec(text)
  local e, v, a = 0, 0, 0
  text = tagtext(text)
  if text == "" then return 0, 0, 0 end
  for w in text:lower():gmatch("[%a\128-\255']+") do
    local x = WORDS[(w:gsub("'", ""))]
    if x then e, v, a = e + x[1], v + x[2], a + x[3] end
  end
  return clamp(e, -50, 50), clamp(v, -50, 50), clamp(a, -50, 50)
end

-- Order-independent fingerprint of SCORE_VERSION + GENRE + WORDS, stored in
-- the mood cache header so an edited lexicon (or a cache written on a computer
-- from a different copy of this file) is never used by mistake.
local LEXFP
do
local function strhash(s, h)
  for i = 1, #s do h = (h * 31 + s:byte(i)) % 16777213 end
  return h
end
local function lexicon_fp()
  local fp = SCORE_VERSION
  for i = 1, #GENRE do
    local g = GENRE[i]
    fp = (fp + strhash(g[1] .. "=" .. g[2] .. "," .. g[3] .. "," .. g[4], 7)) % 16777213
  end
  for w, x in pairs(WORDS) do
    fp = (fp + strhash(w .. ":" .. x[1] .. "," .. x[2] .. "," .. x[3], 11)) % 16777213
  end
  return fp
end
LEXFP = lexicon_fp()
end
local MOOD_HDR = 20

-- BPM file: "RGB2" + count(4) + count * [bpm, key24]. Read through a small
-- window like the mood cache, so it never sits in memory whole.
local function open_bpm()
  local f = io.open(BPM_F, "rb")
  if not f then return nil end
  local h = f:read(8)
  if not h or #h < 8 or h:sub(1, 4) ~= BPM_MAGIC then f:close() return nil end
  return { f = f, n = le32(h, 5), base = -1, cnt = 0, buf = "" }
end

local function bpm_entry(B, r)
  if not B or r >= B.n then return 0, 0 end
  if r < B.base or r >= B.base + B.cnt then
    B.f:seek("set", 8 + r * 4)
    B.buf = B.f:read(4 * 256) or ""
    B.base, B.cnt = r, idiv(#B.buf, 4)
  end
  local p = (r - B.base) * 4 + 1
  local b, h1, h2, h3 = B.buf:byte(p, p + 3)
  if not h3 then return 0, 0 end
  return b, h1 + h2 * 256 + h3 * 65536
end

local function close_bpm(B) if B then B.f:close() end end

local function build_mood(m)
  log("build: entries=%d commit=%d", m.entries, m.commit)
  local G, A, C = {}, {}, {}
  rb.splash(0, "Building mood cache: genres")
  stream_tagfile(T.genre, function(off, s)
    local e, v, a, k = genre_vec(s)
    G[off] = pack3(e, v, a) + (k and 16777216 or 0)
  end)
  rb.splash(0, "Building mood cache: albums")
  stream_tagfile(T.album, function(off, s)
    local e, v, a = word_vec(s)
    if e ~= 0 or v ~= 0 or a ~= 0 then A[off] = pack3(e, v, a) end
  end)
  rb.splash(0, "Building mood cache: comments")
  stream_tagfile(T.comment, function(off, s)
    local e, v, a = word_vec(s)
    if e ~= 0 or v ~= 0 or a ~= 0 then C[off] = pack3(e, v, a) end
  end)
  collectgarbage("collect")
  log("build: maps done")

  local B = open_bpm()
  local out = io.open(MOOD_F .. ".tmp", "w")
  if not out then close_bpm(B) log("build: cannot write cache") return false end
  out:write(MOOD_MAGIC, u32s(m.entries), u32s(m.commit), u32s(m.datasize), u32s(LEXFP))
  local buf, nb, written, nbpm = {}, 0, 0, 0
  local function flush()
    if nb > 0 then out:write(table.concat(buf)); buf = {}; nb = 0 end
  end
  local ok = each_record(m, function(r, c, b, del)
    -- pad any gap (short reads)
    while written < r do nb = nb + 1; buf[nb] = "\0\0\0\0\0"; written = written + 1 end
    if del then
      nb = nb + 1; buf[nb] = "\0\0\0\0\0"
    else
      local ge, gv, ga, known = 0, 0, 0, false
      local g = G[fld(c, b, T.genre)]
      if g then
        if g >= 16777216 then known = true; g = g - 16777216 end
        ge, gv, ga = unpack3(g)
      end
      local we, wv, wa = word_vec(tag_at(T.title, fld(c, b, T.title)))
      local x = A[fld(c, b, T.album)]
      if x then local e, v, a = unpack3(x); we, wv, wa = we + e, wv + v, wa + a end
      x = C[fld(c, b, T.comment)]
      if x then local e, v, a = unpack3(x); we, wv, wa = we + e, wv + v, wa + a end
      we, wv, wa = clamp(we, -50, 50), clamp(wv, -50, 50), clamp(wa, -50, 50)

      local len = idiv(fld(c, b, T.length), 1000)
      local le = 0
      if len > 0 then
        if len < 150 then le = 20 elseif len < 210 then le = 8
        elseif len > 420 then le = -20 elseif len > 330 then le = -8 end
      end

      local bpm = 0
      local bb, bh = bpm_entry(B, r)
      if bb > 0 then
        local p = tag_at(T.filename, fld(c, b, T.filename))
        if p and path_key(p, fld(c, b, T.mtime)) == bh then bpm = bb; nbpm = nbpm + 1 end
      end
      local e = ge + we + le
      if bpm > 0 then e = idiv(e + clamp((bpm - 115) * 2, -100, 100) * 2, 3) end
      local a = ga + wa
      if bpm > 150 then a = a + 10 end
      e, a = clamp(e, -100, 100), clamp(a, -100, 100)
      local v = clamp(gv + wv, -100, 100)
      nb = nb + 1
      buf[nb] = string.char(e + 128, v + 128, a + 128, bpm, known and 3 or 2)
    end
    written = written + 1
    if nb >= 64 then flush() end
  end, "Building mood cache", { all = true })
  flush()
  close_bpm(B)
  while ok and written < m.entries do out:write("\0\0\0\0\0"); written = written + 1 end
  out:close()
  if not ok then os.remove(MOOD_F .. ".tmp"); log("build: cancelled") return false end
  os.remove(MOOD_F)
  os.rename(MOOD_F .. ".tmp", MOOD_F)
  log("build: done, %d tracks with BPM", nbpm)
  return true
end

-- Mood cache reader: validates the header, then serves 5-byte records from a
-- small sliding window (sequential reads during a scan, seek for random access).
local function open_mood(m)
  local f = io.open(MOOD_F, "rb")
  if not f then return nil end
  local h = f:read(MOOD_HDR)
  local size = f:seek("end")
  if not h or #h < MOOD_HDR or h:sub(1, 4) ~= MOOD_MAGIC or size ~= MOOD_HDR + m.entries * 5
     or le32(h, 5) ~= m.entries or le32(h, 9) ~= m.commit or le32(h, 13) ~= m.datasize
     or le32(h, 17) ~= LEXFP then
    f:close()
    return nil
  end
  return { f = f, base = -1, n = 0, buf = "" }
end

local function mood(M, r)
  if r < M.base or r >= M.base + M.n then
    M.f:seek("set", MOOD_HDR + r * 5)
    M.buf = M.f:read(5 * 256) or ""
    M.base, M.n = r, idiv(#M.buf, 5)
    if M.n == 0 then return 0, 0, 0, 0, 0 end
  end
  local p = (r - M.base) * 5 + 1
  local e, v, a, bpm, fl = M.buf:byte(p, p + 4)
  return e - 128, v - 128, a - 128, bpm, fl
end

local function close_mood(M) if M then M.f:close() end end

------------------------------------------------------------------------
-- BPM SCAN (optional; incremental -- only new/changed files are opened)
------------------------------------------------------------------------
local function scan_bpm(m)
  local old = open_bpm()
  local out = io.open(BPM_F .. ".tmp", "w")
  if not out then close_bpm(old) return false end
  out:write(BPM_MAGIC, u32s(m.entries))
  local buf, nb, written, opened, cancelled = {}, 0, 0, 0, false
  each_record(m, function(r, c, b, del)
    while written < r do nb = nb + 1; buf[nb] = "\0\0\0\0"; written = written + 1 end
    local ent = "\0\0\0\0"
    if not del then
      local ob, oh = bpm_entry(old, r)
      local p = tag_at(T.filename, fld(c, b, T.filename))
      if p then
        local h = path_key(p, fld(c, b, T.mtime))
        local bpm = ob
        if oh ~= h then
          if cancelled then bpm = 0
          else bpm = clamp(bpm_from_file(p) or 0, 0, 255); opened = opened + 1 end
          if cancelled then h = 0 end   -- not scanned: leave for next time
        end
        ent = string.char(bpm, h % 256, idiv(h, 256) % 256, idiv(h, 65536) % 256)
      end
    end
    nb = nb + 1; buf[nb] = ent; written = written + 1
    if nb >= 64 then out:write(table.concat(buf)); buf = {}; nb = 0 end
    if not cancelled and r % 16 == 0 then
      rb.splash_progress(r, m.entries, "Scanning BPM tags")
      if cancel_pressed() then cancelled = true end
    end
  end, nil, { all = true, nocancel = true })
  if nb > 0 then out:write(table.concat(buf)) end
  close_bpm(old)
  while written < m.entries do out:write("\0\0\0\0"); written = written + 1 end
  out:close()
  os.remove(BPM_F)
  os.rename(BPM_F .. ".tmp", BPM_F)
  os.remove(MOOD_F)                      -- force mood rebuild with new BPMs
  log("bpm scan: opened %d files%s", opened, cancelled and " (cancelled)" or "")
  return true
end

------------------------------------------------------------------------
-- SELECTION (streams the index; keeps only the best KEEP candidates)
------------------------------------------------------------------------
-- offset of the "<Untagged>" entry in a unique tag file (-1 if none), so
-- untagged artists don't all count as one artist. Looked up once per run.
local untagged_off
do
local untagged_cache = {}
function untagged_off(tag)
  local o = untagged_cache[tag]
  if o then return o end
  o = -1
  stream_tagfile(tag, function(off, s)
    if s == UNTAGGED then o = off return false end
  end)
  untagged_cache[tag] = o
  return o
end
end

-- year used as "now" by Throwback: the clock if it looks set, else the
-- newest year in the library (many players have never had their clock set)
local function this_year(lib_max)
  local y = 0
  if os and os.date then
    local ok, t = pcall(os.date, "*t")
    if ok and type(t) == "table" and t.year then y = t.year end
  end
  if y < 2015 or y > 2100 then y = 0 end
  if lib_max > y then y = lib_max end
  return y
end

local function pick(m, M, recipe)
  local sp = recipe.special
  local maxlp, maxyear, artist_pc = 0, 0, nil
  if sp == "rediscover" or sp == "deepcuts" or sp == "throwback" then
    if sp == "deepcuts" then artist_pc = {} end
    local ok = each_record(m, function(r, c, b)
      local lp = fld(c, b, T.lastplayed)
      if lp > maxlp then maxlp = lp end
      local y = fld(c, b, T.year)
      if y > maxyear and y < 2100 then maxyear = y end
      local pc = fld(c, b, T.playcount)
      if artist_pc and pc > 0 then                              -- played artists only
        local ar = fld(c, b, T.artist)
        artist_pc[ar] = (artist_pc[ar] or 0) + pc
      end
    end, "Reading play stats")
    if not ok then return nil end
  end
  local old_before = 0
  if sp == "throwback" then old_before = this_year(maxyear) - 15 end

  local UA = untagged_off(T.artist)
  local cand, nc, worst = {}, 0, nil
  local function prune()
    table.sort(cand, function(x, y) return x[1] < y[1] end)
    for i = KEEP + 1, #cand do cand[i] = nil end
    nc = #cand
    worst = cand[nc] and cand[nc][1]
  end

  local ok = each_record(m, function(r, c, b)
    local e, v, a, bpm, fl = mood(M, r)
    if fl < 2 then return end
    if r == recipe.skip_r then return end
    local ar = fld(c, b, T.artist)
    if ar == recipe.excl_ar then return end
    if recipe.genre_eq and fld(c, b, T.genre) ~= recipe.genre_eq then return end
    local len = idiv(fld(c, b, T.length), 1000)
    if len > 0 and len < 45 then return end                         -- skits / intros
    if recipe.len_lo and len < recipe.len_lo then return end
    if recipe.len_hi and len > recipe.len_hi then return end
    if recipe.bpm_lo and bpm > 0 and (bpm < recipe.bpm_lo or bpm > recipe.bpm_hi) then return end
    local pc = fld(c, b, T.playcount)
    if recipe.pc_max and pc > recipe.pc_max then return end
    local year = fld(c, b, T.year)
    if recipe.year_lo and (year < recipe.year_lo or year > recipe.year_hi) then return end
    local cd = 0
    if recipe.cadence then                                          -- step-matched BPM
      if bpm == 0 then return end
      local c1 = recipe.cadence
      cd = abs(bpm - c1)
      local d2 = abs(bpm * 2 - c1)
      if d2 < cd then cd = d2 end
      d2 = abs(bpm - c1 * 2)
      if d2 < cd then cd = d2 end
      if cd > 6 then return end
    end

    local s
    if sp == "rediscover" then
      if pc < 3 or maxlp == 0 then return end
      s = -(idiv((maxlp - fld(c, b, T.lastplayed)) * 100, maxlp) + pc * 3)
    elseif sp == "deepcuts" then
      if pc > 0 then return end
      local apc = artist_pc[ar] or 0
      if apc < 5 then return end
      s = -apc + math.random(0, 20)
    elseif sp == "throwback" then
      if year <= 0 or year > old_before then return end
      local p = pc
      if p > 50 then p = 50 end
      s = -(p * 4 + fld(c, b, T.rating) * 6) + math.random(0, 40)
      if pc == 0 then s = s + 50 end
    elseif sp == "shuffle" then
      s = math.random(0, 1000)
    else
      s = abs(e - recipe.e) * recipe.we + abs(v - recipe.v) * recipe.wv + abs(a - recipe.a) * recipe.wa
      if fl % 2 == 0 then s = s + 40 end                            -- genre unknown
      if recipe.pull_ar and ar == recipe.pull_ar then s = s - 60 end
      if recipe.near_year then
        if year <= 0 then s = s + 70
        else
          local dy = abs(year - recipe.near_year)
          if dy > 5 then
            if dy > 25 then dy = 25 end
            s = s + 70 + (dy - 5) * 4
          end
        end
      end
      if recipe.long_bonus and len > 180 then s = s - idiv(clamp(len, 180, 600) - 180, 6) end
      s = s + cd * 12 + math.random(0, 25)
    end
    if worst and nc >= KEEP and s >= worst then return end
    nc = nc + 1
    cand[nc] = { s, r, ar }
    if nc >= KEEP + 64 then prune() end
  end, "Choosing tracks")
  if not ok then return nil end
  prune()

  local out, per_artist = {}, {}
  for i = 1, #cand do
    local t = cand[i]
    local k = per_artist[t[3]] or 0
    if t[3] == UA or k < MAX_PER_ARTIST then                        -- untagged: no cap
      per_artist[t[3]] = k + 1
      out[#out + 1] = t
      if #out >= MAX_TRACKS then break end
    end
  end
  cand = nil
  for i = 2, #out do                                                -- no back-to-back artists
    local x = out[i][3]
    if x == out[i - 1][3] and x ~= UA then
      local done = false
      for j = i + 1, #out do
        if out[j][3] ~= out[i - 1][3] then out[i], out[j] = out[j], out[i] done = true break end
      end
      for j = 1, i - 2 do                                           -- nothing later: look back
        if done then break end
        local y = out[j][3]
        if y ~= x and y ~= out[i - 1][3] and (j == 1 or out[j - 1][3] ~= x)
           and out[j + 1][3] ~= x and (i == #out or out[i + 1][3] ~= y) then
          out[i], out[j] = out[j], out[i]
          done = true
        end
      end
    end
  end
  for i = 1, #out do                                                -- fetch strings for picks only
    local rec = read_record(m, out[i][2])
    local t = { r = out[i][2], len = 0, title = "", artist = "" }
    if rec and #rec >= IDX_SIZE then
      t.path = tag_at(T.filename, fld(rec, 1, T.filename))
      t.len = idiv(fld(rec, 1, T.length), 1000)
      t.title = tagtext(tag_at(T.title, fld(rec, 1, T.title)))
      t.artist = tagtext(tag_at(T.artist, fld(rec, 1, T.artist)))
      if t.title == "" and t.path then                              -- untagged: use the file name
        t.title = t.path:match("([^/]*)$"):gsub("%.[^%.]*$", "")
      end
    end
    out[i] = t
  end
  return out
end

------------------------------------------------------------------------
-- NOW PLAYING
------------------------------------------------------------------------
local now_playing_path
do
local function read_np(off, size)
  if not rb.audio_current_track then return nil end
  local ok, s = pcall(rb.audio_current_track, off, size)
  if not ok or type(s) ~= "string" then return nil end
  return s:match("^(%Z*)")
end

function now_playing_path()
  -- fast path: field offset cached from a previous run (no settings.lua load)
  local f = io.open(NP_F, "r")
  if f then
    local line = f:read("*l") or ""
    f:close()
    local off, size = line:match("^(%d+)%s+(%d+)")
    off, size = tonumber(off), tonumber(size)
    if off then
      local p = read_np(off, size)
      if p and p:sub(1, 1) == "/" then return p end
      if p == "" then return nil end                    -- nothing loaded
    end
  end
  -- slow path: let rbsettings/settings.lua tell us the offset, then cache it
  local p
  if pcall(require, "rbsettings") and pcall(require, "settings") then
    local md = rb.metadata and rb.metadata.mp3_entry
    local spec = md and md.path
    if spec then
      local o, s = spec:match("(0x%x+),%s*(%d+)")
      local off, size = tonumber(o), tonumber(s)
      if off and size then
        p = read_np(off, size)
        if p and p:sub(1, 1) == "/" then
          local w = io.open(NP_F, "w")
          if w then w:write(off, " ", size, "\n"); w:close() end
        end
      end
    end
  end
  rb.metadata, rb.system, rb.settings = nil, nil, nil
  package.loaded["settings"], package.loaded["rbsettings"] = nil, nil
  collectgarbage("collect")
  if p and p:sub(1, 1) == "/" then return p end
  return nil
end
end

------------------------------------------------------------------------
-- OUTPUT
------------------------------------------------------------------------
local save_playlist, start_playlist, request_play
do
local function safe_name(s)
  s = s:gsub("[^%w%-_ &,%(%)'\128-\255]", ""):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")
  if #s > 60 then                         -- cut, then drop a possibly split UTF-8 char
    s = s:sub(1, 60):gsub("[\192-\255][\128-\191]*$", ""):gsub("%s+$", "")
  end
  if s == "" then s = "Playlist" end
  return s
end

function save_playlist(name, list)
  if luadir and luadir.mkdir then pcall(luadir.mkdir, PL_DIR) end
  local path = PL_DIR .. "/Genius - " .. safe_name(name) .. ".m3u8"
  local f = io.open(path, "w")
  if not f then return nil end
  f:write("#EXTM3U\n")
  for i = 1, #list do
    local t = list[i]
    if t.path then
      local label = t.title
      if t.artist ~= "" then label = t.artist .. " - " .. t.title end
      f:write(string.format("#EXTINF:%d,%s\n", t.len, label), t.path, "\n")
    end
  end
  f:close()
  return path
end

-- Start the saved playlist with stock Rockbox's playlist API.
-- playlist_create(dir, file) loads the .m3u8 itself; if that comes back empty
-- the tracks are inserted one by one. Returns ok, detail.
local PLAYLIST_INSERT_LAST = -3          -- apps/playlist.h
function start_playlist(path, list)
  if type(rb.playlist) ~= "function" then return false, "no rb.playlist" end
  local dir, file = path:match("^(.*)/([^/]+)$")
  if not dir then return false, "bad path" end
  local ok, res = pcall(rb.playlist, "create", dir, file)
  if not ok then return false, "create: " .. tostring(res) end
  if type(res) == "number" and res < 0 then return false, "create returned " .. res end
  local function amount()
    local okn, n = pcall(rb.playlist, "amount")
    if okn and type(n) == "number" then return n end
    return 0
  end
  local n, how = amount(), "loaded"
  if n == 0 then
    how = "inserted"
    for i = 1, #list do
      if list[i].path then
        pcall(rb.playlist, "insert_track", list[i].path, PLAYLIST_INSERT_LAST, false, false)
      end
    end
    pcall(rb.playlist, "sync")
    n = amount()
  end
  if n == 0 then return false, "playlist empty after create + insert" end
  local oks, err = pcall(rb.playlist, "start", 0, 0, 0)
  if not oks then return false, "start: " .. tostring(err) end
  return true, string.format("%s %d tracks", how, n)
end

-- Fallback for builds patched to pick this file up when the plugin exits.
function request_play(path)
  local f = io.open(PLAY_F, "w")
  if not f then return false end
  f:write(path)
  f:close()
  return true
end
end

------------------------------------------------------------------------
-- SETTINGS (just the playlist length)
------------------------------------------------------------------------
local set_length, load_cfg, save_cfg, next_length
do
function set_length(n)
  MAX_TRACKS = n
  KEEP = n * 2 + 50
end

function load_cfg()
  local f = io.open(CFG_F, "r")
  if not f then return end
  local s = f:read("*a") or ""
  f:close()
  local n = tonumber(s:match("length=(%d+)"))
  for i = 1, #LENGTHS do if LENGTHS[i] == n then set_length(n) end end
end

function save_cfg()
  local f = io.open(CFG_F, "w")
  if f then f:write("length=", MAX_TRACKS, "\n"); f:close() end
end

function next_length()
  for i = 1, #LENGTHS do
    if LENGTHS[i] == MAX_TRACKS then set_length(LENGTHS[i % #LENGTHS + 1]) save_cfg() return end
  end
  set_length(LENGTHS[1]); save_cfg()
end
end

------------------------------------------------------------------------
-- UI
------------------------------------------------------------------------
local function menu(title, items, sel)
  local r = rb.do_menu(title, items, sel or 0, false)
  if r == nil or r < 0 then return nil end
  return r + 1
end

local function describe(e, v, a, bpm)
  local function lbl(x, lo, hi)
    if x < -33 then return lo elseif x > 33 then return hi end
    return "mid"
  end
  return string.format("%s / %s / %s%s", lbl(e, "slow", "fast"), lbl(v, "dark", "bright"),
                       lbl(a, "soft", "hard"), bpm > 0 and (" " .. bpm .. "bpm") or "")
end

local function length_item() return "Playlist length: " .. MAX_TRACKS end

-- Build the recipe for one of GENIUS_MODES. Returns recipe or nil, message.
local function genius_recipe(seed, mode)
  local UA, UG = untagged_off(T.artist), untagged_off(T.genre)
  local t = seed.title
  local r = { e = seed.e, v = seed.v, a = seed.a, we = 2, wv = 2, wa = 2, skip_r = seed.r }
  local own_artist = seed.ar ~= UA and seed.artist ~= ""
  if mode == 1 then
    r.name = "More like " .. t
    if own_artist then r.pull_ar = seed.ar end
  elseif mode == 2 then
    r.name = "Like " .. t .. ", other artists"
    if own_artist then r.excl_ar = seed.ar end
  elseif mode == 3 then
    if seed.ge == UG or seed.genre == "" then return nil, "Now playing has no genre tag" end
    r.name = "Like " .. t .. ", same genre (" .. seed.genre .. ")"
    r.genre_eq = seed.ge
  elseif mode == 4 then
    r.name = t .. ", up a notch"
    r.e = clamp(seed.e + 30, -100, 100)
  elseif mode == 5 then
    r.name = t .. ", brought down"
    r.e, r.a = clamp(seed.e - 30, -100, 100), clamp(seed.a - 30, -100, 100)
  elseif mode == 6 then
    if seed.year <= 0 then return nil, "Now playing has no year tag" end
    r.name = "Like " .. t .. ", same era (" .. seed.year .. ")"
    r.near_year = seed.year
    if own_artist then r.pull_ar = seed.ar end
  elseif mode == 7 then
    r.name = "Deep cuts like " .. t
    r.pc_max = 0
    if own_artist then r.pull_ar = seed.ar end
  end
  return r
end

local mood_dial
do
local function dial_name(e, v, a, era)
  local parts = {}
  if e ~= 4 then parts[#parts + 1] = DIAL_E[e] end
  if v ~= 4 then parts[#parts + 1] = DIAL_V[v] end
  if a ~= 4 then parts[#parts + 1] = DIAL_A[a] end
  if #parts == 0 then parts[1] = "Middle of the road" end
  if era and era > 1 then parts[#parts + 1] = ERAS[era][1] end
  return "Dial - " .. table.concat(parts, ", ")
end

function mood_dial()
  local e = menu("Energy", DIAL_E, 3)
  local v = e and menu("Mood", DIAL_V, 3)
  local a = v and menu("Edge", DIAL_A, 3)
  if not a then return nil end
  local names = {}
  for i = 1, #ERAS do names[i] = ERAS[i][1] end
  local era = menu("Era", names, 0)
  if not era then return nil end
  local lens, cur = {}, 1
  for i = 1, #LENGTHS do
    lens[i] = LENGTHS[i] .. " tracks"
    if LENGTHS[i] == MAX_TRACKS then cur = i end
  end
  local l = menu("Length", lens, cur - 1)
  if not l then return nil end
  if LENGTHS[l] ~= MAX_TRACKS then set_length(LENGTHS[l]); save_cfg() end
  local r = { name = dial_name(e, v, a, era),
              e = (e - 4) * 30, v = (v - 4) * 30, a = (a - 4) * 30, we = 2, wv = 2, wa = 2 }
  if era > 1 then r.year_lo, r.year_hi = ERAS[era][2], ERAS[era][3] end
  return r
end
end

local function group_menu(title, group)
  local items, map = {}, {}
  for i = 1, #RECIPES do
    if RECIPES[i].group == group then
      items[#items + 1] = RECIPES[i].name
      map[#items] = RECIPES[i]
    end
  end
  items[#items + 1] = "Back"
  local sel = menu(title, items)
  return sel and map[sel]
end

-- S: shared state for the menu handlers { m, M, np, seed, seed_looked, have_bpm }

local function get_seed(S)
  local m, M, np = S.m, S.M, S.np
  rb.splash(0, "Finding now playing...")
  local r = find_record_by_path(np, m.entries)
  if not r and np:sub(1, 2) == "/<" then                  -- volume prefix mismatch
    r = find_record_by_path((np:gsub("^/<[^>]*>", "")), m.entries)
  end
  if not r then return nil end
  local rec = read_record(m, r)
  local e, v, a, bpm = mood(M, r)
  local s = { r = r, ar = fld(rec, 1, T.artist), ge = fld(rec, 1, T.genre),
              year = fld(rec, 1, T.year), e = e, v = v, a = a, bpm = bpm,
              title = tagtext(tag_at(T.title, fld(rec, 1, T.title))),
              artist = tagtext(tag_at(T.artist, fld(rec, 1, T.artist))),
              genre = tagtext(tag_at(T.genre, fld(rec, 1, T.genre))) }
  if s.title == "" then s.title = (np:match("([^/]*)$"):gsub("%.[^%.]*$", "")) end
  return s
end

-- Genius submenu. Returns a recipe or nil.
local function genius_menu(S)
  if S.np and not S.seed_looked then
    S.seed = get_seed(S)
    S.seed_looked = true
    local seed = S.seed
    log("seed: %s", seed and (seed.artist .. " - " .. seed.title) or "not in database")
    if seed then rb.splash(HZ, "Seed: " .. describe(seed.e, seed.v, seed.a, seed.bpm)) end
  end
  local seed = S.seed
  if not seed then
    rb.splash(2 * HZ, S.np and "Now playing track isn't in the database" or "Start playing a track first")
    return nil
  end
  local gsel = 0
  while true do
    local gi = {}
    for i = 1, #GENIUS_MODES do gi[i] = GENIUS_MODES[i] end
    gi[#gi + 1] = length_item()
    gi[#gi + 1] = "Back"
    local g = menu("Genius: " .. seed.title, gi, gsel)
    if not g or g == #gi then return nil end
    if g == #GENIUS_MODES + 1 then
      next_length()
    else
      local recipe, msg = genius_recipe(seed, g)
      if recipe then log("genius mode %d", g) return recipe end
      rb.splash(2 * HZ, msg)
    end
    gsel = g - 1
  end
end

local function rebuild(S, scan)
  close_mood(S.M); S.M = nil
  if scan then
    log("bpm scan start")
    scan_bpm(S.m)
    S.have_bpm = true
  else
    os.remove(MOOD_F)
  end
  if not build_mood(S.m) then rb.splash(2 * HZ, "Cancelled") return false end
  S.M = open_mood(S.m)
  if not S.M then rb.splash(3 * HZ, "Mood cache build failed") return false end
  rb.splash(HZ, scan and "BPM tags updated" or "Mood cache rebuilt")
  return true
end

-- Tools submenu. Returns false if the plugin should exit.
local function tools_menu(S)
  local t = menu("Tools", { S.have_bpm and "Update BPM tags (new files)" or "Scan BPM tags (slow, once)",
                            "Rebuild mood cache", length_item(), "About", "Back" })
  if t == 1 then return rebuild(S, true)
  elseif t == 2 then return rebuild(S, false)
  elseif t == 3 then
    next_length()
    rb.splash(HZ, length_item())
  elseif t == 4 then
    rb.splash(4 * HZ, string.format("RockGenius %s\n%d tracks in database\nBPM file: %s",
              VERSION, S.m.entries, S.have_bpm and "yes" or "no"))
  end
  return true
end

-- Build, save and offer a playlist. Returns true if the plugin should exit.
local function run_recipe(S, recipe)
  log("recipe: %s (length %d)", recipe.name, MAX_TRACKS)
  local list = pick(S.m, S.M, recipe)
  log("picked %d", list and #list or -1)
  if list == nil then rb.splash(HZ, "Cancelled") return false end
  if #list == 0 then
    if recipe.cadence then
      rb.splash(3 * HZ, "No tracks with a matching BPM.\nTools > Scan BPM tags, or run bpm_export.py")
    else
      rb.splash(2 * HZ, "No matching tracks")
    end
    return false
  end
  local path = save_playlist(recipe.name, list)
  local preview = {}
  for i = 1, #list do
    if i > 8 then break end
    local t = list[i]
    preview[#preview + 1] = t.artist ~= "" and (t.artist .. " - " .. t.title) or t.title
  end
  local act = menu(#list .. " tracks: " .. recipe.name,
                   { "Play now", "Back (saved to Playlists)", "-- preview --", unpack(preview) })
  if act ~= 1 then return false end
  if not path then rb.splash(2 * HZ, "Could not save playlist") return false end
  local ok, detail = start_playlist(path, list)
  log("play now: %s (%s)", ok and "started" or "failed", tostring(detail))
  if ok then return true end
  if request_play(path) then
    log("hand-off file written: %s", path)
    rb.splash(3 * HZ, "Couldn't start playback.\nSaved to Playlists: open it there.")
    return true
  end
  rb.splash(2 * HZ, "Could not start or hand off playlist")
  return false
end

local function main()
  log("start v%s", VERSION)
  load_cfg()
  local S = { np = now_playing_path() }
  log("now playing: %s", S.np or "(none)")

  local m, err = open_master()
  if not m then rb.splash(3 * HZ, err) return end
  S.m = m
  log("db: entries=%d commit=%d serial=%d recsize=%d", m.entries, m.commit, m.serial, IDX_SIZE)
  if m.entries == 0 then rb.splash(2 * HZ, "Database is empty") return end

  S.M = open_mood(m)
  if not S.M then
    log("mood cache missing or stale -> rebuilding")
    if not build_mood(m) then rb.splash(2 * HZ, "Cancelled") return end
    S.M = open_mood(m)
    if not S.M then rb.splash(3 * HZ, "Mood cache build failed") return end
  end
  local f = io.open(BPM_F, "rb")
  if f then f:close() end
  S.have_bpm = f and true or false
  log("ready")

  while true do
    local items = { S.np and "Genius (from now playing)" or "Genius (nothing playing)",
                    "Moods", "Workouts", "Discovery", "Mood dial", "Tools", "Quit" }
    local sel = menu("RockGenius " .. VERSION .. " (" .. m.entries .. " tracks)", items)
    if not sel or sel == #items then break end
    local recipe
    if sel == 1 then recipe = genius_menu(S)
    elseif sel == 2 then recipe = group_menu("Moods", "moods")
    elseif sel == 3 then recipe = group_menu("Workouts", "workouts")
    elseif sel == 4 then recipe = group_menu("Discovery", "discovery")
    elseif sel == 5 then recipe = mood_dial()
    elseif sel == 6 then if not tools_menu(S) then break end
    end
    if recipe and run_recipe(S, recipe) then break end
    collectgarbage("collect")
  end
  close_mood(S.M)
  m.f:close()
  close_tags()
end

local ok, e = pcall(main)
if not ok then
  log("ERROR: %s", tostring(e))
  close_tags()
  rb.splash(5 * HZ, "RockGenius error:\n" .. tostring(e))
end
if rb.mem_stats then
  local used, allocd = rb.mem_stats()
  log("exit: heap used=%dK allocated=%dK%s", idiv(used, 1024), idiv(allocd, 1024),
      allocd > 600 * 1024 and "  (audio buffer was borrowed!)" or "")
else
  log("exit")
end
