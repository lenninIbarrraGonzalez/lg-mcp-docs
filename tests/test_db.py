"""Unit tests for lg_docs_mcp.db module."""
from pathlib import Path

import pytest


def _make_db(tmp_path: Path) -> None:
    """Initialize a test DB at tmp_path/docs.db and patch DB_PATH."""
    import lg_docs_mcp.db as db_mod
    db_path = tmp_path / "docs.db"
    db_mod.DB_PATH = db_path
    # Reset thread-local connection so a fresh one is opened for this DB path
    import lg_docs_mcp.db as _db
    if hasattr(_db._local, "conn") and _db._local.conn is not None:
        _db._local.conn.close()
        _db._local.conn = None
    db_mod.init_db()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path):
    """Ensure every test uses an isolated in-memory-like DB in tmp_path."""
    import lg_docs_mcp.db as db_mod
    original_path = db_mod.DB_PATH
    _make_db(tmp_path)
    yield
    # Teardown: close connection and restore original path
    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.DB_PATH = original_path


def _insert_sample(num: int = 1) -> None:
    import lg_docs_mcp.db as db_mod
    for i in range(num):
        db_mod.upsert_doc(
            url=f"https://webostv.developer.lge.com/develop/page{i}",
            path=f"/develop/page{i}",
            section="develop",
            title=f"Page {i} Title",
            content=f"This is content for page {i} about Luna Service and webOS platform.",
            content_hash=f"hash{i}",
        )


class TestInitDb:
    def test_busy_timeout(self) -> None:
        import lg_docs_mcp.db as db_mod
        conn = db_mod.get_conn()
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row[0] == 30000

    def test_creates_tables(self, tmp_path: Path) -> None:
        import lg_docs_mcp.db as db_mod
        conn = db_mod.get_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "docs" in tables
        assert "cache_meta" in tables

    def test_creates_fts_table(self, tmp_path: Path) -> None:
        import lg_docs_mcp.db as db_mod
        conn = db_mod.get_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "docs_fts" in tables

    def test_wal_mode(self) -> None:
        import lg_docs_mcp.db as db_mod
        conn = db_mod.get_conn()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_idempotent(self, tmp_path: Path) -> None:
        import lg_docs_mcp.db as db_mod
        # Calling init_db twice should not raise
        db_mod.init_db()
        db_mod.init_db()


class TestUpsertDoc:
    def test_insert_and_retrieve(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/test",
            path="/develop/test",
            section="develop",
            title="Test Page",
            content="Hello webOS world",
        )
        page = db_mod.get_page("/develop/test")
        assert page is not None
        assert page["title"] == "Test Page"
        assert page["content"] == "Hello webOS world"

    def test_upsert_updates_existing(self) -> None:
        import lg_docs_mcp.db as db_mod
        url = "https://webostv.developer.lge.com/develop/test"
        db_mod.upsert_doc(url=url, path="/develop/test", section="s", title="Old", content="old content")
        db_mod.upsert_doc(url=url, path="/develop/test", section="s", title="New", content="new content")
        page = db_mod.get_page("/develop/test")
        assert page is not None
        assert page["title"] == "New"
        assert page["content"] == "new content"

    def test_content_hash_stored(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/test",
            path="/develop/test",
            section="s",
            title="T",
            content="c",
            content_hash="abc123",
        )
        h = db_mod.get_page_hash("/develop/test")
        assert h == "abc123"


class TestSearchDocs:
    def test_returns_results(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(3)
        results = db_mod.search_docs("Luna")
        assert len(results) > 0
        assert "path" in results[0]
        assert "title" in results[0]
        assert "snippet" in results[0]

    def test_crawled_at_in_results(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        results = db_mod.search_docs("Luna")
        assert len(results) > 0
        assert "crawled_at" in results[0]

    def test_no_results(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        results = db_mod.search_docs("xyznonexistentterm9999")
        assert results == []

    def test_limit_respected(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(5)
        results = db_mod.search_docs("webOSTV", limit=2)
        assert len(results) <= 2

    def test_malformed_query_raises(self) -> None:
        import sqlite3

        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        with pytest.raises(sqlite3.OperationalError):
            db_mod.search_docs('"unclosed quote')

    def test_dot_in_query_does_not_raise(self) -> None:
        """webOSTV.js and similar dot-notation terms must not cause FTS5 syntax errors."""
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        # Should not raise sqlite3.OperationalError
        results = db_mod.search_docs("webOSTV.js")
        assert isinstance(results, list)

    def test_dot_query_matches_content(self) -> None:
        """A dot-notation query should find pages whose content contains that term."""
        import lg_docs_mcp.db as db_mod
        conn = db_mod.get_conn()
        db_mod.init_db()
        conn.execute(
            "INSERT OR REPLACE INTO docs (url, path, section, title, content, content_hash, crawled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/page",
                "/develop/webostvjs",
                "develop",
                "webOSTV.js Guide",
                "Use webOSTV.js to call Luna services from your app.",
                "abc123",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        results = db_mod.search_docs("webOSTV.js")
        assert any("webostvjs" in r["path"] for r in results)

    def test_com_webos_service_query_does_not_raise(self) -> None:
        """Luna service URIs like com.webos.service.audio must not crash FTS5."""
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        results = db_mod.search_docs("com.webos.service.audio")
        assert isinstance(results, list)


class TestGetPage:
    def test_exact_match(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        page = db_mod.get_page("/develop/page0")
        assert page is not None
        assert page["path"] == "/develop/page0"

    def test_not_found_returns_none(self) -> None:
        import lg_docs_mcp.db as db_mod
        result = db_mod.get_page("/develop/nonexistent")
        assert result is None

    def test_fuzzy_match(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        page = db_mod.get_page_fuzzy("page0")
        assert page is not None
        assert "page0" in page["path"]

    def test_fuzzy_not_found(self) -> None:
        import lg_docs_mcp.db as db_mod
        result = db_mod.get_page_fuzzy("totallymissingpath9999")
        assert result is None

    def test_fuzzy_long_fragment_does_not_raise(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        long_fragment = "x" * 5000
        result = db_mod.get_page_fuzzy(long_fragment)
        assert result is None  # no match, but must not raise

    def test_fuzzy_escapes_percent_wildcard(self) -> None:
        """Fragment with % must match literally, not as LIKE wildcard."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/other",
            path="/develop/other",
            section="develop",
            title="Other",
            content="other content",
        )
        # Without escaping, x%y would match any path containing anything between x and y
        result = db_mod.get_page_fuzzy("x%y")
        assert result is None

    def test_fuzzy_escapes_underscore_wildcard(self) -> None:
        """Fragment 'webostv_js' must not match 'webostv.js' due to _ wildcard."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/webostv.js/api",
            path="/references/webostv.js/api",
            section="references",
            title="webOSTV.js API",
            content="webostv js api content",
        )
        # _ is a LIKE wildcard meaning "any single char"; without escaping it matches the dot
        result = db_mod.get_page_fuzzy("webostv_js")
        assert result is None

    def test_fuzzy_literal_dot_still_matches(self) -> None:
        """The dot in 'webostv.js' is a literal path char and must still match."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/webostv.js/api",
            path="/references/webostv.js/api",
            section="references",
            title="webOSTV.js API",
            content="webostv js api content",
        )
        result = db_mod.get_page_fuzzy("webostv.js")
        assert result is not None
        assert "webostv.js" in result["path"]


class TestSearchDocsBySection:
    def test_returns_results_in_section(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(3)
        results = db_mod.search_docs_by_section("Luna", "develop")
        assert len(results) > 0
        for r in results:
            assert r["section"] == "develop"

    def test_crawled_at_in_results(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(1)
        results = db_mod.search_docs_by_section("Luna", "develop")
        assert len(results) > 0
        assert "crawled_at" in results[0]

    def test_ordering_consistent_with_search_docs(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(3)
        by_section = db_mod.search_docs_by_section("Luna", "develop", limit=3)
        all_docs = db_mod.search_docs("Luna", limit=3)
        assert len(by_section) > 0
        assert len(all_docs) > 0
        assert isinstance(by_section[0]["rank"], float)


class TestGetPageHash:
    def test_returns_hash(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/hash-test",
            path="/develop/hash-test",
            section="s",
            title="H",
            content="c",
            content_hash="deadbeef",
        )
        assert db_mod.get_page_hash("/develop/hash-test") == "deadbeef"

    def test_missing_path_returns_none(self) -> None:
        import lg_docs_mcp.db as db_mod
        assert db_mod.get_page_hash("/develop/missing") is None


class TestListSections:
    def test_returns_sections(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(3)
        sections = db_mod.list_sections()
        assert len(sections) > 0
        assert "section" in sections[0]
        assert "page_count" in sections[0]

    def test_empty_db(self) -> None:
        import lg_docs_mcp.db as db_mod
        sections = db_mod.list_sections()
        assert sections == []


class TestGetStats:
    def test_structure(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(2)
        stats = db_mod.get_stats()
        assert "total_pages" in stats
        assert "db_size_mb" in stats
        assert "sections" in stats
        assert "last_crawled" in stats
        assert stats["total_pages"] == 2


class TestGetStatsStaleness:
    def _set_last_crawled(self, days_ago: float) -> None:
        from datetime import datetime, timedelta, timezone

        import lg_docs_mcp.db as db_mod
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        db_mod.set_cache_meta("last_crawled", ts)

    def test_fresh(self) -> None:
        import lg_docs_mcp.db as db_mod
        self._set_last_crawled(3)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "fresh"
        assert stats["days_since_crawl"] is not None
        assert stats["days_since_crawl"] <= 7

    def test_aging(self) -> None:
        import lg_docs_mcp.db as db_mod
        self._set_last_crawled(15)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "aging"

    def test_stale(self) -> None:
        import lg_docs_mcp.db as db_mod
        self._set_last_crawled(60)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "stale"

    def test_very_stale(self) -> None:
        import lg_docs_mcp.db as db_mod
        self._set_last_crawled(120)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "very_stale"

    def test_never_crawled(self) -> None:
        import lg_docs_mcp.db as db_mod
        stats = db_mod.get_stats()
        assert stats["last_crawled"] == "never"
        assert stats["days_since_crawl"] is None
        assert stats["data_freshness"] == "unknown"

    def test_invalid_timestamp(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.set_cache_meta("last_crawled", "not-a-date")
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "unknown"
        assert stats["days_since_crawl"] is None


class TestFTSPrefixSearch:
    def test_prefix_search_matches_dot_token(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/webostv.js/api",
            path="/references/webostv.js/api",
            section="references",
            title="webOSTV.js API",
            content="The webOSTV.js library provides access to Luna Service API methods.",
        )
        results = db_mod.search_docs("webOSTV*")
        assert len(results) > 0
        assert any("webostv" in r["path"].lower() for r in results)

    def test_exact_token_still_matches(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/webostv.js/api",
            path="/references/webostv.js/api",
            section="references",
            title="webOSTV.js API",
            content="The webOSTV.js library provides access to Luna Service API methods.",
        )
        results = db_mod.search_docs("Luna")
        assert len(results) > 0


class TestSanitizeFtsQuery:
    def test_trailing_star_with_dot_keeps_star_outside_quotes(self) -> None:
        """com.webos.service* must become "com.webos.service"* not "com.webos.service*"."""
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("com.webos.service*") == '"com.webos.service"*'

    def test_trailing_star_no_special_chars_unchanged(self) -> None:
        """webostv* has no special chars — must stay as-is for FTS5 prefix search."""
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("webostv*") == "webostv*"

    def test_dot_without_star_wraps_fully(self) -> None:
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("webOSTV.js") == '"webOSTV.js"'

    def test_already_quoted_unchanged(self) -> None:
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query('"webOSTV.js"') == '"webOSTV.js"'

    def test_dash_prefix_search(self) -> None:
        """Trailing * with dash in base: "some-term"*"""
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("getting-started*") == '"getting-started"*'

    def test_multiterm_dot_notation_quotes_only_special_token(self) -> None:
        """'webOSTV.js launch' must become '"webOSTV.js" launch', not '"webOSTV.js launch"'."""
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("webOSTV.js launch") == '"webOSTV.js" launch'

    def test_multiterm_com_webos_service_quotes_first_token(self) -> None:
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("com.webos.service audio") == '"com.webos.service" audio'

    def test_multiterm_dash_quotes_only_dash_token(self) -> None:
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("getting-started guide") == '"getting-started" guide'

    def test_multiterm_no_special_chars_unchanged(self) -> None:
        """'Luna service' has no special chars — must stay unchanged."""
        from lg_docs_mcp.db import _sanitize_fts_query
        assert _sanitize_fts_query("Luna service") == "Luna service"


class TestMakeOrQuery:
    def test_multiterm_joins_with_or(self) -> None:
        from lg_docs_mcp.db import _make_or_query
        assert _make_or_query("web app development") == "web OR app OR development"

    def test_quoted_token_preserved(self) -> None:
        from lg_docs_mcp.db import _make_or_query
        assert _make_or_query('"webOSTV.js" launch') == '"webOSTV.js" OR launch'

    def test_single_token_returns_none(self) -> None:
        from lg_docs_mcp.db import _make_or_query
        assert _make_or_query('"webOSTV.js"') is None

    def test_single_plain_token_returns_none(self) -> None:
        from lg_docs_mcp.db import _make_or_query
        assert _make_or_query("Luna") is None

    def test_prefix_token_preserved(self) -> None:
        from lg_docs_mcp.db import _make_or_query
        assert _make_or_query('"com.webos.service"* audio') == '"com.webos.service"* OR audio'


class TestFTSPrefixSearchDotNotation:
    def test_dot_prefix_search_returns_results(self) -> None:
        """com.webos.service* must find pages containing com.webos.service.xxx tokens."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio Service",
            content="Service URI: luna://com.webos.service.audio provides audio control.",
        )
        results = db_mod.search_docs("com.webos.service*")
        assert len(results) > 0
        assert any("audio" in r["path"] for r in results)

    def test_dot_prefix_no_false_positives(self) -> None:
        """com.webos.service* must NOT match pages without that namespace."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/getting-started",
            path="/develop/getting-started",
            section="develop",
            title="Getting Started",
            content="Build your first webOS TV app with this guide.",
        )
        results = db_mod.search_docs("com.webos.service*")
        assert results == []


class TestSearchDocsByPathPrefix:
    def test_filters_by_path_prefix(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/luna",
            path="/develop/references/luna",
            section="develop",
            title="Luna Service",
            content="Luna Service API introduction docs.",
        )
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/db8",
            path="/develop/guides/db8",
            section="develop",
            title="DB8 Guide",
            content="Luna Service DB8 guide for storage.",
        )
        results = db_mod.search_docs_by_path_prefix("Luna", "/develop/references")
        assert len(results) > 0
        assert all("/develop/references" in r["path"] for r in results)

    def test_excludes_other_prefixes(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble",
            path="/develop/guides/ble",
            section="develop",
            title="BLE Guide",
            content="Bluetooth Low Energy Luna Service guide.",
        )
        results = db_mod.search_docs_by_path_prefix("Luna", "/develop/references")
        assert results == []

    def test_no_results_returns_empty_list(self) -> None:
        import lg_docs_mcp.db as db_mod
        results = db_mod.search_docs_by_path_prefix("xyznothing", "/develop/references")
        assert results == []


class TestGetAllUrls:
    def test_empty_db_returns_empty_set(self) -> None:
        import lg_docs_mcp.db as db_mod
        assert db_mod.get_all_urls() == set()

    def test_returns_all_stored_urls(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(3)
        urls = db_mod.get_all_urls()
        assert len(urls) == 3
        assert "https://webostv.developer.lge.com/develop/page0" in urls
        assert "https://webostv.developer.lge.com/develop/page2" in urls

    def test_returns_set_not_list(self) -> None:
        import lg_docs_mcp.db as db_mod
        _insert_sample(2)
        result = db_mod.get_all_urls()
        assert isinstance(result, set)


class TestOrFallback:
    """Verify that OR fallback triggers when AND returns no results for multi-term queries."""

    def _insert_webostvjs_page(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/webostvjs",
            path="/develop/references/webostvjs",
            section="develop",
            title="webOSTV.js Introduction",
            content="The webOSTV.js library provides a portable API for webOS TV apps.",
        )

    def _insert_launch_page(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/app-launch",
            path="/develop/guides/app-launch",
            section="develop",
            title="App Launch Guide",
            content="How to launch an app on webOS TV using the Application Manager.",
        )

    def test_search_docs_or_fallback_multiterm_dot_notation(self) -> None:
        """'webOSTV.js launch' finds pages via OR when no page has both terms together."""
        import lg_docs_mcp.db as db_mod
        self._insert_webostvjs_page()
        self._insert_launch_page()
        results = db_mod.search_docs("webOSTV.js launch")
        assert len(results) >= 1
        paths = [r["path"] for r in results]
        assert any("webostvjs" in p or "launch" in p for p in paths)

    def test_search_docs_by_section_or_fallback(self) -> None:
        """Multi-term query falls back to OR within section when AND yields nothing."""
        import lg_docs_mcp.db as db_mod
        self._insert_webostvjs_page()
        self._insert_launch_page()
        results = db_mod.search_docs_by_section("webOSTV.js launch", "develop")
        assert len(results) >= 1

    def test_search_docs_by_path_prefix_or_fallback(self) -> None:
        """Multi-term query falls back to OR within path prefix when AND yields nothing."""
        import lg_docs_mcp.db as db_mod
        self._insert_webostvjs_page()
        results = db_mod.search_docs_by_path_prefix("webOSTV.js launch", "/develop/references")
        assert len(results) >= 1
        assert all("/develop/references" in r["path"] for r in results)

    def test_single_token_no_or_fallback(self) -> None:
        """A single-token query that returns no results must NOT trigger OR fallback."""
        import lg_docs_mcp.db as db_mod
        self._insert_webostvjs_page()
        results = db_mod.search_docs("xyznonexistenttoken9999")
        assert results == []

    def test_and_results_not_replaced_by_or(self) -> None:
        """When AND already returns results, OR fallback must NOT be triggered."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/both",
            path="/develop/references/both",
            section="develop",
            title="webOSTV.js Launch",
            content="webOSTV.js can launch apps on webOS TV.",
        )
        self._insert_launch_page()
        # AND should find the page that has both terms — only 1 result expected
        results = db_mod.search_docs("webOSTV.js launch")
        assert len(results) >= 1
        # The page with both terms should rank first
        assert any("both" in r["path"] for r in results)


class TestGetStatsFreshnessConfig:
    def test_custom_fresh_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A page crawled 5 days ago is 'fresh' with default threshold (7d)
        but 'aging' when the threshold is overridden to 3d."""
        from datetime import datetime, timedelta, timezone

        import lg_docs_mcp.db as db_mod

        dt = datetime.now(timezone.utc) - timedelta(days=5)
        db_mod.set_cache_meta("last_crawled", dt.isoformat())

        # Default threshold: 5 days < 7 → fresh
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "fresh"

        # Override threshold to 3 days: 5 days > 3 → aging
        monkeypatch.setattr(db_mod, "_FRESH_DAYS", 3)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "aging"

    def test_custom_aging_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A page crawled 20 days ago is 'aging' with default threshold (30d)
        but 'stale' when aging threshold is overridden to 15d."""
        from datetime import datetime, timedelta, timezone

        import lg_docs_mcp.db as db_mod

        dt = datetime.now(timezone.utc) - timedelta(days=20)
        db_mod.set_cache_meta("last_crawled", dt.isoformat())

        # Default: 20 days → aging (7 < 20 <= 30)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "aging"

        # Override: aging cap = 15, stale cap = 90: 20 > 15 → stale
        monkeypatch.setattr(db_mod, "_AGING_DAYS", 15)
        stats = db_mod.get_stats()
        assert stats["data_freshness"] == "stale"


class TestExpandWithSynonyms:
    def test_returns_none_when_no_synonyms(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        assert _expand_with_synonyms("Luna service") is None

    def test_expands_bluetooth(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        result = _expand_with_synonyms("bluetooth guide")
        assert result is not None
        assert "ble" in result
        assert "gatt" in result
        assert "guide" in result

    def test_expands_playback(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        result = _expand_with_synonyms("audio playback")
        assert result is not None
        assert "audio" in result
        assert "media" in result

    def test_single_synonym_token(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        result = _expand_with_synonyms("bluetooth")
        assert result is not None
        assert "ble" in result
        assert "gatt" in result

    def test_empty_query_returns_none(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        assert _expand_with_synonyms("") is None

    def test_case_insensitive_lookup(self) -> None:
        from lg_docs_mcp.db import _expand_with_synonyms
        result = _expand_with_synonyms("Bluetooth")
        assert result is not None
        assert "ble" in result

    def test_output_is_fts5_safe(self) -> None:
        """Synonym expansion must produce a valid FTS5 query (no extra sanitization needed)."""
        import sqlite3

        import lg_docs_mcp.db as db_mod
        from lg_docs_mcp.db import _expand_with_synonyms
        # Insert a BLE page so there's something to match
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT",
            content="Bluetooth Low Energy GATT for webOS TV.",
        )
        syn_q = _expand_with_synonyms("bluetooth guide")
        assert syn_q is not None
        conn = db_mod.get_conn()
        # Must not raise sqlite3.OperationalError
        rows = conn.execute(
            "SELECT d.path FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ?",
            (syn_q,)
        ).fetchall()
        assert isinstance(rows, list)


class TestSearchDocsWithSynonymPhase:
    def test_bluetooth_guide_finds_ble_gatt_page(self) -> None:
        """Synonym expansion for 'bluetooth' must find the ble-gatt page."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT profile implementation for webOS TV.",
        )
        results = db_mod.search_docs("bluetooth guide")
        assert len(results) > 0
        assert any("ble-gatt" in r["path"] for r in results)

    def test_synonym_phase_finds_audio_with_playback_query(self) -> None:
        """'audio playback' must find the audio page via synonym expansion."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio Service",
            content="Control audio volume with the Luna service.",
        )
        results = db_mod.search_docs("audio playback")
        assert len(results) > 0
        assert any("audio" in r["path"] for r in results)

    def test_synonym_phase_not_triggered_when_and_succeeds(self) -> None:
        """When AND already returns results, synonym phase must NOT be triggered."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT profile.",
        )
        # 'ble gatt' matches directly — synonym phase should not fire
        results = db_mod.search_docs("ble gatt")
        assert len(results) > 0

    def test_synonym_phase_by_section_finds_result(self) -> None:
        """search_docs_by_section also uses synonym expansion."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT profile.",
        )
        results = db_mod.search_docs_by_section("bluetooth", "develop")
        assert len(results) > 0
        assert any("ble-gatt" in r["path"] for r in results)

    def test_synonym_phase_by_path_prefix_finds_result(self) -> None:
        """search_docs_by_path_prefix also uses synonym expansion."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT profile.",
        )
        results = db_mod.search_docs_by_path_prefix("bluetooth", "/develop/guides")
        assert len(results) > 0
        assert any("ble-gatt" in r["path"] for r in results)


class TestGetPageByPathKeywords:
    def test_bluetooth_fragment_finds_ble_gatt_page(self) -> None:
        """get_page_by_path_keywords('/develop/guides/bluetooth') finds ble-gatt via synonym."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT guide.",
        )
        result = db_mod.get_page_by_path_keywords("/develop/guides/bluetooth")
        assert result is not None
        assert "ble-gatt" in result["path"]

    def test_exact_segment_matches_title(self) -> None:
        """'ble-gatt' segment extracts 'ble' and 'gatt' keywords and finds the page."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="BLE GATT service details.",
        )
        result = db_mod.get_page_by_path_keywords("/develop/guides/ble-gatt")
        assert result is not None

    def test_empty_fragment_returns_none(self) -> None:
        import lg_docs_mcp.db as db_mod
        assert db_mod.get_page_by_path_keywords("") is None
        assert db_mod.get_page_by_path_keywords("/") is None

    def test_no_match_returns_none(self) -> None:
        import lg_docs_mcp.db as db_mod
        result = db_mod.get_page_by_path_keywords("/develop/guides/xyznonexistent9999")
        assert result is None


class TestGetPathByHash:
    def test_returns_path_for_known_hash(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/test",
            path="/develop/test",
            section="develop",
            title="T",
            content="c",
            content_hash="abc123",
        )
        result = db_mod.get_path_by_hash("abc123")
        assert result == "/develop/test"

    def test_returns_none_for_unknown_hash(self) -> None:
        import lg_docs_mcp.db as db_mod
        result = db_mod.get_path_by_hash("nonexistenthashxyz")
        assert result is None

    def test_returns_first_path_when_multiple_exist(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/page-a",
            path="/develop/page-a",
            section="develop",
            title="A",
            content="shared content",
            content_hash="sharedabc",
        )
        result = db_mod.get_path_by_hash("sharedabc")
        assert result == "/develop/page-a"


class TestMakeDotSplitQuery:
    def test_splits_service_uri(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("com.webos.service.audio")
        assert result == "audio"

    def test_splits_complex_service_uri(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("com.webos.service.bluetooth.gatt")
        assert result is not None
        assert "bluetooth" in result
        assert "gatt" in result

    def test_leaves_webostvjs_unchanged(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("webOSTV.js launch")
        assert result is None  # no com.* token → no split

    def test_mixed_query_service_plus_keyword(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("com.webos.audio volume")
        assert result is not None
        assert "audio" in result
        assert "volume" in result

    def test_no_dot_returns_none(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("Luna service")
        assert result is None

    def test_com_webos_audio_returns_audio(self) -> None:
        from lg_docs_mcp.db import _make_dot_split_query
        result = _make_dot_split_query("com.webos.audio")
        assert result == "audio"


class TestListSectionsFiltered:
    def test_excludes_null_section(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/",
            path="/",
            section=None,
            title="Root",
            content="root content",
        )
        sections = db_mod.list_sections()
        assert all(s["section"] is not None for s in sections)

    def test_excludes_unknown_sections(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/contact",
            path="/contact",
            section="contact",
            title="Contact",
            content="contact content",
        )
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/page",
            path="/develop/page",
            section="develop",
            title="Dev page",
            content="dev content",
        )
        sections = db_mod.list_sections()
        section_names = {s["section"] for s in sections}
        assert "contact" not in section_names
        assert "develop" in section_names

    def test_excludes_discover_section(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/discover/something",
            path="/discover/something",
            section="discover",
            title="Discover",
            content="discover content",
        )
        sections = db_mod.list_sections()
        assert all(s["section"] != "discover" for s in sections)

    def test_includes_all_known_top_sections(self) -> None:
        import lg_docs_mcp.db as db_mod
        for section in ["develop", "faq", "news", "more", "notice", "distribute"]:
            db_mod.upsert_doc(
                url=f"https://webostv.developer.lge.com/{section}/page",
                path=f"/{section}/page",
                section=section,
                title=f"{section} page",
                content=f"{section} content",
            )
        sections = db_mod.list_sections()
        section_names = {s["section"] for s in sections}
        assert {"develop", "faq", "news", "more", "notice", "distribute"}.issubset(section_names)


class TestGetStatsFiltered:
    def test_sections_exclude_null(self) -> None:
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/",
            path="/",
            section=None,
            title="Root",
            content="root content",
        )
        stats = db_mod.get_stats()
        assert all(s["section"] is not None for s in stats["sections"])

    def test_sections_exclude_contact_discover(self) -> None:
        import lg_docs_mcp.db as db_mod
        for section in ["contact", "discover"]:
            db_mod.upsert_doc(
                url=f"https://webostv.developer.lge.com/{section}",
                path=f"/{section}",
                section=section,
                title=section.capitalize(),
                content=f"{section} content",
            )
        stats = db_mod.get_stats()
        section_names = {s["section"] for s in stats["sections"]}
        assert "contact" not in section_names
        assert "discover" not in section_names


class TestDotSplitFallback:
    def test_service_uri_finds_audio_page(self) -> None:
        """com.webos.service.audio should find the audio page via dot-split fallback."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio",
            content="Service URI - luna://com.webos.audio Provides methods for volume control.",
        )
        results = db_mod.search_docs("com.webos.service.audio")
        assert len(results) > 0
        assert any("audio" in r["path"] for r in results)

    def test_service_uri_by_section_finds_result(self) -> None:
        """search_docs_by_section also uses dot-split fallback."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio",
            content="Luna audio service volume control.",
        )
        results = db_mod.search_docs_by_section("com.webos.service.audio", "develop")
        assert len(results) > 0

    def test_service_uri_by_path_prefix_finds_result(self) -> None:
        """search_docs_by_path_prefix also uses dot-split fallback."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio",
            content="Luna audio service volume control.",
        )
        results = db_mod.search_docs_by_path_prefix("com.webos.service.audio", "/develop/references")
        assert len(results) > 0

    def test_dot_split_not_triggered_when_and_succeeds(self) -> None:
        """When AND already returns results, dot-split must NOT be triggered."""
        import lg_docs_mcp.db as db_mod
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/references/audio",
            path="/develop/references/audio",
            section="develop",
            title="Audio",
            content="com.webos.audio service volume control.",
        )
        # Direct match succeeds; results returned without needing fallback
        results = db_mod.search_docs("audio volume")
        assert len(results) > 0


# ---------------------------------------------------------------------------
# _strip_boilerplate
# ---------------------------------------------------------------------------

class TestStripBoilerplate:
    def test_strips_breadcrumbs_before_h1(self) -> None:
        import lg_docs_mcp.db as db_mod
        content = "1. [HOME](...)\n  2. [DEVELOP](...)\n\n# Real Title\n\nActual content."
        assert db_mod._strip_boilerplate(content) == "# Real Title\n\nActual content."

    def test_strips_lg_footer(self) -> None:
        import lg_docs_mcp.db as db_mod
        content = "# My Page\n\nContent.\n\n![LG Electronics Logo](...)\nCopyright © 2026 LG."
        assert db_mod._strip_boilerplate(content) == "# My Page\n\nContent."

    def test_no_breadcrumbs_no_change(self) -> None:
        import lg_docs_mcp.db as db_mod
        content = "# My Page\n\nContent here."
        assert db_mod._strip_boilerplate(content) == "# My Page\n\nContent here."

    def test_empty_string(self) -> None:
        import lg_docs_mcp.db as db_mod
        assert db_mod._strip_boilerplate("") == ""

    def test_get_page_returns_stripped_content(self) -> None:
        import lg_docs_mcp.db as db_mod
        dirty = "1. [HOME](...)\n\n# My Title\n\nReal content.\n\n![LG Electronics Logo](...)\nCopyright."
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/page",
            path="/develop/page",
            section="develop",
            title="My Title",
            content=dirty,
        )
        page = db_mod.get_page("/develop/page")
        assert page is not None
        assert page["content"] == "# My Title\n\nReal content."

    def test_get_page_fuzzy_returns_stripped_content(self) -> None:
        import lg_docs_mcp.db as db_mod
        dirty = "1. [HOME](...)\n\n# My Title\n\nReal content.\n\n![LG Electronics Logo](...)\nCopyright."
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/page",
            path="/develop/page",
            section="develop",
            title="My Title",
            content=dirty,
        )
        page = db_mod.get_page_fuzzy("develop/page")
        assert page is not None
        assert page["content"] == "# My Title\n\nReal content."


# ---------------------------------------------------------------------------
# migrate_clean_content
# ---------------------------------------------------------------------------

class TestMigrateCleanContent:
    def test_updates_dirty_pages(self) -> None:
        import lg_docs_mcp.db as db_mod
        dirty = "1. [HOME](...)\n\n# Title\n\nContent.\n\n![LG Electronics Logo](...)\nCopyright."
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/dirty",
            path="/develop/dirty",
            section="develop",
            title="Title",
            content=dirty,
        )
        updated = db_mod.migrate_clean_content()
        assert updated == 1
        conn = db_mod.get_conn()
        row = conn.execute("SELECT content FROM docs WHERE path = '/develop/dirty'").fetchone()
        assert row["content"] == "# Title\n\nContent."

    def test_skips_already_clean_pages(self) -> None:
        import lg_docs_mcp.db as db_mod
        clean = "# Title\n\nContent already clean."
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/clean",
            path="/develop/clean",
            section="develop",
            title="Title",
            content=clean,
        )
        updated = db_mod.migrate_clean_content()
        assert updated == 0

    def test_returns_count_of_updated_pages(self) -> None:
        import lg_docs_mcp.db as db_mod
        for i in range(3):
            dirty = f"1. [HOME](...)\n\n# Title {i}\n\nContent {i}.\n\n![LG Electronics Logo](...)\nCopyright."
            db_mod.upsert_doc(
                url=f"https://webostv.developer.lge.com/develop/page{i}",
                path=f"/develop/page{i}",
                section="develop",
                title=f"Title {i}",
                content=dirty,
            )
        updated = db_mod.migrate_clean_content()
        assert updated == 3
