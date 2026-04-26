import json
import logging
import re
import time
from collections import Counter, defaultdict
from typing import Optional

import markdown
from fastapi import HTTPException, Request, status

from open_webui.models.collection_reports import CollectionReports
from open_webui.models.files import FileModel
from open_webui.models.knowledge import KnowledgeForm, KnowledgeModel, Knowledges
from open_webui.models.notes import NoteForm, NoteUpdateForm, Notes
from open_webui.models.users import Users
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.dochat_collections import (
    get_linked_note_ids,
    set_linked_note_ids,
    upsert_linked_note_vector,
)
from open_webui.utils.dochat_documents import (
    ensure_list,
    first_clean_text,
    is_likely_garbled_text,
    normalize_text,
    normalize_timestamp,
)
from open_webui.utils.models import check_model_access, get_all_models

log = logging.getLogger(__name__)

SYNTHESIS_IDLE_STATUS = "idle"
SYNTHESIS_QUEUED_STATUS = "queued"
SYNTHESIS_PROCESSING_STATUS = "processing"
SYNTHESIS_COMPLETED_STATUS = "completed"
SYNTHESIS_COMPLETED_WITH_WARNINGS_STATUS = "completed_with_warnings"
SYNTHESIS_FAILED_STATUS = "failed"

DEFAULT_PAGE_CHUNK_SIZE = 5
DEFAULT_CHAR_CHUNK_SIZE = 6000

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{3,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = {
    "para",
    "como",
    "sobre",
    "entre",
    "pelos",
    "pelas",
    "tambem",
    "também",
    "ainda",
    "documento",
    "colecao",
    "coleção",
    "dados",
    "texto",
    "partir",
    "apenas",
    "depois",
    "muito",
    "mais",
    "menos",
    "sobre",
    "com",
    "sem",
    "dos",
    "das",
    "uma",
    "umas",
    "uns",
    "que",
    "isso",
    "isto",
    "esse",
    "essa",
    "este",
    "esta",
    "ser",
    "sao",
    "são",
    "foi",
    "foram",
    "tem",
    "têm",
    "their",
    "there",
    "which",
    "with",
    "from",
    "this",
    "that",
    "have",
    "were",
    "into",
    "about",
    "your",
}


def get_default_chunk_config() -> dict:
    return {
        "mode": "pages",
        "size": DEFAULT_PAGE_CHUNK_SIZE,
        "fallback_mode": "chars",
        "fallback_size": DEFAULT_CHAR_CHUNK_SIZE,
    }


def build_idle_synthesis_payload(collection_id: Optional[str] = None) -> dict:
    return {
        "jobId": None,
        "collectionId": collection_id,
        "status": SYNTHESIS_IDLE_STATUS,
        "documentsTotal": 0,
        "documentsCompleted": 0,
        "documentsFailed": 0,
        "currentStep": None,
        "currentDocumentId": None,
        "currentDocumentTitle": None,
        "warnings": [],
        "error": None,
        "noteId": None,
        "noteTitle": None,
        "documentStatuses": [],
        "report": None,
        "metadata": {
            "model": None,
            "generatedAt": None,
            "chunkConfig": get_default_chunk_config(),
        },
    }


def get_report_payload(collection_id: str, db=None) -> dict:
    report = CollectionReports.get_report_by_collection_id(collection_id, db=db)
    if report is None:
        return build_idle_synthesis_payload(collection_id)

    document_statuses = CollectionReports.list_document_summaries(report.id, db=db)
    note_title = None
    if report.note_id:
        note = Notes.get_note_by_id(report.note_id, db=db)
        note_title = note.title if note else None

    return {
        "jobId": report.id,
        "collectionId": report.collection_id,
        "status": report.status,
        "documentsTotal": report.documents_total,
        "documentsCompleted": report.documents_processed,
        "documentsFailed": report.documents_failed,
        "currentStep": report.current_step,
        "currentDocumentId": report.current_document_id,
        "currentDocumentTitle": report.current_document_title,
        "warnings": report.warnings or [],
        "error": report.error,
        "noteId": report.note_id,
        "noteTitle": note_title,
        "documentStatuses": [
            {
                "documentId": item.document_id,
                "title": item.title,
                "status": item.status,
                "pageCount": item.page_count,
                "updatedAt": item.document_updated_at,
                "sourceType": item.source_type,
                "warnings": item.warnings or [],
                "error": item.error,
            }
            for item in document_statuses
        ],
        "report": report.final_report,
        "metadata": {
            "model": report.model_name,
            "generatedAt": report.completed_at,
            "chunkConfig": report.chunk_config or get_default_chunk_config(),
            "documentsProcessed": report.documents_processed,
            "documentsFailed": report.documents_failed,
            "includedDocumentIds": report.included_document_ids or [],
            "failedDocuments": report.failed_documents or [],
        },
    }


def get_synthesis_report_or_404(collection_id: str, db=None) -> dict:
    payload = get_report_payload(collection_id, db=db)
    if payload["status"] == SYNTHESIS_IDLE_STATUS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Synthesis not found",
        )
    return payload


def _extract_json_object(content: str) -> dict:
    if not content:
        raise ValueError("Empty model response")

    content = content.strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return json.loads(content[start : end + 1])


def _extract_completion_content(response) -> str:
    payload = response
    if hasattr(response, "body"):
        payload = json.loads(response.body.decode("utf-8"))

    if isinstance(payload, dict):
        return payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return ""


def _model_priority(model_id: str, model: dict) -> tuple[int, int, str]:
    haystack = f"{model_id} {model.get('name', '')}".lower()
    score = 0

    if any(
        token in haystack
        for token in ("embed", "embedding", "rerank", "whisper", "tts", "stt")
    ):
        score -= 100

    if any(token in haystack for token in ("vision", "vl", "image", "audio")):
        score -= 25

    if any(token in haystack for token in ("instruct", "chat", "assistant")):
        score += 20

    if any(token in haystack for token in ("gpt", "claude", "qwen", "llama", "mistral", "gemma")):
        score += 12

    owned_by = (model.get("owned_by") or "").lower()
    if owned_by in {"openai", "anthropic"}:
        score += 10
    elif owned_by == "ollama":
        score += 6

    if model.get("pipe"):
        score -= 40

    return (-score, 0 if model.get("id") == model_id else 1, haystack)


def _is_compatible_text_model(model_id: str, model: dict) -> bool:
    haystack = f"{model_id} {model.get('name', '')}".lower()
    if model.get("pipe"):
        return False

    blocked_tokens = (
        "embed",
        "embedding",
        "rerank",
        "whisper",
        "tts",
        "stt",
        "vision",
        "vl",
        "image",
        "audio",
    )
    return not any(token in haystack for token in blocked_tokens)


def resolve_synthesis_model_id(request: Request, user, requested_model_id: Optional[str]) -> Optional[str]:
    models = getattr(request.app.state, "MODELS", {}) or {}
    if not models:
        return None

    if requested_model_id:
        if requested_model_id not in models:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
        candidate_ids = [requested_model_id]
    else:
        default_model_ids = [
            model_id.strip()
            for model_id in ((getattr(request.app.state.config, "DEFAULT_MODELS", None) or "").split(","))
            if model_id and model_id.strip()
        ]

        preferred_ids = []
        seen_pref = set()
        for candidate_id in default_model_ids:
            if candidate_id in models and candidate_id not in seen_pref:
                preferred_ids.append(candidate_id)
                seen_pref.add(candidate_id)

        remaining_ids = [
            model_id
            for model_id in sorted(
                models.keys(),
                key=lambda model_id: _model_priority(model_id, models[model_id]),
            )
            if model_id not in seen_pref
        ]
        candidate_ids = preferred_ids + remaining_ids

    seen = set()
    for candidate_id in candidate_ids:
        if candidate_id in seen or candidate_id not in models:
            continue
        seen.add(candidate_id)

        try:
            if user.role == "user":
                check_model_access(user, models[candidate_id])
            if not requested_model_id and not _is_compatible_text_model(
                candidate_id, models[candidate_id]
            ):
                continue
            return candidate_id
        except Exception:
            continue

    if requested_model_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access prohibited",
        )

    return None


async def _generate_structured_completion(
    request: Request,
    user,
    model_id: Optional[str],
    *,
    schema_name: str,
    schema_properties: dict,
    required_fields: list[str],
    messages: list[dict],
    task: str,
    task_body: Optional[dict] = None,
    max_tokens: int = 900,
) -> Optional[dict]:
    if not model_id:
        return None

    if not getattr(request.app.state, "MODELS", None):
        await get_all_models(request, user=user)

    models = getattr(request.app.state, "MODELS", {}) or {}
    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": schema_properties,
                    "required": required_fields,
                },
            },
        },
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": task,
            "task_body": task_body or {},
        },
    }

    if models.get(model_id, {}).get("owned_by") == "ollama":
        payload["max_tokens"] = max_tokens
        payload["options"] = {"temperature": 0.1}
    else:
        payload["max_completion_tokens"] = max_tokens
        payload["temperature"] = 0.1

    response = await generate_chat_completion(request, form_data=payload, user=user)
    content = _extract_completion_content(response)
    return _extract_json_object(content)


def _get_document_chunks(file_id: str) -> list[tuple[str, dict]]:
    try:
        result = VECTOR_DB_CLIENT.get(collection_name=f"file-{file_id}")
    except Exception:
        return []

    if not result or not result.documents:
        return []

    documents = result.documents[0] or []
    metadatas = (result.metadatas[0] or []) if result.metadatas else []

    chunks = []
    for index, text in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        chunks.append((text or "", metadata or {}))
    return chunks


def _parse_page_index(metadata: dict, fallback_index: int) -> int:
    metadata = metadata or {}
    for key in ("page", "page_index", "chunk_index"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return max(int(str(value).strip()), 0)
        except (TypeError, ValueError):
            continue

    page_label = metadata.get("page_label")
    if page_label is not None:
        try:
            return max(int(str(page_label).strip()) - 1, 0)
        except (TypeError, ValueError):
            pass

    return fallback_index


def _has_page_metadata(chunks: list[tuple[str, dict]]) -> bool:
    for _, metadata in chunks:
        metadata = metadata or {}
        if any(key in metadata for key in ("page", "page_index", "page_label")):
            return True
    return False


def _document_full_text(file: FileModel, chunks: Optional[list[tuple[str, dict]]] = None) -> str:
    chunks = _get_document_chunks(file.id) if chunks is None else chunks
    if chunks:
        return "\n\n".join(text for text, _ in chunks if normalize_text(text)).strip()
    return ((file.data or {}).get("content") or "").strip()


def _split_text_by_chars(text: str, size: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    results = []
    cursor = 0
    text_length = len(text)
    minimum_window = max(int(size * 0.55), min(size, 1200))

    while cursor < text_length:
        upper_bound = min(cursor + size, text_length)
        next_cursor = upper_bound

        if upper_bound < text_length:
            boundary = text.rfind("\n\n", cursor + minimum_window, upper_bound)
            if boundary == -1:
                boundary = text.rfind(". ", cursor + minimum_window, upper_bound)
            if boundary == -1:
                boundary = text.rfind(" ", cursor + minimum_window, upper_bound)
            if boundary != -1:
                next_cursor = boundary + 1

        chunk = text[cursor:next_cursor].strip()
        if chunk:
            results.append(chunk)

        cursor = max(next_cursor, cursor + 1)

    return results


def build_incremental_document_chunks(file: FileModel, chunk_config: Optional[dict] = None) -> list[dict]:
    chunk_config = chunk_config or get_default_chunk_config()
    raw_chunks = _get_document_chunks(file.id)

    if raw_chunks and _has_page_metadata(raw_chunks):
        page_map: dict[int, list[str]] = defaultdict(list)
        for index, (text, metadata) in enumerate(raw_chunks):
            normalized = normalize_text(text)
            if not normalized:
                continue
            page_map[_parse_page_index(metadata, index)].append(text.strip())

        page_numbers = sorted(page_map.keys())
        if page_numbers:
            page_groups = [
                {
                    "pageIndex": page_index,
                    "text": "\n\n".join(page_map[page_index]).strip(),
                }
                for page_index in page_numbers
                if normalize_text("\n\n".join(page_map[page_index]))
            ]

            size = max(int(chunk_config.get("size") or DEFAULT_PAGE_CHUNK_SIZE), 1)
            chunks = []
            for chunk_index in range(0, len(page_groups), size):
                window = page_groups[chunk_index : chunk_index + size]
                start_page = window[0]["pageIndex"] + 1
                end_page = window[-1]["pageIndex"] + 1
                chunks.append(
                    {
                        "chunkIndex": len(chunks),
                        "sourceRange": f"p.{start_page}-{end_page}"
                        if start_page != end_page
                        else f"p.{start_page}",
                        "text": "\n\n".join(item["text"] for item in window if item["text"]).strip(),
                    }
                )
            return chunks

    full_text = _document_full_text(file, chunks=raw_chunks)
    char_size = max(int(chunk_config.get("fallback_size") or DEFAULT_CHAR_CHUNK_SIZE), 1200)
    char_chunks = _split_text_by_chars(full_text, char_size)
    return [
        {
            "chunkIndex": index,
            "sourceRange": f"chars {start}-{start + len(chunk_text)}",
            "text": chunk_text,
        }
        for index, (start, chunk_text) in enumerate(
            _iter_chunk_positions(char_chunks, full_text)
        )
    ]


def _iter_chunk_positions(chunks: list[str], source_text: str) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    cursor = 0
    for chunk in chunks:
        try:
            position = source_text.index(chunk, cursor)
        except ValueError:
            position = cursor
        positions.append((position, chunk))
        cursor = position + len(chunk)
    return positions


def _extract_sentences(text: str, *, max_sentences: int = 3, max_chars: int = 420) -> list[str]:
    normalized = normalize_text(text) or ""
    if not normalized:
        return []

    sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(normalized) if sentence.strip()]
    selected = []
    total_chars = 0
    for sentence in sentences:
        if len(selected) >= max_sentences:
            break
        if total_chars and total_chars + len(sentence) > max_chars:
            break
        selected.append(sentence)
        total_chars += len(sentence)

    if not selected and normalized:
        return [normalized[:max_chars].strip()]

    return selected


def _fallback_short_summary(text: str, *, max_chars: int = 420) -> str:
    sentences = _extract_sentences(text, max_sentences=3, max_chars=max_chars)
    if not sentences:
        return "Trecho sem texto suficiente para resumo."
    return " ".join(sentences)[:max_chars].strip()


def _extract_keywords(*values: Optional[str], limit: int = 8) -> list[str]:
    counter: Counter[str] = Counter()
    for value in values:
        normalized = normalize_text(value) or ""
        for raw_word in _WORD_RE.findall(normalized.lower()):
            word = raw_word.strip("-' ")
            if not word or word in _STOPWORDS:
                continue
            counter[word] += 1

    return [word for word, _ in counter.most_common(limit)]


async def summarize_chunk(
    request: Request,
    user,
    model_id: Optional[str],
    file: FileModel,
    chunk: dict,
) -> dict:
    fallback = {
        "summary": _fallback_short_summary(chunk.get("text") or ""),
        "confidenceNotes": None,
    }

    if not model_id:
        return fallback

    try:
        response = await _generate_structured_completion(
            request,
            user,
            model_id,
            schema_name="collection_chunk_summary",
            schema_properties={
                "summary": {"type": "string"},
                "confidenceNotes": {"type": ["string", "null"]},
            },
            required_fields=["summary", "confidenceNotes"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce resume apenas o conteudo fornecido. "
                        "Nao invente fatos. Destaque temas, argumentos, fatos e conclusoes explicitas. "
                        "Responda somente com JSON valido."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "documentTitle": first_clean_text(
                                (file.meta or {}).get("title"),
                                (file.meta or {}).get("name"),
                                file.filename,
                            )
                            or file.filename,
                            "sourceRange": chunk.get("sourceRange"),
                            "content": chunk.get("text") or "",
                            "instruction": "Produza um unico paragrafo curto e prudente.",
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            task="collection_synthesis_chunk_summary",
            task_body={"document_id": file.id, "chunk_index": chunk.get("chunkIndex")},
            max_tokens=420,
        )
        if not response:
            return fallback

        summary = normalize_text(response.get("summary")) or fallback["summary"]
        confidence_notes = normalize_text(response.get("confidenceNotes"))
        return {
            "summary": summary,
            "confidenceNotes": confidence_notes,
        }
    except Exception:
        log.warning("Chunk synthesis fallback used for file_id=%s", file.id, exc_info=True)
        return fallback


def _fallback_document_summary(file: FileModel, chunk_summaries: list[dict], warnings: list[str]) -> dict:
    meta = file.meta or {}
    content = " ".join(item.get("summary") or "" for item in chunk_summaries)
    title = first_clean_text(meta.get("title"), meta.get("name"), file.filename) or file.filename
    description = first_clean_text(meta.get("summary"), meta.get("description"))
    theme = first_clean_text(meta.get("document_type"), description, title) or title
    purpose = description or f"Documento relacionado a {title}."

    key_points = [
        item
        for item in (_fallback_short_summary(content, max_chars=180), *(entry.get("summary") for entry in chunk_summaries))
        if normalize_text(item)
    ]
    deduped_points = []
    seen = set()
    for point in key_points:
        folded = normalize_text(point).lower()
        if folded in seen:
            continue
        seen.add(folded)
        deduped_points.append(point)
        if len(deduped_points) >= 5:
            break

    findings = deduped_points[:4]
    conclusion = description or (deduped_points[-1] if deduped_points else "Nao foi possivel extrair uma conclusao confiavel.")
    keywords = _extract_keywords(
        title,
        description,
        content,
        " ".join(ensure_list(meta.get("tags"))),
        limit=8,
    )

    coverage_notes = "; ".join(warnings) if warnings else None
    return {
        "theme": theme,
        "purpose": purpose,
        "keyPoints": deduped_points or ["Resumo incremental gerado sem pontos destacados suficientes."],
        "findings": findings or ["Nao houve achados claros alem do conteudo resumido."],
        "conclusion": conclusion,
        "keywords": keywords,
        "coverageNotes": coverage_notes,
    }


async def consolidate_document_summary(
    request: Request,
    user,
    model_id: Optional[str],
    file: FileModel,
    chunk_summaries: list[dict],
    warnings: list[str],
) -> dict:
    fallback = _fallback_document_summary(file, chunk_summaries, warnings)
    if not model_id:
        return fallback

    meta = file.meta or {}
    title = first_clean_text(meta.get("title"), meta.get("name"), file.filename) or file.filename

    try:
        response = await _generate_structured_completion(
            request,
            user,
            model_id,
            schema_name="collection_document_summary",
            schema_properties={
                "theme": {"type": "string"},
                "purpose": {"type": "string"},
                "keyPoints": {"type": "array", "items": {"type": "string"}},
                "findings": {"type": "array", "items": {"type": "string"}},
                "conclusion": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "coverageNotes": {"type": ["string", "null"]},
            },
            required_fields=[
                "theme",
                "purpose",
                "keyPoints",
                "findings",
                "conclusion",
                "keywords",
                "coverageNotes",
            ],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce consolida resumos intermediarios de um documento em uma estrutura estavel. "
                        "Use apenas os resumos fornecidos. Nao invente conteudo ausente. "
                        "Responda somente com JSON valido."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "documentId": file.id,
                            "title": title,
                            "documentMetadata": {
                                "source": first_clean_text(meta.get("source")),
                                "documentType": first_clean_text(meta.get("document_type")),
                                "summary": first_clean_text(meta.get("summary"), meta.get("description")),
                                "tags": ensure_list(meta.get("tags")),
                            },
                            "chunkSummaries": chunk_summaries,
                            "warnings": warnings,
                            "instruction": (
                                "Produza um resumo consolidado prudente, previsivel e comparavel com outros documentos."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            task="collection_synthesis_document_summary",
            task_body={"document_id": file.id},
            max_tokens=900,
        )
        if not response:
            return fallback

        return {
            "theme": normalize_text(response.get("theme")) or fallback["theme"],
            "purpose": normalize_text(response.get("purpose")) or fallback["purpose"],
            "keyPoints": _clean_string_list(response.get("keyPoints")) or fallback["keyPoints"],
            "findings": _clean_string_list(response.get("findings")) or fallback["findings"],
            "conclusion": normalize_text(response.get("conclusion")) or fallback["conclusion"],
            "keywords": _clean_string_list(response.get("keywords")) or fallback["keywords"],
            "coverageNotes": normalize_text(response.get("coverageNotes")) or fallback["coverageNotes"],
        }
    except Exception:
        log.warning("Document consolidation fallback used for file_id=%s", file.id, exc_info=True)
        return fallback


def _clean_string_list(value) -> list[str]:
    cleaned = []
    for item in ensure_list(value):
        normalized = normalize_text(item)
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _build_common_theme_insights(document_summaries: list[dict]) -> tuple[list[str], list[str], list[str]]:
    theme_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()
    for item in document_summaries:
        theme = normalize_text(item.get("theme"))
        if theme:
            theme_counter[theme.lower()] += 1
        for keyword in _clean_string_list(item.get("keywords")):
            keyword_counter[keyword.lower()] += 1

    recurring = [theme for theme, count in theme_counter.items() if count > 1][:6]
    if not recurring:
        recurring = [keyword for keyword, _ in keyword_counter.most_common(6)]

    convergences = [
        f"Tema recorrente em mais de um documento: {theme}."
        for theme in recurring[:4]
    ]
    divergences = [
        f"Foco singular identificado em {normalize_text(item.get('title')) or 'um documento'}: {normalize_text(item.get('theme')) or 'tema especifico'}."
        for item in document_summaries[:4]
        if normalize_text(item.get("theme"))
    ]
    return recurring, convergences, divergences


def _fallback_collection_report(
    collection: KnowledgeModel,
    document_summaries: list[dict],
    warnings: list[str],
) -> dict:
    recurring_themes, convergences, divergences = _build_common_theme_insights(document_summaries)
    all_findings = []
    for document in document_summaries:
        all_findings.extend(_clean_string_list(document.get("findings")))
    main_findings = []
    seen = set()
    for finding in all_findings:
        folded = finding.lower()
        if folded in seen:
            continue
        seen.add(folded)
        main_findings.append(finding)
        if len(main_findings) >= 6:
            break

    overview = (
        f"A colecao {collection.name} foi sintetizada a partir de {len(document_summaries)} documento(s) "
        "usando resumos incrementais por documento."
    )
    gaps = warnings[:8]
    conclusion = (
        "A sintese indica um panorama util da colecao, mas deve ser lida com cautela "
        "quando houver documentos heterogeneos, lacunas de cobertura ou falhas de processamento."
    )

    future_questions = [
        f"Como aprofundar o tema recorrente '{theme}' na colecao?"
        for theme in recurring_themes[:3]
    ]
    if not future_questions:
        future_questions = ["Que subconjuntos da colecao merecem uma sintese especifica por pasta ou tema?"]

    return {
        "overview": overview,
        "recurringThemes": recurring_themes or ["Colecao heterogenea sem tema recorrente dominante."],
        "mainFindings": main_findings or ["Nao houve achados recorrentes suficientes para uma conclusao forte."],
        "convergences": convergences or ["Nao foi possivel identificar convergencias fortes com o fallback heuristico."],
        "divergences": divergences[:4]
        or ["Os documentos apresentam enfoques variados, sem divergencias claramente consolidadas."],
        "gaps": gaps,
        "conclusion": conclusion,
        "futureQuestions": future_questions,
    }


async def synthesize_collection_report(
    request: Request,
    user,
    model_id: Optional[str],
    collection: KnowledgeModel,
    document_summaries: list[dict],
    warnings: list[str],
) -> dict:
    fallback = _fallback_collection_report(collection, document_summaries, warnings)
    if not model_id:
        return fallback

    try:
        response = await _generate_structured_completion(
            request,
            user,
            model_id,
            schema_name="collection_synthesis_final_report",
            schema_properties={
                "overview": {"type": "string"},
                "recurringThemes": {"type": "array", "items": {"type": "string"}},
                "mainFindings": {"type": "array", "items": {"type": "string"}},
                "convergences": {"type": "array", "items": {"type": "string"}},
                "divergences": {"type": "array", "items": {"type": "string"}},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "conclusion": {"type": "string"},
                "futureQuestions": {"type": "array", "items": {"type": "string"}},
            },
            required_fields=[
                "overview",
                "recurringThemes",
                "mainFindings",
                "convergences",
                "divergences",
                "gaps",
                "conclusion",
                "futureQuestions",
            ],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce produz uma sintese transversal prudente de uma colecao de documentos. "
                        "Use apenas os resumos padronizados fornecidos. "
                        "Sinalize lacunas quando existirem. "
                        "Responda somente com JSON valido."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "collection": {
                                "id": collection.id,
                                "name": collection.name,
                                "description": collection.description,
                            },
                            "documentSummaries": document_summaries,
                            "warnings": warnings,
                            "instruction": (
                                "Produza uma sintese analitica pre-gerada, com conclusoes prudentes e rastreaveis."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            task="collection_synthesis_final_report",
            task_body={"collection_id": collection.id},
            max_tokens=1200,
        )
        if not response:
            return fallback

        return {
            "overview": normalize_text(response.get("overview")) or fallback["overview"],
            "recurringThemes": _clean_string_list(response.get("recurringThemes")) or fallback["recurringThemes"],
            "mainFindings": _clean_string_list(response.get("mainFindings")) or fallback["mainFindings"],
            "convergences": _clean_string_list(response.get("convergences")) or fallback["convergences"],
            "divergences": _clean_string_list(response.get("divergences")) or fallback["divergences"],
            "gaps": _clean_string_list(response.get("gaps")) or fallback["gaps"],
            "conclusion": normalize_text(response.get("conclusion")) or fallback["conclusion"],
            "futureQuestions": _clean_string_list(response.get("futureQuestions")) or fallback["futureQuestions"],
        }
    except Exception:
        log.warning(
            "Collection synthesis fallback used for collection_id=%s",
            collection.id,
            exc_info=True,
        )
        return fallback


def _parse_page_count(file: FileModel, document_chunks: list[dict]) -> Optional[int]:
    meta = file.meta or {}
    for key in ("page_count", "pages", "pageCount", "total_pages"):
        value = meta.get(key)
        if value is None:
            continue
        try:
            parsed = int(str(value).strip())
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            continue

    page_ranges = [chunk for chunk in document_chunks if str(chunk.get("sourceRange", "")).startswith("p.")]
    if page_ranges:
        last_range = str(page_ranges[-1].get("sourceRange"))
        numbers = re.findall(r"\d+", last_range)
        if numbers:
            return max(int(number) for number in numbers)

    return None


def _is_processable_document(file: FileModel) -> tuple[bool, Optional[str]]:
    if file.is_archived:
        return False, "Documento arquivado foi ignorado."

    raw_text = _document_full_text(file)
    if not normalize_text(raw_text):
        return False, "Documento sem texto processavel."

    return True, None


def _select_processable_documents(collection_id: str, db=None) -> tuple[list[FileModel], list[str]]:
    files = Knowledges.get_files_by_id(collection_id, db=db)
    warnings = []
    selected = []

    for file in sorted(
        files,
        key=lambda item: (
            first_clean_text((item.meta or {}).get("folder_path")) or "",
            first_clean_text((item.meta or {}).get("title"), item.filename) or item.filename,
        ),
    ):
        processable, warning = _is_processable_document(file)
        if processable:
            selected.append(file)
        elif warning:
            title = first_clean_text((file.meta or {}).get("title"), file.filename) or file.filename
            warnings.append(f"{title}: {warning}")

    return selected, warnings


def render_synthesis_markdown(
    collection: KnowledgeModel,
    report: dict,
    *,
    generated_at: Optional[int],
    model_name: Optional[str],
    documents_processed: int,
    documents_failed: int,
    warnings: list[str],
) -> str:
    generated_label = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(generated_at))
        if generated_at
        else "momento desconhecido"
    )

    lines = [
        f"# Síntese da coleção: {collection.name}",
        "",
        f"_Gerada em {generated_label}._",
    ]

    if model_name:
        lines.append(f"_Modelo: {model_name}._")
    lines.append(
        f"_Documentos processados: {documents_processed} | Falhas: {documents_failed}._"
    )
    lines.append("")
    lines.append("## Visão geral")
    lines.append(report.get("overview") or "Sem visão geral disponível.")

    section_map = [
        ("## Temas recorrentes", report.get("recurringThemes") or []),
        ("## Principais achados", report.get("mainFindings") or []),
        ("## Convergências", report.get("convergences") or []),
        ("## Divergências", report.get("divergences") or []),
        ("## Lacunas", report.get("gaps") or []),
        ("## Perguntas futuras", report.get("futureQuestions") or []),
    ]

    for title, items in section_map:
        lines.append("")
        lines.append(title)
        if items:
            lines.extend([f"- {item}" for item in items])
        else:
            lines.append("- Nenhum item registado.")

    lines.append("")
    lines.append("## Conclusão")
    lines.append(report.get("conclusion") or "Sem conclusão disponível.")

    if warnings:
        lines.append("")
        lines.append("## Avisos")
        lines.extend([f"- {warning}" for warning in warnings[:12]])

    return "\n".join(lines).strip()


def persist_synthesis_note(
    request: Request,
    collection: KnowledgeModel,
    report_record,
    user,
    report: dict,
    *,
    final_status: str,
    warnings: list[str],
    db=None,
) -> tuple[Optional[str], list[str], str]:
    generated_at = int(time.time())
    markdown_content = render_synthesis_markdown(
        collection,
        report,
        generated_at=generated_at,
        model_name=report_record.model_name,
        documents_processed=report_record.documents_processed,
        documents_failed=report_record.documents_failed,
        warnings=warnings,
    )
    html = markdown.markdown(markdown_content)

    note_title = f"Síntese - {collection.name}"
    existing_note = (
        Notes.get_note_by_id(report_record.note_id, db=db) if report_record.note_id else None
    )
    existing_note_data = (
        existing_note.data if existing_note and isinstance(existing_note.data, dict) else {}
    )
    note_meta = {
        **(existing_note.meta if existing_note and existing_note.meta else {}),
        "category": "sintese",
        "collection_id": collection.id,
        "collection_name": collection.name,
        "generated_by": "collection_synthesis",
        "related": {
            "knowledge_ids": [collection.id],
            "document_ids": report_record.included_document_ids or [],
        },
        "synthesis": {
            "report_id": report_record.id,
            "generated_at": generated_at,
            "status": final_status,
            "model": report_record.model_name,
        },
    }

    access_grants = [
        grant.model_dump() if hasattr(grant, "model_dump") else grant
        for grant in (collection.access_grants or [])
    ]
    note_data = {
        "content": {
            "json": None,
            "md": markdown_content,
            "html": html,
        },
        "versions": existing_note_data.get("versions") or [],
        "files": existing_note_data.get("files") or None,
    }

    if existing_note:
        note = Notes.update_note_by_id(
            existing_note.id,
            NoteUpdateForm(
                title=note_title,
                data=note_data,
                meta=note_meta,
                access_grants=access_grants,
            ),
            db=db,
        )
    else:
        note = Notes.insert_new_note(
            user.id,
            NoteForm(
                title=note_title,
                data=note_data,
                meta=note_meta,
                access_grants=access_grants,
            ),
            db=db,
    )

    if note is None:
        return None, [], final_status

    linked_note_ids = get_linked_note_ids(collection.meta or {})
    if note.id not in linked_note_ids:
        linked_note_ids.append(note.id)
        Knowledges.update_knowledge_by_id(
            id=collection.id,
            form_data=KnowledgeForm(
                name=collection.name,
                description=collection.description,
                meta=set_linked_note_ids(collection.meta or {}, linked_note_ids),
                access_grants=access_grants,
            ),
            db=db,
        )
        collection = Knowledges.get_knowledge_by_id(collection.id, db=db) or collection

    note_warnings: list[str] = []
    effective_status = final_status

    try:
        upsert_linked_note_vector(request, collection, note, user=user)
    except Exception as exc:
        log.warning(
            "Synthesis note vector indexing skipped for collection_id=%s note_id=%s",
            collection.id,
            note.id,
            exc_info=True,
        )
        detail = normalize_text(str(exc))
        warning = (
            "A nota final da sintese foi criada, mas a indexacao vetorial nao foi concluida."
        )
        if detail:
            warning = f"{warning} {detail}"
        note_warnings.append(warning)
        effective_status = SYNTHESIS_COMPLETED_WITH_WARNINGS_STATUS

    return note.id, note_warnings, effective_status


async def process_collection_synthesis_job(
    request: Request,
    collection_id: str,
    report_id: str,
    user_id: str,
    *,
    requested_model_id: Optional[str] = None,
    force_reprocess: bool = False,
) -> None:
    collection = Knowledges.get_knowledge_by_id(collection_id)
    user = Users.get_user_by_id(user_id)

    if collection is None or user is None:
        if report_id:
            CollectionReports.update_report_by_id(
                report_id,
                {
                    "status": SYNTHESIS_FAILED_STATUS,
                    "error": "Colecao ou utilizador nao encontrado.",
                    "current_step": None,
                    "current_document_id": None,
                    "current_document_title": None,
                    "completed_at": int(time.time()),
                },
            )
        return

    started_at = int(time.time())
    CollectionReports.update_report_by_id(
        report_id,
        {
            "status": SYNTHESIS_PROCESSING_STATUS,
            "current_step": "document_selection",
            "current_document_id": None,
            "current_document_title": None,
            "documents_total": 0,
            "documents_processed": 0,
            "documents_failed": 0,
            "warnings": [],
            "failed_documents": [],
            "error": None,
            "started_at": started_at,
            "completed_at": None,
            "final_report": None,
        },
    )

    try:
        if not getattr(request.app.state, "MODELS", None):
            await get_all_models(request, user=user)

        model_id = resolve_synthesis_model_id(request, user, requested_model_id)
        chunk_config = get_default_chunk_config()

        report_record = CollectionReports.update_report_by_id(
            report_id,
            {"model_name": model_id, "chunk_config": chunk_config},
        )
        if report_record is None:
            return

        files, warnings = _select_processable_documents(collection_id)
        document_ids = [file.id for file in files]

        if force_reprocess:
            CollectionReports.delete_document_summaries(report_record.id)
        else:
            CollectionReports.delete_document_summaries_not_in(report_record.id, document_ids)

        CollectionReports.update_report_by_id(
            report_record.id,
            {
                "documents_total": len(files),
                "included_document_ids": document_ids,
                "warnings": warnings,
            },
        )

        if not files:
            CollectionReports.update_report_by_id(
                report_record.id,
                {
                    "status": SYNTHESIS_FAILED_STATUS,
                    "error": "Nenhum documento processavel encontrado na colecao.",
                    "current_step": None,
                    "completed_at": int(time.time()),
                },
            )
            return

        processed = 0
        failed = 0
        failed_documents = []
        final_document_summaries = []

        for file in files:
            title = first_clean_text((file.meta or {}).get("title"), (file.meta or {}).get("name"), file.filename) or file.filename
            document_chunks = build_incremental_document_chunks(file, chunk_config)
            page_count = _parse_page_count(file, document_chunks)
            document_updated_at = normalize_timestamp(file.updated_at)
            existing_summary = CollectionReports.get_document_summary(report_record.id, file.id)

            CollectionReports.update_report_by_id(
                report_record.id,
                {
                    "current_step": "document_summarization",
                    "current_document_id": file.id,
                    "current_document_title": title,
                },
            )

            if (
                not force_reprocess
                and existing_summary
                and existing_summary.status == SYNTHESIS_COMPLETED_STATUS
                and existing_summary.document_updated_at == document_updated_at
                and existing_summary.summary
            ):
                processed += 1
                cached_summary = {
                    **(existing_summary.summary or {}),
                    "documentId": file.id,
                    "title": title,
                }
                final_document_summaries.append(cached_summary)
                CollectionReports.update_report_by_id(
                    report_record.id,
                    {
                        "documents_processed": processed,
                        "documents_failed": failed,
                        "failed_documents": failed_documents,
                    },
                )
                continue

            document_warnings = []
            if is_likely_garbled_text(_document_full_text(file)):
                document_warnings.append("Texto extraido com sinais de degradacao ou ruido.")

            if not document_chunks:
                failed += 1
                error_message = "Nao foi possivel gerar chunks para o documento."
                failed_documents.append(
                    {"document_id": file.id, "title": title, "error": error_message}
                )
                CollectionReports.upsert_document_summary(
                    report_record.id,
                    file.id,
                    title,
                    {
                        "status": SYNTHESIS_FAILED_STATUS,
                        "page_count": page_count,
                        "source_type": first_clean_text((file.meta or {}).get("source_type"), (file.meta or {}).get("content_type")),
                        "document_updated_at": document_updated_at,
                        "text_available": False,
                        "warnings": document_warnings,
                        "error": error_message,
                    },
                )
                CollectionReports.update_report_by_id(
                    report_record.id,
                    {
                        "documents_processed": processed,
                        "documents_failed": failed,
                        "failed_documents": failed_documents,
                    },
                )
                continue

            CollectionReports.upsert_document_summary(
                report_record.id,
                file.id,
                title,
                {
                    "status": SYNTHESIS_PROCESSING_STATUS,
                    "source_type": first_clean_text(
                        (file.meta or {}).get("source_type"),
                        (file.meta or {}).get("content_type"),
                    ),
                    "page_count": page_count,
                    "document_updated_at": document_updated_at,
                    "text_available": True,
                    "chunks": document_chunks,
                    "chunk_summaries": [],
                    "summary": None,
                    "warnings": document_warnings,
                    "error": None,
                },
            )

            chunk_summaries = []
            try:
                for chunk in document_chunks:
                    chunk_result = await summarize_chunk(request, user, model_id, file, chunk)
                    chunk_entry = {
                        "chunkIndex": chunk.get("chunkIndex"),
                        "sourceRange": chunk.get("sourceRange"),
                        "summary": chunk_result.get("summary"),
                        "confidenceNotes": chunk_result.get("confidenceNotes"),
                    }
                    chunk_summaries.append(chunk_entry)
                    CollectionReports.upsert_document_summary(
                        report_record.id,
                        file.id,
                        title,
                        {
                            "status": SYNTHESIS_PROCESSING_STATUS,
                            "source_type": first_clean_text(
                                (file.meta or {}).get("source_type"),
                                (file.meta or {}).get("content_type"),
                            ),
                            "page_count": page_count,
                            "document_updated_at": document_updated_at,
                            "text_available": True,
                            "chunks": document_chunks,
                            "chunk_summaries": chunk_summaries,
                            "warnings": document_warnings,
                            "error": None,
                        },
                    )

                CollectionReports.update_report_by_id(
                    report_record.id,
                    {
                        "current_step": "document_consolidation",
                        "current_document_id": file.id,
                        "current_document_title": title,
                    },
                )

                consolidated_summary = await consolidate_document_summary(
                    request,
                    user,
                    model_id,
                    file,
                    chunk_summaries,
                    document_warnings,
                )
                persisted_summary = {
                    "documentId": file.id,
                    "title": title,
                    **consolidated_summary,
                }
                CollectionReports.upsert_document_summary(
                    report_record.id,
                    file.id,
                    title,
                    {
                        "status": SYNTHESIS_COMPLETED_STATUS,
                        "source_type": first_clean_text(
                            (file.meta or {}).get("source_type"),
                            (file.meta or {}).get("content_type"),
                        ),
                        "page_count": page_count,
                        "document_updated_at": document_updated_at,
                        "text_available": True,
                        "chunks": document_chunks,
                        "chunk_summaries": chunk_summaries,
                        "summary": persisted_summary,
                        "warnings": document_warnings,
                        "error": None,
                    },
                )
                final_document_summaries.append(persisted_summary)
                processed += 1
            except Exception as exc:
                failed += 1
                error_message = normalize_text(str(exc)) or "Erro ao sintetizar o documento."
                failed_documents.append(
                    {"document_id": file.id, "title": title, "error": error_message}
                )
                CollectionReports.upsert_document_summary(
                    report_record.id,
                    file.id,
                    title,
                    {
                        "status": SYNTHESIS_FAILED_STATUS,
                        "source_type": first_clean_text(
                            (file.meta or {}).get("source_type"),
                            (file.meta or {}).get("content_type"),
                        ),
                        "page_count": page_count,
                        "document_updated_at": document_updated_at,
                        "text_available": True,
                        "chunks": document_chunks,
                        "chunk_summaries": chunk_summaries,
                        "summary": None,
                        "warnings": document_warnings,
                        "error": error_message,
                    },
                )

            CollectionReports.update_report_by_id(
                report_record.id,
                {
                    "documents_processed": processed,
                    "documents_failed": failed,
                    "failed_documents": failed_documents,
                },
            )

        warnings = [
            *warnings,
            *[
                f"{item['title']}: {item['error']}"
                for item in failed_documents
                if item.get("title") and item.get("error")
            ],
        ]

        if not final_document_summaries:
            CollectionReports.update_report_by_id(
                report_record.id,
                {
                    "status": SYNTHESIS_FAILED_STATUS,
                    "warnings": warnings,
                    "error": "Nenhum documento foi sintetizado com sucesso.",
                    "current_step": None,
                    "current_document_id": None,
                    "current_document_title": None,
                    "completed_at": int(time.time()),
                },
            )
            return

        CollectionReports.update_report_by_id(
            report_record.id,
            {
                "current_step": "collection_synthesis",
                "current_document_id": None,
                "current_document_title": None,
                "warnings": warnings,
            },
        )

        final_report = await synthesize_collection_report(
            request,
            user,
            model_id,
            collection,
            final_document_summaries,
            warnings,
        )
        status_value = (
            SYNTHESIS_COMPLETED_WITH_WARNINGS_STATUS
            if warnings or failed > 0
            else SYNTHESIS_COMPLETED_STATUS
        )

        report_record = CollectionReports.update_report_by_id(
            report_record.id,
            {
                "status": status_value,
                "current_step": "note_persistence",
                "final_report": final_report,
                "warnings": warnings,
            },
        )

        if report_record is None:
            return

        note_id, note_warnings, status_value = persist_synthesis_note(
            request,
            collection,
            report_record,
            user,
            final_report,
            final_status=status_value,
            warnings=warnings,
        )
        warnings = [*warnings, *note_warnings]

        CollectionReports.update_report_by_id(
            report_record.id,
            {
                "status": status_value,
                "note_id": note_id,
                "final_report": final_report,
                "warnings": warnings,
                "current_step": None,
                "current_document_id": None,
                "current_document_title": None,
                "completed_at": int(time.time()),
            },
        )
    except Exception as exc:
        log.exception("Collection synthesis failed for collection_id=%s", collection_id)
        CollectionReports.update_report_by_id(
            report_id,
            {
                "status": SYNTHESIS_FAILED_STATUS,
                "error": normalize_text(str(exc)) or "Falha ao gerar a síntese.",
                "current_step": None,
                "current_document_id": None,
                "current_document_title": None,
                "completed_at": int(time.time()),
            },
        )
