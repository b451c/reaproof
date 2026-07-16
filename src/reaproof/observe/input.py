"""Synthetic input / gestures (§9.2).

The doctrine prefers in-process window-relative input (JS_WindowMessage), but on
macOS the JSFX @gfx canvas reads the *real* OS mouse state via SWELL/Cocoa, not
posted SWELL messages — so the spec's OS-level fallback (CGEvent) is the working
path here. Every gesture is recorded for provenance (§1.8). Window-relative
fractions are converted to screen coordinates via the window's Quartz bounds, so
gestures are layout/position independent.

KNOWN BOUNDARY (macOS): a CGEvent click aimed at a SWELL *dialog* window that is
not the key window is swallowed by Cocoa as an activation click — it never
reaches the view. Extension UIs are SWELL dialogs, so OS-level clicks are NOT a
reliable way to drive them. Use the in-process paths instead:
  - ``bridge_click`` / ``bridge_drag`` — raw WM_*BUTTON* messages in client
    coords; drives custom-drawn DlgProc chrome (e.g. an extension's own nav
    buttons) without needing focus;
  - ``dialog_command`` — WM_COMMAND to the dialog; the reliable way to press a
    *standard* SWELL Button/menu item (posted BM_CLICK and raw WM_LBUTTON* do
    NOT fire BN_CLICKED on SWELL — verified on the pinned build).
OS-level CGEvent input (``_MacMouse``/``type_text``) is still required when the
observable reads the *real* OS input state (JSFX @gfx mouse_cap, capture polls
using GetCursorPos/GetAsyncKeyState) or when typing into a focused window.

Primitives: move, click (left/right), double_click, hold_click, drag, wheel,
type_text; in-process: bridge_click, bridge_drag, dialog_command.
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


def window_bounds_macos(pid: int, title_substring: str, *,
                        allow_foreign_owner: bool = False):
    """Window bounds (X, Y, W, H) in global top-left coords (CGEvent's frame).

    STRICT owner-PID match by default — a bare title-substring match against
    any process would aim gestures at an unrelated app's window. Foreign
    owners (AU remote-view windows hosted out-of-process) are an explicit
    opt-in via ``allow_foreign_owner=True``.
    """
    import Quartz

    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for want_pid in (True, False) if allow_foreign_owner else (True,):
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

    def _post(self, etype, x, y, button=None):
        Q = self.Q
        e = Q.CGEventCreateMouseEvent(None, etype, (x, y),
                                      button if button is not None
                                      else Q.kCGMouseButtonLeft)
        Q.CGEventPost(Q.kCGHIDEventTap, e)

    def move(self, x, y):
        self._post(self.Q.kCGEventMouseMoved, x, y)

    def down(self, x, y, right: bool = False):
        Q = self.Q
        if right:
            self._post(Q.kCGEventRightMouseDown, x, y, Q.kCGMouseButtonRight)
        else:
            self._post(Q.kCGEventLeftMouseDown, x, y)

    def up(self, x, y, right: bool = False):
        Q = self.Q
        if right:
            self._post(Q.kCGEventRightMouseUp, x, y, Q.kCGMouseButtonRight)
        else:
            self._post(Q.kCGEventLeftMouseUp, x, y)

    def drag_step(self, x, y):
        self._post(self.Q.kCGEventLeftMouseDragged, x, y)

    def _release(self, x, y, right: bool = False):
        """Best-effort button release for finally-paths: a synthetic button
        left DOWN (KeyboardInterrupt mid-gesture, a Quartz error) turns the
        next test's first move into a system-wide drag — a cross-run leak."""
        try:
            self.up(x, y, right)
        except Exception:  # noqa: BLE001 — releasing is already the recovery
            pass

    def drag(self, fx, fy, tx, ty, steps=40, dwell=0.006):
        self.move(fx, fy); time.sleep(0.05)
        self.down(fx, fy)
        try:
            time.sleep(0.08)
            for i in range(1, steps + 1):
                self.drag_step(fx + (tx - fx) * i / steps, fy + (ty - fy) * i / steps)
                time.sleep(dwell)
        finally:
            self._release(tx, ty)
        time.sleep(0.15)

    def click(self, x, y, right: bool = False):
        self.move(x, y); time.sleep(0.03)
        self.down(x, y, right)
        try:
            time.sleep(0.03)
        finally:
            self._release(x, y, right)

    def double_click(self, x, y):
        """A REAL double-click: the second press carries CGEvent click-state 2
        (two independent clicks read as two singles — 'double-click to reset'
        controls never fire on those)."""
        Q = self.Q
        self.click(x, y)
        time.sleep(0.05)
        pt = (x, y)
        for etype in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
            e = Q.CGEventCreateMouseEvent(None, etype, pt, Q.kCGMouseButtonLeft)
            Q.CGEventSetIntegerValueField(e, Q.kCGMouseEventClickState, 2)
            Q.CGEventPost(Q.kCGHIDEventTap, e)
            time.sleep(0.03)

    def hold_click(self, x, y, hold: float = 0.35, right: bool = False):
        """Move + press-HOLD-release. A capture poll that reads the real OS
        mouse (GetCursorPos + GetAsyncKeyState) needs the button held across
        poll ticks — a plain click is too fast to be observed reliably."""
        self.move(x, y); time.sleep(0.15)
        self.down(x, y, right)
        try:
            time.sleep(hold)
        finally:
            self._release(x, y, right)
        time.sleep(0.2)

    def wheel(self, delta_lines: int):
        Q = self.Q
        e = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, int(delta_lines))
        Q.CGEventPost(Q.kCGHIDEventTap, e)


def type_text(text: str, *, enter: bool = False):
    """OS-level unicode typing (layout-independent) into the current key window.

    macOS: CGEvent with an explicit unicode payload, so it types the literal
    characters regardless of keyboard layout. The caller is responsible for
    focus (bring the target frontmost / JS_Window_SetFocus first). ``enter``
    appends a Return keypress (kVK_Return).
    """
    if platform.system() != "Darwin":
        raise NotImplementedError("OS-level typing is macOS (CGEvent) for now")
    import Quartz as Q
    for ch in text:
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.01)
    if enter:
        for down in (True, False):
            Q.CGEventPost(Q.kCGHIDEventTap,
                          Q.CGEventCreateKeyboardEvent(None, 36, down))  # kVK_Return
            time.sleep(0.02)
    time.sleep(0.15)


def bridge_click(session, title_substring: str, client_x: int, client_y: int,
                 *, right: bool = False):
    """In-process click: post WM_*BUTTONDOWN/UP in CLIENT coords to the window.

    The first-class path for SWELL-*dialog* targets on macOS (extension UIs),
    where a CGEvent click to a non-key window is swallowed by Cocoa as an
    activation. Drives custom-drawn DlgProc chrome (nav bars, canvases) without
    needing focus or screen coordinates. NOTE: a *standard* SWELL Button does
    not fire BN_CLICKED from posted button messages — use ``dialog_command``
    for those (verified on the pinned build).
    """
    btn = "RBUTTON" if right else "LBUTTON"
    wparam = 0 if right else 1
    ok = session.eval(f"""
    local h = reaper.JS_Window_Find("{title_substring}", false)
    if not h then return false end
    reaper.JS_WindowMessage_Post(h, "WM_{btn}DOWN", {wparam}, 0, {int(client_x)}, {int(client_y)})
    reaper.JS_WindowMessage_Post(h, "WM_{btn}UP", 0, 0, {int(client_x)}, {int(client_y)})
    return true""")
    if not ok:
        raise RuntimeError(f"bridge_click: window not found (title~='{title_substring}')")


def dialog_command(session, title_substring: str, command_id: int):
    """Press a standard SWELL dialog control in-process via WM_COMMAND.

    Posted BM_CLICK / raw WM_LBUTTON* do NOT fire a SWELL Button's BN_CLICKED
    (verified: the Actions window's Close button ignores both) — WM_COMMAND to
    the dialog is the reliable channel, exactly what the button would send.
    ``command_id`` is the control ID (JS_Window_GetLong(child, "ID")).
    """
    ok = session.eval(f"""
    local h = reaper.JS_Window_Find("{title_substring}", false)
    if not h then return false end
    reaper.JS_WindowMessage_Post(h, "WM_COMMAND", {int(command_id)}, 0, 0, 0)
    return true""")
    if not ok:
        raise RuntimeError(f"dialog_command: window not found (title~='{title_substring}')")


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
    ok = session.eval(f"""
    local h = reaper.JS_Window_Find("{title_substring}", false)
    if not h then return false end
    reaper.JS_WindowMessage_Send(h,"WM_LBUTTONDOWN",1,0,{int(fx)},{int(fy)})
    {moves}
    reaper.JS_WindowMessage_Send(h,"WM_LBUTTONUP",0,0,{int(tx)},{int(ty)})
    return true""")
    if not ok:
        # a drag that silently no-ops on a missing window would let the test
        # proceed as if the gesture happened (bridge_click already raises)
        raise RuntimeError(f"bridge_drag: window not found (title~='{title_substring}')")


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

    def right_click(self, frac):
        x, y = self._to_screen(*frac)
        self.mouse.click(x, y, right=True)
        self.log.add("right_click", frac=frac)

    def hold_click(self, frac, hold: float = 0.35, right: bool = False):
        x, y = self._to_screen(*frac)
        self.mouse.hold_click(x, y, hold=hold, right=right)
        self.log.add("hold_click", frac=frac, hold=hold, right=right)

    def type(self, text: str, *, enter: bool = False):
        """Type into the session's key window (bring the target to front first
        via a click/frontmost; typing goes to whatever holds keyboard focus)."""
        type_text(text, enter=enter)
        self.log.add("type", text=text, enter=enter)

    def double_click(self, frac):
        x, y = self._to_screen(*frac)
        self.mouse.double_click(x, y)
        self.log.add("double_click", frac=frac)

    def wheel(self, delta_lines, frac=(0.5, 0.67)):
        x, y = self._to_screen(*frac)
        self.mouse.move(x, y); time.sleep(0.03)
        self.mouse.wheel(delta_lines)
        self.log.add("wheel", delta=delta_lines, frac=frac)
