import { useCallback, useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { Chessboard, type PieceDropHandlerArgs } from "react-chessboard";
import {
  ApiError,
  checkPracticeMove,
  getDailyFocus,
  getPracticePositions,
  type CheckMoveResult,
  type DailyFocus,
  type PracticePosition,
  type PracticePositionsResult,
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
  positions,
  onSolvedCorrectly,
  onRequestNewSet,
  onUnauthorized,
}: {
  positions: PracticePosition[];
  onSolvedCorrectly: () => void;
  onRequestNewSet: () => void;
  onUnauthorized: () => void;
}) {
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

  // PracticePanel re-renders PracticeBoard in place (no `key`) whenever a
  // new puzzle set is fetched, so the board's own state must resync itself
  // whenever the `positions` array identity changes — otherwise the last
  // solved position from the old set would stay on screen indefinitely.
  useEffect(() => {
    setIndex(0);
    chessRef.current = new Chess(positions[0].fen);
    setFen(chessRef.current.fen());
    setFeedback({ state: "idle" });
    setError(null);
  }, [positions]);

  const handleNextPosition = useCallback(() => {
    if (index + 1 >= positions.length) {
      onRequestNewSet();
      return;
    }
    const nextIndex = index + 1;
    setIndex(nextIndex);
    loadPosition(nextIndex);
  }, [index, positions.length, loadPosition, onRequestNewSet]);

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
      const position = positions[index];

      setFeedback({ state: "checking" });
      setError(null);

      void (async () => {
        try {
          const result: CheckMoveResult = await checkPracticeMove(
            startFen,
            moveUci,
            {
              game_id: position.game_id,
              move_number: position.move_number,
              side: position.side,
            }
          );
          const bestMoveSan = result.best_move
            ? uciToSan(startFen, result.best_move)
            : null;
          setFeedback({
            state: "result",
            correct: result.correct,
            bestMoveSan,
          });
          if (result.correct) onSolvedCorrectly();
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
    [feedback.state, index, positions, onUnauthorized, onSolvedCorrectly]
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
        <button type="button" onClick={handleNextPosition} disabled={feedback.state === "checking"}>
          Next position
        </button>
      </div>
    </div>
  );
}

function PracticePanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [focus, setFocus] = useState<DailyFocus | null>(null);
  const [focusError, setFocusError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [practice, setPractice] = useState<PracticePositionsResult | null>(null);
  const [practiceError, setPracticeError] = useState<string | null>(null);

  const loadPracticePositions = useCallback(async () => {
    try {
      const result = await getPracticePositions();
      setPractice(result);
      setPracticeError(null);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "unauthorized") {
        onUnauthorized();
        return;
      }
      setPracticeError(
        err instanceof ApiError ? err.message : "Failed to load practice puzzles."
      );
    }
  }, [onUnauthorized]);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const result = await getDailyFocus();
        if (cancelled) return;
        setFocus(result);
        setFocusError(null);
        if (result.status === "computing") {
          pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
        } else if (result.status === "ready") {
          void loadPracticePositions();
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        setFocusError(
          err instanceof ApiError ? err.message : "Failed to load today's focus."
        );
      }
    };

    void poll();

    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [onUnauthorized, loadPracticePositions]);

  const handleSolvedCorrectly = useCallback(() => {
    setPractice((prev) =>
      prev ? { ...prev, solved_count: prev.solved_count + 1 } : prev
    );
  }, []);

  if (focusError) {
    return <p className="sync-error">{focusError}</p>;
  }

  if (!focus || focus.status === "computing") {
    const total = focus?.progress_total ?? 0;
    const current = focus?.progress_current ?? 0;
    const hasProgress = total > 0;
    const percent = hasProgress
      ? Math.min(100, Math.round((current / total) * 100))
      : 0;

    return (
      <div className="status-bar-column">
        <div className="status-bar">
          <span className="status-dot" />
          <span className="status-text">
            {hasProgress
              ? `Analyzing your recent games... (${current} of ${total})`
              : "Getting started..."}
          </span>
        </div>
        {hasProgress && (
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
          </div>
        )}
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

      {practiceError && <p className="sync-error">{practiceError}</p>}

      {practice && practice.total_tracked > 0 && (
        <p className="muted practice-progress">
          Solved {practice.solved_count} of {practice.total_tracked} tracked puzzles
        </p>
      )}

      {practice && practice.skipped_count > 0 && (
        <p className="sync-error">
          {practice.skipped_count} puzzle
          {practice.skipped_count === 1 ? "" : "s"} couldn't be loaded from
          your games and were skipped.
        </p>
      )}

      {practice && practice.positions.length > 0 ? (
        <PracticeBoard
          positions={practice.positions}
          onSolvedCorrectly={handleSolvedCorrectly}
          onRequestNewSet={() => void loadPracticePositions()}
          onUnauthorized={onUnauthorized}
        />
      ) : practice ? (
        <p className="muted">No practice positions available right now.</p>
      ) : (
        <p className="muted">Loading practice puzzles...</p>
      )}
    </div>
  );
}

export default PracticePanel;
