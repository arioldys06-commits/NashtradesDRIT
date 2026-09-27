"""
mt5_utils.py — NashtradesDRIT

Utilidades de MT5 compartidas entre signal_engine.py, bot_engine.py y
backtest.py: conexion, carga de velas, posiciones abiertas y calculo de
lote/riesgo en dolares.

IMPORTANTE (2026-09-22): en esta PC, lanzar MT5 desde Python falla con
"Process create failed". Por eso connect_mt5() por defecto usa
launch_terminal=False: se CONECTA a una MT5 que ya abriste y logueaste a mano.
"""
import math
import threading
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, SYMBOL, MAGIC_NUMBER, MIN_LOT_MAX_RISK_USD

TIMEFRAMES = {
    "D1": mt5.TIMEFRAME_D1,  # caja del dia anterior (Box_Theory)
    "H1": mt5.TIMEFRAME_H1,
    "M30": mt5.TIMEFRAME_M30,
    "M15": mt5.TIMEFRAME_M15,
    "M5": mt5.TIMEFRAME_M5,
    "M1": mt5.TIMEFRAME_M1,
}
CANDLES_PER_TIMEFRAME = 300


# ============================================================
# CONEXION
# ============================================================

def connect_mt5(process_name: str = "", launch_terminal: bool = False) -> bool:
    """
    launch_terminal=False (por defecto): se conecta a la terminal ya abierta,
    sin lanzarla ni volver a iniciar sesion.
    launch_terminal=True: la abre con MT5_PATH y hace login con .env.
    """
    if launch_terminal:
        # portable=True es CRITICO: sin esto, mt5.initialize() puede cerrar
        # otra instancia del mismo build (ej. la terminal de TradingProEA).
        ok = mt5.initialize(path=MT5_PATH, login=MT5_LOGIN, password=MT5_PASSWORD,
                            server=MT5_SERVER, portable=True)
    else:
        # Con path apunta a la terminal correcta (y no a la de TradingProEA si
        # ambas estan abiertas); si falla, prueba con la terminal activa. En
        # ambos casos se verifica abajo que la cuenta sea MT5_LOGIN.
        ok = bool(MT5_PATH) and mt5.initialize(path=MT5_PATH)
        if not ok:
            ok = mt5.initialize()

    if not ok:
        print(f"[ERROR] No se pudo conectar a MT5: {mt5.last_error()}")
        if not launch_terminal:
            print("        Abre MT5 manualmente, inicia sesion en la cuenta del bot y reintenta.")
        return False

    account_info = mt5.account_info()
    if account_info is None or (MT5_LOGIN and account_info.login != MT5_LOGIN):
        found = account_info.login if account_info else "ninguna"
        print(f"[ERROR] Conectado a la cuenta {found}, se esperaba {MT5_LOGIN}. "
              f"¿Esta abierta y logueada la terminal correcta?")
        mt5.shutdown()
        return False

    label = f" [{process_name}]" if process_name else ""
    print(f"[OK]{label} Conectado a MT5 — cuenta {account_info.login} (magic={MAGIC_NUMBER})")
    return True


def disconnect_mt5():
    try:
        mt5.shutdown()
    except Exception:
        pass


# ============================================================
# VELAS
# ============================================================

def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def _copy_rates_with_timeout(symbol, timeframe, count, timeout_seconds=10):
    """
    mt5.copy_rates_from_pos() no tiene timeout nativo y puede quedarse colgado
    si la terminal aun no tiene el historial descargado. Se corre en un hilo
    aparte para poder abortar con un mensaje claro.
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


def get_market_data(symbol: str = SYMBOL) -> dict:
    """Carga velas de `symbol` para cada timeframe desde MT5, mas spread y hora UTC."""
    if not mt5.symbol_select(symbol, True):
        print(f"[WARN] El simbolo {symbol} no esta disponible en esta cuenta.")
        return {}

    market_data = {"symbol": symbol}

    for label, tf in TIMEFRAMES.items():
        rates, timed_out = _copy_rates_with_timeout(symbol, tf, CANDLES_PER_TIMEFRAME)
        if timed_out:
            print(f"[WARN] Timeout esperando velas {label} de {symbol} — "
                  f"abre el grafico de {symbol} en {label} en MT5 y desliza hacia atras "
                  f"para forzar la descarga, luego reintenta.")
            return {}
        if rates is None or len(rates) == 0:
            print(f"[WARN] No se pudieron obtener velas {label} de {symbol}")
            return {}
        market_data[label] = _rates_to_df(rates)

    symbol_info = mt5.symbol_info(symbol)
    market_data["spread"] = symbol_info.spread if symbol_info else None
    market_data["point"] = symbol_info.point if symbol_info else None
    market_data["hour_utc"] = datetime.now(timezone.utc).hour

    return market_data


# ============================================================
# POSICIONES
# ============================================================

def has_open_position(symbol: str = SYMBOL, magic: int = MAGIC_NUMBER) -> bool:
    """True si ya hay una posicion abierta en `symbol` con el magic number del bot."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return False
    return any(int(p.magic) == int(magic) for p in positions)


# ============================================================
# RIESGO / LOTE
# ============================================================

def calculate_trade_risk_dollars(symbol: str, direction: str, entry: float, sl: float, lot: float):
    """Perdida en dolares (moneda de la cuenta) si la orden toca el SL. None si no se puede calcular."""
    order_type = mt5.ORDER_TYPE_BUY if str(direction).upper() == "BUY" else mt5.ORDER_TYPE_SELL
    try:
        profit_at_sl = mt5.order_calc_profit(order_type, symbol, float(lot), float(entry), float(sl))
    except Exception:
        return None
    if profit_at_sl is None:
        return None
    return abs(float(profit_at_sl))


def calculate_lot_size(symbol: str, direction: str, entry: float, sl: float, risk_pct: float):
    """
    Lote maximo tal que la perdida hasta el SL no pase de risk_pct % del
    equity, redondeado HACIA ABAJO al volume_step del broker. None si ni el
    lote minimo cabe dentro del riesgo permitido.
    """
    info = mt5.symbol_info(symbol)
    account = mt5.account_info()
    if info is None or account is None or float(account.equity) <= 0:
        return None

    volume_min = float(info.volume_min)
    volume_max = float(info.volume_max)
    volume_step = float(info.volume_step)
    if volume_step <= 0:
        return None

    allowed_risk = float(account.equity) * float(risk_pct) / 100.0
    risk_per_min_lot = calculate_trade_risk_dollars(symbol, direction, entry, sl, volume_min)
    if not risk_per_min_lot or risk_per_min_lot <= 0:
        return None
    if risk_per_min_lot > allowed_risk:
        # Cuenta pequeña: se permite el lote minimo si su riesgo cabe en el tope en dolares
        if MIN_LOT_MAX_RISK_USD > 0 and risk_per_min_lot <= MIN_LOT_MAX_RISK_USD:
            return volume_min
        return None

    raw_lot = volume_min * allowed_risk / risk_per_min_lot
    steps = math.floor((raw_lot - volume_min) / volume_step + 1e-9)
    lot = min(volume_min + steps * volume_step, volume_max)
    lot = round(lot, 8)

    # Verificacion final con el lote redondeado
    risk = calculate_trade_risk_dollars(symbol, direction, entry, sl, lot)
    if risk is None or risk > allowed_risk + 1e-6:
        return None
    return lot


def max_allowed_risk_dollars(symbol: str, lot: float, equity: float, risk_pct: float) -> float:
    """Riesgo maximo aceptado para esta orden: el % normal, o el tope en dolares
    cuando se esta usando el lote minimo en una cuenta pequeña."""
    allowed = float(equity) * float(risk_pct) / 100.0
    info = mt5.symbol_info(symbol)
    if (MIN_LOT_MAX_RISK_USD > 0 and info is not None
            and abs(float(lot) - float(info.volume_min)) < 1e-9):
        allowed = max(allowed, MIN_LOT_MAX_RISK_USD)
    return allowed
