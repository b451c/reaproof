"""Gate: first-class native-extension install (`extensions=` on ReaperSession).

A native REAPER extension dylib must land in ``resource_dir/UserPlugins`` (not
the plugin scan dir) and have its Gatekeeper quarantine cleared, or REAPER
silently refuses to load it. Subject: ``examples/ext/reaproof_testext.c`` — a
minimal extension registering the named action ``_REAPROOF_TEST_EXT_PING``
whose invocation writes ExtState ``reaproof_testext/ping=1``.

- units (REAPER-free): install places the dylib in UserPlugins; a planted
  ``com.apple.quarantine`` xattr is cleared; a missing source raises (loudly,
  never a silent no-op).
- gate (REAPER): with ``extensions=[EXT_TEST]`` the action resolves
  (NamedCommandLookup > 0) AND running it produces the ExtState effect read
  back through a different path (§1.1).
- negative control (REAPER): the SAME assertions on a profile WITHOUT the
  extension — lookup is 0, effect absent. This is the mutation that proves the
  gate can turn RED.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.determinism import DeterminismLock
from reaproof.provision.base import get_provisioner
from reaproof.runner.session import ReaperSession

ACTION = "REAPROOF_TEST_EXT_PING"

# The whole install mechanism under test is macOS-specific (dylib subjects
# built with clang -arch arm64, Gatekeeper quarantine, UserPlugins layout).
pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="extension install semantics are macOS")


def _ensure_ext_subject() -> Path:
    """Build the reference extension if the dylib is not present."""
    if not paths.EXT_TEST.exists():
        script = paths.EXAMPLES / "ext" / "build_ext.sh"
        subprocess.run(["bash", str(script), str(paths.EXT_TEST.parent)],
                       check=True, capture_output=True)
    return paths.EXT_TEST


# ---- units (REAPER-free) ---------------------------------------------------

def test_install_places_dylib_in_userplugins():
    ext = _ensure_ext_subject()
    prov = get_provisioner()
    profile = prov.assemble_profile(
        "ext-unit-place", DeterminismLock(), extensions=[ext]
    )
    installed = profile.resource_dir / "UserPlugins" / ext.name
    assert installed.is_file()
    assert installed.read_bytes() == ext.read_bytes()


@pytest.mark.skipif(not Path("/usr/bin/xattr").exists(), reason="xattr is macOS")
def test_install_clears_quarantine_xattr(tmp_path):
    # Plant the quarantine mark a downloaded/AirDropped dylib would carry; the
    # installed copy must NOT carry it (Gatekeeper would silently refuse it).
    ext = _ensure_ext_subject()
    src = tmp_path / ext.name
    src.write_bytes(ext.read_bytes())
    subprocess.run(
        ["xattr", "-w", "com.apple.quarantine", "0083;00000000;test;", str(src)],
        check=True,
    )
    prov = get_provisioner()
    profile = prov.assemble_profile(
        "ext-unit-quarantine", DeterminismLock(), extensions=[src]
    )
    installed = profile.resource_dir / "UserPlugins" / ext.name
    out = subprocess.run(["xattr", str(installed)], capture_output=True, text=True)
    assert "com.apple.quarantine" not in out.stdout


def test_install_missing_extension_raises(tmp_path):
    prov = get_provisioner()
    with pytest.raises(FileNotFoundError):
        prov.assemble_profile(
            "ext-unit-missing", DeterminismLock(),
            extensions=[tmp_path / "no_such.dylib"],
        )


# ---- REAPER gate + negative control ----------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_extension_loads_and_action_has_effect():
    ext = _ensure_ext_subject()
    with ReaperSession("ext-install", extensions=[ext]) as s:
        cid = s.eval(f"return reaper.NamedCommandLookup('_{ACTION}')")
        assert cid and cid > 0, "extension action did not register"
        # baseline: the effect channel is empty before the action runs
        assert s.eval(
            "return reaper.GetExtState('reaproof_testext','ping')") == ""
        s.eval(f"reaper.Main_OnCommand({cid}, 0); return true")
        s.wait_until(
            "reaper.GetExtState('reaproof_testext','ping') == '1'",
            timeout=10, message="extension action effect (ExtState) visible",
        )


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_profile_without_extension_resolves_zero():
    # The mutation for the gate above: same profile shape, no extension. If
    # this ever resolves >0, isolation is broken (leak from the user config).
    with ReaperSession("ext-absent") as s:
        cid = s.eval(f"return reaper.NamedCommandLookup('_{ACTION}')")
        assert cid == 0, f"action resolved ({cid}) in a profile with no extension"
        assert s.eval(
            "return reaper.GetExtState('reaproof_testext','ping')") == ""
