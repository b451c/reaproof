"""Phase 0 verification gate (§14).

GATE: a "read project state" test passes AND runs identically twice.
NEGATIVE CONTROL: killing the bridge mid-test is reported as a hang/crash, never
a pass. (Plus a deliberately-wrong expectation must go RED — proving the gate
itself can fail.)

These launch the real pinned REAPER, so they are marked `reaper`/`slow`. Run:
    PYTHONPATH=src python -m pytest tests/test_phase0_gate.py -v
"""
import pytest

from reaproof.control.bridge_client import BridgeCrash, BridgeHang, BridgeTimeout
from reaproof.determinism import assert_identical
from reaproof.runner.session import ReaperSession, session

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

# Set known state through the host API, then read it back through the *serialized
# project state chunk* — a different code path from the one that set it (§1.1).
_SETUP = """
while reaper.CountTracks(0) > 0 do reaper.DeleteTrack(reaper.GetTrack(0,0)) end
reaper.InsertTrackAtIndex(0, false)
reaper.InsertTrackAtIndex(1, false)
reaper.GetSetMediaTrackInfo_String(reaper.GetTrack(0,0), 'P_NAME', 'GateProbeA', true)
reaper.GetSetMediaTrackInfo_String(reaper.GetTrack(0,1), 'P_NAME', 'GateProbeB', true)
return true
"""

_READBACK = """
local names = {}
for i = 0, reaper.CountTracks(0)-1 do
  local _, chunk = reaper.GetTrackStateChunk(reaper.GetTrack(0,i), '', false)
  names[#names+1] = chunk:match('NAME%s+"?([^"\\n]+)"?')
end
return { count = reaper.CountTracks(0), names = names }
"""


def _read_state_once() -> dict:
    with session("p0gate") as s:
        s.eval(_SETUP)
        s.wait_until("reaper.CountTracks(0) == 2", timeout=10, message="2 tracks exist")
        return s.eval(_READBACK)


@pytest.mark.gate
def test_read_state_passes_and_is_deterministic():
    """GATE: state read via an independent path, identical across two real runs."""
    runs = [_read_state_once(), _read_state_once()]
    # determinism (§1.4): two independent REAPER launches must agree exactly
    assert_identical(runs, what="project state")
    # correctness: the effect we observe matches the contract
    assert runs[0] == {"count": 2, "names": ["GateProbeA", "GateProbeB"]}


@pytest.mark.gate
def test_gate_can_fail_on_wrong_expectation():
    """The gate is not vacuous: a deliberately-wrong expectation goes RED."""
    state = _read_state_once()
    with pytest.raises(AssertionError):
        assert state["names"] == ["WRONG", "WRONG"]


@pytest.mark.negative_control
def test_killed_bridge_reports_crash_never_pass():
    """NEGATIVE CONTROL: a bridge killed mid-test must surface as crash/hang."""
    s = ReaperSession("p0neg").start()
    try:
        assert s.eval("return 1+1") == 2  # bridge demonstrably alive
        s.provisioner.terminate(s.handle)  # kill REAPER mid-session
        with pytest.raises((BridgeCrash, BridgeHang, BridgeTimeout)):
            s.eval("return 1+1", timeout=10)  # must NOT return a (stale) pass
    finally:
        s.stop()


@pytest.mark.negative_control
def test_wait_until_timeout_is_hard_failure():
    """A predicate that never holds is a hard failure (§1.5), not a silent pass."""
    with session("p0wait") as s:
        with pytest.raises(BridgeTimeout):
            s.wait_until("reaper.CountTracks(0) >= 9999", timeout=3, poll=0.1)
