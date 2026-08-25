"""
GENESIS-Destatis Webservice/API-Client (RESTful/JSON, Version 2020).

Offizielle Doku: https://genesis.destatis.de/genesisWS/swagger-ui/index.html
Anwenderdokumentation "Webservice/API" (Statistisches Bundesamt), Version 5.0.

Authentifizierung: Ein persönlicher API-Token wird anstelle des Benutzernamens
übergeben; der Passwort-Parameter wird bei Token-Nutzung nicht gesendet.

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

GENESIS_BASE = os.getenv("GENESIS_BASE_URL", "https://www-genesis.destatis.de/genesisWS/rest/2020")
_GENESIS_FALLBACK_BASES = (
    "https://www-genesis.destatis.de/genesisWS/rest/2020",
    "https://genesis.destatis.de/genesisWS/rest/2020",
)
GENESIS_API_KEY = os.getenv("GENESIS_API_KEY")
GENESIS_USERNAME = os.getenv("GENESIS_USERNAME")
GENESIS_PASSWORD = os.getenv("GENESIS_PASSWORD")

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
    token = os.getenv("GENESIS_API_KEY") or GENESIS_API_KEY
    username = os.getenv("GENESIS_USERNAME") or GENESIS_USERNAME
    password = os.getenv("GENESIS_PASSWORD") or GENESIS_PASSWORD
    return bool(token or (username and password))


def _auth_params() -> dict[str, str]:
    # Persönlicher Token ersetzt den Benutzernamen. Wichtig: Bei Token-Nutzung
    # KEINEN leeren password-Parameter mitschicken; GENESIS leitet solche
    # Requests aktuell auf die Web-Oberfläche (/datenbank/online/announcement) um.
    token = os.getenv("GENESIS_API_KEY") or GENESIS_API_KEY
    if token:
        return {"username": token}
    return {
        "username": os.getenv("GENESIS_USERNAME") or GENESIS_USERNAME or "",
        "password": os.getenv("GENESIS_PASSWORD") or GENESIS_PASSWORD or "",
    }


def _safe_url(url: httpx.URL | str) -> str:
    """URL für Logs ohne sensible GENESIS-Zugangsdaten."""
    text = str(url)
    secrets = (
        os.getenv("GENESIS_API_KEY") or GENESIS_API_KEY,
        os.getenv("GENESIS_USERNAME") or GENESIS_USERNAME,
        os.getenv("GENESIS_PASSWORD") or GENESIS_PASSWORD,
    )
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class GenesisError(RuntimeError):
    pass


async def _get_json(path: str, params: dict[str, Any]) -> dict:
    if not has_genesis_key():
        raise GenesisError("GENESIS_API_KEY oder GENESIS_USERNAME/GENESIS_PASSWORD ist nicht gesetzt.")
    query = {**_auth_params(), "language": "de"}
    query.update({k: v for k, v in params.items() if v not in (None, "")})
    bases = (GENESIS_BASE, *[base for base in _GENESIS_FALLBACK_BASES if base != GENESIS_BASE])
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False, headers=_HEADERS) as client:
        resp = None
        for base in bases:
            resp = await client.get(f"{base}/{path}", params=query)
            if resp.status_code not in {301, 302, 303, 307, 308}:
                break
            log.warning(
                "GENESIS leitete API-Request um (%s -> %s)",
                _safe_url(resp.url),
                _safe_url(resp.headers.get("location", "")),
            )
        if resp is None or resp.status_code in {301, 302, 303, 307, 308}:
            raise GenesisError(
                "GENESIS hat den API-Aufruf auf die Web-Oberfläche umgeleitet. "
                "Bitte GENESIS_API_KEY prüfen oder später erneut versuchen."
            )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            # Wurde (z.B. wegen Bot-Erkennung oder abgelaufenem Token) auf die
            # HTML-Weboberfläche statt auf die JSON-API umgeleitet.
            log.warning("GENESIS lieferte kein JSON zurück (%s), Endpunkt: %s", content_type, _safe_url(resp.url))
            raise GenesisError(
                f"GENESIS hat statt JSON-Daten eine Webseite geliefert (evtl. Wartung, "
                f"Bot-Schutz oder ungültiger/abgelaufener Token). Antwort kam von: {_safe_url(resp.url)}"
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
    raw_rows = [[cell.strip() for cell in row] for row in reader]
    rows = [row for row in raw_rows if any(row)]
    if not rows:
        return {"header": [], "rows": []}

    data_rows = rows
    for marker in ("__DATA__", "Daten"):
        marker_index = next(
            (index for index, row in enumerate(rows) if len(row) == 1 and row[0] == marker),
            None,
        )
        if marker_index is not None:
            data_rows = rows[marker_index + 1 :]
            break

    for marker in ("__END__", "© Statistisches Bundesamt"):
        marker_index = next(
            (
                index
                for index, row in enumerate(data_rows)
                if row and row[0].startswith(marker)
            ),
            None,
        )
        if marker_index is not None:
            data_rows = data_rows[:marker_index]
            break

    data_rows = [row for row in data_rows if len(row) > 1]
    if not data_rows:
        return {"header": [], "rows": []}

    header = data_rows[0]
    body = data_rows[1:]
    width = max(len(header), *(len(row) for row in body)) if body else len(header)
    normalized_header = [
        cell or f"Spalte {index + 1}"
        for index, cell in enumerate(header + [""] * (width - len(header)))
    ]
    normalized_rows = [row + [""] * (width - len(row)) for row in body]
    return {"header": normalized_header, "rows": normalized_rows}


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
    if not has_genesis_key():
        raise GenesisError("GENESIS_API_KEY oder GENESIS_USERNAME/GENESIS_PASSWORD ist nicht gesetzt.")
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
    bases = (GENESIS_BASE, *[base for base in _GENESIS_FALLBACK_BASES if base != GENESIS_BASE])
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False, headers=_HEADERS) as client:
        resp = None
        for base in bases:
            resp = await client.get(f"{base}/data/chart2table", params=query)
            if resp.status_code not in {301, 302, 303, 307, 308}:
                break
            log.warning(
                "GENESIS leitete Chart-Request um (%s -> %s)",
                _safe_url(resp.url),
                _safe_url(resp.headers.get("location", "")),
            )
        if resp is None or resp.status_code in {301, 302, 303, 307, 308}:
            raise GenesisError("GENESIS hat den Diagramm-Aufruf auf die Web-Oberfläche umgeleitet.")
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
                    msg = f"GENESIS hat eine Webseite statt eines Diagramms geliefert (Antwort von: {_safe_url(resp.url)})"
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
