"""Provisioner abstraction shared by all platforms.

Assembles a hermetic, isolated REAPER resource directory (never touching the
user's real config) from: a generated determinism-locked ``reaper.ini``, the
required REAPER-side extensions, the user's license (to skip the nag), and the
ReaProof bridge deployed as ``Scripts/__startup.lua``. Platform subclasses
implement only launch/liveness/teardown.
"""
from __future__ import annotations

import platform as _platform
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reaproof import paths
from reaproof.determinism import DeterminismLock


@dataclass
class IsolatedProfile:
    """A fully assembled, hermetic REAPER profile for one run."""

    run_id: str
    root: Path           # RUNS/<run_id>
    resource_dir: Path   # the -cfgfile resource dir
    ini_path: Path       # resource_dir/reaper.ini
    run_dir: Path        # resource_dir/_reaproof  (IPC + heartbeat live here)
    plugin_dir: Path     # controlled plugin scan root (VST/VST3/CLAP subdirs)
    artifacts_dir: Path  # where evidence for this run is collected

    def manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "resource_dir": str(self.resource_dir),
            "ini_path": str(self.ini_path),
            "plugin_dir": str(self.plugin_dir),
        }


@dataclass
class LaunchHandle:
    """A running REAPER instance under supervision."""

    pid: int
    profile: IsolatedProfile
    extra: dict[str, Any] = field(default_factory=dict)


class Provisioner(ABC):
    """Common assembly; platform-specific launch/liveness/teardown."""

    #: extension filenames required in every isolated profile (platform-specific)
    required_extensions: tuple[str, ...] = ()
    #: ``reaper.ini`` key carrying the controlled VST scan path (arch-specific)
    vst_path_key: str = "vstpath"

    def assemble_profile(
        self,
        run_id: str,
        lock: DeterminismLock,
        *,
        plugins: list[Path] | None = None,
        jsfx: list[Path] | None = None,
        extensions: list[Path] | None = None,
    ) -> IsolatedProfile:
        root = paths.ensure_runs_dir() / run_id
        resource_dir = root / "resource"
        run_dir = resource_dir / "_reaproof"
        plugin_dir = root / "plugins"
        artifacts_dir = root / "artifacts"
        # clean slate
        if root.exists():
            shutil.rmtree(root)
        for d in (
            resource_dir / "UserPlugins",
            resource_dir / "Scripts",
            run_dir / "cmd" / "in",
            run_dir / "cmd" / "out",
            plugin_dir / "VST",
            plugin_dir / "VST3",
            plugin_dir / "CLAP",
            plugin_dir / "LV2",
            artifacts_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        profile = IsolatedProfile(
            run_id=run_id,
            root=root,
            resource_dir=resource_dir,
            ini_path=resource_dir / "reaper.ini",
            run_dir=run_dir,
            plugin_dir=plugin_dir,
            artifacts_dir=artifacts_dir,
        )

        self._write_ini(profile, lock)
        self._seed_plugin_caches(profile)
        self._install_extensions(profile)
        self._install_license(profile)
        self._deploy_bridge(profile)
        if plugins:
            self.install_plugins(profile, plugins)
        if jsfx:
            self.install_jsfx(profile, jsfx)
        if extensions:
            self.install_extensions(profile, extensions)
        return profile

    def install_jsfx(self, profile: IsolatedProfile, files: list[Path]) -> None:
        """Install JSFX into the profile's Effects/ tree (added as 'JS: <desc>')."""
        dest = profile.resource_dir / "Effects" / "ReaProof"
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / Path(f).name)

    def install_extensions(self, profile: IsolatedProfile, files: list[Path]) -> None:
        """Install native REAPER extensions-under-test into UserPlugins.

        Unlike ``plugins=`` (which goes to the controlled *scan dir* for
        VST/CLAP subjects), a native extension must live in
        ``resource_dir/UserPlugins`` to be loaded at startup. Each installed
        copy also gets its Gatekeeper quarantine cleared (macOS) — a
        locally-built unsigned dylib is otherwise refused with no visible
        error, i.e. a silent failure.
        """
        dest = profile.resource_dir / "UserPlugins"
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            f = Path(f)
            if not f.exists():
                raise FileNotFoundError(f"extension not found: {f}")
            target = dest / f.name
            if f.is_dir():
                shutil.copytree(f, target, dirs_exist_ok=True)
            else:
                shutil.copy2(f, target)
            self._clear_quarantine(target)

    def _clear_quarantine(self, path: Path) -> None:
        """Platform hook: remove OS quarantine marks from an installed artifact."""
        # default: nothing to do (Gatekeeper is macOS-only)

    # ---- assembly steps (overridable) -------------------------------------
    def _write_ini(self, profile: IsolatedProfile, lock: DeterminismLock) -> None:
        """Generate the determinism-locked reaper.ini (§5.1).

        Pinned/frozen here: controlled plugin scan path, UI scale (from the lock's
        DPI), audio config (SR/block + no blocking modal), no splash/auto-update/
        auto-save/last-project. Theme + fonts are pinned *by the pinned REAPER 7.75*
        (a fresh isolated profile uses its built-in default theme — no user theme is
        ever introduced — so captures/goldens reproduce on any machine running the
        same pinned build). macOS has no software-render toggle (Cocoa renders
        consistently); the CI Linux leg forces software GL under Xvfb instead.
        """
        # Controlled VST/VST3 scan path. (CLAP is NOT discovered via vstpath —
        # verified — so a subject CLAP at plugin_dir/CLAP is exposed to REAPER via
        # the CLAP_PATH env var the launcher sets instead.)
        vst = f"{profile.plugin_dir/'VST'};{profile.plugin_dir/'VST3'}"
        ui = lock.dpi / 100.0
        lines = [
            "[reaper]",
            f"{self.vst_path_key}={vst}",
            "vstpath=" + vst,                 # generic fallback key
            # LV2 scan path (REAPER supports LV2 natively since 6.24; the
            # macOS key is lv2path_mac — read out of the pinned binary).
            # The generic key rides along for the Linux/Windows backends.
            f"lv2path_mac={profile.plugin_dir/'LV2'}",
            f"lv2path={profile.plugin_dir/'LV2'}",
            "defsplash=0",                    # no splash window
            "splashupdcheck=0",               # no startup update check (no phone-home)
            "autosavemode=0",                 # no autosave churn
            "loadlastproj=0",                 # always a clean empty project
            f"uiscale={ui:.6f}",              # pin UI scale from the lock (DPI determinism)
            *self._audio_ini_lines(lock),     # SR/block pinned; suppress the no-audio modal
            "[nag]",
            "nag=65535",                      # never the unlicensed nag (license copied in)
            "[verchk]",
            # Far-future last-version-check stamp. `splashupdcheck=0` above only
            # governs the splash; REAPER still runs the standalone startup version
            # check (gated by [verchk] lastt), which on an outdated pinned build
            # phones home and pops an APP-MODAL "New Version Notification". That
            # modal sets [NSApp modalWindow] and silently pollutes any test that
            # cares about modality/foreground (it broke capture-mode arming in the
            # MaxPane assessment). A stamp in the future makes REAPER believe it
            # just checked, so it stays offline and silent.
            "lastt=2000000000",
        ]
        profile.ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _audio_ini_lines(self, lock: DeterminismLock) -> list[str]:
        """Platform default: no audio config (subclasses override)."""
        return []

    def _seed_plugin_caches(self, profile: IsolatedProfile) -> None:
        """Seed REAPER's plugin-scan caches so a fresh profile skips the rescan.

        REAPER force-appends the default VST3/CLAP/AU system dirs to any
        ``vstpath`` we set, so a cold profile would re-validate the host's entire
        plugin collection (minutes) before ``__startup.lua`` ever runs. Seeding a
        valid cache makes REAPER consider those plugins already-known. The
        subject-under-test sits at a *new* controlled path and is scanned
        incrementally (one plugin, fast). Caches are host-specific, so they live in
        ``.cache/`` (gitignored), never VCS — CI containers ship only the subject
        and need no seeding. See DECISIONS D15.
        """
        src_dir = paths.WARM_CACHE if paths.WARM_CACHE.is_dir() else None
        if src_dir is None and paths.USER_REAPER_RES.is_dir():
            # No project warm cache (fresh clone) but the USER'S own REAPER has
            # complete scan caches — same machine, same plugin mtimes, so the
            # skip-entries are valid. Without this, a first run against the
            # user's plugin collection re-scans everything and can wedge on
            # license-protected plugins (verified on a clean-clone smoke test:
            # startup stuck at "Scanning VST plug-ins...").
            src_dir = paths.USER_REAPER_RES
        if src_dir is None:
            return
        for pattern in ("reaper-vstplugins*.ini", "reaper-clap-*.ini",
                        "reaper-auplugins*.ini", "reaper-vstshells*.ini"):
            for src in src_dir.glob(pattern):
                shutil.copy2(src, profile.resource_dir / src.name)

    def _install_extensions(self, profile: IsolatedProfile) -> None:
        """Copy the REAPER-side extensions into the isolated profile.

        These (js_ReaScriptAPI / SWS / ReaImGui) power visual capture + input
        synthesis. The audio battery does not need them, so a missing extension is a
        warning, not a hard error — the universal ``reaproof test`` flow still runs on
        a vanilla REAPER. Install them (and re-run) to enable the visual/input planes.
        """
        import warnings
        up = profile.resource_dir / "UserPlugins"
        for name in self.required_extensions:
            src = paths.USER_USERPLUGINS / name
            if not src.exists():
                warnings.warn(
                    f"REAPER extension not found: {src} — visual/input tests will be "
                    f"unavailable. Install js_ReaScriptAPI/SWS/ReaImGui to enable them.",
                    stacklevel=2,
                )
                continue
            shutil.copy2(src, up / name)

    def _install_license(self, profile: IsolatedProfile) -> None:
        if paths.USER_LICENSE.exists():
            shutil.copy2(paths.USER_LICENSE, profile.resource_dir / "reaper-license.rk")

    def _deploy_bridge(self, profile: IsolatedProfile) -> None:
        if not paths.BRIDGE_LUA.exists():
            raise FileNotFoundError(f"bridge source missing: {paths.BRIDGE_LUA}")
        shutil.copy2(paths.BRIDGE_LUA, profile.resource_dir / "Scripts" / "__startup.lua")

    def install_plugins(self, profile: IsolatedProfile, plugins: list[Path]) -> None:
        """Copy plugin artifacts-under-test into the controlled scan dir by format.

        Unknown formats raise — silently guessing a directory would leave the
        subject unscannable and surface later as a baffling "plugin not found".
        """
        by_suffix = {
            ".vst3": profile.plugin_dir / "VST3",
            ".clap": profile.plugin_dir / "CLAP",
            ".vst": profile.plugin_dir / "VST",
            ".dylib": profile.plugin_dir / "VST",
            ".lv2": profile.plugin_dir / "LV2",   # bundle dir (manifest.ttl inside)
        }
        for p in plugins:
            p = Path(p)
            dest = by_suffix.get(p.suffix.lower())
            if dest is None:
                raise ValueError(
                    f"unsupported plugin format {p.suffix!r} for {p.name} "
                    f"(expected {sorted(by_suffix)}; AU .component is not "
                    f"REAPER-scannable from a custom dir — use auval/pluginval)")
            if p.is_dir():
                shutil.copytree(p, dest / p.name, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest / p.name)

    # ---- relaunch hygiene --------------------------------------------------
    def _reset_run_dir(self, profile: IsolatedProfile) -> None:
        """Purge all IPC state before a launch so a RELAUNCH of the same profile
        cannot satisfy new commands with a previous run's cached artifacts.

        A fresh ``BridgeClient`` restarts its sequence counter at 1, so a stale
        ``cmd/out/00000001.json`` from a prior launch would be read as the answer
        to the new client's first ``eval`` — a false result. Every ``launch()``
        calls this; it also clears the liveness markers so ``wait_ready`` can't
        latch onto a previous run's ``ready.json``.
        """
        rd = profile.run_dir
        for marker in ("ready.json", "heartbeat.json"):
            (rd / marker).unlink(missing_ok=True)
        for sub in ("cmd/in", "cmd/out"):
            d = rd / sub
            if d.is_dir():
                for f in d.iterdir():
                    try:
                        f.unlink()
                    except OSError:
                        pass

    def _launch_env(self, profile: IsolatedProfile) -> dict[str, str]:
        """Environment for the REAPER process (all platforms).

        CLAP_PATH is the ONLY mechanism exposing a controlled CLAP dir to
        REAPER (it does not scan ``vstpath`` for CLAP), so every launcher must
        use this — macOS ``open`` forwards the environment (verified live),
        and Linux/Windows exec directly.
        """
        from reaproof.determinism import subprocess_env
        return subprocess_env({"CLAP_PATH": str(profile.plugin_dir / "CLAP")})

    # ---- platform hooks ----------------------------------------------------
    @abstractmethod
    def launch(self, profile: IsolatedProfile) -> LaunchHandle: ...

    @abstractmethod
    def is_alive(self, handle: LaunchHandle) -> bool: ...

    @abstractmethod
    def terminate(self, handle: LaunchHandle) -> None: ...


def get_provisioner() -> Provisioner:
    sysname = _platform.system()
    if sysname == "Darwin":
        from reaproof.provision.macos import MacOSProvisioner

        return MacOSProvisioner()
    if sysname == "Linux":
        from reaproof.provision.linux import LinuxProvisioner

        return LinuxProvisioner()   # CI-verified (Xvfb); see provision/linux.py
    raise NotImplementedError(
        f"provisioner for {sysname} not implemented (Windows is the remaining target)"
    )
