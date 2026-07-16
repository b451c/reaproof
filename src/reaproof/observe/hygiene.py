"""State-hygiene differ (Universality Roadmap U1).

Snapshot REAPER-visible state before a subject runs and diff it after: a
script/extension/action that leaks tracks, items, dirty flags, windows or
ExtState is reported with the exact leak named. Batteries declare which
mutations they EXPECT; everything else is a finding.

The snapshot is one bridge eval (cheap) plus two out-of-band observations the
bridge cannot make about itself: the persistent-ExtState file bytes and the
OS-level window census.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SNAPSHOT_LUA = """
return (function()
  local t = {}
  t.state_count = reaper.GetProjectStateChangeCount(0)
  t.dirty = reaper.IsProjectDirty(0)
  t.undo = reaper.Undo_CanUndo2(0) or ""
  t.tracks = reaper.CountTracks(0)
  t.items = reaper.CountMediaItems(0)
  local guids = {}
  for i = 0, t.tracks - 1 do
    local tr = reaper.GetTrack(0, i)
    local _, g = reaper.GetSetMediaTrackInfo_String(tr, "GUID", "", false)
    guids[#guids + 1] = g
  end
  t.track_guids = guids
  local names = {}
  local n = 0
  while true do
    local retval, name = reaper.EnumProjExtState(0, "", n)
    if not retval then break end
    n = n + 1
    if n > 512 then break end
  end
  t.projextstate_sections = n
  return t
end)()
"""


@dataclass
class HygieneSnapshot:
    data: dict[str, Any]
    windows: frozenset[str]           # titles of REAPER-owned on-screen windows
    extstate: bytes                   # reaper-extstate.ini bytes ("" if absent)


def snapshot(session) -> HygieneSnapshot:
    data = session.eval(_SNAPSHOT_LUA)
    ini = session.profile.resource_dir / "reaper-extstate.ini"
    ext = ini.read_bytes() if ini.exists() else b""
    return HygieneSnapshot(data=data or {}, windows=_window_titles(session),
                           extstate=ext)


def _window_titles(session) -> frozenset[str]:
    try:
        import Quartz as Q
        wins = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
            Q.kCGNullWindowID) or []
        return frozenset((w.get("kCGWindowName") or "")
                         for w in wins
                         if w.get("kCGWindowOwnerPID") == session.handle.pid)
    except Exception:  # noqa: BLE001 — census is best-effort off-macOS
        return frozenset()


def diff(before: HygieneSnapshot, after: HygieneSnapshot, *,
         expect_project_change: bool = False,
         expect_extstate_change: bool = False) -> list[str]:
    """Named findings for every UNDECLARED difference. Empty list == clean."""
    f: list[str] = []
    b, a = before.data, after.data
    if not expect_project_change:
        if a.get("tracks") != b.get("tracks"):
            f.append(f"track count changed {b.get('tracks')} -> {a.get('tracks')}")
        else:
            new_guids = set(a.get("track_guids") or []) - set(b.get("track_guids") or [])
            if new_guids:
                f.append(f"{len(new_guids)} replaced/new track(s) despite equal count")
        if a.get("items") != b.get("items"):
            f.append(f"item count changed {b.get('items')} -> {a.get('items')}")
        if a.get("state_count") != b.get("state_count"):
            f.append("project state changed "
                     f"({b.get('state_count')} -> {a.get('state_count')})")
        if a.get("dirty") and not b.get("dirty"):
            f.append("project left DIRTY (unsaved-changes prompt will block a quit)")
        if a.get("undo") != b.get("undo"):
            f.append(f"undo point added/changed: {a.get('undo')!r}")
        if a.get("projextstate_sections") != b.get("projextstate_sections"):
            f.append("ProjExtState sections changed "
                     f"({b.get('projextstate_sections')} -> {a.get('projextstate_sections')})")
    if not expect_extstate_change and after.extstate != before.extstate:
        f.append("persistent ExtState (reaper-extstate.ini) changed")
    leaked = after.windows - before.windows
    if leaked:
        f.append(f"window(s) left open: {sorted(t for t in leaked if t)}")
    return f
