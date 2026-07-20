"""Validation for APG target manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_FILENAME = "apg.yml"
OBSOLETE_CONFIG_FILENAME = "manifest.yml"
ROOT_KEYS = frozenset(
    {"modules", "project", "realms", "environments", "services", "ansible", "taskfiles"}
)
OLD_GENERATOR_KEYS = frozenset({"dotenv", "taskfile", "runtime", "ci"})
SERVICE_KINDS = frozenset({"web", "worker", "bot", "job"})
TUNNEL_SERVICE_CONTRACTS = {
    "dockhand": {"local_port": 3000, "remote_port": 3000, "url_path": "/"},
    "haproxy": {
        "local_port": 8404,
        "remote_port": 8404,
        "url_path": "/_stats;norefresh",
    },
    "traefik": {"local_port": 1080, "remote_port": 1080, "url_path": "/dashboard/"},
    "wg-easy": {"local_port": 51821, "remote_port": 51821, "url_path": "/"},
}
RAW_BLOCK_KEYS = frozenset(
    {
        "raw",
        "build_jobs_raw",
        "build_raw",
        "deploy_stack_env_raw",
        "deploy_stack_raw",
        "dockerfile_app",
        "dockerfile_runtime",
        "infisical_raw",
        "root_raw",
        "startup_script",
        "verify_jobs_raw",
    }
)


class ManifestError(RuntimeError):
    """Raised when a target APG manifest is invalid."""


@dataclass(frozen=True)
class Project:
    name: str
    world: str | None = None
    unit: str | None = None

    def to_context(self) -> dict[str, str]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass(frozen=True)
class Realm:
    name: str

    def to_context(self) -> dict[str, str]:
        return {"name": self.name}


@dataclass(frozen=True)
class Environment:
    name: str
    realm: str

    def to_context(self) -> dict[str, str]:
        return {"name": self.name, "realm": self.realm}


@dataclass(frozen=True)
class Service:
    name: str
    kind: str

    def to_context(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind}


@dataclass(frozen=True)
class AnsibleGroup:
    realm: str
    platform: str
    clusters: tuple[str, ...]

    def to_context(self) -> dict[str, object]:
        return {
            "realm": self.realm,
            "platform": self.platform,
            "clusters": list(self.clusters),
        }


@dataclass(frozen=True)
class AnsibleTunnel:
    realm: str
    platform: str
    cluster: str
    service: str

    def to_context(self) -> dict[str, str]:
        return {
            "realm": self.realm,
            "platform": self.platform,
            "cluster": self.cluster,
            "service": self.service,
        }


@dataclass(frozen=True)
class Ansible:
    python_version: str
    groups: tuple[AnsibleGroup, ...]
    tunnels: tuple[AnsibleTunnel, ...]

    def to_context(self) -> dict[str, object]:
        return {
            "python_version": self.python_version,
            "groups": [group.to_context() for group in self.groups],
            "tunnels": [tunnel.to_context() for tunnel in self.tunnels],
        }


@dataclass(frozen=True)
class Taskfile:
    name: str
    file: str
    aliases: tuple[str, ...]
    vars: tuple[tuple[str, str], ...]

    def to_context(self) -> dict[str, object]:
        return {
            "name": self.name,
            "file": self.file,
            "aliases": list(self.aliases),
            "vars": dict(self.vars),
        }


@dataclass(frozen=True)
class ModuleRef:
    name: str
    vars: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class Manifest:
    modules: tuple[ModuleRef, ...]
    project: Project
    realms: tuple[Realm, ...]
    environments: tuple[Environment, ...]
    services: tuple[Service, ...]
    ansible: Ansible | None = None
    taskfiles: tuple[Taskfile, ...] = ()

    def to_context(self) -> dict[str, Any]:
        return {
            "modules": [
                {"name": module.name, "vars": dict(module.vars)}
                for module in self.modules
            ],
            "project": self.project.to_context(),
            "realms": [realm.to_context() for realm in self.realms],
            "environments": [
                environment.to_context() for environment in self.environments
            ],
            "services": [service.to_context() for service in self.services],
            "ansible": self.ansible.to_context() if self.ansible else None,
            "taskfiles": [taskfile.to_context() for taskfile in self.taskfiles],
        }


def _load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ManifestError(f"missing required file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc


def _field_path(source: str, field: str) -> str:
    return f"{source}.{field}"


def _expect_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{path}: must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise ManifestError(f"{path}: keys must be strings")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{path}: must be a list")
    return value


def _required_string(value: Mapping[str, Any], key: str, path: str) -> str:
    if key not in value:
        raise ManifestError(f"{path}: missing required key {key!r}")
    item = value[key]
    if not isinstance(item, str) or item.strip() == "":
        raise ManifestError(f"{_field_path(path, key)}: must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, Any], key: str, path: str) -> str | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, str) or item.strip() == "":
        raise ManifestError(f"{_field_path(path, key)}: must be a non-empty string")
    return item


def _reject_raw_block_fields(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            key_normalized = key_text.lower().replace("-", "_")
            if key_normalized in RAW_BLOCK_KEYS or key_normalized.endswith("_raw"):
                raise ManifestError(
                    f"{_field_path(path, key_text)}: raw block fields are not supported"
                )
            _reject_raw_block_fields(item, _field_path(path, key_text))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_raw_block_fields(item, f"{path}[{index}]")


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: frozenset[str], path: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(
            f"{path}: unknown keys are not supported: {', '.join(unknown)}"
        )


def _require_keys(
    value: Mapping[str, Any], required: frozenset[str], path: str
) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ManifestError(f"{path}: missing required keys: {', '.join(missing)}")


def _check_duplicate_names(names: list[str], path: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ManifestError(f"{path}: duplicate name {name!r}")
        seen.add(name)


def _parse_project(value: Any, source: str) -> Project:
    path = _field_path(source, "project")
    raw = _expect_mapping(value, path)
    if "python_version" in raw:
        raise ManifestError(
            f"{path}.python_version: obsolete field; move python_version into the python module vars"
        )
    allowed = frozenset({"name", "world", "unit"})
    _reject_unknown_keys(raw, allowed, path)
    return Project(
        name=_required_string(raw, "name", path),
        world=_optional_string(raw, "world", path),
        unit=_optional_string(raw, "unit", path),
    )


def _parse_module_vars(value: Any, path: str) -> tuple[tuple[str, object], ...]:
    raw = _expect_mapping(value, path)
    vars_list: list[tuple[str, object]] = []
    for name, item in raw.items():
        if not isinstance(name, str):
            raise ManifestError(f"{path}: keys must be strings")
        if isinstance(item, bool):
            vars_list.append((name, item))
            continue
        if isinstance(item, str):
            if item.strip() == "":
                raise ManifestError(
                    f"{_field_path(path, name)}: must be a non-empty string"
                )
            vars_list.append((name, item))
            continue
        if isinstance(item, list):
            if not all(
                isinstance(entry, str) and entry.strip() != "" for entry in item
            ):
                raise ManifestError(
                    f"{_field_path(path, name)}: must be a list of non-empty strings"
                )
            vars_list.append((name, item))
            continue
        raise ManifestError(
            f"{_field_path(path, name)}: must be a non-empty string, a list of non-empty strings, or a boolean"
        )
    return tuple(vars_list)


def _parse_modules(value: Any, source: str) -> tuple[ModuleRef, ...]:
    path = _field_path(source, "modules")
    modules: list[ModuleRef] = []
    seen: set[str] = set()
    for index, item in enumerate(_expect_list(value, path)):
        item_path = f"{path}[{index}]"
        raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(raw, frozenset({"name", "vars"}), item_path)
        _require_keys(raw, frozenset({"name", "vars"}), item_path)
        name = _required_string(raw, "name", item_path)
        if name in seen:
            raise ManifestError(f"{path}: duplicate module name {name!r}")
        seen.add(name)
        vars_raw = raw["vars"]
        vars_path = _field_path(item_path, "vars")
        if not isinstance(vars_raw, Mapping):
            raise ManifestError(f"{vars_path}: must be a mapping")
        module_vars = _parse_module_vars(vars_raw, vars_path)
        modules.append(ModuleRef(name=name, vars=module_vars))
    if not modules:
        raise ManifestError(f"{path}: must contain at least one module")
    return tuple(modules)


def _parse_realms(value: Any, source: str) -> tuple[Realm, ...]:
    path = _field_path(source, "realms")
    realms: list[Realm] = []
    for index, item in enumerate(_expect_list(value, path)):
        item_path = f"{path}[{index}]"
        raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(raw, frozenset({"name"}), item_path)
        realms.append(Realm(name=_required_string(raw, "name", item_path)))
    _check_duplicate_names([realm.name for realm in realms], path)
    return tuple(realms)


def _parse_environments(
    value: Any, realm_names: set[str], source: str
) -> tuple[Environment, ...]:
    path = _field_path(source, "environments")
    environments: list[Environment] = []
    for index, item in enumerate(_expect_list(value, path)):
        item_path = f"{path}[{index}]"
        raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(raw, frozenset({"name", "realm"}), item_path)
        environment = Environment(
            name=_required_string(raw, "name", item_path),
            realm=_required_string(raw, "realm", item_path),
        )
        if environment.realm not in realm_names:
            raise ManifestError(
                f"{item_path}.realm: unknown realm {environment.realm!r}"
            )
        environments.append(environment)
    _check_duplicate_names([environment.name for environment in environments], path)
    return tuple(environments)


def _parse_services(value: Any, source: str) -> tuple[Service, ...]:
    path = _field_path(source, "services")
    services: list[Service] = []
    for index, item in enumerate(_expect_list(value, path)):
        item_path = f"{path}[{index}]"
        raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(raw, frozenset({"name", "kind"}), item_path)
        service = Service(
            name=_required_string(raw, "name", item_path),
            kind=_required_string(raw, "kind", item_path),
        )
        if service.kind not in SERVICE_KINDS:
            raise ManifestError(
                f"{item_path}.kind: must be one of {', '.join(sorted(SERVICE_KINDS))}"
            )
        services.append(service)
    _check_duplicate_names([service.name for service in services], path)
    return tuple(services)


def _parse_ansible(value: Any, realm_names: set[str], source: str) -> Ansible:
    path = _field_path(source, "ansible")
    raw = _expect_mapping(value, path)
    _reject_unknown_keys(raw, frozenset({"python_version", "groups", "tunnels"}), path)
    _require_keys(raw, frozenset({"python_version", "groups", "tunnels"}), path)
    python_version = _required_string(raw, "python_version", path)
    groups: list[AnsibleGroup] = []
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(_expect_list(raw["groups"], f"{path}.groups")):
        item_path = f"{path}.groups[{index}]"
        group_raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(
            group_raw, frozenset({"realm", "platform", "clusters"}), item_path
        )
        _require_keys(
            group_raw, frozenset({"realm", "platform", "clusters"}), item_path
        )
        realm = _required_string(group_raw, "realm", item_path)
        platform = _required_string(group_raw, "platform", item_path)
        clusters = _expect_list(group_raw["clusters"], f"{item_path}.clusters")
        if not all(
            isinstance(cluster, str) and cluster.strip() for cluster in clusters
        ):
            raise ManifestError(
                f"{item_path}.clusters: must be a list of non-empty strings"
            )
        _check_duplicate_names(clusters, f"{item_path}.clusters")
        if realm not in realm_names:
            raise ManifestError(f"{item_path}.realm: unknown realm {realm!r}")
        identity = (realm, platform)
        if identity in identities:
            raise ManifestError(f"{path}.groups: duplicate identity {realm}:{platform}")
        identities.add(identity)
        groups.append(
            AnsibleGroup(realm=realm, platform=platform, clusters=tuple(clusters))
        )
    tunnels: list[AnsibleTunnel] = []
    tunnel_identities: set[tuple[str, str, str, str]] = set()
    group_clusters = {
        (group.realm, group.platform): set(group.clusters) for group in groups
    }
    for index, item in enumerate(_expect_list(raw["tunnels"], f"{path}.tunnels")):
        item_path = f"{path}.tunnels[{index}]"
        tunnel_raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(
            tunnel_raw,
            frozenset({"realm", "platform", "cluster", "service"}),
            item_path,
        )
        _require_keys(
            tunnel_raw,
            frozenset({"realm", "platform", "cluster", "service"}),
            item_path,
        )
        tunnel = AnsibleTunnel(
            **{
                key: _required_string(tunnel_raw, key, item_path)
                for key in ("realm", "platform", "cluster", "service")
            }
        )
        identity = (tunnel.realm, tunnel.platform, tunnel.cluster, tunnel.service)
        if identity in tunnel_identities:
            raise ManifestError(
                f"{path}.tunnels: duplicate identity {':'.join(identity)}"
            )
        tunnel_identities.add(identity)
        if tunnel.realm not in realm_names:
            raise ManifestError(f"{item_path}.realm: unknown realm {tunnel.realm!r}")
        if tunnel.cluster not in group_clusters.get(
            (tunnel.realm, tunnel.platform), set()
        ):
            raise ManifestError(
                f"{item_path}.cluster: not a member of group {tunnel.realm}:{tunnel.platform}"
            )
        if tunnel.service not in TUNNEL_SERVICE_CONTRACTS:
            raise ManifestError(
                f"{item_path}.service: unsupported service {tunnel.service!r}"
            )
        tunnels.append(tunnel)
    return Ansible(
        python_version=python_version, groups=tuple(groups), tunnels=tuple(tunnels)
    )


def _parse_taskfiles(value: Any, source: str) -> tuple[Taskfile, ...]:
    path = _field_path(source, "taskfiles")
    taskfiles: list[Taskfile] = []
    for index, item in enumerate(_expect_list(value, path)):
        item_path = f"{path}[{index}]"
        raw = _expect_mapping(item, item_path)
        _reject_unknown_keys(
            raw, frozenset({"name", "file", "aliases", "vars"}), item_path
        )
        _require_keys(raw, frozenset({"name", "file"}), item_path)
        aliases_raw = raw.get("aliases", [])
        aliases = _expect_list(aliases_raw, f"{item_path}.aliases")
        if not all(isinstance(alias, str) and alias.strip() for alias in aliases):
            raise ManifestError(
                f"{item_path}.aliases: must be a list of non-empty strings"
            )
        vars_raw = _expect_mapping(raw.get("vars", {}), f"{item_path}.vars")
        vars_items: list[tuple[str, str]] = []
        for name, variable in vars_raw.items():
            if (
                not isinstance(name, str)
                or not isinstance(variable, str)
                or not variable.strip()
            ):
                raise ManifestError(
                    f"{item_path}.vars: must map strings to non-empty strings"
                )
            vars_items.append((name, variable))
        taskfiles.append(
            Taskfile(
                name=_required_string(raw, "name", item_path),
                file=_required_string(raw, "file", item_path),
                aliases=tuple(aliases),
                vars=tuple(vars_items),
            )
        )
    _check_duplicate_names([taskfile.name for taskfile in taskfiles], path)
    return tuple(taskfiles)


def validate_manifest(raw: Any, *, source: str = CONFIG_FILENAME) -> Manifest:
    manifest = _expect_mapping(raw, source)
    if "profile" in manifest:
        raise ManifestError(f"{source}.profile: obsolete field; use modules: [...]")
    if "profiles" in manifest:
        raise ManifestError(f"{source}.profiles: obsolete field; use modules: [...]")
    _reject_raw_block_fields(manifest, source)
    old_keys = sorted(set(manifest) & OLD_GENERATOR_KEYS)
    if old_keys:
        raise ManifestError(
            f"{source}: old generator keys are not supported: {', '.join(old_keys)}"
        )
    _reject_unknown_keys(manifest, ROOT_KEYS, source)
    _require_keys(manifest, ROOT_KEYS - {"ansible", "taskfiles"}, source)

    modules = _parse_modules(manifest["modules"], source)
    project = _parse_project(manifest["project"], source)
    realms = _parse_realms(manifest["realms"], source)
    environments = _parse_environments(
        manifest["environments"], {realm.name for realm in realms}, source
    )
    services = _parse_services(manifest["services"], source)
    ansible = (
        _parse_ansible(manifest["ansible"], {realm.name for realm in realms}, source)
        if "ansible" in manifest
        else None
    )
    taskfiles = (
        _parse_taskfiles(manifest["taskfiles"], source)
        if "taskfiles" in manifest
        else ()
    )
    if any(module.name == "ansible" for module in modules) and ansible is None:
        raise ManifestError(f"{source}.ansible: required when using the ansible module")
    if ansible is not None and not any(module.name == "ansible" for module in modules):
        raise ManifestError(
            f"{source}.modules: must include ansible when ansible config is set"
        )
    return Manifest(
        modules=modules,
        project=project,
        realms=realms,
        environments=environments,
        services=services,
        ansible=ansible,
        taskfiles=taskfiles,
    )


def load_manifest(target: Path) -> Manifest:
    obsolete_path = target / OBSOLETE_CONFIG_FILENAME
    if obsolete_path.exists():
        raise ManifestError(
            f"{obsolete_path}: obsolete APG target config is not supported; rename root {OBSOLETE_CONFIG_FILENAME} to {CONFIG_FILENAME}"
        )
    return validate_manifest(
        _load_yaml(target / CONFIG_FILENAME), source=str(target / CONFIG_FILENAME)
    )
