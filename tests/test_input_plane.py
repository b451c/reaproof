"""Gates: input-plane upgrades (Quality Audit v2 #5).

Three new primitives, each asserted on an OBSERVABLE read back through REAPER
(§1.1) — never on "the event was posted":

- right-click (`_MacMouse` right button, via WindowGesture.hold_click): observed
  by an in-REAPER defer poll recording the max of JS_Mouse_GetState(2) (the OS
  async right-button bit) while the gesture runs. Negative control: the SAME
  gesture with the LEFT button leaves bit 2 at 0 (and sets bit 1) — the bit-2
  assertion turns RED for the wrong primitive.
- keyboard (`type_text`, CGEvent unicode): observed by a gfx.getchar() defer
  loop accumulating typed chars into ExtState. Negative control: typing a
  DIFFERENT string yields that string, not the gate's — content-sensitive.
- in-process SWELL-dialog control press (`dialog_command`, WM_COMMAND):
  observed by the Actions window actually closing (IDCANCEL=2). Posted
  BM_CLICK / raw WM_LBUTTON* do NOT fire a standard SWELL button — that
  boundary is documented in observe/input.py. Negative control: a bogus
  command id leaves the window open (the wait for "closed" times out).

The right-click/keyboard gestures target the Gain JSFX @gfx canvas — an area
that pops no context menu, so no modal can block the bridge.
"""
import pytest

from reaproof import paths
from reaproof.control.bridge_client import BridgeTimeout
from reaproof.observe.input import WindowGesture, dialog_command, type_text
from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

TITLE = "ReaProof Gain (custom rotary knob)"
GAIN_JSFX = sorted((paths.EXAMPLES / "jsfx").glob("ReaProof_Gain.jsfx"))

MOUSE_POLL = """
reaper.SetExtState("rp_ms","maxr","0",false)
reaper.SetExtState("rp_ms","maxl","0",false)
reaper.SetExtState("rp_ms","run","1",false)
local function poll()
  local r = reaper.JS_Mouse_GetState(2)
  local l = reaper.JS_Mouse_GetState(1)
  if r > (tonumber(reaper.GetExtState("rp_ms","maxr")) or 0) then
    reaper.SetExtState("rp_ms","maxr",tostring(r),false)
  end
  if l > (tonumber(reaper.GetExtState("rp_ms","maxl")) or 0) then
    reaper.SetExtState("rp_ms","maxl",tostring(l),false)
  end
  if reaper.GetExtState("rp_ms","run") == "1" then reaper.defer(poll) end
end
poll()
return true"""

KEY_POLL = """
gfx.init("ReaProofKeys", 320, 120, 0, 60, 60)
reaper.SetExtState("rp_keys","buf","",false)
local function poll()
  local c = gfx.getchar()
  while c and c > 0 do
    if c >= 32 and c < 127 then
      reaper.SetExtState("rp_keys","buf",
        reaper.GetExtState("rp_keys","buf") .. string.char(c), false)
    end
    c = gfx.getchar()
  end
  if c and c >= 0 then reaper.defer(poll) end
end
poll()
return true"""


def _open_gain_fx(s: ReaperSession) -> WindowGesture:
    s.eval(f"""
    while reaper.CountTracks(0)>0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
    reaper.InsertTrackAtIndex(0,false)
    local tr=reaper.GetTrack(0,0)
    local fx=reaper.TrackFX_AddByName(tr,'JS: {TITLE}',false,-1)
    reaper.TrackFX_Show(tr,fx,3)
    return fx""")
    s.wait_until(f'reaper.JS_Window_Find("{TITLE}", false) ~= nil', timeout=10)
    return WindowGesture(s, TITLE)


def _held_bits(s: ReaperSession, gesture: WindowGesture, *, right: bool):
    """Run a hold-click over the @gfx canvas while an in-REAPER poll records
    the max observed OS button state; return (max_right, max_left)."""
    s.eval(MOUSE_POLL)
    try:
        gesture.hold_click((0.5, 0.55), hold=0.6, right=right)
        # the poll runs at defer rate (~30 Hz); a 0.6 s hold spans many ticks
        s.wait_until(
            f'reaper.GetExtState("rp_ms","max{"r" if right else "l"}") ~= "0"',
            timeout=5, message="hold registered in OS mouse state",
        )
    finally:
        s.eval('reaper.SetExtState("rp_ms","run","0",false); return true')
    maxr = int(s.eval('return reaper.GetExtState("rp_ms","maxr")'))
    maxl = int(s.eval('return reaper.GetExtState("rp_ms","maxl")'))
    return maxr, maxl


@pytest.mark.gate
def test_right_click_sets_os_right_button_bit():
    with ReaperSession("input-right", jsfx=GAIN_JSFX) as s:
        g = _open_gain_fx(s)
        maxr, _ = _held_bits(s, g, right=True)
        assert maxr == 2, f"right hold never observed (max state(2) = {maxr})"
        # button released cleanly — no stuck synthetic button
        s.wait_until("reaper.JS_Mouse_GetState(2) == 0", timeout=5,
                     message="right button released")


@pytest.mark.negative_control
def test_left_click_does_not_set_right_bit():
    """MUTATION: the same gesture with the LEFT button — the gate's bit-2
    assertion turns RED (state(2) stays 0) while bit 1 proves the click landed."""
    with ReaperSession("input-left", jsfx=GAIN_JSFX) as s:
        g = _open_gain_fx(s)
        maxr, maxl = _held_bits(s, g, right=False)
        assert maxl == 1, f"left hold never observed (max state(1) = {maxl})"
        assert maxr == 0, f"left click leaked into the right-button bit ({maxr})"


FOCUS_ON_KEYS = ('(function() local h = reaper.JS_Window_Find("ReaProofKeys", true); '
                 'return h ~= nil and reaper.JS_Window_GetFocus() == h end)()')


def _focused_key_window(s: ReaperSession):
    """Open the gfx key-catcher and WAIT until it actually holds keyboard
    focus (observable via JS_Window_GetFocus) — a fixed settle-sleep here is
    exactly the §1.5 race that made this gate flake under focus churn."""
    import subprocess

    from reaproof.control.bridge_client import BridgeTimeout
    s.eval(KEY_POLL)
    s.wait_until('reaper.JS_Window_Find("ReaProofKeys", true) ~= nil', timeout=5)
    for attempt in range(3):
        subprocess.run(["osascript", "-e",
                        'tell application "System Events" to set frontmost of '
                        f'(first process whose unix id is {s.handle.pid}) to true'],
                       capture_output=True)
        s.eval('local h=reaper.JS_Window_Find("ReaProofKeys", true);'
               'reaper.JS_Window_SetForeground(h); reaper.JS_Window_SetFocus(h); return true')
        try:
            s.wait_until(FOCUS_ON_KEYS, timeout=3,
                         message="gfx window holds keyboard focus")
            return
        except BridgeTimeout:
            if attempt == 2:
                raise


@pytest.mark.gate
def test_type_text_reaches_key_window():
    with ReaperSession("input-keys") as s:
        try:
            _focused_key_window(s)
            type_text("ab")
            s.wait_until('reaper.GetExtState("rp_keys","buf") == "ab"',
                         timeout=8, message="typed text observed by gfx.getchar")
        finally:
            s.eval("gfx.quit(); return true")


@pytest.mark.negative_control
def test_type_text_content_matters():
    """MUTATION: typing a different string yields that string — the gate's
    'ab' expectation would be RED, so the assertion is content-sensitive."""
    with ReaperSession("input-keys-neg") as s:
        try:
            _focused_key_window(s)
            type_text("xy")
            s.wait_until('reaper.GetExtState("rp_keys","buf") == "xy"',
                         timeout=8, message="mutated text observed")
            got = s.eval('return reaper.GetExtState("rp_keys","buf")')
            assert got != "ab"
        finally:
            s.eval("gfx.quit(); return true")


@pytest.mark.gate
def test_dialog_command_presses_swell_dialog_button():
    with ReaperSession("input-dialog") as s:
        s.eval("reaper.Main_OnCommand(40605, 0); return true")  # Show action list
        s.wait_until('reaper.JS_Window_Find("Actions", false) ~= nil', timeout=10)
        dialog_command(s, "Actions", 2)  # IDCANCEL == the Close button
        s.wait_until('reaper.JS_Window_Find("Actions", false) == nil',
                     timeout=8, message="Actions window closed by WM_COMMAND")


@pytest.mark.negative_control
def test_dialog_command_bogus_id_does_nothing():
    """MUTATION: a command id no control owns must NOT close the window — the
    'closed' wait times out, proving the gate's id is what does the work."""
    with ReaperSession("input-dialog-neg") as s:
        s.eval("reaper.Main_OnCommand(40605, 0); return true")
        s.wait_until('reaper.JS_Window_Find("Actions", false) ~= nil', timeout=10)
        dialog_command(s, "Actions", 9999)
        with pytest.raises(BridgeTimeout):
            s.wait_until('reaper.JS_Window_Find("Actions", false) == nil',
                         timeout=3, message="(expected to time out)")
        # window is demonstrably still there and live
        assert s.eval('return reaper.JS_Window_Find("Actions", false) ~= nil')
