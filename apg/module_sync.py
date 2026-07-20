"""Module-based file synchronization for APG-managed repositories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import jinja2
import yaml
from jinja2.sandbox import SandboxedEnvironment

from apg.manifest import CONFIG_FILENAME, Manifest, ManifestError, load_manifest


MODULE_MANIFEST_FILENAME = "manifest.yml"
MANAGED_MANIFEST_FILENAME = "apg-manifest.json"
DEFAULT_LINTER_TIMEOUT = 30.0
MODULE_KEYS = frozenset({"files", "templates", "linters", "vars"})


@dataclass(frozen=True)
class ModulePaths:
    name: str
    root: Path
    files: Path
    templates: Path
    linters: Path


def resolve_modules_root(path: Path) -> Path:
    """Resolve and validate the external APG module registry."""
    if not path.is_dir():
        raise ModuleError(f"modules root must be an existing directory: {path}")
    return path.resolve()


def _module_paths(modules_root: Path, name: str) -> ModulePaths:
    root = modules_root / name
    return ModulePaths(
        name=name,
        root=root,
        files=root / "files",
        templates=root / "templates",
        linters=root / "linters",
    )


@dataclass(frozen=True)
class MappingEntry:
    source: str
    destination: str


@dataclass(frozen=True)
class ModuleVar:
    name: str
    type: str
    required: bool
    default: object = None


@dataclass(frozen=True)
class Module:
    name: str
    paths: ModulePaths
    files: tuple[MappingEntry, ...]
    templates: tuple[MappingEntry, ...]
    linters: tuple[str, ...]
    vars_schema: tuple[ModuleVar, ...]


@dataclass(frozen=True)
class DesiredFile:
    destination: str
    content: bytes
    executable: bool


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str

    def format(self) -> str:
        return f"{self.kind}: {self.path}"


class ModuleError(RuntimeError):
    """Raised when module configuration or target state is invalid."""


def _load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ModuleError(f"missing required file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ModuleError(f"invalid YAML in {path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise ModuleError(f"cannot read {path}: {exc}") from exc


def _parse_mapping_entry(raw: Any, *, field_name: str) -> MappingEntry:
    if isinstance(raw, str):
        if ":" not in raw:
            raise ModuleError(f"{field_name} entry must be source:destination: {raw!r}")
        source, destination = raw.split(":", maxsplit=1)
        return MappingEntry(
            source=_canonical_relative_path(source.strip(), purpose="source"),
            destination=_canonical_relative_path(
                destination.strip(), purpose="destination"
            ),
        )

    if isinstance(raw, Mapping):
        if set(raw) == {"source", "destination"}:
            return MappingEntry(
                source=_canonical_relative_path(str(raw["source"]), purpose="source"),
                destination=_canonical_relative_path(
                    str(raw["destination"]), purpose="destination"
                ),
            )
        if len(raw) == 1:
            source, destination = next(iter(raw.items()))
            return MappingEntry(
                source=_canonical_relative_path(str(source), purpose="source"),
                destination=_canonical_relative_path(
                    str(destination), purpose="destination"
                ),
            )

    raise ModuleError(f"invalid {field_name} entry: {raw!r}")


def _parse_mapping_entries(raw: Any, *, field_name: str) -> tuple[MappingEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ModuleError(f"module {field_name} must be a list")
    return tuple(_parse_mapping_entry(item, field_name=field_name) for item in raw)


def _parse_linters(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ModuleError("module linters must be a list of paths")
    return tuple(raw)


def _parse_module_var(name: str, raw: Any, path: str) -> ModuleVar:
    if not isinstance(raw, Mapping):
        raise ModuleError(f"{path}: must be a mapping")
    unknown = sorted(set(raw) - {"type", "required", "default"})
    if unknown:
        raise ModuleError(f"{path}: unknown keys: {', '.join(unknown)}")
    type_ = "string"
    if "type" in raw:
        type_value = raw["type"]
        if type_value not in {"string", "boolean", "string-list"}:
            raise ModuleError(
                f"{path}.type: only 'string', 'boolean', or 'string-list' is supported"
            )
        type_ = type_value
    required = False
    if "required" in raw:
        required_value = raw["required"]
        if not isinstance(required_value, bool):
            raise ModuleError(f"{path}.required: must be a boolean")
        required = required_value
    default: object = None
    if "default" in raw:
        default_value = raw["default"]
        if type_ == "string":
            if not isinstance(default_value, str):
                raise ModuleError(f"{path}.default: must be a string")
        elif type_ == "string-list":
            if not isinstance(default_value, list) or not all(
                isinstance(item, str) and item.strip() != "" for item in default_value
            ):
                raise ModuleError(
                    f"{path}.default: must be a list of non-empty strings"
                )
        else:
            if not isinstance(default_value, bool):
                raise ModuleError(f"{path}.default: must be a boolean")
        default = default_value
    return ModuleVar(name=name, type=type_, required=required, default=default)


def _parse_vars_schema(raw: Any, path: str) -> tuple[ModuleVar, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ModuleError(f"{path}: must be a mapping")
    vars_schema: list[ModuleVar] = []
    for name, value in raw.items():
        if not isinstance(name, str):
            raise ModuleError(f"{path}: keys must be strings")
        vars_schema.append(_parse_module_var(name, value, f"{path}.{name}"))
    return tuple(vars_schema)


def load_module(modules_root: Path, name: str) -> Module:
    modules_root = resolve_modules_root(modules_root)
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ModuleError(f"invalid module name: {name!r}")

    paths = _module_paths(modules_root, name)
    manifest_path = paths.root / MODULE_MANIFEST_FILENAME
    raw = _load_yaml(manifest_path)
    if not isinstance(raw, Mapping):
        raise ModuleError(f"module must be a mapping: {manifest_path}")
    if not all(isinstance(key, str) for key in raw):
        raise ModuleError(f"{manifest_path}: keys must be strings")
    if "extends" in raw:
        raise ModuleError(f"{manifest_path}: module inheritance is not supported")
    unknown = sorted(set(raw) - MODULE_KEYS)
    if unknown:
        raise ModuleError(f"{manifest_path}: unknown keys: {', '.join(unknown)}")

    return Module(
        name=name,
        paths=paths,
        files=_parse_mapping_entries(raw.get("files"), field_name="files"),
        templates=_parse_mapping_entries(raw.get("templates"), field_name="templates"),
        linters=_parse_linters(raw.get("linters")),
        vars_schema=_parse_vars_schema(raw.get("vars"), f"{manifest_path}.vars"),
    )


def _validate_module_vars(
    module: Module, module_ref: Any, source: str
) -> dict[str, object]:
    provided = {name: value for name, value in module_ref.vars}
    errors: list[str] = []
    allowed = {var.name for var in module.vars_schema}
    result: dict[str, object] = {}
    for var in module.vars_schema:
        if var.name in provided:
            value = provided[var.name]
            if var.type == "string" and not (isinstance(value, str) and value != ""):
                errors.append(f"var {var.name!r} must be a non-empty string")
            elif var.type == "boolean" and not isinstance(value, bool):
                errors.append(f"var {var.name!r} must be a boolean")
            elif var.type == "string-list" and not (
                isinstance(value, list)
                and all(isinstance(item, str) and item.strip() != "" for item in value)
            ):
                errors.append(f"var {var.name!r} must be a list of non-empty strings")
            result[var.name] = value
        else:
            if var.required:
                errors.append(f"missing required var {var.name!r}")
            elif var.default is not None:
                result[var.name] = var.default
    for name in provided:
        if name not in allowed:
            errors.append(f"unknown var {name!r}")
    if errors:
        raise ModuleError(f"{source}[{module_ref.name}].vars: {', '.join(errors)}")
    return result


def _canonical_relative_path(path: str, *, purpose: str = "target") -> str:
    candidate = Path(path)
    if (
        candidate.is_absolute()
        or path.strip() in {"", "."}
        or ".." in candidate.parts
        or "\\" in path
    ):
        raise ModuleError(f"unsafe {purpose}-relative path: {path!r}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ModuleError(f"unsafe {purpose}-relative path: {path!r}")
    return normalized


def _safe_relative_path(path: str) -> Path:
    return Path(_canonical_relative_path(path))


def _resolve_under(root: Path, path: str) -> Path:
    try:
        relative = _safe_relative_path(path)
        candidate = root / relative
        root_resolved = root.resolve()
        existing_parent = candidate.parent
        while not existing_parent.exists() and existing_parent != root:
            existing_parent = existing_parent.parent
        parent_resolved = existing_parent.resolve()
        if not parent_resolved.is_relative_to(root_resolved):
            raise ModuleError(f"path escapes root through symlinked parent: {path}")
        if candidate.is_symlink():
            raise ModuleError(f"refusing to manage symlink destination: {path}")
        if candidate.exists() and not candidate.resolve().is_relative_to(root_resolved):
            raise ModuleError(f"path escapes target root: {path}")
        return candidate
    except OSError as exc:
        raise ModuleError(f"cannot inspect managed path {path!r}: {exc}") from exc


def _resolve_stale_under(root: Path, path: str) -> Path:
    try:
        candidate = root / _safe_relative_path(path)
        if not candidate.parent.resolve().is_relative_to(root.resolve()):
            raise ModuleError(
                f"stale path escapes root through symlinked parent: {path}"
            )
        return candidate
    except OSError as exc:
        raise ModuleError(f"cannot inspect stale managed path {path!r}: {exc}") from exc


def _source_path(root: Path, source: str) -> Path:
    relative = _safe_relative_path(source)
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ModuleError(f"source escapes APG root: {source}") from exc
    if not candidate.is_file():
        raise ModuleError(f"missing module source: {candidate}")
    return candidate


def _linter_path(module: Module, path: str) -> Path:
    try:
        relative = _safe_relative_path(path)
    except ModuleError as exc:
        raise ModuleError(f"unsafe linter path: {path!r}") from exc
    if relative.suffix != ".py":
        raise ModuleError(f"unsafe linter path: {path!r}")
    candidate = module.paths.linters / relative
    try:
        candidate.resolve().relative_to(module.paths.linters.resolve())
    except ValueError as exc:
        raise ModuleError(f"linter escapes module linters root: {path}") from exc
    if not candidate.is_file():
        raise ModuleError(f"missing linter script: {candidate}")
    return candidate


def _jinja_environment(templates_root: Path) -> SandboxedEnvironment:
    environment = SandboxedEnvironment(
        loader=jinja2.FileSystemLoader(str(templates_root)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    environment.filters["to_yaml"] = lambda value: yaml.safe_dump(
        value, sort_keys=False, allow_unicode=False
    ).rstrip()
    environment.globals.clear()
    return environment


def _context(
    module: Module, module_vars: dict[str, object], manifest: Manifest
) -> dict[str, Any]:
    context = manifest.to_context()
    return {
        "module": module.name,
        "vars": module_vars,
        "manifest": context,
        "project": context["project"],
        "realms": context["realms"],
        "environments": context["environments"],
        "services": context["services"],
        "ansible": context["ansible"],
    }


def compose_target_state(
    manifest: Manifest, modules_root: Path
) -> tuple[tuple[DesiredFile, ...], tuple[str, ...]]:
    desired: list[DesiredFile] = []
    linters: list[tuple[str, str]] = []
    seen_linters: set[tuple[str, str]] = set()
    seen_destinations: set[str] = set()
    source = CONFIG_FILENAME

    for module_ref in manifest.modules:
        module = load_module(modules_root, module_ref.name)
        module_vars = _validate_module_vars(module, module_ref, source)
        context = _context(module, module_vars, manifest)

        for linter in module.linters:
            key = (module.name, linter)
            if key not in seen_linters:
                seen_linters.add(key)
                linters.append(key)

        for entry in module.files:
            source_path = _source_path(module.paths.files, entry.source)
            if entry.destination in seen_destinations:
                raise ModuleError(f"duplicate managed destination: {entry.destination}")
            seen_destinations.add(entry.destination)
            desired.append(
                DesiredFile(
                    destination=entry.destination,
                    content=_read_bytes(source_path),
                    executable=bool(_stat(source_path).st_mode & stat.S_IXUSR),
                )
            )

        if module.templates:
            environment = _jinja_environment(module.paths.templates)
            for entry in module.templates:
                source_path = _source_path(module.paths.templates, entry.source)
                if entry.destination in seen_destinations:
                    raise ModuleError(
                        f"duplicate managed destination: {entry.destination}"
                    )
                seen_destinations.add(entry.destination)
                template_name = source_path.relative_to(
                    module.paths.templates
                ).as_posix()
                try:
                    content = (
                        environment.get_template(template_name)
                        .render(context)
                        .encode("utf-8")
                    )
                except (jinja2.TemplateError, UnicodeError) as exc:
                    raise ModuleError(
                        f"cannot render template {source_path}: {exc}"
                    ) from exc
                desired.append(
                    DesiredFile(
                        destination=entry.destination,
                        content=content,
                        executable=bool(_stat(source_path).st_mode & stat.S_IXUSR),
                    )
                )

    if MANAGED_MANIFEST_FILENAME in seen_destinations:
        raise ModuleError(
            f"{MANAGED_MANIFEST_FILENAME!r} is reserved by APG and cannot be managed by a module"
        )
    managed_content = {
        item.destination: {
            "sha256": hashlib.sha256(item.content).hexdigest(),
            "executable": item.executable,
        }
        for item in desired
    }
    desired.append(
        DesiredFile(
            destination=MANAGED_MANIFEST_FILENAME,
            content=(
                json.dumps(
                    {"files": managed_content},
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
            executable=False,
        )
    )

    return tuple(desired), tuple(
        f"{module_name}:{linter}" for module_name, linter in linters
    )


def load_target_state(
    target: Path, modules_root: Path
) -> tuple[tuple[DesiredFile, ...], tuple[str, ...]]:
    manifest = load_manifest(target)
    return compose_target_state(manifest, modules_root)


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ModuleError(f"cannot read {path}: {exc}") from exc


def _stat(path: Path) -> os.stat_result:
    try:
        return path.stat()
    except OSError as exc:
        raise ModuleError(f"cannot inspect {path}: {exc}") from exc


def _set_executable(path: Path, executable: bool) -> None:
    mode = _stat(path).st_mode
    try:
        if executable:
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            path.chmod(mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
    except OSError as exc:
        raise ModuleError(f"cannot set mode on {path}: {exc}") from exc


def _load_managed_index(target: Path) -> dict[str, tuple[str, bool] | None]:
    index = target / MANAGED_MANIFEST_FILENAME
    if not index.exists():
        return {}
    if index.is_symlink() or not index.is_file():
        raise ModuleError(f"managed index must be a regular file: {index}")
    try:
        text = index.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ModuleError(f"cannot read managed index {index}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        result: dict[str, tuple[str, bool] | None] = {}
        for line in text.splitlines():
            if not line:
                continue
            normalized = _canonical_relative_path(line)
            if normalized != line:
                raise ModuleError(
                    f"non-canonical path in legacy managed index: {line!r}"
                )
            if normalized != MANAGED_MANIFEST_FILENAME:
                result[normalized] = None
        return result
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"files"}
        or not isinstance(raw["files"], Mapping)
    ):
        raise ModuleError(f"unsupported managed index format: {index}")
    result = {}
    for path, metadata in raw["files"].items():
        if (
            not isinstance(path, str)
            or not isinstance(metadata, Mapping)
            or set(metadata) != {"sha256", "executable"}
            or not isinstance(metadata["sha256"], str)
            or not isinstance(metadata["executable"], bool)
        ):
            raise ModuleError(f"invalid managed index entry: {path!r}")
        normalized = _canonical_relative_path(path)
        if normalized != path or normalized == MANAGED_MANIFEST_FILENAME:
            raise ModuleError(f"invalid managed index path: {path!r}")
        digest = metadata["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ModuleError(f"invalid managed index digest for {path!r}")
        result[path] = (digest, metadata["executable"])
    return result


def _stale_findings(
    target: Path, desired_files: tuple[DesiredFile, ...]
) -> tuple[Finding, ...]:
    previous = _load_managed_index(target)
    desired = {item.destination for item in desired_files}
    findings: list[Finding] = []
    for path, metadata in sorted(previous.items()):
        if path in desired:
            continue
        destination = _resolve_stale_under(target, path)
        if destination.is_symlink():
            findings.append(Finding("stale-unsafe", path))
        elif not destination.exists():
            continue
        elif not destination.is_file():
            findings.append(Finding("stale-unsafe", path))
        elif (
            metadata is None
            or hashlib.sha256(_read_bytes(destination)).hexdigest() != metadata[0]
            or bool(_stat(destination).st_mode & stat.S_IXUSR) != metadata[1]
        ):
            findings.append(Finding("stale-modified", path))
        else:
            findings.append(Finding("stale", path))
    return tuple(findings)


def check_target(target: Path, modules_root: Path) -> tuple[Finding, ...]:
    target = target.resolve()
    desired_files, _linters = load_target_state(target, modules_root)
    findings = list(_stale_findings(target, desired_files))

    for item in desired_files:
        destination = _resolve_under(target, item.destination)
        if not destination.exists():
            findings.append(Finding("missing", item.destination))
            continue
        if not destination.is_file():
            findings.append(Finding("not-file", item.destination))
            continue
        if _read_bytes(destination) != item.content:
            findings.append(Finding("changed", item.destination))
        executable = bool(_stat(destination).st_mode & stat.S_IXUSR)
        if executable != item.executable:
            findings.append(Finding("mode", item.destination))

    return tuple(findings)


def sync_target(target: Path, modules_root: Path) -> tuple[Finding, ...]:
    target = target.resolve()
    desired_files, _linters = load_target_state(target, modules_root)
    applied: list[Finding] = []
    stale = _stale_findings(target, desired_files)
    protected = tuple(
        finding
        for finding in stale
        if finding.kind in {"stale-modified", "stale-unsafe"}
    )
    if protected:
        details = ", ".join(finding.format() for finding in protected)
        raise ModuleError(f"refusing to remove former managed files: {details}")
    for finding in stale:
        destination = _resolve_stale_under(target, finding.path)
        try:
            destination.unlink()
        except OSError as exc:
            raise ModuleError(
                f"cannot remove stale managed file {destination}: {exc}"
            ) from exc
        applied.append(Finding("removed", finding.path))

    for item in desired_files:
        destination = _resolve_under(target, item.destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ModuleError(
                f"cannot create managed parent {destination.parent}: {exc}"
            ) from exc
        changed = (
            not destination.exists()
            or not destination.is_file()
            or _read_bytes(destination) != item.content
        )
        if changed:
            try:
                destination.write_bytes(item.content)
            except OSError as exc:
                raise ModuleError(
                    f"cannot write managed file {destination}: {exc}"
                ) from exc
            applied.append(Finding("wrote", item.destination))
        _set_executable(destination, item.executable)

    return tuple(applied)


def run_linters(
    target: Path, modules_root: Path, *, timeout: float = DEFAULT_LINTER_TIMEOUT
) -> tuple[Finding, ...]:
    target = target.resolve()
    _desired_files, linters = load_target_state(target, modules_root)
    findings: list[Finding] = []

    for entry in linters:
        module_name, linter = entry.split(":", maxsplit=1)
        module = load_module(modules_root, module_name)
        script = _linter_path(module, linter)
        try:
            result = subprocess.run(
                [sys.executable, str(script), "--repo", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            findings.append(Finding("linter-timeout", f"{linter}: {timeout:g}s"))
            continue
        except OSError as exc:
            raise ModuleError(f"cannot execute linter {script}: {exc}") from exc
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            findings.append(Finding("linter", f"{linter}: exit {result.returncode}"))

    return tuple(findings)


def verify_target(
    target: Path, modules_root: Path, *, timeout: float = DEFAULT_LINTER_TIMEOUT
) -> tuple[Finding, ...]:
    drift = check_target(target, modules_root)
    linters = run_linters(target, modules_root, timeout=timeout)
    return (*drift, *linters)


def _print_findings(findings: tuple[Finding, ...]) -> None:
    for finding in findings:
        print(finding.format())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    try:
        package_version = version("apexplane-guidelines")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        try:
            package_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))[
                "project"
            ]["version"]
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            package_version = "unknown"
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {package_version}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("check", "verify", "sync"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--modules-root", required=True, type=Path)
        command_parser.add_argument("targets", nargs="+", type=Path)
        if command == "verify":
            command_parser.add_argument(
                "--linter-timeout", type=float, default=DEFAULT_LINTER_TIMEOUT
            )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code = 0
    try:
        modules_root = resolve_modules_root(args.modules_root)
    except ModuleError as exc:
        print(f"apg: {exc}", file=sys.stderr)
        return 2

    if args.command == "verify" and args.linter_timeout <= 0:
        print("apg: --linter-timeout must be greater than zero", file=sys.stderr)
        return 2

    for target in args.targets:
        if len(args.targets) > 1:
            print(f"\n==> {target}")
        try:
            if args.command == "check":
                findings = check_target(target, modules_root)
                exit_code = max(exit_code, 1 if findings else 0)
            elif args.command == "verify":
                findings = verify_target(
                    target, modules_root, timeout=args.linter_timeout
                )
                exit_code = max(exit_code, 1 if findings else 0)
            else:
                findings = sync_target(target, modules_root)
            _print_findings(findings)
        except (ManifestError, ModuleError) as exc:
            prefix = f"{target}: " if len(args.targets) > 1 else ""
            print(f"apg: {prefix}{exc}", file=sys.stderr)
            exit_code = 2

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
