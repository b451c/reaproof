"""ReaProof pytest plugin — enforces the doctrine suite-wide.

- ``--reaproof-repeat=N`` re-runs every `gate`/`determinism`-marked test N times and
  QUARANTINES it if the outcomes disagree (§1.4 — flaky never retried to green).
- At session end it emits a JUnit/JSON/HTML report with a provenance manifest and a
  per-test mutation-verification status (§1.8, §11.2), so trust is automatic, not manual.
- ``--mutation-check`` prints which tests proved an assertion non-vacuous (§1.3) and
  flags `value_bearing`-marked tests that did not.

Registered via ``tests/conftest.py`` (``pytest_plugins``) and the ``pytest11`` entry point.
"""
from __future__ import annotations

import json
from pathlib import Path

from _pytest.runner import runtestprotocol

from reaproof import mutation
from reaproof.report.provenance import build_manifest
from reaproof.report.results import ResultSet, TestResult, to_html, to_json, to_junit


def pytest_addoption(parser):
    g = parser.getgroup("reaproof", "ReaProof trustworthiness enforcement")
    g.addoption("--reaproof-repeat", type=int, default=1,
                help="re-run gate/determinism tests N times; quarantine on disagreement (§1.4)")
    g.addoption("--mutation-check", action="store_true", default=False,
                help="report per-test mutation-verification status (§1.3)")
    g.addoption("--reaproof-report", default=None, metavar="DIR",
                help="write JUnit/JSON/HTML + provenance report to DIR")


def pytest_configure(config):
    config._reaproof_quarantined = set()
    config._reaproof_results = {}      # nodeid -> dict(status,duration,message)
    config.addinivalue_line("markers", "determinism: run >=2x and require identical outcomes")
    config.addinivalue_line("markers", "value_bearing: an assertion that must be mutation-verified")


def _repeatable(item) -> bool:
    return bool(item.get_closest_marker("gate") or item.get_closest_marker("determinism"))


def pytest_runtest_protocol(item, nextitem):
    n = item.config.getoption("reaproof_repeat")
    if n <= 1 or not _repeatable(item):
        return None  # default protocol
    outcomes, last = [], None
    for i in range(n):
        reports = runtestprotocol(item, nextitem=nextitem, log=(i == n - 1))
        last = reports
        call = next((r for r in reports if r.when == "call"), None)
        outcomes.append(bool(call and call.passed))
    if len(set(outcomes)) > 1:  # runs disagreed -> FLAKY -> quarantine (red + visible)
        item.config._reaproof_quarantined.add(item.nodeid)
        for r in last or []:
            if r.when == "call":
                r.outcome = "failed"
                r.longrepr = (f"QUARANTINED (flaky, §1.4): outcomes across {n} runs "
                              f"disagreed {outcomes} — never retried to green")
                # authoritative result record (overrides makereport, which ran earlier)
                item.config._reaproof_results[item.nodeid] = {
                    "status": "quarantined", "duration": getattr(r, "duration", 0.0),
                    "message": f"runs disagreed: {outcomes}",
                    "mutation_verified": item.nodeid in mutation.MUTATION_VERIFIED,
                    "value_bearing": item.get_closest_marker("value_bearing") is not None,
                }
    return True


def pytest_runtest_makereport(item, call):
    if call.when != "call":
        return
    cfg = item.config
    quarantined = item.nodeid in cfg._reaproof_quarantined
    status = ("quarantined" if quarantined
              else "passed" if call.excinfo is None else "failed")
    cfg._reaproof_results[item.nodeid] = {
        "status": status,
        "duration": getattr(call, "duration", 0.0),
        "message": "" if call.excinfo is None else str(call.excinfo.value)[:300],
        "mutation_verified": item.nodeid in mutation.MUTATION_VERIFIED,
        "value_bearing": item.get_closest_marker("value_bearing") is not None,
    }


def _result_set(config) -> ResultSet:
    rs = ResultSet()
    for nodeid, r in config._reaproof_results.items():
        rs.results.append(TestResult(
            name=nodeid, status=r["status"], duration_s=r["duration"],
            message=r["message"],
            mutation_verified=(True if r["mutation_verified"]
                               else (False if r["value_bearing"] else None)),
        ))
    return rs


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    out = config.getoption("reaproof_report")
    if not out:
        return
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    rs = _result_set(config)
    manifest = build_manifest().to_dict()
    (d / "report.junit.xml").write_text(to_junit(rs))
    (d / "report.json").write_text(json.dumps(
        {"manifest": manifest, **json.loads(to_json(rs))}, indent=2))
    (d / "report.html").write_text(to_html(rs))


def pytest_terminal_summary(terminalreporter):
    config = terminalreporter.config
    quarantined = config._reaproof_quarantined
    if quarantined:
        terminalreporter.write_sep("=", "ReaProof: QUARANTINED (flaky) tests", yellow=True)
        for n in sorted(quarantined):
            terminalreporter.write_line(f"  QUARANTINED  {n}")
    if config.getoption("mutation_check"):
        res = config._reaproof_results
        verified = [n for n, r in res.items() if r["mutation_verified"]]
        vacuous_risk = [n for n, r in res.items()
                        if r["value_bearing"] and not r["mutation_verified"]]
        terminalreporter.write_sep("=", "ReaProof: mutation-verification (§1.3)")
        terminalreporter.write_line(f"  mutation-verified: {len(verified)} test(s)")
        for n in vacuous_risk:
            terminalreporter.write_line(f"  VACUOUS-RISK (value_bearing, not mutation-verified): {n}",
                                        red=True)
