"""Universal watchdog plane (Universality Roadmap U1) — macOS backend.

Three detectors that lift EVERY battery, independent of subject class:

- ``CrashReportWatcher`` — collects macOS diagnostic reports
  (``REAPER-*.ips`` / ``.hang`` under ``~/Library/Logs/DiagnosticReports``)
  that appear during the session window, so a fault becomes named evidence
  instead of a bare dead process.
- ``DialogMonitor`` — a background thread that spots REAPER-owned MODAL
  panels. Verified on the pinned 7.75: modal panels (ShowMessageBox, the
  "ReaScript Error" panel) sit at ``kCGWindowLayer`` 8 while the main window
  is layer 0, and they BLOCK the main thread — the bridge cannot see them,
  which is exactly why this monitor runs out-of-process. It can also dismiss
  them (frontmost + Escape, then Return — the ReaScript Error panel answers
  to Escape, message boxes to Return; both verified).
- ``capture_sample`` — a call-stack profile of a wedged process via
  ``/usr/bin/sample`` (no root needed), for hang forensics before teardown.

All detectors observe only; they never touch the subject under test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DIAG_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"
_REPORT_SUFFIXES = (".ips", ".hang", ".diag")


def report_pid(path: Path) -> int | None:
    """The pid recorded in a modern (.ips) diagnostic report header, if any."""
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        return int(json.loads(first).get("pid"))
    except Exception:  # noqa: BLE001 — non-JSON legacy formats: unknown pid
        return None


class CrashReportWatcher:
    """Baseline the diagnostics dir at session start; anything new that names
    our process afterwards is fault evidence for this session."""

    def __init__(self, proc_name: str = "REAPER", pid: int | None = None,
                 diag_dir: Path | None = None):
        self.diag_dir = Path(diag_dir) if diag_dir else DIAG_DIR
        self.proc_name = proc_name
        self.pid = pid
        self._baseline = {p.name for p in self._candidates()}

    def _candidates(self) -> list[Path]:
        if not self.diag_dir.is_dir():
            return []
        return [p for p in self.diag_dir.glob(f"{self.proc_name}*")
                if p.suffix in _REPORT_SUFFIXES]

    def new_reports(self) -> list[Path]:
        out = []
        for p in sorted(self._candidates()):
            if p.name in self._baseline:
                continue
            if self.pid is not None:
                rp = report_pid(p)
                if rp is not None and rp != self.pid:
                    continue  # some other REAPER instance's fault, not ours
            out.append(p)
        return out

    def collect(self, artifacts_dir: Path, timeout: float = 10.0) -> list[Path]:
        """Wait up to ``timeout`` for reports to land (the OS writes them
        asynchronously after a fault) and copy them into the artifacts."""
        artifacts_dir = Path(artifacts_dir)
        deadline = time.monotonic() + timeout
        while True:
            reports = self.new_reports()
            if reports or time.monotonic() > deadline:
                copied = []
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                for r in reports:
                    dest = artifacts_dir / r.name
                    try:
                        shutil.copy2(r, dest)
                        copied.append(dest)
                    except OSError:
                        pass
                return copied
            time.sleep(0.5)


def capture_sample(pid: int, out_path: Path, seconds: int = 2) -> bool:
    """Call-stack profile of a live-but-stuck process (hang forensics)."""
    try:
        r = subprocess.run(
            ["/usr/bin/sample", str(pid), str(seconds), "-file", str(out_path)],
            capture_output=True, timeout=seconds + 20)
        return r.returncode == 0 and Path(out_path).exists()
    except (subprocess.SubprocessError, OSError):
        return False


# ---- modal-panel monitor ----------------------------------------------------

@dataclass(frozen=True)
class PanelSighting:
    title: str
    width: int
    height: int
    layer: int
    window_id: int
    first_seen: float = field(compare=False)


# macOS NSModalPanelWindowLevel as seen by CGWindowList. Verified on the
# pinned 7.75: the main window is layer 0, ShowMessageBox and the
# "ReaScript Error" panel are layer 8 — while REAPER keeps some pre-created
# helper dialogs (e.g. "Dynamic split items") floating at layer 1 even when
# idle. So the modal discriminator is layer >= 8, NOT merely "elevated"; the
# lower bar produced a phantom sighting on a quiet session (caught by this
# plane's own negative control).
MODAL_PANEL_LAYER = 8

# REAPER's own transient progress panels also sit at the modal layer but are
# self-closing startup noise, not a blocked engine (observed: the VST-scan
# progress panel appears ~1 s into a fresh profile's first idle moments).
# The monitor filters these by exact title; raw sightings remain available
# via modal_panels() for anyone who needs the unfiltered view.
BENIGN_PANEL_TITLES = frozenset({
    "Scanning VST plugins...",
    "Scanning LV2 plugins...",
    "Scanning CLAP plugins...",
})


def modal_panels(pid: int, *, min_layer: int = MODAL_PANEL_LAYER) -> list[PanelSighting]:
    """REAPER-owned on-screen windows at (or above) the modal-panel layer."""
    import Quartz as Q
    wins = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID) or []
    now = time.time()
    out = []
    for w in wins:
        if w.get("kCGWindowOwnerPID") != pid:
            continue
        layer = int(w.get("kCGWindowLayer") or 0)
        if layer < min_layer:
            continue
        b = w.get("kCGWindowBounds") or {}
        out.append(PanelSighting(
            title=w.get("kCGWindowName") or "",
            width=int(b.get("Width", 0)), height=int(b.get("Height", 0)),
            layer=layer, window_id=int(w.get("kCGWindowNumber") or 0),
            first_seen=now))
    return out


def _bring_frontmost(pid: int) -> None:
    subprocess.run(["osascript", "-e",
                    'tell application "System Events" to set frontmost of '
                    f'(first process whose unix id is {pid}) to true'],
                   capture_output=True)
    time.sleep(0.4)


def _press_key(vk: int) -> None:
    import Quartz as Q
    for down in (True, False):
        Q.CGEventPost(Q.kCGHIDEventTap,
                      Q.CGEventCreateKeyboardEvent(None, vk, down))
        time.sleep(0.05)


class DialogMonitor:
    """Background poller for modal panels of a supervised REAPER.

    Runs OUT of process because a modal panel blocks REAPER's main thread —
    the bridge is dead exactly when this information matters most. Each new
    panel is recorded (and screenshotted when ``evidence_dir`` is given), so a
    battery failure can NAME the dialog instead of reporting a bare timeout.
    """

    def __init__(self, pid: int, *, poll: float = 0.25,
                 evidence_dir: Path | None = None,
                 benign_titles: frozenset[str] = BENIGN_PANEL_TITLES):
        self.pid = pid
        self.poll = poll
        self.benign_titles = benign_titles
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self._lock = threading.Lock()
        self._sightings: dict[int, PanelSighting] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle
    def start(self) -> "DialogMonitor":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "DialogMonitor":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for p in modal_panels(self.pid):
                    if p.title in self.benign_titles:
                        continue
                    with self._lock:
                        known = p.window_id in self._sightings
                        if not known:
                            self._sightings[p.window_id] = p
                    if not known and self.evidence_dir:
                        self.evidence_dir.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["screencapture", "-x", "-o", "-l",
                             str(p.window_id),
                             str(self.evidence_dir / f"dialog-{p.window_id}.png")],
                            capture_output=True, timeout=15)
            except Exception:  # noqa: BLE001 — observation must never kill the run
                pass
            self._stop.wait(self.poll)

    # -- queries
    def sightings(self) -> list[PanelSighting]:
        with self._lock:
            return list(self._sightings.values())

    def active(self) -> list[PanelSighting]:
        return [p for p in modal_panels(self.pid)
                if p.title not in self.benign_titles]

    def wait_for_panel(self, *, title_substring: str = "",
                       timeout: float = 10.0) -> PanelSighting:
        """Block until a (matching) panel has been sighted; raises on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for s in self.sightings():
                if title_substring in s.title:
                    return s
            time.sleep(0.1)
        raise TimeoutError(
            f"no modal panel matching {title_substring!r} within {timeout}s "
            f"(saw: {[s.title for s in self.sightings()]})")

    def _no_panel_stable(self, checks: int = 3, gap: float = 0.15) -> bool:
        """True only if NO panel is seen across several consecutive polls — a
        single empty poll can be a transient between a panel closing and the
        next one (or the window server re-enumerating)."""
        for _ in range(checks):
            if self.active():
                return False
            time.sleep(gap)
        return True

    # -- recovery
    def dismiss(self, *, attempts: int = 6, timeout: float = 12.0) -> bool:
        """Close whatever modal panel is up: frontmost + Escape, then Return.

        (Verified: the ReaScript Error panel answers to Escape, an OK-only
        message box to Return — so both keys are tried.) Returns True only once
        no panel remains across several consecutive polls (a transient empty
        poll must not read as 'dismissed')."""
        deadline = time.monotonic() + timeout
        keys = [53, 36]  # kVK_Escape, kVK_Return
        i = 0
        while time.monotonic() < deadline and i < attempts * len(keys):
            if self._no_panel_stable():
                return True
            _bring_frontmost(self.pid)
            _press_key(keys[i % len(keys)])
            i += 1
            time.sleep(0.4)
        return self._no_panel_stable()
