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


def run_release_check(
    repo_path: str,
    since_ref: str,
    requirements_path: str,
    test_command: str,
) -> dict:
    raise NotImplementedError("Build this with Bob 2.0 — see BOB_PROMPTS.md")
