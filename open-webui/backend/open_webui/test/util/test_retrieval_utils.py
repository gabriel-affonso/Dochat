import asyncio

from open_webui.retrieval import utils as retrieval_utils


def test_query_collection_with_fallback_uses_non_hybrid_when_hybrid_raises(
    monkeypatch,
):
    async def fake_hybrid(**kwargs):
        raise RuntimeError("boom")

    async def fake_non_hybrid(**kwargs):
        return {
            "documents": [["fallback-doc"]],
            "metadatas": [[{"source": "fallback"}]],
            "distances": [[0.91]],
        }

    monkeypatch.setattr(
        retrieval_utils, "query_collection_with_hybrid_search", fake_hybrid
    )
    monkeypatch.setattr(retrieval_utils, "query_collection", fake_non_hybrid)

    result = asyncio.run(
        retrieval_utils.query_collection_with_fallback(
            collection_names=["collection-a"],
            queries=["query"],
            embedding_function=None,
            k=5,
            hybrid_search=True,
            reranking_function=None,
            k_reranker=4,
            r=0.0,
            hybrid_bm25_weight=0.5,
            enable_enriched_texts=False,
        )
    )

    assert result["documents"][0] == ["fallback-doc"]


def test_query_collection_with_fallback_uses_non_hybrid_when_hybrid_is_empty(
    monkeypatch,
):
    async def fake_hybrid(**kwargs):
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    async def fake_non_hybrid(**kwargs):
        return {
            "documents": [["fallback-doc"]],
            "metadatas": [[{"source": "fallback"}]],
            "distances": [[0.73]],
        }

    monkeypatch.setattr(
        retrieval_utils, "query_collection_with_hybrid_search", fake_hybrid
    )
    monkeypatch.setattr(retrieval_utils, "query_collection", fake_non_hybrid)

    result = asyncio.run(
        retrieval_utils.query_collection_with_fallback(
            collection_names=["collection-a"],
            queries=["query"],
            embedding_function=None,
            k=5,
            hybrid_search=True,
            reranking_function=None,
            k_reranker=4,
            r=0.0,
            hybrid_bm25_weight=0.5,
            enable_enriched_texts=False,
        )
    )

    assert result["documents"][0] == ["fallback-doc"]


def test_query_collection_with_fallback_keeps_hybrid_result_when_it_has_docs(
    monkeypatch,
):
    async def fake_hybrid(**kwargs):
        return {
            "documents": [["hybrid-doc"]],
            "metadatas": [[{"source": "hybrid"}]],
            "distances": [[0.88]],
        }

    async def fake_non_hybrid(**kwargs):
        raise AssertionError("non-hybrid fallback should not run")

    monkeypatch.setattr(
        retrieval_utils, "query_collection_with_hybrid_search", fake_hybrid
    )
    monkeypatch.setattr(retrieval_utils, "query_collection", fake_non_hybrid)

    result = asyncio.run(
        retrieval_utils.query_collection_with_fallback(
            collection_names=["collection-a"],
            queries=["query"],
            embedding_function=None,
            k=5,
            hybrid_search=True,
            reranking_function=None,
            k_reranker=4,
            r=0.0,
            hybrid_bm25_weight=0.5,
            enable_enriched_texts=False,
        )
    )

    assert result["documents"][0] == ["hybrid-doc"]
