"""Tests for the ReaProof pytest plugin (auto-repeat/quarantine + report emission).

Uses pytest's `pytester` to run the plugin against synthetic tests in isolation.
"""
import json

PLUGIN = "reaproof.runner.pytest_plugin"


def test_flaky_test_is_quarantined(pytester):
    pytester.makepyfile("""
        import pytest
        _n = {"c": 0}
        @pytest.mark.gate
        def test_flaky():
            _n["c"] += 1
            assert _n["c"] % 2 == 1   # pass then fail -> the two repeats disagree
    """)
    result = pytester.runpytest("-p", PLUGIN, "--reaproof-repeat=2")
    result.assert_outcomes(failed=1)                     # flaky never passes
    result.stdout.fnmatch_lines(["*QUARANTINED*test_flaky*"])


def test_stable_test_survives_repeat(pytester):
    pytester.makepyfile("""
        import pytest
        @pytest.mark.gate
        def test_stable():
            assert 2 + 2 == 4
    """)
    result = pytester.runpytest("-p", PLUGIN, "--reaproof-repeat=3")
    result.assert_outcomes(passed=1)


def test_report_is_emitted_with_provenance(pytester, tmp_path):
    pytester.makepyfile("""
        import pytest
        @pytest.mark.gate
        def test_ok():
            assert True
    """)
    out = tmp_path / "rep"
    result = pytester.runpytest("-p", PLUGIN, f"--reaproof-report={out}")
    result.assert_outcomes(passed=1)
    assert (out / "report.junit.xml").exists()
    assert (out / "report.html").exists()
    data = json.loads((out / "report.json").read_text())
    assert "manifest" in data and data["manifest"]["reaper_build"]
    assert data["counts"]["passed"] == 1
