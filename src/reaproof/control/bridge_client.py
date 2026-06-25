"""Python client for the in-REAPER file-queue bridge.

Builds the §1.5 ``wait_until`` primitive and, crucially, makes the Phase 0
negative control real: while waiting for any command's response it monitors the
bridge heartbeat and the REAPER process. A stalled heartbeat raises ``BridgeHang``
and a dead process raises ``BridgeCrash`` — so a killed/frozen bridge is reported
as a hang/crash, NEVER as a pass.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

# Poll cadence for the file queue. This is the wait_until mechanism, not a
# sleep-in-an-assertion (§1.5): we poll a predicate, we never sleep *instead* of
# checking state.
_POLL = 0.02


class BridgeError(RuntimeError):
    """The bridge ran the command but it raised (Lua error captured)."""


class BridgeTimeout(TimeoutError):
    """No response within the command/predicate deadline (hard fail, §1.5)."""


class BridgeHang(RuntimeError):
    """Heartbeat stopped advancing while we waited — REAPER is hung (§1.7)."""


class BridgeCrash(RuntimeError):
    """The REAPER process died while we waited — a crash (§1.7)."""


class BridgeNotReady(RuntimeError):
    """The bridge never wrote ready.json within the deadline."""


class BridgeClient:
    def __init__(
        self,
        run_dir: str | Path,
        *,
        is_alive: Callable[[], bool] | None = None,
        hang_timeout: float = 8.0,
    ):
        self.run_dir = Path(run_dir)
        self.in_dir = self.run_dir / "cmd" / "in"
        self.out_dir = self.run_dir / "cmd" / "out"
        self.heartbeat = self.run_dir / "heartbeat.json"
        self.ready_file = self.run_dir / "ready.json"
        self._seq = 0
        self._is_alive = is_alive or (lambda: True)
        self.hang_timeout = hang_timeout
        self.env: dict[str, Any] = {}

    # ---- lifecycle ---------------------------------------------------------
    def wait_ready(self, timeout: float = 60.0) -> dict[str, Any]:
        """Block until the bridge announces itself; return its env snapshot."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready_file.exists():
                try:
                    data = json.loads(self.ready_file.read_text())
                    self.env = data.get("env", {})
                    return self.env
                except (json.JSONDecodeError, OSError):
                    pass
            if not self._is_alive():
                # Process gone before ready => the launch itself failed.
                if self.ready_file.exists():
                    continue
                raise BridgeCrash("REAPER exited before the bridge became ready")
            time.sleep(_POLL)
        raise BridgeNotReady(f"bridge not ready within {timeout}s (run_dir={self.run_dir})")

    # ---- heartbeat ---------------------------------------------------------
    def _read_tick(self) -> int | None:
        try:
            return int(json.loads(self.heartbeat.read_text()).get("tick"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    # ---- core RPC ----------------------------------------------------------
    def eval(self, lua: str, *, timeout: float = 30.0, hang_timeout: float | None = None) -> Any:
        """Run a Lua chunk in REAPER and return its value.

        The chunk's ``return`` value is serialised to JSON by the bridge. A bare
        expression is auto-wrapped in ``return (...)``.
        """
        if "return" not in lua:
            lua = "return (" + lua.strip() + ")"
        self._seq += 1
        seq = f"{self._seq:08d}"
        req = self.in_dir / f"{seq}.lua"
        resp = self.out_dir / f"{seq}.json"
        # atomic publish: tmp + rename, so the bridge only sees a complete request
        tmp = req.with_suffix(".lua.tmp")
        tmp.write_text(lua, encoding="utf-8")
        os.replace(tmp, req)

        hang_to = self.hang_timeout if hang_timeout is None else hang_timeout
        deadline = time.monotonic() + timeout
        last_tick = self._read_tick()
        last_advance = time.monotonic()
        while True:
            if resp.exists():
                return self._parse_response(resp, seq)
            now = time.monotonic()
            tick = self._read_tick()
            if tick is not None and (last_tick is None or tick > last_tick):
                last_tick, last_advance = tick, now
            if not self._is_alive():
                # tiny grace for a final response flush before declaring a crash
                time.sleep(_POLL)
                if resp.exists():
                    return self._parse_response(resp, seq)
                raise BridgeCrash(f"REAPER died while evaluating seq={seq}")
            if now - last_advance > hang_to:
                raise BridgeHang(
                    f"heartbeat stalled {now - last_advance:.1f}s (> {hang_to}s) "
                    f"while evaluating seq={seq} — REAPER is hung"
                )
            if now > deadline:
                raise BridgeTimeout(f"no response for seq={seq} within {timeout}s")
            time.sleep(_POLL)

    @staticmethod
    def _parse_response(resp: Path, seq: str) -> Any:
        data = json.loads(resp.read_text())
        if not data.get("ok", False):
            raise BridgeError(f"seq={seq}: {data.get('error', 'unknown error')}")
        return data.get("result")

    # ---- predicates --------------------------------------------------------
    def wait_until(
        self,
        predicate: str | Callable[[], bool],
        *,
        timeout: float = 30.0,
        poll: float = 0.05,
        message: str = "",
    ) -> None:
        """Wait for a predicate to hold (§1.5). String predicate = a Lua boolean
        expression evaluated in REAPER; callable = evaluated in Python.

        A timeout is a hard failure (the caller captures a snapshot), never a
        silent pass.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                ok = self.eval(predicate, timeout=min(10.0, timeout)) if isinstance(predicate, str) else predicate()
            except (BridgeHang, BridgeCrash):
                raise  # liveness failures propagate immediately
            if ok:
                return
            if time.monotonic() > deadline:
                raise BridgeTimeout(
                    f"wait_until timed out after {timeout}s: {message or predicate!r}"
                )
            time.sleep(poll)

    # ---- convenience -------------------------------------------------------
    def ping(self) -> dict[str, Any]:
        return self.eval(
            "return {app=reaper.GetAppVersion(), tracks=reaper.CountTracks(0), "
            "res=reaper.GetResourcePath()}"
        )
