"""pluginval wrapper (VST2/VST3/AU). Runs headless, out-of-process (§10).

pluginval has no JSON mode; its exit code is authoritative (0 = pass, 1 = fail) and
the text log lists per-test results. We keep the full log as an artifact and parse
the failure count from it. A VST3/AU subject is required to exercise this live; the
parser is unit-tested independently of a plugin.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from reaproof import paths
from reaproof.determinism import subprocess_env
from reaproof.validators.base import ValidatorResult

_FAIL_RE = re.compile(r"!!!\s*(?:Test\s+)?failed", re.IGNORECASE)
_TESTNAME_RE = re.compile(r"^\s*(?:Test:|Testing:|-)\s*(.+?)\s*$")


def parse_pluginval_log(text: str, exit_code: int) -> dict:
    """Derive counts + failed-section names from a pluginval text log."""
    failed_sections: list[str] = []
    current = None
    failed = 0
    for line in text.splitlines():
        # pluginval prefixes a section/test with its name; failures are marked '!!!'
        m = _TESTNAME_RE.match(line)
        if m and not line.lstrip().startswith("!!!"):
            current = m.group(1)
        if _FAIL_RE.search(line):
            failed += 1
            failed_sections.append(current or line.strip()[:80])
    # exit code is authoritative even if the text parse finds nothing
    passed = exit_code == 0 and failed == 0
    if not passed and failed == 0:
        failed = 1
        failed_sections.append("<nonzero exit, no explicit failure line>")
    return {"failed": failed, "failed_tests": failed_sections, "passed": passed}


def run_pluginval(
    plugin: str | Path,
    *,
    artifacts_dir: str | Path,
    strictness: int = 5,
    seed: int | None = None,
    sample_rates: list[int] | None = None,
    block_sizes: list[int] | None = None,
    skip_gui: bool = False,
    timeout: float = 300.0,
) -> ValidatorResult:
    plugin = Path(plugin)
    art = Path(artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    if not paths.PLUGINVAL.exists():
        raise FileNotFoundError(f"pluginval not provisioned: {paths.PLUGINVAL}")

    cmd = [str(paths.PLUGINVAL), "--strictness-level", str(strictness), "--validate",
           str(plugin).rstrip("/")]  # NOTE: no trailing slash on the path (§10 gotcha)
    if skip_gui:
        cmd.insert(1, "--skip-gui-tests")
    if seed is not None:
        cmd[1:1] = ["--randomise", "--seed", hex(seed)]
    if sample_rates:
        cmd[1:1] = ["--sample-rates", ",".join(map(str, sample_rates))]
    if block_sizes:
        cmd[1:1] = ["--block-sizes", ",".join(map(str, block_sizes))]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=subprocess_env())
    log_path = art / f"pluginval-{plugin.stem}.log"
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    counts = parse_pluginval_log(proc.stdout, proc.returncode)
    return ValidatorResult(
        tool="pluginval", target=str(plugin), passed=counts["passed"],
        exit_code=proc.returncode, failed_count=counts["failed"],
        failed_tests=counts["failed_tests"], log_path=str(log_path),
        provenance={"cmd": cmd, "strictness": strictness},
    )


def pluginval_version() -> str:
    try:
        return subprocess.run([str(paths.PLUGINVAL), "--version"], capture_output=True,
                              text=True, timeout=15).stdout.strip() or "present"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
