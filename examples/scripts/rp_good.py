# @description ReaProof reference: clean Python ReaScript
# @version 1.0
# GOOD reference subject for the Python ReaScript battery: registers as a
# real action, touches the RPR_ API, changes nothing, leaves nothing behind.
# (No __file__ here on purpose - REAPER's embedded interpreter does not
# define it for action scripts; that live finding shaped the battery.)
n = RPR_GetProjectStateChangeCount(0)
