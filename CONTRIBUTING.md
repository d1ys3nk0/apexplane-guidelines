# Contributing to ApexPlane Guidelines

ApexPlane Guidelines is the synchronization engine. External module registries own organization and application policy; do not add modules, bundled registries, examples, credentials, or environment-specific configuration to this repository.

## Engine contributions

Open an issue or discussion before a substantial public-contract or workflow change. Keep changes focused, document observable behavior, and avoid compatibility layers unless an existing public contract requires one.

Before opening a merge request:

1. Create a focused branch.
2. Make the smallest engine change that solves the issue.
3. Update the relevant documentation.
4. Run `task check`.
5. Describe the intent, contract impact, and validation in the merge request.

## Authoring external modules

Keep each module in its own `<registry>/<module-name>/` directory with a `manifest.yml`. Put static sources in `files/`, Jinja sources in `templates/`, and Python validations in `linters/`. In the manifest, map every managed file or template from source to target destination, explicitly declare accepted variables and their types, and list every linter to execute.

Test a module locally against a target repository that has an `apg.yml` selecting it and providing `vars`. Run these commands from that target:

```sh
apg check --modules-root <registry-path> . [<target>...]
apg verify --modules-root <registry-path> . [<target>...]
apg sync --modules-root <registry-path> . [<target>...]
```

`sync` writes target files; all operations accept one or more target paths. Inspect and commit the resulting target changes separately from registry changes. Keep registry changes reusable across targets and keep target-specific application behavior in the target repository.

## Conduct

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
