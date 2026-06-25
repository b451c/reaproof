"""Window-targeted pixel capture (§9.1).

Ground truth = real pixels from the real plugin window. Capture is backend-pluggable:
- macOS: ``screencapture -l <CGWindowID>`` (the window is located via Quartz by owner
  PID + title). The in-process js_ReaScriptAPI GDI blit returns black on macOS (SWELL
  limitation), so the spec's OS-level fallback is the primary path here. Requires the
  host app to hold Screen Recording permission.
- Linux (CI/Xvfb): ``import`` / ``xwd`` against the virtual display (no TCC).
- Windows: js_ReaScriptAPI GDI blit in-process.

Every capture returns an RGB uint8 array (H, W, 3) and records the window bounds for
DPI-aware cropping.
"""
from __future__ import annotations

import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class Capture:
    image: np.ndarray            # (H, W, 3) uint8 RGB
    width: int
    height: int
    window_id: int | None
    window_title: str | None
    path: Path | None = None

    def crop(self, box: tuple[int, int, int, int]) -> np.ndarray:
        l, t, r, b = box
        return self.image[t:b, l:r]


def _load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def _find_window_macos(pid: int, title_substring: str):
    import Quartz

    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    # prefer an exact owner-PID match, then any window with the title
    for want_pid in (True, False):
        for w in wins:
            name = w.get("kCGWindowName") or ""
            if title_substring in name and (not want_pid or w.get("kCGWindowOwnerPID") == pid):
                return w.get("kCGWindowNumber"), name
    return None, None


def capture_window_macos(pid: int, title_substring: str, out_path: Path,
                         *, settle: float = 0.4, retries: int = 20) -> Capture:
    """Capture a specific on-screen window owned by ``pid`` whose title contains
    ``title_substring``, via its CGWindowID."""
    out_path = Path(out_path)
    wid = name = None
    deadline = time.monotonic() + retries * 0.25
    while time.monotonic() < deadline:
        wid, name = _find_window_macos(pid, title_substring)
        if wid:
            break
        time.sleep(0.25)
    if not wid:
        raise RuntimeError(f"window not found (pid={pid}, title~='{title_substring}')")
    time.sleep(settle)  # allow the @gfx/editor to paint (caller also waits on redraw)
    proc = subprocess.run(
        ["screencapture", "-x", "-o", "-l", str(wid), str(out_path)],
        capture_output=True, text=True, timeout=30,
    )
    if not out_path.exists():
        raise RuntimeError(f"screencapture failed: {proc.stderr.strip()}")
    img = _load_rgb(out_path)
    if img.std() < 1.0:
        raise RuntimeError(
            "captured an all-black frame — Screen Recording permission likely missing "
            "for the host app (System Settings > Privacy & Security > Screen Recording)"
        )
    return Capture(image=img, width=img.shape[1], height=img.shape[0],
                   window_id=wid, window_title=name, path=out_path)


def capture_stable(session, title_substring: str, out_path: Path, *,
                   max_tries: int = 12, **kw) -> Capture:
    """Capture until two consecutive frames are identical — a wait_until(redraw
    settled) so we never assert on a half-painted frame, without a magic sleep."""
    prev = capture_fx_window(session, title_substring, out_path, **kw)
    for _ in range(max_tries):
        cur = capture_fx_window(session, title_substring, out_path, **kw)
        if cur.image.shape == prev.image.shape and np.array_equal(cur.image, prev.image):
            return cur
        prev = cur
    return prev  # return the latest even if it never fully settled


def capture_via_js(session, title_substring: str, out_path: Path, *, settle: float = 0.3) -> Capture:
    """In-process window capture via js_ReaScriptAPI (JS_GDI blit -> JS_LICE_WritePNG).

    The primary path on Linux + Windows, where the GDI blit reads the real window
    pixels (on macOS/SWELL it returns black, so macOS uses screencapture instead).
    Runs entirely through the bridge — no OS permission, no display coordinates."""
    out_path = Path(out_path)
    session.wait_until(f'reaper.JS_Window_Find("{title_substring}", false) ~= nil',
                       timeout=10, message="fx window")
    ok = session.eval(f"""
    local h = reaper.JS_Window_Find("{title_substring}", false)
    if not h then return false end
    local _, w, ht = reaper.JS_Window_GetClientSize(h)
    local srcDC = reaper.JS_GDI_GetWindowDC(h)
    local bmp = reaper.JS_LICE_CreateBitmap(true, w, ht)
    reaper.JS_GDI_Blit(reaper.JS_LICE_GetDC(bmp), 0, 0, srcDC, 0, 0, w, ht)
    local wrote = reaper.JS_LICE_WritePNG([[{out_path}]], bmp, false)
    reaper.JS_GDI_ReleaseDC(h, srcDC); reaper.JS_LICE_DestroyBitmap(bmp)
    return wrote and w or false
    """)
    if not ok or not out_path.exists():
        raise RuntimeError(f"js capture failed for window '{title_substring}'")
    img = _load_rgb(out_path)
    if img.std() < 1.0:
        raise RuntimeError("js capture returned an all-black frame (unexpected on Linux/Windows)")
    return Capture(image=img, width=img.shape[1], height=img.shape[0],
                   window_id=None, window_title=title_substring, path=out_path)


def capture_fx_window(session, title_substring: str, out_path: Path, **kw) -> Capture:
    """Capture the floated FX/editor window of the plugin under test in ``session``."""
    sysname = platform.system()
    if sysname == "Darwin":
        return capture_window_macos(session.handle.pid, title_substring, out_path, **kw)
    if sysname in ("Linux", "Windows"):
        # js GDI blit reads real pixels on these OSes (CI-verified path)
        return capture_via_js(session, title_substring, out_path,
                              settle=kw.get("settle", 0.3))
    raise NotImplementedError(f"no capture backend for {sysname}")
