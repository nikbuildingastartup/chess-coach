import { useCallback, useRef, useState } from "react";
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

const STRENGTH_OPTIONS: { value: Skill; label: string }[] = [
  { value: "easy", label: "Easy" },
  { value: "medium", label: "Medium" },
  { value: "hard", label: "Hard" },
];

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
  const [fen, setFen] = useState(chessRef.current.fen());
  const [skill, setSkill] = useState<Skill>("medium");
  const [status, setStatus] = useState<GameStatus>({ state: "playing" });
  const [error, setError] = useState<string | null>(null);
  const [savedGame, setSavedGame] = useState<SavedGame | null>(null);

  const finishGame = useCallback(
    async (result: PlayResult, reason: string) => {
      setStatus({ state: "over", result, reason });
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
    if (status.state === "over") return;
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

      <div className={`status-bar status-${isOver ? "done" : "idle"}`}>
        <span className="status-dot" />
        <span className="status-text">
          {status.state === "thinking"
            ? "Engine is thinking..."
            : status.state === "over"
              ? status.reason
              : "Your move."}
        </span>
      </div>

      {error && <p className="sync-error">{error}</p>}

      {isOver && savedGame && (
        <p className="muted">Game over — analysis received.</p>
      )}

      <div className="play-controls">
        <button
          type="button"
          className="resign-button"
          onClick={handleResign}
          disabled={isOver}
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
  );
}

export default PlayPanel;
