"""Unit tests for lg_docs_mcp.checker."""
import concurrent.futures
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _set_last_crawled(days_ago: int) -> None:
    import lg_docs_mcp.db as db_mod
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db_mod.set_cache_meta("last_crawled", dt.isoformat())


class TestIsStale:
    def test_fresh_returns_false(self) -> None:
        _set_last_crawled(1)
        from lg_docs_mcp.checker import is_stale
        assert is_stale(max_age_days=7) is False

    def test_stale_returns_true(self) -> None:
        _set_last_crawled(10)
        from lg_docs_mcp.checker import is_stale
        assert is_stale(max_age_days=7) is True

    def test_at_threshold_returns_true(self) -> None:
        _set_last_crawled(7)
        from lg_docs_mcp.checker import is_stale
        # exactly at threshold (>=) → stale
        assert is_stale(max_age_days=7) is True

    def test_never_crawled_returns_true(self) -> None:
        # No last_crawled set → stale
        from lg_docs_mcp.checker import is_stale
        assert is_stale(max_age_days=7) is True

    def test_custom_threshold_fresh(self) -> None:
        _set_last_crawled(20)
        from lg_docs_mcp.checker import is_stale
        assert is_stale(max_age_days=30) is False

    def test_custom_threshold_stale(self) -> None:
        _set_last_crawled(20)
        from lg_docs_mcp.checker import is_stale
        assert is_stale(max_age_days=15) is True


class TestRunRefresh:
    def test_returns_saved_skipped(self) -> None:
        from lg_docs_mcp import checker

        mock_future = MagicMock()
        mock_future.result.return_value = {"saved": 5, "skipped": 2}

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.return_value = mock_future

        with patch("lg_docs_mcp.checker.concurrent.futures.ProcessPoolExecutor", return_value=mock_executor):
            result = checker.run_refresh(max_depth=5, max_pages=100)

        assert result["status"] == "success"
        assert result["saved"] == 5
        assert result["skipped"] == 2

    def test_timeout_returns_empty(self) -> None:
        from lg_docs_mcp import checker

        mock_future = MagicMock()
        mock_future.result.side_effect = concurrent.futures.TimeoutError()

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.return_value = mock_future

        with patch("lg_docs_mcp.checker.concurrent.futures.ProcessPoolExecutor", return_value=mock_executor):
            result = checker.run_refresh()

        assert result["status"] == "error"
        assert result["saved"] == 0
        assert result["skipped"] == 0
        assert result["error"] == "timeout"

    def test_exception_returns_empty(self) -> None:
        from lg_docs_mcp import checker

        mock_future = MagicMock()
        mock_future.result.side_effect = RuntimeError("boom")

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.return_value = mock_future

        with patch("lg_docs_mcp.checker.concurrent.futures.ProcessPoolExecutor", return_value=mock_executor):
            result = checker.run_refresh()

        assert result["status"] == "error"
        assert result["saved"] == 0
        assert result["skipped"] == 0
        assert result["error"] == "exception"


class TestAutoRefreshLoop:
    def _make_sleep_stopper(self) -> MagicMock:
        """Return a mock sleep that raises KeyboardInterrupt on first call."""
        mock_sleep = MagicMock(side_effect=KeyboardInterrupt)
        return mock_sleep

    def test_calls_refresh_when_stale(self) -> None:
        from lg_docs_mcp import checker

        mock_sleep = self._make_sleep_stopper()
        with patch.object(checker, "is_stale", return_value=True), \
             patch.object(checker, "run_refresh") as mock_refresh, \
             patch.object(checker.time, "sleep", mock_sleep):
            with pytest.raises(KeyboardInterrupt):
                checker.auto_refresh_loop(check_interval_hours=1, max_age_days=7)

        mock_refresh.assert_called_once()

    def test_skips_refresh_when_fresh(self) -> None:
        from lg_docs_mcp import checker

        mock_sleep = self._make_sleep_stopper()
        with patch.object(checker, "is_stale", return_value=False), \
             patch.object(checker, "run_refresh") as mock_refresh, \
             patch.object(checker.time, "sleep", mock_sleep):
            with pytest.raises(KeyboardInterrupt):
                checker.auto_refresh_loop(check_interval_hours=1, max_age_days=7)

        mock_refresh.assert_not_called()

    def test_sleep_duration(self) -> None:
        from lg_docs_mcp import checker

        mock_sleep = self._make_sleep_stopper()
        with patch.object(checker, "is_stale", return_value=False), \
             patch.object(checker, "run_refresh"), \
             patch.object(checker.time, "sleep", mock_sleep):
            with pytest.raises(KeyboardInterrupt):
                checker.auto_refresh_loop(check_interval_hours=6, max_age_days=7)

        mock_sleep.assert_called_once_with(6 * 3600)

    def test_continues_after_exception(self) -> None:
        """Loop catches exceptions from run_refresh and continues to sleep."""
        from lg_docs_mcp import checker

        call_count = 0

        def raise_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt

        with patch.object(checker, "is_stale", return_value=True), \
             patch.object(checker, "run_refresh", side_effect=RuntimeError("fail")), \
             patch.object(checker.time, "sleep", side_effect=raise_then_stop):
            with pytest.raises(KeyboardInterrupt):
                checker.auto_refresh_loop(check_interval_hours=1, max_age_days=7)

        assert call_count >= 2


class TestStartAutoRefreshDaemon:
    def test_returns_thread(self) -> None:
        from lg_docs_mcp import checker

        with patch.object(checker, "auto_refresh_loop", return_value=None):
            t = checker.start_auto_refresh_daemon()
            t.join(timeout=1)

        assert isinstance(t, threading.Thread)

    def test_thread_is_daemon(self) -> None:
        from lg_docs_mcp import checker

        with patch.object(checker, "auto_refresh_loop", return_value=None):
            t = checker.start_auto_refresh_daemon()
            t.join(timeout=1)

        assert t.daemon is True

    def test_kwargs_forwarded(self) -> None:
        from lg_docs_mcp import checker

        calls: list[dict] = []

        def capture(**kwargs) -> None:  # type: ignore[type-arg]
            calls.append(kwargs)

        with patch.object(checker, "auto_refresh_loop", side_effect=capture):
            t = checker.start_auto_refresh_daemon(
                check_interval_hours=12,
                max_age_days=3,
                max_depth=5,
                max_pages=500,
            )
            t.join(timeout=1)

        assert len(calls) == 1
        assert calls[0]["check_interval_hours"] == 12
        assert calls[0]["max_age_days"] == 3
        assert calls[0]["max_depth"] == 5
        assert calls[0]["max_pages"] == 500
