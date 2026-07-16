"""Gate: relaunching the same profile must not answer new commands with a
previous run's cached IPC responses.

A fresh `BridgeClient` restarts its sequence at 1, so a stale
`cmd/out/00000001.json` left by an earlier launch would be read as the answer
to the new client's first `eval` — a false result. `launch()` now calls
`_reset_run_dir` to purge the queue first.

- negative control (REAPER-free): proves the trap is real — a `BridgeClient`
  reading a pre-planted response returns it, so an un-purged relaunch WOULD lie.
- unit: `_reset_run_dir` actually removes the queue.
- gate (REAPER): assemble once, launch/eval/terminate, plant a poison response,
  relaunch, and the fresh eval returns its OWN value, not the poison.
"""
import json

import pytest

from reaproof.control.bridge_client import BridgeClient
from reaproof.determinism import DeterminismLock
from reaproof.provision.base import get_provisioner


def _plant_response(run_dir, seq: int, result):
    out = run_dir / "cmd" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{seq:08d}.json").write_text(
        json.dumps({"id": seq, "ok": True, "result": result})
    )


@pytest.mark.negative_control
def test_stale_response_would_be_read_without_purge(tmp_path):
    # A client whose seq will be 1 finds a pre-existing 00000001.json and returns
    # it immediately — the exact false result the purge prevents.
    run_dir = tmp_path / "_reaproof"
    (run_dir / "cmd" / "in").mkdir(parents=True)
    _plant_response(run_dir, 1, 999)
    client = BridgeClient(run_dir)  # is_alive defaults True; no REAPER needed
    assert client.eval("return 1", timeout=2.0) == 999  # stale poison, proven readable


def test_reset_run_dir_purges_queue_and_markers(tmp_path):
    prov = get_provisioner()
    run_dir = tmp_path / "_reaproof"
    (run_dir / "cmd" / "in").mkdir(parents=True)
    (run_dir / "cmd" / "out").mkdir(parents=True)
    _plant_response(run_dir, 1, 999)
    (run_dir / "cmd" / "in" / "00000001.lua").write_text("return 1")
    (run_dir / "ready.json").write_text("{}")
    (run_dir / "heartbeat.json").write_text("{}")

    class _P:  # minimal profile stand-in with a run_dir attribute
        pass
    p = _P(); p.run_dir = run_dir
    prov._reset_run_dir(p)

    assert not list((run_dir / "cmd" / "out").iterdir())
    assert not list((run_dir / "cmd" / "in").iterdir())
    assert not (run_dir / "ready.json").exists()
    assert not (run_dir / "heartbeat.json").exists()


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_relaunch_returns_fresh_result_not_poison():
    prov = get_provisioner()
    profile = prov.assemble_profile("relaunch-hygiene", DeterminismLock())
    handles = []
    try:
        # run 1
        h1 = prov.launch(profile); handles.append(h1)
        b1 = BridgeClient(profile.run_dir, is_alive=lambda: prov.is_alive(h1))
        b1.wait_ready(120)
        assert b1.eval("return 111") == 111
        prov.terminate(h1); handles.remove(h1)

        # plant a poison response the relaunch's fresh client (seq -> 1) would read
        _plant_response(profile.run_dir, 1, 999)

        # run 2 — SAME profile; launch() must purge the queue first
        h2 = prov.launch(profile); handles.append(h2)
        b2 = BridgeClient(profile.run_dir, is_alive=lambda: prov.is_alive(h2))
        b2.wait_ready(120)
        got = b2.eval("return 222")
        assert got == 222, f"relaunch returned {got!r} — stale/poison leaked through"
    finally:
        for h in handles:
            prov.terminate(h)
