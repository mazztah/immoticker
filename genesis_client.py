"""
GENESIS-Destatis Webservice/API-Client (RESTful/JSON, Version 2020).

Offizielle Doku: https://genesis.destatis.de/genesisWS/swagger-ui/index.html
Anwenderdokumentation "Webservice/API" (Statistisches Bundesamt), Version 5.0.

Authentifizierung: Ein persönlicher API-Token wird anstelle des Benutzernamens
übergeben, das Passwort bleibt dabei leer (siehe Kap. 2.1.3 der Anwenderdoku).

Alle Requests laufen GET-basiert (Query-Parameter), da die Datenmengen für die
hier genutzten Tabellen klein sind und keine Jobs (job=true) benötigt werden.
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

# Ohne einen browser-ähnlichen User-Agent leitet der GENESIS-Reverse-Proxy
# API-Requests teils auf die Web-Oberfläche (HTML-SPA) statt auf die JSON-API
# um (beobachtet: 302 -> /datenbank/online/announcement). Explizite Header
# beugen dem vor.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def has_genesis_key() -> bool:
    return bool(GENESIS_API_KEY)


def _auth_params() -> dict[str, str]:
    # Persönlicher Token ersetzt den Benutzernamen; Passwort entfällt dann (Kap. 2.1.3).
    return {"username": GENESIS_API_KEY or "", "password": ""}


class GenesisError(RuntimeError):
    pass


async def _get_json(path: str, params: dict[str, Any]) -> dict:
    if not GENESIS_API_KEY:
        raise GenesisError("GENESIS_API_KEY ist nicht gesetzt.")
    query = {**_auth_params(), "language": "de"}
    query.update({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{GENESIS_BASE}/{path}"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(url, params=query)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            # Wurde (z.B. wegen Bot-Erkennung oder abgelaufenem Token) auf die
            # HTML-Weboberfläche statt auf die JSON-API umgeleitet.
            log.warning("GENESIS lieferte kein JSON zurück (%s), Endpunkt: %s", content_type, resp.url)
            raise GenesisError(
                f"GENESIS hat statt JSON-Daten eine Webseite geliefert (evtl. Wartung, "
                f"Bot-Schutz oder ungültiger/abgelaufener Token). Antwort kam von: {resp.url}"
            )
        data = resp.json()
    status = data.get("Status", {})
    # Code 0 = erfolgreich, Warnungen (z.B. Code 22) liefern trotzdem Daten.
    if status.get("Type") == "Error":
        raise GenesisError(status.get("Content", "Unbekannter GENESIS-Fehler"))
    return data



async def logincheck() -> dict:
    """Prüft, ob der konfigurierte Token gültig ist."""
    return await _get_json("helloworld/logincheck", {})


async def find(term: str, category: str = "all", pagelength: int = 20) -> dict:
    """Volltextsuche über Tabellen/Statistiken/Merkmale/Zeitreihen."""
    return await _get_json(
        "find/find", {"term": term, "category": category, "pagelength": pagelength}
    )


async def tables_for_statistic(code: str, pagelength: int = 50) -> list[dict]:
    """Liste der Tabellen zu einer Statistik-Nummer (z.B. '61261')."""
    data = await _get_json(
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
    return await _get_json(
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
    query = {
        **_auth_params(),
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
        query["startyear"] = startyear
    if endyear:
        query["endyear"] = endyear
    url = f"{GENESIS_BASE}/data/chart2table"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(url, params=query)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type:
            # GENESIS liefert bei Fehlern JSON statt PNG (z.B. ungültiger Code),
            # oder -- bei Bot-Schutz/abgelaufenem Token -- HTML der Weboberfläche.
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
