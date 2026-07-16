"""Canonical filesystem locations for ReaProof.

Everything is resolved relative to the repo root (``REAPROOF_HOME`` overrides),
so the package works from an editable install and in CI alike. Large provisioned
binaries live under ``.cache/`` (gitignored); durable evidence is the run bundle.
"""
from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("REAPROOF_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # src/reaproof/paths.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()
CACHE = REPO_ROOT / ".cache"
DOWNLOADS = CACHE / "downloads"
TOOLS = CACHE / "tools"
# Warm plugin-scan caches seeded into every fresh profile so REAPER skips the
# multi-minute rescan of the host's plugin collection (DECISIONS D8/D15).
WARM_CACHE = CACHE / "warm_cache"

# REAPER under test. Defaults to a pinned, provisioned copy under .cache (best for
# cross-machine determinism), but ``REAPROOF_REAPER_APP`` points it at any REAPER
# install (e.g. /Applications/REAPER.app) so the tool works out of the box.
def _reaper_app() -> Path:
    env = os.environ.get("REAPROOF_REAPER_APP")
    if env:
        return Path(env).expanduser().resolve()
    return CACHE / "reaper775" / "REAPER.app"


REAPER_APP = _reaper_app()
REAPER_BIN = REAPER_APP / "Contents" / "MacOS" / "REAPER"
REAPER_VERSION = os.environ.get("REAPROOF_REAPER_VERSION", "7.75")


def _reaper_build_claim() -> str:
    env = os.environ.get("REAPROOF_REAPER_BUILD")
    if env:
        return env
    if os.environ.get("REAPROOF_REAPER_APP"):
        # user points at their own install without pinning a build: the claim
        # follows the MEASURED app (a stale pinned default would be a false
        # provenance claim about somebody else's REAPER)
        measured = reaper_app_build(REAPER_APP)
        if measured:
            return measured
    return "7.75.0_e2e941bu"


def reaper_app_build(app: Path | None = None) -> str | None:
    """The build string the resolved REAPER.app ACTUALLY carries (Info.plist
    CFBundleVersion, e.g. "7.75.0_e2e941bu"), or None if unreadable.

    ``REAPER_BUILD`` above is only a *claim* (an env default); when
    ``REAPROOF_REAPER_APP`` points at some other install, provenance/doctor
    compare the claim against this measured value so results are never
    silently attributed to the wrong REAPER.
    """
    import plistlib
    a = Path(app) if app else REAPER_APP
    try:
        with open(a / "Contents" / "Info.plist", "rb") as f:
            v = plistlib.load(f).get("CFBundleVersion")
        return str(v) if v else None
    except Exception:  # noqa: BLE001 — absent/foreign layout: unknown, not fatal
        return None

# In-REAPER bridge source (deployed into each isolated profile as Scripts/__startup.lua)
BRIDGE_LUA = REPO_ROOT / "bridge" / "reaproof_bridge.lua"

# Reference subjects + checked-in profile templates
EXAMPLES = REPO_ROOT / "examples"
PROFILES = REPO_ROOT / "reaper_profiles"
RPP_TEMPLATES = REPO_ROOT / "rpp_templates"
GOLDENS = REPO_ROOT / "goldens"

# Per-run working area (isolated profiles, IPC queues, render scratch) — gitignored
RUNS = CACHE / "runs"

# Compiled reference subjects (CLAP, etc.) + the CLAP SDK headers — gitignored
SUBJECTS = CACHE / "subjects"
CLAP_SDK_INCLUDE = CACHE / "clap-sdk" / "include"
CLAP_GOOD = SUBJECTS / "clap" / "reaproof_gain.clap"
CLAP_BROKEN = SUBJECTS / "clap" / "reaproof_gain_broken.clap"
LV2_GOOD = SUBJECTS / "lv2" / "reaproof_gain.lv2"
LV2_BROKEN = SUBJECTS / "lv2" / "reaproof_gain_broken.lv2"
# Reference native extensions (examples/ext) — the extensions= install subject
# and the deliberately-faulty variant for the U1 fault-forensics gate
EXT_TEST = SUBJECTS / "ext" / "reaper_reaproof_testext.dylib"
EXT_FAULT = SUBJECTS / "ext" / "reaper_reaproof_crashext.dylib"
EXT_NOOP = SUBJECTS / "ext" / "reaper_reaproof_noopext.dylib"

# Validators (macOS host paths; other OSes resolved per-platform in provision/)
PLUGINVAL = TOOLS / "pluginval" / "pluginval.app" / "Contents" / "MacOS" / "pluginval"
CLAP_VALIDATOR = TOOLS / "clap-validator-bin" / "binaries" / "clap-validator"

# Source for REAPER-side extensions (copied into each isolated profile's UserPlugins).
# Reused from the user's install per DECISIONS D10.
USER_REAPER_RES = Path.home() / "Library" / "Application Support" / "REAPER"
USER_USERPLUGINS = USER_REAPER_RES / "UserPlugins"
USER_LICENSE = USER_REAPER_RES / "reaper-license.rk"

# Extensions required in every isolated profile (arm64 macOS names).
REQUIRED_EXTENSIONS = (
    "reaper_js_ReaScriptAPI64ARM.dylib",
    "reaper_sws-arm64.dylib",
    "reaper_imgui-arm64.dylib",
)


REAPER_BUILD = _reaper_build_claim()


def ensure_runs_dir() -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    return RUNS
