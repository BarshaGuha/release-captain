"""Spec subagent — requirement coverage check.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 5.
This is the "document understanding" subagent: it reads requirements.md
and checks each requirement against the actual code and tests.

CONTRACT
--------
Input:
    repo_path: str          — path to the target git repo
    requirements_path: str   — path to requirements.md inside that repo

Output (dict):
    {
        "total_requirements": int,
        "covered": int,
        "requirements": [
            {
                "id": str,             # e.g. "R2"
                "text": str,            # the requirement's own wording
                "status": "covered" | "not_covered" | "unclear",
                "evidence": str,        # file/test that shows it, or what's missing
            }
        ],
    }

Notes for Bob:
- Parse the checkbox list in requirements.md (`- [ ] **R1 — ...**`).
- For each requirement, look for matching code (routes, handlers) AND a
  test that exercises it — "covered" means both exist, not just one.
- Requirements explicitly marked out of scope in the doc are not counted
  against coverage.
"""

from __future__ import annotations


def run(repo_path: str, requirements_path: str) -> dict:
    raise NotImplementedError("Build this with Bob 2.0 — see BOB_PROMPTS.md")
