"""Universal theme battery — ``reaproof test <theme.ReaperThemeZip>`` (U5).

No WALTER/theme linter exists publicly; this battery is the first automated
check for REAPER themes. Stages:

1. **structure lint** (no REAPER): the zip carries exactly one
   ``*.ReaperTheme`` ini; every image member decodes; ``rtconfig.txt`` noted
   (color-only themes are legal — its absence is a note, not a failure).
2. **load + readback** (REAPER): the theme switches LIVE via
   ``OpenColorThemeFile`` (no restart — verified) and the effect is read back
   through a different path (``GetLastColorThemeFile``).
3. **visible change**: real pixels of the main window BEFORE vs AFTER the
   switch must differ — proof the theme actually painted, not just loaded.
   (Re-loading the already-active theme is the mutation: same pixels.)
4. **layout census** (provenance): ``ThemeLayout_GetLayout("seclist")``
   enumeration travels with the report.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from reaproof.report.results import ResultSet, TestResult
from reaproof.runner.autotest import _emit
from reaproof.runner.session import ReaperSession


@dataclass
class ThemeTestOptions:
    change_threshold: float = 0.01   # fraction of main-window pixels that must move


def _add(rs: ResultSet, log, name: str, status: str, **kw) -> None:
    rs.results.append(TestResult(name=name, status=status, **kw))
    log(f"  [{status.upper():9}] {name}"
        + (f" — {kw.get('message')}" if kw.get("message") else ""))


# ---- stage 1: structure lint --------------------------------------------------

def lint_theme_zip(theme: Path) -> tuple[list[str], list[str]]:
    """(errors, notes) for a .ReaperThemeZip. Empty errors == structurally ok."""
    from PIL import Image
    errors: list[str] = []
    notes: list[str] = []
    try:
        zf = zipfile.ZipFile(theme)
    except (zipfile.BadZipFile, OSError) as e:
        return [f"not a readable zip: {e}"], notes
    names = zf.namelist()
    inis = [n for n in names if n.lower().endswith(".reapertheme")]
    if len(inis) != 1:
        errors.append(f"expected exactly one .ReaperTheme member, found {len(inis)}")
    else:
        text = zf.read(inis[0]).decode("utf-8", errors="replace")
        if "[color theme]" not in text:
            errors.append(f"{inis[0]}: missing [color theme] section")
    if not any(n.lower().endswith("rtconfig.txt") for n in names):
        notes.append("no rtconfig.txt (color-only theme: uses the default layout)")
    bad_images = []
    for n in names:
        if n.lower().endswith((".png", ".jpg", ".jpeg")):
            try:
                Image.open(io.BytesIO(zf.read(n))).verify()
            except Exception:  # noqa: BLE001 — any undecodable image is the finding
                bad_images.append(n)
    if bad_images:
        errors.append(f"{len(bad_images)} undecodable image(s), e.g. {bad_images[:3]}")
    return errors, notes


# ---- stages 2-4 ---------------------------------------------------------------

_LAYOUTS_LUA = """
return (function()
  local out = {}
  local i = 0
  while true do
    local ok, name = reaper.ThemeLayout_GetLayout("seclist", i)
    if not ok then break end
    out[#out + 1] = name
    i = i + 1
    if i > 64 then break end
  end
  return out
end)()
"""


def _capture_main(session, out_path: Path) -> np.ndarray:
    from reaproof.observe.visual.capture import capture_window_macos
    return capture_window_macos(session.handle.pid, "REAPER v",
                                out_path, settle=0.6).image


def run_theme_battery(theme: Path, out_dir: Path | None = None,
                      opts: ThemeTestOptions | None = None, *,
                      log=print) -> ResultSet:
    theme = Path(theme)
    opts = opts or ThemeTestOptions()
    rs = ResultSet()
    art = Path(out_dir) if out_dir else None
    if art:
        art.mkdir(parents=True, exist_ok=True)

    log("structure lint…")
    if theme.suffix.lower() == ".reaperthemezip":
        errors, notes = lint_theme_zip(theme)
        if errors:
            _add(rs, log, "structure: zip layout + images decode", "failed",
                 message="; ".join(errors)[:280])
            _emit(rs, out_dir, theme)
            return rs
        _add(rs, log, "structure: zip layout + images decode", "passed",
             message="; ".join(notes)[:200])
    else:
        _add(rs, log, "structure: zip layout + images decode", "skipped",
             message="bare .ReaperTheme (no zip structure to lint)")

    log("load + visible change (live REAPER)…")
    with ReaperSession(f"themetest-{theme.stem}") as s:
        dest = s.profile.resource_dir / "ColorThemes" / theme.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(theme.read_bytes())

        before = _capture_main(s, (art / "before.png") if art
                               else s.profile.root / "before.png")
        res = s.eval(f"""
        local ok = reaper.OpenColorThemeFile([[{dest}]])
        reaper.UpdateArrange(); reaper.UpdateTimeline()
        reaper.ThemeLayout_RefreshAll()
        return {{ok = ok, active = reaper.GetLastColorThemeFile()}}""")
        if not res or not res.get("ok"):
            _add(rs, log, "load: OpenColorThemeFile", "failed",
                 message="returned false (rejected theme)")
            _emit(rs, out_dir, theme)
            return rs
        if Path(res.get("active", "")).name != theme.name:
            _add(rs, log, "load: OpenColorThemeFile", "failed",
                 message=f"readback names {res.get('active')!r}, not the subject")
            _emit(rs, out_dir, theme)
            return rs
        _add(rs, log, "load: OpenColorThemeFile + readback", "passed")

        after = _capture_main(s, (art / "after.png") if art
                              else s.profile.root / "after.png")
        if before.shape != after.shape:
            changed = 1.0
        else:
            changed = float((np.abs(before.astype(np.int16)
                                    - after.astype(np.int16)).max(axis=2) > 8).mean())
        if changed >= opts.change_threshold:
            _add(rs, log, "paint: theme visibly applied", "passed",
                 message=f"{changed:.1%} of main-window pixels changed")
        else:
            _add(rs, log, "paint: theme visibly applied", "failed",
                 message=f"only {changed:.1%} of pixels changed — did it paint?")

        layouts = s.eval(_LAYOUTS_LUA) or []
        _add(rs, log, "layouts: seclist census", "passed",
             message=", ".join(layouts)[:200],
             provenance={"layout_sections": layouts})

    _emit(rs, out_dir, theme)
    return rs
