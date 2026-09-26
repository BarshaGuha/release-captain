"""Changelog subagent.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 2.

CONTRACT
--------
Input:
    repo_path: str   — path to the target git repo (e.g. release-captain-target)
    since_ref: str    — git ref to diff from (e.g. the previous release tag,
                         or the first commit if there's no tag yet)

Output (dict):
    {
        "release_notes_markdown": str,   # human-readable changelog, grouped
                                          # by feature / fix / chore
        "commit_count": int,
        "commits": [
            {"hash": str, "summary": str, "kind": "feature"|"fix"|"chore"|"other"}
        ],
    }

Notes for Bob:
- Read commits with `git log <since_ref>..HEAD --oneline` (and `--stat` if
  you need touched files).
- Classify each commit by its message (feat:/fix:/chore: prefixes if
  present, otherwise a best-effort guess from the summary text).
- Never invent a commit or a change that isn't in `git log`.
"""

from __future__ import annotations


def run(repo_path: str, since_ref: str) -> dict:
    raise NotImplementedError("Build this with Bob 2.0 — see BOB_PROMPTS.md")
