"""Gates: native-extension zero-code battery (Universality Roadmap U3).

The three reference extensions span the outcome space and ARE the mutations:
- testext registers an action -> the observable-diff stage must find it;
- noopext loads but registers nothing -> the honest-warning path;
- a mis-named dylib is never loaded by REAPER -> static naming FAIL.
(The deliberately-faulty variant is exercised by the opt-in U1 fault gate.)
"""
import subprocess
import sys

import pytest

from reaproof import paths
from reaproof.runner.exttest import ExtTestOptions, run_extension_battery

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="extension battery targets the macOS backend")


def _ensure_subjects():
    if not (paths.EXT_TEST.exists() and paths.EXT_NOOP.exists()):
        script = paths.EXAMPLES / "ext" / "build_ext.sh"
        subprocess.run(["bash", str(script), str(paths.EXT_TEST.parent)],
                       check=True, capture_output=True)


def _statuses(rs):
    return {r.name: r.status for r in rs.results}


def _quiet(*a, **k):
    pass


def test_misnamed_dylib_fails_statically(tmp_path):
    lib = tmp_path / "libnotanextension.dylib"
    lib.write_bytes(b"not a real dylib")
    rs = run_extension_battery(lib, log=_quiet)
    st = _statuses(rs)
    assert st["naming: reaper_*.dylib contract"] == "failed"
    assert not rs.gate_green


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_registering_extension_is_proven_by_diff(tmp_path):
    _ensure_subjects()
    rs = run_extension_battery(paths.EXT_TEST, out_dir=tmp_path, log=_quiet)
    st = _statuses(rs)
    assert st["load: attempted by REAPER (splashlog)"] == "passed"
    reg = next(r for r in rs.results
               if r.name == "register: observable actions/APIs")
    assert reg.status == "passed"
    assert any("REAPROOF_TEST_EXT_PING" in a
               for a in reg.provenance["new_actions"] + [reg.message])
    # the diff must be SURGICAL: exactly our one action, not an
    # instance-id-churn avalanche (the melt-down this design prevents)
    assert len(reg.provenance["new_actions"]) <= 3, reg.provenance["new_actions"]
    assert rs.gate_green


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_hook_only_extension_is_an_honest_warning(tmp_path):
    """MUTATION: an extension that registers NOTHING must not fabricate a
    'registered' pass — the stage lands on the visible honest skip."""
    _ensure_subjects()
    rs = run_extension_battery(paths.EXT_NOOP, out_dir=tmp_path, log=_quiet)
    st = _statuses(rs)
    assert st["load: attempted by REAPER (splashlog)"] == "passed"
    assert st["register: observable actions/APIs"] == "skipped"
    assert rs.gate_green    # green WITH the skip visible, not a fake 'verified'


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_run_actions_smoke_passes_on_clean_action(tmp_path):
    """Composition gate: the per-action smoke (dialog+hygiene+liveness
    machinery, each proven red-able in U1/U2 gates) reports the ping action
    clean and its effect observable."""
    _ensure_subjects()
    rs = run_extension_battery(paths.EXT_TEST, out_dir=tmp_path,
                               opts=ExtTestOptions(run_actions=True), log=_quiet)
    smoke = [r for r in rs.results if ": smoke" in r.name]
    assert smoke, "no per-action smoke results"
    assert all(r.status == "passed" for r in smoke), [
        (r.name, r.status, r.message) for r in smoke]
    assert rs.gate_green
