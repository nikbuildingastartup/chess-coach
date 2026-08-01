# Chess Coach — Weakness Profile, Daily Focus & Practice Module

## Context

Der Coaching-Text nach einer Partie (siehe Screenshot vom Nutzer) ist
inhaltlich gut, aber (a) reiner unformatierter Fließtext, (b) bezieht sich
nur auf genau eine Partie, und (c) endet ohne konkrete nächste Aktion. Der
Nutzer will jetzt genau die drei noch fehlenden Komponenten aus dem
Original-Design-Spec bauen:

- **Weakness Profile** — Muster über mehrere Partien hinweg erkennen
  (nicht nur eine Partie isoliert betrachten).
- **Focus Generator** — daraus eine tages-aktuelle, strukturierte
  Empfehlung ableiten (Kernproblem + Erklärung + konkrete Handlungsanweisung
  statt Fließtext-Absatz).
- **Practice Module** — eine interaktive Übung direkt aus den eigenen
  Blunder-Stellungen, mit sofortigem Richtig/Falsch-Feedback.

Entscheidungen mit dem Nutzer geklärt (Fragerunden 1 + 2):
1. **Datenbasis**: nicht nur in-App gespielte Partien, sondern auch die
   letzten importierten Chess.com-Partien sollen nachträglich per Stockfish
   analysiert werden (aktuell haben nur `source="played"`-Partien
   `analysis_json` — die 3000+ importierten Chess.com-Partien haben noch
   keine Analyse).
2. **Format**: strukturierter LLM-Output (Kernproblem/Erklärung/Empfehlung
   als separate Felder) statt Fließtext — gilt sowohl für den neuen
   Cross-Game-Fokuspunkt als auch für den bestehenden Pro-Partie-Coaching-Text.
3. **Umfang**: direkt inklusive interaktivem Übungs-Board (nicht nur bis
   zur Text-Empfehlung).
4. **Caching**: einmal täglich berechnet und gecacht (spec-konform).
5. **Backfill-Menge**: die letzten 10 Partien (Quelle egal) werden bei
   Bedarf nachanalysiert.
6. **Trigger**: automatisch im Hintergrund beim Laden des neuen
   Practice-Tabs, mit Ladezustand in der UI.
7. **Übungs-Check**: ein Zug gilt als richtig, wenn er Stockfishs
   Top-Empfehlung entspricht ODER eval-mäßig innerhalb ~30 Centipawn liegt.
8. **Navigation**: neuer echter "Practice"-Tab (ersetzt den bisherigen
   deaktivierten "Daily focus"-Platzhalter-Pill), zeigt oben den
   Tages-Fokus, darunter mehrere Übungspositionen zum Durchklicken.

## Ein wichtiger Architektur-Fund während der Planung

Um bei einer importierten Chess.com-Partie zu wissen, welche Züge überhaupt
vom Nutzer stammen (und nicht vom Gegner), muss gespeichert sein, mit
welcher Farbe der Nutzer gespielt hat. Das gibt es aktuell nicht:
`Game` hat kein `user_color`-Feld, und der Chess.com-Username wird nirgends
persistiert (nur flüchtig im Frontend-State). Lösung: ein neues
`Game.user_color`-Feld plus ein Singleton-Settings-Eintrag
(`chesscom_username`), der bei jedem `POST /games/sync` aktualisiert wird.
Beim Sync-Lauf wird `user_color` sowohl für neu importierte als auch —
das ist der Clou — für bereits vorhandene Partien nachgetragen (da die
Chess.com-API bei jedem Sync ohnehin wieder White/Black-Info pro Partie
liefert). Der Nutzer muss also nur einmal erneut "Sync" klicken, damit alle
3000+ bestehenden Partien rückwirkend `user_color` bekommen — kein
separates Migrations-Skript nötig.

## Global Constraints

- Backend: `uv`, Python >= 3.12, `backend/`. Frontend: `frontend/`,
  bestehende React+Vite+TS-Struktur.
- Alle neuen Konstanten als benannte Konstanten im Code, keine Magic
  Numbers: `BACKFILL_LIMIT = 10`, `MIN_GAMES_FOR_PATTERN = 3`,
  `PRACTICE_POSITIONS_MAX = 5`, `PRACTICE_CORRECT_TOLERANCE_CP = 30`.
- Schema-Änderungen laufen über den bestehenden Migrations-Mechanismus in
  `backend/app/db.py` (PRAGMA table_info + ALTER TABLE ADD COLUMN /
  CREATE TABLE Muster) — die reale DB mit 3000+ Partien darf nie brechen.
- Graceful Degradation bleibt Leitprinzip (etabliert in der
  Coaching-Summary-Feature): fehlender `FAL_KEY` oder ein LLM-Fehler darf
  nie einen 500er auslösen. Statt eines LLM-generierten Fokuspunkts wird
  in diesem Fall ein einfacher, statistik-basierter Fallback-Text gezeigt
  (z.B. "Du hast zuletzt am häufigsten Materialverluste in der
  Mittelspielphase — X von Y geflaggten Zügen."), niemals ein Fehlerzustand.
- Edge Case aus dem Design-Spec: bei weniger als `MIN_GAMES_FOR_PATTERN`
  analysierten Partien liefert `GET /focus/today` einen
  `status: "insufficient_data"` mit einer generischen, freundlichen
  Nachricht statt einer spekulativen LLM-Analyse dünner Datenbasis.
- Der strukturierte LLM-Output (Headline/Explanation/Recommendation) wird
  per Prompt angefordert (kein garantiertes `response_format=json_object`,
  da unklar ob fal.ai/OpenRouter das für Claude-Modelle zuverlässig
  unterstützt) und beim Parsen tolerant behandelt: bei ungültigem JSON
  wird der rohe Text als `explanation` mit `headline=null` verwendet —
  nie eine Exception nach außen werfen.
- Tests dürfen weiterhin **keine echten fal.ai-Calls** machen (bestehendes
  Mock-Pattern aus `test_coaching.py`/`test_play.py` fortführen).
- Jeder Task: `uv run pytest` grün (Backend) / `npm run build` fehlerfrei
  (Frontend), bevor er als DONE gilt.
- Kein neuer Worktree — dies ist ein neues Feature, dafr wird ein neuer
  Branch/Worktree analog zu den bisherigen Features aufgesetzt (z.B.
  `weakness-profile`), da PR #2 (Play-vs-Engine + Coaching-Summary) bereits
  fertig und mergebereit ist.

## Task 1 — Backend: Schema, Phase-Tagging, user_color-Tracking

- `backend/app/models.py`:
  - `Game.user_color: str | None = None` ("white"/"black") hinzufügen.
  - Neue Tabelle `AppSettings` (Singleton, `id: int = Field(default=1, primary_key=True)`,
    `chesscom_username: str | None = None`) für den zuletzt synchronisierten
    Chess.com-Usernamen.
  - Neue Tabelle `DailyFocus`: `id`, `date: str` (UTC ISO-Datum, unique
    indexed), `status: str` ("computing"/"ready"/"insufficient_data"/"error"),
    `headline: str | None`, `explanation: str | None`,
    `recommendation: str | None`, `source_game_ids_json: str | None`,
    `practice_positions_json: str | None`, `created_at: datetime`.
- `backend/app/db.py`: Migrations-Logik erweitern — `user_color` per
  bestehendem ALTER-TABLE-Muster; `AppSettings`/`DailyFocus` sind neue
  Tabellen, die über `SQLModel.metadata.create_all` bereits automatisch
  angelegt werden sollten (prüfen, dass das bestehende
  `create_db_and_tables()` neue Tabellen ohne Sonderbehandlung erzeugt —
  nur bestehende Tabellen brauchen das ALTER-TABLE-Muster).
- `backend/app/chess_engine.py`: `analyze_game`-Output um `"phase"` ergänzen
  (`"opening" | "middlegame" | "endgame"`), abgeleitet aus `move_number`
  über benannte Schwellwert-Konstanten (z.B. `OPENING_MOVE_LIMIT = 10`,
  `MIDDLEGAME_MOVE_LIMIT = 30`). Neuer Helper
  `fen_before_move(pgn: str, move_number: int, side: str) -> str` —
  spielt die PGN mit `python-chess` bis kurz vor dem angegebenen Halbzug
  nach und gibt die FEN-Stellung davor zurück (wird von Task 3 für die
  Übungspositionen gebraucht).
- `backend/app/routers/games.py` (`sync_games`): nach erfolgreichem Sync
  `AppSettings.chesscom_username` upserten. Im bestehenden
  `if existing is not None: continue`-Zweig VOR dem `continue` prüfen, ob
  `existing.user_color is None`, und falls ja aus den bereits geladenen
  `raw_game`-Daten (White/Black-Username-Vergleich, analog zu
  `_derive_result`) nachtragen + zur Session hinzufügen. Für neu
  importierte Partien `user_color` direkt beim Anlegen setzen.
- `backend/app/routers/play.py` (`save_played_game`): `user_color="white"`
  fest setzen (Human spielt im Play-Modul immer Weiß).
- Tests: `phase`-Klassifizierung für Beispielzüge, `fen_before_move`
  gegen eine bekannte PGN, Sync-Test der bestehende Partien rückwirkend
  mit `user_color` versieht, Migrationstest für die drei Schema-Änderungen
  gegen eine Pre-Branch-DB-Fixture (bestehendes Testmuster fortführen).

## Task 2 — Backend: Batch-Nachanalyse kürzlich importierter Partien

Depends on Task 1.

- Neues Modul `backend/app/analysis_backfill.py`:
  `backfill_recent_games(session, limit=BACKFILL_LIMIT) -> list[Game]` —
  wählt die `limit` neuesten Partien über alle `source`-Werte hinweg nach
  `end_time` aufsteigend absteigend, filtert auf `analyzed == False`, läuft
  jede durch `analyze_game`, speichert `analysis_json`/`analyzed=True`,
  committet nach jeder Partie einzeln (analog zum bestehenden
  Commit-pro-Monat-Muster in `sync_games`, damit ein Absturz nicht die
  bereits analysierten Partien verliert). Partien mit `source="chesscom"`
  und `user_color is None` werden übersprungen und geloggt (Nutzer hat seit
  Einführung dieses Features noch nicht neu gesynced) — nicht als Fehler
  behandeln.
- Gibt die tatsächlich analysierten Partien zurück (für Task 3s
  Weiterverarbeitung).
- Tests: Partien-Auswahl-Logik (nur unanalysierte, nur die neuesten N,
  Sortierung), Übersprung-Verhalten bei fehlendem `user_color`,
  Persistenz nach jeder einzelnen Partie (Teil-Fortschritt-Test analog zu
  bestehenden Sync-Tests).

## Task 3 — Backend: Weakness Profile, strukturierter Daily Focus, `/focus/today`

Depends on Task 1, Task 2.

- Neues Modul `backend/app/weakness_profile.py`:
  `aggregate_weakness_data(games: list[Game]) -> dict` — rein
  deterministisch (keine KI): sammelt aus jeder Partie die
  `blunder`/`mistake`-Einträge, gefiltert auf `entry["side"] == game.user_color`,
  gruppiert nach `phase`, zählt Häufigkeiten, ermittelt das
  häufigste Phase+Classification-Muster. Gibt eine strukturierte
  Zusammenfassung zurück (Counts, Beispiel-Züge, betroffene Partie-IDs).
- `backend/app/coaching.py` (oder neues `focus.py`, Implementierer
  entscheidet): `generate_daily_focus(aggregated_data) -> dict` — baut aus
  den aggregierten Daten einen Prompt, ruft fal.ai/Haiku auf, fordert
  striktes JSON mit den Feldern `headline`, `explanation`, `recommendation`
  an, parst tolerant (siehe Global Constraints). Bei fehlendem `FAL_KEY`
  oder Parse-/API-Fehler: deterministischer Fallback-Text direkt aus
  `aggregated_data` (kein LLM nötig für den Fallback).
- Extraktion der Übungspositionen: aus den geflaggten Zügen des
  häufigsten Musters (Blunder vor Mistake priorisiert, neueste Partien
  zuerst), maximal `PRACTICE_POSITIONS_MAX`, über `fen_before_move` je
  eine FEN-Stellung + der tatsächlich gespielte Zug + Stockfishs
  `best_move` ermitteln.
- `backend/app/routers/focus.py` (neu):
  - `GET /focus/today`: heutiges UTC-Datum ermitteln. Existiert kein
    `DailyFocus`-Eintrag für heute: neuen Eintrag mit `status="computing"`
    anlegen, `BackgroundTasks` auslösen (backfill → aggregate → generate
    → practice positions → Eintrag auf `status="ready"` oder
    `"insufficient_data"`/`"error"` aktualisieren), sofort
    `{status: "computing"}` zurückgeben. Existiert bereits ein Eintrag für
    heute (egal welcher Status): diesen direkt zurückgeben (Frontend
    pollt, falls `status == "computing"`).
  - Response-Model liefert alle `DailyFocus`-Felder plus geparste
    `practice_positions` (Liste von `{fen, played_move, best_move,
    classification}`).
- Tests (LLM gemockt, kein echter fal.ai-Call): erfolgreiche Generierung,
  Fallback bei fehlendem Key, Fallback bei Parse-Fehler,
  `insufficient_data`-Pfad bei < `MIN_GAMES_FOR_PATTERN` analysierten
  Partien, Idempotenz (zweiter `GET`-Call am selben Tag liefert
  denselben Eintrag ohne erneute Berechnung).

## Task 4 — Backend: Strukturierter Pro-Partie-Coaching-Text

Depends on Task 1. Unabhängig von Task 2/3, kann parallel im Kopf
mitgeplant, aber sequenziell nach Task 3 umgesetzt werden.

- `backend/app/coaching.py`: `generate_coaching_summary` auf dieselbe
  strukturierte JSON-Form umstellen wie Task 3s Daily Focus
  (`headline`/`explanation`/`recommendation`), gleiches
  Prompt-anfordern-und-tolerant-parsen-Muster, gleiche
  Graceful-Degradation (fehlender Key/Fehler → `None`, wie bisher).
  `Game.coaching_summary`-Spalte bleibt technisch ein `str`-Feld, speichert
  aber ab jetzt JSON-serialisierte Struktur — keine Schema-Änderung
  nötig, nur geänderter Inhalt.
- `backend/app/routers/play.py`: `SaveGameResponse`/`GameAnalysisResponse`
  von `coaching_summary: str | None` auf ein strukturiertes
  `coaching: {headline, explanation, recommendation} | None` umstellen
  (bewusster Breaking Change der gerade erst gemergten API-Form —
  akzeptabel, da Single-User-Phase ohne externe Konsumenten).
- Bestehende Tests in `test_coaching.py`/`test_play.py` auf die neue Form
  anpassen (nicht neu erfinden, nur Assertions aktualisieren).

## Task 5 — Backend: Übungs-Zug-Check-Endpoint

Depends on Task 1 (chess_engine-Helper).

- `backend/app/routers/practice.py` (neu): `POST /practice/check-move`,
  Body `{fen: str, move_uci: str}` (UCI-Notation, z.B. `"e2e4"`, mit
  optionaler Promotion `"e7e8q"` — robuster als SAN für Drag&Drop-Input).
  Validiert den Zug als legal in der gegebenen Stellung (400 bei
  illegalem Zug), läuft eine kurze Stockfish-Analyse (wiederverwendet die
  Bewertungslogik aus `chess_engine.py`, ggf. als kleiner gemeinsamer
  Helper extrahiert statt dupliziert) um Stockfishs besten Zug UND den
  Eval-Abfall des gespielten Zugs zu ermitteln. `correct = (played_move ==
  best_move) or (drop_cp <= PRACTICE_CORRECT_TOLERANCE_CP)`. Response:
  `{correct: bool, best_move: str, played_eval_cp: int}`.
- Tests: korrekter Zug (Match), korrekter Zug (Toleranz-Fall, nicht exakt
  aber nah dran), falscher Zug, illegaler Zug → 400.

## Task 6 — Frontend: Practice-Tab, Daily Focus, strukturierte Anzeige

Depends on Task 3, Task 4, Task 5 (API-Formen müssen eingefroren sein).

- `frontend/src/api.ts`: Typen für `DailyFocus`/`PracticePosition`,
  `getDailyFocus()` (`GET /focus/today`), `checkPracticeMove(fen, moveUci)`
  (`POST /practice/check-move`). `SavedGame`/Analysis-Typen von
  `coaching_summary: string | null` auf strukturiertes
  `coaching: {headline, explanation, recommendation} | null` umstellen.
- `frontend/src/GameTips.tsx`: bestehenden Fließtext-Absatz durch
  strukturierte Darstellung ersetzen (Überschrift + Erklärungs-Absatz +
  hervorgehobene Empfehlung), bestehende `.card`/Typografie-Tokens
  wiederverwenden, `null` weiterhin graceful (kein Absatz).
- Neue `frontend/src/PracticePanel.tsx`: lädt beim Mount `getDailyFocus()`,
  zeigt bei `status === "computing"` einen Ladezustand (Polling alle
  wenigen Sekunden bis `ready`/`insufficient_data`/`error`), bei `ready`
  oben eine Daily-Focus-Karte (Headline/Explanation/Recommendation) und
  darunter ein interaktives Brett (chess.js + react-chessboard,
  wiederverwendetes Muster aus `PlayPanel.tsx`) geladen mit der ersten
  `practice_position`-FEN. Nutzer zieht → `checkPracticeMove` →
  Richtig/Falsch-Feedback + Stockfishs bester Zug anzeigen → "Nächste
  Position"-Button zyklisch durch `practice_positions`. Bei
  `insufficient_data`: freundlicher Hinweistext statt Board.
- `frontend/src/App.tsx`: `TopNav` — den deaktivierten "Daily focus"-Pill
  durch einen echten "Practice"-Tab ersetzen (analog zum bestehenden
  "Play"-Tab-Muster), rendert `PracticePanel`. Den deaktivierten "See
  today's focus"-Button auf dem Sync-Screen entfernen oder auf den neuen
  Tab verlinken (Implementierer-Entscheidung, sinnvollste UX wählen).
- `npm run build` fehlerfrei; manuelle Prüfung im Browser laut
  Verification unten.

## Nicht Teil dieses Schritts

- Keine dauerhafte Übungs-Historie/Statistik über gelöste Positionen
  hinweg (nur In-Session-Feedback, kein Persistenz-Tracking von
  gelöst/nicht-gelöst über Tage hinweg).
- Kein automatisches Neu-Sync von Chess.com als Teil dieses Features —
  der Nutzer muss einmal manuell "Sync" klicken, damit bestehende Partien
  `user_color` bekommen.
- Keine Eröffnungs-Erkennung/Opening-Book-Abgleich für die
  Phasen-Klassifizierung — rein zugnummer-basierte Heuristik.
- Kein Redesign des Sync-/Play-Screens über die in Task 6 beschriebenen
  Navigationsänderungen hinaus.

## Verification

- `cd backend && uv run pytest` — alle Tests grün, keine echten
  Netzwerk-Calls zu fal.ai.
- `cd frontend && npm run build` — erfolgreich.
- Manuell: einmal "Sync" klicken (bestehende Partien bekommen
  `user_color`), dann Tab "Practice" öffnen — Ladezustand erscheint,
  nach Abschluss der Hintergrund-Analyse erscheint eine strukturierte
  Tages-Empfehlung plus mindestens eine Übungsposition; ein Zug auf dem
  Übungsbrett liefert sofortiges Richtig/Falsch-Feedback.
- Manuell (Fehlerfall): `FAL_KEY` entfernen — Daily Focus zeigt weiterhin
  einen statistik-basierten Fallback-Text statt eines Fehlers oder leeren
  Zustands.
- Manuell (Cold-Start-Fall): auf einer frischen DB mit < 3 analysierten
  Partien — `insufficient_data`-Zustand mit freundlichem Hinweistext statt
  Absturz oder Blindflug-Empfehlung.
- Whole-Branch-Review (Subagent-Driven Development, wie bei den
  vorherigen Features) vor Merge/PR.
