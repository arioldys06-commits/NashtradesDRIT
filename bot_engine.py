import logging
import math
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from supabase import (
    create_client,
    Client,
)

from config import (
    BOT_LOOP_INTERVAL,
    FIXED_LOT,
    MAGIC_NUMBER,
    MAX_DAILY_LOSSES,
    MAX_TRADES_PER_DAY,
    SUPABASE_KEY,
    SUPABASE_URL,
    SYMBOL,
    SYMBOLS,
)

from mt5_utils import (
    calculate_lot_size,
    calculate_trade_risk_dollars,
    connect_mt5,
    disconnect_mt5,
    has_open_position,
)

from news_engine import (
    is_high_impact_news_nearby,
)

from telegram_utils import send_telegram_message


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

# httpx/httpcore loguean cada request HTTP a Supabase en INFO — deja
# la consola casi ilegible para lo que realmente importa (bloqueos,
# ejecuciones, cierres). Se silencian a WARNING; el logging propio de
# bot_engine sigue en INFO sin cambios.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(
    "bot_engine"
)


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


DEVIATION = 20

MAX_PRICE_DEVIATION_RATIO = 0.30


# ============================================================
# ESTADO
# ============================================================

_daily_date = None

_daily_trade_count = 0
_daily_loss_count = 0
_daily_r_sum = 0.0

_strategy_stats = {}

_last_trade_time = {}

_open_tickets = {}


def _new_strategy_stats():
    return {
        "trades": 0,
        "losses": 0,
        "r_sum": 0.0,
    }


def _get_strategy_stats(
    strategy: str,
):
    if strategy not in _strategy_stats:
        _strategy_stats[
            strategy
        ] = _new_strategy_stats()

    return _strategy_stats[
        strategy
    ]


def _reset_daily_counters_if_needed():
    global _daily_date
    global _daily_trade_count
    global _daily_loss_count
    global _daily_r_sum
    global _strategy_stats
    global _last_trade_time

    today = datetime.now(
        timezone.utc
    ).date()

    if _daily_date == today:
        return

    _daily_date = today

    _daily_trade_count = 0
    _daily_loss_count = 0
    _daily_r_sum = 0.0

    _strategy_stats = {}
    _last_trade_time = {}

    log.info(
        "Contadores diarios reiniciados."
    )


# ============================================================
# FILLING MODE
# ============================================================

def _pick_filling_mode(
    symbol_info,
):
    """
    symbol_info.filling_mode es un BITMASK (FOK=1, IOC=2, BOC=4) — un
    sistema de numeración DISTINTO al enum que se manda en la orden
    (mt5.ORDER_FILLING_FOK=0, mt5.ORDER_FILLING_IOC=1). El paquete
    MetaTrader5 de Python NO expone constantes para el bitmask
    (mt5.SYMBOL_FILLING_FOK no existe — solo las del enum), así que
    los bits se comparan con sus valores numéricos directos.

    BUG (2026-09-22): esta función usaba mt5.SYMBOL_FILLING_FOK /
    mt5.SYMBOL_FILLING_IOC, que no existen — lanzaban AttributeError
    en cuanto llegaba una señal real a ejecutar. Se restauran los
    valores numéricos del bitmask.
    """
    SYMBOL_FILLING_FOK_BIT = 1
    SYMBOL_FILLING_IOC_BIT = 2

    filling = int(
        symbol_info.filling_mode
    )

    if filling & SYMBOL_FILLING_FOK_BIT:
        return mt5.ORDER_FILLING_FOK

    if filling & SYMBOL_FILLING_IOC_BIT:
        return mt5.ORDER_FILLING_IOC

    return mt5.ORDER_FILLING_RETURN


# ============================================================
# VALIDACIÓN DEL PRECIO
# ============================================================

def _price_still_valid(
    signal: dict,
    current_price: float,
) -> bool:

    original_entry = float(
        signal["entry_price"]
    )

    sl = float(
        signal["sl"]
    )

    original_risk = abs(
        original_entry - sl
    )

    if original_risk <= 0:
        return False

    deviation = abs(
        current_price
        - original_entry
    )

    max_deviation = (
        original_risk
        * MAX_PRICE_DEVIATION_RATIO
    )

    return (
        deviation
        <= max_deviation
    )


# ============================================================
# LÍMITES
# ============================================================

def _strategy_can_trade(
    signal: dict,
) -> bool:
    global _daily_trade_count
    global _daily_loss_count

    strategy = signal[
        "strategy"
    ]

    stats = _get_strategy_stats(
        strategy
    )

    # ---------------- GLOBAL ----------------

    if (
        _daily_trade_count
        >= MAX_TRADES_PER_DAY
    ):
        log.info(
            "[BLOCK] Máximo global "
            "de trades alcanzado."
        )
        return False

    if (
        _daily_loss_count
        >= MAX_DAILY_LOSSES
    ):
        log.info(
            "[BLOCK] Máximo global "
            "de pérdidas alcanzado."
        )
        return False

    # ---------------- ESTRATEGIA ----------------

    strategy_max_trades = int(
        signal.get(
            "max_trades_per_day"
        )
        or MAX_TRADES_PER_DAY
    )

    if (
        stats["trades"]
        >= strategy_max_trades
    ):
        log.info(
            "[BLOCK] %s alcanzó "
            "máximo diario.",
            strategy,
        )
        return False

    daily_stop = signal.get(
        "daily_stop_loss_r"
    )

    if daily_stop is not None:
        daily_stop = float(
            daily_stop
        )

        if (
            stats["r_sum"]
            <= daily_stop
        ):
            log.info(
                "[BLOCK] %s alcanzó "
                "stop diario %.2fR.",
                strategy,
                daily_stop,
            )
            return False

    # ---------------- COOLDOWN ----------------

    cooldown_minutes = int(
        signal.get(
            "cooldown_minutes"
        )
        or 0
    )

    last_trade = (
        _last_trade_time.get(
            strategy
        )
    )

    if (
        last_trade is not None
        and cooldown_minutes > 0
    ):
        elapsed = (
            datetime.now(
                timezone.utc
            )
            - last_trade
        ).total_seconds() / 60

        if elapsed < cooldown_minutes:
            log.info(
                "[BLOCK] %s cooldown: "
                "%.1f/%s minutos.",
                strategy,
                elapsed,
                cooldown_minutes,
            )

            return False

    return True


# ============================================================
# ORDEN
# ============================================================

def _send_order(
    signal: dict,
    lot: float,
    risk_pct: float,
):
    symbol = signal["symbol"]

    info = mt5.symbol_info(
        symbol
    )

    tick = mt5.symbol_info_tick(
        symbol
    )

    if info is None or tick is None:
        return None

    direction = (
        signal["direction"]
        .upper()
    )

    if direction == "BUY":
        order_type = (
            mt5.ORDER_TYPE_BUY
        )

        price = float(
            tick.ask
        )

    elif direction == "SELL":
        order_type = (
            mt5.ORDER_TYPE_SELL
        )

        price = float(
            tick.bid
        )

    else:
        log.error("Dirección inválida: %s", direction)
        return None

    if not _price_still_valid(
        signal,
        price,
    ):
        log.info(
            "[BLOCK] Precio se alejó "
            "demasiado de la señal."
        )

        return None

    sl = float(
        signal["sl"]
    )

    tp2 = float(
        signal["tp2"]
    )

    if direction == "BUY" and not (sl < price < tp2):
        log.info("[BLOCK] SL/TP inválidos para BUY.")
        return None

    if direction == "SELL" and not (tp2 < price < sl):
        log.info("[BLOCK] SL/TP inválidos para SELL.")
        return None

    account = mt5.account_info()
    if account is None or float(account.equity) <= 0:
        log.error("[BLOCK] No se pudo leer equity de MT5.")
        return None

    estimated_risk = calculate_trade_risk_dollars(
        symbol, direction, price, sl, lot,
    )
    allowed_risk = float(account.equity) * risk_pct / 100.0
    if estimated_risk is None or estimated_risk > allowed_risk + 1e-6:
        log.info(
            "[BLOCK] Riesgo actual %s excede límite $%.2f.",
            estimated_risk, allowed_risk,
        )
        return None

    request = {
        "action":
            mt5.TRADE_ACTION_DEAL,

        "symbol":
            symbol,

        "volume":
            float(lot),

        "type":
            order_type,

        "price":
            price,

        "sl":
            sl,

        "tp":
            tp2,

        "deviation":
            DEVIATION,

        "magic":
            MAGIC_NUMBER,

        "comment":
            signal["strategy"][:30],

        "type_time":
            mt5.ORDER_TIME_GTC,

        "type_filling":
            _pick_filling_mode(
                info
            ),
    }

    result = mt5.order_send(
        request
    )

    if result is None:
        log.error(
            "order_send() devolvió None: %s",
            mt5.last_error(),
        )
        return None

    if (
        result.retcode
        != mt5.TRADE_RETCODE_DONE
    ):
        log.error(
            "Orden rechazada retcode=%s "
            "comment=%s",
            result.retcode,
            result.comment,
        )

        send_telegram_message(
            f"⚠️ Señal RECHAZADA — NashtradesDRIT\n"
            f"{signal.get('strategy')} {direction} score={signal.get('score')}\n"
            f"Motivo: retcode={result.retcode} {result.comment}"
        )

        return None

    return result, float(result.price or price)


# ============================================================
# IDENTIFICAR POSICIÓN
# ============================================================

def _find_current_position(symbol: str):
    positions = mt5.positions_get(
        symbol=symbol
    )

    if not positions:
        return None

    candidates = [
        p
        for p in positions
        if int(p.magic)
        == int(MAGIC_NUMBER)
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda p: p.time,
    )


# ============================================================
# EJECUTAR SEÑAL
# ============================================================

def execute_signal(
    signal: dict,
):
    global _daily_trade_count

    _reset_daily_counters_if_needed()

    symbol = signal["symbol"]

    if has_open_position(
        symbol,
        MAGIC_NUMBER,
    ):
        log.info(
            "[BLOCK] %s: ya existe "
            "posición abierta.",
            symbol,
        )
        return False

    if not _strategy_can_trade(
        signal
    ):
        return False

    # --------------------------------------------------------
    # NEWS
    # --------------------------------------------------------

    if is_high_impact_news_nearby():
        log.info(
            "[BLOCK] Noticia de "
            "alto impacto cercana."
        )
        return False

    # --------------------------------------------------------
    # LOT SIZE
    # --------------------------------------------------------

    risk_pct = signal.get(
        "risk_per_trade_pct"
    )

    if risk_pct is None:
        log.error(
            "Señal sin risk_per_trade_pct."
        )
        return False

    risk_pct = float(
        risk_pct
    )

    max_risk_pct = signal.get(
        "max_risk_per_trade_pct"
    )

    if max_risk_pct is not None:
        risk_pct = min(
            risk_pct,
            float(max_risk_pct),
        )

    direction = str(signal["direction"]).upper()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or direction not in ("BUY", "SELL"):
        return False

    live_price = float(tick.ask if direction == "BUY" else tick.bid)
    if not _price_still_valid(signal, live_price):
        return False

    lot = calculate_lot_size(
        symbol,
        direction,
        live_price,
        float(signal["sl"]),
        risk_pct,
    )

    if lot is None:
        log.info(
            "[BLOCK] No existe lotaje "
            "válido para el riesgo objetivo."
        )
        return False

    # FIXED_LOT se conserva como techo opcional
    # únicamente si está configurado > 0.
    if FIXED_LOT > 0 and lot > FIXED_LOT:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None or float(symbol_info.volume_step) <= 0:
            return False
        volume_min = float(symbol_info.volume_min)
        volume_step = float(symbol_info.volume_step)
        if FIXED_LOT < volume_min:
            log.info("[BLOCK] FIXED_LOT menor que mínimo del bróker.")
            return False
        steps = math.floor(
            (FIXED_LOT - volume_min) / volume_step + 1e-9
        )
        lot = min(lot, round(volume_min + steps * volume_step, 8))
        if lot < volume_min:
            return False

    # --------------------------------------------------------
    # SEND
    # --------------------------------------------------------

    sent = _send_order(
        signal,
        lot,
        risk_pct,
    )

    if sent is None:
        return False

    result, execution_price = sent

    # --------------------------------------------------------
    # POSICIÓN REAL
    # --------------------------------------------------------

    time.sleep(0.25)

    position = (
        _find_current_position(symbol)
    )

    if position is not None:
        ticket = int(
            position.ticket
        )

        execution_price = float(
            position.price_open
        )

    else:
        # Fallback.
        ticket = int(
            result.order
        )

    risk_dollars = (
        calculate_trade_risk_dollars(
            symbol,
            signal["direction"],
            execution_price,
            float(signal["sl"]),
            lot,
        )
    )

    if risk_dollars is None:
        risk_dollars = 0.0

    strategy = signal[
        "strategy"
    ]

    now = datetime.now(
        timezone.utc
    )

    _open_tickets[ticket] = {
        "symbol":
            symbol,

        "signal_id":
            signal.get("id"),

        "strategy":
            strategy,

        "direction":
            signal["direction"],

        "entry_price":
            execution_price,

        "sl":
            float(signal["sl"]),

        "tp1":
            float(signal["tp1"]),

        "tp2":
            float(signal["tp2"]),

        "lot":
            float(lot),

        "risk_dollars":
            float(risk_dollars),

        "opened_at":
            now,

        "breakeven_done":
            False,

        "move_to_breakeven_after_tp1":
            bool(
                signal.get(
                    "move_to_breakeven_after_tp1",
                    True,
                )
            ),

        "breakeven_buffer_points":
            int(
                signal.get(
                    "breakeven_buffer_points",
                    2,
                )
            ),
    }

    _daily_trade_count += 1

    stats = _get_strategy_stats(
        strategy
    )

    stats["trades"] += 1

    _last_trade_time[
        strategy
    ] = now

    log.info(
        "[EXECUTED] %s %s "
        "ticket=%s lot=%.4f "
        "risk=$%.2f",
        strategy,
        signal["direction"],
        ticket,
        lot,
        risk_dollars,
    )

    send_telegram_message(
        f"✅ Orden EJECUTADA — NashtradesDRIT\n"
        f"Ticket: {ticket}\n"
        f"Estrategia: {strategy}\n"
        f"Dirección: {signal['direction']}\n"
        f"Score: {signal.get('score')}/100\n"
        f"Entrada real: {execution_price:.2f} | SL: {float(signal['sl']):.2f}\n"
        f"TP1: {float(signal['tp1']):.2f} | TP2: {float(signal['tp2']):.2f}\n"
        f"Lote: {lot} | Riesgo estimado: ${risk_dollars:.2f}"
    )

    return True


# ============================================================
# BREAK EVEN
# ============================================================

def move_sl_to_breakeven_if_tp1_hit():
    # Sin filtro de symbol: _open_tickets puede tener posiciones de
    # varios símbolos (GOLD, EURUSD) a la vez.
    positions = mt5.positions_get()

    if not positions:
        return

    positions_by_ticket = {
        int(p.ticket): p
        for p in positions
        if int(p.magic) == int(MAGIC_NUMBER)
    }

    for ticket, info in list(
        _open_tickets.items()
    ):
        if info[
            "breakeven_done"
        ]:
            continue

        if not info.get(
            "move_to_breakeven_after_tp1",
            True,
        ):
            continue

        position = (
            positions_by_ticket.get(
                ticket
            )
        )

        if position is None:
            continue

        symbol = info["symbol"]

        info_symbol = mt5.symbol_info(symbol)
        if info_symbol is None:
            continue

        point = float(info_symbol.point)

        tick = mt5.symbol_info_tick(
            symbol
        )

        if tick is None:
            continue

        direction = info[
            "direction"
        ]

        tp1 = float(
            info["tp1"]
        )

        entry = float(
            info["entry_price"]
        )

        buffer_points = int(
            info[
                "breakeven_buffer_points"
            ]
        )

        buffer_price = (
            buffer_points
            * point
        )

        if direction == "BUY":
            tp1_hit = (
                float(tick.bid)
                >= tp1
            )

            new_sl = (
                entry
                + buffer_price
            )

        else:
            tp1_hit = (
                float(tick.ask)
                <= tp1
            )

            new_sl = (
                entry
                - buffer_price
            )

        if not tp1_hit:
            continue

        request = {
            "action":
                mt5.TRADE_ACTION_SLTP,

            "position":
                int(position.ticket),

            "symbol":
                symbol,

            "sl":
                new_sl,

            "tp":
                float(info["tp2"]),

            "magic":
                MAGIC_NUMBER,
        }

        result = mt5.order_send(
            request
        )

        if (
            result is not None
            and result.retcode
            == mt5.TRADE_RETCODE_DONE
        ):
            info[
                "breakeven_done"
            ] = True

            log.info(
                "[BE] ticket=%s "
                "SL -> %.5f",
                ticket,
                new_sl,
            )

            send_telegram_message(
                f"🔒 Breakeven activado — NashtradesDRIT\n"
                f"Ticket: {ticket} (estrategia: {info.get('strategy')})\n"
                f"TP1 alcanzado ({tp1:.2f}). SL movido a {new_sl:.2f}.\n"
                f"Dejando correr hasta TP2 ({float(info['tp2']):.2f})."
            )


# ============================================================
# GUARDAR TRADE
# ============================================================

def _save_closed_trade(
    ticket: int,
    info: dict,
    profit: float,
    r_multiple: float,
    exit_price,
):
    row = {
        "signal_id":
            info.get(
                "signal_id"
            ),

        "ticket":
            ticket,

        "symbol":
            info.get(
                "symbol",
                SYMBOL,
            ),

        "strategy":
            info.get(
                "strategy"
            ),

        "direction":
            info.get(
                "direction"
            ),

        "origen":
            "BOT",

        "entry_price":
            info.get(
                "entry_price"
            ),

        "exit_price":
            exit_price,

        "sl":
            info.get(
                "sl"
            ),

        "tp":
            info.get(
                "tp2"
            ),

        "profit":
            profit,

        "risk_dollars":
            info.get(
                "risk_dollars"
            ),

        "r_multiple":
            r_multiple,

        "opened_at":
            (
                info["opened_at"]
                .isoformat()
                if info.get(
                    "opened_at"
                )
                else None
            ),

        "closed_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "magic_number":
            MAGIC_NUMBER,
    }

    try:
        (
            supabase
            .table(
                "trades_ejecutados"
            )
            .insert(row)
            .execute()
        )

    except Exception:
        log.exception(
            "No se pudo guardar "
            "trade %s",
            ticket,
        )
        return False

    return True


# ============================================================
# CIERRES
# ============================================================

def check_closed_trades():
    global _daily_loss_count
    global _daily_r_sum

    _reset_daily_counters_if_needed()

    # Sin filtro de symbol: puede haber posiciones abiertas de varios
    # símbolos (GOLD, EURUSD) a la vez.
    positions = mt5.positions_get()

    current_tickets = set()

    if positions:
        current_tickets = {
            int(p.ticket)
            for p in positions
            if int(p.magic)
            == int(MAGIC_NUMBER)
        }

    for ticket, info in list(
        _open_tickets.items()
    ):
        if ticket in current_tickets:
            continue

        try:
            deals = mt5.history_deals_get(
                position=ticket
            )

        except Exception:
            deals = None

        if not deals:
            # El historial puede tardar en actualizarse.
            continue

        profit = sum(
            float(d.profit)
            + float(d.swap)
            + float(d.commission)
            for d in deals
        )

        closing_deals = [
            d
            for d in deals
            if getattr(
                d,
                "entry",
                None,
            )
            == mt5.DEAL_ENTRY_OUT
        ]

        exit_price = None

        if closing_deals:
            last_exit = max(
                closing_deals,
                key=lambda d: d.time_msc,
            )

            exit_price = float(
                last_exit.price
            )

        risk_dollars = float(
            info.get(
                "risk_dollars",
                0,
            )
        )

        if risk_dollars > 0:
            r_multiple = (
                profit
                / risk_dollars
            )
        else:
            r_multiple = 0.0

        strategy = info[
            "strategy"
        ]

        stats = _get_strategy_stats(
            strategy
        )

        saved = _save_closed_trade(
            ticket=ticket,
            info=info,
            profit=profit,
            r_multiple=r_multiple,
            exit_price=exit_price,
        )
        if not saved:
            continue

        stats["r_sum"] += r_multiple
        _daily_r_sum += r_multiple

        if profit < 0:
            stats["losses"] += 1
            _daily_loss_count += 1

        log.info(
            "[CLOSED] %s ticket=%s "
            "P/L=$%.2f R=%.3f "
            "strategy_day_R=%.3f",
            strategy,
            ticket,
            profit,
            r_multiple,
            stats["r_sum"],
        )

        send_telegram_message(
            f"{'🔴' if profit < 0 else '🟢'} Posición cerrada — NashtradesDRIT\n"
            f"Estrategia: {strategy} | Ticket: {ticket}\n"
            f"Resultado: {'PÉRDIDA' if profit < 0 else 'GANANCIA'} (${profit:.2f}, {r_multiple:+.2f}R)\n"
            f"Pérdidas hoy: {_daily_loss_count}/{MAX_DAILY_LOSSES} | R acumulado hoy: {_daily_r_sum:+.2f}"
        )

        del _open_tickets[
            ticket
        ]


# ============================================================
# SIGNALS SUPABASE
# ============================================================

def _get_pending_signals():
    try:
        result = (
            supabase
            .table("signals")
            .select("*")
            .eq(
                "status",
                "PENDING",
            )
            .in_(
                "symbol",
                SYMBOLS,
            )
            .order(
                "created_at"
            )
            .limit(20)
            .execute()
        )

        return (
            result.data
            or []
        )

    except Exception:
        log.exception(
            "Error consultando señales."
        )

        return []


def _update_signal_status(
    signal_id,
    status: str,
):
    if not signal_id:
        return

    try:
        (
            supabase
            .table("signals")
            .update(
                {
                    "status":
                        status
                }
            )
            .eq(
                "id",
                signal_id,
            )
            .execute()
        )

    except Exception:
        log.exception(
            "No se pudo actualizar "
            "signal=%s a %s",
            signal_id,
            status,
        )


def process_pending_signals():
    signals = (
        _get_pending_signals()
    )

    for signal in signals:

        # Evita ejecutar una señal de otro magic
        # si en el futuro se comparte tabla.
        signal_magic = signal.get(
            "magic_number"
        )

        if (
            signal_magic is not None
            and int(signal_magic)
            != int(MAGIC_NUMBER)
        ):
            continue

        executed = execute_signal(
            signal
        )

        if executed:
            _update_signal_status(
                signal["id"],
                "EXECUTED",
            )

            # Solo permitimos una posición
            # simultánea por symbol/magic.
            break


# ============================================================
# MAIN
# ============================================================

def main_loop():
    print(
        "[bot_engine] Iniciado "
        f"para {', '.join(SYMBOLS)}"
    )

    if not connect_mt5(launch_terminal=False):
        raise RuntimeError(
            "No se pudo conectar a MT5."
        )

    send_telegram_message(
        "🟢 NashtradesDRIT — Bot Engine activado, conectado a MT5 y EJECUTANDO ÓRDENES REALES."
    )

    try:
        while True:
            try:
                _reset_daily_counters_if_needed()

                move_sl_to_breakeven_if_tp1_hit()

                check_closed_trades()

                process_pending_signals()

            except Exception:
                log.exception(
                    "Error en ciclo bot_engine."
                )

            time.sleep(
                BOT_LOOP_INTERVAL
            )

    except KeyboardInterrupt:
        print(
            "bot_engine detenido."
        )
        send_telegram_message(
            "🔴 NashtradesDRIT — Bot Engine detenido manualmente."
        )

    finally:
        disconnect_mt5()


if __name__ == "__main__":
    main_loop()