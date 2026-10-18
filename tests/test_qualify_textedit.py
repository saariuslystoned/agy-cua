import importlib.util
import json
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
    def test_driver_system_exit_records_blocker_without_private_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            out = root / "runs" / "failed"
            with patch.object(pilot, "ROOT", root), patch.object(pilot, "run", side_effect=SystemExit("PRIVATE_SENTINEL")):
                with self.assertRaises(SystemExit):
                    pilot.main(["--model", pilot.MODELS[0], "--repetition", "1", "--out", str(out)])
            self.assertEqual(json.loads((out / "blocker.json").read_text())["exception_type"], "SystemExit")
            self.assertIn("blocked", (out / "STATE.md").read_text())
            self.assertTrue((out / "PROOF.md").is_file())
            self.assertNotIn("PRIVATE_SENTINEL", "".join(p.read_text() for p in out.iterdir()))

    def test_inspection_excludes_preexisting_document_content(self):
        class Session:
            inspected = []
            def call(self, tool, args):
                if tool == "list_windows":
                    return {"structuredContent": {"windows": [
                        {"window_id": 1, "title": "seed.txt"},
                        {"window_id": 2, "title": "seed.txt"},
                        {"window_id": 3, "title": "Untitled"}]}}
                self.inspected.append(args["window_id"])
                return {"content": [], "structuredContent": {}}
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as root, patch.object(pilot.raw_mcp, "Session", Session):
            pilot.inspect(Path(root), 100, "after", allowed_ids={2, 3})
        self.assertEqual(Session.inspected, [2, 3])

    def test_seed_ownership_requires_one_new_window(self):
        prior = [{"window_id": 1, "title": "seed.txt"}]
        new = {"window_id": 2, "title": "seed.txt"}
        self.assertEqual(pilot.new_seed_window_id(prior, prior + [new], "seed.txt"), 2)
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            pilot.new_seed_window_id(prior, prior, "seed.txt")
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            pilot.new_seed_window_id(prior, prior + [new, {"window_id": 3, "title": "seed.txt"}], "seed.txt")

    def test_existing_attempt_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            out = root / "runs" / "retained"
            out.mkdir(parents=True)
            original = {name: ("original:" + name).encode() for name in
                        ("STATE.md", "PROOF.md", "events.jsonl", "heartbeat", "blocker.json", "result.json")}
            for name, data in original.items():
                (out / name).write_bytes(data)
            with patch.object(pilot, "ROOT", root), patch.object(pilot, "run") as runner:
                with self.assertRaises(FileExistsError):
                    pilot.main(["--model", pilot.MODELS[0], "--repetition", "1", "--out", str(out)])
                runner.assert_not_called()
            self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, original)

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
