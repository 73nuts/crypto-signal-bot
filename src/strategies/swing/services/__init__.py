"""
Services layer module

Contains:
  - executor: executor facade
  - order_service: order execution service
  - stop_loss_service: stop-loss management service
  - notification_manager: notification manager
"""

from .executor import SwingExecutor
from .notification_manager import SwingNotificationManager
from .order_service import OrderService, SymbolConfig
from .stop_loss_service import StopLossService

__all__ = [
    'SwingExecutor',
    'OrderService',
    'SymbolConfig',
    'StopLossService',
    'SwingNotificationManager',
]
