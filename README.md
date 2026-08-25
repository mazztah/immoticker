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
`FILIP_LINKEDIN_URL`, `FILIP_XING_URL`, `SERPAPI_KEY` (optional, s.u.) → **"Bereitstellen"**.

Die Befehle ohne Secrets stehen auch fertig zum Copy-Paste in `DEPLOY_COMMANDS.txt` (nicht committed).

**Sicherere Alternative** (empfohlen für Produktion): im selben Tab "Variablen & Secrets" bei
"Secrets" auf "Referenz hinzufügen" gehen und einen zuvor im Secret Manager angelegten Secret
verknüpfen, statt eine normale Umgebungsvariable zu verwenden.

## Standortanalyse & Live-Websuche (`SERPAPI_KEY`)

Der Button **"Analyse"** in der Top-50-Städte-Sidebar generiert einen Standortbericht (10-Punkte-
Kennzahlenkatalog). Für **echte, tagesaktuelle Zahlen** (Grundstückspreise, Gewerbemieten, Strom-
netzkapazität, Arbeitslosenquote, Förderprogramme etc.) führt das Backend vor der Berichterstellung
mehrere Google-Suchen über [SerpApi](https://serpapi.com) aus (`_run_standort_web_research` in `app.py`).

- Umgebungsvariable `SERPAPI_KEY` setzen (Fly: `fly secrets set SERPAPI_KEY=dein_key`,
  Cloud Run: siehe oben) — ein SerpApi-Konto/API-Key ist nötig (kostenpflichtig ab einem gewissen
  Freikontingent, siehe serpapi.com/pricing).
- **Ohne** `SERPAPI_KEY` funktioniert die Analyse weiterhin, dann aber ohne Live-Daten — das LLM
  kennzeichnet unsichere Zahlen dann explizit als Einschätzung statt sie zu erfinden.
- Bewusst **kein** `groq/compound` (Groqs Modell mit eingebautem Browser-Use-Tool) für diese Recherche
  verwendet — dessen eingebaute Websuche war in der Praxis nicht zuverlässig nutzbar. Stattdessen
  läuft dieselbe SerpApi-Anbindung wie im Schwesterprojekt (Telegram-Bot), die dort bereits produktiv
  funktioniert.



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

## GENESIS-Destatis (amtliche Statistik, `GENESIS_API_KEY`)

Die Sektion **"Statistik"** bindet die [GENESIS-RESTful/JSON-API](https://genesis.destatis.de/genesisWS/swagger-ui/index.html)
des Statistischen Bundesamts an (`genesis_client.py`) — offizielle, kostenlose Zeitreihen wie
Baupreisindex, Bevölkerungsstand, Häuserpreisindex und Baugenehmigungen, direkt als Diagramm
(von GENESIS serverseitig gerendert) und Datentabelle im Frontend.

- Umgebungsvariable `GENESIS_API_KEY` setzen (Fly: `fly secrets set GENESIS_API_KEY=dein_token`).
  Der Wert ist der **persönliche API-Token** aus dem GENESIS-Weboberflächen-Modal
  "Webservice-Schnittstelle (API)" — er wird intern als `username` mit leerem `password` verwendet
  (siehe GENESIS-Anwenderdokumentation "Webservice/API", Kap. 2.1.3).
- **Ohne** `GENESIS_API_KEY` bleibt die Sektion sichtbar, zeigt aber einen Hinweis
  ("nicht konfiguriert") statt Daten — kein harter Fehler für den Rest der App.
- Kuratierte Standard-Tabellen (Baupreise, Bevölkerung) sind mit fest geprüften Tabellencodes
  hinterlegt (`genesis_client.CURATED_TABLES`). Für weitere Themen (Häuserpreisindex,
  Baugenehmigungen, Verbraucherpreise/Mieten) lädt das Frontend die verfügbaren Tabellen live
  über `catalogue/tables2statistic`, da sich die exakten Tabellencodes dort gelegentlich ändern.
- Rate-Limits: GENESIS begrenzt parallel laufende Requests (siehe Kap. 1.7 der Anwenderdoku) —
  bei sehr hoher Last kann `helloworld/logincheck` hängende Requests serverseitig beenden.

## Endpunkte

- `GET /` — Frontend
- `GET /health` — Health-Check (für Cloud Run)
- `GET /api/feeds?category=KI%20USA` — Live-Artikel einer Kategorie (server-seitig gefetcht)
- `GET /api/feeds/meta` — komplette Feed-Datenbank (Name, URL, Kategorie, Beschreibung)
- `POST /api/chat` — KI-Chat, Body: `{"message": "...", "session_id": "...", "articles": [...]}`
- `POST /api/linkedin` — LinkedIn-Artikel-Generator, Body: `{"articles": [...]}`
- `GET /api/genesis/status` — prüft, ob `GENESIS_API_KEY` gesetzt & gültig ist
- `GET /api/genesis/tabellen` — kuratierte Standard-Tabellen + erkundbare Statistiken
- `GET /api/genesis/statistik/{code}/tabellen` — live: Tabellen zu einer Statistik-Nummer
- `GET /api/genesis/suche?begriff=...` — Volltextsuche über GENESIS-Tabellen/Statistiken
- `GET /api/genesis/tabelle/{code}` — Datenzeilen einer Tabelle (JSON, geparst aus ffcsv)
- `GET /api/genesis/chart/{code}` — von GENESIS gerendertes Liniendiagramm (PNG)
