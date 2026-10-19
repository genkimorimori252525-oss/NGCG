#!/usr/bin/env python3
"""Optional local Continuity Hub. Defaults OFF; never launches models itself.

Command-line actions are deterministic; UI events never trigger LLM calls.
Observation accepts published Codex exec JSONL, not private internal state.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from state_store import StateStore, valid_department


def ingest_jsonl(store: StateStore, stream, *, department: str, role: str, run_id: str) -> dict:
    """Ingest only observable events; never invent human/AI messages."""
    if not valid_department(department) or role not in ("sol", "astra"):
        raise ValueError("Department/role required for observed CLI stream")
    events, actual_messages, usage = 0, 0, 0
    for number, raw in enumerate(stream, 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            # Preserve parser failure as a system event, no made-up model output.
            store.append_event("codex-jsonl", f"{run_id}:{number}", "parse_error", {"line": number})
            continue
        if not isinstance(event, dict):
            continue
        typ = str(event.get("type", "unknown"))
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        source_id = f"{run_id}:{number}"
        if typ == "item.completed" and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            store.post_message(department, role, f"{role}@{department}", item["text"],
                               provenance="codex-exec-json", origin_id=source_id)
            actual_messages += 1
        elif typ == "turn.completed":
            fields = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            # UNKNOWN is represented by missing field, not a fabricated zero.
            allowed = {k: fields[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens") if k in fields}
            store.append_event("codex-jsonl", source_id, "usage", {"run_id": run_id, "usage": allowed})
            usage += 1
        elif typ in {"turn.failed", "error"}:
            # Avoid saving arbitrary stderr / secrets in the UI event stream.
            store.append_event("codex-jsonl", source_id, "run_error", {"run_id": run_id, "type": typ})
        elif typ in {"thread.started", "turn.started", "item.started", "item.completed"}:
            # No AI authorship claim for tool events; lightweight provenance only.
            store.append_event("codex-jsonl", source_id, "run_observation", {
                "run_id": run_id, "type": typ, "item_type": item.get("type", "unknown")})
        events += 1
    return {"lines_observed": events, "agent_messages": actual_messages, "usage_events": usage}


def main():
    p = argparse.ArgumentParser(description="Continuity Hub optional project chat")
    p.add_argument("--project", default=".", help="Project root for isolation")
    sub = p.add_subparsers(dest="command", required=True)
    pt = sub.add_parser("team", help="Read or change explicit per-project team mode")
    pt.add_argument("mode", choices=("status", "on", "off"))
    ps = sub.add_parser("serve", help="Start localhost-only UI; requires team mode ON")
    ps.add_argument("--port", type=int, default=0)
    ps = sub.add_parser("message", help="Create attributed durable message; cannot impersonate observed native messages")
    ps.add_argument("--department", required=True, help="Project channel ID (general if no departments)")
    ps.add_argument("--role", choices=("sol", "astra", "operator"), required=True)
    ps.add_argument("--author", required=True)
    ps.add_argument("--recipient", action="append", default=[])
    ps.add_argument("--thread", default="general")
    ps.add_argument("--text", required=True)
    ps = sub.add_parser("inbox")
    ps.add_argument("--recipient", required=True)
    ps = sub.add_parser("ack")
    ps.add_argument("--message", required=True)
    ps.add_argument("--recipient", required=True)
    ps = sub.add_parser("observe", help="Ingest genuine public Codex exec JSONL via stdin")
    ps.add_argument("--department", required=True, help="Project channel ID (general if no departments)")
    ps.add_argument("--role", choices=("sol", "astra"), required=True)
    ps.add_argument("--run-id", default=None)
    ps = sub.add_parser("checkpoint", help="System-sourced, unique checkpoint record")
    ps.add_argument("--task", required=True)
    ps.add_argument("--agent", required=True)
    ps.add_argument("--status", default="IN_PROGRESS")
    ps.add_argument("--evidence", default="")
    ps = sub.add_parser("claim", help="Acquire task lease")
    ps.add_argument("--task", required=True)
    ps.add_argument("--owner", required=True)
    ps = sub.add_parser("release", help="Release task lease with matching epoch")
    ps.add_argument("--task", required=True)
    ps.add_argument("--owner", required=True)
    ps.add_argument("--epoch", type=int, required=True)
    args = p.parse_args()
    store = StateStore(Path(args.project))
    if args.command == "team":
        if args.mode in ("on", "off"):
            store.set_chat_enabled(args.mode == "on")
        print(json.dumps({"project": store.project_id, "chat_enabled": store.chat_enabled()}))
    elif args.command == "serve":
        if not store.chat_enabled():
            raise SystemExit("Team chat is OFF for this project. Enable explicitly: hub_runtime.py team on")
        from hub_server import serve
        serve(store, args.port)
    elif args.command == "message":
        if not store.chat_enabled():
            raise SystemExit("Team chat is OFF")
        ident = store.post_message(args.department, args.role, args.author, args.text,
                                   recipients=tuple(args.recipient), thread_id=args.thread,
                                   provenance="cli-reported")
        print(json.dumps({"id": ident, "provenance": "cli-reported"}))
    elif args.command == "inbox":
        print(json.dumps(store.mailbox(args.recipient), ensure_ascii=False))
    elif args.command == "ack":
        print(json.dumps({"acknowledged": store.acknowledge(args.message, args.recipient)}))
    elif args.command == "observe":
        if not store.chat_enabled():
            raise SystemExit("Team chat is OFF")
        print(json.dumps(ingest_jsonl(store, sys.stdin, department=args.department,
                                      role=args.role, run_id=args.run_id or uuid.uuid4().hex)))
    elif args.command == "checkpoint":
        print(json.dumps({"checkpoint_id": store.checkpoint(args.task, args.agent,
                                                               status=args.status, evidence=args.evidence)}))
    elif args.command == "claim":
        epoch = store.claim(args.task, args.owner)
        print(json.dumps({"claimed": epoch is not None, "epoch": epoch}))
    elif args.command == "release":
        print(json.dumps({"released": store.release(args.task, args.owner, args.epoch)}))


if __name__ == "__main__":
    main()
