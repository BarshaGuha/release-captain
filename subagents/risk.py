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
- Diff package.json / package-lock.json for a Node target, or
  requirements.txt / pyproject.toml for a Python target, between since_ref
  and HEAD — whichever manifest the target repo actually has (see
  _detect_manifest_kind; the output's "manifest_kind" field says which one
  was used, and is "none" with an honest empty finding set if the repo has
  none of the three, rather than silently reporting a false "low").
- A major-version bump is at least "medium" risk; a new dependency with no
  prior review is "medium"; a patch/minor bump is "low" unless the changelog
  of that dependency mentions a breaking change.
- If nothing changed, return risk_level "low" with an empty findings list —
  don't invent risk to make the report look busier.

Severity escalation to "high" (added so the verdict's high-risk NO-GO path
is actually reachable, not just declared in the contract):
- A *production* dependency (declared under "dependencies", not
  "devDependencies") is removed entirely — code that imports it may break
  with no earlier warning.
- A *production* dependency jumps two or more major versions in one release
  (e.g. 3.x -> 6.x) — a single-major bump stays "medium"; skipping majors
  means intermediate breaking changes were never reviewed at all.
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


_GIT_SHOW_TIMEOUT_SECONDS = 30


def _git_show_text(repo_path: str, ref: str, filepath: str) -> str | None:
    """Return the raw text of a file at a given git ref, or None if not found."""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{filepath}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_GIT_SHOW_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"`git show {ref}:{filepath}` in {repo_path} did not finish within "
            f"{_GIT_SHOW_TIMEOUT_SECONDS}s."
        ) from e
    if result.returncode != 0:
        return None
    return result.stdout


def _git_show_json(repo_path: str, ref: str, filepath: str) -> dict | None:
    """Return parsed JSON of a file at a given git ref, or None if not found."""
    text = _git_show_text(repo_path, ref, filepath)
    if text is None:
        return None
    try:
        return json.loads(text)
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


def _classify_bump(old_tuple: tuple[int, int, int], new_tuple: tuple[int, int, int], is_prod: bool) -> tuple[str, str, str]:
    """Shared severity classification for a version change, used by every
    ecosystem (Node, pip, pyproject) so a major-version-skip in a Python
    target is treated exactly as seriously as one in a Node target."""
    if new_tuple[0] > old_tuple[0]:
        major_span = new_tuple[0] - old_tuple[0]
        if is_prod and major_span >= 2:
            return (
                "high",
                "major bump (skipped versions)",
                f"Production dependency jumped {major_span} major versions "
                f"({old_tuple[0]}.x to {new_tuple[0]}.x) in one release; "
                "intermediate breaking changes were never reviewed.",
            )
        return (
            "medium",
            "major bump",
            f"Major version upgrade from {old_tuple[0]}.x to {new_tuple[0]}.x; "
            "may contain breaking changes.",
        )
    if new_tuple < old_tuple:
        return ("low", "downgrade", f"Version downgraded.")
    if new_tuple[1] > old_tuple[1]:
        return ("low", "minor bump", "Minor version bump; backwards-compatible by semver convention.")
    return ("low", "patch bump", "Patch version bump; bug-fix release.")


def _removed_dependency_finding(pkg: str, old_display: str, is_prod: bool) -> dict:
    return {
        "package": pkg,
        "change": f"{old_display} -> (removed)",
        "severity": "high" if is_prod else "low",
        "reason": (
            "Production dependency removed; code that imports it "
            "will break unless this was verified."
        ) if is_prod else "Dev-only dependency removed; no inbound risk.",
    }


def _new_dependency_finding(pkg: str, new_display: str) -> dict:
    return {
        "package": pkg,
        "change": f"(not present) -> {new_display} (new dependency)",
        "severity": "medium",
        "reason": "New dependency introduced; not present in previous release.",
    }


def _detect_manifest_kind(repo: str) -> str:
    """Which dependency manifest this target repo actually uses.

    Checked in this order because a repo could technically have more than
    one (e.g. a Node app with a Python tooling script) — package.json wins
    if present, since that's this tool's most-exercised path; otherwise the
    first Python manifest found.
    """
    if (Path(repo) / "package.json").is_file():
        return "node"
    if (Path(repo) / "requirements.txt").is_file():
        return "pip"
    if (Path(repo) / "pyproject.toml").is_file():
        return "pyproject"
    return "none"


_REQUIREMENT_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==\s*([0-9][0-9A-Za-z.\-]*))?"
)


def _parse_requirements_txt(text: str) -> dict[str, str | None]:
    """Parse a pip requirements.txt into {name: pinned_version_or_None}.

    Only handles simple `name`, `name==1.2.3`, and range-specified lines
    (range specs are recorded as unpinned — None — since there's no single
    resolved version to diff without a lockfile); `-r other.txt` includes
    and `#` comments are skipped rather than mis-parsed as packages.
    """
    deps: dict[str, str | None] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        m = _REQUIREMENT_LINE_RE.match(stripped)
        if not m:
            continue
        name = m.group(1)
        pinned = m.group(3)  # None unless the line used "=="
        deps[name] = pinned
    return deps


def _diff_pip_requirements(repo: str, since_ref: str) -> list[dict]:
    """Diff requirements.txt between since_ref and HEAD. All entries are
    treated as production dependencies — pip's flat requirements.txt has no
    dev/prod distinction the way package.json does."""
    old_text = _git_show_text(repo, since_ref, "requirements.txt") or ""
    new_path = Path(repo) / "requirements.txt"
    new_text = new_path.read_text() if new_path.is_file() else ""

    old_deps = _parse_requirements_txt(old_text)
    new_deps = _parse_requirements_txt(new_text)

    return _diff_flat_dep_maps(old_deps, new_deps, all_prod=True)


def _load_pyproject_deps(text: str) -> dict[str, str | None] | None:
    """Extract {name: pinned_version_or_None} from a pyproject.toml's
    PEP 621 [project.dependencies] or Poetry's [tool.poetry.dependencies].
    Returns None if the file can't be parsed at all (no TOML parser
    available, or malformed TOML) so the caller can report that honestly
    instead of silently treating it as "no dependencies"."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return None
    try:
        data = tomllib.loads(text)
    except Exception:
        return None

    deps: dict[str, str | None] = {}

    project_deps = data.get("project", {}).get("dependencies", [])
    for entry in project_deps:
        # PEP 508 strings, e.g. "requests==2.31.0" or "requests>=2.0"
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==\s*([0-9][0-9A-Za-z.\-]*))?", entry)
        if m:
            deps[m.group(1)] = m.group(3)

    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, str):
            pinned = spec.lstrip("^~=") if re.match(r"^[\^~=]?\d", spec) else None
            deps[name] = pinned if re.match(r"^\d", pinned or "") else None
        # dict-form specs (git deps, extras, etc.) are left unpinned/None —
        # not enough of a single version to diff meaningfully.
        elif isinstance(spec, dict):
            deps.setdefault(name, None)

    return deps


def _diff_pyproject(repo: str, since_ref: str) -> tuple[list[dict], bool]:
    """Diff pyproject.toml between since_ref and HEAD.

    Returns (findings, parsed_ok). parsed_ok is False when no TOML parser
    was available or the file didn't parse — callers should report that as
    an honest gap, not as a clean "no changes" result.
    """
    old_text = _git_show_text(repo, since_ref, "pyproject.toml") or ""
    new_path = Path(repo) / "pyproject.toml"
    new_text = new_path.read_text() if new_path.is_file() else ""

    old_deps = _load_pyproject_deps(old_text) if old_text else {}
    new_deps = _load_pyproject_deps(new_text)

    if new_deps is None:
        return [], False

    return _diff_flat_dep_maps(old_deps or {}, new_deps, all_prod=True), True


def _diff_flat_dep_maps(
    old_deps: dict[str, str | None],
    new_deps: dict[str, str | None],
    all_prod: bool,
) -> list[dict]:
    """Shared diff logic for ecosystems with a single flat name->version map
    (no separate declared-range vs. resolved-lock distinction)."""
    findings: list[dict] = []
    for pkg in sorted(set(old_deps) | set(new_deps)):
        old_ver = old_deps.get(pkg)
        new_ver = new_deps.get(pkg)

        if pkg not in old_deps and pkg in new_deps:
            findings.append(_new_dependency_finding(pkg, new_ver or "unpinned"))
        elif pkg in old_deps and pkg not in new_deps:
            findings.append(_removed_dependency_finding(pkg, old_ver or "unpinned", all_prod))
        else:
            if not old_ver or not new_ver or old_ver == new_ver:
                continue  # unchanged, or can't compare without both pinned
            old_tuple = _parse_version(old_ver)
            new_tuple = _parse_version(new_ver)
            severity, label, reason = _classify_bump(old_tuple, new_tuple, all_prod)
            findings.append({
                "package": pkg,
                "change": f"{old_ver} -> {new_ver} ({label})",
                "severity": severity,
                "reason": reason,
            })
    return findings


def _diff_node_manifest(repo: str, since_ref: str) -> list[dict]:
    """Diff package.json and package-lock.json between since_ref and HEAD.
    Flag major-version bumps and new dependencies as medium risk."""
    # --- declared ranges from package.json ---
    old_manifest = _git_show_json(repo, since_ref, "package.json") or {}
    new_manifest_path = Path(repo) / "package.json"
    with open(new_manifest_path) as f:
        new_manifest = json.load(f)

    old_declared = _collect_deps(old_manifest)
    new_declared = _collect_deps(new_manifest)

    # Production-dependency membership, tracked separately so removal/bump
    # severity can tell a runtime dependency apart from a dev-only one.
    old_prod = set(old_manifest.get("dependencies", {}).keys())
    new_prod = set(new_manifest.get("dependencies", {}).keys())

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
            findings.append(_new_dependency_finding(pkg, new_ver or new_range))

        elif old_range is not None and new_range is None:
            was_prod = pkg in old_prod
            findings.append(_removed_dependency_finding(pkg, old_ver or old_range, was_prod))

        else:
            # Present in both — compare resolved versions first, fall back to declared range
            effective_old = old_ver or old_range or ""
            effective_new = new_ver or new_range or ""

            if effective_old == effective_new:
                continue  # unchanged

            old_tuple = _parse_version(effective_old)
            new_tuple = _parse_version(effective_new)
            is_prod = pkg in old_prod or pkg in new_prod

            severity, label, reason = _classify_bump(old_tuple, new_tuple, is_prod)
            findings.append({
                "package": pkg,
                "change": f"{effective_old} -> {effective_new} ({label})",
                "severity": severity,
                "reason": reason,
            })

    return findings


def run(repo_path: str, since_ref: str) -> dict:
    """
    Diff the target repo's dependency manifest between since_ref and HEAD —
    package.json/package-lock.json for Node, requirements.txt or
    pyproject.toml for Python — whichever the repo actually has.
    """
    repo = str(Path(repo_path).resolve())
    manifest_kind = _detect_manifest_kind(repo)

    parsed_ok = True
    if manifest_kind == "node":
        findings = _diff_node_manifest(repo, since_ref)
    elif manifest_kind == "pip":
        findings = _diff_pip_requirements(repo, since_ref)
    elif manifest_kind == "pyproject":
        findings, parsed_ok = _diff_pyproject(repo, since_ref)
    else:
        findings = []

    # Overall risk level is the highest severity found. A pyproject.toml
    # that couldn't be parsed (no TOML library available, or malformed
    # file) is reported as "unknown" evidence rather than a clean "low" —
    # missing evidence is never a pass.
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    if not parsed_ok:
        risk_level = "high"
    elif findings:
        risk_level = max((f["severity"] for f in findings), key=lambda s: severity_rank[s])
    else:
        risk_level = "low"

    # Build summary markdown
    if manifest_kind == "none":
        summary_markdown = (
            "**Dependency risk: LOW (unverified)**\n\n"
            "No package.json, requirements.txt, or pyproject.toml found in "
            "this repo — dependency risk could not actually be checked."
        )
    elif not parsed_ok:
        summary_markdown = (
            "**Dependency risk: HIGH (unverified)**\n\n"
            "pyproject.toml is present but could not be parsed (no TOML "
            "parser available, or the file is malformed) — dependency risk "
            "could not be checked, which is treated as high risk rather "
            "than silently passing."
        )
    elif not findings:
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
        "manifest_kind": manifest_kind,
    }


if __name__ == "__main__":
    import sys
    import pprint

    repo = sys.argv[1] if len(sys.argv) > 1 else "../release-captain-target"
    ref = sys.argv[2] if len(sys.argv) > 2 else "763d71b"

    result = run(repo, ref)
    print(json.dumps(result, indent=2))
