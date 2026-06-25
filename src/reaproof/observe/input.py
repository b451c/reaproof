"""Synthetic input / gestures (§9.2).

The doctrine prefers in-process window-relative input (JS_WindowMessage), but on
macOS the JSFX @gfx canvas reads the *real* OS mouse state via SWELL/Cocoa, not
posted SWELL messages — so the spec's OS-level fallback (CGEvent) is the working
path here. Every gesture is recorded for provenance (§1.8). Window-relative
fractions are converted to screen coordinates via the window's Quartz bounds, so
gestures are layout/position independent.

Primitives: move, click, double_click, drag, wheel, hover. (Keyboard + right-click
menus build on the same CGEvent path; added as controls need them.)
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GestureLog:
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, **kw):
        self.events.append({"kind": kind, **kw})


def window_bounds_macos(pid: int, title_substring: str):
    """Window bounds (X, Y, W, H) in global top-left coords (CGEvent's frame)."""
    import Quartz

    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for want_pid in (True, False):
        for w in wins:
            name = w.get("kCGWindowName") or ""
            if title_substring in name and (not want_pid or w.get("kCGWindowOwnerPID") == pid):
                b = w["kCGWindowBounds"]
                return b["X"], b["Y"], b["Width"], b["Height"]
    raise RuntimeError(f"window bounds not found (pid={pid}, title~='{title_substring}')")


class _MacMouse:
    """OS-level mouse via CGEvent. Coordinates are global (top-left origin)."""

    def __init__(self):
        import Quartz
        self.Q = Quartz

    def _post(self, etype, x, y):
        Q = self.Q
        e = Q.CGEventCreateMouseEvent(None, etype, (x, y), Q.kCGMouseButtonLeft)
        Q.CGEventPost(Q.kCGHIDEventTap, e)

    def move(self, x, y):
        self._post(self.Q.kCGEventMouseMoved, x, y)

    def down(self, x, y):
        self._post(self.Q.kCGEventLeftMouseDown, x, y)

    def up(self, x, y):
        self._post(self.Q.kCGEventLeftMouseUp, x, y)

    def drag_step(self, x, y):
        self._post(self.Q.kCGEventLeftMouseDragged, x, y)

    def drag(self, fx, fy, tx, ty, steps=40, dwell=0.006):
        self.move(fx, fy); time.sleep(0.05)
        self.down(fx, fy); time.sleep(0.08)
        for i in range(1, steps + 1):
            self.drag_step(fx + (tx - fx) * i / steps, fy + (ty - fy) * i / steps)
            time.sleep(dwell)
        self.up(tx, ty); time.sleep(0.15)

    def click(self, x, y):
        self.move(x, y); time.sleep(0.03); self.down(x, y); time.sleep(0.03); self.up(x, y)

    def double_click(self, x, y):
        self.click(x, y); time.sleep(0.05); self.click(x, y)

    def wheel(self, delta_lines: int):
        Q = self.Q
        e = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, int(delta_lines))
        Q.CGEventPost(Q.kCGHIDEventTap, e)


def bridge_drag(session, title_substring: str, from_client, to_client, *, steps: int = 40):
    """In-process drag via JS_WindowMessage (Linux/Windows path, CI-verified).

    Posts WM_LBUTTONDOWN/MOUSEMOVE/LBUTTONUP to the window in client coordinates. On
    macOS the JSFX @gfx ignores posted SWELL messages (it reads the real mouse), so
    macOS uses CGEvent (_MacMouse) instead; on Linux/Windows the posted messages drive
    the control. Coordinates are client-relative ints."""
    fx, fy = from_client
    tx, ty = to_client
    moves = "\n".join(
        f'reaper.JS_WindowMessage_Send(h,"WM_MOUSEMOVE",1,0,'
        f'{int(fx + (tx-fx)*i/steps)},{int(fy + (ty-fy)*i/steps)})'
        for i in range(1, steps + 1))
    session.eval(f"""
    local h = reaper.JS_Window_Find("{title_substring}", false)
    if not h then return false end
    reaper.JS_WindowMessage_Send(h,"WM_LBUTTONDOWN",1,0,{int(fx)},{int(fy)})
    {moves}
    reaper.JS_WindowMessage_Send(h,"WM_LBUTTONUP",0,0,{int(tx)},{int(ty)})
    return true""")


def _mouse():
    if platform.system() == "Darwin":
        return _MacMouse()
    raise NotImplementedError(
        "OS-level mouse is macOS (CGEvent); on Linux/Windows use bridge_drag "
        "(in-process JS_WindowMessage), wired by the CI gate")


class WindowGesture:
    """Drive gestures at window-relative fractions (0..1) of a titled window."""

    def __init__(self, session, title_substring: str):
        self.session = session
        self.title = title_substring
        self.mouse = _mouse()
        self.log = GestureLog()

    def _to_screen(self, fx: float, fy: float):
        import subprocess
        # ensure the window can receive mouse input
        subprocess.run(["osascript", "-e",
                        f'tell application "System Events" to set frontmost of '
                        f'(first process whose unix id is {self.session.handle.pid}) to true'],
                       capture_output=True)
        x, y, w, h = window_bounds_macos(self.session.handle.pid, self.title)
        return x + w * fx, y + h * fy

    def drag(self, from_frac, to_frac, steps=40):
        fx, fy = self._to_screen(*from_frac)
        tx, ty = self._to_screen(*to_frac)
        self.mouse.drag(fx, fy, tx, ty, steps=steps)
        self.log.add("drag", from_frac=from_frac, to_frac=to_frac, steps=steps)

    def click(self, frac):
        x, y = self._to_screen(*frac)
        self.mouse.click(x, y)
        self.log.add("click", frac=frac)

    def double_click(self, frac):
        x, y = self._to_screen(*frac)
        self.mouse.double_click(x, y)
        self.log.add("double_click", frac=frac)

    def wheel(self, delta_lines, frac=(0.5, 0.67)):
        x, y = self._to_screen(*frac)
        self.mouse.move(x, y); time.sleep(0.03)
        self.mouse.wheel(delta_lines)
        self.log.add("wheel", delta=delta_lines, frac=frac)
