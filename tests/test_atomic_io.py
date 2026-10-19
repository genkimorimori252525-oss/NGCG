import unittest
import tempfile
import os
import sys
from pathlib import Path

tools_dir = Path(__file__).resolve().parent.parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

from atomic_io import (
    atomic_write_text,
    safe_read_text,
    compute_sha256,
    create_backup,
    normalize_newlines
)


class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_atomic_write_and_read(self):
        target = self.dir_path / "test_file.txt"
        content = "Hello, Codex Continuity Guard! 日本語テスト\nLine 2\nLine 3"
        atomic_write_text(target, content)
        self.assertTrue(target.exists())
        read_back = safe_read_text(target)
        self.assertEqual(read_back, content)

    def test_backup_creation(self):
        target = self.dir_path / "target.txt"
        atomic_write_text(target, "Version 1")
        bak = create_backup(target)
        self.assertIsNotNone(bak)
        self.assertTrue(bak.exists())
        self.assertEqual(safe_read_text(bak), "Version 1")

    def test_sha256_newline_normalization(self):
        text_crlf = "line1\r\nline2\r\n"
        text_lf = "line1\nline2\n"
        hash_crlf = compute_sha256(text_crlf, normalize_text=True)
        hash_lf = compute_sha256(text_lf, normalize_text=True)
        self.assertEqual(hash_crlf, hash_lf)


if __name__ == "__main__":
    unittest.main()
