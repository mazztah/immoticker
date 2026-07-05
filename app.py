"""
KI-Immo-Terminal — Backend (eine Python-Datei)
================================================
FastAPI-Backend, das:
  1) 154 validierte RSS-Feeds server-seitig abruft & parst (kein CORS-Proxy nötig,
     dadurch deutlich zuverlässiger als der browser-seitige Fetch der Vorversion)
  2) einen KI-Chat bereitstellt (Groq, mit Modell-Fallback-Kette)
  3) daraus LinkedIn-Artikel aus ausgewählten News generiert
  4) das Frontend (static/index.html) ausliefert

Deployment: Cloud Run-kompatibel (liest $PORT), Docker-Datei liegt bei.
Secrets kommen ausschließlich aus Umgebungsvariablen (siehe .env.example) — niemals hier hardcoden.
"""
import os
import re
import time
import json
import queue
import asyncio
import logging
import threading
from collections import defaultdict
from pathlib import Path
from html import unescape

import httpx
import feedparser
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()  # lädt lokale .env, falls vorhanden (Cloud Run nutzt echte Env-Vars/Secrets, .env existiert dort nicht)
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ki_immo_terminal")

# ============================================================
# KONFIGURATION — ausschließlich aus Umgebungsvariablen (.env lokal, Secret/Env in Cloud Run)
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("XAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Persona- & Profil-Links für den LinkedIn-Generator — bitte mit deinen echten URLs befüllen
# (bewusst NICHT vom LLM generieren lassen, damit die Links garantiert korrekt sind).
FILIP_LANDINGPAGE_URL = os.getenv("LANDINGPAGE_URL", "https://jhjjhdkulandhfdfdpagefjdsh-307619780865.europe-west3.run.app/")
FILIP_LINKEDIN_URL = os.getenv("FILIP_LINKEDIN_URL", "https://www.linkedin.com/in/DEIN-LINKEDIN-PROFIL")  # TODO: eintragen
FILIP_XING_URL = os.getenv("FILIP_XING_URL", "https://www.xing.com/profile/DEIN-XING-PROFIL")  # TODO: eintragen

app = FastAPI(title="KI-Immo-Terminal")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend nicht gefunden</h1>", status_code=404)


@app.get("/api/profile-links")
async def profile_links():
    return {
        "landingpage": FILIP_LANDINGPAGE_URL,
        "linkedin": FILIP_LINKEDIN_URL,
        "xing": FILIP_XING_URL,
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "feeds": len(FEEDS)}


# ============================================================
# FEED-DATENBANK — 154 validierte Quellen (Juli 2026 recherchiert), 5 Kategorien
# ============================================================
FEEDS = [
    {"id": 1, "name": "OpenAI News", "url": "https://openai.com/news/rss.xml", "cat": "KI USA", "desc": "Offizieller News- & Research-Feed von OpenAI"},
    {"id": 2, "name": "Anthropic News (Community-Mirror)", "url": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml", "cat": "KI USA", "desc": "Inoffizieller, aber aktiv gepflegter RSS-Mirror der Anthropic Newsroom (Anthropic selbst bietet keinen offiziellen RSS-Feed an)"},
    {"id": 3, "name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "cat": "KI USA", "desc": "Open-Source-Modelle, Tools & Community-News"},
    {"id": 4, "name": "GitHub Blog", "url": "https://github.blog/feed/", "cat": "KI USA", "desc": "Produkt-Updates von GitHub (Copilot, KI-Coding-Tools)"},
    {"id": 5, "name": "arXiv cs.AI", "url": "https://rss.arxiv.org/rss/cs.AI", "cat": "KI USA", "desc": "Neueste wissenschaftliche Paper im Bereich KI"},
    {"id": 6, "name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "cat": "KI USA", "desc": "Einflussreicher unabhängiger LLM-Entwickler & Blogger"},
    {"id": 7, "name": "BAIR Blog (Berkeley AI Research)", "url": "https://bair.berkeley.edu/blog/feed.xml", "cat": "KI USA", "desc": "Forschungsblog der UC Berkeley KI-Fakultät"},
    {"id": 8, "name": "MIT News – KI", "url": "https://news.mit.edu/rss/topic/artificial-intelligence2", "cat": "KI USA", "desc": "KI-Nachrichten direkt vom MIT"},
    {"id": 9, "name": "MIT Technology Review – KI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "cat": "KI USA", "desc": "Tiefgehende Analysen & Policy zu KI"},
    {"id": 10, "name": "MIRI", "url": "https://intelligence.org/feed", "cat": "KI USA", "desc": "Machine Intelligence Research Institute – KI-Sicherheit"},
    {"id": 11, "name": "O'Reilly Radar – AI/ML", "url": "https://www.oreilly.com/radar/topics/ai-ml/feed/index.xml", "cat": "KI USA", "desc": "Tech-Trends & Analysen zu KI/ML"},
    {"id": 12, "name": "KDnuggets", "url": "https://www.kdnuggets.com/feed", "cat": "KI USA", "desc": "Data Science, Machine Learning & KI-News"},
    {"id": 13, "name": "TechCrunch – KI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "cat": "KI USA", "desc": "Startup- & Funding-News rund um KI"},
    {"id": 14, "name": "VentureBeat – KI", "url": "https://venturebeat.com/category/ai/feed/", "cat": "KI USA", "desc": "Enterprise-KI-News & Analysen"},
    {"id": 15, "name": "Financial Times – KI", "url": "https://www.ft.com/artificial-intelligence?format=rss", "cat": "KI USA", "desc": "Wirtschaftsperspektive auf die KI-Industrie"},
    {"id": 16, "name": "The New York Times – KI", "url": "https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/spotlight/artificial-intelligence/rss.xml", "cat": "KI USA", "desc": "KI-Berichterstattung der NYT"},
    {"id": 17, "name": "WIRED – KI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "cat": "KI USA", "desc": "Technologie- & Kulturperspektive auf KI"},
    {"id": 18, "name": "Ars Technica – KI", "url": "https://arstechnica.com/ai/feed/", "cat": "KI USA", "desc": "Tiefgehende technische KI-Berichterstattung"},
    {"id": 19, "name": "The Guardian – KI", "url": "https://www.theguardian.com/technology/artificialintelligenceai/rss", "cat": "KI USA", "desc": "Internationale KI-Berichterstattung"},
    {"id": 20, "name": "ScienceDaily – KI", "url": "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml", "cat": "KI USA", "desc": "Wissenschaftliche KI-Forschungsmeldungen"},
    {"id": 21, "name": "Microsoft News – KI", "url": "https://news.microsoft.com/source/topics/ai/feed/", "cat": "KI USA", "desc": "Offizielle KI-News von Microsoft"},
    {"id": 22, "name": "AWS Blog – KI", "url": "https://aws.amazon.com/blogs/aws/category/artificial-intelligence/feed/", "cat": "KI USA", "desc": "KI/ML-Produktnews von Amazon Web Services"},
    {"id": 23, "name": "InfoWorld – KI", "url": "https://www.infoworld.com/artificial-intelligence/feed/", "cat": "KI USA", "desc": "Enterprise-Tech- & KI-News"},
    {"id": 24, "name": "Fast Company – KI", "url": "https://www.fastcompany.com/section/artificial-intelligence/rss", "cat": "KI USA", "desc": "Business- & Innovationsperspektive auf KI"},
    {"id": 25, "name": "The Verge – KI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "cat": "KI USA", "desc": "Konsumenten-Tech-Journalismus zu KI"},
    {"id": 26, "name": "Analytics Vidhya", "url": "https://www.analyticsvidhya.com/feed/", "cat": "KI USA", "desc": "Data-Science- & KI-Community-Portal"},
    {"id": 27, "name": "Machine Learning Mastery", "url": "https://machinelearningmastery.com/blog/feed/", "cat": "KI USA", "desc": "Praktische ML-Tutorials & Guides"},
    {"id": 28, "name": "MarkTechPost", "url": "https://www.marktechpost.com/feed/", "cat": "KI USA", "desc": "Paper-Roundups & KI-Produktnews"},
    {"id": 29, "name": "Unite.AI", "url": "https://www.unite.ai/feed/", "cat": "KI USA", "desc": "KI- & Robotik-News und -Analysen"},
    {"id": 30, "name": "AI News", "url": "https://www.artificialintelligence-news.com/feed/", "cat": "KI USA", "desc": "Breite KI-Branchenberichterstattung"},
    {"id": 31, "name": "Crunchbase News – KI", "url": "https://news.crunchbase.com/sections/ai/feed/", "cat": "KI USA", "desc": "Funding- & Investment-News im KI-Sektor"},
    {"id": 32, "name": "insideAI News", "url": "https://insideainews.com/feed/", "cat": "KI USA", "desc": "Enterprise-KI & High-Performance-Computing"},
    {"id": 33, "name": "DailyAI", "url": "https://dailyai.com/feed/", "cat": "KI USA", "desc": "Tägliche kompakte KI-News"},
    {"id": 34, "name": "AI Insider", "url": "https://theaiinsider.tech/feed/", "cat": "KI USA", "desc": "Interviews & Funding-News aus der KI-Branche"},
    {"id": 35, "name": "a16z", "url": "https://a16z.com/feed/", "cat": "KI USA", "desc": "Venture-Capital-Perspektive auf KI & Software"},
    {"id": 36, "name": "Reddit r/MachineLearning", "url": "https://www.reddit.com/r/MachineLearning/.rss", "cat": "KI USA", "desc": "Größte ML-Research-Community weltweit"},
    {"id": 37, "name": "Contextual AI Blog", "url": "https://contextual.ai/blog/feed/", "cat": "KI USA", "desc": "LLM-Hersteller (RAG-fokussiert), Research-Updates"},
    {"id": 38, "name": "Vectra AI Blog", "url": "https://www.vectra.ai/blog/rss.xml", "cat": "KI USA", "desc": "KI-Sicherheitsdienstleister"},
    {"id": 39, "name": "GPTZero Blog", "url": "https://gptzero.me/news/rss/", "cat": "KI USA", "desc": "KI-Detection-Dienstleister"},
    {"id": 40, "name": "Analytics India Magazine – KI", "url": "https://analyticsindiamag.com/ai-news-updates/feed/", "cat": "KI USA", "desc": "Globale KI-Branchennews"},
    {"id": 97, "name": "Google Research Blog", "url": "https://blog.research.google/feeds/posts/default?alt=rss", "cat": "KI USA", "desc": "Offizieller Forschungsblog von Google AI / DeepMind"},
    {"id": 98, "name": "IEEE Spectrum", "url": "https://feeds.feedburner.com/IeeeSpectrum", "cat": "KI USA", "desc": "Technik-Leitmedium mit starkem KI/Robotik-Fokus"},
    {"id": 99, "name": "Hacker News (100+ Punkte)", "url": "https://hnrss.org/frontpage?points=100", "cat": "KI USA", "desc": "Gefilterter Feed der meistdiskutierten Tech-/KI-Themen"},
    {"id": 100, "name": "Agent.ai Blog", "url": "https://blog.agent.ai/rss.xml", "cat": "KI USA", "desc": "KI-Agenten-Plattform & Netzwerk für Professionals"},
    {"id": 101, "name": "DRUID AI Blog", "url": "https://druidai.com/blog/rss.xml", "cat": "KI USA", "desc": "Enterprise-KI-Agenten-Plattform"},
    {"id": 41, "name": "THE DECODER", "url": "https://the-decoder.com/feed/", "cat": "KI DE", "desc": "Führendes deutsches KI-News-Portal"},
    {"id": 42, "name": "Zukunftszentrum KI NRW", "url": "https://www.zukunftszentrum-ki.nrw/feed", "cat": "KI DE", "desc": "Staatlich gefördertes KI-Transformationsprogramm für KMU"},
    {"id": 43, "name": "AI.Hamburg", "url": "https://ai.hamburg/de/feed", "cat": "KI DE", "desc": "KI-Cluster & Community-Blog Hamburg"},
    {"id": 44, "name": "NovoAI Blog", "url": "https://novoai.de/en/feed", "cat": "KI DE", "desc": "KI für die Fertigungsindustrie"},
    {"id": 45, "name": "Lamarr Institute", "url": "https://lamarr-institute.org/feed", "cat": "KI DE", "desc": "Deutsches KI-Forschungsinstitut (ML-Blog)"},
    {"id": 46, "name": "BRACAI", "url": "https://bracai.eu/blog-feed.xml", "cat": "KI DE", "desc": "KI-Insights & Beratung"},
    {"id": 47, "name": "Aleph Alpha", "url": "https://aleph-alpha.com/feed/", "cat": "KI DE", "desc": "Deutscher LLM-Hersteller (Heidelberg)"},
    {"id": 48, "name": "Merantix", "url": "https://news.merantix-capital.com/feed", "cat": "KI DE", "desc": "Berliner KI-Venture-Studio & Capital"},
    {"id": 49, "name": "KI-Campus", "url": "https://ki-campus.org/blog/feed", "cat": "KI DE", "desc": "Deutsche Lernplattform für KI-Weiterbildung"},
    {"id": 50, "name": "ScaDS.AI", "url": "https://scads.ai/blog/feed/", "cat": "KI DE", "desc": "Center for Scalable Data Analytics & AI (Dresden/Leipzig)"},
    {"id": 51, "name": "Cognigy Blog", "url": "https://www.cognigy.com/blog/rss.xml", "cat": "KI DE", "desc": "Deutscher Conversational-AI-Anbieter"},
    {"id": 52, "name": "Inman", "url": "https://www.inman.com/feed/", "cat": "Immobilien USA", "desc": "Führendes unabhängiges Real-Estate-News-Portal"},
    {"id": 53, "name": "HousingWire", "url": "https://www.housingwire.com/feed/", "cat": "Immobilien USA", "desc": "US-Mortgage- & Housing-Finance-Fachpublikation"},
    {"id": 54, "name": "Realtor.com News", "url": "https://www.realtor.com/news/feed", "cat": "Immobilien USA", "desc": "News des großen US-Immobilienportals"},
    {"id": 55, "name": "BiggerPockets Blog", "url": "https://www.biggerpockets.com/blog/feed", "cat": "Immobilien USA", "desc": "Größte US-Community für Immobilieninvestoren"},
    {"id": 56, "name": "Realty Times", "url": "https://realtytimes.com/archives?format=feed", "cat": "Immobilien USA", "desc": "Traditionsreiches Real-Estate-News-Netzwerk"},
    {"id": 57, "name": "RISMedia Housecall", "url": "https://blog.rismedia.com/feed", "cat": "Immobilien USA", "desc": "Branchentrends & Business-Development für Makler"},
    {"id": 58, "name": "Brick Underground", "url": "https://www.brickunderground.com/rss.xml", "cat": "Immobilien USA", "desc": "NYC-Wohnimmobilienmarkt im Detail"},
    {"id": 59, "name": "The Real Deal (NY)", "url": "https://therealdeal.com/new-york/feed/", "cat": "Immobilien USA", "desc": "Führendes Fachmedium für NY Commercial Real Estate"},
    {"id": 60, "name": "Redfin Blog", "url": "https://www.redfin.com/blog/feed/", "cat": "Immobilien USA", "desc": "Marktdaten & Trends von Redfin"},
    {"id": 61, "name": "Keeping Current Matters", "url": "https://www.keepingcurrentmatters.com/feed/", "cat": "Immobilien USA", "desc": "Datengetriebene Markt-Insights für Makler"},
    {"id": 62, "name": "Eye On Housing (NAHB)", "url": "https://eyeonhousing.org/feed/", "cat": "Immobilien USA", "desc": "National Association of Home Builders – Marktdaten"},
    {"id": 63, "name": "Miller Samuel Blog", "url": "https://www.millersamuel.com/blog/feed/", "cat": "Immobilien USA", "desc": "Führendes Real-Estate-Appraisal- & Research-Unternehmen"},
    {"id": 64, "name": "Propmodo", "url": "https://www.propmodo.com/real-estate/feed/", "cat": "Immobilien USA", "desc": "PropTech & Commercial Real Estate Innovation"},
    {"id": 65, "name": "Commercial Property Executive", "url": "https://www.commercialsearch.com/news/feed/", "cat": "Immobilien USA", "desc": "Führende Commercial-Real-Estate-Publikation"},
    {"id": 66, "name": "Better Dwelling", "url": "https://betterdwelling.com/feed/", "cat": "Immobilien USA", "desc": "Nordamerikanische Immobilienmarkt-Analysen"},
    {"id": 67, "name": "Zillow Blog", "url": "https://www.zillow.com/blog/feed/", "cat": "Immobilien USA", "desc": "Marktnews & Trends von Zillow"},
    {"id": 68, "name": "REtipster", "url": "https://retipster.com/feed/", "cat": "Immobilien USA", "desc": "Real-Estate-Investing-Strategien"},
    {"id": 69, "name": "Mashvisor Blog", "url": "https://www.mashvisor.com/blog/feed/", "cat": "Immobilien USA", "desc": "Datengetriebene Immobilieninvestment-Analyse"},
    {"id": 70, "name": "Roofstock Learn", "url": "https://learn.roofstock.com/blog/rss.xml", "cat": "Immobilien USA", "desc": "Buy-and-Hold-Investment-Plattform"},
    {"id": 71, "name": "Norada Real Estate", "url": "https://www.noradarealestate.com/blog/feed", "cat": "Immobilien USA", "desc": "Turnkey-Immobilieninvestments"},
    {"id": 72, "name": "CommercialCafe Blog", "url": "https://www.commercialcafe.com/blog/feed", "cat": "Immobilien USA", "desc": "Yardi-Tochter – Commercial-Real-Estate-News"},
    {"id": 73, "name": "PropertyMetrics Blog", "url": "https://propertymetrics.com/blog/feed/", "cat": "Immobilien USA", "desc": "Commercial-Real-Estate-Finanzanalyse"},
    {"id": 74, "name": "VTS Blog", "url": "https://www.vts.com/feed", "cat": "Immobilien USA", "desc": "CRE-Leasing- & Asset-Management-Software"},
    {"id": 75, "name": "NAIOP Market Share", "url": "https://blog.naiop.org/feed/", "cat": "Immobilien USA", "desc": "Verband für Commercial Real Estate Development"},
    {"id": 76, "name": "theBrokerList Blog", "url": "https://blog.thebrokerlist.com/feed/", "cat": "Immobilien USA", "desc": "Commercial-Real-Estate-Broker-Netzwerk"},
    {"id": 77, "name": "Multi-Housing News", "url": "https://www.multihousingnews.com/feed/", "cat": "Immobilien USA", "desc": "Multifamily- & Apartment-Marktnews"},
    {"id": 78, "name": "RealTrends", "url": "https://www.realtrends.com/feed/", "cat": "Immobilien USA", "desc": "Rankings & Analysen der Maklerbranche"},
    {"id": 79, "name": "Trulia Blog", "url": "https://www.trulia.com/blog/feed/", "cat": "Immobilien USA", "desc": "Wohnungssuche & Marktnews"},
    {"id": 80, "name": "Windermere Real Estate Blog", "url": "https://www.windermere.com/feed", "cat": "Immobilien USA", "desc": "Regionaler US-Makler mit starkem Blog"},
    {"id": 81, "name": "HomeLight Blog", "url": "https://www.homelight.com/blog/feed/", "cat": "Immobilien USA", "desc": "iBuyer & Agent-Matching-Plattform"},
    {"id": 82, "name": "Tom Ferry Blog", "url": "https://blog.tomferry.com/rss.xml", "cat": "Immobilien USA", "desc": "Führender Real-Estate-Coach & Influencer"},
    {"id": 83, "name": "Geek Estate Blog", "url": "https://geekestateblog.com/feed", "cat": "Immobilien USA", "desc": "Real Estate Tech & PropTech Insights"},
    {"id": 84, "name": "AppFolio Blog", "url": "https://www.appfolio.com/blog/feed", "cat": "Immobilien USA", "desc": "Property-Management-Software-Anbieter"},
    {"id": 85, "name": "1000watt Blog", "url": "https://1000watt.net/feed/", "cat": "Immobilien USA", "desc": "Marketing- & Brand-Strategie für Makler"},
    {"id": 86, "name": "Cornell Real Estate Review", "url": "https://blog.realestate.cornell.edu/feed/", "cat": "Immobilien USA", "desc": "Akademische Real-Estate-Forschung"},
    {"id": 87, "name": "NotoriousROB", "url": "https://notoriousrob.com/feed/", "cat": "Immobilien USA", "desc": "Kritischer Branchenkommentar zu Real-Estate-Tech"},
    {"id": 88, "name": "The American Genius – Housing", "url": "https://theamericangenius.com/housing/feed/", "cat": "Immobilien USA", "desc": "Business- & Tech-News für die Immobilienbranche"},
    {"id": 89, "name": "SparkRental Blog", "url": "https://sparkrental.com/feed/", "cat": "Immobilien USA", "desc": "Passives Einkommen & Immobilieninvestment"},
    {"id": 102, "name": "Nareit (REIT.com)", "url": "https://www.reit.com/news/rss", "cat": "Immobilien USA", "desc": "Verband der börsennotierten REITs/institutionellen Immobilieninvestoren – deckt Private-Equity-nahe Themen ab"},
    {"id": 103, "name": "REITSWEEK", "url": "https://reitsweek.com/feed", "cat": "Immobilien USA", "desc": "Analysen & News zu Real Estate Investment Trusts"},
    {"id": 90, "name": "Immobilien Zeitung (IZ)", "url": "https://www.iz.de/news/feed", "cat": "Immobilien DE", "desc": "Führendes deutsches Fachmedium der Immobilienwirtschaft"},
    {"id": 91, "name": "Haufe Immobilienwirtschaft", "url": "https://www.haufe.de/xml/rss_129130.xml", "cat": "Immobilien DE", "desc": "Fachportal für Wohnungs- & Immobilienwirtschaft"},
    {"id": 92, "name": "Lukinski (Family Office)", "url": "https://lukinski.com/feed", "cat": "Immobilien DE", "desc": "Off-Market-Immobilien, Investment & Asset Management"},
    {"id": 93, "name": "First Citiz", "url": "https://firstcitiz.com/feed", "cat": "Immobilien DE", "desc": "Berliner Immobilienmakler mit Marktanalysen"},
    {"id": 94, "name": "MyMortgageGermany", "url": "https://mymortgagegermany.de/feed", "cat": "Immobilien DE", "desc": "Digitaler Baufinanzierungs-Vermittler"},
    {"id": 95, "name": "Investby.Immo", "url": "https://investby.immo/blog/rss-en", "cat": "Immobilien DE", "desc": "Immobilieninvestment-Vermittlung & -Beratung"},
    {"id": 96, "name": "WE-NET Ratgeber", "url": "https://we-net.ch/feed", "cat": "Immobilien DE", "desc": "Immobilienpartner mit Brancheneinblicken (DACH-Raum)"},
    {"id": 104, "name": "RStudio AI Blog", "url": "https://blogs.rstudio.com/ai/index.xml", "cat": "KI USA", "desc": "Posit/RStudio – KI & ML im R/Python-Ökosystem"},
    {"id": 105, "name": "DataRobot Blog", "url": "https://www.datarobot.com/blog/feed/", "cat": "KI USA", "desc": "Enterprise-AI-Plattform-Anbieter"},
    {"id": 106, "name": "SAS Blogs – KI", "url": "https://blogs.sas.com/content/topic/artificial-intelligence/feed/", "cat": "KI USA", "desc": "Analytics- & KI-Softwarehersteller SAS"},
    {"id": 107, "name": "Big Data Analytics News – KI", "url": "https://bigdataanalyticsnews.com/category/artificial-intelligence/feed/", "cat": "KI USA", "desc": "Big-Data- & KI-Branchennews"},
    {"id": 108, "name": "AIwire", "url": "https://www.aiwire.net/feed/", "cat": "KI USA", "desc": "Enterprise-KI-News (HPCwire-Schwesterportal)"},
    {"id": 109, "name": "eWeek – KI", "url": "https://www.eweek.com/feed/", "cat": "KI USA", "desc": "IT-Fachmedium mit KI-Schwerpunkt"},
    {"id": 110, "name": "The Conversation – KI", "url": "https://theconversation.com/topics/artificial-intelligence-ai-90/articles.atom", "cat": "KI USA", "desc": "Wissenschaftler-Analysen zu KI (Atom-Feed)"},
    {"id": 111, "name": "Federal News Network – KI", "url": "https://federalnewsnetwork.com/category/technology-main/artificial-intelligence/feed/", "cat": "KI USA", "desc": "KI in der US-Bundesverwaltung"},
    {"id": 112, "name": "Cisco Blogs – KI", "url": "https://blogs.cisco.com/ai/feed", "cat": "KI USA", "desc": "Netzwerktechnik-Konzern, KI-Infrastruktur"},
    {"id": 113, "name": "AI for Good (ITU)", "url": "https://aiforgood.itu.int/feed/", "cat": "KI USA", "desc": "UN-Initiative für KI mit gesellschaftlichem Nutzen"},
    {"id": 114, "name": "Theodo Data & IA", "url": "https://data-ai.theodo.com/en/technical-blog/rss.xml", "cat": "KI USA", "desc": "Technischer Data/KI-Consulting-Blog"},
    {"id": 115, "name": "AI Weekly (Newsletter)", "url": "https://aiweekly.co/issues.rss", "cat": "KI USA", "desc": "Wöchentlicher kuratierter KI-Newsletter"},
    {"id": 116, "name": "AIhub", "url": "https://aihub.org/feed/?cat=-473", "cat": "KI USA", "desc": "Non-Profit-Portal von KI-Fachgesellschaften"},
    {"id": 117, "name": "AI Summer", "url": "https://theaisummer.com/feed.xml", "cat": "KI USA", "desc": "Deep-Learning-Tutorials & Theorie"},
    {"id": 118, "name": "GovTech – KI", "url": "https://www.govtech.com/artificial-intelligence.rss", "cat": "KI USA", "desc": "KI im öffentlichen Sektor (USA)"},
    {"id": 119, "name": "Live Science – KI", "url": "https://www.livescience.com/feeds/tag/artificial-intelligence", "cat": "KI USA", "desc": "Wissenschaftsjournalismus zu KI"},
    {"id": 120, "name": "Computerworld – KI", "url": "https://www.computerworld.com/artificial-intelligence/feed/", "cat": "KI USA", "desc": "Enterprise-IT-News mit KI-Fokus"},
    {"id": 121, "name": "ScienceNews – KI", "url": "https://www.sciencenews.org/topic/artificial-intelligence/feed", "cat": "KI USA", "desc": "Unabhängiger Wissenschaftsjournalismus"},
    {"id": 122, "name": "Hackaday", "url": "https://hackaday.com/blog/feed", "cat": "KI USA", "desc": "Maker-/Hardware-Kultur mit viel ML/Robotik"},
    {"id": 123, "name": "Tech.eu", "url": "https://tech.eu/feed/", "cat": "KI USA", "desc": "Europäisches Tech-/KI-Startup-Ökosystem"},
    {"id": 124, "name": "A Student of the Real Estate Game", "url": "https://astudentoftherealestategame.com/feed/", "cat": "Immobilien USA", "desc": "Investment-Strategien & Marktkommentar"},
    {"id": 125, "name": "CRE Herald", "url": "https://creherald.com/feed/", "cat": "Immobilien USA", "desc": "Commercial-Real-Estate-Deal-News"},
    {"id": 126, "name": "First American Commercial Blog", "url": "https://blog.firstam.com/commercial/rss.xml", "cat": "Immobilien USA", "desc": "Titelversicherer, CRE-Marktanalysen"},
    {"id": 127, "name": "SimonCRE Blog", "url": "https://blog.simoncre.com/insights/rss.xml", "cat": "Immobilien USA", "desc": "Entwickler-Insights, Einzelhandelsimmobilien"},
    {"id": 128, "name": "Century 21 Real Estate Blog", "url": "https://www.century21.com/real-estate-blog/feed/", "cat": "Immobilien USA", "desc": "Große Makler-Franchise-Marke"},
    {"id": 129, "name": "Outfront (Keller Williams) Blog", "url": "https://outfront.kw.com/feed/", "cat": "Immobilien USA", "desc": "Keller-Williams-Netzwerk-Blog"},
    {"id": 130, "name": "Hooked on Houses", "url": "https://hookedonhouses.net/feed/", "cat": "Immobilien USA", "desc": "Immobilien-Lifestyle & bekannte Häuser"},
    {"id": 131, "name": "FastExpert Blog", "url": "https://fastexpert.com/blog/feed/", "cat": "Immobilien USA", "desc": "Makler-Vermittlungsplattform, Marktguides"},
    {"id": 132, "name": "Real Estate Webmasters Blog", "url": "https://www.realestatewebmasters.com/blogs/rss/", "cat": "Immobilien USA", "desc": "Real-Estate-Tech & Website-Marketing"},
    {"id": 133, "name": "Bubbleinfo", "url": "https://feeds.feedburner.com/bubbleinfo", "cat": "Immobilien USA", "desc": "San-Diego-Marktbeobachtung (langjährig)"},
    {"id": 134, "name": "Kyle Handy Blog", "url": "https://kylehandy.com/feed/", "cat": "Immobilien USA", "desc": "Investoren-Guides & Marktanalysen"},
    {"id": 135, "name": "McKissock Learning – Real Estate", "url": "https://www.mckissock.com/blog/real-estate/real-estate-marketing/feed/", "cat": "Immobilien USA", "desc": "Weiterbildung & Marketing für Makler"},
    {"id": 136, "name": "Colibri Real Estate Blog", "url": "https://www.colibrirealestate.com/feed/", "cat": "Immobilien USA", "desc": "Makler-Ausbildung & Karriere-Guides"},
    {"id": 137, "name": "Rentometer Articles", "url": "https://www.rentometer.com/articles.atom", "cat": "Immobilien USA", "desc": "Mietpreisdaten & Vermieter-Insights"},
    {"id": 138, "name": "Hilton & Hyland Blog", "url": "https://hiltonhyland.com/blog/feed/", "cat": "Immobilien USA", "desc": "Luxusimmobilien Los Angeles"},
    {"id": 139, "name": "Fancy Pants Homes", "url": "https://fancypantshomes.com/feed/", "cat": "Immobilien USA", "desc": "High-End-Luxusimmobilien-Portal"},
    {"id": 140, "name": "Forbes – Business", "url": "https://www.forbes.com/business/feed/", "cat": "Top Magazine", "desc": "Weltweit führendes Wirtschaftsmagazin"},
    {"id": 141, "name": "The Economist – Business", "url": "https://www.economist.com/feeds/print-sections/77/business.xml", "cat": "Top Magazine", "desc": "Globale Wirtschafts- & Politikanalyse"},
    {"id": 142, "name": "Harvard Business Review", "url": "https://feeds.harvardbusiness.org/harvardbusiness?format=xml", "cat": "Top Magazine", "desc": "Führungs- & Managementforschung"},
    {"id": 143, "name": "Bloomberg – Politics", "url": "https://www.bloomberg.com/politics/feeds/site.xml", "cat": "Top Magazine", "desc": "Wirtschaftspolitik & Regulierung"},
    {"id": 144, "name": "Financial Times – US", "url": "https://www.ft.com/rss/home/us", "cat": "Top Magazine", "desc": "Internationale Finanzberichterstattung"},
    {"id": 145, "name": "CNN Business", "url": "http://rss.cnn.com/rss/edition_business.rss", "cat": "Top Magazine", "desc": "Globale Wirtschaftsnachrichten"},
    {"id": 146, "name": "Inc. Magazine", "url": "https://www.inc.com/rss", "cat": "Top Magazine", "desc": "Gründer- & Wachstumsstrategien"},
    {"id": 147, "name": "The Big Picture (Ritholtz)", "url": "https://ritholtz.com/feed/", "cat": "Top Magazine", "desc": "Einflussreicher Marktkommentar (Barry Ritholtz)"},
    {"id": 148, "name": "Abnormal Returns", "url": "https://abnormalreturns.com/feed/", "cat": "Top Magazine", "desc": "Kuratierte Finanz-/Investment-Links"},
    {"id": 149, "name": "The Pragmatic Capitalist", "url": "https://www.pragcap.com/feed/", "cat": "Top Magazine", "desc": "Makroökonomische Analyse"},
    {"id": 150, "name": "Noahpinion", "url": "https://www.noahpinion.blog/feed", "cat": "Top Magazine", "desc": "Vielgelesener Ökonomie- & Tech-Substack"},
    {"id": 151, "name": "FRED Blog (St. Louis Fed)", "url": "https://fredblog.stlouisfed.org/feed/", "cat": "Top Magazine", "desc": "Offizielle US-Notenbank-Datenanalyse"},
    {"id": 152, "name": "Project Syndicate", "url": "https://www.project-syndicate.org/rss", "cat": "Top Magazine", "desc": "Meinungsbeiträge von Ökonomen & Politikern weltweit"},
    {"id": 153, "name": "Conversable Economist", "url": "https://conversableeconomist.com/feed/", "cat": "Top Magazine", "desc": "Wirtschaftsanalyse (Journal of Economic Perspectives)"},
    {"id": 154, "name": "Naked Capitalism", "url": "https://www.nakedcapitalism.com/feed", "cat": "Top Magazine", "desc": "Kritische Finanz- & Wirtschaftskommentare"},
]

FEED_COUNTS = defaultdict(int)
for _f in FEEDS:
    FEED_COUNTS[_f["cat"]] += 1

CATEGORIES = ["KI USA", "KI DE", "Immobilien USA", "Immobilien DE", "Top Magazine"]

# ============================================================
# RSS-FETCH-ENGINE (server-seitig — kein CORS-Proxy mehr nötig)
# ============================================================
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KI-Immo-Terminal/1.0; +https://github.com/)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\'>]+)["\']', re.IGNORECASE)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return unescape(_STRIP_TAGS_RE.sub("", text)).strip()


def _extract_image(entry) -> str | None:
    # media_content / media_thumbnail (feedparser normalisiert media:* Namespace-Felder)
    media_content = getattr(entry, "media_content", None)
    if media_content:
        for m in media_content:
            url = m.get("url")
            medium = m.get("medium", "")
            if url and medium not in ("audio", "video"):
                return url
    media_thumb = getattr(entry, "media_thumbnail", None)
    if media_thumb:
        for m in media_thumb:
            if m.get("url"):
                return m["url"]
    # enclosures mit Bild-Type
    for enc in getattr(entry, "enclosures", []) or getattr(entry, "links", []):
        if isinstance(enc, dict) and str(enc.get("type", "")).startswith("image") and enc.get("href", enc.get("url")):
            return enc.get("href") or enc.get("url")
    # Erstes <img> im HTML-Content suchen
    html_sources = []
    if entry.get("content"):
        html_sources.append(entry["content"][0].get("value", ""))
    html_sources.append(entry.get("summary", ""))
    for html in html_sources:
        match = _IMG_SRC_RE.search(html or "")
        if match:
            return match.group(1)
    return None


def _format_date(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return "", 0
    try:
        ts = time.mktime(parsed)
        pretty = time.strftime("%d. %b", parsed)
        return pretty, int(ts)
    except Exception:
        return "", 0


async def _fetch_one_feed(client: httpx.AsyncClient, feed: dict) -> list[dict]:
    try:
        resp = await client.get(feed["url"], headers=_HTTP_HEADERS, timeout=12.0, follow_redirects=True)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        items = []
        for entry in parsed.entries[:5]:
            pretty_date, ts = _format_date(entry)
            items.append({
                "title": _strip_html(entry.get("title", "Kein Titel")),
                "link": entry.get("link", "#"),
                "description": _strip_html(entry.get("summary", ""))[:180],
                "pubDate": pretty_date,
                "pubDateRaw": ts,
                "image": _extract_image(entry),
                "source": feed["name"],
                "category": feed["cat"],
            })
        return items
    except Exception as exc:
        logger.info("Feed fehlgeschlagen [%s]: %s", feed["name"], exc)
        return []


@app.get("/api/feeds")
async def get_feeds(category: str = Query(..., description="Eine von: " + ", ".join(CATEGORIES))):
    selected = [f for f in FEEDS if f["cat"] == category]
    if not selected:
        return JSONResponse({"error": f"Unbekannte Kategorie: {category}"}, status_code=400)

    sem = asyncio.Semaphore(12)

    async def bound_fetch(client, feed):
        async with sem:
            return feed, await _fetch_one_feed(client, feed)

    async with httpx.AsyncClient(http2=False) as client:
        results = await asyncio.gather(*(bound_fetch(client, f) for f in selected))

    articles, failed = [], []
    for feed, items in results:
        if items:
            articles.extend(items)
        else:
            failed.append(feed["name"])

    articles.sort(key=lambda a: a.get("pubDateRaw") or 0, reverse=True)

    return {
        "category": category,
        "total_sources": len(selected),
        "live_sources": len(selected) - len(failed),
        "failed_sources": failed,
        "articles": articles[:60],
    }


@app.get("/api/feeds/meta")
async def feeds_meta():
    return {"total": len(FEEDS), "by_category": dict(FEED_COUNTS), "feeds": FEEDS}


# ============================================================
# KI-CHAT (Groq, mit Modell-Fallback-Kette — Muster wie chat_ai.py)
# ============================================================
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY ist nicht gesetzt.")
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY, http_client=httpx.Client(timeout=_HTTP_TIMEOUT))
        logger.info("✅ Groq Client initialisiert")
    return _groq_client


GROQ_MODEL_FALLBACK = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]

MAX_CHAT_MESSAGES = 20
_SESSION_TTL = 60 * 60 * 2
chat_histories: dict[str, list] = defaultdict(list)
_last_seen: dict[str, float] = {}

BASE_SYSTEM_PROMPT = """Du bist der KI-Assistent des "KI-Immo-Terminal" — einer Web-App, die RSS-Feeds aus den \
Bereichen Künstliche Intelligenz und Immobilien (USA & Deutschland) sowie führende Wirtschaftsmagazine aggregiert. \
Antworte präzise, sachlich, auf Deutsch (außer explizit anders gewünscht). Erfinde keine Fakten oder Artikel, die \
nicht im Kontext stehen — wenn dir Information fehlt, sag das ehrlich."""


def _ensure_history(session_id: str, articles_context: str) -> list:
    now = time.time()
    last = _last_seen.get(session_id)
    if not chat_histories[session_id] or (last and now - last > _SESSION_TTL):
        system = BASE_SYSTEM_PROMPT + "\n\nAktuell geladene Live-Artikel als Kontext:\n" + articles_context
        chat_histories[session_id] = [{"role": "system", "content": system}]
    _last_seen[session_id] = now
    return chat_histories[session_id]


def _build_articles_context(articles: list[dict]) -> str:
    if not articles:
        return "Aktuell sind noch keine Live-Artikel geladen."
    lines = []
    for a in articles[:50]:
        lines.append(f'- [{a.get("category","")}] {a.get("source","")}: "{a.get("title","")}" '
                      f'({a.get("pubDate","kein Datum")}) — {a.get("link","")}')
    return "\n".join(lines)


async def _call_groq(history: list) -> str:
    client = _get_groq_client()
    last_error = None
    for model_name in GROQ_MODEL_FALLBACK:
        try:
            completion = await asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=history,
                temperature=0.6,
                max_tokens=900,
                top_p=0.9,
                stream=False,
            )
            msg = completion.choices[0] if completion.choices else None
            content = getattr(getattr(msg, "message", None), "content", "") if msg else ""
            return (content or "").strip() or "Entschuldigung, dazu ist mir gerade nichts Sinnvolles eingefallen."
        except Exception as exc:
            last_error = exc
            error_str = str(exc).lower()
            logger.warning("Groq-Modell %s fehlgeschlagen: %s", model_name, exc)
            if any(kw in error_str for kw in ["401", "unauthorized", "invalid_api_key", "authentication"]):
                return "Der KI-Chat hat ein Konfigurationsproblem mit dem GROQ_API_KEY. Bitte in den Umgebungsvariablen prüfen."
            continue
    raise RuntimeError(f"Alle Groq-Modelle fehlgeschlagen: {last_error}")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    articles: list[dict] = []


@app.post("/api/chat")
async def chat(payload: ChatRequest):
    message = (payload.message or "").strip()
    if not message:
        return JSONResponse({"reply": "Bitte eine Nachricht eingeben."}, status_code=400)
    message = message[:2000]
    session_id = payload.session_id or "default"

    if not GROQ_API_KEY:
        return {"reply": "Der KI-Chat ist aktuell nicht konfiguriert (fehlender GROQ_API_KEY in den Umgebungsvariablen)."}

    try:
        articles_context = _build_articles_context(payload.articles)
        history = _ensure_history(session_id, articles_context)
        history.append({"role": "user", "content": message})
        if len(history) > MAX_CHAT_MESSAGES:
            history[:] = [history[0]] + history[-(MAX_CHAT_MESSAGES - 1):]

        reply = await _call_groq(history)
        history.append({"role": "assistant", "content": reply})
        return {"reply": reply}
    except Exception:
        logger.exception("Fehler in /api/chat")
        return {"reply": "🟠 Der KI-Chat ist gerade nicht erreichbar oder überlastet. Bitte in 20–30 Sekunden erneut versuchen."}


# ============================================================
# LINKEDIN-ARTIKEL-GENERATOR (aus per Checkbox ausgewählten News) — mit Token-Streaming
# ============================================================
LINKEDIN_SYSTEM_PROMPT = f"""Du bist ein professioneller Ghostwriter für LinkedIn-Artikel im Bereich KI und \
Immobilien. Du schreibst im authentischen, persönlichen Stil von Filip Makarczyk – Hybrid-Experte mit über \
13 Jahren Property-Management-Erfahrung, der seine eigenen produktionsreifen KI-Systeme (25+ Module) selbst \
baut und betreibt.

Antworte AUSSCHLIESSLICH mit einem validen JSON-Objekt, ohne Markdown-Codeblock, ohne Erklärtext davor oder \
danach, exakt im Format:
{{"headline": "...", "body": "...", "quote": "...", "hashtags": ["...", "..."], "keywords": ["...", "..."]}}

Anforderungen:

- headline: Prägnanter, aufmerksamkeitsstarker Titel (max. 12 Wörter), neugierig machend, mit klarem Nutzenversprechen.

- body: Vollständiger LinkedIn-Artikel auf Deutsch, in Markdown-Light formatiert (**fett** für Zwischenüberschriften, \
"- " für Aufzählungen, Leerzeile zwischen Absätzen). Struktur in klar erkennbare Abschnitte:
  1. **Hook** — starker erster Satz + kurze Einordnung der ausgewählten News
  2. **Was das für 2026 bedeutet** — Analyse der Implikationen für die Immobilienbranche (z.B. Revenue \
Intelligence, Hyper-Personalization, gescheiterte KI-Projekte vermeiden)
  3. **Praktische Benefits** — konkrete Effizienzgewinne als kurze Aufzählung (Reporting, Mieterkommunikation, \
Due Diligence, Marketing etc.)
  4. **Mein roter Faden** — persönliche Perspektive als Hybrid-Experte ("Genau deshalb habe ich in meinem \
KI-Ökosystem…")
  5. **Ausblick** — klare Handlungsempfehlung, dann ein natürlicher Call-to-Action mit dieser Landingpage: \
{FILIP_LANDINGPAGE_URL}
  Danach ein "**Quellen:**"-Abschnitt mit den Original-Links der ausgewählten News.
  Kurze Absätze (LinkedIn-Stil), Emojis sparsam aber gezielt einsetzen. Entwickle einen eigenen roten Faden \
statt die News nur aufzulisten. Nutze die Keywords organisch im Fließtext.

- quote: EIN einzelner, einprägsamer Pull-Quote-Satz (max. 25 Wörter) aus/im Stil des Artikels, der als \
optisch hervorgehobenes Zitat über dem Artikel angezeigt wird.

- hashtags: MINDESTENS 22 relevante Hashtags (ohne #-Symbol) als Array. Gute Mischung aus breiten und \
hoch-spezifischen Tags (z.B. KIImmobilien, PropTech2026, PropertyManagement, VoiceAI, \
MultiAgentOrchestrierung, EUAIAct, NOI, SmartBuilding, RAG, TenantExperience, RealEstateTech, \
DigitalTransformation, HybridExpert, AssetManagement, ProptechGermany, ImmobilienKI usw.).

- keywords: MINDESTENS 22 aktuell relevante Keywords/Phrasen im Kontext des Artikels und der Themen KI + \
Immobilien/Property Management 2026 als Array, z.B. KI-gestütztes Property Management, PropTech 2026, \
Multi-Agent-Orchestrierung, Voice AI Immobilien, RAG-Wissenssysteme, Automatisiertes Reporting, Tenant Voice \
Assistant, Effizienzsteigerung, NOI-Optimierung, Predictive Analytics, Digital Twin, Smart Building, EU AI Act, \
Hyper-Personalization, Agentic AI, Workflow Automation, Data-Driven Property Operations, Generative AI \
Marketing, Due Diligence Automation, Revenue Intelligence, Business Intelligence Real Estate, Property \
Operations Optimization, Asset Management AI, Mieterkommunikation 24/7, Cloud Deployment Immobilien, \
Hybrid-Experte KI Immobilien.

Schreibe professionell, aber persönlich und praxisnah. Der Leser soll spüren, dass hier jemand schreibt, der \
beide Welten wirklich versteht und selbst Systeme baut."""


class LinkedInRequest(BaseModel):
    articles: list[dict]


def _build_linkedin_messages(articles: list[dict]) -> list[dict]:
    articles_text = "\n\n".join(
        f'{i+1}. "{a.get("title","")}" — Quelle: {a.get("source","")} ({a.get("category","")})'
        f'{", " + a["pubDate"] if a.get("pubDate") else ""}\n'
        f'Beschreibung: {a.get("description") or "(keine Beschreibung verfügbar)"}\nLink: {a.get("link","")}'
        for i, a in enumerate(articles)
    )
    return [
        {"role": "system", "content": LINKEDIN_SYSTEM_PROMPT},
        {"role": "user", "content": f"Erstelle einen LinkedIn-Artikel basierend auf diesen "
                                    f"{len(articles)} ausgewählten News:\n\n{articles_text}"},
    ]


def _groq_stream_worker(messages: list[dict], model: str, max_tokens: int, temperature: float, out_q: "queue.Queue"):
    """Läuft in einem eigenen Thread, damit der blockierende Groq-Stream-Iterator den Event-Loop nicht blockiert."""
    try:
        client = _get_groq_client()
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                out_q.put(delta)
    except Exception as exc:
        out_q.put(f"__ERROR__:{exc}")
    finally:
        out_q.put(None)  # Sentinel: Stream fertig


async def _stream_groq(messages: list[dict], model: str, max_tokens: int = 2500, temperature: float = 0.7):
    """Async-Generator, der Groq-Text-Chunks liefert, sobald sie eintreffen (echtes Token-Streaming)."""
    out_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_groq_stream_worker, args=(messages, model, max_tokens, temperature, out_q), daemon=True
    )
    thread.start()
    loop = asyncio.get_event_loop()
    while True:
        chunk = await loop.run_in_executor(None, out_q.get)
        if chunk is None:
            break
        if isinstance(chunk, str) and chunk.startswith("__ERROR__:"):
            raise RuntimeError(chunk[len("__ERROR__:"):])
        yield chunk


@app.post("/api/linkedin")
async def generate_linkedin(payload: LinkedInRequest):
    if not payload.articles:
        return JSONResponse({"error": "Keine Artikel ausgewählt."}, status_code=400)
    if not GROQ_API_KEY:
        return JSONResponse({"error": "GROQ_API_KEY ist nicht konfiguriert."}, status_code=503)

    messages = _build_linkedin_messages(payload.articles)

    async def token_stream():
        try:
            async for chunk in _stream_groq(messages, GROQ_MODEL_FALLBACK[0], max_tokens=2500, temperature=0.75):
                yield chunk
        except Exception as exc:
            logger.exception("Fehler beim LinkedIn-Streaming")
            yield f"\n__STREAM_ERROR__: {exc}"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

