#!/usr/bin/env python3
"""Run an explicit verification command and create local machine evidence.

No LLM call, no background check, no automatic 'PASS' without process exit 0.
The receipt proves a command ran in this workspace; it does not prove a test
suite is adequate, nor prevent same-user intentional evidence fabrication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from atomic_io import atomic_write_text
from loader import find_git_root, load_git_state, parse_evidence
from state_store import StateStore, utc_now


def run_and_record(project_root: Path, task: str, argv: list[str], timeout: int = 600) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task):
        raise ValueError("task ID must contain only alnum, _ or -")
    if not argv:
        raise ValueError("Missing verification command")
    project_root = Path(project_root).resolve()
    state_dir = project_root / "docs" / "ai-continuity"
    proof_dir = state_dir / "verification-receipts"
    proof_dir.mkdir(parents=True, exist_ok=True)
    identity = f"{task}_{uuid.uuid4().hex}"
    log = proof_dir / (identity + ".log")
    receipt = proof_dir / (identity + ".json")
    git = load_git_state(project_root)
    started = time.monotonic()
    with open(log, "wb") as dst:
        try:
            completed = subprocess.run(argv, cwd=project_root, stdin=subprocess.DEVNULL,
                                       stdout=dst, stderr=subprocess.STDOUT, timeout=timeout,
                                       shell=False, check=False)
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            dst.write(b"\n[CCG TIMEOUT]\n")
        except OSError as exc:
            exit_code = 127
            dst.write(("[CCG EXECUTION ERROR] " + type(exc).__name__).encode("utf-8"))
    data = log.read_bytes()
    status = "PASS" if exit_code == 0 else "FAIL"
    rel_log = log.relative_to(project_root).as_posix()
    rel_receipt = receipt.relative_to(project_root).as_posix()
    payload = {
        "schema_version": 1, "task_id": task, "result": status,
        "exit_code": exit_code,
        "command": argv, "observed_at": utc_now(),
        "elapsed_ms": int((time.monotonic()-started)*1000),
        "git_head": git.get("head", "unknown"),
        "git_dirty": bool(git.get("dirty", False)),
        "output_path": rel_log,
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "receipt_id": identity,
    }
    atomic_write_text(receipt, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    index = state_dir / "EVIDENCE_INDEX.md"
    store = StateStore(project_root)
    with store.connection(write=True) as db:
        original = index.read_text(encoding="utf-8") if index.exists() else "# Evidence Index\n\n| ID | Status | Description | Reference |\n|---|---|---|---|\n"
        # Preserve previous attempts, including FAIL/UNKNOWN, as original history.
        cleaned = original.splitlines()
        if parse_evidence(original)["layout"] != (0, 1, 3):
            cleaned.extend(["", "| ID | Status | Description | Reference |", "|---|---|---|---|"])
        cleaned.append(f"| {task} | {status} | Executed command exit={exit_code} | {rel_receipt} |")
        atomic_write_text(index, "\n".join(cleaned).rstrip() + "\n")
        store._insert_event(db, "ccg-test", identity, "verification", {
            "task_id": task, "result": status, "receipt_ref": rel_receipt, "git_head": git.get("head")})
    payload["receipt_ref"] = rel_receipt
    return payload


def main():
    parser = argparse.ArgumentParser(description="Run verifications with durable evidence")
    parser.add_argument("--project", default=".")
    parser.add_argument("--task", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = args.command[1:] if args.command and args.command[0] == "--" else args.command
    output = run_and_record(Path(args.project), args.task, cmd, args.timeout)
    print(json.dumps({"task": args.task, "status": output["result"], "receipt": output["receipt_ref"],
                      "elapsed_ms": output["elapsed_ms"]}))
    sys.exit(0 if output["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
