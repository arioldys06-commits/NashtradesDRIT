"""
test_mt5_connection.py — NashtradesDRIT

Verifica que la terminal MT5 correcta (la de la cuenta nueva de Nash,
NO la de TradingProEA) conecta bien antes de programar nada más.

Correr en Windows, con .env ya lleno:
    python test_mt5_connection.py
"""
import MetaTrader5 as mt5
from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, SYMBOL


def main():
    print("Conectando a MT5...")
    print(f"  Path esperado : {MT5_PATH}")
    print(f"  Login esperado: {MT5_LOGIN}")
    print(f"  Server        : {MT5_SERVER}")
    print()

    # portable=True evita que se cierre otra instancia de MT5 ya corriendo (ej. TradingProEA)
    if not mt5.initialize(path=MT5_PATH, login=MT5_LOGIN, password=MT5_PASSWORD,
                           server=MT5_SERVER, portable=True):
        print(f"[FALLO] mt5.initialize() no pudo conectar. Error: {mt5.last_error()}")
        print("Revisa: ¿la terminal en MT5_PATH existe? ¿está cerrada la de TradingProEA")
        print("        para no confundir cuál instancia toma el proceso?")
        return

    account = mt5.account_info()
    if account is None:
        print(f"[FALLO] No se pudo leer account_info(). Error: {mt5.last_error()}")
        mt5.shutdown()
        return

    print("[OK] Conectado.")
    print(f"  Cuenta conectada : {account.login}")
    print(f"  Nombre           : {account.name}")
    print(f"  Broker           : {account.company}")
    print(f"  Server           : {account.server}")
    print(f"  Balance          : {account.balance} {account.currency}")

    if account.login != MT5_LOGIN:
        print()
        print("[ALERTA] La cuenta conectada NO coincide con MT5_LOGIN en tu .env.")
        print("         Es probable que haya tomado la terminal de TradingProEA por error.")
        print("         Verifica MT5_PATH y que esa terminal esté abierta con esta cuenta.")

    print()
    print(f"Probando datos de mercado para {SYMBOL}...")
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print(f"[FALLO] No se pudo obtener tick de {SYMBOL}. "
              f"¿Está el símbolo visible en Market Watch de esta terminal?")
    else:
        print(f"[OK] {SYMBOL} — bid: {tick.bid}  ask: {tick.ask}")

    mt5.shutdown()
    print()
    print("Prueba finalizada.")


if __name__ == "__main__":
    main()
