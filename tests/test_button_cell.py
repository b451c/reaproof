"""Second control type — a TOGGLE BUTTON — proving the platform generalises beyond the
rotary knob (REFERENCE §7). Demonstrates the button cells: DSP per state, visual on/off
states + redraw, and click interaction. Each is mutation-verified / has a negative control.
"""
import pytest

from reaproof import paths
from reaproof.mutation import mutation_check, scale
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S
from reaproof.observe.audio.render import render_through_jsfx
from reaproof.observe.input import WindowGesture
from reaproof.observe.visual.capture import capture_stable
from reaproof.observe.visual.diff import compare
from reaproof.observe.visual.vision import assert_no_glitch
from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Mute_Button.jsfx"))
FX = "JS: ReaProof Mute (toggle button)"
TITLE = "ReaProof Mute (toggle button)"
INPUT = S.sine(1000, dbfs=-6.0, seconds=1.0)


@pytest.mark.gate
def test_button_dsp_per_state():
    # Off -> passthrough (bit-transparent null vs input), mutation-verified
    off = render_through_jsfx(FX, jsfx_files=JSFX, params={0: 0.0}, input_signal=INPUT, name="btn-off")
    n = min(len(off.samples), len(INPUT))

    def is_null(samples):
        assert A.null_test_dbfs(samples[:n], INPUT[:n]) < -80, "Off state not transparent"

    mutation_check(off.samples, is_null, [("inject x0.5", scale(0.5))]).raise_if_vacuous()
    is_null(off.samples)

    # On -> muted (silent)
    on = render_through_jsfx(FX, jsfx_files=JSFX, params={0: 1.0}, input_signal=INPUT, name="btn-on")
    assert A.rms_dbfs(on.samples) < -100, "On state did not mute"


@pytest.mark.gate
def test_button_visual_states_differ_and_redraw():
    with ReaperSession("btnvis", jsfx=JSFX) as s:
        s.eval(f"""reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'{FX}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,0,0); reaper.TrackFX_Show(tr,fx,3); return fx""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
        off = capture_stable(s, TITLE, s.profile.root / "off.png").image
        s.eval("reaper.TrackFX_SetParam(reaper.GetTrack(0,0),0,0,1)")     # toggle On
        on = capture_stable(s, TITLE, s.profile.root / "on.png").image
    assert_no_glitch(off)
    assert_no_glitch(on)
    # the button visibly changes state on redraw (off gray <-> on green)
    d = compare(off, on)
    assert d.differs(max_fraction=0.01), f"Off/On frames did not differ ({d.changed_fraction:.4f})"


@pytest.mark.gate
def test_button_click_toggles_state():
    with ReaperSession("btnclick", jsfx=JSFX) as s:
        s.eval(f"""reaper.InsertTrackAtIndex(0,false); local tr=reaper.GetTrack(0,0)
        local fx=reaper.TrackFX_AddByName(tr,'{FX}',false,-1)
        reaper.TrackFX_SetParam(tr,fx,0,0); reaper.TrackFX_Show(tr,fx,3); return fx""")
        s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
        before = s.eval("return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
        WindowGesture(s, TITLE).click((0.5, 0.62))                        # click the button
        s.wait_until("reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0) > 0.5",
                     timeout=5, message="click toggled state")
        after = s.eval("return reaper.TrackFX_GetParam(reaper.GetTrack(0,0),0,0)")
    assert before < 0.5 and after >= 0.5, f"click did not toggle: {before} -> {after}"
