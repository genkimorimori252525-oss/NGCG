"""Loader module for Codex Continuity Guard (CCG).

Discovers project roots, loads Charter, Requirements, Amendments, Current state,
Evidence, Beads state, OpenSpec status, and Git metadata.
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    from tools.continuity_guard.atomic_io import (
        safe_read_text,
        compute_sha256,
        normalize_newlines
    )
except ImportError:
    from atomic_io import (
        safe_read_text,
        compute_sha256,
        normalize_newlines
    )


def find_git_root(start_path: Optional[Path] = None) -> Path:
    """Find Git root directory from start_path upwards, or fallback to start_path."""
    curr = Path(start_path or Path.cwd()).resolve()
    for parent in [curr] + list(curr.parents):
        if (parent / ".git").exists():
            return parent
    return curr


def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML-like frontmatter enclosed by --- and return metadata and body."""
    meta: Dict[str, Any] = {}
    normalized = normalize_newlines(text).strip()
    if normalized.startswith("---"):
        parts = normalized.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2].strip()
            for line in fm_text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    meta[key] = val
            return meta, body
    return meta, normalized


def load_charter(project_root: Path) -> Dict[str, Any]:
    """Load docs/ai-continuity/PROJECT_CHARTER.md and calculate its SHA-256."""
    charter_path = project_root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
    if not charter_path.exists():
        return {
            "exists": False,
            "path": str(charter_path),
            "sha256": None,
            "metadata": {},
            "raw_content": "",
            "summary": "CHARTER_NOT_FOUND"
        }

    raw = safe_read_text(charter_path)
    return parse_charter(raw, charter_path)


def parse_charter(raw: str, charter_path: Path) -> Dict[str, Any]:
    """Parse the same source snapshot that is hashed and displayed."""
    sha256 = compute_sha256(raw)
    meta, body = parse_frontmatter(raw)
    body_sha256 = compute_sha256(body)

    return {
        "exists": True,
        "path": str(charter_path),
        "sha256": sha256,
        "body_sha256": body_sha256,
        "metadata": meta,
        "raw_content": raw,
        "body": body,
        "approved_by": meta.get("approved_by", "UNKNOWN"),
        "approved_at": meta.get("approved_at", "UNKNOWN"),
        "charter_version": meta.get("charter_version", "1.0.0"),
        "expected_sha256": meta.get("approved_sha256", meta.get("sha256", None))
    }


def load_requirements(project_root: Path) -> List[Dict[str, Any]]:
    """Parse requirements from docs/ai-continuity/REQUIREMENTS.md."""
    req_path = project_root / "docs" / "ai-continuity" / "REQUIREMENTS.md"
    if not req_path.exists():
        return []

    content = safe_read_text(req_path)
    requirements = []
    # Match headers or bullet points like ### REQ-001: Description or - **REQ-001**: Description
    pattern = re.compile(r"(?:###|\*\*|-)\s*(REQ-[A-Za-z0-9_-]+)[:\s]+(.*?)(?=\n(?:###|\*\*|-)\s*REQ-|\Z)", re.DOTALL)
    for match in pattern.finditer(content):
        req_id = match.group(1).strip()
        body = match.group(2).strip()
        lines = body.split("\n")
        title = lines[0].strip(" *#")
        status = "CONFIRMED"
        for line in lines:
            if "status:" in line.lower():
                status = line.split(":", 1)[1].strip().upper()
        requirements.append({
            "id": req_id,
            "title": title,
            "body": body,
            "status": status
        })
    return requirements


def load_amendments(project_root: Path) -> List[Dict[str, Any]]:
    """Load user amendments from docs/ai-continuity/AMENDMENTS/."""
    amend_dir = project_root / "docs" / "ai-continuity" / "AMENDMENTS"
    if not amend_dir.exists():
        return []

    amendments = []
    for file in sorted(amend_dir.glob("*.md")):
        raw = safe_read_text(file)
        meta, body = parse_frontmatter(raw)
        amd_id = meta.get("amendment_id", file.stem)
        amendments.append({
            "id": amd_id,
            "file": file.name,
            "path": str(file),
            "metadata": meta,
            "status": meta.get("status", "PROPOSED").upper(),
            "approved_by": meta.get("approved_by", None),
            "supersedes": meta.get("supersedes", None),
            "body": body,
            "summary": meta.get("summary", body[:200])
        })
    return amendments


def load_decisions(project_root: Path) -> List[Dict[str, Any]]:
    """Load Architectural Decision Records (ADRs) from docs/ai-continuity/DECISIONS/."""
    dec_dir = project_root / "docs" / "ai-continuity" / "DECISIONS"
    if not dec_dir.exists():
        return []

    decisions = []
    for file in sorted(dec_dir.glob("*.md")):
        raw = safe_read_text(file)
        meta, body = parse_frontmatter(raw)
        adr_id = meta.get("decision_id", file.stem)
        decisions.append({
            "id": adr_id,
            "file": file.name,
            "metadata": meta,
            "status": meta.get("status", "ACCEPTED").upper(),
            "title": meta.get("title", file.stem),
            "body": body
        })
    return decisions


def load_evidence(project_root: Path) -> Dict[str, Any]:
    """Parse EVIDENCE_INDEX.md for test results and proof."""
    evid_path = project_root / "docs" / "ai-continuity" / "EVIDENCE_INDEX.md"
    content = safe_read_text(evid_path) if evid_path.exists() else ""
    return parse_evidence(content)


def parse_evidence(content: str) -> Dict[str, Any]:
    """Read table structure, preserving opaque, case-sensitive task IDs.

    Unknown/missing statuses and malformed rows remain visible, never PASS.
    No row is resolved or superseded merely because a later row succeeds.
    """
    counts = dict.fromkeys(("PASS", "FAIL", "NOT_RUN", "BLOCKED", "UNKNOWN"), 0)
    items, diagnostics = [], []
    id_col, status_col = 0, 1
    ref_col = 3
    id_headers = {"id", "task", "task id", "task_id", "evidence_id", "タスク", "タスクid", "検証id"}
    status_headers = {"status", "state", "result", "状態", "結果", "検証結果"}
    ref_headers = {"reference", "ref", "evidence", "参照", "証拠", "参照先"}
    fence_char, fence_size = "", 0
    source_lines = content.splitlines()
    for number, raw in enumerate(source_lines, 1):
        stripped = raw.strip()
        fence = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence:
            marker, tail = fence.groups()
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size and not tail.strip():
                fence_char, fence_size = "", 0
            continue
        if fence_char or "|" not in stripped or stripped.startswith("#"):
            continue
        cells = re.split(r"(?<!\\)\|", stripped)
        if not cells[0].strip():
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        cells = [c.strip().strip("`*").strip() for c in cells]
        if not cells or all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        folded = [c.casefold() for c in cells]
        next_line = source_lines[number].strip() if number < len(source_lines) else ""
        separators = [c.strip() for c in next_line.strip("|").split("|")]
        header_separator = bool(separators) and all(re.fullmatch(r":?-{2,}:?", c) for c in separators)
        if (header_separator and any(c in id_headers for c in folded)
                and any(c in status_headers for c in folded)):
            id_col = next(i for i, c in enumerate(folded) if c in id_headers)
            status_col = next(i for i, c in enumerate(folded) if c in status_headers)
            ref_col = next((i for i, c in enumerate(folded) if c in ref_headers), 3)
            continue
        task_id = cells[id_col] if id_col < len(cells) else ""
        raw_status = cells[status_col] if status_col < len(cells) else ""
        status = raw_status.upper().replace("NOT RUN", "NOT_RUN")
        if not task_id:
            diagnostics.append({"line": number, "reason": "MISSING_TASK_ID", "raw_line": raw})
            continue
        if status not in counts:
            diagnostics.append({"line": number, "reason": "UNKNOWN_STATUS", "raw_line": raw})
            status = "UNKNOWN"
        counts[status] += 1
        items.append({"id": task_id, "status": status, "raw_status": raw_status,
                      "line": number, "raw_line": raw, "columns": cells,
                      "reference": cells[ref_col] if len(cells) > ref_col else ""})
    return {"items": items, "counts": counts, "diagnostics": diagnostics,
            "layout": (id_col, status_col, ref_col)}


def load_current(project_root: Path) -> Dict[str, Any]:
    """Load docs/ai-continuity/CURRENT.md state file."""
    curr_path = project_root / "docs" / "ai-continuity" / "CURRENT.md"
    if not curr_path.exists():
        return {
            "exists": False,
            "current_task": "None",
            "active_agent": "None",
            "branch": "None",
            "raw_content": ""
        }

    raw = safe_read_text(curr_path)
    return parse_current(raw)


def parse_current(raw: str) -> Dict[str, Any]:
    meta, body = parse_frontmatter(raw)
    return {
        "exists": True,
        "metadata": meta,
        "current_task": meta.get("current_task", "None"),
        "active_agent": meta.get("active_agent", "None"),
        "branch": meta.get("branch", "None"),
        "body": body,
        "raw_content": raw
    }


def load_git_state(project_root: Path) -> Dict[str, Any]:
    """Inspect Git branch, HEAD commit, and dirty status."""
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return {"is_git": False, "branch": "unknown", "head": "unknown", "dirty": False}

    try:
        # Branch
        br_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3
        )
        branch = br_res.stdout.strip() if br_res.returncode == 0 else "unknown"

        # HEAD commit
        hd_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3
        )
        head = hd_res.stdout.strip() if hd_res.returncode == 0 else "unknown"

        # Dirty status
        st_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3
        )
        dirty = bool(st_res.stdout.strip()) if st_res.returncode == 0 else False

        return {
            "is_git": True,
            "branch": branch,
            "head": head,
            "dirty": dirty
        }
    except Exception as e:
        return {"is_git": True, "branch": "unknown", "head": "unknown", "dirty": False, "error": str(e)}


def load_beads_state(project_root: Path) -> Dict[str, Any]:
    """Detect Beads (.beads) directory or bd CLI status."""
    beads_dir = project_root / ".beads"
    has_dir = beads_dir.exists()
    cli_available = shutil.which("bd") is not None

    tasks: List[Dict[str, Any]] = []
    if cli_available and has_dir:
        try:
            res = subprocess.run(
                ["bd", "ready", "--json"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=4
            )
            if res.returncode == 0:
                tasks = json.loads(res.stdout)
        except Exception:
            pass

    return {
        "installed": cli_available,
        "configured": has_dir,
        "ready_tasks": tasks
    }


def load_openspec_state(project_root: Path) -> Dict[str, Any]:
    """Detect OpenSpec (openspec/) directory or CLI status."""
    openspec_dir = project_root / "openspec"
    has_dir = openspec_dir.exists()
    cli_available = shutil.which("openspec") is not None
    return {
        "installed": cli_available,
        "configured": has_dir
    }


def build_recovery_packet(project_root: Path, brief: bool = True, max_chars: int = 2500) -> Dict[str, Any]:
    """Brief is a bounded source index; full explicitly expands original records."""
    if brief:
        from recovery import build_brief
        return build_brief(project_root, max_chars=max_chars)
    charter = load_charter(project_root)
    amendments = load_amendments(project_root)
    current = load_current(project_root)
    evidence = load_evidence(project_root)
    git_state = load_git_state(project_root)
    beads = load_beads_state(project_root)
    lines = ["=== CODEX CONTINUITY RECOVERY PACKET ===", f"PROJECT_ROOT: {project_root}"]
    lines.append(f"GIT: branch={git_state['branch']}, head={git_state['head'][:8]}, dirty={git_state['dirty']}")
    if charter["exists"]:
        meta = charter["metadata"]
        lines.append(f"[CHARTER] {meta.get('project_id', 'unknown')} v{charter['charter_version']} SHA={charter['sha256'][:12]}")
        if charter.get("expected_sha256") and charter["expected_sha256"] != charter["body_sha256"]:
            lines.append("INTEGRITY: CHARTER HASH MISMATCH; run audit before relying on this state.")
        lines.append("Core Mission & Goal:\n" + charter.get("body", ""))
    else:
        lines.append("[CHARTER] WARNING: PROJECT_CHARTER.md NOT FOUND")

    for amendment in amendments:
        approved = amendment["status"] == "APPROVED"
        if approved or not brief:
            label = "APPROVED USER AMENDMENTS" if approved else "UNAPPROVED AMENDMENT"
            lines.append(f"[{label}] {amendment['id']} status={amendment['status']} by={amendment['approved_by']} supersedes={amendment['supersedes']}")
            if amendment["metadata"].get("summary"):
                lines.append(amendment["metadata"]["summary"])
            lines.append(amendment["body"])
        else:
            lines.append(f"[UNAPPROVED] {amendment['id']} status={amendment['status']} -> docs/ai-continuity/AMENDMENTS/{amendment['file']}")

    meta = current.get("metadata", {})
    lines.append(f"[ACTIVE CONTEXT] task={current.get('current_task')} agent={current.get('active_agent')} status={meta.get('status', 'unknown')}")
    if meta.get("evidence_ref"):
        lines.append(f"Evidence ref: {meta['evidence_ref']}")
    for key, value in meta.items():
        if key not in ("current_task", "active_agent", "status", "evidence_ref", "branch", "commit_head", "updated_at"):
            lines.append(f"{key}: {value}")
    if current.get("body"):
        lines.append(current["body"])
    counts = evidence["counts"]
    lines.append("[VERIFICATION SUMMARY] " + ", ".join(f"{key}: {value}" for key, value in counts.items()))
    for item in evidence["items"]:
        if not brief or item["status"] in ("FAIL", "BLOCKED"):
            lines.append(item["raw_line"])
    for finding in evidence["diagnostics"]:
        lines.append(f"[EVIDENCE_PARSE_WARNING L{finding['line']}] {finding['raw_line']}")
    if brief:
        lines.append("Evidence detail: docs/ai-continuity/EVIDENCE_INDEX.md (read relevant rows before completion claims).")
    if beads["ready_tasks"]:
        lines.append("[BEADS READY TASKS]")
        tasks = beads["ready_tasks"][:5] if brief else beads["ready_tasks"]
        lines.extend(f"- {t.get('id', 'task')}: {t.get('title', '')}" for t in tasks)
        if brief and len(beads["ready_tasks"]) > len(tasks):
            lines.append("Additional ready tasks: bd ready --json")
    lines.append("Other task-specific sources: docs/ai-continuity/REQUIREMENTS.md, DECISIONS/ (read as needed).")
    text = "\n".join(lines)
    if brief and len(text) > max_chars:
        text += "\n[SOFT SIZE TARGET EXCEEDED: essential context retained; keep CURRENT concise. Optional detail: restore --full.]"
    return {
        "text": text,
        "charter_sha256": charter.get("sha256"),
        "git_state": git_state,
        "current_task": current.get("current_task"),
        "status": "VALID" if charter["exists"] else "INCOMPLETE"
    }
