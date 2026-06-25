"""Result model + JUnit/JSON/HTML emitters (§11.2).

The HTML surfaces what makes a result trustworthy: status, the mutation-check outcome
(so a reviewer sees the assertion is non-vacuous), artifacts, and the provenance
manifest. Quarantined (flaky) tests are reported prominently and excluded from the
green/red gate while remaining visible (§11.3).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html import escape
from typing import Any


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class despite the name
    name: str
    status: str                       # "passed" | "failed" | "quarantined"
    duration_s: float = 0.0
    message: str = ""
    mutation_verified: bool | None = None   # None = not run; True = proven non-vacuous
    artifacts: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultSet:
    results: list[TestResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {"passed": 0, "failed": 0, "quarantined": 0}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    @property
    def gate_green(self) -> bool:
        # quarantined tests do NOT pass and do NOT fail the gate silently — but any
        # failure is red, and quarantine is surfaced (never counted as green).
        return all(r.status == "passed" for r in self.results) if self.results else False


def to_junit(rs: ResultSet) -> str:
    c = rs.counts()
    suite = ET.Element("testsuite", name="reaproof",
                       tests=str(len(rs.results)), failures=str(c["failed"]),
                       skipped=str(c["quarantined"]))
    for r in rs.results:
        tc = ET.SubElement(suite, "testcase", name=r.name, time=f"{r.duration_s:.3f}")
        if r.status == "failed":
            ET.SubElement(tc, "failure", message=r.message[:500]).text = r.message
        elif r.status == "quarantined":
            # quarantine maps to JUnit skipped + a loud message (visible, not green)
            ET.SubElement(tc, "skipped", message="QUARANTINED (flaky): " + r.message[:400])
        if r.artifacts:
            ET.SubElement(tc, "system-out").text = "artifacts:\n" + "\n".join(r.artifacts)
    return ET.tostring(suite, encoding="unicode")


def to_json(rs: ResultSet) -> str:
    return json.dumps({"counts": rs.counts(), "gate_green": rs.gate_green,
                       "results": [asdict(r) for r in rs.results]}, indent=2)


def to_html(rs: ResultSet) -> str:
    c = rs.counts()
    rows = []
    for r in rs.results:
        badge = {"passed": "#1a7f37", "failed": "#cf222e",
                 "quarantined": "#9a6700"}.get(r.status, "#57606a")
        mut = ("not run" if r.mutation_verified is None
               else ("proven non-vacuous" if r.mutation_verified else "VACUOUS"))
        arts = "<br>".join(escape(a) for a in r.artifacts) or "-"
        rows.append(
            f"<tr><td>{escape(r.name)}</td>"
            f"<td style='color:{badge};font-weight:600'>{r.status.upper()}</td>"
            f"<td>{r.duration_s:.2f}s</td><td>{escape(mut)}</td>"
            f"<td>{escape(r.message)[:200]}</td><td><code>{arts}</code></td></tr>")
    return (
        "<!doctype html><meta charset=utf-8><title>ReaProof report</title>"
        "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #d0d7de;padding:6px 10px;text-align:left}</style>"
        f"<h1>ReaProof report</h1><p>passed {c['passed']} · failed {c['failed']} · "
        f"quarantined {c['quarantined']} · gate "
        f"{'GREEN' if rs.gate_green else 'RED'}</p>"
        "<table><tr><th>test</th><th>status</th><th>time</th><th>mutation</th>"
        "<th>message</th><th>artifacts</th></tr>" + "".join(rows) + "</table>")
