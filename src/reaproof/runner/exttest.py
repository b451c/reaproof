"""Universal, zero-config native-extension battery — ``reaproof test reaper_x.dylib`` (U3).

Extension load failures are SILENT in REAPER (verified: no dialog, no log
entry beyond the splashlog line) — so the only honest load proof is an
OBSERVABLE DIFF between a profile without the extension and one with it:

1. **naming contract** (static) — REAPER only loads ``reaper_*`` dylibs from
   UserPlugins; a mis-named file is never even attempted.
2. **baseline snapshot** — a pristine session enumerates every action
   (kbd_enumerateActions across all sections) and every ``reaper.*`` API
   function.
3. **subject session** — same snapshot WITH the extension installed
   (via the ``extensions=`` install path: UserPlugins + quarantine cleared).
   The splashlog must carry the "Loading plug-in: <name>" line (attempt
   proof); the diff yields the registered actions/APIs (registration proof).
   Zero observables with a successful load line is reported as an honest
   warning (hook-only extensions exist), never a fabricated pass.
4. **opt-in --run-actions** — each newly registered action runs (deferred)
   under the DialogMonitor with a hygiene diff around it; a modal panel, a
   dead engine, process death, or an undeclared state leak is a named FAIL.
   Off by default: extension actions can be destructive or interactive.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from reaproof.observe import hygiene
from reaproof.observe.watchdog import DialogMonitor
from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import _emit
from reaproof.runner.session import ReaperSession

# Main=0, MIDI editor/eventlist/inline, Media Explorer. Section 100
# (Main alt-recording) MIRRORS section 0's action list (verified: one
# registered action enumerated in both), so including it double-counts
# every main action — deliberately omitted.
_SECTIONS = (0, 32060, 32061, 32062, 32063)

# STABLE action identity: numeric command ids are assigned PER INSTANCE for
# extension/script actions (verified the hard way — a naive id-based diff
# declared every SWS action "new" and melted the battery into thousands of
# lookups). The stable key is the NAMED command (_XYZ) when one exists
# (extension/script actions always have one), else the section+label for
# native actions. ReverseNamedCommandLookup runs INSIDE this one chunk — no
# per-entry round-trips.
_ENUM_ACTIONS_LUA = """
return (function()
  local out = {}
  for _, sec in ipairs({%s}) do
    local i = 0
    while true do
      local cmd, name = reaper.kbd_enumerateActions(sec, i)
      if not cmd or cmd == 0 then break end
      local named = reaper.ReverseNamedCommandLookup(cmd)
      if named then
        out[#out + 1] = sec .. ":_" .. named
      else
        out[#out + 1] = sec .. ":~" .. (name or "")
      end
      i = i + 1
    end
  end
  return out
end)()
""" % ", ".join(str(s) for s in _SECTIONS)

_ENUM_API_LUA = """
return (function()
  local out = {}
  for k, v in pairs(reaper) do
    if type(v) == "function" then out[#out + 1] = k end
  end
  return out
end)()
"""


@dataclass
class ExtTestOptions:
    run_actions: bool = False       # actions may be destructive: explicit opt-in
    settle: float = 1.5             # per-action supervision window (s)


def _add(rs: ResultSet, log, name: str, status: str, **kw) -> None:
    rs.results.append(TestResult(name=name, status=status, **kw))
    log(f"  [{status.upper():9}] {name}"
        + (f" — {kw.get('message')}" if kw.get("message") else ""))


def _snapshot(session) -> tuple[set[str], set[str]]:
    actions = set(session.eval(_ENUM_ACTIONS_LUA, timeout=60, hang_timeout=30) or [])
    apis = set(session.eval(_ENUM_API_LUA) or [])
    return actions, apis


def _split(entry: str) -> tuple[str, str]:
    """(section, identity) — identity is '_Named' or '~Native label'."""
    sec, ident = entry.split(":", 1)
    return sec, ident


def run_extension_battery(dylib: Path, out_dir: Path | None = None,
                          opts: ExtTestOptions | None = None, *,
                          log=print) -> ResultSet:
    dylib = Path(dylib)
    opts = opts or ExtTestOptions()
    rs = ResultSet()

    # 1) naming contract — a mis-named dylib is silently ignored by REAPER
    if not dylib.name.startswith("reaper_"):
        _add(rs, log, "naming: reaper_*.dylib contract", "failed",
             message=f"{dylib.name!r} would never be loaded — REAPER only "
                     "loads UserPlugins/reaper_*.dylib")
        _emit(rs, out_dir, dylib)
        return rs
    _add(rs, log, "naming: reaper_*.dylib contract", "passed")

    # 2) baseline (no extension)
    log("baseline snapshot (pristine profile)…")
    with ReaperSession(f"exttest-base-{dylib.stem}") as s:
        base_actions, base_apis = _snapshot(s)

    # 3) subject session
    log("subject snapshot (extension installed)…")
    with ReaperSession(f"exttest-subj-{dylib.stem}", extensions=[dylib]) as s:
        splash = (s.profile.artifacts_dir / "splash.log")
        splash_text = splash.read_text(encoding="utf-8", errors="replace") \
            if splash.exists() else ""
        attempted = f"Loading plug-in: {dylib.name}" in splash_text
        if attempted:
            _add(rs, log, "load: attempted by REAPER (splashlog)", "passed")
        else:
            _add(rs, log, "load: attempted by REAPER (splashlog)", "failed",
                 message="no 'Loading plug-in' line — wrong arch/location?")

        subj_actions, subj_apis = _snapshot(s)
        new_actions = subj_actions - base_actions
        new_apis = subj_apis - base_apis

        if new_actions or new_apis:
            detail = []
            if new_actions:
                detail.append("actions: " + "; ".join(sorted(new_actions)[:8]))
            if new_apis:
                detail.append("APIs: " + ", ".join(sorted(new_apis)[:8]))
            _add(rs, log, "register: observable actions/APIs", "passed",
                 message=" | ".join(detail)[:280],
                 provenance={"new_actions": sorted(new_actions),
                             "new_apis": sorted(new_apis)})
        elif attempted:
            # honest warning, not a fabricated pass: load succeeded but nothing
            # enumerable proves registration (hook-only extensions exist)
            _add(rs, log, "register: observable actions/APIs", "skipped",
                 message="loaded but registered no enumerable actions/APIs "
                         "(hook-only extension?) — nothing to verify")
        else:
            _add(rs, log, "register: observable actions/APIs", "failed",
                 message="not attempted AND nothing registered")

        # 4) opt-in per-action smoke
        if opts.run_actions and new_actions:
            log(f"running {len(new_actions)} registered action(s)…")
            with DialogMonitor(s.handle.pid,
                               evidence_dir=(Path(out_dir) / "dialogs")
                               if out_dir else None) as mon:
                for entry in sorted(new_actions):
                    sec, ident = _split(entry)
                    label = ident.lstrip("_~")
                    if sec != "0":
                        _add(rs, log, f"action [{sec}] {label}: smoke", "skipped",
                             message="non-main section not runnable via Main_OnCommand")
                        continue
                    if not ident.startswith("_"):
                        _add(rs, log, f"action {label}: smoke", "skipped",
                             message="no named command id — cannot resolve reliably")
                        continue
                    cmd = s.eval(f"return reaper.NamedCommandLookup('{ident}')")
                    if not cmd or cmd <= 0:
                        _add(rs, log, f"action {label}: smoke", "failed",
                             message="named command did not resolve in the subject session")
                        continue
                    before = hygiene.snapshot(s)
                    s.eval(f"reaper.defer(function() reaper.Main_OnCommand({cmd}, 0) end); "
                           "return true")
                    verdict = None
                    deadline = time.monotonic() + opts.settle
                    while time.monotonic() < deadline:
                        if not s.is_alive:
                            verdict = "process died"
                            break
                        if mon.sightings():
                            verdict = ("modal panel: "
                                       + (mon.sightings()[0].title or "<untitled>"))
                            break
                        time.sleep(0.25)
                    if verdict is None:
                        try:
                            s.eval("return 1", timeout=3, hang_timeout=2)
                        except Exception:  # noqa: BLE001
                            verdict = "engine stopped/blocked after the action"
                    if verdict:
                        mon.dismiss()
                        _add(rs, log, f"action {label}: smoke", "failed",
                             message=verdict)
                        if not s.is_alive:
                            break               # nothing further to run against
                        continue
                    findings = hygiene.diff(before, hygiene.snapshot(s))
                    if findings:
                        _add(rs, log, f"action {label}: smoke", "failed",
                             message="state leak: " + "; ".join(findings)[:200])
                    else:
                        _add(rs, log, f"action {label}: smoke", "passed")

    _emit(rs, out_dir, dylib)
    return rs
