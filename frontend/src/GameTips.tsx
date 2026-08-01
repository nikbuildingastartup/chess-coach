import type { AnalysisEntry, Coaching } from "./api";

const CLASSIFICATION_LABEL: Record<AnalysisEntry["classification"], string> = {
  blunder: "Blunder",
  mistake: "Mistake",
  inaccuracy: "Inaccuracy",
  good: "Good",
};

function CoachingCard({ coaching }: { coaching: Coaching | null }) {
  if (!coaching) return null;
  const { headline, explanation, recommendation } = coaching;
  if (!headline && !explanation && !recommendation) return null;

  return (
    <div className="card coaching-card">
      {headline && <h3 className="coaching-headline">{headline}</h3>}
      {explanation && <p className="coaching-explanation">{explanation}</p>}
      {recommendation && (
        <p className="coaching-recommendation">{recommendation}</p>
      )}
    </div>
  );
}

function GameTips({
  analysis,
  coaching,
  onPlayAgain,
}: {
  analysis: AnalysisEntry[];
  coaching: Coaching | null;
  onPlayAgain: () => void;
}) {
  return (
    <div className="game-tips">
      <h2>Game tips</h2>
      <CoachingCard coaching={coaching} />
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
