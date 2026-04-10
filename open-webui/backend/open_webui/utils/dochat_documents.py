import os
import re
import time
from typing import Optional

from sqlalchemy.orm import Session

from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.models.files import FileModel, Files
from open_webui.models.groups import Groups
from open_webui.models.knowledge import Knowledges


_LATIN_LETTER_RE = re.compile(r"[A-Za-zÀ-ÿ]")
_NON_WORD_SYMBOL_RE = re.compile(r"[<>{}\[\]|_~`^]")
_PUNCTUATION_RE = re.compile(r"[^\w\sÀ-ÿ]", re.UNICODE)
_SCRIPT_PATTERNS = (
    re.compile(r"[A-Za-zÀ-ÿ]"),
    re.compile(r"[\u0400-\u04FF]"),
    re.compile(r"[\u4E00-\u9FFF]"),
    re.compile(r"[\u0600-\u06FF]"),
)
VERSION_CONTROL_HISTORY_LIMIT = 25
DEFAULT_DOCUMENT_STATUS = "Em elaboração"
TERMINAL_DOCUMENT_STATUS_TOKENS = (
    "conclu",
    "final",
    "aprovad",
    "vigente",
)


def normalize_timestamp(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return int(value / 1_000_000_000) if value > 10_000_000_000 else int(value)


def ensure_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    text = str(value).replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def is_likely_garbled_text(value: Optional[str]) -> bool:
    text = normalize_text(value)
    if not text:
        return False

    lowered = text.lower()
    if "<unk>" in lowered or "\ufffd" in text:
        return True

    non_space_length = len(re.sub(r"\s+", "", text))
    if non_space_length < 16:
        return False

    letter_count = len(_LATIN_LETTER_RE.findall(text))
    punctuation_count = len(_PUNCTUATION_RE.findall(text))
    weird_symbol_count = len(_NON_WORD_SYMBOL_RE.findall(text))
    mixed_script_count = sum(1 for pattern in _SCRIPT_PATTERNS if pattern.search(text))

    if weird_symbol_count / max(non_space_length, 1) > 0.04:
        return True

    if punctuation_count / max(non_space_length, 1) > 0.28:
        return True

    if non_space_length >= 30 and letter_count / max(non_space_length, 1) < 0.45:
        return True

    if mixed_script_count > 1 and punctuation_count / max(non_space_length, 1) > 0.1:
        return True

    return False


def first_clean_text(*values: Optional[str]) -> Optional[str]:
    for value in values:
        text = normalize_text(value)
        if text and not is_likely_garbled_text(text):
            return text
    return None


def clean_string_list(value) -> list[str]:
    cleaned_items = []
    for item in ensure_list(value):
        text = normalize_text(item)
        if text and not is_likely_garbled_text(text):
            cleaned_items.append(text)
    return cleaned_items


def normalize_document_status_value(*values: Optional[str]) -> Optional[str]:
    return first_clean_text(*values)


def get_document_status(meta: Optional[dict]) -> str:
    meta = meta or {}
    return (
        normalize_document_status_value(
            meta.get("document_status"),
            meta.get("status"),
            meta.get("workflow_status"),
            meta.get("review_status"),
            meta.get("state"),
        )
        or DEFAULT_DOCUMENT_STATUS
    )


def is_terminal_document_status(value: Optional[str]) -> bool:
    normalized = (normalize_text(value) or "").lower()
    return any(token in normalized for token in TERMINAL_DOCUMENT_STATUS_TOKENS)


def normalize_document_governance_metadata(meta: Optional[dict]) -> dict:
    updated_meta = dict(meta or {})
    document_status = get_document_status(updated_meta)
    updated_meta["document_status"] = document_status

    locked_reason = first_clean_text(updated_meta.get("locked_reason"))
    raw_locked = updated_meta.get("is_locked_by_status")

    if is_terminal_document_status(document_status):
        updated_meta["is_locked_by_status"] = True
        updated_meta["locked_reason"] = "status_terminal"
    else:
        if locked_reason == "status_terminal" or not isinstance(raw_locked, bool):
            updated_meta["is_locked_by_status"] = False
        else:
            updated_meta["is_locked_by_status"] = raw_locked

        if locked_reason == "status_terminal":
            updated_meta["locked_reason"] = None
        else:
            updated_meta["locked_reason"] = locked_reason

    for key in (
        "copied_from_document_id",
        "copied_from_file_id",
        "copied_from_name",
        "copy_reason",
    ):
        updated_meta[key] = first_clean_text(updated_meta.get(key))

    return updated_meta


def is_document_locked_by_status(meta: Optional[dict]) -> bool:
    return bool(normalize_document_governance_metadata(meta).get("is_locked_by_status"))


def build_editable_copy_name(filename: Optional[str]) -> str:
    base_name = normalize_text(filename) or "Documento"
    stem, extension = os.path.splitext(base_name)
    if not stem:
        stem = base_name
        extension = ""
    return f"{stem} - copia{extension}"


def build_document_actor_payload(user=None) -> Optional[dict]:
    if not user:
        return None

    actor_id = getattr(user, "id", None)
    if not actor_id:
        return None

    return {
        "id": actor_id,
        "name": getattr(user, "name", None),
        "email": getattr(user, "email", None),
    }


def normalize_document_actor(value) -> Optional[dict]:
    if isinstance(value, str):
        value = {"id": value}

    if not isinstance(value, dict):
        return None

    actor_id = first_clean_text(value.get("id"))
    if not actor_id:
        return None

    return {
        "id": actor_id,
        "name": first_clean_text(value.get("name")),
        "email": first_clean_text(value.get("email")),
    }


def normalize_document_version_control(meta: Optional[dict]) -> dict:
    meta = meta or {}
    raw_version_control = meta.get("version_control")
    if not isinstance(raw_version_control, dict):
        raw_version_control = {}

    revision = raw_version_control.get("revision")
    try:
        revision = int(revision)
    except (TypeError, ValueError):
        try:
            revision = int(meta.get("version", 1))
        except (TypeError, ValueError):
            revision = 1

    checked_out_by = normalize_document_actor(raw_version_control.get("checked_out_by"))
    status = raw_version_control.get("status")
    if status != "checked_out" or not checked_out_by:
        status = "available"
        checked_out_by = None

    history = []
    for entry in ensure_list(raw_version_control.get("history")):
        if not isinstance(entry, dict):
            continue

        history_entry = {
            "action": first_clean_text(entry.get("action")) or "update",
            "revision": entry.get("revision"),
            "at": normalize_timestamp(entry.get("at")),
            "by": normalize_document_actor(entry.get("by")),
        }
        try:
            history_entry["revision"] = int(history_entry["revision"])
        except (TypeError, ValueError):
            history_entry["revision"] = revision
        history.append(history_entry)

    return {
        "status": status,
        "revision": max(revision, 1),
        "checked_out_by": checked_out_by,
        "checked_out_at": normalize_timestamp(raw_version_control.get("checked_out_at"))
        if status == "checked_out"
        else None,
        "history": history[-VERSION_CONTROL_HISTORY_LIMIT:],
    }


def is_document_checked_out_by_current_user(meta: Optional[dict], user_id: str) -> bool:
    version_control = normalize_document_version_control(meta)
    return (
        version_control.get("status") == "checked_out"
        and (version_control.get("checked_out_by") or {}).get("id") == user_id
    )


def is_document_checked_out_by_another_user(meta: Optional[dict], user_id: str) -> bool:
    version_control = normalize_document_version_control(meta)
    checked_out_by = version_control.get("checked_out_by") or {}
    return (
        version_control.get("status") == "checked_out"
        and bool(checked_out_by.get("id"))
        and checked_out_by.get("id") != user_id
    )


def apply_document_version_control_metadata(
    meta: Optional[dict],
    user=None,
    *,
    action: str,
    mark_modified: bool = False,
    checkout: bool = False,
    checkin: bool = False,
    bump_revision: bool = False,
) -> dict:
    updated_meta = normalize_document_governance_metadata(meta)
    actor = build_document_actor_payload(user) or normalize_document_actor(
        updated_meta.get("last_modified_by")
    )
    now = int(time.time())
    version_control = normalize_document_version_control(updated_meta)

    if bump_revision:
        version_control["revision"] = max(int(version_control.get("revision") or 1) + 1, 1)

    if checkout:
        version_control["status"] = "checked_out"
        version_control["checked_out_by"] = actor
        version_control["checked_out_at"] = now

    if checkin:
        version_control["status"] = "available"
        version_control["checked_out_by"] = None
        version_control["checked_out_at"] = None

    if mark_modified and actor:
        updated_meta["last_modified_by"] = actor
        updated_meta["last_modified_at"] = now

    history = [
        entry
        for entry in ensure_list(version_control.get("history"))
        if isinstance(entry, dict)
    ]
    history.append(
        {
            "action": action,
            "revision": version_control.get("revision"),
            "at": now,
            "by": actor,
        }
    )
    version_control["history"] = history[-VERSION_CONTROL_HISTORY_LIMIT:]

    updated_meta["version_control"] = version_control
    return updated_meta


def normalize_collection_payload(collection) -> dict:
    if collection is None:
        return {}

    if hasattr(collection, "model_dump"):
        collection = collection.model_dump()

    return {
        "id": collection.get("id"),
        "name": collection.get("name"),
        "description": collection.get("description"),
        "updated_at": normalize_timestamp(collection.get("updated_at")),
        "created_at": normalize_timestamp(collection.get("created_at")),
    }


def guess_document_type(file: FileModel) -> str:
    meta = file.meta or {}
    if meta.get("document_type"):
        return meta["document_type"]

    content_type = meta.get("content_type")
    if isinstance(content_type, str) and "/" in content_type:
        major, minor = content_type.split("/", 1)
        if major == "application" and minor:
            return minor
        return major

    _, ext = os.path.splitext(file.filename or "")
    return ext.lstrip(".").lower() or "document"


def get_processing_status(file: FileModel) -> str:
    data = file.data or {}
    if data.get("processing_status"):
        return data["processing_status"]

    legacy_status = data.get("status")
    if legacy_status == "completed":
        return "ready"
    if legacy_status == "failed":
        return "failed"
    if legacy_status == "pending":
        return "processing"
    return "pending"


def get_embedding_status(file: FileModel) -> str:
    data = file.data or {}
    if data.get("embedding_status"):
        return data["embedding_status"]

    legacy_status = data.get("status")
    if legacy_status == "completed":
        return "ready"
    if legacy_status == "failed":
        return "failed"
    if legacy_status == "pending":
        return "processing"
    return "pending"


def build_document_payload(
    file: FileModel,
    collections: Optional[list] = None,
    related: Optional[dict] = None,
) -> dict:
    meta = normalize_document_governance_metadata(file.meta or {})
    data = file.data or {}

    collection_items = [
        normalize_collection_payload(collection)
        for collection in collections or []
        if collection
    ]
    collection_items = [
        collection
        for idx, collection in enumerate(collection_items)
        if collection and collection.get("id") and collection["id"]
        not in {item.get("id") for item in collection_items[:idx]}
    ]
    primary_collection = collection_items[0] if collection_items else None

    processing_status = get_processing_status(file)
    embedding_status = get_embedding_status(file)
    chunk_count = data.get("chunk_count")
    if chunk_count is None:
        chunk_count = 1 if data.get("content") else 0

    title = first_clean_text(meta.get("title"), meta.get("name"), file.filename) or file.filename
    description = first_clean_text(
        meta.get("description"),
        meta.get("summary"),
        data.get("description"),
        data.get("summary"),
    )
    summary = first_clean_text(meta.get("summary"), data.get("summary"), description)
    source = first_clean_text(meta.get("source"), meta.get("url"), file.filename) or file.filename
    author = first_clean_text(meta.get("author"))
    language = first_clean_text(meta.get("language"))
    tags = clean_string_list(meta.get("tags"))
    entities = clean_string_list(meta.get("entities"))
    uploaded_by = normalize_document_actor(meta.get("uploaded_by")) or {
        "id": file.user_id,
        "name": None,
        "email": None,
    }
    last_modified_by = normalize_document_actor(meta.get("last_modified_by")) or uploaded_by
    version_control = normalize_document_version_control(meta)
    last_modified_at = normalize_timestamp(meta.get("last_modified_at") or file.updated_at)
    document_status = get_document_status(meta)
    is_locked_by_status = bool(meta.get("is_locked_by_status"))
    locked_reason = first_clean_text(meta.get("locked_reason"))
    copied_from_document_id = first_clean_text(meta.get("copied_from_document_id"))
    copied_from_file_id = first_clean_text(meta.get("copied_from_file_id"))
    copied_from_name = first_clean_text(meta.get("copied_from_name"))
    copy_reason = first_clean_text(meta.get("copy_reason"))

    return {
        "id": file.id,
        "title": title,
        "filename": file.filename,
        "description": description,
        "summary": summary or description,
        "document_type": guess_document_type(file),
        "document_status": document_status,
        "is_locked_by_status": is_locked_by_status,
        "locked_reason": locked_reason,
        "copied_from_document_id": copied_from_document_id,
        "collection": primary_collection,
        "collections": collection_items,
        "primary_collection": primary_collection,
        "source": source,
        "author": author,
        "language": language,
        "tags": tags,
        "entities": entities,
        "processing_status": processing_status,
        "embedding_status": embedding_status,
        "chunk_count": chunk_count,
        "version": meta.get("version", 1),
        "updated_at": normalize_timestamp(file.updated_at),
        "created_at": normalize_timestamp(file.created_at),
        "last_processed_at": normalize_timestamp(
            data.get("last_processed_at")
            or (file.updated_at if processing_status == "ready" else None)
        ),
        "is_archived": getattr(file, "is_archived", False),
        "archived_at": normalize_timestamp(getattr(file, "archived_at", None)),
        "metadata": {
            "content_type": meta.get("content_type"),
            "size": meta.get("size"),
            "owner_id": file.user_id,
            "hash": file.hash,
            "source_type": meta.get("source_type"),
            "relative_path": meta.get("relative_path"),
            "folder_path": meta.get("folder_path"),
            "document_status": document_status,
            "is_locked_by_status": is_locked_by_status,
            "locked_reason": locked_reason,
            "copied_from_document_id": copied_from_document_id,
            "copied_from_file_id": copied_from_file_id,
            "copied_from_name": copied_from_name,
            "copy_reason": copy_reason,
            "uploaded_by": uploaded_by,
            "last_modified_by": last_modified_by,
            "last_modified_at": last_modified_at,
            "version_control": version_control,
        },
        "provenance": {
            "added_by": file.user_id,
            "added_by_user": uploaded_by,
            "added_at": normalize_timestamp(file.created_at),
            "modified_by": last_modified_by.get("id") if last_modified_by else None,
            "modified_by_user": last_modified_by,
            "modified_at": last_modified_at,
            "checked_out_by": version_control.get("checked_out_by"),
            "checked_out_at": version_control.get("checked_out_at"),
            "revision": version_control.get("revision"),
            "copied_from_document_id": copied_from_document_id,
            "copied_from_file_id": copied_from_file_id,
            "copied_from_name": copied_from_name,
            "copy_reason": copy_reason,
            "last_processed_at": normalize_timestamp(data.get("last_processed_at")),
        },
        "related": related or {},
    }


def document_matches_filters(
    document: dict,
    *,
    query: Optional[str] = None,
    collection: Optional[str] = None,
    tags: Optional[list | str] = None,
    source: Optional[str] = None,
    document_type: Optional[str] = None,
    document_status: Optional[str] = None,
    locked: Optional[bool] = None,
    archived: Optional[bool] = None,
) -> bool:
    if archived is not None and document.get("is_archived") != archived:
        return False

    if locked is not None and bool(document.get("is_locked_by_status")) != locked:
        return False

    if collection:
        collection_key = collection.lower().strip()
        document_collections = document.get("collections") or []
        if not any(
            collection_key
            in {
                (item.get("id") or "").lower(),
                (item.get("name") or "").lower(),
            }
            for item in document_collections
        ):
            return False

    if source and source.lower() not in (document.get("source") or "").lower():
        return False

    if document_type:
        normalized_type = (document.get("document_type") or "").lower().strip()
        requested_type = document_type.lower().strip()
        if requested_type not in normalized_type:
            return False

    if document_status:
        normalized_status = (document.get("document_status") or "").lower().strip()
        requested_status = document_status.lower().strip()
        if requested_status not in normalized_status:
            return False

    if tags:
        requested_tags = {tag.lower() for tag in ensure_list(tags)}
        document_tags = {tag.lower() for tag in ensure_list(document.get("tags"))}
        if not requested_tags.issubset(document_tags):
            return False

    if query:
        query_key = query.lower()
        haystack = " ".join(
            [
                document.get("title") or "",
                document.get("description") or "",
                document.get("summary") or "",
                document.get("filename") or "",
                document.get("source") or "",
                document.get("author") or "",
                document.get("language") or "",
                document.get("document_type") or "",
                document.get("document_status") or "",
                " ".join(ensure_list(document.get("tags"))),
                " ".join(
                    (collection.get("name") or "")
                    for collection in document.get("collections") or []
                ),
            ]
        ).lower()
        if query_key not in haystack:
            return False

    return True


def collect_accessible_documents(
    user,
    db: Optional[Session] = None,
    include_shared: bool = True,
) -> list[dict]:
    entries: dict[str, dict] = {}

    owned_files = (
        Files.get_files(db=db)
        if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL
        else Files.get_files_by_user_id(user.id, db=db)
    )

    for file in owned_files:
        entries[file.id] = {
            "file": file,
            "collections": Knowledges.get_knowledges_by_file_id(file.id, db=db),
        }

    if include_shared and not (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL):
        filter = {"user_id": user.id}
        groups = Groups.get_groups_by_member_id(user.id, db=db)
        if groups:
            filter["group_ids"] = [group.id for group in groups]

        skip = 0
        limit = 200
        while True:
            result = Knowledges.search_knowledge_files(
                filter=filter, skip=skip, limit=limit, db=db
            )
            if not result.items:
                break

            for item in result.items:
                file = entries.get(item.id, {}).get("file") or Files.get_file_by_id(
                    item.id, db=db
                )
                if not file:
                    continue

                collection = item.model_dump().get("collection")
                collections = entries.get(item.id, {}).get("collections", [])
                if collection and collection.get("id"):
                    seen = {
                        entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                        for entry in collections
                    }
                    if collection["id"] not in seen:
                        collections = [*collections, collection]

                entries[item.id] = {
                    "file": file,
                    "collections": collections
                    or Knowledges.get_knowledges_by_file_id(item.id, db=db),
                }

            skip += limit
            if skip >= result.total:
                break

    return list(entries.values())
