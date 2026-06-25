"""clap-validator + clap-info wrappers (CLAP).

clap-validator runs tests out-of-process by default (crash isolation, §10). We use
its ``--json`` output for robust parsing and keep the raw JSON + stderr as artifacts.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reaproof import paths
from reaproof.determinism import subprocess_env
from reaproof.validators.base import ValidatorResult


def clap_validator_version() -> str:
    try:
        out = subprocess.run([str(paths.CLAP_VALIDATOR), "--version"],
                             capture_output=True, text=True, timeout=15).stdout
        return out.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def parse_clap_json(data: dict) -> dict:
    """Flatten clap-validator JSON into counts + failed-test names."""
    total = passed = failed = skipped = 0
    failed_tests: list[str] = []
    for section in ("plugin-library-tests", "plugin-tests"):
        block = data.get(section, {})
        for _target, tests in block.items():
            for t in tests:
                total += 1
                code = (t.get("status") or {}).get("code")
                if code == "success":
                    passed += 1
                elif code == "skipped":
                    skipped += 1
                else:  # failed / warning / error -> treat as failure
                    failed += 1
                    failed_tests.append(t.get("name", "<unknown>"))
    return {"total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "failed_tests": failed_tests}


def run_clap_validator(
    plugin: str | Path,
    *,
    artifacts_dir: str | Path,
    hide_output: bool = True,
    timeout: float = 180.0,
) -> ValidatorResult:
    plugin = Path(plugin)
    art = Path(artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    if not paths.CLAP_VALIDATOR.exists():
        raise FileNotFoundError(f"clap-validator not provisioned: {paths.CLAP_VALIDATOR}")

    cmd = [str(paths.CLAP_VALIDATOR), "validate", "--json", str(plugin)]
    if hide_output:
        cmd.insert(2, "--hide-output")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=subprocess_env())

    json_path = art / f"clap-validator-{plugin.stem}.json"
    log_path = art / f"clap-validator-{plugin.stem}.stderr.log"
    json_path.write_text(proc.stdout)
    log_path.write_text(proc.stderr)

    try:
        counts = parse_clap_json(json.loads(proc.stdout))
    except json.JSONDecodeError:
        # tool failed before producing JSON -> a hard failure with the log as evidence
        counts = {"total": 0, "passed": 0, "failed": 1, "skipped": 0,
                  "failed_tests": ["<no JSON output>"]}

    passed = proc.returncode == 0 and counts["failed"] == 0
    return ValidatorResult(
        tool="clap-validator",
        target=str(plugin),
        passed=passed,
        exit_code=proc.returncode,
        total=counts["total"],
        passed_count=counts["passed"],
        failed_count=counts["failed"],
        skipped_count=counts["skipped"],
        failed_tests=counts["failed_tests"],
        log_path=str(log_path),
        json_path=str(json_path),
        provenance={"tool_version": clap_validator_version(), "cmd": cmd},
    )
