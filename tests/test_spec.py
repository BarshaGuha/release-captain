"""Tests for subagents/spec.py — requirement coverage and its per-repo
config generalization (see subagents/spec.py's module docstring for the
.release-captain/spec-signals.json schema this replaced the hardcoded
todos.ts regex mapping with).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from subagents import spec  # noqa: E402


def _tmp_repo(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rc_spec_test_"))
    for rel_path, content in files.items():
        path = tmp / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp


def test_repo_supplied_config_is_used_and_named():
    tmp = _tmp_repo({
        "requirements.md": "- [ ] **R1 — Health endpoint responds.**\n",
        "src/app.py": "def health():\n    return {'status': 'ok'}\n",
        "src/app_test.py": "def test_health():\n    assert health()\n",
        ".release-captain/spec-signals.json": json.dumps({
            "impl_file": "src/app.py",
            "test_file": "src/app_test.py",
            "src_dir": "src",
            "signals": {
                "R1": {
                    "impl": "def health",
                    "test": "test_health",
                    "impl_label": "def health()",
                    "test_label": "test_health()",
                }
            },
        }),
    })
    try:
        result = spec.run(str(tmp), "requirements.md")
        assert result["requirements"][0]["status"] == "covered"
        assert "spec-signals.json" in result["config_source"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unmapped_requirement_is_unclear_not_silent_pass():
    tmp = _tmp_repo({
        "requirements.md": (
            "- [ ] **R1 — Health endpoint responds.**\n"
            "- [ ] **R2 — Some other requirement with no mapping.**\n"
        ),
        "src/app.py": "def health():\n    return True\n",
        "src/app_test.py": "def test_health():\n    assert health()\n",
        ".release-captain/spec-signals.json": json.dumps({
            "impl_file": "src/app.py",
            "test_file": "src/app_test.py",
            "src_dir": "src",
            "signals": {
                "R1": {"impl": "def health", "test": "test_health", "impl_label": "", "test_label": ""},
            },
        }),
    })
    try:
        result = spec.run(str(tmp), "requirements.md")
        statuses = {r["id"]: r["status"] for r in result["requirements"]}
        assert statuses["R1"] == "covered"
        assert statuses["R2"] == "unclear"  # never silently "covered"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_repo_config_falls_back_to_default_and_says_so():
    # A repo with none of its own .release-captain/spec-signals.json falls
    # back to the bundled default (this project's own demo target) —
    # confirmed here only by checking config_source names the fallback,
    # not by asserting specific coverage (that depends on the default's
    # own todos.ts-shaped signals, tested implicitly by the live demo).
    tmp = _tmp_repo({"requirements.md": "- [ ] **R1 — Anything.**\n"})
    try:
        result = spec.run(str(tmp), "requirements.md")
        assert "default" in result["config_source"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_requirements_file_returns_empty_not_a_crash():
    tmp = _tmp_repo({})
    try:
        result = spec.run(str(tmp), "requirements.md")
        assert result["total_requirements"] == 0
        assert result["requirements"] == []
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
