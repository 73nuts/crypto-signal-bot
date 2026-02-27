"""
Admin Router - aiogram 3.x.

Commands:
- /admin: stats dashboard
- /add_vip <user_id> <days>: manually grant VIP
- /remove_vip <user_id>: manually revoke VIP
- /fix_order <order_id>: fix orphan order
- /pool_status: address pool status
- /user_info <id>: user info query
- /referral_pending: pending Trader applications
- /referral_approve <uid>: approve Trader
- /referral_reject <uid>: reject Trader
- /referral_stats: Trader statistics

Security:
- All commands use AdminFilter
- Non-admins are silently rejected
"""

from datetime import datetime, timedelta
from typing import Callable, Optional

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.core.structured_logger import get_logger
from src.telegram.config.constants import DEFAULT_VIP_EXTENSION_DAYS, MAX_VIP_EXTENSION_DAYS

from ..filters.admin import AdminFilter
from ..utils.async_wrapper import run_sync

logger = get_logger(__name__)

router = Router(name="admin")

MAX_VIP_DAYS = MAX_VIP_EXTENSION_DAYS
DEFAULT_VIP_DAYS = DEFAULT_VIP_EXTENSION_DAYS


# ========================================
# Sync DAO wrapper functions
# ========================================

def _get_stats() -> dict:
    """Sync: fetch stats."""
    from ..utils.db_provider import get_db
    db = get_db()

    def count_records(sql):
        try:
            result = db.execute_query(sql)
            if result and isinstance(result, list) and len(result) > 0:
                return result[0].get('cnt', 0)
            return 0
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return 0

    return {
        'total_members': count_records("SELECT COUNT(*) as cnt FROM memberships"),
        'active_members': count_records(
            "SELECT COUNT(*) as cnt FROM memberships WHERE status = 'ACTIVE' AND expire_date > NOW()"
        ),
        'today_new': count_records(
            "SELECT COUNT(*) as cnt FROM memberships WHERE DATE(created_at) = CURDATE()"
        ),
        'expiring_24h': count_records("""
            SELECT COUNT(*) as cnt FROM memberships
            WHERE status = 'ACTIVE'
            AND expire_date > NOW()
            AND expire_date <= DATE_ADD(NOW(), INTERVAL 24 HOUR)
        """)
    }


def _find_by_username(username: str) -> Optional[dict]:
    """Sync: find by username."""
    from ..utils.db_provider import get_member_service
    return get_member_service().repository.find_by_username(username)


def _find_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """Sync: find by telegram_id."""
    from ..utils.db_provider import get_member_service
    return get_member_service().repository.find_by_telegram_id(telegram_id)


def _activate_or_renew(
    telegram_id: int,
    membership_type: str,
    duration_days: int,
    level: int,
    order_id: str,
    telegram_username: Optional[str] = None
) -> Optional[int]:
    """Sync: activate or renew membership."""
    from ..utils.db_provider import get_member_service
    return get_member_service().activate_or_renew(
        telegram_id=telegram_id,
        membership_type=membership_type,
        duration_days=duration_days,
        level=level,
        order_id=order_id,
        telegram_username=telegram_username
    )


def _log_audit_event(
    operation: str,
    telegram_id: int,
    operator: str,
    order_id: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    details: Optional[dict] = None
) -> None:
    """Sync: log audit event."""
    from src.telegram.database.audit_dao import AuditOperation

    from ..utils.db_provider import get_audit_dao

    op_enum = getattr(AuditOperation, operation, AuditOperation.ADMIN_ADD_VIP)
    get_audit_dao().log_event(
        operation=op_enum,
        telegram_id=telegram_id,
        operator=operator,
        order_id=order_id,
        old_status=old_status,
        new_status=new_status,
        details=details
    )


def _force_expire_membership(telegram_id: int) -> bool:
    """Sync: force expire membership."""
    from ..utils.db_provider import get_member_service
    return get_member_service().force_expire_membership(telegram_id)


def _get_pending_traders() -> list:
    """Sync: get pending trader applications."""
    from ..utils.db_provider import get_member_service
    return get_member_service().get_pending_traders()


def _find_by_binance_uid(uid: str) -> Optional[dict]:
    """Sync: find by Binance UID."""
    from ..utils.db_provider import get_member_service
    return get_member_service().repository.find_by_binance_uid(uid)


def _approve_trader(telegram_id: int) -> bool:
    """Sync: approve trader application."""
    from ..utils.db_provider import get_member_service
    return get_member_service().approve_trader(telegram_id)


def _reject_trader(telegram_id: int) -> bool:
    """Sync: reject trader application."""
    from ..utils.db_provider import get_member_service
    return get_member_service().reject_trader(telegram_id)


def _get_referral_stats_extended() -> dict:
    """Sync: get referral stats."""
    from ..utils.db_provider import get_member_service
    return get_member_service().get_referral_stats_extended()


def _get_order_by_id(order_id: str) -> Optional[dict]:
    """Sync: get order by ID."""
    from src.telegram.database import OrderDAO

    from ..utils.db_provider import get_db
    db = get_db()
    dao = OrderDAO(db)
    return dao.get_order_by_id(order_id)


async def _kick_user(bot: Bot, telegram_id: int) -> tuple:
    """Kick user from all VIP groups."""
    from src.telegram.access_controller import AccessController
    try:
        controller = AccessController(bot)
        await controller.kick_user(telegram_id)
        return True, "kicked from group"
    except Exception as e:
        return False, str(e)


# ========================================
# Admin commands
# ========================================

@router.message(Command("admin"), AdminFilter())
async def cmd_admin(message: Message, lang: str, t: Callable) -> None:
    """/admin: show admin dashboard with stats."""
    admin_id = message.from_user.id
    logger.info(f"/admin: admin_id={admin_id}")

    try:
        stats = await run_sync(_get_stats)

        response = (
            f"<b>{t('admin.dashboard_title')}</b>\n\n"
            f"<b>{t('admin.stats_title')}</b>\n"
            f"  - {t('admin.total_members')}: {stats['total_members']}\n"
            f"  - {t('admin.active_members')}: {stats['active_members']}\n"
            f"  - {t('admin.today_new')}: {stats['today_new']}\n"
            f"  - {t('admin.expiring_24h')}: {stats['expiring_24h']}\n\n"
            f"<b>{t('admin.quick_commands')}</b>\n"
            f"<code>/add_vip user_id 30</code> - {t('admin.add_vip_hint')}\n"
            f"<code>/remove_vip user_id</code> - {t('admin.remove_vip_hint')}"
        )

        await message.answer(response, parse_mode='HTML')

    except Exception as e:
        logger.error(f"/admin failed: {e}", exc_info=True)
        await message.answer(t('admin.query_failed'))


@router.message(Command("add_vip"), AdminFilter())
async def cmd_add_vip(message: Message, command: CommandObject, lang: str, t: Callable) -> None:
    """/add_vip <user_id|@username> <days>: manually grant VIP."""
    admin_id = message.from_user.id
    args = command.args.split() if command.args else []
    logger.info(f"/add_vip: admin_id={admin_id}, args={args}")

    if len(args) < 1:
        await message.answer(t('admin.add_vip_usage'), parse_mode='HTML')
        return

    user_input = args[0]
    target_user_id = None
    target_username = None

    if user_input.startswith('@'):
        target_username = user_input.lstrip('@')
        existing = await run_sync(_find_by_username, target_username)

        if existing:
            target_user_id = existing['telegram_id']
        else:
            await message.answer(
                t('admin.user_not_found', username=target_username),
                parse_mode='HTML'
            )
            return
    else:
        try:
            target_user_id = int(user_input)
        except ValueError:
            await message.answer(t('admin.invalid_user_id'), parse_mode='HTML')
            return

    days = DEFAULT_VIP_DAYS
    if len(args) >= 2:
        try:
            days = int(args[1])
            if days < 1 or days > MAX_VIP_DAYS:
                await message.answer(t('admin.days_range_error', max=MAX_VIP_DAYS))
                return
        except ValueError:
            await message.answer(t('admin.days_must_be_number'))
            return

    try:
        admin_order_id = f"ADMIN_MANUAL_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        existing = await run_sync(_find_by_telegram_id, target_user_id)
        if existing and existing.get('status') == 'ACTIVE':
            action = t('admin.action_renew')
        else:
            action = t('admin.action_grant')

        member_id = await run_sync(
            _activate_or_renew,
            target_user_id,
            'ADMIN_GRANT',
            days,
            2,  # Premium level
            admin_order_id,
            target_username
        )
        success = member_id is not None
        new_expire = datetime.now() + timedelta(days=days)

        if success:
            await run_sync(
                _log_audit_event,
                'ADMIN_ADD_VIP',
                target_user_id,
                f'admin:{admin_id}',
                admin_order_id,
                None,
                'ACTIVE',
                {'days': days, 'action': action}
            )

            expire_str = new_expire.strftime('%Y-%m-%d %H:%M')
            await message.answer(
                t('admin.vip_success', action=action, days=days,
                  user_id=target_user_id, expire=expire_str),
                parse_mode='HTML'
            )
            logger.info(f"VIP {action}: user={target_user_id}, days={days}")
        else:
            await message.answer(t('admin.operation_failed'))

    except Exception as e:
        logger.error(f"/add_vip failed: {e}", exc_info=True)
        await message.answer(f"{t('admin.operation_failed')}: {e}")


@router.message(Command("remove_vip"), AdminFilter())
async def cmd_remove_vip(message: Message, command: CommandObject, lang: str, t: Callable, bot: Bot) -> None:
    """/remove_vip <user_id|@username>: manually revoke VIP and kick from group."""
    admin_id = message.from_user.id
    args = command.args.split() if command.args else []
    logger.info(f"/remove_vip: admin_id={admin_id}, args={args}")

    if len(args) < 1:
        await message.answer(t('admin.remove_vip_usage'), parse_mode='HTML')
        return

    user_input = args[0]
    target_user_id = None

    if user_input.startswith('@'):
        target_username = user_input.lstrip('@')
        existing = await run_sync(_find_by_username, target_username)

        if existing:
            target_user_id = existing['telegram_id']
        else:
            await message.answer(
                t('admin.user_not_found', username=target_username),
                parse_mode='HTML'
            )
            return
    else:
        try:
            target_user_id = int(user_input)
        except ValueError:
            await message.answer(t('admin.invalid_user_id'), parse_mode='HTML')
            return

    try:
        expire_success = await run_sync(_force_expire_membership, target_user_id)

        if not expire_success:
            await message.answer(
                t('admin.user_not_exist_or_expired', user_id=target_user_id),
                parse_mode='HTML'
            )
            return

        kick_success, kick_msg = await _kick_user(bot, target_user_id)
        if kick_success:
            kick_msg = t('admin.kicked_from_group')
        else:
            kick_msg = t('admin.kick_failed', error=kick_msg)

        await run_sync(
            _log_audit_event,
            'ADMIN_REMOVE_VIP',
            target_user_id,
            f'admin:{admin_id}',
            None,
            'ACTIVE',
            'EXPIRED',
            {'kick_success': kick_success, 'kick_msg': kick_msg}
        )

        await message.answer(
            t('admin.vip_removed', user_id=target_user_id, status=kick_msg),
            parse_mode='HTML'
        )
        logger.info(f"VIP removed: user={target_user_id}, kick={kick_success}")

    except Exception as e:
        logger.error(f"/remove_vip failed: {e}", exc_info=True)
        await message.answer(f"{t('admin.operation_failed')}: {e}")


# ========================================
# Trader Program admin commands
# ========================================

@router.message(Command("referral_pending"), AdminFilter())
async def cmd_referral_pending(message: Message) -> None:
    """/referral_pending: list pending trader UID submissions."""
    admin_id = message.from_user.id
    logger.info(f"/referral_pending: admin_id={admin_id}")

    try:
        pending = await run_sync(_get_pending_traders)

        if not pending:
            await message.answer("No pending referral applications.")
            return

        lines = ["<b>Pending Referral Applications</b>\n"]
        for p in pending:
            username = p.get('telegram_username') or 'N/A'
            lines.append(
                f"- @{username} (ID: {p['telegram_id']})\n"
                f"  UID: <code>{p['binance_uid']}</code>"
            )

        response = "\n".join(lines)
        response += "\n\nUse <code>/referral_approve UID</code> to approve"

        await message.answer(response, parse_mode='HTML')

    except Exception as e:
        logger.error(f"/referral_pending failed: {e}", exc_info=True)
        await message.answer("Query failed.")


@router.message(Command("referral_approve"), AdminFilter())
async def cmd_referral_approve(message: Message, command: CommandObject, bot: Bot) -> None:
    """/referral_approve <uid>: approve trader discount eligibility."""
    admin_id = message.from_user.id
    args = command.args.split() if command.args else []
    logger.info(f"/referral_approve: admin_id={admin_id}, args={args}")

    if not args:
        await message.answer("Usage: <code>/referral_approve UID</code>", parse_mode='HTML')
        return

    uid = args[0]

    try:
        member = await run_sync(_find_by_binance_uid, uid)
        if not member:
            await message.answer(f"UID <code>{uid}</code> not found.", parse_mode='HTML')
            return

        success = await run_sync(_approve_trader, member['telegram_id'])

        if success:
            await message.answer(
                f"Approved! User {member['telegram_id']} now has Trader discount (30% OFF).",
                parse_mode='HTML'
            )

            try:
                await bot.send_message(
                    chat_id=member['telegram_id'],
                    text=(
                        "Your Trader Program application has been approved!\n\n"
                        "You now get 30% OFF on all subscriptions.\n"
                        "Tap /subscribe to see your new prices."
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to notify trader approval: telegram_id={member['telegram_id']}, error={e}")
        else:
            await message.answer("Approval failed.")

    except Exception as e:
        logger.error(f"/referral_approve failed: {e}", exc_info=True)
        await message.answer(f"Error: {e}")


@router.message(Command("referral_reject"), AdminFilter())
async def cmd_referral_reject(message: Message, command: CommandObject, bot: Bot) -> None:
    """/referral_reject <uid>: reject trader discount eligibility."""
    admin_id = message.from_user.id
    args = command.args.split() if command.args else []
    logger.info(f"/referral_reject: admin_id={admin_id}, args={args}")

    if not args:
        await message.answer("Usage: <code>/referral_reject UID</code>", parse_mode='HTML')
        return

    uid = args[0]

    try:
        member = await run_sync(_find_by_binance_uid, uid)
        if not member:
            await message.answer(f"UID <code>{uid}</code> not found.", parse_mode='HTML')
            return

        success = await run_sync(_reject_trader, member['telegram_id'])

        if success:
            await message.answer(
                f"Rejected. User {member['telegram_id']}'s UID has been cleared.",
                parse_mode='HTML'
            )

            try:
                await bot.send_message(
                    chat_id=member['telegram_id'],
                    text=(
                        "Your Trader Program application was not approved.\n\n"
                        "Please ensure you used our referral link to register.\n"
                        "Contact @LisaLyu77 if you have questions."
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to notify trader rejection: telegram_id={member['telegram_id']}, error={e}")
        else:
            await message.answer("Rejection failed.")

    except Exception as e:
        logger.error(f"/referral_reject failed: {e}", exc_info=True)
        await message.answer(f"Error: {e}")


@router.message(Command("referral_stats"), AdminFilter())
async def cmd_referral_stats(message: Message) -> None:
    """/referral_stats: trader program statistics dashboard."""
    admin_id = message.from_user.id
    logger.info(f"/referral_stats: admin_id={admin_id}")

    try:
        stats = await run_sync(_get_referral_stats_extended)

        response = (
            "<b>Trader Program Statistics</b>\n\n"
            "<b>Overview</b>\n"
            f"  Total Applications: {stats.get('total_applications', 0)}\n"
            f"  Verified Traders: {stats.get('verified_count', 0)}\n"
            f"  Pending Review: {stats.get('pending_count', 0)}\n\n"
        )

        order_count = stats.get('order_count', 0)
        total_revenue = stats.get('total_revenue', 0)
        est_rebate = stats.get('est_rebate', 0)

        response += (
            "<b>Revenue (Est.)</b>\n"
            f"  Trader Orders: {order_count}\n"
            f"  Total Revenue: ${total_revenue:.2f}\n"
            f"  Est. Rebate (30%): ${est_rebate:.2f}\n\n"
        )

        top_traders = stats.get('top_traders', [])
        if top_traders:
            response += "<b>Top Traders</b>\n"
            for i, trader in enumerate(top_traders, 1):
                username = trader.get('telegram_username') or str(trader.get('telegram_id', 'N/A'))
                orders = trader.get('order_count', 0)
                amount = float(trader.get('total_amount', 0))
                response += f"  {i}. @{username}: {orders} orders (${amount:.2f})\n"
            response += "\n"

        response += (
            "<b>Commands</b>\n"
            "<code>/referral_pending</code> - View pending\n"
            "<code>/referral_approve UID</code> - Approve\n"
            "<code>/referral_reject UID</code> - Reject"
        )

        await message.answer(response, parse_mode='HTML')

    except Exception as e:
        logger.error(f"/referral_stats failed: {e}", exc_info=True)
        await message.answer("Query failed.")


# ========================================
# Orphan order repair
# ========================================

@router.message(Command("fix_order"), AdminFilter())
async def cmd_fix_order(message: Message, command: CommandObject, lang: str, t: Callable, bot: Bot) -> None:
    """/fix_order <order_id>: fix a paid-but-not-activated order (CONFIRMED status only)."""
    admin_id = message.from_user.id
    args = command.args.strip() if command.args else None
    logger.info(f"/fix_order: admin_id={admin_id}, order_id={args}")

    if not args:
        await message.answer(t('admin.fix_order_usage'), parse_mode='HTML')
        return

    order_id = args

    try:
        order = await run_sync(_get_order_by_id, order_id)

        if not order:
            await message.answer(
                t('admin.fix_order_not_found', order_id=order_id),
                parse_mode='HTML'
            )
            return

        if order.get('status') != 'CONFIRMED':
            await message.answer(
                t('admin.fix_order_not_confirmed', status=order.get('status', 'UNKNOWN')),
                parse_mode='HTML'
            )
            return

        telegram_id = order.get('telegram_id')
        if not telegram_id:
            await message.answer("Order has no telegram_id.", parse_mode='HTML')
            return

        member = await run_sync(_find_by_telegram_id, telegram_id)
        if member and member.get('status') == 'ACTIVE':
            expire_date = member.get('expire_date')
            if expire_date and expire_date > datetime.now():
                await message.answer(
                    t('admin.fix_order_already_active', user_id=telegram_id),
                    parse_mode='HTML'
                )
                return

        duration_days = order.get('duration_days', 30)
        plan_code = order.get('membership_type', 'PREMIUM_M')
        level = 2 if 'PREMIUM' in plan_code else 1

        member_id = await run_sync(
            _activate_or_renew,
            telegram_id,
            plan_code,
            duration_days,
            level,
            order_id,
            member.get('telegram_username') if member else None
        )

        if not member_id:
            await message.answer("Failed to activate membership.", parse_mode='HTML')
            return

        from src.telegram.access_controller import AccessController
        controller = AccessController(bot)
        user_lang = member.get('language', 'en') if member else 'en'

        invite_success = True
        try:
            await controller.send_invites(telegram_id, plan_code, user_lang)
        except Exception as e:
            invite_success = False
            logger.warning(f"Failed to send invites for {telegram_id}: {e}")

        await run_sync(
            _log_audit_event,
            'ADMIN_FIX_ORDER',
            telegram_id,
            f'admin:{admin_id}',
            order_id,
            'CONFIRMED',
            'ACTIVE',
            {'duration_days': duration_days, 'plan_code': plan_code, 'invite_sent': invite_success}
        )

        await message.answer(
            t('admin.fix_order_success',
              order_id=order_id,
              days=duration_days,
              invite='sent' if invite_success else 'failed'),
            parse_mode='HTML'
        )
        logger.info(f"Order fixed: order_id={order_id}, user={telegram_id}, days={duration_days}")

    except Exception as e:
        logger.error(f"/fix_order failed: {e}", exc_info=True)
        await message.answer(f"{t('admin.operation_failed')}: {e}")


# ========================================
# Address pool and user info queries
# ========================================

def _get_pool_stats() -> dict:
    """Sync: get address pool stats."""
    from ..payment.hd_wallet_manager import HDWalletManager
    manager = HDWalletManager()
    return manager.get_pool_stats()


def _get_user_full_info(identifier: str) -> Optional[dict]:
    """Sync: get full user info by telegram_id, @username, or binance_uid."""
    from src.telegram.database import OrderDAO

    from ..utils.db_provider import get_db, get_member_service

    service = get_member_service()
    db = get_db()
    order_dao = OrderDAO(db)

    member = None

    if identifier.startswith('@'):
        member = service.repository.find_by_username(identifier[1:])
    elif identifier.isdigit():
        member = service.repository.find_by_telegram_id(int(identifier))
    else:
        member = service.repository.find_by_binance_uid(identifier)

    if not member:
        return None

    telegram_id = member.get('telegram_id')

    orders = order_dao.get_orders_by_telegram_id(telegram_id, limit=5)

    return {
        'member': member,
        'orders': orders or []
    }


@router.message(Command("pool_status"), AdminFilter())
async def cmd_pool_status(message: Message, lang: str, t: Callable) -> None:
    """/pool_status: address pool health — show counts by status."""
    admin_id = message.from_user.id
    logger.info(f"/pool_status: admin_id={admin_id}")

    try:
        stats = await run_sync(_get_pool_stats)

        response = (
            f"<b>{t('admin.pool_status_title')}</b>\n\n"
            f"Available: <code>{stats['available']}</code>\n"
            f"Assigned: <code>{stats['assigned']}</code>\n"
            f"Used: <code>{stats['used']}</code>\n"
            f"Collecting: <code>{stats['collecting']}</code>\n"
            f"─────────\n"
            f"Total: <code>{stats['total']}</code>"
        )

        if stats['available'] < 5:
            response += "\n\n⚠️ Available addresses low!"

        await message.answer(response, parse_mode='HTML')

    except Exception as e:
        logger.error(f"/pool_status failed: {e}", exc_info=True)
        await message.answer(t('admin.query_failed'))


@router.message(Command("user_info"), AdminFilter())
async def cmd_user_info(message: Message, command: CommandObject, lang: str, t: Callable) -> None:
    """/user_info <identifier>: query user info by telegram_id, @username, or binance_uid."""
    admin_id = message.from_user.id
    identifier = command.args.strip() if command.args else None
    logger.info(f"/user_info: admin_id={admin_id}, identifier={identifier}")

    if not identifier:
        await message.answer(t('admin.user_info_usage'), parse_mode='HTML')
        return

    try:
        info = await run_sync(_get_user_full_info, identifier)

        if not info:
            await message.answer(t('admin.user_not_found_generic'), parse_mode='HTML')
            return

        member = info['member']
        orders = info['orders']

        username = member.get('telegram_username') or 'N/A'
        status = member.get('status', 'N/A')
        plan = member.get('membership_type', 'None')
        expire = member.get('expire_date')
        expire_str = expire.strftime('%Y-%m-%d') if expire else 'N/A'
        is_trader = 'Yes' if member.get('is_referral_verified') else 'No'
        user_lang = member.get('language', 'en')
        binance_uid = member.get('binance_uid') or 'N/A'

        response = (
            f"<b>{t('admin.user_info_title')}</b>\n\n"
            f"Telegram ID: <code>{member['telegram_id']}</code>\n"
            f"Username: @{username}\n"
            f"Plan: {plan}\n"
            f"Status: {status}\n"
            f"Expires: {expire_str}\n"
            f"Trader: {is_trader}\n"
            f"Language: {user_lang}\n"
            f"Binance UID: <code>{binance_uid}</code>\n\n"
        )

        response += f"<b>Recent Orders ({len(orders)})</b>\n"
        if orders:
            for order in orders[:3]:
                order_id = order.get('order_id', 'N/A')
                order_status = order.get('status', 'N/A')
                amount = order.get('expected_amount', 0)
                response += f"  • {order_id}: {order_status} ({amount} USDT)\n"
        else:
            response += "  (none)\n"

        await message.answer(response, parse_mode='HTML')

    except Exception as e:
        logger.error(f"/user_info failed: {e}", exc_info=True)
        await message.answer(f"{t('admin.operation_failed')}: {e}")
