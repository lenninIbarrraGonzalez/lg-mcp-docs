# Contributing to lg-docs-mcp

Thank you for your interest in contributing! This document covers how to set up the project locally, run tests, and submit changes.

## Development setup

```bash
git clone https://github.com/satanas/lg-docs-mcp
cd lg-docs-mcp
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

> **Note:** The first crawl requires Playwright's Chromium browser. Install it with:
> ```bash
> playwright install chromium
> ```

## Running tests

```bash
# Run all tests
pytest

# Run a specific module
pytest tests/test_db.py

# Verbose output
pytest -v

# With short tracebacks (useful for CI)
pytest --tb=short -q
```

All tests use isolated temporary databases — no crawling or network access required.

## Linting and type checking

```bash
# Check style and imports
ruff check src/ tests/

# Auto-fix safe issues
ruff check --fix src/ tests/

# Type checking (strict mode)
mypy src/
```

The project enforces strict mypy type checking. All public functions must have type annotations.

## Project structure

```
src/lg_docs_mcp/
├── server.py     # FastMCP tools and CLI entrypoint (argparse)
├── db.py         # SQLite + FTS5 storage layer
├── scraper.py    # crawl4ai BFS crawler and content extraction
└── checker.py    # Cache staleness detection and auto-refresh daemon

tests/
├── test_server.py
├── test_db.py
├── test_scraper.py
└── test_checker.py
```

## Submitting changes

1. Fork the repository and create a branch:
   ```bash
   git checkout -b fix/your-fix-name
   ```
2. Make your changes with tests. All bug fixes should include a regression test.
3. Ensure tests pass and linting is clean:
   ```bash
   pytest --tb=short -q
   ruff check src/ tests/
   mypy src/
   ```
4. Open a pull request with a clear description of the problem and solution.

## Reporting issues

Use the [GitHub Issues tracker](https://github.com/satanas/lg-docs-mcp/issues).
Please include your Python version, OS, and steps to reproduce the issue.
