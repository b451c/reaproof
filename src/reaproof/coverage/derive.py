"""Derive a plugin's parameter list (§11.1) the universal way — through the host, so
it works for JSFX / VST2 / VST3 / CLAP / AU alike (a superset of what `clap-info` gives
for CLAP only). The coverage report checks the taxonomy against this derived list.
"""
from __future__ import annotations

from typing import Any


def derive_params(session, fx_index: int = 0) -> list[dict[str, Any]]:
    """Return [{index, name, min, max, default, formatted}] for the FX at ``fx_index``
    on the first track, read through the host API."""
    return session.eval(f"""
    local tr = reaper.GetTrack(0,0)
    if not tr then return {{}} end
    local fx = {fx_index}
    local n = reaper.TrackFX_GetNumParams(tr, fx)
    local out = {{}}
    for i = 0, n-1 do
      local _, name = reaper.TrackFX_GetParamName(tr, fx, i, '')
      local cur, mn, mx = reaper.TrackFX_GetParam(tr, fx, i)
      local _, ftext = reaper.TrackFX_GetFormattedParamValue(tr, fx, i, '')
      out[#out+1] = {{ index=i, name=name, min=mn, max=mx, default=cur, formatted=ftext }}
    end
    return out
    """)


def clap_info_params(plugin_path) -> list[dict[str, Any]]:
    """Optional CLAP-specific descriptor via clap-info, IF the binary is provisioned.
    The bridge-based ``derive_params`` is preferred (universal); this exists for the
    full CLAP descriptor/ports JSON when needed."""
    import json
    import shutil
    import subprocess

    exe = shutil.which("clap-info")
    if not exe:
        raise FileNotFoundError("clap-info not provisioned; use derive_params (universal)")
    out = subprocess.run([exe, "--params", str(plugin_path)], capture_output=True,
                         text=True, timeout=60).stdout
    try:
        return json.loads(out).get("params", [])
    except json.JSONDecodeError:
        return []
