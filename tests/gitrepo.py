"""Test helper: build a tiny throwaway git repo with two commits, so
subagents that diff `since_ref..HEAD` (risk.py, spec.py's config lookup,
changelog.py) can be tested against real git history without depending on
any external repo (in particular, without depending on the sibling
release-captain-target clone, which isn't part of this repository and may
not exist wherever these tests run).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def make_repo(tmp_path: Path, base_files: dict[str, str], head_files: dict[str, str]) -> tuple[str, str]:
    """Create a git repo at tmp_path with two commits:
      1. "base"  — writes base_files
      2. "head"  — writes head_files (files not repeated are left as-is;
                   pass the full desired content for any file that changes)

    Returns (repo_path, base_commit_sha).
    """
    repo = tmp_path
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)

    _write_files(repo, base_files)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base_sha = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    if head_files:
        _write_files(repo, head_files)
        _run(["git", "add", "-A"], repo)
        _run(["git", "commit", "-q", "-m", "head"], repo)

    return str(repo), base_sha


def _write_files(repo: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"{' '.join(cmd)} failed: {result.stderr}"
    return result
