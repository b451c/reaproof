"""Gates: feature-manifest engine of the Agent Authoring Harness (U7).

The anti-fabrication rule is the load-bearing assertion: an agent-written
manifest cannot claim coverage by prose. Each rule has its red case (the
mutation) and its green case (the control).
"""
import json
from pathlib import Path

from reaproof.coverage.features import (
    load_manifest, render_report, scaffold, validate,
)


def _manifest(**over):
    m = {
        "subject": {"path": "x.lua", "type": "script", "source": "."},
        "mode": "auto",
        "features": [{
            "id": "gain_db", "title": "Gain", "kind": "param",
            "evidence": "gain.c:62 k=powf(10,db/20)",
            "oracles": ["audio"], "status": "spec-needed",
            "tests": [], "intent": "declared",
        }],
    }
    m.update(over)
    return m


def _feature(**over):
    f = dict(_manifest()["features"][0])
    f.update(over)
    return f


def test_valid_manifest_passes():
    rep = validate(_manifest())
    assert rep.ok, rep.errors
    assert rep.counts == {"spec-needed": 1}


def test_covered_without_tests_is_refused():
    """THE anti-fabrication rule (mutation for every 'covered' claim)."""
    rep = validate(_manifest(features=[_feature(status="covered", tests=[])]))
    assert not rep.ok
    assert any("covered" in e and "test" in e for e in rep.errors)


def test_covered_with_missing_test_file_is_refused(tmp_path):
    rep = validate(
        _manifest(features=[_feature(status="covered",
                                     tests=["no_such_test.py::test_x"])]),
        tests_root=tmp_path)
    assert not rep.ok
    assert any("missing" in e for e in rep.errors)


def test_covered_with_real_test_and_covers_passes(tmp_path):
    tf = tmp_path / "test_gain.py"
    tf.write_text("COVERS = ['gain_db']\n\ndef test_minus12(): pass\n")
    rep = validate(
        _manifest(features=[_feature(status="covered",
                                     tests=["test_gain.py::test_minus12"])]),
        tests_root=tmp_path)
    assert rep.ok, rep.errors


def test_covers_disagreement_is_refused(tmp_path):
    """MUTATION: the test file claims to cover a DIFFERENT feature."""
    tf = tmp_path / "test_gain.py"
    tf.write_text("COVERS = ['some_other_feature']\n\ndef test_x(): pass\n")
    rep = validate(
        _manifest(features=[_feature(status="covered",
                                     tests=["test_gain.py::test_x"])]),
        tests_root=tmp_path)
    assert not rep.ok
    assert any("COVERS" in e for e in rep.errors)


def test_untestable_requires_reason_and_is_surfaced():
    rep = validate(_manifest(features=[_feature(status="untestable")]))
    assert not rep.ok
    rep2 = validate(_manifest(features=[
        _feature(status="untestable", reason="modal-only entry point (D26)")]))
    assert rep2.ok
    assert rep2.untestable and "D26" in rep2.untestable[0]


def test_assumed_intent_requires_and_surfaces_assumption():
    rep = validate(_manifest(features=[_feature(intent="assumed")]))
    assert not rep.ok
    rep2 = validate(_manifest(features=[
        _feature(intent="assumed",
                 assumption="code IS the intent: clamp at ±24 dB")]))
    assert rep2.ok
    assert rep2.assumptions and "clamp" in rep2.assumptions[0]
    assert "ASSUMPTIONS" in render_report(rep2)


def test_evidence_is_mandatory():
    rep = validate(_manifest(features=[_feature(evidence="")]))
    assert not rep.ok
    assert any("evidence" in e for e in rep.errors)


def test_duplicate_ids_and_bad_mode_are_refused():
    m = _manifest(mode="yolo",
                  features=[_feature(), _feature()])
    rep = validate(m)
    assert any("duplicate" in e for e in rep.errors)
    assert any("mode" in e for e in rep.errors)


def test_scaffold_roundtrips_through_json(tmp_path):
    m = scaffold("subj.lua", subject_type="script", mode="auto")
    p = tmp_path / "reaproof_features.json"
    p.write_text(json.dumps(m))
    loaded = load_manifest(p)
    assert loaded["mode"] == "auto"
    # the scaffold's TODO entry is intentionally NOT valid-covered — an agent
    # must fill it in; but it must be structurally parseable
    rep = validate(loaded)
    assert rep.counts.get("spec-needed") == 1
