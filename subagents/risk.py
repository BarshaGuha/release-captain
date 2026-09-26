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


def _strip_range(version: str) -> str:
    """Strip npm range prefixes (^, ~, >=, >, =) from a version string."""
    return version.lstrip("^~>=<").strip()


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver string into (major, minor, patch). Returns (0,0,0) on failure."""
    cleaned = _strip_range(version)
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2].split("-")[0]) if len(parts) > 2 else 0
        return major, minor, patch
    except (ValueError, IndexError):
        return 0, 0, 0


def _bump_type(old: str, new: str) -> str:
    """Return 'major', 'minor', 'patch', or 'unknown' for a version change."""
    old_v = _parse_semver(old)
    new_v = _parse_semver(new)
    if old_v == (0, 0, 0) or new_v == (0, 0, 0):
        return "unknown"
    if new_v[0] != old_v[0]:
        return "major"
    if new_v[1] != old_v[1]:
        return "minor"
    if new_v[2] != old_v[2]:
        return "patch"
    return "unchanged"


def _load_deps(pkg: dict) -> dict[str, str]:
    """Merge dependencies and devDependencies from a parsed package.json dict."""
    deps: dict[str, str] = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    return deps


def _load_lock_versions(lock: dict) -> dict[str, str]:
    """Extract resolved versions from a package-lock.json v2/v3 packages map."""
    versions: dict[str, str] = {}
    for key, info in (lock.get("packages") or {}).items():
        if key.startswith("node_modules/"):
            name = key[len("node_modules/"):]
            versions[name] = info.get("version", "")
    return versions


def _diff_deps(repo_path: str, since_ref: str) -> list[dict]:
    """Compare declared ranges and resolved lock versions between since_ref and HEAD."""
    findings: list[dict] = []

    # --- package.json: declared ranges ---
    try:
        old_pkg_raw = _run_git(repo_path, "show", f"{since_ref}:package.json")
        old_pkg = json.loads(old_pkg_raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return findings  # can't determine — report nothing rather than guessing

    with open(f"{repo_path}/package.json") as f:
        new_pkg = json.load(f)

    old_declared = _load_deps(old_pkg)
    new_declared = _load_deps(new_pkg)

    all_packages = set(old_declared) | set(new_declared)

    for pkg in sorted(all_packages):
        old_range = old_declared.get(pkg)
        new_range = new_declared.get(pkg)

        if old_range is None and new_range is not None:
            # Newly added dependency
            findings.append({
                "package": pkg,
                "change": f"added ({new_range})",
                "severity": "medium",
                "reason": f"New dependency {pkg} ({new_range}) has no prior review history in this repo.",
            })
        elif old_range is not None and new_range is None:
            # Removed dependency
            findings.append({
                "package": pkg,
                "change": f"removed (was {old_range})",
                "severity": "low",
                "reason": f"{pkg} was removed. Verify nothing still imports it.",
            })
        elif old_range != new_range:
            # Declared range changed
            bump = _bump_type(old_range, new_range)
            severity = "medium" if bump == "major" else "low"
            findings.append({
                "package": pkg,
                "change": f"{old_range} -> {new_range} ({bump} bump)",
                "severity": severity,
                "reason": (
                    f"Major version bump for {pkg}: review the changelog for breaking changes."
                    if bump == "major"
                    else f"Range updated for {pkg} from {old_range} to {new_range}."
                ),
            })

    # --- package-lock.json: resolved versions (catches transitive drift) ---
    try:
        old_lock_raw = _run_git(repo_path, "show", f"{since_ref}:package-lock.json")
        old_lock = json.loads(old_lock_raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        old_lock = {}

    try:
        with open(f"{repo_path}/package-lock.json") as f:
            new_lock = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        new_lock = {}

    if old_lock and new_lock:
        old_resolved = _load_lock_versions(old_lock)
        new_resolved = _load_lock_versions(new_lock)

        # Only flag direct deps whose resolved version changed (skip transitive noise)
        direct_pkgs = set(new_declared) | set(old_declared)
        for pkg in sorted(direct_pkgs):
            old_ver = old_resolved.get(pkg)
            new_ver = new_resolved.get(pkg)
            if old_ver and new_ver and old_ver != new_ver:
                # Only add if not already captured by the declared-range diff above
                already_flagged = any(f["package"] == pkg for f in findings)
                if not already_flagged:
                    bump = _bump_type(old_ver, new_ver)
                    severity = "medium" if bump == "major" else "low"
                    findings.append({
                        "package": pkg,
                        "change": f"{old_ver} -> {new_ver} (resolved, {bump} bump)",
                        "severity": severity,
                        "reason": (
                            f"Resolved version of {pkg} changed from {old_ver} to {new_ver}."
                            + (" Major bump — review for breaking changes." if bump == "major" else "")
                        ),
                    })

    return findings


def _overall_risk(findings: list[dict]) -> str:
    """Return the highest severity across all findings."""
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    if not findings:
        return "low"
    return max(findings, key=lambda f: severity_rank.get(f["severity"], 0))["severity"]


def _build_summary(findings: list[dict], risk_level: str) -> str:
    if not findings:
        return f"**Risk level: {risk_level.upper()}** — No dependency changes detected between the reference commit and HEAD."
    lines = [f"**Risk level: {risk_level.upper()}**\n"]
    for f in findings:
        lines.append(f"- **{f['package']}**: {f['change']} — {f['reason']}")
    return "\n".join(lines)


def run(repo_path: str, since_ref: str) -> dict:
    """Run the risk subagent and return the contract dict."""
    findings = _diff_deps(repo_path, since_ref)
    risk_level = _overall_risk(findings)
    summary_markdown = _build_summary(findings, risk_level)
    return {
        "risk_level": risk_level,
        "findings": findings,
        "summary_markdown": summary_markdown,
    }


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    ref = sys.argv[2] if len(sys.argv) > 2 else "763d71b"
    output = run(repo, ref)
    print(json.dumps(output, indent=2))
