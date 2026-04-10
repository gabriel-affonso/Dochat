import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context
from open_webui.utils.misc import sanitize_metadata

log = logging.getLogger(__name__)


class FileRevision(Base):
    __tablename__ = "file_revision"

    id = Column(Text, primary_key=True, unique=True)
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    revision = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    action = Column(Text, nullable=True)
    created_by = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("file_id", "revision", name="uq_file_revision_file_revision"),
    )


class FileRevisionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_id: str
    revision: int
    content: str
    action: Optional[str] = None
    created_by: Optional[dict] = None
    created_at: int
    updated_at: int


class FileRevisionsTable:
    def get_revision_by_file_id_and_revision(
        self, file_id: str, revision: int, db: Optional[Session] = None
    ) -> Optional[FileRevisionModel]:
        with get_db_context(db) as db:
            try:
                row = (
                    db.query(FileRevision)
                    .filter_by(file_id=file_id, revision=revision)
                    .first()
                )
                return FileRevisionModel.model_validate(row) if row else None
            except Exception:
                return None

    def get_revisions_by_file_id(
        self, file_id: str, db: Optional[Session] = None
    ) -> list[FileRevisionModel]:
        with get_db_context(db) as db:
            try:
                rows = (
                    db.query(FileRevision)
                    .filter_by(file_id=file_id)
                    .order_by(FileRevision.revision.desc(), FileRevision.created_at.desc())
                    .all()
                )
                return [FileRevisionModel.model_validate(row) for row in rows]
            except Exception:
                return []

    def upsert_revision(
        self,
        file_id: str,
        revision: int,
        content: str,
        *,
        action: Optional[str] = None,
        created_by: Optional[dict] = None,
        created_at: Optional[int] = None,
        db: Optional[Session] = None,
    ) -> Optional[FileRevisionModel]:
        now = int(created_at or time.time())

        with get_db_context(db) as db:
            try:
                row = (
                    db.query(FileRevision)
                    .filter_by(file_id=file_id, revision=revision)
                    .first()
                )
                sanitized_actor = sanitize_metadata(created_by) if created_by else None

                if row:
                    row.content = content or ""
                    row.action = action or row.action
                    row.created_by = sanitized_actor if sanitized_actor else row.created_by
                    row.created_at = row.created_at or now
                    row.updated_at = int(time.time())
                    db.commit()
                    db.refresh(row)
                    return FileRevisionModel.model_validate(row)

                row = FileRevision(
                    id=str(uuid.uuid4()),
                    file_id=file_id,
                    revision=max(int(revision or 1), 1),
                    content=content or "",
                    action=action,
                    created_by=sanitized_actor,
                    created_at=now,
                    updated_at=int(time.time()),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                return FileRevisionModel.model_validate(row)
            except Exception as exc:
                db.rollback()
                log.exception("Error upserting file revision: %s", exc)
                return None


FileRevisions = FileRevisionsTable()
