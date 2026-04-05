import argparse
import asyncio
import concurrent.futures
import logging
import os
import sys
from enum import Enum
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from lg_docs_mcp import checker, db

logger = logging.getLogger(__name__)

mcp = FastMCP("lg_docs_mcp")

# Sub-section aliases: logical names that map to path prefixes under 'develop'.
# These are NOT top-level sections in the DB (all share section='develop'),
# but users naturally refer to them by name.
_SUBSECTION_ALIASES: dict[str, str] = {
    "references": "/develop/references",
    "guides":     "/develop/guides",
    "tools":      "/develop/tools",
    "samples":    "/develop/samples",
}

# Known top-level sections stored in the DB.  Used to distinguish "valid section
# with no matches" from "unknown section" in no-results messages.
_KNOWN_TOP_SECTIONS: frozenset[str] = frozenset({
    "develop", "distribute", "faq", "news", "more", "notice", "other",
})


# ---------------------------------------------------------------------------
# Response format enum
# ---------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    """Output format for tool responses."""
    MARKDOWN = "markdown"
    JSON = "json"


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _format_results_markdown(
    results: list[dict[str, Any]],
    query: str,
    offset: int,
    has_more: bool,
) -> str:
    lines = [f"# Search Results: '{query}'", ""]
    lines.append(f"Showing {len(results)} result(s) (offset={offset}, has_more={has_more})")
    lines.append("")
    for r in results:
        lines.append(f"## {r.get('title', '(no title)')} — `{r.get('path', '')}`")
        lines.append(f"**Section:** {r.get('section', '')}  ")
        lines.append(f"**Snippet:** {r.get('snippet', '')}")
        lines.append("")
    return "\n".join(lines)


def _build_search_response(
    raw: list[dict[str, Any]],
    limit: int,
    offset: int,
    query: str,
    response_format: ResponseFormat,
    no_results_hint: str | None = None,
) -> dict[str, Any] | str:
    """Build paginated search response from raw db results."""
    has_more = len(raw) > limit + offset
    results = raw[offset: offset + limit]

    if not results:
        if response_format == ResponseFormat.MARKDOWN:
            msg = no_results_hint or f"No results found for '{query}'."
            return f"# No Results\n\n{msg}"
        resp: dict[str, Any] = {"results": [], "count": 0, "offset": offset, "has_more": False}
        if no_results_hint:
            resp["message"] = no_results_hint
        return resp

    if response_format == ResponseFormat.MARKDOWN:
        return _format_results_markdown(results, query, offset, has_more)

    return {"results": results, "count": len(results), "offset": offset, "has_more": has_more}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="lg_search_docs",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def lg_search_docs(
    query: Annotated[str, Field(
        description=(
            "Search terms (supports FTS5 MATCH syntax, e.g. 'webOSTV.js launch'). "
            "Use trailing * for prefix search: 'webostv*' matches 'webostv.js'."
        ),
        min_length=1,
    )],
    limit: Annotated[int, Field(
        description="Maximum number of results to return (1–100).",
        ge=1,
        le=100,
    )] = 10,
    offset: Annotated[int, Field(
        description="Number of results to skip for pagination.",
        ge=0,
    )] = 0,
    response_format: Annotated[ResponseFormat, Field(
        description="Output format: 'json' for structured data or 'markdown' for human-readable text.",
    )] = ResponseFormat.JSON,
) -> dict[str, Any] | str:
    """Search the LG webOS TV documentation cache using full-text search.

    Supports FTS5 MATCH syntax: prefix search (webostv*), phrase search ("web app"),
    dot-notation terms (webOSTV.js, com.webos.service.audio).

    Args:
        query (str): Search terms (e.g. 'webOSTV.js launch', 'Luna service')
        limit (int): Max results (1–100, default 10)
        offset (int): Pagination offset (default 0)
        response_format (str): 'json' (default) or 'markdown'

    Returns:
        JSON (default):
        {
            "results": [{"path": str, "section": str, "title": str,
                         "snippet": str, "rank": float, "crawled_at": str}],
            "count": int,
            "offset": int,
            "has_more": bool
        }
        On no results: {"results": [], "count": 0, "offset": int,
                        "has_more": false, "message": str}
        Markdown: Formatted text with result list and pagination info.

    Examples:
        - Use when: searching for APIs → query='webOSTV.js'
        - Use when: looking for lifecycle events → query='launch lifecycle'
        - Don't use when: you have a specific path (use lg_get_page instead)
    """
    if not query or not query.strip():
        raise ValueError(
            "Query cannot be empty or whitespace only. Provide search terms, e.g. 'webOSTV.js'."
        )
    logger.debug("lg_search_docs query=%r limit=%d offset=%d", query, limit, offset)
    try:
        raw = db.search_docs(query, limit=limit + offset + 1)
    except Exception as e:
        raise RuntimeError(
            f"Search failed: {e}. Check FTS5 MATCH syntax (e.g. avoid unmatched quotes)."
        ) from e

    if not raw[offset: offset + limit]:
        total = db.get_stats()["total_pages"]
        if total == 0:
            hint = (
                f"No results for '{query}'. "
                "The cache is empty — run lg_refresh_cache() to crawl the docs."
            )
        else:
            hint = f"No results for '{query}'. Try a different search term or broaden your query."
        return _build_search_response(raw, limit, offset, query, response_format, hint)

    return _build_search_response(raw, limit, offset, query, response_format)


@mcp.tool(
    name="lg_search_by_section",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def lg_search_by_section(
    section: Annotated[str, Field(
        description=(
            "Documentation section to search within (e.g. 'develop', 'faq', 'other'). "
            "Sub-sections 'references', 'guides', 'tools', 'samples' are also accepted."
        ),
        min_length=1,
    )],
    query: Annotated[str, Field(
        description="Search terms (supports FTS5 MATCH syntax).",
        min_length=1,
    )],
    limit: Annotated[int, Field(
        description="Maximum number of results to return (1–100).",
        ge=1,
        le=100,
    )] = 10,
    offset: Annotated[int, Field(
        description="Number of results to skip for pagination.",
        ge=0,
    )] = 0,
    response_format: Annotated[ResponseFormat, Field(
        description="Output format: 'json' or 'markdown'.",
    )] = ResponseFormat.JSON,
) -> dict[str, Any] | str:
    """Search LG webOS TV docs filtered by section (e.g. 'develop', 'faq').

    Top-level sections (use lg_list_sections() for the authoritative list): 'develop', 'faq',
    'news', 'more', 'notice', 'distribute', 'other'.
    Sub-sections also accepted: 'references', 'guides', 'tools', 'samples'
    (these map to path prefixes under /develop/).
    Note: 'other' groups pages at unrecognized top-level paths (e.g. /privacy-policy, /).

    Args:
        section (str): Section name (e.g. 'develop', 'references', 'guides')
        query (str): Search terms (supports FTS5 MATCH syntax)
        limit (int): Max results (1–100, default 10)
        offset (int): Pagination offset (default 0)
        response_format (str): 'json' (default) or 'markdown'

    Returns:
        JSON (default):
        {
            "results": [{"path": str, "section": str, "title": str,
                         "snippet": str, "rank": float, "crawled_at": str}],
            "count": int,
            "offset": int,
            "has_more": bool
        }
        On no results: {"results": [], "count": 0, "offset": int,
                        "has_more": false, "message": str}
        Markdown: Formatted text with results.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty.")
    if not section or not section.strip():
        raise ValueError("Section cannot be empty. Use lg_list_sections() to see available sections.")
    logger.debug(
        "lg_search_by_section section=%r query=%r limit=%d offset=%d",
        section, query, limit, offset,
    )
    normalized = section.lower()
    fetch_limit = limit + offset + 1
    try:
        if normalized in _SUBSECTION_ALIASES:
            raw = db.search_docs_by_path_prefix(
                query, _SUBSECTION_ALIASES[normalized], limit=fetch_limit
            )
        else:
            raw = db.search_docs_by_section(query, section, limit=fetch_limit)
    except Exception as e:
        raise RuntimeError(f"Search failed: {e}. Check FTS5 MATCH syntax.") from e

    no_results_hint: str | None = None
    if not raw[offset: offset + limit]:
        is_known = normalized in _SUBSECTION_ALIASES or normalized in _KNOWN_TOP_SECTIONS
        if is_known:
            no_results_hint = (
                f"No results for '{query}' in section '{section}'. "
                "Try a different search term or broaden your query."
            )
        else:
            no_results_hint = (
                f"No results for '{query}'. Unknown section '{section}' — "
                "use lg_list_sections() to see available sections."
            )

    return _build_search_response(raw, limit, offset, query, response_format, no_results_hint)


@mcp.tool(
    name="lg_get_page",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def lg_get_page(
    path: Annotated[str, Field(
        description=(
            "The page path, e.g. '/develop/web-app-development/using-webostv.js'. "
            "Partial paths are matched with a fuzzy fallback."
        ),
        min_length=1,
    )],
) -> dict[str, Any]:
    """Retrieve the full markdown content of a cached LG webOS TV docs page.

    Args:
        path (str): The page path. Partial paths are matched with a fuzzy fallback.

    Returns:
        Dict with url, path, section, title, content, crawled_at fields.
        Fuzzy-matched results include '_fuzzy_match: true' to indicate the returned
        path differs from the requested one.
        If not found: {"message": "Page not found for path '...' ..."}

    Examples:
        - Use when: you have a path from search results
        - Don't use when: you don't know the path (use lg_search_docs first)
    """
    logger.debug("lg_get_page path=%r", path)
    page = db.get_page(path)
    if page:
        return page
    page = db.get_page_fuzzy(path)
    if page:
        page["_fuzzy_match"] = True
        return page
    page = db.get_page_by_path_keywords(path)
    if page:
        page["_fuzzy_match"] = True
        return page
    return {
        "message": (
            f"Page not found for path '{path}'. "
            "Try lg_search_docs() to find the correct path."
        )
    }


@mcp.tool(
    name="lg_list_sections",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def lg_list_sections() -> dict[str, Any]:
    """List all cached documentation sections with page counts.

    Returns:
        Dict with:
        - sections (list): [{"section": str, "page_count": int}, ...]
        - last_crawled (str): ISO datetime of last crawl or "never"
    """
    logger.debug("lg_list_sections called")
    sections = db.list_sections()
    last_crawled = db.get_cache_meta("last_crawled")
    return {
        "sections": sections,
        "last_crawled": last_crawled or "never",
    }


@mcp.tool(
    name="lg_get_stats",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def lg_get_stats() -> dict[str, Any]:
    """Return database statistics: total pages, size, section breakdown, last crawl date.

    Returns:
        Dict with:
        - total_pages (int): Number of indexed pages
        - db_size_mb (float): Database size in MB
        - sections (list): Section breakdown with page counts
        - last_crawled (str): ISO datetime of last crawl or "never"
        - days_since_crawl (int | None): Days since last crawl
        - data_freshness (str): "fresh", "aging", "stale", "very_stale", or "unknown"
    """
    logger.debug("lg_get_stats called")
    return db.get_stats()


@mcp.tool(
    name="lg_refresh_cache",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
async def lg_refresh_cache(
    max_depth: Annotated[int, Field(
        description="BFS depth limit (1–20).",
        ge=1,
        le=20,
    )] = 10,
    max_pages: Annotated[int, Field(
        description="Maximum pages to crawl (1–10000).",
        ge=1,
        le=10000,
    )] = 2000,
    resume: Annotated[bool, Field(
        description=(
            "If True, skip URLs already in the DB — useful to resume an interrupted "
            "crawl without re-fetching pages that were already successfully indexed."
        ),
    )] = False,
    ctx: "Context[Any, Any, Any] | None" = None,
) -> dict[str, Any]:
    """Re-crawl webostv.developer.lge.com and refresh the local documentation cache.

    This tool runs a full BFS crawl of the LG webOS TV developer docs site.
    It uses crawl4ai with Playwright (headless Chromium) because the site
    is a JavaScript SPA. The first crawl may take 15–30 minutes.

    Args:
        max_depth (int): BFS depth limit (1–20, default 10)
        max_pages (int): Maximum pages to crawl (1–10000, default 2000)
        resume (bool): Skip URLs already in DB to resume an interrupted crawl (default False)

    Returns:
        Dict with:
        - status (str): "success" or "error"
        - saved (int): Pages saved/updated (on success)
        - skipped (int): Pages skipped as unchanged (on success)
        - error (str): Failure reason (on error)
    """
    from lg_docs_mcp import scraper

    if ctx is not None:
        await ctx.report_progress(0, message="Starting crawl of webostv.developer.lge.com...")
    logger.info("lg_refresh_cache max_depth=%d max_pages=%d resume=%s", max_depth, max_pages, resume)

    def _run_crawl() -> dict[str, Any]:
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
            future = executor.submit(scraper.crawl_docs_sync, max_depth, max_pages, resume)
            return future.result(timeout=7200)

    loop = asyncio.get_event_loop()
    try:
        result: dict[str, Any] = await loop.run_in_executor(None, _run_crawl)
    except concurrent.futures.TimeoutError:
        return {
            "status": "error",
            "error": "Crawl timed out after 2 hours. The process may still be running.",
        }

    if ctx is not None:
        await ctx.report_progress(
            1.0,
            message=(
                f"Crawl complete: {result.get('saved', 0)} pages saved, "
                f"{result.get('skipped', 0)} skipped."
            ),
        )
    logger.info("lg_refresh_cache complete: %s", result)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="lg-docs-mcp",
        description="LG webOS TV developer docs MCP server and CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the MCP server (default)")
    serve_parser.add_argument(
        "--auto-refresh",
        action="store_true",
        default=False,
        help="Enable background auto-refresh daemon (env: LG_DOCS_AUTO_REFRESH=1)",
    )
    serve_parser.add_argument(
        "--auto-refresh-days",
        type=int,
        default=int(os.getenv("LG_DOCS_AUTO_REFRESH_DAYS", "7")),
        help="Days before cache is considered stale (default: 7, env: LG_DOCS_AUTO_REFRESH_DAYS)",
    )
    serve_parser.add_argument(
        "--check-interval-hours",
        type=int,
        default=int(os.getenv("LG_DOCS_CHECK_INTERVAL_HOURS", "24")),
        help="Hours between staleness checks (default: 24, env: LG_DOCS_CHECK_INTERVAL_HOURS)",
    )

    crawl_parser = subparsers.add_parser("crawl", help="Crawl and refresh the docs cache")
    crawl_parser.add_argument("--max-depth", type=int, default=10)
    crawl_parser.add_argument("--max-pages", type=int, default=2000)
    crawl_parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Skip URLs already in the DB (resume an interrupted crawl)",
    )

    search_parser = subparsers.add_parser("search", help="Search the docs cache")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("stats", help="Show cache statistics")
    subparsers.add_parser("check", help="Check cache freshness; exits 0=fresh, 1=stale")
    subparsers.add_parser("clean", help="Strip breadcrumbs and footer from all cached pages")

    args = parser.parse_args()

    db.init_db()

    if args.command == "crawl":
        from lg_docs_mcp import scraper
        result = scraper.crawl_docs_sync(
            max_depth=args.max_depth,
            max_pages=args.max_pages,
            resume=args.resume,
        )
        print(f"saved={result['saved']} skipped={result['skipped']}")
    elif args.command == "search":
        import json
        results = db.search_docs(args.query, limit=args.limit)
        print(json.dumps(results, indent=2))
    elif args.command == "stats":
        import json
        print(json.dumps(db.get_stats(), indent=2))
    elif args.command == "clean":
        updated = db.migrate_clean_content()
        print(f"Updated {updated} pages.")
    elif args.command == "check":
        stats = db.get_stats()
        days = stats.get("days_since_crawl")
        print(f"Last crawled : {stats.get('last_crawled', 'never')}")
        print(f"Days since   : {days if days is not None else 'unknown'}")
        print(f"Freshness    : {stats.get('data_freshness', 'unknown')}")
        if checker.is_stale(7):
            print("Status       : STALE — run: lg-docs-mcp crawl")
            sys.exit(1)
        else:
            print("Status       : FRESH")
            sys.exit(0)
    else:
        # Default: serve (or bare invocation with no subcommand)
        if getattr(args, "auto_refresh", False):
            checker.start_auto_refresh_daemon(
                check_interval_hours=getattr(args, "check_interval_hours", 24),
                max_age_days=getattr(args, "auto_refresh_days", 7),
            )
        mcp.run()


if __name__ == "__main__":
    main()
