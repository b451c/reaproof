"""Gate: ReaperSession.restart() — assemble once, launch N times (Audit v2 #6).

Persistence tests (startup behaviour, ExtState carry-over) need run 2 to see
run 1's disk state. The footgun: ``assemble_profile`` (hence a second
``start()``/session) rmtrees the profile root, silently wiping run 1.
``restart()`` quits REAPER cleanly (the only path on which REAPER flushes
persisted ExtState), relaunches the SAME profile, and re-waits ready — safe
against stale IPC thanks to the launch() queue purge (fix #3).

- gate (REAPER): persisted ExtState set in run 1 is read back in run 2 after
  restart(); a marker file proves the profile was NOT reassembled.
- negative control (REAPER): the wrong pattern — a NEW session — cannot see
  the state (reads ""), proving the gate's readback assertion can turn RED.
- unit: restart() on a never-started session fails loudly.
"""
import pytest

from reaproof.runner.session import ReaperSession

pytestmark = [pytest.mark.reaper, pytest.mark.slow]

SECTION = "rp_restart_gate"


@pytest.mark.gate
def test_restart_preserves_persisted_extstate():
    with ReaperSession("restart-persist") as s:
        first_pid = s.handle.pid
        s.eval(f'reaper.SetExtState("{SECTION}","token","alpha42",true); return true')
        marker = s.profile.resource_dir / "_reaproof_restart_marker"
        marker.write_text("assembled once")

        s.restart()

        assert s.handle.pid != first_pid, "restart did not launch a new process"
        got = s.eval(f'return reaper.GetExtState("{SECTION}","token")')
        assert got == "alpha42", f"persisted ExtState lost across restart: {got!r}"
        assert marker.exists(), "profile was reassembled — restart must reuse it"
        assert s.eval("return 7") == 7  # bridge fully live on the new instance


@pytest.mark.negative_control
def test_fresh_session_does_not_see_prior_state():
    """MUTATION: the wrong pattern (new session = new assemble) — the gate's
    readback turns RED ("" instead of the token), proving restart() is what
    preserves state, not some cross-profile leak."""
    with ReaperSession("restart-neg") as s1:
        s1.eval(f'reaper.SetExtState("{SECTION}_neg","token","alpha42",true); return true')
    with ReaperSession("restart-neg") as s2:
        got = s2.eval(f'return reaper.GetExtState("{SECTION}_neg","token")')
        assert got == "", f"fresh profile saw prior state ({got!r}) — isolation broken"


def test_restart_before_start_fails_loudly():
    s = ReaperSession("restart-unstarted")
    with pytest.raises(AssertionError):
        s.restart()
