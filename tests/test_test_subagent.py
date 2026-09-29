"""Tests for subagents/test.py — in particular that a hung test command is
turned into a clear, bounded RuntimeError (which the orchestrator then
treats as a subagent error, forcing NO-GO) instead of hanging forever.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from subagents import test as _test_subagent  # noqa: E402 (leading underscore: not picked up as a test_* function below)


def test_hung_command_times_out_instead_of_hanging_forever():
    tmp = Path(tempfile.mkdtemp(prefix="rc_test_subagent_"))
    try:
        # node_modules exists so the install step is skipped and only the
        # test-run timeout path is exercised.
        (tmp / "node_modules").mkdir()
        started = time.perf_counter()
        try:
            _test_subagent.run(str(tmp), test_command="sleep 30", test_timeout=1)
            raised = False
        except RuntimeError:
            raised = True
        elapsed = time.perf_counter() - started
        assert raised, "a hung test command must raise, not hang forever"
        assert elapsed < 10, f"timeout should bound the wait to ~1s, took {elapsed:.1f}s"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_normal_command_still_works():
    tmp = Path(tempfile.mkdtemp(prefix="rc_test_subagent_"))
    try:
        (tmp / "node_modules").mkdir()
        result = _test_subagent.run(str(tmp), test_command="echo done", test_timeout=10)
        assert "duration_seconds" in result
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
