"""Gates: ReaScript zero-code battery (Universality Roadmap U2).

The broken reference subjects ARE the mutations: each battery stage is proven
able to turn RED by a subject engineered to trip exactly that stage, and the
clean subject proves the green path (twice, §1.4).
"""
import sys
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.runner.scripttest import (
    ScriptTestOptions, parse_reapack_header, run_script_battery,
)

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="script battery uses the macOS watchdog")

SCRIPTS = paths.EXAMPLES / "scripts"


def _statuses(rs) -> dict[str, str]:
    return {r.name: r.status for r in rs.results}


def _quiet(*a, **k):
    pass


# ---- REAPER-free units -------------------------------------------------------

def test_header_parser_accepts_and_rejects():
    good = "-- @description x\n-- @version 1.2\nlocal a=1\n"
    assert parse_reapack_header(good)["version"] == "1.2"
    assert parse_reapack_header("local a=1\n") is None
    bad = "-- @description x\n-- @version none\n"
    assert not any(c.isdigit() for c in parse_reapack_header(bad)["version"])


# ---- battery gates (REAPER) ---------------------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_clean_subject_is_green_twice(tmp_path):
    for i in (1, 2):   # §1.4: identical outcome across runs
        rs = run_script_battery(SCRIPTS / "rp_good.lua",
                                out_dir=tmp_path / f"run{i}", log=_quiet)
        st = _statuses(rs)
        assert rs.gate_green, st
        assert st["load: script runs without error"] == "passed"
        assert st["hygiene: no undeclared state leaks"] == "passed"
        assert st["action: runs without runtime error"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_load_error_is_named(tmp_path):
    rs = run_script_battery(SCRIPTS / "rp_error_load.lua",
                            out_dir=tmp_path, log=_quiet)
    st = _statuses(rs)
    assert not rs.gate_green
    load = next(r for r in rs.results
                if r.name == "load: script runs without error")
    assert load.status == "failed"
    assert "nil" in load.message    # the actual Lua message travels


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_deferred_error_is_caught_by_supervision(tmp_path):
    rs = run_script_battery(SCRIPTS / "rp_error_defer.lua",
                            out_dir=tmp_path, log=_quiet)
    st = _statuses(rs)
    assert not rs.gate_green
    assert st["load: script runs without error"] == "passed"  # loads fine
    assert st["defer: no runtime error in deferred phase"] == "failed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_state_leaks_are_named_and_declarable(tmp_path):
    rs = run_script_battery(SCRIPTS / "rp_leaky.lua",
                            out_dir=tmp_path / "undeclared", log=_quiet)
    leak = next(r for r in rs.results
                if r.name == "hygiene: no undeclared state leaks")
    assert leak.status == "failed"
    assert "track count" in leak.message and "ExtState" in leak.message

    # the same subject with DECLARED expectations passes the hygiene stage —
    # proves the findings key off the declarations, not off luck
    rs2 = run_script_battery(
        SCRIPTS / "rp_leaky.lua", out_dir=tmp_path / "declared",
        opts=ScriptTestOptions(expect_project_change=True,
                               expect_extstate_change=True),
        log=_quiet)
    st2 = _statuses(rs2)
    assert st2["hygiene: no undeclared state leaks"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_gmem_leak_is_named_and_declarable(tmp_path):
    """Catalog item 8 / forum ask (b): gmem is invisible to every other
    census — the differ watches the namespaces the script attaches and names
    the leaked slots; the same subject with the write DECLARED passes."""
    rs = run_script_battery(SCRIPTS / "rp_gmem_leaky.lua",
                            out_dir=tmp_path / "undeclared", log=_quiet)
    leak = next(r for r in rs.results
                if r.name == "hygiene: no undeclared state leaks")
    assert leak.status == "failed"
    assert "gmem 'RPGmemLeak'" in leak.message and "[3]" in leak.message

    rs2 = run_script_battery(
        SCRIPTS / "rp_gmem_leaky.lua", out_dir=tmp_path / "declared",
        opts=ScriptTestOptions(expect_gmem_change=True), log=_quiet)
    st2 = _statuses(rs2)
    assert st2["hygiene: no undeclared state leaks"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_ui_subject_window_is_checked(tmp_path):
    rs = run_script_battery(SCRIPTS / "rp_ui.lua", out_dir=tmp_path,
                            opts=ScriptTestOptions(ui=True), log=_quiet)
    st = _statuses(rs)
    assert st["ui: window appeared"] == "passed"
    assert st["hygiene: no undeclared state leaks"] == "passed"  # not a leak

    # MUTATION: declaring --ui for a script that opens NO window turns RED
    rs2 = run_script_battery(SCRIPTS / "rp_good.lua", out_dir=tmp_path / "mut",
                             opts=ScriptTestOptions(ui=True), log=_quiet)
    assert _statuses(rs2)["ui: window appeared"] == "failed"


# ---- Python ReaScript gates ---------------------------------------------------

from reaproof.runner.scripttest import python_configured

pyconf = pytest.mark.skipif(
    not python_configured(),
    reason="host REAPER has no ReaScript-Python configured — the profile "
           "mirrors the host config, so there is nothing to run .py with")


@pyconf
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_python_subject_is_green(tmp_path):
    """A .py subject registers as a real action and runs cleanly under
    supervision (interpreter mirrored from the host config)."""
    rs = run_script_battery(SCRIPTS / "rp_good.py", out_dir=tmp_path, log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    assert st["metadata: ReaPack header"] == "passed"
    assert st["action: registers and resolves"] == "passed"
    assert st["action: runs without runtime error"] == "passed"


@pyconf
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_python_runtime_error_is_red(tmp_path):
    """A raising .py surfaces the same terminal ReaScript Error panel as Lua —
    the watchdog supervision must catch it."""
    rs = run_script_battery(SCRIPTS / "rp_error_run.py", out_dir=tmp_path,
                            log=_quiet)
    assert not rs.gate_green
    run = next(r for r in rs.results
               if r.name == "action: runs without runtime error")
    assert run.status == "failed"
