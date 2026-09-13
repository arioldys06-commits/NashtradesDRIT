"""
telegram_utils.py — NashtradesDRIT

Helper simple para enviar mensajes al canal de Telegram desde cualquier
proceso (signal_engine.py, bot_engine.py, news_engine.py).
"""
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[telegram_utils] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHANNEL_ID — no se envio el mensaje")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text}

    try:
        response = requests.post(url, data=payload, timeout=10)
        data = response.json()
        if data.get("ok"):
            return True
        print(f"[telegram_utils] Telegram respondio con error: {data.get('description')}")
        return False
    except Exception as e:
        print(f"[telegram_utils] Fallo al enviar mensaje a Telegram: {e}")
        return False
