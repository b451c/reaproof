"""Phase 3 verification gate (§14) — the hard last mile.

GATE: a knob visual test passes AND its mutation (force the indicator to the wrong
angle / desync drawn-vs-reported) turns it RED; a corrupted frame is caught by both
the perceptual diff and the vision check.
NEGATIVE CONTROL: the broken-angle build (correct DSP, knob drawn mirrored) is caught
by the dual-channel cross-check (reported value disagrees with the drawn angle).

Captures the real JSFX window (screencapture by CGWindowID). Marked reaper/slow.
"""
import time

import numpy as np
import pytest

from reaproof import paths
from reaproof.mutation import mutation_check, offset_value
from reaproof.observe.visual.capture import capture_stable
from reaproof.observe.visual.diff import compare
from reaproof.observe.visual.golden import GoldenKey, GoldenStore
from reaproof.observe.visual.knob import measure_knob_value
from reaproof.observe.visual.vision import assert_no_glitch, glitch_scan
from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

ALL_JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain*.jsfx"))
GOOD_TITLE = "ReaProof Gain (custom rotary knob)"
BROKEN_ANGLE_TITLE = "ReaProof Gain BROKEN (knob drawn at wrong angle)"
VMIN, VMAX = -24.0, 24.0
# 1.5 dB agreement: comfortably catches a mis-drawn angle (the mutation shifts by 12 dB)
# yet absorbs the ~0.15 dB pixel-quantisation seen across the sweep.
AGREE_TOL = 1.5


def _open(session, title_desc):
    session.eval(f"""
    while reaper.CountTracks(0)>0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
    reaper.InsertTrackAtIndex(0,false)
    local tr=reaper.GetTrack(0,0)
    local fx=reaper.TrackFX_AddByName(tr, 'JS: {title_desc}', false, -1)
    reaper.TrackFX_Show(tr, fx, 3)
    return fx""")
    session.wait_until(f'reaper.JS_Window_Find("{title_desc}", false) ~= nil',
                       timeout=10, message="fx window")


def _set_and_measure(session, title_desc, set_db, out):
    reported = session.eval(
        f"reaper.TrackFX_SetParam(reaper.GetTrack(0,0),0,0,{set_db}); "
        f"return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
    cap = capture_stable(session, title_desc, out)
    reading = measure_knob_value(cap.image, vmin=VMIN, vmax=VMAX)
    return reported, reading, cap


@pytest.mark.gate
def test_knob_dual_channel_and_mutation():
    """Good knob: reported value and drawn angle AGREE, and the agreement assertion
    is mutation-verified (a forced wrong angle turns it RED)."""
    with ReaperSession("p3good", jsfx=ALL_JSFX) as s:
        _open(s, GOOD_TITLE)
        reported, reading, cap = _set_and_measure(s, GOOD_TITLE, -6.0, s.profile.root / "good.png")
    assert_no_glitch(cap.image)

    def assert_agrees(drawn_value):
        assert abs(drawn_value - reported) <= AGREE_TOL, \
            f"drawn {drawn_value:.2f} vs reported {reported:.2f}"

    # clean PASS + the §1.3 mutation: a wrong angle (shift the drawn value) must RED-out
    report = mutation_check(reading.value, assert_agrees,
                            [("desync +12 dB", offset_value(12.0)),
                             ("desync -12 dB", offset_value(-12.0))])
    report.raise_if_vacuous()
    assert report.clean_passed and not report.vacuous
    assert abs(reading.value - reported) <= AGREE_TOL  # the real dual-channel check


@pytest.mark.negative_control
def test_broken_angle_build_caught_by_dual_channel():
    """NEGATIVE CONTROL: correct DSP but mirrored knob -> dual-channel DISAGREES."""
    with ReaperSession("p3broken", jsfx=ALL_JSFX) as s:
        _open(s, BROKEN_ANGLE_TITLE)
        reported, reading, cap = _set_and_measure(
            s, BROKEN_ANGLE_TITLE, -6.0, s.profile.root / "broken.png")
    # the engine reports -6 dB; the knob is drawn at +6 dB -> must NOT agree
    assert abs(reading.value - reported) > AGREE_TOL, (
        f"dual-channel missed the mis-drawn knob: drawn {reading.value:.2f} "
        f"vs reported {reported:.2f}")


@pytest.mark.gate
def test_golden_match_and_corrupted_frame_caught():
    """Golden approval + a corrupted frame caught by perceptual diff AND vision."""
    store = GoldenStore(paths.CACHE / "test_goldens")
    key = GoldenKey(plugin="ReaProof_Gain", version="0.0.1", control="GainKnob",
                    state="gain=-6db")
    with ReaperSession("p3gold", jsfx=ALL_JSFX) as s:
        _open(s, GOOD_TITLE)
        _, _, cap = _set_and_measure(s, GOOD_TITLE, -6.0, s.profile.root / "g.png")
    img = cap.image
    # first run: no golden -> approve it (records approver + reason)
    store.approve(img, key, approver="phase3-gate", reason="initial canonical capture")
    again = store.compare(img, key)
    assert again.exists and again.passed, again.note

    # a corrupted frame must be caught by BOTH the diff vs golden and the vision scan
    corrupted = img.copy()
    corrupted[:, :] = 0  # blacked-out frame
    d = compare(store_img(store, key), corrupted)
    assert d.differs(max_fraction=0.002), "perceptual diff missed the corrupted frame"
    assert glitch_scan(corrupted), "vision scan missed the corrupted frame"
    # and the green vision check never overrides a real disagreement:
    with pytest.raises(Exception):
        assert_no_glitch(corrupted)


def store_img(store: GoldenStore, key: GoldenKey) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(store.path(key)).convert("RGB"), dtype=np.uint8)
