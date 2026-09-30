"""Release Captain — GitHub PR comment formatter and poster.

Standalone module: takes an orchestrator report dict and posts (or updates)
a formatted markdown comment on a GitHub pull request.

Kept separate from orchestrator.py so it:
  - Can be tested without a live GitHub token
  - Can be reused from any CI system, not just GitHub Actions

CLI usage (called by the GitHub Action):
    python3 integrations/github_comment.py \
        --repo owner/repo --pr 42 --token ghp_xxx

Environment variables (used by the Action; --flag args take precedence):
    RC_REPO_PATH        path to the checked-out target repo
    RC_SINCE_REF        base SHA of the PR (github.event.pull_request.base.sha)
    RC_REQUIREMENTS     path (or filename) of requirements.md inside the repo
    GITHUB_TOKEN        GitHub token with pull-requests: write permission
    RC_PR_REPO          GitHub repo slug (owner/repo)
    RC_PR_NUMBER        pull request number (integer)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

# HTML marker placed as the very first line of every comment so we can
# find-and-update rather than spam new comments on every push.
_MARKER = "<!-- release-captain -->"

_GITHUB_API = "https://api.github.com"

# Emoji verdicts
_VERDICT_BANNER = {
    "GO":               "✅ GO — ready to ship",
    "GO-WITH-WARNINGS": "⚠️ GO-WITH-WARNINGS — review warnings before shipping",
    "NO-GO":            "❌ NO-GO — blocking issues found",
}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_comment(report: dict) -> str:
    """Format an orchestrator report dict into a GitHub markdown comment.

    Returns a string whose very first line is ``<!-- release-captain -->``
    so ``post_or_update_comment`` can find and overwrite it on subsequent
    pushes.
    """
    verdict  = report.get("verdict", "NO-GO")
    duration = report.get("duration_seconds", 0)
    banner   = _VERDICT_BANNER.get(verdict, f"❓ {verdict}")

    tests    = report.get("tests",     {})
    risk     = report.get("risk",      {})
    spec     = report.get("spec",      {})
    warnings = report.get("warnings",  [])
    rollback = report.get("rollback_plan_markdown", "")
    cl       = report.get("changelog", {})

    lines: list[str] = []

    # ── Marker (invisible to readers) ──────────────────────────────────────
    lines.append(_MARKER)
    lines.append("")

    # ── Verdict banner ─────────────────────────────────────────────────────
    lines.append(f"## {banner}  _(ran in {duration:.1f}s)_")
    lines.append("")

    # ── Summary table ──────────────────────────────────────────────────────
    total_tests  = tests.get("total",  0)
    failed_tests = tests.get("failed", 0)
    passed_flag  = tests.get("passed", False)
    test_icon    = "✅" if passed_flag else "❌"
    if "error" in tests:
        test_cell = f"❌ error — {tests['error'][:60]}"
    elif passed_flag:
        test_cell = f"✅ {total_tests} passed"
    else:
        test_cell = f"❌ {failed_tests} failed / {total_tests} total"

    risk_level = risk.get("risk_level", "unknown")
    risk_icon  = {"low": "✅", "medium": "⚠️", "high": "❌"}.get(risk_level, "❓")
    if "error" in risk:
        risk_cell = f"❌ error — {risk['error'][:60]}"
    else:
        risk_cell = f"{risk_icon} {risk_level.upper()}"

    total_reqs  = spec.get("total_requirements", 0)
    covered     = spec.get("covered", 0)
    spec_icon   = "✅" if covered == total_reqs and total_reqs > 0 else ("⚠️" if covered > 0 else "❌")
    if "error" in spec:
        spec_cell = f"❌ error — {spec['error'][:60]}"
    elif total_reqs == 0:
        spec_cell = "⚠️ no requirements file"
    else:
        spec_cell = f"{spec_icon} {covered}/{total_reqs} covered"

    warn_count = len(warnings)
    warn_cell  = f"⚠️ {warn_count}" if warn_count else "✅ none"

    lines.append("| Check | Result |")
    lines.append("|-------|--------|")
    lines.append(f"| 🧪 Tests      | {test_cell} |")
    lines.append(f"| 🔒 Risk       | {risk_cell} |")
    lines.append(f"| 📋 Spec       | {spec_cell} |")
    lines.append(f"| ⚠️ Warnings   | {warn_cell} |")
    lines.append("")

    # ── Collapsible details blocks ──────────────────────────────────────────

    # Changelog
    cl_notes = cl.get("release_notes_markdown", "")
    cl_count = cl.get("commit_count", 0)
    if "error" in cl:
        cl_body = f"❌ Changelog subagent error: {cl['error']}"
    elif cl_count == 0:
        cl_body = "_No commits found in range._"
    else:
        cl_body = cl_notes or f"_{cl_count} commit(s), no notes generated._"
    lines.append("<details>")
    lines.append(f"<summary>📋 Changelog ({cl_count} commit(s))</summary>")
    lines.append("")
    lines.append(cl_body)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Risk findings
    risk_findings = risk.get("findings", [])
    risk_summary  = risk.get("summary_markdown", "")
    if "error" in risk:
        risk_body = f"❌ Risk subagent error: {risk['error']}"
    elif risk_summary:
        risk_body = risk_summary
    else:
        risk_body = "_No dependency changes detected._"
    lines.append("<details>")
    lines.append(f"<summary>🔒 Dependency Risk ({len(risk_findings)} finding(s))</summary>")
    lines.append("")
    lines.append(risk_body)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Spec / requirements
    req_list = spec.get("requirements", [])
    if "error" in spec:
        spec_body = f"❌ Spec subagent error: {spec['error']}"
    elif not req_list:
        spec_body = "_No requirements evaluated._"
    else:
        req_rows = ["| ID | Requirement | Status |", "|-----|-------------|--------|"]
        for r in req_list:
            icon = {"covered": "✅", "unclear": "⚠️", "not_covered": "❌"}.get(r.get("status", ""), "❓")
            req_text = r.get("text", "")[:80].replace("|", "\\|")
            req_rows.append(f"| {r.get('id','')} | {req_text} | {icon} {r.get('status','')} |")
        spec_body = "\n".join(req_rows)
    lines.append("<details>")
    lines.append(f"<summary>📐 Spec Coverage ({covered}/{total_reqs} requirements)</summary>")
    lines.append("")
    lines.append(spec_body)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Test failures (only if there are any)
    failures = tests.get("failures", [])
    if failures:
        lines.append("<details>")
        lines.append(f"<summary>🔴 Test Failures ({len(failures)})</summary>")
        lines.append("")
        for f in failures:
            name = f.get("name", "unknown")
            msg  = f.get("message", "")
            lines.append(f"- **{name}**" + (f": {msg}" if msg else ""))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── Warnings table (only when non-empty) ────────────────────────────────
    if warnings:
        lines.append("### ⚠️ Non-Blocking Warnings")
        lines.append("")
        lines.append("| Source | ID | Description | Owner | Expiry |")
        lines.append("|--------|----|-------------|-------|--------|")
        for w in warnings:
            desc = w.get("description", "")[:60].replace("|", "\\|")
            lines.append(
                f"| {w.get('source','')} | {w.get('id','')} | {desc} "
                f"| {w.get('owner','')} | {w.get('expiry','')} |"
            )
        lines.append("")

    # ── Rollback plan ────────────────────────────────────────────────────────
    lines.append("### 🔄 Rollback Plan")
    lines.append("")
    lines.append(rollback or "_Not available._")
    lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────────
    commit = report.get("repo_commit", "unknown")
    run_id = report.get("run_id", "")
    lines.append(f"---")
    lines.append(f"_Release Captain · commit `{commit[:12]}` · run `{run_id[:8]}`_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub API calls (urllib.request only — no third-party HTTP library)
# ---------------------------------------------------------------------------

def _api_request(url: str, token: str, method: str = "GET", body: dict | None = None) -> Any:
    """Make a GitHub REST API call and return the parsed JSON response."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":         "application/json",
            "User-Agent":           "release-captain/1.0",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def post_or_update_comment(repo: str, pr_number: int, token: str, body: str) -> None:
    """Create or update the Release Captain comment on a GitHub PR.

    Searches existing comments for one whose body starts with the marker
    ``<!-- release-captain -->``.  If found, PATCHes it; otherwise POSTs a
    new one.  Uses only ``urllib.request`` from the stdlib.
    """
    base = f"{_GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"

    # Collect all existing comments (GitHub paginates at 30 by default)
    existing_id: int | None = None
    page = 1
    while True:
        url = f"{base}?per_page=100&page={page}"
        comments = _api_request(url, token)
        if not isinstance(comments, list) or not comments:
            break
        for c in comments:
            if isinstance(c.get("body"), str) and c["body"].startswith(_MARKER):
                existing_id = c["id"]
                break
        if existing_id is not None or len(comments) < 100:
            break
        page += 1

    if existing_id is not None:
        patch_url = f"{_GITHUB_API}/repos/{repo}/issues/comments/{existing_id}"
        _api_request(patch_url, token, method="PATCH", body={"body": body})
        print(f"[release-captain] Updated comment {existing_id} on PR #{pr_number}", file=sys.stderr)
    else:
        _api_request(base, token, method="POST", body={"body": body})
        print(f"[release-captain] Posted new comment on PR #{pr_number}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point (used directly by the GitHub Actions workflow)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    # Locate the release-captain package root (two levels up from this file)
    _rc_root = str(Path(__file__).resolve().parent.parent)
    if _rc_root not in sys.path:
        sys.path.insert(0, _rc_root)

    from orchestrator import run_release_check  # noqa: E402

    parser = argparse.ArgumentParser(description="Run Release Captain and post a PR comment.")
    parser.add_argument("--repo",  default=os.environ.get("RC_PR_REPO"),    help="owner/repo slug")
    parser.add_argument("--pr",    default=os.environ.get("RC_PR_NUMBER"),  help="PR number")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"),  help="GitHub token")
    args = parser.parse_args()

    repo_slug = args.repo
    pr_number = int(args.pr)
    token     = args.token

    if not repo_slug or not pr_number or not token:
        print(
            "ERROR: --repo, --pr, and --token (or RC_PR_REPO / RC_PR_NUMBER / GITHUB_TOKEN) are required.",
            file=sys.stderr,
        )
        sys.exit(1)

    repo_path    = os.environ.get("RC_REPO_PATH", ".")
    since_ref    = os.environ.get("RC_SINCE_REF", "HEAD~1")
    requirements = os.environ.get("RC_REQUIREMENTS", "requirements.md")

    print(
        f"[release-captain] Running check: repo={repo_path} since={since_ref} reqs={requirements}",
        file=sys.stderr,
    )

    report  = run_release_check(
        repo_path=repo_path,
        since_ref=since_ref,
        requirements_path=requirements,
    )
    comment = format_comment(report)

    print(f"[release-captain] Verdict: {report['verdict']}", file=sys.stderr)

    post_or_update_comment(repo_slug, pr_number, token, comment)

    # Always exit 0 — the comment is informational; CI gating is ci.yml's job.
    sys.exit(0)
