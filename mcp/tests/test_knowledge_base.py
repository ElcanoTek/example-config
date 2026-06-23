"""Tests for the always-on knowledge_base MCP server.

These call the underlying tool functions directly (no live MCP transport), and
assert against the bundled data/handbook.json so the demo stays honest.
"""

import knowledge_base as kb


def test_handbook_loaded():
    """The bundled handbook should load without error and contain articles."""
    assert kb._LOAD_ERROR is None
    assert len(kb._ARTICLES) >= 10
    # Every article carries the fields the tools rely on.
    for article in kb._ARTICLES:
        assert article.get("id")
        assert article.get("title")
        assert article.get("category")
        assert isinstance(article.get("tags"), list)
        assert article.get("body")


def test_list_categories_matches_data():
    """kb_list_categories counts must add up to the number of articles."""
    categories = kb.kb_list_categories()
    assert isinstance(categories, list)
    assert all("error" not in c for c in categories)

    total = sum(c["article_count"] for c in categories)
    assert total == len(kb._ARTICLES)

    # Returned categories are exactly the distinct categories in the data.
    names = {c["category"] for c in categories}
    expected = {a["category"] for a in kb._ARTICLES}
    assert names == expected
    # Sorted by category name.
    assert [c["category"] for c in categories] == sorted(names)


def test_search_finds_known_article():
    """A targeted query should surface the matching article near the top."""
    results = kb.kb_search("time off vacation")
    assert results, "expected at least one match"
    ids = [r["id"] for r in results]
    assert "requesting-time-off" in ids
    # Each result has the documented shape.
    top = results[0]
    assert set(top) == {"id", "title", "category", "snippet", "score"}
    assert top["score"] > 0


def test_search_ranks_by_relevance():
    """A title-matching article should outrank a mere body mention.

    'security' appears in the title/tags of the security articles and only
    incidentally elsewhere, so the top hit should be a Security article.
    """
    results = kb.kb_search("security", limit=10)
    assert results
    # Scores are returned in descending order.
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0]["category"] == "Security"


def test_search_respects_limit():
    """The limit argument caps the number of results returned."""
    results = kb.kb_search("the", limit=3)
    assert len(results) <= 3


def test_search_empty_query():
    """An empty query returns no results rather than everything."""
    assert kb.kb_search("   ") == []


def test_get_article_returns_full_record():
    """kb_get_article returns the complete article for a valid id."""
    article = kb.kb_get_article("incident-response")
    assert "error" not in article
    assert article["id"] == "incident-response"
    assert article["category"] == "Engineering"
    assert isinstance(article["tags"], list)
    assert "incident" in article["body"].lower()


def test_get_article_not_found():
    """An unknown id yields a clear, structured not-found error."""
    article = kb.kb_get_article("does-not-exist")
    assert "error" in article
    assert "does-not-exist" in article["error"]
