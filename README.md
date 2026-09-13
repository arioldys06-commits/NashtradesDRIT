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
├── mt5_utils.py           # Conexion MT5 y carga de velas compartida
├── signal_engine.py       # Proceso 1: evalua estrategias, publica señales a Supabase
├── bot_engine.py          # Proceso 2: polling a señales pendientes, ejecuta/bloquea
├── news_engine.py         # Proceso 3: calendario + noticias + sesgo Claude
├── result_tracker.py      # Tracking de resultados / winrate (pendiente)
├── sync_trades_supabase.py # Sincroniza trades ejecutados a Supabase (pendiente)
├── backtest_engine.py     # Backtesting sobre datos historicos (pendiente)
├── strategies/            # Estrategias y utilidades de indicadores
├── supabase/schema.sql    # Schema del proyecto Supabase
└── START_ALL.bat          # Abre las 3 consolas (signal_engine, bot_engine, news_engine)
```

## Arquitectura (igual a TradingProEA)
`signal_engine.py` y `bot_engine.py` corren como procesos **separados**, cada
uno en su propia consola, comunicandose a traves de la tabla `signals` de
Supabase (no llamadas directas en memoria). Esto permite en el futuro correr
signal_engine en una maquina y bot_engine en otra, cada una con su propio MT5.

- `signal_engine.py`: conecta a MT5, evalua `AGV_Gold_Precision_Scalper` cada
  15s, y si hay señal valida la inserta en `signals` con status='PENDING'.
- `bot_engine.py`: conecta a MT5 (su propia sesion), hace polling a `signals`
  cada 15s, aplica los bloqueos (posicion abierta, limite diario, cooldown,
  noticias via `news_engine.is_high_impact_news_nearby()`), y marca cada
  señal como EXECUTED o BLOCKED_* — el envio real de la orden a MT5 sigue
  pendiente (placeholder) hasta validar con backtesting.
- `news_engine.py`: corre en loop propio (cada 20 min), jala calendario +
  noticias, evalua con Claude, y guarda en `news_events`.

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
