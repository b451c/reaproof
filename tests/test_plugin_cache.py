"""Gates: warm plugin-scan cache upkeep (Quality Audit v2 sweep).

Three defects fixed, each provable red on the old behaviour:
- entries were appended at EOF, but the REAL cache carries an [xvst3_compat]
  section after [vstcache] — appended entries landed in the wrong section,
  REAPER ignored them, and the very scan/hang this cache prevents came back;
- an existing entry whose plugin was UPDATED (mtime changed) was skipped as
  "already cached", leaving a stale hash that no longer suppresses the scan;
- an unstat-able plugin was silently dropped (no warning), leaving exactly one
  plugin unprotected with no signal.
"""
import time
from pathlib import Path

import pytest

from reaproof.provision.plugin_cache import (
    complete_cache, existing_entries, filetime_le_hex,
)

RICH = "8003CB984A5BDA01,330483846{ABCDEF019182FAEB,Some Plugin (Vendor)"


def _cache(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "reaper-vstplugins_arm64.ini"
    p.write_text(body, encoding="utf-8")
    return p


def _plugin(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "scan"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_bytes(b"x")
    return f


def test_new_entry_lands_inside_vstcache_section(tmp_path):
    plug = _plugin(tmp_path, "New_Thing.vst3")
    cache = _cache(tmp_path, "[vstcache]\nOld.vst3=" + RICH +
                   "\n[xvst3_compat]\nAABB=CCDD - Old.vst3\n")
    n = complete_cache(cache, scan_dirs=[([plug.parent], "*.vst3")])
    assert n == 1
    lines = cache.read_text().splitlines()
    entry_i = next(i for i, l in enumerate(lines) if l.startswith("New_Thing.vst3="))
    compat_i = lines.index("[xvst3_compat]")
    assert entry_i < compat_i, "entry appended into the WRONG section (old bug)"
    assert lines[entry_i] == f"New_Thing.vst3={filetime_le_hex(plug.stat().st_mtime)}"


def test_stale_entry_is_refreshed_not_skipped(tmp_path):
    plug = _plugin(tmp_path, "Updated.vst3")
    stale = filetime_le_hex(plug.stat().st_mtime - 12345)  # pre-update hash
    cache = _cache(tmp_path, f"[vstcache]\nUpdated.vst3={stale},99{{id,Updated (V)\n")
    n = complete_cache(cache, scan_dirs=[([plug.parent], "*.vst3")])
    assert n == 1, "stale entry was skipped as already-cached (old bug)"
    fresh = filetime_le_hex(plug.stat().st_mtime)
    assert existing_entries(cache)["Updated.vst3"] == fresh
    assert stale not in cache.read_text()


def test_fresh_entry_is_left_alone(tmp_path):
    """NEGATIVE CONTROL: an up-to-date entry (rich form) is not rewritten —
    proves the refresh really keys off the mtime comparison."""
    plug = _plugin(tmp_path, "Fresh.vst3")
    ft = filetime_le_hex(plug.stat().st_mtime)
    body = f"[vstcache]\nFresh.vst3={ft},330{{id,Fresh (V)\n"
    cache = _cache(tmp_path, body)
    n = complete_cache(cache, scan_dirs=[([plug.parent], "*.vst3")])
    assert n == 0
    assert cache.read_text() == body  # byte-identical: rich metadata preserved


def test_unstatable_plugin_warns_and_others_still_protected(tmp_path):
    good = _plugin(tmp_path, "Good.vst3")
    dangling = good.parent / "Broken.vst3"
    dangling.symlink_to(tmp_path / "no_such_target")
    cache = _cache(tmp_path, "[vstcache]\n")
    with pytest.warns(UserWarning, match="Broken.vst3"):
        n = complete_cache(cache, scan_dirs=[([good.parent], "*.vst3")])
    assert n == 1
    assert "Good.vst3" in existing_entries(cache)
    assert "Broken.vst3" not in existing_entries(cache)


def test_filetime_hex_shape():
    ft = filetime_le_hex(time.time())
    assert len(ft) == 16 and all(c in "0123456789ABCDEF" for c in ft)
