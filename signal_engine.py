"""
signal_engine.py — NashtradesDRIT

Proceso independiente (su propia consola): conecta a MT5, evalua las
estrategias activas contra el mercado en vivo, y cuando encuentra una señal
valida la guarda en la tabla `signals` de Supabase con status='PENDING'.

bot_engine.py (proceso separado, otra consola) hace polling a esa tabla y
decide si ejecuta o bloquea cada señal — separacion igual a la de
TradingProEA (permite en el futuro correr signal_engine en una maquina y
bot_engine en otra, cada una con su propio MT5, apuntando al mismo Supabase).
"""
import time

from supabase import create_client

from config import ALLOWED_STRATEGIES, MIN_SCORE, SYMBOL, SUPABASE_URL, SUPABASE_KEY, MAGIC_NUMBER
from strategies import agv_gold_precision_scalper
from mt5_utils import connect_mt5, get_market_data

SIGNAL_LOOP_INTERVAL = 15  # segundos

# Cada estrategia nueva se registra aquí una vez creada en strategies/
STRATEGY_REGISTRY = {
    agv_gold_precision_scalper.STRATEGY_NAME: agv_gold_precision_scalper.evaluate,
}

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def evaluate_all_strategies(market_data: dict) -> list[dict]:
    """
    Corre todas las estrategias activas (ALLOWED_STRATEGIES) sobre market_data
    y devuelve una lista de señales candidatas (dicts) con score >= MIN_SCORE.
    """
    candidate_signals = []

    for strategy_name in ALLOWED_STRATEGIES:
        strategy_fn = STRATEGY_REGISTRY.get(strategy_name)
        if strategy_fn is None:
            continue

        result = strategy_fn(market_data)
        if result and result.get("score", 0) >= MIN_SCORE:
            result["strategy"] = strategy_name
            result["symbol"] = SYMBOL
            candidate_signals.append(result)

    return candidate_signals


def publish_signal(signal: dict):
    """Guarda la señal en Supabase (tabla signals) para que bot_engine.py la procese."""
    row = {
        "symbol": signal.get("symbol", SYMBOL),
        "strategy": signal.get("strategy"),
        "direction": signal.get("direction"),
        "entry_price": signal.get("entry_price"),
        "sl": signal.get("sl"),
        "tp1": signal.get("tp1"),
        "tp2": signal.get("tp2"),
        "score": signal.get("score"),
        "status": "PENDING",
        "magic_number": MAGIC_NUMBER,
    }
    try:
        result = supabase.table("signals").insert(row).execute()
        signal_id = result.data[0]["id"] if result.data else None
        print(f"[SEÑAL PUBLICADA] id={signal_id} {signal['direction']} score={signal['score']} "
              f"entry={signal['entry_price']:.2f} sl={signal['sl']:.2f} tp1={signal['tp1']:.2f} tp2={signal['tp2']:.2f}")
    except Exception as e:
        print(f"[ERROR] No se pudo publicar la señal en Supabase: {e}")


def main_loop():
    if not connect_mt5(process_name="signal_engine"):
        return

    print(f"[signal_engine] Estrategias activas: {ALLOWED_STRATEGIES}")

    try:
        while True:
            market_data = get_market_data()

            if market_data:
                last_time = market_data["M1"]["time"].iloc[-1]
                print(f"[signal_engine] Ciclo OK — ultima M1: {last_time}")

                signals = evaluate_all_strategies(market_data)
                if not signals:
                    print("[signal_engine] Sin señal (score < umbral o bloqueo de estructura)")
                for signal in signals:
                    publish_signal(signal)
            else:
                print("[signal_engine] No se pudieron cargar velas este ciclo")

            time.sleep(SIGNAL_LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("signal_engine detenido manualmente.")


if __name__ == "__main__":
    main_loop()
