# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Use `PRAGMA table_info()` for DB migration instead of fragile error-string matching
- Escape LIKE wildcards (`%`, `_`) in `get_page_fuzzy()` to prevent incorrect matches
- `url_to_section()` now returns `None` for root/pathless URLs instead of `"unknown"`
- `run_refresh()` now returns `{"error": "timeout"}` or `{"error": "exception"}` on failure,
  making error states distinguishable from a successful crawl with no new pages
- Remove internal access to crawl4ai's private `_results` attribute; iterate results directly
- Fix `is_stale()` type check to accept both `int` and `float` for `days_since_crawl`

### Changed
- Package now installed in editable mode for development (`pip install -e ".[dev]"`)
- Added `ruff` linter to dev dependencies

## [0.1.0] - 2026-02-27

### Added
- Initial release
- BFS crawl of `webostv.developer.lge.com` via crawl4ai + Playwright (headless Chromium)
- SQLite + FTS5 full-text search with BM25 ranking and custom tokenizer for dots/hyphens
- Prefix indexes (2–4 chars) for fast autocomplete-style queries
- Content hashing (SHA-256) to skip unchanged pages on re-crawl
- 6 MCP tools via FastMCP: `search_docs`, `search_by_section`, `get_page`,
  `list_sections`, `get_stats`, `refresh_cache`
- 5 CLI subcommands: `serve`, `crawl`, `search`, `stats`, `check`
- Optional auto-refresh daemon (`--auto-refresh`) with configurable staleness threshold
- Thread-local SQLite connections for safe multi-threaded FastMCP operation
- WAL journal mode + 30s busy timeout for concurrent access
- Full test suite: 109 tests across 4 modules
