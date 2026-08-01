from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.focus import PRACTICE_POSITIONS_MAX, extract_practice_positions, generate_daily_focus
from app.models import Game

AGGREGATED = {
    "total_games": 5,
    "total_flagged": 4,
    "counts_by_pattern": {"opening:blunder": 3, "middlegame:mistake": 1},
    "top_pattern": {"phase": "opening", "classification": "blunder", "count": 3},
    "top_pattern_moves": [
        {
            "game_id": 1,
            "end_time": "2026-01-02T00:00:00+00:00",
            "move_number": 3,
            "san": "Qxf6",
            "side": "white",
            "classification": "blunder",
            "phase": "opening",
            "eval_cp": -900,
            "best_move": "Nf3",
        }
    ],
    "affected_game_ids": [1],
}

EMPTY_AGGREGATED = {
    "total_games": 5,
    "total_flagged": 0,
    "counts_by_pattern": {},
    "top_pattern": None,
    "top_pattern_moves": [],
    "affected_game_ids": [],
}


def _mock_openai_response(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text))]
    return response


def test_generate_daily_focus_returns_parsed_json_on_success():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.focus.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '{"headline": "Opening blunders", "explanation": "You keep hanging '
                'pieces early.", "recommendation": "Slow down in the opening."}'
            )
            mock_openai_cls.return_value = mock_client

            result = generate_daily_focus(AGGREGATED)

    assert result == {
        "headline": "Opening blunders",
        "explanation": "You keep hanging pieces early.",
        "recommendation": "Slow down in the opening.",
    }
    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "anthropic/claude-haiku-4.5"


def test_generate_daily_focus_tolerantly_parses_a_markdown_fenced_response():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.focus.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '```json\n{"headline": "H", "explanation": "E", "recommendation": "R"}\n```'
            )
            mock_openai_cls.return_value = mock_client

            result = generate_daily_focus(AGGREGATED)

    assert result == {"headline": "H", "explanation": "E", "recommendation": "R"}


def test_generate_daily_focus_falls_back_to_stats_text_without_api_key():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = None
        with patch("app.focus.OpenAI") as mock_openai_cls:
            result = generate_daily_focus(AGGREGATED)

    mock_openai_cls.assert_not_called()
    assert result["headline"] is not None
    assert "opening" in result["explanation"]
    assert "blunder" in result["explanation"]
    assert "3" in result["explanation"]
    assert "4" in result["explanation"]


def test_generate_daily_focus_falls_back_to_stats_text_on_api_error():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.focus.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("boom")
            mock_openai_cls.return_value = mock_client

            result = generate_daily_focus(AGGREGATED)

    assert result["headline"] is not None
    assert "opening" in result["explanation"]


def test_generate_daily_focus_falls_back_to_friendly_text_when_no_pattern_found():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = None
        result = generate_daily_focus(EMPTY_AGGREGATED)

    assert result["headline"] == "No major recurring issues found"
    assert result["recommendation"] is not None


def test_generate_daily_focus_uses_raw_text_as_explanation_on_parse_failure():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.focus.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                "This is not JSON at all, just prose about your chess."
            )
            mock_openai_cls.return_value = mock_client

            result = generate_daily_focus(AGGREGATED)

    assert result["headline"] is None
    assert result["recommendation"] is None
    assert result["explanation"] == "This is not JSON at all, just prose about your chess."


def test_generate_daily_focus_uses_raw_text_when_json_is_malformed():
    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = "test-fal-key"
        with patch("app.focus.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_openai_response(
                '{"headline": "Oops", "explanation": unterminated'
            )
            mock_openai_cls.return_value = mock_client

            result = generate_daily_focus(AGGREGATED)

    assert result["headline"] is None
    assert result["explanation"].startswith('{"headline"')


# --- extract_practice_positions -------------------------------------------

SHORT_PGN = "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6"


def _game(game_id: int, pgn: str = SHORT_PGN) -> Game:
    game = Game(
        chesscom_game_id=f"g{game_id}",
        pgn=pgn,
        end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=True,
        user_color="white",
    )
    game.id = game_id
    return game


def test_extract_practice_positions_builds_fen_played_move_and_best_move():
    games = [_game(1)]
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 1,
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
    }

    positions = extract_practice_positions(games, aggregated)

    assert len(positions) == 1
    position = positions[0]
    assert position["played_move"] == "Qxf6"
    assert position["best_move"] == "Qxf3"
    assert position["classification"] == "blunder"
    # FEN should reflect the position right before White's move 3 (Qxf6):
    # White queen still on f3, not yet captured the knight on f6.
    assert " w " in position["fen"]


def test_extract_practice_positions_respects_max_and_skips_unknown_games():
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 999,  # not in `games` -- should be skipped
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
        + [
            {
                "game_id": 1,
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
        * (PRACTICE_POSITIONS_MAX + 2)
    }
    games = [_game(1)]

    positions = extract_practice_positions(games, aggregated)

    assert len(positions) == PRACTICE_POSITIONS_MAX


def test_extract_practice_positions_skips_moves_fen_before_move_cannot_resolve():
    games = [_game(1)]
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 1,
                "move_number": 50,  # far beyond the short PGN's length
                "san": "Zzz",
                "side": "white",
                "classification": "blunder",
                "phase": "endgame",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
    }

    positions = extract_practice_positions(games, aggregated)

    assert positions == []
