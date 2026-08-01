# Chess Coach — LLM-Coaching-Text auf Basis der Stockfish-Analyse

## Context

Der Play-vs-Engine-Feature-Branch (PR #2, noch offen) liefert nach jeder
Partie eine reine Zug-Liste mit Blunder/Mistake/Inaccuracy/Good-Labels.
Das ist strukturell nützlich, fühlt sich aber nicht wie ein menschlicher
Schachcoach an. Ziel jetzt: einen zusätzlichen, von einem LLM generierten
Fließtext-Absatz oberhalb der Zug-Liste, der die Partie coach-artig
einordnet (z.B. "Du hast zweimal die Dame ungedeckt gelassen...").

Langfristiger Kontext vom Nutzer: die App soll irgendwann anderen Nutzern
zur Verfügung stehen, die damit ihr Schach verbessern sollen. Deshalb fiel
die Wahl bewusst auf eine gehostete API statt eines lokalen
Open-Source-Modells — skaliert ohne eigene GPU-Infra, und der Text wird
nur einmal pro Partie generiert (kein teurer Dauerbetrieb).

Statt einer direkten Anthropic-API-Anbindung nutzen wir **fal.ai**
([OpenRouter-Router](https://fal.ai/models/openrouter/router/openai/v1)):
Der Nutzer hat dort bereits Account, Guthaben und API-Key — kein neuer
Anthropic-Account nötig. Fal.ai stellt einen OpenAI-kompatiblen
`/chat/completions`-Endpunkt bereit, hinter dem sich 200+ Modelle
(Claude, Llama, Qwen, DeepSeek, ...) über denselben Code ansprechen
lassen — ein Modellwechsel später ist nur eine Konfigurationszeile, kein
Code-Umbau. Tradeoff: eine zusätzliche Zwischenschicht (Router) statt
direkt beim Anbieter, minimal höhere Latenz/Abhängigkeit.

Entscheidungen mit dem Nutzer geklärt:
1. Trigger: automatisch als Teil von `POST /play/games` (kein separater
   Button).
2. Format: Zusammenfassungs-Absatz **oberhalb** der bestehenden Zug-Liste,
   die Zug-Liste bleibt unverändert erhalten.
3. Modell: ein Claude-Haiku-Modell über fal.ai (Kosten/Skalierung), fest
   im Code, kein Auswahl-UI.
4. Architektur: direkt an fal.ais OpenAI-kompatiblen Endpunkt verdrahtet
   (via `openai`-Python-SDK mit fal als `base_url`), keine eigene
   Provider-Abstraktion nötig — die bekommt man durch fal/OpenRouter quasi
   geschenkt (Modell ist nur ein String-Parameter).
5. Sprache: Englisch (konsistent mit der bestehenden UI).
6. API-Key: Nutzer hat bereits einen fal.ai-Key (`FAL_KEY`) mit Guthaben —
   kommt direkt in `backend/.env`, keine weitere Anleitung nötig.

## fal.ai-Anbindung — technische Eckpunkte

- Endpunkt: `https://fal.run/openrouter/router/openai/v1` (OpenAI-SDK-
  kompatibel, `chat.completions.create(...)`).
- Auth: kein normaler `api_key`-Wert im OpenAI-Client (dort
  `"not-needed"` einsetzen), stattdessen custom Header
  `Authorization: Key <FAL_KEY>` über `default_headers` am Client.
- Modell-Parameter: `"anthropic/claude-haiku-4.5"` — verifizierter
  OpenRouter-Slug (bestätigt über openrouter.ai's Modellkatalog, den
  fal.ais Router 1:1 durchreicht). Implementierer übernimmt diesen Slug
  wörtlich, keine weitere Recherche nötig.
- Env-Var-Name: `FAL_KEY` (Standard-Konvention von fal.ai/`fal_client`),
  nicht `ANTHROPIC_API_KEY`.

## Weiterarbeit im bestehenden Branch

Dieser Schritt baut direkt auf dem noch offenen PR #2
(`worktree-play-vs-engine`) auf — selber Worktree, selbe PR, keine neue
Branch/kein neuer Worktree nötig, da es eine direkte Erweiterung der
gerade gebauten Tipps-Ansicht ist.

## Global Constraints

- Backend: `uv`, Python >= 3.12, `backend/`. Neue Abhängigkeit: `openai`
  (offizielles Python-SDK, hier gegen fal.ais OpenAI-kompatiblen Endpunkt
  verwendet — nicht gegen OpenAI selbst).
- `Settings.fal_api_key: str | None` (env `FAL_KEY`, optional — fehlt er,
  wird das Feature graceful übersprungen, siehe unten). Modellname als
  Konstante im Code: `"anthropic/claude-haiku-4.5"` (verifizierter
  OpenRouter-Slug, siehe oben), nicht env-konfigurierbar
  (Nutzerentscheidung: kein Auswahl-UI).
- **Graceful Degradation, kein Blocker**: Wenn der fal.ai-Call
  fehlschlägt (kein API-Key, Netzwerkfehler, Rate-Limit, o.ä.) oder
  `FAL_KEY` gar nicht gesetzt ist, darf `POST /play/games`
  **trotzdem erfolgreich** die Partie inkl. Zug-Analyse speichern —
  `coaching_summary` bleibt einfach `None`. Kein 500, kein Blockieren des
  bereits funktionierenden Analyse-Flows. Gleiches Prinzip wie beim
  bestehenden "Chess.com API unreachable → App bleibt nutzbar"-Edge-Case
  aus dem Design-Spec.
- **Schema-Änderung**: `Game` bekommt ein neues Feld
  `coaching_summary: str | None`. Muss über den bestehenden
  Migrations-Mechanismus in `backend/app/db.py` (der schon `source`/
  `analysis_json` per `ALTER TABLE ADD COLUMN` nachrüstet) erweitert
  werden — nicht am bestehenden Migrationscode vorbeibauen, sondern den
  gleichen Idempotenz-Check (`PRAGMA table_info`) um diese eine Spalte
  ergänzen.
- Der Coaching-Text wird **einmal generiert und persistiert**
  (`coaching_summary`-Spalte), nicht bei jedem Abruf neu erzeugt — sowohl
  `POST /play/games` als auch `GET /play/games/{id}/analysis` geben ihn
  zurück.
- Tests dürfen **keine echten fal.ai-API-Calls** machen (Kosten,
  Zuverlässigkeit, kein Key in CI nötig) — den `openai`-Client in Tests
  mocken (z.B. via `unittest.mock.patch` auf `chat.completions.create`),
  analog zum bestehenden Muster mit `respx` für den Chess.com-Client, nur
  eben für den SDK-Aufruf statt für rohes `httpx`.
- Jeder Task muss `uv run pytest` grün haben (Backend) bzw. `npm run
  build` fehlerfrei (Frontend), bevor er als DONE gilt.

## Task 1 — Backend: Coaching-Text generieren, speichern, ausliefern

- `backend/app/config.py`: `fal_api_key: str | None = None` zu
  `Settings` hinzufügen (env `FAL_KEY`). `backend/.env.example` um
  `FAL_KEY` ergänzen (als optional dokumentiert, Hinweis dass der Nutzer
  bereits einen Key besitzt).
- `backend/app/models.py`: `Game.coaching_summary: str | None = None`
  hinzufügen.
- `backend/app/db.py`: Migrations-Logik um `coaching_summary` erweitern
  (gleiches `ALTER TABLE ADD COLUMN`-Muster wie für `analysis_json`,
  nullable, kein Default nötig).
- `backend/app/coaching.py` (neu): Funktion
  `generate_coaching_summary(pgn: str, analysis: list[dict], result: str) -> str | None`.
  - Baut aus `analysis` (die bereits vorhandene `analyze_game`-Ausgabe:
    move_number, san, classification, eval_cp, best_move) einen
    strukturierten Prompt — z.B. eine kompakte Liste der
    Blunder/Mistake-Einträge mit Kontext, plus PGN und `result`.
  - System-Prompt etabliert die Rolle: ermutigender, direkter
    Schach-Coach, der 2-4 Sätze auf Englisch schreibt, die die
    wichtigsten wiederkehrenden Fehler dieser einen Partie benennt (kein
    Anspruch auf Muster über mehrere Partien — das ist weiterhin die
    spätere Weakness-Profile-Komponente).
  - Ruft `openai.OpenAI(base_url="https://fal.run/openrouter/router/openai/v1",
    api_key="not-needed", default_headers={"Authorization": f"Key {settings.fal_api_key}"})`
    auf, `chat.completions.create(model="anthropic/claude-haiku-4.5",
    messages=[...], max_tokens=300)`.
  - Bei fehlendem `Settings.fal_api_key` oder jeglichem Fehler beim
    API-Call: `None` zurückgeben (loggen, nicht raisen).
- `backend/app/routers/play.py`:
  - `POST /play/games`: nach `analyze_game` zusätzlich
    `generate_coaching_summary` aufrufen, Ergebnis in
    `Game.coaching_summary` speichern, Response um
    `"coaching_summary": str | None` erweitern.
  - `GET /play/games/{game_id}/analysis`: Response ebenfalls um
    `"coaching_summary"` erweitern.
- Tests (`openai`-Client gemockt, keine echten API-Calls):
  - Erfolgsfall: gemockter Client liefert Text zurück →
    `coaching_summary` korrekt gespeichert und in beiden Endpunkten
    zurückgegeben.
  - Fehlerfall (gemockter Client wirft eine Exception): `POST
    /play/games` liefert trotzdem 200 mit vollständiger `analysis`, nur
    `coaching_summary: null` — Partie ist in der DB gespeichert.
  - Fehlender API-Key (`Settings.fal_api_key = None`):
    `generate_coaching_summary` gibt direkt `None` zurück, ohne einen
    Client-Call zu versuchen.
  - Migrations-Test: bestehendes Muster erweitern, sodass eine
    vor-Branch-Schema-Datei jetzt auch ohne `coaching_summary`-Spalte
    korrekt migriert wird.

## Task 2 — Frontend: Coaching-Text in der Tipps-Ansicht anzeigen

Depends on Task 1 (API-Form ist dann eingefroren).

- `frontend/src/api.ts`: `AnalysisEntry`/`SavedGame`-Typ (bzw. wie auch
  immer die `POST /play/games`-Response aktuell typisiert ist) um
  `coaching_summary: string | null` erweitern.
- `frontend/src/GameTips.tsx`: neuer Absatz **oberhalb** der bestehenden
  Zug-Liste, der `coaching_summary` anzeigt, wenn vorhanden. Ist er
  `null` (API-Key fehlt oder Fehler), wird der Absatz einfach nicht
  gerendert — kein Fehlerzustand, kein "Feature kaputt"-Eindruck, die
  Zug-Liste bleibt wie gehabt vollständig nutzbar (graceful degradation
  auch im Frontend). Styling: bestehende `.card`/Typografie-Tokens aus
  `App.css` wiederverwenden (Serifen-Absatz, dezenter Rahmen), kein neues
  visuelles System.
- `npm run build` fehlerfrei; manuelle Prüfung im Browser (Partie zu Ende
  spielen, Coaching-Absatz erscheint oberhalb der Zug-Liste).

## Nicht Teil dieses Schritts

- Kein Muster-Erkennen über mehrere Partien (Weakness Profile) — der
  Coaching-Text bezieht sich nur auf die gerade gespielte Partie.
- Keine Provider-Abstraktion für andere LLM-Anbieter.
- Kein manuelles Re-Generieren/Nachbessern des Coaching-Texts durch den
  Nutzer.
- Keine Analyse/Coaching-Texte für importierte Chess.com-Partien — nur
  für gespielte Partien, wie schon bei der bestehenden Zug-Analyse.

## Verification

- `cd backend && uv run pytest` — alle Tests grün (bestehende + neue),
  keine echten Netzwerk-Calls zu fal.ai.
- `cd frontend && npm run build` — erfolgreich.
- Manuell: `FAL_KEY` in `backend/.env` setzen, Partie im
  Browser zu Ende spielen, Coaching-Absatz erscheint oberhalb der
  Zug-Liste mit sinnvollem, ermutigendem Text.
- Manuell (Fehlerfall): `FAL_KEY` entfernen/ungültig setzen,
  Partie spielen — Zug-Liste erscheint weiterhin normal, kein Fehler,
  einfach kein Coaching-Absatz.
- Whole-Branch-Review wie bei den vorherigen Features, bevor die
  Änderungen zum offenen PR #2 gepusht werden.
