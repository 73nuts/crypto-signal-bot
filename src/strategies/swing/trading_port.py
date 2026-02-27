"""
Swing trading port protocol

Defines the execution layer interface, supporting dependency injection and mock testing.
"""

from typing import Protocol, Optional, Dict, List, Any, runtime_checkable


@runtime_checkable
class TradingPort(Protocol):
    """
    Trading port protocol

    Defines the unified execution interface, supporting:
      - Production: SwingExecutor
      - Testing: MockTradingPort
    """

    def has_position(self, symbol: str) -> bool:
        """
        Check whether a position exists.

        Args:
            symbol: Asset symbol.

        Returns:
            True if a position exists.
        """
        ...

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get position details.

        Args:
            symbol: Asset symbol.

        Returns:
            Position dict or None.
        """
        ...

    def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        Get all positions.

        Returns:
            List of position dicts.
        """
        ...

    def execute_entry(
        self,
        symbol: str,
        price: float,
        atr: float,
        strategy_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Execute an entry.

        Args:
            symbol: Asset symbol.
            price: Entry price.
            atr: ATR value.
            strategy_name: Strategy name.

        Returns:
            Result dict or None.
        """
        ...

    def execute_exit(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        """
        Execute an exit.

        Args:
            symbol: Asset symbol.
            price: Exit price.
            reason: Exit reason.

        Returns:
            Result dict or None.
        """
        ...

    def update_trailing_stop(
        self,
        symbol: str,
        new_stop: float
    ) -> Optional[Dict[str, Any]]:
        """
        Update trailing stop.

        Args:
            symbol: Asset symbol.
            new_stop: New stop-loss price.

        Returns:
            Update result dict or None.
        """
        ...
