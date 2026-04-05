import concurrent.futures
import logging
import threading
import time
from typing import Any

from lg_docs_mcp import db

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_DAYS: int = 7
_DEFAULT_CHECK_INTERVAL_HOURS: int = 24
_DEFAULT_MAX_DEPTH: int = 10
_DEFAULT_MAX_PAGES: int = 2000


def is_stale(max_age_days: int = _DEFAULT_MAX_AGE_DAYS) -> bool:
    """Return True if cache is older than max_age_days, or never crawled."""
    stats: dict[str, Any] = db.get_stats()
    days_raw: Any = stats.get("days_since_crawl")
    days: int | None = int(days_raw) if isinstance(days_raw, (int, float)) else None
    if days is None:
        logger.debug("is_stale: never crawled — treating as stale")
        return True
    stale = days >= max_age_days
    logger.debug("is_stale: days=%d threshold=%d stale=%s", days, max_age_days, stale)
    return stale


def run_refresh(
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Run the crawler in a subprocess. Returns saved/skipped counts."""
    from lg_docs_mcp import scraper

    logger.info("run_refresh: starting max_depth=%d max_pages=%d", max_depth, max_pages)
    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scraper.crawl_docs_sync, max_depth, max_pages)
        try:
            result: dict[str, Any] = future.result(timeout=7200)
        except concurrent.futures.TimeoutError:
            logger.error("run_refresh: timed out after 2 hours")
            return {"status": "error", "saved": 0, "skipped": 0, "error": "timeout"}
        except Exception:
            logger.exception("run_refresh: crawl raised an exception")
            return {"status": "error", "saved": 0, "skipped": 0, "error": "exception"}
    logger.info("run_refresh: complete %s", result)
    result["status"] = "success"
    return result


def auto_refresh_loop(
    check_interval_hours: int = _DEFAULT_CHECK_INTERVAL_HOURS,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> None:
    """Infinite loop: check staleness on startup and every interval, refresh if needed."""
    logger.info(
        "auto_refresh_loop: started interval=%dh max_age=%dd",
        check_interval_hours, max_age_days,
    )
    while True:
        try:
            if is_stale(max_age_days):
                logger.info("auto_refresh_loop: cache is stale, triggering refresh")
                run_refresh(max_depth=max_depth, max_pages=max_pages)
            else:
                logger.debug("auto_refresh_loop: cache is fresh, skipping")
        except Exception:
            logger.exception("auto_refresh_loop: unexpected error")
        time.sleep(check_interval_hours * 3600)


def start_auto_refresh_daemon(
    check_interval_hours: int = _DEFAULT_CHECK_INTERVAL_HOURS,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> threading.Thread:
    """Start auto_refresh_loop in a daemon thread and return it."""
    t = threading.Thread(
        target=auto_refresh_loop,
        kwargs={
            "check_interval_hours": check_interval_hours,
            "max_age_days": max_age_days,
            "max_depth": max_depth,
            "max_pages": max_pages,
        },
        name="auto-refresh-daemon",
        daemon=True,
    )
    t.start()
    logger.info("start_auto_refresh_daemon: thread started id=%d", t.ident or 0)
    return t
