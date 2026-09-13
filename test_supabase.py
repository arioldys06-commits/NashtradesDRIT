"""
test_supabase.py — NashtradesDRIT

Verifica que la conexion al proyecto Supabase nuevo funciona,
insertando y leyendo un registro de prueba en la tabla signals.

Correr en Windows, con .env ya lleno:
    python test_supabase.py
"""
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[FALLO] Falta SUPABASE_URL o SUPABASE_SERVICE_KEY en .env")
        return

    print(f"Conectando a Supabase: {SUPABASE_URL}")
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    test_signal = {
        "symbol": "GOLD",
        "strategy": "test_connection",
        "direction": "BUY",
        "entry_price": 4348.75,
        "score": 100,
        "status": "TEST",
    }

    print("Insertando señal de prueba...")
    insert_result = client.table("signals").insert(test_signal).execute()

    if not insert_result.data:
        print("[FALLO] No se pudo insertar el registro de prueba.")
        return

    inserted_id = insert_result.data[0]["id"]
    print(f"[OK] Insertado con id: {inserted_id}")

    print("Leyendo de vuelta...")
    read_result = client.table("signals").select("*").eq("id", inserted_id).execute()

    if read_result.data:
        print(f"[OK] Leido correctamente: {read_result.data[0]}")
    else:
        print("[FALLO] Se inserto pero no se pudo leer de vuelta.")
        return

    print("Borrando el registro de prueba...")
    client.table("signals").delete().eq("id", inserted_id).execute()
    print("[OK] Registro de prueba eliminado. Conexion a Supabase funcionando de punta a punta.")


if __name__ == "__main__":
    main()
