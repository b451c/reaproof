"""macOS provisioner: launch the isolated REAPER via ``open`` and supervise it.

Why ``open`` and not direct exec (DECISIONS D7): exec'ing the inner Mach-O from a
non-Aqua shell stalls at GUI init ("swell-cocoa: creating metal device context")
and never reaches the run-loop, so ``defer``/``__startup.lua`` never fire. ``open``
routes the app into the WindowServer session correctly. The trade-off — ``open``
returns no PID — is handled by discovering the PID via the unique ``-cfgfile`` path.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from reaproof import paths
from reaproof.determinism import subprocess_env
from reaproof.provision.base import IsolatedProfile, LaunchHandle, Provisioner


def _find_pid(ini_path: str, exclude: set[int] | None = None) -> int | None:
    """Find the REAPER PID for our unique -cfgfile path (never the user's)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"cfgfile {ini_path}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    if exclude:
        pids = [p for p in pids if p not in exclude]
    return pids[0] if pids else None


class MacOSProvisioner(Provisioner):
    required_extensions = paths.REQUIRED_EXTENSIONS
    vst_path_key = "vstpath_arm64"

    def _audio_ini_lines(self, lock):
        # CoreAudio with the default system devices, matching a configured host
        # profile EXACTLY, so REAPER never shows the blocking "no audio device"
        # modal (which freezes the main thread before __startup runs). An empty
        # input device reads as "unconfigured" and still prompts, so both in/out
        # are set. Offline -renderproject is device-independent, so this never
        # actually grabs hardware for the audio gate. SR/block pinned (§5.1).
        return [
            "coreaudioindevnew=<default system devices>",
            "coreaudiooutdevnew=<default system devices>",
            f"coreaudiosrate={lock.sample_rate}",
            "coreaudiosrateuse=1",
            f"coreaudiobs={lock.block_size}",
            "coreaudiobsuse=1",
        ]

    def launch(self, profile: IsolatedProfile) -> LaunchHandle:
        if not paths.REAPER_APP.exists():
            raise FileNotFoundError(f"pinned REAPER not provisioned: {paths.REAPER_APP}")
        ini = str(profile.ini_path)
        # clear any stale liveness markers so wait_ready can't read a previous run
        for f in (profile.run_dir / "ready.json", profile.run_dir / "heartbeat.json"):
            if f.exists():
                f.unlink()
        # CLAP_PATH (the CLAP-standard extra-search-path env var, which REAPER
        # honours and `open` forwards) points REAPER at the controlled CLAP dir, so
        # a subject-under-test CLAP loads hermetically without touching the user's
        # real ~/Library/Audio/Plug-Ins/CLAP. Empty dir = harmless no-op (JSFX/VST
        # runs are unaffected). REAPER does NOT scan vstpath dirs for CLAP (verified).
        env = subprocess_env({"CLAP_PATH": str(profile.plugin_dir / "CLAP")})
        subprocess.run(
            ["open", "-n", str(paths.REAPER_APP), "--args",
             "-newinst", "-noactivate", "-nosplash", "-cfgfile", ini],
            check=True, env=env, timeout=30,
        )
        # discover the PID (open is async); REAPER appears within a couple seconds
        pid = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            pid = _find_pid(ini)
            if pid:
                break
            time.sleep(0.1)
        if not pid:
            raise RuntimeError(f"REAPER launched but no PID found for {ini}")
        return LaunchHandle(pid=pid, profile=profile, extra={"ini": ini})

    def is_alive(self, handle: LaunchHandle) -> bool:
        try:
            os.kill(handle.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but not ours to signal (shouldn't happen here)

    def terminate(self, handle: LaunchHandle) -> None:
        ini = handle.extra.get("ini", "")
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not self.is_alive(handle):
                break
            try:
                os.kill(handle.pid, sig)
            except ProcessLookupError:
                break
            # wait briefly for exit before escalating
            for _ in range(30):
                if not self.is_alive(handle):
                    break
                time.sleep(0.1)
        # belt-and-braces: reap any straggler bound to our unique cfgfile path
        if ini:
            subprocess.run(["pkill", "-9", "-f", f"cfgfile {ini}"],
                           capture_output=True)
