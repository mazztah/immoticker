# KI-Immo-Terminal

Responsive News-Ticker-Hub für KI- und Immobilien-News (USA & Deutschland) plus Top-Wirtschaftsmagazine,
mit KI-Chat und LinkedIn-Artikel-Generator. Backend liest 344 validierte RSS-Feeds **server-seitig**
(kein CORS-Proxy nötig — dadurch deutlich zuverlässiger als ein reiner Browser-Ansatz).

## Architektur

- **`app.py`** — einzelne Python-Datei (FastAPI): Feed-Datenbank, RSS-Fetch-Engine, `/api/chat`,
  `/api/linkedin`, liefert das Frontend aus.
- **`static/index.html`** — Frontend (Vanilla JS + Tailwind CDN), ruft ausschließlich die eigenen
  Backend-Endpunkte auf. Kein API-Key im Browser nötig.
- **`Dockerfile`** — Cloud-Run-kompatibel (liest `$PORT`).
- **`.env.example`** — Vorlage für Umgebungsvariablen (wird committed).
- **`.env`** — deine echten Keys, **lokal**, in `.gitignore` — wird nie committed.
- **`DEPLOY_COMMANDS.txt`** — fertige Copy-Paste-Befehle mit deinen echten Keys, ebenfalls
  in `.gitignore` — für dich bequem, landet aber nie im Git-Verlauf.

## ⚠️ Wichtiger Sicherheitshinweis

Die Keys, die du mir gegeben hast, wurden **nicht** in `app.py`, `Dockerfile` oder sonst einer
Datei hardcodiert, die committed wird. Ein GitHub-Repo mit Klartext-Keys wird von GitHub
Secret-Scanning erkannt (Groq-Keys werden dann i.d.R. automatisch widerrufen) und ist ein
generelles Leak-Risiko. Stattdessen:

- Lokal: `.env` (gitignored) wird automatisch geladen (`python-dotenv`).
- Cloud Run: Keys werden beim Deploy als Umgebungsvariablen/Secrets injiziert (siehe unten),
  landen nie im Container-Image oder Git-Repo.

**Bitte trotzdem rotieren/widerrufen**, da die Keys einmal im Klartext in unserem Chat standen:
- Groq: https://console.groq.com/keys
- xAI: https://console.x.ai
- Anthropic: https://console.anthropic.com/settings/keys

Zwei Auffälligkeiten, die noch zu klären sind:
- `XAI_API_KEY` und `GROQ_API_KEY` haben bei dir denselben Wert im Groq-Format (`gsk_...`).
  xAI-Keys beginnen normalerweise mit `xai-...` — vermutlich versehentlich doppelt eingefügt.
- `ANTHROPIC_API_KEY` hat nicht das Anthropic-Format (`sk-ant-...`). Aktuell nutzt der Code
  diesen Key noch nicht aktiv (nur Groq ist verdrahtet) — kann aber leicht ergänzt werden,
  sobald ein echter Anthropic-Key vorliegt.

## Lokal starten

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Dann `http://localhost:8080` öffnen. Die `.env` wird automatisch geladen.

## Bei GitHub hochladen

```bash
git init
git add .
git commit -m "KI-Immo-Terminal: Backend, 344 validierte Feeds, KI-Chat, LinkedIn-Generator"
git branch -M main
git remote add origin https://github.com/<DEIN-USERNAME>/<DEIN-REPO>.git
git push -u origin main
```

`.env` und `DEPLOY_COMMANDS.txt` werden durch `.gitignore` automatisch ausgeschlossen.

## Bei Google Cloud Run deployen

```bash
gcloud auth login
gcloud config set project <DEIN-GCP-PROJEKT-ID>

gcloud run deploy ki-immo-terminal \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated
```

Danach trägst du die Keys **direkt in der Cloud Run Konsole** ein (nicht über die Kommandozeile):
[console.cloud.google.com/run](https://console.cloud.google.com/run) → Service `ki-immo-terminal`
öffnen → **"Bearbeiten und neue Version bereitstellen"** → Tab **"Variablen & Secrets"** →
Umgebungsvariablen hinzufügen: `GROQ_API_KEY`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`,
`FILIP_LINKEDIN_URL`, `FILIP_XING_URL` → **"Bereitstellen"**.

Die Befehle ohne Secrets stehen auch fertig zum Copy-Paste in `DEPLOY_COMMANDS.txt` (nicht committed).

**Sicherere Alternative** (empfohlen für Produktion): im selben Tab "Variablen & Secrets" bei
"Secrets" auf "Referenz hinzufügen" gehen und einen zuvor im Secret Manager angelegten Secret
verknüpfen, statt eine normale Umgebungsvariable zu verwenden.

## Feed-Datenbank

344 validierte Quellen in 7 Kategorien (`/api/feeds/meta` gibt die volle Liste zurück):

| Kategorie | Anzahl |
|---|---|
| KI USA | 65 |
| KI Deutschland | 11 |
| Immobilien USA | 56 |
| Immobilien Deutschland | 7 |
| Top-Magazine (Forbes, The Economist, HBR, ...) | 15 |

Bewusst **nicht** dabei: Firmen/Investoren ohne eigenen öffentlichen RSS-Feed (betrifft die
meisten Immobilien-Private-Equity-Häuser). Deutsche Immobilien-Feeds sind dünn gesät, weil die
meisten großen Player (Vonovia, Blackstone-Töchter etc.) keinen RSS-Feed anbieten.

Warum server-seitiges Fetching: Die Vorversion nutzte Browser-CORS-Proxies, die bei ~40
gleichzeitigen Requests schnell in Rate-Limits liefen (daher die vielen "nicht erreichbar"-
Meldungen). Jetzt holt das Backend die Feeds direkt (kein Proxy, keine Browser-CORS-Limits) —
das behebt die Mehrzahl der bisherigen Ausfälle. Ein paar einzelne Feeds können dennoch
zeitweise ausfallen (z.B. wenn eine Quelle selbst offline ist oder Bot-Traffic blockt) —
das Frontend zeigt das transparent an ("X/Y Quellen live").

## Endpunkte

- `GET /` — Frontend
- `GET /health` — Health-Check (für Cloud Run)
- `GET /api/feeds?category=KI%20USA` — Live-Artikel einer Kategorie (server-seitig gefetcht)
- `GET /api/feeds/meta` — komplette Feed-Datenbank (Name, URL, Kategorie, Beschreibung)
- `POST /api/chat` — KI-Chat, Body: `{"message": "...", "session_id": "...", "articles": [...]}`
- `POST /api/linkedin` — LinkedIn-Artikel-Generator, Body: `{"articles": [...]}`
