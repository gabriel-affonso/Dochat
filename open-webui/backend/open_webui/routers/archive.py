from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_session
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.notes import Notes
from open_webui.models.access_grants import AccessGrants
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import get_verified_user
from open_webui.utils.dochat_documents import (
    build_document_payload,
    collect_accessible_documents,
    normalize_timestamp,
)

router = APIRouter()


class ArchiveItemResponse(BaseModel):
    id: str
    type: str
    title: str
    updated_at: Optional[int] = None
    archived_at: Optional[int] = None
    collection: Optional[dict] = None
    tags: list[str] = []
    metadata: dict = {}


class ArchiveListResponse(BaseModel):
    items: list[ArchiveItemResponse]
    total: int


class ArchiveRestoreResponse(BaseModel):
    id: str
    type: str
    restored: bool


def _serialize_chat_item(chat) -> ArchiveItemResponse:
    meta = chat.meta or {}
    return ArchiveItemResponse(
        id=chat.id,
        type="chat",
        title=chat.title,
        updated_at=normalize_timestamp(chat.updated_at),
        archived_at=normalize_timestamp(chat.archived_at),
        collection=None,
        tags=list(meta.get("tags") or []),
        metadata={
            "share_id": chat.share_id,
            "folder_id": chat.folder_id,
            "document_context": meta.get("document_context"),
        },
    )


def _serialize_note_item(note) -> ArchiveItemResponse:
    meta = note.meta or {}
    return ArchiveItemResponse(
        id=note.id,
        type="note",
        title=note.title,
        updated_at=normalize_timestamp(note.updated_at),
        archived_at=normalize_timestamp(note.archived_at),
        collection=None,
        tags=list(meta.get("tags") or []),
        metadata={
            "last_opened_at": normalize_timestamp(note.last_opened_at),
            "related": meta.get("related"),
        },
    )


def _serialize_document_item(document: dict) -> ArchiveItemResponse:
    return ArchiveItemResponse(
        id=document["id"],
        type="document",
        title=document["title"],
        updated_at=document.get("updated_at"),
        archived_at=document.get("archived_at"),
        collection=document.get("collection"),
        tags=list(document.get("tags") or []),
        metadata=document.get("metadata") or {},
    )


def _matches_query(item: ArchiveItemResponse, query: Optional[str]) -> bool:
    if not query:
        return True
    query_key = query.lower().strip()
    haystack = " ".join(
        [
            item.title or "",
            " ".join(item.tags or []),
            (item.collection or {}).get("name") or "",
        ]
    ).lower()
    return query_key in haystack


@router.get("/", response_model=ArchiveListResponse)
async def get_archive_feed(
    type: Optional[str] = Query(None, pattern="^(chat|note|document)$"),
    query: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(60, ge=1, le=200),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    items: list[ArchiveItemResponse] = []

    if type in (None, "chat"):
        items.extend(
            [
                item
                for item in (
                    _serialize_chat_item(chat)
                    for chat in Chats.get_archived_chats_by_user_id(user.id, db=db)
                )
                if _matches_query(item, query)
            ]
        )

    if type in (None, "note"):
        notes = Notes.get_notes_by_user_id(
            user.id,
            "read",
            filter={"archived": True},
            skip=0,
            limit=1000,
            db=db,
        )
        items.extend(
            [
                item
                for item in (_serialize_note_item(note) for note in notes)
                if _matches_query(item, query)
            ]
        )

    if type in (None, "document"):
        documents = [
            build_document_payload(entry["file"], entry.get("collections"))
            for entry in collect_accessible_documents(user, db=db)
        ]
        items.extend(
            [
                item
                for item in (
                    _serialize_document_item(document)
                    for document in documents
                    if document.get("is_archived")
                )
                if _matches_query(item, query)
            ]
        )

    items = sorted(
        items,
        key=lambda item: (
            item.archived_at or 0,
            item.updated_at or 0,
            item.id,
        ),
        reverse=True,
    )

    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    return ArchiveListResponse(items=items[start:end], total=total)


@router.post("/{resource_type}/{resource_id}/restore", response_model=ArchiveRestoreResponse)
async def restore_archived_resource(
    resource_type: str,
    resource_id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    resource_type = resource_type.lower()

    if resource_type == "chat":
        chat = Chats.get_chat_by_id_and_user_id(resource_id, user.id, db=db)
        if not chat and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        restored = Chats.set_chat_archived_state_by_id(resource_id, False, db=db)
    elif resource_type == "note":
        note = Notes.get_note_by_id(resource_id, db=db)
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        if user.role != "admin" and user.id != note.user_id and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="note",
            resource_id=note.id,
            permission="write",
            db=db,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        restored = Notes.set_note_archived_by_id(resource_id, False, db=db)
    elif resource_type == "document":
        file = Files.get_file_by_id(resource_id, db=db)
        if not file:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
        if not (
            user.role == "admin"
            or file.user_id == user.id
            or has_access_to_file(resource_id, "write", user, db=db)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        restored = Files.set_file_archived_state_by_id(resource_id, False, db=db)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Unsupported archive resource type"),
        )

    if not restored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Unable to restore archived resource"),
        )

    return ArchiveRestoreResponse(
        id=resource_id,
        type=resource_type,
        restored=True,
    )
