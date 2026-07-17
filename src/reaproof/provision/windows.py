"""Windows provisioner — first live spike on the Windows VM (2026-07-17).

Mirrors the Linux provisioner's direct-exec shape: REAPER is launched with
``-cfgfile`` into a hermetic profile, the file-queue bridge is deployed by the
shared base class, and liveness rides the Popen handle (``os.kill(pid, 0)`` is
NOT a liveness probe on Windows — it *terminates* the process).

Windows-specific facts baked in (live-verified on the VM):
- The bridge's atomic writes need the remove+rename retry (a concurrent
  reader without FILE_SHARE_DELETE blocks rename) — handled in the bridge Lua.
- The profile ini's ``[nag] nag=65535`` also keeps the evaluation-mode nag
  from blocking the bridge (same behaviour as the Linux leg).
- x64 REAPER under Windows-on-ARM emulation is fine for the bridge; the x64
  js_ReaScriptAPI dll question rides the same emulation.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from reaproof import paths
from reaproof.provision.base import IsolatedProfile, LaunchHandle, Provisioner

#: pinned Windows REAPER (fetched by setup/CI); REAPROOF_REAPER_APP overrides —
#: on the VM that is the system install at C:\Program Files\REAPER (x64)
REAPER_EXE_WINDOWS = Path(
    os.environ.get("REAPROOF_REAPER_APP",
                   paths.CACHE / "reaper775_win" / "REAPER" / "reaper.exe"))

REQUIRED_EXTENSIONS_WINDOWS = ("reaper_js_ReaScriptAPI64.dll",)


class WindowsProvisioner(Provisioner):
    required_extensions = REQUIRED_EXTENSIONS_WINDOWS
    vst_path_key = "vstpath64"          # x64 REAPER reads the 64-bit key

    def _audio_ini_lines(self, lock):
        # [audioconfig] mode=4 = Dummy Audio (live-verified on the VM via
        # GetAudioDeviceInfo("MODE") == "Dummy Audio"). Without ANY audioconfig
        # section a fresh profile pops the app-modal "select your audio device"
        # dialog BEFORE startup scripts, wedging the bridge invisibly — that
        # dialog (not the eval nag) was the Windows first-launch blocker.
        # Dummy SR/block key names await the full leg; offline renders bake
        # RENDER_SRATE into the project and are device-independent regardless.
        return ["[audioconfig]", "mode=4"]

    def launch(self, profile: IsolatedProfile) -> LaunchHandle:
        if not REAPER_EXE_WINDOWS.exists():
            raise FileNotFoundError(
                f"pinned Windows REAPER not provisioned: {REAPER_EXE_WINDOWS} "
                "(set REAPROOF_REAPER_APP or run the provisioning step)")
        self._reset_run_dir(profile)
        proc = subprocess.Popen(
            [str(REAPER_EXE_WINDOWS), "-newinst", "-nosplash",
             "-cfgfile", str(profile.ini_path)],
            env=self._launch_env(profile),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return LaunchHandle(pid=proc.pid, profile=profile,
                            extra={"proc": proc, "ini": str(profile.ini_path)})

    def is_alive(self, handle: LaunchHandle) -> bool:
        proc = handle.extra.get("proc")
        if proc is not None:
            return proc.poll() is None
        # reconstructed handle: SYNCHRONIZE probe via ctypes (never os.kill —
        # on Windows a non-signal "sig" TERMINATES the target)
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, handle.pid)
        if not h:
            return False
        alive = ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102
        ctypes.windll.kernel32.CloseHandle(h)
        return alive

    def terminate(self, handle: LaunchHandle) -> None:
        proc = handle.extra.get("proc")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            for _ in range(50):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
        # straggler reap by unique cfgfile (mirrors the POSIX pkill fallback)
        ini = handle.extra.get("ini", "").replace("'", "''")
        if ini:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='reaper.exe'\" | "
                 f"Where-Object {{ $_.CommandLine -like '*{ini}*' }} | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                capture_output=True, timeout=30)
