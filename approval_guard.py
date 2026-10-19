"""An external, explicitly confirmed charter anchor.

This detects ordinary accidental/self-hash edits. Because CCG and the agent
may run under the same Windows account, this is NOT a cryptographic boundary
against a malicious local process with identical OS permissions.
"""
import json
import os
from pathlib import Path

from state_store import project_key, state_path, utc_now
from atomic_io import atomic_write_text, compute_sha256


def approval_path(root: Path) -> Path:
    return state_path(root).parent / "approved_charter.json"


def read_approval(root: Path) -> dict | None:
    path = approval_path(root)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("project_id") == project_key(root) and d.get("state") == "APPROVED":
            return d
    except (FileNotFoundError, ValueError, OSError):
        pass
    return None


def approve_interactive(root: Path) -> str:
    import sys
    if not sys.stdin.isatty():
        raise RuntimeError("Human approval requires an interactive TTY; agents must not auto-approve")
    charter = root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
    if not charter.is_file():
        raise FileNotFoundError(charter)
    payload = charter.read_text(encoding="utf-8")
    sha = compute_sha256(payload)
    print(f"Review this Charter: {charter}")
    print(f"Full file SHA-256: {sha}")
    print("An external anchor detects later accidental edits, not same-account malicious changes.")
    challenge = f"APPROVE {sha[:12]}"
    if input(f"Type exactly '{challenge}' after reviewing the document: ").strip() != challenge:
        raise RuntimeError("Approval cancelled")
    record = {"version": 1, "state": "APPROVED", "project_id": project_key(root),
              "approved_sha256": sha, "approved_at": utc_now(), "method": "interactive-human"}
    path = approval_path(root)
    atomic_write_text(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return str(path)
