import unittest
import tempfile
import sys
from pathlib import Path

tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

from continuity import (
    cmd_init,
    cmd_status,
    cmd_checkpoint,
    cmd_restore,
    cmd_doctor,
    cmd_rollback
)
import continuity
from loader import load_charter, load_current


class DummyArgs:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        # Create minimal .git directory to act as root
        (self.root / ".git").mkdir()
        self.orig_cwd = Path.cwd()
        import os
        os.chdir(self.root)

    def tearDown(self):
        import os
        os.chdir(self.orig_cwd)
        self.temp_dir.cleanup()

    def test_init_and_rollback(self):
        # Default initialization is now intentionally fail-closed (no hooks).
        args = DummyArgs(dry_run=False, mission="Explicit user goal")
        cmd_init(args)

        # Check initialized files
        self.assertTrue((self.root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md").exists())
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / ".codex" / "hooks.json").exists())
        self.assertTrue((self.root / ".agents" / "skills" / "continuity-guard" / "SKILL.md").exists())

        # Test rollback
        cmd_rollback(args)
        self.assertFalse((self.root / ".agents" / "skills" / "continuity-guard").exists())

    def test_init_without_hooks_does_not_create_codex_directory(self):
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))

        self.assertFalse((self.root / ".codex").exists())

    def test_checkpoint_preserves_existing_next_steps_and_blockers(self):
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))
        current_file = self.root / "docs/ai-continuity/CURRENT.md"
        body = "# Current state\n\nNext: preserve encryption.\nBlocked: waiting for approval.\n"
        current_file.write_text('---\ncurrent_task: previous\n---\n' + body, encoding="utf-8")

        cmd_checkpoint(DummyArgs(task="new-task", agent="Codex", evidence="logs/test.log", status="BLOCKED"))

        current = load_current(self.root)
        self.assertEqual(current["current_task"], "new-task")
        self.assertEqual(current["metadata"]["evidence_ref"], "logs/test.log")
        self.assertEqual(current["body"], body.strip())

    def test_checkpoint_migrates_only_legacy_generated_state_and_preserves_metadata(self):
        current_file = self.root / "docs/ai-continuity/CURRENT.md"
        current_file.parent.mkdir(parents=True)
        old_meta = '---\ncurrent_task: old\nactive_agent: Codex\nstatus: IN_PROGRESS\nevidence_ref: old.log\nupdated_at: yesterday\nblockers: Keep approval requirement\n---\n'
        old_body = '# Current Progress State\n\nTask: old\nAgent: Codex\nStatus: IN_PROGRESS\nEvidence: old.log\nUpdated: yesterday\n\nNext: preserve custom note.'
        current_file.write_text(old_meta + old_body, encoding="utf-8")

        cmd_checkpoint(DummyArgs(task="new", agent="Codex", evidence="new.log", status="BLOCKED"))

        current = load_current(self.root)
        self.assertEqual(current["metadata"]["blockers"], "Keep approval requirement")
        self.assertNotIn("Task: old", current["body"])
        self.assertNotIn("Evidence: old.log", current["body"])
        self.assertIn("Next: preserve custom note.", current["body"])

    def test_init_without_hooks_preserves_existing_hooks(self):
        hooks_file = self.root / ".codex" / "hooks.json"
        hooks_file.parent.mkdir()
        hooks_file.write_text('{"hooks":[{"name":"user-hook"}]}\n', encoding="utf-8")

        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))

        self.assertEqual(hooks_file.read_text(encoding="utf-8"), '{"hooks":[{"name":"user-hook"}]}\n')

    def test_init_generates_skill_with_absolute_cli_path(self):
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))
        skill = (self.root / ".agents" / "skills" / "continuity-guard" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn(f'`py -3.10 "{Path(continuity.__file__).resolve()}"', skill)

    def test_init_without_hooks_preserves_existing_skill_files_and_is_idempotent(self):
        skill_dir = self.root / ".agents" / "skills" / "continuity-guard"
        skill_dir.mkdir(parents=True)
        custom_file = skill_dir / "custom.md"
        custom_file.write_text("keep this", encoding="utf-8")
        agents_file = self.root / "AGENTS.md"
        agents_file.write_text("User instructions\n", encoding="utf-8")
        args = DummyArgs(dry_run=False, no_hooks=True, mission="Test mission")

        cmd_init(args)
        cmd_init(args)

        self.assertEqual(custom_file.read_text(encoding="utf-8"), "keep this")
        agents = agents_file.read_text(encoding="utf-8")
        self.assertEqual(agents.count("<!-- CCG_CONTINUITY_START -->"), 1)
        self.assertTrue(agents.startswith("User instructions\n"))

    def test_rollback_preserves_unmanaged_files_in_skill_directory(self):
        custom_file = self.root / ".agents" / "skills" / "continuity-guard" / "custom.md"
        custom_file.parent.mkdir(parents=True)
        custom_file.write_text("keep this", encoding="utf-8")
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))

        cmd_rollback(DummyArgs(dry_run=False))

        self.assertTrue(custom_file.exists())
        self.assertEqual(custom_file.read_text(encoding="utf-8"), "keep this")

    def test_rollback_preserves_agents_with_malformed_or_duplicate_markers(self):
        agents_file = self.root / "AGENTS.md"
        malformed = "User instructions\n<!-- CCG_CONTINUITY_START -->\nKeep this trailing content\n"
        agents_file.write_text(malformed, encoding="utf-8")
        cmd_rollback(DummyArgs(dry_run=False))
        self.assertEqual(agents_file.read_text(encoding="utf-8"), malformed)

        duplicate = (
            "Before\n<!-- CCG_CONTINUITY_START -->\nManaged\n<!-- CCG_CONTINUITY_END -->\n"
            "User text between\n<!-- CCG_CONTINUITY_START -->\nMore user text\n"
            "<!-- CCG_CONTINUITY_END -->\nAfter\n"
        )
        agents_file.write_text(duplicate, encoding="utf-8")
        cmd_rollback(DummyArgs(dry_run=False))
        self.assertEqual(agents_file.read_text(encoding="utf-8"), duplicate)

    def test_rollback_preserves_skill_with_user_additions(self):
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="Test mission"))
        skill_file = self.root / ".agents" / "skills" / "continuity-guard" / "SKILL.md"
        generated = skill_file.read_text(encoding="utf-8")
        skill_file.write_text(generated + "\nUser addition\n", encoding="utf-8")

        cmd_rollback(DummyArgs(dry_run=False))

        self.assertTrue(skill_file.exists())
        self.assertEqual(skill_file.read_text(encoding="utf-8"), generated + "\nUser addition\n")

    def test_init_without_hooks_requires_explicit_mission_for_new_project(self):
        with self.assertRaises(SystemExit):
            cmd_init(DummyArgs(dry_run=False, no_hooks=True))
        with self.assertRaises(SystemExit):
            cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission="   "))

        self.assertFalse((self.root / "docs" / "ai-continuity").exists())

    def test_init_records_explicit_project_mission_in_generic_state_files(self):
        mission = "Create a local inventory dashboard."
        constraints = "Keep data in SQLite and do not add cloud services."
        cmd_init(DummyArgs(dry_run=False, no_hooks=True, mission=mission, constraints=constraints))

        charter = load_charter(self.root)
        self.assertIn(mission, charter["body"])
        self.assertIn(constraints, charter["body"])
        self.assertEqual(charter["body_sha256"], charter["expected_sha256"])
        self.assertNotIn("REQ-001", (self.root / "docs" / "ai-continuity" / "REQUIREMENTS.md").read_text(encoding="utf-8"))
        self.assertNotIn("T01-T25", (self.root / "docs" / "ai-continuity" / "CURRENT.md").read_text(encoding="utf-8"))
        self.assertNotIn("T01", (self.root / "docs" / "ai-continuity" / "EVIDENCE_INDEX.md").read_text(encoding="utf-8"))

        (self.root / "docs" / "ai-continuity" / "REQUIREMENTS.md").unlink()
        (self.root / "docs" / "ai-continuity" / "CURRENT.md").unlink()
        (self.root / "docs" / "ai-continuity" / "EVIDENCE_INDEX.md").unlink()
        cmd_init(DummyArgs(dry_run=False, no_hooks=True))

        self.assertNotIn("REQ-001", (self.root / "docs" / "ai-continuity" / "REQUIREMENTS.md").read_text(encoding="utf-8"))
        self.assertNotIn("T01-T25", (self.root / "docs" / "ai-continuity" / "CURRENT.md").read_text(encoding="utf-8"))
        self.assertNotIn("T01", (self.root / "docs" / "ai-continuity" / "EVIDENCE_INDEX.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
