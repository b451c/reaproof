"""Trust-machinery tests for the bridge client — deterministic, no REAPER.

These prove the detection logic behind the Phase 0 negative control: a stalled
heartbeat => HANG, a dead process => CRASH, no reply => TIMEOUT. They are the
unit-level guarantee that a killed/hung bridge can never be read as a pass.
"""
import threading
import time

import pytest
from _fakebridge import NO_RESPONSE, FakeBridge

from reaproof.control.bridge_client import (
    BridgeClient,
    BridgeCrash,
    BridgeError,
    BridgeHang,
    BridgeTimeout,
)


def make(tmp_path, handler=None, hang_timeout=0.4):
    rd = tmp_path / "run"
    (rd / "cmd" / "in").mkdir(parents=True)
    (rd / "cmd" / "out").mkdir(parents=True)
    fb = FakeBridge(rd, handler=handler).start()
    client = BridgeClient(rd, is_alive=lambda: fb.alive, hang_timeout=hang_timeout)
    return fb, client


def test_wait_ready_returns_env(tmp_path):
    fb, c = make(tmp_path)
    try:
        assert c.wait_ready(timeout=5) == {"fake": True}
    finally:
        fb.stop()


def test_eval_returns_result(tmp_path):
    fb, c = make(tmp_path, handler=lambda src: 42)
    try:
        c.wait_ready(5)
        assert c.eval("return 21+21") == 42
    finally:
        fb.stop()


def test_eval_propagates_bridge_error(tmp_path):
    def boom(src):
        raise ValueError("synthetic failure")

    fb, c = make(tmp_path, handler=boom)
    try:
        c.wait_ready(5)
        with pytest.raises(BridgeError, match="synthetic failure"):
            c.eval("return whatever")
    finally:
        fb.stop()


def test_wait_until_satisfied(tmp_path):
    state = {"n": 0}

    def handler(src):
        state["n"] += 1
        return state["n"] >= 3

    fb, c = make(tmp_path, handler=handler)
    try:
        c.wait_ready(5)
        c.wait_until("predicate", timeout=5, poll=0.01)  # flips true on 3rd eval
        assert state["n"] >= 3
    finally:
        fb.stop()


def test_wait_until_times_out(tmp_path):
    fb, c = make(tmp_path, handler=lambda src: False)  # never true
    try:
        c.wait_ready(5)
        with pytest.raises(BridgeTimeout):
            c.wait_until("never", timeout=0.5, poll=0.02)
    finally:
        fb.stop()


def test_hang_is_detected_not_passed(tmp_path):
    """The crux: a hung bridge (heartbeat stalls) must raise, never return."""
    fb, c = make(tmp_path, handler=lambda src: True, hang_timeout=0.4)
    try:
        c.wait_ready(5)
        fb.freeze()  # main thread stuck: no heartbeat, no servicing
        with pytest.raises(BridgeHang):
            c.eval("return 1", timeout=5)
    finally:
        fb.stop()


def test_crash_is_detected_not_passed(tmp_path):
    """A dead REAPER must raise BridgeCrash, never return a (stale) pass."""
    fb, c = make(tmp_path, handler=lambda src: True)
    try:
        c.wait_ready(5)
        fb.die()  # is_alive() -> False, loop stops
        with pytest.raises(BridgeCrash):
            c.eval("return 1", timeout=5)
    finally:
        fb.stop()


def test_timeout_when_reply_lost_but_alive(tmp_path):
    """Heartbeat beats, process alive, but no reply -> TIMEOUT (not hang/crash)."""
    fb, c = make(tmp_path, handler=lambda src: NO_RESPONSE, hang_timeout=10)
    try:
        c.wait_ready(5)
        with pytest.raises(BridgeTimeout):
            c.eval("return 1", timeout=0.6)
    finally:
        fb.stop()


def test_crash_during_wait_until_propagates(tmp_path):
    """wait_until must surface a crash immediately, not keep polling to a timeout."""
    fb, c = make(tmp_path, handler=lambda src: False)

    def kill_soon():
        time.sleep(0.2)
        fb.die()

    try:
        c.wait_ready(5)
        threading.Thread(target=kill_soon, daemon=True).start()
        with pytest.raises(BridgeCrash):
            c.wait_until("cond", timeout=10, poll=0.02)
    finally:
        fb.stop()
