# Release Captain — Implementation Plan

## Top-Level Overview

Build Release Captain: a Python orchestrator that runs four subagents **concurrently** against `release-captain-target` (a Hono/TypeScript todo API on branch `release/v1.1.0`), then renders a GO/NO-GO release report in Streamlit.

All five files are stubs today (`raise NotImplementedError`). Each subagent must be implemented, manually verified standalone, and only then wired into the orchestrator. The Streamlit report is built last.

**Confirmed parameters:**
- `since_ref` = `763d71b` — the first commit in the repo; there is no prior release tag
- `requirements_path` = `requirements.md` (relative inside the target repo)
- `test_command` is read from `package.json` `scripts.test` at runtime

**Key constraints from `.bob/rules/release-captain.md`:**
- Subagents are independent — no cross-imports between them
- Each must be runnable alone via its `if __name__ == "__main__"` block
- `orchestrator.py` must run all four with `ThreadPoolExecutor`, never sequentially
- No hardcoded paths; always accept `repo_path` as an argument
- Return real findings from real commands — no fabricated output

---

## Sub-Task 1 — `subagents/changelog.py`

**Status:** [x] done

### Intent
Parse the commit history of the target repo since a given ref and produce a structured, human-readable changelog. This is the simplest subagent (pure `git log` + text classification) and the best one to build first to establish the pattern.

### Expected Outcomes
- `run(repo_path, since_ref)` returns a valid dict matching the contract
- Running standalone against `release-captain-target` with `since_ref="HEAD~10"` (or the earliest commit SHA) prints real commit data
- Commits are classified into `feature / fix / chore / other` based on conventional commit prefixes (`feat:`, `fix:`, `chore:`) with a fallback heuristic
- `release_notes_markdown` groups commits under `## Features`, `## Fixes`, `## Chores` headings

### Todo List
1. Implement a helper `_run_git(repo_path, *args)` that calls `subprocess.run(["git", ...], cwd=repo_path)` and returns stdout
2. Call `git log <since_ref>..HEAD --oneline` to get commits; parse each line into `(hash, summary)`
3. Implement `_classify(summary) -> "feature"|"fix"|"chore"|"other"` using message prefix matching
4. Build `release_notes_markdown` by grouping commits under `## Features / ## Fixes / ## Chores / ## Other` headings, skipping empty groups
5. Return the contract dict: `release_notes_markdown`, `commit_count`, `commits`
6. Add `if __name__ == "__main__":` block that accepts `sys.argv[1]` as `repo_path` and `sys.argv[2]` as `since_ref` (default `763d71b`), prints the result as pretty JSON

### Relevant Context
- Contract in [`subagents/changelog.py`](subagents/changelog.py:1)
- Target repo is at `release-captain-target/` (branch `release/v1.1.0`)
- Use `since_ref = 763d71b` (first commit SHA) for all standalone testing

---

## Sub-Task 2 — `subagents/risk.py`

**Status:** [x] done

### Intent
Diff `package.json` (and optionally `package-lock.json`) between `since_ref` and `HEAD` to detect dependency changes. Classify each change by severity. This subagent demonstrates the "git diff on a structured file" pattern.

### Expected Outcomes
- `run(repo_path, since_ref)` returns `risk_level`, `findings`, and `summary_markdown`
- Major version bumps appear as `severity: "medium"`; patch/minor bumps as `"low"`; newly added packages as `"medium"`; removed packages noted
- If `package.json` has not changed between the refs, returns `risk_level: "low"` with empty `findings`
- Running standalone prints real findings from the target repo's actual git history

### Todo List
1. Use `git show <since_ref>:package.json` to read the old dependency map; parse both old and current `package.json` as JSON
2. Compare `dependencies` and `devDependencies` keys to identify added, removed, and changed packages
3. For each changed package, use `packaging`-style semver comparison (or simple string splitting on `^`/`~` + split `.`) to determine major/minor/patch bump type
4. Assign severity: major bump or new dep → `"medium"`, minor/patch → `"low"`, nothing changed → skip
5. Overall `risk_level` = max severity across all findings (`"high"` is reserved for cases where the dep change explicitly mentions breaking changes — use `"medium"` as the ceiling for automated detection)
6. Build `summary_markdown` listing each finding as a bullet
7. Add `if __name__ == "__main__":` block with defaults `sys.argv[1]` = repo_path, `sys.argv[2]` = `763d71b`

### Relevant Context
- Contract in [`subagents/risk.py`](subagents/risk.py:1)
- Target repo `package.json` has: `@hono/node-server ^1.19.15`, `hono ^4.7.2`, `@types/node ^22.0.0`, `typescript ^5.7.3` — check the actual git history for what changed vs the `since_ref`
- No Python packaging library required — semver parsing can be done with string splitting on the `^`/`~`-stripped version strings

---

## Sub-Task 3 — `subagents/test.py`

**Status:** [x] done

### Intent
Run the target repo's test command as a subprocess, capture output, parse Node's TAP-ish summary lines, and return structured pass/fail data. This is the most concrete subagent — it runs a real command and parses real output.

### Expected Outcomes
- `run(repo_path, test_command)` returns `passed`, `total`, `failed`, `duration_seconds`, `failures`, `raw_output_tail`
- `test_command` is passed in (not read internally — the orchestrator reads it from `package.json`)
- Passing run returns `passed=True, total=7, failed=0` matching the actual test results from the target repo
- If tests fail, `failures` list contains name+message for each failing test
- `raw_output_tail` contains the last ~40 lines of output

### Todo List
1. Run `test_command` with `subprocess.run(shell=True, cwd=repo_path, capture_output=True, text=True)` and record wall-clock time with `time.perf_counter()`
2. Combine stdout+stderr into one string for `raw_output_tail` (last 40 lines)
3. Parse Node test runner summary lines: look for `ℹ pass N`, `ℹ fail N`, `ℹ tests N` patterns (Unicode info symbol `ℹ` / `i` + space + keyword)
4. Parse failing test blocks: lines starting with `✖` followed by the test name; capture subsequent indented lines as the message
5. If no parseable summary found (non-Node runner), fall back: `passed = (returncode == 0)`, `total/failed = 0`
6. Add `if __name__ == "__main__":` block: `sys.argv[1]` = repo_path, `sys.argv[2]` = test_command (default to reading from `package.json`)

### Relevant Context
- Contract in [`subagents/test.py`](subagents/test.py:1)
- Node test runner output format confirmed from previous run (see earlier in this conversation): `ℹ tests 7`, `ℹ pass 7`, `ℹ fail 0`, failures prefixed with `✖`
- Current test command (from `package.json`): `node --experimental-strip-types --test src/routes/*.test.ts`
- The test import fix (`.js` → `.ts`) was already applied, so all 7 tests pass

---

## Sub-Task 4 — `subagents/spec.py`

**Status:** [x] done

### Intent
Read `requirements.md`, parse each checkbox requirement, then check the source code and test file for evidence that each requirement is both implemented and tested. This is the "document understanding" subagent.

### Expected Outcomes
- `run(repo_path, requirements_path)` returns `total_requirements`, `covered`, and a `requirements` list
- All 8 requirements from `release-captain-target/requirements.md` are detected (R1–R8)
- "Out of scope" items in the doc are not counted
- Each requirement gets `status: "covered"|"not_covered"|"unclear"` with a specific `evidence` string pointing to the file/test
- "Covered" requires both: a route/handler in the source **and** a test that exercises it

### Todo List
1. Read and parse `requirements_path` to extract requirement items: regex for `- \[ \] \*\*R\d+ —` pattern; stop before the "Out of scope" line
2. For each requirement, determine what HTTP method + path it describes (e.g. R1 → `GET /api/todos`, R2 → `?completed=`, R7 → `DELETE /:id`)
3. Scan `src/routes/todos.ts` for the corresponding route handler (`todos.get`, `todos.post`, `todos.put`, `todos.delete` with the matching path pattern)
4. Scan `src/routes/todos.test.ts` for a `test(` or `describe(` block whose name or content matches the requirement's endpoint
5. Set `status`: both found → `"covered"`, only one found → `"unclear"`, neither → `"not_covered"`; set `evidence` to the file:line reference or missing piece
6. Count `covered` (status == `"covered"`); return the full dict
7. Add `if __name__ == "__main__":` block: `sys.argv[1]` = repo_path, `sys.argv[2]` = requirements_path

### Relevant Context
- Contract in [`subagents/spec.py`](subagents/spec.py:1)
- Requirements file: [`release-captain-target/requirements.md`](release-captain-target/requirements.md:1) — 8 items, R1–R8
- Route implementations: [`release-captain-target/src/routes/todos.ts`](release-captain-target/src/routes/todos.ts:1)
- Tests: [`release-captain-target/src/routes/todos.test.ts`](release-captain-target/src/routes/todos.test.ts:1)
- R8 ("Test suite is green") is special: no route to find — verify it with a **static check only**: confirm the test file exists, is non-empty, and contains real `assert` calls. Do not re-run `npm test`; that is `test.py`'s job. Running it twice would blur the one-job-per-subagent rule.

---

## Sub-Task 5 — `orchestrator.py`

**Status:** [x] done

### Intent
Wire all four subagents together using `ThreadPoolExecutor`, time the wall-clock run, apply the verdict rule, and return the unified report dict. This file is the headline metric producer — its `duration_seconds` is what goes on the pitch deck.

### Expected Outcomes
- `run_release_check(repo_path, since_ref, requirements_path, test_command)` returns the full contract dict
- All four subagents are dispatched simultaneously (not sequentially)
- `duration_seconds` reflects true parallel wall-clock time (i.e. dominated by the slowest subagent, not their sum)
- Verdict logic: `NO-GO` if `tests["passed"] == False` OR `risk["risk_level"] == "high"` OR any item in `spec["requirements"]` has `status == "not_covered"`; otherwise `GO`
- `rollback_plan_markdown` is derived from the changelog: lists the commits that would be reverted (`git revert <hash>` for each) plus a one-line instruction
- `test_command` is read from `repo_path/package.json` `scripts.test` if the caller passes `None`; otherwise uses whatever is passed in

### Todo List
1. Add a helper `_read_test_command(repo_path)` that reads `package.json` and returns `scripts["test"]`
2. Dispatch all four subagents with `ThreadPoolExecutor(max_workers=4)`: submit `changelog.run`, `risk.run`, `test.run`, `spec.run` as futures
3. Wrap each `future.result()` in a try/except so one subagent failure doesn't crash the whole run — store an error dict in that slot instead
4. Apply verdict rule (as described above) after all futures resolve
5. Build `rollback_plan_markdown` as a short summary sentence: state the branch name and the `since_ref` SHA to revert to (e.g. "To roll back, revert branch `release/v1.1.0` to `763d71b`. Run: `git revert <since_ref>..HEAD` or restore the previous tag."). Do **not** enumerate individual `git revert` commands per commit.
6. Assemble and return the full output dict with `generated_at` (ISO 8601 `datetime.now(timezone.utc).isoformat()`), `duration_seconds`, and all four subagent outputs
7. Add `if __name__ == "__main__":` CLI entry point: `sys.argv[1]` = repo_path, prints the result as pretty JSON

### Relevant Context
- Contract in [`orchestrator.py`](orchestrator.py:1)
- Import all four subagents: `from subagents import changelog, risk, test, spec`
- `concurrent.futures.ThreadPoolExecutor` is the prescribed concurrency mechanism
- `time.perf_counter()` for wall-clock timing (start before submitting futures, stop after all `result()` calls)

---

## Sub-Task 6 — `report/app.py`

**Status:** [x] done

### Intent
Build the Streamlit UI that renders the orchestrator's output as a readable go/no-go report, with a live "Run again" button. This is the demo-facing artifact.

### Expected Outcomes
- Page shows a large, color-coded GO (green) / NO-GO (red) verdict + run duration at the top
- Four expandable sections render each subagent's output:
  - **Changelog:** markdown release notes + commit table
  - **Risk:** risk level badge + findings table
  - **Tests:** pass/fail counts + failures detail + raw output expander
  - **Spec coverage:** coverage fraction + per-requirement status table
- Rollback plan rendered as markdown
- "Run again" button re-runs `orchestrator.run_release_check(...)` live and refreshes the page
- Hardcoded defaults for `repo_path`, `since_ref`, `requirements_path` pointing at `release-captain-target` so the demo works without CLI args

### Todo List
1. Define `REPO_PATH`, `SINCE_REF`, `REQUIREMENTS_PATH` constants at the top pointing at `../release-captain-target` (relative to `report/`)
2. Use `st.session_state` to cache the last report dict; populate it on first load by calling `orchestrator.run_release_check(...)`
3. Render verdict: `st.success("✅ GO")` or `st.error("❌ NO-GO")` + `st.metric("Run duration", f"{duration:.1f}s")`
4. Render each subagent section in a `st.expander` or `st.tabs` layout
5. Add "Run again" button: on click, call `orchestrator.run_release_check(...)` and store result back in `st.session_state`
6. Ensure `sys.path` is adjusted so `import orchestrator` works when running `streamlit run report/app.py` from the repo root

### Relevant Context
- Stub in [`report/app.py`](report/app.py:1)
- `requirements.txt` already has `streamlit>=1.38`
- Orchestrator output shape in [`orchestrator.py`](orchestrator.py:10)
