"""
heartbeat_utils.py — NashtradesDRIT

Helper para que cada proceso (signal_engine, bot_engine, news_engine)
reporte que sigue vivo, para que el dashboard pueda mostrar su estado real.
"""
from datetime import datetime, timezone

from supabase import create_client

from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def send_heartbeat(process_name: str, extra: dict = None):
    try:
        _supabase.table("bot_heartbeats").upsert({
            "process_name": process_name,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "extra": extra or {},
        }).execute()
    except Exception as e:
        print(f"[heartbeat_utils] No se pudo enviar heartbeat de {process_name}: {e}")
