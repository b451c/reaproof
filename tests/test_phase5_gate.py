"""Phase 5 verification gate (§14) — host (macOS) portion.

GATE (host): a window moved to a second monitor renders and reports correctly
(dual-channel still agrees); a DPI/size clipping regression is caught.

Windows + Linux are implemented as provisioner/capture/input backends but claimed
only via CI (PENDING CI) — never asserted green on an OS we did not run on.
"""
import time

import numpy as np
import pytest

from reaproof import paths
from reaproof.observe.visual.capture import capture_stable
from reaproof.observe.visual.knob import measure_knob_value
from reaproof.observe.visual.vision import glitch_scan
from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain.jsfx"))
TITLE = "ReaProof Gain (custom rotary knob)"


def _displays():
    import Quartz
    err, ids, n = Quartz.CGGetActiveDisplayList(8, None, None)
    out = []
    for d in ids[:n]:
        b = Quartz.CGDisplayBounds(d)
        out.append((int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height)))
    return out


@pytest.mark.gate
def test_second_monitor_renders_and_reports():
    mons = _displays()
    if len(mons) < 2:
        pytest.skip("single display — multi-monitor leg needs >=2 (or CI)")
    mx, my, mw, mh = mons[1]
    with ReaperSession("p5mm", jsfx=JSFX) as s:
        s.eval(f"""reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'JS: {TITLE}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,0,-6.0); reaper.TrackFX_Show(tr,fx,3); return fx""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
        s.eval(f"""local h=reaper.JS_Window_Find("{TITLE}", false)
        reaper.JS_Window_SetPosition(h, {mx + 150}, {my + 150}, 554, 321); return true""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=5)
        cap = capture_stable(s, TITLE, s.profile.root / "mon2.png")
        reported = s.eval("return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
    drawn = measure_knob_value(cap.image, vmin=-24, vmax=24).value
    assert abs(drawn - reported) <= 1.5, f"on monitor 2: drawn {drawn:.2f} vs reported {reported:.2f}"


@pytest.mark.negative_control
def test_clipping_regression_is_caught():
    """A clipped control (knob cut off by a too-small/wrong-DPI viewport) must be
    caught, never read as a clean pass. We take a REAL capture and clip out the knob
    region (as a too-small viewport would), then assert the dual-channel measurement
    can no longer find the indicator. (REAPER renders the full @gfx to its window
    buffer, so producing the clip in-app needs a real over-DPI/overflow control -
    PENDING such a subject / CI; the DETECTION is what is verified here.)"""
    with ReaperSession("p5clip", jsfx=JSFX) as s:
        s.eval(f"""reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'JS: {TITLE}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,0,-6.0); reaper.TrackFX_Show(tr,fx,3); return fx""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
        cap = capture_stable(s, TITLE, s.profile.root / "good.png")
    clipped = cap.image.copy()
    clipped[int(clipped.shape[0] * 0.30):, :] = 0   # knob region clipped off (blacked)
    # the indicator is gone -> the dual-channel measurement must FAIL, never read clean
    with pytest.raises(ValueError):
        measure_knob_value(clipped, vmin=-24, vmax=24)
    # a fully blank/clipped frame (no chrome either) is additionally caught by vision
    assert glitch_scan(np.zeros_like(clipped)), "vision scan failed to flag a blank frame"
