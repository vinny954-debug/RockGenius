-- Stubbed Rockbox environment for running rockgenius.lua off-device.
-- Works under lupa's Lua 5.1 (test/run.py) and under the integer build
-- from test/intlua/build.sh, so it must be integer-safe itself.
--
-- Expects a global HARNESS = {
--   root = "/abs/test/root",       -- stands in for the player's drive
--   script = "rockgenius.lua",
--   choices = { 0, 3, ... },       -- do_menu answers, 0-based; -1 = back/cancel
--   np = "/Music/x.mp3" or "",     -- what audio_current_track reports
--   playlist = "load" | "insert" | "fail" | "none",
--   year = 2026,                   -- what os.date("*t").year reports
-- }
local H = HARNESS
local ROOT = H.root
local function map(p)
  p = p:gsub("^/<[^>]*>", "")
  if p:sub(1, 1) == "/" then return ROOT .. p end
  return p
end
local _open, _rm, _mv, _date = io.open, os.remove, os.rename, os.date
io.open = function(p, m) return _open(map(p), m) end
os.remove = function(p) return _rm(map(p)) end
os.rename = function(a, b) return _mv(map(a), map(b)) end
os.date = function(fmt, t)
  if fmt == "*t" then return { year = H.year or 2026, month = 6, day = 1 } end
  return _date(fmt, t)
end
luadir = { mkdir = function(p) os.execute("mkdir -p '" .. map(p) .. "'") end }

-- deterministic integer random numbers (the integer build's math.random is
-- the stock one, which needs floats)
local rstate = 12345
math.randomseed = function(s) end
math.random = function(m, n)
  rstate = (rstate * 75 + 74) % 65537
  if not m then return 0 end
  if not n then m, n = 1, m end
  return m + rstate % (n - m + 1)
end

PEAK = 0
MENUS = {}          -- every menu shown: { title = ..., items = {...} }
SPLASHES = {}
PLAYLOG = {}        -- every rb.playlist call: "create dir file", "insert path", ...
local choices, ci, tick = H.choices, 0, 0
local pl_amount = 0

local function measure()
  collectgarbage("collect")
  local k = collectgarbage("count")
  if k > PEAK then PEAK = k end
end

rb = {
  ROCKBOX_DIR = "/.rockbox", HZ = 100,
  current_tick = function() tick = tick + 1 return tick end,
  yield = function() YC = (YC or 0) + 1; measure() end,
  splash = function(t, s)
    if not s:find("%d+%%$") then print("[splash] " .. s); SPLASHES[#SPLASHES + 1] = s end
  end,
  get_plugin_action = function() return 0 end,
  actions = { PLA_CANCEL = 1, PLA_EXIT = 2 },
  do_menu = function(title, items, sel)
    measure()
    print("[menu] " .. title)
    local copy = {}
    for i = 1, #items do print("   " .. (i - 1) .. ": " .. items[i]); copy[i] = items[i] end
    MENUS[#MENUS + 1] = { title = title, items = copy }
    ci = ci + 1
    local c = choices[ci]
    if c == nil then return -1 end
    print("   -> pick " .. c)
    return c
  end,
  audio_current_track = function(off, size)
    if off ~= 400 then return "garbage\0" end
    return (H.np or "") .. string.rep("\0", 8)
  end,
}

if H.playlist ~= "none" then
  rb.playlist = function(op, a1, a2, a3, a4)
    if op == "create" then
      PLAYLOG[#PLAYLOG + 1] = "create " .. a1 .. " " .. a2
      if H.playlist == "fail" then error("playlist_create failed") end
      pl_amount = 0
      if H.playlist == "load" then              -- playlist_create() loads the file
        local f = io.open(a1 .. "/" .. a2, "r")
        if f then
          for line in f:lines() do
            if line ~= "" and line:sub(1, 1) ~= "#" then pl_amount = pl_amount + 1 end
          end
          f:close()
        end
      end
      return 0
    elseif op == "amount" then return pl_amount
    elseif op == "insert_track" then
      PLAYLOG[#PLAYLOG + 1] = "insert " .. a1 .. " " .. tostring(a2)
      local f = io.open(a1, "rb")               -- the file must exist on the "drive"
      if f then f:close(); pl_amount = pl_amount + 1 end
      return 0
    elseif op == "sync" then PLAYLOG[#PLAYLOG + 1] = "sync" return 0
    elseif op == "start" then
      PLAYLOG[#PLAYLOG + 1] = "start " .. a1 .. " " .. a2 .. " " .. a3
      return 0
    end
    error("unexpected rb.playlist op " .. tostring(op))
  end
end

package.preload["rbsettings"] = function() print("[load] rbsettings") return true end
package.preload["settings"] = function()
  print("[load] settings")
  rb.metadata = { mp3_entry = { path = "0x190, 260, str" } }
  return true
end

local chunk = assert(loadfile(H.script))
chunk()
measure()
print("[peak lua heap] " .. PEAK .. " KB")
for i = 1, #PLAYLOG do print("[playlist] " .. PLAYLOG[i]) end
