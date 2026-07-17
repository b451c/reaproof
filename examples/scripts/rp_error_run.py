# @description ReaProof reference: Python ReaScript that raises
# @version 1.0
# Deliberately-broken subject: raises at run time. A Python runtime error
# surfaces as the same terminal "ReaScript Error" panel as a Lua runtime error,
# so the action stage's watchdog supervision must turn RED.
raise RuntimeError("deliberate ReaProof reference failure")
