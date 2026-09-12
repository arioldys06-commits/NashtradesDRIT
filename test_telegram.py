"""
test_telegram.py — NashtradesDRIT

Verifica que el bot de Telegram y el canal configurados en .env funcionan,
enviando un mensaje de prueba.

Correr en Windows, con .env ya lleno:
    python test_telegram.py
"""
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[FALLO] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHANNEL_ID en .env")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": "✅ NashtradesDRIT — conexión de prueba exitosa.",
    }

    print("Enviando mensaje de prueba a Telegram...")
    response = requests.post(url, data=payload, timeout=10)
    data = response.json()

    if data.get("ok"):
        chat = data["result"]["chat"]
        print("[OK] Mensaje enviado correctamente.")
        print(f"  Canal: {chat.get('title', chat.get('id'))}")
    else:
        print(f"[FALLO] Telegram respondió con error: {data.get('description')}")
        print("Revisa: ¿el bot fue agregado como administrador del canal?")
        print("        ¿TELEGRAM_CHANNEL_ID tiene el formato correcto (ej. -100XXXXXXXXXX)?")


if __name__ == "__main__":
    main()
