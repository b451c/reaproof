"""Audio Unit battery — ``reaproof test MyPlugin.component`` (macOS).

AU discovery is SYSTEM-WIDE (Apple's component manager); a profile cannot pin
its own copy the way VST/CLAP/LV2 scan dirs can. The battery is therefore
*semi-hermetic* and says so in its results: if the subject's component codes
are already registered, the REGISTERED instance is what REAPER loads; if not,
the subject is installed under ``~/Library/Audio/Plug-Ins/Components`` with a
``_RP`` suffix for the run and removed afterwards.

Verified mechanics (live-probed): ``plistlib`` reads the bundle's
``AudioComponents`` array (name "Manu: Name", type/subtype/manufacturer
codes); ``auval -v <type> <subtype> <manu>`` is the conformance validator
(exit 0 + "AU VALIDATION SUCCEEDED"); a hermetic profile scans system AUs at
launch and ``TrackFX_AddByName('AU:<Name>')`` instantiates them; renders ride
the same offline pipeline as every other battery (the FX is added by name —
nothing needs installing into the profile).
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_jsfx
from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import _emit, _mut_determinism, _mut_pathology
from reaproof.runner.session import ReaperSession

#: track-insertable AU types (effects, instruments, music effects)
_INSERTABLE = {"aufx", "aumu", "aumf"}
#: types auval meaningfully validates as plugins (adds generators); output
#: units / converters / codecs in mixed bundles are not plugin subjects
_VALIDATABLE = _INSERTABLE | {"augn"}


@dataclass
class AuTestOptions:
    max_components: int = 4        # auval cap for multi-AU bundles (drop LOGGED)
    signals: bool = True           # render + determinism stage
    sample_rate: int = 48000
    render_timeout: float = 120.0


def read_components(component: Path) -> list[dict]:
    """The bundle's AudioComponents entries (name/type/subtype/manufacturer)."""
    with open(component / "Contents" / "Info.plist", "rb") as f:
        plist = plistlib.load(f)
    out = []
    for c in plist.get("AudioComponents", []):
        if all(k in c for k in ("name", "type", "subtype", "manufacturer")):
            out.append({k: c[k] for k in ("name", "type", "subtype", "manufacturer")})
    return out


def _auval(entry: dict, *, timeout: float = 180.0) -> tuple[bool, str]:
    r = subprocess.run(
        ["auval", "-v", entry["type"], entry["subtype"], entry["manufacturer"]],
        capture_output=True, text=True, timeout=timeout)
    ok = r.returncode == 0 and "AU VALIDATION SUCCEEDED" in (r.stdout or "")
    tail = (r.stdout or r.stderr).strip().splitlines()
    return ok, (tail[-2].strip() if len(tail) >= 2 else "")


def run_au_battery(component: Path, out_dir: Path | None = None,
                   opts: AuTestOptions | None = None, *, log=print) -> ResultSet:
    component = Path(component).resolve()
    opts = opts or AuTestOptions()
    rs = ResultSet()

    def add(name, status, **kw):
        rs.results.append(TestResult(name=name, status=status, **kw))
        log(f"  [{status.upper():9}] {name}"
            + (f" — {kw.get('message')}" if kw.get("message") else ""))

    t0 = time.monotonic()
    # 1) manifest ------------------------------------------------------------
    log("component manifest…")
    try:
        entries = read_components(component)
    except Exception as e:  # noqa: BLE001
        add("static: AudioComponents manifest parses", "failed",
            message=str(e)[:200])
        _emit(rs, out_dir, component)
        return rs
    if not entries:
        add("static: AudioComponents manifest parses", "failed",
            message="no AudioComponents entries — nothing the component "
                    "manager could register")
        _emit(rs, out_dir, component)
        return rs
    add("static: AudioComponents manifest parses", "passed",
        message=f"{len(entries)} component(s): "
                f"{[e['name'] for e in entries][:4]}")

    # 2) registration (semi-hermetic by nature — named, never silent) --------
    target_probe = next((e for e in entries if e["type"] in _INSERTABLE),
                        entries[0])
    try:
        first_ok, _ = _auval(target_probe)
    except subprocess.TimeoutExpired:
        # a wedged probe is a finding, not a crash of the battery
        first_ok = False
    scanned_roots = ("/System/Library/Components",
                     "/Library/Audio/Plug-Ins/Components",
                     str(Path.home() / "Library/Audio/Plug-Ins/Components"))
    in_scanned = any(str(component).startswith(r) for r in scanned_roots)
    installed_copy: Path | None = None
    if first_ok:
        add("install: component registered with the system", "passed",
            message="codes already registered — testing the REGISTERED "
                    "instance (macOS AU discovery is system-wide; a profile "
                    "cannot pin its own copy)")
    elif in_scanned:
        # already where the component manager scans — an install copy would
        # only collide; the per-entry auval verdicts below carry the news
        add("install: component registered with the system", "passed",
            message="component lives in a scanned location; conformance "
                    "verdicts below speak for its registration")
    else:
        dest = (Path.home() / "Library" / "Audio" / "Plug-Ins" / "Components"
                / f"{component.stem}_RP{component.suffix}")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(component, dest, dirs_exist_ok=True)
            installed_copy = dest
            add("install: component registered with the system", "passed",
                message=f"installed for this run at {dest.name} (user "
                        "components dir — semi-hermetic; removed afterwards)")
        except OSError as e:
            add("install: component registered with the system", "failed",
                message=f"could not install: {str(e)[:160]}")
            _emit(rs, out_dir, component)
            return rs

    try:
        # 3) auval conformance per PLUGIN component (capped, drop logged) ----
        log("auval conformance…")
        validatable = [e for e in entries if e["type"] in _VALIDATABLE]
        others = len(entries) - len(validatable)
        if others:
            add("scope: non-plugin components", "skipped",
                message=f"{others} entr(ies) of non-plugin types "
                        f"({sorted({e['type'] for e in entries if e['type'] not in _VALIDATABLE})[:4]}) "
                        "— auval does not validate those as plugins")
        run_list = validatable[: opts.max_components]
        if len(run_list) < len(validatable):
            add("budget: component cap", "skipped",
                message=f"validated {len(run_list)}/{len(validatable)} plugin "
                        f"components (--max-components {opts.max_components}); "
                        "the rest are NOT covered this run")
        for e in run_list:
            try:
                ok, tail = _auval(e)
            except subprocess.TimeoutExpired:
                add(f"validator: auval {e['name']}", "failed",
                    message="auval timed out (wedged component?)")
                continue
            add(f"validator: auval {e['name']}", "passed" if ok else "failed",
                message=tail)

        # 4) load + params in a hermetic-profile REAPER (target picked from
        #    the FULL entry list — the auval cap must not hide a loadable FX)
        target = next((e for e in entries if e["type"] in _INSERTABLE), None)
        if target is None:
            add("load: instantiates in REAPER", "skipped",
                message="no track-insertable component (aufx/aumu/aumf) in "
                        "the validated set")
            _emit(rs, out_dir, component)
            return rs
        short = target["name"].split(": ", 1)[-1]
        fx_add = f"AU:{short}".replace("'", "\\'")
        log(f"load checkpoint ({fx_add})…")
        with ReaperSession(f"au-{component.stem}") as s:
            res = s.eval(f"""
            while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
            reaper.InsertTrackAtIndex(0, false)
            local tr = reaper.GetTrack(0,0)
            local fx = reaper.TrackFX_AddByName(tr, '{fx_add}', false, -1)
            if fx < 0 then return {{fx = fx}} end
            local _, ident = reaper.TrackFX_GetNamedConfigParm(tr, fx, 'fx_ident')
            return {{fx = fx, ident = ident,
                    nparams = reaper.TrackFX_GetNumParams(tr, fx)}}
            """, timeout=90)
        if not isinstance(res, dict) or res.get("fx", -1) < 0:
            add("load: instantiates in REAPER", "failed",
                message=f"TrackFX_AddByName('{fx_add}') found nothing — the "
                        "component registered but REAPER's AU scan does not "
                        "expose it")
            _emit(rs, out_dir, component)
            return rs
        add("load: instantiates in REAPER", "passed",
            message=f"ident={res.get('ident')} nparams={res.get('nparams')}")

        # 5) render integrity + determinism (effects; instruments need MIDI —
        #    the note-feed generalisation for AU rides the next leg) ---------
        if opts.signals and target["type"] == "aufx":
            log("audio integrity + determinism…")
            sr = opts.sample_rate
            sig = S.sine(1000.0, dbfs=-12.0, seconds=1.0, sr=sr)
            try:
                r1 = render_through_jsfx(fx_add, jsfx_files=[], input_signal=sig,
                                         sample_rate=sr, name=f"au-{short}-sine",
                                         render_timeout=opts.render_timeout)
                _mut_pathology(r1.samples, False, "audio: sine_1k is pathology-free",
                               add, artifacts=[str(r1.output_path)])
                r2 = render_through_jsfx(fx_add, jsfx_files=[], input_signal=sig,
                                         sample_rate=sr, name=f"au-{short}-det",
                                         render_timeout=opts.render_timeout)
                _mut_determinism(r1.samples, r2.samples,
                                 "determinism: re-render is bit-identical", add)
            except Exception as e:  # noqa: BLE001
                add("audio: sine_1k renders without crash/hang", "failed",
                    message=str(e)[:200])
        elif opts.signals:
            add("audio: render battery", "skipped",
                message=f"{target['type']} component — instruments need the "
                        "MIDI feed generalised to AU (next leg)")
    finally:
        if installed_copy is not None:
            shutil.rmtree(installed_copy, ignore_errors=True)

    rs.results[0].duration_s = time.monotonic() - t0
    _emit(rs, out_dir, component)
    return rs
