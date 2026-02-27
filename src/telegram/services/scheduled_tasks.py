"""
Scheduled task manager (asyncio).

Tasks:
- check_expired_members: hourly — expire memberships and kick users
- send_renewal_reminders: daily at 10:00 UTC — send renewal reminders
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot

from .member_service import MemberService, RenewalReminder
from ..access_controller import AccessController as GroupController
from .notification_service import NotificationService
from .kick_retry import get_kick_retry_manager, KickRetryManager
from ..database.membership_plan_dao import MembershipPlanDAO


class ScheduledTasks:
    """Scheduled task manager (asyncio)."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._member_service: MemberService = None
        self._group_controller: GroupController = None
        self._notification_service: NotificationService = None
        self._kick_retry_manager: KickRetryManager = None
        self._bot: Optional[Bot] = None
        self._running: bool = False
        self._tasks: list = []

    async def start(self, bot: Bot) -> None:
        """Start all scheduled tasks."""
        if self._running:
            self.logger.warning("Scheduled tasks already running")
            return

        self._bot = bot
        self._running = True

        self._tasks = [
            asyncio.create_task(self._run_expired_check_loop()),
            asyncio.create_task(self._run_renewal_reminder_loop()),
            asyncio.create_task(self._run_orphan_order_check_loop()),
        ]

        self.logger.info("Scheduled tasks started (asyncio)")

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        self._running = False

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        self.logger.info("Scheduled tasks stopped")

    async def _run_expired_check_loop(self) -> None:
        """Expired member check loop (hourly). First run after 60s."""
        await asyncio.sleep(60)

        while self._running:
            try:
                await self._check_expired_members()
            except Exception as e:
                self.logger.error(f"Expired check loop error: {e}", exc_info=True)

            await asyncio.sleep(3600)

    async def _run_renewal_reminder_loop(self) -> None:
        """Renewal reminder loop (daily at 10:00 UTC)."""
        while self._running:
            try:
                now = datetime.utcnow()
                target = now.replace(hour=10, minute=0, second=0, microsecond=0)

                if now >= target:
                    from datetime import timedelta
                    target = target + timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                self.logger.debug(f"Renewal reminders will run in {wait_seconds:.0f}s")

                await asyncio.sleep(wait_seconds)

                if self._running:
                    await self._send_renewal_reminders()

            except Exception as e:
                self.logger.error(f"Renewal reminder loop error: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _run_orphan_order_check_loop(self) -> None:
        """Orphan order detection loop (every 30 min). First run after 5 min."""
        await asyncio.sleep(300)

        while self._running:
            try:
                await self._detect_orphan_orders()
            except Exception as e:
                self.logger.error(f"Orphan order loop error: {e}", exc_info=True)

            await asyncio.sleep(1800)

    def _get_member_service(self) -> MemberService:
        if self._member_service is None:
            self._member_service = MemberService()
        return self._member_service

    def _get_group_controller(self) -> GroupController:
        if self._group_controller is None:
            self._group_controller = GroupController(self._bot)
        return self._group_controller

    def _get_notification_service(self) -> NotificationService:
        if self._notification_service is None:
            self._notification_service = NotificationService(self._bot)
        return self._notification_service

    def _get_kick_retry_manager(self) -> KickRetryManager:
        if self._kick_retry_manager is None:
            self._kick_retry_manager = get_kick_retry_manager()
        return self._kick_retry_manager

    async def _check_expired_members(self) -> None:
        """
        Scheduled task: check expired members and kick from group.

        Steps:
        1. Process kick retry queue
        2. Mark expired memberships (stop signal delivery)
        3. Kick members expired for more than 24h (T+1 kick)
        4. Failed kicks go to retry queue
        """
        self.logger.info("Running expired member check...")

        try:
            service = self._get_member_service()
            controller = self._get_group_controller()
            retry_manager = self._get_kick_retry_manager()

            await self._process_kick_retries(controller, retry_manager)

            result = service.process_expired_members()
            self.logger.info(
                f"Expiry processed: marked_expired={result['marked_expired']}, "
                f"to_kick={result['to_kick']}"
            )

            members_to_kick = service.get_members_to_kick()

            if not members_to_kick:
                self.logger.debug("No members to kick")
                return

            kicked_count = 0
            failed_count = 0

            for member in members_to_kick:
                telegram_id = member["telegram_id"]
                try:
                    kick_results = await controller.kick_user(telegram_id)

                    failed_channels = {k: v for k, v in kick_results.items() if not v}

                    if failed_channels:
                        await retry_manager.add_failed_kick(
                            telegram_id, failed_channels
                        )
                        failed_count += 1
                        self.logger.warning(
                            f"Partial kick failure: telegram_id={telegram_id}, "
                            f"failed={list(failed_channels.keys())}"
                        )
                    else:
                        kicked_count += 1
                        self.logger.info(f"Kicked expired member: telegram_id={telegram_id}")

                except Exception as e:
                    failed_count += 1
                    all_channels = {
                        f"{level}_{lang}": False
                        for (level, lang) in controller.targets.keys()
                    }
                    await retry_manager.add_failed_kick(telegram_id, all_channels)
                    self.logger.error(
                        f"Kick completely failed: telegram_id={telegram_id}, error={e}"
                    )

            self.logger.info(
                f"Kick task done: kicked={kicked_count}, failed={failed_count}, "
                f"total={len(members_to_kick)}"
            )

        except Exception as e:
            self.logger.error(f"Expired check task error: {e}", exc_info=True)

    async def _process_kick_retries(
        self, controller: GroupController, retry_manager: KickRetryManager
    ) -> None:
        """Process the kick retry queue."""
        pending_retries = await retry_manager.get_pending_retries()

        if not pending_retries:
            return

        self.logger.info(f"Processing kick retry queue: {len(pending_retries)} items")

        success_count = 0
        failed_count = 0

        for item in pending_retries:
            try:
                # kick_user kicks all channels; single-channel optimization is future work
                kick_results = await controller.kick_user(item.telegram_id)

                channel_success = kick_results.get(item.channel_key, False)

                await retry_manager.process_retry(item, channel_success)

                if channel_success:
                    success_count += 1
                else:
                    failed_count += 1

            except Exception as e:
                self.logger.error(
                    f"Kick retry exception: telegram_id={item.telegram_id}, "
                    f"channel={item.channel_key}, error={e}"
                )
                await retry_manager.process_retry(item, False)
                failed_count += 1

        self.logger.info(f"Kick retry queue done: success={success_count}, failed={failed_count}")

    async def _detect_orphan_orders(self) -> None:
        """
        Detect and auto-recover orphan orders.

        Orphan order: status=CONFIRMED (payment received) but no ACTIVE membership,
        confirmed more than 10 minutes ago (normal processing window excluded).

        Steps:
        1. Log WARNING
        2. Auto-activate membership
        3. Mark order as PROCESSED on success
        4. Send admin alert on failure
        """
        self.logger.info("Running orphan order detection...")

        try:
            service = self._get_member_service()
            db = service._db  # reuse MemberService's DB connection
            plan_dao = MembershipPlanDAO(db)

            # Query orphan orders
            sql = """
                SELECT po.order_id, po.telegram_id, po.membership_type,
                       po.expected_amount, po.confirmed_at, po.duration_days
                FROM payment_orders po
                LEFT JOIN memberships m
                    ON po.telegram_id = m.telegram_id
                    AND m.status = 'ACTIVE'
                WHERE po.status = 'CONFIRMED'
                  AND po.confirmed_at < NOW() - INTERVAL 10 MINUTE
                  AND m.id IS NULL
                ORDER BY po.confirmed_at ASC
                LIMIT 10
            """

            orphans = db.execute_query(sql, fetch_all=True) or []

            if not orphans:
                self.logger.debug("No orphan orders found")
                return

            self.logger.warning(f"Found {len(orphans)} orphan order(s), attempting auto-activation")

            for orphan in orphans:
                order_id = orphan.get("order_id")
                telegram_id = orphan.get("telegram_id")
                plan_code = orphan.get("membership_type")
                amount = orphan.get("expected_amount")
                confirmed_at = orphan.get("confirmed_at")
                duration_days = orphan.get("duration_days")

                self.logger.warning(
                    f"Orphan order: order_id={order_id}, "
                    f"telegram_id={telegram_id}, plan={plan_code}"
                )

                activated = await self._try_activate_orphan_order(
                    service=service,
                    plan_dao=plan_dao,
                    db=db,
                    order_id=order_id,
                    telegram_id=telegram_id,
                    plan_code=plan_code,
                    duration_days=duration_days,
                )

                if not activated:
                    await self._send_orphan_order_alert(
                        order_id=order_id,
                        telegram_id=telegram_id,
                        plan_code=plan_code,
                        amount=amount,
                        confirmed_at=confirmed_at,
                    )

        except Exception as e:
            self.logger.error(f"Orphan order detection error: {e}", exc_info=True)

    async def _try_activate_orphan_order(
        self,
        service: MemberService,
        plan_dao: MembershipPlanDAO,
        db,
        order_id: str,
        telegram_id: int,
        plan_code: str,
        duration_days: int,
    ) -> bool:
        """Attempt to activate a membership for an orphan order. Returns True on success."""
        try:
            plan = plan_dao.get_plan_by_code(plan_code)
            if not plan:
                self.logger.error(f"Orphan order activation failed: plan not found plan_code={plan_code}")
                return False

            level = plan.get("level", 1)

            # Activate membership
            member_id = service.activate_or_renew(
                telegram_id=telegram_id,
                membership_type=plan_code,
                duration_days=duration_days,
                level=level,
                order_id=order_id,
            )

            if not member_id:
                self.logger.error(
                    f"Orphan order activation failed: order_id={order_id}, telegram_id={telegram_id}"
                )
                return False

            update_sql = """
                UPDATE payment_orders
                SET status = 'PROCESSED', updated_at = NOW(6)
                WHERE order_id = %s AND status = 'CONFIRMED'
            """
            db.execute_update(update_sql, (order_id,))

            self.logger.info(
                f"Orphan order auto-activated: order_id={order_id}, "
                f"telegram_id={telegram_id}, member_id={member_id}"
            )
            return True

        except Exception as e:
            self.logger.error(
                f"Orphan order activation error: order_id={order_id}, error={e}", exc_info=True
            )
            return False

    async def _send_orphan_order_alert(
        self, order_id: str, telegram_id: int, plan_code: str, amount, confirmed_at
    ) -> None:
        """Send an admin alert for an orphan order that could not be auto-activated."""
        try:
            notification = self._get_notification_service()

            message = (
                f"**Orphan Order Alert**\n\n"
                f"Order: `{order_id}`\n"
                f"User: `{telegram_id}`\n"
                f"Plan: {plan_code}\n"
                f"Amount: {amount} USDT\n"
                f"Paid at: {confirmed_at}\n\n"
                f"Payment received but membership not activated — manual action required!"
            )

            await notification.send_admin_alert(message)

        except Exception as e:
            self.logger.error(f"Failed to send orphan order alert: {e}")

    async def _send_renewal_reminders(self) -> None:
        """
        Scheduled task: send renewal reminders.

        Types: T-3, T-1, T+0, ALPHA_CLOSING, Trial (Day 6)
        """
        self.logger.info("Running renewal reminder task...")

        try:
            service = self._get_member_service()
            notification = self._get_notification_service()

            sent_count = 0
            failed_count = 0

            # Trial expiry reminder (Day 6)
            trial_members = service.get_trial_expiring_tomorrow()
            for member in trial_members:
                telegram_id = member.get("telegram_id")
                lang = member.get("language", "en")

                if not telegram_id:
                    continue

                success = await notification.send_trial_expiry_reminder(
                    telegram_id=telegram_id, lang=lang
                )

                if success:
                    sent_count += 1
                    self.logger.info(f"Trial reminder sent: telegram_id={telegram_id}")
                else:
                    failed_count += 1

            # Standard reminder types
            for reminder_type in [
                RenewalReminder.T_MINUS_3,
                RenewalReminder.T_MINUS_1,
                RenewalReminder.T_ZERO,
                RenewalReminder.ALPHA_CLOSING,
            ]:
                members = service.get_expiring_members(reminder_type.value)

                for member in members:
                    telegram_id = member.get("telegram_id")
                    expire_date = member.get("expire_date")
                    plan_code = member.get("membership_type", "PREMIUM_M")

                    if plan_code == "TRIAL_7D":
                        continue  # already handled above

                    if not telegram_id or not expire_date:
                        continue

                    success = await notification.send_renewal_reminder(
                        telegram_id=telegram_id,
                        reminder_type=reminder_type,
                        expire_date=expire_date,
                        plan_code=plan_code,
                    )

                    if success:
                        sent_count += 1
                    else:
                        failed_count += 1

            self.logger.info(
                f"Renewal reminder task done: sent={sent_count}, failed={failed_count}"
            )

        except Exception as e:
            self.logger.error(f"Renewal reminder task error: {e}", exc_info=True)


_scheduled_tasks: ScheduledTasks = None


def get_scheduled_tasks() -> ScheduledTasks:
    """Return the ScheduledTasks singleton."""
    global _scheduled_tasks
    if _scheduled_tasks is None:
        _scheduled_tasks = ScheduledTasks()
    return _scheduled_tasks
