## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Type

- [ ] Catalog entry (adding or correcting a tool)
- [ ] Bug fix
- [ ] New behaviour
- [ ] Docs
- [ ] Refactor / tooling

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] `mypy loadout` passes
- [ ] New behaviour has a test; a fixed bug has a regression test in `tests/test_regressions.py`
- [ ] User-facing changes noted in `CHANGELOG.md` under Unreleased

### If this touches the catalog

- [ ] `loadout catalog validate --source catalog` passes
- [ ] `loadout catalog build --source catalog` re-run and `loadout/data/catalog.db` committed
- [ ] Entry has a `summary` and `binaries` (the command, not the package name)
- [ ] Any `github:` install method names a `checksums` asset

### If this touches the privileged path

- [ ] `sudo` is still only constructed in `policy.elevate()`
- [ ] Package names still go through `policy.validate_package_name`
- [ ] Planning stayed pure — no subprocess or printing in `plan_*`
