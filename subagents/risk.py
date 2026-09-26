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
import re
import subprocess
from pathlib import Path


def _parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse a semver string (with optional leading ^/~/v) into (major, minor, patch)."""
    cleaned = re.sub(r"^[\^~v]", "", version_str.strip())
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(re.sub(r"[^0-9].*", "", parts[2])) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return (0, 0, 0)
    return (major, minor, patch)


def _git_show_json(repo_path: str, ref: str, filepath: str) -> dict | None:
    """Return parsed JSON of a file at a given git ref, or None if not found."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{filepath}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _collect_deps(manifest: dict) -> dict[str, str]:
    """Merge dependencies and devDependencies into a single name->version_range dict."""
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        deps.update(manifest.get(section, {}))
    return deps


def _resolved_versions(lock: dict) -> dict[str, str]:
    """
    Extract resolved (installed) version for each direct dependency from
    package-lock.json v2/v3 (packages field).  Returns name->version.
    """
    packages = lock.get("packages", {})
    result: dict[str, str] = {}
    for key, info in packages.items():
        if not key.startswith("node_modules/"):
            continue
        name = key[len("node_modules/"):]
        # Skip nested/scoped nested packages (e.g. node_modules/a/node_modules/b)
        if "/" in name and not name.startswith("@"):
            continue
        # For scoped packages like @scope/pkg there is exactly one slash
        if name.count("/") > 1:
            continue
        result[name] = info.get("version", "")
    return result


def run(repo_path: str, since_ref: str) -> dict:
    """
    Diff package.json and package-lock.json between since_ref and HEAD.
    Flag major-version bumps and new dependencies as medium risk.
    """
    repo = str(Path(repo_path).resolve())

    # --- declared ranges from package.json ---
    old_manifest = _git_show_json(repo, since_ref, "package.json") or {}
    new_manifest_path = Path(repo) / "package.json"
    with open(new_manifest_path) as f:
        new_manifest = json.load(f)

    old_declared = _collect_deps(old_manifest)
    new_declared = _collect_deps(new_manifest)

    # --- resolved versions from package-lock.json ---
    old_lock = _git_show_json(repo, since_ref, "package-lock.json") or {}
    new_lock_path = Path(repo) / "package-lock.json"
    with open(new_lock_path) as f:
        new_lock = json.load(f)

    old_resolved = _resolved_versions(old_lock)
    new_resolved = _resolved_versions(new_lock)

    findings: list[dict] = []

    all_packages = set(new_declared) | set(old_declared)

    for pkg in sorted(all_packages):
        old_range = old_declared.get(pkg)
        new_range = new_declared.get(pkg)

        old_ver = old_resolved.get(pkg)
        new_ver = new_resolved.get(pkg)

        if old_range is None and new_range is not None:
            # Net-new dependency
            installed = new_ver or new_range
            findings.append({
                "package": pkg,
                "change": f"(not present) -> {installed} (new dependency)",
                "severity": "medium",
                "reason": "New dependency introduced; not present in previous release.",
            })

        elif old_range is not None and new_range is None:
            # Removed dependency — low risk, just note it
            findings.append({
                "package": pkg,
                "change": f"{old_ver or old_range} -> (removed)",
                "severity": "low",
                "reason": "Dependency removed; no inbound risk.",
            })

        else:
            # Present in both — compare resolved versions first, fall back to declared range
            effective_old = old_ver or old_range or ""
            effective_new = new_ver or new_range or ""

            if effective_old == effective_new:
                continue  # unchanged

            old_tuple = _parse_version(effective_old)
            new_tuple = _parse_version(effective_new)

            change_str = f"{effective_old} -> {effective_new}"

            if new_tuple[0] > old_tuple[0]:
                severity = "medium"
                label = "major bump"
                reason = (
                    f"Major version upgrade from {old_tuple[0]}.x to {new_tuple[0]}.x; "
                    "may contain breaking changes."
                )
            elif new_tuple < old_tuple:
                severity = "low"
                label = "downgrade"
                reason = f"Version downgraded from {effective_old} to {effective_new}."
            elif new_tuple[1] > old_tuple[1]:
                severity = "low"
                label = "minor bump"
                reason = "Minor version bump; backwards-compatible by semver convention."
            else:
                severity = "low"
                label = "patch bump"
                reason = "Patch version bump; bug-fix release."

            findings.append({
                "package": pkg,
                "change": f"{change_str} ({label})",
                "severity": severity,
                "reason": reason,
            })

    # Overall risk level is the highest severity found
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    if findings:
        risk_level = max((f["severity"] for f in findings), key=lambda s: severity_rank[s])
    else:
        risk_level = "low"

    # Build summary markdown
    if not findings:
        summary_markdown = (
            "**Dependency risk: LOW**\n\n"
            "No dependency changes detected between `{}` and HEAD.".format(since_ref)
        )
    else:
        lines = [
            f"**Dependency risk: {risk_level.upper()}**\n",
            f"Found {len(findings)} dependency change(s):\n",
        ]
        for f in findings:
            lines.append(f"- **{f['package']}**: {f['change']} — {f['reason']}")
        summary_markdown = "\n".join(lines)

    return {
        "risk_level": risk_level,
        "findings": findings,
        "summary_markdown": summary_markdown,
    }


if __name__ == "__main__":
    import sys
    import pprint

    repo = sys.argv[1] if len(sys.argv) > 1 else "../release-captain-target"
    ref = sys.argv[2] if len(sys.argv) > 2 else "763d71b"

    result = run(repo, ref)
    print(json.dumps(result, indent=2))
