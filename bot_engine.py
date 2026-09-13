"""
bot_engine.py — NashtradesDRIT

Proceso independiente (su propia consola): hace polling a la tabla `signals`
de Supabase (llenada por signal_engine.py, en otra consola) y decide si
ejecuta o bloquea cada señal pendiente.

TODOs pendientes antes de operar en vivo:
  - Envio real de ordenes: execute_signal() todavia solo imprime/registra,
    no manda la orden a MT5 — hacerlo despues de validar con backtesting.
  - Conteo de trades diarios y cooldown: implementados aqui en memoria
    (se pierden si el proceso se reinicia); para produccion mover a Supabase
    (tabla trades_ejecutados) como hace result_tracker.py en TradingProEA.
  - news_engine.py debe correr en su propia consola periodicamente (ver
    START_ALL.bat) para mantener news_events actualizado —
    is_high_impact_news_nearby() solo LEE la tabla, no la actualiza.
"""
import time
from datetime import datetime, timezone

from supabase import create_client

from config import SYMBOL, FIXED_LOT, BOT_LOOP_INTERVAL, SUPABASE_URL, SUPABASE_KEY, MAGIC_NUMBER
from mt5_utils import connect_mt5, has_open_position
from news_engine import is_high_impact_news_nearby
from telegram_utils import send_telegram_message

# Estado en memoria (se reinicia si el bot se reinicia — ver TODO arriba)
_daily_trade_count = 0
_daily_r_result = 0.0
_daily_date = None
_last_trade_time = None

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _reset_daily_counters_if_needed():
    global _daily_trade_count, _daily_r_result, _daily_date
    today = datetime.now(timezone.utc).date()
    if _daily_date != today:
        _daily_date = today
        _daily_trade_count = 0
        _daily_r_result = 0.0


def _cooldown_active(cooldown_minutes: int) -> bool:
    if _last_trade_time is None:
        return False
    elapsed = (datetime.now(timezone.utc) - _last_trade_time).total_seconds() / 60
    return elapsed < cooldown_minutes


def fetch_pending_signals() -> list[dict]:
    try:
        result = (
            supabase.table("signals")
            .select("*")
            .eq("symbol", SYMBOL)
            .eq("status", "PENDING")
            .order("created_at")
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[ERROR] No se pudo consultar señales pendientes: {e}")
        return []


def _update_signal_status(signal_id: str, status: str):
    try:
        supabase.table("signals").update({"status": status}).eq("id", signal_id).execute()
    except Exception as e:
        print(f"[ERROR] No se pudo actualizar status de la señal {signal_id}: {e}")


def execute_signal(signal: dict):
    """
    Placeholder — todavia NO envia la orden real a MT5.
    Aplica los bloqueos absolutos (posicion abierta, limite diario, cooldown,
    noticias) antes de "ejecutar", y actualiza el status en Supabase.
    """
    global _daily_trade_count, _last_trade_time

    _reset_daily_counters_if_needed()
    signal_id = signal["id"]

    if has_open_position():
        print(f"[BLOQUEADO] id={signal_id} — ya hay una posicion abierta en {SYMBOL}.")
        _update_signal_status(signal_id, "BLOCKED_OPEN_POSITION")
        return

    max_trades = 3  # ver strategies/agv_gold_precision_scalper.py MAX_TRADES_PER_DAY
    if _daily_trade_count >= max_trades:
        print(f"[BLOQUEADO] id={signal_id} — limite de operaciones diarias alcanzado.")
        _update_signal_status(signal_id, "BLOCKED_DAILY_LIMIT")
        return

    if _daily_r_result <= -2.0:  # ver DAILY_STOP_LOSS_R
        print(f"[BLOQUEADO] id={signal_id} — limite de perdida diaria (-2R) alcanzado.")
        _update_signal_status(signal_id, "BLOCKED_DAILY_LOSS")
        return

    if _cooldown_active(30):  # ver COOLDOWN_MINUTES
        print(f"[BLOQUEADO] id={signal_id} — cooldown activo tras la ultima operacion.")
        _update_signal_status(signal_id, "BLOCKED_COOLDOWN")
        return

    if is_high_impact_news_nearby():
        print(f"[BLOQUEADO] id={signal_id} — noticia de alto impacto cercana.")
        _update_signal_status(signal_id, "BLOCKED_NEWS")
        return

    print(f"[SEÑAL VALIDA] id={signal_id} {signal['direction']} score={signal['score']} "
          f"entry={signal['entry_price']} sl={signal['sl']} tp1={signal['tp1']} tp2={signal['tp2']}")
    # TODO: aqui va mt5.order_send(...) una vez validado con backtesting
    _update_signal_status(signal_id, "EXECUTED")

    _daily_trade_count += 1
    _last_trade_time = datetime.now(timezone.utc)


def main_loop():
    if not connect_mt5(process_name="bot_engine"):
        return

    print(f"[bot_engine] Haciendo polling a la tabla signals cada {BOT_LOOP_INTERVAL}s...")
    send_telegram_message("🟢 NashtradesDRIT — Bot Engine activado y conectado a MT5.")

    try:
        while True:
            pending = fetch_pending_signals()
            if pending:
                print(f"[bot_engine] {len(pending)} señal(es) pendiente(s)")
                for signal in pending:
                    execute_signal(signal)
            time.sleep(BOT_LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("bot_engine detenido manualmente.")
        send_telegram_message("🔴 NashtradesDRIT — Bot Engine detenido manualmente.")


if __name__ == "__main__":
    main_loop()
