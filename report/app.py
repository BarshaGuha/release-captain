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

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

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
    "release/v1.3.0-dependency-risk (HIGH RISK scenario)": "release/v1.3.0-dependency-risk",
    "release/v1.4.0-spec-warning (GO-WITH-WARNINGS scenario)": "release/v1.4.0-spec-warning",
}
DEFAULT_BRANCH_LABEL = "release/v1.2.0 (GO scenario)"


# ---------------------------------------------------------------------------
# System bootstrap — install Node 22 LTS from official tarball (no root)
# ---------------------------------------------------------------------------

_NODE_VERSION  = "v22.14.0"
_NODE_TARBALL  = f"node-{_NODE_VERSION}-linux-x64.tar.xz"
_NODE_URL      = f"https://nodejs.org/dist/{_NODE_VERSION}/{_NODE_TARBALL}"
# Install into a private directory under the user's home so no root is needed.
_NODE_INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".local", "node22")
_NODE_BIN_DIR     = os.path.join(_NODE_INSTALL_DIR, f"node-{_NODE_VERSION}-linux-x64", "bin")
_NODE_SENTINEL    = os.path.join(os.path.expanduser("~"), ".node22_installed")


def _node_version_ok() -> bool:
    """Return True if the first `node` reachable on PATH is version >=22."""
    try:
        r = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            major = int(r.stdout.strip().lstrip("v").split(".")[0])
            return major >= 22
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        pass
    return False


def _node_version_str() -> str:
    """Return the raw version string of the node currently on PATH, or 'not found'."""
    try:
        r = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "not found"


def _inject_node_bin_to_path() -> None:
    """Prepend the extracted Node 22 bin/ dir to os.environ['PATH']."""
    if _NODE_BIN_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _NODE_BIN_DIR + os.pathsep + os.environ.get("PATH", "")


def _ensure_node() -> None:
    """Make Node.js 22 available without root or apt-get.

    Fast path: if `node --version` already reports >=22 (e.g. on a developer
    machine) this is a complete no-op.

    Otherwise the official Linux x64 tarball is downloaded once from
    nodejs.org using Python's urllib, extracted with Python's tarfile module
    into ~/.local/node22 (a directory the process already owns), and its bin/
    directory is prepended to PATH for the lifetime of this Python process.

    A sentinel file prevents the download/extraction from running more than
    once per Cloud instance (cold-start guard).
    """
    # On every start, try to inject the bin dir in case a previous cold start
    # already installed Node — this is cheap and idempotent.
    _inject_node_bin_to_path()

    if _node_version_ok():
        return  # fast path: node >=22 already on PATH

    if os.path.exists(_NODE_SENTINEL):
        # A previous attempt wrote the sentinel but node is still missing
        # (e.g. nvm tried to compile from source and was OOM-killed, or the
        # tarball extraction was interrupted).  Remove the stale sentinel and
        # fall through to try again — this makes cold-start failures
        # self-healing rather than permanently broken.
        os.remove(_NODE_SENTINEL)

    st.info("⏳ Downloading Node.js 22 LTS — this only happens on the first cold start…")

    os.makedirs(_NODE_INSTALL_DIR, exist_ok=True)

    with st.spinner(f"Downloading {_NODE_TARBALL} from nodejs.org…"):
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tar.xz")
        os.close(tmp_fd)
        try:
            urllib.request.urlretrieve(_NODE_URL, tmp_path)
            with st.spinner("Extracting Node.js 22…"):
                with tarfile.open(tmp_path, "r:xz") as tf:
                    # filter="data" is required on Python >=3.12 to avoid
                    # DeprecationWarning-as-error; safe on older versions too.
                    tf.extractall(
                        _NODE_INSTALL_DIR,
                        filter="data" if sys.version_info >= (3, 12) else None,
                    )
        finally:
            os.unlink(tmp_path)

    # Inject the new bin dir so _node_version_ok() sees the fresh binary.
    _inject_node_bin_to_path()

    # Sanity-check: confirm the extracted binary is actually >=22 before
    # writing the sentinel.  If it isn't, stop with a clear, actionable
    # error rather than letting `npm test` fail silently on the wrong flag.
    if not _node_version_ok():
        found = _node_version_str()
        st.error(
            f"❌ Node.js >=22 is required (target repo uses "
            f"`--experimental-strip-types`, which Node 22+ added), but the "
            f"installed binary reports **{found}**.  "
            f"Delete `{_NODE_SENTINEL}` (if present) and redeploy so the "
            f"correct tarball is fetched."
        )
        st.stop()

    open(_NODE_SENTINEL, "w").close()
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
    """Check out *branch* in the target repo.

    Fetch strategy: only fetch when the remote tracking ref for *branch*
    is absent locally (first time) or when the local HEAD sha differs from
    origin's sha (someone pushed new commits). This avoids a ~4 s network
    round-trip to GitHub on every branch switch when nothing has changed.
    """
    def _sha(ref: str) -> str:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=REPO_PATH, capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    remote_ref = f"refs/remotes/origin/{branch}"
    local_sha  = _sha(remote_ref)

    needs_fetch = True
    if local_sha:
        # Remote ref exists locally — do a lightweight ls-remote instead of
        # a full fetch to check whether the remote has moved.
        ls = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=REPO_PATH, capture_output=True, text=True, timeout=10,
        )
        remote_sha = ls.stdout.split()[0] if ls.stdout.strip() else ""
        needs_fetch = remote_sha and remote_sha != local_sha

    if needs_fetch:
        subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=REPO_PATH, check=True, capture_output=True, text=True,
        )

    # Discard any modifications to tracked files (e.g. package-lock.json
    # written by npm install in test.py) so they never block the checkout.
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=REPO_PATH, check=False, capture_output=True, text=True,
    )
    # Use -B so the branch is created (or reset) from the remote tracking ref.
    subprocess.run(
        ["git", "checkout", "-B", branch, f"origin/{branch}"],
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
        branch=branch,
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
        elif verdict == "GO-WITH-WARNINGS":
            st.warning("## ⚠️ GO — WITH WARNINGS", icon=None)
        else:
            st.error("## ❌ NO-GO — do not ship", icon=None)
    with col_d:
        st.metric("Run duration", f"{duration}s")
    with col_t:
        st.caption(f"Generated: {generated}")


def _render_warnings(warnings: list[dict]) -> None:
    if not warnings:
        return
    st.subheader("⚠️ Warnings (tracked, non-blocking)")
    st.caption(
        "These didn't block the release, but they're recorded with an "
        "owner and a review date rather than silently passing."
    )
    rows = [
        {
            "Source": w["source"],
            "Finding": w["id"],
            "Description": w["description"],
            "Owner": w["owner"],
            "Review by": w["expiry"],
            "Rationale": w["rationale"],
        }
        for w in warnings
    ]
    st.dataframe(rows, width="stretch", hide_index=True)


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


def _fmt_timestamp(iso: str) -> str:
    """Convert ISO timestamp to a human-readable local string, e.g. 'Sep 30, 14:23'."""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso)
        # Convert UTC to local time
        dt_local = dt.astimezone()
        return dt_local.strftime("%b %d, %H:%M")
    except Exception:
        return iso  # fall back to raw string if parsing fails


def _render_history() -> None:
    """Read run_history/index.jsonl and render a newest-first history table."""
    history_dir = orchestrator._DEFAULT_HISTORY_DIR
    index_path = history_dir / "index.jsonl"

    if not index_path.exists():
        st.info(
            "📭 No run history yet — history is recorded here automatically "
            "each time you run a release check. Come back after your first run."
        )
        return

    # Parse all lines, skip blank/corrupt entries silently
    rows = []
    raw_lines = index_path.read_text(encoding="utf-8").splitlines()
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(entry)

    if not rows:
        st.info("📭 History file exists but contains no valid entries yet.")
        return

    # Newest first
    rows.sort(key=lambda r: r.get("generated_at", ""), reverse=True)

    _VERDICT_ICON = {
        "GO":               "🟢 GO",
        "GO-WITH-WARNINGS": "🟡 GO WITH WARNINGS",
        "NO-GO":            "🔴 NO-GO",
    }
    _RISK_ICON = {"low": "🟢", "medium": "🟡", "high": "🔴", "unknown": "⚪"}

    table_rows = []
    for r in rows:
        verdict     = r.get("verdict", "UNKNOWN")
        risk_level  = r.get("risk_level", "unknown")
        duration    = r.get("duration_seconds")
        warning_count = r.get("warning_count", 0)

        # Branch: strip the "release/" prefix for compactness, fall back to commit SHA
        branch = r.get("branch", "")
        branch_label = branch.replace("release/", "") if branch else r.get("repo_commit", "unknown")[:7]

        # Warnings column: blank when none so it doesn't clutter the happy path
        warnings_str = f"⚠️ {warning_count}" if warning_count else "—"

        table_rows.append({
            "When":         _fmt_timestamp(r.get("generated_at", "")),
            "Branch":       branch_label,
            "Verdict":      _VERDICT_ICON.get(verdict, f"⚪ {verdict}"),
            "Tests":        r.get("test_summary", "—"),
            "Risk":         f"{_RISK_ICON.get(risk_level, '⚪')} {risk_level.upper()}",
            "Spec":         r.get("spec_summary", "—"),
            "Duration":     f"{duration}s" if duration is not None else "—",
            "Warnings":     warnings_str,
        })

    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    # Expander: let the user browse the full JSON of any individual run
    with st.expander("🔍 Browse a full run record"):
        # Label each option by branch + timestamp so it's human-scannable
        def _run_label(r: dict) -> str:
            branch = r.get("branch", "")
            branch_label = branch.replace("release/", "") if branch else r.get("repo_commit","")[:7]
            return f"{_fmt_timestamp(r.get('generated_at',''))}  ·  {branch_label}  ·  {r.get('verdict','')}"

        run_options = [r for r in rows if r.get("run_id")]
        if not run_options:
            st.caption("No run IDs available.")
        else:
            selected = st.selectbox(
                "Select a run",
                options=run_options,
                format_func=_run_label,
                label_visibility="collapsed",
            )
            record_path = history_dir / f"{selected['run_id']}.json"
            if record_path.exists():
                full_record = json.loads(record_path.read_text(encoding="utf-8"))
                st.json(full_record, expanded=False)
            else:
                st.warning(
                    "Full record not found — it may have been deleted or "
                    "the app was redeployed (Streamlit Cloud resets the filesystem on restart)."
                )


# ---------------------------------------------------------------------------
# Environment bootstrap — cached so it runs once per server process, not on
# every rerun (every widget interaction re-executes this whole script; the
# Node-version check and repo-existence check are cheap individually, but
# there's no reason to redo them dozens of times per session, and this is
# also the natural place a future, more expensive bootstrap step would need
# this guard). st.cache_resource shares its result across all sessions on
# this server process, matching the sentinel-file idempotency _ensure_node
# already relies on.
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _bootstrap_environment() -> bool:
    _ensure_node()
    _ensure_repo()
    return True


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Release Captain", page_icon="🚢", layout="wide")

st.title("🚢 Release Captain")

# ── Ensure Node.js 22 and the target repo are ready (cached — see above) ──
_bootstrap_environment()

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
_render_warnings(report.get("warnings", []))

st.divider()

# ── Four subagent sections ────────────────────────────────────────────────
tab_cl, tab_risk, tab_tests, tab_spec, tab_hist = st.tabs(
    ["📋 Changelog", "⚠️ Risk", "🧪 Tests", "📐 Spec Coverage", "📜 History"]
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

with tab_hist:
    st.subheader("Run History")
    _render_history()

st.divider()

# ── Rollback plan ─────────────────────────────────────────────────────────
st.subheader("🔙 Rollback Plan")
_render_rollback(report.get("rollback_plan_markdown", "_Not available._"))

st.divider()

# ── Run again button ──────────────────────────────────────────────────────
if st.button("🔄 Run again", type="primary"):
    del st.session_state["report"]
    st.rerun()
