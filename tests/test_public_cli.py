from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


class PublicCliTest(unittest.TestCase):
    def test_version_matches_project_metadata(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        expected = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]

        result = subprocess.run(
            [sys.executable, "-m", "apg.module_sync", "--version"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"module_sync.py {expected}")

    def test_external_registry_sync_then_check_for_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            modules_root = root / "modules"
            (modules_root / "repository" / "files").mkdir(parents=True)
            (modules_root / "repository" / "manifest.yml").write_text(
                "files:\n  - editorconfig:.editorconfig\n", encoding="utf-8"
            )
            (modules_root / "repository" / "files" / "editorconfig").write_text(
                "root = true\n", encoding="utf-8"
            )
            target = root / "target"
            target.mkdir()
            second_target = root / "second-target"
            second_target.mkdir()
            (target / "apg.yml").write_text(
                "modules:\n  - name: repository\n    vars: {}\nproject:\n  name: test\nrealms: []\nenvironments: []\nservices: []\n",
                encoding="utf-8",
            )
            (second_target / "apg.yml").write_text(
                "modules:\n  - name: repository\n    vars: {}\nproject:\n  name: second-test\nrealms: []\nenvironments: []\nservices: []\n",
                encoding="utf-8",
            )
            sync = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "apg.module_sync",
                    "sync",
                    "--modules-root",
                    str(modules_root),
                    str(target),
                    str(second_target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            check = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "apg.module_sync",
                    "check",
                    "--modules-root",
                    str(modules_root),
                    str(target),
                    str(second_target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sync.returncode, 0, sync.stderr)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_invalid_command_exits_with_argparse_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "apg.module_sync", "unknown"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_filesystem_error_exits_two_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            modules_root = root / "modules"
            (modules_root / "bad" / "manifest.yml").mkdir(parents=True)
            target = root / "target"
            target.mkdir()
            (target / "apg.yml").write_text(
                "modules:\n  - name: bad\n    vars: {}\nproject:\n  name: test\nrealms: []\nenvironments: []\nservices: []\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "apg.module_sync",
                    "check",
                    "--modules-root",
                    str(modules_root),
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("apg: cannot read", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
