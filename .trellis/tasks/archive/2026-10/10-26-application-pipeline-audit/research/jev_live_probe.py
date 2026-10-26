"""Remote-only real Jev protocol probe through the Owner-authorized Magpie proxy.

No database, entity generation, research generation, or embedding calls. The
payload is a fictional article and the full current taxonomy questions.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time

from stocks.classification.jev import normalize_answers, questions_for
from stocks.decision import load_decision_route
from stocks.providers import ModelClient, ModelError
from stocks.taxonomy import load_taxonomy

ROOT = Path('/tmp/stocks-v0-application-pipeline-audit-jev-live')

async def main() -> int:
    key = json.loads((ROOT / 'gateway-auth.json').read_text())['api_key']
    os.environ.update(
        CLASSIFICATION_API_BASE_URL='http://127.0.0.1:13425/v1',
        CLASSIFICATION_API_KEY=key,
        CLASSIFICATION_MODEL_NAME='typesafe/jev-latest',
        MODEL_API_BASE_URL='http://127.0.0.1:13425/v1',
        MODEL_API_KEY=key,
        MODEL_NAME='not-called-in-this-probe',
    )
    route = load_decision_route('config/models.yaml')
    taxonomy = load_taxonomy('config/taxonomy.yaml')
    routing = replace(route.routing, max_attempts=1, fallbacks=(), timeout_seconds=45)
    targets = {alias: replace(target, timeout_seconds=45) for alias, target in route.targets.items()}
    questions = questions_for(taxonomy)
    result = {
        'kind': 'real_jev_native_adapter_via_magpie',
        'real_external_call': True,
        'model_requested': 'typesafe/jev-latest',
        'question_count': len(questions),
        'generation_entity_call': False,
        'research_call': False,
        'source': 'fictional in-memory article; not a real market fact',
        'max_attempts': 1,
    }
    started = time.monotonic()
    try:
        response = await ModelClient(targets).typesafe_decision(
            routing,
            state={'document': {
                'title': 'Fictional Acme quarterly revenue',
                'text': 'Fictional integration sample: Acme Holdings (ACME), a US semiconductor company, reported revenue of 42 million USD for 2026 Q3. This is invented test data, not a real market fact.',
            }},
            questions=questions,
            validate_content=lambda value: normalize_answers(json.loads(value), questions, route.noul_threshold),
        )
        result.update(
            status='passed',
            reported_model_name=response.reported_model_name,
            usage=response.usage,
            attempts=[attempt.to_dict() for attempt in response.attempts],
            normalized=normalize_answers(json.loads(response.content), questions, route.noul_threshold),
        )
        # Real answers concern only the fictional sample; no headers/credentials.
        (ROOT / 'real-answers.json').write_text(json.dumps(json.loads(response.content), indent=2) + '\n')
    except ModelError as exc:
        result.update(status='failed', error=exc.to_dict())
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__)
    result['duration_seconds'] = round(time.monotonic() - started, 3)
    (ROOT / 'live-adapter-result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
