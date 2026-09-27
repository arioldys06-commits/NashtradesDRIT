"""
signal_engine.py — NashtradesDRIT

Proceso independiente (su propia consola): se conecta a la MT5 ya abierta,
evalua las estrategias de cada simbolo (config.STRATEGIES_BY_SYMBOL) y, cuando
encuentra una señal valida, la guarda en la tabla `signals` de Supabase con
status='PENDING'. bot_engine.py (otra consola) la ejecuta o la bloquea.

Deduplicacion: cada señal lleva un signal_key (hash de simbolo + estrategia +
direccion + vela del timeframe de confirmacion). La tabla tiene un indice
UNIQUE sobre signal_key, asi que la misma señal no se publica dos veces
aunque el loop corra cada 15s.
"""
import hashlib
import importlib
import time

from supabase import create_client

from config import (
    STRATEGIES_BY_SYMBOL, MIN_SCORE, SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_KEY, MAGIC_NUMBER,
)
from mt5_utils import connect_mt5, get_market_data
from telegram_utils import send_telegram_message
from heartbeat_utils import send_heartbeat

SIGNAL_LOOP_INTERVAL = 15  # segundos

# modulo en strategies/ -> timeframe donde la estrategia confirma su señal
# (igual que STRATEGY_CONFIRM_TF en backtest.py)
STRATEGY_MODULES = {
    "agv_gold_precision_scalper": "M1",
    "ema_momentum_scalper_m5": "M5",
    "impulso_golden_zone": "M15",
    "box_theory": "M15",
}

# Campos de la señal que se guardan en Supabase (bot_engine.py los lee)
SIGNAL_FIELDS = [
    "entry_price", "sl", "tp1", "tp2", "score",
    "risk_per_trade_pct", "max_risk_per_trade_pct", "max_trades_per_day",
    "daily_stop_loss_r", "cooldown_minutes", "breakeven_buffer_points",
    "move_to_breakeven_after_tp1",
]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)


def _load_strategies():
    """Importa cada modulo de strategies/. Si falta uno, avisa y sigue con los demas."""
    registry = {}
    for module_name, confirm_tf in STRATEGY_MODULES.items():
        try:
            module = importlib.import_module(f"strategies.{module_name}")
        except ImportError as e:
            print(f"[WARN] No se pudo cargar strategies/{module_name}.py: {e}")
            continue
        registry[module.STRATEGY_NAME] = (module.evaluate, confirm_tf)
    return registry


STRATEGY_REGISTRY = _load_strategies()


def _to_plain(value):
    """numpy/pandas -> tipos nativos para que Supabase pueda serializarlos."""
    if hasattr(value, "item"):
        return value.item()
    return value


def _signal_key(symbol: str, strategy: str, direction: str, candle_time) -> str:
    raw = f"{MAGIC_NUMBER}|{symbol}|{strategy}|{direction}|{candle_time}"
    return hashlib.sha256(raw.encode()).hexdigest()


def evaluate_symbol(symbol: str, market_data: dict) -> list[dict]:
    candidate_signals = []

    for strategy_name in STRATEGIES_BY_SYMBOL.get(symbol, []):
        entry = STRATEGY_REGISTRY.get(strategy_name)
        if entry is None:
            continue
        strategy_fn, confirm_tf = entry

        try:
            result = strategy_fn(market_data)
        except Exception as e:
            print(f"[ERROR] {strategy_name} ({symbol}) fallo al evaluar: {e}")
            continue

        if result and result.get("score", 0) >= MIN_SCORE:
            result["strategy"] = strategy_name
            result["symbol"] = symbol
            candle_time = market_data[confirm_tf]["time"].iloc[-1]
            result["signal_key"] = _signal_key(symbol, strategy_name, result["direction"], candle_time)
            candidate_signals.append(result)

    return candidate_signals


def publish_signal(signal: dict):
    """Guarda la señal en Supabase (tabla signals) para que bot_engine.py la procese."""
    row = {
        "symbol": signal["symbol"],
        "strategy": signal["strategy"],
        "direction": signal["direction"],
        "status": "PENDING",
        "magic_number": MAGIC_NUMBER,
        "signal_key": signal["signal_key"],
    }
    for field in SIGNAL_FIELDS:
        if signal.get(field) is not None:
            row[field] = _to_plain(signal[field])

    try:
        result = supabase.table("signals").insert(row).execute()
    except Exception as e:
        if "duplicate key" in str(e) or "23505" in str(e):
            return  # ya publicada en un ciclo anterior
        print(f"[ERROR] No se pudo publicar la señal en Supabase: {e}")
        return

    signal_id = result.data[0]["id"] if result.data else None
    print(f"[SEÑAL PUBLICADA] id={signal_id} {signal['symbol']} {signal['strategy']} "
          f"{signal['direction']} score={signal['score']} entry={signal['entry_price']:.5g} "
          f"sl={signal['sl']:.5g} tp1={signal['tp1']:.5g} tp2={signal['tp2']:.5g}")
    send_telegram_message(
        f"📊 Nueva señal NashtradesDRIT\n"
        f"Símbolo: {signal['symbol']}\n"
        f"Estrategia: {signal['strategy']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Score: {signal['score']}/100\n"
        f"Entrada: {signal['entry_price']:.5g}\n"
        f"SL: {signal['sl']:.5g}\n"
        f"TP1: {signal['tp1']:.5g} | TP2: {signal['tp2']:.5g}"
    )


def main_loop():
    if not connect_mt5(process_name="signal_engine", launch_terminal=False):
        return

    active = {s: [n for n in names if n in STRATEGY_REGISTRY] for s, names in STRATEGIES_BY_SYMBOL.items()}
    print(f"[signal_engine] Estrategias activas: {active}")
    send_telegram_message("🟢 NashtradesDRIT — Signal Engine activado y conectado a MT5.")

    try:
        while True:
            last_candles = {}
            for symbol, names in active.items():
                if not names:
                    continue
                market_data = get_market_data(symbol)
                if not market_data:
                    print(f"[signal_engine] {symbol}: no se pudieron cargar velas este ciclo")
                    continue

                last_candles[symbol] = str(market_data["M1"]["time"].iloc[-1])
                for signal in evaluate_symbol(symbol, market_data):
                    publish_signal(signal)

            if last_candles:
                print(f"[signal_engine] Ciclo OK — ultima M1: {last_candles}")
                send_heartbeat("signal_engine", {"last_candle": last_candles})

            time.sleep(SIGNAL_LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("signal_engine detenido manualmente.")
        send_telegram_message("🔴 NashtradesDRIT — Signal Engine detenido manualmente.")


if __name__ == "__main__":
    main_loop()
