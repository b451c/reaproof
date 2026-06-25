"""auval wrapper (macOS AudioUnit validation). System tool; exit 0 = pass."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from reaproof.determinism import subprocess_env
from reaproof.validators.base import ValidatorResult


def auval_available() -> bool:
    return shutil.which("auval") is not None


def run_auval(type_: str, subtype: str, manufacturer: str, *,
              artifacts_dir: str | Path, timeout: float = 180.0) -> ValidatorResult:
    art = Path(artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    if not auval_available():
        raise FileNotFoundError("auval not found (macOS only)")
    cmd = ["auval", "-v", type_, subtype, manufacturer]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=subprocess_env())
    log_path = art / f"auval-{type_}-{subtype}-{manufacturer}.log"
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    text = proc.stdout
    # auval prints "AU VALIDATION SUCCEEDED" / "FAILED"; exit code is authoritative
    passed = proc.returncode == 0 and "FAILED" not in text.upper()
    return ValidatorResult(
        tool="auval", target=f"{type_} {subtype} {manufacturer}", passed=passed,
        exit_code=proc.returncode, failed_count=0 if passed else 1,
        log_path=str(log_path), provenance={"cmd": cmd},
    )
