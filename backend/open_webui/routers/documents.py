import asyncio
from difflib import SequenceMatcher
from io import BytesIO
import json
import logging
import re
import time
import uuid
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_session
from open_webui.models.chats import Chat, ChatFile
from open_webui.models.files import FileForm, Files
from open_webui.models.file_revisions import FileRevisions
from open_webui.models.knowledge import Knowledges
from open_webui.models.notes import Notes
from open_webui.models.users import Users
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.routers.retrieval import ProcessFileForm, process_file
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.dochat_documents import (
    apply_document_version_control_metadata,
    build_editable_copy_name,
    build_document_payload,
    collect_accessible_documents,
    DEFAULT_DOCUMENT_STATUS,
    document_matches_filters,
    ensure_list,
    guess_document_type,
    first_clean_text,
    get_document_status,
    is_document_checked_out_by_another_user,
    is_document_locked_by_status,
    is_likely_garbled_text,
    normalize_document_governance_metadata,
    normalize_document_actor,
    normalize_document_version_control,
    normalize_document_status_value,
    normalize_text,
    normalize_timestamp,
)
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.storage.provider import Storage

router = APIRouter()
log = logging.getLogger(__name__)

LOCKED_DOCUMENT_DETAIL = (
    "Este documento esta finalizado e nao pode mais ser editado diretamente. "
    "Crie uma copia para continuar o trabalho."
)
COPY_ONLY_FOR_LOCKED_DETAIL = "Somente documentos finalizados podem gerar copia editavel."
DOCUMENT_METADATA_META_RESERVED_KEYS = {
    "document_status",
    "is_locked_by_status",
    "locked_reason",
    "copied_from_document_id",
    "copied_from_file_id",
    "copied_from_name",
    "copy_reason",
}


METADATA_RESPONSE_SCHEMA = {
    "name": "document_metadata",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "summary": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "entities": {"type": "array", "items": {"type": "string"}},
            "author": {"type": ["string", "null"]},
            "source": {"type": ["string", "null"]},
            "language": {"type": ["string", "null"]},
            "document_type": {"type": ["string", "null"]},
            "suggested_collection_hints": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "title",
            "description",
            "summary",
            "tags",
            "entities",
            "author",
            "source",
            "language",
            "document_type",
            "suggested_collection_hints",
        ],
    },
}

METADATA_STOPWORDS = {
    "about",
    "ainda",
    "aos",
    "aqui",
    "assim",
    "como",
    "com",
    "cada",
    "das",
    "de",
    "da",
    "do",
    "dos",
    "das",
    "ela",
    "ele",
    "eles",
    "entre",
    "essa",
    "esse",
    "esta",
    "este",
    "estou",
    "forma",
    "foram",
    "foi",
    "have",
    "isso",
    "isto",
    "mais",
    "meu",
    "minha",
    "muito",
    "não",
    "na",
    "nas",
    "no",
    "nos",
    "numa",
    "para",
    "pela",
    "pelos",
    "por",
    "porque",
    "que",
    "sem",
    "ser",
    "sobre",
    "sua",
    "suas",
    "their",
    "them",
    "uma",
    "umas",
    "uns",
    "with",
}

METADATA_GENERATION_TIMEOUT_SECONDS = 12
METADATA_PIPELINE_TIMEOUT_SECONDS = 5
METADATA_TEXT_MAX_WORDS = 300
METADATA_AUTHOR_PAGE_LIMIT = 4
METADATA_TAG_COUNT = 5
METADATA_NOISE_TOKENS = (
    "www.",
    "http://",
    "https://",
    "@",
    "facebook.com",
    "instagram.com",
    "editorial.apoiocliente",
    "copyright",
    "isbn",
    "depósito legal",
    "conceção gráfica",
    "concepção gráfica",
    "capa:",
    "imprensa nacional",
    "casa da moeda",
)
METADATA_FIELD_PATTERNS = {
    "title": [
        re.compile(r"(?:^|\b)t[ií]tulo\s*:\s*(.+?)(?=(?:\bautor\s*:|\bedi[cç][aã]o\s*:|$))", re.IGNORECASE),
    ],
    "author": [
        re.compile(r"(?:^|\b)autor(?:a)?\s*:\s*(.+?)(?=(?:\bedi[cç][aã]o\s*:|\bcoord|\borg|\bintrodu|\bnota|\bapresenta|$))", re.IGNORECASE),
    ],
    "edition": [
        re.compile(r"(?:^|\b)edi[cç][aã]o\s*:\s*(.+?)(?=(?:\bconce[cç][aã]o|\bcapa\s*:|\bdep[óo]sito|\bisbn|$))", re.IGNORECASE),
    ],
}
METADATA_LANGUAGE_ALIASES = {
    "pt": "pt-br",
    "pt-br": "pt-br",
    "pt_br": "pt-br",
    "portugues": "pt-br",
    "português": "pt-br",
    "portugues brasileiro": "pt-br",
    "português brasileiro": "pt-br",
    "pt-pt": "pt-pt",
    "pt_pt": "pt-pt",
    "portugues europeu": "pt-pt",
    "português europeu": "pt-pt",
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
    "en_gb": "en",
    "english": "en",
    "ingles": "en",
    "inglês": "en",
    "es": "es",
    "es-es": "es",
    "es_es": "es",
    "espanhol": "es",
    "spanish": "es",
    "fr": "fr",
    "fr-fr": "fr",
    "fr_fr": "fr",
    "frances": "fr",
    "francês": "fr",
    "french": "fr",
    "de": "de",
    "de-de": "de",
    "de_de": "de",
    "alemao": "de",
    "alemão": "de",
    "german": "de",
    "it": "it",
    "it-it": "it",
    "it_it": "it",
    "italiano": "it",
    "italian": "it",
}
METADATA_LANGUAGE_HINTS = {
    "pt-br": {
        "a",
        "as",
        "com",
        "como",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "não",
        "para",
        "por",
        "que",
        "uma",
    },
    "en": {
        "about",
        "and",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "that",
        "the",
        "this",
        "to",
        "with",
    },
    "es": {
        "con",
        "como",
        "de",
        "del",
        "el",
        "en",
        "es",
        "la",
        "las",
        "los",
        "para",
        "por",
        "que",
        "una",
    },
}
METADATA_DOCUMENT_TYPE_TAG_MAP = {
    "artigo": "artigo",
    "article": "artigo",
    "book": "livro",
    "csv": "planilha",
    "doc": "documento",
    "docx": "documento",
    "html": "pagina web",
    "livro": "livro",
    "manual": "manual",
    "md": "nota",
    "pdf": "documento",
    "planilha": "planilha",
    "ppt": "apresentacao",
    "pptx": "apresentacao",
    "relatorio": "relatorio",
    "report": "relatorio",
    "spreadsheet": "planilha",
    "txt": "texto",
}
METADATA_TAG_DECISION_TREE = (
    {
        "tag": "juridico",
        "keywords": {
            "acordo",
            "clausula",
            "compliance",
            "contrato",
            "direito",
            "juridico",
            "lei",
            "legal",
            "regulamento",
        },
        "children": (
            {
                "tag": "contratos",
                "keywords": {"acordo", "clausula", "contrato", "partes", "termo"},
            },
            {
                "tag": "legislacao",
                "keywords": {"decreto", "estatuto", "lei", "norma", "regulamento"},
            },
            {
                "tag": "compliance",
                "keywords": {"auditoria", "compliance", "governanca", "risco"},
            },
        ),
    },
    {
        "tag": "negocios",
        "keywords": {
            "cliente",
            "comercial",
            "empresa",
            "estrategia",
            "gestao",
            "mercado",
            "negocio",
            "operacao",
            "produto",
            "vendas",
        },
        "children": (
            {
                "tag": "estrategia",
                "keywords": {"estrategia", "planejamento", "roadmap", "objetivo"},
            },
            {
                "tag": "operacoes",
                "keywords": {"fluxo", "indicador", "operacao", "processo", "sla"},
            },
            {
                "tag": "produtos",
                "keywords": {"feature", "lancamento", "produto", "requisito"},
            },
        ),
    },
    {
        "tag": "tecnologia",
        "keywords": {
            "algoritmo",
            "api",
            "codigo",
            "dados",
            "engenharia",
            "modelo",
            "software",
            "sistema",
            "tecnologia",
        },
        "children": (
            {
                "tag": "dados",
                "keywords": {"analytics", "banco", "dataset", "dados", "metrica"},
            },
            {
                "tag": "inteligencia artificial",
                "keywords": {"embedding", "llm", "machine", "modelo", "prompt", "rag"},
            },
            {
                "tag": "desenvolvimento",
                "keywords": {"api", "codigo", "frontend", "backend", "software"},
            },
        ),
    },
    {
        "tag": "financas",
        "keywords": {
            "balanco",
            "credito",
            "financeiro",
            "investimento",
            "orcamento",
            "receita",
            "risco",
        },
        "children": (
            {
                "tag": "contabilidade",
                "keywords": {"balanco", "contabil", "demonstracao", "patrimonio"},
            },
            {
                "tag": "investimentos",
                "keywords": {"ativo", "capital", "carteira", "investimento", "retorno"},
            },
            {
                "tag": "orcamento",
                "keywords": {"budget", "custo", "orcamento", "receita", "valor"},
            },
        ),
    },
    {
        "tag": "pesquisa",
        "keywords": {
            "analise",
            "artigo",
            "estudo",
            "metodo",
            "pesquisa",
            "resultado",
            "teoria",
        },
        "children": (
            {
                "tag": "metodologia",
                "keywords": {"abordagem", "experimento", "metodo", "metodologia"},
            },
            {
                "tag": "resultados",
                "keywords": {"analise", "conclusao", "evidencia", "resultado"},
            },
            {
                "tag": "revisao teorica",
                "keywords": {"bibliografia", "literatura", "revisao", "teoria"},
            },
        ),
    },
    {
        "tag": "educacao",
        "keywords": {
            "aula",
            "curso",
            "didatico",
            "educacao",
            "ensino",
            "formacao",
            "treinamento",
        },
        "children": (
            {
                "tag": "material didatico",
                "keywords": {"apostila", "didatico", "exercicio", "licao"},
            },
            {
                "tag": "formacao",
                "keywords": {"capacitacao", "curso", "formacao", "treinamento"},
            },
        ),
    },
    {
        "tag": "saude",
        "keywords": {
            "clinico",
            "diagnostico",
            "medico",
            "paciente",
            "saude",
            "tratamento",
        },
        "children": (
            {
                "tag": "pesquisa clinica",
                "keywords": {"clinico", "ensaio", "paciente", "protocolo"},
            },
            {
                "tag": "tratamento",
                "keywords": {"diagnostico", "terapia", "tratamento"},
            },
        ),
    },
)
METADATA_TAG_PADDING = (
    "conteudo",
    "referencia",
    "consulta",
    "documentos",
    "analise",
)
METADATA_FIELD_PROMPTS = {
    "title": """
Gere exclusivamente o campo title.
Se o título original do documento estiver claramente presente no excerto das primeiras páginas ou nos metadados atuais, devolva esse título exatamente, preservando nomes próprios.
Se não houver título original confiável, crie um título descritivo, único e identificável em pt-BR, sem inventar fatos e sem usar um rótulo genérico.
Evite devolver apenas o nome do arquivo quando ele não for um título real.
""".strip(),
    "description": f"""
Gere exclusivamente o campo description em pt-BR, com no máximo {METADATA_TEXT_MAX_WORDS} palavras.
Escreva uma descrição factual do documento, cobrindo tipo, tema, escopo, contexto, entidades centrais e recorte temporal/geográfico quando isso aparecer no texto.
Considere o documento inteiro ou o máximo de contexto possível a partir do excerto fornecido.
Não copie trechos longos literalmente e não invente informações.
""".strip(),
    "summary": f"""
Gere exclusivamente o campo summary em pt-BR, com no máximo {METADATA_TEXT_MAX_WORDS} palavras.
Resuma o argumento, a premissa, a tese, o objetivo e/ou a linha principal do texto.
Se o texto não for argumentativo, resuma sua finalidade e conteúdo principal.
Evite repetir a descrição e não copie o início do documento palavra por palavra.
""".strip(),
    "author": """
Gere exclusivamente o campo author.
Procure o autor apenas no excerto das 4 primeiras páginas, priorizando créditos explícitos como "Autor", "Autora", "Por", "By", "Written by" ou assinatura equivalente.
Ignore editoras, gráficas, contatos, copyright, uploader e nomes que não sejam claramente autoria.
Se não encontrar um autor confiável nas 4 primeiras páginas, devolva uma string vazia.
""".strip(),
    "source": """
Gere exclusivamente o campo source.
Identifique a origem declarada do documento quando ela estiver clara (URL, instituição, publicação ou repositório).
Se não houver origem explícita, use o nome do arquivo ou o source atual sem inventar.
""".strip(),
    "language": """
Gere exclusivamente o campo language.
Devolva apenas a sigla em minúsculas no formato usado em pt-BR, por exemplo: pt-br, en, es, fr, de ou it.
Use pt-br para português brasileiro; use pt-pt somente se o texto for claramente português europeu.
""".strip(),
    "document_type": """
Gere exclusivamente o campo document_type.
Classifique o tipo documental com um rótulo curto, reutilizável e em minúsculas, por exemplo: artigo, relatorio, manual, livro, contrato, apresentacao, planilha, nota ou documento.
""".strip(),
    "tags": f"""
Gere exclusivamente o campo tags como uma lista com exatamente {METADATA_TAG_COUNT} tags em pt-BR, em minúsculas, sem duplicatas e com 1 a 4 palavras cada.
As tags devem seguir uma árvore de decisão reutilizável e aparecer em ordem gradual da mais geral para a mais específica:
1. domínio macro do conteúdo (ex.: juridico, tecnologia, negocios, financas, pesquisa, educacao, saude);
2. tipo documental ou categoria intermediária reutilizável (ex.: contratos, relatorio, artigo, manual, dados, operacoes);
3. tema central do documento;
4. subtema, método, entidade, processo ou recorte específico;
5. assunto mais específico que identifique bem este documento sem criar um identificador descartável.
Prefira tags reaproveitáveis em outros documentos, evite sinônimos redundantes e não invente entidades.
""".strip(),
    "entities": """
Gere exclusivamente o campo entities como uma lista de até 12 entidades nomeadas explicitamente citadas no texto.
Priorize pessoas, organizações, sistemas, leis, produtos, lugares e instituições.
Preserve a grafia original e não invente entidades.
""".strip(),
    "suggested_collection_hints": """
Gere exclusivamente o campo suggested_collection_hints como uma lista de até 5 nomes curtos de coleções em pt-BR.
As sugestões devem ser amplas o bastante para agrupar documentos relacionados e específicas o bastante para serem úteis na navegação.
Reaproveite nomes de coleções existentes quando fizer sentido.
""".strip(),
}


class DocumentResponse(BaseModel):
    id: str
    title: str
    filename: str
    description: Optional[str] = None
    summary: Optional[str] = None
    document_type: str
    document_status: str = DEFAULT_DOCUMENT_STATUS
    is_locked_by_status: bool = False
    locked_reason: Optional[str] = None
    copied_from_document_id: Optional[str] = None
    collection: Optional[dict] = None
    collections: list[dict] = Field(default_factory=list)
    primary_collection: Optional[dict] = None
    source: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    tags: list = Field(default_factory=list)
    entities: list = Field(default_factory=list)
    processing_status: str
    embedding_status: str
    chunk_count: int
    version: int | str
    updated_at: Optional[int] = None
    created_at: Optional[int] = None
    last_processed_at: Optional[int] = None
    is_archived: bool = False
    archived_at: Optional[int] = None
    metadata: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    related: dict = Field(default_factory=dict)


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int


class DocumentCollectionResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    updated_at: Optional[int] = None
    created_at: Optional[int] = None
    count: int = 0


class DocumentProcessingResponse(BaseModel):
    id: str
    processing_status: str
    embedding_status: str
    chunk_count: int
    last_processed_at: Optional[int] = None
    error: Optional[str] = None


class DocumentVersionResponse(BaseModel):
    revision: int
    action: Optional[str] = None
    is_current: bool = False
    created_at: Optional[int] = None
    created_by: Optional[dict] = None
    line_count: int = 0
    char_count: int = 0


class DocumentVersionDiffLine(BaseModel):
    type: str
    text: str
    old_line: Optional[int] = None
    new_line: Optional[int] = None


class DocumentVersionCompareResponse(BaseModel):
    base_version: DocumentVersionResponse
    target_version: DocumentVersionResponse
    summary: dict = Field(default_factory=dict)
    lines: list[DocumentVersionDiffLine] = Field(default_factory=list)


class DocumentArchiveForm(BaseModel):
    is_archived: Optional[bool] = None


class DocumentMetadataUpdateForm(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[list[str]] = None
    entities: Optional[list[str]] = None
    author: Optional[str] = None
    source: Optional[str] = None
    language: Optional[str] = None
    document_type: Optional[str] = None
    document_status: Optional[str] = None
    version: Optional[int | str] = None
    suggested_collection_hints: Optional[list[str]] = None
    meta: Optional[dict] = None


class DocumentMetadataSuggestForm(BaseModel):
    instruction: Optional[str] = None
    model: Optional[str] = None


class DocumentMetadataSuggestionResponse(BaseModel):
    document_id: str
    model: str
    title: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    author: Optional[str] = None
    source: Optional[str] = None
    language: Optional[str] = None
    document_type: Optional[str] = None
    document_status: Optional[str] = DEFAULT_DOCUMENT_STATUS
    suggested_collection_hints: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)


def _check_document_access(file, user, permission: str, db: Session) -> None:
    if not (
        user.role == "admin"
        or file.user_id == user.id
        or has_access_to_file(file.id, permission, user, db=db)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


def _raise_locked_document_error() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=LOCKED_DOCUMENT_DETAIL,
    )


def _check_document_not_locked(file) -> None:
    if is_document_locked_by_status(file.meta or {}):
        _raise_locked_document_error()


def _sanitize_document_meta_input(meta: Optional[dict]) -> dict:
    if not isinstance(meta, dict):
        return {}

    return {
        key: value
        for key, value in meta.items()
        if key not in DOCUMENT_METADATA_META_RESERVED_KEYS
    }


def _check_document_checkout_state(file, user) -> None:
    if is_document_checked_out_by_another_user(file.meta or {}, user.id):
        version_control = (file.meta or {}).get("version_control") or {}
        checked_out_by = version_control.get("checked_out_by") or {}
        editor_name = (
            checked_out_by.get("name")
            or checked_out_by.get("email")
            or checked_out_by.get("id")
            or "outro usuário"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Documento em check-out por {editor_name}.",
        )


def _get_document_or_404(id: str, user, db: Session):
    file = Files.get_file_by_id(id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    _check_document_access(file, user, "read", db)
    return file


def _get_related_chat_ids(file_id: str, user, db: Session) -> list[str]:
    rows = (
        db.query(Chat.id)
        .join(ChatFile, Chat.id == ChatFile.chat_id)
        .filter(ChatFile.file_id == file_id, Chat.user_id == user.id)
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def _get_related_note_ids(file_id: str, user, db: Session) -> list[str]:
    notes = Notes.get_notes_by_user_id(user.id, "read", skip=0, limit=500, db=db)
    related_ids = []
    for note in notes:
        related = (note.meta or {}).get("related") or {}
        document_ids = ensure_list(related.get("document_ids"))
        if file_id in document_ids:
            related_ids.append(note.id)
    return related_ids


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


def _parse_metadata_page_index(metadata: dict, fallback_index: int) -> int:
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


def _get_document_text(file, chunks: Optional[list[tuple[str, dict]]] = None) -> str:
    chunks = _get_document_chunks(file.id) if chunks is None else chunks
    if chunks:
        return "\n\n".join(text for text, _ in chunks if text).strip()
    return ((file.data or {}).get("content") or "").strip()


def _build_first_pages_excerpt(
    chunks: list[tuple[str, dict]],
    fallback_text: str,
    *,
    max_pages: int = METADATA_AUTHOR_PAGE_LIMIT,
    max_chars: int = 6000,
) -> str:
    if chunks:
        indexed_chunks = [
            (_parse_metadata_page_index(metadata, index), index, text or "")
            for index, (text, metadata) in enumerate(chunks)
            if normalize_text(text)
        ]

        selected = [
            text.strip()
            for page_index, _, text in sorted(indexed_chunks, key=lambda item: item[:2])
            if page_index < max_pages and text.strip()
        ]
        if not selected:
            selected = [
                text.strip()
                for _, _, text in sorted(indexed_chunks, key=lambda item: item[:2])[:max_pages]
                if text.strip()
            ]

        excerpt = "\n\n".join(selected).strip()
        if excerpt:
            return excerpt[:max_chars]

    return (fallback_text or "").strip()[:max_chars]


def _build_document_response(file, user, db: Session, include_related: bool = False):
    collections = Knowledges.get_knowledges_by_file_id(file.id, db=db)
    related = {}
    if include_related:
        related = {
            "chat_ids": _get_related_chat_ids(file.id, user, db),
            "note_ids": _get_related_note_ids(file.id, user, db),
        }
    return DocumentResponse(
        **build_document_payload(file, collections=collections, related=related)
    )


def _ensure_current_document_revision(file, db: Session):
    current_revision = max(
        int(normalize_document_version_control(file.meta or {}).get("revision") or 1), 1
    )
    current_content = (file.data or {}).get("content") or ""
    if current_content is None:
        current_content = ""

    existing_revision = FileRevisions.get_revision_by_file_id_and_revision(
        file.id, current_revision, db=db
    )
    if existing_revision:
        return existing_revision

    actor = normalize_document_actor((file.meta or {}).get("last_modified_by")) or normalize_document_actor(
        (file.meta or {}).get("uploaded_by")
    ) or {"id": file.user_id}
    created_at = normalize_timestamp(
        (file.meta or {}).get("last_modified_at") or file.updated_at or file.created_at
    ) or int(time.time())

    return FileRevisions.upsert_revision(
        file.id,
        current_revision,
        current_content,
        action="baseline",
        created_by=actor,
        created_at=created_at,
        db=db,
    )


def _build_document_version_response(
    revision, current_revision: int
) -> DocumentVersionResponse:
    content = revision.content or ""
    return DocumentVersionResponse(
        revision=revision.revision,
        action=revision.action,
        is_current=revision.revision == current_revision,
        created_at=normalize_timestamp(revision.created_at),
        created_by=revision.created_by,
        line_count=len(content.splitlines()),
        char_count=len(content),
    )


def _build_document_version_diff(base_content: str, target_content: str) -> list[DocumentVersionDiffLine]:
    base_lines = (base_content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    target_lines = (target_content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    if base_lines == [""] and target_lines == [""]:
        return []

    lines: list[DocumentVersionDiffLine] = []
    old_line = 1
    new_line = 1

    for tag, i1, i2, j1, j2 in SequenceMatcher(a=base_lines, b=target_lines).get_opcodes():
        if tag == "equal":
            for text in base_lines[i1:i2]:
                lines.append(
                    DocumentVersionDiffLine(
                        type="context",
                        text=text,
                        old_line=old_line,
                        new_line=new_line,
                    )
                )
                old_line += 1
                new_line += 1
            continue

        if tag in {"replace", "delete"}:
            for text in base_lines[i1:i2]:
                lines.append(
                    DocumentVersionDiffLine(
                        type="removed",
                        text=text,
                        old_line=old_line,
                        new_line=None,
                    )
                )
                old_line += 1

        if tag in {"replace", "insert"}:
            for text in target_lines[j1:j2]:
                lines.append(
                    DocumentVersionDiffLine(
                        type="added",
                        text=text,
                        old_line=None,
                        new_line=new_line,
                    )
                )
                new_line += 1

    return lines


def _refresh_document_metadata(file, user, db: Session):
    meta = file.meta or {}
    normalized_meta = {
        **meta,
        "title": first_clean_text(meta.get("title"), meta.get("name"), file.filename)
        or file.filename,
        "name": first_clean_text(meta.get("name"), meta.get("title"), file.filename)
        or file.filename,
        "description": first_clean_text(meta.get("description"), meta.get("summary")),
        "summary": first_clean_text(meta.get("summary"), meta.get("description")),
        "document_type": first_clean_text(meta.get("document_type")) or guess_document_type(file),
        "source": first_clean_text(meta.get("source"), meta.get("url"), file.filename)
        or file.filename,
        "author": first_clean_text(meta.get("author")),
        "language": _normalize_language_code(first_clean_text(meta.get("language"))),
        "tags": _clean_metadata_list(meta.get("tags")),
        "entities": _clean_metadata_list(meta.get("entities")),
        "version": meta.get("version", 1),
    }
    normalized_meta = apply_document_version_control_metadata(
        normalized_meta,
        user,
        action="metadata_refresh",
        mark_modified=True,
    )
    return Files.update_file_metadata_by_id(file.id, normalized_meta, db=db)


def _update_document_metadata(
    file, form_data: DocumentMetadataUpdateForm, user, db: Session
):
    meta = file.meta or {}
    normalized_meta = {
        **meta,
        **_sanitize_document_meta_input(form_data.meta),
    }

    if form_data.title is not None:
        clean_title = first_clean_text(form_data.title)
        normalized_meta["title"] = clean_title or file.filename
        normalized_meta["name"] = clean_title or file.filename
    else:
        normalized_meta["title"] = (
            first_clean_text(normalized_meta.get("title"), normalized_meta.get("name"))
            or file.filename
        )
        normalized_meta["name"] = (
            first_clean_text(normalized_meta.get("name"), normalized_meta.get("title"))
            or file.filename
        )

    if form_data.description is not None:
        normalized_meta["description"] = first_clean_text(form_data.description)
    if form_data.summary is not None:
        normalized_meta["summary"] = first_clean_text(form_data.summary)
    if form_data.author is not None:
        normalized_meta["author"] = first_clean_text(form_data.author)
    if form_data.source is not None:
        normalized_meta["source"] = first_clean_text(form_data.source)
    if form_data.language is not None:
        normalized_meta["language"] = _normalize_language_code(
            first_clean_text(form_data.language)
        )
    if form_data.document_type is not None:
        normalized_meta["document_type"] = first_clean_text(form_data.document_type)
    if form_data.document_status is not None:
        normalized_meta["document_status"] = (
            normalize_document_status_value(form_data.document_status)
            or DEFAULT_DOCUMENT_STATUS
        )
    else:
        normalized_meta["document_status"] = get_document_status(normalized_meta)
    if form_data.version is not None:
        normalized_meta["version"] = form_data.version
    if form_data.tags is not None:
        normalized_meta["tags"] = _clean_metadata_list(form_data.tags)
    else:
        normalized_meta["tags"] = _clean_metadata_list(normalized_meta.get("tags"))
    if form_data.entities is not None:
        normalized_meta["entities"] = _clean_metadata_list(form_data.entities)
    else:
        normalized_meta["entities"] = _clean_metadata_list(normalized_meta.get("entities"))
    if form_data.suggested_collection_hints is not None:
        normalized_meta["suggested_collection_hints"] = _clean_metadata_list(
            form_data.suggested_collection_hints
        )

    normalized_meta = normalize_document_governance_metadata(normalized_meta)

    normalized_meta = apply_document_version_control_metadata(
        normalized_meta,
        user,
        action="metadata_update",
        mark_modified=True,
        checkin=bool(normalized_meta.get("is_locked_by_status")),
    )
    return Files.update_file_metadata_by_id(file.id, normalized_meta, db=db)


def _duplicate_document_storage(file, target_file_id: str, filename: str, user) -> tuple[str, int]:
    storage_filename = f"{target_file_id}_{filename}"
    tags = {
        "OpenWebUI-User-Email": user.email,
        "OpenWebUI-User-Id": user.id,
        "OpenWebUI-User-Name": user.name,
        "OpenWebUI-File-Id": target_file_id,
    }

    duplicated_size = 0
    duplicated_path = ""

    if file.path:
        resolved_path = Storage.get_file(file.path)
        with open(resolved_path, "rb") as file_handle:
            contents, duplicated_path = Storage.upload_file(
                file_handle,
                storage_filename,
                tags,
            )
            duplicated_size = len(contents or b"")
        return duplicated_path, duplicated_size

    source_content = ((file.data or {}).get("content") or "").encode("utf-8")
    if source_content:
        contents, duplicated_path = Storage.upload_file(
            BytesIO(source_content),
            storage_filename,
            tags,
        )
        duplicated_size = len(contents or b"")

    return duplicated_path, duplicated_size


def _create_editable_document_copy(
    request: Request,
    file,
    user,
    db: Session,
):
    if not is_document_locked_by_status(file.meta or {}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=COPY_ONLY_FOR_LOCKED_DETAIL,
        )

    source_meta = normalize_document_governance_metadata(file.meta or {})
    source_collections = Knowledges.get_knowledges_by_file_id(file.id, db=db)
    source_title = (
        first_clean_text(source_meta.get("title"), source_meta.get("name"), file.filename)
        or file.filename
    )
    copy_filename = build_editable_copy_name(file.filename)
    copy_title = build_editable_copy_name(source_title)
    copy_file_id = str(uuid.uuid4())

    duplicated_path, duplicated_size = _duplicate_document_storage(
        file, copy_file_id, copy_filename, user
    )

    copy_meta = {
        **source_meta,
        "name": copy_filename,
        "title": copy_title,
        "document_status": DEFAULT_DOCUMENT_STATUS,
        "is_locked_by_status": False,
        "locked_reason": None,
        "copied_from_document_id": file.id,
        "copied_from_file_id": file.id,
        "copied_from_name": source_title,
        "copy_reason": "edit_after_finalization",
        "uploaded_by": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
        },
        "version": 1,
        "size": duplicated_size or int(source_meta.get("size") or 0),
    }
    copy_meta.pop("version_control", None)
    copy_meta = normalize_document_governance_metadata(copy_meta)
    copy_meta = apply_document_version_control_metadata(
        copy_meta,
        user,
        action="copy_create",
        mark_modified=True,
        checkin=True,
    )

    source_content = _get_document_text(file)
    copy_file = Files.insert_new_file(
        user.id,
        FileForm(
            id=copy_file_id,
            filename=copy_filename,
            path=duplicated_path,
            data={},
            meta=copy_meta,
        ),
        db=db,
    )

    if not copy_file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Unable to create editable copy"),
        )

    if source_content:
        process_file(
            request,
            ProcessFileForm(file_id=copy_file.id, content=source_content),
            user=user,
            db=db,
        )
    elif copy_file.path:
        process_file(
            request,
            ProcessFileForm(file_id=copy_file.id),
            user=user,
            db=db,
        )

    writable_collection_ids = []
    for collection in source_collections or []:
        collection_id = (
            collection.get("id")
            if isinstance(collection, dict)
            else getattr(collection, "id", None)
        )
        if not collection_id:
            continue
        if user.role != "admin" and not Knowledges.check_access_by_user_id(
            collection_id, user.id, permission="write", db=db
        ):
            continue

        Knowledges.add_file_to_knowledge_by_id(
            knowledge_id=collection_id,
            file_id=copy_file.id,
            user_id=user.id,
            db=db,
        )
        writable_collection_ids.append(collection_id)

        process_file(
            request,
            ProcessFileForm(file_id=copy_file.id, collection_name=collection_id),
            user=user,
            db=db,
        )

    if writable_collection_ids:
        copy_file_meta = copy_file.meta or {}
        copy_file_meta_payload = (
            copy_file_meta.get("data") if isinstance(copy_file_meta.get("data"), dict) else {}
        )
        copy_file = Files.update_file_metadata_by_id(
            copy_file.id,
            {
                "knowledge_id": writable_collection_ids[0],
                "data": {
                    **copy_file_meta_payload,
                    "knowledge_id": writable_collection_ids[0],
                },
            },
            db=db,
        )

    refreshed_copy = Files.get_file_by_id(copy_file.id, db=db)
    return refreshed_copy or copy_file


def _reprocess_document(request: Request, file, user, db: Session):
    now = int(time.time())
    Files.update_file_data_by_id(
        file.id,
        {
            "status": "pending",
            "processing_status": "processing",
            "embedding_status": "processing",
            "last_processed_at": now,
        },
        db=db,
    )

    standalone_collection = f"file-{file.id}"
    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=standalone_collection):
            VECTOR_DB_CLIENT.delete_collection(collection_name=standalone_collection)
    except Exception:
        pass

    process_file(request, ProcessFileForm(file_id=file.id), user=user, db=db)

    for knowledge in Knowledges.get_knowledges_by_file_id(file.id, db=db):
        try:
            VECTOR_DB_CLIENT.delete(
                collection_name=knowledge.id,
                filter={"file_id": file.id},
            )
        except Exception:
            pass

        process_file(
            request,
            ProcessFileForm(file_id=file.id, collection_name=knowledge.id),
            user=user,
            db=db,
        )


def _metadata_model_priority(model_id: str, model: dict) -> tuple[int, int, str]:
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

    if any(token in haystack for token in ("gpt", "claude", "qwen", "llama", "mistral")):
        score += 12

    owned_by = (model.get("owned_by") or "").lower()
    if owned_by in {"openai", "anthropic"}:
        score += 10
    elif owned_by == "ollama":
        score += 6

    if model.get("pipe"):
        score -= 40

    return (-score, 0 if model.get("id") == model_id else 1, haystack)


def _is_metadata_model_compatible(model_id: str, model: dict) -> bool:
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


def _resolve_metadata_model_id(
    request: Request, user, requested_model_id: Optional[str]
) -> Optional[str]:
    models = getattr(request.app.state, "MODELS", {}) or {}
    if not models:
        return None

    if requested_model_id:
        if requested_model_id not in models:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model not found",
            )
        candidate_ids = [requested_model_id]
    else:
        default_model_ids = [
            model_id.strip()
            for model_id in (
                (getattr(request.app.state.config, "DEFAULT_MODELS", None) or "").split(",")
            )
            if model_id and model_id.strip()
        ]

        preferred_ids = []
        seen_pref = set()
        for candidate_id in default_model_ids:
            if candidate_id and candidate_id in models and candidate_id not in seen_pref:
                preferred_ids.append(candidate_id)
                seen_pref.add(candidate_id)

        remaining_ids = [
            model_id
            for model_id in sorted(
                models.keys(),
                key=lambda model_id: _metadata_model_priority(model_id, models[model_id]),
            )
            if model_id not in seen_pref
        ]
        candidate_ids = preferred_ids + remaining_ids

    seen = set()
    for candidate_id in candidate_ids:
        if not candidate_id or candidate_id in seen or candidate_id not in models:
            continue
        seen.add(candidate_id)

        try:
            if user.role == "user":
                check_model_access(user, models[candidate_id])
            if not requested_model_id and not _is_metadata_model_compatible(
                candidate_id, models[candidate_id]
            ):
                continue
            return candidate_id
        except Exception:
            continue

    if requested_model_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return None


def _extract_completion_content(response) -> str:
    payload = response
    if hasattr(response, "body"):
        payload = json.loads(response.body.decode("utf-8"))

    if isinstance(payload, dict):
        return (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    return ""


def _extract_json_object(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return json.loads(content[start : end + 1])


def _clean_metadata_string(value: Optional[str]) -> Optional[str]:
    text = normalize_text(value)
    if not text:
        return None

    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean_metadata_author(value: Optional[str]) -> Optional[str]:
    text = _clean_metadata_string(value)
    if not text:
        return None

    if _fold_metadata_text(text) in {
        "autor desconhecido",
        "autoria desconhecida",
        "desconhecido",
        "nao identificado",
        "nao informado",
        "unknown",
        "n/a",
        "na",
        "-",
    }:
        return None

    return text


def _clean_metadata_list(value) -> list[str]:
    cleaned = []
    for item in ensure_list(value):
        text = _clean_metadata_string(item)
        if text and not is_likely_garbled_text(text):
            cleaned.append(text)
    return cleaned


def _clean_metadata_multiline_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    text = str(value).replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _truncate_metadata_words(
    value: Optional[str], max_words: int = METADATA_TEXT_MAX_WORDS
) -> Optional[str]:
    text = _clean_metadata_string(value)
    if not text:
        return None

    words = text.split()
    if len(words) <= max_words:
        return text

    truncated = " ".join(words[:max_words]).rstrip(" ,;:-")
    if truncated.endswith((".", "!", "?")):
        return truncated
    return f"{truncated}."


def _fold_metadata_text(value: Optional[str]) -> str:
    text = _clean_metadata_string(value)
    if not text:
        return ""

    return (
        text.lower()
        .replace("á", "a")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ã", "a")
        .replace("ä", "a")
        .replace("ç", "c")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("í", "i")
        .replace("ì", "i")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ó", "o")
        .replace("ò", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ö", "o")
        .replace("ú", "u")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )


def _normalize_language_code(value: Optional[str], fallback_text: str = "") -> Optional[str]:
    text = _clean_metadata_string(value)
    if text:
        normalized = _fold_metadata_text(text).replace("_", "-")
        normalized = re.sub(r"\s+", " ", normalized).strip()

        if normalized in METADATA_LANGUAGE_ALIASES:
            return METADATA_LANGUAGE_ALIASES[normalized]

        if "portugues" in normalized or normalized.startswith("pt"):
            return "pt-pt" if "pt-pt" in normalized or "europeu" in normalized else "pt-br"
        if "english" in normalized or "ingles" in normalized or normalized.startswith("en"):
            return "en"
        if "spanish" in normalized or "espanhol" in normalized or normalized.startswith("es"):
            return "es"
        if "french" in normalized or "frances" in normalized or normalized.startswith("fr"):
            return "fr"
        if "german" in normalized or "alemao" in normalized or normalized.startswith("de"):
            return "de"
        if "italian" in normalized or "italiano" in normalized or normalized.startswith("it"):
            return "it"

        match = re.match(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$", normalized)
        if match:
            return METADATA_LANGUAGE_ALIASES.get(normalized, normalized)

    tokens = Counter(
        re.findall(r"[A-Za-zÀ-ÿ]{2,}", (fallback_text or "").lower())
    )
    scores = {
        language_code: sum(tokens.get(token, 0) for token in hints)
        for language_code, hints in METADATA_LANGUAGE_HINTS.items()
    }
    best_language = max(scores, key=scores.get) if scores else None
    if best_language and scores.get(best_language, 0) >= 2:
        return best_language

    return None


def _normalize_metadata_tag(value: Optional[str]) -> Optional[str]:
    text = _clean_metadata_string(value)
    if not text:
        return None

    text = text.lower().replace("_", " ")
    text = re.sub(r"[^\w\sÀ-ÿ-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip(" -")
    if not text:
        return None

    if len(text.split()) == 1 and _fold_metadata_text(text) in METADATA_STOPWORDS:
        return None

    return " ".join(text.split()[:4])


def _score_metadata_tag_branch(haystack: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if keyword in haystack)


def _select_metadata_tag_branch(document: dict, parsed_tags: list[str], excerpt: str):
    haystack = _fold_metadata_text(
        " ".join(
            [
                document.get("title") or "",
                document.get("description") or "",
                document.get("summary") or "",
                document.get("document_type") or "",
                document.get("source") or "",
                " ".join(ensure_list(document.get("tags"))),
                " ".join(ensure_list(parsed_tags)),
                excerpt or "",
            ]
        )
    )

    best_branch = None
    best_branch_score = 0
    for branch in METADATA_TAG_DECISION_TREE:
        score = _score_metadata_tag_branch(haystack, branch.get("keywords", set()))
        if score > best_branch_score:
            best_branch = branch
            best_branch_score = score

    if not best_branch:
        return None, None

    best_child = None
    best_child_score = 0
    for child in best_branch.get("children", ()):
        score = _score_metadata_tag_branch(haystack, child.get("keywords", set()))
        if score > best_child_score:
            best_child = child
            best_child_score = score

    return best_branch.get("tag"), best_child.get("tag") if best_child else None


def _build_metadata_tag_taxonomy(
    document: dict, parsed_tags, excerpt: str
) -> list[str]:
    parsed_tags = _clean_metadata_list(parsed_tags)
    fallback_tags = _extract_keyword_tags(
        " ".join(
            [
                document.get("title") or "",
                document.get("description") or "",
                document.get("summary") or "",
                excerpt or "",
            ]
        ),
        limit=12,
    )

    domain_tag, branch_tag = _select_metadata_tag_branch(document, parsed_tags, excerpt)
    document_type_tag = _normalize_metadata_tag(
        METADATA_DOCUMENT_TYPE_TAG_MAP.get(
            _fold_metadata_text(document.get("document_type")),
            document.get("document_type"),
        )
    )

    ordered_candidates = [
        domain_tag,
        document_type_tag,
        branch_tag,
        *parsed_tags,
        *fallback_tags,
        *_clean_metadata_list(document.get("tags")),
        *[entity.lower() for entity in _extract_named_entities(excerpt, limit=8)],
        *METADATA_TAG_PADDING,
    ]

    tags = []
    seen = set()
    for candidate in ordered_candidates:
        tag = _normalize_metadata_tag(candidate)
        if not tag:
            continue
        key = _fold_metadata_text(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= METADATA_TAG_COUNT:
            break

    return tags


def _normalize_filename_title(value: Optional[str]) -> Optional[str]:
    text = _clean_metadata_string(value)
    if not text:
        return None

    text = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", text)
    text = text.replace("_", " ").replace("-", " ").replace(".", " ")
    return normalize_text(text)


def _build_fallback_title(document: dict, excerpt: str) -> str:
    hints = _extract_bibliographic_hints(
        excerpt,
        {**document, "author": None},
        include_existing_metadata=True,
    )
    title = first_clean_text(hints.get("title"), document.get("title"))
    filename_title = _normalize_filename_title(
        document.get("source") or document.get("title")
    )
    if title:
        folded_title = _fold_metadata_text(title)
        folded_filename = _fold_metadata_text(filename_title)
        if folded_title not in {"documento", "untitled", "sem titulo"} and (
            not folded_filename or folded_title != folded_filename
        ):
            return title[:180]

    if filename_title and _fold_metadata_text(filename_title) not in {
        "documento",
        "untitled",
        "sem titulo",
    } and not re.fullmatch(
        r"(doc|docs|documento|documentos|arquivo|file)?\s*\d+",
        _fold_metadata_text(filename_title),
    ):
        return filename_title[:180]

    keywords = _extract_keyword_tags(excerpt or "", limit=3)
    document_type = (
        METADATA_DOCUMENT_TYPE_TAG_MAP.get(
            _fold_metadata_text(document.get("document_type")),
            first_clean_text(document.get("document_type")),
        )
        or "documento"
    )
    if keywords:
        base_title = f"{document_type.capitalize()} sobre {', '.join(keywords)}"
    else:
        base_title = document_type.capitalize()

    short_id = (document.get("id") or "")[:8]
    if short_id:
        base_title = f"{base_title} {short_id}"
    return normalize_text(base_title) or "Documento"


def _build_document_excerpt(document_text: str, max_chars: int = 6000) -> str:
    text = _clean_metadata_multiline_text(document_text) or ""
    if not text:
        return ""

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(
            r"\n\s*\n|(?<=[.!?])\s+(?=[A-ZÁÀÃÂÉÊÍÓÔÕÚ0-9])",
            text,
        )
        if normalize_text(paragraph)
    ]

    if not paragraphs:
        return text[:max_chars]

    def is_metadata_noise_paragraph(paragraph: str) -> bool:
        normalized = normalize_text(paragraph) or ""
        lowered = normalized.lower()
        if not normalized:
            return True

        noise_hits = sum(1 for token in METADATA_NOISE_TOKENS if token in lowered)
        colon_count = normalized.count(":")
        digit_ratio = len(re.findall(r"\d", normalized)) / max(len(normalized), 1)
        punctuation_ratio = len(re.findall(r"[^\w\sÀ-ÿ]", normalized, re.UNICODE)) / max(
            len(normalized), 1
        )

        if noise_hits >= 1:
            return True
        if "©" in normalized:
            return True
        if colon_count >= 3 and len(normalized) < 420:
            return True
        if digit_ratio > 0.14 and punctuation_ratio > 0.08:
            return True
        if re.search(r"\b(?:cm|mm|px)\b", lowered) and digit_ratio > 0.08:
            return True
        if lowered.count(" edição ") >= 1 and lowered.count(" autor ") >= 1:
            return True
        return False

    scored_paragraphs = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        normalized = normalize_text(paragraph) or ""
        letters = len(re.findall(r"[A-Za-zÀ-ÿ]", normalized))
        if letters < 24:
            continue
        if is_metadata_noise_paragraph(normalized):
            continue
        penalty = len(re.findall(r"[^\w\sÀ-ÿ]", normalized, re.UNICODE))
        sentence_count = max(1, len(re.split(r"(?<=[.!?])\s+", normalized)))
        colon_penalty = normalized.count(":") * 12
        sentence_bonus = min(sentence_count, 4) * 18
        score = letters - (penalty * 2) - colon_penalty + sentence_bonus
        scored_paragraphs.append((score, paragraph_index, normalized))

    selected: list[str] = []
    current_length = 0

    best_paragraphs = sorted(
        sorted(scored_paragraphs, key=lambda item: item[0], reverse=True)[:8],
        key=lambda item: item[1],
    )
    for _, _, paragraph in best_paragraphs:
        if paragraph in selected:
            continue
        if current_length >= max_chars:
            break
        selected.append(paragraph)
        current_length += len(paragraph) + 2

    excerpt = "\n\n".join(selected).strip()
    if not excerpt:
        excerpt = text[:max_chars]
    return excerpt[:max_chars]


def _extract_keyword_tags(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{3,}", (text or "").lower())
    filtered = [word for word in words if word not in METADATA_STOPWORDS]
    if not filtered:
        return []

    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(limit)]


def _extract_named_entities(text: str, limit: int = 8) -> list[str]:
    matches = re.findall(
        r"\b(?:[A-ZÁÀÃÂÉÊÍÓÔÕÚ][A-Za-zÀ-ÿ'-]+(?:\s+[A-ZÁÀÃÂÉÊÍÓÔÕÚ][A-Za-zÀ-ÿ'-]+){0,3})",
        text or "",
    )
    entities = []
    seen = set()
    for match in matches:
        normalized = _clean_metadata_string(match)
        if not normalized or normalized.lower() in METADATA_STOPWORDS:
            continue
        if " " not in normalized and not normalized.isupper():
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(normalized)
        if len(entities) >= limit:
            break
    return entities


def _fallback_summary_from_excerpt(excerpt: str, max_chars: int) -> Optional[str]:
    clean_excerpt = _clean_metadata_string(excerpt)
    if not clean_excerpt:
        return None

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", clean_excerpt)
        if normalize_text(sentence)
    ]
    if not sentences:
        return clean_excerpt[:max_chars].rstrip()

    keyword_weights = Counter(_extract_keyword_tags(clean_excerpt, limit=16))
    scored = []
    for index, sentence in enumerate(sentences):
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{2,}", sentence.lower())
        if not words:
            continue
        score = sum(keyword_weights.get(word, 0) for word in words)
        score += min(len(words), 24) / 24
        scored.append((score, index, sentence))

    if not scored:
        return " ".join(sentences[:2])[:max_chars].rstrip()

    selected = sorted(
        sorted(scored, key=lambda item: item[0], reverse=True)[:2],
        key=lambda item: item[1],
    )
    summary = " ".join(sentence for _, _, sentence in selected)
    summary = normalize_text(summary) or clean_excerpt
    return summary[:max_chars].rstrip()


def _build_fallback_description(document: dict, excerpt: str) -> Optional[str]:
    hints = _extract_bibliographic_hints(excerpt, document)
    title = first_clean_text(hints.get("title"), document.get("title"))
    author = first_clean_text(hints.get("author"), document.get("author"))
    edition = first_clean_text(hints.get("edition"))
    document_type = first_clean_text(document.get("document_type")) or "documento"
    topics = _extract_keyword_tags(excerpt or title or "", limit=3)
    collections = [
        item.get("name")
        for item in ensure_list(document.get("collections"))
        if isinstance(item, dict) and item.get("name")
    ]

    if title and author:
        description = f"{document_type.capitalize()} sobre {title}, de {author}."
    elif title and edition:
        description = f"{document_type.capitalize()} com edição de {title}."
    elif topics:
        if len(topics) == 1:
            description = f"{document_type.capitalize()} sobre {topics[0]}."
        elif len(topics) == 2:
            description = f"{document_type.capitalize()} sobre {topics[0]} e {topics[1]}."
        else:
            description = (
                f"{document_type.capitalize()} sobre {topics[0]}, {topics[1]} e {topics[2]}."
            )
    elif title:
        description = f"{document_type.capitalize()} relacionado a {title}."
    elif collections:
        description = f"{document_type.capitalize()} associado a {collections[0]}."
    else:
        description = f"{document_type.capitalize()} preparado para consulta e recuperação."

    return _truncate_metadata_words(description)


def _extract_bibliographic_hints(
    text: str, document: dict, include_existing_metadata: bool = True
) -> dict:
    normalized = normalize_text(text) or ""
    hints = {
        "title": None,
        "author": None,
        "edition": None,
    }

    for key, patterns in METADATA_FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(normalized)
            if not match:
                continue
            value = _clean_metadata_string(match.group(1))
            if not value:
                continue
            value = re.split(r"\s{2,}|(?=www\.|https?://|©)", value)[0].strip(" .;-")
            if value and not is_likely_garbled_text(value):
                hints[key] = value[:180]
                break
        if hints[key]:
            continue

    if include_existing_metadata and not hints["title"]:
        hints["title"] = first_clean_text(document.get("title"))
    if include_existing_metadata and not hints["author"]:
        hints["author"] = first_clean_text(document.get("author"))

    return hints


def _build_fallback_summary(document: dict, excerpt: str) -> Optional[str]:
    hints = _extract_bibliographic_hints(excerpt, document)
    title = first_clean_text(hints.get("title"), document.get("title"))
    author = first_clean_text(hints.get("author"), document.get("author"))
    edition = first_clean_text(hints.get("edition"))
    summary = _fallback_summary_from_excerpt(excerpt, 2400)

    if summary and not _looks_like_excerpt_copy(summary, excerpt):
        return _truncate_metadata_words(summary)

    if title and author and edition:
        return _truncate_metadata_words(
            f"Documento editorial dedicado a {title}, de {author}, publicado em {edition}. "
            "Reúne conteúdo preparado para consulta, estudo e recuperação no workspace."
        )

    if title and author:
        return _truncate_metadata_words(
            f"Documento dedicado a {title}, de {author}, com material preparado para leitura, busca e contextualização."
        )

    if title:
        return _truncate_metadata_words(
            f"Documento centrado em {title}, organizado para consulta, recuperação de informação e uso como contexto em conversa."
        )

    return summary


def _looks_like_excerpt_copy(candidate: Optional[str], excerpt: str) -> bool:
    normalized_candidate = _clean_metadata_string(candidate)
    normalized_excerpt = _clean_metadata_string(excerpt)

    if not normalized_candidate or not normalized_excerpt:
        return False

    if len(normalized_candidate) < 48:
        return False

    excerpt_prefix = normalized_excerpt[: max(len(normalized_candidate) + 80, 220)]
    if normalized_candidate in excerpt_prefix:
        return True

    ratio = SequenceMatcher(None, normalized_candidate, excerpt_prefix[: len(normalized_candidate)]).ratio()
    return ratio >= 0.88


def _sanitize_metadata_payload(
    document: dict,
    parsed: dict,
    excerpt: str,
    author_excerpt: str = "",
) -> dict:
    fallback = _build_metadata_fallback_payload(
        document,
        "",
        excerpt,
        author_excerpt=author_excerpt,
    )
    title = first_clean_text(parsed.get("title"), fallback.get("title"))
    description = _truncate_metadata_words(
        first_clean_text(
            parsed.get("description"),
            fallback.get("description"),
            document.get("description"),
        )
    )
    summary = _truncate_metadata_words(
        first_clean_text(
            parsed.get("summary"),
            fallback.get("summary"),
            document.get("summary"),
            description,
        )
    )

    if _looks_like_excerpt_copy(description, excerpt):
        description = fallback.get("description")

    if _looks_like_excerpt_copy(summary, excerpt):
        summary = fallback.get("summary")

    tags = _build_metadata_tag_taxonomy(
        document,
        parsed.get("tags") or fallback.get("tags"),
        excerpt,
    )
    entities = _clean_metadata_list(parsed.get("entities")) or _clean_metadata_list(
        fallback.get("entities")
    )

    return {
        "title": title or fallback.get("title") or document.get("title"),
        "description": description or fallback.get("description"),
        "summary": summary or fallback.get("summary") or description,
        "tags": tags[:METADATA_TAG_COUNT],
        "entities": entities[:12],
        "author": _clean_metadata_author(parsed.get("author"))
        or _clean_metadata_author(fallback.get("author")),
        "source": first_clean_text(parsed.get("source"), fallback.get("source"), document.get("source")),
        "language": _normalize_language_code(
            first_clean_text(
                parsed.get("language"),
                fallback.get("language"),
                document.get("language"),
            ),
            fallback_text=excerpt,
        ),
        "document_type": first_clean_text(
            parsed.get("document_type"),
            fallback.get("document_type"),
            document.get("document_type"),
        ),
        "suggested_collection_hints": _clean_metadata_list(
            parsed.get("suggested_collection_hints")
        )
        or _clean_metadata_list(fallback.get("suggested_collection_hints")),
    }


def _build_metadata_fallback_payload(
    document: dict,
    raw_content: str,
    document_excerpt: str = "",
    *,
    author_excerpt: str = "",
) -> dict:
    normalized_raw = _clean_metadata_string(raw_content) or ""
    excerpt = _build_document_excerpt(document_excerpt or normalized_raw or "")
    author_hint = _extract_bibliographic_hints(
        author_excerpt or excerpt,
        {},
        include_existing_metadata=False,
    ).get("author")
    document_context = {
        **document,
        "author": author_hint,
    }

    description = first_clean_text(
        document_context.get("description"),
        _build_fallback_description(document_context, excerpt),
        document.get("title"),
    )
    summary = first_clean_text(
        document_context.get("summary"),
        _build_fallback_summary(document_context, excerpt),
        description,
    )

    existing_tags = _clean_metadata_list(document.get("tags"))
    fallback_tags = _build_metadata_tag_taxonomy(
        document_context,
        existing_tags or _extract_keyword_tags(excerpt or document.get("title") or ""),
        excerpt,
    )

    fallback_entities = _clean_metadata_list(document.get("entities")) or _extract_named_entities(
        excerpt
    )

    return {
        "title": _build_fallback_title(document_context, author_excerpt or excerpt),
        "description": _truncate_metadata_words(description),
        "summary": _truncate_metadata_words(summary) or _truncate_metadata_words(description),
        "tags": fallback_tags[:METADATA_TAG_COUNT],
        "entities": fallback_entities[:12],
        "author": first_clean_text(author_hint),
        "source": first_clean_text(document.get("source")),
        "language": _normalize_language_code(
            first_clean_text(document.get("language")),
            fallback_text=excerpt,
        ),
        "document_type": first_clean_text(document.get("document_type")),
        "suggested_collection_hints": [
            item.get("name")
            for item in (document.get("collections") or [])
            if item.get("name")
        ][:5],
    }


async def _normalize_metadata_response_to_json(
    request: Request,
    user,
    model_id: str,
    field_name: str,
    raw_content: str,
):
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Converta a resposta abaixo em um JSON válido contendo apenas a chave '{field_name}'. "
                    "Não inclua markdown nem explicações."
                ),
            },
            {"role": "user", "content": raw_content or ""},
        ],
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": _build_metadata_field_response_schema(field_name),
        },
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": "document_metadata_json_normalization",
            "task_body": {"field_name": field_name},
        },
    }

    response = await generate_chat_completion(request, form_data=payload, user=user)
    return _extract_completion_content(response)


def _build_metadata_field_response_schema(field_name: str) -> dict:
    if field_name in {"tags", "entities", "suggested_collection_hints"}:
        field_schema = {"type": "array", "items": {"type": "string"}}
        if field_name == "tags":
            field_schema["minItems"] = METADATA_TAG_COUNT
            field_schema["maxItems"] = METADATA_TAG_COUNT
        elif field_name == "entities":
            field_schema["maxItems"] = 12
        else:
            field_schema["maxItems"] = 5
    elif field_name in {"author", "source", "language", "document_type"}:
        field_schema = {"type": ["string", "null"]}
    else:
        field_schema = {"type": "string"}

    return {
        "name": f"document_metadata_{field_name}",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {field_name: field_schema},
            "required": [field_name],
        },
    }


def _build_metadata_field_prompts(
    field_name: str,
    sanitized_document: dict,
    uploader_context: dict,
    user_instruction: Optional[str],
    document_excerpt: str,
    first_pages_excerpt: str,
) -> tuple[str, str]:
    field_document = dict(sanitized_document)
    if field_name == "author":
        field_document["author"] = None
        excerpt_label = "Excerto das 4 primeiras páginas"
        excerpt_text = first_pages_excerpt or document_excerpt
    elif field_name == "title":
        excerpt_label = "Excerto das primeiras páginas"
        excerpt_text = first_pages_excerpt or document_excerpt
    else:
        excerpt_label = "Excerto do documento"
        excerpt_text = document_excerpt or first_pages_excerpt

    system_prompt = f"""
Você gera um único campo de metadados por vez para um workspace de documentos.
Retorne somente JSON válido com a chave "{field_name}".
Não inclua markdown, comentários ou campos extras.
Baseie a resposta apenas no excerto e nos metadados atuais fornecidos.
{METADATA_FIELD_PROMPTS[field_name]}
""".strip()

    user_prompt = f"""
Campo solicitado: {field_name}

Metadados atuais:
{json.dumps(field_document, ensure_ascii=False)}

Contexto de upload:
{json.dumps(uploader_context, ensure_ascii=False)}

Instrução adicional do usuário:
{user_instruction or "Gere um metadado útil, consistente e bom para recuperação e navegação."}

{excerpt_label}:
{excerpt_text or (first_clean_text(field_document.get("title")) or "Documento sem excerto disponível.")}
""".strip()

    return system_prompt, user_prompt


async def _generate_document_metadata_field_value(
    request: Request,
    user,
    model_id: str,
    models: dict,
    field_name: str,
    sanitized_document: dict,
    uploader_context: dict,
    user_instruction: Optional[str],
    document_excerpt: str,
    first_pages_excerpt: str,
    fallback_value,
):
    system_prompt, user_prompt = _build_metadata_field_prompts(
        field_name,
        sanitized_document,
        uploader_context,
        user_instruction,
        document_excerpt,
        first_pages_excerpt,
    )

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": _build_metadata_field_response_schema(field_name),
        },
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": "document_metadata_field_suggestion",
            "task_body": {
                "document_id": sanitized_document.get("id"),
                "field_name": field_name,
                "instruction": user_instruction,
            },
        },
    }

    if models.get(model_id, {}).get("owned_by") == "ollama":
        payload["max_tokens"] = 900
        payload["options"] = {
            **(payload.get("options") or {}),
            "temperature": 0.1,
        }
    else:
        payload["max_completion_tokens"] = 900
        payload["temperature"] = 0.1

    content = ""
    prepared_payload = payload

    try:
        try:
            prepared_payload = await asyncio.wait_for(
                process_pipeline_inlet_filter(request, payload, user, models),
                timeout=METADATA_PIPELINE_TIMEOUT_SECONDS,
            )
        except Exception:
            log.warning(
                "Document metadata inlet filter failed for field=%s document_id=%s",
                field_name,
                sanitized_document.get("id"),
                exc_info=True,
            )

        response = await asyncio.wait_for(
            generate_chat_completion(request, form_data=prepared_payload, user=user),
            timeout=METADATA_GENERATION_TIMEOUT_SECONDS,
        )
        content = _extract_completion_content(response)
        if is_likely_garbled_text(content):
            raise ValueError(f"Model returned garbled output for {field_name}")
        parsed = _extract_json_object(content)
        return field_name, parsed.get(field_name, fallback_value)
    except Exception:
        if content:
            try:
                normalized_content = await asyncio.wait_for(
                    _normalize_metadata_response_to_json(
                        request,
                        user,
                        model_id,
                        field_name,
                        content,
                    ),
                    timeout=min(8, METADATA_GENERATION_TIMEOUT_SECONDS),
                )
                if is_likely_garbled_text(normalized_content):
                    raise ValueError(
                        f"Normalized metadata output was garbled for {field_name}"
                    )
                parsed = _extract_json_object(normalized_content)
                return field_name, parsed.get(field_name, fallback_value)
            except Exception:
                pass

    return field_name, fallback_value


async def _generate_document_metadata_suggestion(
    request: Request,
    file,
    user,
    form_data: DocumentMetadataSuggestForm,
    db: Session,
) -> DocumentMetadataSuggestionResponse:
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    model_id = _resolve_metadata_model_id(request, user, form_data.model)
    models = getattr(request.app.state, "MODELS", {}) or {}

    collections = Knowledges.get_knowledges_by_file_id(file.id, db=db)
    document = build_document_payload(file, collections=collections)
    owner = Users.get_user_by_id(file.user_id, db=db)

    document_chunks = _get_document_chunks(file.id)
    document_text = _get_document_text(file, chunks=document_chunks)
    document_excerpt = _build_document_excerpt(document_text)
    first_pages_excerpt = _build_first_pages_excerpt(
        document_chunks,
        document_text,
    )

    sanitized_document = {
        "id": document.get("id"),
        "title": first_clean_text(document.get("title")) or file.filename,
        "description": first_clean_text(document.get("description")),
        "summary": first_clean_text(document.get("summary")),
        "document_type": first_clean_text(document.get("document_type"))
        or guess_document_type(file),
        "document_status": first_clean_text(document.get("document_status"))
        or DEFAULT_DOCUMENT_STATUS,
        "source": first_clean_text(document.get("source")) or file.filename,
        "author": first_clean_text(document.get("author")),
        "language": first_clean_text(document.get("language")),
        "tags": _clean_metadata_list(document.get("tags")),
        "entities": _clean_metadata_list(document.get("entities")),
        "collections": document.get("collections") or [],
    }
    uploader_context = {
        "added_by": {
            "id": owner.id if owner else file.user_id,
            "name": owner.name if owner else None,
            "email": owner.email if owner else None,
        },
        "added_at": normalize_timestamp(file.created_at),
    }

    fallback_payload = _build_metadata_fallback_payload(
        sanitized_document,
        "",
        document_excerpt or json.dumps(sanitized_document, ensure_ascii=False),
        author_excerpt=first_pages_excerpt,
    )

    if not model_id:
        parsed = _sanitize_metadata_payload(
            sanitized_document,
            fallback_payload,
            document_excerpt,
            first_pages_excerpt,
        )
        return DocumentMetadataSuggestionResponse(
            document_id=file.id,
            model="dochat-fallback",
            title=parsed.get("title"),
            description=parsed.get("description"),
            summary=parsed.get("summary"),
            tags=ensure_list(parsed.get("tags")),
            entities=ensure_list(parsed.get("entities")),
            author=parsed.get("author"),
            source=parsed.get("source"),
            language=parsed.get("language"),
            document_type=parsed.get("document_type"),
            document_status=sanitized_document.get("document_status"),
            suggested_collection_hints=ensure_list(parsed.get("suggested_collection_hints")),
            provenance={
                **uploader_context,
                "generated_at": int(time.time()),
            },
        )

    field_names = list(METADATA_FIELD_PROMPTS.keys())
    if models.get(model_id, {}).get("owned_by") == "ollama":
        field_results = [
            await _generate_document_metadata_field_value(
                request,
                user,
                model_id,
                models,
                field_name,
                sanitized_document,
                uploader_context,
                form_data.instruction,
                document_excerpt,
                first_pages_excerpt,
                fallback_payload.get(field_name),
            )
            for field_name in field_names
        ]
    else:
        field_results = await asyncio.gather(
            *[
                _generate_document_metadata_field_value(
                    request,
                    user,
                    model_id,
                    models,
                    field_name,
                    sanitized_document,
                    uploader_context,
                    form_data.instruction,
                    document_excerpt,
                    first_pages_excerpt,
                    fallback_payload.get(field_name),
                )
                for field_name in field_names
            ]
        )
    parsed = _sanitize_metadata_payload(
        sanitized_document,
        {field_name: value for field_name, value in field_results},
        document_excerpt,
        first_pages_excerpt,
    )

    return DocumentMetadataSuggestionResponse(
        document_id=file.id,
        model=model_id,
        title=parsed.get("title"),
        description=parsed.get("description"),
        summary=parsed.get("summary"),
        tags=ensure_list(parsed.get("tags")),
        entities=ensure_list(parsed.get("entities")),
        author=parsed.get("author"),
        source=parsed.get("source"),
        language=parsed.get("language"),
        document_type=parsed.get("document_type"),
        document_status=sanitized_document.get("document_status"),
        suggested_collection_hints=ensure_list(parsed.get("suggested_collection_hints")),
        provenance={
            **uploader_context,
            "generated_at": int(time.time()),
        },
    )


@router.get("/collections", response_model=list[DocumentCollectionResponse])
async def get_document_collections(
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    collection_counts: dict[str, dict] = {}
    for entry in collect_accessible_documents(user, db=db):
        payload = build_document_payload(entry["file"], entry.get("collections"))
        for collection in payload.get("collections") or []:
            if not collection or not collection.get("id"):
                continue
            current = collection_counts.get(collection["id"]) or {
                **collection,
                "count": 0,
            }
            current["count"] += 1
            collection_counts[collection["id"]] = current

    return [
        DocumentCollectionResponse(**collection)
        for collection in sorted(
            collection_counts.values(),
            key=lambda item: ((item.get("name") or "").lower(), item.get("id")),
        )
    ]


@router.get("/", response_model=DocumentListResponse)
async def get_documents(
    query: Optional[str] = None,
    collection: Optional[str] = None,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    document_type: Optional[str] = None,
    document_status: Optional[str] = None,
    locked: Optional[bool] = None,
    archived: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(60, ge=1, le=200),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    items = []
    for entry in collect_accessible_documents(user, db=db):
        document = build_document_payload(entry["file"], entry.get("collections"))
        if not document_matches_filters(
            document,
            query=query,
            collection=collection,
            tags=tags,
            source=source,
            document_type=document_type,
            document_status=document_status,
            locked=locked,
            archived=archived,
        ):
            continue
        items.append(DocumentResponse(**document))

    items.sort(key=lambda item: (item.updated_at or 0, item.id), reverse=True)
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    return DocumentListResponse(items=items[start:end], total=total)


@router.get("/{id}/processing", response_model=DocumentProcessingResponse)
async def get_document_processing_state(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    document = _build_document_response(file, user, db)
    data = file.data or {}
    return DocumentProcessingResponse(
        id=id,
        processing_status=document.processing_status,
        embedding_status=document.embedding_status,
        chunk_count=document.chunk_count,
        last_processed_at=document.last_processed_at,
        error=data.get("error"),
    )


@router.get("/{id}/versions", response_model=list[DocumentVersionResponse])
async def get_document_versions(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _ensure_current_document_revision(file, db)

    current_revision = max(
        int(normalize_document_version_control(file.meta or {}).get("revision") or 1), 1
    )
    revisions = FileRevisions.get_revisions_by_file_id(id, db=db)
    return [
        _build_document_version_response(revision, current_revision)
        for revision in revisions
    ]


@router.get("/{id}/versions/compare", response_model=DocumentVersionCompareResponse)
async def compare_document_versions(
    id: str,
    base_revision: int = Query(..., ge=1),
    target_revision: int = Query(..., ge=1),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _ensure_current_document_revision(file, db)

    current_revision = max(
        int(normalize_document_version_control(file.meta or {}).get("revision") or 1), 1
    )
    base = FileRevisions.get_revision_by_file_id_and_revision(id, base_revision, db=db)
    target = FileRevisions.get_revision_by_file_id_and_revision(id, target_revision, db=db)

    if not base or not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )

    diff_lines = _build_document_version_diff(base.content or "", target.content or "")
    added_count = sum(1 for line in diff_lines if line.type == "added")
    removed_count = sum(1 for line in diff_lines if line.type == "removed")

    return DocumentVersionCompareResponse(
        base_version=_build_document_version_response(base, current_revision),
        target_version=_build_document_version_response(target, current_revision),
        summary={
            "added_lines": added_count,
            "removed_lines": removed_count,
            "changed": added_count + removed_count > 0,
        },
        lines=diff_lines,
    )


@router.get("/{id}", response_model=DocumentResponse)
async def get_document_by_id(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    return _build_document_response(file, user, db, include_related=True)


@router.post("/{id}/archive", response_model=DocumentResponse)
async def archive_document_by_id(
    id: str,
    form_data: Optional[DocumentArchiveForm] = None,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)

    if form_data and form_data.is_archived is not None:
        file = Files.set_file_archived_state_by_id(id, form_data.is_archived, db=db)
    else:
        file = Files.toggle_file_archive_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Unable to update document archive state"),
        )
    return _build_document_response(file, user, db)


@router.post("/{id}/metadata/update", response_model=DocumentResponse)
async def update_document_metadata(
    id: str,
    form_data: DocumentMetadataUpdateForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)
    _check_document_not_locked(file)
    _check_document_checkout_state(file, user)
    file = _update_document_metadata(file, form_data, user, db)
    return _build_document_response(file, user, db)


@router.post("/{id}/metadata/suggest", response_model=DocumentMetadataSuggestionResponse)
async def suggest_document_metadata(
    request: Request,
    id: str,
    form_data: DocumentMetadataSuggestForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "read", db)

    try:
        return await _generate_document_metadata_suggestion(
            request, file, user, form_data, db
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/{id}/metadata/refresh", response_model=DocumentResponse)
async def refresh_document_metadata(
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)
    _check_document_not_locked(file)
    _check_document_checkout_state(file, user)
    file = _refresh_document_metadata(file, user, db)
    return _build_document_response(file, user, db)


@router.post("/{id}/embeddings/refresh", response_model=DocumentResponse)
async def refresh_document_embeddings(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)
    _check_document_not_locked(file)
    _reprocess_document(request, file, user, db)
    file = Files.get_file_by_id(id, db=db)
    return _build_document_response(file, user, db)


@router.post("/{id}/reprocess", response_model=DocumentResponse)
async def reprocess_document_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)
    _check_document_not_locked(file)
    _check_document_checkout_state(file, user)
    file = _refresh_document_metadata(file, user, db)
    _reprocess_document(request, file, user, db)
    file = Files.get_file_by_id(id, db=db)
    return _build_document_response(file, user, db)


@router.post("/{id}/copy", response_model=DocumentResponse)
async def create_editable_document_copy(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    file = _get_document_or_404(id, user, db)
    _check_document_access(file, user, "write", db)
    copied_file = _create_editable_document_copy(request, file, user, db)
    return _build_document_response(copied_file, user, db)
