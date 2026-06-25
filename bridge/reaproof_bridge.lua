--[[ ReaProof in-REAPER bridge -------------------------------------------------
Runs inside REAPER on the main thread via a cooperative `defer` loop. Exposes a
local JSON-RPC endpoint over an *atomic file queue* (DECISIONS D11: REAPER's
embedded Lua has no sockets; the doctrine §6.1 sanctions a file/pipe queue).

Protocol (run dir = <resource>/_reaproof):
  request  : cmd/in/<seq>.lua   raw Lua chunk; its return value is the result
  response : cmd/out/<seq>.json {"id":seq,"ok":bool,"result":<json>|"error":<str>}
  heartbeat: heartbeat.json     {"tick":N,"t":<time>,"alive":true}  (every tick)
  ready    : ready.json         written once after init (env snapshot)

All writes are atomic (write <name>.tmp then os.rename). The bridge only reads
complete *.lua (client renames in), the client only reads complete *.json. Every
serviced command is pcall-wrapped so the loop can never die. This file is deployed
into the isolated profile as Scripts/__startup.lua by the provisioner.
----------------------------------------------------------------------------]]

local RES = reaper.GetResourcePath()
-- absolute-first boot marker (direct IO, no reaper.* dir calls) to prove __startup runs
do local bf = io.open(RES .. "/_bridge_boot.txt", "a")
   if bf then bf:write("boot " .. tostring(RES) .. " lua=" .. tostring(_VERSION) .. "\n"); bf:close() end end
local RUN = RES .. "/_reaproof"
local IN  = RUN .. "/cmd/in"
local OUT = RUN .. "/cmd/out"
local HB  = RUN .. "/heartbeat.json"
local READY = RUN .. "/ready.json"
local LOG = RUN .. "/bridge.log"

reaper.RecursiveCreateDirectory(IN, 0)
reaper.RecursiveCreateDirectory(OUT, 0)

local function logmsg(s)
  local f = io.open(LOG, "a"); if f then f:write(string.format("%.3f %s\n", reaper.time_precise(), s)); f:close() end
end

-- Heartbeat the very first thing, before anything that could error, so the
-- supervisor sees liveness and the log shows exactly how far init got.
logmsg("bridge __startup entered; RES=" .. tostring(RES) .. " lua=" .. tostring(_VERSION))

-- ---- minimal JSON encoder (no external deps) -------------------------------
local json = {}
local _escmap = { ['"']='\\"', ['\\']='\\\\', ['\n']='\\n', ['\r']='\\r', ['\t']='\\t', ['\b']='\\b', ['\f']='\\f' }
local function esc(s)
  -- %c (control chars) + backslash + quote; avoids %z which Lua 5.4 removed.
  return (s:gsub('[%c\\"]', function(c)
    return _escmap[c] or string.format('\\u%04x', string.byte(c))
  end))
end
local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" or k % 1 ~= 0 or k < 1 then return false end
    if k > n then n = k end
  end
  for i = 1, n do if t[i] == nil then return false end end
  return true, n
end
function json.encode(v)
  local tv = type(v)
  if v == nil then return "null"
  elseif tv == "boolean" then return v and "true" or "false"
  elseif tv == "number" then
    if v ~= v then return "null" end          -- NaN  (non-finite -> null; analysers detect pathology in audio path)
    if v == math.huge or v == -math.huge then return "null" end
    if math.type and math.type(v) == "integer" then return string.format("%d", v) end
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
    return string.format("%.17g", v)
  elseif tv == "string" then return '"' .. esc(v) .. '"'
  elseif tv == "table" then
    local arr, n = is_array(v)
    if arr then
      local parts = {}
      for i = 1, n do parts[i] = json.encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, val in pairs(v) do
        if type(k) == "string" or type(k) == "number" then
          parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. json.encode(val)
        end
      end
      return "{" .. table.concat(parts, ",") .. "}"
    end
  else
    return '"<' .. tv .. '>"'   -- functions/userdata: not serialisable, mark explicitly
  end
end

-- ---- atomic write ----------------------------------------------------------
local function atomic_write(path, data)
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "wb")
  if not f then return false end
  f:write(data); f:close()
  return os.rename(tmp, path)
end

-- ---- command servicing -----------------------------------------------------
local function service_one(seq)
  local inpath = IN .. "/" .. seq .. ".lua"
  local outpath = OUT .. "/" .. seq .. ".json"
  local f = io.open(inpath, "rb")
  if not f then return end
  local src = f:read("*a"); f:close()
  local resp
  local chunk, cerr = load(src, "=reaproof_cmd:" .. seq, "t")
  if not chunk then
    resp = { id = tonumber(seq) or seq, ok = false, error = "compile: " .. tostring(cerr) }
  else
    local ok, res = pcall(chunk)
    if ok then resp = { id = tonumber(seq) or seq, ok = true, result = res }
    else resp = { id = tonumber(seq) or seq, ok = false, error = tostring(res) } end
  end
  atomic_write(outpath, json.encode(resp))
end

-- discover request seqs lacking a response, ascending
local function pending_seqs()
  local seqs = {}
  local i = 0
  while true do
    local fn = reaper.EnumerateFiles(IN, i)
    if not fn then break end
    i = i + 1
    local seq = fn:match("^(%d+)%.lua$")
    if seq then
      local outf = io.open(OUT .. "/" .. seq .. ".json", "rb")
      if outf then outf:close() else seqs[#seqs + 1] = seq end
    end
  end
  table.sort(seqs, function(a, b) return tonumber(a) < tonumber(b) end)
  return seqs
end

-- ---- ready snapshot --------------------------------------------------------
local function env_snapshot()
  local hwnd = reaper.GetMainHwnd()
  return {
    app_version   = reaper.GetAppVersion(),
    resource_path = RES,
    run_dir       = RUN,
    main_hwnd_ok  = hwnd ~= nil,
    has_js_api    = reaper.JS_ReaScriptAPI_Version ~= nil,
    js_api_version= reaper.JS_ReaScriptAPI_Version and reaper.JS_ReaScriptAPI_Version() or 0,
    has_sws       = reaper.SNM_GetIntConfigVar ~= nil,
    has_imgui     = reaper.ImGui_CreateContext ~= nil,
    lua_version   = _VERSION,
  }
end

-- Announce readiness. Wrap in pcall so any encode/IO fault is logged (not silently
-- swallowed by __startup), and write a plain fallback ready file so the supervisor
-- can still proceed and surface the diagnostic.
local okr, err = pcall(function()
  local snap = env_snapshot()
  atomic_write(READY, json.encode({ ready = true, env = snap, t = reaper.time_precise() }))
  logmsg("bridge ready: " .. json.encode(snap))
end)
if not okr then
  logmsg("READY ERROR: " .. tostring(err))
  -- minimal hand-built ready so the client doesn't time out on a bridge bug
  local f = io.open(READY .. ".tmp", "wb")
  if f then f:write('{"ready":true,"env":{"degraded":true},"error":' ..
    '"' .. tostring(err):gsub('"', "'") .. '"}'); f:close(); os.rename(READY .. ".tmp", READY) end
end

-- ---- defer loop ------------------------------------------------------------
local tick = 0
local function loop()
  tick = tick + 1
  -- heartbeat first, so a stall is always visible to the supervisor
  atomic_write(HB, string.format('{"tick":%d,"t":%.3f,"alive":true}', tick, reaper.time_precise()))
  -- service a bounded batch per tick to keep the UI responsive (§6.1 constraint)
  local ok, seqs = pcall(pending_seqs)
  if ok and seqs then
    local n = 0
    for _, seq in ipairs(seqs) do
      pcall(service_one, seq)
      n = n + 1
      if n >= 16 then break end
    end
  end
  reaper.defer(loop)
end
reaper.defer(loop)
