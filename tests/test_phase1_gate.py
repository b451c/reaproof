"""Phase 1 verification gate (§14).

GATE: a gain test asserts -6 dB output correctly AND its mutation (inject x0.5)
turns it RED; a NaN-producing build is FAILED, not skipped.
NEGATIVE CONTROLS: the broken "ignores its parameter" build FAILS the -6 dB DSP
assertion; the NaN build raises a pathology. Asserts are on rendered audio (the
effect), never the set value (§1.1/§8.4).

Launches REAPER + renders, so marked reaper/slow. Run:
    PYTHONPATH=src python -m pytest tests/test_phase1_gate.py -v
"""
import numpy as np
import pytest

from reaproof.mutation import mutation_check, scale
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_jsfx
from reaproof import paths

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain*.jsfx"))
SR = 48000
INPUT = S.sine(1000, dbfs=-12.0, seconds=2.0, sr=SR)   # peak -12 => RMS -15.01 dBFS
IN_RMS = A.rms_dbfs(INPUT)
# tolerance 0.1 dB: JSFX gain is exact 10^(dB/20) and offline render is deterministic;
# 0.1 dB catches a mis-scaled gain yet is far below the 6 dB the mutation introduces.
TOL_DB = 0.1


@pytest.mark.gate
def test_gain_minus6_db_is_correct_and_mutation_verified():
    r = render_through_jsfx("JS: ReaProof Gain", jsfx_files=JSFX,
                            params={0: -6.0}, input_signal=INPUT, sample_rate=SR,
                            name="p1gain")
    A.assert_no_pathology(r.samples)
    expected = IN_RMS - 6.0

    def assert_gain(samples):
        assert A.approx_dbfs(A.rms_dbfs(samples), expected, tol_db=TOL_DB,
                             why="gain knob at -6 dB => output 6 dB below reference")

    # clean PASS + the canonical mutation (x0.5 = -6 dB) must turn it RED (§1.3)
    report = mutation_check(r.samples, assert_gain, [("inject x0.5 (-6 dB)", scale(0.5))])
    report.raise_if_vacuous()
    assert report.clean_passed and not report.vacuous
    # and the measured effect itself is right
    assert A.approx_dbfs(A.rms_dbfs(r.samples), expected, tol_db=TOL_DB, why="gate")


@pytest.mark.gate
def test_unity_gain_is_transparent_null():
    r = render_through_jsfx("JS: ReaProof Gain", jsfx_files=JSFX,
                            params={0: 0.0}, input_signal=INPUT, sample_rate=SR,
                            name="p1unity")
    A.assert_no_pathology(r.samples)
    n = min(len(r.samples), len(INPUT))
    residual = A.null_test_dbfs(r.samples[:n], INPUT[:n])
    # unity gain must be effectively transparent. -80 dBFS is a justified floor for a
    # float render round-trip; a mis-applied gain would lift this far above -80.
    assert residual < -80, f"unity gain not transparent: residual {residual:.1f} dBFS"


@pytest.mark.negative_control
def test_broken_ignore_build_fails_dsp_assertion():
    """A gain that stores its value but does nothing must FAIL the -6 dB assertion."""
    r = render_through_jsfx("JS: ReaProof Gain BROKEN (ignores its parameter)",
                            jsfx_files=JSFX, params={0: -6.0}, input_signal=INPUT,
                            sample_rate=SR, name="p1broken")
    expected = IN_RMS - 6.0
    # output is unchanged (~ -15 dBFS), so the -6 dB expectation must NOT hold
    assert not A.approx_dbfs(A.rms_dbfs(r.samples), expected, tol_db=TOL_DB,
                             why="broken build ignores param"), \
        "broken-ignore build wrongly satisfied the -6 dB assertion"


@pytest.mark.negative_control
def test_nan_build_is_failed_not_skipped():
    """A build that emits NaN at extreme gain is FAILED via pathology detection (§1.7)."""
    r = render_through_jsfx("JS: ReaProof Gain BROKEN (NaN at extreme)",
                            jsfx_files=JSFX, params={0: 24.0}, input_signal=INPUT,
                            sample_rate=SR, name="p1nan")
    with pytest.raises(A.AudioPathology):
        A.assert_no_pathology(r.samples)
