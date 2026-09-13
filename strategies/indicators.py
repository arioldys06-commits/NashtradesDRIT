"""
strategies/indicators.py — NashtradesDRIT

Utilidades compartidas: EMA, ATR, deteccion de swings y estructura de mercado
(HH/HL, LH/LL, BOS/CHOCH aproximado). Estas son heuristicas razonables para
un v1 — se deben calibrar con backtesting real antes de operar en vivo.

Convencion de datos: cada timeframe es un pandas.DataFrame con columnas
['time', 'open', 'high', 'low', 'close', 'tick_volume'], ordenado
ascendente por tiempo (la vela mas reciente es la ultima fila).
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def find_swing_points(df: pd.DataFrame, left: int = 2, right: int = 2):
    """
    Devuelve (swing_high_idx, swing_low_idx): listas de indices (posicionales)
    donde hay un maximo/minimo local, comparando `left` velas antes y
    `right` velas despues.
    """
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    swing_highs, swing_lows = [], []

    for i in range(left, n - right):
        window_high = highs[i - left:i + right + 1]
        window_low = lows[i - left:i + right + 1]
        if highs[i] == window_high.max() and np.argmax(window_high) == left:
            swing_highs.append(i)
        if lows[i] == window_low.min() and np.argmin(window_low) == left:
            swing_lows.append(i)

    return swing_highs, swing_lows


def structure_bias(df: pd.DataFrame, lookback: int = 40, left: int = 2, right: int = 2) -> str:
    """
    Heuristica de estructura (para M30): compara los ultimos dos swing highs
    y los ultimos dos swing lows dentro de `lookback` velas.
    Devuelve 'up' (HH+HL), 'down' (LH+LL), o 'none' (mixto/insuficiente data).
    """
    recent = df.tail(lookback).reset_index(drop=True)
    swing_highs, swing_lows = find_swing_points(recent, left, right)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "none"

    hh = recent["high"].iloc[swing_highs[-1]] > recent["high"].iloc[swing_highs[-2]]
    hl = recent["low"].iloc[swing_lows[-1]] > recent["low"].iloc[swing_lows[-2]]
    lh = recent["high"].iloc[swing_highs[-1]] < recent["high"].iloc[swing_highs[-2]]
    ll = recent["low"].iloc[swing_lows[-1]] < recent["low"].iloc[swing_lows[-2]]

    if hh and hl:
        return "up"
    if lh and ll:
        return "down"
    return "none"


def detect_bos(df: pd.DataFrame, direction: str, lookback: int = 30, left: int = 2, right: int = 2):
    """
    BOS (Break of Structure) aproximado: la vela mas reciente cierra mas alla
    del ultimo swing high (alcista) o swing low (bajista) previo a esa ruptura.
    Devuelve (bos_confirmado: bool, nivel_roto: float | None).
    """
    recent = df.tail(lookback).reset_index(drop=True)
    swing_highs, swing_lows = find_swing_points(recent, left, right)
    last_close = recent["close"].iloc[-1]

    if direction == "BUY" and swing_highs:
        level = recent["high"].iloc[swing_highs[-1]]
        return last_close > level, level
    if direction == "SELL" and swing_lows:
        level = recent["low"].iloc[swing_lows[-1]]
        return last_close < level, level
    return False, None


def two_leg_pullback(df: pd.DataFrame, direction: str, ema_period: int = 20,
                      lookback: int = 20, tolerance_atr_mult: float = 0.5):
    """
    Heuristica de "segundo intento" (Mack): tras un impulso, el precio
    retrocede en DOS piernas (baja-rebote-baja para compra; sube-caida-sube
    para venta) y se acerca a la EMA de `ema_period`.
    Devuelve True/False.
    """
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 6:
        return False

    ema_series = ema(recent["close"], ema_period)
    atr_series = atr(recent)
    last_atr = atr_series.iloc[-1] if not np.isnan(atr_series.iloc[-1]) else 0
    distance_to_ema = abs(recent["close"].iloc[-1] - ema_series.iloc[-1])

    close_to_ema = distance_to_ema <= max(last_atr * tolerance_atr_mult, 1e-6)

    swing_highs, swing_lows = find_swing_points(recent, left=1, right=1)

    if direction == "BUY":
        # esperamos al menos 2 minimos descendentes seguidos de rebote (dos piernas de bajada)
        has_two_legs = len(swing_lows) >= 2 and recent["low"].iloc[swing_lows[-1]] < recent["low"].iloc[swing_lows[-2]]
    elif direction == "SELL":
        has_two_legs = len(swing_highs) >= 2 and recent["high"].iloc[swing_highs[-1]] > recent["high"].iloc[swing_highs[-2]]
    else:
        return False

    return bool(has_two_legs and close_to_ema)


def choch_impulse_candle(df: pd.DataFrame, direction: str, lookback: int = 15,
                          left: int = 1, right: int = 1, body_ratio_min: float = 0.6):
    """
    CHOCH/BOS en M1 + vela de impulso cerrando cerca de su extremo, tal como
    pide la regla de entrada. Devuelve (confirmado: bool, vela_confirmacion: pd.Series | None).
    """
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 5:
        return False, None

    bos_ok, _ = detect_bos(recent, direction, lookback=lookback, left=left, right=right)
    if not bos_ok:
        return False, None

    last = recent.iloc[-1]
    candle_range = last["high"] - last["low"]
    if candle_range <= 0:
        return False, None

    if direction == "BUY":
        is_bullish = last["close"] > last["open"]
        closes_near_high = (last["high"] - last["close"]) / candle_range <= (1 - body_ratio_min)
        confirmed = is_bullish and closes_near_high
    else:
        is_bearish = last["close"] < last["open"]
        closes_near_low = (last["close"] - last["low"]) / candle_range <= (1 - body_ratio_min)
        confirmed = is_bearish and closes_near_low

    return confirmed, (last if confirmed else None)
