import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

_default_db = Path.home() / ".cache" / "lg-docs-mcp" / "docs.db"

# Freshness thresholds (configurable via env vars, values in days)
_FRESH_DAYS = int(os.getenv("LG_DOCS_FRESH_DAYS", "7"))

# Sections considered valid documentation sections (excludes crawl artifacts like null, contact, discover)
_KNOWN_TOP_SECTIONS = frozenset({
    "develop", "distribute", "faq", "news", "more", "notice", "other",
})
_AGING_DAYS = int(os.getenv("LG_DOCS_AGING_DAYS", "30"))
_STALE_DAYS = int(os.getenv("LG_DOCS_STALE_DAYS", "90"))
DB_PATH = Path(os.getenv("LG_DOCS_DB_PATH", str(_default_db)))

_local = threading.local()


def _get_thread_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        logger.debug("Opening SQLite connection for thread %s", threading.current_thread().name)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return cast(sqlite3.Connection, _local.conn)


def get_conn() -> sqlite3.Connection:
    return _get_thread_conn()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")  # wait up to 30s before failing on lock
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                section TEXT,
                title TEXT,
                content TEXT,
                content_hash TEXT,
                crawled_at TEXT DEFAULT (datetime('now'))
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts
            USING fts5(
                title,
                content,
                content='docs',
                content_rowid='id',
                tokenize="unicode61 tokenchars '.-_'",
                prefix='2 3 4'
            );

            CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
                INSERT INTO docs_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
                INSERT INTO docs_fts(docs_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
                INSERT INTO docs_fts(docs_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
                INSERT INTO docs_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;

            CREATE TABLE IF NOT EXISTS cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path);
            CREATE INDEX IF NOT EXISTS idx_docs_section ON docs(section);
        """)
    # Migrate existing DBs: add content_hash column if missing
    columns = {row[1] for row in conn.execute("PRAGMA table_info(docs)").fetchall()}
    if "content_hash" not in columns:
        conn.execute("ALTER TABLE docs ADD COLUMN content_hash TEXT")
        logger.debug("Migrated docs table: added content_hash column")
    # Migrate existing DBs: add FTS5 prefix indexes if missing
    try:
        with conn:
            conn.execute("INSERT INTO docs_fts_config(k, v) VALUES('prefix', '2 3 4')")
            conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        logger.info("Migrated docs_fts: added prefix indexes")
    except sqlite3.IntegrityError:
        pass  # prefix config already exists — UNIQUE constraint on key


def upsert_doc(url: str, path: str, section: str | None, title: str, content: str, content_hash: str | None = None) -> None:
    conn = get_conn()
    logger.debug("Upserting doc: %s", path)
    with conn:
        conn.execute("""
            INSERT INTO docs (url, path, section, title, content, content_hash, crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                path=excluded.path,
                section=excluded.section,
                title=excluded.title,
                content=excluded.content,
                content_hash=excluded.content_hash,
                crawled_at=excluded.crawled_at
        """, (url, path, section, title, content, content_hash, datetime.now(timezone.utc).isoformat()))


def get_page_hash(path: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT content_hash FROM docs WHERE path = ?", (path,)
    ).fetchone()
    return row["content_hash"] if row else None


def get_path_by_hash(content_hash: str) -> str | None:
    """Return the path of the first page with the given content hash, or None.

    Used during crawling to detect duplicate content at different URLs (e.g. when
    a redirect causes the same page to be crawled under two different paths).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT path FROM docs WHERE content_hash = ? LIMIT 1", (content_hash,)
    ).fetchone()
    return row["path"] if row else None


def _dedupe(rows: list[Any], limit: int) -> list[dict[str, Any]]:
    """Deduplicate FTS5 rows by path (FTS5 can return multiple hits per doc)."""
    seen: set[str] = set()
    result = []
    for row in rows:
        path = row["path"]
        if path not in seen:
            seen.add(path)
            result.append(dict(row))
            if len(result) >= limit:
                break
    return result


def _sanitize_fts_query(query: str) -> str:
    """Quote individual tokens that contain FTS5 special characters.

    Each whitespace-separated token is handled independently:
    - Tokens containing '.', '-', '/', '(' or ')' are wrapped in double quotes.
    - A trailing '*' prefix operator on a token is preserved outside the quotes.
    - Already-quoted queries (starting with '"') are left as-is.

    This allows multi-term queries like 'webOSTV.js launch' to work correctly
    as AND searches ('"webOSTV.js" launch') instead of being forced into a
    phrase search ('"webOSTV.js launch"') that almost never matches.

    Examples:
        'webOSTV.js launch'      -> '"webOSTV.js" launch'
        'com.webos.service*'     -> '"com.webos.service"*'
        'Luna service'           -> 'Luna service'  (unchanged)
        'getting-started guide'  -> '"getting-started" guide'
        '"webOSTV.js"'           -> '"webOSTV.js"'  (already quoted, unchanged)
    """
    if query.startswith('"'):
        return query

    fts_special = set(".-/()")
    tokens = query.split()
    if not tokens:
        return query

    sanitized = []
    for token in tokens:
        trailing_star = token.endswith('*')
        base = token[:-1] if trailing_star else token
        if any(c in base for c in fts_special):
            quoted = '"' + base.replace('"', '""') + '"'
            sanitized.append(quoted + ('*' if trailing_star else ''))
        else:
            sanitized.append(token)  # preserves trailing * if present

    return ' '.join(sanitized)


def _make_or_query(sanitized: str) -> str | None:
    """Return an OR-based fallback query for multi-token queries.

    Splits the sanitized query into top-level tokens (respecting quoted phrases)
    and joins them with ' OR '. Returns None for single-token queries since
    there is nothing to loosen.

    Examples:
        '"webOSTV.js" launch'  -> '"webOSTV.js" OR launch'
        'web app development'  -> 'web OR app OR development'
        '"webOSTV.js"'         -> None  (single token, no fallback needed)
    """
    tokens = re.findall(r'"[^"]*"[\*]?|\S+', sanitized)
    if len(tokens) <= 1:
        return None
    return ' OR '.join(tokens)


_SYNONYMS: dict[str, list[str]] = {
    "bluetooth":  ["ble", "gatt"],
    "playback":   ["audio", "media", "streaming"],
    "launch":     ["lifecycle", "activate", "appinfo"],
    "sound":      ["audio", "volume"],
    "video":      ["media", "streaming"],
    "network":    ["connection", "wifi"],
    "storage":    ["database", "db8"],
    "sensor":     ["motion", "mrcu"],
}


_DOT_PREFIXES = frozenset({"com", "webos", "service", "lge", "palm", "webostv"})


def _make_dot_split_query(query: str) -> str | None:
    """Return a simplified query by splitting dot-notation service URIs into keywords.

    Only processes tokens that start with 'com.' (Luna service URIs). Tokens like
    'webOSTV.js' or 'getting-started' are left unchanged, and None is returned if
    no com.* token is found.

    Examples:
        'com.webos.service.audio'           -> 'audio'
        'com.webos.service.bluetooth.gatt'  -> 'bluetooth gatt'
        'com.webos.audio volume'            -> 'audio volume'
        'webOSTV.js launch'                 -> None  (no com.* token)
        'Luna service'                      -> None  (no dots)
    """
    tokens = query.split()
    new_tokens: list[str] = []
    found_dot = False
    for token in tokens:
        if '.' in token and not token.startswith('"') and token.lower().startswith('com.'):
            parts = token.rstrip('*').split('.')
            meaningful = [p for p in parts if p.lower() not in _DOT_PREFIXES and len(p) > 1]
            if meaningful:
                found_dot = True
                new_tokens.extend(meaningful)
            else:
                new_tokens.append(token)
        else:
            new_tokens.append(token)
    if not found_dot or not new_tokens:
        return None
    return ' '.join(new_tokens)


def _expand_with_synonyms(query: str) -> str | None:
    """Return a synonym-expanded OR query, or None if no synonyms apply.

    Each token is sanitized individually before joining, so the result is
    already FTS5-safe and must NOT be passed through _sanitize_fts_query().

    Examples:
        'bluetooth guide'  -> '(bluetooth OR ble OR gatt) guide'
        'audio playback'   -> 'audio (playback OR audio OR media OR streaming)'
        'Luna service'     -> None  (no synonyms)
    """
    tokens = query.split()
    if not tokens:
        return None
    expanded_tokens = []
    found_synonym = False
    for token in tokens:
        token_lower = token.lower()
        syns = _SYNONYMS.get(token_lower)
        if syns:
            found_synonym = True
            alternatives = [token_lower] + syns
            sanitized_alts = [_sanitize_fts_query(a) for a in alternatives]
            expanded_tokens.append("(" + " OR ".join(sanitized_alts) + ")")
        else:
            expanded_tokens.append(_sanitize_fts_query(token))
    if not found_synonym:
        return None
    # Use explicit AND so FTS5 correctly parses parenthesized OR groups combined with plain tokens
    return " AND ".join(expanded_tokens)


def search_docs(query: str, limit: int = 10) -> list[dict[str, Any]]:
    conn = get_conn()
    _SQL = """
        SELECT
            d.path,
            d.section,
            d.title,
            snippet(docs_fts, 1, '[', ']', '…', 64) AS snippet,
            bm25(docs_fts) AS rank,
            d.crawled_at
        FROM docs_fts
        JOIN docs d ON d.id = docs_fts.rowid
        WHERE docs_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    # Fetch 3x to ensure enough unique docs after deduplication
    sanitized = _sanitize_fts_query(query)
    rows = conn.execute(_SQL, (sanitized, limit * 3)).fetchall()
    results = _dedupe(rows, limit)
    if not results:
        or_q = _make_or_query(sanitized)
        if or_q:
            rows = conn.execute(_SQL, (or_q, limit * 3)).fetchall()
            results = _dedupe(rows, limit)
    if not results:
        syn_q = _expand_with_synonyms(query)
        if syn_q:
            try:
                rows = conn.execute(_SQL, (syn_q, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    if not results:
        dot_q = _make_dot_split_query(query)
        if dot_q:
            try:
                dot_sanitized = _sanitize_fts_query(dot_q)
                rows = conn.execute(_SQL, (dot_sanitized, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    return results


def search_docs_by_section(query: str, section: str, limit: int = 10) -> list[dict[str, Any]]:
    conn = get_conn()
    _SQL = """
        SELECT
            d.path,
            d.section,
            d.title,
            snippet(docs_fts, 1, '[', ']', '…', 64) AS snippet,
            bm25(docs_fts) AS rank,
            d.crawled_at
        FROM docs_fts
        JOIN docs d ON d.id = docs_fts.rowid
        WHERE docs_fts MATCH ?
          AND d.section = ?
        ORDER BY rank
        LIMIT ?
    """
    sanitized = _sanitize_fts_query(query)
    rows = conn.execute(_SQL, (sanitized, section, limit * 3)).fetchall()
    results = _dedupe(rows, limit)
    if not results:
        or_q = _make_or_query(sanitized)
        if or_q:
            rows = conn.execute(_SQL, (or_q, section, limit * 3)).fetchall()
            results = _dedupe(rows, limit)
    if not results:
        syn_q = _expand_with_synonyms(query)
        if syn_q:
            try:
                rows = conn.execute(_SQL, (syn_q, section, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    if not results:
        dot_q = _make_dot_split_query(query)
        if dot_q:
            try:
                dot_sanitized = _sanitize_fts_query(dot_q)
                rows = conn.execute(_SQL, (dot_sanitized, section, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    return results


def search_docs_by_path_prefix(query: str, path_prefix: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search docs filtered by path prefix (e.g. '/develop/references').

    Used to support logical sub-sections like 'references', 'guides', 'tools'
    that share the top-level section 'develop' but differ in path prefix.
    """
    conn = get_conn()
    _SQL = """
        SELECT
            d.path,
            d.section,
            d.title,
            snippet(docs_fts, 1, '[', ']', '…', 64) AS snippet,
            bm25(docs_fts) AS rank,
            d.crawled_at
        FROM docs_fts
        JOIN docs d ON d.id = docs_fts.rowid
        WHERE docs_fts MATCH ?
          AND d.path LIKE ?
        ORDER BY rank
        LIMIT ?
    """
    sanitized = _sanitize_fts_query(query)
    path_pattern = f"{path_prefix}/%"
    rows = conn.execute(_SQL, (sanitized, path_pattern, limit * 3)).fetchall()
    results = _dedupe(rows, limit)
    if not results:
        or_q = _make_or_query(sanitized)
        if or_q:
            rows = conn.execute(_SQL, (or_q, path_pattern, limit * 3)).fetchall()
            results = _dedupe(rows, limit)
    if not results:
        syn_q = _expand_with_synonyms(query)
        if syn_q:
            try:
                rows = conn.execute(_SQL, (syn_q, path_pattern, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    if not results:
        dot_q = _make_dot_split_query(query)
        if dot_q:
            try:
                dot_sanitized = _sanitize_fts_query(dot_q)
                rows = conn.execute(_SQL, (dot_sanitized, path_pattern, limit * 3)).fetchall()
                results = _dedupe(rows, limit)
            except Exception:
                pass
    return results


def _strip_boilerplate(content: str) -> str:
    """Strip breadcrumb navigation and LG footer from page content.

    Removes numbered breadcrumb lists that appear before the first H1 heading,
    and removes the LG Electronics logo image and all content following it
    (copyright notices, terms links, internal table of contents).
    """
    # Strip breadcrumb navigation (everything before the first H1 heading)
    h1_match = re.search(r'^# .+', content, re.MULTILINE)
    if h1_match and h1_match.start() > 0:
        content = content[h1_match.start():]
    # Strip LG footer (LG logo image and everything after it)
    footer_match = re.search(r'\n!\[LG Electronics Logo\]', content)
    if footer_match:
        content = content[:footer_match.start()]
    return content.strip()


def get_page(path: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT url, path, section, title, content, crawled_at FROM docs WHERE path = ?",
        (path,)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    if result.get("content"):
        result["content"] = _strip_boilerplate(result["content"])
    return result


def get_page_fuzzy(fragment: str) -> dict[str, Any] | None:
    fragment = fragment[:200]  # prevent expensive full-table scan on huge inputs
    # Escape LIKE special characters so literal % and _ in paths are matched correctly
    escaped = fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = get_conn()
    row = conn.execute(
        "SELECT url, path, section, title, content, crawled_at FROM docs WHERE path LIKE ? ESCAPE '\\' LIMIT 1",
        (f"%{escaped}%",)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    if result.get("content"):
        result["content"] = _strip_boilerplate(result["content"])
    return result


_PATH_STOPWORDS = frozenset({
    "the", "this", "that", "and", "for", "not", "does", "with",
    "page", "from", "your", "exist", "using", "into", "have",
})


def get_page_by_path_keywords(fragment: str) -> dict[str, Any] | None:
    """Find a page by extracting keywords from a path fragment and FTS-searching titles.

    Extracts the last path segment, splits on hyphens, filters stopwords,
    applies synonym expansion, and queries docs_fts with a title-column filter.

    Example: '/develop/guides/bluetooth' -> keywords ['bluetooth','ble','gatt']
             -> 'title: bluetooth OR title: ble OR title: gatt'
             -> matches the BLE GATT page.
    """
    fragment = fragment.strip("/")
    parts = [p for p in fragment.split("/") if p]
    if not parts:
        return None
    # Use only the last segment to avoid generic path components ('guides', 'references')
    raw_keywords = parts[-1].split("-")
    if not raw_keywords:
        return None
    all_keywords: list[str] = []
    for kw in raw_keywords:
        kw_lower = kw.lower()
        if kw_lower in _PATH_STOPWORDS or len(kw_lower) < 2:
            continue
        syns = _SYNONYMS.get(kw_lower)
        if syns:
            all_keywords.append(kw_lower)
            all_keywords.extend(syns)
        else:
            all_keywords.append(kw_lower)
    if not all_keywords:
        return None
    title_terms = " OR ".join(f"title: {kw}" for kw in all_keywords)
    _SQL = """
        SELECT d.url, d.path, d.section, d.title, d.content, d.crawled_at
        FROM docs_fts
        JOIN docs d ON d.id = docs_fts.rowid
        WHERE docs_fts MATCH ?
        ORDER BY bm25(docs_fts)
        LIMIT 1
    """
    conn = get_conn()
    try:
        row = conn.execute(_SQL, (title_terms,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    result = dict(row)
    if result.get("content"):
        result["content"] = _strip_boilerplate(result["content"])
    return result


def list_sections() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT section, COUNT(*) AS page_count
        FROM docs
        GROUP BY section
        ORDER BY page_count DESC
    """).fetchall()
    return [dict(r) for r in rows if r["section"] in _KNOWN_TOP_SECTIONS]


def get_stats() -> dict[str, Any]:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    sections = conn.execute("""
        SELECT section, COUNT(*) AS page_count
        FROM docs
        GROUP BY section
        ORDER BY page_count DESC
    """).fetchall()
    db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2) if DB_PATH.exists() else 0.0
    last_crawled = get_cache_meta("last_crawled") or "never"
    days_since_crawl: int | None = None
    data_freshness = "unknown"
    if last_crawled != "never":
        try:
            last_dt = datetime.fromisoformat(last_crawled)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days_since_crawl = (datetime.now(timezone.utc) - last_dt).days
            if days_since_crawl <= _FRESH_DAYS:
                data_freshness = "fresh"
            elif days_since_crawl <= _AGING_DAYS:
                data_freshness = "aging"
            elif days_since_crawl <= _STALE_DAYS:
                data_freshness = "stale"
            else:
                data_freshness = "very_stale"
        except ValueError:
            pass
    return {
        "total_pages": total,
        "db_size_mb": db_size_mb,
        "sections": [dict(r) for r in sections if r["section"] in _KNOWN_TOP_SECTIONS],
        "last_crawled": last_crawled,
        "days_since_crawl": days_since_crawl,
        "data_freshness": data_freshness,
    }


def get_cache_meta(key: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_cache_meta(key: str, value: str) -> None:
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
            (key, value)
        )


def migrate_clean_content() -> int:
    """Strip breadcrumbs and LG footer from all stored pages and rebuild the FTS index.

    Updates existing rows in-place so that search snippets also reflect the cleaned content.
    Returns the number of pages updated.
    """
    conn = get_conn()
    rows = conn.execute("SELECT id, content FROM docs").fetchall()
    updated = 0
    for row in rows:
        original = row["content"] or ""
        cleaned = _strip_boilerplate(original)
        if cleaned != original:
            with conn:
                conn.execute("UPDATE docs SET content = ? WHERE id = ?", (cleaned, row["id"]))
            updated += 1
    if updated > 0:
        with conn:
            conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        logger.info("migrate_clean_content: updated %d pages, FTS index rebuilt", updated)
    return updated


def get_all_urls() -> set[str]:
    """Return all URLs currently stored in the DB.

    Used by the resumable crawl feature to build a ResumeFilter that prevents
    crawl4ai from re-fetching pages already successfully indexed.
    """
    conn = get_conn()
    rows = conn.execute("SELECT url FROM docs").fetchall()
    return {row["url"] for row in rows}
