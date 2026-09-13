"""
Configuración central de NashtradesDRIT.
Lee todo desde .env — no hardcodear credenciales aquí.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- MT5 ---
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # anon/publishable — solo lectura (RLS), usar en frontend
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")  # service_role — permisos completos, SOLO backend

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

# --- News engine (calendario ForexFactory + Alpha Vantage + sesgo Claude) ---
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Timezone ---
LOCAL_TZ = os.getenv("LOCAL_TZ", "America/Santo_Domingo")

# --- Bot identity ---
MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20260901"))
SYMBOL = os.getenv("SYMBOL", "GOLD")

# --- Estrategias activas (se va llenando a medida que se definan) ---
ALLOWED_STRATEGIES = [
    "AGV_Gold_Precision_Scalper",
]

# --- Parámetros de scoring / filtros ---
# Piso general del motor de señales. AGV_Gold_Precision_Scalper exige 90/100
# internamente (ver strategies/agv_gold_precision_scalper.py) — este MIN_SCORE
# es solo el filtro global que aplica a cualquier estrategia futura.
MIN_SCORE = 75
BOT_LOOP_INTERVAL = 15  # segundos

# --- Protecciones de riesgo (placeholders, calibrar con backtests) ---
FIXED_LOT = 0.01
MAX_LOSSES_OUTSIDE_KILLZONE = 2
