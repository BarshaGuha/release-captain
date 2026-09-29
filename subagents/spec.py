"""Spec subagent — requirement coverage check.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 5.
This is the "document understanding" subagent: it reads requirements.md
and checks each requirement against the actual code and tests.

CONTRACT
--------
Input:
    repo_path: str          — path to the target git repo
    requirements_path: str   — path to requirements.md inside that repo

Output (dict):
    {
        "total_requirements": int,
        "covered": int,
        "requirements": [
            {
                "id": str,             # e.g. "R2"
                "text": str,            # the requirement's own wording
                "status": "covered" | "not_covered" | "unclear",
                "evidence": str,        # file/test that shows it, or what's missing
            }
        ],
    }

Notes for Bob:
- Parse the checkbox list in requirements.md (`- [ ] **R1 — ...**`).
- For each requirement, look for matching code (routes, handlers) AND a
  test that exercises it — "covered" means both exist, not just one.
- Requirements explicitly marked out of scope in the doc are not counted
  against coverage.

PER-REPO CONFIGURATION
-----------------------
The requirement→code/test signal mapping is repo-specific by nature (it has
to name real files, function names, and patterns), so it is no longer
hardcoded to this project's original demo target. It is now read from a
config file:

    <repo_path>/.release-captain/spec-signals.json

with this shape:

    {
        "impl_file": "src/routes/todos.ts",   # relative to repo_path
        "test_file": "src/routes/todos.test.ts",
        "src_dir": "src",                      # used when a signal sets "impl_glob": true
        "static_check_requirement_id": "R8",   # optional: this one req gets a
                                                # suite-health check instead of
                                                # pattern matching (see _check_r8_static)
        "signals": {
            "R1": {
                "impl": "todos\\.get\\('/',",
                "test": "app\\.request\\('/api/todos'\\)",
                "impl_label": "todos.get('/', …) in todos.ts",
                "test_label": "app.request('/api/todos') in todos.test.ts",
                "impl_glob": false   # optional; search all files under src_dir instead
            },
            ...
        }
    }

If no such file exists in the target repo, spec.run() falls back to
_DEFAULT_CONFIG below — the exact mapping this project shipped with for its
own demo target (a Hono todos API) — so existing behavior is unchanged for
that repo. Any other repo without its own config will report every
requirement as "unclear: no config found for this repo", rather than
silently reusing signals that describe a different codebase.
"""

from __future__ import annotations

import copy
import json
import os
import re


# ---------------------------------------------------------------------------
# Default signal mapping — this project's own demo target only
#
# Kept as the fallback so a repo that hasn't been given its own
# .release-captain/spec-signals.json still behaves exactly as before.
# Each entry maps a requirement ID to a pair of search strings:
#   (impl_pattern, test_pattern)
#
# Both are regex patterns searched against the raw file text.
# R8 is special: instead of route strings we do a static assertion-count check.
# ---------------------------------------------------------------------------

# Patterns for todos.ts (the Hono router file).
# The router is mounted at /api/todos so its own handlers use relative paths:
#   '/'   ← GET list + POST create
#   '/:id' ← GET one, PUT update, DELETE remove
# requireApiKey appears in write handlers (R9).
# TodoDatabase / node:sqlite appears in db.ts (R10 — checked via db.ts).
#
# Patterns for todos.test.ts use the full paths that app.request() receives.

_DEFAULT_SIGNALS: dict[str, dict] = {
    "R1": {
        "impl": r"todos\.get\('/',",           # GET / handler in todos.ts
        "test": r"app\.request\('/api/todos'\)",  # bare /api/todos GET
        "impl_label": "todos.get('/', …) in todos.ts",
        "test_label": "app.request('/api/todos') in todos.test.ts",
    },
    "R2": {
        "impl": r"completed.*===.*want|c\.req\.query\('completed'\)",
        "test": r"completed=true",
        "impl_label": "completed filter logic in todos.ts",
        "test_label": "?completed=true query in todos.test.ts",
    },
    "R3": {
        "impl": r"todos\.get\('/:id',",
        "test": r"app\.request\('/api/todos/",
        "impl_label": "todos.get('/:id', …) in todos.ts",
        "test_label": "app.request('/api/todos/<id>') in todos.test.ts",
    },
    "R4": {
        "impl": r"todos\.post\('/',",
        "test": r"method:\s*'POST'",
        "impl_label": "todos.post('/', …) in todos.ts",
        "test_label": "method: 'POST' in todos.test.ts",
    },
    "R5": {
        "impl": r"input\.title.*trim\(\)|Title is required",
        "test": r"status,\s*400",
        "impl_label": "blank-title rejection in todos.ts",
        "test_label": "assert status 400 in todos.test.ts",
    },
    "R6": {
        "impl": r"todos\.put\('/:id',",
        "test": r"method:\s*'PUT'",
        "impl_label": "todos.put('/:id', …) in todos.ts",
        "test_label": "method: 'PUT' in todos.test.ts",
    },
    "R7": {
        "impl": r"todos\.delete\('/:id',",
        "test": r"method:\s*'DELETE'",
        "impl_label": "todos.delete('/:id', …) in todos.ts",
        "test_label": "method: 'DELETE' in todos.test.ts",
    },
    # R8 is handled separately — static check only.
    "R9": {
        "impl": r"requireApiKey",
        "test": r"x-api-key|401",
        "impl_label": "requireApiKey middleware in todos.ts",
        "test_label": "x-api-key / 401 assertions in todos.test.ts",
    },
    "R10": {
        # Implementation lives in db.ts, not todos.ts — check the whole src tree.
        "impl": r"TodoDatabase|node:sqlite",
        "test": r"TodoDatabase|survive.*restart|dbFile",
        "impl_label": "TodoDatabase / node:sqlite in src/",
        "test_label": "TodoDatabase persistence test in todos.test.ts",
        "impl_glob": True,   # search all .ts files under src/ instead of only todos.ts
    },
}

_DEFAULT_CONFIG: dict = {
    "impl_file": os.path.join("src", "routes", "todos.ts"),
    "test_file": os.path.join("src", "routes", "todos.test.ts"),
    "src_dir": "src",
    "static_check_requirement_id": "R8",
    "signals": _DEFAULT_SIGNALS,
}

_CONFIG_RELATIVE_PATH = os.path.join(".release-captain", "spec-signals.json")


def _load_spec_config(repo_path: str, config_path: str | None) -> tuple[dict, str]:
    """Resolve the signal-mapping config for repo_path.

    Precedence: an explicit config_path argument, then
    <repo_path>/.release-captain/spec-signals.json, then the bundled
    default (this project's own demo target only).

    Returns (config, source) where source is a short human-readable string
    recorded in the output for transparency about which mapping was used.
    """
    candidate = config_path
    if candidate is not None and not os.path.isabs(candidate):
        candidate = os.path.join(repo_path, candidate)

    if candidate is None:
        auto_path = os.path.join(repo_path, _CONFIG_RELATIVE_PATH)
        if os.path.isfile(auto_path):
            candidate = auto_path

    if candidate is not None and os.path.isfile(candidate):
        with open(candidate, encoding="utf-8") as fh:
            try:
                config = json.load(fh)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid spec-signals config at {candidate}: {e}") from e
        return config, candidate

    return copy.deepcopy(_DEFAULT_CONFIG), "built-in default (todos.ts demo target)"


def _read(path: str) -> str:
    """Return file text, or '' if the file doesn't exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _search_src(src_dir: str, pattern: str) -> bool:
    """Walk all .ts files under src_dir looking for pattern."""
    compiled = re.compile(pattern)
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if fname.endswith(".ts"):
                text = _read(os.path.join(root, fname))
                if compiled.search(text):
                    return True
    return False


def _check_r8_static(test_file_path: str) -> tuple[bool, str]:
    """
    R8 — 'test suite is real and green'.
    Static check: verify the test file is non-empty and contains real assert calls.
    Does NOT re-run the suite.
    Returns (ok: bool, evidence: str).
    """
    text = _read(test_file_path)
    if not text.strip():
        return False, "todos.test.ts is empty — no real test suite present"

    assert_count = len(re.findall(r"\bassert\b", text))
    if assert_count == 0:
        return False, "todos.test.ts contains no assert calls — not a real test suite"

    test_count = len(re.findall(r"\btest\(", text))
    return (
        True,
        (
            f"todos.test.ts is non-empty with {assert_count} assert call(s) "
            f"across {test_count} test() block(s) — real test suite confirmed (static check)"
        ),
    )


def _parse_requirements(req_text: str) -> list[dict]:
    """
    Parse checkbox items from requirements.md.
    Matches lines like:  - [ ] **R1 — Some text.**
    Skips 'Out of scope' lines and any other non-requirement lines.
    Returns list of {"id": "R1", "text": "..."}.
    """
    pattern = re.compile(
        r"-\s+\[[ xX]\]\s+\*\*(R\d+)\s*[—–-]+\s*(.*?)\*\*",
        re.IGNORECASE,
    )
    results = []
    for line in req_text.splitlines():
        # Skip out-of-scope notes
        if re.search(r"out of scope", line, re.IGNORECASE):
            continue
        m = pattern.search(line)
        if m:
            req_id = m.group(1).strip()
            # Include the rest of the line after the closing ** for the full text
            # (sometimes description continues after the bold span)
            bold_end = m.end()
            # Grab everything on the line after the ** that ended the match
            rest_of_line = line[bold_end:].strip().lstrip(".")
            full_text = m.group(2).strip()
            if rest_of_line:
                full_text = full_text + " " + rest_of_line
            results.append({"id": req_id, "text": full_text})
    return results


def run(repo_path: str, requirements_path: str, config_path: str | None = None) -> dict:
    """
    Check every requirement in requirements_path against the implementation
    and tests in repo_path.

    requirements_path may be an absolute path or a path relative to repo_path.

    config_path optionally names the signal-mapping JSON file to use (see
    the module docstring); if omitted, <repo_path>/.release-captain/
    spec-signals.json is used when present, else the bundled default for
    this project's own demo target.

    Returns the contract dict described in the module docstring, plus a
    "config_source" field naming which mapping was actually used.
    """
    config, config_source = _load_spec_config(repo_path, config_path)

    # Resolve relative requirements_path against repo_path so callers can
    # pass either an absolute path or a bare filename like "requirements.md".
    if not os.path.isabs(requirements_path):
        requirements_path = os.path.join(repo_path, requirements_path)
    req_md = _read(requirements_path)
    if not req_md:
        return {
            "total_requirements": 0,
            "covered": 0,
            "requirements": [],
            "config_source": config_source,
        }

    requirements_raw = _parse_requirements(req_md)

    impl_file = os.path.join(repo_path, config["impl_file"])
    test_file = os.path.join(repo_path, config["test_file"])
    src_dir = os.path.join(repo_path, config.get("src_dir", ""))
    static_check_id = config.get("static_check_requirement_id")
    signal_map = config.get("signals", {})

    impl_text = _read(impl_file)
    test_text = _read(test_file)

    results = []
    for req in requirements_raw:
        rid = req["id"]
        text = req["text"]

        # ---- Requirement designated for a static suite-health check --------
        if static_check_id is not None and rid == static_check_id:
            ok, evidence = _check_r8_static(test_file)
            results.append(
                {
                    "id": rid,
                    "text": text,
                    "status": "covered" if ok else "not_covered",
                    "evidence": evidence,
                }
            )
            continue

        # ---- All other requirements: route/handler + test check ------------
        signals = signal_map.get(rid)
        if signals is None:
            results.append(
                {
                    "id": rid,
                    "text": text,
                    "status": "unclear",
                    "evidence": (
                        f"No signal mapping defined for {rid} in {config_source}"
                    ),
                }
            )
            continue

        impl_pattern = signals["impl"]
        test_pattern = signals["test"]
        use_glob = signals.get("impl_glob", False)

        if use_glob:
            impl_found = _search_src(src_dir, impl_pattern)
        else:
            impl_found = bool(re.search(impl_pattern, impl_text))

        test_found = bool(re.search(test_pattern, test_text))

        if impl_found and test_found:
            status = "covered"
            evidence = (
                f"impl: {signals['impl_label']}; "
                f"test: {signals['test_label']}"
            )
        elif impl_found and not test_found:
            status = "unclear"
            evidence = (
                f"impl found ({signals['impl_label']}) "
                f"but no matching test ({signals['test_label']} not found)"
            )
        elif not impl_found and test_found:
            status = "unclear"
            evidence = (
                f"test found ({signals['test_label']}) "
                f"but no matching impl ({signals['impl_label']} not found)"
            )
        else:
            status = "not_covered"
            evidence = (
                f"impl not found ({signals['impl_label']}); "
                f"test not found ({signals['test_label']})"
            )

        results.append(
            {
                "id": rid,
                "text": text,
                "status": status,
                "evidence": evidence,
            }
        )

    covered_count = sum(1 for r in results if r["status"] == "covered")
    return {
        "total_requirements": len(results),
        "covered": covered_count,
        "requirements": results,
        "config_source": config_source,
    }


if __name__ == "__main__":
    import sys
    import json

    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    reqs = sys.argv[2] if len(sys.argv) > 2 else os.path.join(repo, "requirements.md")

    result = run(repo, reqs)

    print(f"\n{'='*60}")
    print(f"Spec check: {result['covered']}/{result['total_requirements']} requirements covered")
    print(f"{'='*60}\n")
    for r in result["requirements"]:
        icon = "✅" if r["status"] == "covered" else ("⚠️ " if r["status"] == "unclear" else "❌")
        print(f"{icon} {r['id']} [{r['status']}]  {r['text'][:70]}")
        print(f"     → {r['evidence']}")
        print()

    print("\nFull JSON output:")
    print(json.dumps(result, indent=2))
