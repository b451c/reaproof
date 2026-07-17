-- @description ReaProof reference: leaks shared gmem
-- @version 1.0
-- Deliberately-leaky reference subject for the gmem hygiene differ (forum
-- ask (b), catalog item 8): attaches a named gmem namespace and writes a
-- value that outlives the script. gmem is invisible to the project/ExtState/
-- window censuses - only the namespace-aware differ can see it.
reaper.gmem_attach("RPGmemLeak")
reaper.gmem_write(3, 7.5)
