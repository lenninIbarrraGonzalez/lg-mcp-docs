"""Unit tests for lg_docs_mcp.server tool functions."""
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path):
    """Redirect all DB operations to a temp directory."""
    import lg_docs_mcp.db as db_mod
    original_path = db_mod.DB_PATH
    db_path = tmp_path / "docs.db"
    db_mod.DB_PATH = db_path
    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.init_db()
    yield
    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.DB_PATH = original_path


def _seed(n: int = 3) -> None:
    import lg_docs_mcp.db as db_mod
    for i in range(n):
        db_mod.upsert_doc(
            url=f"https://webostv.developer.lge.com/develop/item{i}",
            path=f"/develop/item{i}",
            section="develop",
            title=f"Item {i}",
            content=f"Content about Luna Service and item {i} details.",
        )


class TestSearchDocsValidation:
    def test_empty_query_raises(self) -> None:
        from lg_docs_mcp.server import lg_search_docs
        with pytest.raises((ValueError, Exception)):
            lg_search_docs(query="")

    def test_whitespace_only_query_raises(self) -> None:
        from lg_docs_mcp.server import lg_search_docs
        with pytest.raises((ValueError, Exception)):
            lg_search_docs(query="   ")

    def test_no_results_returns_message(self) -> None:
        from lg_docs_mcp.server import lg_search_docs
        _seed(1)
        result = lg_search_docs(query="xyzabsolutelynothing9999")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result

    def test_valid_query_returns_results(self) -> None:
        from lg_docs_mcp.server import lg_search_docs
        _seed(3)
        result = lg_search_docs(query="Luna")
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert "path" in result["results"][0]

    def test_response_format_json_returns_envelope(self) -> None:
        from lg_docs_mcp.server import ResponseFormat, lg_search_docs
        _seed(3)
        result = lg_search_docs(query="Luna", response_format=ResponseFormat.JSON)
        assert isinstance(result, dict)
        assert "results" in result
        assert "count" in result
        assert "offset" in result
        assert "has_more" in result

    def test_response_format_markdown_returns_string(self) -> None:
        from lg_docs_mcp.server import ResponseFormat, lg_search_docs
        _seed(3)
        result = lg_search_docs(query="Luna", response_format=ResponseFormat.MARKDOWN)
        assert isinstance(result, str)
        assert "Luna" in result

    def test_offset_paginates_results(self) -> None:
        from lg_docs_mcp.server import lg_search_docs
        _seed(5)
        first = lg_search_docs(query="Luna", limit=2, offset=0)
        second = lg_search_docs(query="Luna", limit=2, offset=2)
        assert isinstance(first, dict)
        assert isinstance(second, dict)
        first_paths = [r["path"] for r in first["results"]]
        second_paths = [r["path"] for r in second["results"]]
        assert first_paths != second_paths


class TestGetPage:
    def test_exact_match(self) -> None:
        from lg_docs_mcp.server import lg_get_page
        _seed(1)
        result = lg_get_page(path="/develop/item0")
        assert "content" in result
        assert result["path"] == "/develop/item0"

    def test_fuzzy_fallback(self) -> None:
        from lg_docs_mcp.server import lg_get_page
        _seed(1)
        result = lg_get_page(path="item0")
        assert "content" in result
        assert "item0" in result["path"]

    def test_not_found_returns_message(self) -> None:
        from lg_docs_mcp.server import lg_get_page
        result = lg_get_page(path="/develop/totally/missing/page")
        assert "message" in result

    def test_fuzzy_match_sets_indicator(self) -> None:
        from lg_docs_mcp.server import lg_get_page
        _seed(1)
        result = lg_get_page(path="item0")
        assert "content" in result
        assert result.get("_fuzzy_match") is True

    def test_exact_match_no_fuzzy_indicator(self) -> None:
        from lg_docs_mcp.server import lg_get_page
        _seed(1)
        result = lg_get_page(path="/develop/item0")
        assert "content" in result
        assert "_fuzzy_match" not in result


class TestSearchBySection:
    def test_filters_by_section(self) -> None:
        import lg_docs_mcp.db as db_mod
        from lg_docs_mcp.server import lg_search_by_section
        _seed(2)
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/webostv.js/api",
            path="/references/webostv.js/api",
            section="references",
            title="webOSTV.js API",
            content="Content about Luna Service in references section.",
        )
        result = lg_search_by_section(section="develop", query="Luna")
        assert isinstance(result, dict)
        for r in result["results"]:
            if "section" in r:
                assert r["section"] == "develop"

    def test_empty_query_raises(self) -> None:
        from lg_docs_mcp.server import lg_search_by_section
        with pytest.raises((ValueError, Exception)):
            lg_search_by_section(section="develop", query="")

    def test_empty_section_raises(self) -> None:
        from lg_docs_mcp.server import lg_search_by_section
        with pytest.raises((ValueError, Exception)):
            lg_search_by_section(section="", query="webOSTV")


class TestGetStats:
    def test_returns_expected_keys(self) -> None:
        from lg_docs_mcp.server import lg_get_stats
        _seed(2)
        stats = lg_get_stats()
        assert "total_pages" in stats
        assert "db_size_mb" in stats
        assert "sections" in stats
        assert "last_crawled" in stats
        assert "days_since_crawl" in stats
        assert "data_freshness" in stats
        assert stats["total_pages"] == 2


class TestCheckSubcommand:
    def _run_main(self, argv: list[str]) -> int:
        from unittest.mock import patch
        with patch("sys.argv", argv):
            from lg_docs_mcp.server import main
            try:
                main()
                return 0
            except SystemExit as e:
                return int(e.code) if e.code is not None else 0

    def test_fresh_exits_zero(self, capsys: Any) -> None:
        from datetime import datetime, timedelta, timezone

        import lg_docs_mcp.db as db_mod
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        db_mod.set_cache_meta("last_crawled", dt.isoformat())

        code = self._run_main(["lg-docs-mcp", "check"])
        captured = capsys.readouterr()
        assert code == 0
        assert "FRESH" in captured.out

    def test_stale_exits_one(self, capsys: Any) -> None:
        from datetime import datetime, timedelta, timezone

        import lg_docs_mcp.db as db_mod
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        db_mod.set_cache_meta("last_crawled", dt.isoformat())

        code = self._run_main(["lg-docs-mcp", "check"])
        captured = capsys.readouterr()
        assert code == 1
        assert "STALE" in captured.out

    def test_never_crawled_exits_one(self, capsys: Any) -> None:
        code = self._run_main(["lg-docs-mcp", "check"])
        captured = capsys.readouterr()
        assert code == 1
        assert "STALE" in captured.out


def _seed_path(path: str, section: str, title: str, content: str) -> None:
    import lg_docs_mcp.db as db_mod
    db_mod.upsert_doc(
        url=f"https://webostv.developer.lge.com{path}",
        path=path,
        section=section,
        title=title,
        content=content,
    )


class TestSearchBySectionAliases:
    def test_references_alias_returns_results(self) -> None:
        """section='references' must find pages under /develop/references/."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed_path("/develop/references/luna", "develop", "Luna Service API",
                   "Luna Service API introduction for webOS TV.")
        result = lg_search_by_section(section="references", query="Luna")
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert "/develop/references" in result["results"][0]["path"]

    def test_guides_alias_returns_results(self) -> None:
        """section='guides' must find pages under /develop/guides/."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed_path("/develop/guides/db8", "develop", "DB8 Guide",
                   "DB8 is the Luna Service for database access.")
        result = lg_search_by_section(section="guides", query="Luna")
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert "/develop/guides" in result["results"][0]["path"]

    def test_tools_alias_returns_results(self) -> None:
        """section='tools' must find pages under /develop/tools/."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed_path("/develop/tools/cli", "develop", "CLI Developer Guide",
                   "Use the webOS TV CLI to build and deploy apps.")
        result = lg_search_by_section(section="tools", query="CLI")
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert "/develop/tools" in result["results"][0]["path"]

    def test_references_alias_excludes_guides(self) -> None:
        """section='references' must NOT return pages under /develop/guides/."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed_path("/develop/guides/db8", "develop", "DB8 Guide",
                   "Luna Service for database access.")
        result = lg_search_by_section(section="references", query="Luna")
        assert isinstance(result, dict)
        assert result["count"] == 0 or all(
            "/develop/references" in r["path"] for r in result["results"]
        )

    def test_unknown_section_still_works(self) -> None:
        """Non-alias, non-existent section returns no-results message gracefully."""
        from lg_docs_mcp.server import lg_search_by_section
        result = lg_search_by_section(section="nonexistent", query="Luna")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result
        assert "list_sections()" in result["message"]


class TestNoResultsMessages:
    def test_empty_db_suggests_refresh_cache(self) -> None:
        """With 0 pages, the no-results message must mention lg_refresh_cache()."""
        from lg_docs_mcp.server import lg_search_docs
        result = lg_search_docs(query="anything")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result
        assert "refresh_cache" in result["message"]

    def test_populated_db_no_refresh_suggestion(self) -> None:
        """With pages in DB, the no-results message must NOT suggest refresh_cache()."""
        from lg_docs_mcp.server import lg_search_docs
        _seed(5)
        result = lg_search_docs(query="xyznothing9999")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result
        assert "refresh_cache" not in result["message"]

    def test_valid_section_no_results_suggests_different_terms(self) -> None:
        """Valid section with no matches must suggest trying different terms, not lg_list_sections()."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed(1)
        result = lg_search_by_section(section="develop", query="xyznothing9999")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result
        assert "list_sections()" not in result["message"]
        assert "different search term" in result["message"] or "broaden" in result["message"]

    def test_unknown_section_no_results_mentions_list_sections(self) -> None:
        """Unknown section must mention lg_list_sections() in the no-results message."""
        from lg_docs_mcp.server import lg_search_by_section
        _seed(1)
        result = lg_search_by_section(section="zzunknown", query="xyznothing9999")
        assert isinstance(result, dict)
        assert result["count"] == 0
        assert "message" in result
        assert "list_sections()" in result["message"]


class TestGetPageKeywordFallback:
    def test_bluetooth_path_finds_ble_gatt_page(self) -> None:
        """lg_get_page('/develop/guides/bluetooth') finds ble-gatt via keyword FTS fallback."""
        import lg_docs_mcp.db as db_mod
        from lg_docs_mcp.server import lg_get_page
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="Bluetooth Low Energy GATT profile for webOS TV.",
        )
        result = lg_get_page(path="/develop/guides/bluetooth")
        assert "content" in result
        assert "ble-gatt" in result["path"]
        assert result.get("_fuzzy_match") is True

    def test_keyword_fallback_sets_fuzzy_flag(self) -> None:
        """Keyword FTS fallback must always set _fuzzy_match=True."""
        import lg_docs_mcp.db as db_mod
        from lg_docs_mcp.server import lg_get_page
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/develop/guides/ble-gatt",
            path="/develop/guides/ble-gatt",
            section="develop",
            title="BLE GATT Service",
            content="BLE GATT service content.",
        )
        result = lg_get_page(path="/develop/guides/bluetooth")
        assert result.get("_fuzzy_match") is True

    def test_not_found_still_returns_message(self) -> None:
        """If keyword fallback also fails, message is returned."""
        from lg_docs_mcp.server import lg_get_page
        result = lg_get_page(path="/develop/guides/bluetooth")
        assert "message" in result
