# Release Captain

An AI release-readiness assistant built on IBM Bob 2.0. Given a release
branch and a requirements document, it runs four subagents in parallel —
changelog, risk, test, spec — and assembles a go/no-go report with release
notes and a rollback plan.

## Layout

```
release-captain/
  subagents/
    changelog.py   # reads commit history -> release notes
    risk.py         # diffs dependencies -> breaking-change risk
    test.py         # runs the test suite -> pass/fail summary
    spec.py         # reads requirements.md -> coverage check (document understanding)
  orchestrator.py    # runs the four subagents in parallel, assembles the report
  report/app.py       # Streamlit UI for the report
  .bob/rules/         # Bob's project rules for this repo
  BOB_PROMPTS.md       # the exact prompts to build this with Bob 2.0, in order
```

## Target repo

This checks `release-captain-target` (a prepped copy of IBM's
`express-todo-api-modern` sample, on branch `release/v1.1.0`), expected to
be cloned as a sibling folder:

```
some-folder/
  release-captain/         <- this repo
  release-captain-target/   <- the repo being checked
```

## Getting started

1. Read `BOB_PROMPTS.md` and run its prompts in order inside Bob 2.0 IDE.
2. Every subagent has its input/output contract documented in its own
   docstring — build to that contract, don't guess the shape.
3. Fill in `../release-captain-target/BASELINE_TIMING.md` by hand, once,
   before or alongside building — that's the real "before" number.

## Status

- [ ] Baseline manual timing recorded
- [ ] `subagents/changelog.py`
- [ ] `subagents/risk.py`
- [ ] `subagents/test.py`
- [ ] `subagents/spec.py`
- [ ] `orchestrator.py` (parallel dispatch)
- [ ] `report/app.py` + hosted demo link
- [ ] Saved as a Bob Workflow
