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
        extensions: list[Path] | None = None,
        ready_timeout: float = 120.0,
        provisioner: Provisioner | None = None,
    ):
        self.lock = lock or DeterminismLock()
        self.plugins = plugins
        self.jsfx = jsfx
        self.extensions = extensions
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
            self.run_id, self.lock, plugins=self.plugins, jsfx=self.jsfx,
            extensions=self.extensions,
        )
        self.crash_watcher = self._make_crash_watcher()
        self.handle = self.provisioner.launch(self.profile)
        if self.crash_watcher is not None:
            self.crash_watcher.pid = self.handle.pid
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

    def restart(self, *, hard: bool = False,
                ready_timeout: float | None = None) -> "ReaperSession":
        """Relaunch the SAME profile — persisted config/ExtState carries over.

        The "assemble once, launch N times" pattern for startup-behaviour and
        persistence tests. Do NOT reach for a second ``start()``/session:
        ``assemble_profile`` rmtrees the profile root, wiping every trace of
        run 1. ``restart()`` quits REAPER *cleanly* (REAPER only flushes
        persisted ExtState + ini state to disk on a real quit), relaunches the
        same resource dir (``launch()`` purges the stale IPC queue), and
        re-waits ready. ``hard=True`` skips the clean quit (SIGTERM) for
        crash-recovery tests — state persisted since the last flush is then
        NOT guaranteed to survive. A failed clean quit raises (with snapshot)
        rather than silently falling back to a kill that would lose state.
        """
        assert self.profile is not None and self.handle is not None, \
            "restart() needs a started session"
        if hard:
            self.stop()
        else:
            if not self._quit_cleanly():
                self.collect_snapshot("restart-quit-timeout")
                self.stop()
                raise RuntimeError(
                    "clean quit for restart() timed out — persisted state may be "
                    "unflushed; failing loudly instead of restarting on a kill"
                )
        self.handle = self.provisioner.launch(self.profile)
        self.bridge = BridgeClient(
            self.profile.run_dir,
            is_alive=lambda: self.provisioner.is_alive(self.handle),
        )
        try:
            self.env = self.bridge.wait_ready(ready_timeout or self.ready_timeout)
        except Exception:
            self.collect_snapshot("restart-ready-failure")
            self.stop()
            raise
        return self

    def _quit_cleanly(self, timeout: float = 30.0) -> bool:
        """Quit via REAPER's own File>Quit (action 40004) so atexit/ExtState
        flush runs. First switch to a known-clean blank project with the
        ``noprompt:`` prefix — a dirty project would pop the save-changes modal
        and block the bridge forever."""
        blank = self.profile.resource_dir / "_blank.rpp"
        if not blank.exists():
            blank.write_text('<REAPER_PROJECT 0.1 "7.75/macOS" 0\n>\n')
        try:
            self.eval(f"reaper.Main_openProject([[noprompt:{blank}]]); return true")
            self.wait_until("reaper.IsProjectDirty(0) == 0", timeout=10,
                            message="blank project open + clean before quit")
            self.eval(
                "reaper.defer(function() reaper.Main_OnCommand(40004, 0) end); "
                "return true")
        except Exception:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.provisioner.is_alive(self.handle):
                self.handle = None
                return True
            time.sleep(0.2)  # lifecycle poll, not an assertion path
        return False

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
    def _make_crash_watcher(self):
        """Arm macOS diagnostic-report collection for this session (U1)."""
        import platform as _p
        if _p.system() != "Darwin":
            return None
        from reaproof.observe.watchdog import CrashReportWatcher
        return CrashReportWatcher()

    def collect_artifacts(self) -> None:
        if not self.profile:
            return
        art = self.profile.artifacts_dir
        for name in ("ready.json", "heartbeat.json", "bridge.log"):
            src = self.profile.run_dir / name
            if src.exists():
                with contextlib.suppress(OSError):
                    shutil.copy2(src, art / name)
        if getattr(self, "crash_watcher", None) is not None:
            with contextlib.suppress(Exception):
                # quick sweep only — a battery that EXPECTS a fault waits via
                # crash_watcher.collect(timeout=...) itself
                self.crash_watcher.collect(art / "diagnostics", timeout=0.0)

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
    except BaseException as exc:
        # parity with ReaperSession.__exit__: every failure carries a labelled
        # snapshot (§1.8) no matter which context-manager spelling was used
        with contextlib.suppress(Exception):
            s.collect_snapshot(f"exit-{type(exc).__name__}")
        raise
    finally:
        s.collect_artifacts()
        s.stop()
