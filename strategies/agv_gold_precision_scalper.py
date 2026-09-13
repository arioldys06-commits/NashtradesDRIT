"""
strategies/agv_gold_precision_scalper.py — NashtradesDRIT

AGV Gold Precision Scalper: sistema hibrido para XAUUSD/GOLD, multi-timeframe
(H1 -> M1), inspirado en:
  - Al Brooks:     regimen de mercado (tendencia/rango) y direccion general
  - Mack (PATS):   segundo intento / retroceso de dos piernas hacia EMA
  - Bob Volman:    ruptura + retesteo, entrada de precision
  - Linda Raschke: filtros de volatilidad/horario

Reglas de compra (venta = las contrarias):
  H1:  precio sobre EMA200 y EMA200 con pendiente positiva
  M30: estructura de maximos y minimos ascendentes (HH/HL)
  M15: BOS alcista confirmado
  M5:  retroceso de dos piernas hacia EMA20 (zona de ruptura/soporte)
  M1:  CHOCH/BOS alcista + vela de impulso cerrando cerca de su maximo
  Entrada: ruptura del maximo de la vela de confirmacion M1

Puntuacion (100 pts, minimo 90 para operar):
  H1 tendencia + EMA200        20
  M30 momentum/estructura      15
  M15 estructura/BOS           20
  M5 retroceso de dos piernas  20
  M1 CHOCH/BOS confirmacion    15
  Spread/volatilidad/horario   10

Bloqueos absolutos (ignoran la puntuacion):
  - Mercado lateral/congestionado en M30 (bias 'none')
  - Spread excesivo
  - Movimiento ya extendido sin retroceso disponible (no hay two_leg_pullback)
  - Contradiccion entre H1, M30 y M15 (bias no coincide en las tres)
  - Score < 90

Nota: el filtro de noticias de alto impacto y el de "otra posicion abierta en
XAUUSD" deben aplicarse en bot_engine.py antes de ejecutar la señal (no se
puede verificar posiciones abiertas ni calendario economico desde una
funcion de evaluate() aislada) — ver TODOs en bot_engine.py.
"""
from strategies.indicators import (
    ema, atr, structure_bias, detect_bos, two_leg_pullback, choch_impulse_candle,
)

STRATEGY_NAME = "AGV_Gold_Precision_Scalper"
MIN_SCORE_TO_TRADE = 90

# --- Gestion de riesgo recomendada por esta estrategia ---
# El motor de ejecucion (bot_engine.py / result_tracker.py) debe leer estos
# campos del signal dict para aplicar la gestion; todavia no estan
# implementados en bot_engine.py (placeholder actualmente).
RISK_PER_TRADE_PCT = 0.5       # recomendado
MAX_RISK_PER_TRADE_PCT = 1.0   # limite duro
TP1_R = 1.0
TP1_CLOSE_PCT = 50
TP2_R_MIN = 1.8
TP2_R_MAX = 2.0
MAX_TRADES_PER_DAY = 3
DAILY_STOP_LOSS_R = -2.0
COOLDOWN_MINUTES = 30

# --- Sesion (Raschke): evitar horas de muy baja liquidez ---
# Horas en UTC. Cubre solape Londres/NY aproximadamente.
SESSION_START_UTC = 7
SESSION_END_UTC = 20

# --- Filtro de spread (placeholder — calibrar con datos reales del broker) ---
MAX_SPREAD_POINTS = 50


def _session_ok(current_hour_utc: int) -> bool:
    return SESSION_START_UTC <= current_hour_utc <= SESSION_END_UTC


def _spread_ok(spread_points) -> bool:
    if spread_points is None:
        return True  # si no se paso el dato, no bloqueamos por esto
    return spread_points <= MAX_SPREAD_POINTS


def _h1_score(df_h1, direction: str) -> int:
    ema200 = ema(df_h1["close"], 200)
    if len(ema200) < 3:
        return 0

    price_above = df_h1["close"].iloc[-1] > ema200.iloc[-1]
    price_below = df_h1["close"].iloc[-1] < ema200.iloc[-1]
    ema_rising = ema200.iloc[-1] > ema200.iloc[-3]
    ema_falling = ema200.iloc[-1] < ema200.iloc[-3]

    if direction == "BUY" and price_above and ema_rising:
        return 20
    if direction == "SELL" and price_below and ema_falling:
        return 20
    return 0


def _evaluate_direction(market_data: dict, direction: str):
    """Evalua todas las condiciones para una direccion (BUY o SELL) y devuelve
    (score, hard_blocked, confirmation_candle, entry_level)."""
    df_h1 = market_data.get("H1")
    df_m30 = market_data.get("M30")
    df_m15 = market_data.get("M15")
    df_m5 = market_data.get("M5")
    df_m1 = market_data.get("M1")

    if any(df is None or len(df) < 30 for df in [df_h1, df_m30, df_m15, df_m5, df_m1]):
        return 0, True, None, None

    score = 0

    # H1 — tendencia + EMA200 (20 pts)
    score += _h1_score(df_h1, direction)

    # M30 — estructura HH/HL o LH/LL (15 pts)
    m30_bias = structure_bias(df_m30)
    expected_bias = "up" if direction == "BUY" else "down"
    if m30_bias == expected_bias:
        score += 15
    elif m30_bias == "none":
        # mercado lateral/congestionado -> bloqueo absoluto
        return score, True, None, None

    # M15 — BOS confirmado (20 pts)
    m15_bos, _ = detect_bos(df_m15, direction)
    if m15_bos:
        score += 20

    # Contradiccion H1/M30/M15: si M30 no confirma la direccion, bloqueo absoluto
    if m30_bias != expected_bias:
        return score, True, None, None

    # M5 — retroceso de dos piernas hacia EMA20 (20 pts)
    pullback_ok = two_leg_pullback(df_m5, direction)
    if pullback_ok:
        score += 20
    else:
        # movimiento ya extendido sin retroceso disponible -> bloqueo absoluto
        return score, True, None, None

    # M1 — CHOCH/BOS + vela de impulso (15 pts)
    choch_ok, confirmation_candle = choch_impulse_candle(df_m1, direction)
    if choch_ok:
        score += 15

    # Spread/volatilidad/horario (10 pts)
    spread_points = market_data.get("spread")
    current_hour_utc = market_data.get("hour_utc")
    spread_ok = _spread_ok(spread_points)
    session_ok = _session_ok(current_hour_utc) if current_hour_utc is not None else True
    if spread_ok and session_ok:
        score += 10

    hard_blocked = not spread_ok  # spread demasiado grande = bloqueo absoluto

    entry_level = None
    if confirmation_candle is not None:
        entry_level = confirmation_candle["high"] if direction == "BUY" else confirmation_candle["low"]

    return score, hard_blocked, confirmation_candle, entry_level


def evaluate(market_data: dict):
    """
    Punto de entrada llamado por signal_engine.py.
    market_data debe incluir, ademas de los DataFrames por timeframe:
      - 'spread': spread actual en puntos (opcional)
      - 'hour_utc': hora actual UTC como entero (opcional, para filtro de sesion)
    Devuelve un dict de señal o None.
    """
    best_signal = None

    for direction in ("BUY", "SELL"):
        score, hard_blocked, confirmation_candle, entry_level = _evaluate_direction(market_data, direction)

        if hard_blocked or score < MIN_SCORE_TO_TRADE or confirmation_candle is None:
            continue

        df_m5 = market_data["M5"]
        atr_m5 = atr(df_m5).iloc[-1]
        if atr_m5 is None or atr_m5 != atr_m5:  # NaN check
            continue

        if direction == "BUY":
            sl = confirmation_candle["low"] - atr_m5 * 0.3
            risk = entry_level - sl
            tp1 = entry_level + risk * TP1_R
            tp2 = entry_level + risk * TP2_R_MAX
        else:
            sl = confirmation_candle["high"] + atr_m5 * 0.3
            risk = sl - entry_level
            tp1 = entry_level - risk * TP1_R
            tp2 = entry_level - risk * TP2_R_MAX

        if risk <= 0:
            continue

        best_signal = {
            "direction": direction,
            "entry_price": entry_level,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_close_pct": TP1_CLOSE_PCT,
            "score": score,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "daily_stop_loss_r": DAILY_STOP_LOSS_R,
            "cooldown_minutes": COOLDOWN_MINUTES,
        }
        break  # una direccion valida es suficiente

    return best_signal
