"""Release Captain orchestrator.

CONTRACT
--------
run_release_check(repo_path, since_ref, requirements_path, test_command) -> dict:
    {
        "verdict": "GO" | "NO-GO",
        "generated_at": str,          # ISO timestamp
        "duration_seconds": float,     # wall-clock time for this whole run
        "changelog": <output of subagents/changelog.py>,
        "risk": <output of subagents/risk.py>,
        "tests": <output of subagents/test.py>,
        "spec": <output of subagents/spec.py>,
        "rollback_plan_markdown": str,   # derived from the changelog
    }

Verdict rule:
    NO-GO if: tests["passed"] is False OR tests["passed"] is None OR
              risk["risk_level"] == "high" OR
              any requirement has status == "not_covered".
    Otherwise GO.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from subagents import changelog, risk, test, spec


def _read_test_command(repo_path: str) -> str:
    """Read scripts.test from the repo's package.json."""
    pkg_path = Path(repo_path) / "package.json"
    with open(pkg_path) as f:
        pkg = json.load(f)
    return pkg["scripts"]["test"]


def _make_rollback_plan(changelog_result: dict, since_ref: str) -> str:
    """Build a one-sentence rollback summary from the changelog output."""
    commit_count = changelog_result.get("commit_count", 0)
    if commit_count == 0:
        return f"No commits since `{since_ref}`; nothing to roll back."
    return (
        f"To roll back this release, revert branch to `{since_ref}` "
        f"(undoes {commit_count} commit(s) merged since that ref)."
    )


def run_release_check(
    repo_path: str,
    since_ref: str,
    requirements_path: str,
    test_command: str | None = None,
) -> dict:
    """Run all four subagents concurrently and return the full release report."""
    repo_path = str(Path(repo_path).resolve())

    # Resolve test command from package.json if not supplied
    if test_command is None:
        test_command = _read_test_command(repo_path)

    start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all four futures before calling .result() on any of them
        future_changelog = executor.submit(changelog.run, repo_path, since_ref)
        future_risk      = executor.submit(risk.run,      repo_path, since_ref)
        future_test      = executor.submit(test.run,      repo_path, test_command)
        future_spec      = executor.submit(spec.run,      repo_path, requirements_path)

        # Collect results — wrap each in try/except so one failure can't crash the run
        try:
            changelog_result = future_changelog.result()
        except Exception as e:
            changelog_result = {"error": str(e)}

        try:
            risk_result = future_risk.result()
        except Exception as e:
            risk_result = {"error": str(e)}

        try:
            test_result = future_test.result()
        except Exception as e:
            test_result = {"error": str(e)}

        try:
            spec_result = future_spec.result()
        except Exception as e:
            spec_result = {"error": str(e)}

    duration = time.perf_counter() - start

    # --- Verdict rule ---
    tests_passed = test_result.get("passed")   # None if subagent errored
    risk_level   = risk_result.get("risk_level", "high")  # pessimistic default on error
    requirements = spec_result.get("requirements", [])

    no_go = (
        tests_passed is not True          # False OR None → NO-GO
        or risk_level == "high"
        or any(r.get("status") == "not_covered" for r in requirements)
        or "error" in test_result         # subagent itself crashed
        or "error" in risk_result
        or "error" in spec_result
    )

    verdict = "NO-GO" if no_go else "GO"

    rollback_plan = _make_rollback_plan(changelog_result, since_ref)

    return {
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration, 3),
        "changelog": changelog_result,
        "risk": risk_result,
        "tests": test_result,
        "spec": spec_result,
        "rollback_plan_markdown": rollback_plan,
    }


if __name__ == "__main__":
    import os
    import pprint

    repo   = os.path.join(os.path.dirname(__file__), "release-captain-target")
    since  = "763d71b"
    reqs   = os.path.join(repo, "requirements.md")

    print("Running release check …\n")
    report = run_release_check(
        repo_path=os.path.abspath(repo),
        since_ref=since,
        requirements_path=reqs,
        test_command=None,   # read from package.json
    )

    print(f"{'='*60}")
    print(f"VERDICT:          {report['verdict']}")
    print(f"Duration:         {report['duration_seconds']}s")
    print(f"Generated at:     {report['generated_at']}")
    print(f"{'='*60}\n")

    print("--- Changelog ---")
    print(f"  commit_count: {report['changelog'].get('commit_count')}")
    print(report['changelog'].get('release_notes_markdown', ''))

    print("\n--- Risk ---")
    print(f"  risk_level: {report['risk'].get('risk_level')}")
    print(report['risk'].get('summary_markdown', ''))

    print("\n--- Tests ---")
    t = report['tests']
    print(f"  passed={t.get('passed')}  total={t.get('total')}  failed={t.get('failed')}  duration={t.get('duration_seconds')}s")

    print("\n--- Spec ---")
    s = report['spec']
    print(f"  covered={s.get('covered')}/{s.get('total_requirements')}")
    for r in s.get('requirements', []):
        icon = "✅" if r['status'] == "covered" else ("⚠️ " if r['status'] == "unclear" else "❌")
        print(f"  {icon} {r['id']} [{r['status']}]  {r['text'][:60]}")

    print("\n--- Rollback Plan ---")
    print(report['rollback_plan_markdown'])

    print("\n--- Full report (JSON) ---")
    pprint.pprint(report)
