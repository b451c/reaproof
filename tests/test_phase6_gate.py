"""Phase 6 verification gate (§14).

GATE: the matrix runs in CI (the .github workflow; PENDING CI for the OS legs),
uploads artifacts, REPORTS COVERAGE GAPS, and QUARANTINES (does not hide) an injected
flaky test. The coverage + quarantine + report machinery is verified here locally;
the matrix execution is the CI's job.
"""
import pytest

from reaproof.coverage import coverage_report
from reaproof.report.results import ResultSet, TestResult, to_html, to_json, to_junit
from reaproof.runner.quarantine import Verdict, repeat


@pytest.mark.gate
def test_coverage_reports_gaps():
    # a knob exercised only on a few dimensions -> the rest are reported as gaps
    rep = coverage_report({"rotary_knob": {"functional", "dsp", "visual_at_value",
                                           "interaction", "redraw", "multimonitor"}})
    gaps = rep.gaps()
    assert "rotary_knob" in gaps
    assert "automation" in gaps["rotary_knob"] and "state" in gaps["rotary_knob"]
    assert 0.0 < rep.fraction < 1.0
    # N/A cells are excluded from the denominator (a meter has no 'functional' cell)
    mrep = coverage_report({"meter": {"dsp", "visual_at_value"}})
    assert "functional" not in mrep.controls[0].uncovered


@pytest.mark.gate
def test_full_coverage_has_no_gaps():
    from reaproof.coverage.taxonomy import applicable_cells
    rep = coverage_report({"rotary_knob": set(applicable_cells("rotary_knob"))})
    assert rep.gaps() == {} and rep.fraction == 1.0


@pytest.mark.negative_control
def test_flaky_test_is_quarantined_not_retried_to_green():
    calls = {"n": 0}

    def flaky():            # passes on run 1, fails on run 2 -> disagreement
        calls["n"] += 1
        assert calls["n"] % 2 == 1, "flaky failure"

    res = repeat(flaky, n=2)
    assert res.verdict is Verdict.QUARANTINED, res.verdict
    assert res.quarantined and "FLAKY" in res.reason.upper()


def test_stable_outcomes_pass_or_fail_cleanly():
    assert repeat(lambda: 1 + 1, n=3).verdict is Verdict.PASSED

    def always_fail():
        raise AssertionError("nope")
    assert repeat(always_fail, n=2).verdict is Verdict.FAILED


def test_report_emits_and_quarantine_is_visible_not_green():
    rs = ResultSet([
        TestResult("knob_dual_channel", "passed", 1.2, mutation_verified=True,
                   artifacts=["good.png"]),
        TestResult("flaky_thing", "quarantined", 0.3, message="runs disagreed"),
    ])
    assert not rs.gate_green                       # quarantine is never counted green
    j = to_junit(rs)
    assert "QUARANTINED" in j and "skipped" in j   # visible in JUnit, not a silent pass
    data = to_json(rs)
    assert '"quarantined": 1' in data and '"gate_green": false' in data
    html = to_html(rs)
    assert "QUARANTINED" in html and "non-vacuous" in html
