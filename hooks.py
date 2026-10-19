#!/usr/bin/env python
"""Codex Native Hook Handler.

Handles SessionStart(startup|resume|compact) and PreCompact events.
Reads event payload from stdin, locates project root, and emits
restoration packet via additionalContext.
"""

import sys
import json
import time
from pathlib import Path

# Add current directory to path if run standalone
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

try:
    from loader import find_git_root, build_recovery_packet
    from validator import run_full_audit
except ImportError:
    from tools.continuity_guard.loader import find_git_root, build_recovery_packet
    from tools.continuity_guard.validator import run_full_audit


def handle_session_start(event: dict, project_root: Path) -> dict:
    """Handle SessionStart hook.

    All sources, including compact, use the same bounded recovery index.
    Detailed audit remains an explicit operation; the index shows anchor state.
    """
    source = event.get("source", "unknown")
    start_time = time.time()
    packet = build_recovery_packet(project_root, brief=True)
    elapsed_ms = (time.time() - start_time) * 1000

    context_text = packet["text"]

    system_msg = (
        f"[Codex Continuity Guard] Context restored for event=SessionStart(source={source}) "
        f"in {elapsed_ms:.1f}ms. Status: {packet['status']}."
    )

    return {
        "additionalContext": context_text,
        "systemMessage": system_msg,
        "hookMetadata": {
            "source": source,
            "status": packet["status"],
            "elapsed_ms": elapsed_ms,
            "charter_sha256": packet.get("charter_sha256")
        }
    }


def handle_pre_compact(event: dict, project_root: Path) -> dict:
    """Handle PreCompact hook to alert on unsaved state."""
    audit_result = run_full_audit(project_root)
    if not audit_result["healthy"]:
        msg = f"[Codex Continuity Guard] WARNING: Pre-compaction audit detected {audit_result['counts']['errors']} error(s)!"
    else:
        msg = "[Codex Continuity Guard] Pre-compaction integrity check passed. Charter and records safe."

    return {
        "systemMessage": msg
    }


def main():
    # Read stdin if provided
    raw_input = ""
    event = {}
    if not sys.stdin.isatty():
        try:
            raw_input = sys.stdin.read()
            if raw_input.strip():
                event = json.loads(raw_input)
        except Exception:
            # Handle non-JSON or partial inputs safely without crashing
            event = {}

    # Determine cwd from event or current directory
    cwd_hint = event.get("cwd") or event.get("working_directory") or Path.cwd()
    project_root = find_git_root(Path(cwd_hint))

    event_name = event.get("event") or event.get("hook_event") or "SessionStart"

    response = {}
    if "sessionstart" in event_name.lower():
        response = handle_session_start(event, project_root)
    elif "precompact" in event_name.lower():
        response = handle_pre_compact(event, project_root)
    else:
        # Default fallback
        response = handle_session_start(event, project_root)

    # Output JSON to stdout
    print(json.dumps(response, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
