"""
GENESIS-Destatis Webservice/API-Client (RESTful/JSON, Version 2020).

Offizielle Doku: https://genesis.destatis.de/genesisWS/swagger-ui/index.html
Anwenderdokumentation "Webservice/API" (Statistisches Bundesamt), Version 5.1
(01.06.2026): "Die GET-Methoden mit Credentials wurden durch die bisher
parallel angebotenen POST-Methoden der RESTful/JSON-Schnittstelle ersetzt."

D.h. seit diesem Release funktionieren KEINE GET-Requests mit
username/password (oder Token) als Query-Parameter mehr -- solche Requests
werden von GENESIS nicht mehr als API-Aufruf erkannt und stattdessen auf die
Web-Oberfläche umgeleitet (HTML statt JSON).

Aktuelles Schema (Kap. 2.1.3 + jede Methodenbeschreibung):
  - Anfragemethode: POST
  - Zugangsdaten (username/password bzw. Token): als HTTP-Header-Felder
  - alle übrigen Parameter: im Request-Body, Content-Type
    application/x-www-form-urlencoded

Authentifizierung: Ein persönlicher API-Token wird anstelle des Benutzernamens
übergeben, das Passwort bleibt dabei leer (Kap. 2.1.3 der Anwenderdoku).
"""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("genesis")

GENESIS_BASE = "https://genesis.destatis.de/genesisWS/rest/2020"
GENESIS_API_KEY = os.getenv("GENESIS_API_KEY")

_HTTP_TIMEOUT = httpx.Timeout(25.0, connect=10.0)

_UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def has_genesis_key() -> bool:
    return bool(GENESIS_API_KEY)


def _auth_headers() -> dict[str, str]:
    # Persönlicher Token ersetzt den Benutzernamen; Passwort entfällt dann (Kap. 2.1.3).
    # Seit v5.1 werden diese als HTTP-Header übertragen, nicht mehr als Query-Parameter.
    return {"username": GENESIS_API_KEY or "", "password": ""}


class GenesisError(RuntimeError):
    pass


async def _post_json(path: str, body_params: dict[str, Any]) -> dict:
    if not GENESIS_API_KEY:
        raise GenesisError("GENESIS_API_KEY ist nicht gesetzt.")
    headers = {**_UA_HEADERS, **_auth_headers()}
    body = {"language": "de"}
    body.update({k: v for k, v in body_params.items() if v not in (None, "")})
    url = f"{GENESIS_BASE}/{path}"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, data=body)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            # Wurde (z.B. wegen veraltetem Request-Format, Wartung oder
            # ungültigem/abgelaufenem Token) auf die HTML-Weboberfläche
            # statt auf die JSON-API umgeleitet.
            log.warning("GENESIS lieferte kein JSON zurück (%s), Endpunkt: %s", content_type, resp.url)
            raise GenesisError(
                f"GENESIS hat statt JSON-Daten eine Webseite geliefert (evtl. Wartung, "
                f"ungültiger/abgelaufener Token oder eine erneut geänderte Schnittstelle). "
                f"Antwort kam von: {resp.url}"
            )
        data = resp.json()
    status = data.get("Status", {})
    # Code 0 = erfolgreich, Warnungen (z.B. Code 22) liefern trotzdem Daten.
    if status.get("Type") == "Error":
        raise GenesisError(status.get("Content", "Unbekannter GENESIS-Fehler"))
    return data


async def logincheck() -> dict:
    """Prüft, ob der konfigurierte Token gültig ist."""
    return await _post_json("helloworld/logincheck", {})


async def find(term: str, category: str = "all", pagelength: int = 20) -> dict:
    """Volltextsuche über Tabellen/Statistiken/Merkmale/Zeitreihen."""
    return await _post_json(
        "find/find", {"term": term, "category": category, "pagelength": pagelength}
    )


async def tables_for_statistic(code: str, pagelength: int = 50) -> list[dict]:
    """Liste der Tabellen zu einer Statistik-Nummer (z.B. '61261')."""
    data = await _post_json(
        "catalogue/tables2statistic", {"name": code, "pagelength": pagelength}
    )
    return data.get("List") or []


async def table_data(
    name: str,
    startyear: str | None = None,
    endyear: str | None = None,
    regionalvariable: str | None = None,
    regionalkey: str | None = None,
) -> dict:
    """
    Rohdaten einer Tabelle (JSON-eingebettet, flaches CSV im 'ffcsv'-Format
    unter Object.Content). Siehe Kap. 2.5.11 der Anwenderdoku.
    """
    return await _post_json(
        "data/table",
        {
            "name": name,
            "area": "all",
            "format": "ffcsv",
            "compress": "false",
            "startyear": startyear,
            "endyear": endyear,
            "regionalvariable": regionalvariable,
            "regionalkey": regionalkey,
        },
    )


def parse_ffcsv(raw_content: str) -> dict:
    """
    Parst das flache 'ffcsv'-Format (Semikolon-getrennt) aus Object.Content
    in {"header": [...], "rows": [[...], ...]}. Bewusst generisch gehalten,
    da sich Spaltenzahl/-reihenfolge je nach Tabelle unterscheidet.
    """
    if not raw_content:
        return {"header": [], "rows": []}
    reader = csv.reader(io.StringIO(raw_content), delimiter=";")
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return {"header": [], "rows": []}
    return {"header": rows[0], "rows": rows[1:]}


async def chart_png(
    name: str,
    chart_type: int = 0,
    zoom: int = 2,
    startyear: str | None = None,
    endyear: str | None = None,
) -> bytes:
    """
    Liefert ein von GENESIS serverseitig gerendertes Diagramm (PNG) zu einer
    Tabelle zurück -- roher Filedownload, kein JSON-Wrapper (Kap. 2.5.2).
    """
    if not GENESIS_API_KEY:
        raise GenesisError("GENESIS_API_KEY ist nicht gesetzt.")
    headers = {**_UA_HEADERS, **_auth_headers()}
    body = {
        "language": "de",
        "name": name,
        "area": "all",
        "chartType": chart_type,
        "drawPoints": "false",
        "zoom": zoom,
        "focus": "false",
        "tops": "false",
        "format": "png",
    }
    if startyear:
        body["startyear"] = startyear
    if endyear:
        body["endyear"] = endyear
    url = f"{GENESIS_BASE}/data/chart2table"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, data=body)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type:
            # GENESIS liefert bei Fehlern JSON statt PNG (z.B. ungültiger Code),
            # oder -- bei veraltetem Request-Format/abgelaufenem Token -- HTML
            # der Weboberfläche.
            try:
                err = resp.json()
                msg = err.get("Status", {}).get("Content", "Diagramm nicht verfügbar")
            except Exception:
                if "html" in content_type:
                    msg = f"GENESIS hat eine Webseite statt eines Diagramms geliefert (Antwort von: {resp.url})"
                else:
                    msg = "Diagramm nicht verfügbar"
            raise GenesisError(msg)
        return resp.content



# ---------------------------------------------------------------------------
# Kuratierte, für Immobilien/Standortanalysen relevante Tabellen.
# Bewusst mit bestätigten, offiziell dokumentierten Tabellencodes befüllt.
# Weitere Tabellen lassen sich über tables_for_statistic() je Statistik-Nummer
# nachladen (z.B. für Baugenehmigungen/Häuserpreise, deren exakte
# Standard-Tabelle je nach Regionalstand variiert).
# ---------------------------------------------------------------------------
CURATED_TABLES: list[dict] = [
    {
        "code": "61261-0002",
        "statistic": "61261",
        "label": "Baupreisindex Wohngebäude",
        "description": "Preisindizes für den Neubau konventionell gefertigter "
        "Wohngebäude, Quartalswerte (Basis 2015=100).",
        "category": "Baupreise",
    },
    {
        "code": "12411-0001",
        "statistic": "12411",
        "label": "Bevölkerung: Deutschland",
        "description": "Bevölkerungsstand (Fortschreibung), Stichtag, gesamt Deutschland.",
        "category": "Bevölkerung",
    },
    {
        "code": "12411-0009",
        "statistic": "12411",
        "label": "Bevölkerung: Bundesländer",
        "description": "Bevölkerungsstand nach Bundesland, Stichtag.",
        "category": "Bevölkerung",
    },
    {
        "code": "12411-0015",
        "statistic": "12411",
        "label": "Bevölkerung: Kreise",
        "description": "Bevölkerungsstand nach Kreisen, Stichtag -- Basis für "
        "kleinräumige Standortanalysen.",
        "category": "Bevölkerung",
    },
]

# Statistiken, zu denen es weitere (nicht fest kodierte) Tabellen gibt --
# das Frontend kann darüber live bei GENESIS nachfragen (tables_for_statistic).
EXPLORABLE_STATISTICS: list[dict] = [
    {"code": "61262", "label": "Häuserpreisindex / Immobilienpreise"},
    {"code": "31111", "label": "Baugenehmigungen"},
    {"code": "61111", "label": "Verbraucherpreise (u.a. Wohnen/Mieten)"},
    {"code": "61261", "label": "Baupreisindizes (Bauwirtschaft)"},
    {"code": "12411", "label": "Bevölkerungsstand (Fortschreibung)"},
]
