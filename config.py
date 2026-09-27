"""
Configuración central de NashtradesDRIT.
Lee todo desde .env — no hardcodear credenciales aquí.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Busca el .env en la carpeta del proyecto (junto a este archivo). Tambien
# acepta los nombres que Windows suele poner al descargar: "env" o ".env.txt".
_BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = next((p for p in (_BASE_DIR / ".env", _BASE_DIR / "env", _BASE_DIR / ".env.txt", _BASE_DIR / "env.txt")
                 if p.is_file()), None)
if ENV_FILE is None:
    print(f"[config] ERROR: no se encontro el archivo .env en {_BASE_DIR}")
else:
    load_dotenv(ENV_FILE, override=True)

# --- MT5 ---
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0") or 0)
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# service_role — permisos completos, SOLO backend. Las tablas tienen RLS de
# solo lectura para la anon key, asi que los procesos Python necesitan esta.
# Si .env solo trae SUPABASE_KEY y es la secreta (sb_secret_... / service_role),
# se usa esa para todo el backend.
_env_supabase_key = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or _env_supabase_key
# bot_engine.py / news_engine.py importan SUPABASE_KEY: se usa la service key
# (para poder escribir con RLS activo).
SUPABASE_KEY = SUPABASE_SERVICE_KEY

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Acepta cualquiera de los dos nombres en .env
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_CHANNEL_ID = TELEGRAM_CHAT_ID

# --- News engine (calendario ForexFactory + Alpha Vantage + sesgo Claude) ---
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Timezone ---
LOCAL_TZ = os.getenv("LOCAL_TZ", "America/Santo_Domingo")

# --- Bot identity ---
MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20260901"))
SYMBOL = os.getenv("SYMBOL", "GOLD")
EURUSD_SYMBOL = os.getenv("EURUSD_SYMBOL", "EURUSD")

# --- Estrategias por simbolo ---
# signal_engine.py evalua solo estas; backtest.py tambien las usa.
STRATEGIES_BY_SYMBOL = {
    SYMBOL: [
        "AGV_Gold_Precision_Scalper",
        "EMA_Momentum_Scalper_M5",
        "Impulso_Golden_Zone",
        "Box_Theory",
    ],
    # EURUSD: agregar aqui las estrategias que tenias activas para EURUSD.
    # Vacio = no se generan señales de EURUSD.
    EURUSD_SYMBOL: [],
}
SYMBOLS = list(STRATEGIES_BY_SYMBOL.keys())
ALLOWED_STRATEGIES = sorted({s for names in STRATEGIES_BY_SYMBOL.values() for s in names})

# --- Parámetros de scoring / filtros ---
# Piso general del motor de señales. Cada estrategia exige su propio minimo
# internamente (AGV: 90, Box_Theory: 80).
MIN_SCORE = 75
BOT_LOOP_INTERVAL = 15  # segundos

# --- Protecciones de riesgo globales (bot_engine.py) ---
# Cada señal trae ademas sus propios limites (max_trades_per_day,
# daily_stop_loss_r, cooldown_minutes, risk_per_trade_pct).
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
MAX_DAILY_LOSSES = int(os.getenv("MAX_DAILY_LOSSES", "2"))
# Techo de lote: el lote se calcula por riesgo % y nunca pasa de este valor.
# 0 = sin techo.
FIXED_LOT = float(os.getenv("FIXED_LOT", "0.01"))
MAX_LOSSES_OUTSIDE_KILLZONE = 2
