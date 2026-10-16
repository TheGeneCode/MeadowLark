# Contributing to MeadowLark

Thanks for considering a contribution.

## Development Setup

See [Developer Setup](README.md#developer-setup) in the README for cloning, installing dependencies, and running from source.

## Before Opening a PR

- Follow the coding standards in `.claude/CLAUDE.md` — Ruff/PEP8, double quotes, type hints on all functions, list comprehensions preferred over manual loops, no unused imports.
- Run `ruff check` and resolve any warnings before submitting.
- Run `uv run basedpyright` and fix any new type errors (pre-existing ones are baselined in `.basedpyright/baseline.json`; don't add to it).
- Add or update tests for any behavior change.
- Keep PRs focused — one feature or fix per PR.

## Reporting Bugs

Open a GitHub issue with:
- Steps to reproduce
- What you expected vs. what happened
- Your MeadowLark version and Windows version
- Relevant lines from `error_log.txt`, if applicable

## Reporting Security Issues

Do not open a public issue for security vulnerabilities — see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
