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


def existing_keys(cache_path: Path) -> set[str]:
    keys: set[str] = set()
    if not cache_path.exists():
        return keys
    for line in cache_path.read_text(errors="replace").splitlines():
        if "=" in line and not line.startswith("["):
            keys.add(line.split("=", 1)[0].strip())
    return keys


def complete_cache(cache_path: Path) -> int:
    """Append skip-entries for every present-but-uncached VST/VST3 plugin.

    Returns the number of entries added. Idempotent: re-running adds only newly
    appeared plugins. The cache file is a single ``[vstcache]`` section, so new
    entries are simply appended.
    """
    if not cache_path.exists():
        return 0
    have = existing_keys(cache_path)
    additions: list[str] = []
    for dirs, ext in ((VST3_DIRS, "*.vst3"), (VST_DIRS, "*.vst")):
        for d in dirs:
            if not d.is_dir():
                continue
            for plugin in sorted(d.glob(ext)):
                key = _cache_key(plugin)
                if key in have:
                    continue
                try:
                    ft = filetime_le_hex(plugin.stat().st_mtime)
                except OSError:
                    continue
                additions.append(f"{key}={ft}")
                have.add(key)
    if additions:
        with cache_path.open("a", encoding="utf-8") as f:
            if not cache_path.read_text(errors="replace").endswith("\n"):
                f.write("\n")
            f.write("\n".join(additions) + "\n")
    return len(additions)


def complete_all_caches(warm_cache_dir: Path) -> dict[str, int]:
    """Complete every VST cache file in the warm-cache dir (arm64 + x86_64)."""
    result = {}
    for name in ("reaper-vstplugins_arm64.ini", "reaper-vstplugins64.ini"):
        p = warm_cache_dir / name
        if p.exists():
            result[name] = complete_cache(p)
    return result
