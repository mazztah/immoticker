# KI-Immo-Terminal (Optimiert – Juli 2026)

Vollständig überarbeitete Version mit folgenden Verbesserungen:

## Neue Optimierungen (diese Version)

### 1. LinkedIn-Artikel-Generator stark verbessert
- **Schöne Formatierung**: Viele Zwischenüberschriften mit Emojis, kurze Absätze, Bullet-Listen
- **Passende Zitate** werden automatisch eingebaut
- **Prominenter Abschnitt ganz unten** mit:
  - LinkedIn Profil
  - Xing Profil  
  - Landingpage + Pilotgespräch
- Mindestens **22 Hashtags** + **22 Keywords**
- `max_tokens=2500` für längere, hochwertige Artikel
- Streaming-Vorbereitung (`/api/linkedin/stream`)

### 2. Ticker deutlich langsamer
- Animation-Dauer von ~20s auf **45 Sekunden** erhöht → viel angenehmer zu lesen

### 3. Weitere Verbesserungen
- Stärkerer persönlicher roter Faden (Hybrid-Experte)
- Bessere Struktur & Lesbarkeit auf LinkedIn

## Original-Features (unverändert)
- 154 validierte RSS-Feeds (server-seitig)
- KI-Chat mit Groq (Fallback-Modelle)
- 5 Kategorien: KI USA, KI DE, Immobilien USA, Immobilien DE, Top Magazine

## Starten

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

Öffne http://localhost:8080

## Deployment

Siehe `DEPLOY_COMMANDS.txt`

**Wichtig**: Die Keys (GROQ_API_KEY etc.) kommen ausschließlich über Environment Variables / Secret Manager – nie im Code oder Git.

## Autor / Kontakt

Filip Makarczyk – Hybrid-Experte für KI-gestütztes Property Management

- Landingpage: https://jhjjhdkulandhfdfdpagefjdsh-307619780865.europe-west3.run.app/
- Xing: https://www.xing.com/profile/Filip_Makarczyk
- LinkedIn: https://www.linkedin.com/in/filip-makarczyk

---

Erstellt mit ❤️ und vielen Optimierungen für bessere LinkedIn-Performance.