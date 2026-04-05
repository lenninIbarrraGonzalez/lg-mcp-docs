"""Real MCP protocol tests — tools are invoked via ClientSession over in-memory streams.

These tests exercise the full MCP stack: tool registration, serialization,
and response parsing. They do NOT call Python functions directly.
"""
import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

import lg_docs_mcp.db as db_mod
from lg_docs_mcp.server import mcp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path):
    """Redirect DB to a temp dir and reset thread-local connection."""
    original_path = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "docs.db"
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
    for i in range(n):
        db_mod.upsert_doc(
            url=f"https://webostv.developer.lge.com/develop/item{i}",
            path=f"/develop/item{i}",
            section="develop",
            title=f"Item {i}",
            content=f"Content about Luna Service and item {i} details.",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(result: Any) -> Any:
    """Extract the tool payload from a CallToolResult.

    FastMCP 1.26+ places the return value in structuredContent['result'].
    For dict-returning tools structuredContent is the authoritative value.
    """
    if result.structuredContent is not None and "result" in result.structuredContent:
        return result.structuredContent["result"]
    # Fallback: parse the first TextContent block as JSON
    return json.loads(result.content[0].text)


async def _call(name: str, **kwargs: Any) -> Any:
    """Call an MCP tool over in-process streams and return the raw result."""
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
# Tool discovery
# ---------------------------------------------------------------------------

async def _list_tools() -> Any:
    """Call list_tools over in-process streams and return the result."""
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
                result = await session.list_tools()
                captured.append(result)
                tg.cancel_scope.cancel()

    return captured[0]


class TestListTools:
    @pytest.mark.asyncio
    async def test_all_tools_registered(self) -> None:
        tools = await _list_tools()
        names = {t.name for t in tools.tools}
        assert "lg_search_docs" in names
        assert "lg_search_by_section" in names
        assert "lg_get_page" in names
        assert "lg_list_sections" in names
        assert "lg_get_stats" in names
        assert "lg_refresh_cache" in names

    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self) -> None:
        tools = await _list_tools()
        by_name = {t.name: t for t in tools.tools}
        for name in ("lg_search_docs", "lg_get_page", "lg_list_sections", "lg_get_stats"):
            assert by_name[name].description, f"{name} has no description"

    @pytest.mark.asyncio
    async def test_tools_have_annotations(self) -> None:
        """All read-only tools must expose readOnlyHint=True."""
        tools = await _list_tools()
        by_name = {t.name: t for t in tools.tools}
        read_only_tools = (
            "lg_search_docs", "lg_search_by_section", "lg_get_page",
            "lg_list_sections", "lg_get_stats",
        )
        for name in read_only_tools:
            tool = by_name[name]
            assert tool.annotations is not None, f"{name} has no annotations"
            assert tool.annotations.readOnlyHint is True, f"{name} readOnlyHint should be True"

    @pytest.mark.asyncio
    async def test_refresh_cache_not_readonly(self) -> None:
        tools = await _list_tools()
        by_name = {t.name: t for t in tools.tools}
        tool = by_name["lg_refresh_cache"]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False


# ---------------------------------------------------------------------------
# lg_search_docs
# ---------------------------------------------------------------------------

class TestSearchDocsTool:
    @pytest.mark.asyncio
    async def test_returns_results(self) -> None:
        _seed(3)
        result = await _call("lg_search_docs", query="Luna")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data, dict)
        assert data["count"] > 0
        assert "path" in data["results"][0]

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self) -> None:
        result = await _call("lg_search_docs", query="")
        assert result.isError

    @pytest.mark.asyncio
    async def test_whitespace_only_query_returns_error(self) -> None:
        result = await _call("lg_search_docs", query="   ")
        assert result.isError

    @pytest.mark.asyncio
    async def test_no_results_returns_message(self) -> None:
        _seed(1)
        result = await _call("lg_search_docs", query="xyzabsolutelynothing9999")
        assert not result.isError
        data = _payload(result)
        assert data["count"] == 0
        assert "message" in data

    @pytest.mark.asyncio
    async def test_limit_respected(self) -> None:
        _seed(5)
        result = await _call("lg_search_docs", query="Luna", limit=2)
        assert not result.isError
        data = _payload(result)
        assert data["count"] <= 2

    @pytest.mark.asyncio
    async def test_limit_zero_returns_error(self) -> None:
        result = await _call("lg_search_docs", query="Luna", limit=0)
        assert result.isError

    @pytest.mark.asyncio
    async def test_limit_over_100_returns_error(self) -> None:
        result = await _call("lg_search_docs", query="Luna", limit=500)
        assert result.isError

    @pytest.mark.asyncio
    async def test_result_shape(self) -> None:
        _seed(2)
        result = await _call("lg_search_docs", query="Luna")
        assert not result.isError
        data = _payload(result)
        assert data["count"] > 0
        row = data["results"][0]
        for key in ("path", "section", "title", "snippet", "rank"):
            assert key in row, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_pagination_envelope(self) -> None:
        """Response must always include count, offset, has_more keys."""
        _seed(3)
        result = await _call("lg_search_docs", query="Luna")
        assert not result.isError
        data = _payload(result)
        assert "count" in data
        assert "offset" in data
        assert "has_more" in data
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_fts5_error_returns_tool_error(self) -> None:
        """An unmatched quote triggers FTS5 syntax error → tool error."""
        _seed(1)
        result = await _call("lg_search_docs", query='"unclosed')
        assert result.isError

    @pytest.mark.asyncio
    async def test_response_format_markdown(self) -> None:
        _seed(2)
        result = await _call("lg_search_docs", query="Luna", response_format="markdown")
        assert not result.isError
        text = result.content[0].text
        assert "Luna" in text

    @pytest.mark.asyncio
    async def test_offset_pagination(self) -> None:
        _seed(5)
        page1 = await _call("lg_search_docs", query="Luna", limit=2, offset=0)
        page2 = await _call("lg_search_docs", query="Luna", limit=2, offset=2)
        d1 = _payload(page1)
        d2 = _payload(page2)
        assert d1["offset"] == 0
        assert d2["offset"] == 2
        paths1 = [r["path"] for r in d1["results"]]
        paths2 = [r["path"] for r in d2["results"]]
        assert paths1 != paths2


# ---------------------------------------------------------------------------
# lg_search_by_section
# ---------------------------------------------------------------------------

class TestSearchBySectionTool:
    @pytest.mark.asyncio
    async def test_returns_results_for_valid_section(self) -> None:
        _seed(3)
        result = await _call("lg_search_by_section", section="develop", query="Luna")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data, dict)
        assert data["count"] > 0

    @pytest.mark.asyncio
    async def test_results_belong_to_section(self) -> None:
        _seed(3)
        db_mod.upsert_doc(
            url="https://webostv.developer.lge.com/references/api",
            path="/references/api",
            section="references",
            title="API Ref",
            content="Luna Service API reference content.",
        )
        result = await _call("lg_search_by_section", section="develop", query="Luna")
        assert not result.isError
        data = _payload(result)
        for row in data["results"]:
            if "section" in row:
                assert row["section"] == "develop"

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self) -> None:
        result = await _call("lg_search_by_section", section="develop", query="")
        assert result.isError

    @pytest.mark.asyncio
    async def test_empty_section_returns_error(self) -> None:
        result = await _call("lg_search_by_section", section="", query="Luna")
        assert result.isError

    @pytest.mark.asyncio
    async def test_no_results_returns_message(self) -> None:
        _seed(1)
        result = await _call("lg_search_by_section", section="develop", query="xyznothing9999")
        assert not result.isError
        data = _payload(result)
        assert data["count"] == 0
        assert "message" in data


# ---------------------------------------------------------------------------
# lg_get_page
# ---------------------------------------------------------------------------

class TestGetPageTool:
    @pytest.mark.asyncio
    async def test_exact_path_returns_page(self) -> None:
        _seed(1)
        result = await _call("lg_get_page", path="/develop/item0")
        assert not result.isError
        data = _payload(result)
        assert "content" in data
        assert data["path"] == "/develop/item0"

    @pytest.mark.asyncio
    async def test_page_has_expected_fields(self) -> None:
        _seed(1)
        result = await _call("lg_get_page", path="/develop/item0")
        assert not result.isError
        data = _payload(result)
        for key in ("url", "path", "section", "title", "content", "crawled_at"):
            assert key in data, f"Missing field: {key}"

    @pytest.mark.asyncio
    async def test_fuzzy_path_returns_page(self) -> None:
        _seed(1)
        result = await _call("lg_get_page", path="item0")
        assert not result.isError
        data = _payload(result)
        assert "content" in data
        assert "item0" in data["path"]

    @pytest.mark.asyncio
    async def test_fuzzy_match_sets_indicator(self) -> None:
        _seed(1)
        result = await _call("lg_get_page", path="item0")
        assert not result.isError
        data = _payload(result)
        assert data.get("_fuzzy_match") is True

    @pytest.mark.asyncio
    async def test_exact_match_no_fuzzy_indicator(self) -> None:
        _seed(1)
        result = await _call("lg_get_page", path="/develop/item0")
        assert not result.isError
        data = _payload(result)
        assert "_fuzzy_match" not in data

    @pytest.mark.asyncio
    async def test_missing_page_returns_message(self) -> None:
        result = await _call("lg_get_page", path="/develop/totally/missing")
        assert not result.isError
        data = _payload(result)
        assert "message" in data

    @pytest.mark.asyncio
    async def test_empty_path_returns_error(self) -> None:
        result = await _call("lg_get_page", path="")
        assert result.isError


# ---------------------------------------------------------------------------
# lg_list_sections
# ---------------------------------------------------------------------------

class TestListSectionsTool:
    @pytest.mark.asyncio
    async def test_returns_sections_and_last_crawled(self) -> None:
        _seed(3)
        result = await _call("lg_list_sections")
        assert not result.isError
        data = _payload(result)
        assert "sections" in data
        assert "last_crawled" in data

    @pytest.mark.asyncio
    async def test_sections_contain_develop(self) -> None:
        _seed(3)
        result = await _call("lg_list_sections")
        assert not result.isError
        data = _payload(result)
        sections = [s["section"] for s in data["sections"]]
        assert "develop" in sections

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_list(self) -> None:
        result = await _call("lg_list_sections")
        assert not result.isError
        data = _payload(result)
        assert data["sections"] == []

    @pytest.mark.asyncio
    async def test_page_count_correct(self) -> None:
        _seed(4)
        result = await _call("lg_list_sections")
        assert not result.isError
        data = _payload(result)
        develop = next(s for s in data["sections"] if s["section"] == "develop")
        assert develop["page_count"] == 4

    @pytest.mark.asyncio
    async def test_last_crawled_never_when_not_set(self) -> None:
        result = await _call("lg_list_sections")
        assert not result.isError
        data = _payload(result)
        assert data["last_crawled"] == "never"


# ---------------------------------------------------------------------------
# lg_get_stats
# ---------------------------------------------------------------------------

class TestGetStatsTool:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self) -> None:
        _seed(2)
        result = await _call("lg_get_stats")
        assert not result.isError
        data = _payload(result)
        for key in ("total_pages", "db_size_mb", "sections", "last_crawled",
                    "days_since_crawl", "data_freshness"):
            assert key in data, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_total_pages_count(self) -> None:
        _seed(5)
        result = await _call("lg_get_stats")
        assert not result.isError
        data = _payload(result)
        assert data["total_pages"] == 5

    @pytest.mark.asyncio
    async def test_freshness_unknown_when_never_crawled(self) -> None:
        result = await _call("lg_get_stats")
        assert not result.isError
        data = _payload(result)
        assert data["data_freshness"] == "unknown"
        assert data["last_crawled"] == "never"
        assert data["days_since_crawl"] is None

    @pytest.mark.asyncio
    async def test_freshness_fresh_after_recent_crawl(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        db_mod.set_cache_meta("last_crawled", dt.isoformat())

        result = await _call("lg_get_stats")
        assert not result.isError
        data = _payload(result)
        assert data["data_freshness"] == "fresh"

    @pytest.mark.asyncio
    async def test_db_size_mb_is_float(self) -> None:
        result = await _call("lg_get_stats")
        assert not result.isError
        data = _payload(result)
        assert isinstance(data["db_size_mb"], float)
