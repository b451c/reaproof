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


# ---- honest skips + invisible errors in the report (Quality Audit v2 sweep) --

def _run_report(pytester, tmp_path, source: str):
    out = tmp_path / "rep"
    pytester.makepyfile(source)
    result = pytester.runpytest("-p", PLUGIN, f"--reaproof-report={out}")
    return result, json.loads((out / "report.json").read_text())


def test_honest_skips_are_reported_as_skips_not_failures(pytester, tmp_path):
    """An in-body pytest.skip() and a decorator skip both appear in the report
    as SKIPPED (visible), and honest skips do not redden the gate."""
    result, data = _run_report(pytester, tmp_path, """
        import pytest
        def test_green():
            assert True
        def test_inbody_skip():
            pytest.skip("appearance is pinned (D27)")
        @pytest.mark.skipif(True, reason="tool not provisioned")
        def test_decorator_skip():
            assert False  # never runs
    """)
    result.assert_outcomes(passed=1, skipped=2)
    assert data["counts"] == {"passed": 1, "failed": 0, "skipped": 2,
                              "quarantined": 0}
    assert data["gate_green"] is True
    by_name = {r["name"]: r for r in data["results"]}
    assert by_name[[n for n in by_name if "inbody" in n][0]]["status"] == "skipped"
    assert by_name[[n for n in by_name if "decorator" in n][0]]["status"] == "skipped"


def test_all_skipped_is_never_green(pytester, tmp_path):
    """NEGATIVE CONTROL: a run that proves nothing (every test skipped) must
    not manufacture a green gate — mass-skip is the fake-green vector."""
    _, data = _run_report(pytester, tmp_path, """
        import pytest
        def test_a():
            pytest.skip("nope")
        @pytest.mark.skipif(True, reason="nope")
        def test_b():
            assert False
    """)
    assert data["counts"]["skipped"] == 2
    assert data["gate_green"] is False


def test_real_failure_still_reds_the_gate(pytester, tmp_path):
    """NEGATIVE CONTROL/MUTATION: the skip fix must not have widened 'skip' to
    swallow real exceptions — a genuine failure stays FAILED and reds the gate."""
    _, data = _run_report(pytester, tmp_path, """
        def test_green():
            assert True
        def test_red():
            assert 1 == 2
    """)
    assert data["counts"]["failed"] == 1
    assert data["gate_green"] is False


def test_setup_error_is_visible_and_reds_the_gate(pytester, tmp_path):
    """A fixture/setup ERROR used to be INVISIBLE in the report (never recorded)
    — with other tests green, gate_green stayed true: a false green."""
    result, data = _run_report(pytester, tmp_path, """
        import pytest
        @pytest.fixture
        def broken():
            raise RuntimeError("fixture exploded")
        def test_green():
            assert True
        def test_uses_broken(broken):
            assert True
    """)
    result.assert_outcomes(passed=1, errors=1)
    by_name = {r["name"]: r for r in data["results"]}
    errored = by_name[[n for n in by_name if "uses_broken" in n][0]]
    assert errored["status"] == "failed"
    assert "setup error" in errored["message"]
    assert data["gate_green"] is False


def test_repeat_covers_unmarked_tests_too(pytester):
    """--reaproof-repeat used to repeat only gate/determinism-marked tests —
    a plain flaky test slipped through the §1.4 defence unrepeated."""
    pytester.makepyfile("""
        _n = {"c": 0}
        def test_plain_flaky():          # NO marker
            _n["c"] += 1
            assert _n["c"] % 2 == 1
    """)
    result = pytester.runpytest("-p", PLUGIN, "--reaproof-repeat=2")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*QUARANTINED*test_plain_flaky*"])


def test_mutation_check_fails_the_run_on_vacuous_risk(pytester):
    """--mutation-check used to only PRINT the vacuous-risk line — a
    value_bearing test that never proved an assertion non-vacuous passed
    green. It must fail the run."""
    pytester.makepyfile("""
        import pytest
        @pytest.mark.value_bearing
        def test_never_mutation_verified():
            assert True
    """)
    result = pytester.runpytest("-p", PLUGIN, "--mutation-check")
    assert result.ret != 0, "vacuous-risk run exited 0"
    result.stdout.fnmatch_lines(["*VACUOUS-RISK*test_never_mutation_verified*"])
    # negative control: without the flag the run is not failed by this
    result2 = pytester.runpytest("-p", PLUGIN)
    assert result2.ret == 0


def test_teardown_error_downgrades_pass(pytester, tmp_path):
    """A teardown ERROR after a green call must not leave the record 'passed'."""
    result, data = _run_report(pytester, tmp_path, """
        import pytest
        @pytest.fixture
        def leaky():
            yield 1
            raise RuntimeError("teardown exploded")
        def test_uses_leaky(leaky):
            assert True
    """)
    result.assert_outcomes(passed=1, errors=1)
    r = data["results"][0]
    assert r["status"] == "failed" and "teardown error" in r["message"]
    assert data["gate_green"] is False
