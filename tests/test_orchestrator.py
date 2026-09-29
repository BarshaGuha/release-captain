"""Tests for orchestrator._compute_verdict — the conjunctive GO /
GO-WITH-WARNINGS / NO-GO rule described in orchestrator.py's module
docstring. These are plain assert-based functions: runnable under pytest
(`pytest tests/`) and, since they take no fixtures, also directly by
calling each test_* function, which is how they were verified while this
suite was written (no network access here to install pytest itself).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import orchestrator  # noqa: E402


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_clean_run_is_go():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {"risk_level": "low", "findings": []},
        {"requirements": [{"id": "R1", "status": "covered"}]},
        NOW,
    )
    assert verdict == "GO"
    assert warnings == []


def test_medium_risk_is_go_with_warnings_not_silent_pass():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {
            "risk_level": "medium",
            "findings": [
                {"package": "lodash", "change": "4.0->4.1", "severity": "medium", "reason": "minor"}
            ],
        },
        {"requirements": []},
        NOW,
    )
    assert verdict == "GO-WITH-WARNINGS"
    assert len(warnings) == 1
    w = warnings[0]
    assert w["source"] == "risk"
    assert w["id"] == "lodash"
    # A warning must always be disposed (owner + rationale + expiry), never
    # just a bare flag — this is the point of the whole warning tier.
    assert w["owner"]
    assert w["rationale"]
    assert w["expiry"]


def test_unclear_spec_requirement_is_go_with_warnings():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {"risk_level": "low", "findings": []},
        {"requirements": [{"id": "R2", "status": "unclear", "text": "auth", "evidence": "none"}]},
        NOW,
    )
    assert verdict == "GO-WITH-WARNINGS"
    assert warnings[0]["source"] == "spec"
    assert warnings[0]["id"] == "R2"


def test_failed_tests_block_regardless_of_everything_else():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": False},
        {"risk_level": "low", "findings": []},
        {"requirements": [{"id": "R1", "status": "covered"}]},
        NOW,
    )
    assert verdict == "NO-GO"
    assert warnings == []  # blocking short-circuits warning computation


def test_none_passed_blocks_missing_evidence_never_a_pass():
    # tests["passed"] is None when the test subagent produced no verdict at
    # all (as distinct from an explicit False) — must still block.
    verdict, _ = orchestrator._compute_verdict(
        {"passed": None},
        {"risk_level": "low", "findings": []},
        {"requirements": []},
        NOW,
    )
    assert verdict == "NO-GO"


def test_high_risk_blocks():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {"risk_level": "high", "findings": []},
        {"requirements": []},
        NOW,
    )
    assert verdict == "NO-GO"
    assert warnings == []


def test_not_covered_requirement_blocks():
    verdict, _ = orchestrator._compute_verdict(
        {"passed": True},
        {"risk_level": "low", "findings": []},
        {"requirements": [{"id": "R1", "status": "not_covered"}]},
        NOW,
    )
    assert verdict == "NO-GO"


def test_subagent_error_blocks_each_of_the_three():
    for key in ("test", "risk", "spec"):
        results = {
            "test": {"passed": True},
            "risk": {"risk_level": "low", "findings": []},
            "spec": {"requirements": []},
        }
        results[key] = {"error": "boom"}
        verdict, _ = orchestrator._compute_verdict(
            results["test"], results["risk"], results["spec"], NOW
        )
        assert verdict == "NO-GO", f"expected NO-GO when {key} subagent errors"


def test_no_go_beats_warnings_conjunctive_not_compensatory():
    # A blocking failure (not_covered) alongside a medium-risk finding must
    # still be NO-GO, not "averaged" into GO-WITH-WARNINGS — gates are
    # conjunctive: nothing compensates for a blocking failure.
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {
            "risk_level": "medium",
            "findings": [{"package": "x", "change": "1->2", "severity": "medium", "reason": "r"}],
        },
        {"requirements": [{"id": "R1", "status": "not_covered"}]},
        NOW,
    )
    assert verdict == "NO-GO"
    assert warnings == []


def test_warning_expiry_is_in_the_future():
    verdict, warnings = orchestrator._compute_verdict(
        {"passed": True},
        {
            "risk_level": "medium",
            "findings": [{"package": "x", "change": "1->2", "severity": "medium", "reason": "r"}],
        },
        {"requirements": []},
        NOW,
    )
    expiry = datetime.fromisoformat(warnings[0]["expiry"])
    assert expiry.date() > NOW.date()


if __name__ == "__main__":
    # Fallback runner for environments without pytest installed: run every
    # test_* function directly and report pass/fail, so this suite is
    # verifiable without a network connection to install pytest.
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
