"""
signal_engine.py — NashtradesDRIT

Orquesta las estrategias en strategies/ y decide si se genera una señal.
"""
from config import ALLOWED_STRATEGIES, MIN_SCORE, SYMBOL
from strategies import agv_gold_precision_scalper

# Cada estrategia nueva se registra aquí una vez creada en strategies/
STRATEGY_REGISTRY = {
    agv_gold_precision_scalper.STRATEGY_NAME: agv_gold_precision_scalper.evaluate,
}


def evaluate_all_strategies(market_data: dict) -> list[dict]:
    """
    Corre todas las estrategias activas (ALLOWED_STRATEGIES) sobre market_data
    y devuelve una lista de señales candidatas (dicts) con score >= MIN_SCORE.

    market_data: estructura con velas M1/M5/M15/M30/H1 ya cargadas
                 (formato a definir junto con la primera estrategia).
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


if __name__ == "__main__":
    print("signal_engine.py listo — sin estrategias registradas todavía.")
