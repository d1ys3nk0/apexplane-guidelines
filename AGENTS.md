# APG Contributor Instructions

## Scope

APG provides reusable engineering standards, managed files, target-repository linters, and synchronization workflows. Keep application architecture, product behavior, operational configuration, credentials, and environment-specific details in the repositories that own them.

## Working rules

- Keep guidance concise, reusable, and enforceable where practical.
- Put reusable static baselines in the owning module’s `files/` directory.
- Put reusable rendered content in the owning module’s `templates/` directory.
- Put target-repository validation in the owning module’s `linters/` directory.
- Add a convention to a module registry only when it is shared across repositories.
- Avoid secrets, customer information, private hostnames, and machine-specific paths.
- Do not hard-wrap Markdown prose or list items.

## Validation

Run `task check` before handing off changes.
