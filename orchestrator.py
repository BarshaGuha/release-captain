"""Release Captain orchestrator.

CONTRACT
--------
run_release_check(repo_path, since_ref, requirements_path, test_command) -> dict:
    {
        "verdict": "GO" | "GO-WITH-WARNINGS" | "NO-GO",
        "generated_at": str,          # ISO timestamp
        "duration_seconds": float,     # wall-clock time for this whole run
        "changelog": <output of subagents/changelog.py>,
        "risk": <output of subagents/risk.py>,
        "tests": <output of subagents/test.py>,
        "spec": <output of subagents/spec.py>,
        "rollback_plan_markdown": str,   # derived from the changelog
        "warnings": [                    # non-blocking findings, always disposed
            {
                "source": str,            # "risk" | "spec"
                "id": str,                # package name or requirement id
                "description": str,
                "owner": str,             # who is accountable for tracking this
                "rationale": str,
                "expiry": str,            # ISO date; disposition should be revisited by then
            }
        ],
        "run_id": str,        # stable id for this run, tying the record below to this dict
        "repo_commit": str,   # resolved HEAD sha of repo_path at run time ("unknown" if unresolvable)
    }

Every run is also persisted to disk (see _write_run_record) as
<history_dir>/<run_id>.json, plus one line appended to
<history_dir>/index.jsonl, so a past verdict can be looked up later by
run_id or commit instead of only existing for the lifetime of the Streamlit
session that produced it. This is a local, filesystem-backed audit trail —
adequate for development and for a single long-lived deployment, but note
that Streamlit Community Cloud's filesystem does not survive a redeploy, so
history written there is lost when the app restarts; swapping history_dir
for a persistent store (e.g. an S3 bucket or a small database) is the
natural next step once this runs somewhere longer-lived.

Verdict rule (conjunctive: any blocking condition forces NO-GO; nothing
compensates for anything else — a strong test run does not offset a missing
requirement):
    NO-GO (blocking) if: tests["passed"] is not True (False or None) OR
              risk["risk_level"] == "high" OR
              any requirement has status == "not_covered" OR
              any subagent raised an error (missing evidence is never a pass).

    Otherwise, if there are non-blocking findings — risk["risk_level"] ==
    "medium", or any requirement status == "unclear" — the verdict is
    GO-WITH-WARNINGS. Each such finding is recorded in "warnings" with an
    owner, rationale, and expiry, rather than either silently passing or
    forcing a disproportionate block. A warning never escalates itself to
    NO-GO — only the blocking conditions above do that.

    Otherwise GO.
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from subagents import changelog, risk, test, spec

# How long a recorded warning stands before its disposition should be
# revisited. Kept as a module constant so it's one place to tune, not a
# magic number buried in the verdict logic.
_WARNING_EXPIRY_DAYS = 14

# Default location for the run-history audit trail (see _write_run_record).
# Lives next to this file, not inside the target repo being checked, since
# the target repo is often an ephemeral clone.
_DEFAULT_HISTORY_DIR = Path(__file__).resolve().parent / "run_history"


def _read_test_command(repo_path: str) -> str:
    """Read scripts.test from the repo's package.json."""
    pkg_path = Path(repo_path) / "package.json"
    with open(pkg_path) as f:
        pkg = json.load(f)
    return pkg["scripts"]["test"]


def _resolve_commit(repo_path: str) -> str:
    """Return the resolved HEAD sha of repo_path, or 'unknown' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return "unknown"


def _write_run_record(report: dict, history_dir: Path) -> None:
    """Persist this run's full report to <history_dir>/<run_id>.json, and
    append a compact summary line to <history_dir>/index.jsonl for quick
    lookup without reading every record.

    Best-effort: a write failure (read-only filesystem, disk full) is logged
    to stderr and never raised — an audit trail that can't be written is a
    real gap, but it must never be the reason a release check itself fails.
    """
    try:
        history_dir.mkdir(parents=True, exist_ok=True)

        record_path = history_dir / f"{report['run_id']}.json"
        with open(record_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        # Derive compact summaries from subagent results for the index line
        # so the History tab can display meaningful info without opening each
        # full record file.
        _tests = report.get("tests", {})
        _risk  = report.get("risk", {})
        _spec  = report.get("spec", {})
        passed  = _tests.get("passed")
        total   = _tests.get("total", 0)
        failed  = _tests.get("failed", 0)
        test_summary = (
            f"{total} tests — {failed} failed"
            if not passed
            else f"{total} tests passed"
        )

        index_line = {
            "run_id":          report["run_id"],
            "generated_at":    report["generated_at"],
            "repo_commit":     report["repo_commit"],
            "branch":          report.get("branch", ""),
            "verdict":         report["verdict"],
            "warning_count":   len(report.get("warnings", [])),
            "duration_seconds": report.get("duration_seconds"),
            "risk_level":      _risk.get("risk_level", "unknown"),
            "test_summary":    test_summary,
            "spec_summary":    (
                f"{_spec.get('covered', '?')}/{_spec.get('total_requirements', '?')} requirements"
            ),
        }
        with open(history_dir / "index.jsonl", "a") as f:
            f.write(json.dumps(index_line) + "\n")
    except OSError as e:
        import sys
        print(f"[orchestrator] warning: could not write run history: {e}", file=sys.stderr)


def _compute_verdict(
    test_result: dict,
    risk_result: dict,
    spec_result: dict,
    generated_at: datetime,
) -> tuple[str, list[dict]]:
    """Pure verdict logic, kept separate from I/O so it's directly testable.

    Returns (verdict, warnings) where verdict is one of
    "GO" | "GO-WITH-WARNINGS" | "NO-GO".
    """
    tests_passed = test_result.get("passed")   # None if subagent errored
    risk_level   = risk_result.get("risk_level", "high")  # pessimistic default on error
    requirements = spec_result.get("requirements", [])

    # Blocking (conjunctive): any one of these forces NO-GO regardless of
    # how clean everything else is. Nothing here compensates for anything
    # else, and missing evidence (a subagent error) is never treated as a
    # pass.
    no_go = (
        tests_passed is not True          # False OR None → NO-GO
        or risk_level == "high"
        or any(r.get("status") == "not_covered" for r in requirements)
        or "error" in test_result         # subagent itself crashed
        or "error" in risk_result
        or "error" in spec_result
    )

    # Non-blocking (disposed, not silently dropped and not hard-blocked):
    # medium-severity dependency risk, and spec requirements the coverage
    # check couldn't confidently classify either way.
    expiry = (generated_at + timedelta(days=_WARNING_EXPIRY_DAYS)).date().isoformat()
    warnings: list[dict] = []

    if not no_go:
        for finding in risk_result.get("findings", []):
            if finding.get("severity") == "medium":
                warnings.append({
                    "source": "risk",
                    "id": finding.get("package", "unknown"),
                    "description": finding.get("change", ""),
                    "owner": "release-authority",
                    "rationale": finding.get("reason", ""),
                    "expiry": expiry,
                })

        for r in requirements:
            if r.get("status") == "unclear":
                warnings.append({
                    "source": "spec",
                    "id": r.get("id", "unknown"),
                    "description": r.get("text", ""),
                    "owner": "evaluation-owner",
                    "rationale": (
                        f"Coverage check could not confidently classify this "
                        f"requirement (evidence: {r.get('evidence', 'none')})."
                    ),
                    "expiry": expiry,
                })

    if no_go:
        verdict = "NO-GO"
    elif warnings:
        verdict = "GO-WITH-WARNINGS"
    else:
        verdict = "GO"

    return verdict, warnings


def _make_rollback_plan(changelog_result: dict, since_ref: str) -> str:
    """Build a one-sentence rollback summary from the changelog output."""
    commit_count = changelog_result.get("commit_count", 0)
    if commit_count == 0:
        return f"No commits since `{since_ref}`; nothing to roll back."
    return (
        f"To roll back this release, revert branch to `{since_ref}` "
        f"(undoes {commit_count} commit(s) merged since that ref)."
    )


def run_release_check(
    repo_path: str,
    since_ref: str,
    requirements_path: str,
    test_command: str | None = None,
    history_dir: str | Path | None = None,
    branch: str | None = None,
) -> dict:
    """Run all four subagents concurrently and return the full release report.

    Also persists the report to history_dir (default: run_history/ next to
    this file) so it can be looked up later by run_id or commit — see the
    module docstring's note on run_id/repo_commit/history persistence.

    branch, if supplied, is recorded in the index line so the History tab
    can display which branch was checked without opening the full record.
    """
    repo_path = str(Path(repo_path).resolve())

    # Resolve test command from package.json if not supplied
    if test_command is None:
        test_command = _read_test_command(repo_path)

    start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all four futures before calling .result() on any of them
        future_changelog = executor.submit(changelog.run, repo_path, since_ref)
        future_risk      = executor.submit(risk.run,      repo_path, since_ref)
        future_test      = executor.submit(test.run,      repo_path, test_command)
        future_spec      = executor.submit(spec.run,      repo_path, requirements_path)

        # Collect results — wrap each in try/except so one failure can't crash the run
        try:
            changelog_result = future_changelog.result()
        except Exception as e:
            changelog_result = {"error": str(e)}

        try:
            risk_result = future_risk.result()
        except Exception as e:
            risk_result = {"error": str(e)}

        try:
            test_result = future_test.result()
        except Exception as e:
            test_result = {"error": str(e)}

        try:
            spec_result = future_spec.result()
        except Exception as e:
            spec_result = {"error": str(e)}

    duration = time.perf_counter() - start
    generated_at = datetime.now(timezone.utc)

    verdict, warnings = _compute_verdict(test_result, risk_result, spec_result, generated_at)

    rollback_plan = _make_rollback_plan(changelog_result, since_ref)

    report = {
        "run_id": uuid.uuid4().hex,
        "verdict": verdict,
        "generated_at": generated_at.isoformat(),
        "repo_commit": _resolve_commit(repo_path),
        "branch": branch or "",
        "duration_seconds": round(duration, 3),
        "changelog": changelog_result,
        "risk": risk_result,
        "tests": test_result,
        "spec": spec_result,
        "rollback_plan_markdown": rollback_plan,
        "warnings": warnings,
    }

    resolved_history_dir = Path(history_dir) if history_dir is not None else _DEFAULT_HISTORY_DIR
    _write_run_record(report, resolved_history_dir)

    return report


if __name__ == "__main__":
    import os
    import pprint

    repo   = os.path.join(os.path.dirname(__file__), "release-captain-target")
    since  = "763d71b"
    reqs   = os.path.join(repo, "requirements.md")

    print("Running release check …\n")
    report = run_release_check(
        repo_path=os.path.abspath(repo),
        since_ref=since,
        requirements_path=reqs,
        test_command=None,   # read from package.json
    )

    print(f"{'='*60}")
    print(f"VERDICT:          {report['verdict']}")
    print(f"Duration:         {report['duration_seconds']}s")
    print(f"Generated at:     {report['generated_at']}")
    print(f"{'='*60}\n")

    print("--- Changelog ---")
    print(f"  commit_count: {report['changelog'].get('commit_count')}")
    print(report['changelog'].get('release_notes_markdown', ''))

    print("\n--- Risk ---")
    print(f"  risk_level: {report['risk'].get('risk_level')}")
    print(report['risk'].get('summary_markdown', ''))

    print("\n--- Tests ---")
    t = report['tests']
    print(f"  passed={t.get('passed')}  total={t.get('total')}  failed={t.get('failed')}  duration={t.get('duration_seconds')}s")

    print("\n--- Spec ---")
    s = report['spec']
    print(f"  covered={s.get('covered')}/{s.get('total_requirements')}")
    for r in s.get('requirements', []):
        icon = "✅" if r['status'] == "covered" else ("⚠️ " if r['status'] == "unclear" else "❌")
        print(f"  {icon} {r['id']} [{r['status']}]  {r['text'][:60]}")

    print("\n--- Rollback Plan ---")
    print(report['rollback_plan_markdown'])

    print("\n--- Warnings ---")
    if report['warnings']:
        for w in report['warnings']:
            print(f"  ⚠️  [{w['source']}] {w['id']}: {w['description']}")
            print(f"      owner={w['owner']}  expiry={w['expiry']}")
            print(f"      {w['rationale']}")
    else:
        print("  none")

    print("\n--- Full report (JSON) ---")
    pprint.pprint(report)
