"""Gates: LV2 format support (Universality Roadmap U4).

- metadata validation via lv2_validate (good bundle passes; a syntactically
  corrupted manifest turns RED — the mutation);
- REAPER discovery/instantiation through the controlled ``lv2path_mac`` scan
  dir (verified live in the U4 spike);
- DSP null-test through the real LV2 binary: at -6 dB the good build lands at
  the dialed gain; the broken build (gain applied at HALF the dB — the classic
  knob-lies defect) lands measurably elsewhere. The broken subject IS the
  mutation for the render assertion.
"""
import shutil
import subprocess
import sys

import numpy as np
import pytest

from reaproof import paths
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.validators.lv2 import lv2_validate_available, run_lv2_validate

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="exercises the macOS lv2path_mac key")

needs_lv2_tools = pytest.mark.skipif(
    not lv2_validate_available(), reason="lv2_validate not installed (brew install lv2 sord)")

FX_NAME = "LV2: ReaProof Gain LV2"
FX_BROKEN = "LV2: ReaProof Gain LV2-broken"
SET_MINUS_6 = (
    # LV2 control-port params hold their value while idle (unlike CLAP's
    # event-queue params). TrackFX_SetParam speaks the PLAIN declared scale
    # (same units TrackFX_GetParam reports — verified: a normalized 0.375
    # passed straight through as +0.375 dB), so -6 dB is simply -6.0.
    "reaper.TrackFX_SetParam(tr, fx, 0, -6.0)"
)


def _ensure_subjects():
    if not (paths.LV2_GOOD.exists() and paths.LV2_BROKEN.exists()):
        script = paths.EXAMPLES / "lv2" / "build_lv2.sh"
        subprocess.run(["bash", str(script), str(paths.LV2_GOOD.parent)],
                       check=True, capture_output=True)


# ---- metadata validation ------------------------------------------------------

@needs_lv2_tools
def test_good_bundle_validates(tmp_path):
    _ensure_subjects()
    r = run_lv2_validate(paths.LV2_GOOD, artifacts_dir=tmp_path)
    assert r.passed, r.summary()
    assert r.total > 0


@needs_lv2_tools
@pytest.mark.negative_control
def test_corrupted_manifest_turns_red(tmp_path):
    _ensure_subjects()
    broken = tmp_path / "corrupt.lv2"
    shutil.copytree(paths.LV2_GOOD, broken)
    ttl = broken / "manifest.ttl"
    ttl.write_text(ttl.read_text().replace("lv2:binary", "lv2:binry"), encoding="utf-8")
    r = run_lv2_validate(broken, artifacts_dir=tmp_path)
    assert not r.passed, "undefined property was not flagged"


@needs_lv2_tools
def test_empty_bundle_is_never_a_pass(tmp_path):
    empty = tmp_path / "empty.lv2"
    empty.mkdir()
    r = run_lv2_validate(empty, artifacts_dir=tmp_path)
    assert not r.passed        # 0-of-0 verified nothing


# ---- REAPER hosting + DSP ------------------------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_lv2_discovered_and_transparent_at_unity():
    from reaproof.runner.session import ReaperSession
    _ensure_subjects()
    with ReaperSession("lv2-load", plugins=[paths.LV2_GOOD]) as s:
        res = s.eval(f"""
        while reaper.CountTracks(0)>0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
        reaper.InsertTrackAtIndex(0,false)
        local tr = reaper.GetTrack(0,0)
        local fx = reaper.TrackFX_AddByName(tr, '{FX_NAME}', false, -1)
        return {{fx = fx, n = fx >= 0 and reaper.TrackFX_GetNumParams(tr, fx) or 0}}""")
        assert res["fx"] >= 0, "LV2 subject not discovered via lv2path_mac"
        assert res["n"] >= 1


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_lv2_render_minus6_and_broken_is_caught(tmp_path):
    from reaproof.observe.audio.render import render_through_plugin
    _ensure_subjects()
    sig = S.sine(1000.0, dbfs=-12.0, seconds=1.0, sr=48000)
    in_rms = A.rms_dbfs(sig)

    good = render_through_plugin(FX_NAME, plugin_files=[paths.LV2_GOOD],
                                 input_signal=sig, sample_rate=48000,
                                 extra_setup=SET_MINUS_6, name="lv2-good")
    delta_good = A.rms_dbfs(good.samples) - in_rms
    # -6 dB dialed => -6 dB measured (0.25 dB: render/param quantisation floor)
    assert abs(delta_good - (-6.0)) <= 0.25, f"good build applied {delta_good:.2f} dB"

    broken = render_through_plugin(FX_BROKEN, plugin_files=[paths.LV2_BROKEN],
                                   input_signal=sig, sample_rate=48000,
                                   extra_setup=SET_MINUS_6, name="lv2-broken")
    delta_broken = A.rms_dbfs(broken.samples) - in_rms
    # MUTATION: the broken build halves the dB — the -6 assertion must be RED
    assert abs(delta_broken - (-3.0)) <= 0.4, f"broken applied {delta_broken:.2f} dB"
    assert abs(delta_broken - (-6.0)) > 1.0
    # both renders are pathology-free real audio, not silence
    A.assert_no_pathology(good.samples)
    assert np.max(np.abs(good.samples)) > 0.01
