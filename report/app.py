"""Release Captain report — Streamlit view.

Build this with Bob 2.0 — see BOB_PROMPTS.md, prompt 7.
This is the "interface & hosting" piece: a page that takes the orchestrator's
output and shows a real, readable go/no-go report. This is what gets
deployed for the demo link.

Expected shape:
- A clear GO / NO-GO verdict at the top, with the run duration.
- Four sections (Changelog, Risk, Tests, Spec coverage), each rendering
  its subagent's output.
- A rollback plan section.
- A "run again" button that calls orchestrator.run_release_check(...) live,
  so the demo can show the real thing happening, not a canned screenshot.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Release Captain", layout="wide")
st.title("Release Captain")
st.caption("Build this with Bob 2.0 — see BOB_PROMPTS.md, prompt 7.")
