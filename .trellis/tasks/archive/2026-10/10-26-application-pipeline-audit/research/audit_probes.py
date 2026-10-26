"""Remote-only reproduction probes for the two source-audit findings.

These assert the intended contract; failures confirm the reported current defects.
Run separately from implementation regression tests. No external model calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

PROJECT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT / "backend" / "tests"))


def test_worker_honors_settings_config_paths(monkeypatch):
    import stocks.worker as worker
    from stocks.settings import Settings

    config = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        tasks_config_path="custom/tasks.yaml",
        models_config_path="custom/models.yaml",
        sources_config_path="custom/sources.yaml",
    )
    monkeypatch.setattr(worker, "settings", lambda: config)
    monkeypatch.setattr(worker, "session_factory", lambda: object())
    context = worker.WorkerContext.from_settings()
    assert (
        context.task_config_path,
        context.model_config_path,
        context.sources_config_path,
    ) == (
        config.tasks_config_path,
        config.models_config_path,
        config.sources_config_path,
    )


@pytest.fixture
def isolated_engine():
    from stocks.models import Base

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


def test_historical_semantic_document_keeps_saved_facts(isolated_engine, monkeypatch):
    import stocks.research.service as research_service
    from stocks.changes import ChangeWindow, current_watermark, scope_fingerprint
    from stocks.providers import ModelClient
    from stocks.research.service import select_evidence
    from test_incremental_evidence import (
        add_change,
        add_document_with_chunks,
        add_facts,
        create_run,
        embedding_targets,
        fake_embed,
        make_config,
        query_snapshot,
        search_hit,
        semantic_result,
    )

    with Session(isolated_engine) as session:
        document, chunks = add_document_with_chunks(session)
        facts = add_facts(session, document, chunks[0])
        add_change(session, document)
        watermark = current_watermark(session)
        run = create_run(session, "audit-historical-fact")
        window = ChangeWindow(
            start=watermark,
            end=watermark,
            task_id=run.task_id,
            fingerprint=scope_fingerprint(run.task_id, run.scope),
            resume=False,
        )
        monkeypatch.setattr(
            research_service,
            "semantic_search",
            lambda *args, **kwargs: semantic_result(search_hit(document, chunks[0])),
        )
        result = select_evidence(
            session,
            make_config(),
            embedding_targets(),
            ModelClient({}),
            run_id=run.id,
            objective=run.objective,
            scope=run.scope,
            as_of=run.created_at,
            queries=("inflation",),
            limit_per_query=2,
            max_tokens=30000,
            change_window=window,
            scan_query_snapshot=query_snapshot(),
            embed=fake_embed,
        )
        groups = [
            item
            for item in result.package.evidence_groups
            if item["document_id"] == str(document.id)
        ]
        assert len(groups) == 1, "Historical document itself must be selected"
        assert {item["fact_id"] for item in groups[0]["fact_refs"]} == {
            str(fact.id) for fact in facts
        }, "Stored Facts must accompany the selected historical document"
