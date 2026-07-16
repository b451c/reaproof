"""Gates: Universal Watchdog plane (Universality Roadmap U1).

- CrashReportWatcher units run against a TEMP diagnostics dir with planted
  report files (the matching/baseline logic is the unit; the OS writer is not
  ours to test).
- The DialogMonitor gates use REAL modal panels in a live REAPER — verified
  behaviour on the pinned build: modal panels sit at CGWindowLayer 8 and BLOCK
  the bridge, so the monitor must see and dismiss them from outside.
- The hygiene differ has pure-function units + a live leak gate.
- The end-to-end fault gate (a real segfaulting extension) is OPT-IN via
  REAPROOF_FAULT_GATES=1: it necessarily triggers the OS crash-reporter UI,
  which is invasive on a workstation. The detection logic it exercises is
  fully covered by the planted-report units either way.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.observe import hygiene
from reaproof.observe.watchdog import CrashReportWatcher, DialogMonitor, report_pid

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="U1 watchdog backend is macOS")


# ---- CrashReportWatcher units (planted reports, real matching logic) --------

def _plant(diag: Path, name: str, pid: int | None = None) -> Path:
    diag.mkdir(parents=True, exist_ok=True)
    p = diag / name
    header = {"app_name": "REAPER", "name": "REAPER"}
    if pid is not None:
        header["pid"] = pid
    p.write_text(json.dumps(header) + "\n{...body...}\n")
    return p


def test_preexisting_reports_are_not_ours(tmp_path):
    _plant(tmp_path, "REAPER-2026-01-01-000000.ips", pid=111)
    w = CrashReportWatcher(diag_dir=tmp_path)
    assert w.new_reports() == []          # baseline excluded
    fresh = _plant(tmp_path, "REAPER-2026-07-16-120000.ips", pid=222)
    assert w.new_reports() == [fresh]


def test_pid_filter_excludes_other_instances(tmp_path):
    w = CrashReportWatcher(diag_dir=tmp_path, pid=555)
    _plant(tmp_path, "REAPER-2026-07-16-120001.ips", pid=444)   # someone else
    ours = _plant(tmp_path, "REAPER-2026-07-16-120002.ips", pid=555)
    assert w.new_reports() == [ours]


def test_collect_copies_reports_to_artifacts(tmp_path):
    w = CrashReportWatcher(diag_dir=tmp_path / "diag")
    _plant(tmp_path / "diag", "REAPER-2026-07-16-120003.ips", pid=1)
    copied = w.collect(tmp_path / "art", timeout=0.0)
    assert len(copied) == 1 and copied[0].exists()


def test_report_pid_parses_header(tmp_path):
    p = _plant(tmp_path, "REAPER-x.ips", pid=987)
    assert report_pid(p) == 987
    legacy = tmp_path / "REAPER-legacy.hang"
    legacy.write_text("Process: REAPER [123]\n")   # non-JSON header
    assert report_pid(legacy) is None


# ---- hygiene differ pure-function units -------------------------------------

def _snap(**over):
    data = {"state_count": 10, "dirty": 0, "undo": "", "tracks": 1,
            "items": 0, "track_guids": ["{A}"], "projextstate_sections": 0}
    data.update(over)
    return hygiene.HygieneSnapshot(data=data, windows=frozenset({"REAPER"}),
                                   extstate=b"")


def test_hygiene_clean_pair_has_no_findings():
    assert hygiene.diff(_snap(), _snap()) == []


def test_hygiene_names_each_leak():
    before = _snap()
    after = hygiene.HygieneSnapshot(
        data={"state_count": 11, "dirty": 1, "undo": "Add track",
              "tracks": 2, "items": 1, "track_guids": ["{A}", "{B}"],
              "projextstate_sections": 1},
        windows=frozenset({"REAPER", "Leaked Window"}),
        extstate=b"[x]\nk=v\n")
    findings = hygiene.diff(before, after)
    text = "\n".join(findings)
    for needle in ("track count", "item count", "DIRTY", "undo",
                   "ProjExtState", "ExtState", "Leaked Window"):
        assert needle in text, f"missing finding for {needle}: {findings}"


def test_hygiene_expected_changes_are_suppressed():
    before = _snap()
    after = _snap(state_count=11, dirty=1, undo="Add track")
    assert hygiene.diff(before, after, expect_project_change=True) == []


# ---- live gates --------------------------------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_dialog_monitor_names_blocks_and_dismisses():
    from reaproof.control.bridge_client import BridgeHang, BridgeTimeout
    from reaproof.runner.session import ReaperSession

    with ReaperSession("watchdog-dialog") as s:
        # splashlog forensics landed with the launch (U1.4)
        splash = s.profile.artifacts_dir / "splash.log"
        assert splash.exists()
        assert "Loading plug-in: reaper_sws" in splash.read_text(
            encoding="utf-8", errors="replace")

        with DialogMonitor(s.handle.pid,
                           evidence_dir=s.profile.artifacts_dir / "dialogs") as mon:
            # negative control FIRST: a quiet session shows no modal panel
            time.sleep(1.0)
            assert mon.sightings() == [], "phantom panel on a quiet session"

            s.eval('reaper.defer(function() '
                   'reaper.ShowMessageBox("body","RP Watchdog Gate",0) end); '
                   'return true')
            # NB: a message-box panel carries NO kCGWindowName (verified) —
            # it is sighted as an untitled modal panel; only the "ReaScript
            # Error" panel is named. The layer is the discriminator.
            seen = mon.wait_for_panel(timeout=10)
            assert seen.layer >= 8 and seen.width > 100 and seen.height > 80
            # the panel blocks REAPER's main thread -> bridge must be dead
            with pytest.raises((BridgeHang, BridgeTimeout)):
                s.eval("return 1", timeout=2, hang_timeout=1.5)
            assert mon.dismiss(), f"panel not dismissed: {mon.active()}"
            assert s.eval("return 42") == 42     # bridge resumed
            evidence = list((s.profile.artifacts_dir / "dialogs").glob("*.png"))
            assert evidence, "no screenshot evidence for the sighted panel"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_reascript_error_panel_is_named_evidence():
    """A ReaScript runtime error pops the named "ReaScript Error" panel. The
    watchdog names + dismisses it from outside.

    FINDING (verified, documented as D29): unlike an app-modal ShowMessageBox
    (whose dismissal revives the bridge), a ReaScript error is TERMINAL for the
    whole defer engine — REAPER's bridge (itself a deferred script) stops
    ticking and does not come back on dismissal. So the honest post-condition
    is that the bridge is wedged (eval raises), not that it resumes. This is
    the mechanism that forces U2 to run a script-under-test's code via a bridge
    pcall (so its error never reaches REAPER's handler) or to relaunch."""
    from reaproof.control.bridge_client import BridgeHang, BridgeTimeout
    from reaproof.runner.session import ReaperSession

    with ReaperSession("watchdog-scripterr") as s:
        bad = s.profile.resource_dir / "Scripts" / "rp_bad.lua"
        bad.write_text('error("watchdog gate boom")\n')
        cmd = s.eval(f"return reaper.AddRemoveReaScript(true, 0, [[{bad}]], true)")
        assert cmd and cmd > 0
        with DialogMonitor(s.handle.pid) as mon:
            s.eval(f"reaper.defer(function() reaper.Main_OnCommand({cmd}, 0) end); "
                   "return true")
            seen = mon.wait_for_panel(title_substring="ReaScript Error", timeout=10)
            assert seen.title == "ReaScript Error"    # the panel is NAMED
            assert mon.dismiss()                       # and dismissable
            # the process is still up, but the defer engine (bridge) is dead
            assert s.is_alive
            with pytest.raises((BridgeHang, BridgeTimeout)):
                s.eval("return 7", timeout=3, hang_timeout=2)


@pytest.mark.reaper
@pytest.mark.slow
def test_hygiene_live_leak_and_clean():
    from reaproof.runner.session import ReaperSession

    with ReaperSession("watchdog-hygiene") as s:
        a = hygiene.snapshot(s)
        b = hygiene.snapshot(s)
        assert hygiene.diff(a, b) == []              # negative control: no-op
        s.eval("reaper.InsertTrackAtIndex(0,false); return true")
        c = hygiene.snapshot(s)
        findings = hygiene.diff(b, c)
        assert any("track count" in f for f in findings), findings
        s.eval("reaper.DeleteTrack(reaper.GetTrack(0,0)); return true")


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("REAPROOF_FAULT_GATES") != "1",
                    reason="opt-in (spawns the OS crash-reporter UI): "
                           "set REAPROOF_FAULT_GATES=1")
def test_real_fault_produces_named_evidence():
    from reaproof.runner.session import ReaperSession

    script = paths.EXAMPLES / "ext" / "build_ext.sh"
    if not paths.EXT_FAULT.exists():
        subprocess.run(["bash", str(script), str(paths.EXT_FAULT.parent)],
                       check=True, capture_output=True)
    s = ReaperSession("watchdog-fault", extensions=[paths.EXT_FAULT])
    try:
        s.start()
        cmd = s.eval("return reaper.NamedCommandLookup('_REAPROOF_TEST_EXT_FAULT')")
        assert cmd and cmd > 0
        s.eval(f"reaper.defer(function() reaper.Main_OnCommand({cmd}, 0) end); "
               "return true")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and s.is_alive:
            time.sleep(0.25)
        assert not s.is_alive, "deliberate fault did not terminate the process"
        reports = s.crash_watcher.collect(
            s.profile.artifacts_dir / "diagnostics", timeout=25)
        assert reports, "no diagnostic report collected for the fault"
    finally:
        s.stop()
