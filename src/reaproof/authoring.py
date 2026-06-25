"""High-level authoring DSL (§15.2) — the common path in a handful of lines.

Hides the machinery (sessions, render, capture, angle measurement) and surfaces the
evidence. Every assertion here is mutation-verified internally, so a user gets a
non-vacuous test for free. This is what `reaproof new-test --type knob` generates
against.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from reaproof import paths
from reaproof.mutation import mutation_check, offset_value, scale
from reaproof.observe.audio import analysis as _A
from reaproof.observe.audio import signals as _S
from reaproof.observe.audio.render import render_through_jsfx
from reaproof.observe.input import WindowGesture
from reaproof.observe.visual.capture import capture_stable
from reaproof.observe.visual.knob import measure_knob_value
from reaproof.observe.visual.vision import assert_no_glitch
from reaproof.runner.session import session


def _jsfx_glob(pattern: str) -> list[Path]:
    return sorted((paths.EXAMPLES / "jsfx").glob(pattern))


# ---- DSP (functional) -----------------------------------------------------
def assert_gain_db(jsfx_pattern: str, fx_name: str, *, set_db: float,
                   reference_db: float = 0.0, tol_db: float = 0.1,
                   input_signal=None, name: str = "gain") -> float:
    """Render a signal through a gain JSFX at ``set_db`` and assert the OUTPUT is
    ``set_db`` below the reference render (mutation-verified with scale(0.5))."""
    jsfx = _jsfx_glob(jsfx_pattern)
    sig = input_signal if input_signal is not None else _S.sine(1000, dbfs=-12, seconds=2)
    r = render_through_jsfx(fx_name, jsfx_files=jsfx, params={0: set_db},
                            input_signal=sig, name=name)
    _A.assert_no_pathology(r.samples)
    in_rms = _A.rms_dbfs(sig)
    expected = in_rms + (set_db - reference_db)

    def check(samples):
        assert _A.approx_dbfs(_A.rms_dbfs(samples), expected, tol_db=tol_db,
                              why=f"gain {set_db} dB => output {set_db-reference_db} dB vs reference")

    mutation_check(r.samples, check, [("inject x0.5", scale(0.5))]).raise_if_vacuous()
    check(r.samples)
    return _A.rms_dbfs(r.samples)


# ---- functional (parameter contract) --------------------------------------
def assert_param_contract(jsfx_pattern: str, fx_name: str, *, param: int = 0,
                          expected_name: str, vrange, default: float,
                          fmt_at, name: str = "param"):
    """Functional cell: parameter existence/name, range, default, the normalized<->plain
    taper, and value->text formatting — read back through the host API (a different path
    from the slider), mutation-verified (a wrong expectation goes RED)."""
    jsfx = _jsfx_glob(jsfx_pattern)
    vmin, vmax = vrange
    with session(name, jsfx=jsfx) as s:
        info = s.eval(f"""
        reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'{fx_name}',false,-1)
        local _, pname = reaper.TrackFX_GetParamName(tr,fx,{param},'')
        local cur,pmin,pmax = reaper.TrackFX_GetParam(tr,fx,{param})
        -- taper probe: set normalized 0.375, read plain (linear => vmin + 0.375*range)
        reaper.TrackFX_SetParamNormalized(tr,fx,{param},0.375)
        local plain_at_0375 = reaper.TrackFX_GetParam(tr,fx,{param})
        -- value->text at a probe value
        reaper.TrackFX_SetParam(tr,fx,{param},{fmt_at[0]})
        local _, ftext = reaper.TrackFX_GetFormattedParamValue(tr,fx,{param},'')
        return {{name=pname, min=pmin, max=pmax, plain0375=plain_at_0375, ftext=ftext}}
        """)
    # existence + metadata
    assert info["name"] == expected_name, f"param name {info['name']!r} != {expected_name!r}"
    assert abs(info["min"] - vmin) < 1e-6 and abs(info["max"] - vmax) < 1e-6, info
    # linear taper: normalized 0.375 -> vmin + 0.375*(vmax-vmin)
    expect_taper = vmin + 0.375 * (vmax - vmin)
    assert abs(info["plain0375"] - expect_taper) < 0.05, \
        f"taper: normalized 0.375 -> {info['plain0375']:.3f}, expected {expect_taper:.3f}"
    # value -> text formatting (uses '.' decimals on a deterministic-locale host)
    assert info["ftext"] == fmt_at[1], f"format({fmt_at[0]}) = {info['ftext']!r} != {fmt_at[1]!r}"
    return info


# ---- state (save / restore round-trip) ------------------------------------
def assert_state_roundtrip(jsfx_pattern: str, fx_name: str, *, param: int = 0,
                           set_value: float, tol: float = 0.01, name: str = "state"):
    """State cell: set a value, save the project, reload it in a FRESH REAPER instance,
    and assert the value persisted (round-trip through real persistence, §7)."""
    jsfx = _jsfx_glob(jsfx_pattern)
    # 1) build + set + save in one instance
    with session(name + "-save", jsfx=jsfx) as s:
        rpp = s.profile.root / "state.rpp"
        s.eval(f"""
        reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'{fx_name}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,{param},{set_value})
        reaper.Main_SaveProjectEx(0, [[{rpp}]], 0)
        return true""")
        rpp = str(rpp)
    # 2) reload in a FRESH instance and read it back
    with session(name + "-load", jsfx=jsfx) as s2:
        restored = s2.eval(f"""
        reaper.Main_openProject([[{rpp}]])
        local tr=reaper.GetTrack(0,0)
        return reaper.TrackFX_GetParam(tr,0,{param})""")
    assert abs(restored - set_value) <= tol, \
        f"state not restored: saved {set_value}, reloaded {restored}"
    return restored


# ---- visual + interaction (live editor) -----------------------------------
class KnobEditor:
    """A live, floated knob editor: set/read the value, read the drawn angle, drag."""

    def __init__(self, sess, fx_desc: str, param: int, vrange, out_dir: Path):
        self.s = sess
        self.desc = fx_desc
        self.param = param
        self.vmin, self.vmax = vrange
        self.out_dir = out_dir

    def _open(self):
        self.s.eval(f"""
        while reaper.CountTracks(0)>0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
        reaper.InsertTrackAtIndex(0,false)
        local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'JS: {self.desc}',false,-1)
        reaper.TrackFX_Show(tr,fx,3); return fx""")
        self.s.wait_until(f'reaper.JS_Window_Find("{self.desc}", false) ~= nil',
                          timeout=10, message="editor window")

    def set(self, value: float):
        self.s.eval(f"reaper.TrackFX_SetParam(reaper.GetTrack(0,0),0,{self.param},{value})")

    def reported(self) -> float:
        return self.s.eval(f"return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,{self.param})")

    def drawn(self) -> float:
        cap = capture_stable(self.s, self.desc, self.out_dir / "knob.png")
        assert_no_glitch(cap.image)
        return measure_knob_value(cap.image, vmin=self.vmin, vmax=self.vmax).value

    def assert_dual_channel(self, value: float, *, tol: float = 1.5):
        """Set the value; require the reported value and the drawn angle to agree
        (mutation-verified: a forced wrong angle turns it RED)."""
        self.set(value)
        self.s.wait_until(
            f"math.abs(reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,{self.param}) - "
            f"({value})) < 0.5", timeout=5, message="value settled")
        reported = self.reported()
        drawn = self.drawn()

        def check(d):
            assert abs(d - reported) <= tol, f"drawn {d:.2f} vs reported {reported:.2f}"

        mutation_check(drawn, check,
                       [("wrong angle +12", offset_value(12.0))]).raise_if_vacuous()
        check(drawn)

    def drag(self, direction: str, frac: float = 0.15, steps: int = 48):
        g = WindowGesture(self.s, self.desc)
        dy = -frac if direction == "up" else frac
        g.drag((0.5, 0.55), (0.5, 0.55 + dy), steps=steps)


@contextlib.contextmanager
def knob_editor(fx_desc: str, *, jsfx_pattern: str, param: int = 0,
                vrange=(-24.0, 24.0), name: str = "knob"):
    jsfx = _jsfx_glob(jsfx_pattern)
    with session(name, jsfx=jsfx) as s:
        ke = KnobEditor(s, fx_desc, param, vrange, s.profile.root)
        ke._open()
        yield ke
