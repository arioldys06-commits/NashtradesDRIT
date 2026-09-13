"""
mt5_utils.py — NashtradesDRIT

Utilidades de MT5 compartidas entre signal_engine.py y bot_engine.py,
para no duplicar la logica de conexion y carga de velas entre ambos procesos.
"""
import threading
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, SYMBOL, MAGIC_NUMBER

TIMEFRAMES = {
    "H1": mt5.TIMEFRAME_H1,
    "M30": mt5.TIMEFRAME_M30,
    "M15": mt5.TIMEFRAME_M15,
    "M5": mt5.TIMEFRAME_M5,
    "M1": mt5.TIMEFRAME_M1,
}
CANDLES_PER_TIMEFRAME = 300


def connect_mt5(process_name: str = "") -> bool:
    # portable=True es CRITICO: sin esto, mt5.initialize() puede cerrar
    # cualquier otra instancia del mismo build de MT5 que ya este corriendo
    # (ej. la terminal de TradingProEA) para tomar su lugar.
    if not mt5.initialize(path=MT5_PATH, login=MT5_LOGIN, password=MT5_PASSWORD,
                           server=MT5_SERVER, portable=True):
        print(f"[ERROR] No se pudo conectar a MT5: {mt5.last_error()}")
        return False

    account_info = mt5.account_info()
    if account_info is None or account_info.login != MT5_LOGIN:
        print("[ERROR] Conectado a una cuenta/terminal distinta a la esperada. Revisar MT5_PATH.")
        return False

    label = f" [{process_name}]" if process_name else ""
    print(f"[OK]{label} Conectado a MT5 — cuenta {account_info.login} ({SYMBOL}, magic={MAGIC_NUMBER})")
    return True


def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def _copy_rates_with_timeout(symbol, timeframe, count, timeout_seconds=10):
    """
    mt5.copy_rates_from_pos() no tiene timeout nativo y puede quedarse colgado
    esperando indefinidamente si la terminal aun no tiene el historial
    descargado (comun en cuentas nuevas). Se corre en un hilo aparte para
    poder abortar con un mensaje claro en vez de colgar el proceso en silencio.
    """
    result = {"rates": None, "done": False}

    def _worker():
        result["rates"] = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        result["done"] = True

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if not result["done"]:
        return None, True  # (rates, timed_out)
    return result["rates"], False


def get_market_data() -> dict:
    """Carga velas de SYMBOL para cada timeframe desde MT5, mas spread y hora UTC."""
    market_data = {}

    for label, tf in TIMEFRAMES.items():
        rates, timed_out = _copy_rates_with_timeout(SYMBOL, tf, CANDLES_PER_TIMEFRAME)
        if timed_out:
            print(f"[WARN] Timeout esperando velas {label} de {SYMBOL} — "
                  f"la terminal MT5 probablemente aun no tiene el historial descargado. "
                  f"Abre el grafico de {SYMBOL} en {label} manualmente y desliza hacia atras "
                  f"para forzar la descarga, luego reintenta.")
            return {}
        if rates is None or len(rates) == 0:
            print(f"[WARN] No se pudieron obtener velas {label} de {SYMBOL}")
            return {}
        market_data[label] = _rates_to_df(rates)

    symbol_info = mt5.symbol_info(SYMBOL)
    market_data["spread"] = symbol_info.spread if symbol_info else None
    market_data["hour_utc"] = datetime.now(timezone.utc).hour

    return market_data


def has_open_position() -> bool:
    """True si ya hay una posicion abierta en SYMBOL con el magic number del bot."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return False
    return any(p.magic == MAGIC_NUMBER for p in positions)
