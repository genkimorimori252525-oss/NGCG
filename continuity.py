#!/usr/bin/env python
"""Codex Continuity Guard (CCG) CLI Tool.

Commands:
- doctor      : Environment & project integrity diagnostic
- init        : Initialize CCG docs, skills, and hooks in project
- status      : Display project objective, active tasks, and evidence summary
- checkpoint  : Record progress, evidence, and update handoff
- restore     : Generate brief or full restoration context
- audit       : Detect charter drift, unverified claims, and conflicts
- export      : Export audit report and state snapshot
- rollback    : Safely remove CCG configurations without harming existing files
"""

import os
import sys
import json
import argparse
import datetime
import uuid

from state_store import StateStore
from approval_guard import approve_interactive
from pathlib import Path

# Add script directory to sys.path
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

try:
    from atomic_io import atomic_write_text, safe_read_text, compute_sha256, create_backup
    from loader import (
        find_git_root,
        load_charter,
        load_requirements,
        load_amendments,
        load_evidence,
        load_current,
        load_git_state,
        load_beads_state,
        load_openspec_state,
        build_recovery_packet
    )
    from validator import run_full_audit
except ImportError:
    from tools.continuity_guard.atomic_io import atomic_write_text, safe_read_text, compute_sha256, create_backup
    from tools.continuity_guard.loader import (
        find_git_root,
        load_charter,
        load_requirements,
        load_amendments,
        load_evidence,
        load_current,
        load_git_state,
        load_beads_state,
        load_openspec_state,
        build_recovery_packet
    )
    from tools.continuity_guard.validator import run_full_audit

CCG_AGENTS_BLOCK_START = "<!-- CCG_CONTINUITY_START -->"
CCG_AGENTS_BLOCK_END = "<!-- CCG_CONTINUITY_END -->"

CCG_AGENTS_CONTENT = f"""
{CCG_AGENTS_BLOCK_START}
## Continuity Guard (project-scoped)
- On start/resume/context loss, load a **brief** restore once; use source details only when required.
- Keep original records; brief is a bounded index, not execution authority. Recall only applicable source sections.
- Treat the user's latest instructions as authoritative. Preserve the charter, amendments, blockers and evidence.
- Let CCG/scripts record routine events and checkpoints; use manual checkpoint only on unsupported paths.
- Never claim PASS without observed evidence. Team chat is OFF unless explicitly enabled per project.
- Do not enable Codex hooks or overwrite existing configuration without a tested canary.
{CCG_AGENTS_BLOCK_END}
""".strip()


SKILL_TEMPLATE = """---
name: continuity-guard
description: Restore CCG state on project resume or context loss; checkpoint substantial work before interruption or a major milestone.
---
<!-- CCG_SKILL_START -->
# Continuity Guard

From the project root: `py -3.10 "{CLI_PATH}" <command>`.

- `restore --brief`: once on start/resume or missing state; a bounded source index. Reuse loaded context. Use `recall <source> [--task ID | --section HEADING]` for relevant original text; source is relative to docs/ai-continuity. Continue pages with --offset and --sha256. `restore --full` explicitly expands all records.
- `init --no-hooks --mission "<USER_GOAL>" [--constraints "<EXPLICIT_CONSTRAINTS>"]`: only work likely to span sessions/compaction or need handoff. Several commands alone do not justify initialization.
- Prefer script-driven checkpoints. Use `checkpoint --task <ID> [--agent <NAME>]` manually only when automated observation is unavailable. CCG stores concurrent records in SQLite; CURRENT is only the latest human-readable projection.
- `status` for an overview; `audit` for suspected drift or overall project completion; `doctor` for setup trouble. Do not run all routinely.

Current user instructions take precedence. Record explicit goal changes in AMENDMENTS and actual verification in EVIDENCE_INDEX. Preserve original goals, constraints, approvals and FAIL/BLOCKED/UNKNOWN. Omitted text stays unread: resolve applicable constraints and uncertainty before acting; unclear scope stays HOLD. An index never grants approval. Avoid routine full audits and repeated summaries for minor work. Hooks stay disabled.

`rollback` preserves edited Skills and continuity records.
<!-- CCG_SKILL_END -->
"""

CHARTER_TEMPLATE = """---
project_id: "codex-continuity-guard"
charter_version: "1.0.0"
approved_by: "PENDING_USER_REVIEW"
approved_at: "{DATE}"
approved_sha256: "{APPROVED_HASH}"
---

# PROJECT CHARTER: Codex Continuity Guard

## 1. プロジェクトの使命 (Core Mission)
コンテキスト圧縮・セッション再開・マルチエージェント環境においても、
ユーザーの初期目標、明示的な変更指示、禁止事項、検証結果、未完了タスクを確実に復元・維持し、
AIの自己都合による要件改ざんや誤った成功宣言を防ぐ。

## 2. 完成条件 (Acceptance Criteria)
1. コンテキスト圧縮後 (SessionStart: source=compact) に復元パケットが自動再注入されること。
2. PROJECT_CHARTER.md の無断改ざんが監査 (continuity audit) で100%検知されること。
3. 未検証のタスク完了（Fake Success）が検知・警告されること。
4. Windows 11 / PowerShell 環境でオフラインで動作すること。
5. 全25項目の受入試験 (T01〜T25) が検証されること。

## 3. 絶対的な禁止事項 (Prohibitions & Non-goals)
- ユーザー承認のない Charter の改ざん・自動要約による置換の禁止。
- 客観的テスト証拠のない成功宣言の禁止。
- 既存プロジェクト設定の無断破壊・上書きの禁止。
- 外部APIやクラウド同期への必須依存の禁止。
"""

REQUIREMENTS_TEMPLATE = """# Project Requirements

### REQ-001: Automatic Context Restoration on Compaction
- Description: SessionStart(compact) Hook injects the Context Recovery Packet before model inference.
- Status: CONFIRMED

### REQ-002: Immutable Charter & Tamper Detection
- Description: PROJECT_CHARTER.md integrity is verified via SHA-256 matching approved_sha256.
- Status: CONFIRMED

### REQ-003: Objective Verification & Fake Success Prevention
- Description: Tasks cannot be closed without corresponding PASS evidence in EVIDENCE_INDEX.md.
- Status: CONFIRMED

### REQ-004: Windows Native PowerShell & Path Resilience
- Description: Operates reliably with native PowerShell, handling spaces and Japanese paths.
- Status: CONFIRMED
"""

CURRENT_TEMPLATE = """---
current_task: "T01-T25 Integration Test"
active_agent: "Codex Continuity Guard"
branch: "master"
completed_tasks: ""
updated_at: "{DATE}"
---

# Current Progress State

Currently executing pilot integration and verification test suite.
"""

EVIDENCE_TEMPLATE = """# Evidence Index

| ID | Status | Description | Reference |
|---|---|---|---|
| T01 | NOT_RUN | Read-only inspection of existing repos | logs/t01.log |
| T02 | NOT_RUN | Sandbox initialization safety | logs/t02.log |
| T03 | NOT_RUN | AGENTS.md reading and goal identification | logs/t03.log |
| T04 | NOT_RUN | Session restart recovery | logs/t04.log |
| T05 | NOT_RUN | Manual context compaction recovery | logs/t05.log |
| T06 | NOT_RUN | Automatic context compaction hook | logs/t06.log |
| T07 | NOT_RUN | Consecutive 3x compaction resilience | logs/t07.log |
| T08 | NOT_RUN | Explicit requirement amendment handling | logs/t08.log |
| T09 | NOT_RUN | Unauthorized charter modification detection | logs/t09.log |
| T10 | NOT_RUN | False success declaration detection | logs/t10.log |
| T11 | NOT_RUN | Stale handoff detection | logs/t11.log |
| T12 | NOT_RUN | Known failure reason retention | logs/t12.log |
| T13 | NOT_RUN | Clean session discovery | logs/t13.log |
| T14 | NOT_RUN | Multi-agent claim conflict detection | logs/t14.log |
| T15 | NOT_RUN | Git worktree independence | logs/t15.log |
| T16 | NOT_RUN | Windows Japanese & space path resilience | logs/t16.log |
| T17 | NOT_RUN | Hook disabled / untrusted warning & fallback | logs/t17.log |
| T18 | NOT_RUN | Task DB corruption recovery | logs/t18.log |
| T19 | NOT_RUN | Offline execution resilience | logs/t19.log |
| T20 | NOT_RUN | Secret & transcript leak prevention | logs/t20.log |
| T21 | NOT_RUN | Prompt injection resistance in charter | logs/t21.log |
| T22 | NOT_RUN | Hook duplication prevention | logs/t22.log |
| T23 | NOT_RUN | Performance: token size & hook execution speed | logs/t23.log |
| T24 | NOT_RUN | Safe rollback capability | logs/t24.log |
| T25 | NOT_RUN | Clean environment reproducibility | logs/t25.log |
"""

RECOVERY_TEMPLATE = """# Recovery and Rollback Procedures

## If Hooks Fail to Fire
1. Use the `continuity-guard` Skill to run `restore --brief` manually.
2. If using a project-local installation, run `python tools/continuity-guard/continuity.py restore --brief`.
3. Verify `.codex/hooks.json` configuration and trust status in Codex.

## If Charter Drift is Detected
1. Use the `continuity-guard` Skill to run `audit` and identify changes.
2. Check `docs/ai-continuity/AMENDMENTS/` for approved updates.
3. Only after the user reviews it, re-approve using `approve-charter` in an interactive terminal; never treat a self-hash as approval.

## Rollback
1. Use the `continuity-guard` Skill to run `rollback` and remove CCG integration cleanly.
2. If using a project-local installation, run `python tools/continuity-guard/continuity.py rollback`.
"""

PROJECT_REQUIREMENTS_TEMPLATE = """# Project Requirements

Record only requirements explicitly stated or confirmed by the user. No additional requirements have been recorded yet.
"""

PROJECT_CURRENT_TEMPLATE = """---
current_task: "not-started"
active_agent: "Codex"
branch: "unknown"
completed_tasks: ""
updated_at: "{DATE}"
---

# Current Progress State

No task checkpoint has been recorded yet.
"""

PROJECT_EVIDENCE_TEMPLATE = """# Evidence Index

Only record verification that was actually performed.

| ID | Status | Description | Reference |
|---|---|---|---|
"""


def build_user_project_charter(project_root: Path, mission: str, constraints: str, approved_at: str) -> str:
    """Build a project Charter from explicitly supplied user intent."""
    body = f"""# PROJECT CHARTER: {project_root.name}

## Core Mission
{mission.strip()}

## Constraints
{constraints.strip() if constraints and constraints.strip() else "No additional constraints were stated."}

## Completion Criteria
Use only the completion conditions explicitly stated by the user. Do not invent missing requirements."""
    body_hash = compute_sha256(body)
    return f"""---
project_id: "{project_root.name}"
charter_version: "1.0.0"
approved_by: "PENDING_USER_REVIEW"
approved_at: "{approved_at}"
approved_sha256: "{body_hash}"
---

{body}
"""


def cmd_doctor(args):
    """Run diagnostics on environment and project integrity."""
    project_root = find_git_root()
    print("=== Codex Continuity Guard Doctor ===")
    print(f"Project Root: {project_root}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")

    git_state = load_git_state(project_root)
    print(f"Git: {'Installed' if git_state['is_git'] else 'Not a git repo'} (branch={git_state['branch']}, head={git_state['head'][:8] if git_state['head'] else 'none'})")

    beads = load_beads_state(project_root)
    print(f"Beads (bd): {'Installed' if beads['installed'] else 'Not found in PATH'} (configured: {beads['configured']})")

    openspec = load_openspec_state(project_root)
    print(f"OpenSpec: {'Installed' if openspec['installed'] else 'Not found in PATH'} (configured: {openspec['configured']})")

    charter = load_charter(project_root)
    print(f"PROJECT_CHARTER.md: {'Found' if charter['exists'] else 'Missing'}")
    if charter["exists"]:
        print(f"  SHA-256: {charter['sha256']}")
        print(f"  Expected: {charter['expected_sha256']}")

    audit = run_full_audit(project_root)
    print("\n--- Audit Results ---")
    print(f"Health Status: {'HEALTHY' if audit['healthy'] else 'ISSUES DETECTED'}")
    print(f"Errors: {audit['counts']['errors']}, Warnings: {audit['counts']['warnings']}, Infos: {audit['counts']['infos']}")
    for err in audit["errors"]:
        print(f"  [ERROR] {err['code']}: {err['message']}")
    for warn in audit["warnings"]:
        print(f"  [WARNING] {warn['code']}: {warn['message']}")


def cmd_init(args):
    """Initialize CCG docs, skills, and hooks in project."""
    project_root = find_git_root()
    dry_run = getattr(args, "dry_run", False)
    today = datetime.date.today().isoformat()

    print(f"Initializing Codex Continuity Guard in {project_root} (dry-run: {dry_run})...")

    docs_dir = project_root / "docs" / "ai-continuity"
    # Old auto-generated hooks.json used a non-current schema; fail closed.
    if getattr(args, "enable_hooks", False):
        raise SystemExit("Hooks require a version-verified canary; no automatic registration in this release")
    no_hooks = True
    mission = getattr(args, "mission", None)
    constraints = getattr(args, "constraints", "")
    charter_file = docs_dir / "PROJECT_CHARTER.md"
    if (not mission or not mission.strip()) and not charter_file.exists():
        print("ERROR: init --no-hooks requires --mission for a new project.", file=sys.stderr)
        raise SystemExit(2)

    subdirs = [
        docs_dir / "AMENDMENTS",
        docs_dir / "DECISIONS",
        docs_dir / "HANDOFFS",
        project_root / ".agents" / "skills" / "continuity-guard"
    ]
    if not no_hooks:
        subdirs.append(project_root / ".codex")

    for d in subdirs:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
        print(f"  Directory: {d.relative_to(project_root)}")

    # Files to create
    use_project_templates = no_hooks or bool(mission)
    files_to_create = {
        docs_dir / "REQUIREMENTS.md": PROJECT_REQUIREMENTS_TEMPLATE if use_project_templates else REQUIREMENTS_TEMPLATE,
        docs_dir / "CURRENT.md": (PROJECT_CURRENT_TEMPLATE if use_project_templates else CURRENT_TEMPLATE).replace("{DATE}", today),
        docs_dir / "EVIDENCE_INDEX.md": PROJECT_EVIDENCE_TEMPLATE if use_project_templates else EVIDENCE_TEMPLATE,
        docs_dir / "RECOVERY.md": RECOVERY_TEMPLATE,
        project_root / ".agents" / "skills" / "continuity-guard" / "SKILL.md": (
            SKILL_TEMPLATE
            .replace("{CLI_PATH}", str((script_dir / "continuity.py").resolve()))
        )
    }

    # Charter special handling: compute initial hash
    if not charter_file.exists():
        if mission:
            final_charter = build_user_project_charter(project_root, mission, constraints, today)
        else:
            raw_charter = CHARTER_TEMPLATE.replace("{DATE}", today).replace("{APPROVED_HASH}", "PENDING_HASH")
            temp_meta, temp_body = raw_charter.split("---", 2)[1], raw_charter.split("---", 2)[2].strip()
            body_hash = compute_sha256(temp_body)
            final_charter = CHARTER_TEMPLATE.replace("{DATE}", today).replace("{APPROVED_HASH}", body_hash)
        files_to_create[charter_file] = final_charter

    for fpath, content in files_to_create.items():
        if not fpath.exists():
            print(f"  Creating file: {fpath.relative_to(project_root)}")
            if not dry_run:
                atomic_write_text(fpath, content.strip() + "\n")
        else:
            print(f"  Preserving existing: {fpath.relative_to(project_root)}")

    # Update AGENTS.md
    agents_file = project_root / "AGENTS.md"
    if agents_file.exists():
        existing_agents = safe_read_text(agents_file)
        if CCG_AGENTS_BLOCK_START not in existing_agents:
            print("  Merging CCG rules block into AGENTS.md")
            new_agents = existing_agents.rstrip() + "\n\n" + CCG_AGENTS_CONTENT + "\n"
            if not dry_run:
                atomic_write_text(agents_file, new_agents, backup=True)
        else:
            print("  AGENTS.md already contains CCG rules block.")
    else:
        print("  Creating AGENTS.md with CCG rules block")
        if not dry_run:
            atomic_write_text(agents_file, CCG_AGENTS_CONTENT + "\n")

    if no_hooks:
        print("  Skipping Codex Hooks (--no-hooks)")
        print("CCG initialization complete.")
        return

    # Hook registration is intentionally not automatic. The installed Codex
    # version and its hook schema must be checked in a separate canary.
    print("  Hooks remain disabled by default (safe).")
    print("CCG initialization complete.")


def cmd_approve_charter(args):
    project_root = find_git_root()
    print("External approval saved at:", approve_interactive(project_root))


def cmd_status(args):
    """Display project objective, active tasks, and evidence summary."""
    project_root = find_git_root()
    charter = load_charter(project_root)
    current = load_current(project_root)
    evidence = load_evidence(project_root)
    git_state = load_git_state(project_root)

    print("=== Codex Continuity Guard Status ===")
    print(f"Git: branch={git_state['branch']}, head={git_state['head'][:8] if git_state['head'] else 'none'}")
    if charter["exists"]:
        print(f"Charter ID: {charter['metadata'].get('project_id', 'unknown')} (v{charter['charter_version']})")
        print(f"Charter SHA-256: {charter['sha256'][:12]}...")
    else:
        print("Charter: MISSING")

    print(f"Active Task: {current.get('current_task', 'None')}")
    print(f"Active Agent: {current.get('active_agent', 'None')}")

    ev_counts = evidence["counts"]
    print(f"Test Status: PASS={ev_counts['PASS']}, FAIL={ev_counts['FAIL']}, BLOCKED={ev_counts['BLOCKED']}, NOT_RUN={ev_counts['NOT_RUN']}")


def cmd_checkpoint(args):
    """Persist one unique checkpoint and a projection without lost concurrent writes.

    SQLite serializes concurrent writers across processes. CURRENT.md is a
    latest-state projection; authoritative history is the DB + unique handoffs.
    """
    project_root = find_git_root()
    task = args.task or "manual-checkpoint"
    agent = args.agent or "user"
    evidence_ref = args.evidence or ""
    status = (args.status or "IN_PROGRESS").upper()
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_state = load_git_state(project_root)
    store = StateStore(project_root)

    with store.connection(write=True) as db:
        current_file = project_root / "docs" / "ai-continuity" / "CURRENT.md"
        previous = load_current(project_root)
        previous_meta = previous.get("metadata", {})
        body = previous.get("body", "")
        legacy_prefix = "# Current Progress State\n\n" + "\n".join(
            f"{label}: {previous_meta.get(key, '')}" for label, key in (
                ("Task", "current_task"), ("Agent", "active_agent"), ("Status", "status"),
                ("Evidence", "evidence_ref"), ("Updated", "updated_at")))
        if body == legacy_prefix or body.startswith(legacy_prefix + "\n"):
            body = "# Current Progress State\n\n" + body[len(legacy_prefix):].lstrip()

        managed = {"current_task", "active_agent", "branch", "commit_head", "status", "evidence_ref", "updated_at"}
        raw = previous.get("raw_content", "").replace("\r\n", "\n").strip()
        extras = []
        if raw.startswith("---") and len(raw.split("---", 2)) == 3:
            extras = [line for line in raw.split("---", 2)[1].splitlines()
                      if line.strip() and line.split(":", 1)[0].strip() not in managed]
        extra_metadata = "\n".join(extras)
        cp_id = uuid.uuid4().hex
        current_content = f"""---
current_task: "{task}"
active_agent: "{agent}"
branch: "{git_state.get('branch', 'unknown')}"
commit_head: "{git_state.get('head', 'unknown')}"
status: "{status}"
evidence_ref: "{evidence_ref}"
updated_at: "{now_str}"
{extra_metadata}
---

{body or "# Current Progress State"}
"""
        clean_task = "".join(c for c in task if c.isalnum() or c in "-_")[:60] or "task"
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"handoff_{stamp}_{clean_task}_{cp_id[:12]}.md"
        handoff = project_root / "docs" / "ai-continuity" / "HANDOFFS" / name
        atomic_write_text(handoff, current_content)
        atomic_write_text(current_file, current_content)
        db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cp_id, store.project_id, agent, task, status, evidence_ref,
                    git_state.get("head", "unknown"), int(git_state.get("dirty", False)),
                    name, now_str))
        store._insert_event(db, "ccg-checkpoint", cp_id, "checkpoint", {
            "checkpoint_id": cp_id, "agent": agent, "task": task,
            "status": status, "evidence": evidence_ref, "handoff": name})
    print(f"Checkpoint saved: id={cp_id}, task={task}, agent={agent}, handoff={name}")


def cmd_worktrees(args):
    """Read-only overview of parent and linked worktree research histories."""
    from worktree_view import worktree_report
    try:
        overview = worktree_report(find_git_root())
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.json:
        print(json.dumps(overview, ensure_ascii=False, indent=2))
        return
    print("=== NGCG WORKTREE PARENT OVERVIEW ===")
    for item in [overview["parent"], *overview["variants"]]:
        print(f"[{item['role'].upper()}] {item['branch']}  {item['path']}")
        print(f"  task={item['current_task'] or 'unrecorded'} status={item['current_status']} "
              f"approval={item['charter_approval']}")
        print(f"  evidence={item['evidence_counts']} checkpoints={len(item['latest_checkpoints'])}")
        for warning in item["warnings"]:
            print("  WARNING: " + warning)
    print(f"Variants: {overview['variant_count']} (recommended: 2); "
          f"layout={overview['layout']}")
    print(overview["notice"])


def cmd_restore(args):
    """Generate restoration context packet."""
    project_root = find_git_root()
    brief = not getattr(args, "full", False)
    packet = build_recovery_packet(project_root, brief=brief, max_chars=getattr(args, "max_chars", 2500))
    print(packet["text"])


def cmd_recall(args):
    """Read an original source page without updating records or authority."""
    from recovery import recall_source, render_recall_json
    try:
        # JSON carries original content once, not both a rendered and raw copy.
        page = recall_source(find_git_root(), args.source, offset=args.offset,
                             max_chars=args.max_chars, expected_sha256=args.sha256,
                             task=args.task, section=args.section)
        output = render_recall_json(page, args.max_chars) if args.json else page["text"]
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(str(exc))
    print(output)


def cmd_audit(args):
    """Run full audit and exit with non-zero code if critical errors exist."""
    project_root = find_git_root()
    audit = run_full_audit(project_root)

    print("=== Codex Continuity Guard Audit ===")
    print(f"Healthy: {audit['healthy']}")
    print(f"Errors: {audit['counts']['errors']}, Warnings: {audit['counts']['warnings']}, Infos: {audit['counts']['infos']}")

    for err in audit["errors"]:
        print(f"[ERROR] {err['code']}: {err['message']}")
    for warn in audit["warnings"]:
        print(f"[WARNING] {warn['code']}: {warn['message']}")
    for inf in audit["infos"]:
        print(f"[INFO] {inf['code']}: {inf['message']}")

    if not audit["healthy"]:
        sys.exit(1)
    else:
        sys.exit(0)


def cmd_export(args):
    """Export project continuity status and audit snapshot."""
    project_root = find_git_root()
    out_dir = Path(args.out or (project_root / "docs" / "ai-continuity" / "exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    export_file = out_dir / f"continuity_export_{ts}.json"

    charter = load_charter(project_root)
    current = load_current(project_root)
    evidence = load_evidence(project_root)
    audit = run_full_audit(project_root)

    data = {
        "timestamp": ts,
        "project_root": str(project_root),
        "charter": charter,
        "current": current,
        "evidence": evidence,
        "audit": audit
    }

    atomic_write_text(export_file, json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Continuity snapshot exported to {export_file}")


def cmd_rollback(args):
    """Safely remove CCG additions from AGENTS.md, hooks.json, and skills."""
    project_root = find_git_root()
    dry_run = getattr(args, "dry_run", False)
    print(f"Rolling back CCG configurations in {project_root} (dry-run: {dry_run})...")

    # 1. AGENTS.md
    agents_file = project_root / "AGENTS.md"
    if agents_file.exists():
        content = safe_read_text(agents_file)
        start_count = content.count(CCG_AGENTS_BLOCK_START)
        end_count = content.count(CCG_AGENTS_BLOCK_END)
        if start_count == 1 and end_count == 1:
            start_index = content.index(CCG_AGENTS_BLOCK_START)
            end_index = content.index(CCG_AGENTS_BLOCK_END)
            if end_index >= start_index + len(CCG_AGENTS_BLOCK_START):
                print("  Removing CCG block from AGENTS.md")
                cleaned = content[:start_index] + content[end_index + len(CCG_AGENTS_BLOCK_END):]
                if not dry_run:
                    atomic_write_text(agents_file, cleaned, backup=True)
            else:
                print("  Preserving AGENTS.md: CCG markers are out of order.")
        elif start_count or end_count:
            print("  Preserving AGENTS.md: CCG markers are missing or duplicated.")

    # 2. .codex/hooks.json
    hooks_file = project_root / ".codex" / "hooks.json"
    if hooks_file.exists():
        try:
            data = json.loads(safe_read_text(hooks_file))
            if isinstance(data.get("hooks"), list):
                print("  Removing legacy CCG hooks from .codex/hooks.json")
                data["hooks"] = [h for h in data["hooks"] if not isinstance(h, dict) or
                                 h.get("name") not in ("ccg-session-start", "ccg-pre-compact")]
                if not dry_run:
                    atomic_write_text(hooks_file, json.dumps(data, indent=2, ensure_ascii=False) + "\n", backup=True)
        except Exception:
            pass

    # 3. .agents/skills/continuity-guard
    skill_dir = project_root / ".agents" / "skills" / "continuity-guard"
    if skill_dir.exists():
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            skill_content = safe_read_text(skill_file)
            skill_start = "<!-- CCG_SKILL_START -->"
            skill_end = "<!-- CCG_SKILL_END -->"
            expected_skill = SKILL_TEMPLATE.replace(
                "{CLI_PATH}", str((script_dir / "continuity.py").resolve())
            ).strip() + "\n"
            if skill_content.replace("\r\n", "\n") == expected_skill:
                print("  Removing unchanged CCG-managed Skill file")
                if not dry_run:
                    skill_file.unlink()
            elif skill_start in skill_content or skill_end in skill_content:
                print("  Preserving modified or malformed Skill file.")
        if skill_dir.exists() and not any(skill_dir.iterdir()):
            print(f"  Removing empty Skill directory: {skill_dir.relative_to(project_root)}")
            if not dry_run:
                skill_dir.rmdir()

    print("Rollback completed safely.")


def main():
    parser = argparse.ArgumentParser(description="Codex Continuity Guard (CCG)")
    subparsers = parser.add_subparsers(dest="command")

    # doctor
    subparsers.add_parser("doctor", help="Run environment diagnostic")

    # init
    p_init = subparsers.add_parser("init", help="Initialize CCG in current project")
    p_init.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p_init.add_argument("--no-hooks", action="store_true", help="Legacy option; hooks are disabled by default")
    p_init.add_argument("--enable-hooks", action="store_true", help="Fail closed; requires a verified canary")
    p_init.add_argument("--mission", help="Explicit project goal provided by the user")
    p_init.add_argument("--constraints", default="", help="Explicit user-stated project constraints")

    # Explicit out-of-band approval: never triggered by init or a checkpoint.
    subparsers.add_parser("approve-charter", help="Human TTY review and external charter hash anchor")

    # status
    subparsers.add_parser("status", help="Show current continuity status")

    # checkpoint
    p_cp = subparsers.add_parser("checkpoint", help="Record progress checkpoint")
    p_cp.add_argument("--task", required=True, help="Task identifier (e.g. REQ-001)")
    p_cp.add_argument("--agent", default="Codex", help="Agent identifier")
    p_cp.add_argument("--evidence", default="", help="Evidence reference")
    p_cp.add_argument("--status", default="IN_PROGRESS", help="Task status")

    # Optional read-only parent view; no automatic worktree creation/merging.
    p_wt = subparsers.add_parser("worktrees", help="Inspect parent and linked worktree histories")
    p_wt.add_argument("--json", action="store_true", help="Machine-readable parent/variant overview")

    # restore
    p_res = subparsers.add_parser("restore", help="Generate restoration context packet")
    p_res.add_argument("--brief", action="store_true", default=True, help="Brief restoration packet")
    p_res.add_argument("--full", action="store_true", help="Full restoration report")
    p_res.add_argument("--max-chars", type=int, default=2500, help="Brief output budget (1000-20000 characters)")

    p_read = subparsers.add_parser("recall", help="Read a bounded page of an original continuity source")
    p_read.add_argument("source", help="Path relative to docs/ai-continuity, or AMENDMENTS inventory")
    p_read.add_argument("--offset", type=int, default=0, help="Character cursor in selected text")
    p_read.add_argument("--max-chars", type=int, default=3000)
    p_read.add_argument("--sha256", help="Expected source hash; required for continuation")
    selection = p_read.add_mutually_exclusive_group()
    selection.add_argument("--task", help="Exact case-sensitive evidence ID; returns all matching rows")
    selection.add_argument("--section", help="Exact unique Markdown heading")
    p_read.add_argument("--json", action="store_true", help="Structured page and continuation metadata")

    # audit
    subparsers.add_parser("audit", help="Run integrity audit")

    # export
    p_exp = subparsers.add_parser("export", help="Export audit snapshot")
    p_exp.add_argument("--out", help="Output directory")

    # rollback
    p_rb = subparsers.add_parser("rollback", help="Safely remove CCG configuration")
    p_rb.add_argument("--dry-run", action="store_true", help="Preview rollback changes")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    cmd_map = {
        "doctor": cmd_doctor,
        "approve-charter": cmd_approve_charter,
        "init": cmd_init,
        "status": cmd_status,
        "checkpoint": cmd_checkpoint,
        "worktrees": cmd_worktrees,
        "restore": cmd_restore,
        "recall": cmd_recall,
        "audit": cmd_audit,
        "export": cmd_export,
        "rollback": cmd_rollback
    }

    func = cmd_map.get(args.command)
    if func:
        func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
