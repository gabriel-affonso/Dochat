import logging
from typing import Optional

import markdown
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from open_webui.socket.main import sio

from open_webui.models.groups import Groups
from open_webui.models.users import Users, UserResponse
from open_webui.models.notes import (
    NoteListResponse,
    Notes,
    NoteModel,
    NoteForm,
    NoteUpdateForm,
    NoteUserResponse,
)
from open_webui.models.access_grants import AccessGrants, has_public_read_access_grant

from open_webui.config import (
    BYPASS_ADMIN_ACCESS_CONTROL,
)
from open_webui.constants import ERROR_MESSAGES


from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import has_permission, filter_allowed_access_grants
from open_webui.internal.db import get_session
from sqlalchemy.orm import Session
from open_webui.utils.dochat_collections import (
    get_linked_collections_for_note,
    remove_note_from_all_linked_collections,
    sync_note_to_linked_collections,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _truncate_note_data(data: Optional[dict], max_length: int = 1000) -> Optional[dict]:
    if not data:
        return data
    md = (data.get("content") or {}).get("md") or ""
    return {"content": {"md": md[:max_length]}}


############################
# GetNotes
############################


class NoteItemResponse(BaseModel):
    id: str
    user_id: str
    title: str
    data: Optional[dict]
    meta: Optional[dict] = None
    is_pinned: bool = False
    is_archived: bool = False
    archived_at: Optional[int] = None
    last_opened_at: Optional[int] = None
    updated_at: int
    created_at: int
    user: Optional[UserResponse] = None


def _ensure_notes_feature(request: Request, user, db: Session) -> None:
    if user.role != "admin" and not has_permission(
        user.id, "features.notes", request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


def _build_notes_filter(
    *,
    user,
    db: Session,
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    permission: str = "read",
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    archived: Optional[bool] = None,
    pinned: Optional[bool] = None,
    recent: bool = False,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
) -> dict:
    filter = {"permission": permission}

    if query:
        filter["query"] = query
    if view_option:
        filter["view_option"] = view_option
    if order_by:
        filter["order_by"] = order_by
    if direction:
        filter["direction"] = direction
    if archived is not None:
        filter["archived"] = archived
    if pinned is not None:
        filter["pinned"] = pinned
    if recent:
        filter["recent"] = True
        filter["order_by"] = order_by or "last_opened_at"
        filter["direction"] = direction or "desc"
    if date_from is not None:
        filter["date_from"] = date_from
    if date_to is not None:
        filter["date_to"] = date_to

    if not user.role == "admin" or not BYPASS_ADMIN_ACCESS_CONTROL:
        groups = Groups.get_groups_by_member_id(user.id, db=db)
        if groups:
            filter["group_ids"] = [group.id for group in groups]
        filter["user_id"] = user.id

    return filter


def _serialize_note_item(note, users: dict[str, object]) -> NoteItemResponse:
    return NoteItemResponse(
        **{
            **note.model_dump(),
            "data": _truncate_note_data(note.data),
            "user": (
                UserResponse(**users[note.user_id].model_dump())
                if note.user_id in users
                else None
            ),
        }
    )


def _get_note_write_access(note, user, db: Session) -> bool:
    return (
        user.role == "admin"
        or user.id == note.user_id
        or AccessGrants.has_access(
            user_id=user.id,
            resource_type="note",
            resource_id=note.id,
            permission="write",
            db=db,
        )
    )


def _serialize_note_response(note: NoteModel) -> dict:
    payload = note.model_dump()
    data = payload.get("data") or {}
    content = data.get("content") or {}
    md = content.get("md") or ""

    if md and not content.get("html"):
        payload["data"] = {
            **data,
            "content": {
                **content,
                "html": markdown.markdown(md),
            },
        }

    return payload


def _require_note_write_access(note, user, db: Session) -> None:
    if not _get_note_write_access(note, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


@router.get("/", response_model=list[NoteItemResponse])
async def get_notes(
    request: Request,
    page: Optional[int] = None,
    query: Optional[str] = None,
    archived: Optional[bool] = False,
    pinned: Optional[bool] = None,
    recent: bool = False,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    limit = None
    skip = None
    if page is not None:
        limit = 60
        skip = (page - 1) * limit

    filter = _build_notes_filter(
        user=user,
        db=db,
        query=query,
        permission="read",
        archived=archived,
        pinned=pinned,
        recent=recent,
        order_by=order_by,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
    )

    notes = Notes.get_notes_by_user_id(
        user.id, "read", filter=filter, skip=skip, limit=limit, db=db
    )
    if not notes:
        return []

    user_ids = list(set(note.user_id for note in notes))
    users = {user.id: user for user in Users.get_users_by_user_ids(user_ids, db=db)}

    return [_serialize_note_item(note, users) for note in notes]


@router.get("/pinned", response_model=list[NoteItemResponse])
async def get_pinned_notes(
    request: Request,
    page: Optional[int] = None,
    query: Optional[str] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    return await get_notes(
        request=request,
        page=page,
        query=query,
        archived=False,
        pinned=True,
        recent=False,
        user=user,
        db=db,
    )


@router.get("/archived", response_model=list[NoteItemResponse])
async def get_archived_notes(
    request: Request,
    page: Optional[int] = None,
    query: Optional[str] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    return await get_notes(
        request=request,
        page=page,
        query=query,
        archived=True,
        pinned=None,
        recent=False,
        user=user,
        db=db,
    )


@router.get("/recent", response_model=list[NoteItemResponse])
async def get_recent_notes(
    request: Request,
    page: Optional[int] = None,
    query: Optional[str] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    return await get_notes(
        request=request,
        page=page,
        query=query,
        archived=False,
        pinned=None,
        recent=True,
        user=user,
        db=db,
    )


@router.get("/search", response_model=NoteListResponse)
async def search_notes(
    request: Request,
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    permission: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    archived: Optional[bool] = None,
    pinned: Optional[bool] = None,
    recent: bool = False,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    limit = None
    skip = None
    if page is not None:
        limit = 60
        skip = (page - 1) * limit

    filter = _build_notes_filter(
        user=user,
        db=db,
        query=query,
        view_option=view_option,
        permission=permission or "read",
        order_by=order_by,
        direction=direction,
        archived=archived,
        pinned=pinned,
        recent=recent,
        date_from=date_from,
        date_to=date_to,
    )

    result = Notes.search_notes(user.id, filter, skip=skip, limit=limit, db=db)
    for note in result.items:
        note.data = _truncate_note_data(note.data)
    return result


############################
# CreateNewNote
############################


@router.post("/create", response_model=Optional[NoteModel])
async def create_new_note(
    request: Request,
    form_data: NoteForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    try:
        note = Notes.insert_new_note(user.id, form_data, db=db)
        return note
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetNoteById
############################


class NoteResponse(NoteModel):
    write_access: bool = False
    linked_collections: list[dict] = []


@router.get("/{id}", response_model=Optional[NoteResponse])
async def get_note_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if user.role != "admin" and (
        user.id != note.user_id
        and (
            not AccessGrants.has_access(
                user_id=user.id,
                resource_type="note",
                resource_id=note.id,
                permission="read",
                db=db,
            )
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    write_access = (
        _get_note_write_access(note, user, db)
    )

    return NoteResponse(
        **_serialize_note_response(note),
        write_access=write_access,
        linked_collections=get_linked_collections_for_note(
            note.id,
            user,
            note_meta=note.meta or {},
            db=db,
        ),
    )


############################
# UpdateNoteById
############################


@router.post("/{id}/update", response_model=Optional[NoteModel])
async def update_note_by_id(
    request: Request,
    id: str,
    form_data: NoteUpdateForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    _require_note_write_access(note, user, db)

    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_notes",
        db=db,
    )

    try:
        note = Notes.update_note_by_id(id, form_data, db=db)
        sync_note_to_linked_collections(request, note, user=user, db=db)
        await sio.emit(
            "note-events",
            note.model_dump(),
            to=f"note:{note.id}",
        )

        return note
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# UpdateNoteAccessById
############################


class NoteAccessGrantsForm(BaseModel):
    access_grants: list[dict]


@router.post("/{id}/access/update", response_model=Optional[NoteModel])
async def update_note_access_by_id(
    request: Request,
    id: str,
    form_data: NoteAccessGrantsForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    _require_note_write_access(note, user, db)

    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_notes",
        db=db,
    )

    AccessGrants.set_access_grants("note", id, form_data.access_grants, db=db)

    return Notes.get_note_by_id(id, db=db)


############################
# DeleteNoteById
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_note_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    _require_note_write_access(note, user, db)

    try:
        remove_note_from_all_linked_collections(id, db=db)
        note = Notes.delete_note_by_id(id, db=db)
        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


class NoteRenameForm(BaseModel):
    title: str


@router.post("/{id}/rename", response_model=Optional[NoteModel])
async def rename_note_by_id(
    request: Request,
    id: str,
    form_data: NoteRenameForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    _require_note_write_access(note, user, db)
    note = Notes.rename_note_by_id(id, form_data.title, db=db)
    if note:
        sync_note_to_linked_collections(request, note, user=user, db=db)
        await sio.emit("note-events", note.model_dump(), to=f"note:{note.id}")
    return note


class NotePinnedForm(BaseModel):
    is_pinned: Optional[bool] = None


@router.post("/{id}/pin", response_model=Optional[NoteModel])
async def pin_note_by_id(
    request: Request,
    id: str,
    form_data: Optional[NotePinnedForm] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    _require_note_write_access(note, user, db)
    target_state = (
        form_data.is_pinned if form_data and form_data.is_pinned is not None else None
    )
    note = (
        Notes.set_note_pinned_by_id(id, target_state, db=db)
        if target_state is not None
        else Notes.toggle_note_pinned_by_id(id, db=db)
    )
    if note:
        await sio.emit("note-events", note.model_dump(), to=f"note:{note.id}")
    return note


class NoteArchivedForm(BaseModel):
    is_archived: Optional[bool] = None


@router.post("/{id}/archive", response_model=Optional[NoteModel])
async def archive_note_by_id(
    request: Request,
    id: str,
    form_data: Optional[NoteArchivedForm] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    _require_note_write_access(note, user, db)
    target_state = (
        form_data.is_archived
        if form_data and form_data.is_archived is not None
        else None
    )
    note = (
        Notes.set_note_archived_by_id(id, target_state, db=db)
        if target_state is not None
        else Notes.toggle_note_archived_by_id(id, db=db)
    )
    if note:
        await sio.emit("note-events", note.model_dump(), to=f"note:{note.id}")
    return note


@router.post("/{id}/open", response_model=Optional[NoteModel])
async def open_note_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_notes_feature(request, user, db)

    note = Notes.get_note_by_id(id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if user.role != "admin" and (
        user.id != note.user_id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="note",
            resource_id=note.id,
            permission="read",
            db=db,
        )
        and not has_public_read_access_grant(note.access_grants)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    note = Notes.touch_last_opened_at_by_id(id, db=db)
    if note:
        await sio.emit("note-events", note.model_dump(), to=f"note:{note.id}")
    return note
