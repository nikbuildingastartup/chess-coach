import chess
import pytest

from app.chess_engine import (
    MIDDLEGAME_MOVE_LIMIT,
    OPENING_MOVE_LIMIT,
    _classify_phase,
    analyze_game,
    fen_before_move,
)

# 3. Qxf6?? hangs the queen for a knight: gxf6 recaptures it for free.
# This is a textbook blunder we can predict the classification of without
# needing to trust the engine's judgment on anything subtle.
BLUNDERING_PGN = "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6"


def test_analyze_game_flags_the_hung_queen_as_blunder_or_mistake():
    analysis = analyze_game(BLUNDERING_PGN)

    # One entry per half-move: e4, Nf6, Qf3, Nc6, Qxf6, gxf6.
    assert len(analysis) == 6

    qxf6_entry = analysis[4]
    assert qxf6_entry["san"] == "Qxf6"
    assert qxf6_entry["move_number"] == 3
    assert qxf6_entry["side"] == "white"
    # Losing a queen for a knight is a >200cp swing; must not classify as
    # "good" due to a sign error in the mover-perspective calculation.
    assert qxf6_entry["classification"] in ("blunder", "mistake")
    assert qxf6_entry["best_move"] is not None
    assert qxf6_entry["best_move"] != "Qxf6"


def test_analyze_game_reports_side_correctly_for_white_and_black_moves():
    analysis = analyze_game(BLUNDERING_PGN)

    # Moves alternate white, black, white, black, ... starting with white.
    expected_sides = ["white", "black", "white", "black", "white", "black"]
    assert [entry["side"] for entry in analysis] == expected_sides

    # Sanity-check against the actual SAN so the index mapping above is
    # trustworthy: e4/Qf3/Qxf6 are White's moves, Nf6/Nc6/gxf6 are Black's.
    white_sans = {entry["san"] for entry in analysis if entry["side"] == "white"}
    black_sans = {entry["san"] for entry in analysis if entry["side"] == "black"}
    assert white_sans == {"e4", "Qf3", "Qxf6"}
    assert black_sans == {"Nf6", "Nc6", "gxf6"}


def test_analyze_game_only_reports_best_move_for_non_good_classifications():
    analysis = analyze_game(BLUNDERING_PGN)

    for entry in analysis:
        if entry["classification"] == "good":
            assert entry["best_move"] is None
        else:
            assert entry["best_move"] is not None


def test_analyze_game_entries_have_expected_shape():
    analysis = analyze_game(BLUNDERING_PGN)

    for entry in analysis:
        assert set(entry.keys()) == {
            "move_number",
            "san",
            "side",
            "classification",
            "eval_cp",
            "best_move",
            "phase",
        }
        assert entry["side"] in ("white", "black")
        assert entry["classification"] in ("blunder", "mistake", "inaccuracy", "good")
        assert isinstance(entry["eval_cp"], int)
        assert entry["phase"] in ("opening", "middlegame", "endgame")


def test_analyze_game_tags_phase_as_opening_for_all_moves_in_a_short_game():
    # BLUNDERING_PGN only reaches move 3, well within OPENING_MOVE_LIMIT (10).
    analysis = analyze_game(BLUNDERING_PGN)

    assert all(entry["move_number"] <= OPENING_MOVE_LIMIT for entry in analysis)
    assert all(entry["phase"] == "opening" for entry in analysis)


def test_classify_phase_thresholds():
    assert _classify_phase(1) == "opening"
    assert _classify_phase(OPENING_MOVE_LIMIT) == "opening"
    assert _classify_phase(OPENING_MOVE_LIMIT + 1) == "middlegame"
    assert _classify_phase(MIDDLEGAME_MOVE_LIMIT) == "middlegame"
    assert _classify_phase(MIDDLEGAME_MOVE_LIMIT + 1) == "endgame"


def test_fen_before_move_returns_position_right_before_the_target_half_move():
    # 3. Qxf6?? is White's 3rd move -- the FEN right before it must have
    # White to move, move 3, and the queen still on f3 (not yet captured
    # on f6).
    fen = fen_before_move(BLUNDERING_PGN, move_number=3, side="white")

    board_before = chess.Board(fen)
    assert board_before.turn is True  # White to move
    assert board_before.fullmove_number == 3
    assert board_before.piece_at(chess.parse_square("f3")) is not None


def test_fen_before_move_matches_board_state_reached_by_manual_replay():
    fen = fen_before_move(BLUNDERING_PGN, move_number=2, side="black")

    # Manually replay to right before Black's 2nd move (2... Nc6) and
    # compare FENs directly.
    board = chess.Board()
    board.push_san("e4")
    board.push_san("Nf6")
    board.push_san("Qf3")
    assert fen == board.fen()


def test_fen_before_move_raises_when_target_is_never_reached():
    with pytest.raises(RuntimeError):
        fen_before_move(BLUNDERING_PGN, move_number=50, side="white")


def test_analyze_game_returns_empty_list_for_empty_pgn():
    """chess.js's `.pgn()` on a fresh, moveless game returns "" -- e.g. a
    human resigns before making any move. `chess.pgn.read_game` returns
    None for that input, which used to trip an `assert game is not None`
    and surface as an opaque 500. There are no moves to analyze, so this
    should just return an empty list instead of raising.
    """
    assert analyze_game("") == []
    assert analyze_game("   ") == []
