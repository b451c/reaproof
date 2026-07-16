-- @description ReaProof reference script (clean subject)
-- @version 1.0
-- @author ReaProof
-- A side-effect-free script: computes, uses non-persistent ExtState as a
-- scratchpad, and cleans up after itself. The battery must stay green.
local acc = 0
for i = 1, 100 do acc = acc + i end
reaper.SetExtState("rp_good", "sum", tostring(acc), false)
local ok = reaper.GetExtState("rp_good", "sum") == "5050"
reaper.DeleteExtState("rp_good", "sum", false)
if not ok then error("arithmetic check failed") end
