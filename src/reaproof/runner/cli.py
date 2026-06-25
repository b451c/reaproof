"""The `reaproof` CLI (USER_GUIDE §10): setup · doctor · init · new-test · run · report."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from reaproof import paths


def _ok(b: bool) -> str:
    return "✓" if b else "✗"


def cmd_doctor(args) -> int:
    checks = {
        f"REAPER {paths.REAPER_VERSION} ({paths.REAPER_BUILD})": paths.REAPER_APP.exists(),
        "js_ReaScriptAPI / SWS / ReaImGui (user install)": all(
            (paths.USER_USERPLUGINS / n).exists() for n in paths.REQUIRED_EXTENSIONS),
        "warm plugin cache (.cache/warm_cache)": paths.WARM_CACHE.is_dir(),
        "clap-validator": paths.CLAP_VALIDATOR.exists(),
        "pluginval": paths.PLUGINVAL.exists(),
        "auval (system, macOS)": shutil.which("auval") is not None,
    }
    for mod in ("numpy", "scipy", "soundfile", "pyloudnorm", "PIL", "pytest"):
        try:
            __import__(mod); checks[f"python: {mod}"] = True
        except Exception:  # noqa: BLE001
            checks[f"python: {mod}"] = False
    if sys.platform == "darwin":
        try:
            import Quartz  # noqa: F401
            checks["pyobjc Quartz (macOS capture/input)"] = True
        except Exception:  # noqa: BLE001
            checks["pyobjc Quartz (macOS capture/input)"] = False
    for name, ok in checks.items():
        print(f"  {_ok(ok)} {name}")
    missing = [k for k, v in checks.items() if not v]
    if missing:
        print(f"\n{len(missing)} missing — see `reaproof setup` / docs/USER_GUIDE.md")
    return 0 if not missing else 1


def cmd_setup(args) -> int:
    print(f"ReaProof setup (REAPER {args.reaper})")
    print("Provisioning is described in docs/USER_GUIDE.md.")
    print("Verifying current state:\n")
    return cmd_doctor(args)


def cmd_init(args) -> int:
    from reaproof.runner.scaffold import init_project
    root = init_project(args.path)
    print(f"scaffolded {root}/ (reaproof.toml, tests/, goldens/)")
    return 0


def cmd_new_test(args) -> int:
    from reaproof.runner.scaffold import new_test
    dest = new_test(args.out, type=args.type, control=args.control)
    print(f"wrote {dest}\nrun it:  PYTHONPATH=src python -m pytest {dest} -v")
    return 0


def cmd_run(args) -> int:
    report_dir = args.report or str(paths.RUNS / "report")
    cmd = [sys.executable, "-m", "pytest", *(args.paths or ["tests/"]), "-q",
           f"--reaproof-report={report_dir}", f"--reaproof-repeat={args.repeat}"]
    if args.mutation_check:
        cmd.append("--mutation-check")
    import os
    e = dict(os.environ)
    e.update({"LC_ALL": "en_US.UTF-8", "LC_NUMERIC": "C", "TZ": "UTC"})
    e.setdefault("PYTHONPATH", str(paths.REPO_ROOT / "src"))
    rc = subprocess.run(cmd, env=e).returncode
    print(f"\nreport: {report_dir}/report.html  (JUnit/JSON alongside; provenance embedded)")
    return rc


def cmd_test(args) -> int:
    """Universal, zero-code test of a compiled plugin (.clap/.vst3/.vst)."""
    from reaproof.runner.autotest import AutotestOptions, run_autotest
    plugin = Path(args.plugin)
    if not plugin.exists():
        print(f"plugin not found: {plugin}")
        return 2
    out = Path(args.out) if args.out else paths.RUNS / f"autotest-{plugin.stem}"
    print(f"ReaProof — universal test of {plugin.name}\n")
    opts = AutotestOptions(
        is_instrument=args.instrument,
        sweep_params=not args.no_sweep,
        max_params=args.max_params,
        full=args.full,
    )
    rs = run_autotest(plugin, out_dir=out, opts=opts)
    c = rs.counts()
    print(f"\n{'GREEN ✓' if rs.gate_green else 'RED ✗'}  "
          f"passed {c['passed']} · failed {c['failed']} · skipped {c['quarantined']}")
    print(f"report: {out}/report.html  (JUnit/JSON alongside)")
    return 0 if rs.gate_green else 1


def cmd_goldens(args) -> int:
    import json as _json

    from reaproof.observe.visual.golden import GoldenStore
    store = GoldenStore()
    if args.action == "list":
        metas = sorted(store.root.glob("*.json"))
        if not metas:
            print(f"no approved goldens in {store.root}")
            return 0
        for m in metas:
            d = _json.loads(m.read_text())
            print(f"  {m.stem}  approver={d.get('approver')}  reason={d.get('reason')}")
        return 0
    if args.action == "approve":
        import numpy as np
        from PIL import Image

        from reaproof.observe.visual.golden import GoldenKey
        if not (args.candidate and args.plugin and args.control and args.state):
            print("approve needs --candidate --plugin --control --state [--version]")
            return 2
        img = np.asarray(Image.open(args.candidate).convert("RGB"))
        key = GoldenKey(plugin=args.plugin, version=args.version, control=args.control,
                        state=args.state)
        p = store.approve(img, key, approver=args.approver, reason=args.reason)
        print(f"approved golden: {p}")
        return 0
    return 2


def cmd_report(args) -> int:
    print("Reports are written per-run under .cache/runs/<id>/artifacts/ and via the")
    print("report module (JUnit/JSON/HTML). Open the HTML for the evidence bundle.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="reaproof",
                                description="Trustworthy testing for REAPER plugins/scripts.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="provision/verify pinned REAPER + validators")
    sp.add_argument("--reaper", default=paths.REAPER_VERSION)
    sp.set_defaults(func=cmd_setup)

    sub.add_parser("doctor", help="verify the environment").set_defaults(func=cmd_doctor)

    sp = sub.add_parser("init", help="scaffold a tests folder")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("new-test", help="generate a test")
    sp.add_argument("--type", default="knob")
    sp.add_argument("--control", default="GainKnob")
    sp.add_argument("--out", default="tests")
    sp.set_defaults(func=cmd_new_test)

    sp = sub.add_parser("run", help="run tests (emits JUnit/JSON/HTML + provenance report)")
    sp.add_argument("paths", nargs="*")
    sp.add_argument("--mutation-check", action="store_true",
                    help="report per-test mutation-verification (flag VACUOUS-risk)")
    sp.add_argument("--repeat", type=int, default=1,
                    help="run gate/determinism tests N times; quarantine on disagreement")
    sp.add_argument("--report", default=None, help="report output dir")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("test", help="universal zero-code test of a plugin (.clap/.vst3/.vst)")
    sp.add_argument("plugin", help="path to the plugin bundle")
    sp.add_argument("--out", default=None, help="report output dir")
    sp.add_argument("--full", action="store_true", help="sweep ALL parameters (no cap)")
    sp.add_argument("--no-sweep", action="store_true", help="skip the per-parameter sweep")
    sp.add_argument("--instrument", action="store_true",
                    help="plugin generates sound (skip the silence->silence check)")
    sp.add_argument("--max-params", type=int, default=16,
                    help="cap the per-parameter sweep (default 16; use --full for all)")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("goldens", help="review/approve reference images (never auto-update)")
    sp.add_argument("action", choices=["list", "approve"])
    sp.add_argument("--candidate", help="PNG to approve as the golden")
    sp.add_argument("--plugin"); sp.add_argument("--version", default="0.0.1")
    sp.add_argument("--control"); sp.add_argument("--state")
    sp.add_argument("--approver", default="cli-user")
    sp.add_argument("--reason", default="approved via CLI")
    sp.set_defaults(func=cmd_goldens)

    sub.add_parser("report", help="show results").set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
