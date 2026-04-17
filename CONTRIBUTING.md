# Contributing to Kali Tools Manager

Thanks for your interest in improving `kalitools`! This document covers the
short-path for getting a development environment running and the conventions
we follow.

## Quick start

```bash
git clone https://github.com/MushroomCyber/Kali-Tools-Manager.git
cd Kali-Tools-Manager

# Python 3.10+ required. Use a venv:
python3 -m venv .venv
source .venv/bin/activate

# Install with every extra so you can run tests + optional features:
pip install -e '.[notifications,disk,tui,dev]'

# Run:
kalitools --help
```

## Running tests

```bash
pytest -q
```

## Linting & formatting

```bash
ruff check .
ruff format .
```

## What to work on

- Look for issues labelled **good first issue** or **help wanted**.
- Bug fixes with a regression test attached go in fastest.
- For larger changes (new UI, new data sources), open an issue first so we
  can agree on direction before you write a lot of code.

## Pull request checklist

- [ ] Branch is up to date with `main`.
- [ ] Code passes `ruff check .` and `pytest -q` locally.
- [ ] New behaviour has a test.
- [ ] User-facing changes have a note in `CHANGELOG.md` under **Unreleased**.
- [ ] No personal state (e.g. installed packages, absolute home paths) is
      committed to `kalitools/data/`.

## Security issues

Please **do not** open a public issue for security bugs. See
[SECURITY.md](SECURITY.md) for reporting instructions.

## Code style

- Python 3.10+; prefer modern syntax (`match`, `|` unions) only where it
  improves readability.
- Use `subprocess.run(..., shell=False)` with list arguments — never
  concatenate untrusted input into shell strings.
- Route JSON writes through `kalitools.manager._atomic_write_json` (or
  `_atomic_write_text`) so partial writes never corrupt user state.
- Keep `manager.py` subprocess wrappers dumb; put policy in the caller.

## Commit messages

We follow a loose Conventional Commits style:

- `fix: …` for bug fixes
- `feat: …` for new features
- `refactor: …`, `docs: …`, `test: …`, `chore: …` as appropriate

Thanks for helping make this a better tool for the community!
