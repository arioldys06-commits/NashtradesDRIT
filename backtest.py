"""
backtest.py — NashtradesDRIT

Motor de backtesting. Descarga historial REAL de velas desde MT5 (la
misma terminal que ya usas para operar en vivo) y corre las estrategias
registradas —usando exactamente evaluate() de cada una, sin reimplementar
nada— vela por vela, simulando la ejecución de SL/TP1(parcial)/breakeven/
TP2 para medir winrate, R promedio, R total y drawdown.

USO:
    python backtest.py --symbol GOLD --months 6
    python backtest.py --symbol EURUSD --months 6
    python backtest.py --all --months 6

Requiere MT5 abierto y logueado manualmente (igual que signal_engine.py).

LIMITACIONES A TENER EN CUENTA AL LEER LOS RESULTADOS:
  - El spread se aproxima con un valor CONSTANTE por símbolo (no hay
    historial de spread real disponible vía copy_rates). El spread real
    se ensancha en noticias/rollover — el backtest es ciego a eso.
  - No simula slippage de ejecución (entra exactamente al precio
    calculado por la estrategia).
  - Los resultados están en múltiplos de R (riesgo normalizado), NO en
    dólares — el tamaño de posición real depende del cálculo dinámico
    de lote que solo existe en vivo (calculate_lot_size), no aquí.
  - Si al descargar falta historial profundo de algún timeframe, puede
    hacer falta abrir ese gráfico en MT5 y desplazarlo hacia atrás una
    vez (mismo comportamiento que ya vimos en vivo).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from config import STRATEGIES_BY_SYMBOL, EURUSD_SYMBOL, SYMBOL
from mt5_utils import connect_mt5, disconnect_mt5, TIMEFRAMES
from strategies import (
    agv_gold_precision_scalper,
    ema_momentum_scalper_m5,
    impulso_golden_zone,
    box_theory,
)

STRATEGY_REGISTRY = {
    agv_gold_precision_scalper.STRATEGY_NAME: agv_gold_precision_scalper,
    ema_momentum_scalper_m5.STRATEGY_NAME: ema_momentum_scalper_m5,
    impulso_golden_zone.STRATEGY_NAME: impulso_golden_zone,
    box_theory.STRATEGY_NAME: box_theory,
}

# Timeframe donde cada estrategia CONFIRMA su señal (igual que
# _signal_timeframe en signal_engine.py — solo hace falta re-evaluar la
# estrategia cuando cierra una vela de este timeframe).
STRATEGY_CONFIRM_TF = {
    "AGV_Gold_Precision_Scalper": "M1",
    "EMA_Momentum_Scalper_M5": "M5",
    "Impulso_Golden_Zone": "M15",
    "Box_Theory": "M15",
}

# Spread aproximado (en puntos) por símbolo — no hay historial de spread
# real disponible, se usa un valor constante conservador.
ASSUMED_SPREAD_POINTS = {
    "GOLD": 25,
    "EURUSD": 12,
}

MAX_BARS = 300  # mismo tamaño de ventana que usa get_market_data() en vivo
DATA_DIR = "backtest_data"


# ============================================================
# DESCARGA / CACHÉ DE HISTORIAL
# ============================================================

def _cache_path(symbol: str, tf_name: str) -> str:
    safe_symbol = symbol.replace("/", "_")
    return os.path.join(DATA_DIR, f"{safe_symbol}_{tf_name}.csv")


def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close", "tick_volume"]].reset_index(drop=True)


def download_history(symbol: str, months: int, force: bool = False) -> dict:
    """Descarga (o carga de caché) las velas de los 5 timeframes para un
    símbolo, cubriendo los últimos `months` meses."""
    os.makedirs(DATA_DIR, exist_ok=True)

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=months * 30)

    history = {}

    for tf_name, tf_value in TIMEFRAMES.items():
        cache_file = _cache_path(symbol, tf_name)

        if not force and os.path.exists(cache_file):
            df = pd.read_csv(cache_file, parse_dates=["time"])
            df["time"] = pd.to_datetime(df["time"], utc=True)
            print(f"[cache] {symbol} {tf_name}: {len(df)} velas")
            history[tf_name] = df
            continue

        rates = mt5.copy_rates_range(symbol, tf_value, date_from, date_to)

        if rates is None or len(rates) == 0:
            print(
                f"[WARN] Sin datos para {symbol} {tf_name}. Si esto se "
                f"repite, abre el gráfico de {symbol} en {tf_name} en MT5 "
                f"y desplázalo hacia atrás para forzar la descarga del "
                f"historial, luego reintenta."
            )
            history[tf_name] = pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "tick_volume"]
            )
            continue

        df = _rates_to_df(rates)
        df.to_csv(cache_file, index=False)
        print(f"[descargado] {symbol} {tf_name}: {len(df)} velas")
        history[tf_name] = df

    return history


# ============================================================
# CONSTRUCCIÓN DE market_data COMO EN VIVO
# ============================================================

def _slice_as_of(df: pd.DataFrame, as_of_time, max_bars: int = MAX_BARS) -> pd.DataFrame:
    """Devuelve las últimas `max_bars` velas con time <= as_of_time —
    exactamente lo que vería get_market_data() en vivo en ese instante."""
    idx = df["time"].searchsorted(as_of_time, side="right")
    start = max(0, idx - max_bars)
    return df.iloc[start:idx].reset_index(drop=True)


def build_market_data(history: dict, as_of_time, symbol: str) -> dict:
    market_data = {}
    for tf_name, df in history.items():
        market_data[tf_name] = _slice_as_of(df, as_of_time)

    market_data["spread"] = ASSUMED_SPREAD_POINTS.get(symbol, 20)
    market_data["hour_utc"] = as_of_time.hour
    return market_data


# ============================================================
# GENERACIÓN DE SEÑALES CANDIDATAS POR ESTRATEGIA
# ============================================================

def generate_candidates(symbol: str, strategy_name: str, history: dict) -> list:
    """Recorre el timeframe de confirmación de la estrategia bar a bar,
    llamando a evaluate() con las velas que habrían estado disponibles en
    ese momento. Devuelve una lista de señales candidatas con su hora de
    entrada."""
    strategy = STRATEGY_REGISTRY[strategy_name]
    confirm_tf = STRATEGY_CONFIRM_TF[strategy_name]
    confirm_df = history.get(confirm_tf)

    if confirm_df is None or len(confirm_df) < 60:
        return []

    candidates = []
    warmup = 60  # mínimo de velas antes de empezar a evaluar
    total = len(confirm_df)

    for i in range(warmup, total):
        if (i - warmup) % 5000 == 0 and i > warmup:
            pct = (i - warmup) / (total - warmup) * 100
            print(f"    ...{strategy_name}: {i - warmup}/{total - warmup} velas procesadas ({pct:.0f}%)")

        as_of_time = confirm_df["time"].iloc[i]

        market_data = build_market_data(history, as_of_time, symbol)

        try:
            signal = strategy.evaluate(market_data)
        except Exception as e:
            print(f"[ERROR] {strategy_name} en {as_of_time}: {e}")
            continue

        if not signal:
            continue

        signal["strategy"] = strategy_name
        signal["symbol"] = symbol
        signal["entry_time"] = as_of_time
        candidates.append(signal)

    return candidates


# ============================================================
# SIMULACIÓN DE LA OPERACIÓN (SL / TP1 parcial / breakeven / TP2)
# ============================================================

def _first_touch(df_m1: pd.DataFrame, level: float, direction: str, kind: str):
    """Devuelve la posición (entera) de la primera vela M1 donde el
    precio toca `level`, o None si nunca se toca en el rango dado.
    kind='sl' o 'tp' determina si se compara con low o high."""
    if direction == "BUY":
        series = df_m1["low"] if kind == "sl" else df_m1["high"]
        touched = series <= level if kind == "sl" else series >= level
    else:
        series = df_m1["high"] if kind == "sl" else df_m1["low"]
        touched = series >= level if kind == "sl" else series <= level

    hits = np.flatnonzero(touched.values)
    if len(hits) == 0:
        return None
    return int(hits[0])


def simulate_trade(signal: dict, m1_from_entry: pd.DataFrame) -> dict:
    direction = signal["direction"]
    entry = float(signal["entry_price"])
    sl = float(signal["sl"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    tp1_close_pct = float(signal.get("tp1_close_pct", 100)) / 100.0
    move_to_be = bool(signal.get("move_to_breakeven_after_tp1", True))
    be_buffer_points = float(signal.get("breakeven_buffer_points", 0))

    risk = abs(entry - sl)
    if risk <= 0 or len(m1_from_entry) == 0:
        return {"resolved": False}

    # --- Fase 1: hasta TP1 o SL original ---
    sl_hit_1 = _first_touch(m1_from_entry, sl, direction, "sl")
    tp1_hit = _first_touch(m1_from_entry, tp1, direction, "tp")

    if sl_hit_1 is not None and (tp1_hit is None or sl_hit_1 <= tp1_hit):
        # SL tocado antes (o en la misma vela) que TP1 -> conservador: SL primero
        return {"resolved": True, "r_multiple": -1.0, "exit_reason": "SL"}

    if tp1_hit is None:
        return {"resolved": False}  # nunca se resolvió dentro del historial

    r_tp1 = abs(tp1 - entry) / risk

    if tp1_close_pct >= 0.999:
        return {"resolved": True, "r_multiple": r_tp1, "exit_reason": "TP1_FULL"}

    # --- Fase 2: remanente, desde la vela de TP1 en adelante ---
    remainder = m1_from_entry.iloc[tp1_hit:].reset_index(drop=True)

    if not move_to_be:
        stop_level_2 = sl
    else:
        # point-size aproximado: usamos la distancia de riesgo / 10000
        # como proxy razonable si no tenemos symbol_info aquí.
        point_estimate = risk / 5000.0
        buffer_price = be_buffer_points * point_estimate
        stop_level_2 = entry + buffer_price if direction == "BUY" else entry - buffer_price

    sl2_hit = _first_touch(remainder, stop_level_2, direction, "sl")
    tp2_hit = _first_touch(remainder, tp2, direction, "tp")

    if sl2_hit is not None and (tp2_hit is None or sl2_hit <= tp2_hit):
        r_stop2 = (
            (stop_level_2 - entry) / risk if direction == "BUY"
            else (entry - stop_level_2) / risk
        )
        r_final = tp1_close_pct * r_tp1 + (1 - tp1_close_pct) * r_stop2
        return {"resolved": True, "r_multiple": r_final, "exit_reason": "BE_OR_SL2"}

    if tp2_hit is None:
        return {"resolved": False}

    r_tp2 = (
        (tp2 - entry) / risk if direction == "BUY"
        else (entry - tp2) / risk
    )
    r_final = tp1_close_pct * r_tp1 + (1 - tp1_close_pct) * r_tp2
    return {"resolved": True, "r_multiple": r_final, "exit_reason": "TP2"}


# ============================================================
# BACKTEST COMPLETO DE UN SÍMBOLO (respeta límites de bot_engine)
# ============================================================

def run_backtest_for_symbol(symbol: str, months: int) -> dict:
    print(f"\n{'=' * 60}\nBacktest {symbol} — últimos {months} meses\n{'=' * 60}")

    history = download_history(symbol, months)
    m1_df = history.get("M1")

    if m1_df is None or len(m1_df) < 100:
        print(f"[ERROR] Historial M1 insuficiente para {symbol}, se omite.")
        return {"symbol": symbol, "trades": []}

    strategy_names = STRATEGIES_BY_SYMBOL.get(symbol, [])

    all_candidates = []
    for strategy_name in strategy_names:
        strategy_module = STRATEGY_REGISTRY[strategy_name]
        candidates = generate_candidates(symbol, strategy_name, history)
        print(f"  {strategy_name}: {len(candidates)} señales candidatas (antes de límites)")
        all_candidates.extend(candidates)

    all_candidates.sort(key=lambda s: s["entry_time"])

    # --- Simular respetando: 1 posición por símbolo a la vez, cooldown
    # y límites diarios POR ESTRATEGIA (igual que bot_engine.py) ---
    trades = []
    skipped_no_m1_coverage = 0
    symbol_position_open_until = None
    last_trade_time = {}
    daily_trade_count = {}
    daily_r_sum = {}
    current_day = None

    for signal in all_candidates:
        entry_time = signal["entry_time"]
        strategy_name = signal["strategy"]

        day = entry_time.date()
        if day != current_day:
            current_day = day
            daily_trade_count = {}
            daily_r_sum = {}

        if symbol_position_open_until is not None and entry_time < symbol_position_open_until:
            continue  # ya hay una posición abierta en este símbolo

        cooldown_min = float(signal.get("cooldown_minutes", 0) or 0)
        last_t = last_trade_time.get(strategy_name)
        if last_t is not None and (entry_time - last_t).total_seconds() / 60 < cooldown_min:
            continue

        max_trades = int(signal.get("max_trades_per_day", 99) or 99)
        if daily_trade_count.get(strategy_name, 0) >= max_trades:
            continue

        daily_stop = signal.get("daily_stop_loss_r")
        if daily_stop is not None and daily_r_sum.get(strategy_name, 0.0) <= float(daily_stop):
            continue

        m1_from_entry = m1_df[m1_df["time"] >= entry_time].reset_index(drop=True)

        # Si el historial M1 no cubre la fecha de esta señal (M1 suele
        # tener mucho MENOS profundidad que M15/H1 por límite del bróker),
        # el filtro >= devolvería TODO el M1 disponible — una simulación
        # con velas de un período totalmente distinto. Se descarta en vez
        # de inventar un resultado.
        if len(m1_from_entry) == 0:
            skipped_no_m1_coverage += 1
            continue
        gap_minutes = (m1_from_entry["time"].iloc[0] - entry_time).total_seconds() / 60
        if gap_minutes > 5:
            skipped_no_m1_coverage += 1
            continue

        result = simulate_trade(signal, m1_from_entry)

        if not result.get("resolved"):
            continue

        r = result["r_multiple"]

        trades.append({
            "strategy": strategy_name,
            "direction": signal["direction"],
            "entry_time": entry_time,
            "score": signal.get("score"),
            "r_multiple": r,
            "exit_reason": result["exit_reason"],
        })

        last_trade_time[strategy_name] = entry_time
        daily_trade_count[strategy_name] = daily_trade_count.get(strategy_name, 0) + 1
        daily_r_sum[strategy_name] = daily_r_sum.get(strategy_name, 0.0) + r

        # Estimar duración de la posición para bloquear nuevas señales del
        # símbolo mientras esta sigue abierta (aprox: hasta que se resolvió).
        exit_idx_guess = min(len(m1_from_entry) - 1, 2000)
        symbol_position_open_until = entry_time + timedelta(minutes=exit_idx_guess)

    if skipped_no_m1_coverage > 0:
        print(
            f"\n  [AVISO] {skipped_no_m1_coverage} señales descartadas por "
            f"quedar fuera del rango cubierto por el historial M1 "
            f"(M1 suele tener MENOS profundidad que los timeframes "
            f"superiores). Si el número es alto respecto al total de "
            f"candidatas, los resultados solo reflejan el período reciente "
            f"donde sí hay M1 real — no los 6 meses completos."
        )

    return {"symbol": symbol, "trades": trades}


# ============================================================
# REPORTE
# ============================================================

def print_report(result: dict):
    trades = result["trades"]
    symbol = result["symbol"]

    if not trades:
        print(f"\n{symbol}: 0 operaciones simuladas en el período.")
        return

    df = pd.DataFrame(trades)

    print(f"\n--- Resultado {symbol} (todas las estrategias) ---")
    _print_stats_block(df)

    for strategy_name, group in df.groupby("strategy"):
        print(f"\n  · {strategy_name}")
        _print_stats_block(group, indent="    ")

    out_path = os.path.join(DATA_DIR, f"trades_{symbol}.csv")
    df.to_csv(out_path, index=False)
    print(f"\n  Log de operaciones guardado en: {out_path}")


def _print_stats_block(df: pd.DataFrame, indent: str = "  "):
    n = len(df)
    wins = (df["r_multiple"] > 0).sum()
    winrate = wins / n * 100 if n else 0
    avg_r = df["r_multiple"].mean()
    total_r = df["r_multiple"].sum()

    equity_curve = df["r_multiple"].cumsum()
    running_max = equity_curve.cummax()
    drawdown = equity_curve - running_max
    max_dd = drawdown.min()

    print(f"{indent}Operaciones: {n}")
    print(f"{indent}Winrate: {winrate:.1f}%")
    print(f"{indent}R promedio: {avg_r:.3f}")
    print(f"{indent}R total: {total_r:.2f}")
    print(f"{indent}Máximo drawdown: {max_dd:.2f}R")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Backtest NashtradesDRIT")
    parser.add_argument("--symbol", type=str, help="Símbolo específico (GOLD o EURUSD)")
    parser.add_argument("--all", action="store_true", help="Correr todos los símbolos configurados")
    parser.add_argument("--months", type=int, default=6, help="Meses de historial hacia atrás")
    args = parser.parse_args()

    if not args.symbol and not args.all:
        print("Especifica --symbol GOLD|EURUSD o --all")
        sys.exit(1)

    if not connect_mt5(launch_terminal=False):
        print("[ERROR] No se pudo conectar a MT5. ¿Está abierto y logueado manualmente?")
        sys.exit(1)

    try:
        symbols = list(STRATEGIES_BY_SYMBOL.keys()) if args.all else [args.symbol]

        for symbol in symbols:
            result = run_backtest_for_symbol(symbol, args.months)
            print_report(result)

    finally:
        disconnect_mt5()


if __name__ == "__main__":
    main()