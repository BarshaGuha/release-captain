---
name: release-check
description: Use when the user wants to run a Release Captain release check, implement or re-implement the changelog/risk/test/spec subagents, or run run_release_check against a repo or branch. Walks through building each subagent, running it standalone, then wiring the orchestrator — mirrors prompts 2-6 in BOB_PROMPTS.md.
---

# Release Check Skill

This skill re-runs the full Release Captain build-and-check sequence (BOB_PROMPTS.md prompts 2–6) against any target repo and branch. Follow the steps below in order. Each step names the file it produces — verify the file, not just the chat reply.

## Parameters to confirm before starting

Before doing any work, identify these four values. Read them from the user's message, or ask if missing:

- **`REPO_PATH`** — path to the target git repo (e.g. `../release-captain-target` or an absolute path)
- **`SINCE_REF`** — git ref to diff from (e.g. a tag like `v1.1.0`, a commit SHA, or `HEAD~N`). If unsure, run `git log --oneline` in the target repo and use the first commit SHA or the last release tag.
- **`REQUIREMENTS_PATH`** — relative path inside the target repo to the requirements file (default: `requirements.md`)
- **`SINCE_REF` source** — confirm the ref exists: run `git log --oneline` in the target repo to verify it appears

Do not proceed past this point until all four are known.

---

## Step 1 — Changelog subagent (`subagents/changelog.py`)

**Goal:** implement and verify before moving on.

1. Read the current `subagents/changelog.py` to check whether it's already implemented (not a stub).
2. If it's a stub (`raise NotImplementedError`), implement it per its docstring contract:
   - Run `git log <SINCE_REF>..HEAD --oneline` via subprocess with `cwd=REPO_PATH`
   - Classify each commit as `feature / fix / chore / other` using conventional-commit prefix matching first, then keyword heuristics
   - Return `{ release_notes_markdown, commit_count, commits[] }` exactly as the docstring specifies
   - Add an `if __name__ == "__main__":` block with `sys.argv[1]` = repo_path, `sys.argv[2]` = since_ref (defaulting to `SINCE_REF`)
3. Run it standalone: `python3 subagents/changelog.py <REPO_PATH> <SINCE_REF>`
4. Verify: output is valid JSON, `commit_count` matches real git history, commits are classified sensibly.
5. Do not proceed to Step 2 until the standalone run succeeds.

---

## Step 2 — Risk subagent (`subagents/risk.py`)

**Goal:** implement and verify before moving on.

1. Read the current `subagents/risk.py` to check whether it's already implemented.
2. If it's a stub, implement it per its docstring contract:
   - Use `git show <SINCE_REF>:package.json` to get the old declared ranges; compare with HEAD `package.json`
   - Also diff resolved versions from `package-lock.json` (or `requirements.txt` for Python targets) for direct deps
   - Classify: major bump or new dep → `medium`; minor/patch → `low`; nothing changed → `low` with empty findings
   - Return `{ risk_level, findings[], summary_markdown }` exactly as the docstring specifies
   - Add an `if __name__ == "__main__":` block with defaults `REPO_PATH` and `SINCE_REF`
3. Run it standalone: `python3 subagents/risk.py <REPO_PATH> <SINCE_REF>`
4. Verify: output is valid JSON, findings match real changes in the target repo's dependency files.
5. Do not proceed to Step 3 until the standalone run succeeds.

---

## Step 3 — Test subagent (`subagents/test.py`)

**Goal:** implement and verify before moving on.

1. Read the current `subagents/test.py` to check whether it's already implemented.
2. If it's a stub, implement it per its docstring contract:
   - Read `test_command` from `<REPO_PATH>/package.json` `scripts.test` (never hardcode)
   - Prepend `<REPO_PATH>/node_modules/.bin` to the subprocess `PATH` env so locally installed binaries (tsx, vitest, etc.) are found — this is what `npm test` does automatically
   - Run the command with `subprocess.run(shell=True, cwd=REPO_PATH, capture_output=True, text=True, env=env)`
   - Parse Node test runner TAP-ish summary: `ℹ tests N`, `ℹ pass N`, `ℹ fail N`, `ℹ duration_ms N`
   - Parse failures from the `✖ failing tests:` section only (not from suite roll-up lines earlier in output)
   - Return `{ passed, total, failed, duration_seconds, failures[], raw_output_tail }` exactly as the docstring specifies
   - Add an `if __name__ == "__main__":` block; default test_command reads from `package.json`
3. Run it standalone: `python3 subagents/test.py <REPO_PATH>`
4. Verify: `passed`, `total`, `failed` match real `npm test` output. If tests fail, `failures[]` contains the right test names and error messages.
5. Do not proceed to Step 4 until the standalone run succeeds.

---

## Step 4 — Spec subagent (`subagents/spec.py`)

**Goal:** implement and verify before moving on.

1. Read the current `subagents/spec.py` to check whether it's already implemented.
2. If it's a stub, implement it per its docstring contract:
   - Parse checkbox items from `<REPO_PATH>/<REQUIREMENTS_PATH>` up to (not including) any "Out of scope" line
   - For each requirement, search for matching route/handler code AND a matching test — both must be present for `"covered"`
   - **R8-style requirements** ("test suite is green") get a static check only: verify the test file is non-empty and contains real `assert` calls. Do NOT re-run the test suite — that's `test.py`'s job.
   - Return `{ total_requirements, covered, requirements[] }` exactly as the docstring specifies
   - Add an `if __name__ == "__main__":` block with defaults `REPO_PATH` and `REQUIREMENTS_PATH`
3. Read the actual source and test files before writing any pattern matchers — match against real substring content, not assumed style.
4. Run it standalone: `python3 subagents/spec.py <REPO_PATH> <REQUIREMENTS_PATH>`
5. Verify: all in-scope requirements appear, out-of-scope items are absent, evidence strings point to real line numbers.
6. Do not proceed to Step 5 until the standalone run succeeds and coverage looks correct.

---

## Step 5 — Orchestrator (`orchestrator.py`)

**Goal:** wire all four subagents in parallel and produce the final report.

1. Read the current `orchestrator.py` to check whether it's already implemented.
2. If it's a stub, implement it per its docstring contract:
   - Use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` — submit all four futures before calling `.result()` on any of them
   - Time the whole run with `time.perf_counter()` — start before submitting, stop after all results collected
   - Wrap each `future.result()` in try/except; store `{"error": str(exc), "subagent": name}` on failure so one bad subagent can't crash the whole run
   - Read `test_command` from `<REPO_PATH>/package.json` if not passed explicitly
   - Verdict rule (exactly as documented): NO-GO if `tests["passed"] == False` OR `risk["risk_level"] == "high"` OR any requirement has `status == "not_covered"`
   - `rollback_plan_markdown`: a short summary sentence — branch name + `SINCE_REF` SHA to revert to — **not** a list of individual `git revert` commands per commit
   - `generated_at`: `datetime.now(timezone.utc).isoformat()`
   - Return the full contract dict with all required keys
   - Add an `if __name__ == "__main__":` block that prints a human-readable summary then the full JSON
3. Run it: `python3 orchestrator.py <REPO_PATH> <SINCE_REF> <REQUIREMENTS_PATH>`
4. Show the full output including `verdict`, `duration_seconds`, and all four subagent sections.

---

## Completion checklist

Before declaring done, confirm:

- [ ] All four `subagents/*.py` files run standalone without errors
- [ ] `orchestrator.py` runs end-to-end and prints a valid verdict
- [ ] `duration_seconds` reflects true parallel wall-clock time (dominated by the slowest subagent)
- [ ] Verdict is `GO` or `NO-GO` per the documented rule — not a guess
- [ ] No subagent imports or calls another subagent (independence rule from `.bob/rules/release-captain.md`)
- [ ] No paths to the target repo are hardcoded anywhere in `subagents/` or `orchestrator.py`
