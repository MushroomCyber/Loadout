# Contributing

The easiest useful contribution is **adding a tool to the catalog** — one YAML
file, no Python. See [docs/CATALOG.md](docs/CATALOG.md).

For code, [TODO.md](TODO.md) is the current backlog, ordered by how much each
item affects someone actually using the tool.

## Setup

```bash
git clone https://github.com/MushroomCyber/Loadout.git
cd Loadout
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,tui]'

loadout --help
pytest
ruff check .
mypy loadout
```

Use `pipx install --editable .` only when you want the repo checkout itself to be
managed by pipx; `pipx install loadout` is not the right command for working from
this source tree.

## Adding a tool

```bash
$EDITOR catalog/web/mytool.yaml
loadout catalog validate --source catalog
loadout catalog build --source catalog
loadout show mytool
```

Prefer several install methods over one. The point of the provider layer is that
a tool available through Go or Homebrew should not be apt-only in the catalog.

## Adding a provider

1. Subclass `Provider` in `loadout/providers/`.
2. Declare `name`, `required_spec_keys`, `executables`, `needs_root`.
3. Implement `plan_install` and `plan_remove`. **They must be pure** — return
   `Step` objects, run nothing, print nothing.
4. Implement `list_installed()` as one bulk call, not one per tool.
5. Register it in `loadout/providers/__init__.py`.
6. Test the planned argv. Never shell out in a test.

```python
def test_my_provider_argv():
    steps = MyProvider().plan_install(Tool(id="x"), method("mine", package="x"))
    assert steps[0].argv == ["mine", "install", "x"]
```

## House rules

- **Planning is pure.** If a function decides *what* to do, it must not also do
  it. That separation is why `--dry-run` is free and the install path is testable.
- **`sudo` only in `policy.elevate()`.** A test enforces this.
- **Validate anything reaching an argv** through `policy.validate_package_name`
  or `policy.validate_argv`.
- **No network at import or construction time.** Ever.
- **Every command supports `--json`**, with a stable shape.
- **Errors carry a remediation.** `LoadoutError("x failed", remediation="try y")`.
  A user should never have to guess what to do next.
- **A button must call an action that already exists.** Every control in the
  browser dispatches to the same `action_*` method its key binding does, so the
  mouse and keyboard paths cannot drift apart. Adding a control means wiring it
  to an action, not reimplementing the behaviour beside one.
- **Never encode state in amber.** In a security tool amber reads as *something
  is wrong*; it is not free to spend on "partly done". Put the detail in the
  label and let colour reinforce it.
- Type annotations on new code; `mypy loadout` must pass.

## Tests

New behaviour needs a test. Fixed bugs need a regression test in
`tests/test_regressions.py`, named for what it locks down — those tests exist so
a bug that shipped once cannot ship twice.

Tests run against a temporary XDG root and must never touch the real catalog,
state database or network.

## Commits and pull requests

Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`,
`catalog:` for catalog-only changes.

- [ ] `pytest`, `ruff check .` and `mypy loadout` pass
- [ ] New behaviour has a test; fixed bugs have a regression test
- [ ] User-facing changes noted in `CHANGELOG.md` under Unreleased
- [ ] No machine-specific state committed to `catalog/` (CI checks this)

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
