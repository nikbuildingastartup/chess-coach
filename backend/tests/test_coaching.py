from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.coaching import _build_user_prompt, generate_coaching_summary
from app.models import LlmUsage

SAMPLE_ANALYSIS = [
    {
        "move_number": 1,
        "san": "e4",
        "side": "white",
        "classification": "good",
        "eval_cp": 20,
        "best_move": None,
    },
    {
        "move_number": 2,
        "san": "Qh5",
        "side": "black",
        "classification": "blunder",
        "eval_cp": -900,
        "best_move": "Nf6",
    },
    {
        "move_number": 3,
        "san": "Qxf6",
        "side": "white",
        "classification": "blunder",
        "eval_cp": -900,
        "best_move": "Nf3",
    },
    {
        "move_number": 4,
        "san": "gxf6",
        "side": "black",
        "classification": "good",
        "eval_cp": 900,
        "best_move": "gxf6",
    },
]


def test_build_user_prompt_only_includes_the_users_white_moves():
    prompt = _build_user_prompt("1. e4 Qh5 2. Qxf6 gxf6", SAMPLE_ANALYSIS, "win")

    # Isolate the "Flagged moves" section so a Black SAN merely appearing
    # in the raw PGN text doesn't produce a false pass/fail here -- what
    # matters is which moves are listed as *flagged* for the player.
    flagged_section = prompt.split("Flagged moves")[1]

    # The engine's Black blunder (Qh5) must never be attributed to the
    # player -- only White's flagged move (Qxf6) belongs in the prompt.
    assert "Qxf6" in flagged_section
    assert "Qh5" not in flagged_section
    assert "played White" in prompt


def test_build_user_prompt_reports_no_mistakes_when_only_engine_erred():
    engine_only_blunder = [
        {
            "move_number": 2,
            "san": "Qh5",
            "side": "black",
            "classification": "blunder",
            "eval_cp": -900,
            "best_move": "Nf6",
        }
    ]

    prompt = _build_user_prompt("1. e4 Qh5", engine_only_blunder, "win")

    assert "No blunders or mistakes were flagged in this game." in prompt


def _mock_openai_response(text: str, usage: MagicMock | None = None) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text))]
    response.usage = usage
    return response


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


def test_generate_coaching_summary_returns_none_without_api_key(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = None
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    assert result is None
    mock_openai_cls.assert_not_called()


def test_generate_coaching_summary_returns_structured_dict_on_success(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '{"headline": "Hanging pieces after queen trades", '
                '"explanation": "You keep hanging your queen to simple tactics.", '
                '"recommendation": "Slow down and check captures before moving."}'
            )
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    assert result == {
        "headline": "Hanging pieces after queen trades",
        "explanation": "You keep hanging your queen to simple tactics.",
        "recommendation": "Slow down and check captures before moving.",
    }
    mock_openai_cls.assert_called_once()
    _, call_kwargs = mock_openai_cls.call_args
    assert call_kwargs["base_url"] == "https://fal.run/openrouter/router/openai/v1"
    assert call_kwargs["default_headers"] == {"Authorization": "Key test-fal-key"}

    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "anthropic/claude-haiku-4.5"
    assert create_kwargs["max_tokens"] == 300


def test_generate_coaching_summary_strips_markdown_code_fence(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '```json\n{"headline": "Queen safety", "explanation": "...", '
                '"recommendation": "..."}\n```'
            )
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    assert result == {
        "headline": "Queen safety",
        "explanation": "...",
        "recommendation": "...",
    }


def test_generate_coaching_summary_uses_raw_text_as_explanation_when_not_json(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                "You keep hanging your queen to simple tactics -- slow down and check captures."
            )
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    assert result == {
        "headline": None,
        "explanation": (
            "You keep hanging your queen to simple tactics -- slow down and check captures."
        ),
        "recommendation": None,
    }


def test_generate_coaching_summary_returns_none_on_api_error(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("boom")
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    assert result is None


def test_generate_coaching_summary_records_llm_usage_on_success(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            usage = MagicMock(prompt_tokens=50, completion_tokens=20, total_tokens=70)
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '{"headline": "H", "explanation": "E", "recommendation": "R"}',
                usage=usage,
            )
            mock_openai_cls.return_value = mock_client

            generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win", session)

    rows = session.exec(select(LlmUsage)).all()
    assert len(rows) == 1
    assert rows[0].call_site == "coaching"
    assert rows[0].prompt_tokens == 50
    assert rows[0].completion_tokens == 20


def test_generate_coaching_summary_survives_record_llm_usage_raising(session):
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            usage = MagicMock(prompt_tokens=50, completion_tokens=20, total_tokens=70)
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '{"headline": "H", "explanation": "E", "recommendation": "R"}',
                usage=usage,
            )
            mock_openai_cls.return_value = mock_client

            with patch(
                "app.coaching.record_llm_usage", side_effect=RuntimeError("boom")
            ):
                result = generate_coaching_summary(
                    "1. e4 Nf6", SAMPLE_ANALYSIS, "win", session
                )

    assert result == {"headline": "H", "explanation": "E", "recommendation": "R"}
