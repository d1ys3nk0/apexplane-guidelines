from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from apg.manifest import ManifestError
from apg.module_sync import (
    MANAGED_MANIFEST_FILENAME,
    ModuleError,
    check_target as _check_target,
    compose_target_state as _compose_target_state,
    load_module as _load_module,
    run_linters as _run_linters,
    sync_target as _sync_target,
    verify_target as _verify_target,
)


def load_module(name: str, modules_root: Path):
    return _load_module(modules_root, name)


def compose_target_state(manifest, modules_root: Path):
    return _compose_target_state(manifest, modules_root)


def check_target(target: Path, modules_root: Path):
    return _check_target(target, modules_root)


def sync_target(target: Path, modules_root: Path):
    return _sync_target(target, modules_root)


def run_linters(target: Path, modules_root: Path, **kwargs):
    return _run_linters(target, modules_root, **kwargs)


def verify_target(target: Path, modules_root: Path):
    return _verify_target(target, modules_root)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_module(root: Path, name: str) -> Path:
    module_dir = root / name
    (module_dir / "files").mkdir(parents=True, exist_ok=True)
    (module_dir / "templates").mkdir(parents=True, exist_ok=True)
    (module_dir / "linters").mkdir(parents=True, exist_ok=True)
    write(module_dir / "manifest.yml", "{}\n")
    return module_dir


def manifest(*modules: str) -> str:
    module_lines = "\n".join(
        f"  - name: {module}\n    vars: {{}}" for module in modules
    )
    return f"""
modules:
{module_lines}
project:
  name: app
realms: []
environments: []
services:
  - name: api
    kind: web
""".lstrip()


def manifest_with_python() -> str:
    return """
modules:
  - name: repository
    vars: {}
  - name: taskfile
    vars: {}
  - name: python
    vars:
      python_version: "3.13"
project:
  name: app
realms: []
environments: []
services:
  - name: api
    kind: web
""".lstrip()


class ModuleSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.modules_root = Path(self.temporary_directory.name) / "modules"
        self.modules_root.mkdir()
        self._make_catalogue()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _make_catalogue(self) -> None:
        """Create only the fixtures needed to test APG's generic module engine."""
        for name, manifest_content, files, templates, linters in (
            (
                "common",
                "linters:\n  - links.py\n  - architecture_dependencies.py\n",
                {},
                {},
                {"links.py": "", "architecture_dependencies.py": ""},
            ),
            (
                "repository",
                "files:\n  - editorconfig:.editorconfig\nlinters:\n  - repository_contract.py\n",
                {"editorconfig": "root = true\n"},
                {},
                {"repository_contract.py": ""},
            ),
            (
                "taskfile",
                "files:\n  - Taskfile.yml:Taskfile.yml\n",
                {"Taskfile.yml": "version: '3'\n"},
                {},
                {},
            ),
            (
                "python",
                "vars:\n  python_version:\n    type: string\n    required: true\ntemplates:\n  - python-version.j2:.python-version\nlinters:\n  - contract.py\n",
                {},
                {"python-version.j2": "{{ vars.python_version }}" + chr(10)},
                {"contract.py": ""},
            ),
            (
                "ci",
                "files:\n  - verify.yml:.github/workflows/_verify.yml\n",
                {"verify.yml": "verify\n"},
                {},
                {},
            ),
            ("python-ci", "{}\n", {}, {}, {}),
            (
                "ansible",
                "templates:\n  - apg.yml.j2:.taskfiles/apg.yml\nfiles:\n  - run:.taskfiles/apg/bin/run\n  - static.py:.taskfiles/apg/tests/static/apc_target_static.py\n",
                {"run": "#!/bin/sh\n", "static.py": ""},
                {
                    "apg.yml.j2": "{% for group in ansible.groups %}{% for cluster in group.clusters %}{{ group.realm }}:{{ group.platform }}:{{ cluster }}:setup"
                    + chr(10)
                    + "{% endfor %}{% endfor %}{% for tunnel in ansible.tunnels %}{{ tunnel.realm }}:{{ tunnel.platform }}:{{ tunnel.cluster }}:{{ tunnel.service }}"
                    + chr(10)
                    + "{% endfor %}"
                },
                {},
            ),
            (
                "precommit",
                "vars:\n  shell:\n    type: boolean\n    default: true\n  uv:\n    type: boolean\n    default: true\n  ty:\n    type: boolean\n    default: true\n  ruff:\n    type: boolean\n    default: true\n  static:\n    type: boolean\n    default: true\ntemplates:\n  - config.j2:.pre-commit-config.yaml\n",
                {},
                {
                    "config.j2": "{% if vars.shell %}shellcheck shfmt {% endif %}{% if vars.uv %}uv-lock {% endif %}{% if vars.ty %}ty-types {% endif %}{% if vars.ruff %}ruff {% endif %}{% if vars.static %}static-tests{% endif %}"
                },
                {},
            ),
        ):
            module_dir = make_module(self.modules_root, name)
            write(module_dir / "manifest.yml", manifest_content)
            for path, content in files.items():
                write(module_dir / "files" / path, content)
            for path, content in templates.items():
                write(module_dir / "templates" / path, content)
            for path, content in linters.items():
                write(module_dir / "linters" / path, content)

    def test_loads_domain_modules_and_composes_manifest_order(self) -> None:
        from apg.manifest import validate_manifest

        target_manifest = validate_manifest(
            {
                "modules": [
                    {"name": "common", "vars": {}},
                    {"name": "repository", "vars": {}},
                    {"name": "taskfile", "vars": {}},
                    {
                        "name": "python",
                        "vars": {
                            "python_version": "3.13",
                        },
                    },
                    {"name": "ci", "vars": {}},
                    {"name": "python-ci", "vars": {}},
                ],
                "project": {"name": "app"},
                "realms": [],
                "environments": [],
                "services": [],
            }
        )
        desired, linters = compose_target_state(target_manifest, self.modules_root)

        destinations = {item.destination for item in desired}
        self.assertIn(".editorconfig", destinations)
        self.assertIn(".python-version", destinations)
        self.assertNotIn("pyproject.toml", destinations)
        self.assertIn(".github/workflows/_verify.yml", destinations)
        self.assertEqual(
            linters,
            (
                "common:links.py",
                "common:architecture_dependencies.py",
                "repository:repository_contract.py",
                "python:contract.py",
            ),
        )

    def test_checked_in_modules_have_unique_managed_destinations(self) -> None:
        owners: dict[str, str] = {}
        conflicts: list[str] = []

        for module_dir in sorted(p for p in self.modules_root.iterdir() if p.is_dir()):
            manifest_path = module_dir / "manifest.yml"
            if not manifest_path.is_file():
                continue
            module = load_module(module_dir.name, self.modules_root)
            for entry in (*module.files, *module.templates):
                owner = owners.setdefault(entry.destination, module.name)
                if owner != module.name:
                    conflicts.append(f"{entry.destination}: {owner}, {module.name}")

        self.assertEqual(conflicts, [])

    def test_ansible_module_renders_topology_tasks_and_runtime_paths(self) -> None:
        from apg.manifest import validate_manifest

        target_manifest = validate_manifest(
            {
                "modules": [
                    {"name": "taskfile", "vars": {}},
                    {"name": "ansible", "vars": {}},
                ],
                "project": {"name": "app"},
                "realms": [{"name": "prd"}],
                "environments": [],
                "services": [],
                "ansible": {
                    "python_version": "3.13",
                    "groups": [
                        {"realm": "prd", "platform": "ycl", "clusters": ["app", "dbs"]}
                    ],
                    "tunnels": [
                        {
                            "realm": "prd",
                            "platform": "ycl",
                            "cluster": "app",
                            "service": "traefik",
                        }
                    ],
                },
            }
        )
        desired, _ = compose_target_state(target_manifest, self.modules_root)
        rendered = next(
            item.content.decode()
            for item in desired
            if item.destination == ".taskfiles/apg.yml"
        )
        destinations = {item.destination for item in desired}

        self.assertIn("prd:ycl:app:setup", rendered)
        self.assertIn("prd:ycl:app:traefik", rendered)
        self.assertIn(".taskfiles/apg/bin/run", destinations)
        self.assertIn(".taskfiles/apg/tests/static/apc_target_static.py", destinations)

    def test_missing_target_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ManifestError, "missing required file"):
                check_target(Path(tmp), self.modules_root)

    def test_missing_manifest_module_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("missing"))

            with self.assertRaisesRegex(ModuleError, "missing required file"):
                check_target(repo, self.modules_root)

    def test_missing_required_module_var_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("python"))

            with self.assertRaisesRegex(
                ModuleError, "missing required var 'python_version'"
            ):
                check_target(repo, self.modules_root)

    def test_unknown_module_var_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: python
    vars:
      python_version: "3.13"
      extra: "value"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            with self.assertRaisesRegex(ModuleError, "unknown var 'extra'"):
                check_target(repo, self.modules_root)

    def test_boolean_module_var_with_default_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "with_bool")
            write(
                module_root / "with_bool" / "manifest.yml",
                """
vars:
  name:
    type: string
    required: true
  flag:
    type: boolean
    default: true
templates:
  - flag.j2:flag.txt
""",
            )
            write(
                module_root / "with_bool" / "templates" / "flag.j2",
                "{{ 'on' if vars.flag else 'off' }}",
            )
            write(
                repo / "apg.yml",
                """
modules:
  - name: with_bool
    vars:
      name: "x"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            sync_target(repo, module_root)

            self.assertEqual((repo / "flag.txt").read_text(encoding="utf-8"), "on")

    def test_boolean_module_var_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "with_bool")
            write(
                module_root / "with_bool" / "manifest.yml",
                """
vars:
  flag:
    type: boolean
    default: true
templates:
  - flag.j2:flag.txt
""",
            )
            write(
                module_root / "with_bool" / "templates" / "flag.j2",
                "{{ 'on' if vars.flag else 'off' }}",
            )
            write(
                repo / "apg.yml",
                """
modules:
  - name: with_bool
    vars:
      flag: false
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            sync_target(repo, module_root)

            self.assertEqual((repo / "flag.txt").read_text(encoding="utf-8"), "off")

    def test_boolean_module_var_string_value_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: precommit
    vars:
      shell: "true"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            with self.assertRaisesRegex(ModuleError, "var 'shell' must be a boolean"):
                check_target(repo, self.modules_root)

    def test_string_list_module_var_renders_into_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "with_list")
            write(
                module_root / "with_list" / "manifest.yml",
                """
vars:
  name:
    type: string
    required: true
  deps:
    type: string-list
    required: true
templates:
  - list.j2:list.txt
""",
            )
            write(
                module_root / "with_list" / "templates" / "list.j2",
                "{% for item in vars.deps %}- {{ item }}\n{% endfor %}",
            )
            write(
                repo / "apg.yml",
                """
modules:
  - name: with_list
    vars:
      name: "deps"
      deps:
        - "alembic>=1.0"
        - "click>=8.0"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            sync_target(repo, module_root)

            self.assertEqual(
                (repo / "list.txt").read_text(encoding="utf-8"),
                "- alembic>=1.0\n- click>=8.0\n",
            )

    def test_string_list_module_var_default_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "with_list_default")
            write(
                module_root / "with_list_default" / "manifest.yml",
                """
vars:
  deps:
    type: string-list
    required: false
    default:
      - "fallback-a"
      - "fallback-b"
templates:
  - list.j2:list.txt
""",
            )
            write(
                module_root / "with_list_default" / "templates" / "list.j2",
                "{{ vars.deps | join(',') }}",
            )
            write(repo / "apg.yml", manifest("with_list_default"))

            sync_target(repo, module_root)

            self.assertEqual(
                (repo / "list.txt").read_text(encoding="utf-8"), "fallback-a,fallback-b"
            )

    def test_string_list_module_var_rejects_non_list_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "with_list")
            write(
                module_root / "with_list" / "manifest.yml",
                """
vars:
  deps:
    type: string-list
    required: true
templates:
  - list.j2:list.txt
""",
            )
            write(
                repo / "apg.yml",
                """
modules:
  - name: with_list
    vars:
      deps: "not-a-list"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            with self.assertRaisesRegex(
                ModuleError, "var 'deps' must be a list of non-empty strings"
            ):
                check_target(repo, module_root)

    def test_string_list_module_schema_rejects_unknown_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(
                module_root / "bad" / "manifest.yml",
                """
vars:
  items:
    type: int-list
""",
            )

            with self.assertRaisesRegex(
                ModuleError, "only 'string', 'boolean', or 'string-list' is supported"
            ):
                load_module("bad", module_root)

    def test_string_list_module_default_rejects_non_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad_default")
            write(
                module_root / "bad_default" / "manifest.yml",
                """
vars:
  items:
    type: string-list
    default: "not-a-list"
""",
            )

            with self.assertRaisesRegex(
                ModuleError, "default: must be a list of non-empty strings"
            ):
                load_module("bad_default", module_root)

    def test_python_module_writes_python_version_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            write(
                repo / "apg.yml",
                """
modules:
  - name: python
    vars:
      python_version: "3.13"
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )

            sync_target(repo, self.modules_root)

            self.assertEqual(
                (repo / ".python-version").read_text(encoding="utf-8"), "3.13\n"
            )
            self.assertFalse((repo / "pyproject.toml").exists())

    def test_precommit_module_renders_with_explicit_toggles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: precommit
    vars:
      shell: true
      uv: true
      ty: true
      ruff: true
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )
            sync_target(repo, self.modules_root)
            rendered = (repo / ".pre-commit-config.yaml").read_text(encoding="utf-8")
            self.assertIn("shellcheck", rendered)
            self.assertIn("shfmt", rendered)
            self.assertIn("uv-lock", rendered)
            self.assertIn("ty-types", rendered)
            self.assertIn("ruff", rendered)

    def test_precommit_module_renders_without_shell_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: precommit
    vars:
      shell: false
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )
            sync_target(repo, self.modules_root)
            rendered = (repo / ".pre-commit-config.yaml").read_text(encoding="utf-8")
            self.assertNotIn("shellcheck", rendered)
            self.assertNotIn("shfmt", rendered)
            self.assertIn("uv-lock", rendered)

    def test_precommit_module_renders_without_static_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: precommit
    vars:
      static: false
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )
            sync_target(repo, self.modules_root)
            rendered = (repo / ".pre-commit-config.yaml").read_text(encoding="utf-8")
            self.assertNotIn("static-tests", rendered)

    def test_precommit_module_renders_static_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "apg.yml",
                """
modules:
  - name: precommit
    vars: {}
project:
  name: app
realms: []
environments: []
services: []
""".lstrip(),
            )
            sync_target(repo, self.modules_root)
            rendered = (repo / ".pre-commit-config.yaml").read_text(encoding="utf-8")
            self.assertIn("static-tests", rendered)

    def test_check_reports_and_sync_repairs_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest_with_python())
            write(repo / ".editorconfig", "drift\n")

            findings = check_target(repo, self.modules_root)
            self.assertIn(
                ("changed", ".editorconfig"),
                {(finding.kind, finding.path) for finding in findings},
            )

            sync_target(repo, self.modules_root)

            self.assertEqual(check_target(repo, self.modules_root), ())
            self.assertEqual(
                (repo / "Taskfile.yml").read_text(encoding="utf-8"),
                (self.modules_root / "taskfile" / "files" / "Taskfile.yml").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                (repo / ".python-version").read_text(encoding="utf-8").strip(), "3.13"
            )

    def test_sync_preserves_executable_mode_from_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            module_root = Path(tmp) / "modules"
            make_module(module_root, "exec")
            write(
                module_root / "exec" / "manifest.yml",
                "templates:\n  - script.j2:bin/script\n",
            )
            write(
                module_root / "exec" / "templates" / "script.j2", "#!/bin/sh\nexit 0\n"
            )
            (module_root / "exec" / "templates" / "script.j2").chmod(0o755)
            write(repo / "apg.yml", manifest("exec"))

            sync_target(repo, module_root)

            self.assertTrue((repo / "bin/script").stat().st_mode & stat.S_IXUSR)

    def test_unsafe_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "files" / "source", "x\n")
            write(
                module_root / "bad" / "manifest.yml", "files:\n  - source:../escape\n"
            )
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(
                ModuleError, "unsafe destination-relative path"
            ):
                check_target(repo, module_root)

    def test_symlink_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "safe")
            write(module_root / "safe" / "files" / "source", "x\n")
            write(module_root / "safe" / "manifest.yml", "files:\n  - source:managed\n")
            write(repo / "apg.yml", manifest("safe"))
            os.symlink(Path(tmp) / "outside", repo / "managed")

            with self.assertRaisesRegex(
                ModuleError, "refusing to manage symlink destination"
            ):
                check_target(repo, module_root)

    def test_obsolete_target_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "manifest.yml", manifest("repository"))

            with self.assertRaisesRegex(
                ManifestError, "rename root manifest.yml to apg.yml"
            ):
                check_target(repo, self.modules_root)

    def test_obsolete_target_config_is_rejected_even_when_apg_yml_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))
            write(repo / "manifest.yml", manifest("repository"))

            with self.assertRaisesRegex(
                ManifestError, "rename root manifest.yml to apg.yml"
            ):
                check_target(repo, self.modules_root)

    def test_duplicate_managed_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(module_root / "one" / "files" / "one", "one\n")
            write(module_root / "two" / "files" / "two", "two\n")
            write(module_root / "one" / "manifest.yml", "files:\n  - one:managed\n")
            write(module_root / "two" / "manifest.yml", "files:\n  - two:managed\n")
            write(repo / "apg.yml", manifest("one", "two"))

            with self.assertRaisesRegex(
                ModuleError, "duplicate managed destination: managed"
            ):
                check_target(repo, module_root)

    def test_normalized_duplicate_managed_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "files" / "one", "one\n")
            write(module_root / "bad" / "files" / "two", "two\n")
            write(
                module_root / "bad" / "manifest.yml",
                "files:\n  - one:x\n  - two:./x\n",
            )
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(
                ModuleError, "duplicate managed destination: x"
            ):
                check_target(repo, module_root)

    def test_unknown_module_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "manifest.yml", "filez: []\n")

            with self.assertRaisesRegex(ModuleError, "unknown keys: filez"):
                load_module("bad", module_root)

    def test_template_sandbox_rejects_arbitrary_code_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(
                module_root / "bad" / "manifest.yml",
                "templates:\n  - exploit.j2:result\n",
            )
            write(
                module_root / "bad" / "templates" / "exploit.j2",
                "{{ cycler.__init__.__globals__.os.getcwd() }}",
            )
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "cannot render template"):
                check_target(repo, module_root)

    def test_template_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(
                module_root / "bad" / "manifest.yml",
                "templates:\n  - invalid.j2:result\n",
            )
            write(module_root / "bad" / "templates" / "invalid.j2", "{% invalid %}")
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "cannot render template"):
                check_target(repo, module_root)

    def test_linters_are_deduplicated_and_fail_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(
                module_root / "one" / "manifest.yml",
                "linters:\n  - ok.py\n  - fail.py\n",
            )
            write(module_root / "two" / "manifest.yml", "linters:\n  - ok.py\n")
            write(
                module_root / "one" / "linters" / "ok.py",
                "import sys\nraise SystemExit(0)\n",
            )
            write(
                module_root / "one" / "linters" / "fail.py",
                "import sys\nraise SystemExit(1)\n",
            )
            write(
                module_root / "two" / "linters" / "ok.py",
                "import sys\nraise SystemExit(0)\n",
            )
            write(repo / "apg.yml", manifest("one", "two"))

            findings = run_linters(repo, module_root)

            self.assertEqual(
                [(finding.kind, finding.path) for finding in findings],
                [("linter", "fail.py: exit 1")],
            )

    def test_verify_returns_drift_and_linter_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "files" / "source", "x\n")
            write(
                module_root / "bad" / "manifest.yml",
                "files:\n  - source:managed\nlinters:\n  - fail.py\n",
            )
            write(module_root / "bad" / "linters" / "fail.py", "raise SystemExit(1)\n")
            write(repo / "apg.yml", manifest("bad"))

            findings = verify_target(repo, module_root)

            self.assertIn(
                ("missing", "managed"),
                {(finding.kind, finding.path) for finding in findings},
            )
            self.assertIn(
                ("linter", "fail.py: exit 1"),
                {(finding.kind, finding.path) for finding in findings},
            )

    def test_linter_timeout_is_reported_as_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "slow")
            write(module_root / "slow" / "manifest.yml", "linters:\n  - slow.py\n")
            write(
                module_root / "slow" / "linters" / "slow.py",
                "import time\ntime.sleep(1)\n",
            )
            write(repo / "apg.yml", manifest("slow"))

            findings = run_linters(repo, module_root, timeout=0.01)

            self.assertEqual(
                [(finding.kind, finding.path) for finding in findings],
                [("linter-timeout", "slow.py: 0.01s")],
            )

    def test_unsafe_linter_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "manifest.yml", "linters:\n  - ../escape.py\n")
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "unsafe linter path"):
                run_linters(repo, module_root)

    def test_missing_linter_script_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "manifest.yml", "linters:\n  - missing.py\n")
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "missing linter script"):
                run_linters(repo, module_root)

    def test_cli_check_exits_nonzero_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "apg.module_sync",
                    "check",
                    "--modules-root",
                    str(self.modules_root),
                    str(repo),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("missing: .editorconfig", result.stdout)

    def test_cli_rejects_removed_profile_argument_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "apg.module_sync", "check", "repository", tmp],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)

    def test_cli_requires_modules_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "apg.module_sync", "check", tmp],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--modules-root", result.stderr)

    def test_cli_rejects_missing_modules_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "apg.module_sync",
                    "check",
                    "--modules-root",
                    str(Path(tmp) / "missing"),
                    tmp,
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("modules root must be an existing directory", result.stderr)

    def test_sync_writes_managed_index_with_sorted_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest_with_python())
            sync_target(repo, self.modules_root)

            managed = (repo / MANAGED_MANIFEST_FILENAME).read_text(encoding="utf-8")
            entries = json.loads(managed)["files"]
            self.assertEqual(list(entries), sorted(entries))
            self.assertEqual(MANAGED_MANIFEST_FILENAME, "apg-manifest.json")
            self.assertIn(".editorconfig", entries)
            self.assertIn(".python-version", entries)
            self.assertIn("Taskfile.yml", entries)
            self.assertTrue(managed.endswith("\n"))
            self.assertFalse((repo / ".managed").exists())

    def test_obsolete_managed_index_is_not_generated_or_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))
            write(repo / ".managed", "obsolete listing\n")

            sync_target(repo, self.modules_root)

            self.assertEqual(
                (repo / ".managed").read_text(encoding="utf-8"), "obsolete listing\n"
            )
            self.assertTrue((repo / "apg-manifest.json").is_file())
            self.assertNotIn(
                ("changed", ".managed"),
                {
                    (finding.kind, finding.path)
                    for finding in check_target(repo, self.modules_root)
                },
            )

    def test_managed_index_lists_every_composed_destination(self) -> None:
        from apg.manifest import validate_manifest

        target_manifest = validate_manifest(
            {
                "modules": [
                    {"name": "repository", "vars": {}},
                    {"name": "taskfile", "vars": {}},
                    {
                        "name": "python",
                        "vars": {
                            "python_version": "3.13",
                        },
                    },
                ],
                "project": {"name": "app"},
                "realms": [],
                "environments": [],
                "services": [],
            }
        )
        desired, _ = compose_target_state(target_manifest, self.modules_root)
        index_entry = next(
            item for item in desired if item.destination == MANAGED_MANIFEST_FILENAME
        )
        listed = json.loads(index_entry.content)["files"]
        expected = sorted(
            item.destination
            for item in desired
            if item.destination != MANAGED_MANIFEST_FILENAME
        )
        self.assertEqual(list(listed), expected)

    def test_check_reports_drift_on_managed_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))
            write(repo / MANAGED_MANIFEST_FILENAME, "stale listing\n")

            findings = check_target(repo, self.modules_root)
            self.assertIn(
                ("changed", MANAGED_MANIFEST_FILENAME),
                {(finding.kind, finding.path) for finding in findings},
            )

    def test_sync_repairs_managed_index_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))
            write(repo / MANAGED_MANIFEST_FILENAME, "stale listing\n")

            sync_target(repo, self.modules_root)

            self.assertEqual(check_target(repo, self.modules_root), ())
            managed = (repo / MANAGED_MANIFEST_FILENAME).read_text(encoding="utf-8")
            self.assertIn(".editorconfig", json.loads(managed)["files"])

    def test_sync_writes_versionless_managed_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))

            sync_target(repo, self.modules_root)

            managed = json.loads(
                (repo / MANAGED_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(set(managed), {"files"})
            self.assertEqual(check_target(repo, self.modules_root), ())

    def test_versioned_managed_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "apg.yml", manifest("repository"))
            write(
                repo / MANAGED_MANIFEST_FILENAME,
                json.dumps({"version": 1, "files": {}}),
            )

            with self.assertRaisesRegex(ModuleError, "unsupported managed index"):
                check_target(repo, self.modules_root)

    def test_module_cannot_manage_reserved_index_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "files" / "source", "x\n")
            write(
                module_root / "bad" / "manifest.yml",
                f"files:\n  - source:{MANAGED_MANIFEST_FILENAME}\n",
            )
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "reserved by APG"):
                check_target(repo, module_root)

    def test_normalized_reserved_index_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "bad")
            write(module_root / "bad" / "files" / "source", "x\n")
            write(
                module_root / "bad" / "manifest.yml",
                "files:\n  - source:./apg-manifest.json\n",
            )
            write(repo / "apg.yml", manifest("bad"))

            with self.assertRaisesRegex(ModuleError, "reserved by APG"):
                check_target(repo, module_root)

    def test_sync_removes_unmodified_former_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(module_root / "one" / "files" / "source", "managed\n")
            write(module_root / "one" / "manifest.yml", "files:\n  - source:old.txt\n")
            write(repo / "apg.yml", manifest("one"))
            sync_target(repo, module_root)
            write(repo / "apg.yml", manifest("two"))

            self.assertIn(
                ("stale", "old.txt"),
                {
                    (finding.kind, finding.path)
                    for finding in check_target(repo, module_root)
                },
            )
            findings = sync_target(repo, module_root)

            self.assertFalse((repo / "old.txt").exists())
            self.assertIn(
                ("removed", "old.txt"),
                {(finding.kind, finding.path) for finding in findings},
            )

    def test_sync_refuses_modified_former_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(module_root / "one" / "files" / "source", "managed\n")
            write(module_root / "one" / "manifest.yml", "files:\n  - source:old.txt\n")
            write(repo / "apg.yml", manifest("one"))
            sync_target(repo, module_root)
            write(repo / "old.txt", "user change\n")
            write(repo / "apg.yml", manifest("two"))

            with self.assertRaisesRegex(ModuleError, "stale-modified: old.txt"):
                sync_target(repo, module_root)

            self.assertEqual(
                (repo / "old.txt").read_text(encoding="utf-8"), "user change\n"
            )

    def test_sync_refuses_symlink_at_former_managed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(module_root / "one" / "files" / "source", "managed\n")
            write(module_root / "one" / "manifest.yml", "files:\n  - source:old.txt\n")
            write(repo / "apg.yml", manifest("one"))
            sync_target(repo, module_root)
            (repo / "old.txt").unlink()
            os.symlink(Path(tmp) / "outside", repo / "old.txt")
            write(repo / "apg.yml", manifest("two"))

            self.assertIn(
                ("stale-unsafe", "old.txt"),
                {
                    (finding.kind, finding.path)
                    for finding in check_target(repo, module_root)
                },
            )
            with self.assertRaisesRegex(ModuleError, "stale-unsafe: old.txt"):
                sync_target(repo, module_root)

    def test_sync_refuses_mode_changed_former_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "one")
            make_module(module_root, "two")
            write(module_root / "one" / "files" / "source", "managed\n")
            write(module_root / "one" / "manifest.yml", "files:\n  - source:old.txt\n")
            write(repo / "apg.yml", manifest("one"))
            sync_target(repo, module_root)
            (repo / "old.txt").chmod(0o755)
            write(repo / "apg.yml", manifest("two"))

            with self.assertRaisesRegex(ModuleError, "stale-modified: old.txt"):
                sync_target(repo, module_root)

    def test_legacy_index_never_authorizes_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            module_root = Path(tmp) / "modules"
            make_module(module_root, "empty")
            write(repo / "apg.yml", manifest("empty"))
            write(repo / "apg-manifest.json", "apg-manifest.json\nold.txt\n")
            write(repo / "old.txt", "unknown original content\n")

            with self.assertRaisesRegex(ModuleError, "stale-modified: old.txt"):
                sync_target(repo, module_root)


if __name__ == "__main__":
    unittest.main()
