import asyncio
from types import SimpleNamespace

from open_webui.routers import search as search_router


def _make_request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace()))
    )


def test_unified_search_returns_uploaded_files_chats_and_notes(monkeypatch):
    fake_user = SimpleNamespace(id="user-1", role="user")

    fake_chat = SimpleNamespace(
        id="chat-1",
        title="Conversa alpha",
        meta={},
        archived=False,
        pinned=False,
        share_id=None,
        updated_at=1_710_000_000,
    )
    fake_message = SimpleNamespace(
        id="chat-1-msg-1",
        role="user",
        content="O termo zebra apareceu na conversa com anexo.",
        created_at=1_710_000_010,
    )

    fake_note = SimpleNamespace(
        id="note-1",
        title="Nota zebra",
        meta={},
        data={"content": {"md": "Registrar zebra na nota para consulta futura."}},
        is_archived=False,
        is_pinned=False,
        last_opened_at=1_710_000_020,
        updated_at=1_710_000_030,
    )

    fake_file = SimpleNamespace(
        id="file-1",
        user_id="user-1",
        hash="hash-1",
        filename="relatorio.txt",
        path="/tmp/relatorio.txt",
        data={"content": "O upload zebra foi processado corretamente."},
        meta={"name": "Relatorio financeiro", "content_type": "text/plain"},
        is_archived=False,
        archived_at=None,
        created_at=1_710_000_040,
        updated_at=1_710_000_050,
    )

    monkeypatch.setattr(
        search_router.Chats,
        "get_chats_by_user_id",
        lambda *args, **kwargs: SimpleNamespace(items=[fake_chat]),
    )
    monkeypatch.setattr(
        search_router.ChatMessages,
        "get_messages_by_chat_id",
        lambda *args, **kwargs: [fake_message],
    )
    monkeypatch.setattr(
        search_router.ChatMessages,
        "get_latest_sources_by_chat_id",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        search_router.Notes,
        "get_notes_by_user_id",
        lambda *args, **kwargs: [fake_note],
    )
    monkeypatch.setattr(
        search_router,
        "_build_note_collections_map",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        search_router,
        "collect_accessible_documents",
        lambda *args, **kwargs: [{"file": fake_file, "collections": []}],
    )
    monkeypatch.setattr(
        search_router,
        "_get_document_search_sources",
        lambda *args, **kwargs: [
            {
                "text": "O upload zebra foi processado corretamente.",
                "metadata": {"chunk_index": 0},
                "origin": "content",
            }
        ],
    )

    response = asyncio.run(
        search_router.unified_search(
            request=_make_request(),
            query="zebra",
            type=None,
            date_from=None,
            date_to=None,
            collection=None,
            tags=None,
            source=None,
            archived=None,
            pinned=None,
            page=1,
            limit=20,
            user=fake_user,
            db=None,
        )
    )

    assert response.total == 3

    results_by_type = {item.type: item for item in response.items}
    assert set(results_by_type) == {"chat", "note", "document"}

    chat_result = results_by_type["chat"]
    assert chat_result.occurrence_count == 1
    assert chat_result.occurrences[0].matched_text.lower() == "zebra"

    note_result = results_by_type["note"]
    assert note_result.occurrence_count >= 1
    assert any(
        occurrence.matched_text.lower() == "zebra"
        for occurrence in note_result.occurrences
    )

    document_result = results_by_type["document"]
    assert document_result.title == "Relatorio financeiro"
    assert document_result.collection is None
    assert document_result.occurrence_count == 1
    assert document_result.occurrences[0].matched_text.lower() == "zebra"
    assert "upload zebra" in document_result.occurrences[0].snippet.lower()


def test_unified_search_matches_metadata_for_chats_notes_and_documents(monkeypatch):
    fake_user = SimpleNamespace(id="user-1", role="user")

    fake_chat = SimpleNamespace(
        id="chat-1",
        title="Conversa sem termo",
        meta={"topic": "Atlas reservado", "document_context": {"collection_ids": ["acervo"]}},
        archived=False,
        pinned=False,
        share_id=None,
        updated_at=1_710_000_000,
    )
    fake_message = SimpleNamespace(
        id="chat-1-msg-1",
        role="user",
        content="Nenhuma palavra relevante aqui.",
        created_at=1_710_000_010,
    )

    fake_note = SimpleNamespace(
        id="note-1",
        title="Nota sem termo",
        meta={"category": "Atlas institucional"},
        data={"content": {"md": "Texto comum sem correspondencia direta."}},
        is_archived=False,
        is_pinned=False,
        last_opened_at=1_710_000_020,
        updated_at=1_710_000_030,
    )

    fake_file = SimpleNamespace(
        id="file-1",
        user_id="user-1",
        hash="hash-1",
        filename="relatorio.txt",
        path="/tmp/relatorio.txt",
        data={"content": ""},
        meta={
            "name": "Relatorio financeiro",
            "content_type": "text/plain",
            "folder_path": "acervo/atlas",
        },
        is_archived=False,
        archived_at=None,
        created_at=1_710_000_040,
        updated_at=1_710_000_050,
    )

    monkeypatch.setattr(
        search_router.Chats,
        "get_chats_by_user_id",
        lambda *args, **kwargs: SimpleNamespace(items=[fake_chat]),
    )
    monkeypatch.setattr(
        search_router.ChatMessages,
        "get_messages_by_chat_id",
        lambda *args, **kwargs: [fake_message],
    )
    monkeypatch.setattr(
        search_router.ChatMessages,
        "get_latest_sources_by_chat_id",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        search_router.Notes,
        "get_notes_by_user_id",
        lambda *args, **kwargs: [fake_note],
    )
    monkeypatch.setattr(
        search_router,
        "_build_note_collections_map",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        search_router,
        "collect_accessible_documents",
        lambda *args, **kwargs: [{"file": fake_file, "collections": []}],
    )
    monkeypatch.setattr(
        search_router,
        "_get_document_search_sources",
        lambda *args, **kwargs: [],
    )

    response = asyncio.run(
        search_router.unified_search(
            request=_make_request(),
            query="atlas",
            type=None,
            date_from=None,
            date_to=None,
            collection=None,
            tags=None,
            source=None,
            archived=None,
            pinned=None,
            page=1,
            limit=20,
            user=fake_user,
            db=None,
        )
    )

    assert response.total == 3

    results_by_type = {item.type: item for item in response.items}
    assert set(results_by_type) == {"chat", "note", "document"}

    for result in results_by_type.values():
        assert result.occurrence_count >= 1
        assert any(
            (occurrence.location or "").startswith("metadados")
            for occurrence in result.occurrences
        )


def test_get_document_search_sources_includes_markdown_transcriptions(monkeypatch):
    fake_file = SimpleNamespace(
        id="file-1",
        data={"content": "", "markdown": "# Titulo\nOrquidea rara em markdown"},
        meta={"content_type": "text/markdown"},
        path=None,
    )

    monkeypatch.setattr(search_router, "_get_document_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        search_router,
        "_get_collection_document_chunks",
        lambda *args, **kwargs: [],
    )

    sources = search_router._get_document_search_sources(
        _make_request(),
        SimpleNamespace(id="user-1", role="user"),
        None,
        fake_file,
        [],
        hydrate_missing_text=False,
    )

    assert any(
        source["origin"] == "markdown" and "Orquidea rara" in source["text"]
        for source in sources
    )
