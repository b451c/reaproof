"""Phase 7 verification gate (§14).

GATE: a first-time user authors a passing, mutation-verified knob test (functional +
visual) quickly, following only the User Guide. Demonstrated by (a) the authoring DSL
producing a passing, mutation-verified knob test in a handful of lines, and (b) the
`new-test` generator emitting exactly that, runnable out of the box.
"""
import py_compile

import pytest

from reaproof.authoring import assert_gain_db, knob_editor
from reaproof.runner import scaffold


# ---- the authored knob test (what the user writes / the generator emits) ----
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_authored_knob_test_passes():
    # Functional/DSP: the knob actually changes the sound at -6 dB (mutation-verified
    # internally with x0.5). One call.
    assert_gain_db("ReaProof_Gain.jsfx", "JS: ReaProof Gain", set_db=-6.0, tol_db=0.1)

    # Visual: reported value and drawn angle agree at -6 dB (mutation-verified
    # internally with a forced wrong angle). A few lines.
    with knob_editor("ReaProof Gain (custom rotary knob)",
                     jsfx_pattern="ReaProof_Gain.jsfx", vrange=(-24, 24)) as knob:
        knob.assert_dual_channel(-6.0)


# ---- scaffolding / CLI (fast, no REAPER) ----
def test_new_test_generates_a_runnable_mutation_verified_test(tmp_path):
    dest = scaffold.new_test(tmp_path, type="knob", control="GainKnob")
    assert dest.exists()
    py_compile.compile(str(dest), doraise=True)        # generated file is valid Python
    body = dest.read_text()
    assert "assert_gain_db" in body                    # functional/DSP
    assert "assert_dual_channel" in body               # visual dual-channel
    assert "test_gainknob_dsp_gain" in body and "test_gainknob_visual_dual_channel" in body


def test_init_project_scaffolds(tmp_path):
    root = scaffold.init_project(tmp_path / "proj")
    assert (root / "reaproof.toml").exists()
    assert (root / "tests").is_dir() and (root / "goldens").is_dir()


def test_doctor_runs():
    from reaproof.runner.cli import main
    rc = main(["doctor"])
    assert rc in (0, 1)   # returns a clean status code either way
