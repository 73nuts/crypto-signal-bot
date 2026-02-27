"""
Payment service runner.

Responsibilities:
1. Start payment listener (core business)
2. Start fund collection (low-frequency task, every 6 hours)
3. Maintain heartbeat file (for Docker Healthcheck)

Architecture:
- asyncio runs multiple tasks in parallel
- Sync methods run in thread pool via executor
"""

import asyncio
import logging
import os
import queue
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram import Bot

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from src.core.config import settings
from src.telegram.access_controller import AccessController as GroupController
from src.telegram.payment.fund_collector import FundCollector
from src.telegram.payment.payment_monitor import PaymentMonitor

# Heartbeat file path
HEARTBEAT_FILE = Path('/tmp/payment_heartbeat')

# Config
HEARTBEAT_INTERVAL = 30  # heartbeat interval (seconds)
POLL_INTERVAL = 10  # payment listener interval (seconds)
COLLECT_INTERVAL_HOURS = 6  # fund collection interval (hours)


class PaymentServiceRunner:
    """Payment service runner."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.running = False
        self.executor = ThreadPoolExecutor(max_workers=2)

        # Thread-safe callback queue (avoid calling asyncio API from threads)
        self._callback_queue: queue.Queue = queue.Queue()

        # Components (lazy init, wait for env vars to load)
        self.monitor: Optional[PaymentMonitor] = None
        self.collector: Optional[FundCollector] = None
        self.group_controller: Optional[GroupController] = None

    def _init_components(self):
        """Initialize components."""
        try:
            # Get Bot Token
            bot_token = settings.get_secret('TELEGRAM_BOT_TOKEN')
            if not bot_token:
                raise ValueError("TELEGRAM_BOT_TOKEN not configured")

            # Create Bot instance
            bot = Bot(token=bot_token)

            # Create GroupController for sending invites after payment confirmation
            self.group_controller = GroupController(bot=bot)

            # Create payment monitor with confirmation callback
            self.monitor = PaymentMonitor(
                on_payment_confirmed=self._on_payment_confirmed
            )

            # Create fund collector
            self.collector = FundCollector()

            self.logger.info("Payment service components initialized")
            return True

        except Exception as e:
            self.logger.error(f"Component initialization failed: {e}", exc_info=True)
            return False

    def _on_payment_confirmed(
        self,
        order_id: str,
        telegram_id: int,
        plan_code: str
    ):
        """
        Payment confirmed callback - enqueue for processing.

        Note: called from PaymentMonitor's thread pool.
        For thread safety, only enqueues; does not call asyncio API directly.

        Args:
            order_id: Order ID
            telegram_id: User ID
            plan_code: Plan code
        """
        self.logger.info(
            f"Payment confirmed callback: order={order_id}, "
            f"user={telegram_id}, plan={plan_code}"
        )

        # Enqueue for main event loop (thread-safe)
        self._callback_queue.put((telegram_id, plan_code))
        self.logger.debug(f"Callback queued: user={telegram_id}")

    async def _process_callbacks(self):
        """
        Process callback queue - runs in the main event loop.

        Dequeues payment confirmations and sends invite links.
        Sends invite for user's preferred language channel.
        """
        self.logger.info("Callback processor started")

        while self.running:
            try:
                # Non-blocking dequeue
                telegram_id, plan_code = self._callback_queue.get_nowait()

                if self.group_controller:
                    try:
                        user_lang = self._get_user_language(telegram_id)
                        await self.group_controller.send_invites(
                            telegram_id, plan_code, lang=user_lang
                        )
                        self.logger.info(
                            f"Invite sent: user={telegram_id}, lang={user_lang}"
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to send invite: {e}")

            except queue.Empty:
                # Queue empty, wait briefly
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Callback processing error: {e}")

    def _get_user_language(self, telegram_id: int) -> str:
        """
        Get user language preference.

        Checks i18n cache first, falls back to database.

        Args:
            telegram_id: User Telegram ID

        Returns:
            Language code ('zh' | 'en')
        """
        try:
            from src.telegram.i18n import get_user_language
            cached_lang = get_user_language(telegram_id)
            if cached_lang:
                return cached_lang
        except Exception:
            pass

        # Fallback: query database
        try:
            from src.telegram.services.member_service import MemberService
            member_service = MemberService()
            db_lang = member_service.get_language(telegram_id)
            if db_lang:
                return db_lang
        except Exception:
            pass

        return 'en'  # default

    async def heartbeat_loop(self):
        """Heartbeat loop - periodically update heartbeat file."""
        self.logger.info("Heartbeat task started")

        while self.running:
            try:
                HEARTBEAT_FILE.write_text(
                    datetime.now().isoformat()
                )
                self.logger.debug(f"Heartbeat updated: {HEARTBEAT_FILE}")
            except Exception as e:
                self.logger.error(f"Heartbeat update failed: {e}")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def payment_monitor_loop(self):
        """
        Payment monitor loop with catch-up logic.

        Catch-up mode: poll continuously when behind, no sleep.
        Live mode: sleep normally when caught up.
        """
        self.logger.info("Payment monitor task started")

        # Initialize block number
        if self.monitor:
            self.monitor.last_block = (
                self.monitor.w3.eth.block_number -
                self.monitor.block_confirmations
            )
            self.logger.info(f"Monitoring from block {self.monitor.last_block}")

        poll_count = 0
        while self.running:
            poll_count += 1
            try:
                if self.monitor:
                    loop = asyncio.get_event_loop()
                    # Log INFO every 10 polls to avoid flooding
                    if poll_count % 10 == 1:
                        self.logger.info(f"[poll#{poll_count}] running...")
                    is_caught_up = await loop.run_in_executor(
                        self.executor,
                        self.monitor._poll_once
                    )

                    # Catch-up mode: continue immediately without sleep
                    if not is_caught_up:
                        continue

            except Exception as e:
                self.logger.error(f"Payment monitor error: {e}", exc_info=True)
                await asyncio.sleep(30)  # back off on error
                continue

            # Live mode: caught up, sleep normally
            await asyncio.sleep(POLL_INTERVAL)

    async def fund_collector_loop(self):
        """Fund collection loop - runs every 6 hours."""
        self.logger.info(f"Fund collector task started (interval: {COLLECT_INTERVAL_HOURS}h)")

        # Wait 1 hour before first collection
        await asyncio.sleep(3600)

        while self.running:
            try:
                if self.collector:
                    self.logger.info("Starting fund collection...")

                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        self.executor,
                        self.collector.collect_all
                    )

                    self.logger.info(
                        f"Fund collection done: success={result['success']}, "
                        f"failed={result['failed']}"
                    )
            except Exception as e:
                self.logger.error(f"Fund collection error: {e}", exc_info=True)

            await asyncio.sleep(COLLECT_INTERVAL_HOURS * 3600)

    async def run(self):
        """Start service."""
        self.logger.info("=" * 50)
        self.logger.info("Payment service starting...")
        self.logger.info("=" * 50)

        if not self._init_components():
            self.logger.error("Component initialization failed, exiting")
            return

        self.running = True

        # Create initial heartbeat file
        HEARTBEAT_FILE.write_text(datetime.now().isoformat())

        # Run all tasks in parallel
        try:
            await asyncio.gather(
                self.heartbeat_loop(),
                self.payment_monitor_loop(),
                self.fund_collector_loop(),
                self._process_callbacks(),
            )
        except asyncio.CancelledError:
            self.logger.info("Service tasks cancelled")
        finally:
            self.running = False
            self.executor.shutdown(wait=True)
            self.logger.info("Payment service stopped")

    def stop(self):
        """Stop service."""
        self.logger.info("Stop signal received...")
        self.running = False


def setup_signal_handlers(runner: PaymentServiceRunner):
    """Set up OS signal handlers."""
    def signal_handler(signum, frame):
        runner.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main():
    """Entry point."""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    # Reduce third-party library log verbosity
    logging.getLogger('web3').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Telegram Payment Service Starting...")

    runner = PaymentServiceRunner()
    setup_signal_handlers(runner)

    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("Service interrupted")
    except Exception as e:
        logger.error(f"Service exited with error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
