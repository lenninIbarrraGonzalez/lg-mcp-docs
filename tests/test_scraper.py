"""Unit tests for lg_docs_mcp.scraper module."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _isolated_db(tmp_path: Path) -> None:
    """Point the DB module to a fresh temp DB and open a new connection."""
    import lg_docs_mcp.db as db_mod

    db_mod.DB_PATH = tmp_path / "docs.db"
    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.init_db()


def _restore_db(original_path: Path) -> None:
    import lg_docs_mcp.db as db_mod

    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.DB_PATH = original_path


# ---------------------------------------------------------------------------
# url_to_path
# ---------------------------------------------------------------------------

class TestUrlToPath:
    def test_returns_path(self) -> None:
        from lg_docs_mcp.scraper import url_to_path
        assert url_to_path("https://webostv.developer.lge.com/develop/getting-started") == "/develop/getting-started"

    def test_deep_path(self) -> None:
        from lg_docs_mcp.scraper import url_to_path
        url = "https://webostv.developer.lge.com/develop/web-app-development/using-webostv.js"
        assert url_to_path(url) == "/develop/web-app-development/using-webostv.js"

    def test_root_path(self) -> None:
        from lg_docs_mcp.scraper import url_to_path
        assert url_to_path("https://webostv.developer.lge.com/") == "/"

    def test_query_string_excluded(self) -> None:
        from lg_docs_mcp.scraper import url_to_path
        result = url_to_path("https://webostv.developer.lge.com/develop/test?q=1")
        assert result == "/develop/test"
        assert "?" not in result

    def test_references_path(self) -> None:
        from lg_docs_mcp.scraper import url_to_path
        url = "https://webostv.developer.lge.com/references/webostv.js/luna-service-api"
        assert url_to_path(url) == "/references/webostv.js/luna-service-api"


# ---------------------------------------------------------------------------
# url_to_section
# ---------------------------------------------------------------------------

class TestUrlToSection:
    def test_extracts_develop_section(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/develop/getting-started") == "develop"

    def test_extracts_references_section(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/references/webostv.js/api") == "references"

    def test_extracts_guides_section(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/guides/setup/setup-environment") == "guides"

    def test_extracts_distribute_section(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/distribute/app-submission") == "distribute"

    def test_root_path_returns_other(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/") == "other"

    def test_empty_path_returns_other(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com") == "other"

    def test_unknown_section_returns_other(self) -> None:
        from lg_docs_mcp.scraper import url_to_section
        assert url_to_section("https://webostv.developer.lge.com/privacy-policy/terms") == "other"


# ---------------------------------------------------------------------------
# extract_title
# ---------------------------------------------------------------------------

class TestExtractTitle:
    def test_extracts_h1(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("# My Page Title\n\nSome content.") == "My Page Title"

    def test_returns_empty_if_no_h1(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("## Secondary heading\n\nContent.") == ""

    def test_empty_string_returns_empty(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("") == ""

    def test_first_h1_wins(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("# First Title\n\n# Second Title") == "First Title"

    def test_strips_surrounding_whitespace(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("#   Padded Title  \n\nContent") == "Padded Title"

    def test_hash_without_space_not_matched(self) -> None:
        from lg_docs_mcp.scraper import extract_title
        assert extract_title("#NoSpace\n\nContent") == ""


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_returns_16_char_hex(self) -> None:
        from lg_docs_mcp.scraper import content_hash
        h = content_hash("some content")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        from lg_docs_mcp.scraper import content_hash
        assert content_hash("hello") == content_hash("hello")

    def test_different_inputs_differ(self) -> None:
        from lg_docs_mcp.scraper import content_hash
        assert content_hash("hello") != content_hash("world")

    def test_empty_string(self) -> None:
        from lg_docs_mcp.scraper import content_hash
        h = content_hash("")
        assert len(h) == 16


# ---------------------------------------------------------------------------
# clean_content
# ---------------------------------------------------------------------------

class TestCleanContent:
    def test_collapses_excess_newlines(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        assert clean_content("line1\n\n\n\nline2") == "line1\n\nline2"

    def test_strips_leading_trailing_whitespace(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        assert clean_content("  content  ") == "content"

    def test_preserves_double_newlines(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        assert clean_content("line1\n\nline2") == "line1\n\nline2"

    def test_empty_string(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        assert clean_content("") == ""

    def test_strips_breadcrumbs_before_h1(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        text = "1. [HOME](https://webostv.developer.lge.com/)\n  2. [DEVELOP](...)\n\n# My Page\n\nContent here."
        assert clean_content(text) == "# My Page\n\nContent here."

    def test_strips_lg_footer(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        text = "# My Page\n\nContent here.\n\n![LG Electronics Logo](https://example.com/logo.png)\n* [TERMS](https://example.com)\n\nCopyright © 2026 LG Electronics."
        assert clean_content(text) == "# My Page\n\nContent here."

    def test_strips_breadcrumbs_and_footer_together(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        text = "1. [HOME](...)\n  2. [DEVELOP](...)\n\n# Real Title\n\nActual content.\n\n![LG Electronics Logo](...)\nCopyright © 2026 LG Electronics."
        assert clean_content(text) == "# Real Title\n\nActual content."

    def test_no_breadcrumbs_unchanged(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        text = "# My Page\n\nContent here."
        assert clean_content(text) == "# My Page\n\nContent here."

    def test_no_footer_unchanged(self) -> None:
        from lg_docs_mcp.scraper import clean_content
        text = "# My Page\n\nContent without footer."
        assert clean_content(text) == "# My Page\n\nContent without footer."


# ---------------------------------------------------------------------------
# extract_content_from_html
# ---------------------------------------------------------------------------

class TestExtractContentFromHtml:
    def test_extracts_matching_main(self) -> None:
        from lg_docs_mcp.scraper import extract_content_from_html
        html = "<main><p>Hello webOS</p></main>"
        with patch("lg_docs_mcp.scraper.html_to_markdown", return_value="Hello webOS"):
            result = extract_content_from_html(html)
        assert result == "Hello webOS"

    def test_returns_empty_if_no_selector_match(self) -> None:
        from lg_docs_mcp.scraper import extract_content_from_html
        html = "<div><p>No matching selector</p></div>"
        with patch("lg_docs_mcp.scraper.CONTENT_SELECTOR", "article"):
            result = extract_content_from_html(html)
        assert result == ""

    def test_returns_empty_for_empty_html(self) -> None:
        from lg_docs_mcp.scraper import extract_content_from_html
        result = extract_content_from_html("")
        assert result == ""


# ---------------------------------------------------------------------------
# html_to_markdown
# ---------------------------------------------------------------------------

class TestHtmlToMarkdown:
    def test_returns_string(self) -> None:
        from lg_docs_mcp.scraper import html_to_markdown
        mock_result = MagicMock()
        mock_result.raw_markdown = "# Hello"
        mock_gen = MagicMock()
        mock_gen.generate_markdown.return_value = mock_result
        with patch("lg_docs_mcp.scraper._get_md_generator", return_value=mock_gen):
            result = html_to_markdown("<h1>Hello</h1>")
        assert result == "# Hello"

    def test_fallback_when_no_raw_markdown_attr(self) -> None:
        from lg_docs_mcp.scraper import html_to_markdown
        mock_gen = MagicMock()
        mock_gen.generate_markdown.return_value = "plain string result"
        with patch("lg_docs_mcp.scraper._get_md_generator", return_value=mock_gen):
            result = html_to_markdown("<p>hi</p>")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# crawl_docs_sync
# ---------------------------------------------------------------------------

class TestCrawlDocsSync:
    def test_returns_saved_skipped_dict(self) -> None:
        from lg_docs_mcp.scraper import crawl_docs_sync
        expected = {"saved": 5, "skipped": 2}
        with patch("lg_docs_mcp.scraper.crawl_docs", new=AsyncMock(return_value=expected)):
            result = crawl_docs_sync(max_depth=1, max_pages=10)
        assert result == expected

    def test_passes_parameters_through(self) -> None:
        from lg_docs_mcp.scraper import crawl_docs_sync
        mock_crawl = AsyncMock(return_value={"saved": 0, "skipped": 0})
        with patch("lg_docs_mcp.scraper.crawl_docs", new=mock_crawl):
            crawl_docs_sync(max_depth=3, max_pages=50)
        mock_crawl.assert_called_once_with(max_depth=3, max_pages=50, resume=False)


# ---------------------------------------------------------------------------
# crawl_docs (async, mocked crawler)
# ---------------------------------------------------------------------------

def _make_mock_crawler(results: list[object]) -> MagicMock:
    """Build an AsyncWebCrawler mock that yields the given results from arun()."""
    mock_crawler = MagicMock()
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=None)
    mock_crawler.arun = AsyncMock(return_value=results)
    return mock_crawler


def _mock_crawl4ai(monkeypatch: pytest.MonkeyPatch, crawler: MagicMock) -> None:
    """Inject crawl4ai mocks into sys.modules so inner imports in crawl_docs resolve."""
    import sys
    import types

    bfs_mod = types.ModuleType("crawl4ai.deep_crawling")
    bfs_mod.BFSDeepCrawlStrategy = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    filters_mod = types.ModuleType("crawl4ai.deep_crawling.filters")
    filters_mod.FilterChain = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    filters_mod.URLPatternFilter = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    crawl4ai_mod = types.ModuleType("crawl4ai")
    crawl4ai_mod.AsyncWebCrawler = MagicMock(return_value=crawler)  # type: ignore[attr-defined]
    crawl4ai_mod.BrowserConfig = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    crawl4ai_mod.CrawlerRunConfig = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "crawl4ai", crawl4ai_mod)
    monkeypatch.setitem(sys.modules, "crawl4ai.deep_crawling", bfs_mod)
    monkeypatch.setitem(sys.modules, "crawl4ai.deep_crawling.filters", filters_mod)


class TestCrawlDocs:
    def test_skips_failed_results(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            failed = MagicMock()
            failed.success = False

            crawler = _make_mock_crawler([failed])
            _mock_crawl4ai(monkeypatch, crawler)

            from lg_docs_mcp.scraper import crawl_docs
            result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            assert result["saved"] == 0
            assert result["skipped"] >= 1
        finally:
            _restore_db(original)

    def test_skips_empty_markdown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            empty = MagicMock()
            empty.success = True
            empty.url = "https://webostv.developer.lge.com/develop/getting-started"
            empty.html = ""
            empty.markdown = MagicMock()
            empty.markdown.raw_markdown = "   "

            crawler = _make_mock_crawler([empty])
            _mock_crawl4ai(monkeypatch, crawler)

            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value="   "):
                from lg_docs_mcp.scraper import crawl_docs
                result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            assert result["saved"] == 0
            assert result["skipped"] >= 1
        finally:
            _restore_db(original)

    def test_skips_oversized_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            big = MagicMock()
            big.success = True
            big.url = "https://webostv.developer.lge.com/develop/getting-started"

            crawler = _make_mock_crawler([big])
            _mock_crawl4ai(monkeypatch, crawler)

            oversized = "x" * 600_000
            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value=oversized):
                with patch("lg_docs_mcp.scraper.MAX_CONTENT_SIZE", 500_000):
                    from lg_docs_mcp.scraper import crawl_docs
                    result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            assert result["saved"] == 0
            assert result["skipped"] >= 1
        finally:
            _restore_db(original)

    def test_saves_valid_page(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            item = MagicMock()
            item.success = True
            item.url = "https://webostv.developer.lge.com/develop/web-app-development/using-webostv.js"

            crawler = _make_mock_crawler([item])
            _mock_crawl4ai(monkeypatch, crawler)

            valid_md = "# webOSTV.js\n\nThis is the webOSTV.js API documentation."
            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value=valid_md):
                from lg_docs_mcp.scraper import crawl_docs
                result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            assert result["saved"] == 1
            assert result["skipped"] == 0
            page = db_mod.get_page("/develop/web-app-development/using-webostv.js")
            assert page is not None
            assert page["title"] == "webOSTV.js"
        finally:
            _restore_db(original)

    def test_skips_unchanged_page(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            from lg_docs_mcp.scraper import clean_content, content_hash
            md = "# Getting Started\n\nSome content."
            cleaned = clean_content(md)
            h = content_hash(cleaned)
            db_mod.upsert_doc(
                url="https://webostv.developer.lge.com/develop/getting-started",
                path="/develop/getting-started",
                section="develop",
                title="Getting Started",
                content=cleaned,
                content_hash=h,
            )

            item = MagicMock()
            item.success = True
            item.url = "https://webostv.developer.lge.com/develop/getting-started"

            crawler = _make_mock_crawler([item])
            _mock_crawl4ai(monkeypatch, crawler)

            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value=md):
                from lg_docs_mcp.scraper import crawl_docs
                result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            assert result["saved"] == 0
            assert result["skipped"] == 1
        finally:
            _restore_db(original)

    def test_page_exception_counted_as_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            item = MagicMock()
            item.success = True
            item.url = "https://webostv.developer.lge.com/develop/broken-page"

            crawler = _make_mock_crawler([item])
            _mock_crawl4ai(monkeypatch, crawler)

            with patch("lg_docs_mcp.scraper._get_markdown_text", side_effect=RuntimeError("boom")):
                from lg_docs_mcp.scraper import crawl_docs
                result = asyncio.run(crawl_docs(max_depth=1, max_pages=5))

            # Exception should not propagate; page counted as skipped
            assert result["saved"] == 0
            assert result["skipped"] >= 1
        finally:
            _restore_db(original)


# ---------------------------------------------------------------------------
# ResumeFilter
# ---------------------------------------------------------------------------

class TestResumeFilter:
    def test_allows_new_url(self) -> None:
        from lg_docs_mcp.scraper import ResumeFilter
        f = ResumeFilter({"https://webostv.developer.lge.com/develop/page1"})
        assert f.apply("https://webostv.developer.lge.com/develop/page2") is True

    def test_blocks_known_url(self) -> None:
        from lg_docs_mcp.scraper import ResumeFilter
        known = "https://webostv.developer.lge.com/develop/page1"
        f = ResumeFilter({known})
        assert f.apply(known) is False

    def test_empty_set_allows_all(self) -> None:
        from lg_docs_mcp.scraper import ResumeFilter
        f = ResumeFilter(set())
        assert f.apply("https://webostv.developer.lge.com/develop/anything") is True

    def test_name_attribute(self) -> None:
        from lg_docs_mcp.scraper import ResumeFilter
        f = ResumeFilter(set())
        assert f.name == "ResumeFilter"


# ---------------------------------------------------------------------------
# crawl_docs resume mode
# ---------------------------------------------------------------------------

class TestCrawlDocsResume:
    def test_resume_with_existing_url_skips_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When resume=True, a URL already in the DB is passed to ResumeFilter
        and crawl4ai should not process it (it's excluded from the filter chain)."""
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            existing_url = "https://webostv.developer.lge.com/develop/already-done"
            db_mod.upsert_doc(
                url=existing_url,
                path="/develop/already-done",
                section="develop",
                title="Already Done",
                content="Content already indexed.",
                content_hash="abc123",
            )

            # Verify ResumeFilter blocks the existing URL
            from lg_docs_mcp.scraper import ResumeFilter
            urls = db_mod.get_all_urls()
            f = ResumeFilter(urls)
            assert f.apply(existing_url) is False
            assert f.apply("https://webostv.developer.lge.com/develop/new-page") is True
        finally:
            _restore_db(original)

    def test_resume_false_does_not_add_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When resume=False (default), get_all_urls is never called."""
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            item = MagicMock()
            item.success = True
            item.url = "https://webostv.developer.lge.com/develop/page0"

            crawler = _make_mock_crawler([item])
            _mock_crawl4ai(monkeypatch, crawler)

            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value="# Title\n\nContent."), \
                 patch("lg_docs_mcp.db.get_all_urls") as mock_get_urls:
                from lg_docs_mcp.scraper import crawl_docs
                asyncio.run(crawl_docs(max_depth=1, max_pages=5, resume=False))

            mock_get_urls.assert_not_called()
        finally:
            _restore_db(original)

    def test_resume_true_calls_get_all_urls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When resume=True, get_all_urls() is called to build the exclusion set."""
        import lg_docs_mcp.db as db_mod
        original = db_mod.DB_PATH
        _isolated_db(tmp_path)
        try:
            item = MagicMock()
            item.success = True
            item.url = "https://webostv.developer.lge.com/develop/page0"

            crawler = _make_mock_crawler([item])
            _mock_crawl4ai(monkeypatch, crawler)

            with patch("lg_docs_mcp.scraper._get_markdown_text", return_value="# Title\n\nContent."), \
                 patch("lg_docs_mcp.db.get_all_urls", return_value=set()) as mock_get_urls:
                from lg_docs_mcp.scraper import crawl_docs
                asyncio.run(crawl_docs(max_depth=1, max_pages=5, resume=True))

            mock_get_urls.assert_called_once()
        finally:
            _restore_db(original)
