import type { AnalysisEntry } from "./api";

const CLASSIFICATION_LABEL: Record<AnalysisEntry["classification"], string> = {
  blunder: "Blunder",
  mistake: "Mistake",
  inaccuracy: "Inaccuracy",
  good: "Good",
};

function GameTips({
  analysis,
  onPlayAgain,
}: {
  analysis: AnalysisEntry[];
  onPlayAgain: () => void;
}) {
  return (
    <div className="game-tips">
      <h2>Game tips</h2>
      {analysis.length === 0 ? (
        <p className="muted">No notable moves — nothing to review.</p>
      ) : (
        <ul className="tips-list">
          {analysis.map((entry, index) => (
            <li
              key={`${entry.move_number}-${index}`}
              className={`tip-row tip-${entry.classification}`}
            >
              <span className="tip-move">
                {entry.move_number}. {entry.san}
              </span>
              <span className="tip-label">
                {CLASSIFICATION_LABEL[entry.classification]}
              </span>
              {entry.classification !== "good" && entry.best_move && (
                <span className="tip-best-move">
                  Better: {entry.best_move}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={onPlayAgain}>
        Play again
      </button>
    </div>
  );
}

export default GameTips;
