---
applyTo: "**/*.{py,pyw,pyi}"
---

# Python Coding Standards

The standards for this repository are in [`.claude/CLAUDE.md`](../../.claude/CLAUDE.md) and
the `[tool.ruff]` config in `pyproject.toml`. Read both before writing or reviewing Python
here. The essentials:

- Ruff runs with `select = ["ALL"]`. Do not hand-fix auto-fixable findings.
- No file past ~1000 lines. No duplicated logic — extract a parameterized function.
- Run `uv run pytest -q` and `uv run ruff check` (zero findings) after any change.
