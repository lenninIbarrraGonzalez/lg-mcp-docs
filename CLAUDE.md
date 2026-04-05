# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
playwright install chromium   # required only for actual crawling
```

## Common commands

```bash
# Tests
pytest                        # all tests
pytest tests/test_db.py       # single module
pytest -v --tb=short -q       # CI-style output

# Linting
ruff check src/ tests/
ruff check --fix src/ tests/  # auto-fix

# Type checking (strict mode — all public functions need annotations)
mypy src/

# CLI
lg-docs-mcp crawl             # populate local cache (15–30 min, needs Playwright)
lg-docs-mcp search "query"    # search against local cache
lg-docs-mcp stats             # cache statistics
lg-docs-mcp serve             # start MCP server
```

## Architecture

The project has four modules under `src/lg_docs_mcp/`:

- **server.py** — FastMCP server and CLI entrypoint (`argparse`). Defines the 6 MCP tools, response formatting helpers, and the `_SUBSECTION_ALIASES` dict that maps logical names (`references`, `guides`, `tools`, `samples`) to path prefixes under the `develop` section in the DB.

- **db.py** — SQLite storage layer. Uses thread-local connections (safe for concurrent FastMCP requests). FTS5 virtual table with a custom tokenizer that preserves dots, hyphens, and underscores — this is what makes queries like `com.webos.service.audio` and `webOSTV.js` work as single tokens. Search uses multi-strategy fallback: strict FTS5 → OR query → synonym expansion → dot-notation splitting. WAL mode + 30 s busy timeout.

- **scraper.py** — Async BFS crawler using `crawl4ai` + Playwright. Converts HTML to Markdown via `crawl4ai`'s `DefaultMarkdownGenerator`, extracts content with the `main` CSS selector by default, and skips pages whose SHA-256 content hash hasn't changed. Supports resume via a `crawled_urls` set persisted in the DB.

- **checker.py** — Cache staleness detection with three thresholds (`FRESH_DAYS=7`, `AGING_DAYS=30`, `STALE_DAYS=90`). Optionally runs as a background daemon thread that checks freshness every 24 h and triggers a re-crawl when the cache exceeds `AUTO_REFRESH_DAYS`.

## Key data model

The `docs` table stores: `url`, `path`, `section` (top-level: develop/distribute/faq/…), `title`, `content` (Markdown), `content_hash`. The FTS5 virtual table `docs_fts` indexes `title` and `content` with prefix indexes on 2–4 chars. Sub-sections (references, guides, tools, samples) are stored with `section='develop'` and distinguished by path prefix — the server translates section aliases to `path LIKE '/develop/references%'` queries.

## Tests

All tests use isolated temporary SQLite databases — no network access or Playwright required. The test files map 1-to-1 with source modules, plus `test_mcp_protocol.py` for MCP transport integration and `test_usage.py` for end-to-end usage scenarios.
