"""Usage tests — real MCP protocol calls against the populated DB.

These tests simulate how an AI assistant or developer would actually use
the lg-docs MCP tools: realistic queries, multi-step workflows, and
navigation patterns against the real webostv.developer.lge.com cache.

Skip behaviour: all tests are skipped automatically if the DB has fewer
than 10 pages. Populate it first with: lg-docs-mcp crawl
"""
import json
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

import lg_docs_mcp.db as db_mod
from lg_docs_mcp.server import mcp


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def real_db():
    """Use the real populated DB (respects LG_DOCS_DB_PATH env var).

    Resets the thread-local connection so the correct DB file is opened.
    Skips all tests in this module if the DB is not populated.
    """
    if hasattr(db_mod._local, "conn") and db_mod._local.conn is not None:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_mod.init_db()
    stats = db_mod.get_stats()
    if stats["total_pages"] < 10:
        pytest.skip("DB not populated — run: lg-docs-mcp crawl")
    yield
    # No teardown needed — tests are read-only


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_mcp_protocol.py)
# ---------------------------------------------------------------------------

def _payload(result: Any) -> Any:
    """Extract the tool payload from a CallToolResult.

    FastMCP 1.26+ places the return value in structuredContent['result'].
    """
    if result.structuredContent is not None and "result" in result.structuredContent:
        return result.structuredContent["result"]
    return json.loads(result.content[0].text)


async def _call(name: str, **kwargs: Any) -> Any:
    """Invoke an MCP tool over in-process streams."""
    captured: list[Any] = []

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                mcp._mcp_server.run,
                server_read,
                server_write,
                mcp._mcp_server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=kwargs or None)
                captured.append(result)
                tg.cancel_scope.cancel()

    return captured[0]


# ---------------------------------------------------------------------------
# 1. Discovery flow
# ---------------------------------------------------------------------------

class TestDiscoveryFlow:
    """Simulate first-time exploration of the docs before searching."""

    @pytest.mark.asyncio
    async def test_list_sections_shows_develop(self) -> None:
        result = await _call("lg_list_sections")
        data = _payload(result)
        sections = {s["section"]: s["page_count"] for s in data["sections"]}
        assert "develop" in sections
        assert sections["develop"] > 50

    @pytest.mark.asyncio
    async def test_stats_populated(self) -> None:
        result = await _call("lg_get_stats")
        data = _payload(result)
        assert data["total_pages"] > 100
        assert data["data_freshness"] != "unknown"

    @pytest.mark.asyncio
    async def test_sections_all_have_pages(self) -> None:
        result = await _call("lg_list_sections")
        data = _payload(result)
        for s in data["sections"]:
            assert s["page_count"] >= 1, f"Section '{s['section']}' has 0 pages"

    @pytest.mark.asyncio
    async def test_last_crawled_is_iso_date(self) -> None:
        result = await _call("lg_list_sections")
        data = _payload(result)
        from datetime import datetime
        last = data["last_crawled"]
        assert last != "never", "Cache has never been crawled"
        # Should parse as ISO datetime without error
        datetime.fromisoformat(last)


# ---------------------------------------------------------------------------
# 2. Real content search
# ---------------------------------------------------------------------------

class TestSearchRealContent:
    """Search for actual webOS TV ecosystem terms."""

    @pytest.mark.asyncio
    async def test_search_luna_returns_results(self) -> None:
        result = await _call("lg_search_docs", query="Luna")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data, dict)
        assert data["count"] > 0

    @pytest.mark.asyncio
    async def test_search_luna_results_in_develop_section(self) -> None:
        result = await _call("lg_search_docs", query="Luna")
        data = _payload(result)
        sections = {r["section"] for r in data["results"]}
        assert "develop" in sections

    @pytest.mark.asyncio
    async def test_search_webostvjs_no_protocol_error(self) -> None:
        """webOSTV.js must not cause an FTS5 syntax error."""
        result = await _call("lg_search_docs", query="webOSTV.js")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_search_webostvjs_finds_relevant_pages(self) -> None:
        result = await _call("lg_search_docs", query="webOSTV.js")
        data = _payload(result)
        assert data["count"] > 0
        paths_and_titles = " ".join(
            r["path"] + r.get("title", "") for r in data["results"]
        ).lower()
        assert "webostv" in paths_and_titles

    @pytest.mark.asyncio
    async def test_search_getting_started(self) -> None:
        result = await _call("lg_search_docs", query="getting started")
        data = _payload(result)
        assert data["count"] > 0
        paths = [r["path"] for r in data["results"]]
        assert any("getting-started" in p for p in paths)

    @pytest.mark.asyncio
    async def test_search_web_app_results_in_develop(self) -> None:
        result = await _call("lg_search_docs", query="web app development", limit=10)
        data = _payload(result)
        assert data["count"] > 0
        sections = [r["section"] for r in data["results"]]
        assert "develop" in sections

    @pytest.mark.asyncio
    async def test_prefix_search_webostv(self) -> None:
        """Trailing * prefix search must return results."""
        result = await _call("lg_search_docs", query="webOSTV*")
        data = _payload(result)
        assert data["count"] > 0

    @pytest.mark.asyncio
    async def test_search_app_debugging(self) -> None:
        result = await _call("lg_search_docs", query="app debugging")
        data = _payload(result)
        assert data["count"] > 0
        paths = [r["path"] for r in data["results"]]
        assert any("debug" in p for p in paths)

    @pytest.mark.asyncio
    async def test_snippet_highlights_query_term(self) -> None:
        """FTS5 snippets must contain the query term wrapped in brackets."""
        result = await _call("lg_search_docs", query="Luna")
        data = _payload(result)
        assert data["count"] > 0
        snippets = [r.get("snippet", "") for r in data["results"]]
        assert any("[Luna]" in s or "[luna]" in s.lower() for s in snippets)

    @pytest.mark.asyncio
    async def test_rank_is_numeric(self) -> None:
        result = await _call("lg_search_docs", query="Luna")
        data = _payload(result)
        assert data["count"] > 0
        for row in data["results"]:
            assert isinstance(row["rank"], (int, float))


# ---------------------------------------------------------------------------
# 3. Section drill-down
# ---------------------------------------------------------------------------

class TestSectionDrillDown:
    """Filter searches by section — like an AI narrowing scope."""

    @pytest.mark.asyncio
    async def test_search_in_develop_all_results_in_develop(self) -> None:
        result = await _call("lg_search_by_section", section="develop", query="web app")
        data = _payload(result)
        assert data["count"] > 0
        for row in data["results"]:
            assert row["section"] == "develop"

    @pytest.mark.asyncio
    async def test_search_in_faq_all_results_in_faq(self) -> None:
        result = await _call("lg_search_by_section", section="faq", query="app")
        data = _payload(result)
        # May have no results if FAQ has no match — that's valid; just validate shape
        if data["count"] == 0:
            return  # No results — acceptable
        for row in data["results"]:
            assert row["section"] == "faq"

    @pytest.mark.asyncio
    async def test_section_filter_gives_fewer_than_global(self) -> None:
        """lg_search_by_section('develop', q) <= lg_search_docs(q) for any query."""
        result_all = await _call("lg_search_docs", query="app", limit=100)
        result_dev = await _call("lg_search_by_section", section="develop", query="app", limit=100)
        all_data = _payload(result_all)
        dev_data = _payload(result_dev)
        assert all_data["count"] > 0
        assert dev_data["count"] <= all_data["count"]

    @pytest.mark.asyncio
    async def test_develop_and_faq_differ_for_same_query(self) -> None:
        result_dev = await _call("lg_search_by_section", section="develop", query="app")
        result_faq = await _call("lg_search_by_section", section="faq", query="app")
        dev_data = _payload(result_dev)
        faq_data = _payload(result_faq)
        dev_paths = {r["path"] for r in dev_data["results"]}
        faq_paths = {r["path"] for r in faq_data["results"]}
        # Sections must not share the same pages
        assert dev_paths.isdisjoint(faq_paths)


# ---------------------------------------------------------------------------
# 4. Page navigation
# ---------------------------------------------------------------------------

class TestGetPageNavigation:
    """Navigate to specific pages by exact path or fuzzy fragment."""

    @pytest.mark.asyncio
    async def test_get_exact_path_getting_started(self) -> None:
        result = await _call("lg_get_page", path="/develop/getting-started")
        data = _payload(result)
        assert "content" in data
        assert "message" not in data
        assert "getting" in data["title"].lower() or "started" in data["title"].lower()

    @pytest.mark.asyncio
    async def test_get_page_all_fields_present(self) -> None:
        result = await _call("lg_get_page", path="/develop/getting-started")
        data = _payload(result)
        for key in ("url", "path", "section", "title", "content", "crawled_at"):
            assert key in data, f"Missing field: {key}"

    @pytest.mark.asyncio
    async def test_get_page_content_is_substantial(self) -> None:
        result = await _call("lg_get_page", path="/develop/getting-started")
        data = _payload(result)
        assert len(data.get("content", "")) > 100

    @pytest.mark.asyncio
    async def test_get_page_url_is_lge_domain(self) -> None:
        result = await _call("lg_get_page", path="/develop/getting-started")
        data = _payload(result)
        assert "lge.com" in data["url"]

    @pytest.mark.asyncio
    async def test_fuzzy_path_match_getting_started(self) -> None:
        result = await _call("lg_get_page", path="getting-started")
        data = _payload(result)
        assert "content" in data
        assert data.get("_fuzzy_match") is True
        assert "getting-started" in data["path"]

    @pytest.mark.asyncio
    async def test_get_page_section_matches_path(self) -> None:
        result = await _call("lg_get_page", path="/develop/getting-started")
        data = _payload(result)
        assert data["section"] == "develop"

    @pytest.mark.asyncio
    async def test_missing_page_returns_message_not_error(self) -> None:
        result = await _call("lg_get_page", path="/develop/this-page-does-not-exist-xyz999")
        assert not result.isError  # MCP-level success
        data = _payload(result)
        assert "message" in data


# ---------------------------------------------------------------------------
# 5. Multi-step workflows
# ---------------------------------------------------------------------------

class TestMultiStepWorkflow:
    """Simulate full AI assistant interaction patterns."""

    @pytest.mark.asyncio
    async def test_search_then_navigate(self) -> None:
        """Step 1: search → Step 2: read the top result."""
        search_result = await _call("lg_search_docs", query="Luna service", limit=5)
        data = _payload(search_result)
        assert data["count"] > 0
        top_path = data["results"][0]["path"]

        page_result = await _call("lg_get_page", path=top_path)
        page = _payload(page_result)
        assert "content" in page
        assert len(page["content"]) > 0

    @pytest.mark.asyncio
    async def test_list_sections_then_search_in_top_section(self) -> None:
        """Step 1: discover sections → Step 2: search inside the biggest one."""
        sections_result = await _call("lg_list_sections")
        sections_data = _payload(sections_result)
        top_section = max(sections_data["sections"], key=lambda s: s["page_count"])
        section_name = top_section["section"]

        search_result = await _call("lg_search_by_section", section=section_name, query="app")
        data = _payload(search_result)
        for row in data["results"]:
            if "section" in row:
                assert row["section"] == section_name

    @pytest.mark.asyncio
    async def test_stats_then_search_result_count_consistent(self) -> None:
        """Stats total_pages must be >= number of search results."""
        stats_result = await _call("lg_get_stats")
        stats = _payload(stats_result)
        total = stats["total_pages"]

        search_result = await _call("lg_search_docs", query="webOS", limit=100)
        data = _payload(search_result)
        assert data["count"] <= total

    @pytest.mark.asyncio
    async def test_fuzzy_fallback_workflow(self) -> None:
        """Step 1: search → take top path → try fragment → fuzzy match."""
        search_result = await _call("lg_search_docs", query="getting started build app", limit=3)
        data = _payload(search_result)
        assert data["count"] > 0
        top_path = data["results"][0]["path"]

        fragment = top_path.rstrip("/").rsplit("/", 1)[-1]
        page_result = await _call("lg_get_page", path=fragment)
        page = _payload(page_result)
        assert "content" in page
        assert fragment in page["path"]

    @pytest.mark.asyncio
    async def test_refine_search_with_section_filter(self) -> None:
        """Step 1: broad search → Step 2: narrow to 'develop' section."""
        broad_result = await _call("lg_search_docs", query="app", limit=20)
        broad = _payload(broad_result)

        narrow_result = await _call("lg_search_by_section", section="develop", query="app", limit=20)
        narrow = _payload(narrow_result)

        for row in narrow["results"]:
            if "section" in row:
                assert row["section"] == "develop"

        narrow_paths = {r["path"] for r in narrow["results"]}
        broad_paths = {r["path"] for r in broad["results"]}
        assert narrow_paths <= broad_paths or len(narrow_paths) > 0

    @pytest.mark.asyncio
    async def test_multiple_pages_from_search(self) -> None:
        """Retrieve 3 pages found via search and verify all have real content."""
        search_result = await _call("lg_search_docs", query="webOS TV", limit=3)
        data = _payload(search_result)
        assert data["count"] >= 2

        for row in data["results"]:
            page_result = await _call("lg_get_page", path=row["path"])
            page = _payload(page_result)
            assert "content" in page, f"No content for {row['path']}"
            assert len(page["content"]) > 50, f"Content too short for {row['path']}"


# ---------------------------------------------------------------------------
# 6. Edge cases with real data
# ---------------------------------------------------------------------------

class TestEdgeCasesRealData:
    """Validate data quality and edge cases using real cached content."""

    @pytest.mark.asyncio
    async def test_crawled_at_is_iso_timestamp(self) -> None:
        from datetime import datetime
        result = await _call("lg_search_docs", query="webOS")
        data = _payload(result)
        assert data["count"] > 0
        for row in data["results"]:
            datetime.fromisoformat(row["crawled_at"])

    @pytest.mark.asyncio
    async def test_page_url_is_webostv_domain(self) -> None:
        result = await _call("lg_search_docs", query="Luna")
        data = _payload(result)
        assert data["count"] > 0
        top_path = data["results"][0]["path"]
        page_result = await _call("lg_get_page", path=top_path)
        page = _payload(page_result)
        assert "webostv.developer.lge.com" in page["url"]

    @pytest.mark.asyncio
    async def test_search_limit_10_default(self) -> None:
        """Default limit=10 should not return more than 10 results."""
        result = await _call("lg_search_docs", query="app")
        data = _payload(result)
        assert data["count"] <= 10

    @pytest.mark.asyncio
    async def test_db_size_mb_positive(self) -> None:
        result = await _call("lg_get_stats")
        data = _payload(result)
        assert data["db_size_mb"] > 0.0

    @pytest.mark.asyncio
    async def test_days_since_crawl_is_integer(self) -> None:
        result = await _call("lg_get_stats")
        data = _payload(result)
        assert isinstance(data["days_since_crawl"], int)
        assert data["days_since_crawl"] >= 0

    @pytest.mark.asyncio
    async def test_sections_in_stats_match_list_sections(self) -> None:
        """Section names from lg_get_stats and lg_list_sections must be identical."""
        stats_result = await _call("lg_get_stats")
        stats = _payload(stats_result)
        sections_result = await _call("lg_list_sections")
        sections = _payload(sections_result)

        stats_names = {s["section"] for s in stats["sections"]}
        list_names = {s["section"] for s in sections["sections"]}
        assert stats_names == list_names

    @pytest.mark.asyncio
    async def test_search_dot_notation_service_uri(self) -> None:
        """Luna service URIs like com.webos.service.xxx must not crash FTS5."""
        result = await _call("lg_search_docs", query="com.webos.service")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data, dict)
