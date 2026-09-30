# Bob 2.0 prompts — paste these in order

Open this repo (`release-captain`) in Bob 2.0 IDE, with `release-captain-target`
cloned NESTED inside it (Bob's sandbox can't reach a sibling folder outside
the opened project root — see the runbook). Run these prompts in order.
Each one names the file it should produce — check that file, don't just
trust the chat reply.

## 0. Sanity check (do this first, once)

Open `release-captain-target` in Bob and ask:

> Run `npm install` and `npm test` in this repo and tell me the result.

This confirms the target repo's real test suite actually passes before
anything else depends on it.

## 1. Plan mode — the workflow

Switch to **Plan mode**, then:

> I'm building Release Captain: an orchestrator (`orchestrator.py`) that runs
> four subagents in parallel — changelog, risk, test, spec — against a
> target git repo, and produces a go/no-go release report. Each subagent's
> contract is already documented in its own docstring in `subagents/`. Read
> `.bob/rules/release-captain.md` and the four subagent stub files, then
> draft an implementation plan: what each subagent needs to do concretely
> for the `release-captain-target` repo, in what order I should build and
> test them, and how the orchestrator should run them concurrently.

Keep this plan — it's demo material showing Bob's own multi-step reasoning.

## 2. Changelog subagent

Switch to **Agent mode**:

> Implement `subagents/changelog.py` per its docstring contract. Use `git
> log <since_ref>..HEAD --oneline` against the repo at `repo_path`. Classify
> each commit as feature/fix/chore/other from its message. Then run it
> against `release-captain-target` from commit `763d71b` to `HEAD` and
> show me the output.

(`763d71b` is the first commit in that repo — the "since" point for this
release. Check the actual hash with `git log --oneline` if it's cloned
somewhere else.)

## 3. Risk subagent

> Implement `subagents/risk.py` per its docstring contract. Diff
> `package.json` and `package-lock.json` between `since_ref` and HEAD in the
> target repo, and flag major-version bumps or new dependencies. Then run it
> against `release-captain-target` and show me the output.

## 4. Test subagent

> Implement `subagents/test.py` per its docstring contract. Read the test
> command from the target repo's `package.json` `scripts.test`, run it as a
> subprocess, and parse pass/fail counts from Node's built-in test runner
> output. Then run it against `release-captain-target` and show me the
> output.

## 5. Spec subagent (document understanding)

> Implement `subagents/spec.py` per its docstring contract. Parse the
> checkbox list in the target repo's `requirements.md`, and for each
> requirement check whether matching code AND a matching test both exist in
> `src/routes/`. Then run it against `release-captain-target` and show me
> the output — I expect all 8 requirements to come back covered.

## 6. Orchestrator (agent mode, parallel dispatch)

> Implement `orchestrator.py` per its docstring contract. Run the four
> subagents concurrently with a thread pool, not sequentially, and time the
> whole run with `time.perf_counter()`. Apply the NO-GO rule documented in
> the docstring. Then run `run_release_check` against
> `release-captain-target` and show me the full report, including the
> wall-clock duration.

**This is the number for the pitch deck** — replace "<10 min" with whatever
this actually prints.

## 7. Report UI + hosting

> Build out `report/app.py`: a Streamlit page that calls
> `orchestrator.run_release_check(...)` and displays the verdict, duration,
> and all four subagents' findings clearly, plus the rollback plan. Add a
> "Run again" button. Then tell me the exact commands to deploy this to
> Streamlit Community Cloud.

## 8. Save it as a Workflow

> Save the sequence from prompts 2–6 as a reusable Bob Workflow named
> "release-check", so it can be re-run against a different repo or branch
> later.

---

# Phase 2 — post-hackathon value-add features

These three build on the shipped, tested v1 (8 production-readiness items,
25 passing tests, 4 live demo scenarios). Do them in this order — each is
independent, but this order goes simplest/lowest-risk first.

## 9. Run-history trend view

Switch to **Agent mode**:

> `orchestrator.py`'s docstring documents that every run is written to
> `<history_dir>/<run_id>.json` plus a line appended to
> `<history_dir>/index.jsonl` (default `history_dir` is `run_history/` at the
> repo root — check `_DEFAULT_HISTORY_DIR` in `orchestrator.py`). Nothing
> reads this back yet. Add a new tab to `report/app.py` called "History"
> that reads `index.jsonl`, and for each run shows: timestamp, verdict
> (color-coded — green for GO, yellow for GO-WITH-WARNINGS, red for NO-GO),
> the commit sha, and the run duration, newest first. If the file doesn't
> exist yet (fresh deploy), show a friendly empty state instead of erroring.
> Run the app locally and confirm the History tab shows the runs from
> today's demo (GO, NO-GO test-failure, NO-GO dependency-risk,
> GO-WITH-WARNINGS) once you've re-run each scenario once.

## 10. GitHub PR / Action integration

Switch to **Plan mode** first:

> I want Release Captain's verdict to be posted automatically as a comment
> on a pull request in `release-captain-target`, using a GitHub Action
> triggered on `pull_request`. Read `orchestrator.py`'s docstring for the
> report shape. Draft a plan: what the workflow YAML needs to do (checkout,
> install Python deps, run the orchestrator against the PR's base and head
> refs), how to format the report dict as a readable markdown PR comment
> (verdict banner, warnings table, links to the four subagent sections), and
> how to post/update that comment using the Action's built-in `GITHUB_TOKEN`
> (no extra secret needed) via the GitHub REST API — updating the same
> comment on subsequent pushes instead of spamming a new one each time.

Then **Agent mode**:

> Implement the plan: add `integrations/github_comment.py` (formats a report
> dict from `orchestrator.run_release_check` into markdown, and
> posts/updates a PR comment given a repo, PR number, and token) and
> `.github/workflows/release-captain-check.yml` in `release-captain-target`
> that runs on every PR, calls the orchestrator with `since_ref` set to the
> PR's base sha, and posts the comment. Open a real test PR against
> `release-captain-target` (e.g. from a throwaway branch with a trivial
> change) and confirm the Action actually runs and posts a comment with the
> correct verdict.

## 11. Real Claude-powered reasoning for ambiguous cases

Switch to **Plan mode** first:

> Right now every subagent's judgment is heuristic — regex matching in
> `subagents/spec.py`, git diffing in `subagents/risk.py`. I want to add a
> real Claude API call for the cases that are already honestly reported as
> ambiguous: `spec.py` items with `status == "unclear"` (no signal mapping
> found). This must NOT change `_compute_verdict`'s pass/fail math or break
> any of the 10 existing tests in `tests/test_orchestrator.py` — the model
> call should only enrich the `rationale` shown to a human reviewer with
> real reasoning about the requirement text and the available code context,
> not silently flip an item to "covered". If the API call fails or
> `ANTHROPIC_API_KEY` isn't set, fall back to the existing templated
> rationale rather than crashing the run. Draft a plan for where this call
> goes (a new `subagents/judge.py`, called by the orchestrator only on
> `unclear` items) and how it's tested without needing a live API key for
> every test run (mock the API call in tests; one real integration test that
> only runs if the key is present).

Then **Agent mode**:

> Implement `subagents/judge.py` per the plan: takes an `unclear`
> requirement (text, evidence, config source) and the repo's relevant source
> file content, calls the Claude API with a tightly scoped prompt asking it
> to explain — not decide pass/fail, just explain — what's genuinely
> ambiguous about matching this requirement to the code, in 1-3 sentences.
> Wire it into `orchestrator.py` so each `unclear` warning's `rationale`
> field is replaced with this real explanation when the API key is present.
> Add `ANTHROPIC_API_KEY` as a Streamlit secret and confirm the
> GO-WITH-WARNINGS demo (`release/v1.4.0-spec-warning`, R11) now shows a
> genuinely reasoned rationale instead of the templated
> "Coverage check could not confidently classify this requirement" text.
> Then run `python3 run_tests.py` and confirm all 25 existing tests still
> pass unmodified.
