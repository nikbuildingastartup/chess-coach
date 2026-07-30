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
                    "classification": classification,
                    "eval_cp": eval_after_mover_pov,
                    "best_move": best_move_san if classification != "good" else None,
                }
            )

            info_before = info_after

    return results
