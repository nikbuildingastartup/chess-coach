"""Stockfish engine interaction: given a position, produce a move.

Focused on talking to the Stockfish UCI process only — no DB/model code
here (game persistence is handled elsewhere).
"""

import io

import chess
import chess.engine
import chess.pgn

from app.config import settings

# Stockfish "Skill Level" UCI option (0-20) per opponent-strength preset.
SKILL_LEVELS: dict[str, int] = {
    "easy": 3,
    "medium": 10,
    "hard": 18,
}

ENGINE_MOVE_TIME_SECONDS = 0.5

# Per-position analysis budget for post-game review. Deliberately shallow —
# speed over precision, this is for blunder detection, not deep prep.
ANALYSIS_TIME_SECONDS = 0.1

# A large stand-in centipawn value for forced-mate scores, so mate scores
# still compare sensibly against centipawn thresholds.
MATE_SCORE_CP = 100_000

# Centipawn-drop thresholds (mover's perspective) for move classification.
BLUNDER_THRESHOLD_CP = 200
MISTAKE_THRESHOLD_CP = 100
INACCURACY_THRESHOLD_CP = 50

# Centipawn-drop tolerance (mover's perspective) below which a Practice
# Module move is still accepted as "correct" even if it doesn't exactly
# match Stockfish's top choice -- multiple moves are often equally sound.
PRACTICE_CORRECT_TOLERANCE_CP = 30

# Fullmove-number thresholds for coarse game-phase tagging. A move with
# `move_number <= OPENING_MOVE_LIMIT` is "opening"; up through
# `MIDDLEGAME_MOVE_LIMIT` is "middlegame"; anything later is "endgame".
# Deliberately simple (move-count based, not material-based) -- good enough
# for aggregating weaknesses by phase, not a precise phase detector.
OPENING_MOVE_LIMIT = 10
MIDDLEGAME_MOVE_LIMIT = 30


def get_engine_move(fen: str, skill: str) -> str:
    """Ask Stockfish for a move in the given position.

    Args:
        fen: Position to move from, in FEN notation.
        skill: One of "easy", "medium", "hard" — mapped to a Stockfish
            "Skill Level" UCI option.

    Returns:
        The engine's chosen move in SAN notation.
    """
    skill_level = SKILL_LEVELS[skill]
    board = chess.Board(fen)

    with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
        engine.configure({"Skill Level": skill_level})
        result = engine.play(board, chess.engine.Limit(time=ENGINE_MOVE_TIME_SECONDS))
        move = result.move
        if move is None:
            raise RuntimeError(
                "Stockfish returned no move for a position that should have "
                "legal moves available."
            )
        return board.san(move)


def _score_to_cp(score: chess.engine.Score) -> int:
    """Convert a python-chess Score to a plain centipawn int.

    Mate scores are mapped to a large-but-finite value (with the mate's
    sign preserved) so they still compare correctly against the cp
    thresholds used for blunder classification.
    """
    return score.score(mate_score=MATE_SCORE_CP)


def _classify(drop_cp: int) -> str:
    """Classify a move given how many centipawns the mover's evaluation
    dropped by (positive = position got worse for the mover)."""
    if drop_cp >= BLUNDER_THRESHOLD_CP:
        return "blunder"
    if drop_cp >= MISTAKE_THRESHOLD_CP:
        return "mistake"
    if drop_cp >= INACCURACY_THRESHOLD_CP:
        return "inaccuracy"
    return "good"


def _classify_phase(move_number: int) -> str:
    """Classify a half-move's game phase from its (fullmove-number) index."""
    if move_number <= OPENING_MOVE_LIMIT:
        return "opening"
    if move_number <= MIDDLEGAME_MOVE_LIMIT:
        return "middlegame"
    return "endgame"


def fen_before_move(pgn: str, move_number: int, side: str) -> str:
    """Replay a PGN and return the FEN of the position right before a given
    half-move (identified by fullmove number + side to move).

    Used by the Practice Module (a later task) to reconstruct the exact
    position a recorded mistake was made in, so it can be presented as a
    puzzle.

    Args:
        pgn: The game's PGN, as stored on `Game.pgn`.
        move_number: The fullmove number of the target half-move (matches
            `analyze_game`'s `"move_number"` field, i.e. `board.fullmove_number`
            at the time that half-move was played).
        side: "white" or "black" -- which side made the target half-move
            (matches `analyze_game`'s `"side"` field).

    Returns:
        The FEN of the position immediately before the target half-move was
        played.

    Raises:
        RuntimeError: if the PGN can't be parsed, or the game doesn't reach
            a position matching (move_number, side) before ending.
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise RuntimeError(f"Could not parse PGN into a game: {pgn!r}")
    board = game.board()
    target_color = chess.WHITE if side == "white" else chess.BLACK

    for move in game.mainline_moves():
        if board.fullmove_number == move_number and board.turn == target_color:
            return board.fen()
        board.push(move)

    raise RuntimeError(
        f"PGN never reaches move_number={move_number}, side={side!r} "
        "before the game ends."
    )


def check_move(fen: str, move_uci: str) -> dict:
    """Check whether a single played move is "correct" for the Practice
    Module: it matches Stockfish's best move, or is close enough in
    evaluation (within `PRACTICE_CORRECT_TOLERANCE_CP`) to still count.

    Mirrors `analyze_game`'s before/after `engine.analyse` pattern, but for
    a single position + move instead of a whole game.

    Args:
        fen: Position to check the move in, in FEN notation.
        move_uci: The played move in UCI notation (e.g. "e2e4", or
            "e7e8q" for a promotion).

    Returns:
        A dict with "correct" (bool), "best_move" (UCI str, or None if the
        engine reports no principal variation), and "played_eval_cp" (the
        position's evaluation after the played move, from the mover's
        perspective).

    Raises:
        ValueError: if `move_uci` doesn't parse as a UCI move, or parses
            but isn't legal in the given position.
    """
    board = chess.Board(fen)

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as e:
        raise ValueError(f"Malformed UCI move: {move_uci!r}") from e

    if move not in board.legal_moves:
        raise ValueError(f"Illegal move {move_uci!r} in position {fen!r}")

    with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
        limit = chess.engine.Limit(time=ANALYSIS_TIME_SECONDS)

        info_before = engine.analyse(board, limit)
        eval_before_mover_pov = _score_to_cp(info_before["score"].relative)
        pv = info_before.get("pv")
        best_move_uci = pv[0].uci() if pv else None

        board.push(move)
        info_after = engine.analyse(board, limit)
        eval_after_mover_pov = -_score_to_cp(info_after["score"].relative)

    drop_cp = eval_before_mover_pov - eval_after_mover_pov
    correct = (move.uci() == best_move_uci) or (drop_cp <= PRACTICE_CORRECT_TOLERANCE_CP)

    return {
        "correct": correct,
        "best_move": best_move_uci,
        "played_eval_cp": eval_after_mover_pov,
    }


def analyze_game(pgn: str) -> list[dict]:
    """Analyze every half-move of a finished game and flag blunders.

    For each half-move, we compare Stockfish's evaluation of the position
    right before the move to its evaluation right after — both expressed
    from the perspective of the player who made the move — and classify
    the drop. `chess.engine.Score.relative` (as returned in `info["score"]`
    from `engine.analyse`) is already relative to whichever side is to
    move in the analysed position. So:
      - the "before" evaluation, relative to the mover, is simply the
        relative score of the pre-move position (mover is to move there).
      - the "after" evaluation, relative to the mover, is the *negation*
        of the relative score of the post-move position (the opponent is
        to move there, so the raw relative score is from their point of
        view).
    A single SimpleEngine instance is opened once and reused for every
    position in the game — reopening the engine process per move would be
    far too slow for a full game.
    """
    if not pgn.strip():
        # A fresh, moveless game (e.g. resigning before making any move)
        # produces an empty PGN string. There are no moves to analyze.
        return []

    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise RuntimeError(f"Could not parse PGN into a game: {pgn!r}")
    board = game.board()

    results: list[dict] = []

    with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
        limit = chess.engine.Limit(time=ANALYSIS_TIME_SECONDS)
        info_before = engine.analyse(board, limit)

        for move in game.mainline_moves():
            move_number = board.fullmove_number
            san = board.san(move)
            # `board.turn` before pushing the move is the side that is
            # about to move -- i.e. the side making *this* move.
            side = "white" if board.turn == chess.WHITE else "black"

            eval_before_mover_pov = _score_to_cp(info_before["score"].relative)
            pv = info_before.get("pv")
            best_move_san = board.san(pv[0]) if pv else None

            board.push(move)
            info_after = engine.analyse(board, limit)
            eval_after_mover_pov = -_score_to_cp(info_after["score"].relative)

            drop_cp = eval_before_mover_pov - eval_after_mover_pov
            classification = _classify(drop_cp)

            results.append(
                {
                    "move_number": move_number,
                    "san": san,
                    "side": side,
                    "classification": classification,
                    "eval_cp": eval_after_mover_pov,
                    "best_move": best_move_san if classification != "good" else None,
                    "phase": _classify_phase(move_number),
                }
            )

            info_before = info_after

    return results
