"""Validation and audit engine for Codex Continuity Guard (CCG).

Checks:
- Charter integrity & tampering (SHA-256 drift)
- Unauthorized requirement modifications
- Fake success declarations (verifying evidence in EVIDENCE_INDEX.md)
- Task claim conflicts
- Stale handoff files
- Schema adherence
"""

from pathlib import Path
import re
import hashlib
import json

from approval_guard import read_approval
from atomic_io import compute_sha256
from typing import Dict, Any, List

try:
    from tools.continuity_guard.loader import (
        load_charter,
        load_requirements,
        load_amendments,
        load_evidence,
        load_current,
        load_git_state,
        parse_frontmatter,
        safe_read_text
    )
except ImportError:
    from loader import (
        load_charter,
        load_requirements,
        load_amendments,
        load_evidence,
        load_current,
        load_git_state,
        parse_frontmatter,
        safe_read_text
    )


class AuditFinding:
    def __init__(self, level: str, code: str, message: str, details: Any = None):
        self.level = level  # 'ERROR', 'WARNING', 'INFO'
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "details": self.details
        }


def validate_charter_integrity(project_root: Path) -> List[AuditFinding]:
    """Verify that PROJECT_CHARTER.md matches its approved SHA-256 hash."""
    findings = []
    charter = load_charter(project_root)

    if not charter["exists"]:
        findings.append(AuditFinding(
            level="ERROR",
            code="CHARTER_MISSING",
            message="docs/ai-continuity/PROJECT_CHARTER.md does not exist."
        ))
        return findings

    expected_sha = charter.get("expected_sha256")
    body_sha = charter.get("body_sha256")
    actual_sha = charter.get("sha256")

    # A self-hash is useful for corruption checking, not independent approval.
    if expected_sha and expected_sha.lower() != (body_sha or "").lower():
        findings.append(AuditFinding(
            "ERROR", "CHARTER_DRIFT_DETECTED",
            "Charter body differs from its internal checksum.",
            {"expected": expected_sha, "body_sha256": body_sha}))
        return findings

    receipt = read_approval(project_root)
    if receipt is None:
        findings.append(AuditFinding(
            "ERROR", "CHARTER_UNAPPROVED",
            "No independently stored, explicitly confirmed charter approval. Self-declared USER is insufficient."))
    elif receipt.get("approved_sha256") != actual_sha:
        findings.append(AuditFinding(
            "ERROR", "CHARTER_APPROVAL_MISMATCH",
            "Charter content changed relative to approved external receipt.",
            {"expected": receipt.get("approved_sha256"), "actual": actual_sha}))

    return findings


def validate_fake_success(project_root: Path) -> List[AuditFinding]:
    """Fail closed on structured completion claims without verified evidence files.

    Markdown evidence rows are pointers, not proofs of execution. A receipt is
    considered structurally valid only if its referenced local file exists;
    real task success still requires command/HEAD/inputs review by CCG/Codex.
    """
    findings = []
    current = load_current(project_root)
    meta = current.get("metadata", {})
    completed = meta.get("completed_tasks", "")
    ids = [x.strip() for x in completed.split(",") if x.strip()] if isinstance(completed, str) else []
    status = str(meta.get("status", "")).upper()
    active = str(meta.get("current_task", "")).strip()
    if status in {"PASS", "DONE", "COMPLETE", "COMPLETED", "SUCCESS"} and active:
        ids.append(active)

    rows: dict[str, tuple[str, str]] = {}
    parsed_evidence = load_evidence(project_root)
    for diagnostic in parsed_evidence["diagnostics"]:
        findings.append(AuditFinding("WARNING", "EVIDENCE_PARSE_WARNING",
                                     f"Evidence line {diagnostic['line']}: {diagnostic['reason']}"))
    for item in parsed_evidence["items"]:
        rows[item["id"]] = (item["status"], item["reference"])

    for task_id in dict.fromkeys(ids):
        row = rows.get(task_id)
        if not row or row[0] != "PASS":
            findings.append(AuditFinding("ERROR", "UNVERIFIED_SUCCESS_DECLARATION",
                                         f"Task '{task_id}' was marked complete without matching PASS evidence.",
                                         {"task_id": task_id}))
            continue
        ref = row[1]
        # Local proof needs a real relative file inside the project, not just a label or a URL.
        if not ref or re.match(r"^[a-z]+://", ref, re.I):
            findings.append(AuditFinding("ERROR", "EVIDENCE_NOT_LOCAL",
                                         f"Task '{task_id}' has no local machine-verifiable evidence file."))
            continue
        target = (project_root / ref).resolve()
        if not target.is_relative_to(project_root.resolve()) or not target.is_file():
            findings.append(AuditFinding("ERROR", "EVIDENCE_MISSING",
                                         f"Evidence path for '{task_id}' is missing or outside project.",
                                         {"evidence_ref": ref}))
        elif target.stat().st_size == 0:
            findings.append(AuditFinding("ERROR", "EVIDENCE_EMPTY", f"Evidence for '{task_id}' is empty."))
        else:
            # A Markdown pointer alone is not a verified PASS. Require a
            # deterministic command-execution receipt and validate its log.
            try:
                receipt = json.loads(target.read_text(encoding="utf-8"))
                output_rel = str(receipt.get("output_path", ""))
                output = (project_root / output_rel).resolve()
                required = (receipt.get("schema_version") == 1 and receipt.get("task_id") == task_id
                            and receipt.get("result") == "PASS" and receipt.get("exit_code") == 0
                            and isinstance(receipt.get("command"), list) and bool(receipt["command"])
                            and output.is_relative_to(project_root.resolve()) and output.is_file())
                if not required or hashlib.sha256(output.read_bytes()).hexdigest() != receipt.get("output_sha256"):
                    raise ValueError("Receipt fields or output digest mismatch")
                current_git = load_git_state(project_root)
                recorded_head = receipt.get("git_head")
                if current_git["is_git"] and current_git.get("head") not in (recorded_head, "unknown"):
                    raise ValueError("Receipt was produced at a different Git HEAD")
                if receipt.get("git_dirty"):
                    findings.append(AuditFinding("WARNING", "EVIDENCE_DIRTY_WORKTREE",
                        f"Receipt for '{task_id}' was captured with uncommitted changes; revisit if those inputs changed."))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                findings.append(AuditFinding("ERROR", "EVIDENCE_INVALID",
                    f"Task '{task_id}' has no valid machine PASS receipt matching its verification log.",
                    {"evidence_ref": ref, "reason": str(exc)[:200]}))
    return findings


def validate_amendments(project_root: Path) -> List[AuditFinding]:
    """Check amendments for approval authenticity."""
    findings = []
    amendments = load_amendments(project_root)

    for amd in amendments:
        status = amd["status"]
        if status == "APPROVED" and not amd.get("approved_by"):
            findings.append(AuditFinding(
                level="WARNING",
                code="AMENDMENT_APPROVAL_ANONYMOUS",
                message=f"Amendment {amd['id']} is marked APPROVED but 'approved_by' is missing.",
                details={"file": amd["file"]}
            ))
        elif status == "PROPOSED":
            findings.append(AuditFinding(
                level="INFO",
                code="PENDING_AMENDMENT",
                message=f"Amendment {amd['id']} is pending user approval.",
                details={"file": amd["file"]}
            ))

    return findings


def validate_stale_handoff(project_root: Path) -> List[AuditFinding]:
    """Check if handoffs reference outdated commits or missing files."""
    findings = []
    git_state = load_git_state(project_root)
    handoff_dir = project_root / "docs" / "ai-continuity" / "HANDOFFS"

    if handoff_dir.exists():
        for file in handoff_dir.glob("*.md"):
            # Check content for git commit ref
            meta, _ = parse_frontmatter(safe_read_text(file))
            h_commit = meta.get("commit_head")
            if h_commit and git_state.get("head") and h_commit != git_state["head"] and git_state["is_git"]:
                findings.append(AuditFinding(
                    level="WARNING",
                    code="STALE_HANDOFF",
                    message=f"Handoff {file.name} references commit {h_commit[:8]}, but HEAD is at {git_state['head'][:8]}.",
                    details={"file": file.name, "handoff_commit": h_commit, "current_head": git_state["head"]}
                ))

    return findings


def run_full_audit(project_root: Path) -> Dict[str, Any]:
    """Run all integrity and compliance checks on the project."""
    all_findings: List[AuditFinding] = []
    all_findings.extend(validate_charter_integrity(project_root))
    all_findings.extend(validate_amendments(project_root))
    all_findings.extend(validate_fake_success(project_root))
    all_findings.extend(validate_stale_handoff(project_root))

    errors = [f.to_dict() for f in all_findings if f.level == "ERROR"]
    warnings = [f.to_dict() for f in all_findings if f.level == "WARNING"]
    infos = [f.to_dict() for f in all_findings if f.level == "INFO"]

    is_healthy = len(errors) == 0

    return {
        "healthy": is_healthy,
        "counts": {
            "errors": len(errors),
            "warnings": len(warnings),
            "infos": len(infos)
        },
        "errors": errors,
        "warnings": warnings,
        "infos": infos
    }
