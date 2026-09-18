# Running / Testing
- App: `uv run python meadowlark.pyw`
- Tests: `uv run pytest -q` (never bare `pytest`); single file: `uv run pytest tests/test_x.py -q`
- Lint: `uv run ruff check` (zero findings; `select = ["ALL"]`, so the ruff config is the style guide)
- Shared library: `genekit` (pinned git dependency, see global CLAUDE.md); no in-repo shared package.

# Structural Guidelines
- Feature implementation: Suggest directory structure (`src/`, `tests/`).
