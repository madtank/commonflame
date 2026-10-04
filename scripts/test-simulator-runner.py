"""Small standard-library checks for private host checkpoint storage."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "runner", Path(__file__).with_name("simulate.py")
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CheckpointTests(unittest.TestCase):
    def test_private_checkpoint_replaces_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{}")
            path.chmod(0o644)
            runner.atomic_json(
                path, {"refresh_token": "test-fixture-only"}, private=True
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("test-fixture-only", path.read_text())

    def test_checkpoint_cannot_replace_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.json"
            target.write_text("unchanged")
            link = Path(directory) / "state.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                runner.atomic_json(link, {"value": "changed"}, private=True)
            self.assertEqual(target.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
