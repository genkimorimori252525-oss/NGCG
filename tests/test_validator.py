import unittest
import tempfile
import sys
from pathlib import Path

tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

from validator import (
    validate_charter_integrity,
    validate_fake_success,
    run_full_audit
)
from atomic_io import atomic_write_text, compute_sha256


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "docs" / "ai-continuity").mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_charter_tamper_detection(self):
        charter_path = self.root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
        content = "---\nproject_id: secure-proj\napproved_sha256: correct_hash\n---\nOriginal content"
        atomic_write_text(charter_path, content)

        # Integrity check should detect drift
        findings = validate_charter_integrity(self.root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "CHARTER_DRIFT_DETECTED")

    def test_fake_success_detection(self):
        current_path = self.root / "docs" / "ai-continuity" / "CURRENT.md"
        current_content = "---\ncurrent_task: none\ncompleted_tasks: TASK-999\n---\nProgress"
        atomic_write_text(current_path, current_content)

        evidence_path = self.root / "docs" / "ai-continuity" / "EVIDENCE_INDEX.md"
        # No PASS entry for TASK-999
        evidence_content = "| EVID-001 | PASS | Some other task | log.txt |"
        atomic_write_text(evidence_path, evidence_content)

        findings = validate_fake_success(self.root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "UNVERIFIED_SUCCESS_DECLARATION")


if __name__ == "__main__":
    unittest.main()
