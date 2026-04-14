import re
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from pypdf import PdfReader
import docx2txt

from open_webui.internal.db import get_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.notes import Notes
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user
from open_webui.utils.dochat_documents import (
    build_document_payload,
    collect_accessible_documents,
    document_matches_filters,
    ensure_list,
    normalize_collection_payload,
    normalize_timestamp,
)

router = APIRouter()
MAX_OCCURRENCES_PER_RESULT = 120


class SearchOccurrenceResponse(BaseModel):
    id: str
    snippet: str
    matched_text: str
    location: Optional[str] = None
    page: Optional[int] = None
    message_id: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    updated_at: Optional[int] = None
    is_archived: Optional[bool] = None


class SearchResultResponse(BaseModel):
    id: str
    type: str
    title: str
    snippet: Optional[str] = None
    score: float = 0.0
    updated_at: Optional[int] = None
    collection: Optional[dict] = None
    tags: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    detail: dict = Field(default_factory=dict)
    occurrence_count: int = 0
    occurrences: list[SearchOccurrenceResponse] = Field(default_factory=list)


class SearchListResponse(BaseModel):
    items: list[SearchResultResponse]
    total: int


def _safe_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            chunks.append(_content_to_text(item))
        return " ".join(chunk for chunk in chunks if chunk)
    if isinstance(content, dict):
        preferred_keys = (
            "text",
            "content",
            "md",
            "markdown",
            "markdown_content",
            "content_md",
            "body",
            "value",
            "full_text",
            "extracted_text",
            "transcript",
        )
        chunks = []
        for key in preferred_keys:
            if key in content:
                chunk = _content_to_text(content.get(key))
                if chunk:
                    chunks.append(chunk)

        if chunks:
            return " ".join(chunk for chunk in chunks if chunk)

        flattened_values = []
        for value in content.values():
            chunk = _content_to_text(value)
            if chunk:
                flattened_values.append(chunk)
        return " ".join(flattened_values)
    return str(content)


def _match_score(query: Optional[str], *values: str) -> float:
    if not query:
        return 0.0

    query_key = _normalize_search_text(query)[0].strip()
    if not query_key:
        return 0.0

    best = 0.0
    for idx, value in enumerate(values):
        haystack = _normalize_search_text(value)[0]
        if not haystack:
            continue
        if haystack == query_key:
            best = max(best, 1.0 - idx * 0.05)
        elif query_key in haystack:
            best = max(best, 0.88 - idx * 0.05)
    return max(best, 0.0)


def _matches_date(
    updated_at: Optional[int], date_from: Optional[int], date_to: Optional[int]
) -> bool:
    if updated_at is None:
        return False
    if date_from is not None and updated_at < date_from:
        return False
    if date_to is not None and updated_at > date_to:
        return False
    return True


def _compact_text(value: Optional[str]) -> str:
    return _content_to_text(value).replace("\n", " ").replace("\r", " ").strip()


def _snippet_from_text(text: str, start: int, end: int, context: int = 96) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    snippet = _compact_text(text[left:right])
    if left > 0:
        snippet = f"...{snippet}"
    if right < len(text):
        snippet = f"{snippet}..."
    return snippet


def _build_occurrence_id(prefix: str, *parts) -> str:
    values = [prefix, *[str(part) for part in parts if part is not None]]
    return ":".join(values)


def _normalize_search_text(value: Optional[str]) -> tuple[str, list[int]]:
    text = value or ""
    normalized_chars: list[str] = []
    index_map: list[int] = []

    for index, character in enumerate(text):
        decomposed = unicodedata.normalize("NFKD", character)
        for normalized_character in decomposed:
            if unicodedata.combining(normalized_character):
                continue
            normalized_chars.append(normalized_character.lower())
            index_map.append(index)

    return "".join(normalized_chars), index_map


def _find_text_occurrences(
    text: str,
    query: Optional[str],
    *,
    occurrence_prefix: str,
    location: Optional[str] = None,
    page: Optional[int] = None,
    message_id: Optional[str] = None,
    updated_at: Optional[int] = None,
    is_archived: Optional[bool] = None,
) -> list[SearchOccurrenceResponse]:
    original_text = text or ""
    if not query or not original_text:
        return []

    normalized_text, index_map = _normalize_search_text(original_text)
    normalized_query, _ = _normalize_search_text(query)
    if not normalized_text or not normalized_query:
        return []

    pattern = re.compile(re.escape(normalized_query), re.IGNORECASE)
    occurrences = []
    for index, match in enumerate(pattern.finditer(normalized_text)):
        start, end = match.span()
        original_start = index_map[start]
        original_end = index_map[end - 1] + 1
        occurrences.append(
            SearchOccurrenceResponse(
                id=_build_occurrence_id(occurrence_prefix, index, start, end),
                snippet=_snippet_from_text(original_text, original_start, original_end),
                matched_text=original_text[original_start:original_end],
                location=location,
                page=page,
                message_id=message_id,
                start=original_start,
                end=original_end,
                updated_at=updated_at,
                is_archived=is_archived,
            )
        )
    return occurrences


def _build_title_occurrence(
    query: Optional[str],
    title: str,
    *,
    prefix: str,
    updated_at: Optional[int] = None,
    is_archived: Optional[bool] = None,
) -> list[SearchOccurrenceResponse]:
    normalized_title, index_map = _normalize_search_text(title or "")
    normalized_query, _ = _normalize_search_text(query)
    if not normalized_query or normalized_query not in normalized_title:
        return []
    start = normalized_title.find(normalized_query)
    end = start + len(normalized_query)
    original_start = index_map[start]
    original_end = index_map[end - 1] + 1
    return [
        SearchOccurrenceResponse(
            id=_build_occurrence_id(prefix, "title", original_start, original_end),
            snippet=title,
            matched_text=title[original_start:original_end],
            location="titulo",
            updated_at=updated_at,
            is_archived=is_archived,
            start=original_start,
            end=original_end,
        )
    ]


def _limit_result_occurrences(
    occurrences: list[SearchOccurrenceResponse],
    *,
    limit: int = MAX_OCCURRENCES_PER_RESULT,
) -> tuple[list[SearchOccurrenceResponse], int]:
    total = len(occurrences)
    if total <= limit:
        return occurrences, total
    return occurrences[:limit], total


def _flatten_searchable_metadata(
    value,
    *,
    prefix: str = "",
    exclude_keys: Optional[set[str]] = None,
) -> list[tuple[str, str]]:
    exclude_keys = exclude_keys or set()
    entries: list[tuple[str, str]] = []

    if value is None or isinstance(value, bool):
        return entries

    if isinstance(value, dict):
        for key, item in value.items():
            if key in exclude_keys:
                continue

            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            entries.extend(
                _flatten_searchable_metadata(
                    item,
                    prefix=nested_prefix,
                    exclude_keys=exclude_keys,
                )
            )
        return entries

    if isinstance(value, list):
        for item in value:
            entries.extend(
                _flatten_searchable_metadata(
                    item,
                    prefix=prefix,
                    exclude_keys=exclude_keys,
                )
            )
        return entries

    if isinstance(value, (str, int, float)):
        normalized_text = _compact_text(str(value))
        if normalized_text:
            entries.append((prefix, normalized_text))

    return entries


def _metadata_location(path: str) -> str:
    if not path:
        return "metadados"

    pretty_path = " › ".join(
        segment.replace("_", " ").strip()
        for segment in path.split(".")
        if segment.strip()
    )
    return f"metadados • {pretty_path}" if pretty_path else "metadados"


def _search_metadata(
    value,
    query: Optional[str],
    *,
    occurrence_prefix: str,
    updated_at: Optional[int] = None,
    is_archived: Optional[bool] = None,
    exclude_keys: Optional[set[str]] = None,
) -> tuple[list[SearchOccurrenceResponse], list[str]]:
    entries = _flatten_searchable_metadata(value, exclude_keys=exclude_keys)
    occurrences: list[SearchOccurrenceResponse] = []
    texts: list[str] = []

    for index, (path, text) in enumerate(entries):
        texts.append(text)
        occurrences.extend(
            _find_text_occurrences(
                text,
                query,
                occurrence_prefix=f"{occurrence_prefix}:meta:{index}",
                location=_metadata_location(path),
                updated_at=updated_at,
                is_archived=is_archived,
            )
        )

    return occurrences, texts


def _get_document_chunks(file_id: str) -> list[tuple[str, dict]]:
    try:
        result = VECTOR_DB_CLIENT.get(collection_name=f"file-{file_id}")
    except Exception:
        return []

    if not result or not result.documents or not result.metadatas:
        return []

    documents = result.documents[0] or []
    metadatas = result.metadatas[0] or []

    chunks = []
    for index, text in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        chunks.append((text or "", metadata or {}))
    return chunks


def _result_to_chunks(result) -> list[tuple[str, dict]]:
    if not result or not result.documents:
        return []

    documents = result.documents[0] or []
    metadatas = (result.metadatas[0] or []) if getattr(result, "metadatas", None) else []

    chunks = []
    for index, text in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        chunks.append((text or "", metadata or {}))
    return chunks


def _get_collection_document_chunks(collection_id: str, file_id: str) -> list[tuple[str, dict]]:
    try:
        result = VECTOR_DB_CLIENT.query(
            collection_name=collection_id,
            filter={"file_id": file_id},
            limit=None,
        )
    except Exception:
        return []

    return _result_to_chunks(result)


def _get_document_markdown_text(file) -> str:
    file_data = _safe_dict(file.data)
    content_payload = _safe_dict(file_data.get("content"))
    transcription_payload = _safe_dict(file_data.get("transcription"))

    markdown_candidates = [
        content_payload.get("md"),
        content_payload.get("markdown"),
        content_payload.get("markdown_content"),
        file_data.get("markdown"),
        file_data.get("markdown_content"),
        file_data.get("content_md"),
        transcription_payload.get("markdown"),
        transcription_payload.get("md"),
    ]

    for candidate in markdown_candidates:
        normalized = _compact_text(_content_to_text(candidate))
        if normalized:
            return normalized

    return ""


def _extract_document_text(request: Request, user, file) -> str:
    file_meta = _safe_dict(file.meta)
    file_path = getattr(file, "path", None)
    if not file_path:
        return ""

    resolved_file_path = None
    try:
        resolved_file_path = Storage.get_file(file_path)
        loader = Loader(
            engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
            user=user,
            DATALAB_MARKER_API_KEY=request.app.state.config.DATALAB_MARKER_API_KEY,
            DATALAB_MARKER_API_BASE_URL=request.app.state.config.DATALAB_MARKER_API_BASE_URL,
            DATALAB_MARKER_ADDITIONAL_CONFIG=request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
            DATALAB_MARKER_SKIP_CACHE=request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
            DATALAB_MARKER_FORCE_OCR=request.app.state.config.DATALAB_MARKER_FORCE_OCR,
            DATALAB_MARKER_PAGINATE=request.app.state.config.DATALAB_MARKER_PAGINATE,
            DATALAB_MARKER_STRIP_EXISTING_OCR=request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
            DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
            DATALAB_MARKER_FORMAT_LINES=request.app.state.config.DATALAB_MARKER_FORMAT_LINES,
            DATALAB_MARKER_USE_LLM=request.app.state.config.DATALAB_MARKER_USE_LLM,
            DATALAB_MARKER_OUTPUT_FORMAT=request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
            EXTERNAL_DOCUMENT_LOADER_URL=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
            EXTERNAL_DOCUMENT_LOADER_API_KEY=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
            TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
            DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
            DOCLING_API_KEY=request.app.state.config.DOCLING_API_KEY,
            DOCLING_PARAMS=request.app.state.config.DOCLING_PARAMS,
            PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
            PDF_LOADER_MODE=request.app.state.config.PDF_LOADER_MODE,
            DOCUMENT_INTELLIGENCE_ENDPOINT=request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
            DOCUMENT_INTELLIGENCE_KEY=request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
            DOCUMENT_INTELLIGENCE_MODEL=request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
            MISTRAL_OCR_API_BASE_URL=request.app.state.config.MISTRAL_OCR_API_BASE_URL,
            MISTRAL_OCR_API_KEY=request.app.state.config.MISTRAL_OCR_API_KEY,
            MINERU_API_MODE=request.app.state.config.MINERU_API_MODE,
            MINERU_API_URL=request.app.state.config.MINERU_API_URL,
            MINERU_API_KEY=request.app.state.config.MINERU_API_KEY,
            MINERU_API_TIMEOUT=request.app.state.config.MINERU_API_TIMEOUT,
            MINERU_PARAMS=request.app.state.config.MINERU_PARAMS,
        )
        docs = loader.load(
            file.filename,
            file_meta.get("content_type"),
            resolved_file_path,
        )
        extracted = " ".join(
            doc.page_content for doc in docs if getattr(doc, "page_content", "")
        )
        if extracted.strip():
            return extracted
    except Exception:
        pass

    if not resolved_file_path:
        return ""

    suffix = Path(resolved_file_path).suffix.lower()

    try:
        if suffix in {".md", ".txt", ".rst", ".csv"}:
            return Path(resolved_file_path).read_text(encoding="utf-8", errors="ignore")
        if suffix == ".docx":
            return docx2txt.process(resolved_file_path) or ""
        if suffix == ".pdf":
            reader = PdfReader(resolved_file_path)
            return " ".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""

    return ""


def _ensure_document_content(
    request: Request,
    user,
    file,
    db: Session,
    *,
    hydrate_missing_text: bool = False,
) -> str:
    file_data = _safe_dict(file.data)
    content = _compact_text(file_data.get("content"))
    if content or not hydrate_missing_text:
        return content

    extracted_content = _extract_document_text(request, user, file)
    if not extracted_content:
        return ""

    Files.update_file_data_by_id(file.id, {"content": extracted_content}, db=db)
    file.data = {**file_data, "content": extracted_content}
    return extracted_content


def _get_document_search_sources(
    request: Request,
    user,
    db: Session,
    file,
    collections: list | None = None,
    *,
    hydrate_missing_text: bool = False,
) -> list[dict]:
    sources: list[dict] = []
    seen = set()

    def append_source(text: str, metadata: dict | None = None, origin: str = "content"):
        normalized_text = _compact_text(text)
        if not normalized_text:
            return

        metadata = _safe_dict(metadata)
        page = metadata.get("page")
        chunk_index = metadata.get("chunk_index")
        signature = (
            normalized_text[:400],
            page if page is None else str(page),
            chunk_index if chunk_index is None else str(chunk_index),
            origin,
        )
        if signature in seen:
            return
        seen.add(signature)

        sources.append(
            {
                "text": normalized_text,
                "metadata": metadata,
                "origin": origin,
            }
        )

    content_text = _ensure_document_content(
        request,
        user,
        file,
        db,
        hydrate_missing_text=hydrate_missing_text,
    )
    append_source(content_text or "", {"source_type": "file_data"}, "content")
    append_source(
        _get_document_markdown_text(file),
        {"source_type": "markdown"},
        "markdown",
    )

    for text, metadata in _get_document_chunks(file.id):
        append_source(text, metadata, "standalone")

    for collection in collections or []:
        collection_id = getattr(collection, "id", None) or _safe_dict(collection).get("id")
        if not collection_id:
            continue
        for text, metadata in _get_collection_document_chunks(collection_id, file.id):
            append_source(text, metadata, f"collection:{collection_id}")

    return sources


def _build_note_collections_map(user, db: Session) -> dict[str, list[dict]]:
    note_collections: dict[str, list[dict]] = {}
    for knowledge in Knowledges.get_knowledge_bases(db=db):
        if not (
            user.role == "admin"
            or knowledge.user_id == user.id
            or AccessGrants.has_access(
                user_id=user.id,
                resource_type="knowledge",
                resource_id=knowledge.id,
                permission="read",
                db=db,
            )
        ):
            continue

        collection_payload = normalize_collection_payload(knowledge)
        linked_note_ids = ensure_list((knowledge.meta or {}).get("linked_note_ids"))
        for note_id in linked_note_ids:
            note_collections.setdefault(note_id, []).append(collection_payload)
    return note_collections


@router.get("", response_model=SearchListResponse, include_in_schema=False)
@router.get("/", response_model=SearchListResponse)
async def unified_search(
    request: Request,
    query: Optional[str] = None,
    type: Optional[str] = Query(None, pattern="^(chat|note|document)$"),
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    collection: Optional[str] = None,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    archived: Optional[bool] = None,
    pinned: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(60, ge=1, le=200),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    results: list[SearchResultResponse] = []
    query = (query or "").strip() or None
    requested_tags = ensure_list(tags)
    normalized_collection_filter = (collection or "").lower().strip()
    note_collections_map = _build_note_collections_map(user, db)

    if type in (None, "chat"):
        chats = Chats.get_chats_by_user_id(user.id, db=db).items
        for chat in chats:
            try:
                chat_meta = _safe_dict(chat.meta)
                if archived is not None and bool(chat.archived) != archived:
                    continue
                if pinned is not None and bool(chat.pinned) != pinned:
                    continue

                updated_at = normalize_timestamp(chat.updated_at)
                if not _matches_date(updated_at, date_from, date_to):
                    continue

                chat_tags = ensure_list(chat_meta.get("tags"))
                if requested_tags and not set(tag.lower() for tag in requested_tags).issubset(
                    {tag.lower() for tag in chat_tags}
                ):
                    continue

                document_context = _safe_dict(chat_meta.get("document_context"))
                context_collection_ids = {
                    str(value).lower()
                    for value in ensure_list(document_context.get("collection_ids"))
                }
                if (
                    normalized_collection_filter
                    and normalized_collection_filter not in context_collection_ids
                ):
                    continue

                messages = ChatMessages.get_messages_by_chat_id(chat.id, db=db)
                message_occurrences: list[SearchOccurrenceResponse] = []
                message_details = []
                for index, message in enumerate(messages):
                    message_text = _content_to_text(message.content)
                    if message_text:
                        message_details.append(
                            {
                                "id": message.id.split(f"{chat.id}-", 1)[-1],
                                "role": message.role,
                                "content": _compact_text(message_text),
                                "created_at": normalize_timestamp(message.created_at),
                            }
                        )

                    message_occurrences.extend(
                        _find_text_occurrences(
                            message_text,
                            query,
                            occurrence_prefix=f"chat:{chat.id}:{message.id}",
                            location=f"mensagem {index + 1}",
                            message_id=message.id.split(f"{chat.id}-", 1)[-1],
                            updated_at=normalize_timestamp(message.created_at),
                            is_archived=chat.archived,
                        )
                    )

                title_occurrences = _build_title_occurrence(
                    query,
                    chat.title or "",
                    prefix=f"chat:{chat.id}",
                    updated_at=updated_at,
                    is_archived=chat.archived,
                )
                metadata_occurrences, chat_metadata_texts = _search_metadata(
                    chat_meta,
                    query,
                    occurrence_prefix=f"chat:{chat.id}",
                    updated_at=updated_at,
                    is_archived=chat.archived,
                )
                occurrences = title_occurrences + message_occurrences + metadata_occurrences

                score = max(
                    _match_score(
                        query,
                        chat.title,
                        " ".join(chat_tags),
                        " ".join(chat_metadata_texts),
                    ),
                    0.7 if occurrences else 0.0,
                )
                if query and score == 0 and not occurrences:
                    continue

                occurrences, occurrence_count = _limit_result_occurrences(occurrences)

                snippet = (
                    occurrences[0].snippet
                    if occurrences
                    else next(
                        (
                            message["content"][:280]
                            for message in reversed(message_details)
                            if message["content"]
                        ),
                        None,
                    )
                )

                results.append(
                    SearchResultResponse(
                        id=chat.id,
                        type="chat",
                        title=chat.title,
                        snippet=snippet,
                        score=score,
                        updated_at=updated_at,
                        collection=None,
                        tags=chat_tags,
                        metadata={
                            "archived": chat.archived,
                            "pinned": bool(chat.pinned),
                            "share_id": chat.share_id,
                        },
                        detail={
                            "message_count": len(messages),
                            "messages": message_details[-12:],
                            "last_sources": ChatMessages.get_latest_sources_by_chat_id(
                                chat.id, db=db
                            ),
                            "document_context": document_context,
                            "meta": chat_meta,
                        },
                        occurrence_count=occurrence_count,
                        occurrences=occurrences,
                    )
                )
            except Exception:
                continue

    if type in (None, "note"):
        notes = Notes.get_notes_by_user_id(
            user.id,
            "read",
            filter={
                "archived": archived,
                "pinned": pinned,
                **({"date_from": date_from} if date_from is not None else {}),
                **({"date_to": date_to} if date_to is not None else {}),
            },
            skip=0,
            limit=1000,
            db=db,
        )
        for note in notes:
            try:
                note_meta = _safe_dict(note.meta)
                note_data = _safe_dict(note.data)
                linked_collections = note_collections_map.get(note.id, [])
                if normalized_collection_filter:
                    matches_collection = any(
                        normalized_collection_filter
                        in {
                            (item.get("id") or "").lower(),
                            (item.get("name") or "").lower(),
                        }
                        for item in linked_collections
                    )
                    if not matches_collection:
                        continue

                note_tags = ensure_list(note_meta.get("tags"))
                if requested_tags and not set(tag.lower() for tag in requested_tags).issubset(
                    {tag.lower() for tag in note_tags}
                ):
                    continue

                updated_at = normalize_timestamp(note.updated_at)
                if not _matches_date(updated_at, date_from, date_to):
                    continue

                content_md = _safe_dict(note_data.get("content")).get("md") or ""
                metadata_occurrences, note_metadata_texts = _search_metadata(
                    note_meta,
                    query,
                    occurrence_prefix=f"note:{note.id}",
                    updated_at=updated_at,
                    is_archived=note.is_archived,
                )
                occurrences = _build_title_occurrence(
                    query,
                    note.title or "",
                    prefix=f"note:{note.id}",
                    updated_at=updated_at,
                    is_archived=note.is_archived,
                )
                occurrences.extend(
                    _find_text_occurrences(
                        content_md,
                        query,
                        occurrence_prefix=f"note:{note.id}:content",
                        location="conteudo",
                        updated_at=updated_at,
                        is_archived=note.is_archived,
                    )
                )
                occurrences.extend(metadata_occurrences)

                score = max(
                    _match_score(
                        query,
                        note.title,
                        content_md,
                        " ".join(note_tags),
                        " ".join(note_metadata_texts),
                    ),
                    0.72 if occurrences else 0.0,
                )
                if query and score == 0 and not occurrences:
                    continue

                occurrences, occurrence_count = _limit_result_occurrences(occurrences)

                results.append(
                    SearchResultResponse(
                        id=note.id,
                        type="note",
                        title=note.title,
                        snippet=occurrences[0].snippet
                        if occurrences
                        else (content_md or "")[:280] or None,
                        score=score,
                        updated_at=updated_at,
                        collection=linked_collections[0] if linked_collections else None,
                        tags=note_tags,
                        metadata={
                            "archived": note.is_archived,
                            "pinned": note.is_pinned,
                            "last_opened_at": normalize_timestamp(note.last_opened_at),
                        },
                        detail={
                            "content_md": content_md,
                            "linked_collections": linked_collections,
                            "meta": note_meta,
                        },
                        occurrence_count=occurrence_count,
                        occurrences=occurrences,
                    )
                )
            except Exception:
                continue

    if type in (None, "document"):
        for entry in collect_accessible_documents(user, db=db):
            try:
                document = build_document_payload(entry["file"], entry.get("collections"))
                if not document_matches_filters(
                    document,
                    query=None,
                    collection=collection,
                    tags=requested_tags,
                    source=source,
                    archived=archived,
                ):
                    continue

                if not _matches_date(document.get("updated_at"), date_from, date_to):
                    continue

                occurrences = _build_title_occurrence(
                    query,
                    document.get("title") or "",
                    prefix=f"document:{document['id']}",
                    updated_at=document.get("updated_at"),
                    is_archived=document.get("is_archived"),
                )
                document_metadata_payload = {
                    "filename": document.get("filename"),
                    "document_type": document.get("document_type"),
                    "collections": document.get("collections"),
                    "entities": document.get("entities"),
                    "version": document.get("version"),
                    "metadata": document.get("metadata"),
                    "provenance": document.get("provenance"),
                    "related": document.get("related"),
                    "raw_meta": {
                        key: value
                        for key, value in _safe_dict(entry["file"].meta).items()
                        if key
                        not in {
                            "title",
                            "name",
                            "description",
                            "summary",
                            "source",
                            "url",
                            "author",
                            "language",
                            "tags",
                            "entities",
                            "document_type",
                            "content_type",
                        }
                    },
                    "raw_data": {
                        key: value
                        for key, value in _safe_dict(entry["file"].data).items()
                        if key
                        not in {
                            "content",
                            "markdown",
                            "markdown_content",
                            "content_md",
                        }
                    },
                }
                metadata_occurrences, document_metadata_texts = _search_metadata(
                    document_metadata_payload,
                    query,
                    occurrence_prefix=f"document:{document['id']}",
                    updated_at=document.get("updated_at"),
                    is_archived=document.get("is_archived"),
                )
                occurrences.extend(metadata_occurrences)

                document_sources = _get_document_search_sources(
                    request,
                    user,
                    db,
                    entry["file"],
                    entry.get("collections"),
                    hydrate_missing_text=bool(query),
                )
                for source_index, source_entry in enumerate(document_sources):
                    metadata = _safe_dict(source_entry.get("metadata"))
                    source_page = metadata.get("page")
                    chunk_index = metadata.get("chunk_index", source_index)
                    origin = source_entry.get("origin")
                    location = (
                        "markdown transcrito"
                        if origin == "markdown"
                        else (
                            f"pagina {int(source_page) + 1}"
                            if source_page is not None
                            else f"trecho {int(chunk_index) + 1}"
                        )
                    )
                    occurrences.extend(
                        _find_text_occurrences(
                            source_entry.get("text") or "",
                            query,
                            occurrence_prefix=f"document:{document['id']}:chunk:{source_index}",
                            location=location,
                            page=int(source_page) + 1 if source_page is not None else None,
                            updated_at=document.get("updated_at"),
                            is_archived=document.get("is_archived"),
                        )
                    )

                score = max(
                    _match_score(
                        query,
                        document.get("title"),
                        document.get("description"),
                        document.get("summary"),
                        document.get("source"),
                        document.get("author"),
                        document.get("language"),
                        " ".join(ensure_list(document.get("entities"))),
                        " ".join(document_metadata_texts),
                        " ".join(
                            source_entry.get("text", "")[:200]
                            for source_entry in document_sources[:6]
                        ),
                        " ".join(
                            (item.get("name") or "")
                            for item in document.get("collections") or []
                        ),
                        " ".join(ensure_list(document.get("tags"))),
                    ),
                    0.74 if occurrences else 0.0,
                )
                if query and score == 0 and not occurrences:
                    continue

                occurrences, occurrence_count = _limit_result_occurrences(occurrences)

                results.append(
                    SearchResultResponse(
                        id=document["id"],
                        type="document",
                        title=document["title"],
                        snippet=occurrences[0].snippet
                        if occurrences
                        else document.get("description")
                        or next(
                            (
                                source_entry.get("text", "")[:280]
                                for source_entry in document_sources
                                if source_entry.get("text")
                            ),
                            None,
                        )
                        or None,
                        score=score,
                        updated_at=document.get("updated_at"),
                        collection=document.get("collection"),
                        tags=document.get("tags") or [],
                        metadata=document.get("metadata") or {},
                        detail={
                            **document,
                            "description": _safe_dict(entry["file"].meta).get("description")
                            or _safe_dict(entry["file"].data).get("description"),
                            "search_sources": [
                                {
                                    "origin": source_entry.get("origin"),
                                    "page": _safe_dict(source_entry.get("metadata")).get("page"),
                                    "chunk_index": _safe_dict(source_entry.get("metadata")).get(
                                        "chunk_index"
                                    ),
                                }
                                for source_entry in document_sources[:24]
                            ],
                        },
                        occurrence_count=occurrence_count,
                        occurrences=occurrences,
                    )
                )
            except Exception:
                continue

    if query:
        results.sort(
            key=lambda item: (
                item.score,
                item.updated_at or 0,
                item.occurrence_count,
                item.id,
            ),
            reverse=True,
        )
    else:
        results.sort(
            key=lambda item: (item.updated_at or 0, item.occurrence_count, item.id),
            reverse=True,
        )

    total = len(results)
    start = (page - 1) * limit
    end = start + limit
    return SearchListResponse(items=results[start:end], total=total)
