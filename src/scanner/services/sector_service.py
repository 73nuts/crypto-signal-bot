"""
Sector Mapping Update Service

Service extracted from ScannerScheduler, responsible for:
  - Detecting new coins
  - AI classification of new coins
  - Sending admin review notifications
"""

from typing import Optional

from src.scanner.sector_updater import get_sector_updater
from src.notifications.telegram_broadcaster import get_broadcaster
from src.core.config import settings
from src.core.structured_logger import get_logger


class SectorService:
    """
    Sector mapping update service

    Responsibilities:
      - Detect newly listed coins
      - Call AI for classification
      - Send admin review notifications
    """

    def __init__(self, broadcaster=None):
        """
        Initialize sector service

        Args:
            broadcaster: Telegram broadcaster
        """
        self.logger = get_logger(__name__)
        self._broadcaster = broadcaster or get_broadcaster()

    async def check_updates(self) -> bool:
        """
        Check for sector mapping updates

        Flow:
          1. Fetch Top 200 futures
          2. Compare against existing mapping, identify new coins
          3. Classify new coins with AI
          4. If new coins found, push admin review notification

        Returns:
            Whether there are new coins pending review
        """
        self.logger.info("Auto-checking sector mapping updates...")

        try:
            updater = get_sector_updater()

            # Run check
            proposal = updater.check_and_notify()

            if not proposal:
                self.logger.info("No new coins to classify")
                return False

            # Get admin ID
            admin_id = settings.get_secret('ADMIN_TELEGRAM_ID')
            if not admin_id or not self._broadcaster or not self._broadcaster.is_ready:
                self.logger.warning("Cannot send admin notification: ADMIN_TELEGRAM_ID not configured or bot not initialized")
                return True

            # Format message
            message = updater.format_proposal_message(proposal)

            # Create approval buttons (aiogram 3.x)
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Approve",
                        callback_data=f"sector_approve_{proposal['id']}"
                    ),
                    InlineKeyboardButton(
                        text="Reject",
                        callback_data=f"sector_reject_{proposal['id']}"
                    ),
                ]
            ])

            # Send to admin
            await self._broadcaster.bot.send_message(
                chat_id=int(admin_id),
                text=message,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )

            self.logger.info(
                f"Sector update proposal sent: {proposal['total_new']} new coins, "
                f"proposal_id={proposal['id']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Sector update check failed: {e}")
            return False


# Factory function
_sector_service: Optional[SectorService] = None


def get_sector_service() -> SectorService:
    """Get SectorService singleton"""
    global _sector_service
    if _sector_service is None:
        _sector_service = SectorService()
    return _sector_service
