from app.chess_engine import analyze_game

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
        }
        assert entry["side"] in ("white", "black")
        assert entry["classification"] in ("blunder", "mistake", "inaccuracy", "good")
        assert isinstance(entry["eval_cp"], int)


def test_analyze_game_returns_empty_list_for_empty_pgn():
    """chess.js's `.pgn()` on a fresh, moveless game returns "" -- e.g. a
    human resigns before making any move. `chess.pgn.read_game` returns
    None for that input, which used to trip an `assert game is not None`
    and surface as an opaque 500. There are no moves to analyze, so this
    should just return an empty list instead of raising.
    """
    assert analyze_game("") == []
    assert analyze_game("   ") == []
