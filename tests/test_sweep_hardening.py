"""Gates for the Quality Audit v2 independent sweep (REAPER-free units).

Each test pins one fixed defect and proves the fix can turn RED (the old
behaviour IS the mutation). Grouped by plane.
"""
import json
import math
import plistlib
import subprocess
import sys
import time

import numpy as np
import pytest

from reaproof import paths
from reaproof.control.bridge_client import (
    BridgeClient, BridgeNotReady, _decode_nonfinite,
)
from reaproof.coverage import coverage_report
from reaproof.determinism import DeterminismLock, NonDeterminismError, assert_identical
from reaproof.observe.audio import analysis as A
from reaproof.provision.base import get_provisioner
from reaproof.provision.linux import LinuxProvisioner
from reaproof.provision.macos import MacOSProvisioner
from reaproof.report.provenance import build_manifest
from reaproof.validators.clap import clap_verdict


# ---- audio analysis ---------------------------------------------------------

def test_null_test_rejects_length_mismatch():
    """A truncated render used to null perfectly on the overlapping prefix —
    the missing tail was never examined (false 'transparent' PASS)."""
    a = np.sin(np.linspace(0, 100, 48000))
    with pytest.raises(ValueError, match="equal lengths"):
        A.null_test_dbfs(a, a[:-4800])          # 10% tail chopped off
    with pytest.raises(ValueError, match="empty"):
        A.null_test_dbfs(np.array([]), np.array([]))
    # negative control: the legitimate use is untouched
    assert A.null_test_dbfs(a, a) < -300         # bit-identical -> floor


def test_empty_signal_is_a_pathology_not_a_clean_pass():
    """A render that produced NO audio must hard-fail §1.7, never read clean."""
    issues = A.detect_pathologies(np.array([]))
    assert [i.kind for i in issues] == ["empty"]
    with pytest.raises(A.AudioPathology, match="empty"):
        A.assert_no_pathology(np.array([]))
    # negative control: a real, clean signal (whole periods, no DC) still passes
    A.assert_no_pathology(np.sin(2 * np.pi * np.arange(4800) / 48) * 0.5)


def test_spectral_centroid_of_silence_is_nan_not_zero():
    c = A.spectral_centroid(np.zeros(4800))
    assert math.isnan(c), f"silence produced a fake 'reading' of {c} Hz"
    assert math.isnan(A.spectral_centroid(np.array([])))
    # negative control: a 1 kHz tone measures ~1 kHz, not NaN
    sr = 48000
    tone = np.sin(2 * np.pi * 1000 * np.arange(sr) / sr)
    assert abs(A.spectral_centroid(tone, sr=sr) - 1000) < 50


def test_lufs_rejects_sub_400ms_input():
    with pytest.raises(ValueError, match="400 ms"):
        A.lufs_integrated(np.zeros(4800), sr=48000)   # 100 ms


# ---- determinism gate -------------------------------------------------------

def test_assert_identical_refuses_unserializable_values():
    """default=str let objects with equal str() count as identical — the §1.4
    gate must refuse what it cannot faithfully compare."""
    with pytest.raises(ValueError, match="non-JSON-serializable"):
        assert_identical([{1, 2}, {1, 2}])
    # negative controls: real behaviour intact
    assert_identical([{"a": 1}, {"a": 1}])
    with pytest.raises(NonDeterminismError):
        assert_identical([{"a": 1}, {"a": 2}])


# ---- coverage ---------------------------------------------------------------

def test_empty_coverage_is_zero_not_hundred_percent():
    rep = coverage_report({})
    assert rep.fraction == 0.0, "an untested plugin reported 100% coverage"


# ---- validators -------------------------------------------------------------

def test_clap_zero_tests_is_not_a_pass():
    zero = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": []}
    assert clap_verdict(0, zero) is False, "0/0 with exit 0 read as PASS"
    ok = {"total": 5, "passed": 5, "failed": 0, "skipped": 0, "failed_tests": []}
    assert clap_verdict(0, ok) is True           # negative control
    assert clap_verdict(1, ok) is False
    assert clap_verdict(0, {**ok, "failed": 1}) is False


# ---- provisioner ------------------------------------------------------------

def test_install_plugins_rejects_unknown_format(tmp_path):
    try:
        prov = get_provisioner()
    except NotImplementedError as e:   # no provisioner for this OS yet (Windows)
        pytest.skip(str(e))
    with pytest.raises(ValueError, match="unsupported plugin format"):
        prov.assemble_profile("sweep-badfmt", DeterminismLock(),
                              plugins=[tmp_path / "thing.component"])


def test_launch_env_carries_clap_path_on_all_platforms(tmp_path):
    """Linux never set CLAP_PATH (a CLAP subject was invisible to REAPER on
    the CI substrate); the shared _launch_env closes that on every backend."""
    class _P:
        plugin_dir = tmp_path / "plugins"
    for prov in (MacOSProvisioner(), LinuxProvisioner()):
        env = prov._launch_env(_P())
        assert env["CLAP_PATH"] == str(tmp_path / "plugins" / "CLAP")
        assert env["LC_NUMERIC"] == "C"          # determinism lock rides along


@pytest.mark.skipif(sys.platform == "win32",
                    reason="drives the Linux provisioner's POSIX signal path "
                           "(no SIGKILL on Windows)")
def test_linux_terminate_reaps_cfgfile_stragglers(tmp_path):
    """Straggler bound to the profile's unique cfgfile survives the main-pid
    kill — terminate must reap it (mirrors the macOS behaviour)."""
    ini = str(tmp_path / "reaper.ini")
    main = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    straggler = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "-cfgfile", ini])
    from reaproof.provision.base import LaunchHandle
    handle = LaunchHandle(pid=main.pid, profile=None, extra={"ini": ini})
    try:
        LinuxProvisioner().terminate(handle)
        main.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and straggler.poll() is None:
            time.sleep(0.1)
        assert straggler.poll() is not None, "cfgfile straggler survived terminate"
    finally:
        for p in (main, straggler):
            if p.poll() is None:
                p.kill(); p.wait()


# ---- provenance version binding ---------------------------------------------

def test_reaper_app_build_reads_the_actual_plist(tmp_path):
    app = tmp_path / "Fake.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleVersion": "9.99.0_test"}))
    assert paths.reaper_app_build(app) == "9.99.0_test"
    assert paths.reaper_app_build(tmp_path / "missing.app") is None


@pytest.mark.skipif(paths.reaper_app_build() is None,
                    reason="pinned REAPER not provisioned (no Info.plist to measure)")
def test_manifest_flags_a_build_mismatch(monkeypatch):
    m = build_manifest()
    assert m.reaper_app_build == paths.REAPER_BUILD   # pinned app matches claim
    assert m.reaper_build_mismatch is False
    # MUTATION: the claim diverges from the measured app -> flagged
    monkeypatch.setattr(paths, "REAPER_BUILD", "0.0.0_wrong")
    m2 = build_manifest()
    assert m2.reaper_build_mismatch is True


# ---- bridge client ----------------------------------------------------------

def _run_dir(tmp_path):
    run = tmp_path / "_reaproof"
    (run / "cmd" / "in").mkdir(parents=True)
    (run / "cmd" / "out").mkdir(parents=True)
    return run


def test_degraded_ready_raises_not_proceeds(tmp_path):
    """A degraded env has no capability flags — proceeding would turn every
    downstream capability gate into a hollow skip."""
    run = _run_dir(tmp_path)
    (run / "ready.json").write_text(
        '{"ready":true,"env":{"degraded":true},"error":"snapshot exploded"}')
    with pytest.raises(BridgeNotReady, match="DEGRADED"):
        BridgeClient(run).wait_ready(timeout=1)


def test_healthy_ready_still_returns_env(tmp_path):
    run = _run_dir(tmp_path)
    (run / "ready.json").write_text('{"ready":true,"env":{"has_js_api":true}}')
    assert BridgeClient(run).wait_ready(timeout=1) == {"has_js_api": True}


def test_response_with_invalid_utf8_is_replaced_not_fatal(tmp_path):
    run = _run_dir(tmp_path)
    (run / "cmd" / "out" / "00000001.json").write_bytes(
        b'{"id":1,"ok":true,"result":"caf\xe9"}')   # latin-1 e-acute
    got = BridgeClient(run).eval("return 1", timeout=2)
    assert got == "caf�"    # replacement char, not UnicodeDecodeError


def test_wait_until_rejects_statement_predicates(tmp_path):
    run = _run_dir(tmp_path)
    with pytest.raises(ValueError, match="EXPRESSION"):
        BridgeClient(run).wait_until("local x = 1", timeout=1)


def test_decode_nonfinite_sentinels():
    v = _decode_nonfinite({"a": [{"__reaproof_nonfinite__": "nan"},
                                 {"__reaproof_nonfinite__": "inf"}],
                           "b": {"__reaproof_nonfinite__": "-inf"},
                           "c": 1.5})
    assert math.isnan(v["a"][0]) and v["a"][1] == float("inf")
    assert v["b"] == float("-inf") and v["c"] == 1.5


# ---- OS input hygiene (stubbed Quartz: the unit is OUR sequencing logic) ----

class _StubQ:
    kCGEventMouseMoved = "moved"
    kCGEventLeftMouseDown = "ldown"
    kCGEventLeftMouseUp = "lup"
    kCGEventRightMouseDown = "rdown"
    kCGEventRightMouseUp = "rup"
    kCGEventLeftMouseDragged = "drag"
    kCGMouseButtonLeft = 0
    kCGMouseButtonRight = 1
    kCGHIDEventTap = 0
    kCGMouseEventClickState = 99

    def __init__(self):
        self.posted = []
        self.click_states = []

    def CGEventCreateMouseEvent(self, src, etype, pt, btn):
        return {"etype": etype, "pt": pt, "btn": btn}

    def CGEventPost(self, tap, e):
        self.posted.append(e)

    def CGEventSetIntegerValueField(self, e, field, val):
        if field == self.kCGMouseEventClickState:
            self.click_states.append((e["etype"], val))


def _stub_mouse():
    from reaproof.observe.input import _MacMouse
    m = _MacMouse.__new__(_MacMouse)
    m.Q = _StubQ()
    return m


def test_drag_releases_button_even_when_interrupted():
    """A synthetic button left DOWN after an exception turns the next test's
    first move into a desktop-wide drag — the cross-run leak class."""
    m = _stub_mouse()

    def boom(x, y):
        raise RuntimeError("interrupted mid-drag")
    m.drag_step = boom
    with pytest.raises(RuntimeError):
        m.drag(0, 0, 10, 10, steps=3, dwell=0)
    etypes = [e["etype"] for e in m.Q.posted]
    assert "ldown" in etypes and etypes[-1] == "lup", \
        f"button left down after interruption: {etypes}"


def test_double_click_carries_click_state_two():
    """Two independent singles never fire a 'double-click to reset' control —
    the second press must carry CGEvent click-state 2."""
    m = _stub_mouse()
    m.double_click(5, 5)
    assert ("ldown", 2) in m.Q.click_states and ("lup", 2) in m.Q.click_states
