"""Tests for generate_status_json._backup_status.

Two backup conventions exist: the Mac Mini's backup_popday_runtime.py writes
a timestamped subdirectory per backup; PythonAnywhere's deploy script writes
a flat popday.sqlite3.<timestamp>.bak file directly under the backup root.
_backup_status must read either correctly for display (2026-07-14 MOT).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_spec = importlib.util.spec_from_file_location(
    "generate_status_json",
    Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py",
)
gsj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsj)


class BackupStatusTests(unittest.TestCase):
    def test_empty_root_reports_zero_backups(self):
        with TemporaryDirectory() as tmp:
            status = gsj._backup_status(Path(tmp))
        self.assertEqual(status["retained_backups"], 0)
        self.assertFalse(status["live_database_backed_up"])

    def test_mac_mini_style_directory_backups(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in ("20260710-080000", "20260711-080000"):
                backup_dir = root / stamp
                backup_dir.mkdir()
                (backup_dir / "popday.sqlite3").write_text("x")
                (backup_dir / "manifest.txt").write_text("x")
            status = gsj._backup_status(root)
        self.assertEqual(status["retained_backups"], 2)
        self.assertEqual(status["latest_backup_at"], "20260711-080000")
        self.assertTrue(status["live_database_backed_up"])
        self.assertIsNotNone(status["latest_manifest_path"])

    def test_pythonanywhere_style_flat_bak_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in ("20260710T080555Z", "20260711T090622Z"):
                (root / f"popday.sqlite3.{stamp}.bak").write_text("x")
            status = gsj._backup_status(root)
        self.assertEqual(status["retained_backups"], 2)
        self.assertEqual(status["latest_backup_at"], "20260711T090622Z")
        self.assertTrue(status["live_database_backed_up"])
        self.assertIsNone(status["latest_manifest_path"])


if __name__ == "__main__":
    unittest.main()
