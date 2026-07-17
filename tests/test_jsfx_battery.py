"""Gates: JSFX zero-code battery (forum ask, catalog credit: mequaz t=310135 #5).

The broken reference subjects ARE the mutations: each battery stage is proven
able to turn RED by a subject engineered to trip exactly that stage, and the
clean subject proves the green path (twice, §1.4). The battery itself is built
on live-verified 7.75 mechanics — see the module docstring of
``reaproof.runner.jsfxtest`` (a compile-broken JSFX still inserts, still lists
its sliders as params, and passes audio bit-exact; only the FX window carries
the compiler message).
"""
import sys
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.runner.jsfxtest import (
    _ERROR_TEXT, JsfxTestOptions, parse_jsfx, run_jsfx_battery,
)

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="JSFX battery uses the macOS session stack")

JSFX = paths.EXAMPLES / "jsfx"


def _statuses(rs) -> dict[str, str]:
    return {r.name: r.status for r in rs.results}


def _quiet(*a, **k):
    pass


# ---- REAPER-free units: the static parser ------------------------------------

def test_parser_reads_the_gain_subject():
    src = parse_jsfx((JSFX / "ReaProof_Gain.jsfx").read_text())
    assert src.desc == "ReaProof Gain (custom rotary knob)"
    assert [s.name for s in src.sliders] == ["Gain (dB)"]
    s = src.sliders[0]
    assert (s.number, s.default, s.lo, s.hi, s.hidden) == (1, 0.0, -24.0, 24.0, False)
    assert src.sections == ["init", "slider", "sample", "gfx"]
    assert src.writes_audio and not src.uses_midi
    assert src.gfx_functions == ["draw_knob"]
    # draw_knob is only used inside @gfx — legal, must NOT be flagged
    assert src.gfx_funcs_used_outside == []


def test_parser_sparse_and_hidden_sliders_in_number_order():
    src = parse_jsfx((JSFX / "ReaProof_Sparse_Hidden.jsfx").read_text())
    assert [(s.number, s.name, s.hidden) for s in src.sliders] == [
        (1, "Gain (dB)", False), (5, "Mix", False), (40, "Drive scale", True)]


def test_parser_flags_gfx_function_used_outside_gfx():
    src = parse_jsfx((JSFX / "ReaProof_Gain_BrokenCompile.jsfx").read_text())
    assert src.gfx_functions == ["db2gain"]
    assert src.gfx_funcs_used_outside == ["db2gain"]


def test_parser_variants_file_enum_gmem_nodesc_comments():
    text = (
        "options:gmem=AcmeSpace\n"
        "slider1:/samples:none:Sample file\n"
        "slider2:mode=1<0,3,1{A,B,C,D}>Mode\n"
        "slider3:0.5<0,1>Plain\n"
        "@sample\n"
        "// spl0 = 0/0; (commented out — must not count as an spl write)\n"
        "/* function fake() in a comment */\n"
        "x = 1;\n")
    src = parse_jsfx(text)
    assert src.desc is None
    assert src.gmem_namespace == "AcmeSpace"
    f, e, p = src.sliders
    assert f.is_file and f.name == "Sample file"
    assert (e.lo, e.hi) == (0.0, 3.0)
    assert (p.lo, p.hi) == (0.0, 1.0)
    assert not src.writes_audio and src.gfx_functions == []


def test_error_regex_matches_real_compiler_messages():
    # both texts read verbatim from the FX window of broken subjects (7.75)
    assert _ERROR_TEXT.match("@sample:17: syntax error: missing ) or ]")
    assert _ERROR_TEXT.match("@sample:17: 'apply' undefined: 'spl0 =  <!> apply(spl0);'")
    # desc text / slider labels must NOT look like errors
    assert not _ERROR_TEXT.match("ReaProof Gain (custom rotary knob)")
    assert not _ERROR_TEXT.match("Gain (dB)")
    assert not _ERROR_TEXT.match("2 in 2 out")


# ---- battery gates (REAPER) ---------------------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_clean_subject_is_green_twice(tmp_path):
    for i in (1, 2):   # §1.4: identical outcome across runs
        rs = run_jsfx_battery(JSFX / "ReaProof_Gain.jsfx",
                              out_dir=tmp_path / f"run{i}", log=_quiet)
        st = _statuses(rs)
        assert rs.gate_green, st
        assert st["compile: REAPER inserts and compiles the JSFX"] == "passed"
        assert st["params: declared sliders match live params"] == "passed"
        assert st["state: remove + re-add is factory reset"] == "passed"
        assert st["determinism: re-render is bit-identical"] == "passed"
        assert st["param sweep [0] Gain (dB): stable across range"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_compile_broken_is_red_with_the_compiler_error(tmp_path):
    """Catalog item 1: the file inserts silently; the battery must still turn
    RED and carry REAPER's own compiler message + the @gfx-function hint."""
    rs = run_jsfx_battery(JSFX / "ReaProof_Gain_BrokenCompile.jsfx",
                          out_dir=tmp_path, log=_quiet)
    assert not rs.gate_green
    comp = next(r for r in rs.results
                if r.name == "compile: REAPER inserts and compiles the JSFX")
    assert comp.status == "failed"
    assert "db2gain" in comp.message and "undefined" in comp.message
    assert "@gfx" in comp.message            # the likely-cause hint names item 1
    # the battery STOPS: no audio render may green-wash a non-compiling subject
    assert not any(r.name.startswith("audio:") for r in rs.results)


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_sparse_hidden_mapping_and_hidden_sweep(tmp_path):
    """Catalog item 6: sparse slider numbers resolve as dense params by name,
    and the hidden slider is swept like any other param."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Sparse_Hidden.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False), log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    par = next(r for r in rs.results
               if r.name == "params: declared sliders match live params")
    assert "3 slider(s) (1 hidden)" in par.message
    assert st["param sweep [2] Drive scale (hidden): stable across range"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_runaway_loop_at_extreme_is_red_in_the_sweep(tmp_path):
    """The honest extreme-setting failure for JSFX: the host scrubs non-finite
    output and clamps to ±1 (a JSFX cannot emit NaN into the graph), so a
    runaway @sample loop that STALLS the render is what a sweep-reachable
    extreme really does in the wild — and it must fail via the render
    timeout, not wedge the battery. This also proves the staircase envelope
    actually REACHES the top of the declared range (plain-unit scale)."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Gain_BrokenHang.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, render_timeout=45.0), log=_quiet)
    assert not rs.gate_green
    sweep = next(r for r in rs.results if r.name.startswith("param sweep [0]"))
    assert sweep.status == "failed"
    # either the render never produced output (timeout) or it left a stable
    # partial file (truncation guard) — both are the stall, named
    assert "no output within" in sweep.message or "truncated" in sweep.message, \
        sweep.message


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_state_leak_survives_readd_is_red(tmp_path):
    """Catalog item 7: state leaking through gmem into a fresh instance means
    remove + re-add is NOT a factory reset — the chunk diff must turn RED."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Gain_BrokenReset.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    st = _statuses(rs)
    reset = next(r for r in rs.results
                 if r.name == "state: remove + re-add is factory reset")
    assert reset.status == "failed", st
    assert reset.mutation_verified        # the tweak WAS visible to the oracle
    assert "leak" in reset.message
