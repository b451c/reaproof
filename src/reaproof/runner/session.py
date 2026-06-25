"""A supervised, isolated REAPER session — the unit the runner owns.

Context manager that provisions a hermetic profile, launches REAPER under
supervision, waits for the bridge, exposes ``eval``/``wait_until``, and
*guarantees* teardown (no orphan REAPER, §5.4). On a hang/crash/timeout it
collects a snapshot before re-raising, so every failure carries evidence (§1.8).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from reaproof.control.bridge_client import BridgeClient
from reaproof.determinism import DeterminismLock
from reaproof.provision.base import IsolatedProfile, LaunchHandle, Provisioner, get_provisioner


class ReaperSession:
    def __init__(
        self,
        name: str = "session",
        *,
        lock: DeterminismLock | None = None,
        plugins: list[Path] | None = None,
        jsfx: list[Path] | None = None,
        ready_timeout: float = 120.0,
        provisioner: Provisioner | None = None,
    ):
        self.lock = lock or DeterminismLock()
        self.plugins = plugins
        self.jsfx = jsfx
        self.ready_timeout = ready_timeout
        self.provisioner = provisioner or get_provisioner()
        self.run_id = f"{name}-{os.getpid()}-{int(time.time() * 1000)}"
        self.profile: IsolatedProfile | None = None
        self.handle: LaunchHandle | None = None
        self.bridge: BridgeClient | None = None
        self.env: dict[str, Any] = {}

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> "ReaperSession":
        self.profile = self.provisioner.assemble_profile(
            self.run_id, self.lock, plugins=self.plugins, jsfx=self.jsfx
        )
        self.handle = self.provisioner.launch(self.profile)
        self.bridge = BridgeClient(
            self.profile.run_dir,
            is_alive=lambda: self.provisioner.is_alive(self.handle),
        )
        try:
            self.env = self.bridge.wait_ready(self.ready_timeout)
        except Exception:
            self.collect_snapshot("ready-failure")
            self.stop()
            raise
        return self

    def stop(self) -> None:
        if self.handle is not None:
            with contextlib.suppress(Exception):
                self.provisioner.terminate(self.handle)
            self.handle = None

    def __enter__(self) -> "ReaperSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            with contextlib.suppress(Exception):
                self.collect_snapshot(f"exit-{getattr(exc_type, '__name__', 'error')}")
        self.collect_artifacts()
        self.stop()

    # ---- delegated control -------------------------------------------------
    def eval(self, lua: str, **kw) -> Any:
        assert self.bridge is not None, "session not started"
        return self.bridge.eval(lua, **kw)

    def wait_until(self, predicate: str | Callable[[], bool], **kw) -> None:
        assert self.bridge is not None, "session not started"
        self.bridge.wait_until(predicate, **kw)

    def ping(self) -> dict[str, Any]:
        assert self.bridge is not None, "session not started"
        return self.bridge.ping()

    @property
    def is_alive(self) -> bool:
        return self.handle is not None and self.provisioner.is_alive(self.handle)

    # ---- evidence ----------------------------------------------------------
    def collect_artifacts(self) -> None:
        if not self.profile:
            return
        art = self.profile.artifacts_dir
        for name in ("ready.json", "heartbeat.json", "bridge.log"):
            src = self.profile.run_dir / name
            if src.exists():
                with contextlib.suppress(OSError):
                    shutil.copy2(src, art / name)

    def collect_snapshot(self, label: str) -> Path | None:
        """Capture a labelled snapshot of bridge state for a failure (§1.8)."""
        if not self.profile:
            return None
        snap = self.profile.artifacts_dir / f"snapshot-{label}"
        snap.mkdir(parents=True, exist_ok=True)
        for name in ("ready.json", "heartbeat.json", "bridge.log"):
            src = self.profile.run_dir / name
            if src.exists():
                with contextlib.suppress(OSError):
                    shutil.copy2(src, snap / name)
        return snap


@contextlib.contextmanager
def session(name: str = "session", **kw):
    s = ReaperSession(name, **kw)
    try:
        yield s.start()
    finally:
        s.collect_artifacts()
        s.stop()
