"""Risk subagent — dependency and breaking-change check.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 3.

CONTRACT
--------
Input:
    repo_path: str   — path to the target git repo
    since_ref: str    — git ref to diff from

Output (dict):
    {
        "risk_level": "low" | "medium" | "high",
        "findings": [
            {
                "package": str,
                "change": str,        # e.g. "3.1.0 -> 4.0.0 (major bump)"
                "severity": "low" | "medium" | "high",
                "reason": str,        # plain-English why this is flagged
            }
        ],
        "summary_markdown": str,
    }

Notes for Bob:
- Diff package.json / package-lock.json (or requirements.txt / pyproject.toml
  for a Python target) between since_ref and HEAD.
- A major-version bump is at least "medium" risk; a new dependency with no
  prior review is "medium"; a patch/minor bump is "low" unless the changelog
  of that dependency mentions a breaking change.
- If nothing changed, return risk_level "low" with an empty findings list —
  don't invent risk to make the report look busier.
"""

from __future__ import annotations


def run(repo_path: str, since_ref: str) -> dict:
    raise NotImplementedError("Build this with Bob 2.0 — see BOB_PROMPTS.md")
