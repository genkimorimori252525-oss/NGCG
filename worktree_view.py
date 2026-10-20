"""Read-only parent overview of a Git repository's linked worktrees.

The main worktree is the coordinator; each linked worktree keeps its existing
path-scoped NGCG documents and SQLite history. No migrations, automatic Git
operations, or promotion of a child's PASS/approval into parent authority.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

from approval_guard import read_approval
from loader import load_charter, load_current, load_evidence
from state_store import project_key, state_path


def _git_worktrees(root: Path) -> list[dict]:
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain", "-z"],
            cwd=root, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Could not inspect Git worktrees") from exc
    if result.returncode:
        raise ValueError("Not a usable Git worktree: " +
                         result.stderr.decode("utf-8", errors="replace").strip())
    records: list[dict] = []
    record: dict = {}
    for part in result.stdout.split(b"\0"):
        if not part:
            if record:
                records.append(record)
                record = {}
            continue
        key, _, value = part.partition(b" ")
        field = key.decode("ascii", errors="replace")
        record[field] = value.decode("utf-8", errors="surrogateescape") if value else True
    if record:
        records.append(record)
    if not records or records[0].get("bare"):
        raise ValueError("A non-bare main worktree is required")
    return records


def _latest_checkpoints(root: Path) -> tuple[list[dict], str | None]:
    """Never initialize an absent store merely to display its status."""
    database = state_path(root)
    if not database.is_file():
        return [], None
    uri = "file:" + quote(str(database.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT agent, task, status, created_at FROM checkpoints "
                "WHERE project_id=? ORDER BY rowid DESC LIMIT 3",
                (project_key(root),),
            ).fetchall()
        return [
            {"agent": row[0], "task": row[1], "status": row[2], "created_at": row[3]}
            for row in rows
        ], None
    except sqlite3.Error:
        return [], "Local checkpoint database could not be read"


def _inspect(record: dict, current: Path, role: str) -> dict:
    root = Path(record["worktree"]).resolve()
    branch_ref = record.get("branch")
    branch = (branch_ref.removeprefix("refs/heads/") if isinstance(branch_ref, str)
              else "(detached)" if record.get("detached") else "(unknown)")
    details = {
        "role": role, "path": str(root), "branch": branch,
        "head": record.get("HEAD", "unknown"), "is_current": root == current,
        "available": root.is_dir(), "locked": bool(record.get("locked")),
        "prunable": bool(record.get("prunable")),
        "charter_sha256": None, "charter_approval": "UNAPPROVED",
        "current_task": None, "current_status": "UNKNOWN",
        "evidence_counts": None, "latest_checkpoints": [], "warnings": [],
    }
    if not root.is_dir():
        details["warnings"].append("Worktree directory is missing")
        return details
    try:
        charter = load_charter(root)
        if charter["exists"]:
            details["charter_sha256"] = charter["sha256"]
            approval = read_approval(root)
            if approval is not None:
                details["charter_approval"] = (
                    "APPROVED" if approval.get("approved_sha256") == charter["sha256"]
                    else "APPROVAL_MISMATCH"
                )
        else:
            details["warnings"].append("No local project Charter")
        active = load_current(root)
        if active.get("exists"):
            details["current_task"] = active.get("current_task")
            details["current_status"] = active.get("metadata", {}).get("status", "UNKNOWN")
        details["evidence_counts"] = load_evidence(root)["counts"]
    except (OSError, UnicodeError, ValueError) as exc:
        details["warnings"].append("Continuity documents are not readable: " + type(exc).__name__)
    details["latest_checkpoints"], warning = _latest_checkpoints(root)
    if warning:
        details["warnings"].append(warning)
    return details


def worktree_report(root: Path) -> dict:
    """Return one parent's overview; no writes or branch-dependent assumptions."""
    here = Path(root).resolve()
    records = _git_worktrees(here)
    parent = _inspect(records[0], here, "parent")
    children = [_inspect(item, here, "variant") for item in records[1:]]
    count = len(children)
    return {
        "parent": parent, "variants": children, "variant_count": count,
        "recommended_variants": 2,
        "layout": "PARENT_AND_TWO_VARIANTS" if count == 2 else (
            "SINGLE_WORKTREE" if count == 0 else "PARTIAL" if count == 1 else "MORE_THAN_TWO"),
        "notice": (
            "Each worktree has its own path-scoped NGCG records. "
            "The parent view is read-only; child evidence and approval are not inherited "
            "as overall success. No agents, worktrees or merges are started automatically."
        ),
    }
