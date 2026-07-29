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

function GamesList({ games }: { games: Game[] }) {
  if (games.length === 0) {
    return <p>No games imported yet.</p>;
  }

  return (
    <table className="games-list">
      <thead>
        <tr>
          <th>Date</th>
          <th>Time class</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody>
        {games.map((game) => (
          <tr key={game.chesscom_game_id}>
            <td>{new Date(game.end_time).toLocaleString()}</td>
            <td>{game.time_class}</td>
            <td>{game.result}</td>
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
  onSynced: () => void;
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
      onSynced();
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

  return (
    <form onSubmit={handleSubmit} className="sync-panel">
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
      {result && (
        <p className="sync-result">
          Imported {result.imported} new game(s) ({result.total} total).
        </p>
      )}
      {error && <p className="sync-error">{error}</p>}
    </form>
  );
}

function App() {
  const [token, setTokenState] = useState<string | null>(() => getToken());
  const [games, setGames] = useState<Game[]>([]);
  const [gamesError, setGamesError] = useState<string | null>(null);

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
    <div className="app">
      <h1>Chess Coach</h1>
      <SyncPanel onSynced={loadGames} onUnauthorized={handleUnauthorized} />
      <h2>Games</h2>
      {gamesError && <p className="sync-error">{gamesError}</p>}
      <GamesList games={games} />
    </div>
  );
}

export default App;
