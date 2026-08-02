import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clearToken,
  getToken,
  listGames,
  setToken as storeToken,
  syncGames,
  type Game,
} from "./api";
import PlayPanel from "./PlayPanel";
import PracticePanel from "./PracticePanel";
import UsagePill from "./UsagePill";
import "./App.css";

function TokenGate({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    onSubmit(value.trim());
  };

  return (
    <form onSubmit={handleSubmit} className="token-gate">
      <h1>Chess Coach</h1>
      <label htmlFor="token">Access token</label>
      <input
        id="token"
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Enter your access token"
        autoFocus
      />
      <button type="submit">Continue</button>
    </form>
  );
}

const LOST_OUTCOMES = new Set([
  "resigned",
  "checkmated",
  "timeout",
  "abandoned",
]);
const DRAWN_OUTCOMES = new Set([
  "agreed",
  "repetition",
  "stalemate",
  "insufficient",
  "50move",
  "timevsinsufficient",
]);

function resultTone(result: string): "won" | "lost" | "drawn" {
  if (result === "win") return "won";
  if (LOST_OUTCOMES.has(result)) return "lost";
  if (DRAWN_OUTCOMES.has(result)) return "drawn";
  return "drawn";
}

function resultLabel(result: string): string {
  const tone = resultTone(result);
  if (tone === "won") return "Won";
  if (tone === "lost") return "Lost";
  return "Drew";
}

function GamesList({ games }: { games: Game[] }) {
  if (games.length === 0) {
    return <p className="muted">No games imported yet.</p>;
  }

  return (
    <table className="games-list">
      <thead>
        <tr>
          <th>Date</th>
          <th>Result</th>
          <th>Time control</th>
        </tr>
      </thead>
      <tbody>
        {games.map((game) => (
          <tr key={game.chesscom_game_id}>
            <td>
              {new Date(game.end_time).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
            </td>
            <td className={`result result-${resultTone(game.result)}`}>
              {resultLabel(game.result)}
            </td>
            <td>{game.time_class}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SyncPanel({
  onSynced,
  onUnauthorized,
}: {
  onSynced: (username: string) => void;
  onUnauthorized: () => void;
}) {
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ imported: number; total: number } | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await syncGames(username.trim());
      setResult(res);
      onSynced(username.trim());
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        if (err.kind === "chesscom_unreachable") {
          setError("Chess.com is currently unreachable. Please try again later.");
        } else if (err.kind === "user_not_found") {
          setError("No Chess.com user by that name. Double-check the username.");
        } else {
          setError(err.message || "Something went wrong while syncing.");
        }
      } else {
        setError("Something went wrong while syncing.");
      }
    } finally {
      setLoading(false);
    }
  };

  const statusTone = error ? "error" : result ? "done" : "idle";
  const statusText = error
    ? error
    : result
      ? `Up to date · imported ${result.imported} new game${result.imported === 1 ? "" : "s"} (${result.total} total)`
      : "Enter a Chess.com username to sync";

  return (
    <form onSubmit={handleSubmit} className="sync-panel">
      <div className={`status-bar status-${statusTone}`}>
        <span className="status-dot" />
        <span className="status-text">{statusText}</span>
        <div className="status-steps">
          <span className={loading ? "step step-active" : "step"}>
            {loading ? "Syncing" : "Idle"}
          </span>
          <span className={result ? "step step-active" : "step"}>Done</span>
        </div>
      </div>
      <div className="sync-controls">
        <label htmlFor="username">Chess.com username</label>
        <input
          id="username"
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="e.g. hikaru"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !username.trim()}>
          {loading ? "Syncing..." : "Sync"}
        </button>
      </div>
    </form>
  );
}

type Tab = "sync" | "play" | "practice";

const NAV_TABS: { key: Tab | null; label: string }[] = [
  { key: null, label: "Auth" },
  { key: null, label: "Cold start" },
  { key: "sync", label: "Sync" },
  { key: "play", label: "Play" },
  { key: "practice", label: "Practice" },
];

function TopNav({
  activeTab,
  onTabChange,
}: {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}) {
  return (
    <nav className="top-nav">
      {NAV_TABS.map(({ key, label }) => {
        if (key === null) {
          return (
            <span key={label} className="nav-pill nav-pill-disabled">
              {label}
            </span>
          );
        }
        return (
          <button
            key={label}
            type="button"
            className={key === activeTab ? "nav-pill nav-pill-active" : "nav-pill"}
            onClick={() => onTabChange(key)}
          >
            {label}
          </button>
        );
      })}
    </nav>
  );
}

function App() {
  const [token, setTokenState] = useState<string | null>(() => getToken());
  const [games, setGames] = useState<Game[]>([]);
  const [gamesError, setGamesError] = useState<string | null>(null);
  const [syncedUsername, setSyncedUsername] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("sync");

  const handleUnauthorized = useCallback(() => {
    clearToken();
    setTokenState(null);
    setGames([]);
  }, []);

  const loadGames = useCallback(async () => {
    try {
      const fetched = await listGames();
      setGames(fetched);
      setGamesError(null);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "unauthorized") {
        handleUnauthorized();
        return;
      }
      setGamesError(
        err instanceof ApiError ? err.message : "Failed to load games."
      );
    }
  }, [handleUnauthorized]);

  useEffect(() => {
    if (token) {
      loadGames();
    }
  }, [token, loadGames]);

  if (!token) {
    return (
      <TokenGate
        onSubmit={(newToken) => {
          storeToken(newToken);
          setTokenState(newToken);
        }}
      />
    );
  }

  return (
    <div className="page">
      <div className="top-bar">
        <TopNav activeTab={activeTab} onTabChange={setActiveTab} />
        <UsagePill />
      </div>
      {activeTab === "sync" ? (
        <div className="app">
          <p className="eyebrow">Sync</p>
          <h1>
            {syncedUsername ? `${syncedUsername} on Chess.com` : "Chess Coach"}
          </h1>
          <div className="card">
            <SyncPanel
              onSynced={(username) => {
                setSyncedUsername(username);
                loadGames();
              }}
              onUnauthorized={handleUnauthorized}
            />
          </div>
          <div className="card">
            {gamesError && <p className="sync-error">{gamesError}</p>}
            <GamesList games={games} />
          </div>
          <button
            type="button"
            className="focus-button"
            onClick={() => setActiveTab("practice")}
          >
            See today's focus
          </button>
        </div>
      ) : activeTab === "play" ? (
        <div className="app">
          <p className="eyebrow">Play</p>
          <h1>Play vs. the engine</h1>
          <div className="card">
            <PlayPanel onUnauthorized={handleUnauthorized} />
          </div>
        </div>
      ) : (
        <div className="app">
          <p className="eyebrow">Practice</p>
          <h1>Today's focus</h1>
          <div className="card">
            <PracticePanel onUnauthorized={handleUnauthorized} />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
