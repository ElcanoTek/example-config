#!/usr/bin/env python3
"""
Team Knowledge Base MCP Server

An ALWAYS-ON Model Context Protocol (MCP) server with no credentials. It loads a
bundled company handbook (data/handbook.json) at startup and exposes simple
search/read tools over it. This is the canonical "point an agent at your own
docs" pattern: swap data/handbook.json for your content (or rewrite the loader
to read your wiki / a vector store) and the tools below work unchanged.

The server runs the moment fleet boots because it needs no secrets. It is
dependency-free apart from the MCP runtime - the search scorer is a plain
keyword/substring ranker so the example stays portable.

Runs over stdio inside the fleet sandbox.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Log to stderr - stdout is reserved for the STDIO transport.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("knowledge_base")

# Path to the bundled handbook. Override with HANDBOOK_PATH to point at your own
# JSON file without editing this server.
DEFAULT_HANDBOOK_PATH = Path(__file__).resolve().parent / "data" / "handbook.json"
HANDBOOK_PATH = Path(os.environ.get("HANDBOOK_PATH", DEFAULT_HANDBOOK_PATH))


def _load_articles(path: Path) -> list[dict[str, Any]]:
    """Read and validate the handbook JSON. Returns a list of article dicts.

    Raises FileNotFoundError if the file is missing and ValueError if it cannot
    be parsed or is not the expected shape. Callers surface these as clear,
    model-readable error messages rather than crashing the server.
    """
    if not path.exists():
        raise FileNotFoundError(f"Handbook file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Handbook file is not valid JSON: {exc}") from exc

    # Accept either a bare list of articles or {"articles": [...]}.
    if isinstance(raw, dict):
        articles = raw.get("articles", [])
    elif isinstance(raw, list):
        articles = raw
    else:
        raise ValueError("Handbook JSON must be a list or an object with 'articles'.")

    if not isinstance(articles, list):
        raise ValueError("Handbook 'articles' must be a list.")
    return articles


# Load once at startup. If it fails we keep an empty corpus and remember the
# error so every tool can report it clearly instead of failing opaquely.
_ARTICLES: list[dict[str, Any]] = []
_LOAD_ERROR: str | None = None
try:
    _ARTICLES = _load_articles(HANDBOOK_PATH)
    logger.info("Loaded %d handbook articles from %s", len(_ARTICLES), HANDBOOK_PATH)
except (FileNotFoundError, ValueError) as exc:
    _LOAD_ERROR = str(exc)
    logger.error("Failed to load handbook: %s", _LOAD_ERROR)


def _article_text(article: dict[str, Any]) -> str:
    """Concatenate the searchable fields of an article into one lowercase blob."""
    tags = article.get("tags", [])
    tag_text = " ".join(t for t in tags if isinstance(t, str))
    parts = [
        str(article.get("title", "")),
        str(article.get("body", "")),
        tag_text,
        str(article.get("category", "")),
    ]
    return " ".join(parts).lower()


def _snippet(body: str, terms: list[str], width: int = 180) -> str:
    """Return a short snippet of body centered on the first matching term."""
    body = " ".join(body.split())  # collapse whitespace
    low = body.lower()
    pos = -1
    for term in terms:
        idx = low.find(term)
        if idx != -1 and (pos == -1 or idx < pos):
            pos = idx
    if pos == -1:
        return body[:width] + ("..." if len(body) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(body), start + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


@mcp.tool()
def kb_list_categories() -> list[dict[str, Any]]:
    """List every category in the knowledge base with how many articles it holds.

    Use this first to discover what topics the handbook covers (for example
    Engineering, Security, People, Support, Data). Returns a list of
    {category, article_count} objects sorted by category name. If the handbook
    could not be loaded, returns a single object with an "error" field.
    """
    if _LOAD_ERROR is not None:
        return [{"error": _LOAD_ERROR}]

    counts: dict[str, int] = {}
    for article in _ARTICLES:
        category = str(article.get("category", "Uncategorized"))
        counts[category] = counts.get(category, 0) + 1
    return [
        {"category": category, "article_count": counts[category]}
        for category in sorted(counts)
    ]


@mcp.tool()
def kb_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search the knowledge base and return the best-matching articles, ranked.

    The query is split into keywords; each article is scored by how many query
    terms occur in its title, body, tags, and category (title and tag hits are
    weighted more heavily). This is a simple dependency-free substring ranker,
    not semantic search - good enough to demo "ask the handbook" and easy to
    replace with your own retriever.

    Args:
        query: Free-text search terms, e.g. "how do I request time off".
        limit: Maximum number of results to return (default 5).

    Returns a ranked list of {id, title, category, snippet, score}. An empty
    list means no article matched. If the handbook could not be loaded, returns
    a single object with an "error" field.
    """
    if _LOAD_ERROR is not None:
        return [{"error": _LOAD_ERROR}]

    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []

    results: list[dict[str, Any]] = []
    for article in _ARTICLES:
        blob = _article_text(article)
        title = str(article.get("title", "")).lower()
        tag_text = " ".join(
            t for t in article.get("tags", []) if isinstance(t, str)
        ).lower()

        score = 0
        for term in terms:
            if term in blob:
                score += 1
            if term in title:
                score += 2  # title hits matter more
            if term in tag_text:
                score += 1  # tag hits are a strong signal

        if score > 0:
            results.append(
                {
                    "id": article.get("id"),
                    "title": article.get("title"),
                    "category": article.get("category"),
                    "snippet": _snippet(str(article.get("body", "")), terms),
                    "score": score,
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[: max(0, limit)]


@mcp.tool()
def kb_get_article(article_id: str) -> dict[str, Any]:
    """Fetch the full text of one article by its id.

    Use the id returned by kb_search or kb_list_categories. Returns the complete
    article as {id, title, category, tags, body}. If no article has that id (or
    the handbook could not be loaded), returns an object with an "error" field
    so the model can recover gracefully.

    Args:
        article_id: The kebab-case id of the article, e.g. "requesting-time-off".
    """
    if _LOAD_ERROR is not None:
        return {"error": _LOAD_ERROR}

    for article in _ARTICLES:
        if article.get("id") == article_id:
            return {
                "id": article.get("id"),
                "title": article.get("title"),
                "category": article.get("category"),
                "tags": article.get("tags", []),
                "body": article.get("body"),
            }
    return {"error": f"No article found with id '{article_id}'."}


if __name__ == "__main__":
    logger.info("Starting Team Knowledge Base MCP Server")
    if _LOAD_ERROR is not None:
        logger.warning(
            "Handbook did not load (%s). Tools will return an error until the "
            "file is present at %s.",
            _LOAD_ERROR,
            HANDBOOK_PATH,
        )
    try:
        mcp.run(transport="stdio")
    except Exception as exc:  # noqa: BLE001 - top-level guard for clean exit
        logger.error("Failed to start server: %s", exc)
        sys.exit(1)
