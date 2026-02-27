"""
Trading flow Saga definition.

Flow:
1. validate_signal - Validate trading signal (check positions, risk controls)
2. create_order - Create trading order (delegate to Executor)
3. update_position - Verify position record (confirm DB consistency)
4. notify - Send trade notification

Compensations:
- compensate_order - Cancel order / close position
- compensate_position - Roll back position record
"""

import logging
from typing import Dict, Any

from src.core.saga import SagaDefinition, get_orchestrator
from src.core.idempotency import idempotent

logger = logging.getLogger(__name__)


# ========================================
# Executor singleton
# ========================================

_executor_instance = None
_executor_testnet = None


def _get_executor(testnet: bool = True):
    """Get SwingExecutor singleton.

    Args:
        testnet: Whether to use testnet

    Returns:
        SwingExecutor instance
    """
    global _executor_instance, _executor_testnet

    # Re-create if testnet flag changes
    if _executor_instance is None or _executor_testnet != testnet:
        from src.strategies.swing.services.executor import SwingExecutor

        _executor_instance = SwingExecutor(testnet=testnet)
        _executor_testnet = testnet
        logger.info(f"SwingExecutor initialized: testnet={testnet}")

    return _executor_instance


def _get_position_manager():
    """Get PositionManager instance."""
    from src.trading.position_manager import PositionManager

    return PositionManager()


# ========================================
# Saga step implementations
# ========================================


async def validate_signal(context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate trading signal.

    Checks:
    1. Required parameters present
    2. No existing position (prevent duplicate entry)
    3. ATR is valid

    Args:
        context: Contains symbol, side, signal_type, entry_price, atr

    Returns:
        Validation result

    Raises:
        ValueError: Validation failed
    """
    symbol = context.get("symbol")
    side = context.get("side")
    signal_type = context.get("signal_type", "ENTRY")
    entry_price = context.get("entry_price")
    atr = context.get("atr")
    testnet = context.get("testnet", True)

    logger.info(f"[Saga] Validate signal: symbol={symbol}, side={side}, type={signal_type}")

    # 1. Required parameter check
    if not symbol:
        raise ValueError("Missing symbol parameter")
    if not entry_price:
        raise ValueError("Missing entry_price parameter")
    if signal_type == "ENTRY" and not atr:
        raise ValueError("Entry signal missing atr parameter")

    # 2. Check for existing position (entry signals only)
    if signal_type == "ENTRY":
        executor = _get_executor(testnet)
        existing_position = executor.get_position(symbol)
        if existing_position:
            status = existing_position.get("status")
            if status in ("OPEN", "PENDING"):
                raise ValueError(f"[{symbol}] Existing {status} position, skipping entry")

    return {"valid": True, "symbol": symbol, "side": side, "signal_type": signal_type}


async def create_order(context: Dict[str, Any]) -> Dict[str, Any]:
    """Create trading order - delegate to Executor.

    Entry: calls executor.execute_entry() or execute_limit_entry()
    Exit: calls executor.execute_exit()

    Args:
        context: Contains symbol, entry_price, atr, strategy_name, signal_type

    Returns:
        Order and position info

    Raises:
        RuntimeError: Order execution failed
    """
    symbol = context["symbol"]
    entry_price = context.get("entry_price")
    atr = context.get("atr")
    strategy_name = context.get("strategy_name", "saga")
    signal_type = context.get("signal_type", "ENTRY")
    use_limit = context.get("use_limit_order", False)  # Default: market order
    testnet = context.get("testnet", True)

    executor = _get_executor(testnet)

    if signal_type == "ENTRY":
        logger.info(
            f"[Saga] Create entry order: symbol={symbol}, price={entry_price}, atr={atr}"
        )

        if use_limit:
            result = executor.execute_limit_entry(
                symbol=symbol, price=entry_price, atr=atr, strategy_name=strategy_name
            )
        else:
            result = executor.execute_entry(
                symbol=symbol, price=entry_price, atr=atr, strategy_name=strategy_name
            )

        if not result:
            raise RuntimeError(f"[{symbol}] Entry order execution failed")

        return {
            "order_id": result.get("order_id"),
            "position_id": result.get("position_id"),
            "price": result.get("price") or result.get("limit_price"),
            "quantity": result.get("quantity"),
            "stop_loss": result.get("stop_loss"),
            "leverage": result.get("leverage"),
            "symbol": symbol,
            "status": "CREATED",
        }

    else:  # EXIT
        exit_price = context.get("exit_price") or entry_price
        exit_reason = context.get("exit_reason", "SAGA_EXIT")

        logger.info(f"[Saga] Create exit order: symbol={symbol}, price={exit_price}")

        result = executor.execute_exit(
            symbol=symbol, price=exit_price, reason=exit_reason
        )

        if not result:
            raise RuntimeError(f"[{symbol}] Exit order execution failed")

        return {
            "order_id": result.get("order_id"),
            "position_id": result.get("position_id"),
            "price": result.get("exit_price"),
            "pnl": result.get("pnl"),
            "symbol": symbol,
            "status": "CLOSED",
        }


async def compensate_order(context: Dict[str, Any]) -> None:
    """Compensation: cancel order or reverse close position.

    Cases:
    - PENDING: Limit order not filled -> cancel order
    - OPEN: Already filled -> reverse close position
    """
    symbol = context.get("symbol")
    testnet = context.get("testnet", True)
    order_result = context.get("step_results", {}).get("create_order", {})
    position_id = order_result.get("position_id")

    logger.warning(f"[Saga] Compensate order: symbol={symbol}, position_id={position_id}")

    if not position_id:
        logger.info(f"[{symbol}] Compensate: no position_id, order may not have been created")
        return

    executor = _get_executor(testnet)
    position = executor.get_position(symbol)

    if not position:
        logger.info(f"[{symbol}] Compensate: position no longer exists")
        return

    status = position.get("status")

    if status == "PENDING":
        # Limit order not filled -> cancel + clear record
        logger.info(f"[{symbol}] Compensate: cancel PENDING order")
        pending_order_id = position.get("pending_order_id")
        if pending_order_id and symbol in executor._clients:
            try:
                executor._clients[symbol].cancel_order(pending_order_id)
                logger.info(f"[{symbol}] Order cancelled: {pending_order_id}")
            except Exception as e:
                logger.warning(f"[{symbol}] Cancel order failed: {e}")

        # Cancel position record
        pm = _get_position_manager()
        pm.cancel_pending_position(position_id, "SAGA_COMPENSATE")

    elif status in ("OPEN", "PARTIAL_CLOSED"):
        # Already filled -> reverse close position
        logger.info(f"[{symbol}] Compensate: reverse close position")
        try:
            # Use entry price as exit price (PnL ~0 in compensation scenario)
            exit_price = float(position.get("entry_price", 0))
            executor.execute_exit(
                symbol=symbol, price=exit_price, reason="SAGA_COMPENSATE"
            )
            logger.info(f"[{symbol}] Compensation close completed")
        except Exception as e:
            logger.error(f"[{symbol}] Compensation close failed: {e}")
            raise

    logger.warning(f"[{symbol}] Order compensation complete")


async def update_position(context: Dict[str, Any]) -> Dict[str, Any]:
    """Verify position record.

    The create_order step already created the position record via Executor;
    this step only validates it.

    Args:
        context: Contains symbol, step_results

    Returns:
        Position verification result
    """
    symbol = context["symbol"]
    order_result = context.get("step_results", {}).get("create_order", {})
    position_id = order_result.get("position_id")

    logger.info(f"[Saga] Verify position: symbol={symbol}, position_id={position_id}")

    if not position_id:
        # create_order should have returned position_id; raise if missing
        raise RuntimeError(f"[{symbol}] create_order did not return position_id")

    # Verify position exists
    pm = _get_position_manager()
    position = pm.get_position_by_id(position_id)

    if not position:
        raise RuntimeError(f"[{symbol}] Position record not found: {position_id}")

    return {
        "position_id": position_id,
        "symbol": symbol,
        "status": position.get("status"),
        "entry_price": float(position.get("entry_price", 0)),
        "verified": True,
    }


async def compensate_position(context: Dict[str, Any]) -> None:
    """Compensation: roll back position record.

    If compensate_order already handled the close, ensures DB state is consistent.
    """
    symbol = context.get("symbol")
    position_result = context.get("step_results", {}).get("update_position", {})
    position_id = position_result.get("position_id")

    logger.warning(f"[Saga] Compensate position record: symbol={symbol}, position_id={position_id}")

    if not position_id:
        logger.info(f"[{symbol}] Compensate: no position_id, skipping")
        return

    pm = _get_position_manager()
    position = pm.get_position_by_id(position_id)

    if position and position.get("status") not in ("CLOSED", "CANCELLED"):
        pm.cancel_pending_position(position_id, "SAGA_COMPENSATE_POSITION")
        logger.warning(f"[{symbol}] Position record rolled back: position_id={position_id}")


async def notify_trade(context: Dict[str, Any]) -> Dict[str, Any]:
    """Send trade notification.

    Note: Actual notifications are handled by handlers.py via PositionOpenedEvent.
    This step only logs.

    Args:
        context: Contains symbol, side, step_results

    Returns:
        Notification result
    """
    symbol = context["symbol"]
    signal_type = context.get("signal_type", "ENTRY")
    order_result = context.get("step_results", {}).get("create_order", {})

    price = order_result.get("price", 0)
    position_id = order_result.get("position_id")

    logger.info(
        f"[Saga] Trade complete notification: symbol={symbol}, type={signal_type}, "
        f"price={price}, position_id={position_id}"
    )

    # Notification handled via event-driven flow; return success
    return {"notified": True, "position_id": position_id, "price": price}


# ========================================
# Saga definition registration
# ========================================

_saga_registered = False


def register_trading_saga() -> SagaDefinition:
    """Register trading flow Saga.

    Returns:
        SagaDefinition
    """
    global _saga_registered

    if _saga_registered:
        logger.debug("TradingSaga already registered, skipping")
        return None

    saga = SagaDefinition(saga_type="trading", timeout=120)

    saga.add_step(
        name="validate_signal",
        forward=validate_signal,
        compensate=None,  # Validation step needs no compensation
        timeout=10,
        retries=1,
    )

    saga.add_step(
        name="create_order",
        forward=create_order,
        compensate=compensate_order,
        timeout=60,  # Order placement may take time
        retries=2,
    )

    saga.add_step(
        name="update_position",
        forward=update_position,
        compensate=compensate_position,
        timeout=15,
        retries=2,
    )

    saga.add_step(
        name="notify",
        forward=notify_trade,
        compensate=None,  # Notification step needs no compensation
        timeout=15,
        retries=1,
    )

    # Register with orchestrator
    orchestrator = get_orchestrator()
    orchestrator.register(saga)

    _saga_registered = True
    logger.info("TradingSaga registered")
    return saga


# ========================================
# Convenience entry point
# ========================================


@idempotent(
    key_func=lambda symbol, signal_id, **_: f"trade:{symbol}:{signal_id}",
    operation="execute_trade",
    ttl_hours=24,
)
async def execute_trade(
    symbol: str,
    signal_id: str,
    side: str = "LONG",
    signal_type: str = "ENTRY",
    entry_price: float = None,
    atr: float = None,
    strategy_name: str = None,
    stop_price: float = None,
    quantity: float = None,
    use_limit_order: bool = False,
    testnet: bool = True,
) -> Dict[str, Any]:
    """Execute trade (idempotent entry point).

    Orchestrates the complete trade flow via Saga, including:
    - Signal validation
    - Order creation (with retry)
    - Position recording
    - Notification sending

    On failure, automatically compensates (cancel order / close position).

    Args:
        symbol: Trading pair (BTC, ETH, etc.)
        signal_id: Signal ID for idempotency (e.g. "BTC:20260115")
        side: Direction (LONG/SHORT)
        signal_type: Signal type (ENTRY/EXIT)
        entry_price: Entry price
        atr: ATR value (required for entry; used to calculate SL and position size)
        strategy_name: Strategy name
        stop_price: Stop-loss price (optional; default calculated from ATR)
        quantity: Quantity (optional; default calculated by risk management)
        use_limit_order: Whether to use limit order (default: market order)
        testnet: Whether to use testnet

    Returns:
        Execution result containing position_id, order_id, price, etc.

    Raises:
        ValueError: Parameter validation failed
        RuntimeError: Execution failed (compensation triggered)
    """
    # Ensure Saga is registered
    register_trading_saga()

    orchestrator = get_orchestrator()

    context = {
        "symbol": symbol,
        "signal_id": signal_id,
        "side": side,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "atr": atr,
        "strategy_name": strategy_name or "saga",
        "stop_price": stop_price,
        "quantity": quantity,
        "use_limit_order": use_limit_order,
        "testnet": testnet,
    }

    logger.info(f"[Saga] Starting trade execution: symbol={symbol}, signal_id={signal_id}")

    result = await orchestrator.execute(
        saga_type="trading",
        context=context,
        idempotency_key=f"trade:{symbol}:{signal_id}",
    )

    logger.info(f"[Saga] Trade execution complete: symbol={symbol}, result={result}")

    return result
