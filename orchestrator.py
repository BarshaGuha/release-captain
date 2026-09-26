"""Release Captain orchestrator.

Build this with Bob 2.0 — see BOB_PROMPTS.md, prompt 6.
This is the "agent mode" piece: it runs the four subagents (in parallel,
via a thread/process pool — not one after another) and assembles their
output into one go/no-go report.

CONTRACT
--------
run_release_check(repo_path, since_ref, requirements_path, test_command) -> dict:
    {
        "verdict": "GO" | "NO-GO",
        "generated_at": str,          # ISO timestamp
        "duration_seconds": float,     # wall-clock time for this whole run —
                                        # this is the number that goes on the
                                        # pitch deck next to "<10 min"
        "changelog": <output of subagents/changelog.py>,
        "risk": <output of subagents/risk.py>,
        "tests": <output of subagents/test.py>,
        "spec": <output of subagents/spec.py>,
        "rollback_plan_markdown": str,   # derived from the changelog: what
                                          # to revert and how
    }

Verdict rule (keep this simple and explainable in the demo):
    NO-GO if: tests failed, OR risk_level == "high", OR any requirement is
    "not_covered". Otherwise GO.

Notes for Bob:
- Run the four subagents concurrently (e.g. concurrent.futures.ThreadPoolExecutor
  with 4 workers) and time the whole run with time.perf_counter() — that
  duration is the headline metric.
- Read test_command from the target repo's own package.json rather than
  hardcoding "npm test", so this works on other repos later too.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from subagents import changelog, risk, spec, test as test_agent


def _read_test_command(repo_path: str) -> str:
    """Read scripts.test from the repo's package.json."""
    pkg_path = os.path.join(repo_path, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    return pkg["scripts"]["test"]


def _build_rollback_plan(repo_path: str, since_ref: str, changelog_result: dict) -> str:
    """
    Build a short rollback summary sentence.
    Points at the branch and since_ref SHA to revert to; does not enumerate
    individual git-revert commands per commit.
    """
    # Try to get the current branch name
    try:
        import subprocess
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        branch = "current branch"

    commit_count = changelog_result.get("commit_count", 0)
    commits_phrase = f"{commit_count} commit(s)" if commit_count else "the changes"

    return (
        f"To roll back, revert `{branch}` to `{since_ref}` (before {commits_phrase} in this release). "
        f"Run: `git revert {since_ref}..HEAD` or force-reset with `git reset --hard {since_ref}` "
        f"and force-push if the branch has been published."
    )


def _verdict(tests_result: dict, risk_result: dict, spec_result: dict) -> str:
    """
    NO-GO if: tests failed, OR risk_level == "high", OR any requirement is "not_covered".
    Otherwise GO.
    """
    if not tests_result.get("passed", False):
        return "NO-GO"
    if risk_result.get("risk_level") == "high":
        return "NO-GO"
    requirements = spec_result.get("requirements", [])
    if any(r.get("status") == "not_covered" for r in requirements):
        return "NO-GO"
    return "GO"


def run_release_check(
    repo_path: str,
    since_ref: str,
    requirements_path: str,
    test_command: str | None = None,
) -> dict:
    """Run all four subagents concurrently and return the unified report."""
    if test_command is None:
        test_command = _read_test_command(repo_path)

    t_start = time.perf_counter()

    # Dispatch all four subagents simultaneously
    futures_map = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures_map["changelog"] = pool.submit(changelog.run, repo_path, since_ref)
        futures_map["risk"]      = pool.submit(risk.run,      repo_path, since_ref)
        futures_map["tests"]     = pool.submit(test_agent.run, repo_path, test_command)
        futures_map["spec"]      = pool.submit(spec.run,       repo_path, requirements_path)
        # Collect results — executor blocks here until all four are done
        results = {}
        for name, future in futures_map.items():
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {"error": str(exc), "subagent": name}

    duration_seconds = round(time.perf_counter() - t_start, 3)

    changelog_result = results["changelog"]
    risk_result      = results["risk"]
    tests_result     = results["tests"]
    spec_result      = results["spec"]

    verdict = _verdict(tests_result, risk_result, spec_result)
    rollback_plan = _build_rollback_plan(repo_path, since_ref, changelog_result)

    return {
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "changelog": changelog_result,
        "risk": risk_result,
        "tests": tests_result,
        "spec": spec_result,
        "rollback_plan_markdown": rollback_plan,
    }


if __name__ == "__main__":
    repo          = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    ref           = sys.argv[2] if len(sys.argv) > 2 else "763d71b"
    req_path      = sys.argv[3] if len(sys.argv) > 3 else "requirements.md"
    test_cmd      = sys.argv[4] if len(sys.argv) > 4 else None

    report = run_release_check(repo, ref, req_path, test_cmd)

    # ── human-readable summary ──────────────────────────────────────────────
    verdict = report["verdict"]
    icon = "✅" if verdict == "GO" else "❌"
    print(f"\n{icon}  VERDICT: {verdict}")
    print(f"   Generated : {report['generated_at']}")
    print(f"   Duration  : {report['duration_seconds']}s")

    print("\n── Changelog ──────────────────────────────────────────────────────")
    print(f"  {report['changelog'].get('commit_count', '?')} commit(s)")
    print(report['changelog'].get('release_notes_markdown', ''))

    print("\n── Risk ───────────────────────────────────────────────────────────")
    print(report['risk'].get('summary_markdown', ''))

    print("\n── Tests ──────────────────────────────────────────────────────────")
    t = report['tests']
    print(f"  passed={t.get('passed')}  total={t.get('total')}  "
          f"failed={t.get('failed')}  duration={t.get('duration_seconds')}s")

    print("\n── Spec Coverage ──────────────────────────────────────────────────")
    s = report['spec']
    print(f"  {s.get('covered')}/{s.get('total_requirements')} requirements covered")
    for r in s.get('requirements', []):
        icon2 = "✅" if r['status'] == 'covered' else ("⚠️ " if r['status'] == 'unclear' else "❌")
        print(f"  {icon2} {r['id']}: {r['status']}")

    print("\n── Rollback Plan ──────────────────────────────────────────────────")
    print(f"  {report['rollback_plan_markdown']}")

    print("\n── Full JSON ──────────────────────────────────────────────────────")
    print(json.dumps(report, indent=2))
