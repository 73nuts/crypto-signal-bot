"""
Payment flow Saga definition.

Flow:
1. verify_payment - Verify payment info (placeholder; actual verification in monitor)
2. activate_membership - Activate membership
3. send_invite - Send invite link
4. notify - Send notification + publish event

Compensations:
- compensate_membership - Roll back membership status
- compensate_invite - Revoke invite (log only)
"""
from typing import Any, Callable, Dict, Optional

from src.core.events import PaymentReceivedEvent
from src.core.idempotency import idempotent
from src.core.message_bus import get_message_bus
from src.core.saga import SagaDefinition, get_orchestrator
from src.core.structured_logger import get_logger

logger = get_logger(__name__)

# Global callback storage (set by payment_monitor)
_on_payment_confirmed_callback: Optional[Callable] = None


def set_payment_confirmed_callback(callback: Callable) -> None:
    """Set payment confirmation callback.

    Args:
        callback: Signature (order_id, telegram_id, plan_code) -> None
    """
    global _on_payment_confirmed_callback
    _on_payment_confirmed_callback = callback
    logger.info("PaymentSaga: payment confirmation callback set")


# ========================================
# Saga step implementations
# ========================================

async def verify_payment(context: Dict[str, Any]) -> Dict[str, Any]:
    """Verify payment info.

    Placeholder step - actual verification is done in payment_monitor._process_transfer.
    This step only logs confirmation that verification passed.

    Args:
        context: Contains order_id, tx_hash, amount, to_address

    Returns:
        Verification result
    """
    order_id = context['order_id']
    tx_hash = context.get('tx_hash', '')

    logger.info(
        f"[Saga] verify_payment: order_id={order_id}, "
        f"tx={tx_hash[:16] if tx_hash else 'N/A'}... (pre-verified)"
    )

    # Verification done in monitor; confirm directly
    return {
        'verified': True,
        'order_id': order_id,
        'tx_hash': tx_hash
    }


async def activate_membership(context: Dict[str, Any]) -> Dict[str, Any]:
    """Activate membership.

    Calls MemberService to activate membership.

    Args:
        context: Contains telegram_id, plan_code, order_id, duration_days, level

    Returns:
        Activation result
    """
    import asyncio

    from src.telegram.services.member_service import MemberService

    telegram_id = context['telegram_id']
    plan_code = context['plan_code']
    order_id = context['order_id']
    duration_days = context.get('duration_days', 30)
    level = context.get('level', 1)

    logger.info(
        f"[Saga] activate_membership: telegram_id={telegram_id}, "
        f"plan={plan_code}, days={duration_days}"
    )

    # MemberService is sync; wrap with to_thread
    member_service = MemberService()
    membership_id = await asyncio.to_thread(
        member_service.activate_or_renew,
        telegram_id=telegram_id,
        membership_type=plan_code,
        duration_days=duration_days,
        level=level,
        order_id=order_id
    )

    if not membership_id:
        raise RuntimeError("Membership activation failed: activate_or_renew returned empty result")

    logger.info(
        f"[Saga] Membership activated: telegram_id={telegram_id}, "
        f"membership_id={membership_id}"
    )

    # Save to context for downstream steps and compensation
    return {
        'activated': True,
        'telegram_id': telegram_id,
        'plan_code': plan_code,
        'membership_id': membership_id
    }


async def compensate_membership(context: Dict[str, Any]) -> None:
    """Compensation: roll back membership activation.

    Membership compensation is complex; uses manual handling strategy:
    - Send alert to admin
    - Log detailed info
    """
    from src.telegram.alert_manager import alert_manager

    telegram_id = context.get('telegram_id')
    order_id = context.get('order_id')
    membership_id = context.get('membership_id')

    logger.warning(
        f"[Saga] Compensate membership activation: telegram_id={telegram_id}, "
        f"order_id={order_id}, membership_id={membership_id}"
    )

    # Send admin alert
    alert_manager.sync_alert_critical(
        f"Saga compensation: membership activation requires manual review\n"
        f"order_id={order_id}\n"
        f"telegram_id={telegram_id}\n"
        f"membership_id={membership_id}"
    )


async def send_invite(context: Dict[str, Any]) -> Dict[str, Any]:
    """Send group invite link.

    Executes on_payment_confirmed callback.

    Args:
        context: Contains telegram_id, plan_code, order_id

    Returns:
        Send result
    """
    import asyncio

    telegram_id = context['telegram_id']
    plan_code = context['plan_code']
    order_id = context['order_id']

    logger.info(f"[Saga] send_invite: telegram_id={telegram_id}")

    # Execute payment confirmation callback (sends invite link, etc.)
    global _on_payment_confirmed_callback
    if _on_payment_confirmed_callback:
        try:
            # Callback is sync; wrap with to_thread
            await asyncio.to_thread(
                _on_payment_confirmed_callback,
                order_id,
                telegram_id,
                plan_code
            )
            logger.info(f"[Saga] Invite link sent: telegram_id={telegram_id}")
        except Exception as e:
            # Invite send failure does not block flow; log warning
            logger.warning(f"[Saga] Invite link send failed: {e}")
    else:
        logger.warning("[Saga] Payment confirmation callback not set, skipping invite send")

    return {
        'sent': True,
        'telegram_id': telegram_id,
        'order_id': order_id
    }


async def compensate_invite(context: Dict[str, Any]) -> None:
    """Compensation: revoke invite.

    Invite links cannot be revoked once sent; log for manual handling.
    """
    telegram_id = context.get('telegram_id')

    logger.warning(f"Compensate invite send: telegram_id={telegram_id} (cannot auto-revoke)")


async def notify_payment_success(context: Dict[str, Any]) -> Dict[str, Any]:
    """Send payment success notification + publish event.

    Publishes PaymentReceivedEvent.

    Args:
        context: Contains telegram_id, order_id, plan_code, tx_hash, amount

    Returns:
        Notification result
    """
    telegram_id = context['telegram_id']
    order_id = context['order_id']
    plan_code = context['plan_code']
    tx_hash = context.get('tx_hash', '')
    amount = context.get('amount', 0.0)

    logger.info(
        f"[Saga] notify_payment_success: "
        f"telegram_id={telegram_id}, order_id={order_id}"
    )

    # Publish payment success event
    message_bus = get_message_bus()
    await message_bus.publish(PaymentReceivedEvent(
        order_id=order_id,
        telegram_id=telegram_id,
        amount=amount,
        tx_hash=tx_hash,
        plan_code=plan_code
    ))

    logger.info(f"[Saga] PaymentReceivedEvent published: order_id={order_id}")

    return {
        'notified': True,
        'order_id': order_id
    }


# ========================================
# Saga definition registration
# ========================================

def register_payment_saga() -> SagaDefinition:
    """Register payment flow Saga.

    Returns:
        SagaDefinition
    """
    saga = SagaDefinition(saga_type="payment", timeout=120)

    saga.add_step(
        name="verify_payment",
        forward=verify_payment,
        compensate=None,  # Verification step needs no compensation
        timeout=30,
        retries=2
    )

    saga.add_step(
        name="activate_membership",
        forward=activate_membership,
        compensate=compensate_membership,
        timeout=30,
        retries=2
    )

    saga.add_step(
        name="send_invite",
        forward=send_invite,
        compensate=compensate_invite,
        timeout=30,
        retries=2
    )

    saga.add_step(
        name="notify",
        forward=notify_payment_success,
        compensate=None,  # Notification step needs no compensation
        timeout=30,
        retries=1
    )

    # Register with orchestrator
    orchestrator = get_orchestrator()
    orchestrator.register(saga)

    logger.info("PaymentSaga registered")
    return saga


# ========================================
# Convenience entry point
# ========================================

@idempotent(
    key_func=lambda order_id, **_: f"payment:{order_id}",
    operation="process_payment",
    ttl_hours=24
)
async def process_payment(
    order_id: str,
    telegram_id: int,
    plan_code: str,
    tx_hash: str = None,
    amount: float = 0.0,
    to_address: str = None,
    duration_days: int = 30,
    level: int = 1
) -> Dict[str, Any]:
    """Process payment (idempotent entry point).

    Args:
        order_id: Order number
        telegram_id: Telegram user ID
        plan_code: Plan code
        tx_hash: Transaction hash
        amount: Payment amount
        to_address: Receiving address
        duration_days: Membership duration (days)
        level: Permission level

    Returns:
        Processing result
    """
    orchestrator = get_orchestrator()

    context = {
        'order_id': order_id,
        'telegram_id': telegram_id,
        'plan_code': plan_code,
        'tx_hash': tx_hash,
        'amount': amount,
        'to_address': to_address,
        'duration_days': duration_days,
        'level': level,
    }

    logger.info(
        f"[Saga] process_payment started: order_id={order_id}, "
        f"telegram_id={telegram_id}, plan={plan_code}"
    )

    result = await orchestrator.execute(
        saga_type="payment",
        context=context,
        idempotency_key=f"payment:{order_id}"
    )

    logger.info(f"[Saga] process_payment complete: order_id={order_id}, result={result}")

    return result
