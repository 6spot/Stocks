#!/usr/bin/env python3
"""Owner-confirmed recovery drill for an interrupted Worker process.

This is **not** a pytest module: it drives real subprocesses against the current
task database on the isolated test server, so it is only executed by hand there.

What it proves end to end (AC-3 of V1-01):

1. a real Worker process claims a queued Run, executes the first step and
   persists it, then blocks inside the Research Agent model call;
2. ``SIGKILL`` of that process leaves the Run ``running`` with the succeeded
   step stored and no fabricated result for the blocked step;
3. the Owner must confirm the old process stopped before ending the Run, and a
   ``running`` Run cannot be retried;
4. after ``end`` + ``retry`` a new Worker process finishes the Run by reusing the
   copied step and its Evidence snapshot, without re-running Evidence Selection
   (no second embedding request) and without duplicating Repository rows;
5. the ended Run keeps its own history: step, Evidence Package and error.

The model transport is injected in-process with ``httpx.MockTransport``, so no
real Provider or external network is used. The Repository seed uses the same
production ingest/parse/embedding entry points a Worker would have produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stocks.embedding_space import VECTOR_DIMENSIONS
from stocks.models import (
    Analysis,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    EvidencePackage,
    Report,
    Source,
    TaskRun,
    TaskStepResult,
)
from stocks.repository import (
    DocumentInput,
    ParseChunkInput,
    ingest_document,
    persist_embedding_result,
    persist_parse_result,
)
from stocks.settings import Settings, settings

TASK_ID = "saved_evidence_brief"
SOURCE_ID = "ac3_fixed_source"
DEDUPE_KEY = "ac3-fixed-document"
CHUNK_TEXTS = (
    "Inflation declined to 2.4 percent in September.",
    "The central bank kept its policy rate unchanged.",
)
SIGNAL_SECONDS = 90.0
# Config paths in the CLI/Worker defaults are relative to the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _config() -> Settings:
    return settings()


def _vector() -> tuple[float, ...]:
    return (1.0,) + (0.0,) * (VECTOR_DIMENSIONS - 1)


def _store(config, source_id, payload, *, content_type):
    digest = hashlib.sha256(payload).hexdigest()
    return f"memory://{source_id}/{digest}", digest


def _embedding_model_name() -> str:
    name = os.environ.get("EMBEDDING_MODEL_NAME")
    if not name:
        raise SystemExit("EMBEDDING_MODEL_NAME must be set for the drill")
    return name


def seed() -> dict:
    """Ensure one fixed Source/Document/Chunk/Embedding set exists.

    Idempotent on purpose: repeated drills must reuse the same Repository rows
    instead of creating new Document versions, so an earlier partial seed is
    completed rather than skipped.
    """

    from stocks.repository import EmbeddingInput, find_parse_batch, get_embedding_state

    config = _config()
    now = datetime.now(UTC)
    with Session(_engine()) as session:
        if session.get(Source, SOURCE_ID) is None:
            session.add(
                Source(
                    id=SOURCE_ID,
                    name="AC-3 fixed source",
                    connector="rss",
                    publisher="AC-3 fixed publisher",
                    trust_tier="T1",
                )
            )
            session.commit()
        payload = (
            "<html><body><article><p>"
            + "</p><p>".join(CHUNK_TEXTS)
            + "</p></article></body></html>"
        ).encode()
        ingested = ingest_document(
            session,
            config,
            DocumentInput(
                source_id=SOURCE_ID,
                dedupe_key=DEDUPE_KEY,
                title="AC-3 fixed document",
                payload=payload,
                content_type="text/html",
                collected_at=now,
                original_url="https://example.test/ac3-fixed-document",
                published_at=now,
                summary=None,
                completeness="complete",
                fetch_error=None,
            ),
            store=_store,
        )
        document = ingested.document
        batch = find_parse_batch(
            session,
            document.id,
            processor_version="ac3-drill",
            config_fingerprint="a" * 64,
        )
        if batch is None:
            batch = persist_parse_result(
                session,
                document.id,
                processor_version="ac3-drill",
                config_fingerprint="a" * 64,
                content_hash=document.content_hash,
                status="completed",
                error=None,
                chunks=tuple(
                    ParseChunkInput(text=text, locator={"segments": []})
                    for text in CHUNK_TEXTS
                ),
                attempted_at=now,
            ).batch
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.parse_batch_id == batch.id)
                .order_by(DocumentChunk.position)
            ).all()
        )
        state = get_embedding_state(session, document.id)
        indexed = (
            state is not None
            and state.status == "completed"
            and state.parse_batch_id == batch.id
            and state.model_name == _embedding_model_name()
        )
        if not indexed:
            persist_embedding_result(
                session,
                document.id,
                parse_batch_id=batch.id,
                model_alias="embedding",
                model_name=_embedding_model_name(),
                dimensions=VECTOR_DIMENSIONS,
                status="completed",
                embeddings=tuple(
                    EmbeddingInput(chunk_id=chunk.id, vector=_vector())
                    for chunk in chunks
                ),
                attempted_at=now,
                error=None,
            )
        return {
            "seeded": ingested.created,
            "embedding_created": not indexed,
            "document_id": str(document.id),
            "document_version": document.version,
            "parse_batch_id": str(batch.id),
            "chunk_ids": [str(chunk.id) for chunk in chunks],
        }


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["DATABASE_URL"])


def reset() -> dict:
    """Clear this drill Task's Run/Report rows so counts start from a known state.

    Only rows belonging to Runs of ``TASK_ID`` are removed, so a shared database
    never loses another Task's history. The reset refuses to run while this Task
    still has a ``queued``/``running`` Run: that would delete live data the Owner
    has not ended. Repository rows (Documents/Chunks/Embeddings) are left
    untouched — the drill must reuse them across the interruption, not recreate
    them.
    """

    from sqlalchemy import delete

    from stocks.models import ModelCall

    with Session(_engine()) as session:
        run_ids = list(
            session.scalars(
                select(TaskRun.id).where(TaskRun.task_id == TASK_ID)
            ).all()
        )
        active = session.scalar(
            select(TaskRun)
            .where(
                TaskRun.task_id == TASK_ID,
                TaskRun.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        if active is not None:
            raise SystemExit(
                "refusing to reset: Task "
                f"{TASK_ID} still has an active Run "
                f"{active.id} ({active.status}); end it with "
                "`python -m stocks.execution end --run-id <id> --confirm-stopped` "
                "first"
            )
        deleted: dict[str, int] = {}
        # Child rows first: these FKs point at the Runs being removed.
        for model in (Report, Analysis, EvidencePackage, ModelCall, TaskStepResult):
            statement = delete(model).where(model.run_id.in_(run_ids))
            deleted[model.__tablename__] = (
                session.execute(statement).rowcount if run_ids else 0
            )
        deleted[TaskRun.__tablename__] = (
            session.execute(
                delete(TaskRun).where(TaskRun.task_id == TASK_ID)
            ).rowcount
            if run_ids
            else 0
        )
        session.commit()
    return {"deleted": deleted, "task_id": TASK_ID, "runs_removed": len(run_ids)}


def _write_calls(path: Path | None, entries: list[dict]) -> None:
    if path is None:
        return
    path.write_text(json.dumps(entries), encoding="utf-8")


def _handle_embeddings(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    inputs = body.get("input")
    if not isinstance(inputs, list):
        return httpx.Response(400, json={"error": "invalid input"})
    data = [
        {"index": index, "embedding": list(_vector())}
        for index in range(len(inputs))
    ]
    return httpx.Response(200, json={"model": body.get("model"), "data": data})


def _handle_chat(
    request: httpx.Request, behavior: str, signal_path: Path | None
) -> httpx.Response:
    body = json.loads(request.content)
    messages = body.get("messages")
    context = {}
    if isinstance(messages, list) and messages:
        try:
            context = json.loads(messages[-1]["content"])
        except (KeyError, TypeError, ValueError):
            context = {}
    if behavior == "block":
        if signal_path is not None:
            signal_path.write_text(
                json.dumps({"blocked_at": datetime.now(UTC).isoformat()}),
                encoding="utf-8",
            )
        # Stay inside the Agent step until the Owner's drill kills this process.
        while True:
            time.sleep(5)
    evidence = context.get("evidence") or []
    if not evidence:
        return httpx.Response(400, json={"error": "no evidence"})
    first = evidence[0]
    ref = {
        "chunk_id": first["chunk_id"],
        "document_id": first["document_id"],
    }
    output = {
        "summary": "Seeded inflation evidence was reused after Owner recovery.",
        "findings": [
            {
                "kind": "fact",
                "statement": first.get("text", "Inflation declined to 2.4 percent."),
                "rationale": "Directly quoted from the selected saved Chunk.",
                "uncertainty": "Single fixed document; coverage is limited.",
                "evidence_refs": [ref],
            }
        ],
        "open_questions": ["No further fixed evidence is available."],
        "evidence_refs": [ref],
    }
    return httpx.Response(
        200,
        json={
            "model": body.get("model"),
            "choices": [{"message": {"content": json.dumps(output)}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
            },
        },
    )


def make_transport(
    behavior: str, signal_path: Path | None, calls_path: Path | None
):
    entries: list[dict] = []
    if calls_path is not None and calls_path.exists():
        calls_path.unlink()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        entries.append({"path": path})
        _write_calls(calls_path, entries)
        if path.endswith("/embeddings"):
            return _handle_embeddings(request)
        if path.endswith("/chat/completions"):
            return _handle_chat(request, behavior, signal_path)
        return httpx.Response(404, json={"error": "unexpected path"})

    return httpx.MockTransport(handler)


def work(behavior: str, signal_path: str | None, calls_path: str | None) -> int:
    import stocks.worker as worker

    transport = make_transport(
        behavior,
        Path(signal_path) if signal_path else None,
        Path(calls_path) if calls_path else None,
    )
    context = worker.WorkerContext.from_settings(
        model_transport=transport,
        max_concurrent_runs=1,
    )
    payload = worker.run_once(context)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def mock_provider(port: int, calls_path: str | None) -> int:
    """Serve the two Provider paths locally for a real Worker CLI smoke run.

    The Worker reaches this over real HTTP on ``127.0.0.1`` only, so no external
    Provider or network is involved while the real ``python -m stocks.worker``
    process exercises the configured OpenAI-compatible protocol.
    """

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = Path(calls_path) if calls_path else None
    entries: list[dict] = []
    if calls is not None and calls.exists():
        calls.unlink()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # noqa: A003 - silence stdlib logs
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except ValueError:
                self._send(400, {"error": "invalid json"})
                return
            entries.append({"path": self.path})
            _write_calls(calls, entries)
            if self.path.endswith("/embeddings"):
                inputs = body.get("input") or []
                self._send(
                    200,
                    {
                        "model": body.get("model"),
                        "data": [
                            {"index": index, "embedding": list(_vector())}
                            for index in range(len(inputs))
                        ],
                    },
                )
                return
            if self.path.endswith("/chat/completions"):
                response = _handle_chat(
                    httpx.Request("POST", "http://mock/chat/completions", content=raw),
                    "respond",
                    None,
                )
                self._send(
                    200,
                    json.loads(response.content),
                )
                return
            self._send(404, {"error": "unexpected path"})

        def _send(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"listening": f"127.0.0.1:{port}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _cli(args: list[str], *, timeout: float = 60.0) -> dict:
    process = subprocess.run(
        [sys.executable, "-m", "stocks.execution", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )
    return {
        "command": " ".join(args),
        "exit_code": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }


def _payload(command: dict) -> dict:
    try:
        return json.loads(command["stdout"]) if command["stdout"] else {}
    except ValueError:
        return {}


def _step_summary(payload: dict) -> list[dict]:
    return [
        {
            "step_id": item["step_id"],
            "status": item["status"],
            "evidence_refs": len(item.get("evidence_refs") or []),
            "evidence_package_id": (item.get("output") or {}).get(
                "evidence_package_id"
            ),
            "error": item.get("error"),
        }
        for item in payload.get("steps", [])
    ]


def _db_state(run_id: str) -> dict:
    with Session(_engine()) as session:
        run_uuid = uuid.UUID(run_id)
        run = session.get(TaskRun, run_uuid)
        if run is None:
            return {"run": None}
        steps = list(
            session.scalars(
                select(TaskStepResult)
                .where(TaskStepResult.run_id == run_uuid)
                .order_by(TaskStepResult.position)
            ).all()
        )
        package = session.scalar(
            select(EvidencePackage).where(EvidencePackage.run_id == run_uuid)
        )
        analysis = session.scalar(
            select(Analysis).where(Analysis.run_id == run_uuid)
        )
        report = session.scalar(select(Report).where(Report.run_id == run_uuid))
        return {
            "run": {
                "run_id": str(run.id),
                "status": run.status,
                "triggered_by": run.triggered_by,
                "retry_of_run_id": (
                    str(run.retry_of_run_id) if run.retry_of_run_id else None
                ),
                "process_token_present": run.process_token is not None,
                "error": run.error,
                "started_at": run.started_at.isoformat()
                if run.started_at
                else None,
                "finished_at": run.finished_at.isoformat()
                if run.finished_at
                else None,
            },
            "steps": [
                {
                    "step_id": step.step_id,
                    "position": step.position,
                    "status": step.status,
                    "error": step.error,
                    "created_at": step.created_at.isoformat(),
                    "evidence_refs": step.evidence_refs,
                    "output_keys": sorted((step.output or {}).keys()),
                }
                for step in steps
            ],
            "package": None
            if package is None
            else {
                "evidence_package_id": str(package.id),
                "selected_count": package.selected_count,
                "chunk_ids": [
                    item.get("chunk_id")
                    for item in package.selected_chunks
                ],
                "document_ids": [
                    item.get("document_id")
                    for item in package.selected_chunks
                ],
                "parse_batch_ids": [
                    item.get("parse_batch_id")
                    for item in package.selected_chunks
                ],
            },
            "analysis_id": str(analysis.id) if analysis else None,
            "report_id": str(report.id) if report else None,
            "report_has_content": bool(report and report.content),
        }


def _counts() -> dict:
    with Session(_engine()) as session:
        return {
            "documents": session.scalar(
                select(func.count()).select_from(Document)
            ),
            "chunks": session.scalar(
                select(func.count()).select_from(DocumentChunk)
            ),
            "embeddings": session.scalar(
                select(func.count()).select_from(DocumentChunkEmbedding)
            ),
            "evidence_packages": session.scalar(
                select(func.count()).select_from(EvidencePackage)
            ),
            "runs": session.scalar(select(func.count()).select_from(TaskRun)),
        }


def _wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.2)
    return False


def _read_calls(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return [item["path"] for item in json.loads(path.read_text())]
    except (ValueError, KeyError, TypeError):
        return []


def drive(workdir: Path) -> int:
    report: dict = {"workdir": str(workdir), "checks": {}, "commands": []}
    workdir.mkdir(parents=True, exist_ok=True)
    signal_path = workdir / "agent-blocked.json"
    calls_path = workdir / "worker-calls.json"
    log_path = workdir / "worker.log"
    for path in (signal_path, calls_path):
        if path.exists():
            path.unlink()

    report["seed"] = seed()
    report["reset"] = reset()
    report["counts_before"] = _counts()
    trigger = _cli(["trigger", "--task", TASK_ID])
    report["commands"].append(trigger)
    payload = _payload(trigger)
    run_id = payload.get("run_id")
    if trigger["exit_code"] != 0 or not run_id:
        report["result"] = "trigger_failed"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["run_id"] = run_id
    report["triggered_status"] = payload.get("status")

    script = Path(__file__).resolve()
    log = log_path.open("w", encoding="utf-8")
    worker_process = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "worker",
            "--behavior",
            "block",
            "--signal-file",
            str(signal_path),
            "--calls-file",
            str(calls_path),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=REPO_ROOT,
    )
    blocked = _wait_for(signal_path, SIGNAL_SECONDS)
    report["checks"]["agent_step_blocked"] = blocked
    report["state_while_blocked"] = _db_state(run_id)
    report["calls_while_blocked"] = _read_calls(calls_path)
    if not blocked:
        worker_process.kill()
        worker_process.wait(timeout=30)
        log.close()
        report["worker_log"] = log_path.read_text()[-3000:]
        report["result"] = "agent_step_never_started"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    retry_while_running = _cli(["retry", "--run-id", run_id])
    report["commands"].append(retry_while_running)
    report["checks"]["retry_refused_while_running"] = (
        retry_while_running["exit_code"] == 2
        and "RUN_NOT_ENDED" in retry_while_running["stderr"]
    )
    end_without_confirmation = _cli(["end", "--run-id", run_id])
    report["commands"].append(end_without_confirmation)
    report["checks"]["end_requires_confirmation"] = (
        end_without_confirmation["exit_code"] == 2
        and "OWNER_CONFIRMATION_REQUIRED" in end_without_confirmation["stderr"]
    )

    # Simulate a hard Worker interruption: the Owner confirms the process is gone.
    os.kill(worker_process.pid, signal.SIGKILL)
    killed_exit = worker_process.wait(timeout=30)
    log.close()
    report["worker_exit_code"] = killed_exit
    report["checks"]["worker_sigkilled"] = killed_exit == -signal.SIGKILL
    # Let PostgreSQL terminate the killed backend's session and release its
    # transaction before the Owner ends the Run.
    time.sleep(2)
    report["state_after_kill"] = _db_state(run_id)

    show_after_kill = _cli(["show", "--run-id", run_id])
    report["commands"].append(show_after_kill)
    report["show_after_kill"] = {
        "status": _payload(show_after_kill).get("status"),
        "steps": _step_summary(_payload(show_after_kill)),
    }

    ended = _cli(["end", "--run-id", run_id, "--confirm-stopped"])
    report["commands"].append(ended)
    ended_payload = _payload(ended)
    report["ended_status"] = ended_payload.get("status")
    report["ended_error"] = ended_payload.get("error")
    report["checks"]["ended_by_owner"] = (
        ended["exit_code"] == 0
        and ended_payload.get("status") == "cancelled"
        and (ended_payload.get("error") or {}).get("code")
        == "RUN_ENDED_BY_OWNER"
    )
    report["state_after_end"] = _db_state(run_id)

    retried = _cli(["retry", "--run-id", run_id])
    report["commands"].append(retried)
    retried_payload = _payload(retried)
    retry_run_id = retried_payload.get("run_id")
    report["retry_run_id"] = retry_run_id
    report["checks"]["retry_queued"] = (
        retried["exit_code"] == 0
        and retried_payload.get("status") == "queued"
        and retried_payload.get("triggered_by") == "retry"
        and retried_payload.get("retry_of_run_id") == run_id
        and retry_run_id != run_id
    )
    report["state_retry_before_worker"] = (
        _db_state(retry_run_id) if retry_run_id else None
    )

    if retry_run_id:
        run_worker = subprocess.run(
            [
                sys.executable,
                str(script),
                "worker",
                "--behavior",
                "respond",
                "--calls-file",
                str(calls_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=REPO_ROOT,
        )
        report["retry_worker_exit_code"] = run_worker.returncode
        report["retry_worker_stdout"] = run_worker.stdout.strip()
        report["retry_worker_stderr"] = run_worker.stderr.strip()
        report["retry_worker_calls"] = _read_calls(calls_path)
        report["state_retry_after_worker"] = _db_state(retry_run_id)
        report["state_original_after_retry"] = _db_state(run_id)
        report["counts_after"] = _counts()

        retry_state = report["state_retry_after_worker"]
        original_state = report["state_after_end"]
        retry_steps = {
            step["step_id"]: step for step in retry_state["steps"]
        }
        checks = report["checks"]
        checks["retry_worker_claims_run"] = run_worker.returncode == 0
        checks["retry_run_succeeded"] = retry_state["run"]["status"] == "succeeded"
        checks["retry_run_has_report"] = bool(
            retry_state["analysis_id"] and retry_state["report_id"]
        )
        checks["retry_reused_succeeded_step"] = (
            retry_steps.get("evidence", {}).get("status") == "succeeded"
            and "research" in retry_steps
        )
        retry_before = report["state_retry_before_worker"] or {}
        checks["retry_step_snapshot_copied_before_worker_run"] = bool(
            [step["step_id"] for step in retry_before.get("steps") or []]
            == ["evidence"]
            and (retry_before.get("steps") or [{}])[0].get("status")
            == "succeeded"
            and (retry_before.get("package") or {}).get("chunk_ids")
            == (original_state["package"] or {}).get("chunk_ids")
        )
        checks["evidence_selection_not_rerun"] = not any(
            path.endswith("/embeddings") for path in report["retry_worker_calls"]
        )
        checks["retry_agent_step_ran_once"] = (
            sum(
                1
                for path in report["retry_worker_calls"]
                if path.endswith("/chat/completions")
            )
            == 1
        )
        checks["retry_package_is_new_run_owned"] = bool(
            retry_state["package"]
            and retry_state["package"]["evidence_package_id"]
            != (original_state["package"] or {}).get("evidence_package_id")
        )
        checks["retry_package_reused_evidence_snapshot"] = bool(
            retry_state["package"]
            and original_state["package"]
            and retry_state["package"]["chunk_ids"]
            == original_state["package"]["chunk_ids"]
            and retry_state["package"]["parse_batch_ids"]
            == original_state["package"]["parse_batch_ids"]
        )
        checks["original_history_preserved"] = bool(
            original_state["run"]["status"] == "cancelled"
            and original_state["run"]["error"]["code"] == "RUN_ENDED_BY_OWNER"
            and original_state["run"]["process_token_present"] is False
            and [step["step_id"] for step in original_state["steps"]]
            == ["evidence"]
            and original_state["steps"][0]["status"] == "succeeded"
            and original_state["analysis_id"] is None
            and original_state["report_id"] is None
        )
        checks["repository_not_duplicated"] = (
            report["counts_before"]["documents"]
            == report["counts_after"]["documents"]
            and report["counts_before"]["chunks"]
            == report["counts_after"]["chunks"]
            and report["counts_before"]["embeddings"]
            == report["counts_after"]["embeddings"]
            and report["counts_after"]["runs"]
            == report["counts_before"]["runs"] + 2
        )
        checks["state_after_kill_kept_progress"] = bool(
            report["state_after_kill"]["run"]["status"] == "running"
            and [step["step_id"] for step in report["state_after_kill"]["steps"]]
            == ["evidence"]
            and report["state_after_kill"]["steps"][0]["status"] == "succeeded"
            and report["state_after_kill"]["run"]["error"] is None
        )
        report["result"] = (
            "passed" if all(checks.values()) else "failed"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["result"] == "passed" else 1

    report["result"] = "retry_failed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Seed the fixed Source/Document/Chunk set.")
    commands.add_parser(
        "reset",
        help=f"Clear {TASK_ID}'s Run/Report rows before a fresh drill.",
    )
    worker = commands.add_parser("worker", help="Run one real Worker cycle.")
    worker.add_argument("--behavior", choices=["block", "respond"], required=True)
    worker.add_argument("--signal-file", default=None)
    worker.add_argument("--calls-file", default=None)
    mock = commands.add_parser(
        "mock-provider", help="Serve a local OpenAI-compatible model endpoint."
    )
    mock.add_argument("--port", type=int, required=True)
    mock.add_argument("--calls-file", default=None)
    drive_parser = commands.add_parser(
        "drive", help="Run the full Owner recovery drill."
    )
    drive_parser.add_argument("--workdir", default="/tmp/ac3-drill")
    args = parser.parse_args(argv)
    if args.command == "seed":
        print(json.dumps(seed(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "reset":
        print(json.dumps(reset(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "worker":
        return work(args.behavior, args.signal_file, args.calls_file)
    if args.command == "mock-provider":
        return mock_provider(args.port, args.calls_file)
    return drive(Path(args.workdir))


if __name__ == "__main__":
    raise SystemExit(main())
