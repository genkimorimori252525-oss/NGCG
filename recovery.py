"""Bounded, deterministic recovery and lossless, read-only source paging.

Inspired by progressive disclosure, not an LLM summary or an authority store.
Budgeting concerns model-visible text; original records are never rewritten.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from approval_guard import read_approval
from loader import parse_charter, parse_current, parse_evidence


MIN_BUDGET = 1000
MAX_BUDGET = 20000
DOCS = Path("docs/ai-continuity")


def check_budget(max_chars: int) -> None:
    if not MIN_BUDGET <= max_chars <= MAX_BUDGET:
        raise ValueError(f"max_chars must be between {MIN_BUDGET} and {MAX_BUDGET}")


def read_source(root: Path, source: str) -> dict:
    """Snapshot once; hash the actual bytes, including newline conventions."""
    base = (Path(root).resolve() / DOCS).resolve()
    candidate = Path(source)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Source must be relative to docs/ai-continuity")
    target = (base / candidate).resolve()
    if not target.is_relative_to(base) or not target.is_relative_to(Path(root).resolve()):
        raise ValueError("Source escapes project continuity documents")
    if target.is_dir() and source.replace("\\", "/").rstrip("/") == "AMENDMENTS":
        # Inventory is itself paged; each file is later read from its original bytes.
        entries = []
        for path in sorted(target.glob("*.md")):
            item = read_source(root, path.relative_to(base).as_posix())
            entries.append(f"{item['source']} sha256={item['sha256']} lines={len(item['content'].splitlines())}")
        content = "\n".join(entries)
        data = content.encode("utf-8")
    else:
        data = target.read_bytes()
        content = data.decode("utf-8")
    return {"source": target.relative_to(base).as_posix(), "content": content,
            "sha256": hashlib.sha256(data).hexdigest()}


def recall_source(root: Path, source: str, *, offset: int = 0, max_chars: int = 3000,
                  expected_sha256: str | None = None, task: str | None = None,
                  section: str | None = None) -> dict:
    """Return original text, not a generated summary. Offset is a character cursor.

    A giant single line is explicitly paginated too. Hash must stay pinned when
    continuing, so a changed source cannot silently mix with an earlier page.
    """
    check_budget(max_chars)
    snapshot = read_source(root, source)
    sha = snapshot["sha256"]
    if expected_sha256 and (not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)
                            or sha != expected_sha256.lower()):
        raise ValueError("SOURCE_CHANGED: reread the source index before continuing")
    if offset and not expected_sha256:
        raise ValueError("Continuation requires --sha256 from the preceding page")
    original = snapshot["content"]
    content = original
    matches = None
    selected_lines = None
    task_spans = []
    if task is not None:
        if Path(source).name != "EVIDENCE_INDEX.md" or section is not None:
            raise ValueError("--task selects exact evidence IDs; do not combine with --section")
        rows = [item for item in parse_evidence(original)["items"] if item["id"] == task]
        if not rows:
            raise ValueError("TASK_NOT_FOUND: no exact evidence ID match")
        content = "\n".join(row["raw_line"] for row in rows)
        matches = len(rows)
        selected_lines = [row["line"] for row in rows]
        cursor = 0
        for row in rows:
            task_spans.append((cursor, cursor + len(row['raw_line']) + 1, row['line']))
            cursor += len(row['raw_line']) + 1
    if section is not None:
        lines = original.splitlines(keepends=True)
        ranges = []
        for i, line in enumerate(lines):
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
            if heading and heading[2].casefold() == section.casefold():
                end = i + 1
                while end < len(lines):
                    next_heading = re.match(r"^(#{1,6})\s+", lines[end])
                    if next_heading and len(next_heading[1]) <= len(heading[1]):
                        break
                    end += 1
                ranges.append((i, end))
        if not ranges:
            raise ValueError("SECTION_NOT_FOUND: use an exact heading")
        if len(ranges) != 1:
            raise ValueError("AMBIGUOUS_SECTION: repeated heading; read source by offset")
        start, end = ranges[0]
        content = "".join(lines[start:end])
        matches = 1
        selected_lines = [start + 1, end]
    if offset < 0 or offset > len(content):
        raise ValueError("Offset outside selected source")
    # Reserve room for hash, continuation, provenance and incomplete-page warning.
    header = f"SOURCE {snapshot['source']} sha256={sha}\n"
    if len(header) > max_chars // 2:
        raise ValueError("Source path too long for this budget; increase --max-chars")
    reserve = len(header) + 200
    end = min(len(content), offset + max_chars - reserve)
    page = content[offset:end]
    next_offset = end if end < len(content) else None
    if task_spans:
        selected_lines = [number for start, stop, number in task_spans if start < end and stop > offset]
        line = selected_lines[0] if selected_lines else 1
    elif section is not None:
        line = selected_lines[0] + content.count('\n', 0, offset)
        selected_lines = [line, line + page.count('\n')]
    else:
        line = original.count("\n", 0, offset) + 1
    footer = f"\nREAD offset={offset}:{end} source_line={line}"
    if next_offset is not None:
        footer += f"\nUNREAD; same source/selector, --offset {next_offset} --sha256 {sha}"
    else:
        footer += "\nEND of selected text; selection is not proof of authority or task completion."
    text = header + page + footer
    return {"text": text, "content": page, "sha256": sha, "source": snapshot["source"],
            "offset": offset, "next_offset": next_offset, "matches": matches,
            "source_lines": selected_lines, "selection": {"task": task, "section": section},
            "chars": len(text), "utf8_bytes": len(text.encode("utf-8"))}


def render_recall_json(page: dict, max_chars: int) -> str:
    """Bound serialized JSON too, including escaping and provenance metadata."""
    def render(size):
        more = size < len(page["content"]) or page["next_offset"] is not None
        bounds = page["source_lines"]
        payload = {key: page[key] for key in ("source", "sha256", "offset", "matches", "selection")}
        payload.update(content=page["content"][:size],
                       next_offset=page["offset"] + size if more else None,
                       source_line_bounds=[bounds[0], bounds[-1]] if bounds else None,
                       read_state="PARTIAL" if more else "END_SELECTED_TEXT")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(render(0)) >= max_chars:
        raise ValueError("Budget too small for JSON metadata")
    low, high = 0, len(page["content"])
    while low < high:
        mid = (low + high + 1) // 2
        if len(render(mid)) < max_chars:  # include stdout's trailing newline
            low = mid
        else:
            high = mid - 1
    if page["content"] and not low:
        raise ValueError("Budget too small for source text")
    return render(low)


def build_brief(root: Path, *, max_chars: int = 2500) -> dict:
    """A bounded index plus verbatim excerpts; omitted material remains unread."""
    check_budget(max_chars)
    root = Path(root).resolve()
    snapshots = {}
    for name in ("PROJECT_CHARTER.md", "CURRENT.md", "EVIDENCE_INDEX.md"):
        try:
            snapshots[name] = read_source(root, name)
        except FileNotFoundError:
            pass
    charter_snapshot = snapshots.get("PROJECT_CHARTER.md")
    charter = (parse_charter(charter_snapshot["content"], root / DOCS / "PROJECT_CHARTER.md")
               if charter_snapshot else {"exists": False})
    current_snapshot = snapshots.get("CURRENT.md")
    current = parse_current(current_snapshot["content"]) if current_snapshot else {}
    evidence = parse_evidence(snapshots.get("EVIDENCE_INDEX.md", {}).get("content", ""))
    approval = read_approval(root)
    approved = approval is not None and approval.get("approved_sha256") == charter.get("sha256")
    approval_state = "APPROVED" if approved else "UNAPPROVED"
    if approval is not None and not approved:
        approval_state = "APPROVAL_MISMATCH"
    integrity = ""
    if charter.get("expected_sha256") and charter["expected_sha256"] != charter.get("body_sha256"):
        integrity = " CHARTER HASH MISMATCH"
    meta = current.get("metadata", {})
    # Long metadata is not silently authoritative; source is always indexed below.
    task = str(current.get("current_task", "None"))
    status = str(meta.get("status", "UNKNOWN"))
    amendment_dir = root / DOCS / "AMENDMENTS"
    amendments = sorted(amendment_dir.glob("*.md")) if amendment_dir.is_dir() else []
    counts = " ".join(f"{key}:{value}" for key, value in evidence["counts"].items())
    lines = ["=== CODEX CONTINUITY RECOVERY PACKET (index) ===",
             f"CHARTER {approval_state}{integrity}",
             f"ACTIVE task={task[:100]} status={status[:60]} (see CURRENT.md)",
             f"EVIDENCE {counts}; diagnostics={len(evidence['diagnostics'])}; amendments={len(amendments)}"]
    footer = ("\nHOLD: omitted text is UNREAD, not resolved or permission. Read applicable goals, constraints, "
              "amendments and unresolved evidence before acting; unclear scope stays on hold. "
              "No approval inferred.\nSources relative to docs/ai-continuity. "
              "Use: recall <source> [--section HEADING | --task ID]; continue with --offset + --sha256. "
              "restore --full explicitly expands all records.")
    sources, candidates, deferred = [], [], []
    for name in ("PROJECT_CHARTER.md", "CURRENT.md", "EVIDENCE_INDEX.md"):
        if name not in snapshots:
            sources.append(f"MISSING {name}")
            deferred.append({"source": name, "reason": "missing"})
            continue
        snap = snapshots[name]
        source_lines = snap["content"].splitlines()
        sources.append(f"{name} L1-{len(source_lines)} sha256={snap['sha256']}")
        if name == "EVIDENCE_INDEX.md":
            rows = evidence["items"]
            # Exact task equality only; unrelated/unknown scope is never resolved.
            rows = sorted(rows, key=lambda item: (item["id"] != task, item["status"] == "PASS", item["line"]))
            for row in rows:
                if row["status"] != "PASS" or row["id"] == task:
                    candidates.append((name, row["line"], row["raw_line"]))
            for finding in evidence["diagnostics"]:
                candidates.append((name, finding["line"], finding["raw_line"]))
            continue
        # Prefer labelled next steps/constraints, but never treat heuristics as complete.
        numbered = [(i, line) for i, line in enumerate(source_lines, 1)
                    if line.strip() and line.strip() != "---"]
        if len(snap["content"]) > 650:
            important = re.compile(r"next|goal|mission|constraint|block|unknown|stop|禁止|制約|目的|次|未決|停止|承認", re.I)
            numbered.sort(key=lambda pair: (not bool(important.search(pair[1])), pair[0]))
        for number, line in numbered:
            candidates.append((name, number, line))
    if amendments:
        sources.append("AMENDMENTS inventory (recall AMENDMENTS); bodies UNREAD")
        deferred.append({"source": "AMENDMENTS", "reason": "read applicable amendments"})
    base = "\n".join(lines + sources)
    # Reserve a fixed slot for the final omitted-item count too.
    reserve = 60
    if len(base + footer) + reserve > max_chars:
        # Even a tiny budget keeps the read gate and source entry points intact.
        base = "\n".join(lines[:2] + [f"EVIDENCE {counts}; diagnostics={len(evidence['diagnostics'])}",
                                    "Sources: PROJECT_CHARTER.md CURRENT.md EVIDENCE_INDEX.md AMENDMENTS"])
    if len(base + footer) + reserve > max_chars:
        raise ValueError("Budget too small for recovery metadata and read gate")
    output = base
    seen = set()
    for source, number, line in candidates:
        key = (source, number)
        if key in seen:
            continue
        seen.add(key)
        entry = f"\n{source}:L{number} {line}"
        if len(output) + len(entry) + len(footer) + reserve <= max_chars:
            output += entry
        else:
            deferred.append({"source": source, "line": number, "reason": "budget"})
    text = output + f"\nDeferred items={len(deferred)}; source records unchanged." + footer
    packet_status = "INCOMPLETE" if len(snapshots) < 3 else "VALID"
    if integrity or approval_state == "APPROVAL_MISMATCH":
        packet_status = "INTEGRITY_ERROR"
    return {"text": text, "charter_sha256": charter.get("sha256"),
            "git_state": {"is_git": None, "branch": "unknown", "head": "unknown", "dirty": None,
                          "inspection": "deferred; inspect Git before edits"},
            "current_task": task, "status": packet_status,
            "action_state": "HOLD_FOR_RELEVANT_SOURCE_REVIEW",
            "read_state": "PARTIAL" if deferred else "EXCERPTS", "approval_state": approval_state,
            "deferred": deferred, "chars": len(text), "utf8_bytes": len(text.encode("utf-8")),
            "budget_chars": max_chars, "diagnostics": evidence["diagnostics"]}
