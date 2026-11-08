# Contributing to MeadowLark

Thanks for considering a contribution.

## Development Setup

See [Developer Setup](README.md#developer-setup) in the README for cloning, installing dependencies, and running from source.

## Coding Standards

- **Ruff is the style guide.** `pyproject.toml` runs `select = ["ALL"]` with a 100-character line
  limit; the ignore list there is deliberate. Let `uv run ruff check --fix` and `uv run ruff format`
  handle auto-fixable findings rather than fixing them by hand.
- **Types:** annotate every function. `uv run basedpyright` must report no errors beyond
  `.basedpyright/baseline.json` — never add to the baseline.
- **Size and reuse:** keep modules under ~1000 lines and don't duplicate logic — extract a
  parameterized function instead.
- **Tests:** add or update tests for any behaviour change (`tests/`, pytest).

## Before Opening a PR

Run these from the repo root; CI runs the same checks on Python 3.11–3.14:

    uv run pytest -q
    uv run ruff check
    uv run ruff format --check
    uv run basedpyright

Keep PRs focused — one feature or fix per PR.

## Git Hooks (optional)

`git config core.hooksPath .githooks` enables the repo's hooks:

- **pre-push** runs `ruff check` and `ruff format --check` so lint failures are caught before CI.
  (It also runs a maintainer-only history scanner when that script exists on the machine; for
  everyone else it prints a one-line "skipped" notice.)
- **post-commit / post-checkout** rebuild the maintainer's code knowledge graph (`graphify-out/`).
  Without graphify installed they print a notice and do nothing. Set `GRAPHIFY_SKIP_HOOK=1` to
  silence post-commit.

## Reporting Bugs

Use the **Bug report** form on the [Issues](https://github.com/TheGeneCode/MeadowLark/issues/new/choose)
page. It asks for your MeadowLark version (Settings → About), Windows version, steps to reproduce,
and relevant lines from `error_log.txt` (in `%APPDATA%\MeadowLark`).

**Never paste `ytdlp_debug.log` or your `cookies.txt`** — they contain PO tokens and login sessions.
Remove your username from any file paths you paste.

## Reporting Security Issues

Do not open a public issue for security vulnerabilities — see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
