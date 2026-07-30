"""Stockfish engine interaction: given a position, produce a move.

Focused on talking to the Stockfish UCI process only — no DB/model code
here (game persistence is handled elsewhere).
"""

import chess
import chess.engine

from app.config import settings

# Stockfish "Skill Level" UCI option (0-20) per opponent-strength preset.
SKILL_LEVELS: dict[str, int] = {
    "easy": 3,
    "medium": 10,
    "hard": 18,
}

ENGINE_MOVE_TIME_SECONDS = 0.5


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
        assert move is not None
        return board.san(move)
