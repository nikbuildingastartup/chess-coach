import json
from datetime import datetime, timedelta, timezone

from app.models import Game
from app.weakness_profile import MIN_GAMES_FOR_PATTERN, aggregate_weakness_data

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _game(
    *,
    game_id: int,
    end_time: datetime,
    analysis: list[dict],
    user_color: str | None = "white",
) -> Game:
    game = Game(
        chesscom_game_id=f"g{game_id}",
        pgn="1. e4 e5",
        end_time=end_time,
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=True,
        analysis_json=json.dumps(analysis),
        user_color=user_color,
    )
    game.id = game_id
    return game


def _entry(move_number, san, side, classification, phase, eval_cp=-300, best_move="Nf3"):
    return {
        "move_number": move_number,
        "san": san,
        "side": side,
        "classification": classification,
        "phase": phase,
        "eval_cp": eval_cp,
        "best_move": best_move,
    }


def test_aggregate_counts_only_the_users_own_side():
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(2, "Qh5", "black", "blunder", "opening"),  # opponent, must be ignored
            _entry(3, "Qxf6", "white", "blunder", "opening"),
        ],
    )

    result = aggregate_weakness_data([game])

    assert result["total_flagged"] == 1
    assert result["top_pattern"] == {"phase": "opening", "classification": "blunder", "count": 1}
    assert [m["san"] for m in result["top_pattern_moves"]] == ["Qxf6"]


def test_aggregate_ignores_inaccuracy_and_good_classifications():
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(1, "e4", "white", "good", "opening"),
            _entry(2, "Nf3", "white", "inaccuracy", "opening"),
        ],
    )

    result = aggregate_weakness_data([game])

    assert result["total_flagged"] == 0
    assert result["top_pattern"] is None
    assert result["top_pattern_moves"] == []
    assert result["affected_game_ids"] == []


def test_aggregate_picks_most_frequent_phase_classification_pattern():
    game1 = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(2, "a", "white", "blunder", "opening"),
            _entry(4, "b", "white", "blunder", "opening"),
            _entry(20, "c", "white", "mistake", "middlegame"),
        ],
    )
    game2 = _game(
        game_id=2,
        end_time=BASE_TIME + timedelta(days=1),
        analysis=[
            _entry(6, "d", "white", "blunder", "opening"),
        ],
    )

    result = aggregate_weakness_data([game1, game2])

    assert result["top_pattern"] == {"phase": "opening", "classification": "blunder", "count": 3}
    assert result["total_flagged"] == 4
    assert result["affected_game_ids"] == [1, 2]
    assert result["counts_by_pattern"] == {
        "opening:blunder": 3,
        "middlegame:mistake": 1,
    }


def test_aggregate_orders_top_pattern_moves_newest_game_first():
    older = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[_entry(2, "older-move", "white", "blunder", "opening")],
    )
    newer = _game(
        game_id=2,
        end_time=BASE_TIME + timedelta(days=5),
        analysis=[_entry(2, "newer-move", "white", "blunder", "opening")],
    )

    result = aggregate_weakness_data([older, newer])

    assert [m["san"] for m in result["top_pattern_moves"]] == ["newer-move", "older-move"]


def test_aggregate_breaks_ties_by_preferring_blunder_over_mistake():
    # Both patterns tied at count=1 -- blunder must win the tie regardless
    # of insertion order.
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(2, "mistake-move", "white", "mistake", "middlegame"),
            _entry(4, "blunder-move", "white", "blunder", "endgame"),
        ],
    )

    result = aggregate_weakness_data([game])

    assert result["top_pattern"]["classification"] == "blunder"
    assert result["top_pattern"]["phase"] == "endgame"


def test_aggregate_skips_games_without_analysis_or_user_color():
    unanalyzed = Game(
        chesscom_game_id="g1",
        pgn="1. e4 e5",
        end_time=BASE_TIME,
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=False,
        analysis_json=None,
        user_color="white",
    )
    no_color = _game(
        game_id=2,
        end_time=BASE_TIME,
        analysis=[_entry(2, "x", "white", "blunder", "opening")],
        user_color=None,
    )

    result = aggregate_weakness_data([unanalyzed, no_color])

    assert result["total_flagged"] == 0
    assert result["total_games"] == 2


def test_aggregate_handles_empty_games_list():
    result = aggregate_weakness_data([])

    assert result["total_games"] == 0
    assert result["total_flagged"] == 0
    assert result["top_pattern"] is None


def test_min_games_for_pattern_constant_value():
    # Pinned per the design spec's Global Constraints -- a change here is a
    # deliberate scope change, not a silent tweak.
    assert MIN_GAMES_FOR_PATTERN == 3
