# Release Captain — rules for Bob

This file scopes how Bob should work in this repo. It's what the pitch deck
means by "rules & workflow" keeping every run structured and repeatable.

## Project shape

- `subagents/changelog.py`, `subagents/risk.py`, `subagents/test.py`,
  `subagents/spec.py` — one job each, each returns a plain dict matching the
  contract documented in its own docstring. Don't let one subagent import
  or depend on another; they must be runnable independently and in
  parallel.
- `orchestrator.py` — runs all four concurrently and assembles the report.
  Never call the subagents sequentially in this file.
- `report/app.py` — Streamlit page that renders the orchestrator's output.
- The target repo (`release-captain-target`, or whichever repo is being
  checked) is always passed in as a path argument. Never hardcode a path
  to it inside `subagents/` or `orchestrator.py`.

## Output discipline

- Every subagent returns real findings from real commands (`git log`,
  `npm test`, reading `requirements.md`) — never a fabricated or
  placeholder result, even while the code is still a stub.
- If a subagent can't determine something, it says so explicitly in its
  output rather than guessing.

## When building

- Implement one subagent at a time; run it standalone against
  `release-captain-target` before moving to the next.
- Keep functions small enough to unit-test; add a quick manual check
  (a `if __name__ == "__main__":` block or a short script) per subagent
  so its output can be eyeballed before wiring it into the orchestrator.
