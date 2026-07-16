-- @description ReaProof reference script (state-leaking subject)
-- @version 1.0
reaper.InsertTrackAtIndex(0, false)               -- leaks a track
reaper.SetExtState("rp_leaky", "mark", "1", true) -- leaks persistent ExtState
