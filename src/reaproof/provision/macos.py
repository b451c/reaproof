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
from reaproof.provision.base import IsolatedProfile, LaunchHandle, Provisioner


def _find_pid(ini_path: str, exclude: set[int] | None = None) -> int | None:
    """Find the MAIN REAPER PID for our unique -cfgfile path (never the user's).

    ``pgrep -f`` matches EVERY process whose command line carries the cfgfile
    path — including REAPER's transient plugin-scan helper, observed running
    with the same ``-cfgfile`` plus a ``__vst_scan__`` marker. Latching a
    helper means liveness reports the session dead the moment the scan ends,
    and ``terminate`` kills the helper while the real REAPER lives on as an
    orphan. So: fetch the matches' command lines and drop (a) anything carrying
    a ``__vst_scan__`` marker, (b) anything whose *parent* is also a match
    (helpers are children of the main instance; the main is not).
    """
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
    if not pids:
        return None
    try:
        ps = subprocess.run(
            ["ps", "-o", "pid=,ppid=,command=", "-p", ",".join(map(str, pids))],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return pids[0]  # ps unavailable: degrade to the old behaviour
    matched = set(pids)
    mains = []
    for line in ps.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
        if pid not in matched:
            continue
        if "__vst_scan__" in cmd:
            continue                    # transient plugin-scan helper
        if ppid in matched:
            continue                    # child of another match = a helper
        mains.append(pid)
    return mains[0] if mains else None


class MacOSProvisioner(Provisioner):
    required_extensions = paths.REQUIRED_EXTENSIONS
    vst_path_key = "vstpath_arm64"

    def _clear_quarantine(self, path) -> None:
        # Gatekeeper refuses a quarantined unsigned dylib in UserPlugins with no
        # visible error (REAPER simply doesn't load it) — clear the xattr so a
        # locally-built extension-under-test actually loads.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(path)],
                       capture_output=True)

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
        # purge liveness markers AND the IPC queue so a relaunch of this profile
        # can't answer new commands with a previous run's cached responses
        self._reset_run_dir(profile)
        # CLAP_PATH (the CLAP-standard extra-search-path env var, which REAPER
        # honours and `open` forwards — verified live: os.getenv inside REAPER)
        # points REAPER at the controlled CLAP dir, so a subject-under-test CLAP
        # loads hermetically without touching the user's real
        # ~/Library/Audio/Plug-Ins/CLAP. Empty dir = harmless no-op.
        env = self._launch_env(profile)
        # -splashlog: REAPER writes its full startup log (incl. every
        # "Loading plug-in: <name>.dylib" line — verified) to this file; a
        # first-class forensic artifact for extension/scan issues (U1.4)
        splash = profile.artifacts_dir / "splash.log"
        subprocess.run(
            ["open", "-n", str(paths.REAPER_APP), "--args",
             "-newinst", "-noactivate", "-nosplash",
             "-splashlog", str(splash), "-cfgfile", ini],
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
