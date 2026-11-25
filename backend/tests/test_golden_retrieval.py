"""Golden-set retrieval gate on the exact chat search path (lexical; no embedding service or model).

Thresholds come from scripts/rag_eval.py runs (see docs/rag-evaluation.md); a drop means chunking,
ranking or the evidence budget regressed. Hybrid quality is measured by the evaluation script.
"""

import json
import re
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.connectors.core import records
from backend.db.models import ConnectorAccount
from backend.db.session import get_session_factory
from backend.processing.passages import tokens
from backend.schemas.agent import RunRequest, SearchSnapshots
from backend.services import workspace_tools
from backend.services.agent import model_output
from backend.services.ingestion import ingest_source
from backend.tests.golden_set import QUESTIONS, load_corpus

pytestmark = pytest.mark.anyio
OWNER = "user_golden_gate"
MIN_LEXICAL_HIT_RATE = 0.85
MAX_P95_EVIDENCE_TOKENS = 1800


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


async def test_golden_set_lexical_retrieval_meets_recall_and_budget(database: None) -> None:
    async with get_session_factory().begin() as db:
        identifiers = await load_corpus(db, OWNER)
    for identifier in identifiers:
        await ingest_source(identifier)
    async with get_session_factory()() as db:
        accounts = {
            str(account.id): account.provider
            for account in await db.scalars(select(ConnectorAccount).where(ConnectorAccount.owner_id == OWNER))
        }
    scope: dict[str, object] = {"accounts": [{"id": key, "provider": value} for key, value in accounts.items()]}
    ctx = workspace_tools.Context(OWNER, uuid4(), RunRequest(question="golden gate"), scope, [])
    hits, sizes = [], []
    for question in QUESTIONS:
        for query in (question.question, question.keywords):
            view = model_output(await workspace_tools.snapshots(ctx, SearchSnapshots(query=query)))
            sizes.append(tokens(json.dumps(view, ensure_ascii=False)))
            if question.gold:
                texts = [compact(str(item["text"])) for item in records(view["materials"])]
                hits.append(all(any(compact(gold) in text for text in texts) for gold in question.gold))
    hit_rate = sum(hits) / len(hits)
    p95 = sorted(sizes)[int(len(sizes) * 0.95) - 1]
    assert hit_rate >= MIN_LEXICAL_HIT_RATE, f"golden lexical hit rate {hit_rate:.3f}"
    assert p95 <= MAX_P95_EVIDENCE_TOKENS, f"p95 evidence tokens {p95}"
