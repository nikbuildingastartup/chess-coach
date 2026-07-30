from unittest.mock import MagicMock, patch

from app.coaching import generate_coaching_summary

SAMPLE_ANALYSIS = [
    {
        "move_number": 3,
        "san": "Qxf6",
        "classification": "blunder",
        "eval_cp": -900,
        "best_move": "Nf3",
    },
    {
        "move_number": 4,
        "san": "gxf6",
        "classification": "good",
        "eval_cp": 900,
        "best_move": "gxf6",
    },
]


def _mock_openai_response(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text))]
    return response


def test_generate_coaching_summary_returns_none_without_api_key():
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = None
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win")

    assert result is None
    mock_openai_cls.assert_not_called()


def test_generate_coaching_summary_returns_text_on_success():
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                "You keep hanging your queen to simple tactics -- slow down and check captures."
            )
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win")

    assert result == (
        "You keep hanging your queen to simple tactics -- slow down and check captures."
    )
    mock_openai_cls.assert_called_once()
    _, call_kwargs = mock_openai_cls.call_args
    assert call_kwargs["base_url"] == "https://fal.run/openrouter/router/openai/v1"
    assert call_kwargs["default_headers"] == {"Authorization": "Key test-fal-key"}

    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "anthropic/claude-haiku-4.5"
    assert create_kwargs["max_tokens"] == 300


def test_generate_coaching_summary_returns_none_on_api_error():
    with patch("app.coaching.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.coaching.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("boom")
            mock_openai_cls.return_value = mock_client

            result = generate_coaching_summary("1. e4 Nf6", SAMPLE_ANALYSIS, "win")

    assert result is None
