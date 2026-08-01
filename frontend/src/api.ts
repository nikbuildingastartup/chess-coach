const TOKEN_STORAGE_KEY = "chess-coach-token";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000";

export type ApiErrorKind =
  | "unauthorized"
  | "chesscom_unreachable"
  | "user_not_found"
  | "other";

export class ApiError extends Error {
  kind: ApiErrorKind;
  status: number;

  constructor(kind: ApiErrorKind, status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export interface SyncResult {
  imported: number;
  total: number;
}

export interface Game {
  chesscom_game_id: string;
  end_time: string;
  time_class: string;
  result: string;
}

export type Skill = "easy" | "medium" | "hard";

export type PlayResult = "win" | "loss" | "draw";

export interface AnalysisEntry {
  move_number: number;
  san: string;
  side: "white" | "black";
  classification: "blunder" | "mistake" | "inaccuracy" | "good";
  eval_cp: number;
  best_move: string | null;
}

export interface Coaching {
  headline: string | null;
  explanation: string | null;
  recommendation: string | null;
}

export interface SavedGame {
  game_id: number;
  analysis: AnalysisEntry[];
  coaching: Coaching | null;
}

export interface GameAnalysis {
  analysis: AnalysisEntry[];
  coaching: Coaching | null;
}

export type FocusStatus = "computing" | "ready" | "insufficient_data" | "error";

export interface PracticePosition {
  fen: string;
  played_move: string;
  best_move: string | null;
  classification: string;
}

export interface DailyFocus {
  id: number;
  date: string;
  status: FocusStatus;
  headline: string | null;
  explanation: string | null;
  recommendation: string | null;
  created_at: string;
  practice_positions: PracticePosition[];
}

export interface CheckMoveResult {
  correct: boolean;
  best_move: string | null;
  played_eval_cp: number;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // response body wasn't JSON (or was empty) — fall back to statusText
    }

    if (response.status === 401) {
      throw new ApiError("unauthorized", response.status, detail);
    }
    if (response.status === 502) {
      throw new ApiError("chesscom_unreachable", response.status, detail);
    }
    if (response.status === 404) {
      throw new ApiError("user_not_found", response.status, detail);
    }
    throw new ApiError("other", response.status, detail);
  }

  return (await response.json()) as T;
}

export function syncGames(username: string): Promise<SyncResult> {
  return request<SyncResult>("/games/sync", {
    method: "POST",
    body: JSON.stringify({ username }),
  });
}

export function listGames(): Promise<Game[]> {
  return request<Game[]>("/games", { method: "GET" });
}

export function getEngineMove(fen: string, skill: Skill): Promise<string> {
  return request<{ move: string }>("/play/engine-move", {
    method: "POST",
    body: JSON.stringify({ fen, skill }),
  }).then((res) => res.move);
}

export function savePlayedGame(
  pgn: string,
  result: PlayResult
): Promise<SavedGame> {
  return request<SavedGame>("/play/games", {
    method: "POST",
    body: JSON.stringify({ pgn, result }),
  });
}

export function getDailyFocus(): Promise<DailyFocus> {
  return request<DailyFocus>("/focus/today", { method: "GET" });
}

export function checkPracticeMove(
  fen: string,
  moveUci: string
): Promise<CheckMoveResult> {
  return request<CheckMoveResult>("/practice/check-move", {
    method: "POST",
    body: JSON.stringify({ fen, move_uci: moveUci }),
  });
}
