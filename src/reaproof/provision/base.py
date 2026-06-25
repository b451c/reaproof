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
        return profile

    def install_jsfx(self, profile: IsolatedProfile, files: list[Path]) -> None:
        """Install JSFX into the profile's Effects/ tree (added as 'JS: <desc>')."""
        dest = profile.resource_dir / "Effects" / "ReaProof"
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / Path(f).name)

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
            "defsplash=0",                    # no splash window
            "splashupdcheck=0",               # no startup update check (no phone-home)
            "autosavemode=0",                 # no autosave churn
            "loadlastproj=0",                 # always a clean empty project
            f"uiscale={ui:.6f}",              # pin UI scale from the lock (DPI determinism)
            *self._audio_ini_lines(lock),     # SR/block pinned; suppress the no-audio modal
            "[nag]",
            "nag=65535",                      # never the unlicensed nag (license copied in)
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
        if not paths.WARM_CACHE.is_dir():
            return
        for src in paths.WARM_CACHE.glob("reaper-*.ini"):
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
        """Copy plugin artifacts-under-test into the controlled scan dir by format."""
        for p in plugins:
            p = Path(p)
            suffix = p.suffix.lower()
            dest = {
                ".vst3": profile.plugin_dir / "VST3",
                ".clap": profile.plugin_dir / "CLAP",
                ".vst": profile.plugin_dir / "VST",
                ".dylib": profile.plugin_dir / "VST",
            }.get(suffix, profile.plugin_dir / "VST")
            if p.is_dir():
                shutil.copytree(p, dest / p.name, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest / p.name)

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
