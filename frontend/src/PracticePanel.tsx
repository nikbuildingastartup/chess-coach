import { useCallback, useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { Chessboard, type PieceDropHandlerArgs } from "react-chessboard";
import {
  ApiError,
  checkPracticeMove,
  getDailyFocus,
  type CheckMoveResult,
  type DailyFocus,
} from "./api";

const POLL_INTERVAL_MS = 4000;

type MoveFeedback =
  | { state: "idle" }
  | { state: "checking" }
  | { state: "result"; correct: boolean; bestMoveSan: string | null };

function uciToSan(fen: string, moveUci: string): string | null {
  try {
    const chess = new Chess(fen);
    const from = moveUci.slice(0, 2);
    const to = moveUci.slice(2, 4);
    const promotion = moveUci.length > 4 ? moveUci.slice(4) : undefined;
    const move = chess.move({ from, to, promotion });
    return move ? move.san : null;
  } catch {
    return null;
  }
}

function DailyFocusCard({ focus }: { focus: DailyFocus }) {
  if (!focus.headline && !focus.explanation && !focus.recommendation) {
    return null;
  }
  return (
    <div className="card coaching-card">
      {focus.headline && <h3 className="coaching-headline">{focus.headline}</h3>}
      {focus.explanation && (
        <p className="coaching-explanation">{focus.explanation}</p>
      )}
      {focus.recommendation && (
        <p className="coaching-recommendation">{focus.recommendation}</p>
      )}
    </div>
  );
}

function PracticeBoard({
  focus,
  onUnauthorized,
}: {
  focus: DailyFocus;
  onUnauthorized: () => void;
}) {
  const positions = focus.practice_positions;
  const [index, setIndex] = useState(0);
  const chessRef = useRef(new Chess(positions[0].fen));
  const [fen, setFen] = useState(chessRef.current.fen());
  const [feedback, setFeedback] = useState<MoveFeedback>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);

  const loadPosition = useCallback((posIndex: number) => {
    const position = positions[posIndex];
    chessRef.current = new Chess(position.fen);
    setFen(chessRef.current.fen());
    setFeedback({ state: "idle" });
    setError(null);
  }, [positions]);

  const handleNextPosition = useCallback(() => {
    const nextIndex = (index + 1) % positions.length;
    setIndex(nextIndex);
    loadPosition(nextIndex);
  }, [index, positions.length, loadPosition]);

  const onPieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs): boolean => {
      if (!targetSquare || feedback.state !== "idle") return false;

      const startFen = chessRef.current.fen();
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

      const promotion = move.promotion ?? "";
      const moveUci = `${sourceSquare}${targetSquare}${promotion}`;

      setFeedback({ state: "checking" });
      setError(null);

      void (async () => {
        try {
          const result: CheckMoveResult = await checkPracticeMove(
            startFen,
            moveUci
          );
          const bestMoveSan = result.best_move
            ? uciToSan(startFen, result.best_move)
            : null;
          setFeedback({
            state: "result",
            correct: result.correct,
            bestMoveSan,
          });
        } catch (err) {
          if (err instanceof ApiError && err.kind === "unauthorized") {
            onUnauthorized();
            return;
          }
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to check that move."
          );
          chessRef.current = new Chess(startFen);
          setFen(chessRef.current.fen());
          setFeedback({ state: "idle" });
        }
      })();

      return true;
    },
    [feedback.state, onUnauthorized]
  );

  const position = positions[index];

  return (
    <div className="practice-board-panel">
      <p className="muted practice-position-meta">
        Position {index + 1} of {positions.length} — your{" "}
        {position.classification} ({position.played_move})
      </p>

      <div className="board-container">
        <Chessboard
          options={{
            position: fen,
            onPieceDrop,
            id: "practice-board",
            allowDragging: feedback.state === "idle",
          }}
        />
      </div>

      <div
        className={`status-bar${
          feedback.state === "result"
            ? feedback.correct
              ? " status-done"
              : " status-error"
            : ""
        }`}
      >
        <span className="status-dot" />
        <span className="status-text">
          {feedback.state === "checking"
            ? "Checking move..."
            : feedback.state === "result"
              ? feedback.correct
                ? "Correct — that was the best move."
                : feedback.bestMoveSan
                  ? `Not quite — Stockfish preferred ${feedback.bestMoveSan}.`
                  : "Not quite the best move."
              : "Find the best move for this position."}
        </span>
      </div>

      {error && <p className="sync-error">{error}</p>}

      <div className="play-controls">
        <button
          type="button"
          onClick={handleNextPosition}
          disabled={
            feedback.state === "checking" ||
            (positions.length <= 1 && feedback.state === "idle")
          }
        >
          Next position
        </button>
      </div>
    </div>
  );
}

function PracticePanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [focus, setFocus] = useState<DailyFocus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const result = await getDailyFocus();
        if (cancelled) return;
        setFocus(result);
        setError(null);
        if (result.status === "computing") {
          pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to load today's focus."
        );
      }
    };

    void poll();

    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [onUnauthorized]);

  if (error) {
    return <p className="sync-error">{error}</p>;
  }

  if (!focus || focus.status === "computing") {
    return (
      <div className="status-bar">
        <span className="status-dot" />
        <span className="status-text">
          Analyzing your recent games to build today's focus...
        </span>
      </div>
    );
  }

  if (focus.status === "insufficient_data") {
    return (
      <p className="muted">
        Not enough analyzed games yet to build a daily focus. Sync and play
        (or import) a few more games, then check back here.
      </p>
    );
  }

  if (focus.status === "error") {
    return (
      <p className="sync-error">
        Something went wrong computing today's focus. Please try again
        later.
      </p>
    );
  }

  return (
    <div className="practice-panel">
      <DailyFocusCard focus={focus} />
      {focus.practice_positions.length > 0 ? (
        <PracticeBoard focus={focus} onUnauthorized={onUnauthorized} />
      ) : (
        <p className="muted">
          No practice positions available from today's focus yet.
        </p>
      )}
    </div>
  );
}

export default PracticePanel;
