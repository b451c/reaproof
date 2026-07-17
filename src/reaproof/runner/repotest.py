"""ReaPack repo mode — ``reaproof test-repo <path>`` (U6.1).

ReaPack ecosystem CI validates METADATA only; no code is ever executed. This
mode adds the missing layer: metadata check (via ``reapack-index --check``
when the gem is installed — honest skip otherwise) + the per-package
zero-code batteries, budget-capped with the drop count logged (no silent
truncation).

Package discovery follows the reapack-index rule: only files in at least one
SUBDIRECTORY (the category) are packages; files at the repo root are not
indexed — reported as a note, never silently ignored.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import _emit
from reaproof.runner.scripttest import ScriptTestOptions, parse_reapack_header, run_script_battery


@dataclass
class RepoTestOptions:
    max_packages: int = 8          # per-package batteries launch REAPER: cap it
    script_opts: ScriptTestOptions | None = None


def _add(rs: ResultSet, log, name: str, status: str, **kw) -> None:
    rs.results.append(TestResult(name=name, status=status, **kw))
    log(f"  [{status.upper():9}] {name}"
        + (f" — {kw.get('message')}" if kw.get("message") else ""))


def discover_packages(repo: Path) -> tuple[list[Path], list[Path]]:
    """(packages, root_ignored): .lua/.eel/.jsfx files with a ReaPack header,
    split by the in-a-category rule."""
    packages, root_ignored = [], []
    for f in sorted(repo.rglob("*")):
        if f.suffix.lower() not in (".lua", ".eel", ".jsfx") or not f.is_file():
            continue
        header = parse_reapack_header(
            f.read_text(encoding="utf-8", errors="replace"))
        if header is None:
            continue                        # not a ReaPack package: no header
        if f.parent == repo:
            root_ignored.append(f)          # root files are never indexed
        else:
            packages.append(f)
    return packages, root_ignored


def run_repo_battery(repo: Path, out_dir: Path | None = None,
                     opts: RepoTestOptions | None = None, *,
                     log=print) -> ResultSet:
    repo = Path(repo)
    opts = opts or RepoTestOptions()
    rs = ResultSet()

    # stage 1: metadata via reapack-index (optional tool)
    tool = shutil.which("reapack-index")
    if tool:
        proc = subprocess.run([tool, "--check", str(repo)],
                              capture_output=True, text=True, timeout=300)
        status = "passed" if proc.returncode == 0 else "failed"
        _add(rs, log, "metadata: reapack-index --check", status,
             message=(proc.stdout + proc.stderr).strip()[-240:])
    else:
        _add(rs, log, "metadata: reapack-index --check", "skipped",
             message="reapack-index not installed (needs Ruby >= 3.2; "
                     "gem install reapack-index)")

    # stage 2: discovery
    packages, root_ignored = discover_packages(repo)
    if root_ignored:
        _add(rs, log, "layout: packages must live in a category subdir",
             "failed",
             message=f"{len(root_ignored)} header-carrying file(s) at the repo "
                     f"root are never indexed by ReaPack: "
                     f"{[f.name for f in root_ignored][:5]}")
    if not packages:
        _add(rs, log, "discovery: ReaPack packages", "failed",
             message="no packages found (a package = .lua/.eel with a ReaPack "
                     "header inside a category subdirectory)")
        _emit(rs, out_dir, repo)
        return rs
    _add(rs, log, "discovery: ReaPack packages", "passed",
         message=f"{len(packages)} package(s)")

    # stage 3: per-package batteries, budget-capped with the drop LOGGED
    run_list = packages[: opts.max_packages]
    if len(run_list) < len(packages):
        _add(rs, log, "budget: package cap", "skipped",
             message=f"ran {len(run_list)}/{len(packages)} packages "
                     f"(--max-packages {opts.max_packages}); the rest are "
                     f"NOT covered this run")
    for pkg in run_list:
        rel = pkg.relative_to(repo)
        pkg_out = (Path(out_dir) / str(rel).replace("/", "_")) if out_dir else None
        if pkg.suffix.lower() == ".jsfx":
            # repo mode runs the STRUCTURAL JSFX stages (compile proof, param
            # mapping, factory reset) — the full audio battery per package
            # would multiply REAPER launches; run `reaproof test <pkg>` for it
            from reaproof.runner.jsfxtest import JsfxTestOptions, run_jsfx_battery
            sub = run_jsfx_battery(
                pkg, out_dir=pkg_out,
                opts=JsfxTestOptions(signals=False, sweep_params=False),
                log=lambda *_: None)
        else:
            sub = run_script_battery(
                pkg, out_dir=pkg_out,
                opts=opts.script_opts or ScriptTestOptions(), log=lambda *_: None)
        worst = ("failed" if any(r.status == "failed" for r in sub.results)
                 else "passed")
        detail = "; ".join(f"{r.name.split(':')[0]}={r.status}"
                           for r in sub.results)[:220]
        _add(rs, log, f"package {rel}", worst, message=detail)
        rs.results.extend(TestResult(
            name=f"{rel} :: {r.name}", status=r.status,
            duration_s=r.duration_s, message=r.message,
            mutation_verified=r.mutation_verified) for r in sub.results)

    _emit(rs, out_dir, repo)
    return rs
