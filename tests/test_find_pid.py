"""Gate: `_find_pid` latches the MAIN REAPER instance, never a helper (#8).

`pgrep -f "cfgfile {ini}"` matches every process carrying the cfgfile path —
including REAPER's transient plugin-scan helper (same ``-cfgfile`` plus a
``__vst_scan__`` marker). Naive ``pids[0]`` can latch the helper: liveness then
reports the session dead when the scan ends, and terminate() kills the helper
while the real REAPER runs on as an orphan.

REAPER-free tests use REAL processes (python sleepers with crafted argv), so
pgrep/ps behave exactly as in production — nothing is mocked:

- negative control: a scanner-marked decoy ALONE — the naive first-pgrep-hit IS
  the decoy (the trap is real); fixed `_find_pid` returns None instead.
- gate (unit): decoy spawned FIRST (lower pid) + a fake main — `_find_pid`
  returns the main.
- ppid heuristic: a helper CHILD carrying the cfgfile but no marker is excluded
  because its parent is also a match.
- gate (REAPER): a live session with a same-ini decoy scanner alongside —
  `_find_pid` still resolves to the supervised REAPER pid.
"""
import subprocess
import sys
import time

import pytest

from reaproof.provision.macos import _find_pid

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS provisioner")

SLEEP = "import time; time.sleep(60)"


def _pgrep(ini: str) -> list[int]:
    out = subprocess.run(["pgrep", "-f", f"cfgfile {ini}"],
                         capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def _wait_visible(ini: str, n: int, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(_pgrep(ini)) >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {n} processes matching {ini}, saw {_pgrep(ini)}")


@pytest.mark.negative_control
def test_naive_first_hit_latches_the_scan_helper(tmp_path):
    """The trap, proven with the naive strategy: a scanner-marked process is a
    pgrep hit, so old pids[0] returned IT. Fixed _find_pid returns None."""
    ini = str(tmp_path / "reaper.ini")
    decoy = subprocess.Popen(
        [sys.executable, "-c", SLEEP, "-cfgfile", ini, "-__vst_scan__"])
    try:
        _wait_visible(ini, 1)
        naive = _pgrep(ini)[0]
        assert naive == decoy.pid  # old behaviour: wrong process latched
        assert _find_pid(ini) is None  # fixed: a helper is never "the main"
    finally:
        decoy.kill(); decoy.wait()


def test_main_preferred_over_scan_helper_spawned_first(tmp_path):
    ini = str(tmp_path / "reaper.ini")
    decoy = subprocess.Popen(
        [sys.executable, "-c", SLEEP, "-cfgfile", ini, "-__vst_scan__"])
    main = subprocess.Popen([sys.executable, "-c", SLEEP, "-cfgfile", ini])
    try:
        _wait_visible(ini, 2)
        assert _find_pid(ini) == main.pid
    finally:
        for p in (decoy, main):
            p.kill(); p.wait()


def test_child_helper_without_marker_is_excluded(tmp_path):
    """A helper child that carries the cfgfile but NO __vst_scan__ marker is
    still excluded, because its parent is also a match (ppid heuristic)."""
    ini = str(tmp_path / "reaper.ini")
    spawn_child = ("import subprocess, sys, time; "
                   "subprocess.Popen([sys.executable, '-c', "
                   f"{SLEEP!r}] + sys.argv[1:] + ['-helper']); "
                   "time.sleep(60)")
    parent = subprocess.Popen(
        [sys.executable, "-c", spawn_child, "-cfgfile", ini])
    try:
        _wait_visible(ini, 2)
        assert _find_pid(ini) == parent.pid
    finally:
        subprocess.run(["pkill", "-9", "-f", f"cfgfile {ini}"], capture_output=True)
        parent.wait()


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_live_session_pid_resolves_past_decoy_scanner():
    from reaproof.runner.session import ReaperSession

    with ReaperSession("findpid-gate") as s:
        ini = str(s.profile.ini_path)
        decoy = subprocess.Popen(
            [sys.executable, "-c", SLEEP, "-cfgfile", ini, "-__vst_scan__"])
        try:
            _wait_visible(ini, 2)
            found = _find_pid(ini)
            assert found == s.handle.pid, (
                f"_find_pid latched {found}, supervised REAPER is {s.handle.pid} "
                f"(decoy {decoy.pid})")
            assert found != decoy.pid
        finally:
            decoy.kill(); decoy.wait()
