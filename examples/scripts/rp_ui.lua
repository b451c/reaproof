-- @description ReaProof reference script (UI subject: gfx window)
-- @version 1.0
gfx.init("RP UI Subject", 240, 100, 0, 80, 80)
local function tick()
  gfx.x, gfx.y = 8, 8
  gfx.drawstr("ReaProof UI subject")
  gfx.update()
  if gfx.getchar() >= 0 then reaper.defer(tick) end
end
reaper.defer(tick)
