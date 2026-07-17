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
    # the pinned-build CLAIM must match what the resolved app ACTUALLY is —
    # REAPROOF_REAPER_APP can point anywhere, and provenance must never lie
    actual = paths.reaper_app_build()
    reaper_label = f"REAPER {paths.REAPER_VERSION} ({paths.REAPER_BUILD})"
    reaper_ok = paths.REAPER_APP.exists() and actual == paths.REAPER_BUILD
    if actual and actual != paths.REAPER_BUILD:
        reaper_label += (f" — app is actually {actual}; set REAPROOF_REAPER_BUILD "
                         f"to match or provenance would be wrong")
    checks = {
        reaper_label: reaper_ok,
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
    print("Provisioning is described in docs/USER_GUIDE.md and DECISIONS.md (D4-D18).")
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
    # `reaproof run` applies the determinism lock; a raw pytest hint would not
    print(f"wrote {dest}\nrun it:  reaproof run {dest}")
    return 0


def cmd_run(args) -> int:
    report_dir = args.report or str(paths.RUNS / "report")
    cmd = [sys.executable, "-m", "pytest", *(args.paths or ["tests/"]), "-q",
           f"--reaproof-report={report_dir}", f"--reaproof-repeat={args.repeat}"]
    if args.mutation_check:
        cmd.append("--mutation-check")
    import os

    from reaproof.determinism import LOCKED_ENV
    e = dict(os.environ)
    e.update(LOCKED_ENV)  # the FULL lock (incl. PYTHONHASHSEED/LANG), not a subset
    e.setdefault("PYTHONPATH", str(paths.REPO_ROOT / "src"))
    rc = subprocess.run(cmd, env=e).returncode
    print(f"\nreport: {report_dir}/report.html  (JUnit/JSON alongside; provenance embedded)")
    return rc


def _print_verdict(rs, out) -> int:
    c = rs.counts()
    print(f"\n{'GREEN ✓' if rs.gate_green else 'RED ✗'}  "
          f"passed {c['passed']} · failed {c['failed']} · skipped {c['skipped']} · "
          f"quarantined {c['quarantined']}")
    print(f"report: {out}/report.html  (JUnit/JSON alongside)")
    return 0 if rs.gate_green else 1


def cmd_test(args) -> int:
    """Universal, zero-code test — dispatched by subject type:
    .clap/.vst3/.vst -> plugin battery; .lua/.eel/.py -> ReaScript battery;
    reaper_*.dylib -> native-extension battery; .jsfx -> JSFX battery."""
    subject = Path(args.plugin)
    if not subject.exists():
        print(f"subject not found: {subject}")
        return 2
    out = Path(args.out) if args.out else paths.RUNS / f"autotest-{subject.stem}"
    print(f"ReaProof — universal test of {subject.name}\n")
    if subject.suffix.lower() in (".lua", ".eel", ".py"):
        from reaproof.runner.scripttest import ScriptTestOptions, run_script_battery
        rs = run_script_battery(subject, out_dir=out, opts=ScriptTestOptions(
            ui=args.ui,
            expect_project_change=args.expect_modifies_project,
            expect_extstate_change=args.expect_extstate,
            expect_gmem_change=args.expect_gmem,
        ))
        return _print_verdict(rs, out)
    if subject.suffix.lower() == ".dylib":
        from reaproof.runner.exttest import ExtTestOptions, run_extension_battery
        rs = run_extension_battery(subject, out_dir=out,
                                   opts=ExtTestOptions(run_actions=args.run_actions))
        return _print_verdict(rs, out)
    if subject.suffix.lower() in (".reaperthemezip", ".reapertheme"):
        from reaproof.runner.themetest import run_theme_battery
        rs = run_theme_battery(subject, out_dir=out)
        return _print_verdict(rs, out)
    if subject.suffix.lower() == ".jsfx":
        from reaproof.runner.jsfxtest import JsfxTestOptions, run_jsfx_battery
        rs = run_jsfx_battery(subject, out_dir=out, opts=JsfxTestOptions(
            sweep_params=not args.no_sweep,
            max_params=args.max_params,
            full=args.full,
            is_instrument=args.instrument,
        ))
        return _print_verdict(rs, out)
    from reaproof.runner.autotest import AutotestOptions, run_autotest
    plugin = subject
    opts = AutotestOptions(
        is_instrument=args.instrument,
        sweep_params=not args.no_sweep,
        max_params=args.max_params,
        full=args.full,
    )
    rs = run_autotest(plugin, out_dir=out, opts=opts)
    return _print_verdict(rs, out)


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


def cmd_test_repo(args) -> int:
    from reaproof.runner.repotest import RepoTestOptions, run_repo_battery
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"repo not found: {repo}")
        return 2
    out = Path(args.out) if args.out else paths.RUNS / f"repotest-{repo.name}"
    print(f"ReaProof — ReaPack repo test of {repo}\n")
    rs = run_repo_battery(repo, out_dir=out,
                          opts=RepoTestOptions(max_packages=args.max_packages))
    return _print_verdict(rs, out)


def cmd_author(args) -> int:
    """Scaffold the agent-authoring flow: feature-manifest skeleton + charter."""
    import json as _json
    subject = Path(args.subject)
    if not subject.exists():
        print(f"subject not found: {subject}")
        return 2
    from reaproof.coverage.features import scaffold
    stype = {".lua": "script", ".eel": "script", ".dylib": "extension",
             ".clap": "plugin", ".vst3": "plugin", ".vst": "plugin",
             ".lv2": "plugin", ".jsfx": "jsfx",
             ".reaperthemezip": "theme", ".reapertheme": "theme",
             }.get(subject.suffix.lower(), "script")
    manifest = Path(args.out or (subject.parent / "reaproof_features.json"))
    if manifest.exists() and not args.force:
        print(f"manifest exists: {manifest} (use --force to overwrite)")
        return 2
    manifest.write_text(_json.dumps(
        scaffold(subject, subject_type=stype, mode=args.mode),
        indent=2) + "\n", encoding="utf-8")
    charter = paths.REPO_ROOT / "docs" / "AGENT_TEST_AUTHORING.md"
    print(f"scaffolded {manifest}  (mode: {args.mode})")
    print(f"\nAGENT: read and follow the charter now: {charter}")
    print("  1. inventory features from the SOURCE into the manifest")
    print("  2. run the zero-code battery first:  reaproof test " + str(subject))
    print("  3. write specs; each test lists COVERS = ['feature_id', ...]")
    print("  4. verify:  reaproof features-report "
          f"{manifest} --tests {subject.parent}")
    return 0


def cmd_features_report(args) -> int:
    """Validate a feature manifest (anti-fabrication rules) + print coverage."""
    from reaproof.coverage.features import load_manifest, render_report, validate
    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"manifest not found: {manifest}")
        return 2
    rep = validate(load_manifest(manifest),
                   tests_root=Path(args.tests) if args.tests else None)
    print(render_report(rep))
    return 0 if rep.ok else 1


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

    sp = sub.add_parser("test", help="universal zero-code test of a plugin "
                                     "(.clap/.vst3/.vst), JSFX (.jsfx), ReaScript "
                                     "(.lua/.eel), extension or theme")
    sp.add_argument("plugin", help="path to the plugin bundle / script")
    sp.add_argument("--out", default=None, help="report output dir")
    sp.add_argument("--full", action="store_true", help="sweep ALL parameters (no cap)")
    sp.add_argument("--no-sweep", action="store_true", help="skip the per-parameter sweep")
    sp.add_argument("--instrument", action="store_true",
                    help="plugin generates sound (skip the silence->silence check)")
    sp.add_argument("--max-params", type=int, default=16,
                    help="cap the per-parameter sweep (default 16; use --full for all)")
    sp.add_argument("--ui", action="store_true",
                    help="script: a window is EXPECTED to appear (checked, not a leak)")
    sp.add_argument("--expect-modifies-project", action="store_true",
                    help="script: project edits are declared/expected")
    sp.add_argument("--expect-extstate", action="store_true",
                    help="script: persistent ExtState writes are declared/expected")
    sp.add_argument("--expect-gmem", action="store_true",
                    help="script: writes to its attached gmem namespace(s) are "
                         "declared/expected (the differ watches every "
                         "gmem_attach'd namespace)")
    sp.add_argument("--run-actions", action="store_true",
                    help="extension: RUN each registered action under supervision "
                         "(opt-in — actions can be destructive/interactive)")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("goldens", help="review/approve reference images (never auto-update)")
    sp.add_argument("action", choices=["list", "approve"])
    sp.add_argument("--candidate", help="PNG to approve as the golden")
    sp.add_argument("--plugin"); sp.add_argument("--version", default="0.0.1")
    sp.add_argument("--control"); sp.add_argument("--state")
    sp.add_argument("--approver", default="cli-user")
    sp.add_argument("--reason", default="approved via CLI")
    sp.set_defaults(func=cmd_goldens)

    sp = sub.add_parser("test-repo", help="test a ReaPack repository: metadata check "
                                          "+ per-package zero-code batteries")
    sp.add_argument("repo", help="path to the ReaPack repository root")
    sp.add_argument("--out", default=None, help="report output dir")
    sp.add_argument("--max-packages", type=int, default=8,
                    help="battery cap (each launches REAPER); dropped rest is logged")
    sp.set_defaults(func=cmd_test_repo)

    sp = sub.add_parser("author", help="scaffold agent test-authoring for a subject "
                                       "(feature manifest + charter instructions)")
    sp.add_argument("subject", help="path to the subject (script/plugin/ext/theme)")
    sp.add_argument("--mode", choices=["auto", "interactive"], default="interactive",
                    help="auto: the agent decides everything and records assumptions; "
                         "interactive: the agent asks at checkpoints")
    sp.add_argument("--out", default=None, help="manifest path (default: next to subject)")
    sp.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    sp.set_defaults(func=cmd_author)

    sp = sub.add_parser("features-report",
                        help="validate a feature manifest + print coverage/assumptions")
    sp.add_argument("manifest")
    sp.add_argument("--tests", default=None,
                    help="root dir for resolving test references (enables the "
                         "covered-implies-existing-tests check)")
    sp.set_defaults(func=cmd_features_report)

    sub.add_parser("report", help="show results").set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
