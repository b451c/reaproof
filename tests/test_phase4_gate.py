"""Phase 4 verification gate (§14).

GATE: a drag-the-knob test verifies reported value AND drawn position track the
gesture, runs identically twice, and its mutation (drag to a different endpoint)
turns RED.

Drives the real @gfx knob with synthetic OS input (CGEvent) and reads back both
channels (reported value + drawn angle). Marked reaper/slow.
"""
import pytest

from reaproof import paths
from reaproof.observe.input import WindowGesture
from reaproof.observe.visual.capture import capture_stable
from reaproof.observe.visual.knob import measure_knob_value
from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

ALL_JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain.jsfx"))
TITLE = "ReaProof Gain (custom rotary knob)"
VMIN, VMAX = -24.0, 24.0
START = -6.0
DRAG_PX_FRAC = 0.15           # ~48 px on the ~321 px window => ~12 dB at 0.25 dB/px


def _drag_and_read(direction: str):
    """Open the knob at START, drag up or down, return (start, reported_end, drawn)."""
    with ReaperSession("p4", jsfx=ALL_JSFX) as s:
        s.eval(f"""
        while reaper.CountTracks(0)>0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
        reaper.InsertTrackAtIndex(0,false)
        local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'JS: {TITLE}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,0,{START})
        reaper.TrackFX_Show(tr,fx,3)
        return fx""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
        start = s.eval("return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
        g = WindowGesture(s, TITLE)
        dy = -DRAG_PX_FRAC if direction == "up" else DRAG_PX_FRAC
        g.drag((0.5, 0.55), (0.5, 0.55 + dy), steps=48)
        # wait_until the reported value settles away from the start (gesture applied)
        s.wait_until(f"math.abs(reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0) - "
                     f"({start})) > 3", timeout=8, message="drag moved the value")
        reported_end = s.eval("return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
        cap = capture_stable(s, TITLE, s.profile.root / f"drag_{direction}.png")
        drawn = measure_knob_value(cap.image, vmin=VMIN, vmax=VMAX).value
    return start, reported_end, drawn


@pytest.mark.gate
def test_drag_tracks_gesture_on_both_channels_and_is_deterministic():
    s1, end1, drawn1 = _drag_and_read("up")
    # (1) reported value tracks the gesture (drag up => louder)
    assert end1 - s1 >= 6.0, f"drag up did not raise the value ({s1:.1f} -> {end1:.1f})"
    # (2) DUAL-CHANNEL: the drawn knob angle tracks the reported value
    assert abs(drawn1 - end1) <= 2.0, f"drawn {drawn1:.1f} vs reported {end1:.1f}"
    # (3) determinism: an identical drag lands within tolerance (else quarantine, §1.4)
    s2, end2, drawn2 = _drag_and_read("up")
    assert abs(end1 - end2) <= 2.0, f"drag not repeatable: {end1:.1f} vs {end2:.1f}"


@pytest.mark.negative_control
def test_drag_to_different_endpoint_turns_red():
    """MUTATION/NEG: dragging to a different endpoint (down) gives the opposite
    result, so an 'increased' expectation goes RED — the gesture endpoint matters."""
    s, end, drawn = _drag_and_read("down")
    # opposite endpoint => value decreased; the gate's 'increased >= 6' would be RED
    assert end - s <= -6.0, f"drag down did not lower the value ({s:.1f} -> {end:.1f})"
    # dual-channel still holds: the drawn angle matches the (now lower) reported value
    assert abs(drawn - end) <= 2.0, f"drawn {drawn:.1f} vs reported {end:.1f}"
