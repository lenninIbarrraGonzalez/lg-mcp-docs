import asyncio
import functools
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from lg_docs_mcp import db

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _get_md_generator() -> Any:
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    return DefaultMarkdownGenerator()


START_URL = os.getenv(
    "LG_DOCS_START_URL",
    "https://webostv.developer.lge.com",  # root: BFS discovers all sections including /distribute
)
_EXTRA_START_URLS: list[str] = [
    u.strip()
    for u in os.getenv("LG_DOCS_EXTRA_START_URLS", "").split(",")
    if u.strip()
]
URL_PATTERN = os.getenv("LG_DOCS_URL_PATTERN", "*webostv.developer.lge.com*")
CONTENT_SELECTOR = os.getenv("LG_DOCS_CONTENT_SELECTOR", "main")
MAX_CONTENT_SIZE = int(os.getenv("LG_DOCS_MAX_CONTENT_SIZE", str(500_000)))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def url_to_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path


_KNOWN_SECTIONS = frozenset({
    "develop", "distribute", "faq", "news", "more", "notice",
    "references", "guides", "tools", "samples",
})


def url_to_section(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if parts:
        first = parts[0]
        return first if first in _KNOWN_SECTIONS else "other"
    return "other"


def extract_title(markdown: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def clean_content(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip breadcrumb navigation (everything before the first H1 heading)
    h1_match = re.search(r'^# .+', text, re.MULTILINE)
    if h1_match and h1_match.start() > 0:
        text = text[h1_match.start():]
    # Strip LG footer (LG logo image and everything after it)
    footer_match = re.search(r'\n!\[LG Electronics Logo\]', text)
    if footer_match:
        text = text[:footer_match.start()]
    return text.strip()


def html_to_markdown(html: str) -> str:
    """Convert HTML fragment to markdown using crawl4ai's generator."""
    gen = _get_md_generator()
    result = gen.generate_markdown(
        input_html=html,
        base_url="https://webostv.developer.lge.com",
    )
    if hasattr(result, "raw_markdown"):
        return result.raw_markdown or ""
    return str(result)


def extract_content_from_html(html: str) -> str:
    """Extract doc body from raw HTML using BeautifulSoup, then convert to markdown."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.select_one(CONTENT_SELECTOR)
    if content_div:
        return html_to_markdown(str(content_div))
    return ""


def _get_markdown_text(result: object) -> str:
    """Extract clean markdown from a crawl4ai result using a 3-level fallback chain.

    1. CSS selector (preferred) — crawl4ai renders the SPA with Playwright; we then use
       BeautifulSoup to extract the CONTENT_SELECTOR element from the rendered HTML and
       convert it to markdown via crawl4ai's DefaultMarkdownGenerator. This gives the
       cleanest, most focused output (nav, headers, footers stripped).
    2. crawl4ai raw markdown (fallback) — if CSS extraction yields nothing (e.g. the
       selector didn't match), use the markdown attribute that crawl4ai already produced
       from the full page HTML.
    3. Empty string (last resort) — returned when markdown is None or blank; the caller
       treats an empty result as a page to skip (increments skipped counter).
    """
    html = getattr(result, "html", None) or ""
    if html:
        extracted = extract_content_from_html(html)
        if extracted.strip():
            return extracted

    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    if hasattr(md, "raw_markdown"):
        return md.raw_markdown or ""
    return str(md)


class ResumeFilter:
    """crawl4ai URL filter that skips URLs already stored in the DB.

    Enables resuming an interrupted crawl without re-fetching pages that were
    successfully saved in a previous (possibly incomplete) run.
    """

    def __init__(self, already_crawled: set[str]) -> None:
        self._already_crawled = already_crawled
        self.name = "ResumeFilter"

    def apply(self, url: str) -> bool:
        """Return True (allow) if the URL is not yet in the DB, False (skip) if it is."""
        return url not in self._already_crawled


def _process_crawl_results(container: object, saved: int, skipped: int) -> tuple[int, int]:
    """Process a crawl4ai result container, upsert pages to DB, return updated counters."""
    for result in container:  # type: ignore[attr-defined]
        try:
            if not result.success:
                skipped += 1
                continue

            url = result.url
            markdown = _get_markdown_text(result)
            if not markdown.strip():
                skipped += 1
                continue

            if len(markdown) > MAX_CONTENT_SIZE:
                logger.warning("Skipping %s: content too large (%d bytes)", url, len(markdown))
                skipped += 1
                continue

            markdown = clean_content(markdown)
            path = url_to_path(url)
            new_hash = content_hash(markdown)

            existing_hash = db.get_page_hash(path)
            if existing_hash == new_hash:
                logger.debug("Skipped (unchanged): %s", path)
                skipped += 1
                continue

            existing_path = db.get_path_by_hash(new_hash)
            if existing_path is not None and existing_path != path:
                logger.debug("Skipped duplicate: %s (same content as %s)", path, existing_path)
                skipped += 1
                continue

            title = extract_title(markdown)
            section = url_to_section(url)

            db.upsert_doc(url, path, section, title, markdown, new_hash)
            logger.debug("Saved: %s", path)
            saved += 1
            if (saved + skipped) % 100 == 0:
                logger.info(
                    "Crawl progress: processed=%d saved=%d skipped=%d",
                    saved + skipped, saved, skipped,
                )
        except Exception:
            logger.warning(
                "Error processing page %s", getattr(result, "url", "?"), exc_info=True
            )
            skipped += 1
    return saved, skipped


async def crawl_docs(max_depth: int = 10, max_pages: int = 2000, resume: bool = False) -> dict[str, int]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter

    db.init_db()
    logger.info("Starting LG webOS TV docs crawl (max_depth=%d, max_pages=%d)", max_depth, max_pages)

    filters: list[ResumeFilter | URLPatternFilter] = [URLPatternFilter(patterns=[URL_PATTERN])]
    if resume:
        already_crawled = db.get_all_urls()
        if already_crawled:
            logger.info("resume mode: skipping %d already-crawled URLs", len(already_crawled))
            filters.append(ResumeFilter(already_crawled))
        else:
            logger.info("resume mode: DB is empty, performing full crawl")
    filter_chain = FilterChain(filters=filters)
    strategy = BFSDeepCrawlStrategy(
        max_depth=max_depth,
        max_pages=max_pages,
        filter_chain=filter_chain,
    )

    browser_cfg = BrowserConfig(headless=True)
    run_cfg = CrawlerRunConfig(
        deep_crawl_strategy=strategy,
        wait_until="networkidle",
        page_timeout=30000,
    )

    saved = 0
    skipped = 0

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        container = await asyncio.wait_for(
            crawler.arun(START_URL, config=run_cfg),
            timeout=7200,  # 2-hour global limit; prevents hang on redirect loops
        )
        saved, skipped = _process_crawl_results(container, saved, skipped)

        for extra_url in _EXTRA_START_URLS:
            logger.info("Crawling extra start URL: %s", extra_url)
            extra_strategy = BFSDeepCrawlStrategy(
                max_depth=max_depth,
                max_pages=max_pages,
                filter_chain=filter_chain,
            )
            extra_run_cfg = CrawlerRunConfig(
                deep_crawl_strategy=extra_strategy,
                wait_until="networkidle",
                page_timeout=30000,
            )
            try:
                extra_container = await asyncio.wait_for(
                    crawler.arun(extra_url, config=extra_run_cfg),
                    timeout=3600,
                )
                saved, skipped = _process_crawl_results(extra_container, saved, skipped)
            except Exception:
                logger.warning("Error crawling extra URL %s", extra_url, exc_info=True)

    db.set_cache_meta("last_crawled", datetime.now(timezone.utc).isoformat())
    logger.info("Crawl complete: saved=%d, skipped=%d", saved, skipped)

    return {"saved": saved, "skipped": skipped}


def crawl_docs_sync(max_depth: int = 10, max_pages: int = 2000, resume: bool = False) -> dict[str, int]:
    return asyncio.run(crawl_docs(max_depth=max_depth, max_pages=max_pages, resume=resume))
