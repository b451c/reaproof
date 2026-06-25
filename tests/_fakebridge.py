"""A Python stand-in for the in-REAPER Lua bridge.

Lets us test the client's trust machinery — response handling, ``wait_until``,
and crucially the HANG/CRASH detection that powers the Phase 0 negative control —
deterministically, with no REAPER process. The fake speaks the exact file-queue
protocol (cmd/in/<seq>.lua -> cmd/out/<seq>.json, heartbeat.json, ready.json).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


#: handler may return this to model "received but never answered" (tests BridgeTimeout
#: while the heartbeat keeps beating — distinct from a hang).
NO_RESPONSE = object()


class FakeBridge:
    def __init__(self, run_dir: Path, handler: Callable[[str], Any] | None = None):
        self.run_dir = Path(run_dir)
        self.in_dir = self.run_dir / "cmd" / "in"
        self.out_dir = self.run_dir / "cmd" / "out"
        self.in_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.handler = handler or (lambda src: True)
        self.alive = True       # flip to False to simulate a process crash
        self.beating = True     # flip to False to simulate a hang (heartbeat stall)
        self._tick = 0
        self._run = False
        self._consumed: set[str] = set()
        self._thread: threading.Thread | None = None

    def _atomic(self, path: Path, data: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data)
        os.replace(tmp, path)

    def start(self) -> "FakeBridge":
        self._atomic(self.run_dir / "ready.json",
                     json.dumps({"ready": True, "env": {"fake": True}}))
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while self._run:
            if not self.beating:
                # a hung main thread: neither heartbeat nor command servicing
                time.sleep(0.005)
                continue
            self._tick += 1
            self._atomic(self.run_dir / "heartbeat.json",
                         json.dumps({"tick": self._tick, "t": time.time(), "alive": True}))
            # service requests
            for f in sorted(self.in_dir.glob("*.lua")):
                # re-check liveness per request so freeze()/die() are atomic: an
                # in-flight iteration must not service a request that arrives after
                # the fault is injected.
                if not self.beating or not self._run:
                    break
                seq = f.stem
                out = self.out_dir / f"{seq}.json"
                if out.exists() or seq in self._consumed:
                    continue
                src = f.read_text()
                try:
                    result = self.handler(src)
                    if result is NO_RESPONSE:
                        self._consumed.add(seq)  # received but deliberately never answered
                        continue
                    resp = {"id": int(seq), "ok": True, "result": result}
                except Exception as e:  # noqa: BLE001 - emulate bridge error capture
                    resp = {"id": int(seq), "ok": False, "error": str(e)}
                self._atomic(out, json.dumps(resp))
            time.sleep(0.005)

    # ---- fault injection ----
    def freeze(self) -> None:
        """Stop the heartbeat but keep 'running' — simulates a hung REAPER."""
        self.beating = False

    def die(self) -> None:
        """Simulate process death: heartbeat stops and is_alive() goes False."""
        self.beating = False
        self.alive = False
        self._run = False

    def stop(self) -> None:
        self._run = False
        if self._thread:
            self._thread.join(timeout=2)
