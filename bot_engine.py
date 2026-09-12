"""
bot_engine.py — NashtradesDRIT

Loop principal: conecta a MT5, pide señales a signal_engine, ejecuta y gestiona posiciones.
Esqueleto de arquitectura — sin lógica de gestión de riesgo todavía (se define con las estrategias).
"""
import time
import MetaTrader5 as mt5

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    MAGIC_NUMBER, SYMBOL, FIXED_LOT, BOT_LOOP_INTERVAL,
)
from signal_engine import evaluate_all_strategies


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


def get_market_data() -> dict:
    """Placeholder — cargar velas M1/M5/M15/M30/H1 de SYMBOL desde MT5."""
    return {}


def execute_signal(signal: dict):
    """Placeholder — enviar orden a MT5 usando FIXED_LOT y MAGIC_NUMBER."""
    print(f"[SEÑAL] {signal}")


def main_loop():
    if not connect_mt5():
        return

    try:
        while True:
            market_data = get_market_data()
            signals = evaluate_all_strategies(market_data)

            for signal in signals:
                execute_signal(signal)

            time.sleep(BOT_LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("Bot detenido manualmente.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main_loop()
