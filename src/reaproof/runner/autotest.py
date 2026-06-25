"""Universal, zero-config plugin tester — the turnkey ``reaproof test <plugin>`` flow.

Point it at a compiled audio plugin (``.clap`` / ``.vst3`` / ``.vst``) and it runs a
real *no-false-results* QA battery with **no user-written test code**:

  1. **Validator** (clap-validator / pluginval), crash-isolated, if the tool is present.
  2. **Load checkpoint** — the plugin loads in a hermetic pinned REAPER and instantiates.
  3. **Parameter discovery** — every parameter (name/range/default) read through the host.
  4. **Audio integrity** — standard signals (silence/sine/noise/impulse/sweep/full-scale)
     rendered through the plugin must be free of NaN/Inf/denormal (and clicks/DC on tones),
     and a re-render must be bit-identical (determinism).
  5. **Parameter robustness** — each parameter is swept across its whole range (a staircase
     automation envelope) feeding noise; the output must stay pathology-free at every setting
     (catches NaN-at-extreme-settings, blow-ups, instability) without knowing the semantics.

Every pathology/determinism assertion is mutation-verified (§1.3) so a green result is
trustworthy. For *semantic* correctness ("gain is exactly -6 dB", "the knob shows X") you
still write a small spec with the authoring API — but that is optional; this battery is the
universal floor that needs nothing but the plugin.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from reaproof import paths
from reaproof.control.bridge_client import BridgeClient
from reaproof.determinism import DeterminismLock, subprocess_env
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_plugin
from reaproof.provision.base import get_provisioner
from reaproof.report.results import ResultSet, TestResult, to_html, to_json, to_junit
from reaproof import mutation as M

_FMT = {".clap": "clap", ".vst3": "vst3", ".vst": "vst"}


def detect_format(plugin: Path) -> str:
    fmt = _FMT.get(plugin.suffix.lower())
    if not fmt:
        raise ValueError(
            f"unsupported plugin format {plugin.suffix!r} (expected .clap/.vst3/.vst). "
            f"REAPER extensions (reaper_*.dylib) need a custom test, not the universal battery."
        )
    return fmt


@dataclass
class DiscoveredFX:
    name: str           # the TrackFX_AddByName string
    ident: str
    fmt: str


@dataclass
class Discovery:
    fxs: list[DiscoveredFX] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)
    loaded: bool = False
    detail: str = ""


# ---- name discovery: read the freshly-written scan cache for our unique bundle -----
def _parse_clap_cache(text: str, bundle_basename: str) -> list[tuple[str, str]]:
    """Return [(display_name, plugin_id)] from a reaper-clap-*.ini section."""
    out: list[tuple[str, str]] = []
    in_sec = False
    for line in text.splitlines():
        if line.startswith("["):
            in_sec = line.strip() == f"[{bundle_basename}]"
            continue
        if in_sec and "=" in line and not line.startswith("_="):
            pid, _, rhs = line.partition("=")
            name = rhs.split("|", 1)[1] if "|" in rhs else rhs
            out.append((name.strip(), pid.strip()))
    return out


def _parse_vst_cache(text: str, bundle_basename: str) -> list[tuple[str, str]]:
    """Return [(display_name, '')] for a bundle from a reaper-vstplugins*.ini line."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        key, _, rhs = line.partition("=")
        if key.strip() == bundle_basename and rhs:
            disp = rhs.split(",")[-1].strip()
            if disp:
                out.append((disp, ""))
    return out


def discover(plugin: Path, *, unique_suffix: str = "_RP", timeout: float = 120.0) -> Discovery:
    """Install the plugin in a hermetic profile, launch REAPER, and read back: did it
    load, what is its TrackFX add-name, and its full parameter list."""
    fmt = detect_format(plugin)
    prov = get_provisioner()
    profile = prov.assemble_profile(f"autotest-discover-{plugin.stem}", DeterminismLock())
    sub = {"clap": "CLAP", "vst3": "VST3", "vst": "VST"}[fmt]
    bundle_name = f"{plugin.stem}{unique_suffix}{plugin.suffix}"
    dest = profile.plugin_dir / sub / bundle_name
    if plugin.is_dir():
        shutil.copytree(plugin, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(plugin, dest)

    ini = str(profile.ini_path)
    for f in (profile.run_dir / "ready.json", profile.run_dir / "heartbeat.json"):
        if f.exists():
            f.unlink()
    env = subprocess_env({"CLAP_PATH": str(profile.plugin_dir / "CLAP")})
    subprocess.run(["open", "-n", str(paths.REAPER_APP), "--args", "-newinst",
                    "-noactivate", "-nosplash", "-cfgfile", ini], check=True, env=env, timeout=30)
    from reaproof.provision.macos import _find_pid
    pid = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not pid:
        pid = _find_pid(ini)
        time.sleep(0.1)
    import os
    bridge = BridgeClient(profile.run_dir, is_alive=lambda: bool(pid) and _alive(pid))
    disc = Discovery()
    try:
        bridge.wait_ready(timeout)
        # read the scan cache our unique bundle just produced -> display name(s)
        names: list[tuple[str, str]] = []
        if fmt == "clap":
            for cache in profile.resource_dir.glob("reaper-clap-*.ini"):
                names += _parse_clap_cache(cache.read_text(errors="replace"), bundle_name)
        else:
            for cache in profile.resource_dir.glob("reaper-vstplugins*.ini"):
                names += _parse_vst_cache(cache.read_text(errors="replace"), bundle_name)
        # de-dup, keep order
        seen = set()
        for nm, pid_ in names:
            if nm not in seen:
                seen.add(nm)
                disc.fxs.append(DiscoveredFX(name=nm, ident=pid_, fmt=fmt))
        # confirm it actually instantiates + read params
        if disc.fxs:
            res = bridge.eval(_LOAD_AND_PARAMS.replace("__NAME__", disc.fxs[0].name.replace("'", "\\'")))
            disc.loaded = bool(res.get("fx", -1) >= 0)
            disc.params = res.get("params", []) or []
            disc.detail = f"fx={res.get('fx')} ident={res.get('ident')} nparams={len(disc.params)}"
        else:
            disc.detail = "plugin did not appear in the scan cache (failed to scan/validate)"
    finally:
        subprocess.run(["pkill", "-9", "-f", f"cfgfile {ini}"], capture_output=True)
    return disc


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


_LOAD_AND_PARAMS = r"""
while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
reaper.InsertTrackAtIndex(0, false)
local tr = reaper.GetTrack(0,0)
local fx = reaper.TrackFX_AddByName(tr, '__NAME__', false, -1)
local out = { fx = fx, params = {} }
if fx < 0 then return out end
local _, ident = reaper.TrackFX_GetNamedConfigParm(tr, fx, 'fx_ident')
out.ident = ident
local n = reaper.TrackFX_GetNumParams(tr, fx)
for i = 0, n-1 do
  local _, name = reaper.TrackFX_GetParamName(tr, fx, i, '')
  local cur, mn, mx = reaper.TrackFX_GetParam(tr, fx, i)
  out.params[#out.params+1] = { index=i, name=name, min=mn, max=mx, default=cur }
end
return out
"""


# ---- standard test signals (deterministic) --------------------------------
def _signals(sr: int) -> dict[str, np.ndarray]:
    return {
        "silence": S.silence(1.0, sr=sr),
        "sine_1k": S.sine(1000.0, dbfs=-12.0, seconds=1.0, sr=sr),
        "noise": S.noise(dbfs=-12.0, seconds=1.0, sr=sr),
        "impulse": S.impulse(1.0, sr=sr),
        "sweep": S.sweep(20.0, 20000.0, dbfs=-12.0, seconds=1.0, sr=sr),
        "fullscale_sine": S.sine(1000.0, dbfs=0.0, seconds=1.0, sr=sr),
    }


def _staircase_env_lua(idx: int, lo: float, hi: float, steps: int, dur: float) -> str:
    """A flat-stepped automation envelope sweeping param `idx` lo->hi across the render."""
    pts = []
    for k in range(steps):
        v = lo + (hi - lo) * k / max(1, steps - 1)
        t0 = dur * k / steps
        t1 = dur * (k + 1) / steps - 1e-4
        pts.append(f"reaper.InsertEnvelopePoint(_e,{t0:.5f},{v:.6f},0,0,false,true)")
        pts.append(f"reaper.InsertEnvelopePoint(_e,{t1:.5f},{v:.6f},0,0,false,true)")
    body = "\n".join(pts)
    return (f"do local _e=reaper.GetFXEnvelope(tr,fx,{idx},true)\n"
            f"reaper.DeleteEnvelopePointRange(_e,-1,1000000)\n{body}\n"
            f"reaper.Envelope_SortPoints(_e) end")


@dataclass
class AutotestOptions:
    sample_rate: int = 48000
    is_instrument: bool = False     # skip the silence->silence check for generators
    sweep_params: bool = True
    max_params: int = 16            # cap the per-param sweep unless `full`
    full: bool = False              # sweep ALL params, no cap


def run_autotest(plugin: Path, out_dir: Path | None = None,
                 opts: AutotestOptions | None = None, *, log=print) -> ResultSet:
    """Run the full universal battery and return a ResultSet (+ write a report bundle)."""
    plugin = Path(plugin)
    opts = opts or AutotestOptions()
    fmt = detect_format(plugin)
    sr = opts.sample_rate
    rs = ResultSet()

    def add(name, status, **kw):
        rs.results.append(TestResult(name=name, status=status, **kw))
        log(f"  [{status.upper():9}] {name}" + (f" — {kw.get('message')}" if kw.get("message") else ""))

    t0 = time.monotonic()
    # 1) validator -----------------------------------------------------------
    log("validator…")
    _validator(plugin, fmt, add, out_dir)

    # 2) load + discover -----------------------------------------------------
    log("load checkpoint + parameter discovery…")
    disc = discover(plugin)
    if not disc.loaded:
        add("load: plugin instantiates in REAPER", "failed", message=disc.detail)
        _emit(rs, out_dir, plugin)
        return rs
    fx_name = disc.fxs[0].name
    add("load: plugin instantiates in REAPER", "passed",
        message=disc.detail, provenance={"fx_name": fx_name, "params": len(disc.params)})

    # 3) audio integrity at default params -----------------------------------
    log("audio integrity (standard signals)…")
    sigs = _signals(sr)
    renders: dict[str, Any] = {}
    for sname, sig in sigs.items():
        try:
            r = render_through_plugin(fx_name, plugin_files=[plugin], input_signal=sig,
                                      sample_rate=sr, name=f"at-{plugin.stem}-{sname}")
            renders[sname] = r
        except Exception as e:  # crash/hang/timeout in the render = hard fail (§1.7)
            add(f"audio: {sname} renders without crash/hang", "failed", message=str(e)[:200])
            continue
        # hard pathologies (NaN/Inf/denormal) always; click/DC only on tonal signals
        hard_only = sname in ("noise", "impulse", "sweep")
        dur = time.monotonic()
        _mut_pathology(r.samples, hard_only, f"audio: {sname} is pathology-free", add,
                       artifacts=[str(r.output_path)])
        if sname == "silence" and not opts.is_instrument:
            _check(lambda: _assert_silent(r.samples), f"audio: silence in -> silence out", add)

    # 4) determinism ---------------------------------------------------------
    if "sine_1k" in renders:
        log("determinism…")
        try:
            again = render_through_plugin(fx_name, plugin_files=[plugin],
                                          input_signal=sigs["sine_1k"], sample_rate=sr,
                                          name=f"at-{plugin.stem}-determ")
            _mut_determinism(renders["sine_1k"].samples, again.samples,
                             "determinism: re-render is bit-identical", add)
        except Exception as e:
            add("determinism: re-render is bit-identical", "failed", message=str(e)[:200])

    # 5) parameter robustness sweep -----------------------------------------
    if opts.sweep_params and disc.params:
        plist = disc.params if opts.full else disc.params[:opts.max_params]
        if len(plist) < len(disc.params):
            log(f"parameter robustness: sweeping {len(plist)}/{len(disc.params)} params "
                f"(use --full for all; the rest are NOT covered)")
        else:
            log(f"parameter robustness: sweeping all {len(plist)} params…")
        noise = S.noise(dbfs=-12.0, seconds=1.0, sr=sr)
        for p in plist:
            idx, pname = p["index"], p.get("name", f"param{p['index']}")
            lo, hi = float(p.get("min", 0.0)), float(p.get("max", 1.0))
            if hi == lo:
                continue
            env = _staircase_env_lua(idx, lo, hi, steps=8, dur=len(noise) / sr)
            try:
                r = render_through_plugin(fx_name, plugin_files=[plugin], input_signal=noise,
                                          sample_rate=sr, extra_setup=env,
                                          name=f"at-{plugin.stem}-sweep{idx}")
            except Exception as e:
                add(f"param sweep [{idx}] {pname}: stable across range", "failed", message=str(e)[:200])
                continue
            _mut_pathology(r.samples, True, f"param sweep [{idx}] {pname}: stable across range", add)

    rs.results[0].duration_s = time.monotonic() - t0
    _emit(rs, out_dir, plugin)
    return rs


# ---- assertion + mutation helpers -----------------------------------------
def _assert_silent(samples) -> None:
    rms = A.rms_dbfs(samples)
    assert rms <= -80.0, f"silence in produced {rms:.1f} dBFS out (self-noise / oscillation?)"


def _mut_pathology(samples, hard_only, name, add, artifacts=None):
    rep = M.mutation_check(
        samples, lambda s: A.assert_no_pathology(s, hard_only=hard_only),
        [("inject NaN", M.inject_nan())])
    if rep.vacuous and rep.clean_passed:  # clean passed but mutation didn't kill (can't happen for NaN)
        add(name, "failed", message="assertion vacuous", mutation_verified=False)
    elif not rep.clean_passed:
        add(name, "failed", message="pathology detected on clean render",
            mutation_verified=True, artifacts=artifacts or [])
    else:
        add(name, "passed", mutation_verified=True, artifacts=artifacts or [])


def _mut_determinism(a, b, name, add):
    def assert_identical(pair):
        resid = A.null_test_dbfs(pair[0], pair[1])
        assert resid <= -120.0, f"non-deterministic: residual {resid:.1f} dBFS"
    rep = M.mutation_check((a, b), assert_identical,
                           [("scale 1%", lambda p: (p[0], np.asarray(p[1]) * 0.99))])
    if not rep.clean_passed:
        add(name, "failed", message="renders differ", mutation_verified=True)
    else:
        add(name, "passed", mutation_verified=True)


def _check(fn, name, add):
    try:
        fn()
        add(name, "passed")
    except AssertionError as e:
        add(name, "failed", message=str(e)[:200])


def _validator(plugin: Path, fmt: str, add, art_dir):
    import tempfile
    art = Path(art_dir) / "validator" if art_dir else Path(tempfile.mkdtemp())
    art.mkdir(parents=True, exist_ok=True)
    try:
        if fmt == "clap" and paths.CLAP_VALIDATOR.exists():
            from reaproof.validators.clap import run_clap_validator
            r = run_clap_validator(plugin, artifacts_dir=art)
            add("validator: clap-validator conformance", "passed" if r.passed else "failed",
                message=r.summary())
        elif fmt in ("vst3", "vst") and paths.PLUGINVAL.exists():
            from reaproof.validators.pluginval import run_pluginval
            r = run_pluginval(plugin, artifacts_dir=art)
            add("validator: pluginval conformance", "passed" if r.passed else "failed",
                message=r.summary())
        else:
            add(f"validator ({fmt})", "quarantined",
                message=f"no validator provisioned for {fmt} (skipped, not a failure)")
    except Exception as e:
        add(f"validator ({fmt})", "quarantined", message=f"validator error: {str(e)[:160]}")


def _emit(rs: ResultSet, out_dir: Path | None, plugin: Path):
    if not out_dir:
        return
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.junit.xml").write_text(to_junit(rs), encoding="utf-8")
    (out_dir / "report.json").write_text(to_json(rs), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(rs), encoding="utf-8")
