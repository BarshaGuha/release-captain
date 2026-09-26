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
import os
import re
import subprocess
import sys
import time


def _read_test_command(repo_path: str) -> str:
    """Read scripts.test from the repo's package.json."""
    pkg_path = os.path.join(repo_path, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    return pkg["scripts"]["test"]


def _tail(text: str, n: int = 40) -> str:
    """Return the last n lines of text."""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _parse_node_summary(output: str) -> dict:
    """
    Parse Node's built-in test runner TAP-ish summary lines.

    The runner emits lines like:
        ℹ tests 7
        ℹ pass 7
        ℹ fail 0
        ℹ duration_ms 106.78

    Returns a dict with keys: total, passed_count, failed_count, duration_ms.
    All values default to None when not found.
    """
    result = {"total": None, "passed_count": None, "failed_count": None, "duration_ms": None}

    # ℹ (U+2139) followed by a keyword and integer/float value
    # Also handle plain ASCII 'i' prefix used in some Node versions
    pattern = re.compile(
        r"^[ℹi]\s+(tests|pass|fail|duration_ms)\s+([\d.]+)",
        re.MULTILINE,
    )
    for m in pattern.finditer(output):
        key, value = m.group(1), m.group(2)
        if key == "tests":
            result["total"] = int(value)
        elif key == "pass":
            result["passed_count"] = int(value)
        elif key == "fail":
            result["failed_count"] = int(value)
        elif key == "duration_ms":
            result["duration_ms"] = float(value)

    return result


def _parse_failures(output: str) -> list[dict]:
    """
    Extract individual test failures from Node test runner output.

    Failure blocks look like:
        ✖ failing tests:

        test at path/to/file.ts:1:1
        ✖ <test name> (<duration>ms)
          '<error message>'
    """
    failures: list[dict] = []

    # Individual failing test lines appear in the "✖ failing tests:" section at
    # the bottom of Node's output. Each block looks like:
    #
    #   test at <file>:<line>:<col>          <- location line (no ✖)
    #   ✖ <test name> (<duration>ms)         <- the line we match
    #     <error detail lines>               <- indented
    #
    # Suite roll-up lines also use ✖ but appear earlier in the run output
    # (before the "✖ failing tests:" header) and are NOT preceded by a
    # "test at …" location line — we use that to disambiguate.
    fail_line = re.compile(r"^[✖x]\s+(.+?)\s+\(\d+[\d.]*ms\)$")
    lines = output.splitlines()

    # Find where the "failing tests:" summary block starts
    summary_start = 0
    for idx, ln in enumerate(lines):
        if re.search(r"failing tests", ln, re.IGNORECASE):
            summary_start = idx
            break

    for i, line in enumerate(lines):
        # Only parse failures from the summary block
        if i < summary_start:
            continue
        m = fail_line.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        # Collect subsequent indented lines as the message (up to next blank or non-indented)
        message_lines: list[str] = []
        for j in range(i + 1, min(i + 10, len(lines))):
            next_line = lines[j]
            if next_line.strip() == "":
                break
            if next_line.startswith("  ") or next_line.startswith("\t"):
                message_lines.append(next_line.strip())
            else:
                break
        failures.append({"name": name, "message": " ".join(message_lines) or "no detail captured"})

    return failures


def run(repo_path: str, test_command: str) -> dict:
    """Run the test subagent and return the contract dict."""
    t_start = time.perf_counter()

    # Prepend the repo's own node_modules/.bin to PATH so locally installed
    # binaries (e.g. tsx, vitest) are found, matching what `npm test` does.
    env = os.environ.copy()
    local_bin = os.path.join(os.path.abspath(repo_path), "node_modules", ".bin")
    env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")

    proc = subprocess.run(
        test_command,
        shell=True,
        cwd=repo_path,
        capture_output=True,
        text=True,
        env=env,
    )

    duration_seconds = time.perf_counter() - t_start

    # Combine stdout + stderr — Node test runner writes to stdout but npm
    # wrapper lines appear on stdout too; stderr catches anything extra.
    combined = proc.stdout
    if proc.stderr:
        combined = combined + "\n" + proc.stderr if combined else proc.stderr

    raw_output_tail = _tail(combined, 40)

    summary = _parse_node_summary(combined)

    total = summary["total"] if summary["total"] is not None else 0
    failed = summary["failed_count"] if summary["failed_count"] is not None else 0

    # duration_ms from parser overrides wall-clock only when available and
    # more precise (excludes npm overhead)
    if summary["duration_ms"] is not None:
        duration_seconds = summary["duration_ms"] / 1000.0

    # passed = True only when the process exited cleanly AND fail count is 0
    passed = (proc.returncode == 0) and (failed == 0)

    failures = _parse_failures(combined) if not passed else []

    return {
        "passed": passed,
        "total": total,
        "failed": failed,
        "duration_seconds": round(duration_seconds, 3),
        "failures": failures,
        "raw_output_tail": raw_output_tail,
    }


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "release-captain-target"
    cmd = sys.argv[2] if len(sys.argv) > 2 else _read_test_command(repo)
    output = run(repo, cmd)
    # Print everything except the (potentially long) raw tail first, then tail
    tail = output.pop("raw_output_tail")
    print(json.dumps(output, indent=2))
    print("\n--- raw_output_tail ---")
    print(tail)
