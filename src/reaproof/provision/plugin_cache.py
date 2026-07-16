"""Complete REAPER's VST scan cache so a fresh isolated profile never re-scans.

The obstacle on a developer host (DECISIONS D16): REAPER force-scans the OS
standard VST3 dir, and license-protected plugins (iZotope RX, Waves WaveShell,
SoundID, ...) hang REAPER's headless scanner indefinitely. They can't be chmod'd
(root-owned) and shouldn't be moved (the user's system).

The fix exploits REAPER's cache key format, verified empirically: a cache entry
``Name.vst3=<HASH>`` is skipped on startup iff ``<HASH>`` equals the plugin
bundle's mtime encoded as a little-endian Windows FILETIME. So for every plugin
present in the scan dirs but absent from the seed cache, we synthesise a valid
skip-entry. REAPER then matches mtime, considers it already-known, and never
loads it — no hang, nothing touched on disk. The subject-under-test lives in a
*separate* controlled dir and is still scanned normally.
"""
from __future__ import annotations

import struct
from pathlib import Path

# Standard macOS scan locations REAPER force-includes for VST/VST3.
VST3_DIRS = [Path("/Library/Audio/Plug-Ins/VST3"), Path.home() / "Library/Audio/Plug-Ins/VST3"]
VST_DIRS = [Path("/Library/Audio/Plug-Ins/VST"), Path.home() / "Library/Audio/Plug-Ins/VST"]

_EPOCH_DELTA = 11_644_473_600  # seconds between 1601-01-01 and 1970-01-01


def filetime_le_hex(mtime: float) -> str:
    """A POSIX mtime as REAPER stores it: 100ns-since-1601, little-endian, hex."""
    ft = int((mtime + _EPOCH_DELTA) * 10_000_000)
    return struct.pack("<Q", ft).hex().upper()


def _cache_key(plugin_path: Path) -> str:
    # REAPER keys are the bundle filename with spaces replaced by underscores.
    return plugin_path.name.replace(" ", "_")


def existing_entries(cache_path: Path) -> dict[str, str]:
    """key -> stored FILETIME hash (the value's FIRST comma-separated field).

    Real REAPER entries are rich — ``key=FILETIME,size{id,display name`` — and
    only the leading FILETIME participates in the skip decision; synthesized
    skip-entries are the bare ``key=FILETIME`` form (verified working, D15).
    """
    entries: dict[str, str] = {}
    if not cache_path.exists():
        return entries
    for line in cache_path.read_text(errors="replace").splitlines():
        if "=" in line and not line.startswith("["):
            k, _, v = line.partition("=")
            entries[k.strip()] = v.strip().split(",", 1)[0]
    return entries


def existing_keys(cache_path: Path) -> set[str]:
    return set(existing_entries(cache_path))


def complete_cache(cache_path: Path,
                   scan_dirs: list[tuple[list[Path], str]] | None = None) -> int:
    """Add/refresh skip-entries for every present VST/VST3 plugin.

    Returns the number of entries added or refreshed. A skip-entry only works
    while its hash equals the bundle's CURRENT mtime — so an entry whose plugin
    was since updated is REFRESHED, not skipped (a stale hash would silently
    re-trigger the very scan/hang this cache exists to prevent). Entries are
    written inside the ``[vstcache]`` section (never blindly appended at EOF,
    where a later section would orphan them). An unstat-able plugin is
    reported loudly (it stays scannable and can hang REAPER); the rest are
    still protected. ``scan_dirs`` overrides the system dirs for testing.
    """
    if not cache_path.exists():
        return 0
    have = existing_entries(cache_path)
    upserts: dict[str, str] = {}
    for dirs, ext in scan_dirs or ((VST3_DIRS, "*.vst3"), (VST_DIRS, "*.vst")):
        for d in dirs:
            if not d.is_dir():
                continue
            for plugin in sorted(d.glob(ext)):
                key = _cache_key(plugin)
                try:
                    ft = filetime_le_hex(plugin.stat().st_mtime)
                except OSError as e:
                    import warnings
                    warnings.warn(
                        f"cannot stat {plugin} ({e}) — no skip-entry written; "
                        f"REAPER WILL scan this plugin (possible hang if it is "
                        f"license-protected)", stacklevel=2)
                    continue
                if have.get(key) != ft:
                    upserts[key] = ft
    if not upserts:
        return 0
    _write_entries(cache_path, upserts)
    return len(upserts)


def _write_entries(cache_path: Path, upserts: dict[str, str]) -> None:
    """Update/insert ``key=hash`` lines inside the [vstcache] section."""
    lines = cache_path.read_text(errors="replace").splitlines()
    out: list[str] = []
    pending = dict(upserts)
    in_cache_sec = False
    section_seen = False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_cache_sec and pending:      # leaving [vstcache]: flush inserts
                out.extend(f"{k}={v}" for k, v in sorted(pending.items()))
                pending.clear()
            in_cache_sec = (s == "[vstcache]")
            section_seen = section_seen or in_cache_sec
            out.append(line)
            continue
        if "=" in line and not s.startswith("["):
            k = line.split("=", 1)[0].strip()
            if k in pending:                  # refresh a stale entry in place
                out.append(f"{k}={pending.pop(k)}")
                continue
        out.append(line)
    if pending:                               # file ended inside a section
        if not section_seen and not in_cache_sec:
            out.append("[vstcache]")
        out.extend(f"{k}={v}" for k, v in sorted(pending.items()))
    cache_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def complete_all_caches(warm_cache_dir: Path) -> dict[str, int]:
    """Complete every VST cache file in the warm-cache dir (arm64 + x86_64)."""
    result = {}
    for name in ("reaper-vstplugins_arm64.ini", "reaper-vstplugins64.ini"):
        p = warm_cache_dir / name
        if p.exists():
            result[name] = complete_cache(p)
    return result
