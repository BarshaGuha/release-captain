"""Test subagent — run the suite and summarize results.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 4.

CONTRACT
--------
Input:
    repo_path: str        — path to the target git repo
    test_command: str      — the command to run (read it from that repo's
                              package.json "scripts.test", don't hardcode it)

Output (dict):
    {
        "passed": bool,
        "total": int,
        "failed": int,
        "duration_seconds": float,
        "failures": [
            {"name": str, "message": str}
        ],
        "raw_output_tail": str,   # last ~40 lines, for the report's detail view
    }

Notes for Bob:
- Actually run the command (subprocess), don't simulate it.
- Node's built-in test runner (`node --test`) prints TAP-ish output — parse
  pass/fail counts from it; don't assume a specific framework's format
  since the target repo could change.
- If the command exits non-zero but produces no parseable summary, still
  return passed=False with whatever detail is available in raw_output_tail.
"""

from __future__ import annotations


def run(repo_path: str, test_command: str) -> dict:
    raise NotImplementedError("Build this with Bob 2.0 — see BOB_PROMPTS.md")
