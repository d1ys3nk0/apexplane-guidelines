# ApexPlane Guidelines

ApexPlane Guidelines is an engine for applying user-owned repository standards through external module registries.

It ships no modules or example registries. The `apg` CLI synchronizes reusable engineering modules into target repositories.

## Install

APG requires Python 3.13. Install from a clone with `uv`:

```sh
git clone https://github.com/d1ys3nk0/apexplane-guidelines.git
cd apexplane-guidelines
uv tool install .
apg --version
```

Use `uv run apg --help` while developing from the clone.

## Use a module registry

Every APG operation requires `--modules-root`, an existing directory containing the registry. A module lives at `<modules-root>/<module-name>/`:

```text
<module-name>/
├── manifest.yml
├── files/       # copied unchanged
├── templates/   # rendered with Jinja
└── linters/     # Python scripts run by `verify`
```

Only the directories used by the module need to exist. Define its contents in `manifest.yml`; file and template entries map `source: destination`, variables declare `type`, `required`, and optional `default`, and `linters` lists paths below `linters/`.

```yaml
vars:
  python_version:
    type: string
    required: true
files:
  - editorconfig:.editorconfig
templates:
  - ci.yml.j2:.github/workflows/verify.yml
linters:
  - policy.py
```

Supported variable types are `string`, `boolean`, and `string-list`. Template context contains `vars`, `module`, and the target manifest’s `project`, `realms`, `environments`, `services`, and optional `ansible` data.

Treat a module registry as trusted executable code. APG renders its Jinja templates in a restricted sandbox, but `verify` intentionally executes the selected registry’s Python linters. Review and pin registry revisions before using them in CI or against a sensitive checkout.

## Configure a target

Each target repository has an `apg.yml` that selects modules and supplies their variables. The following uses a module named `baseline` from the external registry:

```yaml
modules:
  - name: baseline
    vars:
      python_version: "3.13"
project:
  name: example-app
realms: []
environments: []
services: []
```

Modules compose in this order. A destination may be managed by only one selected module. APG writes `apg-manifest.json` in the target to record managed paths and content hashes; do not edit or declare it in a module. When a previously managed path is removed from the selected modules, `check` reports it and `sync` deletes it only if it is still a regular, unmodified file. APG refuses unsafe or modified stale paths.

## Local workflow

From the target repository, point APG at a checked-out registry:

```sh
apg check --modules-root ../apg-modules .
apg verify --linter-timeout 30 --modules-root ../apg-modules .
apg sync --modules-root ../apg-modules .
```

`check` reports managed-file drift, `verify` also runs selected module linters, and `sync` updates managed files. Each command accepts one or more target repositories, so a control-plane workspace can run the same operation across an explicit batch:

```sh
apg check --modules-root apg/modules children/repository-a children/repository-b
apg sync --modules-root apg/modules children/repository-a children/repository-b
apg verify --modules-root apg/modules children/repository-a children/repository-b
```

Review `sync` changes before committing. Registry authors can iterate by editing the registry and running the same commands against a disposable or local target.

## Docker

The container image contains only the APG engine. Bind-mount both your registry and target repository:

```sh
docker run --rm \
  -v "$PWD/../apg-modules:/registry:ro" \
  -v "$PWD:/target" \
  ghcr.io/d1ys3nk0/apexplane-guidelines:v0.1.0 \
  apg verify --modules-root /registry /target
```

Use `sync` in place of `verify` when the mounted target should be updated.

## GitHub Actions

Use the APG image in the target repository’s workflow and check out the trusted module registry at a pinned revision. Run `check` for drift or `verify` for drift plus registry linters:

```yaml
jobs:
  apg-verify:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
        with:
          repository: your-organization/apg-modules
          ref: pinned-commit
          path: .apg-modules
      - run: >-
          docker run --rm
          -v "$GITHUB_WORKSPACE:/target:ro"
          ghcr.io/d1ys3nk0/apexplane-guidelines:v0.1.0
          apg verify --modules-root /target/.apg-modules /target
```

Pin the APG image and registry revision according to your release policy. This project’s GitHub Actions workflow validates the package and publishes the engine image.

## Development

Run the engine checks before submitting changes:

```sh
task check
```

See the [contribution guide](https://github.com/d1ys3nk0/apexplane-guidelines/blob/main/CONTRIBUTING.md), [security policy](https://github.com/d1ys3nk0/apexplane-guidelines/blob/main/SECURITY.md), and [target manifest schema](https://raw.githubusercontent.com/d1ys3nk0/apexplane-guidelines/main/apg/manifest.schema.yaml).
