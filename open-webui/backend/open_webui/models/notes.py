import json
import time
import uuid
from typing import Optional
from functools import lru_cache

from sqlalchemy.orm import Session
from open_webui.internal.db import Base, get_db, get_db_context
from open_webui.models.groups import Groups
from open_webui.models.users import User, UserModel, Users, UserResponse
from open_webui.models.access_grants import AccessGrantModel, AccessGrants


from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, Text, JSON
from sqlalchemy import or_, func, cast

####################
# Note DB Schema
####################


class Note(Base):
    __tablename__ = "note"

    id = Column(Text, primary_key=True, unique=True)
    user_id = Column(Text)

    title = Column(Text)
    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)
    archived_at = Column(BigInteger, nullable=True)
    last_opened_at = Column(BigInteger, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class NoteModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    title: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    is_pinned: bool = False
    is_archived: bool = False
    archived_at: Optional[int] = None
    last_opened_at: Optional[int] = None

    access_grants: list[AccessGrantModel] = Field(default_factory=list)

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


####################
# Forms
####################


class NoteForm(BaseModel):
    title: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: Optional[list[dict]] = None


class NoteUpdateForm(BaseModel):
    title: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None
    archived_at: Optional[int] = None
    last_opened_at: Optional[int] = None
    access_grants: Optional[list[dict]] = None


class NoteUserResponse(NoteModel):
    user: Optional[UserResponse] = None


class NoteItemResponse(BaseModel):
    id: str
    title: str
    data: Optional[dict]
    updated_at: int
    created_at: int
    user: Optional[UserResponse] = None


class NoteListResponse(BaseModel):
    items: list[NoteUserResponse]
    total: int


class NoteTable:
    @staticmethod
    def _normalize_filter_timestamp(value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        return int(value * 1_000_000_000) if value < 10_000_000_000 else int(value)

    def _apply_note_filters(self, query, user_id: str, filter: dict, db, permission: str):
        query_key = filter.get("query")
        if query_key:
            normalized_query = query_key.replace("-", "").replace(" ", "")
            query = query.filter(
                or_(
                    func.replace(func.replace(Note.title, "-", ""), " ", "").ilike(
                        f"%{normalized_query}%"
                    ),
                    func.replace(
                        func.replace(cast(Note.data["content"]["md"], Text), "-", ""),
                        " ",
                        "",
                    ).ilike(f"%{normalized_query}%"),
                )
            )

        view_option = filter.get("view_option")
        if view_option == "created":
            query = query.filter(Note.user_id == user_id)
        elif view_option == "shared":
            query = query.filter(Note.user_id != user_id)

        archived = filter.get("archived")
        if archived is not None:
            query = query.filter(Note.is_archived == archived)

        pinned = filter.get("pinned")
        if pinned is not None:
            query = query.filter(Note.is_pinned == pinned)

        date_from = self._normalize_filter_timestamp(filter.get("date_from"))
        if date_from is not None:
            query = query.filter(Note.updated_at >= date_from)

        date_to = self._normalize_filter_timestamp(filter.get("date_to"))
        if date_to is not None:
            query = query.filter(Note.updated_at <= date_to)

        query = self._has_permission(
            db,
            query,
            filter,
            permission=permission,
        )

        return query

    @staticmethod
    def _get_order_columns(
        order_by: Optional[str], direction: Optional[str], recent: bool = False
    ):
        is_asc = direction == "asc"
        if order_by == "name":
            primary_sort = Note.title.asc() if is_asc else Note.title.desc()
            return [primary_sort, Note.updated_at.desc(), Note.id.asc()]
        if order_by == "created_at":
            primary_sort = Note.created_at.asc() if is_asc else Note.created_at.desc()
            return [primary_sort, Note.updated_at.desc(), Note.id.asc()]
        if order_by == "updated_at":
            primary_sort = Note.updated_at.asc() if is_asc else Note.updated_at.desc()
            return [primary_sort, Note.created_at.desc(), Note.id.asc()]
        if order_by == "last_opened_at" or recent:
            primary_sort = (
                Note.last_opened_at.asc() if is_asc else Note.last_opened_at.desc()
            )
            return [primary_sort, Note.updated_at.desc(), Note.created_at.desc()]

        return [Note.is_pinned.desc(), Note.updated_at.desc(), Note.created_at.desc()]

    def _get_access_grants(
        self, note_id: str, db: Optional[Session] = None
    ) -> list[AccessGrantModel]:
        return AccessGrants.get_grants_by_resource("note", note_id, db=db)

    def _to_note_model(
        self,
        note: Note,
        access_grants: Optional[list[AccessGrantModel]] = None,
        db: Optional[Session] = None,
    ) -> NoteModel:
        note_data = NoteModel.model_validate(note).model_dump(exclude={"access_grants"})
        note_data["access_grants"] = (
            access_grants
            if access_grants is not None
            else self._get_access_grants(note_data["id"], db=db)
        )
        return NoteModel.model_validate(note_data)

    def _has_permission(self, db, query, filter: dict, permission: str = "read"):
        return AccessGrants.has_permission_filter(
            db=db,
            query=query,
            DocumentModel=Note,
            filter=filter,
            resource_type="note",
            permission=permission,
        )

    def insert_new_note(
        self, user_id: str, form_data: NoteForm, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        with get_db_context(db) as db:
            note = NoteModel(
                **{
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    **form_data.model_dump(exclude={"access_grants"}),
                    "is_pinned": False,
                    "is_archived": False,
                    "archived_at": None,
                    "last_opened_at": None,
                    "created_at": int(time.time_ns()),
                    "updated_at": int(time.time_ns()),
                    "access_grants": [],
                }
            )

            new_note = Note(**note.model_dump(exclude={"access_grants"}))

            db.add(new_note)
            db.commit()
            AccessGrants.set_access_grants(
                "note", note.id, form_data.access_grants, db=db
            )
            return self._to_note_model(new_note, db=db)

    def get_notes(
        self, skip: int = 0, limit: int = 50, db: Optional[Session] = None
    ) -> list[NoteModel]:
        with get_db_context(db) as db:
            query = db.query(Note).order_by(
                Note.is_pinned.desc(), Note.updated_at.desc(), Note.created_at.desc()
            )
            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)
            notes = query.all()
            note_ids = [note.id for note in notes]
            grants_map = AccessGrants.get_grants_by_resources("note", note_ids, db=db)
            return [
                self._to_note_model(
                    note, access_grants=grants_map.get(note.id, []), db=db
                )
                for note in notes
            ]

    def search_notes(
        self,
        user_id: str,
        filter: dict = {},
        skip: int = 0,
        limit: int = 30,
        db: Optional[Session] = None,
    ) -> NoteListResponse:
        with get_db_context(db) as db:
            query = db.query(Note, User).outerjoin(User, User.id == Note.user_id)
            if filter:
                permission = filter.get("permission", "write")
                query = self._apply_note_filters(query, user_id, filter, db, permission)
                query = query.order_by(
                    *self._get_order_columns(
                        filter.get("order_by"),
                        filter.get("direction"),
                        recent=bool(filter.get("recent")),
                    )
                )

            else:
                query = query.order_by(*self._get_order_columns(None, None))

            # Count BEFORE pagination
            total = query.count()

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            items = query.all()

            note_ids = [note.id for note, _ in items]
            grants_map = AccessGrants.get_grants_by_resources("note", note_ids, db=db)

            notes = []
            for note, user in items:
                notes.append(
                    NoteUserResponse(
                        **self._to_note_model(
                            note,
                            access_grants=grants_map.get(note.id, []),
                            db=db,
                        ).model_dump(),
                        user=(
                            UserResponse(**UserModel.model_validate(user).model_dump())
                            if user
                            else None
                        ),
                    )
                )

            return NoteListResponse(items=notes, total=total)

    def get_notes_by_user_id(
        self,
        user_id: str,
        permission: str = "read",
        filter: Optional[dict] = None,
        skip: int = 0,
        limit: int = 50,
        db: Optional[Session] = None,
    ) -> list[NoteModel]:
        with get_db_context(db) as db:
            user_group_ids = [
                group.id for group in Groups.get_groups_by_member_id(user_id, db=db)
            ]

            filter = filter or {}
            filter = {
                **filter,
                "user_id": user_id,
                "group_ids": user_group_ids,
            }
            query = db.query(Note)
            query = self._apply_note_filters(
                query, user_id, filter, db, permission
            )
            query = query.order_by(
                *self._get_order_columns(
                    filter.get("order_by"),
                    filter.get("direction"),
                    recent=bool(filter.get("recent")),
                )
            )

            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)

            notes = query.all()
            note_ids = [note.id for note in notes]
            grants_map = AccessGrants.get_grants_by_resources("note", note_ids, db=db)
            return [
                self._to_note_model(
                    note, access_grants=grants_map.get(note.id, []), db=db
                )
                for note in notes
            ]

    def get_note_by_id(
        self, id: str, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        with get_db_context(db) as db:
            note = db.query(Note).filter(Note.id == id).first()
            return self._to_note_model(note, db=db) if note else None

    def update_note_by_id(
        self, id: str, form_data: NoteUpdateForm, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        with get_db_context(db) as db:
            note = db.query(Note).filter(Note.id == id).first()
            if not note:
                return None

            form_data = form_data.model_dump(exclude_unset=True)

            if "title" in form_data:
                note.title = form_data["title"]
            if "data" in form_data:
                note.data = {**(note.data or {}), **form_data["data"]}
            if "meta" in form_data:
                note.meta = {**(note.meta or {}), **form_data["meta"]}
            if "is_pinned" in form_data:
                note.is_pinned = form_data["is_pinned"]
            if "is_archived" in form_data:
                note.is_archived = form_data["is_archived"]
                note.archived_at = int(time.time_ns()) if note.is_archived else None
            if "archived_at" in form_data:
                note.archived_at = form_data["archived_at"]
            if "last_opened_at" in form_data:
                note.last_opened_at = form_data["last_opened_at"]

            if "access_grants" in form_data:
                AccessGrants.set_access_grants(
                    "note", id, form_data["access_grants"], db=db
                )

            note.updated_at = int(time.time_ns())

            db.commit()
            return self._to_note_model(note, db=db) if note else None

    def rename_note_by_id(
        self, id: str, title: str, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        return self.update_note_by_id(id, NoteUpdateForm(title=title), db=db)

    def set_note_pinned_by_id(
        self, id: str, is_pinned: bool, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        return self.update_note_by_id(
            id, NoteUpdateForm(is_pinned=is_pinned), db=db
        )

    def toggle_note_pinned_by_id(
        self, id: str, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        note = self.get_note_by_id(id, db=db)
        if not note:
            return None
        return self.set_note_pinned_by_id(id, not note.is_pinned, db=db)

    def set_note_archived_by_id(
        self, id: str, is_archived: bool, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        return self.update_note_by_id(
            id,
            NoteUpdateForm(
                is_archived=is_archived,
                archived_at=int(time.time_ns()) if is_archived else None,
            ),
            db=db,
        )

    def toggle_note_archived_by_id(
        self, id: str, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        note = self.get_note_by_id(id, db=db)
        if not note:
            return None
        return self.set_note_archived_by_id(id, not note.is_archived, db=db)

    def touch_last_opened_at_by_id(
        self, id: str, db: Optional[Session] = None
    ) -> Optional[NoteModel]:
        with get_db_context(db) as db:
            note = db.query(Note).filter(Note.id == id).first()
            if not note:
                return None

            note.last_opened_at = int(time.time_ns())
            db.commit()
            return self._to_note_model(note, db=db)

    def delete_note_by_id(self, id: str, db: Optional[Session] = None) -> bool:
        try:
            with get_db_context(db) as db:
                AccessGrants.revoke_all_access("note", id, db=db)
                db.query(Note).filter(Note.id == id).delete()
                db.commit()
                return True
        except Exception:
            return False


Notes = NoteTable()
