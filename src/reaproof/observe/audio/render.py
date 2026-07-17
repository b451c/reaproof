"""Offline audio render pipeline (§8.1) — the deterministic DSP ground truth.

Builds a real project (track + known input media + the plugin-under-test) through
the bridge, bakes render settings into the .rpp, then renders it with
``reaper -renderproject`` in a separate process (sample-deterministic) and returns
the output samples + a provenance manifest. Asserting on these samples is asserting
on the observable EFFECT, never on the value we set (§1.1).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from reaproof import paths
from reaproof.control.bridge_client import BridgeClient
from reaproof.determinism import DeterminismLock, host_descriptor, sha256_file, subprocess_env
from reaproof.provision.base import get_provisioner
from reaproof.runner.session import ReaperSession

#: format -> controlled-scan subdir, for compiled subjects installed before launch
_PLUGIN_SUBDIR = {".clap": "CLAP", ".vst3": "VST3", ".vst": "VST",
                  ".dylib": "VST", ".lv2": "LV2"}


@dataclass
class RenderResult:
    samples: np.ndarray          # (n_samples, channels), float64
    sample_rate: int
    output_path: Path
    rpp_path: Path
    provenance: dict[str, Any] = field(default_factory=dict)


def _offline_render(rpp: Path, ini: Path, out: Path, *, timeout: float = 120.0,
                    clap_path: Path | None = None) -> None:
    """Render a templated .rpp headlessly and wait for the output to appear.

    ``clap_path`` (when given) is exported as ``CLAP_PATH`` so the separate render
    process discovers the controlled subject CLAP exactly as the bridge launch did.
    macOS goes through ``open -n`` (app bundle); Linux and Windows exec the
    pinned binary directly (both live-verified on their VM legs).
    """
    import sys
    if out.exists():
        out.unlink()
    log = rpp.with_suffix(".renderlog.txt")
    extra = {"CLAP_PATH": str(clap_path)} if clap_path else None
    args = ["-renderproject", str(rpp), "-nosplash", "-ignoreerrors",
            "-splashlog", str(log), "-cfgfile", str(ini)]
    if sys.platform == "darwin":
        subprocess.run(["open", "-n", str(paths.REAPER_APP), "--args", *args],
                       check=True, env=subprocess_env(extra), timeout=30)
    elif sys.platform == "win32":
        from reaproof.provision.windows import REAPER_EXE_WINDOWS
        subprocess.Popen([str(REAPER_EXE_WINDOWS), *args],
                         env=subprocess_env(extra),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        from reaproof.provision.linux import REAPER_BIN_LINUX
        subprocess.Popen([str(REAPER_BIN_LINUX), *args],
                         env=subprocess_env(extra),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if out.exists() and out.stat().st_size > 1024:
                # let the file settle (size stable across two polls)
                s1 = out.stat().st_size
                time.sleep(0.3)
                if out.exists() and out.stat().st_size == s1:
                    return
            time.sleep(0.3)
        raise TimeoutError(f"render produced no output within {timeout}s: {out}")
    finally:
        _reap_render(ini)


def _reap_render(ini: Path) -> None:
    """Kill the straggler render process bound to our unique cfgfile."""
    import sys
    if sys.platform == "win32":
        safe = str(ini).replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='reaper.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{safe}*' }} | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=30)
    else:
        subprocess.run(["pkill", "-9", "-f", f"cfgfile {ini}"],
                       capture_output=True)


#: the deterministic reference note clip for instrument subjects:
#: (start_s, dur_s, midi_pitch, velocity) — a mid-register note, a low note and
#: a two-note chord, inside the first second
REFERENCE_NOTES: tuple[tuple[float, float, int, int], ...] = (
    (0.05, 0.40, 60, 100),
    (0.50, 0.35, 48, 90),
    (0.50, 0.35, 64, 70),
)


def _midi_notes_lua(notes, item_len: float) -> str:
    """Lua that creates a MIDI item on ``tr`` carrying the given notes."""
    ins = "\n".join(
        f"  reaper.MIDI_InsertNote(take, false, false, "
        f"reaper.MIDI_GetPPQPosFromProjTime(take, {s:.6f}), "
        f"reaper.MIDI_GetPPQPosFromProjTime(take, {s + d:.6f}), "
        f"0, {int(pitch)}, {int(vel)}, true)"
        for s, d, pitch, vel in notes)
    return (f"do\n  local it = reaper.CreateNewMIDIItemInProj(tr, 0.0, "
            f"{item_len:.6f}, false)\n"
            f"  local take = reaper.GetActiveTake(it)\n{ins}\n"
            f"  reaper.MIDI_Sort(take)\nend")


def render_through_jsfx(
    fx_name: str,
    *,
    jsfx_files: list[Path],
    params: dict[int, float] | None = None,
    input_signal: np.ndarray,
    sample_rate: int = 48000,
    channels: int = 2,
    lock: DeterminismLock | None = None,
    extra_setup: str = "",
    name: str = "render",
    render_timeout: float = 120.0,
    midi_notes=None,
) -> RenderResult:
    """Render ``input_signal`` through a JSFX with the given parameter values.

    ``extra_setup`` is a Lua snippet run after the FX is added (with ``tr`` and ``fx``
    in scope) — e.g. to attach a parameter automation envelope before rendering.
    ``render_timeout`` bounds the offline render; a stalled audio thread (runaway
    JSFX loop) surfaces as the TimeoutError instead of wedging the suite.
    ``midi_notes`` (list of ``(start_s, dur_s, pitch, velocity)``) additionally
    places a MIDI item on the track — the deterministic note feed for
    instrument subjects (mechanics live-verified: bit-identical re-renders).
    """
    lock = lock or DeterminismLock(sample_rate=sample_rate)
    params = params or {}
    if midi_notes:
        item_len = len(np.asarray(input_signal)) / float(sample_rate)
        extra_setup = _midi_notes_lua(midi_notes, item_len) + "\n" + extra_setup
    s = ReaperSession(name, jsfx=jsfx_files, lock=lock)
    s.start()
    prof = s.profile
    inp = prof.root / "input.wav"
    out = prof.root / "render_out.wav"
    rpp = prof.root / "render.rpp"
    sf.write(inp, np.asarray(input_signal, dtype=np.float32), sample_rate, subtype="FLOAT")

    set_params = "\n".join(
        f"reaper.TrackFX_SetParam(tr, fx, {idx}, {val})" for idx, val in params.items()
    )
    # track + master must carry the channel count or spl2+ have nowhere to go
    # (both default to stereo; REAPER only accepts even counts)
    nch = channels + (channels % 2)
    set_nchan = (
        f"reaper.SetMediaTrackInfo_Value(tr, 'I_NCHAN', {nch})\n"
        f"reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), 'I_NCHAN', {nch})"
    ) if channels > 2 else ""
    try:
        built = s.eval(f"""
        while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
        reaper.InsertTrackAtIndex(0, false)
        local tr = reaper.GetTrack(0,0)
        reaper.SetOnlyTrackSelected(tr)
        reaper.SetEditCurPos(0, false, false)
        reaper.InsertMedia([[{inp}]], 0)
        {set_nchan}
        local fx = reaper.TrackFX_AddByName(tr, '{fx_name}', false, -1)
        if fx < 0 then return {{err='fx not found: {fx_name}'}} end
        {set_params}
        {extra_setup}
        reaper.GetSetProjectInfo(0, 'RENDER_BOUNDSFLAG', 1, true)   -- entire project
        reaper.GetSetProjectInfo(0, 'RENDER_SRATE', {sample_rate}, true)
        reaper.GetSetProjectInfo(0, 'RENDER_CHANNELS', {channels}, true)
        reaper.GetSetProjectInfo_String(0, 'RENDER_FILE', [[{out}]], true)
        reaper.GetSetProjectInfo_String(0, 'RENDER_PATTERN', '', true)
        reaper.Main_SaveProjectEx(0, [[{rpp}]], 0)
        return {{fx=fx, items=reaper.CountMediaItems(0)}}
        """)
    finally:
        # a bridge error must not orphan the session's REAPER — the plugin
        # path always had this finally, the JSFX path leaked its instance on
        # an eval raise (user-spotted as a stuck loading screen)
        s.stop()
    if isinstance(built, dict) and built.get("err"):
        raise RuntimeError(f"render build failed: {built['err']}")

    _offline_render(rpp, prof.ini_path, out, timeout=render_timeout)
    data, osr = sf.read(out, dtype="float64", always_2d=True)

    provenance = {
        "host": host_descriptor(),
        "reaper_build": paths.REAPER_BUILD,
        "fx_name": fx_name,
        "params": params,
        "sample_rate": osr,
        "channels": data.shape[1],
        "lock": lock.as_dict(),
        "input_sha256": sha256_file(inp),
        "output_sha256": sha256_file(out),
        "jsfx": [j[1] if isinstance(j, tuple) else Path(j).name
                 for j in jsfx_files],
        "rpp": str(rpp),
        "output_path": str(out),
    }
    return RenderResult(samples=data, sample_rate=osr, output_path=out,
                        rpp_path=rpp, provenance=provenance)


def render_through_plugin(
    fx_name: str,
    *,
    plugin_files: list[Path],
    params: dict[int, float] | None = None,
    param_envelopes: dict[int, float] | None = None,
    input_signal: np.ndarray,
    sample_rate: int = 48000,
    channels: int = 2,
    lock: DeterminismLock | None = None,
    extra_setup: str = "",
    name: str = "render",
    unique_suffix: str = "_RP",
) -> RenderResult:
    """Render ``input_signal`` through a COMPILED plugin (CLAP/VST3) under test.

    Mirrors :func:`render_through_jsfx` but installs a compiled bundle into the
    controlled scan dir *before* launch (REAPER scans plugins only at startup),
    under a unique bundle name so it is freshly scanned and never collides with a
    same-named copy already installed on the host. Asserting on the returned
    samples is asserting on the observable EFFECT, never on the value we set (§1.1).

    ``param_envelopes`` maps a param index to a CONSTANT plain-unit value and pins
    it with a flat automation envelope. This is the reliable way to set a compiled
    plugin's parameter for an offline render: REAPER delivers CLAP param changes
    through the process-time event queue, so ``TrackFX_SetParam`` does NOT "stick"
    while the plugin is idle (verified on a known-good control CLAP — not a subject
    bug). An envelope is read AT render time, so the value actually takes effect.
    """
    lock = lock or DeterminismLock(sample_rate=sample_rate)
    params = params or {}
    param_envelopes = param_envelopes or {}
    dur = len(np.asarray(input_signal)) / float(sample_rate)
    env_lua = "\n".join(
        f"""
        do local _e = reaper.GetFXEnvelope(tr, fx, {idx}, true)
        reaper.DeleteEnvelopePointRange(_e, -1, 1000000)
        reaper.InsertEnvelopePoint(_e, 0.0, {val}, 0, 0, false, true)
        reaper.InsertEnvelopePoint(_e, {dur:.6f}, {val}, 0, 0, false, true)
        reaper.Envelope_SortPoints(_e) end"""
        for idx, val in param_envelopes.items()
    )
    prov = get_provisioner()
    run_id = f"{name}-{os.getpid()}-{int(time.time() * 1000)}"
    profile = prov.assemble_profile(run_id, lock)

    installed: list[str] = []
    for src in plugin_files:
        src = Path(src)
        sub = _PLUGIN_SUBDIR.get(src.suffix.lower(), "VST")
        dest = profile.plugin_dir / sub / f"{src.stem}{unique_suffix}{src.suffix}"
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
        installed.append(dest.name)

    inp = profile.root / "input.wav"
    out = profile.root / "render_out.wav"
    rpp = profile.root / "render.rpp"
    sf.write(inp, np.asarray(input_signal, dtype=np.float32), sample_rate, subtype="FLOAT")
    set_params = "\n".join(
        f"reaper.TrackFX_SetParam(tr, fx, {idx}, {val})" for idx, val in params.items()
    )

    handle = prov.launch(profile)
    bridge = BridgeClient(profile.run_dir, is_alive=lambda: prov.is_alive(handle))
    try:
        bridge.wait_ready(120.0)
        built = bridge.eval(f"""
        while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
        reaper.InsertTrackAtIndex(0, false)
        local tr = reaper.GetTrack(0,0)
        reaper.SetOnlyTrackSelected(tr)
        reaper.SetEditCurPos(0, false, false)
        reaper.InsertMedia([[{inp}]], 0)
        local fx = reaper.TrackFX_AddByName(tr, '{fx_name}', false, -1)
        if fx < 0 then return {{err='fx not found: {fx_name}'}} end
        {set_params}
        {env_lua}
        {extra_setup}
        reaper.GetSetProjectInfo(0, 'RENDER_BOUNDSFLAG', 1, true)   -- entire project
        reaper.GetSetProjectInfo(0, 'RENDER_SRATE', {sample_rate}, true)
        reaper.GetSetProjectInfo(0, 'RENDER_CHANNELS', {channels}, true)
        reaper.GetSetProjectInfo_String(0, 'RENDER_FILE', [[{out}]], true)
        reaper.GetSetProjectInfo_String(0, 'RENDER_PATTERN', '', true)
        reaper.Main_SaveProjectEx(0, [[{rpp}]], 0)
        local _, ident = reaper.TrackFX_GetNamedConfigParm(tr, fx, 'fx_ident')
        return {{fx=fx, items=reaper.CountMediaItems(0), ident=ident}}
        """)
    finally:
        prov.terminate(handle)
    if isinstance(built, dict) and built.get("err"):
        raise RuntimeError(f"render build failed: {built['err']}")

    _offline_render(rpp, profile.ini_path, out, clap_path=profile.plugin_dir / "CLAP")
    data, osr = sf.read(out, dtype="float64", always_2d=True)

    provenance = {
        "host": host_descriptor(),
        "reaper_build": paths.REAPER_BUILD,
        "fx_name": fx_name,
        "fx_ident": (built or {}).get("ident") if isinstance(built, dict) else None,
        "param_envelopes": param_envelopes,
        "params": params,
        "sample_rate": osr,
        "channels": data.shape[1],
        "lock": lock.as_dict(),
        "input_sha256": sha256_file(inp),
        "output_sha256": sha256_file(out),
        "plugins": installed,
        "rpp": str(rpp),
        "output_path": str(out),
    }
    return RenderResult(samples=data, sample_rate=osr, output_path=out,
                        rpp_path=rpp, provenance=provenance)
