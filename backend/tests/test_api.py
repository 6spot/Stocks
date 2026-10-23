from datetime import UTC, datetime
from uuid import uuid4

from stocks.api import _embedding_state
from stocks.models import DocumentEmbeddingState, DocumentParseState
from stocks.embedding_space import VECTOR_DIMENSIONS


def test_embedding_state_marks_chunks_pending_after_reparse():
    document_id = uuid4()
    current_batch_id = uuid4()
    previous_batch_id = uuid4()
    parse_state = DocumentParseState(
        document_id=document_id,
        status="completed",
        processor_version="parser-v1",
        config_fingerprint="a" * 64,
        latest_batch_id=current_batch_id,
        current_batch_id=current_batch_id,
        error=None,
        chunk_count=3,
        attempted_at=datetime(2026, 10, 20, 4, tzinfo=UTC),
    )
    embedding_state = DocumentEmbeddingState(
        document_id=document_id,
        status="completed",
        parse_batch_id=previous_batch_id,
        model_alias="embedding",
        model_name="text-embedding-3-small",
        dimensions=VECTOR_DIMENSIONS,
        chunk_count=2,
        indexed_count=2,
        error=None,
        attempted_at=datetime(2026, 10, 20, 3, tzinfo=UTC),
    )

    result = _embedding_state(embedding_state, parse_state, 3)

    assert result is not None
    assert result["status"] == "pending"
    assert result["current"] is False
    assert result["parse_batch_id"] == str(current_batch_id)
    assert result["last_attempt_parse_batch_id"] == str(previous_batch_id)
    assert result["chunk_count"] == 3
    assert result["indexed_count"] == 0
    assert result["unindexed_count"] == 3


def test_embedding_state_reports_current_batch_attempt():
    document_id = uuid4()
    batch_id = uuid4()
    parse_state = DocumentParseState(
        document_id=document_id,
        status="completed",
        processor_version="parser-v1",
        config_fingerprint="a" * 64,
        latest_batch_id=batch_id,
        current_batch_id=batch_id,
        error=None,
        chunk_count=2,
        attempted_at=datetime(2026, 10, 20, 4, tzinfo=UTC),
    )
    embedding_state = DocumentEmbeddingState(
        document_id=document_id,
        status="failed",
        parse_batch_id=batch_id,
        model_alias="embedding",
        model_name="text-embedding-3-small",
        dimensions=VECTOR_DIMENSIONS,
        chunk_count=2,
        indexed_count=1,
        error={
            "code": "EMBEDDING_REQUEST_FAILED",
            "message": "All configured embedding targets failed",
        },
        attempted_at=datetime(2026, 10, 20, 5, tzinfo=UTC),
    )

    result = _embedding_state(embedding_state, parse_state, 2)

    assert result is not None
    assert result["status"] == "failed"
    assert result["current"] is True
    assert result["indexed_count"] == 1
    assert result["unindexed_count"] == 1
    assert result["error"] == embedding_state.error
