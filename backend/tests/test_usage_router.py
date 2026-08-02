from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import LlmUsage

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def get_session_override():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_usage(
    session: Session,
    *,
    call_site: str,
    created_at: datetime,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    input_cost_usd: float = 0.0001,
    output_cost_usd: float = 0.00025,
) -> LlmUsage:
    row = LlmUsage(
        created_at=created_at,
        call_site=call_site,
        model="anthropic/claude-haiku-4.5",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        input_cost_usd=input_cost_usd,
        output_cost_usd=output_cost_usd,
        total_cost_usd=input_cost_usd + output_cost_usd,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_usage_summary_empty_table_returns_zeros(client):
    response = client.get("/usage/summary", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "total_cost_usd": 0,
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "since": None,
        "by_call_site": [],
        "by_day": [],
    }


def test_usage_summary_aggregates_totals_by_call_site_and_day(client, db_engine):
    now = datetime.now(timezone.utc)
    today = now.date()
    yesterday = today - timedelta(days=1)

    with Session(db_engine) as session:
        _seed_usage(
            session,
            call_site="coaching",
            created_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            prompt_tokens=100,
            completion_tokens=50,
            input_cost_usd=0.0001,
            output_cost_usd=0.00025,
        )
        _seed_usage(
            session,
            call_site="coaching",
            created_at=datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc),
            prompt_tokens=200,
            completion_tokens=80,
            input_cost_usd=0.0002,
            output_cost_usd=0.0004,
        )
        _seed_usage(
            session,
            call_site="focus",
            created_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            prompt_tokens=300,
            completion_tokens=120,
            input_cost_usd=0.0003,
            output_cost_usd=0.0006,
        )

    response = client.get("/usage/summary", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()

    assert body["total_calls"] == 3
    assert body["total_input_tokens"] == 100 + 200 + 300
    assert body["total_output_tokens"] == 50 + 80 + 120
    assert body["total_cost_usd"] == pytest.approx(0.0001 + 0.00025 + 0.0002 + 0.0004 + 0.0003 + 0.0006)
    assert body["since"] == yesterday.isoformat()

    by_call_site = {entry["call_site"]: entry for entry in body["by_call_site"]}
    assert by_call_site["coaching"]["calls"] == 2
    assert by_call_site["coaching"]["cost_usd"] == pytest.approx(0.0001 + 0.00025 + 0.0002 + 0.0004)
    assert by_call_site["focus"]["calls"] == 1
    assert by_call_site["focus"]["cost_usd"] == pytest.approx(0.0003 + 0.0006)

    # by_day is oldest first.
    assert [d["date"] for d in body["by_day"]] == [yesterday.isoformat(), today.isoformat()]
    yesterday_bucket = body["by_day"][0]
    today_bucket = body["by_day"][1]
    assert yesterday_bucket["calls"] == 1
    assert yesterday_bucket["cost_usd"] == pytest.approx(0.0002 + 0.0004)
    assert today_bucket["calls"] == 2
    assert today_bucket["cost_usd"] == pytest.approx(0.0001 + 0.00025 + 0.0003 + 0.0006)


def test_usage_summary_days_param_limits_by_day_window_but_not_totals(client, db_engine):
    now = datetime.now(timezone.utc)
    today = now.date()
    old_day = today - timedelta(days=10)

    with Session(db_engine) as session:
        _seed_usage(
            session,
            call_site="coaching",
            created_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        )
        _seed_usage(
            session,
            call_site="coaching",
            created_at=datetime.combine(old_day, datetime.min.time(), tzinfo=timezone.utc),
        )

    response = client.get("/usage/summary?days=3", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()

    # Totals still include the old row.
    assert body["total_calls"] == 2
    assert body["since"] == old_day.isoformat()

    # by_day excludes the row older than the 3-day window.
    assert [d["date"] for d in body["by_day"]] == [today.isoformat()]
    assert body["by_day"][0]["calls"] == 1


def test_usage_summary_requires_auth(client):
    response = client.get("/usage/summary")
    assert response.status_code == 401
