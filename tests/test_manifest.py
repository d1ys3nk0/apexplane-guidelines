from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apg.manifest import ManifestError, load_manifest, validate_manifest


VALID_MANIFEST = {
    "modules": [
        {"name": "repository", "vars": {}},
        {"name": "taskfile", "vars": {}},
        {"name": "python", "vars": {"python_version": "3.13"}},
        {"name": "ci", "vars": {}},
        {"name": "python-ci", "vars": {}},
    ],
    "project": {"name": "app", "world": "example", "unit": "core"},
    "realms": [{"name": "prd"}, {"name": "stg"}],
    "environments": [
        {"name": "prd-hq", "realm": "prd"},
        {"name": "stg-uat", "realm": "stg"},
    ],
    "services": [{"name": "api", "kind": "web"}, {"name": "worker", "kind": "worker"}],
}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ManifestTest(unittest.TestCase):
    def test_valid_manifest_loads(self) -> None:
        manifest = validate_manifest(VALID_MANIFEST)

        self.assertEqual(
            [module.name for module in manifest.modules],
            ["repository", "taskfile", "python", "ci", "python-ci"],
        )
        self.assertEqual(dict(manifest.modules[2].vars), {"python_version": "3.13"})
        self.assertEqual(manifest.project.name, "app")
        self.assertEqual(
            [service.kind for service in manifest.services], ["web", "worker"]
        )

    def test_rejects_obsolete_profile_field_with_migration_guidance(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw.pop("modules")
        raw["profile"] = "python-runtime"

        with self.assertRaisesRegex(ManifestError, r"obsolete field; use modules"):
            validate_manifest(raw)

    def test_rejects_obsolete_profiles_field_with_migration_guidance(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw.pop("modules")
        raw["profiles"] = ["repository", "python"]

        with self.assertRaisesRegex(ManifestError, r"obsolete field; use modules"):
            validate_manifest(raw)

    def test_rejects_duplicate_modules(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {"name": "repository", "vars": {}},
            {"name": "python", "vars": {}},
            {"name": "repository", "vars": {}},
        ]

        with self.assertRaisesRegex(
            ManifestError, "duplicate module name 'repository'"
        ):
            validate_manifest(raw)

    def test_rejects_empty_modules(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = []

        with self.assertRaisesRegex(ManifestError, "must contain at least one module"):
            validate_manifest(raw)

    def test_rejects_module_item_without_name(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"vars": {}}]

        with self.assertRaisesRegex(ManifestError, "missing required keys?: name"):
            validate_manifest(raw)

    def test_rejects_unknown_module_item_keys(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "repository", "vars": {}, "extra": "x"}]

        with self.assertRaisesRegex(ManifestError, "unknown keys are not supported"):
            validate_manifest(raw)

    def test_rejects_missing_vars(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "repository"}]

        with self.assertRaisesRegex(ManifestError, "missing required keys?: vars"):
            validate_manifest(raw)

    def test_rejects_non_mapping_vars(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "repository", "vars": "bad"}]

        with self.assertRaisesRegex(ManifestError, "vars: must be a mapping"):
            validate_manifest(raw)

    def test_rejects_empty_module_var_value(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "repository", "vars": {"key": ""}}]

        with self.assertRaisesRegex(
            ManifestError, "vars.key: must be a non-empty string"
        ):
            validate_manifest(raw)

    def test_accepts_list_module_var_value(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {
                "name": "python",
                "vars": {
                    "python_version": "3.13",
                    "deps": ["alembic>=1.0", "click>=8.0"],
                },
            }
        ]

        manifest = validate_manifest(raw)
        self.assertEqual(
            dict(manifest.modules[0].vars),
            {"python_version": "3.13", "deps": ["alembic>=1.0", "click>=8.0"]},
        )

    def test_rejects_list_module_var_with_non_string_item(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {
                "name": "python",
                "vars": {
                    "python_version": "3.13",
                    "deps": ["alembic>=1.0", 42],
                },
            }
        ]

        with self.assertRaisesRegex(
            ManifestError, "vars.deps: must be a list of non-empty strings"
        ):
            validate_manifest(raw)

    def test_rejects_list_module_var_with_empty_string_item(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {
                "name": "python",
                "vars": {
                    "python_version": "3.13",
                    "deps": ["alembic>=1.0", ""],
                },
            }
        ]

        with self.assertRaisesRegex(
            ManifestError, "vars.deps: must be a list of non-empty strings"
        ):
            validate_manifest(raw)

    def test_accepts_empty_list_module_var_value(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {
                "name": "python",
                "vars": {
                    "python_version": "3.13",
                    "extras": [],
                },
            }
        ]

        manifest = validate_manifest(raw)
        self.assertEqual(
            dict(manifest.modules[0].vars), {"python_version": "3.13", "extras": []}
        )

    def test_rejects_non_string_non_list_non_bool_var(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "repository", "vars": {"key": 42}}]

        with self.assertRaisesRegex(
            ManifestError, "non-empty string, a list of non-empty strings, or a boolean"
        ):
            validate_manifest(raw)

    def test_rejects_obsolete_project_python_version(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["project"] = {"name": "app", "python_version": "3.13"}

        with self.assertRaisesRegex(
            ManifestError, "move python_version into the python module vars"
        ):
            validate_manifest(raw)

    def test_rejects_old_generator_keys(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["dotenv"] = {"contract": []}

        with self.assertRaisesRegex(ManifestError, "old generator keys"):
            validate_manifest(raw)

    def test_rejects_unknown_keys(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["owner"] = "team"

        with self.assertRaisesRegex(ManifestError, "unknown keys"):
            validate_manifest(raw)

    def test_accepts_taskfile_includes_for_the_taskfile_module(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["taskfiles"] = [
            {
                "name": "ci",
                "file": "ci.yml",
                "aliases": ["pipeline"],
                "vars": {"CI_URL": "https://ci.example.test/example/project"},
            }
        ]

        manifest = validate_manifest(raw)

        self.assertEqual(manifest.to_context()["taskfiles"], raw["taskfiles"])

    def test_validates_ansible_topology_and_tunnel_contract(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [
            {"name": "repository", "vars": {}},
            {"name": "taskfile", "vars": {}},
            {"name": "ansible", "vars": {}},
        ]
        raw["ansible"] = {
            "python_version": "3.13",
            "groups": [{"realm": "prd", "platform": "ycl", "clusters": ["app", "dbs"]}],
            "tunnels": [
                {
                    "realm": "prd",
                    "platform": "ycl",
                    "cluster": "app",
                    "service": "traefik",
                }
            ],
        }

        manifest = validate_manifest(raw)
        assert manifest.ansible is not None
        self.assertEqual(manifest.ansible.groups[0].clusters, ("app", "dbs"))
        self.assertEqual(manifest.ansible.tunnels[0].service, "traefik")

    def test_rejects_invalid_ansible_tunnel_service_and_cluster(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["modules"] = [{"name": "ansible", "vars": {}}]
        raw["ansible"] = {
            "python_version": "3.13",
            "groups": [{"realm": "prd", "platform": "ycl", "clusters": ["app"]}],
            "tunnels": [
                {
                    "realm": "prd",
                    "platform": "ycl",
                    "cluster": "missing",
                    "service": "unknown",
                }
            ],
        }
        with self.assertRaisesRegex(ManifestError, "not a member"):
            validate_manifest(raw)
        raw["ansible"]["tunnels"][0]["cluster"] = "app"
        with self.assertRaisesRegex(ManifestError, "unsupported service"):
            validate_manifest(raw)

    def test_rejects_raw_block_fields(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["project"] = {"name": "app", "root_raw": "x"}

        with self.assertRaisesRegex(ManifestError, "raw block fields"):
            validate_manifest(raw)

    def test_rejects_duplicate_names(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["services"] = [
            {"name": "api", "kind": "web"},
            {"name": "api", "kind": "worker"},
        ]

        with self.assertRaisesRegex(ManifestError, "duplicate name 'api'"):
            validate_manifest(raw)

    def test_rejects_unknown_environment_realm(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["environments"] = [{"name": "qa", "realm": "qa"}]

        with self.assertRaisesRegex(ManifestError, "unknown realm 'qa'"):
            validate_manifest(raw)

    def test_rejects_invalid_service_kind(self) -> None:
        raw = dict(VALID_MANIFEST)
        raw["services"] = [{"name": "api", "kind": "cron"}]

        with self.assertRaisesRegex(ManifestError, "must be one of"):
            validate_manifest(raw)

    def test_rejects_obsolete_root_manifest_yml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(
                repo / "manifest.yml",
                """modules:
  - name: repository
    vars: {}
project:
  name: app
realms: []
environments: []
services: []
""",
            )

            with self.assertRaisesRegex(
                ManifestError, "rename root manifest.yml to apg.yml"
            ):
                load_manifest(repo)


if __name__ == "__main__":
    unittest.main()
