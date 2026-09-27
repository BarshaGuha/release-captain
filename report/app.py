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

import os
import subprocess
import sys

# Ensure the repo root is on sys.path so `import orchestrator` and
# `from subagents import ...` resolve correctly when Streamlit runs
# this file from any working directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import orchestrator  # noqa: E402 — must come after sys.path fix

import streamlit as st

# ---------------------------------------------------------------------------
# Config — points at the sibling target repo.
# Override via environment variables for deployment on other repos.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_PATH         = os.environ.get("RC_REPO_PATH",    os.path.join(_HERE, "..", "release-captain-target"))
REPO_PATH         = os.path.abspath(REPO_PATH)
TARGET_CLONE_URL  = "https://github.com/BarshaGuha/release-captain-target.git"
SINCE_REF         = os.environ.get("RC_SINCE_REF",    "763d71b")
REQUIREMENTS_PATH = os.environ.get("RC_REQUIREMENTS", "requirements.md")
TEST_COMMAND      = os.environ.get("RC_TEST_COMMAND",  None)   # None → read from package.json

# Branch choices shown in the UI
BRANCH_OPTIONS = {
    "release/v1.2.0 (GO scenario)":           "release/v1.2.0",
    "release/v1.2.0-regression (NO-GO scenario)": "release/v1.2.0-regression",
}
DEFAULT_BRANCH_LABEL = "release/v1.2.0 (GO scenario)"


# ---------------------------------------------------------------------------
# System bootstrap — install Node 22 on Streamlit Cloud if missing
# ---------------------------------------------------------------------------

_NODE_SENTINEL = os.path.join(os.path.expanduser("~"), ".node22_installed")


def _ensure_node() -> None:
    """Install Node.js 22 via NodeSource if it isn't available.

    Only runs once per Cloud instance (sentinel file guards repeat installs).
    On a developer's machine where node >=22 is already present this is a
    no-op (the `node --version` check exits immediately).
    """
    # Fast path: node already present and recent enough
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ver = result.stdout.strip()          # e.g. "v22.14.0"
            major = int(ver.lstrip("v").split(".")[0])
            if major >= 22:
                return                            # already good
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        pass                                      # node missing or too old — fall through

    if os.path.exists(_NODE_SENTINEL):
        # Already tried installing this session — don't loop
        st.error("Node.js 22 install was attempted but node is still not found. Check app logs.")
        st.stop()

    st.info("⏳ Installing Node.js 22 — this only happens on the first cold start…")
    with st.spinner("Setting up Node.js 22 via NodeSource…"):
        subprocess.run(
            "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
            shell=True,
            check=True,
        )
        subprocess.run(
            ["apt-get", "install", "-y", "nodejs"],
            check=True,
        )
    open(_NODE_SENTINEL, "w").close()             # mark as done
    st.success("✅ Node.js 22 installed.")
    st.rerun()


# ---------------------------------------------------------------------------
# Repo bootstrap — clone + npm install if the folder doesn't exist
# ---------------------------------------------------------------------------

def _ensure_repo() -> None:
    """Clone the target repo if it isn't already present, then npm install."""
    if not os.path.isdir(REPO_PATH):
        st.info("⏳ Cloning target repo — this only happens once on first run…")
        with st.spinner("Cloning target repo from GitHub…"):
            subprocess.run(
                ["git", "clone", TARGET_CLONE_URL, REPO_PATH],
                check=True,
                capture_output=True,
                text=True,
            )
        with st.spinner("Installing Node dependencies (npm install)…"):
            subprocess.run(
                ["npm", "install"],
                cwd=REPO_PATH,
                check=True,
                capture_output=True,
                text=True,
            )
        st.success("✅ Repo cloned and dependencies installed.")
        st.rerun()   # re-render now that the folder exists


def _checkout_branch(branch: str) -> None:
    """Check out *branch* in the target repo (fetch first to ensure it's present)."""
    subprocess.run(
        ["git", "fetch", "--all"],
        cwd=REPO_PATH, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "checkout", branch],
        cwd=REPO_PATH, check=True, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Release check runner
# ---------------------------------------------------------------------------

def _run(branch: str) -> dict:
    _checkout_branch(branch)
    req_path = os.path.join(REPO_PATH, REQUIREMENTS_PATH)
    return orchestrator.run_release_check(
        repo_path=REPO_PATH,
        since_ref=SINCE_REF,
        requirements_path=req_path,
        test_command=TEST_COMMAND,
    )


# ---------------------------------------------------------------------------
# Render helpers (unchanged)
# ---------------------------------------------------------------------------

def _render_verdict(report: dict) -> None:
    verdict = report["verdict"]
    duration = report["duration_seconds"]
    generated = report["generated_at"]

    col_v, col_d, col_t = st.columns([2, 1, 2])
    with col_v:
        if verdict == "GO":
            st.success("## ✅ GO — ready to ship", icon=None)
        else:
            st.error("## ❌ NO-GO — do not ship", icon=None)
    with col_d:
        st.metric("Run duration", f"{duration}s")
    with col_t:
        st.caption(f"Generated: {generated}")


def _render_changelog(data: dict) -> None:
    if "error" in data:
        st.error(f"Changelog subagent failed: {data['error']}")
        return

    st.markdown(f"**{data.get('commit_count', 0)} commit(s) since baseline**")
    st.markdown(data.get("release_notes_markdown", "_No commits found._"))

    commits = data.get("commits", [])
    if commits:
        with st.expander("Raw commit list"):
            rows = [
                {"Hash": c["hash"], "Kind": c["kind"], "Summary": c["summary"]}
                for c in commits
            ]
            st.table(rows)


def _render_risk(data: dict) -> None:
    if "error" in data:
        st.error(f"Risk subagent failed: {data['error']}")
        return

    level = data.get("risk_level", "unknown").upper()
    level_color = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(level, "⚪")
    st.markdown(f"**Overall risk: {level_color} {level}**")

    findings = data.get("findings", [])
    if not findings:
        st.caption("No dependency changes detected.")
    else:
        rows = [
            {
                "Package": f["package"],
                "Change": f["change"],
                "Severity": f["severity"].upper(),
                "Reason": f["reason"],
            }
            for f in findings
        ]
        st.table(rows)


def _render_tests(data: dict) -> None:
    if "error" in data:
        st.error(f"Test subagent failed: {data['error']}")
        return

    passed  = data.get("passed", False)
    total   = data.get("total", 0)
    failed  = data.get("failed", 0)
    dur     = data.get("duration_seconds", 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Result",   "PASSED ✅" if passed else "FAILED ❌")
    c2.metric("Total",    total)
    c3.metric("Failed",   failed)
    c4.metric("Duration", f"{dur}s")

    failures = data.get("failures", [])
    if failures:
        st.markdown("**Failures:**")
        for f in failures:
            st.error(f"**{f['name']}** — {f['message']}")

    raw = data.get("raw_output_tail", "")
    if raw:
        with st.expander("Full test output"):
            st.code(raw, language=None)


def _render_spec(data: dict) -> None:
    if "error" in data:
        st.error(f"Spec subagent failed: {data['error']}")
        return

    total   = data.get("total_requirements", 0)
    covered = data.get("covered", 0)

    frac = covered / total if total else 0
    st.metric("Coverage", f"{covered}/{total}")
    st.progress(frac)

    reqs = data.get("requirements", [])
    if reqs:
        rows = []
        for r in reqs:
            status_icon = {"covered": "✅", "not_covered": "❌", "unclear": "⚠️"}.get(r["status"], "❓")
            rows.append({
                "ID": r["id"],
                "Status": f"{status_icon} {r['status']}",
                "Summary": r["text"][:80] + ("…" if len(r["text"]) > 80 else ""),
                "Evidence": r["evidence"],
            })
        st.dataframe(rows, width="stretch", hide_index=True)


def _render_rollback(text: str) -> None:
    st.markdown(text)


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Release Captain", page_icon="🚢", layout="wide")

st.title("🚢 Release Captain")

# ── Ensure Node.js 22 is available (installs on Streamlit Cloud if missing) ──
_ensure_node()

# ── Ensure the target repo exists (clone on first run) ────────────────────
_ensure_repo()

# ── Branch selector ───────────────────────────────────────────────────────
st.markdown("**Select a scenario to check:**")
branch_label = st.radio(
    label="Branch",
    options=list(BRANCH_OPTIONS.keys()),
    index=0,
    horizontal=True,
    label_visibility="collapsed",
)
selected_branch = BRANCH_OPTIONS[branch_label]

st.caption(
    f"Checking `release-captain-target` · branch `{selected_branch}` "
    f"· since `{SINCE_REF}` · requirements: `{REQUIREMENTS_PATH}`"
)

# ── Run check — re-run whenever the branch selection changes ──────────────
if (
    "report" not in st.session_state
    or st.session_state.get("last_branch") != selected_branch
):
    with st.spinner(f"Running release check on `{selected_branch}`…"):
        st.session_state["report"] = _run(selected_branch)
        st.session_state["last_branch"] = selected_branch

report = st.session_state["report"]

# ── Verdict banner ────────────────────────────────────────────────────────
_render_verdict(report)

st.divider()

# ── Four subagent sections ────────────────────────────────────────────────
tab_cl, tab_risk, tab_tests, tab_spec = st.tabs(
    ["📋 Changelog", "⚠️ Risk", "🧪 Tests", "📐 Spec Coverage"]
)

with tab_cl:
    st.subheader("Changelog")
    _render_changelog(report.get("changelog", {}))

with tab_risk:
    st.subheader("Dependency Risk")
    _render_risk(report.get("risk", {}))

with tab_tests:
    st.subheader("Test Results")
    _render_tests(report.get("tests", {}))

with tab_spec:
    st.subheader("Requirement Coverage")
    _render_spec(report.get("spec", {}))

st.divider()

# ── Rollback plan ─────────────────────────────────────────────────────────
st.subheader("🔙 Rollback Plan")
_render_rollback(report.get("rollback_plan_markdown", "_Not available._"))

st.divider()

# ── Run again button ──────────────────────────────────────────────────────
if st.button("🔄 Run again", type="primary"):
    del st.session_state["report"]
    st.rerun()
