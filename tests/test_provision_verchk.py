"""Gate: the isolated profile must never let REAPER pop the app-modal
"New Version Notification" on startup.

That modal is gated by `[verchk] lastt`, NOT by `splashupdcheck` (which only
governs the splash). On an outdated pinned build it phones home and sets
`[NSApp modalWindow]`, silently polluting any test that cares about modality or
foreground state (it broke capture-mode arming in the MaxPane assessment).
The provisioner seeds a far-future `lastt` so REAPER believes it just checked.

Deterministic gate: the generated reaper.ini carries the seed (mutation: drop
it -> RED). REAPER-observable smoke: no "New Version" window after startup
(environment-gated negative control — only turns red when a newer REAPER
actually exists, which is the real-world hazard this guards against).
"""
import pytest

from reaproof.determinism import DeterminismLock
from reaproof.provision.base import get_provisioner


def test_generated_ini_seeds_future_verchk_stamp():
    prov = get_provisioner()
    profile = prov.assemble_profile("verchk-gate", DeterminismLock())
    ini = profile.ini_path.read_text()
    assert "[verchk]" in ini, "reaper.ini has no [verchk] section"
    # find the lastt under [verchk] and assert it is far in the future
    section = ini.split("[verchk]", 1)[1]
    lastt_line = next(
        (ln for ln in section.splitlines() if ln.strip().startswith("lastt=")), None
    )
    assert lastt_line, "[verchk] has no lastt stamp"
    stamp = int(lastt_line.split("=", 1)[1])
    # 2000000000 = 2033; anything comfortably beyond 2030 keeps REAPER offline.
    assert stamp > 1_900_000_000, f"lastt={stamp} is not a future stamp"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_no_update_notification_window_on_startup():
    from reaproof.runner.session import session
    with session("verchk-startup") as s:
        if not s.env.get("has_js_api"):
            pytest.skip("js_ReaScriptAPI unavailable — cannot enumerate windows")
        # give REAPER the window it would have popped time to appear, then assert
        # it never does. wait_until would pass instantly on absence, so poll a
        # fixed budget and assert absence throughout.
        import time
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            found = s.eval("reaper.JS_Window_Find('New Version', false) ~= nil")
            assert not found, "the update-notification modal appeared despite the verchk seed"
            time.sleep(0.5)
