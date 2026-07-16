-- @description ReaProof reference script (error in the DEFERRED phase)
-- @version 1.0
local n = 0
local function tick()
  n = n + 1
  if n >= 3 then error("deferred boom (deliberate)") end
  reaper.defer(tick)
end
reaper.defer(tick)
