"""Phase 2 verification gate (§14).

GATE: a known-good plugin passes and a deliberately-broken one fails — THROUGH the
validator layer — with artifacts. Demonstrated with CLAP Subject #2 (good) and its
broken variant (state.load is a no-op -> fails state restoration), via clap-validator.
"""
import subprocess
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.validators.clap import run_clap_validator
from reaproof.validators.pluginval import parse_pluginval_log


def _ensure_clap_subjects() -> None:
    """Build the CLAP reference subjects if the bundles are not present."""
    if paths.CLAP_GOOD.exists() and paths.CLAP_BROKEN.exists():
        return
    script = paths.EXAMPLES / "clap" / "build_clap.sh"
    out = paths.SUBJECTS / "clap"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bash", str(script), str(paths.CLAP_SDK_INCLUDE), str(out)],
                   check=True, capture_output=True)


needs_clap = pytest.mark.skipif(
    not paths.CLAP_VALIDATOR.exists(), reason="clap-validator not provisioned"
)


@pytest.mark.gate
@needs_clap
def test_known_good_clap_passes_validator(tmp_path):
    _ensure_clap_subjects()
    r = run_clap_validator(paths.CLAP_GOOD, artifacts_dir=tmp_path)
    assert r.passed, f"known-good plugin failed: {r.summary()} {r.failed_tests}"
    assert r.exit_code == 0 and r.failed_count == 0
    assert r.total > 0 and r.passed_count > 0
    assert Path(r.json_path).exists()  # artifact present (§1.8)


@pytest.mark.negative_control
@needs_clap
def test_broken_clap_fails_through_validator_layer(tmp_path):
    """NEGATIVE CONTROL: the broken state.load build must FAIL via the validator."""
    _ensure_clap_subjects()
    r = run_clap_validator(paths.CLAP_BROKEN, artifacts_dir=tmp_path)
    assert not r.passed, "broken plugin wrongly passed the validator"
    assert r.exit_code != 0 and r.failed_count >= 1
    # the specific defect we injected is caught: state is not restored
    assert any("state" in t for t in r.failed_tests), r.failed_tests
    assert Path(r.json_path).exists() and Path(r.log_path).exists()


# ---- pluginval parser unit test (no VST3 subject required) ----
def test_pluginval_parser_pass_and_fail():
    ok = parse_pluginval_log("Testing: Basic\n  Success\nAll tests completed", exit_code=0)
    assert ok["passed"] and ok["failed"] == 0

    bad = parse_pluginval_log(
        "Testing: Parameters\n!!! Test failed: parameter out of range\n", exit_code=1)
    assert not bad["passed"] and bad["failed"] >= 1

    # nonzero exit with no explicit failure line still counts as a failure
    silent = parse_pluginval_log("partial output", exit_code=1)
    assert not silent["passed"] and silent["failed"] >= 1
