#!/usr/bin/env python3
"""MP-01 controlled API-to-Worker configuration acceptance on the isolated server.

Adapted from the archived application-pipeline-audit smoke. Uses real PostgreSQL,
S3 and application paths, with controlled HTTP/model transports. Worker is built
from Settings without explicit paths; custom source/task/model identities must
survive API enqueue, Worker claim/execution and persisted research model calls.
Only the main agent runs this script, after sourcing the private runtime.env.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import UUID, uuid4

ARTICLE = b"""<html><head><meta charset="utf-8"></head><body><article>
<h1>Acme Holdings quarterly revenue</h1>
<p>Acme Holdings (ACME) reported revenue of 42 million USD for 2026 Q3.</p>
</article></body></html>"""
STATEMENT = "Acme Holdings (ACME) reported revenue of 42 million USD for 2026 Q3."
ROOT = Path(__file__).resolve().parents[4]


class SmokeFailure(Exception):
    """Contains only a fixed diagnostic code, never configuration or secrets."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise SmokeFailure(code)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def run(output: Path, manifest: dict) -> None:
    # Fail before importing the app or contacting services outside this task.
    db_url = os.environ.get("TEST_DATABASE_URL", "")
    db_parts = urlsplit(db_url)
    s3_parts = urlsplit(os.environ.get("S3_ENDPOINT_URL", ""))
    require(db_parts.hostname in {"127.0.0.1", "localhost"} and db_parts.port == 15561,
            "EXPECTED_ISOLATED_POSTGRES_LOOPBACK_15561")
    require(s3_parts.hostname in {"127.0.0.1", "localhost"} and s3_parts.port == 18361,
            "EXPECTED_ISOLATED_S3_LOOPBACK_18361")
    require(os.environ.get("S3_ACCESS_KEY") and os.environ.get("S3_SECRET_KEY"),
            "ISOLATED_S3_CREDENTIALS_REQUIRED")
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    import boto3
    from botocore.config import Config
    import httpx
    import yaml
    from sqlalchemy import func, select, text
    from stocks.embedding_space import VECTOR_DIMENSIONS

    suffix = uuid4().hex[:12]
    source_id = f"mp_smoke_{suffix}"
    collection_task = f"mp_collect_{suffix}"
    research_task = f"mp_research_{suffix}"
    bucket = f"stocks-mp-smoke-{suffix}"
    article_url = "https://source.smoke.test/article"
    stamp = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Controlled source</title><link>https://source.smoke.test/</link>'
        '<description>Isolated smoke</description><item>'
        '<guid isPermaLink="false">manual-pipeline-one</guid>'
        '<title>Acme Holdings quarterly revenue</title>'
        f'<link>{article_url}</link><pubDate>{stamp}</pubDate>'
        f'<description>{STATEMENT}</description></item></channel></rss>'
    ).encode()
    sources = {"sources": [{
        "source_id": source_id, "name": "Controlled manual pipeline source",
        "connector": "rss", "enabled": True, "publisher": "Smoke fixture",
        "trust_tier": "T1", "feed_url": "https://source.smoke.test/feed.xml",
        "timeout_seconds": 5, "max_response_bytes": 65536,
        "max_redirects": 1, "max_items": 1,
        "user_agent": "StocksSmoke/1.0 (smoke@example.com)",
    }]}
    # Reuse the actual shipped article schemas instead of relaxing their checks.
    shipped = yaml.safe_load((ROOT / "config" / "tasks.yaml").read_text())
    collect = deepcopy(next(t for t in shipped["tasks"] if t["id"] == "collect_enabled_sources"))
    collect.update(id=collection_task, name="Controlled smoke collect", sources=[source_id])
    research = deepcopy(next(t for t in shipped["tasks"] if t["id"] == "saved_evidence_brief"))
    research.update(id=research_task, name="Controlled smoke research",
                    objective="分析 Acme Holdings（ACME）已保存的季度收入证据与资料边界。")
    selection = research["steps"][0]["select_evidence"]
    selection.update(queries=["Acme Holdings ACME quarterly revenue 42 million USD"],
                     source_ids=[source_id], limit_per_query=4, max_tokens=12000)
    tasks = {"tasks": [collect, research], "agents": deepcopy(shipped["agents"])}
    # This fixture exercises stored evidence without external research Tools.
    for agent in tasks["agents"]:
        agent.pop("tools", None)
    models = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    require(models["agents"]["classification"].get("protocol") == "typesafe",
            "CURRENT_NATIVE_JEV_INTEGRATION_REQUIRED")
    require("classification_entities" in models["agents"], "EXPLICIT_ENTITY_ROUTE_REQUIRED")
    for provider in models["providers"].values():
        provider.update(base_url="https://models.smoke.test/v1", api_key="controlled-smoke-key",
                        timeout_seconds=10)
    for alias, model in models["models"].items():
        model["name"] = f"controlled-{alias}"
    for agent in models["agents"].values():
        agent["routing"].update(timeout_seconds=10, retries=0, retry_backoff_seconds=0)
    for name, value in (("tasks", tasks), ("models", models), ("sources", sources)):
        (output / f"{name}.yaml").write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
    os.environ.update(
        DATABASE_URL=db_url, S3_BUCKET=bucket,
        TASKS_CONFIG_PATH=str(output / "tasks.yaml"),
        MODELS_CONFIG_PATH=str(output / "models.yaml"),
        SOURCES_CONFIG_PATH=str(output / "sources.yaml"),
        OWNER_PASSWORD=secrets.token_urlsafe(24), OWNER_SESSION_SECRET=secrets.token_urlsafe(48),
        OWNER_COOKIE_SECURE="false",
    )
    from stocks.settings import settings
    settings.cache_clear()
    from stocks.db import session_factory
    session_factory.cache_clear()
    from stocks.api import app
    from fastapi.testclient import TestClient
    from stocks.collection.http import SafeFetcher
    from stocks.models import (Analysis, Document, DocumentChunk, DocumentChunkEmbedding,
                               DocumentParseState, DocumentProcessingState, EvidencePackage,
                               Fact, ModelCall, Report, TaskRun)
    from stocks.s3 import read_snapshot
    from stocks.indexing.service import load_embedding_client
    from stocks.worker import WorkerContext, execute_next_document_stage, execute_next_run

    config = settings()
    factory = session_factory()
    with factory() as session:
        # No DDL or deletions: main must initialize the current schema first.
        require(session.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 1,
                "PGVECTOR_EXTENSION_REQUIRED")
        require(session.scalar(select(func.count()).select_from(TaskRun).where(
            TaskRun.status.in_(["queued", "running"]))) == 0, "UNRELATED_ACTIVE_RUNS_PRESENT")
        require(session.scalar(select(func.count()).select_from(DocumentProcessingState).where(
            DocumentProcessingState.status.notin_(["completed", "skipped"]))) == 0,
            "UNRELATED_DOCUMENT_BACKLOG_PRESENT")
    s3 = boto3.client("s3", endpoint_url=config.s3_endpoint_url,
                      region_name=config.s3_region,
                      aws_access_key_id=getattr(config, "s3_access_key"),
                      aws_secret_access_key=getattr(config, "s3_secret_key"),
                      config=Config(signature_version="s3v4"))
    create_bucket = {"Bucket": bucket}
    if config.s3_region != "us-east-1":
        create_bucket["CreateBucketConfiguration"] = {"LocationConstraint": config.s3_region}
    s3.create_bucket(**create_bucket)
    manifest.update(source_id=source_id, collection_task_id=collection_task,
                    research_task_id=research_task, bucket=bucket, output_dir=str(output),
                    postgres="real isolated PostgreSQL + pgvector",
                    storage="real isolated S3 put/get", external="controlled HTTP/model transports",
                    browser="not exercised; API citation contracts only")
    manifest["stage"] = "services_ready"
    write_json(output / "manifest.json", manifest)

    source_calls = Counter()
    model_calls = Counter()
    observed = {}

    def source_handler(request):
        require(request.url.host == "source.smoke.test", "UNEXPECTED_SOURCE_HOST")
        source_calls[request.url.path] += 1
        if request.url.path == "/feed.xml":
            payload, media = feed, "application/rss+xml"
        elif request.url.path == "/article":
            payload, media = ARTICLE, "text/html; charset=utf-8"
        else:
            raise SmokeFailure("UNEXPECTED_SOURCE_PATH")
        # Preserve the streaming behavior that SafeFetcher size/decoding checks use.
        return httpx.Response(200, headers={"content-type": media}, stream=httpx.ByteStream(payload))

    def model_handler(request):
        require(request.url.host == "models.smoke.test", "UNEXPECTED_MODEL_HOST")
        body = json.loads(request.content)
        if request.url.path.endswith("/systemone"):
            model_calls["jev_native"] += 1
            require(STATEMENT in body["state"]["excerpt"], "JEV_DID_NOT_RECEIVE_REAL_PARSED_TEXT")
            answers = {}
            choices = {"doc_type": "company", "event_type": "earnings"}
            true_questions = {"should_extract", "topics.growth", "regions.US"}
            for identity, question in body["questions"].items():
                if question["type"] == "choice":
                    selected = choices.get(identity)
                    require(selected in question["criteria"], "JEV_FIXTURE_CRITERIA_CHANGED")
                    probabilities = {key: float(key == selected) for key in question["criteria"]}
                    answers[identity] = {"type": "choice", "choice": selected,
                                         "probabilities": probabilities, "confidence": 1.0}
                else:
                    require(question["type"] == "noul", "UNKNOWN_JEV_QUESTION_TYPE")
                    answers[identity] = {"type": "noul", "noul": 0.99 if identity in true_questions else 0.01}
            return httpx.Response(200, json={"model": body["model"], "answers": answers,
                                             "usage": {"input_tokens": 100, "output_tokens": 30}})
        if request.url.path.endswith("/embeddings"):
            model_calls["embedding"] += 1
            inputs = body["input"]
            inputs = [inputs] if isinstance(inputs, str) else inputs
            vector = [1.0] + [0.0] * (VECTOR_DIMENSIONS - 1)
            return httpx.Response(200, json={"model": body["model"], "data": [
                {"index": i, "embedding": vector} for i in range(len(inputs))]})
        require(request.url.path.endswith("/chat/completions"), "UNEXPECTED_MODEL_PATH")
        supplied = json.loads(body["messages"][-1]["content"])
        if "chunks" in supplied:
            model_calls["fact_extraction"] += 1
            chunk = next((c for c in supplied["chunks"] if STATEMENT in c["text"]), None)
            require(chunk, "FACT_EXTRACTOR_DID_NOT_RECEIVE_REAL_CHUNK")
            observed["extraction_chunk_id"] = chunk["chunk_id"]
            answer = {"facts": [{"fact_type": "company_metric", "chunk_id": chunk["chunk_id"],
                                  "subject_name": "Acme Holdings", "symbol": "ACME",
                                  "metric_id": "revenue", "statement": STATEMENT,
                                  "value": 42, "unit": "million USD",
                                  "period_start": "2026-07-01T00:00:00Z",
                                  "period_end": "2026-09-30T23:59:59Z"}]}
        elif "run_id" in supplied and "evidence" in supplied:
            model_calls["research"] += 1
            evidence = next((e for e in supplied["evidence"]
                             if e["document_id"] == manifest["document_id"] and e.get("fact_refs")), None)
            require(evidence, "RESEARCH_DID_NOT_RECEIVE_SAME_DOCUMENT_AND_FACT")
            require(STATEMENT in evidence["text"], "RESEARCH_TEXT_MISMATCH")
            require(manifest["fact_id"] in {f["fact_id"] for f in evidence["fact_refs"]},
                    "RESEARCH_FACT_ID_MISMATCH")
            observed["research_input"] = supplied
            ref = {"chunk_id": evidence["chunk_id"], "document_id": evidence["document_id"]}
            observed["citation"] = ref
            prose = "受控来源记载 Acme Holdings（ACME）2026 Q3 收入为 42 million USD。单篇资料不足以核实趋势。"
            answer = {"title": "Acme 季度收入的已保存证据与判断边界", "summary": prose,
                      "sections": [{"heading": "收入记录及资料限制", "body": prose, "finding_ids": ["revenue"]}],
                      "findings": [{"id": "revenue", "title": "季度收入记录", "kind": "fact",
                                    "statement": STATEMENT, "rationale": "原文和已保存 Fact 指向同一季度收入记录。",
                                    "uncertainty": "仅为受控集成验证；未进行真实外部来源核实。", "evidence_refs": [ref]}],
                      "open_questions": ["其他独立来源是否支持这一收入记录？"], "evidence_refs": [ref]}
        else:
            model_calls["classification_entities"] += 1
            require(STATEMENT in supplied["excerpt"], "ENTITY_EXTRACTOR_TEXT_MISMATCH")
            answer = {"companies": ["Acme Holdings"], "symbols": ["ACME"],
                      "reason": "The supplied article explicitly names Acme Holdings (ACME).",
                      "evidence": [{"kind": "company", "value": "Acme Holdings", "quote": STATEMENT},
                                   {"kind": "symbol", "value": "ACME", "quote": STATEMENT}]}
        return httpx.Response(200, json={"model": body["model"],
            "choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}})

    model_transport = httpx.MockTransport(model_handler)
    source_transport = httpx.MockTransport(source_handler)
    worker = WorkerContext.from_settings(model_transport=model_transport)
    manifest["config_paths"] = {"tasks": worker.task_config_path,
                                "models": worker.model_config_path,
                                "sources": worker.sources_config_path}
    require(worker.task_config_path == config.tasks_config_path
            and worker.model_config_path == config.models_config_path
            and worker.sources_config_path == config.sources_config_path,
            "WORKER_DID_NOT_INHERIT_API_SETTINGS")

    def step(name):
        manifest.update(stage=name, source_calls=dict(source_calls), model_calls=dict(model_calls))
        write_json(output / "manifest.json", manifest)
        print(f"CHECKPOINT {name}", flush=True)

    with patch("stocks.collection.service.SafeFetcher", side_effect=lambda: SafeFetcher(
        resolver=lambda host, port: ["93.184.216.34"], transport=source_transport)):
        with TestClient(app) as api:
            def request(method, endpoint, **kwargs):
                response = api.request(method, endpoint, **kwargs)
                require(response.status_code == 200, f"API_{method}_{response.status_code}")
                return response

            login = request("POST", "/api/session/login", json={"password": os.environ["OWNER_PASSWORD"]}).json()
            api.headers.update({"X-CSRF-Token": login["session"]["csrf_token"]})
            collection = request("POST", f"/api/tasks/{collection_task}/runs").json()
            manifest["collection_run_id"] = collection["run_id"]
            require(collection["created"], "COLLECTION_RUN_NOT_NEW")
            repeated = request("POST", f"/api/tasks/{collection_task}/runs").json()
            require(not repeated["created"] and repeated["run_id"] == collection["run_id"],
                    "DUPLICATE_TRIGGER_NOT_COALESCED")
            step("collection_queued")
            result = execute_next_run(worker, process_token=f"mp-{suffix}-collect")
            require(result and result["run_id"] == collection["run_id"] and result["status"] == "succeeded",
                    "COLLECTION_WORKER_FAILED")
            with factory() as session:
                documents = list(session.scalars(select(Document).where(Document.source_id == source_id)))
                require(len(documents) == 1, "EXPECTED_EXACTLY_ONE_COLLECTED_DOCUMENT")
                document = documents[0]
                require(document.completeness == "complete", "COLLECTION_ONLY_PARTIAL")
                require(read_snapshot(config, document.snapshot_key) == ARTICLE, "REAL_S3_BYTES_MISMATCH")
                manifest.update(document_id=str(document.id), document_version=document.version,
                                content_hash=document.content_hash, snapshot_key=document.snapshot_key)
            step("collection_stored_in_real_s3")
            for _ in range(16):
                with factory() as session:
                    states = list(session.scalars(select(DocumentProcessingState).where(
                        DocumentProcessingState.document_id == UUID(manifest["document_id"]))))
                    view = {s.stage: {"status": s.status, "attempts": s.attempts,
                                      "wait_reason": s.wait_reason,
                                      "error_code": (s.error or {}).get("code")} for s in states}
                manifest["processing"] = view
                require(not any(s["status"] == "failed" for s in view.values()), "DOCUMENT_STAGE_FAILED")
                if len(view) == 4 and all(s["status"] == "completed" for s in view.values()):
                    break
                execution = execute_next_document_stage(worker, process_token=f"mp-{suffix}-document")
                require(execution, "DOCUMENT_BACKLOG_STALLED")
                require(execution.get("status") != "failed", "DOCUMENT_WORKER_EXECUTION_FAILED")
                step("processing_" + execution["stage"])
            else:
                raise SmokeFailure("DOCUMENT_STAGE_BUDGET_EXHAUSTED")
            with factory() as session:
                doc_id = UUID(manifest["document_id"])
                document = session.get(Document, doc_id)
                classification = document.classification or {}
                require(classification.get("should_extract") is True, "EXPECTED_POSITIVE_EXTRACTION_DECISION")
                require(classification.get("symbols") == ["ACME"], "ENTITY_RESULT_NOT_PERSISTED")
                roles = {c.get("role") for c in classification.get("model_calls", [])}
                require({"classification", "classification_entities"}.issubset(roles), "JEV_ENTITY_PROVENANCE_MISSING")
                parse_state = session.get(DocumentParseState, doc_id)
                require(parse_state and parse_state.current_batch_id, "CURRENT_PARSE_MISSING")
                chunks = list(session.scalars(select(DocumentChunk).where(
                    DocumentChunk.parse_batch_id == parse_state.current_batch_id)))
                facts = list(session.scalars(select(Fact).where(Fact.document_id == doc_id)))
                require(len(facts) == 1 and facts[0].value == 42, "PERSISTED_FACT_MISMATCH")
                require(facts[0].evidence_ref["chunk_id"] == observed["extraction_chunk_id"], "FACT_CITATION_MISMATCH")
                embeddings = session.scalar(select(func.count()).select_from(DocumentChunkEmbedding).where(
                    DocumentChunkEmbedding.chunk_id.in_([c.id for c in chunks])))
                require(embeddings == len(chunks) and embeddings > 0, "PGVECTOR_ROWS_MISSING")
                manifest.update(parse_batch_id=str(parse_state.current_batch_id),
                                chunk_ids=[str(c.id) for c in chunks], fact_id=str(facts[0].id),
                                embedding_rows=embeddings)
                write_json(output / "classification.json", classification)
            step("document_processing_ready")
            research_run = request("POST", f"/api/tasks/{research_task}/runs").json()
            manifest["research_run_id"] = research_run["run_id"]
            step("research_queued")
            result = execute_next_run(worker, process_token=f"mp-{suffix}-research")
            require(result and result["run_id"] == research_run["run_id"] and result["status"] == "succeeded",
                    "RESEARCH_WORKER_FAILED")
            with factory() as session:
                run_id = UUID(manifest["research_run_id"])
                package = session.scalar(select(EvidencePackage).where(EvidencePackage.run_id == run_id))
                analysis = session.scalar(select(Analysis).where(Analysis.run_id == run_id))
                report = session.scalar(select(Report).where(Report.run_id == run_id))
                require(package and analysis and report, "SAVED_RESEARCH_CHAIN_MISSING")
                require(analysis.findings and analysis.sections, "EMPTY_ANALYSIS_NOT_ACCEPTABLE")
                calls = list(session.scalars(select(ModelCall).where(ModelCall.run_id == run_id)))
                require(calls and all(c.attempt_model_name.startswith("controlled-") for c in calls),
                        "CUSTOM_MODEL_IDENTITY_NOT_PERSISTED")
                manifest["persisted_model_names"] = sorted({c.attempt_model_name for c in calls})
                require(any(g["document_id"] == manifest["document_id"] and
                            manifest["fact_id"] in {f["fact_id"] for f in g.get("fact_refs", [])}
                            for g in package.evidence_groups), "SAME_DATA_NOT_FROZEN_IN_PACKAGE")
                manifest.update(evidence_package_id=str(package.id), analysis_id=str(analysis.id),
                                report_id=str(report.id), cited_chunk_id=observed["citation"]["chunk_id"])
            step("research_analysis_report_persisted")
            report_id, chunk_id = manifest["report_id"], manifest["cited_chunk_id"]
            detail = request("GET", f"/api/reports/{report_id}").json()
            require(chunk_id in detail["cited_chunk_ids"], "REPORT_DOES_NOT_CITE_BATCH")
            evidence = request("GET", f"/api/reports/{report_id}/evidence/{chunk_id}").json()["evidence"]
            require(evidence["document_id"] == manifest["document_id"] and evidence["chunk_available"]
                    and evidence["version_matches"] and STATEMENT in evidence["text"], "CITATION_RETRIEVAL_MISMATCH")
            original = request("GET", evidence["original"]["endpoint"])
            require(original.content == ARTICLE, "ORIGINAL_API_BYTES_MISMATCH")
            require(original.headers.get("x-content-hash-match") == "true", "ORIGINAL_API_HASH_MISMATCH")
            require(hashlib.sha256(original.content).hexdigest() == manifest["content_hash"], "ORIGINAL_SHA256_MISMATCH")
            download = request("GET", evidence["original"]["endpoint"], params={"download": True})
            require(download.content == ARTICLE and "attachment" in download.headers.get("content-disposition", ""),
                    "ORIGINAL_DOWNLOAD_MISMATCH")
            write_json(output / "report-detail.json", detail)
            write_json(output / "research-input.json", observed["research_input"])
            write_json(output / "citation.json", evidence)
            # Exercise actual vector retrieval independently of incremental direct hits.
            # API does not expose transport injection; inject only its real model
            # client factory's supported transport, preserving API/search results.
            with patch("stocks.api.load_embedding_client", side_effect=lambda path:
                       load_embedding_client(path, transport=model_transport)):
                search = request("GET", "/api/search", params={"query": "Acme revenue", "source_id": source_id}).json()
            write_json(output / "semantic-search.json", search)
            hits = search.get("hits", [])
            require(any(h.get("document_id") == manifest["document_id"] for h in hits),
                    "SEMANTIC_SEARCH_DID_NOT_RETRIEVE_BATCH")
            require(source_calls["/feed.xml"] == 1 and source_calls["/article"] == 1, "SOURCE_CALL_COUNT_MISMATCH")
            require(all(model_calls[k] >= 1 for k in ("jev_native", "classification_entities", "fact_extraction", "embedding", "research")),
                    "EXPECTED_MODEL_ROLES_NOT_EXERCISED")
            step("report_citation_original_and_semantic_search_verified")
    manifest.update(status="passed", external_success=False, cleanup="resources intentionally retained for main")
    step("complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output = (args.output_dir or Path(__file__).resolve().parent / f"smoke-output-{uuid4().hex[:12]}").resolve()
    # Harness evidence/config artifacts stay under the current task research tree.
    require(output.is_relative_to(Path(__file__).resolve().parent), "OUTPUT_MUST_BE_UNDER_TASK_RESEARCH")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "running", "stage": "preflight", "external_success": False}
    try:
        run(output, manifest)
    except Exception as exc:
        # Never echo DB URLs, credentials, arbitrary transport bodies or exception messages.
        manifest.update(status="failed", error_type=type(exc).__name__,
                        failure_code=str(exc) if isinstance(exc, SmokeFailure) else "UNEXPECTED_EXCEPTION_REVIEW_CURRENT_STAGE",
                        cleanup="partial rows/bucket retained; main owns cleanup")
        write_json(output / "manifest.json", manifest)
        print(json.dumps({"status": "failed", "stage": manifest["stage"], "error_type": type(exc).__name__,
                          "manifest": str(output / "manifest.json")}))
        return 1
    print(json.dumps({"status": "passed", "manifest": str(output / "manifest.json"), "external_success": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
