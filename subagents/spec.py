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
"""

from __future__ import annotations

import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Requirements parsing
# ---------------------------------------------------------------------------

def _parse_requirements(requirements_path: str) -> list[dict]:
    """
    Parse checkbox items from requirements.md up to (but not including)
    any "Out of scope" line.  Returns a list of {"id": str, "text": str}.
    """
    with open(requirements_path) as f:
        content = f.read()

    requirements = []
    for line in content.splitlines():
        # Stop at out-of-scope section
        if re.search(r"out of scope", line, re.IGNORECASE):
            break
        # Match: - [ ] **R<n> — <text>**  (checked or unchecked)
        m = re.match(r"^\s*-\s+\[[ xX]\]\s+\*\*(R\d+)\s+[—–-]+\s+(.+?)\*\*(.*)$", line)
        if m:
            req_id = m.group(1)
            title = m.group(2).strip()
            rest = m.group(3).strip().lstrip(".")
            text = (title + (" " + rest if rest else "")).strip()
            requirements.append({"id": req_id, "text": text})

    return requirements


# ---------------------------------------------------------------------------
# Evidence helpers — all operate on file contents, no subprocesses
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _has_code(source: str, *patterns: str) -> tuple[bool, str]:
    """
    Return (True, first_matching_line_ref) if ANY pattern matches in source.
    patterns are plain strings; matching is substring (case-sensitive).
    """
    lines = source.splitlines()
    for pattern in patterns:
        for i, line in enumerate(lines, 1):
            if pattern in line:
                return True, f"line {i}: {line.strip()}"
    return False, ""


def _has_test(test_source: str, *patterns: str) -> tuple[bool, str]:
    """
    Return (True, first_matching_line_ref) if ANY pattern matches in test source.
    """
    return _has_code(test_source, *patterns)


# ---------------------------------------------------------------------------
# Per-requirement checks
# ---------------------------------------------------------------------------

# Each entry: (req_id, code_patterns, test_patterns)
# Both lists must match (at least one pattern per list) for "covered".
# Patterns are substrings searched in the respective source files.
_CHECKS: list[tuple[str, list[str], list[str]]] = [
    (
        "R1",
        # Route is `todos.get('/', (c)` — match up to the comma so we don't
        # accidentally catch the /:id variant; also accept double-quote style.
        ["todos.get('/',", 'todos.get("/",', "db.getAll()"],
        ["GET /api/todos", "Array.isArray", "seeded todo"],
    ),
    (
        "R2",
        ["c.req.query('completed')", 'c.req.query("completed")',
         "req.query('completed')", 'req.query("completed")'],
        ["completed=true", "completed=false", "filters by completed"],
    ),
    (
        "R3",
        ["todos.get('/:id',", 'todos.get("/:id",', "getById"],
        ["GET /api/todos/:id", "does-not-exist", "404"],
    ),
    (
        "R4",
        ["todos.post('/',", 'todos.post("/",', "db.create"],
        ["status, 201", "assert.equal(res.status, 201)", "completed, false",
         "completed: false"],
    ),
    (
        "R5",
        ["400", "Title is required", "title?.trim()", "title.trim()"],
        ["rejects a missing title", "title: '   '", 'title: "   "', "status, 400",
         "assert.equal(res.status, 400)"],
    ),
    (
        "R6",
        ["todos.put('/:id',", 'todos.put("/:id",', "db.update"],
        ["PUT", "completed: true", "updates an existing"],
    ),
    (
        "R7",
        ["todos.delete('/:id',", 'todos.delete("/:id",', "db.delete"],
        ["204", "removes an existing", "DELETE"],
    ),
]


def _check_r8_static(test_source: str, test_file_path: str) -> dict:
    """
    R8: Test suite is real and green.
    Static check: test file exists, is non-empty, and contains real assert calls.
    (Running npm test is test.py's job — we only verify the file is substantive.)
    """
    if not test_source.strip():
        return {
            "id": "R8",
            "text": "Test suite is real and green.",
            "status": "not_covered",
            "evidence": f"{test_file_path} is empty or missing.",
        }

    has_assert, ref = _has_code(test_source, "assert.", "assert(")
    if not has_assert:
        return {
            "id": "R8",
            "text": "Test suite is real and green.",
            "status": "unclear",
            "evidence": f"{test_file_path} exists but contains no assert calls.",
        }

    # Count test() / it() calls as a rough substantiveness check
    test_calls = len(re.findall(r"\btest\s*\(", test_source))
    return {
        "id": "R8",
        "text": "Test suite is real and green.",
        "status": "covered",
        "evidence": (
            f"{test_file_path} is non-empty, contains {test_calls} test() call(s) "
            f"and real assert statements ({ref})."
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(repo_path: str, requirements_path: str) -> dict:
    """Run the spec subagent and return the contract dict."""
    # Resolve paths
    req_full = os.path.join(repo_path, requirements_path) if not os.path.isabs(requirements_path) else requirements_path
    routes_file = os.path.join(repo_path, "src", "routes", "todos.ts")
    test_file = os.path.join(repo_path, "src", "routes", "todos.test.ts")

    parsed_reqs = _parse_requirements(req_full)

    source = _read(routes_file)
    test_source = _read(test_file)

    # Build id→text map from parsed requirements
    req_map = {r["id"]: r["text"] for r in parsed_reqs}

    results: list[dict] = []

    for req_id, code_pats, test_pats in _CHECKS:
        text = req_map.get(req_id, f"{req_id} (not found in requirements.md)")

        code_found, code_ref = _has_code(source, *code_pats)
        test_found, test_ref = _has_test(test_source, *test_pats)

        if code_found and test_found:
            status = "covered"
            evidence = (
                f"Route: {os.path.relpath(routes_file, repo_path)}, {code_ref}. "
                f"Test: {os.path.relpath(test_file, repo_path)}, {test_ref}."
            )
        elif code_found and not test_found:
            status = "unclear"
            evidence = (
                f"Route implemented ({os.path.relpath(routes_file, repo_path)}, {code_ref}) "
                f"but no matching test found in {os.path.relpath(test_file, repo_path)}."
            )
        elif not code_found and test_found:
            status = "unclear"
            evidence = (
                f"Test found ({os.path.relpath(test_file, repo_path)}, {test_ref}) "
                f"but no matching route found in {os.path.relpath(routes_file, repo_path)}."
            )
        else:
            status = "not_covered"
            evidence = (
                f"No matching route in {os.path.relpath(routes_file, repo_path)} "
                f"and no matching test in {os.path.relpath(test_file, repo_path)}."
            )

        results.append({"id": req_id, "text": text, "status": status, "evidence": evidence})

    # R8 is a static file check, not a route pattern check
    r8_text = req_map.get("R8", "Test suite is real and green.")
    r8_result = _check_r8_static(test_source, os.path.relpath(test_file, repo_path))
    r8_result["text"] = r8_text
    results.append(r8_result)

    covered = sum(1 for r in results if r["status"] == "covered")

    return {
        "total_requirements": len(results),
        "covered": covered,
        "requirements": results,
    }


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    req_path = sys.argv[2] if len(sys.argv) > 2 else "requirements.md"
    output = run(repo, req_path)
    # Pretty-print summary then full detail
    print(f"Coverage: {output['covered']}/{output['total_requirements']}\n")
    for r in output["requirements"]:
        icon = "✅" if r["status"] == "covered" else ("⚠️ " if r["status"] == "unclear" else "❌")
        print(f"{icon} {r['id']}: {r['status']}")
        print(f"   {r['evidence']}")
    print("\n--- full JSON ---")
    print(json.dumps(output, indent=2))
