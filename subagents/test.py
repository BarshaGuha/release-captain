"""Test subagent — run the suite and summarize results.

Build this file with Bob 2.0 — see BOB_PROMPTS.md, prompt 4.

CONTRACT
--------
Input:
    repo_path: str        — path to the target git repo
    test_command: str      — the command to run (read it from that repo's
                              package.json "scripts.test", don't hardcode it)

Output (dict):
    {
        "passed": bool,
        "total": int,
        "failed": int,
        "duration_seconds": float,
        "failures": [
            {"name": str, "message": str}
        ],
        "raw_output_tail": str,   # last ~40 lines, for the report's detail view
    }

Notes for Bob:
- Actually run the command (subprocess), don't simulate it.
- Node's built-in test runner (`node --test`) prints TAP-ish output — parse
  pass/fail counts from it; don't assume a specific framework's format
  since the target repo could change.
- If the command exits non-zero but produces no parseable summary, still
  return passed=False with whatever detail is available in raw_output_tail.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path


def run(repo_path: str, test_command: str | None = None) -> dict:
    """Run the test suite and return a structured results dict.

    If *test_command* is None, reads scripts.test from the repo's package.json.
    """
    repo = Path(repo_path)

    # Step 1 — resolve test command from package.json if not provided
    if test_command is None:
        pkg = json.loads((repo / "package.json").read_text())
        test_command = pkg["scripts"]["test"]

    # Step 2a — install dependencies if node_modules is absent.
    # Only runs on a cold environment; skipped once the tree is present so
    # the timing numbers reported to the orchestrator aren't inflated.
    if not (repo / "node_modules").exists():
        install = subprocess.run(
            ["npm", "install"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise RuntimeError(
                f"npm install failed in {repo}:\n{install.stderr.strip()}"
            )

    # Step 2b — run the command, capturing stdout+stderr, timing wall-clock
    start = time.perf_counter()
    result = subprocess.run(
        test_command,
        shell=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter() - start

    combined = result.stdout + result.stderr

    # Step 3 — parse Node built-in test runner summary lines
    # Format: "ℹ pass N" / "ℹ fail N"  (ℹ = U+2139)
    pass_count = 0
    fail_count = 0

    pass_match = re.search(r"[\u2139\#]\s*pass\s+(\d+)", combined)
    fail_match = re.search(r"[\u2139\#]\s*fail\s+(\d+)", combined)

    if pass_match:
        pass_count = int(pass_match.group(1))
    if fail_match:
        fail_count = int(fail_match.group(1))

    total = pass_count + fail_count

    # Step 3b — collect individual failure entries.
    # Node's built-in runner has two sections:
    #   1. Inline streaming output:  "  ✖ <name> (Xms)" (indented = test-level)
    #   2. "✖ failing tests:" summary block at the end with AssertionError text
    #
    # Strategy: collect failure names from section 1, then for each name look
    # up its AssertionError message from section 2 (the trailing summary block).
    lines = combined.splitlines()

    # Build a map: failure name → assertion message from the trailing block
    assertion_map: dict[str, str] = {}
    in_failing_block = False
    current_fail_name: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r"\u2716\s+failing tests", stripped):  # ✖ failing tests:
            in_failing_block = True
            continue
        if not in_failing_block:
            continue
        # "✖ <name> (Xms)" inside the summary block
        m = re.match(r"\u2716\s+(.+?)\s*\(\d+", stripped)
        if m:
            current_fail_name = m.group(1).strip()
            assertion_map.setdefault(current_fail_name, "")
            continue
        # First non-empty, non-"test at" line after a name = the error message
        if current_fail_name and stripped and not stripped.startswith("test at"):
            if not assertion_map[current_fail_name]:
                assertion_map[current_fail_name] = stripped
                current_fail_name = None  # only grab the first message line

    # Collect failure names from inline indented ✖ lines (actual test entries)
    failures: list[dict] = []
    for line in lines:
        # TAP outer: "not ok N - <description>"
        not_ok = re.match(r"\s*not ok\s+\d+\s*[-–]?\s*(.+)", line)
        # Inline ✖: must be indented (suite-level ✖ lines have no leading space)
        x_fail = re.match(r"( +)\u2716\s+(.+?)\s*\(\d+", line)
        if not_ok:
            name = not_ok.group(1).strip()
        elif x_fail:
            name = x_fail.group(2).strip()
        else:
            continue
        message = assertion_map.get(name, "")
        failures.append({"name": name, "message": message})

    # Step 4 — passed = True only when exit code is 0 AND fail count is 0
    passed = result.returncode == 0 and fail_count == 0

    # Step 5 — last ~40 lines for the report detail view
    raw_output_tail = "\n".join(lines[-40:])

    return {
        "passed": passed,
        "total": total,
        "failed": fail_count,
        "duration_seconds": round(duration, 3),
        "failures": failures,
        "raw_output_tail": raw_output_tail,
    }


if __name__ == "__main__":
    import sys
    import pprint

    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    print(f"Running tests in: {repo}")
    output = run(repo)
    pprint.pprint(output)
    verdict = "PASS ✓" if output["passed"] else "FAIL ✗"
    print(f"\n→ {verdict}  ({output['total']} total, {output['failed']} failed, {output['duration_seconds']}s)")
