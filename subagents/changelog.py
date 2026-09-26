"""Changelog subagent.

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

Notes:
- Read commits with `git log <since_ref>..HEAD --oneline`.
- Classify each commit by its message (feat:/fix:/chore: prefixes if
  present, otherwise a best-effort guess from the summary text).
- Never invent a commit or a change that isn't in `git log`.
"""

from __future__ import annotations

import subprocess


_FEAT_KEYWORDS = {"add", "added", "adds", "implement", "implements", "implemented",
                  "introduce", "introduces", "introduced", "new", "create", "creates",
                  "created", "support", "supports", "supported", "enable", "enables"}
_FIX_KEYWORDS  = {"fix", "fixes", "fixed", "bug", "repair", "repairs", "repaired",
                  "correct", "corrects", "corrected", "resolve", "resolves", "resolved",
                  "patch", "patches", "patched"}
_CHORE_KEYWORDS = {"chore", "update", "updates", "updated", "upgrade", "upgrades",
                   "upgraded", "bump", "bumps", "bumped", "refactor", "refactors",
                   "refactored", "clean", "cleans", "cleaned", "lint", "format",
                   "formats", "formatted", "deps", "dependencies", "dependency",
                   "ci", "cd", "test", "tests", "doc", "docs", "documentation",
                   "style", "styles", "styled", "remove", "removes", "removed",
                   "delete", "deletes", "deleted"}


def _classify(message: str) -> str:
    """Classify a commit message into feature | fix | chore | other."""
    lower = message.lower()

    # Conventional-commit prefixes take priority
    if lower.startswith(("feat:", "feat(", "feature:", "feature(")):
        return "feature"
    if lower.startswith(("fix:", "fix(", "bugfix:", "bugfix(")):
        return "fix"
    if lower.startswith(("chore:", "chore(", "refactor:", "refactor(", "docs:",
                          "docs(", "style:", "style(", "test:", "test(", "ci:",
                          "ci(", "build:", "build(")):
        return "chore"

    # Best-effort keyword scan on the first word of the message
    first_word = lower.split()[0].rstrip("s").rstrip("ed") if lower.split() else ""
    words = {w.strip(",:;.!?()[]") for w in lower.split()}

    if first_word in _FIX_KEYWORDS or words & _FIX_KEYWORDS:
        return "fix"
    if first_word in _FEAT_KEYWORDS or words & _FEAT_KEYWORDS:
        return "feature"
    if first_word in _CHORE_KEYWORDS or words & _CHORE_KEYWORDS:
        return "chore"

    return "other"


def run(repo_path: str, since_ref: str) -> dict:
    """Run the changelog subagent.

    Parameters
    ----------
    repo_path : str
        Filesystem path to the target git repository.
    since_ref : str
        Git ref (commit hash, tag, or branch) to start the range from
        (exclusive).  Commits from *since_ref* (exclusive) to HEAD
        (inclusive) are reported.

    Returns
    -------
    dict with keys:
        release_notes_markdown : str
        commit_count : int
        commits : list[dict]  — each has keys: hash, summary, kind
    """
    result = subprocess.run(
        ["git", "log", f"{since_ref}..HEAD", "--oneline"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    commits = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        short_hash, _, summary = line.partition(" ")
        kind = _classify(summary)
        commits.append({"hash": short_hash, "summary": summary, "kind": kind})

    # Group by kind for the markdown
    groups: dict[str, list[dict]] = {
        "feature": [],
        "fix":     [],
        "chore":   [],
        "other":   [],
    }
    for c in commits:
        groups[c["kind"]].append(c)

    sections = []
    label_map = {
        "feature": "## Features",
        "fix":     "## Bug Fixes",
        "chore":   "## Chores",
        "other":   "## Other",
    }
    for kind in ("feature", "fix", "chore", "other"):
        if groups[kind]:
            lines = [label_map[kind]]
            for c in groups[kind]:
                lines.append(f"- `{c['hash']}` {c['summary']}")
            sections.append("\n".join(lines))

    release_notes_markdown = "\n\n".join(sections) if sections else "_No commits found._"

    return {
        "release_notes_markdown": release_notes_markdown,
        "commit_count": len(commits),
        "commits": commits,
    }


if __name__ == "__main__":
    import os
    import pprint

    repo = os.path.join(os.path.dirname(__file__), "..", "release-captain-target")
    since = "763d71b"
    output = run(os.path.abspath(repo), since)
    pprint.pprint(output)
    print("\n--- Release Notes ---")
    print(output["release_notes_markdown"])
