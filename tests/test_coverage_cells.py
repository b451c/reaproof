"""Additional taxonomy cells for the rotary knob (REFERENCE §7), closing two cells
that the phase gates did not demonstrate: FUNCTIONAL (parameter contract) and STATE
(save/restore round-trip). Each is mutation-verified / has a negative control.

(Value<->text round-trip is additionally covered for the CLAP subject by
clap-validator's param-conversion tests in Phase 2.)
"""
import pytest

from reaproof import paths
from reaproof.authoring import assert_param_contract, assert_state_roundtrip
from reaproof.mutation import mutation_check, offset_value

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

JSFX = "ReaProof_Gain.jsfx"
FX = "JS: ReaProof Gain"


@pytest.mark.gate
def test_functional_param_contract():
    """Existence/name, range, default, linear taper, value->text — read back via the
    host API (a different path from the slider)."""
    assert_param_contract(JSFX, FX, expected_name="Gain (dB)", vrange=(-24, 24),
                          default=0.0, fmt_at=(-6.0, "-6.0"))


@pytest.mark.negative_control
def test_param_contract_is_not_vacuous():
    """A wrong expectation must turn the functional check RED."""
    with pytest.raises(AssertionError):
        assert_param_contract(JSFX, FX, expected_name="WRONG NAME", vrange=(-24, 24),
                              default=0.0, fmt_at=(-6.0, "-6.0"))


@pytest.mark.gate
def test_state_save_restore_roundtrip():
    """Set -6 dB, save the project, reload in a FRESH REAPER, value persists
    (mutation-verified: a wrong expectation goes RED)."""
    restored = assert_state_roundtrip(JSFX, FX, set_value=-6.0)

    def check(v):
        assert abs(v - (-6.0)) <= 0.01, f"restored {v} != -6.0"

    mutation_check(restored, check, [("drift +3", offset_value(3.0))]).raise_if_vacuous()
    check(restored)


@pytest.mark.negative_control
def test_state_roundtrip_catches_wrong_value():
    restored = assert_state_roundtrip(JSFX, FX, set_value=-6.0)
    assert abs(restored - 12.0) > 1.0   # a non-persisting build would not return -6


@pytest.mark.gate
def test_coverage_param_derivation_universal():
    """The coverage param list is derived through the host (universal: JSFX/VST/CLAP),
    matching what the taxonomy report checks against (§11.1)."""
    from reaproof.coverage.derive import derive_params
    from reaproof.coverage import coverage_report
    from reaproof.runner.session import session as _session
    jsfx = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain.jsfx"))
    with _session("derive", jsfx=jsfx) as s:
        s.eval(f"reaper.InsertTrackAtIndex(0,false)\n"
               f"reaper.TrackFX_AddByName(reaper.GetTrack(0,0),'{FX}',false,-1)\n"
               f"return true")
        params = derive_params(s, 0)
    names = [p["name"] for p in params]
    assert "Gain (dB)" in names, names              # the knob param is discovered
    # a coverage report over the derived control reports its uncovered cells
    rep = coverage_report({"rotary_knob": {"functional", "dsp", "visual_at_value"}})
    assert "rotary_knob" in rep.gaps()
