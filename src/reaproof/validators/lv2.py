"""LV2 metadata validation via ``lv2_validate``/``sord_validate`` (U4).

REAPER hosts LV2 natively (since 6.24; the pinned 7.75 scans ``lv2path_mac``
— verified live). ``lv2_validate`` (Homebrew ``lv2`` package) checks every
Turtle file against the installed LV2 vocabularies: property domains/ranges,
typed literals, well-formed port symbols. Binary-level linting (``lv2lint``)
is Linux-only and belongs to the CI leg.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from reaproof.determinism import subprocess_env
from reaproof.validators.base import ValidatorResult

_ERRORS = re.compile(r"Found (\d+) errors? among (\d+) files")


def lv2_validate_available() -> bool:
    return shutil.which("lv2_validate") is not None


def run_lv2_validate(bundle: str | Path, *, artifacts_dir: str | Path,
                     timeout: float = 120.0) -> ValidatorResult:
    """Validate every .ttl in an .lv2 bundle. PASS iff zero errors AND at
    least one file was actually checked (0-of-0 verifies nothing)."""
    bundle = Path(bundle)
    art = Path(artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    tool = shutil.which("lv2_validate")
    if not tool:
        raise FileNotFoundError(
            "lv2_validate not installed (brew install lv2 sord)")
    ttls = sorted(bundle.glob("*.ttl"))
    if not ttls:
        return ValidatorResult(tool="lv2_validate", target=str(bundle),
                               passed=False, exit_code=-1,
                               failed_tests=["<no .ttl files in bundle>"])
    proc = subprocess.run([tool, *map(str, ttls)], capture_output=True,
                          text=True, timeout=timeout, env=subprocess_env())
    log_path = art / f"lv2_validate-{bundle.stem}.log"
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr,
                        encoding="utf-8")
    m = _ERRORS.search(proc.stdout + proc.stderr)
    errors = int(m.group(1)) if m else -1
    checked = int(m.group(2)) if m else 0
    passed = proc.returncode == 0 and errors == 0 and checked > 0
    return ValidatorResult(
        tool="lv2_validate", target=str(bundle), passed=passed,
        exit_code=proc.returncode, total=checked,
        passed_count=checked if passed else 0,
        failed_count=errors if errors > 0 else (0 if passed else 1),
        log_path=str(log_path),
    )
