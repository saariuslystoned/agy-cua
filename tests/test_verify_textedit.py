import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "verify_textedit", Path(__file__).resolve().parents[1] / "scripts/verify_textedit.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


class FileOracleTest(unittest.TestCase):
    def test_exact_bytes_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.txt"
            self.assertFalse(oracle.verify_file(path)["passed"])
            path.write_bytes(oracle.NOTE.encode())
            self.assertTrue(oracle.verify_file(path)["passed"])
            for invalid in (oracle.SEED.encode(), (oracle.NOTE + "extra\n").encode(), oracle.NOTE.encode("utf-16")):
                path.write_bytes(invalid)
                self.assertFalse(oracle.verify_file(path)["passed"])
            other = Path(directory) / "other.txt"
            other.write_text(oracle.NOTE)
            path.unlink()
            path.symlink_to(other)
            self.assertFalse(oracle.verify_file(path)["passed"])

    def test_cli_is_read_only_and_does_not_certify_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            seed, note = Path(directory) / "seed.txt", Path(directory) / "note.txt"
            seed.write_text(oracle.SEED)
            note.write_text(oracle.NOTE)
            before = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = oracle.main(["--seed", str(seed), "--note", str(note)])
            self.assertEqual(code, 0)
            self.assertIsNone(json.loads(output.getvalue())["ui_recovery_verified"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(directory).iterdir()})
            note.write_text(oracle.SEED)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(oracle.main(["--seed", str(seed), "--note", str(note)]), 1)
