import unittest
import tempfile
import sys
from pathlib import Path

tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

from loader import (
    parse_frontmatter,
    load_charter,
    load_requirements,
    load_amendments,
    load_evidence,
    load_current,
    build_recovery_packet
)
from atomic_io import atomic_write_text, compute_sha256


class TestLoader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "docs" / "ai-continuity").mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_frontmatter(self):
        text = "---\nkey: value\nnumber: 42\n---\nBody line 1\nBody line 2"
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta.get("key"), "value")
        self.assertEqual(meta.get("number"), "42")
        self.assertIn("Body line 1", body)

    def test_load_charter(self):
        charter_path = self.root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
        content = "---\nproject_id: test-proj\ncharter_version: 1.0.0\napproved_sha256: 1234\n---\n# Mission\nBuild resilient AI memory."
        atomic_write_text(charter_path, content)

        charter = load_charter(self.root)
        self.assertTrue(charter["exists"])
        self.assertEqual(charter["metadata"]["project_id"], "test-proj")
        self.assertEqual(charter["expected_sha256"], "1234")
        self.assertIsNotNone(charter["sha256"])

    def test_build_recovery_packet(self):
        charter_path = self.root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
        content = "---\nproject_id: test-proj\ncharter_version: 1.0.0\n---\nCore mission content."
        atomic_write_text(charter_path, content)

        packet = build_recovery_packet(self.root)
        self.assertIn("CODEX CONTINUITY RECOVERY PACKET", packet["text"])
        self.assertIn("test-proj", packet["text"])
        self.assertEqual(packet["status"], "INCOMPLETE")
        self.assertEqual(packet['action_state'], 'HOLD_FOR_RELEVANT_SOURCE_REVIEW')

    def test_brief_indexes_deferred_context_and_full_preserves_all_sources(self):
        docs = self.root / "docs" / "ai-continuity"
        body = "# Unusual charter structure\n" + "重要な目的。" * 1000 + "\n禁止: CLOUD_UPLOAD禁止"
        atomic_write_text(docs / "PROJECT_CHARTER.md", '---\napproved_sha256: mismatch\n---\n' + body)
        atomic_write_text(docs / "CURRENT.md", '---\ncurrent_task: resume\nstatus: BLOCKED\nevidence_ref: logs/check.log\nnext_steps: CUSTOM_NEXT_STEP\n---\n次に実施: NEXT_REQUIRED_STEP')
        (docs / "AMENDMENTS").mkdir()
        atomic_write_text(docs / "AMENDMENTS" / "one.md", '---\nstatus: APPROVED\napproved_by: USER\nsummary: short\nsupersedes: old-change\n---\n' + "変更内容。" * 100 + "\nAPPROVAL_TAIL_CONSTRAINT")
        rows = [f"| FAIL-{i:03} | FAIL | FAILURE_{i} | log{i} |" for i in range(8)]
        rows.append("| EVID-999 | BLOCKED | BLOCKED_REASON | blocked.log |")
        atomic_write_text(docs / "EVIDENCE_INDEX.md", "\n".join(rows))

        text = build_recovery_packet(self.root, brief=True, max_chars=2500)["text"]

        self.assertLessEqual(len(text), 2500)
        for required in ("CLOUD_UPLOAD禁止", "NEXT_REQUIRED_STEP", "CUSTOM_NEXT_STEP", "CHARTER HASH MISMATCH", "logs/check.log", "HOLD", "AMENDMENTS", "FAIL:8", "BLOCKED:1"):
            self.assertIn(required, text)
        full = build_recovery_packet(self.root, brief=False)["text"]
        for required in (body, "APPROVAL_TAIL_CONSTRAINT", "old-change", "FAILURE_7", "BLOCKED_REASON"):
            self.assertIn(required, full)
        self.assertNotIn("TRUNCATED", text)

    def test_full_expands_optional_detail_while_brief_links_sources(self):
        docs = self.root / "docs" / "ai-continuity"
        atomic_write_text(docs / "PROJECT_CHARTER.md", "Keep data local.")
        atomic_write_text(docs / "EVIDENCE_INDEX.md", "| EVID-001 | PASS | PASS_DETAIL | logs/pass.log |")
        (docs / "AMENDMENTS").mkdir()
        atomic_write_text(docs / "AMENDMENTS" / "proposal.md", "---\nstatus: PROPOSED\n---\nPROPOSED_DETAIL")

        brief = build_recovery_packet(self.root, brief=True)["text"]
        full = build_recovery_packet(self.root, brief=False, max_chars=10)["text"]

        self.assertNotIn("PASS_DETAIL", brief)
        self.assertNotIn("PROPOSED_DETAIL", brief)
        self.assertIn("EVIDENCE_INDEX.md", brief)
        self.assertIn("recall AMENDMENTS", brief)
        self.assertIn("PASS_DETAIL", full)
        self.assertIn("PROPOSED_DETAIL", full)
        self.assertNotIn("TRUNCATED", full)


if __name__ == "__main__":
    unittest.main()
