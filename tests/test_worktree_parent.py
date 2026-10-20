"""Git-backed parent view: two independent worktree research histories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from state_store import StateStore, state_path
from worktree_view import worktree_report


@unittest.skipUnless(shutil.which("git"), "Git is required for worktree integration tests")
class WorktreeParentTests(unittest.TestCase):
    def git(self, *args, cwd=None):
        run = subprocess.run(["git", *args], cwd=cwd or self.main,
                             text=True, capture_output=True, timeout=20,
                             encoding="utf-8", errors="replace")
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.main = self.base / "main repository"
        self.main.mkdir()
        self.old_state = os.environ.get("CCG_STATE_ROOT")
        os.environ["CCG_STATE_ROOT"] = str(self.base / "sqlite state")
        self.git("init", "-b", "main", cwd=self.main)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "NGCG Test")
        docs = self.main / "docs" / "ai-continuity"
        docs.mkdir(parents=True)
        (docs / "PROJECT_CHARTER.md").write_text("# Goal\nCompare two implementations.\n", encoding="utf-8")
        (docs / "CURRENT.md").write_text(
            "---\ncurrent_task: parent-coordination\nstatus: IN_PROGRESS\n---\nParent\n",
            encoding="utf-8",
        )
        (docs / "EVIDENCE_INDEX.md").write_text(
            "| ID | Status | Description | Reference |\n|---|---|---|---|\n",
            encoding="utf-8",
        )
        self.git("add", ".")
        self.git("commit", "-m", "research baseline")
        self.a = self.base / "variant A 研究"
        self.b = self.base / "variant B 研究"
        self.git("worktree", "add", "-b", "variant-a", str(self.a))
        self.git("worktree", "add", "-b", "variant-b", str(self.b))

    def tearDown(self):
        if self.old_state is None:
            os.environ.pop("CCG_STATE_ROOT", None)
        else:
            os.environ["CCG_STATE_ROOT"] = self.old_state
        self.tmp.cleanup()

    def test_two_variants_keep_separate_progress_and_history(self):
        for root, task, status, agent in (
            (self.a, "MODEL-A", "FAIL", "Codex-A"),
            (self.b, "MODEL-B", "PASS", "Codex-B"),
        ):
            folder = root / "docs" / "ai-continuity"
            (folder / "CURRENT.md").write_text(
                f"---\ncurrent_task: {task}\nstatus: {status}\n---\n",
                encoding="utf-8",
            )
            (folder / "EVIDENCE_INDEX.md").write_text(
                "| ID | Status | Description | Reference |\n|---|---|---|---|\n"
                f"| {task} | {status} | observed locally | receipt.json |\n",
                encoding="utf-8",
            )
            StateStore(root).checkpoint(task=task, agent=agent, status=status)

        self.assertFalse(state_path(self.main).exists())
        result = worktree_report(self.main)
        self.assertFalse(state_path(self.main).exists(), "Read-only parent view must not create a DB")
        self.assertEqual(result["layout"], "PARENT_AND_TWO_VARIANTS")
        self.assertEqual(result["variant_count"], 2)
        self.assertEqual(result["parent"]["current_task"], "parent-coordination")
        by_branch = {item["branch"]: item for item in result["variants"]}
        self.assertEqual(by_branch["variant-a"]["current_task"], "MODEL-A")
        self.assertEqual(by_branch["variant-b"]["current_task"], "MODEL-B")
        self.assertEqual(by_branch["variant-a"]["evidence_counts"]["FAIL"], 1)
        self.assertEqual(by_branch["variant-b"]["evidence_counts"]["PASS"], 1)
        self.assertEqual(by_branch["variant-a"]["latest_checkpoints"][0]["agent"], "Codex-A")
        self.assertEqual(by_branch["variant-b"]["latest_checkpoints"][0]["agent"], "Codex-B")
        self.assertEqual(by_branch["variant-a"]["charter_approval"], "UNAPPROVED")
        self.assertNotEqual(state_path(self.a), state_path(self.b))

    def test_child_can_read_same_parent_without_changing_authority(self):
        result = worktree_report(self.b)
        self.assertEqual(result["parent"]["path"], str(self.main.resolve()))
        self.assertFalse(result["parent"]["is_current"])
        self.assertTrue(next(x for x in result["variants"] if x["branch"] == "variant-b")["is_current"])
        self.assertIn("not inherited", result["notice"])
        self.assertEqual(result["recommended_variants"], 2)

    def test_cli_json_has_no_hidden_write_or_extra_worktree(self):
        script = Path(__file__).resolve().parent.parent / "continuity.py"
        # Force a legacy code page even on Linux, reproducing Windows redirection.
        # Worktree CLI must emit Unicode text/JSON without a charmap crash.
        legacy_env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        result = subprocess.run(
            [sys.executable, "-B", str(script), "worktrees", "--json"],
            cwd=self.main, capture_output=True, text=True,
            encoding="utf-8", timeout=20, env=legacy_env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["variant_count"], 2)
        self.assertEqual(len(payload["variants"]), 2)
        self.assertIn("研究", result.stdout)
        human = subprocess.run(
            [sys.executable, "-B", str(script), "worktrees"],
            cwd=self.main, capture_output=True, text=True,
            encoding="utf-8", timeout=20, env=legacy_env,
        )
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn(str(self.a.resolve()), human.stdout)
        self.assertIn(str(self.b.resolve()), human.stdout)
        self.assertFalse(state_path(self.main).exists())


if __name__ == "__main__":
    unittest.main()
