"""Gates: theme battery (Universality Roadmap U5).

Subjects are generated from the app's own bundled Default_7.0 theme:
- a RECOLORED variant (main colors patched garish) — the good subject whose
  switch must visibly repaint the main window;
- a CORRUPTED variant (one image truncated) — the lint mutation;
- re-loading the ALREADY-ACTIVE default theme — the paint-stage mutation
  (same pixels: the visible-change assertion must be the thing that fails).
"""
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.runner.themetest import (
    ThemeTestOptions, lint_theme_zip, run_theme_battery,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin",
                       reason="theme battery captures the macOS main window"),
    pytest.mark.skipif(
        not (paths.REAPER_APP / "Contents" / "InstallFiles" / "ColorThemes"
             / "Default_7.0.ReaperThemeZip").exists(),
        reason="pinned REAPER not provisioned (theme subjects derive from its bundle)"),
]

DEFAULT_ZIP = (paths.REAPER_APP / "Contents" / "InstallFiles" / "ColorThemes"
               / "Default_7.0.ReaperThemeZip")


def _quiet(*a, **k):
    pass


def _statuses(rs):
    return {r.name: r.status for r in rs.results}


def _make_recolored(dest: Path) -> Path:
    """Copy the default theme, patching main col_* values to a garish palette
    so the switch MUST repaint (identity would defeat the paint gate)."""
    src = zipfile.ZipFile(DEFAULT_ZIP)
    out = zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED)
    for info in src.infolist():
        data = src.read(info.filename)
        if info.filename.lower().endswith(".reapertheme"):
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"^(col_main_bg2?|col_main_text2?|col_tr1_bg|col_tr2_bg|"
                          r"col_arrangebg|col_tl_bg|col_transport_editbk)=.*$",
                          r"\1=255", text, flags=re.M)
            data = text.encode("utf-8")
        out.writestr(info, data)
    out.close()
    src.close()
    return dest


def _make_corrupted(dest: Path) -> Path:
    src = zipfile.ZipFile(DEFAULT_ZIP)
    out = zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED)
    broke = False
    for info in src.infolist():
        data = src.read(info.filename)
        if not broke and info.filename.lower().endswith(".png"):
            data = data[: len(data) // 2]          # truncated -> undecodable
            broke = True
        out.writestr(info, data)
    out.close()
    src.close()
    return dest


# ---- lint units (no REAPER) ----------------------------------------------------

def test_default_theme_lints_clean():
    errors, notes = lint_theme_zip(DEFAULT_ZIP)
    assert errors == []


def test_corrupted_image_turns_lint_red(tmp_path):
    bad = _make_corrupted(tmp_path / "RPCorrupt.ReaperThemeZip")
    errors, _ = lint_theme_zip(bad)
    assert any("undecodable" in e for e in errors), errors


def test_garbage_file_is_not_a_theme(tmp_path):
    junk = tmp_path / "junk.ReaperThemeZip"
    junk.write_bytes(b"this is not a zip")
    errors, _ = lint_theme_zip(junk)
    assert any("zip" in e for e in errors)


# ---- live gates -----------------------------------------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_recolored_theme_loads_and_visibly_paints(tmp_path):
    subject = _make_recolored(tmp_path / "RPVariant.ReaperThemeZip")
    rs = run_theme_battery(subject, out_dir=tmp_path / "report", log=_quiet)
    st = _statuses(rs)
    assert st["structure: zip layout + images decode"] == "passed"
    assert st["load: OpenColorThemeFile + readback"] == "passed"
    assert st["paint: theme visibly applied"] == "passed", [
        (r.name, r.message) for r in rs.results]
    assert rs.gate_green


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_reloading_active_theme_fails_the_paint_stage(tmp_path):
    """MUTATION: 'switching' to the already-active default theme repaints
    nothing — only the paint stage may fail, proving it is pixel-sensitive
    (and that the good gate's pass is not vacuous)."""
    subject = tmp_path / "Default_7.0.ReaperThemeZip"
    shutil.copy2(DEFAULT_ZIP, subject)
    rs = run_theme_battery(subject, out_dir=tmp_path / "report", log=_quiet)
    st = _statuses(rs)
    assert st["load: OpenColorThemeFile + readback"] == "passed"
    assert st["paint: theme visibly applied"] == "failed"
    assert not rs.gate_green
