"""Automation taxonomy cell (REFERENCE §7, USER_GUIDE §5.3): render with a parameter
automation envelope sweeping the gain over time, and verify the OUTPUT tracks the
envelope and is smooth (no clicks/zipper at control points). Negative control: a build
that ignores its parameter does not track the sweep.
"""
import numpy as np
import pytest

from reaproof import paths
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_jsfx

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

SR = 48000
INPUT = S.sine(1000, dbfs=-6.0, seconds=2.0, sr=SR)   # steady tone, RMS ~ -9 dBFS
IN_RMS = A.rms_dbfs(INPUT)

# Gain param (index 0) envelope: 0 dB at t=0 -> -24 dB at t=2 s. FX param envelope
# values are in the param's plain units here (the [-24,24] linear knob), shape 0=linear.
ENV_LUA = """
local env = reaper.GetFXEnvelope(tr, fx, 0, true)
reaper.DeleteEnvelopePointRange(env, -1, 1000000)
reaper.InsertEnvelopePoint(env, 0.0, 0.0, 0, 0, false, true)
reaper.InsertEnvelopePoint(env, 2.0, -24.0, 0, 0, false, true)
reaper.Envelope_SortPoints(env)
"""


@pytest.mark.gate
def test_automation_tracks_envelope_and_is_smooth():
    r = render_through_jsfx("JS: ReaProof Gain", jsfx_files=sorted(
        (paths.EXAMPLES / "jsfx").glob("ReaProof_Gain.jsfx")),
        params={}, input_signal=INPUT, extra_setup=ENV_LUA, name="autom")
    A.assert_no_pathology(r.samples)                       # smooth: no clicks/zipper
    env = A.rms_envelope_dbfs(r.samples, SR, window_ms=50)
    # tracks: starts near the input (gain 0 dB), ends ~24 dB lower (gain -24 dB)
    assert abs(env[0] - IN_RMS) < 2.0, f"start {env[0]:.1f} vs input {IN_RMS:.1f}"
    assert abs(env[-1] - (IN_RMS - 24.0)) < 3.0, f"end {env[-1]:.1f} vs {IN_RMS-24:.1f}"
    # smoothly decreasing (sample-accurate tracking, no jumps up)
    assert (np.diff(env) <= 0.5).mean() > 0.9, "envelope is not a smooth descent"


@pytest.mark.negative_control
def test_automation_not_tracked_by_ignore_build():
    """A build that ignores its parameter outputs a FLAT level despite the envelope —
    so the 'tracks the sweep' assertion would go RED."""
    r = render_through_jsfx("JS: ReaProof Gain BROKEN (ignores its parameter)",
        jsfx_files=sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain_BrokenIgnore.jsfx")),
        params={}, input_signal=INPUT, extra_setup=ENV_LUA, name="autom-neg")
    env = A.rms_envelope_dbfs(r.samples, SR, window_ms=50)
    # the good build drops ~24 dB across the sweep; a non-tracking build barely moves
    assert (env[0] - env[-1]) < 6.0, f"ignore-build wrongly tracked: {env[0]-env[-1]:.1f} dB drop"
