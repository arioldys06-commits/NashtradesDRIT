"""
bot_engine.py — NashtradesDRIT

Loop principal: conecta a MT5, pide señales a signal_engine, ejecuta y gestiona posiciones.

TODOs pendientes antes de operar en vivo:
  - Envio real de ordenes: execute_signal() todavia solo imprime/registra,
    no manda la orden a MT5 — hacerlo despues de validar con backtesting.
  - Conteo de trades diarios y cooldown: implementados aqui en memoria
    (se pierden si el proceso se reinicia); para produccion mover a Supabase
    (tabla trades_ejecutados) como hace result_tracker.py en TradingProEA.
  - news_engine.py debe correr periodicamente (ej. cada 15-30 min, en un
    proceso/scheduler separado) para mantener news_events actualizado —
    is_high_impact_news_nearby() solo LEE la tabla, no la actualiza.
"""
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    MAGIC_NUMBER, SYMBOL, FIXED_LOT, BOT_LOOP_INTERVAL,
)
from signal_engine import evaluate_all_strategies
from news_engine import is_high_impact_news_nearby

TIMEFRAMES = {
    "H1": mt5.TIMEFRAME_H1,
    "M30": mt5.TIMEFRAME_M30,
    "M15": mt5.TIMEFRAME_M15,
    "M5": mt5.TIMEFRAME_M5,
    "M1": mt5.TIMEFRAME_M1,
}
CANDLES_PER_TIMEFRAME = 300

# Estado en memoria (se reinicia si el bot se reinicia — ver TODO arriba)
_daily_trade_count = 0
_daily_r_result = 0.0
_daily_date = None
_last_trade_time = None


def connect_mt5() -> bool:
    if not mt5.initialize(path=MT5_PATH, login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        print(f"[ERROR] No se pudo conectar a MT5: {mt5.last_error()}")
        return False

    account_info = mt5.account_info()
    if account_info is None or account_info.login != MT5_LOGIN:
        print("[ERROR] Conectado a una cuenta/terminal distinta a la esperada. Revisar MT5_PATH.")
        return False

    print(f"[OK] Conectado a MT5 — cuenta {account_info.login} ({SYMBOL}, magic={MAGIC_NUMBER})")
    return True


def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def get_market_data() -> dict:
    """Carga velas de SYMBOL para cada timeframe desde MT5, mas spread y hora UTC."""
    market_data = {}

    for label, tf in TIMEFRAMES.items():
        rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, CANDLES_PER_TIMEFRAME)
        if rates is None or len(rates) == 0:
            print(f"[WARN] No se pudieron obtener velas {label} de {SYMBOL}")
            return {}
        market_data[label] = _rates_to_df(rates)

    symbol_info = mt5.symbol_info(SYMBOL)
    market_data["spread"] = symbol_info.spread if symbol_info else None
    market_data["hour_utc"] = datetime.now(timezone.utc).hour

    return market_data


def has_open_position() -> bool:
    """Bloqueo absoluto: no operar si ya hay una posicion abierta en SYMBOL con este magic."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return False
    return any(p.magic == MAGIC_NUMBER for p in positions)


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


def execute_signal(signal: dict):
    """
    Placeholder — todavia NO envia la orden real a MT5.
    Aplica los bloqueos absolutos que dependen de estado del bot/cuenta
    (posicion abierta, limite diario, cooldown) antes de "ejecutar".
    """
    global _daily_trade_count, _last_trade_time

    _reset_daily_counters_if_needed()

    if has_open_position():
        print(f"[BLOQUEADO] Ya hay una posicion abierta en {SYMBOL} — se ignora la señal.")
        return

    if _daily_trade_count >= signal.get("max_trades_per_day", 3):
        print("[BLOQUEADO] Limite de operaciones diarias alcanzado.")
        return

    if _daily_r_result <= signal.get("daily_stop_loss_r", -2.0):
        print("[BLOQUEADO] Limite de perdida diaria (-2R) alcanzado — bot en pausa por hoy.")
        return

    if _cooldown_active(signal.get("cooldown_minutes", 30)):
        print("[BLOQUEADO] Cooldown activo tras la ultima operacion.")
        return

    # Bloqueo absoluto: noticia de alto impacto cercana (calendario, no sesgo de IA)
    if is_high_impact_news_nearby():
        print("[BLOQUEADO] Noticia de alto impacto cercana (ver news_events en Supabase).")
        return

    print(f"[SEÑAL VALIDA] {signal}")
    # TODO: aqui va mt5.order_send(...) una vez validado con backtesting

    _daily_trade_count += 1
    _last_trade_time = datetime.now(timezone.utc)


def main_loop():
    if not connect_mt5():
        return

    try:
        while True:
            market_data = get_market_data()
            if market_data:
                last_close = market_data["M1"]["close"].iloc[-1]
                last_time = market_data["M1"]["time"].iloc[-1]
                print(f"[CICLO] Velas cargadas OK — ultima M1: {last_time} close={last_close}")
                signals = evaluate_all_strategies(market_data)
                if not signals:
                    print("[CICLO] Sin señal (score < 90 o bloqueo activo)")
                for signal in signals:
                    execute_signal(signal)
            else:
                print("[CICLO] No se pudieron cargar velas — revisar conexion MT5")

            time.sleep(BOT_LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("Bot detenido manualmente.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main_loop()
