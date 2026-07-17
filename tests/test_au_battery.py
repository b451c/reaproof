"""Gates: Audio Unit battery (semi-hermetic by nature — AU discovery is
system-wide). Apple's CoreAudio.component ships with every macOS, which makes
it the universal green reference; the broken reference is FABRICATED in tmp
(a component bundle whose codes exist nowhere) — auval must refuse it.
"""
import plistlib
import sys
from pathlib import Path

import pytest

from reaproof.runner.aubattery import AuTestOptions, read_components, run_au_battery

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="Audio Units are macOS")

COREAUDIO = Path("/System/Library/Components/CoreAudio.component")


def _statuses(rs):
    return {r.name: r.status for r in rs.results}


def _quiet(*a, **k):
    pass


def test_manifest_reader_on_the_system_bundle():
    entries = read_components(COREAUDIO)
    assert len(entries) > 5                     # Apple ships dozens
    assert all({"name", "type", "subtype", "manufacturer"} <= set(e)
               for e in entries)
    assert any(e["type"] == "aufx" for e in entries)


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_system_bundle_validates_and_loads(tmp_path):
    """Green path on the universal subject: manifest parses, auval passes for
    the validated subset, and a track-insertable Apple AU instantiates in the
    hermetic profile. (Render determinism for AU was proven live on a
    third-party effect; the repo gate stays structural because the FIRST
    aufx in Apple's bundle varies across macOS versions.)"""
    rs = run_au_battery(COREAUDIO, out_dir=tmp_path,
                        opts=AuTestOptions(max_components=2, signals=False),
                        log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    assert st["static: AudioComponents manifest parses"] == "passed"
    auvals = [r for r in rs.results if r.name.startswith("validator: auval")]
    assert auvals and all(r.status == "passed" for r in auvals)
    assert st["load: instantiates in REAPER"] == "passed"


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_unregistrable_component_is_red(tmp_path):
    """A bundle whose AudioComponents codes register nowhere must FAIL auval —
    and the install stage must clean up its semi-hermetic copy."""
    fake = tmp_path / "RPFake.component"
    (fake / "Contents").mkdir(parents=True)
    with open(fake / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"AudioComponents": [{
            "name": "ReaProof: Fabricated Nothing",
            "type": "aufx", "subtype": "zzq9", "manufacturer": "zzq9",
        }]}, f)
    rs = run_au_battery(fake, out_dir=tmp_path / "out",
                        opts=AuTestOptions(signals=False), log=_quiet)
    assert not rs.gate_green
    auval = next(r for r in rs.results if r.name.startswith("validator: auval"))
    assert auval.status == "failed"
    copy = (Path.home() / "Library" / "Audio" / "Plug-Ins" / "Components"
            / "RPFake_RP.component")
    assert not copy.exists()        # the finally cleanup ran
