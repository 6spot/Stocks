import hashlib
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stocks.embedding_space import VECTOR_DIMENSIONS
from stocks.models import (
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentCollectionState,
    DocumentEmbeddingState,
    DocumentParseBatch,
    DocumentParseState,
    DocumentProcessingState,
    Source,
)
from stocks.changes import record_change
from stocks.processing.stages import (
    STAGE_INDEX,
    STAGE_PARSE,
    ProcessingError,
    StageClaim,
    StageOutcome,
    apply_stage_outcome,
    assert_stage_owned,
    create_stage_states,
)
from stocks.s3 import read_snapshot, store_snapshot
from stocks.settings import Settings


@dataclass(frozen=True)
class DocumentInput:
    source_id: str
    dedupe_key: str
    title: str
    payload: bytes
    content_type: str
    collected_at: datetime
    original_url: str | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    summary: str | None = None
    completeness: str = "complete"
    fetch_error: dict | None = None


@dataclass(frozen=True)
class IngestResult:
    document: Document
    created: bool


@dataclass(frozen=True)
class ParseChunkInput:
    text: str
    locator: dict


@dataclass(frozen=True)
class ParseBatchResult:
    batch: DocumentParseBatch
    state: DocumentParseState
    reused: bool = False


@dataclass(frozen=True)
class EmbeddingInput:
    chunk_id: UUID
    vector: tuple[float, ...]


@dataclass(frozen=True)
class SearchFilters:
    source_ids: tuple[str, ...] = ()
    published_from: datetime | None = None
    published_to: datetime | None = None
    collected_from: datetime | None = None
    collected_to: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "published_from",
            "published_to",
            "collected_from",
            "collected_to",
        ):
            value = getattr(self, field_name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from >= self.published_to
        ):
            raise ValueError("published_from must be earlier than published_to")
        if (
            self.collected_from is not None
            and self.collected_to is not None
            and self.collected_from >= self.collected_to
        ):
            raise ValueError("collected_from must be earlier than collected_to")
        if any(not source_id.strip() for source_id in self.source_ids):
            raise ValueError("source_ids must not contain empty values")


@dataclass(frozen=True)
class SearchHit:
    chunk_id: UUID
    document_id: UUID
    parse_batch_id: UUID
    position: int
    text: str
    locator: dict
    title: str
    source_id: str
    version: int
    published_at: datetime | None
    source_updated_at: datetime | None
    collected_at: datetime
    completeness: str
    similarity: float | None


@dataclass(frozen=True)
class SearchCoverage:
    current_documents: int
    indexed_documents: int
    failed_documents: int
    current_chunks: int
    indexed_chunks: int

    @property
    def unindexed_chunks(self) -> int:
        return self.current_chunks - self.indexed_chunks

    @property
    def documents_without_complete_index(self) -> int:
        return self.current_documents - self.indexed_documents


def ingest_document(
    session: Session,
    config: Settings,
    item: DocumentInput,
    *,
    store: Callable[..., tuple[str, str]] = store_snapshot,
) -> IngestResult:
    source = session.get(Source, item.source_id)
    if source is None:
        raise ValueError(f"Unknown source: {item.source_id}")
    if not item.content_type.strip():
        raise ValueError("content_type must not be empty")
    if not item.title.strip():
        raise ValueError("title must not be empty")
    if not item.dedupe_key.strip():
        raise ValueError("dedupe_key must not be empty")
    if item.completeness not in {"complete", "partial"}:
        raise ValueError("completeness must be 'complete' or 'partial'")
    if item.completeness == "partial" and item.fetch_error is None:
        raise ValueError("partial content must include fetch_error")
    if item.completeness == "complete" and item.fetch_error is not None:
        raise ValueError("complete content must not include fetch_error")

    content_hash = hashlib.sha256(item.payload).hexdigest()
    rows = _document_rows(session, item)
    for existing in rows:
        if existing.content_hash == content_hash:
            _upsert_collection_state(session, item, existing)
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
            session.refresh(existing)
            return IngestResult(document=existing, created=False)

    try:
        object_key, stored_hash = store(
            config, item.source_id, item.payload, content_type=item.content_type
        )
    except Exception:
        session.rollback()
        raise
    if stored_hash != content_hash:
        raise RuntimeError("Stored content hash does not match the supplied bytes")

    next_version = max((row.version for row in rows), default=0) + 1
    document = Document(
        source_id=item.source_id,
        dedupe_key=item.dedupe_key,
        version=next_version,
        title=item.title,
        original_url=item.original_url,
        published_at=item.published_at,
        source_updated_at=item.source_updated_at,
        collected_at=item.collected_at,
        summary=item.summary,
        completeness=item.completeness,
        fetch_error=deepcopy(item.fetch_error),
        content_type=item.content_type,
        content_size=len(item.payload),
        content_hash=content_hash,
        snapshot_key=object_key,
        trust_tier_snapshot=source.trust_tier,
    )
    session.add(document)
    try:
        session.flush()
        _upsert_collection_state(session, item, document)
        create_stage_states(session, document.id)
        # The change record joins the same transaction as the new version, so a
        # research window can never see a Document without its change (or a
        # change pointing at a Document that is not visible yet).
        record_change(
            session,
            kind="document_ingested",
            document_id=document.id,
            recorded_at=item.collected_at,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        for existing in _document_rows(session, item):
            if existing.content_hash == content_hash:
                return IngestResult(document=existing, created=False)
        raise
    except Exception:
        session.rollback()
        raise
    session.refresh(document)
    return IngestResult(document=document, created=True)


def _upsert_collection_state(
    session: Session, item: DocumentInput, document: Document
) -> None:
    state = session.get(
        DocumentCollectionState, (item.source_id, item.dedupe_key)
    )
    if state is None:
        state = DocumentCollectionState(
            source_id=item.source_id,
            dedupe_key=item.dedupe_key,
            latest_document_id=document.id,
            completeness=item.completeness,
            fetch_error=deepcopy(item.fetch_error),
            published_at=item.published_at,
            source_updated_at=item.source_updated_at,
            last_attempted_at=item.collected_at,
        )
        session.add(state)
    else:
        state.latest_document_id = document.id
        state.completeness = item.completeness
        state.fetch_error = deepcopy(item.fetch_error)
        state.published_at = item.published_at
        state.source_updated_at = item.source_updated_at
        state.last_attempted_at = item.collected_at


def _document_rows(session: Session, item: DocumentInput) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.source_id == item.source_id,
                Document.dedupe_key == item.dedupe_key,
            )
            .order_by(Document.version.desc())
        ).all()
    )


def get_document(session: Session, document_id: UUID) -> Document | None:
    return session.get(Document, document_id)


def get_latest_document(
    session: Session, source_id: str, dedupe_key: str
) -> Document | None:
    """Return the Document actually acquired most recently.

    Version numbers only record creation order, so a reused content version can
    be older than the bytes acquired most recently. The collection state holds
    that pointer and keeps it consistent with the skip/reuse decision.
    """
    state = get_collection_state(session, source_id, dedupe_key)
    if state is None:
        return None
    return session.get(Document, state.latest_document_id)


def get_collection_state(
    session: Session, source_id: str, dedupe_key: str
) -> DocumentCollectionState | None:
    return session.get(DocumentCollectionState, (source_id, dedupe_key))


def list_document_versions(
    session: Session,
    source_id: str,
    dedupe_key: str,
    *,
    limit: int | None = None,
    after_version: int | None = None,
) -> list[Document]:
    """List immutable content versions with an optional version keyset page."""

    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if after_version is not None and after_version < 0:
        raise ValueError("after_version must be zero or greater")
    statement = (
        select(Document)
        .where(
            Document.source_id == source_id,
            Document.dedupe_key == dedupe_key,
        )
        .order_by(Document.version)
    )
    if after_version is not None:
        statement = statement.where(Document.version > after_version)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def read_document_bytes(config: Settings, document: Document) -> bytes:
    return read_snapshot(config, document.snapshot_key)


def get_embedding_state(
    session: Session, document_id: UUID
) -> DocumentEmbeddingState | None:
    return session.get(DocumentEmbeddingState, document_id)


def list_chunk_embeddings(
    session: Session,
    chunk_ids: list[UUID],
    *,
    model_name: str,
    dimensions: int,
) -> dict[UUID, tuple[float, ...]]:
    if not chunk_ids:
        return {}
    rows = session.scalars(
        select(DocumentChunkEmbedding).where(
            DocumentChunkEmbedding.chunk_id.in_(chunk_ids),
            DocumentChunkEmbedding.model_name == model_name,
            DocumentChunkEmbedding.dimensions == dimensions,
        )
    ).all()
    return {
        row.chunk_id: tuple(float(value) for value in row.embedding)
        for row in rows
    }


def persist_embedding_result(
    session: Session,
    document_id: UUID,
    *,
    parse_batch_id: UUID,
    model_alias: str,
    model_name: str,
    dimensions: int,
    status: str,
    embeddings: tuple[EmbeddingInput, ...],
    attempted_at: datetime,
    error: dict | None,
    stage_claim: StageClaim | None = None,
    stage_final_batch: bool = True,
    stage_processor_version: str | None = None,
    stage_config_fingerprint: str | None = None,
) -> DocumentEmbeddingState:
    if status not in {"completed", "failed"}:
        raise ValueError("invalid embedding status")
    if status == "failed" and error is None:
        raise ValueError("failed embedding status must include an error")
    if status == "completed" and error is not None:
        raise ValueError("completed embedding status must not include an error")
    if dimensions != VECTOR_DIMENSIONS:
        raise ValueError("Embedding dimensions do not match the configured vector space")
    if not model_alias.strip() or not model_name.strip():
        raise ValueError("Embedding model identity must not be empty")
    if attempted_at.utcoffset() is None:
        raise ValueError("attempted_at must include a timezone")

    document = session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:
        raise ValueError(f"Unknown document: {document_id}")
    if stage_claim is not None:
        # The ownership fence comes first: an attempt that was recovered and
        # re-claimed by another executor must be refused under the row lock before
        # it can write any vector or stage result.
        assert_stage_owned(session, stage_claim)
    # Sessions run with ``expire_on_commit=False``: the parse pointer read at index
    # start may be stale, so re-read it here. Every parse-state write takes the
    # Document row lock first, and a new current batch releases the dependent
    # stages in that same transaction, so this read is authoritative for the rest
    # of this transaction and a superseded batch is detectable before anything is
    # written.
    parse_state = session.scalar(
        select(DocumentParseState)
        .where(DocumentParseState.document_id == document_id)
        .execution_options(populate_existing=True)
    )
    if parse_state is None or parse_state.current_batch_id != parse_batch_id:
        if stage_claim is not None:
            # A committed new current batch always releases the stage, so this
            # attempt no longer owns it. Report that instead of a batch mismatch,
            # which would otherwise be recorded as a processor failure against
            # input somebody else is now responsible for.
            raise ProcessingError(
                "STAGE_NOT_OWNED",
                "Processing stage is not owned by this execution",
            )
        raise ValueError("Embedding batch is not the current successful parse")
    parse_batch = session.get(DocumentParseBatch, parse_batch_id)
    if (
        parse_batch is None
        or parse_batch.document_id != document_id
        or parse_batch.status != "completed"
        or parse_batch.content_hash != document.content_hash
    ):
        raise ValueError("Embedding parse batch does not match the Document")

    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.parse_batch_id == parse_batch_id)
            .order_by(DocumentChunk.position)
        ).all()
    )
    chunk_ids = {chunk.id for chunk in chunks}
    supplied_ids = [item.chunk_id for item in embeddings]
    if len(set(supplied_ids)) != len(supplied_ids):
        raise ValueError("Embedding input contains duplicate chunk IDs")
    if any(chunk_id not in chunk_ids for chunk_id in supplied_ids):
        raise ValueError("Embedding input does not belong to the current parse batch")
    if any(len(item.vector) != dimensions for item in embeddings):
        raise ValueError("Embedding dimensions do not match the configured vector space")

    if embeddings:
        statement = insert(DocumentChunkEmbedding).values(
            [
                {
                    "chunk_id": item.chunk_id,
                    "model_alias": model_alias,
                    "model_name": model_name,
                    "dimensions": dimensions,
                    "embedding": list(item.vector),
                    "indexed_at": attempted_at,
                }
                for item in embeddings
            ]
        )
        statement = statement.on_conflict_do_update(
            index_elements=["chunk_id", "model_name", "dimensions"],
            set_={
                "model_alias": statement.excluded.model_alias,
                "embedding": statement.excluded.embedding,
                "indexed_at": statement.excluded.indexed_at,
            },
        )
        session.execute(statement)
        session.flush()

    embedded_ids = set(
        session.scalars(
            select(DocumentChunkEmbedding.chunk_id).where(
                DocumentChunkEmbedding.chunk_id.in_(chunk_ids),
                DocumentChunkEmbedding.model_name == model_name,
                DocumentChunkEmbedding.dimensions == dimensions,
            )
        ).all()
    )
    if status == "completed" and embedded_ids != chunk_ids:
        raise ValueError("completed embedding status requires every current chunk")

    state = session.get(DocumentEmbeddingState, document_id)
    if state is None:
        state = DocumentEmbeddingState(
            document_id=document_id,
            status=status,
            parse_batch_id=parse_batch_id,
            model_alias=model_alias,
            model_name=model_name,
            dimensions=dimensions,
            chunk_count=len(chunks),
            indexed_count=len(embedded_ids),
            error=deepcopy(error),
            attempted_at=attempted_at,
        )
        session.add(state)
    else:
        state.status = status
        state.parse_batch_id = parse_batch_id
        state.model_alias = model_alias
        state.model_name = model_name
        state.dimensions = dimensions
        state.chunk_count = len(chunks)
        state.indexed_count = len(embedded_ids)
        state.error = deepcopy(error)
        state.attempted_at = attempted_at
    if stage_claim is not None:
        if stage_final_batch:
            # The final coverage check and the stage completion commit together,
            # so an interrupted multi-batch run keeps its vectors but never
            # claims a completed stage. The status comes from the persisted
            # coverage, not the caller.
            apply_stage_outcome(
                session,
                _index_stage_outcome(
                    stage_claim,
                    status=status,
                    error=error,
                    batch_id=parse_batch_id,
                    processor_version=stage_processor_version,
                    config_fingerprint=stage_config_fingerprint,
                ),
                now=attempted_at,
            )
        # An intermediate batch only commits its vectors; the ownership check at
        # the start of this transaction already fenced the writer, so an
        # interrupted stage that was recovered and re-claimed by another
        # executor cannot keep writing results under the old token.
    if stage_final_batch and status == "completed":
        # Vectors for the current batch make the Document semantically
        # researchable, which is a change research must not miss.
        record_change(
            session,
            kind="document_indexed",
            document_id=document_id,
            recorded_at=attempted_at,
        )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(state)
    return state


def _search_filters(filters: SearchFilters) -> list:
    conditions = []
    if filters.source_ids:
        conditions.append(Document.source_id.in_(filters.source_ids))
    if filters.published_from is not None:
        conditions.append(Document.published_at >= filters.published_from)
    if filters.published_to is not None:
        conditions.append(Document.published_at < filters.published_to)
    if filters.collected_from is not None:
        conditions.append(Document.collected_at >= filters.collected_from)
    if filters.collected_to is not None:
        conditions.append(Document.collected_at < filters.collected_to)
    return conditions


def _current_chunk_query(filters: SearchFilters):
    return (
        select(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(
            DocumentParseState,
            and_(
                DocumentParseState.document_id == DocumentChunk.document_id,
                DocumentParseState.current_batch_id
                == DocumentChunk.parse_batch_id,
            ),
        )
        .where(*_search_filters(filters))
    )


def _search_hit(
    chunk: DocumentChunk,
    document: Document,
    *,
    similarity: float | None,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.id,
        document_id=document.id,
        parse_batch_id=chunk.parse_batch_id,
        position=chunk.position,
        text=chunk.text,
        locator=deepcopy(chunk.locator),
        title=document.title,
        source_id=document.source_id,
        version=document.version,
        published_at=document.published_at,
        source_updated_at=document.source_updated_at,
        collected_at=document.collected_at,
        completeness=document.completeness,
        similarity=similarity,
    )


def search_chunks(
    session: Session,
    query_vector: tuple[float, ...],
    *,
    model_name: str,
    dimensions: int,
    filters: SearchFilters,
    limit: int,
) -> list[SearchHit]:
    if len(query_vector) != dimensions:
        raise ValueError("Query vector dimensions do not match the embedding space")
    if dimensions != VECTOR_DIMENSIONS:
        raise ValueError("Embedding dimensions do not match the configured vector space")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")

    distance = DocumentChunkEmbedding.embedding.cosine_distance(
        list(query_vector)
    ).label("distance")
    # pgvector registers its HNSW settings when the library is loaded.
    session.execute(text("LOAD 'vector'"))
    session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    rows = session.execute(
        select(
            DocumentChunk,
            Document,
            distance,
        )
        .select_from(DocumentChunkEmbedding)
        .join(
            DocumentChunk,
            DocumentChunkEmbedding.chunk_id == DocumentChunk.id,
        )
        .join(
            Document,
            Document.id == DocumentChunk.document_id,
        )
        .join(
            DocumentParseState,
            and_(
                DocumentParseState.document_id == DocumentChunk.document_id,
                DocumentParseState.current_batch_id
                == DocumentChunk.parse_batch_id,
            ),
        )
        .where(
            DocumentChunkEmbedding.model_name == model_name,
            DocumentChunkEmbedding.dimensions == dimensions,
            *_search_filters(filters),
        )
        .order_by(
            distance,
            DocumentChunk.document_id,
            DocumentChunk.position,
        )
        .limit(limit)
    ).all()
    return [
        _search_hit(
            chunk,
            document,
            similarity=max(-1.0, min(1.0, 1.0 - float(row_distance))),
        )
        for chunk, document, row_distance in rows
    ]


def get_chunk_context(
    session: Session,
    chunk_id: UUID,
    *,
    radius: int = 1,
) -> list[SearchHit]:
    if not 0 <= radius <= 5:
        raise ValueError("radius must be between 0 and 5")
    anchor = session.get(DocumentChunk, chunk_id)
    if anchor is None:
        raise ValueError(f"Unknown chunk: {chunk_id}")
    parse_state = session.get(DocumentParseState, anchor.document_id)
    if (
        parse_state is None
        or parse_state.current_batch_id != anchor.parse_batch_id
    ):
        raise ValueError("Chunk is not part of the current successful parse batch")
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.parse_batch_id == anchor.parse_batch_id,
                DocumentChunk.position >= anchor.position - radius,
                DocumentChunk.position <= anchor.position + radius,
            )
            .order_by(DocumentChunk.position)
        ).all()
    )
    documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_({chunk.document_id for chunk in chunks})
            )
        ).all()
    }
    return [
        _search_hit(chunk, documents[chunk.document_id], similarity=None)
        for chunk in chunks
    ]


def current_document_hits(
    session: Session,
    document_ids: tuple[UUID, ...] | list[UUID],
    *,
    per_document_limit: int = 8,
) -> list[SearchHit]:
    """Read readable Chunks from each Document's current successful batch.

    Incremental selection already has a page of changed Documents but no vector
    query for them. This reads what is actually available now without rebuilding
    an index or treating an older parse batch as current evidence.
    """

    if not 1 <= per_document_limit <= 200:
        raise ValueError("per_document_limit must be between 1 and 200")
    normalized = tuple(dict.fromkeys(document_ids))
    if not normalized:
        return []
    rows = session.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(
            DocumentParseState,
            and_(
                DocumentParseState.document_id == Document.id,
                DocumentParseState.current_batch_id
                == DocumentChunk.parse_batch_id,
            ),
        )
        .where(Document.id.in_(normalized))
        .order_by(Document.id, DocumentChunk.position)
    ).all()
    grouped: dict[UUID, list[SearchHit]] = {}
    for chunk, document in rows:
        bucket = grouped.setdefault(document.id, [])
        if len(bucket) < per_document_limit:
            bucket.append(_search_hit(chunk, document, similarity=None))
    return [hit for document_id in normalized for hit in grouped.get(document_id, ())]


def search_chunks_exact(
    session: Session,
    term: str,
    *,
    filters: SearchFilters,
    limit: int,
) -> list[SearchHit]:
    normalized = term.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}", normalized):
        raise ValueError(
            "Exact search term must be a short code using letters, digits, ._:/-"
        )
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    boundary = rf"(?<![[:alnum:]_])(?:{re.escape(normalized)})(?![[:alnum:]_])"
    rows = session.execute(
        _current_chunk_query(filters)
        .add_columns(Document)
        .where(DocumentChunk.text.op("~*")(boundary))
        .order_by(
            Document.published_at.desc().nullslast(),
            Document.collected_at.desc(),
            DocumentChunk.document_id,
            DocumentChunk.position,
        )
        .limit(limit)
    ).all()
    return [
        _search_hit(chunk, document, similarity=None)
        for chunk, document in rows
    ]


def get_embedding_coverage(
    session: Session,
    *,
    model_name: str,
    dimensions: int,
    filters: SearchFilters,
) -> SearchCoverage:
    if dimensions != VECTOR_DIMENSIONS:
        raise ValueError("Embedding dimensions do not match the configured vector space")
    join_conditions = (
        DocumentChunk.__table__.join(
            Document,
            Document.id == DocumentChunk.document_id,
        )
        .join(
            DocumentParseState,
            and_(
                DocumentParseState.document_id == DocumentChunk.document_id,
                DocumentParseState.current_batch_id
                == DocumentChunk.parse_batch_id,
            ),
        )
    )
    conditions = _search_filters(filters)
    current_documents = session.scalar(
        select(func.count(func.distinct(DocumentChunk.document_id)))
        .select_from(join_conditions)
        .where(*conditions)
    ) or 0
    current_chunks = session.scalar(
        select(func.count(DocumentChunk.id))
        .select_from(join_conditions)
        .where(*conditions)
    ) or 0
    indexed_chunks = session.scalar(
        select(func.count(DocumentChunk.id))
        .select_from(
            join_conditions.join(
                DocumentChunkEmbedding,
                and_(
                    DocumentChunkEmbedding.chunk_id == DocumentChunk.id,
                    DocumentChunkEmbedding.model_name == model_name,
                    DocumentChunkEmbedding.dimensions == dimensions,
                ),
            )
        )
        .where(*conditions)
    ) or 0
    indexed_documents = session.scalar(
        select(func.count(func.distinct(DocumentEmbeddingState.document_id)))
        .select_from(
            join_conditions.join(
                DocumentEmbeddingState,
                and_(
                    DocumentEmbeddingState.document_id
                    == DocumentChunk.document_id,
                    DocumentEmbeddingState.parse_batch_id
                    == DocumentParseState.current_batch_id,
                    DocumentEmbeddingState.status == "completed",
                    DocumentEmbeddingState.model_name == model_name,
                    DocumentEmbeddingState.dimensions == dimensions,
                ),
            )
        )
        .where(*conditions)
    ) or 0
    failed_documents = session.scalar(
        select(func.count(func.distinct(DocumentEmbeddingState.document_id)))
        .select_from(
            join_conditions.join(
                DocumentEmbeddingState,
                and_(
                    DocumentEmbeddingState.document_id
                    == DocumentChunk.document_id,
                    DocumentEmbeddingState.parse_batch_id
                    == DocumentParseState.current_batch_id,
                    DocumentEmbeddingState.status == "failed",
                    DocumentEmbeddingState.model_name == model_name,
                    DocumentEmbeddingState.dimensions == dimensions,
                ),
            )
        )
        .where(*conditions)
    ) or 0
    return SearchCoverage(
        current_documents=int(current_documents),
        indexed_documents=int(indexed_documents),
        failed_documents=int(failed_documents),
        current_chunks=int(current_chunks),
        indexed_chunks=int(indexed_chunks),
    )


def find_parse_batch(
    session: Session,
    document_id: UUID,
    *,
    processor_version: str,
    config_fingerprint: str,
) -> DocumentParseBatch | None:
    return session.scalar(
        select(DocumentParseBatch)
        .where(
            DocumentParseBatch.document_id == document_id,
            DocumentParseBatch.processor_version == processor_version,
            DocumentParseBatch.config_fingerprint == config_fingerprint,
        )
        .order_by(DocumentParseBatch.attempt_number.desc())
        .limit(1)
    )


def get_parse_state(session: Session, document_id: UUID) -> DocumentParseState | None:
    return session.get(DocumentParseState, document_id)


def get_parse_batch(session: Session, batch_id: UUID) -> DocumentParseBatch | None:
    return session.get(DocumentParseBatch, batch_id)


def list_parse_batches(
    session: Session, document_id: UUID
) -> list[DocumentParseBatch]:
    return list(
        session.scalars(
            select(DocumentParseBatch)
            .where(DocumentParseBatch.document_id == document_id)
            .order_by(DocumentParseBatch.attempt_number)
        ).all()
    )


def list_parse_chunks(
    session: Session,
    document_id: UUID,
    *,
    batch_id: UUID | None = None,
    current: bool = True,
    limit: int | None = None,
    after_position: int | None = None,
) -> list[DocumentChunk]:
    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if after_position is not None and after_position < -1:
        raise ValueError("after_position must be -1 or greater")
    selected_batch = batch_id
    if selected_batch is None:
        state = get_parse_state(session, document_id)
        if state is None:
            return []
        selected_batch = (
            state.current_batch_id if current else state.latest_batch_id
        )
    if selected_batch is None:
        return []
    statement = (
        select(DocumentChunk)
        .where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.parse_batch_id == selected_batch,
        )
        .order_by(DocumentChunk.position)
    )
    if after_position is not None:
        statement = statement.where(DocumentChunk.position > after_position)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def persist_parse_result(
    session: Session,
    document_id: UUID,
    *,
    processor_version: str,
    config_fingerprint: str,
    content_hash: str,
    status: str,
    error: dict | None,
    chunks: tuple[ParseChunkInput, ...],
    attempted_at: datetime,
    stage_claim: StageClaim | None = None,
) -> ParseBatchResult:
    if status not in {"completed", "failed", "unsupported"}:
        raise ValueError("invalid parse status")
    if status == "completed" and (not chunks or error is not None):
        raise ValueError("completed parse must contain chunks and no error")
    if status != "completed" and chunks:
        raise ValueError("failed or unsupported parse must not contain chunks")
    if status != "completed" and error is None:
        raise ValueError("failed or unsupported parse must include an error")
    if any(not chunk.text.strip() for chunk in chunks):
        raise ValueError("chunk text must not be empty")

    document = session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:
        raise ValueError(f"Unknown document: {document_id}")
    if document.content_hash != content_hash:
        raise ValueError("Document content hash changed during processing")
    attempt_number = (
        session.scalar(
            select(func.max(DocumentParseBatch.attempt_number)).where(
                DocumentParseBatch.document_id == document_id,
            )
        )
        or 0
    ) + 1
    batch = DocumentParseBatch(
        document_id=document_id,
        processor_version=processor_version,
        config_fingerprint=config_fingerprint,
        content_hash=content_hash,
        attempt_number=attempt_number,
        status=status,
        error=deepcopy(error),
        chunk_count=len(chunks),
        created_at=attempted_at,
    )
    session.add(batch)
    try:
        session.flush()
        for position, chunk in enumerate(chunks):
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    parse_batch_id=batch.id,
                    position=position,
                    text=chunk.text,
                    locator=deepcopy(chunk.locator),
                )
            )
        state = session.get(
            DocumentParseState, document_id, populate_existing=True
        )
        if state is None:
            state = DocumentParseState(
                document_id=document_id,
                status=status,
                processor_version=processor_version,
                config_fingerprint=config_fingerprint,
                latest_batch_id=batch.id,
                current_batch_id=batch.id if status == "completed" else None,
                error=deepcopy(error),
                chunk_count=len(chunks),
                attempted_at=attempted_at,
            )
            session.add(state)
        else:
            state.status = status
            state.processor_version = processor_version
            state.config_fingerprint = config_fingerprint
            state.latest_batch_id = batch.id
            if status == "completed":
                state.current_batch_id = batch.id
            state.error = deepcopy(error)
            state.chunk_count = len(chunks)
            state.attempted_at = attempted_at
        if stage_claim is not None:
            # Same transaction as the batch and its chunks: a committed parse
            # result never exists without its stage progress, and a stale owner
            # cannot write either because the token is checked under the row lock.
            # The status comes from the persisted batch, never from the caller.
            apply_stage_outcome(
                session,
                _parse_stage_outcome(stage_claim, batch, attempted_at),
                now=attempted_at,
            )
        if status == "completed":
            # Only a successful attempt makes the now-parseable text researchable.
            # A failed or unsupported attempt changes nothing research may use.
            record_change(
                session,
                kind="document_parsed",
                document_id=document_id,
                recorded_at=attempted_at,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(batch)
    assert state is not None
    session.refresh(state)
    return ParseBatchResult(batch=batch, state=state)


def _parse_stage_outcome(
    claim: StageClaim,
    batch: DocumentParseBatch,
    attempted_at: datetime,
) -> StageOutcome:
    """Derive the parse stage outcome from the batch that was just persisted."""

    if batch.status == "completed":
        return StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_PARSE,
            owner_token=claim.owner_token,
            status="completed",
            processor_version=batch.processor_version,
            config_fingerprint=batch.config_fingerprint,
            input_batch_id=batch.id,
            applicability={
                "requires_embedding": True,
                "requires_classification": True,
            },
            # A retried parse that finally succeeded produces a new current batch
            # that downstream stages must pick up.
            reopen_downstream=True,
        )
    return StageOutcome(
        document_id=claim.document_id,
        stage=STAGE_PARSE,
        owner_token=claim.owner_token,
        status="failed",
        error=batch.error
        or {
            "code": "PARSE_FAILED",
            "message": "Document could not be parsed",
        },
        processor_version=batch.processor_version,
        config_fingerprint=batch.config_fingerprint,
        input_batch_id=batch.id,
    )


def _index_stage_outcome(
    claim: StageClaim,
    *,
    status: str,
    error: dict | None,
    batch_id: UUID,
    processor_version: str | None = None,
    config_fingerprint: str | None = None,
) -> StageOutcome:
    """Derive the index stage outcome from the persisted embedding coverage."""

    if status == "completed":
        return StageOutcome(
            document_id=claim.document_id,
            stage=STAGE_INDEX,
            owner_token=claim.owner_token,
            status="completed",
            processor_version=processor_version,
            config_fingerprint=config_fingerprint,
            input_batch_id=batch_id,
            applicability={"requires_embedding": True},
        )
    return StageOutcome(
        document_id=claim.document_id,
        stage=STAGE_INDEX,
        owner_token=claim.owner_token,
        status="failed",
        error=error
        or {"code": "INDEX_FAILED", "message": "Document could not be indexed"},
        processor_version=processor_version,
        config_fingerprint=config_fingerprint,
        input_batch_id=batch_id,
    )
