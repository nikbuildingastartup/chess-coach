import { useEffect, useState } from "react";
import { getUsageSummary, type UsageSummary } from "./api";

function formatCost(costUsd: number): string {
  return `$${costUsd.toFixed(4)}`;
}

function UsagePill() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    getUsageSummary()
      .then((res) => {
        if (!cancelled) setSummary(res);
      })
      .catch((err) => {
        // Usage display is a nice-to-have — never let it block the rest of the UI.
        console.error("Failed to load usage summary", err);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const recentDays = summary ? summary.by_day.slice(-7) : [];

  return (
    <div className="usage-pill-container">
      <button
        type="button"
        className="usage-pill"
        onClick={() => setOpen((prev) => !prev)}
      >
        {summary ? formatCost(summary.total_cost_usd) : "$—"}
      </button>
      {open && summary && (
        <div className="usage-popover">
          <div className="usage-popover-summary">
            <span>{summary.total_calls} calls</span>
            <span>{formatCost(summary.total_cost_usd)} total</span>
          </div>

          <h4 className="usage-popover-heading">By call site</h4>
          {summary.by_call_site.length === 0 ? (
            <p className="muted">No calls yet.</p>
          ) : (
            <ul className="usage-list">
              {summary.by_call_site.map((entry) => (
                <li key={entry.call_site} className="usage-list-row">
                  <span>{entry.call_site}</span>
                  <span>{entry.calls}</span>
                  <span>{formatCost(entry.cost_usd)}</span>
                </li>
              ))}
            </ul>
          )}

          <h4 className="usage-popover-heading">Last 7 days</h4>
          {recentDays.length === 0 ? (
            <p className="muted">No usage yet.</p>
          ) : (
            <ul className="usage-list">
              {recentDays.map((entry) => (
                <li key={entry.date} className="usage-list-row">
                  <span>{entry.date}</span>
                  <span>{entry.calls}</span>
                  <span>{formatCost(entry.cost_usd)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default UsagePill;
