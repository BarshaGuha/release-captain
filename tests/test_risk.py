"""Tests for subagents/risk.py — dependency-risk severity assignment,
across all three supported manifest ecosystems (Node, pip, pyproject).

Covers, most importantly, that "high" severity is actually reachable (the
bug this project's own code review found: risk_level could be declared
"high" in the contract but no code path ever produced it) for every
ecosystem, not just Node.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from subagents import risk  # noqa: E402
from tests.gitrepo import make_repo  # noqa: E402


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="rc_risk_test_"))


# ---------------------------------------------------------------------------
# Node (package.json / package-lock.json)
# ---------------------------------------------------------------------------

def test_node_removed_production_dependency_is_high():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={
                "package.json": '{"dependencies":{"express":"3.0.0"},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/express":{"version":"3.0.0"}}}',
            },
            head_files={
                "package.json": '{"dependencies":{},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{}}',
            },
        )
        result = risk.run(repo, base)
        assert result["manifest_kind"] == "node"
        assert result["risk_level"] == "high"
        assert any(f["package"] == "express" and f["severity"] == "high" for f in result["findings"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_node_removed_dev_dependency_is_low():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={
                "package.json": '{"dependencies":{},"devDependencies":{"jest":"1.0.0"}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/jest":{"version":"1.0.0"}}}',
            },
            head_files={
                "package.json": '{"dependencies":{},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{}}',
            },
        )
        result = risk.run(repo, base)
        assert result["risk_level"] == "low"
        assert result["findings"][0]["severity"] == "low"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_node_single_major_bump_is_medium_not_high():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={
                "package.json": '{"dependencies":{"react":"17.0.0"},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"17.0.0"}}}',
            },
            head_files={
                "package.json": '{"dependencies":{"react":"18.0.0"},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"18.0.0"}}}',
            },
        )
        result = risk.run(repo, base)
        assert result["risk_level"] == "medium"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_node_multi_major_skip_is_high():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={
                "package.json": '{"dependencies":{"react":"16.0.0"},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"16.0.0"}}}',
            },
            head_files={
                "package.json": '{"dependencies":{"react":"19.0.0"},"devDependencies":{}}',
                "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"19.0.0"}}}',
            },
        )
        result = risk.run(repo, base)
        assert result["risk_level"] == "high"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_node_no_changes_is_low_empty():
    tmp = _tmp()
    try:
        manifest = '{"dependencies":{"lodash":"4.0.0"},"devDependencies":{}}'
        lock = '{"lockfileVersion":3,"packages":{"node_modules/lodash":{"version":"4.0.0"}}}'
        repo, base = make_repo(
            tmp,
            base_files={"package.json": manifest, "package-lock.json": lock},
            head_files={},
        )
        result = risk.run(repo, base)
        assert result["risk_level"] == "low"
        assert result["findings"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# pip (requirements.txt)
# ---------------------------------------------------------------------------

def test_pip_multi_major_skip_is_high():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={"requirements.txt": "flask==1.0.0\n"},
            head_files={"requirements.txt": "flask==3.0.0\n"},
        )
        result = risk.run(repo, base)
        assert result["manifest_kind"] == "pip"
        assert result["risk_level"] == "high"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pip_removed_dependency_is_high():
    tmp = _tmp()
    try:
        repo, base = make_repo(
            tmp,
            base_files={"requirements.txt": "flask==1.0.0\noldpkg==1.0.0\n"},
            head_files={"requirements.txt": "flask==1.0.0\n"},
        )
        result = risk.run(repo, base)
        assert any(f["package"] == "oldpkg" and f["severity"] == "high" for f in result["findings"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# pyproject.toml (PEP 621)
# ---------------------------------------------------------------------------

def test_pyproject_major_skip_is_high():
    tmp = _tmp()
    try:
        base_toml = '[project]\nname = "demo"\ndependencies = ["django==3.0.0"]\n'
        head_toml = '[project]\nname = "demo"\ndependencies = ["django==5.0.0"]\n'
        repo, base = make_repo(
            tmp,
            base_files={"pyproject.toml": base_toml},
            head_files={"pyproject.toml": head_toml},
        )
        result = risk.run(repo, base)
        assert result["manifest_kind"] == "pyproject"
        assert result["risk_level"] == "high"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# No manifest at all
# ---------------------------------------------------------------------------

def test_no_manifest_reports_none_kind_not_false_low_confidence():
    tmp = _tmp()
    try:
        repo, base = make_repo(tmp, base_files={"README.md": "hi\n"}, head_files={})
        result = risk.run(repo, base)
        assert result["manifest_kind"] == "none"
        # Still technically "low" (nothing to flag), but the summary must
        # say plainly that nothing was actually checked.
        assert "not actually" in result["summary_markdown"] or "could not actually" in result["summary_markdown"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    failures = []
    tests = {name: fn for name, fn in list(globals().items()) if name.startswith("test_")}
    for name, fn in tests.items():
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
