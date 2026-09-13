# NashtradesDRIT

Bot de trading automatizado para XAUUSD (oro), construido desde cero, independiente de TradingProEA.

## Diferencias clave vs TradingProEA
- Cuenta MT5: XMGlobal nueva y distinta (no la 381384791)
- Backend: proyecto Supabase nuevo y separado (no qilvrvnwdtpbkcfwktqs)
- Magic number distinto (ver `config.py`)
- Estrategias: 100% nuevas, aún por definir

## Requisito obligatorio antes de correr el bot
MetaTrader5 (paquete de Python) se conecta a **una sola terminal MT5 a la vez** por proceso.
Como TradingProEA ya usa una terminal con su propia cuenta, este bot necesita:

1. Instalar una **segunda copia de MetaTrader 5** en una carpeta distinta
   (ej. `C:\Program Files\MetaTrader 5 - Nash\terminal64.exe`)
2. Iniciar sesión ahí con la cuenta XMGlobal nueva
3. En `config.py` / `.env`, apuntar `MT5_PATH` a esa terminal específica

Si esto no está hecho, `mt5.initialize()` puede conectar a la terminal equivocada
(la de TradingProEA) sin avisar.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Editar .env con: cuenta MT5 nueva, password, server, path a la terminal, credenciales Supabase nuevas
```

Luego correr el schema en el proyecto Supabase nuevo:
```
supabase/schema.sql
```

## Probar las conexiones (antes de programar estrategias)

Con `.env` ya lleno:

```bash
python test_mt5_connection.py   # verifica que conecta a la terminal/cuenta correcta
python test_telegram.py         # envía un mensaje de prueba al canal
```

`test_mt5_connection.py` alerta si la cuenta conectada no coincide con `MT5_LOGIN`
en `.env` — señal de que tomó la terminal equivocada (ej. la de TradingProEA).

## Estructura

```
NashtradesDRIT/
├── config.py              # Configuración central (lee .env)
├── signal_engine.py       # Generación de señales (orquesta strategies/)
├── bot_engine.py          # Loop de ejecución en MT5
├── result_tracker.py      # Tracking de resultados / winrate
├── sync_trades_supabase.py # Sincroniza trades ejecutados a Supabase
├── backtest_engine.py     # Backtesting sobre datos históricos
├── strategies/            # Estrategias nuevas (vacío, por definir)
├── supabase/schema.sql    # Schema inicial para el proyecto Supabase nuevo
└── START_ALL.bat          # Lanza todos los procesos
```

## Estrategias

### AGV Gold Precision Scalper (`strategies/agv_gold_precision_scalper.py`)
Sistema hibrido multi-timeframe (H1→M1) inspirado en Al Brooks, Mack (PATS),
Bob Volman y Linda Raschke. Score 0-100, exige ≥90 para operar. Ver el
docstring del archivo para las reglas completas de entrada, bloqueos
absolutos y gestion de riesgo (TP1/TP2, breakeven, limite diario, cooldown).

Verificado con datos sinteticos: la logica de deteccion (tendencia H1,
estructura M30, BOS M15, CHOCH+impulso M1) funciona correctamente. Los
umbrales de tolerancia (multiplicador de ATR, periodo de EMA para el
retroceso M5) **todavia no estan calibrados con datos reales** — hacerlo
con `backtest_engine.py` antes de operar en vivo o incluso en demo.

## Próximos pasos
1. Calibrar los umbrales de AGV_Gold_Precision_Scalper con backtesting real
2. Implementar `news_engine.py` (filtro de noticias de alto impacto) — ver TODOs en bot_engine.py
3. Implementar el envio real de ordenes en `execute_signal()` (bot_engine.py)
4. Probar en cuenta demo antes de arriesgar capital real
