# Release Captain — Implementation Plan

## Overview

**Goal:** Replace the 14-minute manual release checklist (documented in
`release-captain-target/BASELINE_TIMING.md`) with an automated Python
orchestrator that runs four focused subagents in parallel and produces a
single go/no-go report in seconds.

**Scope:** Implement the four subagent stubs (`subagents/changelog.py`,
`subagents/risk.py`, `subagents/test.py`, `subagents/spec.py`) and wire them
together in `orchestrator.py`. The Streamlit UI (`report/app.py`) and the
Bob workflow (`release-check` skill) are already stubbed and are not part of
this plan's implementation scope — they will work once the orchestrator is
real.

**Target repo facts (fixed inputs for every run):**
- Repo: `release-captain-target/` — a Hono/TypeScript todo API
- Branch: `release/v1.2.0`
- Baseline ref: `763d71b` (first commit — the "since" point for this release)
- Test command: `node --experimental-strip-types --test src/routes/*.test.ts`
  (read from `package.json scripts.test` — never hardcode it)
- Requirements file: `requirements.md` (10 requirements, R1–R10; R8–R10 are new)
- Dependency manifest: `package.json` + `package-lock.json` (Node/npm project)

**Build order rule (from `.bob/rules/release-captain.md`):**
Build and validate each subagent standalone before wiring the orchestrator.
Never let one subagent import another. Each must have a
`if __name__ == "__main__":` guard for standalone testing.

---

## Sub-Task 1 — Changelog subagent

**Status:** [x] done

**Intent:**
Implement `subagents/changelog.py` so it reads the real commit history of any
target repo between a `since_ref` and `HEAD`, classifies each commit, and
returns a structured dict plus human-readable release notes markdown.

**Expected Outcomes:**
- `run(repo_path, since_ref)` returns a dict matching the contract in its
  docstring (keys: `release_notes_markdown`, `commit_count`, `commits`).
- Running it against `release-captain-target` from `763d71b` produces the
  actual list of commits on the `release/v1.2.0` branch with each commit
  classified as `feature | fix | chore | other`.
- A `if __name__ == "__main__":` block prints the dict when executed directly.

**Todo List:**
1. Use `subprocess.run(["git", "log", f"{since_ref}..HEAD", "--oneline"],
   cwd=repo_path)` to get raw commit lines.
2. For each line, split off the short hash and classify the message:
   - `feat:` prefix → `feature`
   - `fix:` prefix → `fix`
   - `chore:` prefix → `chore`
   - otherwise → `other` (but try keywords like "add", "update", "remove"
     for a best-effort guess)
3. Build `release_notes_markdown` grouped by kind (Features / Fixes / Chores /
   Other), using a simple markdown list.
4. Return the contract dict.
5. Add `if __name__ == "__main__":` that calls `run()` with
   `release-captain-target` and `763d71b` and pretty-prints the result.
6. Run it standalone; verify commit count and classifications look correct.

**Relevant Context:**
- Contract: `subagents/changelog.py` docstring
- Target repo git log: `release-captain-target/.git/`
- Build rule: `.bob/rules/release-captain.md`

---

## Sub-Task 2 — Risk subagent

**Status:** [ ] pending

**Intent:**
Implement `subagents/risk.py` so it diffs `package.json` and
`package-lock.json` between `since_ref` and `HEAD`, identifies dependency
changes, and assigns a risk level based on the severity of those changes.

**Expected Outcomes:**
- `run(repo_path, since_ref)` returns a dict matching the contract
  (`risk_level`, `findings`, `summary_markdown`).
- Running against `release-captain-target` from `763d71b` surfaces the
  version bumps in that release's `package.json` and assigns them the correct
  severity (`low` for patch/minor, `medium` for major, `medium` for net-new
  dependencies).
- Returns `risk_level: "low"` with empty `findings` if nothing changed —
  never invents risk.
- Standalone `if __name__ == "__main__":` guard present.

**Todo List:**
1. Use `git show {since_ref}:package.json` (via subprocess, `cwd=repo_path`)
   to get the old manifest; parse current `package.json` from disk.
2. For each dependency key (`dependencies`, `devDependencies`), compare old
   vs new version strings.
3. Classify each changed dep:
   - New dep not present before → `medium` ("new dependency, unreviewed")
   - Major version bump (e.g. `3.x` → `4.x`) → `medium`
   - Minor/patch bump → `low`
   - Removed dep → `low` (note it but it's not a risk)
4. Set overall `risk_level` to the highest severity found, or `"low"` if
   findings list is empty.
5. Build `summary_markdown` listing each finding.
6. Add `if __name__ == "__main__":` guard and run standalone.

**Relevant Context:**
- Contract: `subagents/risk.py` docstring
- `release-captain-target/package.json` — current deps: `hono ^4.13.5`,
  `@hono/node-server ^1.19.15`; devDeps: `typescript ^5.7.3`,
  `@types/node ^22.0.0`
- The `BASELINE_TIMING.md` notes "Version bump only, low risk" for the manual
  check — the automated result should match that assessment.

---

## Sub-Task 3 — Test subagent

**Status:** [ ] pending

**Intent:**
Implement `subagents/test.py` so it reads the test command from the target
repo's `package.json`, runs it as a subprocess, and parses pass/fail counts
from Node's built-in test runner TAP-ish output.

**Expected Outcomes:**
- `run(repo_path, test_command)` returns a dict matching the contract
  (`passed`, `total`, `failed`, `duration_seconds`, `failures`,
  `raw_output_tail`).
- Running against `release-captain-target` returns `passed: True`,
  `total: 12`, `failed: 0` (matching `BASELINE_TIMING.md`'s "12 pass,
  0 fail").
- If the process exits non-zero, `passed` is `False` and `failures` contains
  whatever detail is extractable.
- Standalone guard present.

**Todo List:**
1. If `test_command` is `None`, read `scripts.test` from the repo's
   `package.json` via `json.load`.
2. Run the command via `subprocess.run` with `cwd=repo_path`, capturing both
   stdout and stderr, and recording wall-clock time with `time.perf_counter`.
3. Parse the output for Node's test runner summary lines:
   - `# pass N` → `total` and `passed count`
   - `# fail N` → `failed count`
   - Individual failure blocks (`not ok` lines) → `failures` list entries
4. Set `passed = True` only if exit code is 0 and `failed == 0`.
5. Set `raw_output_tail` to the last ~40 lines of combined stdout/stderr.
6. Add `if __name__ == "__main__":` guard and run standalone.

**Relevant Context:**
- Contract: `subagents/test.py` docstring
- Test command: `node --experimental-strip-types --test src/routes/*.test.ts`
  (from `package.json scripts.test`)
- 12 test cases exist in `src/routes/todos.test.ts`
- Node >= 22 required (`package.json engines`)

---

## Sub-Task 4 — Spec subagent

**Status:** [ ] pending

**Intent:**
Implement `subagents/spec.py` so it parses the checkbox list in
`requirements.md`, then for each requirement checks whether matching
implementation code AND a matching test both exist in `src/routes/`, and
reports coverage.

**Expected Outcomes:**
- `run(repo_path, requirements_path)` returns a dict matching the contract
  (`total_requirements`, `covered`, `requirements` list).
- Running against `release-captain-target` returns 10 requirements total
  (R1–R10), all 10 `covered`, since every requirement has a corresponding
  route handler in `todos.ts` and a test case in `todos.test.ts`.
- Out-of-scope items from `requirements.md` (pagination, rate-limiting,
  multi-user) are not counted against coverage.
- Standalone guard present.

**Todo List:**
1. Parse `requirements.md` with a regex matching `- [ ] **RN — ...` lines;
   extract requirement ID and text. Skip any "out of scope" note lines.
2. For each requirement, extract the exact HTTP method + path string from its
   text (e.g. R1 → `GET /api/todos`, R3 → `GET /api/todos/:id`,
   R4 → `POST /api/todos`). Store these as the canonical search strings.
3. Search `src/routes/todos.ts` for an exact match of that route string using
   `grep` (or `re.search` over the file text) — implementation present when
   the route handler literally contains that method+path.
4. Search `src/routes/todos.test.ts` for an exact match of the same route
   string — test present when the test file references the exact endpoint.
5. Status: `covered` = both found; `not_covered` = neither found;
   `unclear` = only one side found. Record the matching filename as `evidence`.
6. Add `if __name__ == "__main__":` guard and run standalone; confirm all
   10 requirements come back `covered`.

**Relevant Context:**
- Contract: `subagents/spec.py` docstring
- Requirements: `release-captain-target/requirements.md` — 10 items (R1–R10),
  with "Out of scope" note at the bottom that must be excluded
- Implementation: `src/routes/todos.ts` (routes for all 10)
- Tests: `src/routes/todos.test.ts` (12 test cases covering all 10
  requirements, including auth and persistence)

---

## Sub-Task 5 — Orchestrator

**Status:** [ ] pending

**Intent:**
Implement `orchestrator.py` so it dispatches all four subagents concurrently
using a thread pool, times the whole run, applies the NO-GO verdict rule, and
derives a rollback plan from the changelog output.

**Expected Outcomes:**
- `run_release_check(repo_path, since_ref, requirements_path, test_command)`
  returns the full report dict matching the orchestrator docstring contract.
- All four subagents run in parallel (not sequentially) — wall-clock time
  reflects concurrent execution.
- Verdict is `"GO"` for `release-captain-target` on `release/v1.2.0` (tests
  pass, risk is not high, no uncovered requirements).
- `duration_seconds` is printed — this is the pitch-deck headline number
  replacing the manual 14:36.
- If any subagent raises an exception, the orchestrator catches it, records
  `{"error": str(e)}` for that subagent's key, and still returns a complete
  report (marking verdict `"NO-GO"` if a required subagent failed).

**Todo List:**
1. Import all four subagents at the top; read `test_command` from
   `package.json scripts.test` if the caller passed `None`.
2. Use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` and submit all
   four subagent `run()` calls as futures.
3. Start `time.perf_counter()` before submitting futures; stop it after all
   `future.result()` calls complete.
4. Apply verdict rule (from docstring): `NO-GO` if `tests["passed"] is False`
   OR `risk["risk_level"] == "high"` OR any requirement has
   `status == "not_covered"`.
5. Derive `rollback_plan_markdown` from `changelog["commits"]`: list what
   was added and note that reverting to the previous tag/ref would undo it.
6. Build and return the full report dict with `generated_at` (ISO timestamp
   via `datetime.utcnow().isoformat()`).
7. Add `if __name__ == "__main__":` guard that calls `run_release_check`
   against `release-captain-target` and pretty-prints the full report with
   duration.

**Relevant Context:**
- Contract: `orchestrator.py` docstring
- Verdict rule: tests failed → NO-GO; risk_level == "high" → NO-GO;
  any `not_covered` requirement → NO-GO; otherwise GO
- `report/app.py` already calls `orchestrator.run_release_check(...)` with
  `test_command=None` — the orchestrator must handle `None` gracefully
- `concurrent.futures` is stdlib; no extra dependency needed

---

## Build Order

```
changelog → risk → test → spec → orchestrator
```

Each subagent is validated standalone against the target repo before the next
one is started. The orchestrator is last because it depends on all four being
correct.

## Non-Goals

- No changes to `report/app.py` — it is already fully implemented.
- No new Python dependencies beyond stdlib + `streamlit` (already in
  `requirements.txt`).
- No modification to `release-captain-target/` source files.
- No unit test files (the `if __name__ == "__main__":` manual checks are
  sufficient per the project rules).
