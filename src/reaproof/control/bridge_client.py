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
import re
import time
from pathlib import Path
from typing import Any, Callable

# Poll cadence for the file queue. This is the wait_until mechanism, not a
# sleep-in-an-assertion (§1.5): we poll a predicate, we never sleep *instead* of
# checking state.
_POLL = 0.02

# A chunk that already begins with a statement keyword is a statement sequence,
# never a bare expression, so it must NOT be wrapped in `return (...)`.
_STATEMENT_START = re.compile(
    r"^(return|local|do|if|for|while|function|repeat|goto|break|end|::)\b"
)


def _has_top_level_statement(stripped: str) -> bool:
    """True if `stripped` contains a `return` keyword or `;` at bracket depth 0,
    i.e. as an actual top-level statement — ignoring occurrences nested inside
    (), [], {} (e.g. a function body) and inside string/comment literals.

    This is the crux of NOT wrapping. A substring test can't tell a nested
    `return` (inside `(function() ... return x end)()`, which we MUST wrap) from
    a top-level one (`reaper.foo(); return true`, which we must NOT wrap); a
    cheap depth scan can.
    """
    depth = 0
    i, n = 0, len(stripped)
    while i < n:
        c = stripped[i]
        if c in "\"'":                                   # skip a quoted string
            q = c
            i += 1
            while i < n:
                if stripped[i] == "\\":
                    i += 2
                    continue
                if stripped[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "-" and stripped[i:i + 2] == "--":       # skip a line comment
            nl = stripped.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if c == "[" and stripped[i:i + 2] == "[[":        # skip a long string
            end = stripped.find("]]", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0:
            if c == ";":
                return True
            if (
                stripped[i:i + 6] == "return"
                and (i == 0 or not (stripped[i - 1].isalnum() or stripped[i - 1] == "_"))
                and (i + 6 >= n or not (stripped[i + 6].isalnum() or stripped[i + 6] == "_"))
            ):
                return True
        i += 1
    return False


def _wrap_lua(lua: str) -> str:
    """Return `lua` ready to run as a chunk whose value the bridge serialises.

    A single Lua *expression* is wrapped in `return (...)` so its value comes
    back. Wrapping is skipped only when the chunk is already a statement
    sequence — it begins with a statement keyword, or contains a `return`/`;` at
    the top level (see ``_has_top_level_statement``).

    This replaces the old `"return" not in lua` heuristic, which had a silent
    trap: a single-line expression that merely CONTAINED the word `return`
    (e.g. an inline `(function() ... return x end)()`) was left unwrapped and
    evaluated to `nil` — a false result, the one thing this platform must never
    produce.
    """
    stripped = lua.strip()
    if not stripped:
        return lua
    if _STATEMENT_START.match(stripped) or _has_top_level_statement(stripped):
        return lua
    return "return (" + stripped + ")"


_NONFINITE = {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}


def _decode_nonfinite(v: Any) -> Any:
    """Restore the bridge's tagged non-finite sentinels to real floats.

    Lua NaN/±inf used to be encoded as JSON null and surfaced as Python None —
    indistinguishable from a legitimate nil, hiding exactly the §1.7 pathology
    class. They now travel as {"__reaproof_nonfinite__": "nan"|"inf"|"-inf"}.
    """
    if isinstance(v, dict):
        if set(v.keys()) == {"__reaproof_nonfinite__"}:
            return _NONFINITE.get(v["__reaproof_nonfinite__"], float("nan"))
        return {k: _decode_nonfinite(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode_nonfinite(x) for x in v]
    return v


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
        """Block until the bridge announces itself; return its env snapshot.

        A DEGRADED ready file (the bridge's env snapshot failed) raises: its
        env carries none of the capability flags (has_js_api, has_imgui, ...),
        so downstream capability gates would silently read False and skip —
        proceeding on it converts a bridge fault into hollow passes.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready_file.exists():
                try:
                    data = json.loads(self.ready_file.read_text(
                        encoding="utf-8", errors="replace"))
                    env = data.get("env", {})
                    if env.get("degraded"):
                        raise BridgeNotReady(
                            "bridge came up DEGRADED (env snapshot failed): "
                            + str(data.get("error", "unknown")))
                    self.env = env
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
            return int(json.loads(self.heartbeat.read_text(
                encoding="utf-8", errors="replace")).get("tick"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    # ---- core RPC ----------------------------------------------------------
    def eval(self, lua: str, *, timeout: float = 30.0, hang_timeout: float | None = None) -> Any:
        """Run a Lua chunk in REAPER and return its value.

        The chunk's ``return`` value is serialised to JSON by the bridge. A bare
        expression is auto-wrapped in ``return (...)`` (see ``_wrap_lua``).

        Constraints callers must know:
        - only the FIRST return value crosses the bridge (multi-return REAPER
          APIs: wrap in a table — ``return {reaper.GetTrackUIVolPan(tr,0,0)}``);
        - Lua NaN/±inf come back as float('nan')/inf (never silently None);
        - the bridge runs chunks on REAPER's defer loop, which also feeds the
          heartbeat — a single chunk that occupies the main thread for longer
          than ``hang_timeout`` (default 8 s) is indistinguishable from a hung
          REAPER and raises BridgeHang. For a legitimately long-running chunk
          pass ``hang_timeout=<expected seconds>`` explicitly.
        """
        lua = _wrap_lua(lua)
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
                    f"while evaluating seq={seq} — REAPER is hung, OR this one "
                    f"chunk legitimately needs the main thread longer than "
                    f"hang_timeout (pass hang_timeout=<seconds> to eval)"
                )
            if now > deadline:
                raise BridgeTimeout(f"no response for seq={seq} within {timeout}s")
            time.sleep(_POLL)

    @staticmethod
    def _parse_response(resp: Path, seq: str) -> Any:
        data = json.loads(resp.read_text(encoding="utf-8", errors="replace"))
        if not data.get("ok", False):
            raise BridgeError(f"seq={seq}: {data.get('error', 'unknown error')}")
        return _decode_nonfinite(data.get("result"))

    # ---- predicates --------------------------------------------------------
    def wait_until(
        self,
        predicate: str | Callable[[], bool],
        *,
        timeout: float = 30.0,
        poll: float = 0.05,
        message: str = "",
    ) -> None:
        """Wait for a predicate to hold (§1.5). String predicate = a Lua
        EXPRESSION whose truthiness is decided by LUA rules in REAPER; callable
        = evaluated in Python.

        The Lua-side truthiness coercion matters: 0, "" and {} are truthy in
        Lua but falsy in Python — deciding on the Python side made a predicate
        like ``reaper.CountSelectedMediaItems(0)`` (0 selected = truthy in the
        author's Lua reasoning) unable to ever fire. A statement-shaped
        predicate raises immediately instead of misbehaving.

        A timeout is a hard failure (the caller captures a snapshot), never a
        silent pass.
        """
        if isinstance(predicate, str):
            px = predicate.strip()
            if _STATEMENT_START.match(px) or _has_top_level_statement(px):
                raise ValueError(
                    "wait_until needs a Lua EXPRESSION (its truthiness is "
                    f"tested), not a statement sequence: {predicate!r}")
            lua = f"return ({px}) and true or false"
        deadline = time.monotonic() + timeout
        while True:
            try:
                ok = (self.eval(lua, timeout=min(10.0, timeout))
                      if isinstance(predicate, str) else predicate())
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
