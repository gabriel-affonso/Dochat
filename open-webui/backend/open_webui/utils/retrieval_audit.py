import re
import time
from typing import Any, Optional
from urllib.parse import quote

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.utils.dochat_documents import (
    first_clean_text,
    normalize_document_version_control,
)


def _safe_identifier(value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    return normalized or fallback


def _safe_int(*values) -> Optional[int]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _safe_float(*values) -> Optional[float]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_excerpt(text: Optional[str], limit: int = 560) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _normalize_document_status(
    chunk_metadata: dict,
    file_meta: dict,
    version_control: dict,
) -> str:
    explicit_status = first_clean_text(
        chunk_metadata.get("document_status"),
        file_meta.get("document_status"),
        file_meta.get("status"),
        file_meta.get("workflow_status"),
        file_meta.get("review_status"),
        file_meta.get("state"),
    )
    if explicit_status:
        return explicit_status

    if version_control.get("status") == "checked_out":
        return "Em revisão"

    return "Não informado"


def _status_rank(status: Optional[str]) -> int:
    normalized = (status or "").strip().lower()
    if any(token in normalized for token in ("final", "conclu", "aprovad", "vigente")):
        return 4
    if any(token in normalized for token in ("revis", "review", "checked", "checkout")):
        return 3
    if any(token in normalized for token in ("rascun", "draft", "elabora", "pendente")):
        return 2
    if normalized:
        return 1
    return 0


def _source_type_rank(source_type: Optional[str]) -> int:
    normalized = (source_type or "").strip().lower()
    if any(token in normalized for token in ("original_pdf", "original", "canon")):
        return 4
    if any(
        token in normalized
        for token in ("markdown", "transcription", "structured", "docling", "extraction")
    ):
        return 3
    if "ocr" in normalized:
        return 2
    if normalized:
        return 1
    return 0


def _revision_rank(revision: Optional[int]) -> int:
    return max(int(revision or 0), 0)


def _compute_evidence_score(
    retrieval_score: Optional[float],
    rerank_score: Optional[float],
    status_rank: int,
    source_type_rank: int,
    revision: Optional[int],
) -> float:
    base_score = _safe_float(rerank_score, retrieval_score, 0.0) or 0.0
    return round(
        base_score
        + (status_rank * 0.08)
        + (source_type_rank * 0.04)
        + min(_revision_rank(revision), 9) * 0.005,
        4,
    )


def _resolve_collection_from_source(
    source_info: dict,
    file_id: Optional[str],
    collection_cache: dict[str, tuple[Optional[str], Optional[str]]],
) -> tuple[Optional[str], Optional[str]]:
    source_type = source_info.get("type")
    source_id = source_info.get("id")
    source_name = source_info.get("name")

    if source_type == "collection" and source_id:
        return source_id, source_name

    if not file_id:
        return None, None

    if file_id in collection_cache:
        return collection_cache[file_id]

    collection_id = None
    collection_name = None
    for knowledge in Knowledges.get_knowledges_by_file_id(file_id) or []:
        collection_id = getattr(knowledge, "id", None) or knowledge.get("id")
        collection_name = getattr(knowledge, "name", None) or knowledge.get("name")
        if collection_id:
            break

    collection_cache[file_id] = (collection_id, collection_name)
    return collection_cache[file_id]


def _build_navigation_payload(
    *,
    collection_id: Optional[str],
    file_id: Optional[str],
    page_number: Optional[int],
    revision: Optional[int],
    source_url: Optional[str],
) -> dict:
    query = []
    if file_id:
        query.append(f"fileId={quote(str(file_id))}")
    if page_number:
        query.append("tab=original")
        query.append(f"page={page_number}")
    elif revision:
        query.append("tab=versions")
        query.append(f"revision={revision}")
    else:
        query.append("tab=markdown")

    if revision and page_number:
        query.append(f"revision={revision}")

    if collection_id and file_id:
        href = f"/workspace/knowledge/{quote(str(collection_id))}"
        if query:
            href = f"{href}?{'&'.join(query)}"
        return {
            "document_href": href,
            "snippet_href": href,
            "document_target": "_self",
        }

    if file_id:
        file_href = f"/api/v1/files/{quote(str(file_id))}/content"
        if page_number:
            file_href = f"{file_href}#page={page_number}"
        return {
            "document_href": file_href,
            "snippet_href": file_href,
            "document_target": "_blank",
        }

    if source_url:
        return {
            "document_href": source_url,
            "snippet_href": source_url,
            "document_target": "_blank",
        }

    return {
        "document_href": None,
        "snippet_href": None,
        "document_target": "_self",
    }


def build_retrieval_audit(
    sources: list[dict],
    *,
    assistant_message_id: Optional[str] = None,
    user_message_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    query_text: Optional[str] = None,
    model_id: Optional[str] = None,
    top_k_requested: Optional[int] = None,
) -> Optional[dict]:
    if not sources:
        return None

    audit_id = f"ra_{_safe_identifier(assistant_message_id, str(int(time.time() * 1000)))}"
    file_cache: dict[str, Any] = {}
    collection_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
    source_reference_ids: dict[str, int] = {}
    chunks = []

    for source in sources:
        source_info = source.get("source") or {}
        documents = source.get("document") or []
        metadatas = source.get("metadata") or []
        distances = source.get("distances") or []

        for index, document_text in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = distances[index] if index < len(distances) else None

            file_id = first_clean_text(
                metadata.get("file_id"),
                source_info.get("id") if source_info.get("type") == "file" else None,
            )
            if file_id and file_id not in file_cache:
                file_cache[file_id] = Files.get_file_by_id(file_id)

            file = file_cache.get(file_id)
            file_meta = (getattr(file, "meta", None) or {}) if file else {}
            version_control = normalize_document_version_control(file_meta)

            collection_id, collection_name = _resolve_collection_from_source(
                source_info, file_id, collection_cache
            )

            document_name = (
                first_clean_text(
                    metadata.get("name"),
                    metadata.get("document_name"),
                    file_meta.get("title"),
                    file_meta.get("name"),
                    getattr(file, "filename", None),
                    metadata.get("source"),
                )
                or "Documento"
            )
            document_key = (
                first_clean_text(
                    metadata.get("logical_document_id"),
                    metadata.get("document_id"),
                    file_meta.get("logical_document_id"),
                    file_meta.get("document_id"),
                )
                or document_name.lower()
            )

            source_type = first_clean_text(
                metadata.get("source_type"),
                file_meta.get("source_type"),
                file_meta.get("content_type"),
                source_info.get("type"),
            )
            document_status = _normalize_document_status(
                metadata,
                file_meta,
                version_control,
            )
            document_type = first_clean_text(
                metadata.get("document_type"),
                file_meta.get("document_type"),
            )

            revision = _safe_int(
                metadata.get("revision"),
                metadata.get("version"),
                metadata.get("document_version"),
                version_control.get("revision"),
                file_meta.get("version"),
            )
            version_label = first_clean_text(
                metadata.get("version_label"),
                metadata.get("document_version_label"),
                file_meta.get("version_label"),
                file_meta.get("version"),
            )
            if not version_label and revision:
                version_label = f"Rev. {revision}"

            page_index = _safe_int(metadata.get("page"))
            page_number = (
                page_index + 1 if page_index is not None else _safe_int(metadata.get("page_label"))
            )

            retrieval_score = _safe_float(metadata.get("retrieval_score"), distance)
            rerank_score = _safe_float(metadata.get("rerank_score"), metadata.get("score"))

            status_rank = _status_rank(document_status)
            source_rank = _source_type_rank(source_type)
            evidence_score = _compute_evidence_score(
                retrieval_score=retrieval_score,
                rerank_score=rerank_score,
                status_rank=status_rank,
                source_type_rank=source_rank,
                revision=revision,
            )

            source_key = (
                first_clean_text(metadata.get("source"))
                or first_clean_text(source_info.get("id"))
                or document_key
            )
            if source_key not in source_reference_ids:
                source_reference_ids[source_key] = len(source_reference_ids) + 1

            source_url = first_clean_text(metadata.get("url"), source_info.get("url"))
            navigation = _build_navigation_payload(
                collection_id=collection_id,
                file_id=file_id,
                page_number=page_number,
                revision=revision,
                source_url=source_url,
            )

            chunk_id = f"{audit_id}_c{len(chunks) + 1}"
            chunks.append(
                {
                    "id": chunk_id,
                    "rank_position": len(chunks) + 1,
                    "source_reference_id": source_reference_ids[source_key],
                    "source_key": source_key,
                    "document_key": document_key,
                    "collection_id": collection_id,
                    "collection_name": collection_name,
                    "document_id": first_clean_text(
                        metadata.get("document_id"),
                        file_meta.get("document_id"),
                        file_id,
                    )
                    or file_id,
                    "file_id": file_id,
                    "document_name": document_name,
                    "document_type": document_type,
                    "document_status": document_status,
                    "source_type": source_type,
                    "revision": revision,
                    "version_label": version_label,
                    "page_number": page_number,
                    "page_index": page_index,
                    "section_label": first_clean_text(metadata.get("section_label")),
                    "chunk_index": _safe_int(metadata.get("chunk_index"), index),
                    "offset_start": _safe_int(
                        metadata.get("offset_start"),
                        metadata.get("start_index"),
                    ),
                    "offset_end": _safe_int(
                        metadata.get("offset_end"),
                        metadata.get("end_index"),
                    ),
                    "retrieval_score": retrieval_score,
                    "rerank_score": rerank_score,
                    "evidence_score": evidence_score,
                    "text_excerpt": _normalize_excerpt(document_text),
                    "is_used_in_answer": False,
                    "is_cited_in_ui": False,
                    "used_reason": None,
                    "navigation": navigation,
                    "metadata": {
                        "collection_name": metadata.get("collection_name"),
                        "page_label": metadata.get("page_label"),
                        "raw_source": metadata.get("source"),
                    },
                }
            )

    if not chunks:
        return None

    ranked_chunks = sorted(
        chunks,
        key=lambda chunk: (
            chunk.get("evidence_score") or 0.0,
            chunk.get("rerank_score") or 0.0,
            chunk.get("retrieval_score") or 0.0,
            _revision_rank(chunk.get("revision")),
        ),
        reverse=True,
    )

    used_limit = min(3, len(ranked_chunks))
    used_chunks = ranked_chunks[:used_limit]
    for idx, chunk in enumerate(used_chunks):
        chunk["is_used_in_answer"] = True
        chunk["used_reason"] = "priority_grounding"
        if idx < 2:
            chunk["is_cited_in_ui"] = True

    distinct_document_count = len(
        {
            chunk.get("document_id") or chunk.get("file_id") or chunk.get("document_name")
            for chunk in chunks
        }
    )

    alerts = []
    document_versions: dict[str, set[str]] = {}
    document_statuses: dict[str, set[str]] = {}

    for chunk in chunks:
        if chunk.get("version_label"):
            document_versions.setdefault(chunk["document_key"], set()).add(
                chunk["version_label"]
            )
        if chunk.get("document_status"):
            document_statuses.setdefault(chunk["document_key"], set()).add(
                chunk["document_status"]
            )

    conflicting_version_chunks = [
        chunk["id"]
        for chunk in chunks
        if len(document_versions.get(chunk["document_key"], set())) > 1
    ]
    if conflicting_version_chunks:
        alerts.append(
            {
                "id": "version_conflict",
                "type": "conflict",
                "title": "Conflito potencial entre versões",
                "description": "A recuperação trouxe versões diferentes do mesmo documento. A resposta deve priorizar a versão mais estável.",
                "chunk_ids": conflicting_version_chunks,
            }
        )

    status_conflict_chunks = [
        chunk["id"]
        for chunk in chunks
        if len(document_statuses.get(chunk["document_key"], set())) > 1
    ]
    if status_conflict_chunks:
        alerts.append(
            {
                "id": "status_conflict",
                "type": "warning",
                "title": "Estado documental heterogêneo",
                "description": "Há evidências com estados documentais diferentes para o mesmo item. Prefira a evidência mais governada.",
                "chunk_ids": status_conflict_chunks,
            }
        )

    top_score = ranked_chunks[0].get("evidence_score") or 0.0
    has_insufficient_support = top_score < 0.48 or (len(chunks) == 1 and top_score < 0.64)
    if has_insufficient_support:
        alerts.append(
            {
                "id": "insufficient_support",
                "type": "warning",
                "title": "Suporte documental parcial",
                "description": "As evidências recuperadas são poucas ou pouco confiáveis para sustentar uma resposta categórica.",
                "chunk_ids": [chunk["id"] for chunk in ranked_chunks[:2]],
            }
        )

    if top_score >= 0.9:
        grounding_confidence = "high"
    elif top_score >= 0.62:
        grounding_confidence = "medium"
    else:
        grounding_confidence = "low"

    return {
        "id": audit_id,
        "chat_id": chat_id,
        "message_id": assistant_message_id,
        "user_message_id": user_message_id,
        "query_text": query_text,
        "model_id": model_id,
        "top_k_requested": top_k_requested,
        "top_k_returned": len(chunks),
        "selection_method": "priority_grounding_v1",
        "created_at": int(time.time()),
        "summary": {
            "retrieved_chunks": len(chunks),
            "retrieved_documents": distinct_document_count,
            "used_chunks": len([chunk for chunk in chunks if chunk["is_used_in_answer"]]),
            "cited_chunks": len([chunk for chunk in chunks if chunk["is_cited_in_ui"]]),
            "has_conflict": any(alert["type"] == "conflict" for alert in alerts),
            "has_insufficient_support": has_insufficient_support,
            "grounding_confidence": grounding_confidence,
        },
        "alerts": alerts,
        "citation_source_map": source_reference_ids,
        "used_chunk_ids": [chunk["id"] for chunk in chunks if chunk["is_used_in_answer"]],
        "chunks": chunks,
    }


def build_grounding_guidance(retrieval_audit: Optional[dict]) -> str:
    if not retrieval_audit:
        return ""

    used_chunks = [
        chunk
        for chunk in retrieval_audit.get("chunks", [])
        if chunk.get("is_used_in_answer")
    ][:3]

    guidance_lines = [
        "### Grounding Rules:",
        "- Use the retrieved evidence as the primary factual basis for the answer.",
        "- Do not present unsupported claims as factual.",
        "- If the documentary support is insufficient, explicitly say that the support is partial or insufficient.",
        "- Prefer evidence from finalized or better-governed documents when statuses differ.",
        "- If the evidence conflicts, acknowledge the conflict instead of inventing certainty.",
    ]

    if used_chunks:
        guidance_lines.append("")
        guidance_lines.append("### Primary Evidence Candidates:")
        for chunk in used_chunks:
            details = [
                chunk.get("document_name") or "Documento",
                chunk.get("version_label"),
                chunk.get("document_status"),
                chunk.get("source_type"),
            ]
            details = [detail for detail in details if detail]
            guidance_lines.append(f"- [{chunk['id']}] {' | '.join(details)}")

    if retrieval_audit.get("summary", {}).get("has_conflict"):
        guidance_lines.append(
            "- There is a potential conflict in the recovered evidence. Mention that explicitly if it affects the answer."
        )

    return "\n".join(guidance_lines).strip()
