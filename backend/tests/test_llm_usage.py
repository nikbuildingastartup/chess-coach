from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.llm_usage import (
    INPUT_COST_PER_TOKEN_USD,
    OUTPUT_COST_PER_TOKEN_USD,
    record_llm_usage,
)
from app.models import LlmUsage


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_record_llm_usage_writes_a_row_with_correct_totals_and_costs(session):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=40, total_tokens=140)

    record_llm_usage(session, "coaching", "anthropic/claude-haiku-4.5", usage)

    rows = session.exec(select(LlmUsage)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.call_site == "coaching"
    assert row.model == "anthropic/claude-haiku-4.5"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 40
    assert row.total_tokens == 140
    assert row.input_cost_usd == pytest.approx(100 * INPUT_COST_PER_TOKEN_USD)
    assert row.output_cost_usd == pytest.approx(40 * OUTPUT_COST_PER_TOKEN_USD)
    assert row.total_cost_usd == pytest.approx(
        100 * INPUT_COST_PER_TOKEN_USD + 40 * OUTPUT_COST_PER_TOKEN_USD
    )


def test_record_llm_usage_derives_total_tokens_when_missing():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=None)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        record_llm_usage(s, "focus", "anthropic/claude-haiku-4.5", usage)
        row = s.exec(select(LlmUsage)).one()
        assert row.total_tokens == 15


def test_record_llm_usage_does_nothing_when_usage_is_none(session):
    record_llm_usage(session, "coaching", "anthropic/claude-haiku-4.5", None)

    rows = session.exec(select(LlmUsage)).all()
    assert rows == []


def test_record_llm_usage_never_raises_even_if_session_add_fails(session):
    class ExplodingSession:
        def add(self, *args, **kwargs):
            raise RuntimeError("boom")

        def rollback(self):
            pass

    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    # Must not raise.
    record_llm_usage(ExplodingSession(), "coaching", "anthropic/claude-haiku-4.5", usage)
