"""Universal, zero-config ReaScript battery — ``reaproof test <script.lua>`` (U2).

No public framework runs ReaScript code in CI anywhere in the ecosystem
(ReaPack CI validates metadata only) — this battery is that missing layer.
Stages, all derivable without user-written test code:

1. **static lint** — ``luacheck`` with the community-standard globals
   (``reaper``, ``gfx``), when the tool is installed; honest skip otherwise.
2. **ReaPack header lint** — if the script carries a header, ``@version`` is
   mandatory (reapack-index rule) and must contain a digit.
3. **load checkpoint** — the script runs via the bridge inside ``pcall``, so a
   load-time/top-level error comes back as a NAMED failure with the Lua
   message (it never reaches REAPER's terminal error handler — see D29).
4. **deferred-phase supervision** — after load, the defer engine is watched:
   a "ReaScript Error" panel sighting or a dead bridge = FAIL (D29: a script
   error kills REAPER's whole defer engine; only the out-of-process watchdog
   can see it).
5. **state hygiene** — leaks (tracks/items/dirty/undo/ExtState/windows) are
   named findings unless declared expected via options.
6. **UI check** (``--ui``) — the script's window must actually appear
   (JS enumeration); its presence is then not counted as a leak.
7. **action registration** — in a FRESH session (D29: never reuse a session
   that may carry subject defers), the script registers as a real action,
   resolves via NamedCommandLookup, and runs deferred under the DialogMonitor.

EEL scripts get stages 2 and 7 only (dofile/pcall is Lua-specific); Python
ReaScripts are honestly skipped (they need a configured interpreter).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from reaproof.observe import hygiene
from reaproof.observe.watchdog import DialogMonitor
from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import _emit
from reaproof.runner.session import ReaperSession


@dataclass
class ScriptTestOptions:
    ui: bool = False                     # a window is EXPECTED to appear
    expect_project_change: bool = False  # script legitimately edits the project
    expect_extstate_change: bool = False  # script legitimately persists state
    expect_gmem_change: bool = False     # script legitimately writes shared gmem
    settle: float = 2.5                  # deferred-phase supervision window (s)


#: gmem namespaces a script binds to — the hygiene differ watches exactly these
_GMEM_ATTACH = re.compile(r"gmem_attach\(\s*['\"]([\w.-]+)")


def _add(rs: ResultSet, log, name: str, status: str, **kw) -> None:
    rs.results.append(TestResult(name=name, status=status, **kw))
    log(f"  [{status.upper():9}] {name}"
        + (f" — {kw.get('message')}" if kw.get("message") else ""))


# ---- stage 1: luacheck -------------------------------------------------------

def _stage_luacheck(script: Path, rs: ResultSet, log) -> None:
    tool = shutil.which("luacheck")
    if not tool:
        _add(rs, log, "static: luacheck", "skipped",
             message="luacheck not installed (luarocks install luacheck)")
        return
    r = subprocess.run(
        [tool, "--globals", "reaper", "gfx", "--no-color", "-q", str(script)],
        capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        _add(rs, log, "static: luacheck", "passed")
    else:
        tail = (r.stdout or r.stderr).strip()[-300:]
        _add(rs, log, "static: luacheck", "failed", message=tail)


# ---- stage 2: ReaPack header lint --------------------------------------------

_TAG = re.compile(r"^\s*(?:--|//|#|/\*|@)?\s*@(\w+)\s*(.*)$")


def parse_reapack_header(text: str) -> dict[str, str] | None:
    """The leading-comment ReaPack header as {tag: value}, or None if absent.

    Comment prefixes cover the packaged languages: ``--`` (Lua/EEL),
    ``//`` (JSFX/EEL2), ``#`` (Python), ``/* */`` blocks. A JSFX package's
    header follows its ``desc:`` line, so non-comment directive lines never
    terminate the scan before the first tag is seen.
    """
    tags: dict[str, str] = {}
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if tags and not stripped.startswith(("--", "//", "#", "*", "@")) \
                and stripped != "":
            break
        m = _TAG.match(line)
        if m:
            tags.setdefault(m.group(1).lower(), m.group(2).strip())
    return tags or None


def python_configured() -> bool:
    """True iff the HOST REAPER has ReaScript-Python configured — the isolated
    profile mirrors those ini keys (see Provisioner._python_ini_lines)."""
    from reaproof import paths
    user_ini = paths.USER_REAPER_RES / "reaper.ini"
    if not user_ini.exists():
        return False
    text = user_ini.read_text(encoding="utf-8", errors="replace")
    return "pythonlibdll64=" in text and "pythonlibpath64=" in text


def _stage_header(script: Path, rs: ResultSet, log) -> None:
    header = parse_reapack_header(
        script.read_text(encoding="utf-8", errors="replace"))
    if header is None:
        _add(rs, log, "metadata: ReaPack header", "skipped",
             message="no header (fine for private scripts; ReaPack needs one)")
        return
    version = header.get("version", "")
    if not version or not any(c.isdigit() for c in version):
        _add(rs, log, "metadata: ReaPack header", "failed",
             message="@version missing or carries no digit "
                     "(mandatory per reapack-index)")
    else:
        _add(rs, log, "metadata: ReaPack header", "passed",
             message=f"@version {version}")


# ---- stages 3-6: load + supervision + hygiene + UI ---------------------------

def _stage_load_and_hygiene(script: Path, opts: ScriptTestOptions,
                            rs: ResultSet, log, art: Path | None) -> None:
    # gmem is invisible to project/ExtState/window censuses — watch every
    # namespace the script binds to (catalog item 8 / forum ask (b))
    gmem_ns = sorted(set(_GMEM_ATTACH.findall(
        script.read_text(encoding="utf-8", errors="replace"))))
    with ReaperSession(f"scripttest-{script.stem}") as s:
        before = hygiene.snapshot(s, gmem_namespaces=gmem_ns)
        with DialogMonitor(s.handle.pid,
                           evidence_dir=(art / "dialogs") if art else None) as mon:
            res = s.eval(
                "local ok, err = pcall(dofile, [[%s]])\n"
                "return {ok = ok, err = tostring(err)}" % script)
            if not res or not res.get("ok"):
                _add(rs, log, "load: script runs without error", "failed",
                     message=(res or {}).get("err", "no result"))
                return
            _add(rs, log, "load: script runs without error", "passed")

            # deferred-phase supervision (D29: only the watchdog can see it)
            deadline = time.monotonic() + opts.settle
            engine_dead = None
            while time.monotonic() < deadline:
                err_panels = [p for p in mon.sightings()
                              if "ReaScript Error" in p.title]
                if err_panels:
                    engine_dead = "ReaScript Error panel appeared"
                    break
                try:
                    s.eval("return 1", timeout=3, hang_timeout=2)
                except Exception:  # noqa: BLE001 — any bridge death counts
                    engine_dead = "defer engine stopped (bridge dead)"
                    break
                time.sleep(0.4)
            if engine_dead:
                _add(rs, log, "defer: no runtime error in deferred phase",
                     "failed", message=engine_dead)
                return
            _add(rs, log, "defer: no runtime error in deferred phase", "passed")

            after = hygiene.snapshot(s, gmem_namespaces=gmem_ns)

        if opts.ui:
            new_windows = after.windows - before.windows
            if new_windows:
                _add(rs, log, "ui: window appeared", "passed",
                     message=str(sorted(t for t in new_windows if t)))
            else:
                _add(rs, log, "ui: window appeared", "failed",
                     message="--ui declared but no new window is on screen")
            after = hygiene.HygieneSnapshot(     # expected window ≠ a leak
                data=after.data, windows=before.windows,
                extstate=after.extstate, gmem=after.gmem)

        findings = hygiene.diff(
            before, after,
            expect_project_change=opts.expect_project_change,
            expect_extstate_change=opts.expect_extstate_change,
            expect_gmem_change=opts.expect_gmem_change)
        if findings:
            _add(rs, log, "hygiene: no undeclared state leaks", "failed",
                 message="; ".join(findings)[:280])
        else:
            _add(rs, log, "hygiene: no undeclared state leaks", "passed")


# ---- stage 7: real action registration + run ---------------------------------

def _stage_action(script: Path, opts: ScriptTestOptions,
                  rs: ResultSet, log, art: Path | None) -> None:
    with ReaperSession(f"scriptact-{script.stem}") as s:
        # the script must live inside the profile for a relocatable action
        dest = s.profile.resource_dir / "Scripts" / script.name
        dest.write_bytes(script.read_bytes())
        cmd = s.eval(f"return reaper.AddRemoveReaScript(true, 0, [[{dest}]], true)")
        if not cmd or cmd <= 0:
            _add(rs, log, "action: registers and resolves", "failed",
                 message="AddRemoveReaScript returned no command id")
            return
        named = s.eval(f"return reaper.ReverseNamedCommandLookup({cmd})")
        _add(rs, log, "action: registers and resolves", "passed",
             message=f"cmd={cmd} _{named or '?'}")

        # the FIRST .py action initialises the embedded interpreter, which can
        # stall the main thread for several seconds (live-measured) — give the
        # supervision window that headroom before judging the engine dead
        settle = opts.settle + (6.0 if script.suffix.lower() == ".py" else 0.0)
        with DialogMonitor(s.handle.pid,
                           evidence_dir=(art / "dialogs") if art else None) as mon:
            s.eval(f"reaper.defer(function() reaper.Main_OnCommand({cmd}, 0) end); "
                   "return true")
            deadline = time.monotonic() + settle
            verdict = None
            while time.monotonic() < deadline:
                if any("ReaScript Error" in p.title for p in mon.sightings()):
                    verdict = "ReaScript Error panel (runtime error as an action)"
                    break
                time.sleep(0.3)
            if verdict is None:
                try:
                    s.eval("return 1", timeout=3, hang_timeout=2)
                except Exception:  # noqa: BLE001
                    verdict = "defer engine stopped after the action ran"
            if verdict:
                mon.dismiss()
                _add(rs, log, "action: runs without runtime error", "failed",
                     message=verdict)
            else:
                _add(rs, log, "action: runs without runtime error", "passed")
        # D29: this session may carry subject defers/a wounded engine — the
        # context manager tears it down; it is never reused.


# ---- entry -------------------------------------------------------------------

def run_script_battery(script: Path, out_dir: Path | None = None,
                       opts: ScriptTestOptions | None = None, *,
                       log=print) -> ResultSet:
    # absolute: the Lua chunk dofile()s this path inside REAPER, whose cwd
    # is NOT the caller's (relative paths fail with 'cannot open')
    script = Path(script).resolve()
    opts = opts or ScriptTestOptions()
    rs = ResultSet()
    suffix = script.suffix.lower()
    if suffix == ".py" and not python_configured():
        _add(rs, log, "script battery", "skipped",
             message="the host REAPER has no ReaScript-Python configured "
                     "(Preferences > Plug-ins > ReaScript) — the isolated "
                     "profile mirrors that config, so there is nothing to "
                     "run the subject with")
        _emit(rs, out_dir, script)
        return rs

    log("static + metadata…")
    if suffix == ".lua":
        _stage_luacheck(script, rs, log)
    _stage_header(script, rs, log)

    if suffix == ".lua":
        log("load + deferred-phase supervision + hygiene…")
        _stage_load_and_hygiene(script, opts, rs, log, out_dir)
    else:
        _add(rs, log, "load: script runs without error", "skipped",
             message=f"{suffix} has no in-bridge load path (Lua only); "
                     "covered by the action stage")

    log("action registration + run…")
    _stage_action(script, opts, rs, log, out_dir)

    _emit(rs, out_dir, script)
    return rs
