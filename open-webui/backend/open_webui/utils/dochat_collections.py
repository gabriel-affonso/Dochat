import hashlib
import logging
import time
from typing import Optional

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from open_webui.internal.db import get_db_context
from open_webui.models.access_grants import AccessGrants
from open_webui.models.knowledge import Knowledge, Knowledges
from open_webui.models.notes import NoteModel, Notes
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import save_docs_to_vector_db
from open_webui.utils.dochat_documents import (
    ensure_list,
    normalize_collection_payload,
    normalize_timestamp,
)

log = logging.getLogger(__name__)


def get_linked_note_ids(meta: Optional[dict]) -> list[str]:
    if not meta:
        return []

    linked_note_ids = ensure_list(meta.get("linked_note_ids"))
    seen = set()
    result = []
    for note_id in linked_note_ids:
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        result.append(note_id)
    return result


def set_linked_note_ids(meta: Optional[dict], note_ids: list[str]) -> dict:
    next_meta = {**(meta or {})}
    next_meta["linked_note_ids"] = note_ids
    return next_meta


def _can_read_note(note: NoteModel, user, db: Optional[Session] = None) -> bool:
    if not note:
        return False

    if user.role == "admin" or note.user_id == user.id:
        return True

    return AccessGrants.has_access(
        user_id=user.id,
        resource_type="note",
        resource_id=note.id,
        permission="read",
        db=db,
    )


def get_linked_notes_for_knowledge(
    knowledge,
    user,
    db: Optional[Session] = None,
) -> list[dict]:
    linked_notes = []
    for note_id in get_linked_note_ids(getattr(knowledge, "meta", None) or {}):
        note = Notes.get_note_by_id(note_id, db=db)
        if not note or not _can_read_note(note, user, db):
            continue

        linked_notes.append(
            {
                "id": note.id,
                "title": note.title,
                "created_at": normalize_timestamp(note.created_at),
                "updated_at": normalize_timestamp(note.updated_at),
                "last_opened_at": normalize_timestamp(note.last_opened_at),
                "is_pinned": note.is_pinned,
                "is_archived": note.is_archived,
                "meta": note.meta or {},
            }
        )

    linked_notes.sort(
        key=lambda item: (item.get("updated_at") or 0, item.get("title") or item["id"]),
        reverse=True,
    )
    return linked_notes


def get_linked_collections_for_note(
    note_id: str,
    user,
    note_meta: Optional[dict] = None,
    db: Optional[Session] = None,
) -> list[dict]:
    collection_id = (note_meta or {}).get("collection_id")
    if isinstance(collection_id, str) and collection_id:
        knowledge = Knowledges.get_knowledge_by_id(collection_id, db=db)
        if knowledge and (
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
            return [normalize_collection_payload(knowledge)]

    collections = []
    for knowledge in Knowledges.get_knowledge_bases(db=db):
        meta = getattr(knowledge, "meta", None) or {}
        if note_id not in get_linked_note_ids(meta):
            continue

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

        collections.append(normalize_collection_payload(knowledge))

    return collections


def _build_note_document(note: NoteModel) -> Optional[Document]:
    content = ((note.data or {}).get("content") or {}).get("md") or ""
    content = content.strip()
    if not content:
        return None

    note_hash = hashlib.sha256(
        f"{note.id}:{note.updated_at}:{note.title}:{content}".encode("utf-8")
    ).hexdigest()

    metadata = {
        "name": note.title,
        "title": note.title,
        "source": f"note:{note.id}",
        "source_type": "note",
        "note_id": note.id,
        "note_title": note.title,
        "updated_at": normalize_timestamp(note.updated_at),
        "created_at": normalize_timestamp(note.created_at),
        "hash": note_hash,
    }

    return Document(page_content=content, metadata=metadata)


def upsert_linked_note_vector(
    request,
    knowledge,
    note: NoteModel,
    user=None,
) -> bool:
    if not knowledge or not note:
        return False

    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id,
            filter={"source_type": "note", "note_id": note.id},
        )
    except Exception:
        log.debug("Linked note vector cleanup skipped", exc_info=True)

    doc = _build_note_document(note)
    if doc is None:
        return True

    save_docs_to_vector_db(
        request,
        [doc],
        knowledge.id,
        metadata={
            "source_type": "note",
            "note_id": note.id,
            "note_title": note.title,
        },
        add=True,
        user=user,
    )
    return True


def remove_linked_note_vector(knowledge_id: str, note_id: str) -> None:
    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge_id,
            filter={"source_type": "note", "note_id": note_id},
        )
    except Exception:
        log.debug("Linked note vector deletion skipped", exc_info=True)


def sync_note_to_linked_collections(
    request,
    note: NoteModel,
    user=None,
    db: Optional[Session] = None,
) -> list[str]:
    synced_collection_ids = []
    for knowledge in Knowledges.get_knowledge_bases(db=db):
        meta = getattr(knowledge, "meta", None) or {}
        if note.id not in get_linked_note_ids(meta):
            continue

        upsert_linked_note_vector(request, knowledge, note, user=user)
        synced_collection_ids.append(knowledge.id)

    return synced_collection_ids


def remove_note_from_all_linked_collections(
    note_id: str,
    db: Optional[Session] = None,
) -> list[str]:
    updated_collection_ids = []
    for knowledge in Knowledges.get_knowledge_bases(db=db):
        meta = getattr(knowledge, "meta", None) or {}
        linked_note_ids = get_linked_note_ids(meta)
        if note_id not in linked_note_ids:
            continue

        next_linked_note_ids = [value for value in linked_note_ids if value != note_id]
        with get_db_context(db) as local_db:
            local_db.query(Knowledge).filter_by(id=knowledge.id).update(
                {
                    "meta": set_linked_note_ids(meta, next_linked_note_ids),
                    "updated_at": int(time.time()),
                }
            )
            local_db.commit()

        remove_linked_note_vector(knowledge.id, note_id)
        updated_collection_ids.append(knowledge.id)

    return updated_collection_ids
