"""One real-document classify reprocess in a disposable DB snapshot only."""
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import dotenv_values

ROOT = Path('/closeout')
PRIVATE = ROOT / 'private'
OUT = ROOT / 'artifacts'
gateway = json.loads((PRIVATE / 'gateway.json').read_text())
pg = dotenv_values(PRIVATE / 'postgres.env')
os.environ.update(
    DATABASE_URL=f"postgresql+psycopg://closeout:{pg['POSTGRES_PASSWORD']}@127.0.0.1:15549/closeout",
    CLASSIFICATION_API_BASE_URL=gateway['base_url'],
    CLASSIFICATION_API_KEY=gateway['api_key'],
    CLASSIFICATION_MODEL_NAME='typesafe/jev-latest',
    MODEL_API_BASE_URL=gateway['base_url'],
    MODEL_API_KEY=gateway['api_key'],
    MODEL_NAME='workbuddy-ai/deepseek-v4.1-flash',
)

from sqlalchemy import select
from stocks.db import session_factory
from stocks.models import Document, DocumentChunk, DocumentProcessingState
from stocks.processing.processors import build_stage_registry
from stocks.processing.runner import run_stages_for_documents
from stocks.settings import settings
from stocks.taxonomy import load_taxonomy
from stocks.classification.service import taxonomy_fingerprint

DOC_ID = UUID('08e03a1f-5282-48d2-a692-eb65c5db80cb')
factory = session_factory()

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')

def chunks(session):
    return [(str(c.id), str(c.parse_batch_id), hashlib.sha256(c.text.encode()).hexdigest())
            for c in session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == DOC_ID).order_by(DocumentChunk.id))]

with factory() as session:
    document = session.get(Document, DOC_ID)
    assert document is not None
    before = document.classification
    identity = (document.version, document.content_hash, document.snapshot_key)
    original_chunks = chunks(session)
    source = {'document_id': str(DOC_ID), 'source_id': document.source_id, 'title': document.title,
              'original_url': document.original_url, 'content_hash': document.content_hash, 'version': document.version}
    save('classification-before.json', before)
    save('source-identity.json', source)

expected = taxonomy_fingerprint(load_taxonomy('config/taxonomy.yaml'))
started = time.monotonic()
executions, errors = run_stages_for_documents(
    factory, settings(), build_stage_registry('config/models.yaml', taxonomy_path='config/taxonomy.yaml', sources_config_path='config/sources.yaml'),
    [DOC_ID], ['classify'], reprocess=True, owner_token='closeout-' + uuid4().hex,
)
elapsed = round(time.monotonic() - started, 3)
save('reprocess-execution.json', {'items': [item.to_dict() for item in executions], 'errors': errors})
with factory() as session:
    document = session.get(Document, DOC_ID)
    state = session.get(DocumentProcessingState, (DOC_ID, 'classify'))
    after = document.classification
    save('classification-after.json', after)
    assert not errors, 'Stage reopening failed'
    if state.status != 'completed':
        print(json.dumps({'stage_status': state.status, 'error': state.error}, default=str))
        raise SystemExit(1)
    assert after['taxonomy_fingerprint'] == expected
    assert expected != before['taxonomy_fingerprint']
    assert (document.version, document.content_hash, document.snapshot_key) == identity
    assert chunks(session) == original_chunks
    native = [c for c in after['model_calls'] if c.get('protocol') == 'typesafe']
    assert len(native) == 1
    assert native[0].get('reported_model_name'), 'Actual native model identity missing'
    result = {
        'status': 'passed', 'environment': 'isolated PostgreSQL snapshot on 100.120.14.84',
        'production_endpoint_modified': False, 'production_classification_modified': False,
        'source': source, 'elapsed_seconds': elapsed,
        'old_taxonomy_version': before['taxonomy_version'], 'new_taxonomy_version': after['taxonomy_version'],
        'old_taxonomy_fingerprint': before['taxonomy_fingerprint'], 'new_taxonomy_fingerprint': expected,
        'stage_status': state.status, 'reprocess_requested': state.reprocess_requested,
        'category': after['category'], 'companies': after['companies'], 'symbols': after['symbols'],
        'native_call': native[0], 'model_call_count': len(after['model_calls']), 'usage': after['usage'],
        'raw_document_and_chunks_unchanged': True,
        'classification_history_boundary': 'Current classification JSONB replaced in isolated copy; before/after exported. No new parse batch is created by classify.',
        'external_success': True,
        'scope': 'Real Jev and real entity generation through Magpie; existing saved real SEC document. No new collection, extraction, embedding or research run.',
    }
    save('acceptance-summary.json', result)
    print(json.dumps(result, ensure_ascii=False, default=str))
