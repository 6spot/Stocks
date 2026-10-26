"""Article contract and export boundaries, without a live model or database."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stocks.execution.config import load_task_configuration
from stocks.models import Analysis
from stocks.research.service import (
    parse_agent_output,
    render_report,
    validate_analysis_content,
)


REF = {
    "chunk_id": "chunk-1",
    "document_id": "document-1",
    "parse_batch_id": "batch-1",
    "document_version": 2,
    "source_id": "sec",
    "position": 0,
    "title": "SEC [release]",
}
ALLOWED = {(REF["chunk_id"], REF["document_id"]): REF}


def article():
    return {
        "title": "政策变化与研究边界",
        "summary": "本次研究仅有一份来源。",
        "sections": [{
            "heading": "政策变化的影响",
            "body": "当前材料表明政策发生变化，实际影响仍待观察。",
            "finding_ids": ["policy"],
        }],
        "findings": [{
            "id": "policy",
            "title": "政策出现变化",
            "kind": "fact",
            "statement": "来源披露了政策变化。",
            "rationale": "披露内容支持该判断。",
            "uncertainty": "只有一个来源。",
            "evidence_refs": [{"chunk_id": "chunk-1", "document_id": "document-1"}],
        }],
        "open_questions": ["实际影响如何？"],
        "evidence_refs": [],
    }


@pytest.fixture
def schema():
    config_path = Path(__file__).resolve().parents[2] / "config" / "tasks.yaml"
    config = load_task_configuration(config_path)
    return next(agent.output_schema for agent in config.agents if agent.id == "research_writer")


def test_article_normalizes_finding_refs_and_allows_shared_support(schema):
    output = article()
    output["sections"].append({
        "heading": "判断边界", "body": "同一披露仍只有一个来源。", "finding_ids": ["policy"]
    })
    parsed, refs = parse_agent_output(json.dumps(output), schema, ALLOWED)
    findings, _ = validate_analysis_content(parsed, ALLOWED)
    assert refs == [{key: value for key, value in REF.items() if key != "title"}]
    assert findings[0]["evidence_refs"] == refs
    assert parsed["sections"] == output["sections"]


@pytest.mark.parametrize("invalid", [
    "duplicate_finding", "unknown_finding", "unused_finding", "duplicate_section_ref",
    "blank_heading", "blank_body", "blank_title", "no_sections", "old_protocol",
    "foreign_finding_ref", "foreign_top_ref", "uncited_finding",
])
def test_article_rejects_invalid_associations_and_refs(schema, invalid):
    output = article()
    if invalid == "duplicate_finding":
        output["findings"].append(deepcopy(output["findings"][0]))
    elif invalid == "unknown_finding":
        output["sections"][0]["finding_ids"] = ["unknown"]
    elif invalid == "unused_finding":
        output["sections"][0]["finding_ids"] = []
    elif invalid == "duplicate_section_ref":
        output["sections"][0]["finding_ids"] = ["policy", "policy"]
    elif invalid in {"blank_heading", "blank_body"}:
        output["sections"][0][invalid.removeprefix("blank_")] = " \n "
    elif invalid == "blank_title":
        output["title"] = " "
    elif invalid == "no_sections":
        output["sections"] = []
    elif invalid == "old_protocol":
        del output["title"]
        del output["sections"]
    elif invalid == "foreign_finding_ref":
        output["findings"][0]["evidence_refs"][0]["document_id"] = "foreign"
    elif invalid == "foreign_top_ref":
        output["evidence_refs"] = [{"chunk_id": "foreign", "document_id": "foreign"}]
    elif invalid == "uncited_finding":
        output["findings"][0]["evidence_refs"] = []
    with pytest.raises(ValueError):
        parse_agent_output(json.dumps(output), schema, ALLOWED)


def analysis_from(output, schema, allowed):
    parsed, refs = parse_agent_output(json.dumps(output), schema, allowed)
    findings, questions = validate_analysis_content(parsed, allowed)
    return Analysis(
        title=parsed["title"], summary=parsed["summary"], sections=parsed["sections"],
        findings=findings, open_questions=questions, evidence_refs=refs,
        coverage={"gaps": []}, research_inputs=[],
    )


def export(analysis, evidence):
    return render_report(
        title=analysis.title, objective="研究政策变化", scope={},
        as_of=datetime(2026, 10, 26, tzinfo=UTC), analysis=analysis, evidence=evidence,
    )


def test_export_preserves_article_and_links_authorized_versions(schema):
    analysis = analysis_from(article(), schema, ALLOWED)
    rendered = export(analysis, [REF])
    assert rendered.startswith("# 政策变化与研究边界\n")
    assert "## 政策变化的影响\n" in rendered
    assert analysis.sections[0]["body"] in rendered
    assert "[E1](/api/documents/document-1/original)" in rendered
    assert "SEC \\[release\\]" in rendered
    assert "版本：2" in rendered
    assert "Why it matters" not in rendered
    assert "## Finding" not in rendered
    with pytest.raises(ValueError, match="unauthorized"):
        export(analysis, [])


def test_top_level_and_finding_citations_are_both_preserved(schema):
    other = {**REF, "chunk_id": "chunk-2", "document_id": "document-2"}
    allowed = {**ALLOWED, (other["chunk_id"], other["document_id"]): other}
    output = article()
    output["evidence_refs"] = [
        {"chunk_id": other["chunk_id"], "document_id": other["document_id"]},
    ]
    parsed, refs = parse_agent_output(json.dumps(output), schema, allowed)
    assert [(ref["chunk_id"], ref["document_id"]) for ref in refs] == [
        ("chunk-2", "document-2"), ("chunk-1", "document-1"),
    ]
    analysis = analysis_from(parsed, {}, allowed)
    analysis.evidence_refs = refs
    rendered = export(analysis, [REF, other])
    assert "/api/documents/document-1/original" in rendered
    assert "/api/documents/document-2/original" in rendered


def test_export_numbers_duplicate_inputs_once_in_frozen_order(schema):
    other = {**REF, "chunk_id": "chunk-2", "document_id": "document-2"}
    allowed = {**ALLOWED, (other["chunk_id"], other["document_id"]): other}
    output = article()
    output["findings"][0]["evidence_refs"] = [
        {"chunk_id": other["chunk_id"], "document_id": other["document_id"]}
    ]
    output["evidence_refs"] = [
        {"chunk_id": REF["chunk_id"], "document_id": REF["document_id"]}
    ]
    analysis = analysis_from(output, schema, allowed)
    rendered = export(analysis, [REF, REF, other])
    assert "参考来源：[E2](/api/documents/document-2/original)" in rendered
    assert rendered.count("- [E1]") == 1
    assert rendered.count("- [E2]") == 1
    assert "[E3]" not in rendered


def test_empty_evidence_article_and_top_level_only_citation(schema):
    output = article()
    output["findings"] = []
    output["sections"] = [{
        "heading": "研究限制", "body": "尚无可引用材料，无法形成判断。", "finding_ids": []
    }]
    analysis = analysis_from(output, schema, {})
    rendered = export(analysis, [])
    assert "尚无可引用材料，无法形成判断。" in rendered
    assert "## 参考来源" not in rendered
    assert analysis.findings == []
    output["evidence_refs"] = [{"chunk_id": "chunk-1", "document_id": "document-1"}]
    analysis = analysis_from(output, schema, ALLOWED)
    assert "SEC \\[release\\]" in export(analysis, [REF])
    with pytest.raises(ValueError, match="outside"):
        parse_agent_output(json.dumps(output), schema, {})
