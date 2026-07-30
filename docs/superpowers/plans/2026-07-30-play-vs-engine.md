# Chess Coach — Play vs. Stockfish + Post-Game Tips

## Context

Chess.com-Import läuft (PR #1, 3081 echte Partien importiert, Sync-Screen im
finalen Look). Nächstes Ziel laut Nutzer, **heute noch erreichbar**: gegen
den Computer spielen können, und nach der Partie Tipps bekommen, wie man
sich verbessern kann — der "Play Module" + "Analysis Engine"-Teil aus dem
Original-Spec
([docs/superpowers/specs/2026-07-28-chess-coach-design.md](../specs/2026-07-28-chess-coach-design.md)),
bewusst ohne die Weakness-Profile-Komponente (Muster über mehrere Partien
erkennen) — das ist laut Nutzer ein späterer Schritt.

Entscheidungen mit dem Nutzer geklärt:
1. Tipps kommen **nach** der Partie als Zug-Liste (Blunder/Mistake/
   Inaccuracy/Good + Stockfishs besserer Zug), nicht live während des Spiels.
2. Stärke: 3 feste Stufen (Leicht/Mittel/Schwer), kein Feinslider.
3. Kein Muster-Erkennen über mehrere Partien (Weakness Profile) — nur pro
   Partie.
4. "Play" wird ein echter, klickbarer Tab neben "Sync" (lokaler UI-State,
   kein Router nötig). Auth/Cold start/Daily focus bleiben Deko.

## Vorbereitung (vor den eigentlichen Tasks, direkt von mir ausgeführt)

1. Die uncommitteten Sync-Screen-Styling-Änderungen im aktuellen Worktree
   committen und auf PR #1 pushen.
2. PR #1 mergen (Standard-Merge nach main) — Sync-Feature ist fertig
   getestet, sauberer Abschluss.
3. Neuen Worktree/Branch **von main** für dieses Feature aufsetzen (z. B.
   `play-vs-engine`), damit der gesamte bisherige Code (Backend + Frontend)
   als Basis vorhanden ist.
4. `brew install stockfish` (lokale Engine-Binary, wird von `PATH`
   gefunden — kein hartcodierter Pfad im Code).

Danach folgt die Umsetzung als 4 Tasks über Subagent-Driven Development
(wie beim letzten Feature), da der Nutzer das explizit so möchte.

## Global Constraints

- Backend: `uv`, Python >= 3.12, `backend/`. Neue Abhängigkeit:
  `python-chess`. Stockfish-Pfad kommt aus `Settings.stockfish_path: str`
  (env-gesteuert, Default `"stockfish"` — wird über `PATH` gefunden, kein
  hartcodierter Homebrew-Pfad).
- Alle neuen Endpunkte hinter `require_auth` (bestehende Dependency aus
  `backend/app/auth.py`).
- **Schema-Änderung** an `Game` (`backend/app/models.py`):
  - `chesscom_game_id` wird **nullable** (gespielte Partien haben keine
    Chess.com-ID; SQLite erlaubt mehrere NULLs trotz Unique-Index).
  - neues Feld `source: str` — `"chesscom"` oder `"played"`. Der
    bestehende Sync-Endpoint (`routers/games.py`) muss beim Schreiben
    `source="chesscom"` setzen (kleine Anpassung an bestehendem Code).
  - neues Feld `analysis_json: str | None` — JSON-serialisierte Zug-Liste
    (siehe unten), `None` solange nicht analysiert.
  - Für gespielte Partien: `time_class = "untimed"`, `result` ∈
    `"win"|"loss"|"draw"` (aus Nutzer-Perspektive — bewusst simpler als
    Chess.com's Vokabular, da wir hier selbst die Werte erzeugen).
- **Analyse-Logik** (Blunder-Klassifizierung): für jeden Halbzug Stockfish-
  Eval vor und nach dem Zug vergleichen (aus Sicht des Ziehenden), mit
  `chess.engine.Limit(time=0.1)` pro Stellung (Geschwindigkeit vor
  Präzision — reicht für Blunder-Erkennung, ~40 Züge ≈ 8s synchron pro
  Request). Schwellen: ≥200cp Verschlechterung = `blunder`, 100–199cp =
  `mistake`, 50–99cp = `inaccuracy`, <50cp = `good`. Bei allem außer `good`
  zusätzlich Stockfishs vorgeschlagenen besseren Zug (SAN) mitliefern. Eine
  einzige `SimpleEngine`-Instanz pro Analyse-Durchlauf wiederverwenden
  (nicht pro Zug neu starten).
- **Engine-Gegner-Stärke**: 3 Presets über Stockfish "Skill Level" (UCI-
  Option, 0–20) — `easy=3`, `medium=10`, `hard=18`. Für Spielzüge kurzes
  Zeitlimit (`chess.engine.Limit(time=0.5)`), damit die App responsiv
  bleibt.
- Jeder Task muss `uv run pytest` grün haben (Backend) bzw. `npm run
  build` fehlerfrei (Frontend), bevor er als DONE gilt.

## Task 1 — Backend: Engine-Zug-Endpunkt

- `backend/app/config.py`: `stockfish_path: str = "stockfish"` zu
  `Settings` hinzufügen.
- `backend/app/chess_engine.py` (neu): Hilfsfunktion(en) um python-chess +
  Stockfish anzusprechen — `get_engine_move(fen: str, skill: str) -> str`
  (gibt den Zug als SAN zurück, `skill` ∈ `"easy"|"medium"|"hard"` gemäß
  obiger Skill-Level-Zuordnung, `Limit(time=0.5)`).
- `backend/app/routers/play.py` (neu): `POST /play/engine-move` — Body
  `{"fen": str, "skill": "easy"|"medium"|"hard"}`, hinter `require_auth`,
  ruft `get_engine_move`, gibt `{"move": "<SAN>"}` zurück. 422 bei
  ungültigem `skill`-Wert (Pydantic-Enum). In `main.py` einbinden.
- Tests: mocken die Engine-Interaktion nicht komplett weg (Stockfish ist
  lokal installiert, echte Aufrufe sind schnell genug bei `time=0.5`) —
  mindestens ein Test, der für eine bekannte Startstellung einen legalen
  Zug zurückbekommt, plus 401-Test ohne Token, plus 422 bei ungültigem
  `skill`.

## Task 2 — Backend: gespielte Partie speichern + analysieren

Depends on Task 1 (`chess_engine.py` wird erweitert).

- `backend/app/models.py`: Schema-Änderungen aus den Global Constraints
  umsetzen (`chesscom_game_id` nullable, `source`, `analysis_json`).
  Bestehenden Sync-Code (`routers/games.py`) anpassen, damit er weiterhin
  funktioniert (`source="chesscom"` setzen) — bestehende Tests müssen grün
  bleiben.
- `backend/app/chess_engine.py`: `analyze_game(pgn: str) -> list[dict]`
  ergänzen — implementiert die Blunder-Klassifizierung aus den Global
  Constraints, gibt pro Halbzug
  `{"move_number": int, "san": str, "classification": "blunder"|"mistake"|"inaccuracy"|"good", "eval_cp": int, "best_move": str | None}`
  zurück.
- `backend/app/routers/play.py`: `POST /play/games` — Body
  `{"pgn": str, "result": "win"|"loss"|"draw"}`, hinter `require_auth`.
  Speichert die Partie (`source="played"`, `chesscom_game_id=None`,
  `time_class="untimed"`), ruft `analyze_game`, speichert das Ergebnis in
  `analysis_json`, `analyzed=True`, gibt
  `{"game_id": int, "analysis": [...]}` zurück (gleiche Struktur wie
  `analyze_game`). Zusätzlich `GET /play/games/{game_id}/analysis` —
  liefert die gespeicherte Analyse erneut (für Reload-Fälle).
- Tests: Partie speichern + Analyse-Response-Struktur prüfen (bei einer
  kurzen PGN mit einem bekannten Blunder — z. B. eine Partie, die eine
  Dame en prise stehen lässt), Idempotenz ist hier kein Thema (jede
  gespielte Partie ist neu), 401-Test, `GET .../analysis` nach dem Speichern.

## Task 3 — Frontend: Play-Tab mit Brett gegen die Engine

Depends on Task 1+2 (API-Form ist dann eingefroren).

- Neue Abhängigkeiten: `chess.js`, `react-chessboard`.
- `frontend/src/App.tsx`: `TopNav` wird teilweise funktional — lokaler
  State `activeTab: "sync" | "play"`, "Sync" und "Play" klickbar (Auth/Cold
  start/Daily focus bleiben rein dekorativ, `disabled`-Optik). Je nach Tab
  wird `SyncPanel`-Bereich oder neue `PlayPanel`-Komponente gerendert.
- `frontend/src/PlayPanel.tsx` (neu): hält eine `chess.js`-`Chess()`-
  Instanz im State. Strength-Auswahl (3 Buttons Leicht/Mittel/Schwer,
  gleiche visuelle Sprache wie bestehende Buttons/Pills). `react-chessboard`
  fürs Brett, `onPieceDrop` validiert per `chess.js`, bei legalem Zug:
  Board updaten, dann `POST /play/engine-move` mit resultierendem FEN +
  gewählter Stärke, Engine-Antwortzug anwenden. Nach jedem Halbzug
  `chess.isGameOver()` prüfen (Matt/Patt/Remis). "Resign"-Button beendet
  die Partie sofort als Niederlage.
- `frontend/src/api.ts`: `getEngineMove(fen, skill)` und
  `savePlayedGame(pgn, result)` ergänzen, gleiches Fehler-/Auth-Handling
  wie bestehende Funktionen (401 → Token löschen, wie bei `syncGames`).
- Bei Spielende: Ergebnis aus Nutzer-Perspektive bestimmen, `savePlayedGame`
  aufrufen, Analyse-Response zwischenspeichern für Task 4.
- `npm run build` muss fehlerfrei sein; kein automatisierter Test nötig
  (gleiche Begründung wie beim Sync-Screen — manuell im Browser prüfen).

## Task 4 — Frontend: Tipps-Ansicht nach Spielende

Depends on Task 3 (Analyse-Daten liegen nach Spielende im State vor).

- Neue Komponente `frontend/src/GameTips.tsx`: Liste der Halbzüge aus der
  Analyse-Response — Zugnummer, SAN, farbiges Label je Klassifizierung
  (Blunder=rot, Mistake=orange, Inaccuracy=gelb/gedämpft, Good=grün/neutral
  — bestehende Farbtokens aus `App.css` wiederverwenden, z. B. `--lost` für
  Blunder, `--won` für Good), bei allem außer Good zusätzlich "Besser:
  \<best_move\>". "Play again"-Button setzt `PlayPanel` auf ein neues Spiel
  zurück.
- Einbindung in `PlayPanel`: nach erfolgreichem `savePlayedGame` wird statt
  Brett die `GameTips`-Ansicht gezeigt.
- `npm run build` fehlerfrei; manuelle Prüfung im Browser (Partie zu Ende
  spielen, Tipps erscheinen).

## Nicht Teil dieses Schritts

- Weakness Profile (Muster über mehrere Partien).
- Focus Generator / Daily-Focus-Screen.
- Live-Tipps während der Partie.
- Analyse von importierten Chess.com-Partien (nur gespielte Partien werden
  in diesem Schritt automatisch analysiert — Chess.com-Partien nachträglich
  zu analysieren ist ein separater, späterer Schritt).

## Verification

- `cd backend && uv run pytest` — alle Tests grün (bestehende + neue).
- `cd frontend && npm run build` — erfolgreich.
- Manuell im Browser: Play-Tab öffnen, Stärke wählen, Partie bis Matt/
  Aufgabe spielen, Tipps-Liste erscheint mit sinnvollen Klassifizierungen.
- Whole-Branch-Review + ggf. Fix-Wave wie beim letzten Feature, bevor
  gemerged/PR erstellt wird.
