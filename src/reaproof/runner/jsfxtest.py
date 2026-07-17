"""Universal, zero-code JSFX battery — ``reaproof test <effect.jsfx>``.

Failure-mode catalog credit: **mequaz** (forum thread t=310135, post #5) — a
Windows JSFX/Lua instrument developer who contributed the real-world list of
silent JSFX failure modes this battery turns into checks. v1 covers his items
1/6/7/9 (compile proof, slider→param mapping incl. hidden sliders, remove/
re-add factory reset, multi-samplerate renders); serialize/gmem/UI-race checks
(items 2-5, 8) are the v2 slice.

Verified mechanics this battery is built on (REAPER 7.75, live-probed):

- A JSFX that FAILS TO COMPILE still inserts via ``TrackFX_AddByName`` and
  still reports its declared sliders as live params — the param list is
  parsed independently of compilation, so a declared-vs-live diff is NOT a
  compile proof. The broken FX is a silent bit-exact passthrough (not a mute).
- The floating FX window is the only mechanical compile surface: a compiled
  JSFX shows its ``desc:`` text in a Static control; a broken one shows
  REAPER's own compiler message there instead (``@sample:17: syntax error:
  missing ) or ]`` / ``@sample:17: 'apply' undefined: '... <!> ...'``).
  js_ReaScriptAPI reads that text — the battery surfaces the real compiler
  error as the failure message.
- Sparse slider numbers map to DENSE param indices in slider-number order
  (slider1/slider5/slider40 → params 0/1/2); hidden ``-`` sliders are real
  params (label reported without the ``-``); the wrapper appends Bypass/Wet/
  Delta as the last three params; ``TrackFX_SetParam`` AND FX-param envelope
  points both speak PLAIN slider units (verified: an envelope at 24.0 equals
  slider +24 dB; 1.0 equals +1 dB — NOT normalized).
- The ``<JS ident "">`` block of the track chunk carries slider values (by
  slider number) and byte-diffs cleanly — remove + re-add must restore the
  virgin block (factory reset, catalog item 7). An ``@init`` slider write
  propagates to the wrapper param and the chunk line.
- A JSFX CANNOT deliver NaN/Inf to the graph: EEL2 guards division (0/0 = 0,
  ``sqrt(-1)`` = 1) and the host scrubs non-finite output to 0 and clamps
  |spl| to 1.0 (verified: ``log(-1)``, ``exp(710)``, ``inf*0`` all render as
  silence; ``spl0=2`` renders as 1.0). The sweep therefore keeps the
  mutation-verified pathology assertion for cross-version honesty, but the
  real-world extreme-setting catcher is the crash/hang path: a runaway loop
  stalls the render and fails via the render timeout (§1.7).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_jsfx
from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import (
    _emit, _mut_determinism, _mut_pathology, _staircase_env_lua,
)
from reaproof.runner.session import ReaperSession

#: the host wrapper appends these to every JSFX, after the declared sliders
WRAPPER_PARAMS = ("Bypass", "Wet", "Delta")

#: REAPER's compiler message, as shown in the FX window Static control
_ERROR_TEXT = re.compile(r"^@[\w.]+:\d+:")


# ---- static parse ------------------------------------------------------------

@dataclass
class SliderDecl:
    number: int              # sliderN — 1-based, may be sparse
    name: str                # label without a leading '-'
    hidden: bool             # label started with '-'
    default: float | None    # None for file sliders
    lo: float | None
    hi: float | None
    is_file: bool = False


@dataclass
class JsfxSource:
    desc: str | None
    sliders: list[SliderDecl] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    writes_audio: bool = False    # assigns to spl<N>/spl(...) in code
    uses_midi: bool = False       # midirecv/midisend in code
    gmem_namespace: str | None = None
    gfx_functions: list[str] = field(default_factory=list)
    gfx_funcs_used_outside: list[str] = field(default_factory=list)


_SLIDER = re.compile(r"^slider(\d+):(.*)$")
_NUMERIC = re.compile(
    r"^(?:[A-Za-z_]\w*=)?"            # optional variable_name=
    r"([-+]?[\d.]+(?:[eE][-+]?\d+)?)"  # default
    r"(?:<([^>]*)>)?"                  # optional <lo,hi[,inc{...}]>
    r"(.*)$")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def parse_jsfx(text: str) -> JsfxSource:
    """Parse the JSFX header + section layout (plain text, no compiler)."""
    src = JsfxSource(desc=None)
    section = "header"
    section_code: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^@(\w+)", line)
        if m:
            section = m.group(1)
            if section not in src.sections:
                src.sections.append(section)
            continue
        section_code.setdefault(section, []).append(line)
        if section != "header":
            continue
        if line.startswith("desc:") and src.desc is None:
            src.desc = line[5:].strip()
        m = re.match(r"^options:.*\bgmem=(\S+)", line)
        if m:
            src.gmem_namespace = m.group(1)
        m = _SLIDER.match(line)
        if m:
            num, rest = int(m.group(1)), m.group(2).strip()
            if rest.startswith("/"):
                # file slider: /dir:default_file:label
                label = rest.split(":")[-1].strip()
                src.sliders.append(SliderDecl(
                    number=num, name=label.lstrip("-"),
                    hidden=label.startswith("-"), default=None, lo=None,
                    hi=None, is_file=True))
                continue
            n = _NUMERIC.match(rest)
            if not n:
                continue
            default = float(n.group(1))
            lo = hi = None
            if n.group(2):
                parts = n.group(2).split(",")
                try:
                    lo = float(parts[0])
                    hi = float(re.match(r"[-+]?[\d.]*(?:[eE][-+]?\d+)?",
                                        parts[1].strip()).group(0) or "nan")
                except (IndexError, ValueError):
                    lo = hi = None
            label = n.group(3).strip()
            src.sliders.append(SliderDecl(
                number=num, name=label.lstrip("-"),
                hidden=label.startswith("-"), default=default, lo=lo, hi=hi))
    src.sliders.sort(key=lambda s: s.number)

    code = {sec: _strip_comments("\n".join(lines))
            for sec, lines in section_code.items() if sec != "header"}
    all_code = "\n".join(code.values())
    src.writes_audio = bool(re.search(
        r"\bspl(?:\d+\s*[-+*/|&~^]?=|\()",
        code.get("sample", "") + "\n" + code.get("block", "")))
    src.uses_midi = bool(re.search(r"\bmidi(?:recv|send)", all_code))
    src.gfx_functions = re.findall(r"\bfunction\s+([A-Za-z_][\w.]*)\s*\(",
                                   code.get("gfx", ""))
    outside = "\n".join(v for k, v in code.items() if k != "gfx")
    src.gfx_funcs_used_outside = [
        f for f in src.gfx_functions
        if re.search(rf"\b{re.escape(f)}\s*\(", outside)]
    return src


# ---- battery -----------------------------------------------------------------

@dataclass
class JsfxTestOptions:
    sample_rate: int = 48000                       # primary render SR
    extra_rates: tuple[int, ...] = (44100, 96000)  # catalog item 9
    signals: bool = True           # audio-integrity/multi-SR/determinism renders
    sweep_params: bool = True
    max_params: int = 16
    full: bool = False
    is_instrument: bool = False    # generates sound; skip silence->silence
    render_timeout: float = 120.0  # per-render cap; a stalled JSFX loop = fail


def _add_factory(rs: ResultSet, log):
    def add(name, status, **kw):
        rs.results.append(TestResult(name=name, status=status, **kw))
        log(f"  [{status.upper():9}] {name}"
            + (f" — {kw.get('message')}" if kw.get("message") else ""))
    return add


_INSERT_AND_DISCOVER = r"""
while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
reaper.InsertTrackAtIndex(0, false)
local tr = reaper.GetTrack(0,0)
local fx = reaper.TrackFX_AddByName(tr, '__ADD__', false, -1)
local out = { fx = fx, params = {} }
if fx < 0 then return out end
local _, ident = reaper.TrackFX_GetNamedConfigParm(tr, fx, 'fx_ident')
out.ident = ident
local n = reaper.TrackFX_GetNumParams(tr, fx)
for i = 0, n - 1 do
  local _, name = reaper.TrackFX_GetParamName(tr, fx, i, '')
  local cur, mn, mx = reaper.TrackFX_GetParam(tr, fx, i)
  out.params[#out.params+1] = { index=i, name=name, min=mn, max=mx, default=cur }
end
reaper.TrackFX_Show(tr, fx, 3)
return out
"""

_READ_STATICS = r"""
local tr = reaper.GetTrack(0,0)
local fxwin = reaper.TrackFX_GetFloatingWindow(tr, 0)
if not fxwin then return { floating = false } end
local out = { floating = true, statics = {} }
local n, kaddrs = reaper.JS_Window_ListAllChild(fxwin)
for addr in string.gmatch(kaddrs or '', '[^,]+') do
  local hw = reaper.JS_Window_HandleFromAddress(addr)
  if hw and reaper.JS_Window_GetClassName(hw) == 'Static' then
    local t = reaper.JS_Window_GetTitle(hw)
    if t and t ~= '' then out.statics[#out.statics+1] = t end
  end
end
return out
"""

_JS_BLOCK = r"""
local tr = reaper.GetTrack(0,0)
local _, chunk = reaper.GetTrackStateChunk(tr, '', false)
return chunk:match('(<JS .-\n>)') or ''
"""


def _stage_compile_and_params(subject: Path, source: JsfxSource, add,
                              *, log) -> list[dict] | None:
    """Compile proof + declared-vs-live param diff + factory-reset check.

    Returns the LIVE param list (dicts) on success, None when the subject
    did not insert (nothing further can run).
    """
    fx_add = f"JS:ReaProof/{subject.name}".replace("'", "\\'")
    with ReaperSession(f"jsfxtest-{subject.stem}", jsfx=[subject]) as s:
        disc = s.eval(_INSERT_AND_DISCOVER.replace("__ADD__", fx_add), timeout=60)
        if not isinstance(disc, dict) or disc.get("fx", -1) < 0:
            add("compile: REAPER inserts and compiles the JSFX", "failed",
                message="TrackFX_AddByName did not insert the file")
            return None
        live_params = disc.get("params") or []

        # -- compile proof: REAPER's own compiler message in the FX window ----
        statics: list[str] = []
        floating = False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            res = s.eval(_READ_STATICS, timeout=30)
            floating = bool(isinstance(res, dict) and res.get("floating"))
            statics = (res.get("statics") or []) if isinstance(res, dict) else []
            if statics:
                break
            time.sleep(0.3)
        errors = [t for t in statics if _ERROR_TEXT.match(t)]
        desc_shown = bool(source.desc) and source.desc in statics
        gfx_note = ""
        if source.gfx_funcs_used_outside:
            gfx_note = (" — likely cause: function(s) defined in @gfx but used "
                        f"elsewhere: {', '.join(source.gfx_funcs_used_outside)} "
                        "(a JSFX compiles @gfx last; move them to @init)")
        if errors:
            add("compile: REAPER inserts and compiles the JSFX", "failed",
                message=f"compiler error: {errors[0]}{gfx_note}")
            return None
        if not floating or not statics:
            add("compile: REAPER inserts and compiles the JSFX", "failed",
                message="could not read the FX window (compile status unverified "
                        "— not claiming green without the proof)")
            return None
        if source.desc and not desc_shown:
            add("compile: REAPER inserts and compiles the JSFX", "failed",
                message="FX window shows neither the desc text nor a compiler "
                        "error — compile status unverified")
            return None
        add("compile: REAPER inserts and compiles the JSFX", "passed",
            message=("desc shown in the FX window, no compiler error"
                     if desc_shown else
                     "no compiler error in the FX window (file declares no desc)"))
        s.eval("reaper.TrackFX_Show(reaper.GetTrack(0,0), 0, 2); return true")

        # -- declared-vs-live param diff (validates the sweep mapping) --------
        declared = [d.name for d in source.sliders]
        live = [p.get("name", "") for p in live_params]
        expected = declared + list(WRAPPER_PARAMS)
        if live == expected:
            add("params: declared sliders match live params", "passed",
                message=f"{len(declared)} slider(s) "
                        f"({sum(1 for d in source.sliders if d.hidden)} hidden) "
                        f"+ wrapper {'/'.join(WRAPPER_PARAMS)}")
        else:
            add("params: declared sliders match live params", "failed",
                message=f"declared {expected} but live {live} — the battery's "
                        "slider→param mapping would be wrong; header parse and "
                        "host disagree")
            return None

        # -- factory reset (catalog item 7) -----------------------------------
        sweepable = [(i, d) for i, d in enumerate(source.sliders)
                     if not d.is_file and d.lo is not None
                     and d.hi is not None and d.hi != d.lo]
        if not sweepable:
            add("state: remove + re-add is factory reset", "skipped",
                message="no range sliders to tweak — the chunk diff would be "
                        "vacuous (nothing can change it)")
            return live_params
        gmem_note = ""
        if source.gmem_namespace:
            # gmem is freed when its LAST user detaches — removing the only
            # instance would wipe the very state we are hunting. Attaching
            # the bridge to the declared namespace emulates the concurrent
            # instance / attached Lua that keeps it alive in the wild.
            s.eval(f"reaper.gmem_attach('{source.gmem_namespace}'); return true",
                   timeout=30)
            gmem_note = (f" (battery attached to gmem namespace "
                         f"'{source.gmem_namespace}' to emulate a concurrent "
                         "instance)")
        virgin = s.eval(_JS_BLOCK, timeout=30)
        tweaks = "\n".join(
            f"reaper.TrackFX_SetParam(tr, 0, {i}, {d.lo + (d.hi - d.lo) * 0.73:.6f})"
            for i, d in sweepable)
        s.eval("local tr = reaper.GetTrack(0,0)\n" + tweaks + "\nreturn true",
               timeout=30)
        tweaked = s.eval(_JS_BLOCK, timeout=30)
        if tweaked == virgin:
            # the oracle never registered the tweak — a pass here would be
            # exactly the vacuous green §1.3 forbids
            add("state: remove + re-add is factory reset", "failed",
                message="chunk oracle VACUOUS — param tweaks did not change "
                        "the FX chunk, so a clean diff proves nothing",
                mutation_verified=False)
            return live_params
        ok = s.eval(r"""
        local tr = reaper.GetTrack(0,0)
        reaper.TrackFX_Delete(tr, 0)
        return reaper.TrackFX_AddByName(tr, '__ADD__', false, -1) >= 0
        """.replace("__ADD__", fx_add), timeout=30)
        if not ok:
            add("state: remove + re-add is factory reset", "failed",
                message="re-add failed (TrackFX_AddByName < 0)")
            return live_params
        # @init of the fresh instance runs on the audio thread — poll the
        # chunk through a settle window; ANY divergence from the virgin
        # block is a leak, stability across the window is the reset proof
        readd = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            readd = s.eval(_JS_BLOCK, timeout=30)
            if readd != virgin:
                break
            time.sleep(0.25)
        if readd == virgin:
            add("state: remove + re-add is factory reset", "passed",
                mutation_verified=True,
                message="re-added FX chunk is byte-identical to the virgin "
                        f"insert (tweak WAS visible to the oracle){gmem_note}")
        else:
            add("state: remove + re-add is factory reset", "failed",
                mutation_verified=True,
                message="state survived remove + re-add — leaking through "
                        f"gmem/files/serialize{gmem_note}. "
                        f"virgin={virgin!r:.120} re-added={str(readd)!r:.120}")
        return live_params


def run_jsfx_battery(subject: Path, out_dir: Path | None = None,
                     opts: JsfxTestOptions | None = None, *,
                     log=print) -> ResultSet:
    subject = Path(subject).resolve()
    opts = opts or JsfxTestOptions()
    rs = ResultSet()
    add = _add_factory(rs, log)
    t0 = time.monotonic()

    # 1) static parse ---------------------------------------------------------
    log("static parse…")
    try:
        text = subject.read_text(encoding="utf-8", errors="replace")
        source = parse_jsfx(text)
    except OSError as e:
        add("static: JSFX header parses", "failed", message=str(e)[:200])
        _emit(rs, out_dir, subject)
        return rs
    bits = [f"{len(source.sliders)} slider(s)",
            f"sections: {', '.join('@' + s for s in source.sections) or 'none'}"]
    if source.gmem_namespace:
        bits.append(f"gmem namespace '{source.gmem_namespace}' declared "
                    "(gmem hygiene diff is the v2 slice)")
    if source.gfx_functions:
        bits.append(f"function(s) defined in @gfx: {', '.join(source.gfx_functions)}")
    add("static: JSFX header parses", "passed", message="; ".join(bits))

    # 2) compile proof + param diff + factory reset (one session) -------------
    log("compile proof + param mapping + factory reset…")
    live_params = _stage_compile_and_params(subject, source, add, log=log)
    if live_params is None:
        _emit(rs, out_dir, subject)
        return rs

    # 3) render battery -------------------------------------------------------
    if source.uses_midi and not source.writes_audio:
        add("audio: render battery", "skipped",
            message="MIDI-driven JSFX (midirecv/midisend, no spl writes) — "
                    "the audio battery needs a MIDI feed (v2)")
        rs.results[0].duration_s = time.monotonic() - t0
        _emit(rs, out_dir, subject)
        return rs

    fx_name = f"JS:ReaProof/{subject.name}"
    sr = opts.sample_rate
    sigs = {
        "silence": S.silence(1.0, sr=sr),
        "sine_1k": S.sine(1000.0, dbfs=-12.0, seconds=1.0, sr=sr),
        "noise": S.noise(dbfs=-12.0, seconds=1.0, sr=sr),
        "impulse": S.impulse(1.0, sr=sr),
        "sweep": S.sweep(20.0, 20000.0, dbfs=-12.0, seconds=1.0, sr=sr),
        "fullscale_sine": S.sine(1000.0, dbfs=0.0, seconds=1.0, sr=sr),
    }
    renders = {}
    if opts.signals:
        log(f"audio integrity at {sr} Hz…")
    for sname, sig in sigs.items() if opts.signals else ():
        try:
            r = render_through_jsfx(fx_name, jsfx_files=[subject], input_signal=sig,
                                    sample_rate=sr, name=f"jt-{subject.stem}-{sname}",
                                    render_timeout=opts.render_timeout)
            renders[sname] = r
        except Exception as e:  # noqa: BLE001 — crash/hang in render = fail (§1.7)
            add(f"audio: {sname} renders without crash/hang", "failed",
                message=str(e)[:200])
            continue
        hard_only = sname in ("noise", "impulse", "sweep")
        if r.samples.shape[0] < len(sig):
            # a stalled/killed render can leave a stable partial file — a
            # truncated result must never read as "pathology-free"
            add(f"audio: {sname} is pathology-free", "failed",
                message=f"render truncated: {r.samples.shape[0]}/{len(sig)} samples")
            continue
        _mut_pathology(r.samples, hard_only, f"audio: {sname} is pathology-free",
                       add, artifacts=[str(r.output_path)])
        if sname == "silence" and not opts.is_instrument and not source.uses_midi:
            rms = A.rms_dbfs(r.samples)
            if rms <= -80.0:
                add("audio: silence in -> silence out", "passed")
            else:
                add("audio: silence in -> silence out", "failed",
                    message=f"silence in produced {rms:.1f} dBFS out "
                            "(self-noise / oscillation?)")

    # multi-samplerate (catalog item 9) --------------------------------------
    for xsr in opts.extra_rates if opts.signals else ():
        log(f"audio integrity at {xsr} Hz…")
        for sname, sig in (("sine_1k", S.sine(1000.0, dbfs=-12.0, seconds=1.0, sr=xsr)),
                           ("noise", S.noise(dbfs=-12.0, seconds=1.0, sr=xsr))):
            try:
                r = render_through_jsfx(fx_name, jsfx_files=[subject],
                                        input_signal=sig, sample_rate=xsr,
                                        name=f"jt-{subject.stem}-{sname}-{xsr}",
                                        render_timeout=opts.render_timeout)
            except Exception as e:  # noqa: BLE001
                add(f"audio: {sname}@{xsr}Hz renders without crash/hang",
                    "failed", message=str(e)[:200])
                continue
            if r.samples.shape[0] < len(sig):
                add(f"audio: {sname}@{xsr}Hz is pathology-free", "failed",
                    message=f"render truncated: {r.samples.shape[0]}/{len(sig)} samples")
                continue
            _mut_pathology(r.samples, sname == "noise",
                           f"audio: {sname}@{xsr}Hz is pathology-free", add)

    # determinism -------------------------------------------------------------
    if "sine_1k" in renders:
        if source.uses_midi and A.rms_dbfs(renders["sine_1k"].samples) < -110.0:
            # a MIDI instrument without a MIDI feed renders silence — the
            # determinism mutation cannot register on it (vacuous), and that
            # is a missing feed, not a subject defect
            add("determinism: re-render is bit-identical", "skipped",
                message="output silent without a MIDI feed — unprovable here "
                        "(MIDI-feed renders are the v2 slice)")
        else:
            log("determinism…")
            try:
                again = render_through_jsfx(fx_name, jsfx_files=[subject],
                                            input_signal=sigs["sine_1k"],
                                            sample_rate=sr,
                                            name=f"jt-{subject.stem}-determ",
                                            render_timeout=opts.render_timeout)
                _mut_determinism(renders["sine_1k"].samples, again.samples,
                                 "determinism: re-render is bit-identical", add)
            except Exception as e:  # noqa: BLE001
                add("determinism: re-render is bit-identical", "failed",
                    message=str(e)[:200])

    # 4) per-slider sweep (item 6 — hidden sliders swept too) -----------------
    if opts.sweep_params:
        sweepable = [p for i, p in enumerate(live_params)
                     if i < len(source.sliders)          # wrapper params excluded
                     and not source.sliders[i].is_file
                     and float(p.get("max", 0)) != float(p.get("min", 0))]
        plist = sweepable if opts.full else sweepable[:opts.max_params]
        if len(plist) < len(sweepable):
            log(f"sweeping {len(plist)}/{len(sweepable)} sliders "
                f"(--full for all; the rest are NOT covered)")
        elif plist:
            log(f"sweeping all {len(plist)} slider(s) full-range…")
        noise = S.noise(dbfs=-12.0, seconds=1.0, sr=sr)
        for p in plist:
            idx = int(p["index"])
            pname = p.get("name") or f"param{idx}"
            lo, hi = float(p["min"]), float(p["max"])
            env = _staircase_env_lua(idx, lo, hi, steps=8, dur=len(noise) / sr)
            label = (f"param sweep [{idx}] {pname}"
                     + (" (hidden)" if source.sliders[idx].hidden else "")
                     + ": stable across range")
            try:
                r = render_through_jsfx(fx_name, jsfx_files=[subject],
                                        input_signal=noise, sample_rate=sr,
                                        extra_setup=env,
                                        name=f"jt-{subject.stem}-sweep{idx}",
                                        render_timeout=opts.render_timeout)
            except Exception as e:  # noqa: BLE001
                add(label, "failed", message=str(e)[:200])
                continue
            if r.samples.shape[0] < len(noise):
                add(label, "failed",
                    message=f"render truncated: {r.samples.shape[0]}/{len(noise)} "
                            "samples (stalled mid-sweep?)")
                continue
            _mut_pathology(r.samples, True, label, add)

    rs.results[0].duration_s = time.monotonic() - t0
    _emit(rs, out_dir, subject)
    return rs
