import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context


class CollectionReport(Base):
    __tablename__ = "collection_report"

    id = Column(Text, primary_key=True, unique=True)
    collection_id = Column(
        Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id = Column(Text, nullable=False)
    note_id = Column(Text, ForeignKey("note.id", ondelete="SET NULL"), nullable=True)

    status = Column(Text, nullable=False)
    model_name = Column(Text, nullable=True)
    chunk_config = Column(JSON, nullable=True)

    documents_total = Column(Integer, nullable=False, default=0)
    documents_processed = Column(Integer, nullable=False, default=0)
    documents_failed = Column(Integer, nullable=False, default=0)

    current_step = Column(Text, nullable=True)
    current_document_id = Column(Text, nullable=True)
    current_document_title = Column(Text, nullable=True)

    included_document_ids = Column(JSON, nullable=True)
    failed_documents = Column(JSON, nullable=True)
    final_report = Column(JSON, nullable=True)
    warnings = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    completed_at = Column(BigInteger, nullable=True)


class CollectionReportModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    collection_id: str
    user_id: str
    note_id: Optional[str] = None
    status: str
    model_name: Optional[str] = None
    chunk_config: dict = Field(default_factory=dict)
    documents_total: int = 0
    documents_processed: int = 0
    documents_failed: int = 0
    current_step: Optional[str] = None
    current_document_id: Optional[str] = None
    current_document_title: Optional[str] = None
    included_document_ids: list[str] = Field(default_factory=list)
    failed_documents: list[dict] = Field(default_factory=list)
    final_report: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: int
    updated_at: int
    started_at: Optional[int] = None
    completed_at: Optional[int] = None


class DocumentSummary(Base):
    __tablename__ = "document_summary"

    id = Column(Text, primary_key=True, unique=True)
    collection_report_id = Column(
        Text, ForeignKey("collection_report.id", ondelete="CASCADE"), nullable=False
    )
    document_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    source_type = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    document_updated_at = Column(BigInteger, nullable=True)
    text_available = Column(Boolean, nullable=False, default=False)

    status = Column(Text, nullable=False)
    chunks = Column(JSON, nullable=True)
    chunk_summaries = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)
    warnings = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "collection_report_id",
            "document_id",
            name="uq_document_summary_report_document",
        ),
    )


class DocumentSummaryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    collection_report_id: str
    document_id: str
    title: str
    source_type: Optional[str] = None
    page_count: Optional[int] = None
    document_updated_at: Optional[int] = None
    text_available: bool = False
    status: str
    chunks: list[dict] = Field(default_factory=list)
    chunk_summaries: list[dict] = Field(default_factory=list)
    summary: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: int
    updated_at: int


class CollectionReportTable:
    def _to_collection_report_model(self, report: CollectionReport) -> CollectionReportModel:
        return CollectionReportModel.model_validate(report)

    def _to_document_summary_model(self, summary: DocumentSummary) -> DocumentSummaryModel:
        return DocumentSummaryModel.model_validate(summary)

    def get_report_by_id(
        self, report_id: str, db: Optional[Session] = None
    ) -> Optional[CollectionReportModel]:
        with get_db_context(db) as db:
            report = db.query(CollectionReport).filter_by(id=report_id).first()
            return self._to_collection_report_model(report) if report else None

    def get_report_by_collection_id(
        self, collection_id: str, db: Optional[Session] = None
    ) -> Optional[CollectionReportModel]:
        with get_db_context(db) as db:
            report = (
                db.query(CollectionReport)
                .filter_by(collection_id=collection_id)
                .first()
            )
            return self._to_collection_report_model(report) if report else None

    def upsert_report_for_collection(
        self,
        collection_id: str,
        user_id: str,
        payload: Optional[dict] = None,
        db: Optional[Session] = None,
    ) -> CollectionReportModel:
        payload = payload or {}
        with get_db_context(db) as db:
            report = (
                db.query(CollectionReport)
                .filter_by(collection_id=collection_id)
                .first()
            )
            now = int(time.time())

            if report is None:
                report = CollectionReport(
                    id=str(uuid.uuid4()),
                    collection_id=collection_id,
                    user_id=user_id,
                    note_id=payload.get("note_id"),
                    status=payload.get("status", "idle"),
                    model_name=payload.get("model_name"),
                    chunk_config=payload.get("chunk_config") or {},
                    documents_total=payload.get("documents_total", 0),
                    documents_processed=payload.get("documents_processed", 0),
                    documents_failed=payload.get("documents_failed", 0),
                    current_step=payload.get("current_step"),
                    current_document_id=payload.get("current_document_id"),
                    current_document_title=payload.get("current_document_title"),
                    included_document_ids=payload.get("included_document_ids") or [],
                    failed_documents=payload.get("failed_documents") or [],
                    final_report=payload.get("final_report"),
                    warnings=payload.get("warnings") or [],
                    error=payload.get("error"),
                    created_at=now,
                    updated_at=now,
                    started_at=payload.get("started_at"),
                    completed_at=payload.get("completed_at"),
                )
                db.add(report)
            else:
                for key, value in payload.items():
                    setattr(report, key, value)
                report.updated_at = now

            db.commit()
            db.refresh(report)
            return self._to_collection_report_model(report)

    def update_report_by_id(
        self,
        report_id: str,
        payload: dict,
        db: Optional[Session] = None,
    ) -> Optional[CollectionReportModel]:
        with get_db_context(db) as db:
            report = db.query(CollectionReport).filter_by(id=report_id).first()
            if report is None:
                return None

            for key, value in payload.items():
                setattr(report, key, value)
            report.updated_at = int(time.time())

            db.commit()
            db.refresh(report)
            return self._to_collection_report_model(report)

    def list_document_summaries(
        self,
        report_id: str,
        db: Optional[Session] = None,
    ) -> list[DocumentSummaryModel]:
        with get_db_context(db) as db:
            rows = (
                db.query(DocumentSummary)
                .filter_by(collection_report_id=report_id)
                .order_by(DocumentSummary.updated_at.desc(), DocumentSummary.title.asc())
                .all()
            )
            return [self._to_document_summary_model(row) for row in rows]

    def get_document_summary(
        self,
        report_id: str,
        document_id: str,
        db: Optional[Session] = None,
    ) -> Optional[DocumentSummaryModel]:
        with get_db_context(db) as db:
            row = (
                db.query(DocumentSummary)
                .filter_by(collection_report_id=report_id, document_id=document_id)
                .first()
            )
            return self._to_document_summary_model(row) if row else None

    def upsert_document_summary(
        self,
        report_id: str,
        document_id: str,
        title: str,
        payload: Optional[dict] = None,
        db: Optional[Session] = None,
    ) -> DocumentSummaryModel:
        payload = payload or {}
        with get_db_context(db) as db:
            row = (
                db.query(DocumentSummary)
                .filter_by(collection_report_id=report_id, document_id=document_id)
                .first()
            )
            now = int(time.time())

            if row is None:
                row = DocumentSummary(
                    id=str(uuid.uuid4()),
                    collection_report_id=report_id,
                    document_id=document_id,
                    title=title,
                    source_type=payload.get("source_type"),
                    page_count=payload.get("page_count"),
                    document_updated_at=payload.get("document_updated_at"),
                    text_available=payload.get("text_available", False),
                    status=payload.get("status", "pending"),
                    chunks=payload.get("chunks") or [],
                    chunk_summaries=payload.get("chunk_summaries") or [],
                    summary=payload.get("summary"),
                    warnings=payload.get("warnings") or [],
                    error=payload.get("error"),
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.title = title
                for key, value in payload.items():
                    setattr(row, key, value)
                row.updated_at = now

            db.commit()
            db.refresh(row)
            return self._to_document_summary_model(row)

    def delete_document_summaries(
        self, report_id: str, db: Optional[Session] = None
    ) -> None:
        with get_db_context(db) as db:
            db.query(DocumentSummary).filter_by(collection_report_id=report_id).delete()
            db.commit()

    def delete_document_summaries_not_in(
        self,
        report_id: str,
        document_ids: list[str],
        db: Optional[Session] = None,
    ) -> None:
        with get_db_context(db) as db:
            query = db.query(DocumentSummary).filter_by(collection_report_id=report_id)
            if document_ids:
                query = query.filter(DocumentSummary.document_id.notin_(document_ids))
            query.delete(synchronize_session=False)
            db.commit()


CollectionReports = CollectionReportTable()
