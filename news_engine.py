"""
news_engine.py — NashtradesDRIT

Adaptado de news_engine.py de TradingProEA. Hace tres cosas:
1. Jala el calendario macro de Forex Factory (gratis, sin API key).
2. Jala titulares de noticias financieras de Alpha Vantage (gratis, 25 req/dia).
3. Evalua cada evento/noticia con Claude para asignar un sesgo direccional
   (alcista / bajista / neutral) sobre GOLD, con su justificacion.

IMPORTANTE: el sesgo de IA es CONTEXTO, no una senal de entrada — no dispara
trades por si solo. El BLOQUEO real que pide AGV_Gold_Precision_Scalper
("noticia de alto impacto cercana") lo hace is_high_impact_news_nearby(),
que solo mira el calendario (impact='Alto'), no el sesgo de IA.

Requiere (agregar a .env de este proyecto):
    ALPHA_VANTAGE_API_KEY=...
    ANTHROPIC_API_KEY=...

Requiere (pip, ya en requirements.txt):
    requests supabase python-dotenv anthropic
"""

import os
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv
from supabase import create_client, Client
import anthropic

from config import SUPABASE_URL, SUPABASE_KEY

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [news_engine] %(levelname)s %(message)s",
)
log = logging.getLogger("news_engine")

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
AV_NEWS_URL = "https://www.alphavantage.co/query"

# Monedas relevantes para GOLD: USD mueve el oro directamente;
# EUR/GBP/JPY/CHF se incluyen por su efecto indirecto vía DXY
RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF"}
RELEVANT_IMPACT = {"High", "Medium"}  # se descartan Low/Holiday

# Ventana de bloqueo absoluto para AGV_Gold_Precision_Scalper (solo impact='Alto')
NEWS_BLOCK_MINUTES_BEFORE = 30
NEWS_BLOCK_MINUTES_AFTER = 30

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


# ------------------------------------------------------------------
# 1) Calendario macro (Forex Factory, JSON gratis)
# ------------------------------------------------------------------
def fetch_forexfactory_calendar() -> list[dict]:
    try:
        resp = requests.get(FF_CALENDAR_URL, timeout=15)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        log.error(f"Fallo al jalar calendario ForexFactory: {e}")
        return []

    filtered = []
    for e in events:
        currency = e.get("country") or e.get("currency")
        impact = e.get("impact")
        if currency not in RELEVANT_CURRENCIES:
            continue
        if impact not in RELEVANT_IMPACT:
            continue
        filtered.append(
            {
                "source": "ForexFactory",
                "raw_type": "calendar_event",
                "event_time": e.get("date"),
                "currency": currency,
                "impact": "Alto" if impact == "High" else "Medio",
                "title": e.get("title"),
                "summary": None,
                "url": e.get("url"),
                "forecast": e.get("forecast"),
                "previous_value": e.get("previous"),
                "actual_value": e.get("actual"),
            }
        )
    log.info(f"Calendario ForexFactory: {len(filtered)} eventos relevantes")
    return filtered


# ------------------------------------------------------------------
# 2) Noticias (Alpha Vantage NEWS_SENTIMENT, gratis)
# ------------------------------------------------------------------
def fetch_alphavantage_news(limit: int = 20) -> list[dict]:
    if not ALPHA_VANTAGE_API_KEY:
        log.warning("ALPHA_VANTAGE_API_KEY no configurada, se omite esta fuente")
        return []

    params = {
        "function": "NEWS_SENTIMENT",
        "topics": "economy_macro,financial_markets",
        "apikey": ALPHA_VANTAGE_API_KEY,
        "limit": limit,
    }
    try:
        resp = requests.get(AV_NEWS_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"Fallo al jalar noticias Alpha Vantage: {e}")
        return []

    if "feed" not in data:
        log.warning(f"Alpha Vantage sin datos util (posible limite diario): {data.get('Information', data)}")
        return []

    keywords = ("gold", "xau", "fed", "inflation", "rate", "dollar", "dxy")
    articles = []
    for item in data["feed"]:
        title_lower = item.get("title", "").lower()
        summary_lower = item.get("summary", "").lower()
        if not any(k in title_lower or k in summary_lower for k in keywords):
            continue
        articles.append(
            {
                "source": "AlphaVantage",
                "raw_type": "news_article",
                "event_time": _parse_av_time(item.get("time_published")),
                "currency": "USD",
                "impact": None,
                "title": item.get("title"),
                "summary": item.get("summary"),
                "url": item.get("url"),
                "forecast": None,
                "previous_value": None,
                "actual_value": None,
            }
        )
    log.info(f"Alpha Vantage: {len(articles)} articulos relevantes filtrados")
    return articles


def _parse_av_time(raw):
    # Alpha Vantage entrega formato YYYYMMDDTHHMMSS
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


# ------------------------------------------------------------------
# 3) Evaluacion con IA (Claude) — contexto, NO señal de entrada
# ------------------------------------------------------------------
def evaluate_with_claude(item: dict) -> dict:
    if claude is None:
        return {"ai_bias": "neutral", "ai_confidence": "baja", "ai_reasoning": "ANTHROPIC_API_KEY no configurada"}

    contexto = item["title"]
    if item.get("summary"):
        contexto += f"\n{item['summary']}"
    if item.get("forecast") or item.get("previous_value"):
        contexto += f"\nForecast: {item.get('forecast')} | Previo: {item.get('previous_value')}"

    prompt = f"""Eres un analista macro enfocado en GOLD (oro/XAUUSD).
Evalua el siguiente evento/noticia SOLO en funcion de su impacto esperado sobre GOLD.

Evento:
{contexto}

Responde EXCLUSIVAMENTE en este formato, sin texto adicional:
SESGO: [alcista|bajista|neutral]
CONFIANZA: [alta|media|baja]
RAZON: [una frase corta, max 25 palabras, sin porcentajes]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        bias, confidence, reason = "neutral", "baja", "No se pudo evaluar"
        for line in text.splitlines():
            if line.upper().startswith("SESGO:"):
                bias = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("CONFIANZA:"):
                confidence = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("RAZON:"):
                reason = line.split(":", 1)[1].strip()
        return {"ai_bias": bias, "ai_confidence": confidence, "ai_reasoning": reason}
    except Exception as e:
        log.error(f"Fallo evaluacion Claude para '{item['title'][:50]}': {e}")
        return {"ai_bias": "neutral", "ai_confidence": "baja", "ai_reasoning": "Error de evaluacion"}


# ------------------------------------------------------------------
# 4) Guardado en Supabase (con dedup)
# ------------------------------------------------------------------
def _dedup_key(item: dict) -> str:
    raw = f"{item['source']}|{item['title']}|{item.get('event_time')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def save_events(items: list[dict]) -> int:
    saved = 0
    for item in items:
        item["dedup_key"] = _dedup_key(item)
        try:
            supabase.table("news_events").upsert(
                item, on_conflict="dedup_key"
            ).execute()
            saved += 1
        except Exception as e:
            log.error(f"Fallo al guardar '{item['title'][:50]}': {e}")
    return saved


# ------------------------------------------------------------------
# 5) Bloqueo real para AGV_Gold_Precision_Scalper
# ------------------------------------------------------------------
def is_high_impact_news_nearby(
    minutes_before: int = NEWS_BLOCK_MINUTES_BEFORE,
    minutes_after: int = NEWS_BLOCK_MINUTES_AFTER,
) -> bool:
    """
    Bloqueo absoluto real: True si hay un evento de calendario con
    impact='Alto' dentro de la ventana [ahora - minutes_before, ahora + minutes_after].
    Esto es lo que bot_engine.py debe llamar antes de ejecutar una señal —
    el sesgo de IA (ai_bias) es solo contexto informativo, no bloquea nada.
    """
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(minutes=minutes_before)).isoformat()
    window_end = (now + timedelta(minutes=minutes_after)).isoformat()

    try:
        result = (
            supabase.table("news_events")
            .select("title,event_time,impact")
            .eq("impact", "Alto")
            .gte("event_time", window_start)
            .lte("event_time", window_end)
            .execute()
        )
    except Exception as e:
        log.error(f"Fallo al consultar news_events para bloqueo: {e}")
        return False  # si falla la consulta, no bloqueamos (fail-open) — ajustar si se prefiere fail-closed

    if result.data:
        titles = ", ".join(e["title"] for e in result.data)
        log.info(f"[BLOQUEO NOTICIAS] Evento de alto impacto cercano: {titles}")
        return True
    return False


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def run_once():
    log.info("=== Iniciando ciclo de news_engine ===")
    items = fetch_forexfactory_calendar() + fetch_alphavantage_news()

    if not items:
        log.info("Sin eventos/noticias nuevas relevantes")
        return

    for item in items:
        item.update(evaluate_with_claude(item))

    saved = save_events(items)
    log.info(f"=== Ciclo terminado: {saved}/{len(items)} guardados ===")


NEWS_LOOP_INTERVAL_MINUTES = 20


def main_loop():
    """Corre run_once() en loop continuo — su propia consola, separado de
    signal_engine.py y bot_engine.py."""
    print(f"[news_engine] Loop iniciado — cada {NEWS_LOOP_INTERVAL_MINUTES} minutos")
    try:
        while True:
            run_once()
            time.sleep(NEWS_LOOP_INTERVAL_MINUTES * 60)
    except KeyboardInterrupt:
        print("news_engine detenido manualmente.")


if __name__ == "__main__":
    main_loop()
