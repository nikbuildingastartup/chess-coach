import { useCallback, useMemo, useRef, useState, useEffect } from "react";
import { Chess, type Move, type Square } from "chess.js";
import {
  Chessboard,
  type PieceDropHandlerArgs,
  type PieceHandlerArgs,
  type SquareHandlerArgs,
} from "react-chessboard";
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

interface LastMove {
  from: string;
  to: string;
}

interface LegalTarget {
  square: string;
  capture: boolean;
}

// Helper function to find the king square of the side to move (used for
// check highlighting).
function findKingSquare(chess: Chess): string | null {
  const board = chess.board();
  const sideToMove = chess.turn();
  for (const row of board) {
    for (const square of row) {
      if (square && square.type === "k" && square.color === sideToMove) {
        return square.square;
      }
    }
  }
  return null;
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
  const [lastMove, setLastMove] = useState<LastMove | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [legalTargets, setLegalTargets] = useState<LegalTarget[]>([]);

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
        const move = chess.move(san);
        setFen(chess.fen());
        if (move) {
          setLastMove({ from: move.from, to: move.to });
        }
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

  // Shared "apply this already-executed move, check for game over, request
  // the engine's reply" logic. Both the drag-drop handler and the
  // click-to-move handler call this after they've successfully applied a
  // move via chess.move() — keeps the two input paths from duplicating the
  // post-move bookkeeping.
  const applyUserMove = useCallback(
    (move: Move) => {
      const chess = chessRef.current;
      setFen(chess.fen());
      setError(null);
      setLastMove({ from: move.from, to: move.to });
      setSelectedSquare(null);
      setLegalTargets([]);

      if (!checkGameOver(chess)) {
        void requestEngineMove(chess.fen());
      }
    },
    [checkGameOver, requestEngineMove]
  );

  const tryUserMove = useCallback(
    (from: string, to: string): boolean => {
      if (status.state !== "playing") return false;

      const chess = chessRef.current;
      let move: Move | null;
      try {
        move = chess.move({ from, to, promotion: "q" });
      } catch {
        return false;
      }
      if (!move) return false;

      applyUserMove(move);
      return true;
    },
    [status.state, applyUserMove]
  );

  const onPieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs): boolean => {
      if (!targetSquare) return false;
      return tryUserMove(sourceSquare, targetSquare);
    },
    [tryUserMove]
  );

  // Selects a square for click-to-move: if it holds a piece belonging to
  // the side to move, highlight its legal destinations; otherwise clear
  // the selection.
  const selectSquare = useCallback((square: string) => {
    const chess = chessRef.current;
    const piece = chess.get(square as Square);
    if (piece && piece.color === chess.turn()) {
      const moves = chess.moves({ square: square as Square, verbose: true });
      if (moves.length > 0) {
        setSelectedSquare(square);
        setLegalTargets(
          moves.map((m) => ({ square: m.to, capture: !!m.captured }))
        );
        return;
      }
    }
    setSelectedSquare(null);
    setLegalTargets([]);
  }, []);

  // Shared click handler for both onSquareClick (empty squares) and
  // onPieceClick (squares with a piece on them) — react-chessboard only
  // fires one or the other depending on square contents.
  const handleSquareInteraction = useCallback(
    (square: string | null) => {
      if (!square || status.state !== "playing") return;

      if (selectedSquare) {
        if (square === selectedSquare) {
          setSelectedSquare(null);
          setLegalTargets([]);
          return;
        }
        if (legalTargets.some((t) => t.square === square)) {
          tryUserMove(selectedSquare, square);
          return;
        }
      }

      selectSquare(square);
    },
    [status.state, selectedSquare, legalTargets, tryUserMove, selectSquare]
  );

  const onSquareClick = useCallback(
    ({ square }: SquareHandlerArgs) => handleSquareInteraction(square),
    [handleSquareInteraction]
  );

  const onPieceClick = useCallback(
    ({ square }: PieceHandlerArgs) => handleSquareInteraction(square),
    [handleSquareInteraction]
  );

  // Drag start also shows legal-move dots for the piece being dragged.
  const onPieceDrag = useCallback(
    ({ square }: PieceHandlerArgs) => {
      if (status.state !== "playing" || !square) return;
      selectSquare(square);
    },
    [status.state, selectSquare]
  );

  // Merge last-move, check, and legal-move-dot highlights into one
  // squareStyles object without letting one source clobber another.
  const squareStyles = useMemo(() => {
    const styles: Record<string, React.CSSProperties> = {};
    // A fresh instance built from `fen` (rather than reading chessRef.current
    // directly) so this memo's dependency array is accurate — chessRef is
    // mutated in place and wouldn't otherwise be a valid useMemo dependency.
    const chess = new Chess(fen);

    if (lastMove) {
      styles[lastMove.from] = {
        ...styles[lastMove.from],
        backgroundColor: "var(--last-move-overlay)",
      };
      styles[lastMove.to] = {
        ...styles[lastMove.to],
        backgroundColor: "var(--last-move-overlay)",
      };
    }

    if (chess.inCheck()) {
      const kingSquare = findKingSquare(chess);
      if (kingSquare) {
        styles[kingSquare] = {
          ...styles[kingSquare],
          backgroundColor: "var(--check-overlay)",
        };
      }
    }

    for (const target of legalTargets) {
      styles[target.square] = {
        ...styles[target.square],
        backgroundImage: target.capture
          ? "radial-gradient(circle, transparent 58%, var(--legal-move-overlay) 60%, var(--legal-move-overlay) 72%, transparent 74%)"
          : "radial-gradient(circle, var(--legal-move-overlay) 22%, transparent 24%)",
      };
    }

    if (selectedSquare) {
      styles[selectedSquare] = {
        ...styles[selectedSquare],
        backgroundColor: "var(--legal-move-overlay)",
      };
    }

    return styles;
  }, [fen, lastMove, legalTargets, selectedSquare]);

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
    setLastMove(null);
    setSelectedSquare(null);
    setLegalTargets([]);
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
                    onPieceDrag,
                    onSquareClick,
                    onPieceClick,
                    squareStyles,
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
