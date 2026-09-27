"""
strategies/box_theory.py — NashtradesDRIT

Box Theory (the.rumers.trading): estrategia de reversion a la media dentro
del rango del dia anterior, para XAUUSD/GOLD.

Construccion de la caja (D1):
  - Techo  = HIGH de la vela diaria anterior (ya cerrada)
  - Piso   = LOW  de la vela diaria anterior
  - Linea media = (techo + piso) / 2  -> divide la caja en dos mitades

Lectura (M15):
  - Precio en la parte ALTA de la caja  -> zona cara: buscar VENTAS
    (vendedores defienden el techo del dia anterior)
  - Precio en la parte BAJA de la caja  -> zona barata: buscar COMPRAS
    (compradores / institucionales defienden el piso -> "buyers are in control")
  - Precio cerca de la linea media      -> NO operar (sin ventaja)

Reglas de compra (venta = las contrarias):
  1. La vela M15 cerrada toca la zona baja (ultimo EDGE_ZONE_PCT de la caja)
  2. Esa vela M15 es de rechazo: cierra alcista, dentro de la caja, y en la
     mitad superior de su propio rango (mecha inferior = rechazo del piso)
  3. El precio NO ha cerrado fuera de la caja (si rompio, la caja se invalida
     para reversion — no se intenta "atrapar el cuchillo")
  Entrada: precio actual (cierre de la ultima M1)
  SL:      por debajo del piso de la caja / minimo de la vela de rechazo + buffer ATR
  TP1:     linea media de la caja (se cierra TP1_CLOSE_PCT% y SL a breakeven)
  TP2:     el lado opuesto de la caja (techo), con un pequeño margen

Puntuacion (100 pts, minimo MIN_SCORE_TO_TRADE):
  Precio en zona extrema de la caja       30  (obligatorio)
  Vela M15 de rechazo                     25  (obligatorio)
  Tamaño de caja sano vs ATR diario       15
  Confirmacion M5 (BOS en la direccion)   15
  Spread + sesion                         15

Bloqueos absolutos:
  - Datos insuficientes (D1/M15/M5/M1)
  - Cierre M15 fuera de la caja (ruptura)
  - Precio fuera de la zona extrema (mitad de la caja = sin ventaja)
  - Spread excesivo o fuera de sesion
  - Relacion riesgo/beneficio hacia TP2 menor a MIN_RR_TP2
  - Misma vela M15 ya señalada (evita publicar la misma señal cada 15s)
"""
from strategies.indicators import atr, detect_bos

STRATEGY_NAME = "Box_Theory"
MIN_SCORE_TO_TRADE = 80

# --- Parametros de la caja (calibrar con backtesting real) ---
EDGE_ZONE_PCT = 0.25          # 25% superior/inferior de la caja = zona operable
REJECTION_CLOSE_PCT = 0.5     # la vela de rechazo cierra en la mitad favorable de su rango
BOX_MIN_ATR_MULT = 0.5        # caja demasiado pequeña vs ATR D1 -> poco recorrido
BOX_MAX_ATR_MULT = 2.0        # caja demasiado grande (dia de noticia) -> niveles poco fiables
SL_BUFFER_ATR_M15 = 0.3       # buffer del SL fuera de la caja, en ATR M15
TP2_MARGIN_PCT = 0.10         # TP2 un 10% antes del lado opuesto (no pedir el extremo exacto)
MIN_RR_TP2 = 1.5

# --- Gestion de riesgo ---
RISK_PER_TRADE_PCT = 0.5
MAX_RISK_PER_TRADE_PCT = 1.0
TP1_CLOSE_PCT = 50
MAX_TRADES_PER_DAY = 3
DAILY_STOP_LOSS_R = -2.0
COOLDOWN_MINUTES = 30

# --- Sesion (UTC) y spread ---
SESSION_START_UTC = 7
SESSION_END_UTC = 20
MAX_SPREAD_POINTS = 50

# Vela M15 ya señalada (el signal_engine evalua cada 15s: sin esto la misma
# vela de rechazo generaria ~60 señales iguales).
_last_signaled_candle = None


def get_box(df_d1):
    """Caja del dia anterior: (techo, piso, linea_media, rango) o None."""
    if df_d1 is None or len(df_d1) < 2:
        return None
    prev_day = df_d1.iloc[-2]  # iloc[-1] es el dia en curso (aun formandose)
    top, bottom = float(prev_day["high"]), float(prev_day["low"])
    size = top - bottom
    if size <= 0:
        return None
    return top, bottom, (top + bottom) / 2, size


def _rejection_candle(candle, direction: str, top: float, bottom: float, size: float) -> bool:
    candle_range = candle["high"] - candle["low"]
    if candle_range <= 0:
        return False
    # Posicion del cierre dentro de la propia vela (0 = minimo, 1 = maximo)
    close_pos = (candle["close"] - candle["low"]) / candle_range

    if direction == "BUY":
        touched_zone = candle["low"] <= bottom + size * EDGE_ZONE_PCT
        return (touched_zone and candle["close"] > candle["open"]
                and candle["close"] > bottom and close_pos >= REJECTION_CLOSE_PCT)
    touched_zone = candle["high"] >= top - size * EDGE_ZONE_PCT
    return (touched_zone and candle["close"] < candle["open"]
            and candle["close"] < top and (1 - close_pos) >= REJECTION_CLOSE_PCT)


def _evaluate_direction(market_data: dict, direction: str, box):
    """Devuelve (score, hard_blocked, rejection_candle)."""
    top, bottom, mid, size = box
    df_d1, df_m15, df_m5 = market_data["D1"], market_data["M15"], market_data["M5"]
    candle = df_m15.iloc[-2]  # ultima M15 CERRADA
    price = float(market_data["M1"]["close"].iloc[-1])

    # Ruptura: cierre M15 fuera de la caja invalida la reversion
    if candle["close"] > top or candle["close"] < bottom:
        return 0, True, None

    # Zona extrema (obligatoria): el precio actual sigue en el lado correcto de la caja
    if direction == "BUY":
        in_zone = bottom < price <= bottom + size * EDGE_ZONE_PCT
    else:
        in_zone = top - size * EDGE_ZONE_PCT <= price < top
    if not in_zone:
        return 0, True, None
    score = 30

    # Vela de rechazo (obligatoria)
    if not _rejection_candle(candle, direction, top, bottom, size):
        return score, True, None
    score += 25

    # Tamaño de caja razonable vs ATR diario
    atr_d1 = atr(df_d1).iloc[-2]
    if atr_d1 == atr_d1 and BOX_MIN_ATR_MULT * atr_d1 <= size <= BOX_MAX_ATR_MULT * atr_d1:
        score += 15

    # Confirmacion M5: BOS a favor de la reversion
    # detect_bos devuelve (bool, nivel) en una version de indicators.py y
    # solo bool en otra: se aceptan ambas.
    try:
        bos_result = detect_bos(df_m5, direction)
    except Exception:
        bos_result = False
    m5_bos = bos_result[0] if isinstance(bos_result, tuple) else bool(bos_result)
    if m5_bos:
        score += 15

    # Spread + sesion (bloqueo absoluto si fallan)
    spread = market_data.get("spread")
    hour = market_data.get("hour_utc")
    spread_ok = spread is None or spread <= MAX_SPREAD_POINTS
    session_ok = hour is None or SESSION_START_UTC <= hour <= SESSION_END_UTC
    if not (spread_ok and session_ok):
        return score, True, None
    score += 15

    return score, False, candle


def evaluate(market_data: dict):
    """
    Punto de entrada llamado por signal_engine.py.
    market_data debe incluir 'D1', 'M15', 'M5', 'M1' (DataFrames) y
    opcionalmente 'spread' y 'hour_utc'. Devuelve un dict de señal o None.
    """
    global _last_signaled_candle

    if any(market_data.get(tf) is None or len(market_data[tf]) < 30 for tf in ("D1", "M15", "M5", "M1")):
        return None

    box = get_box(market_data["D1"])
    if box is None:
        return None
    top, bottom, mid, size = box

    df_m15 = market_data["M15"]
    atr_m15 = atr(df_m15).iloc[-2]
    if atr_m15 != atr_m15:  # NaN
        return None
    buffer = atr_m15 * SL_BUFFER_ATR_M15
    entry = float(market_data["M1"]["close"].iloc[-1])

    for direction in ("BUY", "SELL"):
        score, hard_blocked, candle = _evaluate_direction(market_data, direction, box)
        if hard_blocked or score < MIN_SCORE_TO_TRADE:
            continue

        candle_key = (str(candle["time"]), direction)
        if candle_key == _last_signaled_candle:
            return None

        if direction == "BUY":
            sl = min(bottom, candle["low"]) - buffer
            tp1 = mid
            tp2 = top - size * TP2_MARGIN_PCT
            risk, reward = entry - sl, tp2 - entry
        else:
            sl = max(top, candle["high"]) + buffer
            tp1 = mid
            tp2 = bottom + size * TP2_MARGIN_PCT
            risk, reward = sl - entry, entry - tp2

        if risk <= 0 or reward / risk < MIN_RR_TP2:
            continue

        _last_signaled_candle = candle_key
        return {
            "direction": direction,
            "entry_price": entry,
            "sl": float(sl),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "tp1_close_pct": TP1_CLOSE_PCT,
            "move_to_breakeven_after_tp1": True,
            "breakeven_buffer_points": 2,
            "score": score,
            "box_top": top,
            "box_bottom": bottom,
            "box_mid": mid,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "daily_stop_loss_r": DAILY_STOP_LOSS_R,
            "cooldown_minutes": COOLDOWN_MINUTES,
        }

    return None
