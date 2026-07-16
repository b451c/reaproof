"""REAPER e2e gates for the Quality Audit v2 sweep fixes (bridge + session).

One live session covers the bridge-protocol fixes; a second covers the
session() helper's snapshot parity.
"""
import math

import pytest

from reaproof.control.bridge_client import BridgeError, BridgeTimeout
from reaproof.observe.input import bridge_drag
from reaproof.runner.session import ReaperSession, session

pytestmark = [pytest.mark.reaper, pytest.mark.slow]


@pytest.mark.gate
def test_bridge_protocol_hardening_end_to_end():
    with ReaperSession("sweep-bridge") as s:
        # (1) NaN/±inf come back as floats, DISCRIMINATED from nil/None —
        # null-encoding used to alias a §1.7 pathology with a legitimate nil
        nan = s.eval("return 0/0")
        assert isinstance(nan, float) and math.isnan(nan)
        assert s.eval("return 1/0") == float("inf")
        assert s.eval("return -1/0") == float("-inf")
        assert s.eval("return nil") is None      # nil is STILL None (control)

        # (2) an unserialisable (too-deep) result is a loud BridgeError,
        # not a silent client timeout
        deep = ("local t={} local c=t for i=1,100 do c.n={} c=c.n end return t")
        with pytest.raises(BridgeError, match="depth"):
            s.eval(deep)
        assert s.eval("return 42") == 42          # bridge alive afterwards

        # (3) wait_until truthiness is LUA's: 0 is truthy and fires instantly…
        s.wait_until("0", timeout=5, message="Lua-truthy 0 fires")
        # …while false genuinely waits (mutation control: the fix didn't just
        # make every predicate fire)
        with pytest.raises(BridgeTimeout):
            s.wait_until("false", timeout=2, message="(expected timeout)")

        # (4) an in-process drag on a missing window raises, never no-ops
        with pytest.raises(RuntimeError, match="window not found"):
            bridge_drag(s, "NoSuchWindow_ReaProof_XYZ", (1, 1), (5, 5))


@pytest.mark.gate
def test_session_helper_snapshots_failures_like_the_class():
    holder = {}
    with pytest.raises(RuntimeError, match="boom"):
        with session("snap-parity") as s:
            holder["profile"] = s.profile
            raise RuntimeError("boom")
    snap = holder["profile"].artifacts_dir / "snapshot-exit-RuntimeError"
    assert snap.is_dir(), "session() failure produced no labelled snapshot (§1.8)"
