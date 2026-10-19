import unittest
import tempfile
import json
import sys
from pathlib import Path

tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

from hooks import handle_session_start, handle_pre_compact
from atomic_io import atomic_write_text, compute_sha256


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "docs" / "ai-continuity").mkdir(parents=True)

        charter_path = self.root / "docs" / "ai-continuity" / "PROJECT_CHARTER.md"
        content = "---\nproject_id: hook-test\ncharter_version: 1.0.0\n---\n# Mission\nTest Hook resilience."
        atomic_write_text(charter_path, content)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_start_compact(self):
        event = {"event": "SessionStart", "source": "compact", "cwd": str(self.root)}
        res = handle_session_start(event, self.root)

        self.assertIn("additionalContext", res)
        self.assertIn("systemMessage", res)
        self.assertIn("CODEX CONTINUITY RECOVERY PACKET", res["additionalContext"])
        self.assertIn("hook-test", res["additionalContext"])
        self.assertEqual(res["hookMetadata"]["source"], "compact")

    def test_pre_compact(self):
        event = {"event": "PreCompact", "cwd": str(self.root)}
        res = handle_pre_compact(event, self.root)
        self.assertIn("systemMessage", res)


if __name__ == "__main__":
    unittest.main()
