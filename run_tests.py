#!/usr/bin/env python3
"""Run the self-test suite in tests/.

Prefers pytest if it's installed (`pip install -r requirements-dev.txt`)
since it gives better output and discovery; falls back to each test file's
own plain-assert runner (see each tests/test_*.py's `if __name__ ==
"__main__"` block) when pytest isn't available, so the suite is always
runnable with nothing beyond the standard library.

Usage: python3 run_tests.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent / "tests"


def _try_pytest() -> int | None:
    try:
        import pytest  # noqa: F401
    except ImportError:
        return None
    return subprocess.run([sys.executable, "-m", "pytest", str(TESTS_DIR), "-v"]).returncode


def _fallback_runner() -> int:
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    overall_ok = True
    for test_file in test_files:
        print(f"\n=== {test_file.name} ===")
        result = subprocess.run([sys.executable, str(test_file)])
        overall_ok = overall_ok and result.returncode == 0
    return 0 if overall_ok else 1


if __name__ == "__main__":
    code = _try_pytest()
    if code is None:
        print("pytest not installed — using each test file's built-in fallback runner.")
        print("For nicer output: pip install -r requirements-dev.txt\n")
        code = _fallback_runner()
    sys.exit(code)
