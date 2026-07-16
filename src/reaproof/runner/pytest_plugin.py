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

from _pytest.outcomes import Skipped
from _pytest.runner import runtestprotocol

from reaproof import mutation
from reaproof.report.provenance import build_manifest
from reaproof.report.results import ResultSet, TestResult, to_html, to_json, to_junit


def pytest_addoption(parser):
    g = parser.getgroup("reaproof", "ReaProof trustworthiness enforcement")
    g.addoption("--reaproof-repeat", type=int, default=1,
                help="re-run EVERY test N times; quarantine on disagreement (§1.4)")
    g.addoption("--mutation-check", action="store_true", default=False,
                help="enforce per-test mutation-verification: a value_bearing test "
                     "that never proved an assertion non-vacuous FAILS the run (§1.3)")
    g.addoption("--reaproof-report", default=None, metavar="DIR",
                help="write JUnit/JSON/HTML + provenance report to DIR")


def pytest_configure(config):
    config._reaproof_quarantined = set()
    config._reaproof_results = {}      # nodeid -> dict(status,duration,message)
    config.addinivalue_line("markers", "determinism: run >=2x and require identical outcomes")
    config.addinivalue_line("markers", "value_bearing: an assertion that must be mutation-verified")


def pytest_runtest_protocol(item, nextitem):
    # --reaproof-repeat=N is an explicit opt-in: EVERY selected test is
    # repeated (§1.4 "every test >= 2x"), not just gate/determinism-marked
    # ones — a plain authored test deserves the same flake defence.
    n = item.config.getoption("reaproof_repeat")
    if n <= 1:
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


def _record(item, status: str, duration: float, message: str) -> None:
    item.config._reaproof_results[item.nodeid] = {
        "status": status,
        "duration": duration,
        "message": message[:300],
        "mutation_verified": item.nodeid in mutation.MUTATION_VERIFIED,
        "value_bearing": item.get_closest_marker("value_bearing") is not None,
    }


def pytest_runtest_makereport(item, call):
    cfg = item.config
    duration = getattr(call, "duration", 0.0)
    if call.when == "setup":
        # A decorator/skipif skip (or a fixture ERROR) never reaches the call
        # phase — without recording it here the test would be INVISIBLE in the
        # report: an omitted error would leave gate_green true (a false green),
        # and honest skips (§ doctrine) would be hidden instead of surfaced.
        if call.excinfo is not None:
            if call.excinfo.errisinstance(Skipped):
                _record(item, "skipped", duration, str(call.excinfo.value))
            else:
                _record(item, "failed", duration,
                        "setup error: " + str(call.excinfo.value))
        return
    if call.when == "teardown":
        # A teardown error after a green call is still a defect — a record that
        # says "passed" while pytest reports ERROR would be a false green.
        if call.excinfo is not None:
            prior = cfg._reaproof_results.get(item.nodeid)
            if prior is not None and prior["status"] == "passed":
                prior["status"] = "failed"
                prior["message"] = ("teardown error: "
                                    + str(call.excinfo.value))[:300]
        return
    if call.when != "call":
        return
    if item.nodeid in cfg._reaproof_quarantined:
        status, message = "quarantined", str(call.excinfo.value) if call.excinfo else ""
    elif call.excinfo is None:
        status, message = "passed", ""
    elif call.excinfo.errisinstance(Skipped):
        # an in-body pytest.skip() is an HONEST SKIP, not a failure — reporting
        # it as failed would flip the gate red on a truthful constraint
        status, message = "skipped", str(call.excinfo.value)
    else:
        status, message = "failed", str(call.excinfo.value)
    _record(item, status, duration, message)


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
    # --mutation-check ENFORCES §1.3: a vacuous-risk test must fail the run,
    # not just print a red line nobody's CI reads.
    if config.getoption("mutation_check"):
        vacuous = [n for n, r in config._reaproof_results.items()
                   if r["value_bearing"] and not r["mutation_verified"]]
        if vacuous and session.exitstatus == 0:
            session.exitstatus = 1
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
