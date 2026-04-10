import time
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
import logging
import io
import zipfile

from sqlalchemy.orm import Session
from open_webui.internal.db import get_db_context, get_session
from open_webui.models.groups import Groups
from open_webui.models.collection_reports import CollectionReports
from open_webui.models.knowledge import (
    Knowledge,
    KnowledgeFileListResponse,
    Knowledges,
    KnowledgeForm,
    KnowledgeResponse,
    KnowledgeUserResponse,
)
from open_webui.models.files import Files, FileModel, FileMetadataResponse
from open_webui.models.notes import NoteModel, Notes
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import (
    process_file,
    ProcessFileForm,
    process_files_batch,
    BatchProcessFilesForm,
)
from open_webui.storage.provider import Storage

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_verified_user, get_admin_user
from open_webui.utils.access_control import filter_allowed_access_grants
from open_webui.models.access_grants import AccessGrants


from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.models.models import Models, ModelForm
from open_webui.utils.dochat_collections import (
    get_linked_note_ids,
    get_linked_notes_for_knowledge,
    remove_linked_note_vector,
    set_linked_note_ids,
    upsert_linked_note_vector,
)
from open_webui.utils.collection_synthesis import (
    SYNTHESIS_PROCESSING_STATUS,
    SYNTHESIS_QUEUED_STATUS,
    get_default_chunk_config,
    get_report_payload,
    process_collection_synthesis_job,
)

log = logging.getLogger(__name__)

router = APIRouter()

############################
# getKnowledgeBases
############################

PAGE_ITEM_COUNT = 30

############################
# Knowledge Base Embedding
############################

KNOWLEDGE_BASES_COLLECTION = "knowledge-bases"


async def embed_knowledge_base_metadata(
    request: Request,
    knowledge_base_id: str,
    name: str,
    description: str,
) -> bool:
    """Generate and store embedding for knowledge base."""
    try:
        content = f"{name}\n\n{description}" if description else name
        embedding = await request.app.state.EMBEDDING_FUNCTION(content)
        VECTOR_DB_CLIENT.upsert(
            collection_name=KNOWLEDGE_BASES_COLLECTION,
            items=[
                {
                    "id": knowledge_base_id,
                    "text": content,
                    "vector": embedding,
                    "metadata": {
                        "knowledge_base_id": knowledge_base_id,
                    },
                }
            ],
        )
        return True
    except Exception as e:
        log.error(f"Failed to embed knowledge base {knowledge_base_id}: {e}")
        return False


def remove_knowledge_base_metadata_embedding(knowledge_base_id: str) -> bool:
    """Remove knowledge base embedding."""
    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=KNOWLEDGE_BASES_COLLECTION,
            ids=[knowledge_base_id],
        )
        return True
    except Exception as e:
        log.debug(f"Failed to remove embedding for {knowledge_base_id}: {e}")
        return False


class KnowledgeAccessResponse(KnowledgeUserResponse):
    write_access: Optional[bool] = False
    file_count: int = 0
    folder_count: int = 0
    folders: list[str] = []


class KnowledgeAccessListResponse(BaseModel):
    items: list[KnowledgeAccessResponse]
    total: int


class KnowledgeLinkedNoteResponse(BaseModel):
    id: str
    title: str
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    last_opened_at: Optional[int] = None
    is_pinned: bool = False
    is_archived: bool = False
    meta: dict = {}


class KnowledgeLinkedNotesResponse(BaseModel):
    items: list[KnowledgeLinkedNoteResponse]
    total: int


class KnowledgeNoteIdForm(BaseModel):
    note_id: str


class KnowledgeDirectorySyncFileForm(BaseModel):
    file_id: str
    relative_path: str


class CollectionSynthesisStartForm(BaseModel):
    model: Optional[str] = None


def _can_read_knowledge(knowledge, user, db: Session) -> bool:
    return bool(
        user.role == "admin"
        or knowledge.user_id == user.id
        or AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="read",
            db=db,
        )
    )


def _can_write_knowledge(knowledge, user, db: Session) -> bool:
    return bool(
        knowledge.user_id == user.id
        or (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
        or user.role == "admin"
        or AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
    )


def _update_knowledge_meta(knowledge_id: str, meta: dict, db: Session):
    db.query(Knowledge).filter_by(id=knowledge_id).update(
        {
            "meta": meta,
            "updated_at": int(time.time()),
        }
    )
    db.commit()
    return Knowledges.get_knowledge_by_id(id=knowledge_id, db=db)


def _normalize_folder_path(path: Optional[str]) -> Optional[str]:
    if not isinstance(path, str):
        return None

    normalized = "/".join(part.strip() for part in path.split("/") if part.strip())
    return normalized or None


def _expand_folder_ancestors(path: Optional[str]) -> list[str]:
    normalized = _normalize_folder_path(path)
    if not normalized:
        return []

    segments = normalized.split("/")
    return ["/".join(segments[: index + 1]) for index in range(len(segments))]


def _get_registered_folder_paths(knowledge_meta: Optional[dict]) -> list[str]:
    if not isinstance(knowledge_meta, dict):
        return []

    registered_paths = []
    for value in knowledge_meta.get("document_folders") or []:
        if isinstance(value, dict):
            path = value.get("path")
        else:
            path = value

        registered_paths.extend(_expand_folder_ancestors(path))

    return sorted(set(registered_paths))


def _extract_folder_label(file_meta: Optional[dict]) -> Optional[str]:
    if not isinstance(file_meta, dict):
        return None

    folder_path = _normalize_folder_path(file_meta.get("folder_path"))
    if folder_path:
        return folder_path

    relative_path = file_meta.get("relative_path")
    if isinstance(relative_path, str) and "/" in relative_path.strip("/"):
        return _normalize_folder_path(relative_path.strip("/").rsplit("/", 1)[0])

    return None


def _build_knowledge_file_summary(knowledge_id: str, db: Session) -> tuple[int, list[str]]:
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id, db=db)
    files = Knowledges.get_file_metadatas_by_id(knowledge_id, db=db)
    folders = sorted(
        set(_get_registered_folder_paths(knowledge.meta if knowledge else None)).union(
            {
                folder
                for folder in (_extract_folder_label(file.meta) for file in files)
                if folder
            }
        )
    )
    return len(files), folders


def _queue_collection_file_processing(
    request: Request,
    knowledge_id: str,
    file_id: str,
    user,
):
    try:
        with get_db_context() as background_db:
            Files.update_file_data_by_id(
                file_id,
                {
                    "status": "pending",
                    "processing_status": "processing",
                    "embedding_status": "processing",
                    "error": None,
                    "last_processed_at": int(time.time()),
                },
                db=background_db,
            )

            process_file(
                request,
                ProcessFileForm(file_id=file_id, collection_name=knowledge_id),
                user=user,
                db=background_db,
            )
    except Exception:
        log.exception(
            "Queued collection processing failed for knowledge_id=%s file_id=%s",
            knowledge_id,
            file_id,
        )


def _build_knowledge_response(knowledge, user, db: Session) -> "KnowledgeFilesResponse":
    files = Knowledges.get_file_metadatas_by_id(knowledge.id, db=db)
    folders = sorted(
        set(_get_registered_folder_paths(knowledge.meta)).union(
            {
                folder
                for folder in (_extract_folder_label(file.meta) for file in files)
                if folder
            }
        )
    )

    return KnowledgeFilesResponse(
        **knowledge.model_dump(),
        files=files,
        linked_notes=[
            KnowledgeLinkedNoteResponse(**item)
            for item in get_linked_notes_for_knowledge(knowledge, user, db=db)
        ],
        write_access=_can_write_knowledge(knowledge, user, db),
        file_count=len(files),
        folder_count=len(folders),
        folders=folders,
    )


@router.get("/", response_model=KnowledgeAccessListResponse)
async def get_knowledge_bases(
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    page = max(page, 1)
    limit = PAGE_ITEM_COUNT
    skip = (page - 1) * limit

    filter = {}
    groups = Groups.get_groups_by_member_id(user.id, db=db)
    user_group_ids = {group.id for group in groups}

    if not user.role == "admin" or not BYPASS_ADMIN_ACCESS_CONTROL:
        if groups:
            filter["group_ids"] = [group.id for group in groups]

        filter["user_id"] = user.id

    result = Knowledges.search_knowledge_bases(
        user.id, filter=filter, skip=skip, limit=limit, db=db
    )

    # Batch-fetch writable knowledge IDs in a single query instead of N has_access calls
    knowledge_base_ids = [knowledge_base.id for knowledge_base in result.items]
    writable_knowledge_base_ids = AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type="knowledge",
        resource_ids=knowledge_base_ids,
        permission="write",
        user_group_ids=user_group_ids,
        db=db,
    )

    knowledge_summaries = {
        knowledge_base.id: _build_knowledge_file_summary(knowledge_base.id, db)
        for knowledge_base in result.items
    }

    return KnowledgeAccessListResponse(
        items=[
            KnowledgeAccessResponse(
                **knowledge_base.model_dump(),
                write_access=(
                    user.id == knowledge_base.user_id
                    or (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
                    or knowledge_base.id in writable_knowledge_base_ids
                ),
                file_count=knowledge_summaries.get(knowledge_base.id, (0, []))[0],
                folder_count=len(knowledge_summaries.get(knowledge_base.id, (0, []))[1]),
                folders=knowledge_summaries.get(knowledge_base.id, (0, []))[1],
            )
            for knowledge_base in result.items
        ],
        total=result.total,
    )


@router.get("/search", response_model=KnowledgeAccessListResponse)
async def search_knowledge_bases(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    page = max(page, 1)
    limit = PAGE_ITEM_COUNT
    skip = (page - 1) * limit

    filter = {}
    if query:
        filter["query"] = query
    if view_option:
        filter["view_option"] = view_option

    groups = Groups.get_groups_by_member_id(user.id, db=db)
    user_group_ids = {group.id for group in groups}

    if not user.role == "admin" or not BYPASS_ADMIN_ACCESS_CONTROL:
        if groups:
            filter["group_ids"] = [group.id for group in groups]

        filter["user_id"] = user.id

    result = Knowledges.search_knowledge_bases(
        user.id, filter=filter, skip=skip, limit=limit, db=db
    )

    # Batch-fetch writable knowledge IDs in a single query instead of N has_access calls
    knowledge_base_ids = [knowledge_base.id for knowledge_base in result.items]
    writable_knowledge_base_ids = AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type="knowledge",
        resource_ids=knowledge_base_ids,
        permission="write",
        user_group_ids=user_group_ids,
        db=db,
    )

    knowledge_summaries = {
        knowledge_base.id: _build_knowledge_file_summary(knowledge_base.id, db)
        for knowledge_base in result.items
    }

    return KnowledgeAccessListResponse(
        items=[
            KnowledgeAccessResponse(
                **knowledge_base.model_dump(),
                write_access=(
                    user.id == knowledge_base.user_id
                    or (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
                    or knowledge_base.id in writable_knowledge_base_ids
                ),
                file_count=knowledge_summaries.get(knowledge_base.id, (0, []))[0],
                folder_count=len(knowledge_summaries.get(knowledge_base.id, (0, []))[1]),
                folders=knowledge_summaries.get(knowledge_base.id, (0, []))[1],
            )
            for knowledge_base in result.items
        ],
        total=result.total,
    )


@router.get("/search/files", response_model=KnowledgeFileListResponse)
async def search_knowledge_files(
    query: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    page = max(page, 1)
    limit = PAGE_ITEM_COUNT
    skip = (page - 1) * limit

    filter = {}
    if query:
        filter["query"] = query

    groups = Groups.get_groups_by_member_id(user.id, db=db)
    if groups:
        filter["group_ids"] = [group.id for group in groups]

    filter["user_id"] = user.id

    return Knowledges.search_knowledge_files(
        filter=filter, skip=skip, limit=limit, db=db
    )


############################
# CreateNewKnowledge
############################


@router.post("/create", response_model=Optional[KnowledgeResponse])
async def create_new_knowledge(
    request: Request,
    form_data: KnowledgeForm,
    user=Depends(get_admin_user),
):
    # NOTE: We intentionally do NOT use Depends(get_session) here.
    # Database operations (filter_allowed_access_grants, insert_new_knowledge) manage their own sessions.
    # This prevents holding a connection during embed_knowledge_base_metadata()
    # which makes external embedding API calls (1-5+ seconds).
    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_knowledge",
    )

    knowledge = Knowledges.insert_new_knowledge(user.id, form_data)

    if knowledge:
        # Embed knowledge base for semantic search
        await embed_knowledge_base_metadata(
            request,
            knowledge.id,
            knowledge.name,
            knowledge.description,
        )
        return knowledge
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_EXISTS,
        )


############################
# ReindexKnowledgeFiles
############################


@router.post("/reindex", response_model=bool)
async def reindex_knowledge_files(
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    knowledge_bases = Knowledges.get_knowledge_bases(db=db)

    log.info(f"Starting reindexing for {len(knowledge_bases)} knowledge bases")

    for knowledge_base in knowledge_bases:
        try:
            files = Knowledges.get_files_by_id(knowledge_base.id, db=db)
            try:
                if VECTOR_DB_CLIENT.has_collection(collection_name=knowledge_base.id):
                    VECTOR_DB_CLIENT.delete_collection(
                        collection_name=knowledge_base.id
                    )
            except Exception as e:
                log.error(f"Error deleting collection {knowledge_base.id}: {str(e)}")
                continue  # Skip, don't raise

            failed_files = []
            for file in files:
                try:
                    await run_in_threadpool(
                        process_file,
                        request,
                        ProcessFileForm(
                            file_id=file.id, collection_name=knowledge_base.id
                        ),
                        user=user,
                        db=db,
                    )
                except Exception as e:
                    log.error(
                        f"Error processing file {file.filename} (ID: {file.id}): {str(e)}"
                    )
                    failed_files.append({"file_id": file.id, "error": str(e)})
                    continue

        except Exception as e:
            log.error(f"Error processing knowledge base {knowledge_base.id}: {str(e)}")
            # Don't raise, just continue
            continue

        if failed_files:
            log.warning(
                f"Failed to process {len(failed_files)} files in knowledge base {knowledge_base.id}"
            )
            for failed in failed_files:
                log.warning(f"File ID: {failed['file_id']}, Error: {failed['error']}")

    log.info(f"Reindexing completed.")
    return True


############################
# ReindexKnowledgeBases
############################


@router.post("/metadata/reindex", response_model=dict)
async def reindex_knowledge_base_metadata_embeddings(
    request: Request,
    user=Depends(get_admin_user),
):
    """Batch embed all existing knowledge bases. Admin only.

    NOTE: We intentionally do NOT use Depends(get_session) here.
    This endpoint loops through ALL knowledge bases and calls embed_knowledge_base_metadata()
    for each one, making N external embedding API calls. Holding a session during
    this entire operation would exhaust the connection pool.
    """
    knowledge_bases = Knowledges.get_knowledge_bases()
    log.info(f"Reindexing embeddings for {len(knowledge_bases)} knowledge bases")

    success_count = 0
    for kb in knowledge_bases:
        if await embed_knowledge_base_metadata(request, kb.id, kb.name, kb.description):
            success_count += 1

    log.info(f"Embedding reindex complete: {success_count}/{len(knowledge_bases)}")
    return {"total": len(knowledge_bases), "success": success_count}


############################
# GetKnowledgeById
############################


class KnowledgeFilesResponse(KnowledgeResponse):
    files: Optional[list[FileMetadataResponse]] = None
    linked_notes: Optional[list[KnowledgeLinkedNoteResponse]] = None
    write_access: Optional[bool] = False
    file_count: int = 0
    folder_count: int = 0
    folders: list[str] = []


@router.get("/{id}", response_model=Optional[KnowledgeFilesResponse])
async def get_knowledge_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)

    if knowledge:
        if _can_read_knowledge(knowledge, user, db):
            return _build_knowledge_response(knowledge, user, db)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateKnowledgeById
############################


@router.post("/{id}/update", response_model=Optional[KnowledgeFilesResponse])
async def update_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeForm,
    user=Depends(get_verified_user),
):
    # NOTE: We intentionally do NOT use Depends(get_session) here.
    # Database operations manage their own short-lived sessions internally.
    # This prevents holding a connection during embed_knowledge_base_metadata()
    # which makes external embedding API calls (1-5+ seconds).
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    # Is the user the original creator, in a group with write access, or an admin
    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_knowledge",
    )

    knowledge = Knowledges.update_knowledge_by_id(id=id, form_data=form_data)
    if knowledge:
        # Re-embed knowledge base for semantic search
        await embed_knowledge_base_metadata(
            request,
            knowledge.id,
            knowledge.name,
            knowledge.description,
        )
        with get_db_context() as db:
            return _build_knowledge_response(knowledge, user, db)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# UpdateKnowledgeAccessById
############################


class KnowledgeAccessGrantsForm(BaseModel):
    access_grants: list[dict]


@router.post("/{id}/access/update", response_model=Optional[KnowledgeFilesResponse])
async def update_knowledge_access_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeAccessGrantsForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_knowledge",
    )

    AccessGrants.set_access_grants("knowledge", id, form_data.access_grants, db=db)

    return _build_knowledge_response(
        Knowledges.get_knowledge_by_id(id=id, db=db),
        user,
        db,
    )


############################
# GetKnowledgeFilesById
############################


@router.get("/{id}/files", response_model=KnowledgeFileListResponse)
async def get_knowledge_files_by_id(
    id: str,
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    limit: Optional[int] = Query(30, ge=1, le=500),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):

    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    page = max(page, 1)

    skip = (page - 1) * limit

    filter = {}
    if query:
        filter["query"] = query
    if view_option:
        filter["view_option"] = view_option
    if order_by:
        filter["order_by"] = order_by
    if direction:
        filter["direction"] = direction

    return Knowledges.search_files_by_id(
        id, user.id, filter=filter, skip=skip, limit=limit, db=db
    )


@router.get("/{id}/notes", response_model=KnowledgeLinkedNotesResponse)
async def get_knowledge_linked_notes(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_read_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    items = [
        KnowledgeLinkedNoteResponse(**item)
        for item in get_linked_notes_for_knowledge(knowledge, user, db=db)
    ]
    return KnowledgeLinkedNotesResponse(items=items, total=len(items))


@router.post("/{id}/notes/add", response_model=Optional[KnowledgeFilesResponse])
async def add_note_to_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeNoteIdForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_write_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    note = Notes.get_note_by_id(form_data.note_id, db=db)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not (
        user.role == "admin"
        or note.user_id == user.id
        or AccessGrants.has_access(
            user_id=user.id,
            resource_type="note",
            resource_id=note.id,
            permission="read",
            db=db,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    linked_note_ids = get_linked_note_ids(knowledge.meta or {})
    if form_data.note_id not in linked_note_ids:
        linked_note_ids.append(form_data.note_id)
        knowledge = _update_knowledge_meta(
            knowledge.id,
            set_linked_note_ids(knowledge.meta or {}, linked_note_ids),
            db,
        )

    upsert_linked_note_vector(request, knowledge, note, user=user)
    return _build_knowledge_response(knowledge, user, db)


@router.post("/{id}/notes/remove", response_model=Optional[KnowledgeFilesResponse])
async def remove_note_from_knowledge_by_id(
    id: str,
    form_data: KnowledgeNoteIdForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_write_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    linked_note_ids = [
        note_id
        for note_id in get_linked_note_ids(knowledge.meta or {})
        if note_id != form_data.note_id
    ]
    knowledge = _update_knowledge_meta(
        knowledge.id,
        set_linked_note_ids(knowledge.meta or {}, linked_note_ids),
        db,
    )
    remove_linked_note_vector(knowledge.id, form_data.note_id)

    return _build_knowledge_response(knowledge, user, db)


@router.post("/{id}/synthesis", response_model=dict)
async def start_collection_synthesis(
    request: Request,
    background_tasks: BackgroundTasks,
    id: str,
    form_data: CollectionSynthesisStartForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_write_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    existing_report = CollectionReports.get_report_by_collection_id(id, db=db)
    if existing_report and existing_report.status in {
        SYNTHESIS_QUEUED_STATUS,
        SYNTHESIS_PROCESSING_STATUS,
    }:
        return get_report_payload(id, db=db)

    report = CollectionReports.upsert_report_for_collection(
        id,
        user.id,
        {
            "status": SYNTHESIS_QUEUED_STATUS,
            "model_name": form_data.model,
            "chunk_config": existing_report.chunk_config
            if existing_report and existing_report.chunk_config
            else get_default_chunk_config(),
            "error": None,
            "current_step": None,
            "current_document_id": None,
            "current_document_title": None,
        },
        db=db,
    )

    background_tasks.add_task(
        process_collection_synthesis_job,
        request,
        id,
        report.id,
        user.id,
        requested_model_id=form_data.model,
        force_reprocess=False,
    )

    return get_report_payload(id, db=db)


@router.post("/{id}/synthesis/regenerate", response_model=dict)
async def regenerate_collection_synthesis(
    request: Request,
    background_tasks: BackgroundTasks,
    id: str,
    form_data: CollectionSynthesisStartForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_write_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    existing_report = CollectionReports.get_report_by_collection_id(id, db=db)
    if existing_report and existing_report.status in {
        SYNTHESIS_QUEUED_STATUS,
        SYNTHESIS_PROCESSING_STATUS,
    }:
        return get_report_payload(id, db=db)

    report = CollectionReports.upsert_report_for_collection(
        id,
        user.id,
        {
            "status": SYNTHESIS_QUEUED_STATUS,
            "model_name": form_data.model,
            "chunk_config": get_default_chunk_config(),
            "error": None,
            "current_step": None,
            "current_document_id": None,
            "current_document_title": None,
        },
        db=db,
    )

    background_tasks.add_task(
        process_collection_synthesis_job,
        request,
        id,
        report.id,
        user.id,
        requested_model_id=form_data.model,
        force_reprocess=True,
    )

    return get_report_payload(id, db=db)


@router.get("/{id}/synthesis/status", response_model=dict)
async def get_collection_synthesis_status(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_read_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return get_report_payload(id, db=db)


@router.get("/{id}/synthesis", response_model=dict)
async def get_collection_synthesis(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_read_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return get_report_payload(id, db=db)


@router.post("/{id}/directory-sync/upsert", response_model=Optional[KnowledgeFilesResponse])
async def upsert_directory_sync_file_to_knowledge(
    request: Request,
    background_tasks: BackgroundTasks,
    id: str,
    form_data: KnowledgeDirectorySyncFileForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not _can_write_knowledge(knowledge, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if file.data is None:
        Files.update_file_data_by_id(file.id, {}, db=db)
        file = Files.get_file_by_id(file.id, db=db)

    Files.update_file_metadata_by_id(
        file.id,
        {
            "knowledge_id": id,
            "data": {
                **(
                    file.meta.get("data")
                    if isinstance(file.meta, dict) and isinstance(file.meta.get("data"), dict)
                    else {}
                ),
                "knowledge_id": id,
            },
            "source_type": "directory_sync",
            "relative_path": form_data.relative_path,
        },
        db=db,
    )

    existing_files = Knowledges.get_files_by_id(id, db=db)
    for existing_file in existing_files:
        if existing_file.id == file.id:
            continue

        existing_meta = existing_file.meta or {}
        if (
            existing_meta.get("source_type") == "directory_sync"
            and existing_meta.get("relative_path") == form_data.relative_path
        ):
            Knowledges.remove_file_from_knowledge_by_id(id, existing_file.id, db=db)
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=knowledge.id, filter={"file_id": existing_file.id}
                )
                if existing_file.hash:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=knowledge.id,
                        filter={"hash": existing_file.hash},
                    )
            except Exception:
                log.debug("Directory sync vector cleanup skipped", exc_info=True)

            if user.role == "admin" or existing_file.user_id == user.id:
                Files.delete_file_by_id(existing_file.id, db=db)
            break

    if not Knowledges.has_file(id, form_data.file_id, db=db):
        Knowledges.add_file_to_knowledge_by_id(
            knowledge_id=id,
            file_id=form_data.file_id,
            user_id=user.id,
            db=db,
        )

    background_tasks.add_task(
        _queue_collection_file_processing,
        request,
        id,
        form_data.file_id,
        user,
    )

    return _build_knowledge_response(
        Knowledges.get_knowledge_by_id(id=id, db=db),
        user,
        db,
    )


############################
# AddFileToKnowledge
############################


class KnowledgeFileIdForm(BaseModel):
    file_id: str


@router.post("/{id}/file/add", response_model=Optional[KnowledgeFilesResponse])
def add_file_to_knowledge_by_id(
    request: Request,
    background_tasks: BackgroundTasks,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if file.data is None:
        Files.update_file_data_by_id(file.id, {}, db=db)
        file = Files.get_file_by_id(file.id, db=db)

    file_meta = file.meta or {}
    file_meta_payload = file_meta.get("data") if isinstance(file_meta.get("data"), dict) else {}
    Files.update_file_metadata_by_id(
        file.id,
        {
            "knowledge_id": id,
            "data": {
                **file_meta_payload,
                "knowledge_id": id,
            },
        },
        db=db,
    )

    if not Knowledges.has_file(id, form_data.file_id, db=db):
        Knowledges.add_file_to_knowledge_by_id(
            knowledge_id=id, file_id=form_data.file_id, user_id=user.id, db=db
        )

    background_tasks.add_task(
        _queue_collection_file_processing,
        request,
        id,
        form_data.file_id,
        user,
    )

    if knowledge:
        return _build_knowledge_response(
            Knowledges.get_knowledge_by_id(id=id, db=db),
            user,
            db,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/{id}/file/update", response_model=Optional[KnowledgeFilesResponse])
def update_file_from_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Validate the file actually belongs to this knowledge base
    if not Knowledges.has_file(knowledge_id=id, file_id=form_data.file_id, db=db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Remove content from the vector database
    VECTOR_DB_CLIENT.delete(
        collection_name=knowledge.id, filter={"file_id": form_data.file_id}
    )

    # Add content to the vector database
    try:
        process_file(
            request,
            ProcessFileForm(file_id=form_data.file_id, collection_name=id),
            user=user,
            db=db,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if knowledge:
        return _build_knowledge_response(
            Knowledges.get_knowledge_by_id(id=id, db=db),
            user,
            db,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# RemoveFileFromKnowledge
############################


@router.post("/{id}/file/remove", response_model=Optional[KnowledgeFilesResponse])
def remove_file_from_knowledge_by_id(
    id: str,
    form_data: KnowledgeFileIdForm,
    delete_file: bool = Query(True),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Validate the file actually belongs to this knowledge base
    if not Knowledges.has_file(knowledge_id=id, file_id=form_data.file_id, db=db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    Knowledges.remove_file_from_knowledge_by_id(
        knowledge_id=id, file_id=form_data.file_id, db=db
    )

    # Remove content from the vector database
    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id, filter={"file_id": form_data.file_id}
        )  # Remove by file_id first

        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id, filter={"hash": file.hash}
        )  # Remove by hash as well in case of duplicates
    except Exception as e:
        log.debug("This was most likely caused by bypassing embedding processing")
        log.debug(e)
        pass

    if delete_file:
        try:
            # Remove the file's collection from vector database
            file_collection = f"file-{form_data.file_id}"
            if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
        except Exception as e:
            log.debug("This was most likely caused by bypassing embedding processing")
            log.debug(e)
            pass

        # Delete file from database
        Files.delete_file_by_id(form_data.file_id, db=db)

    if knowledge:
        return _build_knowledge_response(
            Knowledges.get_knowledge_by_id(id=id, db=db),
            user,
            db,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# DeleteKnowledgeById
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_knowledge_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    log.info(f"Deleting knowledge base: {id} (name: {knowledge.name})")

    # Get all models
    models = Models.get_all_models(db=db)
    log.info(f"Found {len(models)} models to check for knowledge base {id}")

    # Update models that reference this knowledge base
    for model in models:
        if model.meta and hasattr(model.meta, "knowledge"):
            knowledge_list = model.meta.knowledge or []
            # Filter out the deleted knowledge base
            updated_knowledge = [k for k in knowledge_list if k.get("id") != id]

            # If the knowledge list changed, update the model
            if len(updated_knowledge) != len(knowledge_list):
                log.info(f"Updating model {model.id} to remove knowledge base {id}")
                model.meta.knowledge = updated_knowledge
                # Create a ModelForm for the update
                model_form = ModelForm(
                    id=model.id,
                    name=model.name,
                    base_model_id=model.base_model_id,
                    meta=model.meta,
                    params=model.params,
                    access_grants=model.access_grants,
                    is_active=model.is_active,
                )
                Models.update_model_by_id(model.id, model_form, db=db)

    # Clean up vector DB
    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass

    # Remove knowledge base embedding
    remove_knowledge_base_metadata_embedding(id)

    result = Knowledges.delete_knowledge_by_id(id=id, db=db)
    return result


############################
# ResetKnowledgeById
############################


@router.post("/{id}/reset", response_model=Optional[KnowledgeResponse])
async def reset_knowledge_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass

    knowledge = Knowledges.reset_knowledge_by_id(id=id, db=db)
    return knowledge


############################
# AddFilesToKnowledge
############################


@router.post("/{id}/files/batch/add", response_model=Optional[KnowledgeFilesResponse])
async def add_files_to_knowledge_batch(
    request: Request,
    id: str,
    form_data: list[KnowledgeFileIdForm],
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    """
    Add multiple files to a knowledge base
    """
    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="knowledge",
            resource_id=knowledge.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    # Batch-fetch all files to avoid N+1 queries
    log.info(f"files/batch/add - {len(form_data)} files")
    file_ids = [form.file_id for form in form_data]
    files = Files.get_files_by_ids(file_ids, db=db)

    # Verify all requested files were found
    found_ids = {file.id for file in files}
    missing_ids = [fid for fid in file_ids if fid not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File {missing_ids[0]} not found",
        )

    # Process files
    try:
        result = await process_files_batch(
            request=request,
            form_data=BatchProcessFilesForm(files=files, collection_name=id),
            user=user,
            db=db,
        )
    except Exception as e:
        log.error(
            f"add_files_to_knowledge_batch: Exception occurred: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Only add files that were successfully processed
    successful_file_ids = [r.file_id for r in result.results if r.status == "completed"]
    for file_id in successful_file_ids:
        Knowledges.add_file_to_knowledge_by_id(
            knowledge_id=id, file_id=file_id, user_id=user.id, db=db
        )

    # If there were any errors, include them in the response
    if result.errors:
        error_details = [f"{err.file_id}: {err.error}" for err in result.errors]
        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=Knowledges.get_file_metadatas_by_id(knowledge.id, db=db),
            warnings={
                "message": "Some files failed to process",
                "errors": error_details,
            },
        )

    return KnowledgeFilesResponse(
        **knowledge.model_dump(),
        files=Knowledges.get_file_metadatas_by_id(knowledge.id, db=db),
    )


############################
# ExportKnowledgeById
############################


@router.get("/{id}/export")
async def export_knowledge_by_id(
    id: str, user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    """
    Export a knowledge base as a zip file containing .txt files.
    Admin only.
    """

    knowledge = Knowledges.get_knowledge_by_id(id=id, db=db)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    files = Knowledges.get_files_by_id(id, db=db)

    # Create zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            content = file.data.get("content", "") if file.data else ""
            if content:
                # Use original filename with .txt extension
                filename = file.filename
                if not filename.endswith(".txt"):
                    filename = f"{filename}.txt"
                zf.writestr(filename, content)

    zip_buffer.seek(0)

    # Sanitize knowledge name for filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in knowledge.name)
    zip_filename = f"{safe_name}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )
