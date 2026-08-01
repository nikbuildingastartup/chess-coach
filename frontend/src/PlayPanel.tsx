import { useCallback, useRef, useState, useEffect } from "react";
import { Chess } from "chess.js";
import { Chessboard, type PieceDropHandlerArgs } from "react-chessboard";
import {
  ApiError,
  getEngineMove,
  savePlayedGame,
  type PlayResult,
  type SavedGame,
  type Skill,
} from "./api";
import GameTips from "./GameTips";

const STRENGTH_OPTIONS: { value: Skill; label: string }[] = [
  { value: "easy", label: "Easy · ~1300 Elo" },
  { value: "medium", label: "Medium · ~1900 Elo" },
  { value: "hard", label: "Hard · ~2500 Elo" },
];

// Unicode chess piece symbols for rendering captured pieces
const PIECE_SYMBOLS: Record<string, string> = {
  P: "♟",
  N: "♞",
  B: "♝",
  R: "♜",
  Q: "♛",
  K: "♚",
  p: "♙",
  n: "♘",
  b: "♗",
  r: "♖",
  q: "♕",
  k: "♔",
};

// Standard chess piece values for material calculation
const PIECE_VALUES: Record<string, number> = {
  p: 1,
  n: 3,
  b: 3,
  r: 5,
  q: 9,
  k: 0,
};

interface CapturedPiece {
  piece: string;
  color: "white" | "black";
}

// Helper function to calculate material advantage from board state
function calculateMaterialAdvantage(chess: Chess): string {
  const board = chess.board();
  let whiteValue = 0;
  let blackValue = 0;

  for (const row of board) {
    for (const square of row) {
      if (square) {
        const value = PIECE_VALUES[square.type] || 0;
        if (square.color === "w") {
          whiteValue += value;
        } else {
          blackValue += value;
        }
      }
    }
  }

  const difference = whiteValue - blackValue;
  if (difference === 0) {
    return "Even";
  }
  const side = difference > 0 ? "White" : "Black";
  const amount = Math.abs(difference);
  return `${side} +${amount}`;
}

// Helper function to get captured pieces from game history
function getCapturedPieces(chess: Chess): { white: CapturedPiece[]; black: CapturedPiece[] } {
  const history = chess.history({ verbose: true });
  const whiteCaptured: CapturedPiece[] = [];
  const blackCaptured: CapturedPiece[] = [];

  for (const move of history) {
    if (move.captured) {
      if (move.color === "w") {
        blackCaptured.push({ piece: move.captured, color: "black" });
      } else {
        whiteCaptured.push({ piece: move.captured, color: "white" });
      }
    }
  }

  return { white: whiteCaptured, black: blackCaptured };
}

type GameStatus =
  | { state: "playing" }
  | { state: "thinking" }
  | { state: "over"; result: PlayResult; reason: string };

function outcomeFromGameOver(chess: Chess): { result: PlayResult; reason: string } {
  if (chess.isCheckmate()) {
    // The side to move is checkmated. If it's black to move, white (the
    // human, who always plays white) delivered mate and wins.
    const humanWon = chess.turn() === "b";
    return {
      result: humanWon ? "win" : "loss",
      reason: humanWon ? "Checkmate — you win!" : "Checkmate — the engine wins.",
    };
  }
  if (chess.isStalemate()) {
    return { result: "draw", reason: "Draw by stalemate." };
  }
  if (chess.isThreefoldRepetition()) {
    return { result: "draw", reason: "Draw by threefold repetition." };
  }
  if (chess.isInsufficientMaterial()) {
    return { result: "draw", reason: "Draw by insufficient material." };
  }
  if (chess.isDraw()) {
    return { result: "draw", reason: "Draw." };
  }
  return { result: "draw", reason: "Game over." };
}

function PlayPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  // Held in a ref so async callbacks always mutate/read the same live
  // instance, while `fen` in state drives re-renders of the board.
  const chessRef = useRef(new Chess());
  const moveListRef = useRef<HTMLDivElement>(null);
  const [fen, setFen] = useState(chessRef.current.fen());
  const [skill, setSkill] = useState<Skill>("medium");
  const [status, setStatus] = useState<GameStatus>({ state: "playing" });
  const [error, setError] = useState<string | null>(null);
  const [savedGame, setSavedGame] = useState<SavedGame | null>(null);
  const [saving, setSaving] = useState(false);

  // Auto-scroll move list to the bottom when new moves are added
  useEffect(() => {
    if (moveListRef.current) {
      moveListRef.current.scrollTop = moveListRef.current.scrollHeight;
    }
  }, [fen]);

  const finishGame = useCallback(
    async (result: PlayResult, reason: string) => {
      setStatus({ state: "over", result, reason });
      setSaving(true);
      const pgn = chessRef.current.pgn();
      try {
        const saved = await savePlayedGame(pgn, result);
        setSavedGame(saved);
      } catch (err) {
        if (err instanceof ApiError && err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to save the game."
        );
      } finally {
        setSaving(false);
      }
    },
    [onUnauthorized]
  );

  const checkGameOver = useCallback(
    (chess: Chess): boolean => {
      if (!chess.isGameOver()) return false;
      const { result, reason } = outcomeFromGameOver(chess);
      void finishGame(result, reason);
      return true;
    },
    [finishGame]
  );

  const requestEngineMove = useCallback(
    async (currentFen: string) => {
      setStatus({ state: "thinking" });
      try {
        const san = await getEngineMove(currentFen, skill);
        const chess = chessRef.current;
        chess.move(san);
        setFen(chess.fen());
        if (!checkGameOver(chess)) {
          setStatus({ state: "playing" });
        }
      } catch (err) {
        if (err instanceof ApiError && err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to get the engine's move."
        );
        setStatus({ state: "playing" });
      }
    },
    [skill, checkGameOver, onUnauthorized]
  );

  const onPieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs): boolean => {
      if (!targetSquare || status.state !== "playing") return false;

      const chess = chessRef.current;
      let move;
      try {
        move = chess.move({
          from: sourceSquare,
          to: targetSquare,
          promotion: "q",
        });
      } catch {
        return false;
      }
      if (!move) return false;

      setFen(chess.fen());
      setError(null);

      if (!checkGameOver(chess)) {
        void requestEngineMove(chess.fen());
      }

      return true;
    },
    [status.state, checkGameOver, requestEngineMove]
  );

  const handleResign = useCallback(() => {
    if (status.state !== "playing") return;
    void finishGame("loss", "You resigned.");
  }, [status, finishGame]);

  const handleNewGame = useCallback(() => {
    chessRef.current = new Chess();
    setFen(chessRef.current.fen());
    setStatus({ state: "playing" });
    setError(null);
    setSavedGame(null);
  }, []);

  const isOver = status.state === "over";
  const canResign = status.state === "playing";

  // Render move list
  const renderMoveList = () => {
    const moves = chessRef.current.history();
    const rows = [];

    for (let i = 0; i < moves.length; i += 2) {
      const moveNumber = Math.floor(i / 2) + 1;
      const whiteMove = moves[i];
      const blackMove = moves[i + 1];

      rows.push(
        <div key={moveNumber} className="move-list-row">
          <span className="move-number">{moveNumber}</span>
          <span className="move">{whiteMove}</span>
          {blackMove && <span className="move">{blackMove}</span>}
        </div>
      );
    }

    return rows;
  };

  // Render captured pieces
  const renderCapturedPieces = () => {
    const captured = getCapturedPieces(chessRef.current);

    return (
      <div className="captured-pieces">
        {captured.black.length > 0 && (
          <div className="captured-pieces-row">
            {captured.black.map((p, i) => (
              <span key={i} className="captured-piece">
                {PIECE_SYMBOLS[p.piece.toUpperCase()] || p.piece}
              </span>
            ))}
          </div>
        )}
        {captured.white.length > 0 && (
          <div className="captured-pieces-row">
            {captured.white.map((p, i) => (
              <span key={i} className="captured-piece">
                {PIECE_SYMBOLS[p.piece.toLowerCase()] || p.piece}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="play-panel">
      <div className="strength-selector">
        {STRENGTH_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={
              opt.value === skill ? "nav-pill nav-pill-active" : "nav-pill"
            }
            onClick={() => setSkill(opt.value)}
            disabled={status.state !== "playing"}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {isOver && savedGame ? (
        <>
          <div className="status-bar status-done">
            <span className="status-dot" />
            <span className="status-text">
              {status.state === "over" ? status.reason : ""}
            </span>
          </div>

          {error && <p className="sync-error">{error}</p>}

          <GameTips
            analysis={savedGame.analysis}
            coaching={savedGame.coaching}
            onPlayAgain={handleNewGame}
          />
        </>
      ) : (
        <>
          <div className="play-game-container">
            <div className="board-section">
              <div className="board-container">
                <Chessboard
                  options={{
                    position: fen,
                    onPieceDrop,
                    id: "play-vs-engine-board",
                    allowDragging: status.state === "playing",
                  }}
                />
              </div>

              <div className={`status-bar${isOver ? " status-done" : ""}`}>
                <span className="status-dot" />
                <span className="status-text">
                  {status.state === "thinking"
                    ? "Engine is thinking..."
                    : status.state === "over"
                      ? saving
                        ? "Analyzing your game..."
                        : status.reason
                      : "Your move."}
                </span>
              </div>

              {error && <p className="sync-error">{error}</p>}

              <div className="play-controls">
                <button
                  type="button"
                  className="resign-button"
                  onClick={handleResign}
                  disabled={!canResign}
                >
                  Resign
                </button>
                {isOver && (
                  <button type="button" onClick={handleNewGame}>
                    New game
                  </button>
                )}
              </div>
            </div>

            <div className="play-sidebar">
              <div className="material-badge">
                {calculateMaterialAdvantage(chessRef.current)}
              </div>

              {renderCapturedPieces()}

              <div className="move-list" ref={moveListRef}>
                {renderMoveList()}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default PlayPanel;
