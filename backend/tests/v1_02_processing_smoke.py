"""Bounded Worker/CLI process smoke for the unified Document processing backlog.

Not collected by pytest (no ``test_`` prefix). It runs on the task-isolated test
server against the task database and object storage, seeds one real Document
version, drives the actual ``stocks.worker --once`` and ``stocks.processing``
processes, and asserts the observable stage outcomes.

No real model is called. The subprocess Worker has no usable embedding route, which
is exactly the case that must not stop parsing; a later in-process index run injects
a fixed embedding response to prove the index stage completes, reuses committed
vectors and leaves the Chunks a report could cite untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from stocks.db import session_factory  # noqa: E402
from stocks.embedding_space import VECTOR_DIMENSIONS  # noqa: E402
from stocks.models import (  # noqa: E402
    Document,
    DocumentCollectionState,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentEmbeddingState,
    DocumentParseBatch,
    DocumentParseState,
    DocumentProcessingState,
    Source,
)
from stocks.processing.processors import (  # noqa: E402
    build_index_processor,
    build_parse_processor,
)
from stocks.processing.runner import StageRegistry, run_stage_now  # noqa: E402
from stocks.processing.stages import (  # noqa: E402
    STAGE_INDEX,
    STAGE_PARSE,
    request_reprocess,
    retry_stage,
)
from stocks.providers import (  # noqa: E402
    EmbeddingResponse,
    EmbeddingTargets,
    ModelTarget,
)
from stocks.repository import (  # noqa: E402
    DocumentInput,
    ingest_document,
    list_parse_chunks,
)
from stocks.s3 import store_snapshot  # noqa: E402
from stocks.settings import settings  # noqa: E402

ARTICLE = b"""\
<html><head><meta charset="utf-8" /></head><body>
<article>
  <h1>Smoke inflation update</h1>
  <p>The annual rate was <b>3.1%</b> and the prior value was 2.8%.</p>
  <table><tr><th>Metric</th><th>Value</th></tr><tr><td>CPI</td><td>3.1%</td></tr></table>
</article>
</body></html>
"""

DEDUPE_KEY = "smoke-processing-1"


BROKEN_MODELS_YAML = """\
providers:
  default:
    protocol: openai_compatible
    base_url: https://model.example/v1
    api_key: ${SMOKE_MISSING_MODEL_KEY}
    capabilities: [chat, text]
    timeout_seconds: 5
models:
  chat:
    provider: default
    name: smoke
    capabilities: [chat, text]
embedding:
  models: [missing_embedding]
"""


def run(args: list[str]) -> tuple[int, dict | str]:
    completed = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    text = completed.stdout.strip() or completed.stderr.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    return completed.returncode, payload


def reset_smoke_rows() -> None:
    """Remove only this smoke run's own rows.

    Keyed to the smoke Source and dedupe key, so it never touches another task's
    Documents. A re-run of this drill must start from a clean backlog.
    """

    with session_factory()() as session:
        # The collection-state pointer references the latest Document, so it has to
        # go first.
        session.query(DocumentCollectionState).filter(
            DocumentCollectionState.source_id == "smoke_processing",
            DocumentCollectionState.dedupe_key == DEDUPE_KEY,
        ).delete(synchronize_session=False)
        session.flush()
        documents = session.scalars(
            select(Document).where(
                Document.source_id == "smoke_processing",
                Document.dedupe_key == DEDUPE_KEY,
            )
        ).all()
        for document in documents:
            session.query(DocumentChunkEmbedding).filter(
                DocumentChunkEmbedding.chunk_id.in_(
                    select(DocumentChunk.id).where(
                        DocumentChunk.document_id == document.id
                    )
                )
            ).delete(synchronize_session=False)
            for state in session.scalars(
                select(DocumentProcessingState).where(
                    DocumentProcessingState.document_id == document.id
                )
            ).all():
                session.delete(state)
            for model in (DocumentEmbeddingState, DocumentParseState):
                row = session.get(model, document.id)
                if row is not None:
                    session.delete(row)
            session.query(DocumentChunk).filter(
                DocumentChunk.document_id == document.id
            ).delete(synchronize_session=False)
            session.query(DocumentParseBatch).filter(
                DocumentParseBatch.document_id == document.id
            ).delete(synchronize_session=False)
            session.delete(document)
        session.commit()


def seed() -> UUID:
    config = settings()
    with session_factory()() as session:
        if session.get(Source, "smoke_processing") is None:
            session.add(
                Source(
                    id="smoke_processing",
                    name="Smoke Processing",
                    connector="rss",
                    trust_tier="T1",
                )
            )
            session.commit()
        result = ingest_document(
            session,
            config,
            DocumentInput(
                source_id="smoke_processing",
                dedupe_key=DEDUPE_KEY,
                title="Smoke inflation update",
                payload=ARTICLE,
                content_type="text/html; charset=utf-8",
                collected_at=datetime.now(UTC),
                published_at=datetime.now(UTC),
                completeness="complete",
            ),
            store=store_snapshot,
        )
        return result.document.id


def stage_map(document_id: UUID) -> dict[str, dict]:
    with session_factory()() as session:
        rows = session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document_id
            )
        ).all()
        return {
            row.stage: {
                "status": row.status,
                "attempts": row.attempts,
                "wait_reason": row.wait_reason,
                "error": row.error,
            }
            for row in rows
        }


def status_rows(document_id: UUID) -> dict[str, dict]:
    code, payload = run(
        ["stocks.processing", "status", "--document-id", str(document_id)]
    )
    if code != 0 or not isinstance(payload, dict):
        return {}
    return {item["stage"]: item for item in payload.get("items", [])}


def fixed_embedding(client, targets, texts):
    return EmbeddingResponse(
        embeddings=[
            tuple(1.0 if index == 0 else 0.0 for index in range(VECTOR_DIMENSIONS))
            for _ in texts
        ],
        model_alias="embedding",
    )


def make_registry(config) -> StageRegistry:
    targets = EmbeddingTargets(
        aliases=("embedding",),
        targets=(
            ModelTarget(
                base_url="https://embeddings.example/v1",
                api_key="smoke-key",
                name="text-embedding-3-small",
                dimensions=VECTOR_DIMENSIONS,
            ),
        ),
    )
    return StageRegistry(
        parse=build_parse_processor(),
        index=build_index_processor(targets, None, embed=fixed_embedding),
    )


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: object = None) -> None:
        print(f"{'PASS' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(f"{name}: {detail!r}")

    reset_smoke_rows()
    document_id = seed()
    print(f"seeded document {document_id}")

    code, status_before = run(
        ["stocks.processing", "status", "--document-id", str(document_id)]
    )
    check("status exit 0", code == 0, status_before)
    check(
        "status lists the new Document's four stages",
        isinstance(status_before, dict)
        and len(status_before.get("items", [])) == 4,
        status_before if not isinstance(status_before, dict) else "count mismatch",
    )
    check(
        "status reports classify/facts as unavailable",
        isinstance(status_before, dict)
        and {"classify", "facts"}
        <= set(status_before.get("unavailable_stages", [])),
        status_before if not isinstance(status_before, dict) else None,
    )
    check(
        "status shows the unavailable stages as waiting, not skipped",
        all(
            status_before["items"] and row["status"] == "pending"
            for row in [
                item
                for item in status_before.get("items", [])
                if item["stage"] in {"classify", "facts"}
            ]
        )
        and all(
            item["wait_reason"] == "PROCESSOR_NOT_AVAILABLE"
            and item["processor_available"] is False
            for item in status_before.get("items", [])
            if item["stage"] in {"classify", "facts"}
        ),
        status_before if not isinstance(status_before, dict) else None,
    )

    # The Worker runs with a deliberately broken model configuration: parsing must
    # still work and the embedding route must only fail an index attempt.
    broken = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, dir="/tmp"
    )
    broken.write(BROKEN_MODELS_YAML)
    broken.close()
    models_config = ["--models-config", broken.name]

    code, once = run(["stocks.worker", "--once", "--poll-seconds", "1", *models_config])
    check("worker --once exit 0", code == 0, once)
    check(
        "worker --once processed a Document stage",
        isinstance(once, dict)
        and isinstance(once.get("processing"), dict)
        and once["processing"].get("stage") in {"parse", "index"}
        and once["processing"].get("status") in {"completed", "failed"},
        once if not isinstance(once, dict) else None,
    )

    stages = stage_map(document_id)
    check("parse completed", stages["parse"]["status"] == "completed", stages["parse"])
    check(
        "index did not run without a usable embedding route",
        stages["index"]["status"] in {"pending", "failed"},
        stages["index"],
    )
    check(
        "a failed index attempt is a safe error, not a silent skip",
        stages["index"]["status"] != "failed"
        or (
            stages["index"]["error"] is not None
            and "code" in stages["index"]["error"]
        ),
        stages["index"],
    )

    with session_factory()() as session:
        batches = session.scalars(
            select(DocumentParseBatch).where(
                DocumentParseBatch.document_id == document_id
            )
        ).all()
        chunks = list_parse_chunks(session, document_id)
        document = session.get(Document, document_id)
        stored = document is not None
    check(
        "one completed parse batch",
        [b.status for b in batches] == ["completed"],
        [b.status for b in batches],
    )
    check("chunks persisted with locators", bool(chunks) and all(c.locator for c in chunks))
    check("document still readable", stored)

    code, again = run(
        ["stocks.worker", "--once", "--poll-seconds", "1", *models_config]
    )
    # The second cycle reaches the index stage, whose embedding route is
    # deliberately broken here. A failed Document stage does not stop the slot,
    # but ``--once`` must still surface it through the exit code.
    check(
        "second --once exit 1 because the index stage really failed",
        code == 1
        and isinstance(again, dict)
        and (again.get("processing") or {}).get("status") == "failed",
        again,
    )
    stages_after = stage_map(document_id)
    check(
        "repeated polling does not reprocess a completed parse",
        stages_after["parse"]["attempts"] == stages["parse"]["attempts"],
        (stages["parse"], stages_after["parse"]),
    )

    # Index through the real stage path with a fixed embedding response.
    config = settings()
    registry = make_registry(config)
    owner = f"smoke-{document_id}"
    with session_factory()() as session:
        # A failed automatic attempt is backed off; the Owner retry is the path
        # that makes it claimable again, exactly as the CLI does.
        retry_stage(session, document_id, STAGE_INDEX)
    execution = run_stage_now(
        session_factory(), config, registry, document_id, STAGE_INDEX, owner_token=owner
    )
    check(
        "index completes with a usable route",
        execution is not None and execution.status == "completed",
        None if execution is None else execution.to_dict(),
    )
    with session_factory()() as session:
        chunks_after_index = [
            (chunk.id, chunk.text, chunk.parse_batch_id)
            for chunk in list_parse_chunks(session, document_id)
        ]
    check(
        "indexing left the cited Chunks unchanged",
        chunks_after_index
        == [(chunk.id, chunk.text, chunk.parse_batch_id) for chunk in chunks],
    )

    # An interrupted stage must not be released without Owner confirmation.
    with session_factory()() as session:
        state = session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document_id,
                DocumentProcessingState.stage == "index",
            )
        ).one()
        state.status = "running"
        state.owner_token = "smoke-stale-owner"
        session.commit()
    code, refused = run(
        [
            "stocks.processing",
            "retry",
            "--document-id",
            str(document_id),
            "--stage",
            "index",
        ]
    )
    check("retry without confirmation exits 1", code == 1, refused)
    check(
        "retry without confirmation is refused",
        isinstance(refused, dict)
        and refused["errors"][0]["error"]["code"] == "OWNER_CONFIRMATION_REQUIRED",
        refused if not isinstance(refused, dict) else None,
    )
    code, released = run(
        [
            "stocks.processing",
            "retry",
            "--document-id",
            str(document_id),
            "--stage",
            "index",
            "--confirm-stopped",
        ]
    )
    check("retry with confirmation exits 0", code == 0, released)
    with session_factory()() as session:
        index_state = session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document_id,
                DocumentProcessingState.stage == "index",
            )
        ).one()
        owner_cleared = index_state.owner_token is None
        released_status = index_state.status
    check("recovery clears the stale owner token", owner_cleared, released_status)
    check(
        "recovery recognises the already committed index result",
        released_status == "completed",
        released_status,
    )

    # Explicit reprocess is the only path that really re-runs a finished stage.
    code, reprocessed_index = run(
        [
            "stocks.processing",
            "reprocess",
            "--document-id",
            str(document_id),
            "--stage",
            "index",
        ]
    )
    check("explicit index reprocess exits 0", code == 0, reprocessed_index)
    check(
        "explicit index reprocess reopens the stage",
        stage_map(document_id)["index"]["status"] == "pending",
        stage_map(document_id)["index"],
    )
    # Leave it completed again so the later reparse assertion starts clean.
    retry_execution = run_stage_now(
        session_factory(),
        config,
        registry,
        document_id,
        STAGE_INDEX,
        owner_token=f"smoke-reprocess-{document_id}",
    )
    check(
        "reprocessed index completes without reparsing",
        retry_execution is not None and retry_execution.status == "completed",
        None if retry_execution is None else retry_execution.to_dict(),
    )

    # A completed stage is not rerun by retry, only by explicit reprocess.
    code, refused_completed = run(
        [
            "stocks.processing",
            "retry",
            "--document-id",
            str(document_id),
            "--stage",
            "parse",
        ]
    )
    check("retry of a completed stage exits 1", code == 1, refused_completed)
    code, reprocessed = run(
        [
            "stocks.processing",
            "reprocess",
            "--document-id",
            str(document_id),
            "--stage",
            "parse",
        ]
    )
    check("explicit reprocess exits 0", code == 0, reprocessed)
    check(
        "explicit reprocess reopens the stage",
        stage_map(document_id)["parse"]["status"] == "pending",
        stage_map(document_id)["parse"],
    )

    code, executed = run(
        ["stocks.worker", "--once", "--poll-seconds", "1", *models_config]
    )
    check("worker after reprocess exit 0", code == 0, executed)
    stages_final = stage_map(document_id)
    check(
        "reprocess produced a new completed parse",
        stages_final["parse"]["status"] == "completed",
        stages_final["parse"],
    )
    with session_factory()() as session:
        batches = session.scalars(
            select(DocumentParseBatch).where(
                DocumentParseBatch.document_id == document_id
            )
        ).all()
        current_chunks = list_parse_chunks(session, document_id)
    check(
        "old parse batches are preserved",
        len(batches) >= 2,
        [b.status for b in batches],
    )
    check(
        "the new current batch has its own Chunks",
        bool(current_chunks)
        and current_chunks[0].parse_batch_id
        != chunks_after_index[0][2],
        current_chunks[0].parse_batch_id if current_chunks else None,
    )

    # Reprocessing the classification is out of scope for this release, but the
    # Facts row must not be reopened by a parse reprocess (PRD R4).
    with session_factory()() as session:
        facts = session.scalars(
            select(DocumentProcessingState).where(
                DocumentProcessingState.document_id == document_id,
                DocumentProcessingState.stage == "facts",
            )
        ).one()
        facts.status = "completed"
        facts.attempts = 1
        session.commit()
        request_reprocess(session, document_id, STAGE_PARSE)
    code, _ = run(["stocks.worker", "--once", "--poll-seconds", "1", *models_config])
    check("worker after facts-completed reparse exit 0", code == 0)
    final = stage_map(document_id)
    check(
        "a reparse keeps completed Facts completed",
        final["facts"]["status"] == "completed" and final["facts"]["attempts"] == 1,
        final["facts"],
    )
    check(
        "a reparse reopens the index for the new current batch",
        final["index"]["status"] in {"pending", "completed"},
        final["index"],
    )

    os.unlink(broken.name)
    print("\nsummary:", "FAILED" if failures else "OK")
    for failure in failures:
        print(" -", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
