import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("qualify_textedit", SCRIPTS / "qualify_textedit.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class FileOracleTest(unittest.TestCase):
    def test_inspection_excludes_preexisting_document_content(self):
        class Session:
            inspected = []
            def call(self, tool, args):
                if tool == "list_windows":
                    return {"structuredContent": {"windows": [
                        {"window_id": 1, "title": "private-existing-note"},
                        {"window_id": 2, "title": "seed.txt"},
                        {"window_id": 3, "title": "Untitled"}]}}
                self.inspected.append(args["window_id"])
                return {"content": [], "structuredContent": {}}
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as root, patch.object(pilot.raw_mcp, "Session", Session):
            pilot.inspect(Path(root), 100, "after", allowed_ids={3}, allowed_titles={"seed.txt"})
        self.assertEqual(Session.inspected, [2, 3])

    def test_exact_content_only_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "note.txt"
            self.assertFalse(pilot.verify_file(path)["passed"])
            path.write_bytes(pilot.NOTE.encode())
            self.assertTrue(pilot.verify_file(path)["passed"])
            path.write_text(pilot.SEED)
            self.assertFalse(pilot.verify_file(path)["passed"])
            path.write_text(pilot.NOTE + "extra\n")
            self.assertFalse(pilot.verify_file(path)["passed"])
            path.write_bytes(pilot.NOTE.encode("utf-16"))
            self.assertFalse(pilot.verify_file(path)["passed"])
            other = Path(root) / "other.txt"
            other.write_text(pilot.NOTE)
            path.unlink()
            path.symlink_to(other)
            self.assertFalse(pilot.verify_file(path)["passed"])


if __name__ == "__main__":
    unittest.main()
