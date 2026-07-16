"""Gates: ReaPack repo mode (U6.1). Fixture repo built in tmp from the
reference script subjects; the broken package IS the mutation."""
import shutil
import sys
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.runner.repotest import RepoTestOptions, discover_packages, run_repo_battery

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="per-package batteries use the macOS backend")

SCRIPTS = paths.EXAMPLES / "scripts"


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "Category").mkdir(parents=True)
    shutil.copy2(SCRIPTS / "rp_good.lua", repo / "Category" / "rp_good.lua")
    shutil.copy2(SCRIPTS / "rp_error_load.lua",
                 repo / "Category" / "rp_error_load.lua")
    # header-carrying file at the ROOT: ReaPack never indexes it
    shutil.copy2(SCRIPTS / "rp_good.lua", repo / "rp_rootfile.lua")
    # headerless helper: not a package at all
    shutil.copy2(SCRIPTS / "rp_noheader.lua", repo / "Category" / "helper.lua")
    return repo


def test_discovery_applies_reapack_rules(tmp_path):
    repo = _fixture_repo(tmp_path)
    packages, root_ignored = discover_packages(repo)
    names = [p.name for p in packages]
    assert "rp_good.lua" in names and "rp_error_load.lua" in names
    assert "helper.lua" not in names            # no header = not a package
    assert [f.name for f in root_ignored] == ["rp_rootfile.lua"]


@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_repo_battery_names_good_and_broken_packages(tmp_path):
    repo = _fixture_repo(tmp_path)
    rs = run_repo_battery(repo, out_dir=tmp_path / "report",
                          opts=RepoTestOptions(), log=lambda *_: None)
    st = {r.name: r.status for r in rs.results}
    assert st["metadata: reapack-index --check"] in ("passed", "skipped")
    assert st["layout: packages must live in a category subdir"] == "failed"
    assert st["package Category/rp_good.lua"] == "passed"
    assert st["package Category/rp_error_load.lua"] == "failed"   # the mutation
    assert not rs.gate_green


@pytest.mark.reaper
@pytest.mark.slow
def test_budget_cap_is_logged_never_silent(tmp_path):
    repo = _fixture_repo(tmp_path)
    (repo / "rp_rootfile.lua").unlink()          # keep only clean layout
    rs = run_repo_battery(repo, opts=RepoTestOptions(max_packages=1),
                          log=lambda *_: None)
    caps = [r for r in rs.results if r.name == "budget: package cap"]
    assert caps and caps[0].status == "skipped"
    assert "1/2" in caps[0].message
