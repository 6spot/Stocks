#!/usr/bin/env python3
"""Remote-only three-document taxonomy/research smoke, with optional native Jev.

Parent owns database initialization, containers, execution, and cleanup. All
source HTTP and all non-Jev models are controlled. See taxonomy-flow-smoke.md.
No schema changes, securities master, live sources, or external Tools are used.
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
from urllib.parse import urlparse
from unittest.mock import patch
from uuid import UUID

import boto3
from botocore.config import Config
import httpx
from sqlalchemy import func, select, text
import yaml

ROOT = Path(__file__).resolve().parents[4]
TASK = ROOT / ".trellis/tasks/10-27-taxonomy-research-flow"
GATEWAY = "http://127.0.0.1:13425/v1/systemone"
COMPANY = "Northstar Silicon"
SYMBOL = "NSIL"
STATEMENT = "Northstar Silicon (NSIL) reported revenue of 42 million USD for 2026 Q3."
LIMITS = "全部材料为虚构合成测试数据，不能作为真实公司事实、预测或投资依据；除可选 Jev 分类外，来源及生成模型响应均受控。"
SPECS = {
    "explicit": {
        "title": "Fictional Northstar Silicon (NSIL) quarterly semiconductor results",
        "lead": STATEMENT,
        "paragraph": "Northstar Silicon (NSIL) describes US semiconductor demand and chip design in this fictional company earnings article. The reported revenue belongs only to this invented source; it has no external verification. Packaging availability is discussed as context without additional numeric claims.",
        "companies": [COMPANY], "symbols": [SYMBOL], "category": "company", "event_type": "earnings",
    },
    "name_only": {
        "title": "Fictional Northstar Silicon semiconductor product commentary",
        "lead": "Northstar Silicon describes its chip design roadmap and US semiconductor demand without publishing a ticker or numeric operating metrics.",
        "paragraph": "Northstar Silicon discusses qualitative semiconductor packaging requirements and demand in the US. This fictional company commentary contains no ticker symbol, financial values or quantitative forecast. It supports discussion of the company's stated priorities, while an exact revenue figure cannot be extracted from this article.",
        "companies": [COMPANY], "symbols": [], "category": "company", "event_type": None,
    },
    "background": {
        "title": "Fictional US semiconductor industry background",
        "lead": "The US semiconductor industry faces qualitative changes in chip demand and advanced packaging needs; this background article names no company or ticker.",
        "paragraph": "US semiconductor demand depends on chip design, fabrication capacity and advanced packaging availability. This fictional industry background describes supply constraints qualitatively, without naming a company, security or ticker and without numeric observations. Sector context cannot establish that any particular business is mentioned or benefits.",
        "companies": [], "symbols": [], "category": "industry", "event_type": None,
    },
}
for spec in SPECS.values():
    paragraphs = "".join(f"<p>{spec['paragraph']}</p>" for _ in range(8))
    spec["raw"] = (
        "<!doctype html><html><head>"
        f"<title>{spec['title']}</title>"
        '<meta property="article:published_time" content="2026-10-25T12:00:00Z">'
        f"</head><body><main><article><h1>{spec['title']}</h1>"
        f"<p>{spec['lead']}</p>{paragraphs}"
        "<p>This entire article is synthetic integration data, not a real market fact.</p>"
        "</article></main></body></html>"
    ).encode()


class SmokeFailure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise SmokeFailure(code)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def identity_for(supplied):
    title = supplied.get("document", {}).get("title")
    matches = [key for key, spec in SPECS.items() if spec["title"] == title]
    require(len(matches) == 1, "UNKNOWN_DOCUMENT_INPUT")
    return matches[0]


def main_run(args, manifest):
    runtime = args.runtime_dir.resolve()
    # This repository's workstation is macOS. Also require the expected remote
    # checkout/runtime boundary and explicitly loopback test services.
    require(sys.platform == "linux" and Path("/proc").is_dir(), "REMOTE_LINUX_REQUIRED")
    require(str(runtime).startswith("/tmp/stocks-v0-taxonomy-research-flow"), "TASK_RUNTIME_REQUIRED")
    require(ROOT.is_relative_to(runtime), "RUN_FROM_TASK_REMOTE_CHECKOUT")
    db_url = os.environ.get("TEST_DATABASE_URL", "")
    parsed_db = urlparse(db_url)
    parsed_s3 = urlparse(os.environ.get("S3_ENDPOINT_URL", ""))
    require(parsed_db.hostname in {"127.0.0.1", "localhost"} and parsed_db.port == 15547,
            "TASK_POSTGRES_LOOPBACK_REQUIRED")
    require(parsed_s3.hostname in {"127.0.0.1", "localhost"} and parsed_s3.port == 18347,
            "TASK_S3_LOOPBACK_REQUIRED")
    require(os.environ.get("S3_ACCESS_KEY") and os.environ.get("S3_SECRET_KEY"),
            "TASK_S3_CREDENTIALS_REQUIRED")
    # Never persist this key or place it in generated provider configuration.
    gateway_key = None
    if args.real_jev:
        auth_path = runtime / "gateway-auth.json"
        require(auth_path.is_file() and not auth_path.is_symlink(), "PRIVATE_GATEWAY_AUTH_REQUIRED")
        require(auth_path.stat().st_mode & 0o077 == 0, "GATEWAY_AUTH_MUST_BE_PRIVATE")
        gateway_key = json.loads(auth_path.read_text())["api_key"]
        require(isinstance(gateway_key, str) and bool(gateway_key), "GATEWAY_AUTH_INVALID")
    from stocks.models import VECTOR_DIMENSIONS

    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
    output = runtime / "artifacts" / ("taxonomy-flow-" + suffix)
    output.mkdir(parents=True, exist_ok=False)
    manifest.update(output_dir=str(output), real_jev=args.real_jev, real_jev_call_count=0,
                    external_success=False, status="running", stage="configuration",
                    limits=LIMITS, browser="not exercised; API contracts only")
    counts, source_calls = Counter(), Counter()
    manifest["model_transport_calls"] = counts
    manifest["source_http_calls"] = source_calls

    def step(name):
        manifest["stage"] = name
        write_json(output / "manifest.json", manifest)

    source_id = f"taxonomy_source_{suffix}"
    collect_id, research_id = f"taxonomy_collect_{suffix}", f"taxonomy_research_{suffix}"
    bucket = f"taxonomy-flow-{suffix}"
    feed_items = "".join(
        f"<item><guid>{key}-{suffix}</guid><title>{spec['title']}</title>"
        f"<link>https://source.smoke.test/{key}</link>"
        "<pubDate>Sun, 25 Oct 2026 12:00:00 GMT</pubDate></item>"
        for key, spec in SPECS.items()
    )
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel>'
            '<title>Synthetic taxonomy fixtures</title><link>https://source.smoke.test/</link>'
            '<description>Controlled fictional evidence</description>' + feed_items + '</channel></rss>').encode()
    sources = {"sources": [{
        "source_id": source_id, "name": "Synthetic taxonomy source", "connector": "rss",
        "enabled": True, "publisher": "Synthetic taxonomy fixture", "trust_tier": "T1",
        "timeout_seconds": 5, "max_items": 3, "max_response_bytes": 1000000,
        "max_redirects": 1, "feed_url": "https://source.smoke.test/feed.xml",
        "user_agent": "StocksTaxonomySmoke/1.0 (smoke@example.com)",
    }]}
    shipped = yaml.safe_load((ROOT / "config/tasks.yaml").read_text())
    collect = deepcopy(next(t for t in shipped["tasks"] if t["id"] == "collect_enabled_sources"))
    collect.update(id=collect_id, name="Synthetic three-document collection", sources=[source_id])
    research = deepcopy(next(t for t in shipped["tasks"] if t["id"] == "saved_evidence_brief"))
    research.update(
        id=research_id, name="虚构公司与行业背景证据研究",
        objective="区分 Northstar Silicon（NSIL）显式公司证据、只有公司名的证据与半导体行业背景，逐项引用并说明合成数据边界。",
        strategy="evidence_first",
        scope={"companies": [COMPANY], "symbols": [SYMBOL], "industries": ["semiconductors"], "regions": ["US"]},
    )
    research["steps"][0]["select_evidence"].update(
        queries=["Northstar Silicon NSIL quarterly revenue company commentary",
                 "US semiconductor demand advanced packaging industry background"],
        source_ids=[source_id], limit_per_query=24, max_tokens=30000,
    )
    tasks = {"tasks": [collect, research], "agents": deepcopy(shipped["agents"])}
    require(all(not agent.get("tools") for agent in tasks["agents"]), "EXTERNAL_TOOLS_FORBIDDEN")
    models = yaml.safe_load((ROOT / "config/models.yaml").read_text())
    require(models["agents"]["classification"].get("protocol") == "typesafe", "NATIVE_JEV_REQUIRED")
    require("classification_entities" in models["agents"], "ENTITY_ROLE_REQUIRED")
    for provider in models["providers"].values():
        provider.update(base_url="https://models.smoke.test/v1", api_key="controlled-placeholder", timeout_seconds=45)
    for alias, model in models["models"].items():
        model["name"] = "typesafe/jev-latest" if alias == "classification" else f"controlled-{alias}"
    for agent in models["agents"].values():
        agent["routing"].update(timeout_seconds=45, retries=0, retry_backoff_seconds=0)
    for name, value in (("tasks", tasks), ("models", models), ("sources", sources)):
        (output / f"{name}.yaml").write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
    os.environ.update(
        DATABASE_URL=db_url, S3_BUCKET=bucket,
        TASKS_CONFIG_PATH=str(output / "tasks.yaml"), MODELS_CONFIG_PATH=str(output / "models.yaml"),
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
    from stocks.classification.service import taxonomy_fingerprint
    from stocks.collection.http import SafeFetcher
    from stocks.models import (Analysis, Document, DocumentChunk, DocumentChunkEmbedding,
                               DocumentParseBatch, DocumentParseState, DocumentProcessingState,
                               EvidencePackage, Fact, Report, TaskRun)
    from stocks.s3 import read_snapshot
    from stocks.taxonomy import load_taxonomy
    from stocks.worker import WorkerContext, execute_next_document_stage, execute_next_run

    config, factory = settings(), session_factory()
    taxonomy = load_taxonomy(ROOT / "config/taxonomy.yaml")
    manifest.update(source_id=source_id, collection_task_id=collect_id, research_task_id=research_id,
                    bucket=bucket, model_requested="typesafe/jev-latest",
                    taxonomy_fingerprint=taxonomy_fingerprint(taxonomy),
                    source_mode="controlled fake public HTTPS via SafeFetcher permitted resolver",
                    model_mode="only Jev native live" if args.real_jev else "all controlled",
                    postgres="real task PostgreSQL/pgvector", storage="real task S3 put/get")
    step("preflight")
    with factory() as session:
        require(session.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 1,
                "PGVECTOR_REQUIRED")
        require(session.scalar(select(func.count()).select_from(TaskRun).where(
            TaskRun.status.in_(["queued", "running"]))) == 0, "UNRELATED_ACTIVE_RUNS")
        require(session.scalar(select(func.count()).select_from(DocumentProcessingState).where(
            DocumentProcessingState.status.notin_(["completed", "skipped"]))) == 0, "UNRELATED_DOCUMENT_BACKLOG")
    s3 = boto3.client("s3", endpoint_url=config.s3_endpoint_url, region_name=config.s3_region,
                      aws_access_key_id=getattr(config, "s3_access_key"),
                      aws_secret_access_key=getattr(config, "s3_secret_key"),
                      config=Config(signature_version="s3v4"))
    bucket_args = {"Bucket": bucket}
    if config.s3_region != "us-east-1":
        bucket_args["CreateBucketConfiguration"] = {"LocationConstraint": config.s3_region}
    s3.create_bucket(**bucket_args)
    observed, document_ids = {}, {}

    def source_handler(request):
        require(request.url.scheme == "https" and request.url.host == "source.smoke.test", "SOURCE_URL_FORBIDDEN")
        source_calls[request.url.path] += 1
        if request.url.path == "/feed.xml":
            raw, media = feed, "application/rss+xml"
        else:
            key = request.url.path.removeprefix("/")
            require(key in SPECS, "SOURCE_PATH_FORBIDDEN")
            raw, media = SPECS[key]["raw"], "text/html; charset=utf-8"
        return httpx.Response(200, headers={"content-type": media}, stream=httpx.ByteStream(raw))

    async def model_handler(request):
        require(request.url.scheme == "https" and request.url.host == "models.smoke.test", "MODEL_HOST_FORBIDDEN")
        body = json.loads(request.content)
        if request.url.path == "/v1/systemone":
            counts["jev_native"] += 1
            key = identity_for(body["state"])
            spec = SPECS[key]
            require(spec["lead"] in body["state"]["excerpt"], "JEV_PARSED_EXCERPT_MISSING")
            require(body["model"] == "typesafe/jev-latest", "JEV_MODEL_CHANGED")
            # The current product creates questions and validates the raw response.
            # No answer normalization, replacement, retry or fallback in this harness.
            write_json(output / f"jev-request-{key}.json", body)
            if args.real_jev:
                manifest["real_jev_call_count"] += 1
                require(manifest["real_jev_call_count"] <= 3, "REAL_JEV_CALL_BUDGET")
                step("real_jev_" + key)
                async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False) as gateway:
                    live = await gateway.post(GATEWAY, headers={"Authorization": f"Bearer {gateway_key}"}, json=body)
                # Preserve bytes for the unmodified strict product adapter. Auth
                # headers and the private key never enter artifacts or stdout.
                return httpx.Response(live.status_code, content=live.content,
                                      headers={"content-type": live.headers.get("content-type", "application/json")})
            answers = {}
            choices = {"doc_type": spec["category"], "event_type": spec["event_type"] or "__none__"}
            true_ids = {"should_extract", "topics.growth", "related_industries.semiconductors", "regions.US"}
            for identity, question in body["questions"].items():
                if question["type"] == "choice":
                    selected = choices.get(identity)
                    require(selected in question["criteria"], "FIXTURE_CHOICE_NOT_CANONICAL")
                    answers[identity] = {"type": "choice", "choice": selected, "confidence": 1.0,
                                         "probabilities": {item: float(item == selected) for item in question["criteria"]}}
                else:
                    require(question["type"] == "noul", "UNKNOWN_JEV_PRIMITIVE")
                    answers[identity] = {"type": "noul", "noul": 0.99 if identity in true_ids else 0.01}
            return httpx.Response(200, json={"model": body["model"], "answers": answers,
                                             "usage": {"input_tokens": 100, "output_tokens": 30}})
        if request.url.path == "/v1/embeddings":
            counts["embedding"] += 1
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            # Deterministic vectors exercise PG/vector plumbing, not relevance.
            return httpx.Response(200, json={"model": body["model"], "data": [
                {"index": index, "embedding": [1.0] + [0.0] * (VECTOR_DIMENSIONS - 1)}
                for index in range(len(inputs))]})
        require(request.url.path == "/v1/chat/completions", "MODEL_PATH_FORBIDDEN")
        supplied = json.loads(body["messages"][-1]["content"])
        if "chunks" in supplied:
            counts["fact_extraction"] += 1
            chunk = next((item for item in supplied["chunks"] if STATEMENT in item["text"]), None)
            answer = {"facts": []}
            if chunk:
                observed["extraction_chunk_id"] = chunk["chunk_id"]
                answer["facts"] = [{
                    "fact_type": "company_metric", "chunk_id": chunk["chunk_id"],
                    "subject_name": COMPANY, "symbol": SYMBOL, "metric_id": "revenue", "statement": STATEMENT,
                    "value": 42, "unit": "million USD", "period_start": "2026-07-01T00:00:00Z",
                    "period_end": "2026-09-30T23:59:59Z",
                }]
        elif "evidence" in supplied:
            counts["research"] += 1
            observed["research_input"] = supplied
            write_json(output / "research-input.json", supplied)
            evidence = supplied["evidence"]
            require(set(document_ids.values()).issubset({item["document_id"] for item in evidence}),
                    "RESEARCH_INPUT_MUST_INCLUDE_ALL_THREE_DOCUMENTS")
            refs = []
            for key in SPECS:
                candidates = [item for item in evidence if item["document_id"] == document_ids[key]]
                chosen = next((item for item in candidates if item["chunk_id"] == observed.get("extraction_chunk_id")), candidates[0])
                refs.append({"chunk_id": chosen["chunk_id"], "document_id": chosen["document_id"]})
            observed["citations"] = dict(zip(SPECS, refs))
            answer = {
                "title": "虚构半导体公司证据与行业背景的研究边界",
                "summary": "本批虚构证据同时覆盖显式公司收入、仅公司名称的定性说明及半导体行业背景。行业关联不能证明原文提及公司或代码。" + LIMITS,
                "sections": [
                    {"heading": "公司证据", "body": "合成财报明确写出 Northstar Silicon（NSIL）季度收入为 42 million USD；第二篇只出现公司名，不能补推代码，也没有可提取的数值。", "finding_ids": ["revenue", "name_only"]},
                    {"heading": "行业背景与解释边界", "body": "背景材料描述美国半导体需求和先进封装约束，可辅助理解产业环境；其中没有公司或代码，不能用于断言特定公司受益。", "finding_ids": ["background", "relationship"]},
                    {"heading": "数据与方法局限", "body": LIMITS + "固定向量仅验证检索和引用管道，不能证明语义排序质量；未接入真实证券主数据或真实行情。", "finding_ids": []},
                ],
                "findings": [
                    {"id": "revenue", "title": "虚构季度收入记录", "kind": "fact",
                     "statement": "虚构公司财报原文明示 NSIL 和季度收入 42 million USD，数值事实只归属该版本。",
                     "rationale": "原文及保存的 Fact 引用同一条记录。", "uncertainty": LIMITS, "evidence_refs": [refs[0]]},
                    {"id": "name_only", "title": "只有公司名的定性材料", "kind": "fact",
                     "statement": "公司名称材料只支持定性路线图说明，没有股票代码或数值，不能推断 NSIL。",
                     "rationale": "仅按该篇原文明示实体解释证据。", "uncertainty": "无法从这篇材料提取数值或验证公司表现。", "evidence_refs": [refs[1]]},
                    {"id": "background", "title": "没有公司实体的行业背景", "kind": "fact",
                     "statement": "纯行业材料支持半导体需求与封装背景讨论，不证明文中出现任何公司或代码。",
                     "rationale": "行业关联标签和实际公司实体是不同的证据关系。", "uncertainty": "不能据此判断某家公司受益。", "evidence_refs": [refs[2]]},
                    {"id": "relationship", "title": "产业约束与公司经营的待验证关系", "kind": "model_inference",
                     "statement": "公司材料和行业背景可以并列用于研究，行业约束对公司经营的影响仍是待核实假设。",
                     "rationale": "并列证据提供讨论背景，并未建立因果联系。", "uncertainty": LIMITS, "evidence_refs": [refs[0], refs[2]]},
                ],
                "open_questions": ["真实公司与行业数据尚未提供；需要独立来源验证后才可形成现实投资判断。"],
                "evidence_refs": refs,
            }
            write_json(output / "controlled-research-response.json", answer)
        else:
            counts["classification_entities"] += 1
            key = identity_for(supplied)
            spec = SPECS[key]
            evidence = [{"kind": "company", "value": name, "quote": spec["lead"]} for name in spec["companies"]]
            evidence += [{"kind": "symbol", "value": symbol, "quote": spec["lead"]} for symbol in spec["symbols"]]
            answer = {"companies": spec["companies"], "symbols": spec["symbols"], "evidence": evidence,
                      "reason": "受控实体响应只返回当前原文明示的公司和代码；公司名与行业关联不能用于推断代码。"}
        return httpx.Response(200, json={"model": body["model"], "choices": [
            {"message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}})

    source_transport, model_transport = httpx.MockTransport(source_handler), httpx.MockTransport(model_handler)
    worker = WorkerContext(session_factory=factory, settings=config,
                           task_config_path=config.tasks_config_path,
                           model_config_path=config.models_config_path,
                           sources_config_path=config.sources_config_path,
                           model_transport=model_transport)
    with patch("stocks.collection.service.SafeFetcher", side_effect=lambda: SafeFetcher(
        resolver=lambda host, port: ["93.184.216.34"], transport=source_transport)):
        with TestClient(app) as client:
            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                require(response.status_code < 400, f"API_{method}_{path}_{response.status_code}")
                return response

            login = request("POST", "/api/session/login", json={"password": os.environ["OWNER_PASSWORD"]}).json()
            client.headers.update({"X-CSRF-Token": login["session"]["csrf_token"]})
            run_id = request("POST", f"/api/tasks/{collect_id}/runs").json()["run_id"]
            manifest["collection_run_id"] = run_id
            result = execute_next_run(worker, process_token=f"taxonomy-{suffix}-collect")
            require(result and result["run_id"] == run_id and result["status"] == "succeeded", "COLLECTION_FAILED")
            with factory() as session:
                documents = list(session.scalars(select(Document).where(Document.source_id == source_id)))
                require(len(documents) == 3, "EXACT_THREE_SAME_BATCH_DOCUMENTS_REQUIRED")
                manifest["documents"] = {}
                for document in documents:
                    key = identity_for({"document": {"title": document.title}})
                    raw = SPECS[key]["raw"]
                    require(document.version == 1 and document.completeness == "complete", "DOCUMENT_VERSION_OR_COMPLETENESS")
                    require(document.content_hash == hashlib.sha256(raw).hexdigest(), "DOCUMENT_RAW_HASH")
                    require(read_snapshot(config, document.snapshot_key) == raw, "S3_BYTES_MISMATCH")
                    document_ids[key] = str(document.id)
                    manifest["documents"][key] = {"document_id": str(document.id), "version": document.version,
                                                   "content_hash": document.content_hash, "snapshot_key": document.snapshot_key}
            step("three_documents_stored")
            for _ in range(40):
                with factory() as session:
                    states = list(session.scalars(select(DocumentProcessingState).where(
                        DocumentProcessingState.document_id.in_([UUID(value) for value in document_ids.values()]))))
                    view = {key: {s.stage: {"status": s.status, "attempts": s.attempts,
                                          "error_code": (s.error or {}).get("code"), "wait_reason": s.wait_reason}
                                  for s in states if str(s.document_id) == document_id}
                            for key, document_id in document_ids.items()}
                manifest["processing"] = view
                require(not any(s.status == "failed" for s in states), "DOCUMENT_STAGE_FAILED")
                if all(set(stages) == {"parse", "index", "classify", "facts"} and
                       all(value["status"] in {"completed", "skipped"} for value in stages.values()) for stages in view.values()):
                    break
                execution = execute_next_document_stage(worker, process_token=f"taxonomy-{suffix}-document")
                require(execution and execution.get("status") != "failed", "DOCUMENT_PROCESSING_STALLED_OR_FAILED")
                step("processing_" + execution["stage"])
            else:
                raise SmokeFailure("DOCUMENT_STAGE_BUDGET_EXHAUSTED")

            classifications = {}
            with factory() as session:
                for key, document_id in document_ids.items():
                    document = session.get(Document, UUID(document_id))
                    cls = document.classification or {}
                    classifications[key] = cls
                    # Persist raw stored provenance before asserting model semantics.
                    write_json(output / "classification.json", classifications)
                    spec = SPECS[key]
                    require(cls.get("companies") == spec["companies"] and cls.get("symbols") == spec["symbols"], "EXACT_ENTITIES_" + key)
                    require(cls.get("category") == spec["category"], "CATEGORY_" + key)
                    require(cls.get("event_type") is None or cls["event_type"] in taxonomy.event_type_ids, "CANONICAL_EVENT_" + key)
                    for field, section in (("topics", "topics"), ("related_industries", "industries"), ("regions", "regions")):
                        values = cls.get(field, [])
                        require(isinstance(values, list) and len(values) == len(set(values)) and
                                set(values).issubset({label.id for label in getattr(taxonomy, section)}), "CANONICAL_LABELS_" + key)
                    require("semiconductors" in cls["related_industries"] and "US" in cls["regions"], "EXPECTED_INDUSTRY_REGION_" + key)
                    if not args.real_jev:
                        require(cls["topics"] == ["growth"] and cls["related_industries"] == ["semiconductors"] and
                                cls["regions"] == ["US"] and cls.get("event_type") == spec["event_type"] and
                                cls.get("should_extract") is True, "CONTROLLED_LABELS_" + key)
                    require(cls.get("requested_model_name") == "typesafe/jev-latest" and cls.get("model_alias") == "classification", "MODEL_IDENTITY_" + key)
                    require(cls.get("reported_model_name") and cls.get("taxonomy_fingerprint") == taxonomy_fingerprint(taxonomy) and
                            cls.get("taxonomy_version") == taxonomy.version, "MODEL_TAXONOMY_PROVENANCE_" + key)
                    calls = {call.get("role"): call for call in cls.get("model_calls", [])}
                    require(set(calls) == {"classification", "classification_entities"}, "ROLE_PROVENANCE_" + key)
                    require(calls["classification"].get("protocol") == "typesafe" and calls["classification"].get("answers") and
                            calls["classification_entities"].get("reported_model_name") == "controlled-research", "ROLE_MODEL_TRACE_" + key)
                    parse = session.get(DocumentParseState, UUID(document_id))
                    require(parse and parse.status == "completed" and parse.current_batch_id, "CURRENT_PARSE_" + key)
                    batch = session.get(DocumentParseBatch, parse.current_batch_id)
                    require(batch and batch.content_hash == document.content_hash and batch.status == "completed", "PARSE_VERSION_HASH_" + key)
                    chunks = list(session.scalars(select(DocumentChunk).where(DocumentChunk.parse_batch_id == parse.current_batch_id)))
                    require(chunks and any(spec["lead"] in chunk.text for chunk in chunks), "PARSED_ARTICLE_TEXT_" + key)
                    chunk_ids = {str(chunk.id) for chunk in chunks}
                    provenance = cls.get("reason_or_evidence_ref", {})
                    require(provenance.get("parse_batch_id") == str(parse.current_batch_id) and
                            bool(provenance.get("chunk_ids")) and set(provenance["chunk_ids"]).issubset(chunk_ids) and
                            provenance.get("truncated") is False, "CLASSIFICATION_CHUNK_PROVENANCE_" + key)
                    expected_entities = {("company", value) for value in spec["companies"]} | {("symbol", value) for value in spec["symbols"]}
                    require({(item["kind"], item["value"]) for item in provenance.get("entity_evidence", [])} == expected_entities, "ENTITY_EVIDENCE_" + key)
                    embedding_rows = session.scalar(select(func.count()).select_from(DocumentChunkEmbedding).where(
                        DocumentChunkEmbedding.chunk_id.in_([chunk.id for chunk in chunks])))
                    require(embedding_rows == len(chunks), "VECTOR_ROWS_" + key)
                    stages = view[key]
                    require(all(stages[stage]["status"] == "completed" for stage in ("parse", "index", "classify")), "REQUIRED_STAGES_" + key)
                    require(stages["facts"]["status"] == ("completed" if cls["should_extract"] else "skipped"), "FACT_APPLICABILITY_" + key)
                    facts = list(session.scalars(select(Fact).where(Fact.document_id == UUID(document_id))))
                    if key == "explicit":
                        require(cls["should_extract"] is True and len(facts) == 1, "NUMERIC_DOCUMENT_FACT_REQUIRED")
                        fact = facts[0]
                        require(fact.value == 42 and fact.unit == "million USD" and fact.symbol == SYMBOL and
                                fact.subject_name == COMPANY and fact.metric_id == "revenue" and fact.statement == STATEMENT and
                                fact.origin_method == "llm_extraction", "NUMERIC_FACT_VALUE_IDENTITY")
                        require(fact.evidence_ref["chunk_id"] == observed.get("extraction_chunk_id") and
                                fact.evidence_ref["chunk_id"] in chunk_ids, "NUMERIC_FACT_CITATION")
                        manifest["fact_id"] = str(fact.id)
                    else:
                        require(not facts, "NONNUMERIC_DOCUMENT_MUST_NOT_INVENT_FACTS_" + key)
                    manifest["documents"][key].update(parse_batch_id=str(parse.current_batch_id), chunk_ids=sorted(chunk_ids),
                                                       embedding_rows=embedding_rows, fact_ids=[str(fact.id) for fact in facts])
            step("document_classification_fact_and_vectors_verified")
            run_id = request("POST", f"/api/tasks/{research_id}/runs").json()["run_id"]
            manifest["research_run_id"] = run_id
            result = execute_next_run(worker, process_token=f"taxonomy-{suffix}-research")
            require(result and result["run_id"] == run_id and result["status"] == "succeeded", "RESEARCH_FAILED")
            with factory() as session:
                package = session.scalar(select(EvidencePackage).where(EvidencePackage.run_id == UUID(run_id)))
                analysis = session.scalar(select(Analysis).where(Analysis.run_id == UUID(run_id)))
                report = session.scalar(select(Report).where(Report.run_id == UUID(run_id)))
                require(package and analysis and report and analysis.findings and analysis.sections, "PERSISTED_RESEARCH_CHAIN")
                require(set(document_ids.values()).issubset({group["document_id"] for group in package.evidence_groups}), "FROZEN_COMPANY_AND_BACKGROUND")
                require(any(group["document_id"] == document_ids["explicit"] and
                            manifest["fact_id"] in {fact["fact_id"] for fact in group.get("fact_refs", [])}
                            for group in package.evidence_groups), "FROZEN_NUMERIC_FACT")
                manifest.update(evidence_package_id=str(package.id), analysis_id=str(analysis.id), report_id=str(report.id))
                write_json(output / "evidence-package.json", {"selected_chunks": package.selected_chunks, "evidence_groups": package.evidence_groups})
            detail = request("GET", f"/api/reports/{manifest['report_id']}").json()
            write_json(output / "report-detail.json", detail)
            citation_proof = {}
            for key, ref in observed["citations"].items():
                chunk_id = ref["chunk_id"]
                require(chunk_id in detail["cited_chunk_ids"], "REPORT_CITATION_" + key)
                evidence = request("GET", f"/api/reports/{manifest['report_id']}/evidence/{chunk_id}").json()["evidence"]
                require(evidence["document_id"] == document_ids[key] and evidence["chunk_available"] and
                        evidence["version_matches"] and evidence["document_version"] == 1, "CITATION_VERSION_" + key)
                require(chunk_id in manifest["documents"][key]["chunk_ids"] and evidence["text"], "CITATION_CHUNK_" + key)
                original = request("GET", evidence["original"]["endpoint"])
                require(original.content == SPECS[key]["raw"] and original.headers.get("x-content-hash-match") == "true" and
                        hashlib.sha256(original.content).hexdigest() == manifest["documents"][key]["content_hash"], "CITED_ORIGINAL_HASH_" + key)
                download = request("GET", evidence["original"]["endpoint"], params={"download": True})
                require(download.content == original.content and "attachment" in download.headers.get("content-disposition", ""), "ORIGINAL_DOWNLOAD_" + key)
                citation_proof[key] = {"evidence": evidence, "original_hash_verified": True, "download_verified": True}
                write_json(output / "citation-proof.json", citation_proof)
            require(source_calls == Counter({"/feed.xml": 1, "/explicit": 1, "/name_only": 1, "/background": 1}), "SOURCE_COUNTS")
            require(counts["jev_native"] == 3 and counts["classification_entities"] == 3 and counts["research"] == 1 and
                    counts["embedding"] >= 1 and counts["fact_extraction"] >= 1, "MODEL_ROLE_COUNTS")
            require(manifest["real_jev_call_count"] == (3 if args.real_jev else 0), "LIVE_CALL_COUNTS")
    manifest.update(status="passed", external_success=False, cleanup="task bucket and database rows retained for parent; no cleanup performed")
    step("complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True,
                        help="Remote task root containing checkout and private gateway-auth.json")
    parser.add_argument("--real-jev", action="store_true", help="Forward ONLY native Jev to the fixed loopback gateway")
    args = parser.parse_args()
    manifest = {"status": "failed", "stage": "remote_preflight", "external_success": False,
                "real_jev": args.real_jev, "real_jev_call_count": 0}
    try:
        main_run(args, manifest)
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, SmokeFailure):
            manifest["error_code"] = str(exc)
        output = manifest.get("output_dir")
        if output:
            write_json(Path(output) / "manifest.json", manifest)
        # Do not print exception messages, response bodies, URLs or credentials.
        print(json.dumps({"status": "failed", "stage": manifest["stage"], "error_type": type(exc).__name__,
                          "error_code": manifest.get("error_code"), "output_dir": output,
                          "external_success": False, "real_jev": args.real_jev,
                          "real_jev_call_count": manifest["real_jev_call_count"]}))
        return 1
    print(json.dumps({"status": "passed", "manifest": str(Path(manifest["output_dir"]) / "manifest.json"),
                      "external_success": False, "real_jev": args.real_jev,
                      "real_jev_call_count": manifest["real_jev_call_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
