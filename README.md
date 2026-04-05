# lg-docs-mcp


[![PyPI](https://img.shields.io/pypi/v/lg-docs-mcp)](https://pypi.org/project/lg-docs-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/lg-docs-mcp)](https://pypi.org/project/lg-docs-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MCP server for LG webOS TV developer documentation. Crawls and indexes [webostv.developer.lge.com](https://webostv.developer.lge.com) locally, then exposes a full-text search and retrieval API through the Model Context Protocol.

---

## Overview

LG webOS TV developer documentation is served as a Next.js single-page application, which makes it difficult to search programmatically or integrate with AI tools. **lg-docs-mcp** solves this by:

1. **Crawling** the entire site with a headless Chromium browser
2. **Indexing** all pages in a local SQLite database with FTS5 full-text search
3. **Serving** search and retrieval tools over the Model Context Protocol (MCP)

Once set up, any MCP-compatible AI client (Claude Desktop, Claude Code, etc.) can search and read the webOS TV documentation directly, without internet access.

---

## Features

- Full-text search with BM25 ranking across the entire webOS TV developer docs
- Section-scoped search (develop, references, guides, faq, samples, tools)
- Fuzzy page retrieval by path — partial paths and approximate matches supported
- Smart query handling: automatic quoting of dotted identifiers like `webOSTV.js` and `com.webos.service.audio`
- Synonym expansion for common terms (e.g. `bluetooth` matches `ble`, `gatt`)
- Resumable crawl — continue an interrupted session without starting over
- Optional auto-refresh daemon to keep the local cache up to date
- Fully offline after the initial crawl

---

## How It Works

```
webostv.developer.lge.com
         │
         │  crawl4ai + Playwright/Chromium (BFS, async)
         ▼
   SQLite + FTS5
   (~/.cache/lg-docs-mcp/docs.db)
         │
         │  FastMCP (stdio transport)
         ▼
   MCP Client (Claude Desktop, Claude Code, …)
```

Pages are converted to Markdown and stored with their URL, path, section, and a SHA-256 content hash. On re-crawls, unchanged pages are skipped. Searches use FTS5 with BM25 ranking and fall back through progressively relaxed strategies when the strict query returns no results.

---

## Quickstart

```bash
# 1. Install
pip install lg-docs-mcp

# 2. Install Playwright browser (required — the site is a Next.js SPA)
playwright install chromium

# 3. Crawl the docs (first run: 15–30 min)
lg-docs-mcp crawl

# 4. Verify
lg-docs-mcp stats
lg-docs-mcp search "webOSTV.js launch"
```

---

## Installation

### Stable release

```bash
pip install lg-docs-mcp
playwright install chromium
```

### Development

```bash
git clone https://github.com/lenninIbarrraGonzalez/lg-mcp-docs
cd lg-docs-mcp
pip install -e ".[dev]"
playwright install chromium
```

> **Why Playwright?** The webOS TV developer site is a Next.js SPA that requires JavaScript execution to render content. Playwright drives a real Chromium instance to fetch fully-rendered HTML.

---

## CLI Reference

| Command | Description |
|---|---|
| `serve` | Start the MCP server |
| `crawl` | Crawl the docs site and populate the local cache |
| `search <query>` | Run a search against the local cache |
| `stats` | Show cache statistics and freshness info |
| `check` | Check cache freshness (exits 0 if fresh, 1 if stale) |

### `serve`

Start the MCP server. Connect any MCP client to stdin/stdout.

```bash
lg-docs-mcp serve

# Enable the auto-refresh daemon (checks every 24 h, refreshes if cache is older than 7 days)
lg-docs-mcp serve --auto-refresh
```

### `crawl`

Crawl the documentation site. The first crawl downloads the full site (typically 15–30 minutes). Subsequent crawls skip pages whose content has not changed.

```bash
# Full crawl with defaults
lg-docs-mcp crawl

# Limit scope for a quick test
lg-docs-mcp crawl --max-pages 200 --max-depth 3

# Resume an interrupted crawl
lg-docs-mcp crawl --resume

# Crawl with a larger page budget
lg-docs-mcp crawl --max-pages 5000 --max-depth 10
```

| Flag | Default | Description |
|---|---|---|
| `--max-pages` | 2000 | Maximum number of pages to crawl |
| `--max-depth` | 5 | Maximum BFS depth from the start URL |
| `--resume` | false | Continue a previously interrupted crawl |

### `search`

Search the local cache using FTS5 query syntax.

```bash
# Simple keyword search
lg-docs-mcp search "webOSTV.js"

# Phrase search
lg-docs-mcp search '"media playback"'

# AND search (both terms required)
lg-docs-mcp search "launch AND service"

# Prefix search
lg-docs-mcp search "subscri*"

# Limit results
lg-docs-mcp search "audio" --limit 5
```

### `stats`

Print a summary of the local cache.

```bash
lg-docs-mcp stats
# Total pages: 1842
# Database size: 48.3 MB
# Last crawled: 2026-04-01T14:22:10
# Cache status: fresh
```

### `check`

Exit with code 0 if the cache is fresh, 1 if stale. Useful in scripts and CI.

```bash
lg-docs-mcp check || lg-docs-mcp crawl
```

---

## MCP Integration

### Claude Desktop

Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "lg-docs": {
      "command": "lg-docs-mcp",
      "args": ["serve"]
    }
  }
}
```

### Claude Code (CLI)

Add to `.claude/settings.local.json` in your project, or to `~/.claude/settings.json` for global access:

```json
{
  "mcpServers": {
    "lg-docs": {
      "command": "lg-docs-mcp",
      "args": ["serve"]
    }
  }
}
```

### Other MCP Clients

The server uses the standard **stdio transport**. Any MCP client that supports stdio can connect by running `lg-docs-mcp serve` as a subprocess.

---

## Tools Reference

### Overview

| Tool | Description |
|---|---|
| `lg_search_docs` | Full-text search across all cached pages |
| `lg_search_by_section` | Full-text search scoped to a specific section |
| `lg_get_page` | Retrieve the full content of a page by path |
| `lg_list_sections` | List all cached sections with page counts |
| `lg_get_stats` | Return database statistics and freshness info |
| `lg_refresh_cache` | Re-crawl the docs site (long-running, async) |

---

### `lg_search_docs`

Full-text search across all cached pages using FTS5 / BM25 ranking.

**Parameters**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Search query (FTS5 syntax supported) |
| `limit` | integer | no | 10 | Maximum number of results |
| `offset` | integer | no | 0 | Pagination offset |
| `response_format` | string | no | `"json"` | `"json"` or `"markdown"` |

**Example**

```json
{
  "tool": "lg_search_docs",
  "arguments": {
    "query": "webOSTV.js launch application",
    "limit": 5
  }
}
```

```json
{
  "results": [
    {
      "path": "/develop/web-app-development/using-webostv.js/",
      "title": "Using webOSTV.js",
      "section": "develop",
      "snippet": "…use webOSTV.js to launch other applications using the launch method…",
      "score": 12.4
    }
  ],
  "total": 1,
  "query": "webOSTV.js launch application"
}
```

---

### `lg_search_by_section`

Same as `lg_search_docs` but restricted to a single documentation section.

**Parameters**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `section` | string | yes | — | One of: `develop`, `distribute`, `faq`, `references`, `guides`, `tools`, `samples` |
| `query` | string | yes | — | Search query |
| `limit` | integer | no | 10 | Maximum results |
| `offset` | integer | no | 0 | Pagination offset |
| `response_format` | string | no | `"json"` | `"json"` or `"markdown"` |

**Example**

```json
{
  "tool": "lg_search_by_section",
  "arguments": {
    "section": "references",
    "query": "com.webos.service.audio",
    "limit": 3
  }
}
```

---

### `lg_get_page`

Retrieve the full Markdown content of a page by its URL path. Supports exact, fuzzy, and keyword-based matching.

**Parameters**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | yes | — | URL path, partial path, or descriptive keywords |

**Example**

```json
{
  "tool": "lg_get_page",
  "arguments": {
    "path": "/develop/web-app-development/using-webostv.js/"
  }
}
```

Partial paths and approximate matches also work:

```json
{ "path": "using-webostv" }
{ "path": "webostv.js" }
```

---

### `lg_list_sections`

List all sections present in the local cache with their page counts.

**Parameters:** none

**Example response**

```json
{
  "sections": [
    { "section": "develop", "count": 312 },
    { "section": "references", "count": 891 },
    { "section": "guides", "count": 147 },
    { "section": "faq", "count": 88 },
    { "section": "samples", "count": 54 },
    { "section": "tools", "count": 23 }
  ]
}
```

---

### `lg_get_stats`

Return statistics about the local cache: total pages, database size, last crawl date, and freshness status.

**Parameters:** none

**Example response**

```json
{
  "total_pages": 1842,
  "db_size_mb": 48.3,
  "last_crawled": "2026-04-01T14:22:10",
  "cache_status": "fresh",
  "days_since_crawl": 3
}
```

---

### `lg_refresh_cache`

Re-crawl the documentation site and update the local cache. This is a long-running operation (15–30 min for a full crawl). Unchanged pages are skipped via content hashing.

**Parameters**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `max_depth` | integer | no | 5 | Maximum BFS depth (1–20) |
| `max_pages` | integer | no | 2000 | Maximum pages to crawl (1–10000) |
| `resume` | boolean | no | false | Resume an interrupted crawl |

**Example**

```json
{
  "tool": "lg_refresh_cache",
  "arguments": {
    "max_pages": 5000,
    "resume": true
  }
}
```

---

## Search Tips

### FTS5 Query Syntax

| Pattern | Example | Meaning |
|---|---|---|
| Single keyword | `launch` | Pages containing "launch" |
| Multiple keywords | `launch service` | Pages containing both words (AND) |
| Phrase | `"media playback"` | Exact phrase match |
| OR | `audio OR video` | Either term |
| NOT | `launch NOT tutorial` | First term, excluding second |
| Prefix | `subscri*` | Words starting with "subscri" |

### Dotted Identifiers

Service URIs and JavaScript namespaces are tokenized as single units. You can search for them directly:

```
com.webos.service.audio
webOSTV.js
com.webos.applicationManager
```

The query sanitizer automatically quotes these tokens to prevent FTS5 from treating the dots as operators.

### Synonym Expansion

Certain queries are automatically expanded to include common synonyms:

| Query term | Also searches |
|---|---|
| `bluetooth` | `ble`, `gatt` |
| `ble` | `bluetooth`, `gatt` |
| `tv` | `television`, `webos` |
| `remote` | `rcu`, `magic remote` |

---

## Configuration

All settings can be overridden via environment variables.

| Variable | Default | Description |
|---|---|---|
| `LG_DOCS_DB_PATH` | `~/.cache/lg-docs-mcp/docs.db` | SQLite database path |
| `LG_DOCS_START_URL` | webostv.developer.lge.com root | Crawl entry point |
| `LG_DOCS_URL_PATTERN` | `*webostv.developer.lge.com*` | BFS URL glob filter |
| `LG_DOCS_CONTENT_SELECTOR` | `main` | CSS selector for content extraction |
| `LG_DOCS_MAX_CONTENT_SIZE` | `500000` | Maximum bytes per page (larger pages are skipped) |
| `LG_DOCS_FRESH_DAYS` | `7` | Days before cache is considered aging |
| `LG_DOCS_AGING_DAYS` | `30` | Days before cache is considered stale |
| `LG_DOCS_STALE_DAYS` | `90` | Days before cache is considered very stale |
| `LG_DOCS_AUTO_REFRESH` | `0` | Set to `1` to enable the background auto-refresh daemon |
| `LG_DOCS_AUTO_REFRESH_DAYS` | `7` | Maximum cache age before a refresh is triggered |
| `LG_DOCS_CHECK_INTERVAL_HOURS` | `24` | How often the daemon checks freshness |

**Example: custom database path**

```bash
export LG_DOCS_DB_PATH=/data/lg-docs.db
lg-docs-mcp crawl
lg-docs-mcp serve
```

---

## Architecture

| Component | Technology | Role |
|---|---|---|
| Crawler | crawl4ai + Playwright/Chromium | Async BFS; renders the Next.js SPA and converts HTML to Markdown |
| Storage | SQLite + FTS5 | Local index with BM25 ranking and a custom tokenizer for dots/hyphens/underscores |
| Server | FastMCP | Exposes MCP tools over stdio transport |
| Cache checker | Python daemon | Periodically checks freshness and triggers re-crawls when configured |

**Notable design decisions:**

- **Thread-local SQLite connections** — safe for concurrent FastMCP requests
- **WAL journal mode + 30 s busy timeout** — reliable under lock contention
- **Content hashing (SHA-256)** — re-crawls skip unchanged pages
- **FTS5 prefix indexes (2–4 chars)** — fast autocomplete-style prefix queries
- **Multi-strategy search fallback** — strict FTS5 → OR query → synonym expansion → dot-notation splitting

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and pull request guidelines.

---

## License

[MIT](LICENSE)
