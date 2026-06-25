"""Linux provisioner (CI substrate — verified in the GitHub Actions Xvfb leg, §5.3).

Linux is the recommended CI substrate: the real GUI renders into Xvfb (no Screen
Recording TCC), js_ReaScriptAPI GDI blit reads real pixels, and JS_WindowMessage
drives controls in-process. REAPER is exec'd directly (no macOS `open`); a Dummy/null
audio device avoids hardware. The pinned Linux REAPER tarball + the Linux
js_ReaScriptAPI `.so` are provisioned by `reaproof setup` / the CI job (via ReaPack).

NOTE: implemented against the documented Linux tooling; verified in CI, not on the
macOS host (it is never imported there). `get_provisioner()` returns it on Linux.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from reaproof import paths
from reaproof.determinism import subprocess_env
from reaproof.provision.base import IsolatedProfile, LaunchHandle, Provisioner

# Linux REAPER + the js extension live under .cache (fetched by setup/CI).
REAPER_BIN_LINUX = paths.CACHE / "reaper775_linux" / "REAPER" / "reaper"
REQUIRED_EXTENSIONS_LINUX = ("reaper_js_ReaScriptAPI64.so",)


class LinuxProvisioner(Provisioner):
    required_extensions = REQUIRED_EXTENSIONS_LINUX
    vst_path_key = "vstpath"

    def _audio_ini_lines(self, lock):
        # Dummy audio device: no hardware, fixed SR/block (§5.1). REAPER's Dummy Audio
        # is selected by an empty CoreAudio-equivalent here; the offline render path is
        # device-independent regardless.
        return [
            "dummy_audio=1",
            f"reaudio_srate={lock.sample_rate}",
            f"reaudio_bsize={lock.block_size}",
        ]

    def launch(self, profile: IsolatedProfile) -> LaunchHandle:
        if not REAPER_BIN_LINUX.exists():
            raise FileNotFoundError(
                f"pinned Linux REAPER not provisioned: {REAPER_BIN_LINUX} "
                "(run `reaproof setup` / the CI provisioning step)")
        for f in (profile.run_dir / "ready.json", profile.run_dir / "heartbeat.json"):
            if f.exists():
                f.unlink()
        # direct exec under the (Xvfb) display — no `open`; force software GL for
        # cross-machine pixel determinism is set in the environment by the CI job.
        proc = subprocess.Popen(
            [str(REAPER_BIN_LINUX), "-newinst", "-nosplash", "-cfgfile", str(profile.ini_path)],
            env=subprocess_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return LaunchHandle(pid=proc.pid, profile=profile, extra={"proc": proc})

    def is_alive(self, handle: LaunchHandle) -> bool:
        try:
            os.kill(handle.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def terminate(self, handle: LaunchHandle) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not self.is_alive(handle):
                return
            try:
                os.kill(handle.pid, sig)
            except ProcessLookupError:
                return
            for _ in range(30):
                if not self.is_alive(handle):
                    return
                time.sleep(0.1)
