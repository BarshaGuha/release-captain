# Bob 2.0 prompts — paste these in order

Open this repo (`release-captain`) in Bob 2.0 IDE, with `release-captain-target`
cloned as a sibling folder. Run these prompts in order. Each one names the
file it should produce — check that file, don't just trust the chat reply.

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
> against `../release-captain-target` from commit `763d71b` to `HEAD` and
> show me the output.

(`763d71b` is the first commit in that repo — the "since" point for this
release. Check the actual hash with `git log --oneline` if it's cloned
somewhere else.)

## 3. Risk subagent

> Implement `subagents/risk.py` per its docstring contract. Diff
> `package.json` and `package-lock.json` between `since_ref` and HEAD in the
> target repo, and flag major-version bumps or new dependencies. Then run it
> against `../release-captain-target` and show me the output.

## 4. Test subagent

> Implement `subagents/test.py` per its docstring contract. Read the test
> command from the target repo's `package.json` `scripts.test`, run it as a
> subprocess, and parse pass/fail counts from Node's built-in test runner
> output. Then run it against `../release-captain-target` and show me the
> output.

## 5. Spec subagent (document understanding)

> Implement `subagents/spec.py` per its docstring contract. Parse the
> checkbox list in the target repo's `requirements.md`, and for each
> requirement check whether matching code AND a matching test both exist in
> `src/routes/`. Then run it against `../release-captain-target` and show me
> the output — I expect all 8 requirements to come back covered.

## 6. Orchestrator (agent mode, parallel dispatch)

> Implement `orchestrator.py` per its docstring contract. Run the four
> subagents concurrently with a thread pool, not sequentially, and time the
> whole run with `time.perf_counter()`. Apply the NO-GO rule documented in
> the docstring. Then run `run_release_check` against
> `../release-captain-target` and show me the full report, including the
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
