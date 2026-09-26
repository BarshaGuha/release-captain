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

import json
import subprocess
import sys


def _run_git(repo_path: str, *args: str) -> str:
    """Run a git command inside repo_path and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _classify(summary: str) -> str:
    """Classify a commit summary into feature / fix / chore / other."""
    lower = summary.lower()

    # Conventional commit prefixes take priority
    if lower.startswith(("feat:", "feat(", "feature:", "feature(")):
        return "feature"
    if lower.startswith(("fix:", "fix(", "bugfix:", "bugfix(")):
        return "fix"
    if lower.startswith(("chore:", "chore(", "refactor:", "refactor(", "docs:", "docs(",
                          "test:", "test(", "ci:", "ci(", "build:", "build(",
                          "style:", "style(", "perf:", "perf(")):
        return "chore"

    # Heuristic fallback on keyword presence
    if any(w in lower for w in ("add", "new", "implement", "support", "introduce", "create", "filter")):
        return "feature"
    if any(w in lower for w in ("fix", "bug", "patch", "correct", "repair", "resolve", "revert")):
        return "fix"
    if any(w in lower for w in ("prep", "update", "bump", "upgrade", "clean", "refactor",
                                 "remove", "delete", "rename", "move", "format", "lint",
                                 "test", "spec", "doc", "readme", "changelog", "release")):
        return "chore"

    return "other"


def run(repo_path: str, since_ref: str) -> dict:
    """Run the changelog subagent and return the contract dict."""
    raw = _run_git(repo_path, "log", f"{since_ref}..HEAD", "--oneline")

    commits = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # `git log --oneline` format: "<hash> <summary>"
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        commit_hash, summary = parts[0], parts[1]
        commits.append({
            "hash": commit_hash,
            "summary": summary,
            "kind": _classify(summary),
        })

    # Group by kind
    groups: dict[str, list[dict]] = {
        "feature": [],
        "fix": [],
        "chore": [],
        "other": [],
    }
    for c in commits:
        groups[c["kind"]].append(c)

    # Build markdown — skip empty groups
    section_labels = {
        "feature": "## Features",
        "fix": "## Fixes",
        "chore": "## Chores",
        "other": "## Other",
    }
    sections: list[str] = []
    for kind, label in section_labels.items():
        if groups[kind]:
            lines = [label]
            for c in groups[kind]:
                lines.append(f"- {c['summary']} (`{c['hash']}`)")
            sections.append("\n".join(lines))

    release_notes_markdown = "\n\n".join(sections) if sections else "_No commits found in range._"

    return {
        "release_notes_markdown": release_notes_markdown,
        "commit_count": len(commits),
        "commits": commits,
    }


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    ref = sys.argv[2] if len(sys.argv) > 2 else "763d71b"
    output = run(repo, ref)
    print(json.dumps(output, indent=2))
