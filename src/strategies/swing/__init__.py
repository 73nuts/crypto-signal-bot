"""
Swing daily trend strategy module

Strategy config:
  BTC/ETH/BNB: swing-ensemble (multi-period Donchian ensemble)
  SOL: swing-breakout (single-period breakout)

Architecture:
  - scheduler.py: scheduler service (runs daily at UTC 00:01)
  - executor.py: executor (single source of truth: positions table)
  - ensemble_strategy.py: BTC/ETH/BNB strategy
  - breakout_strategy.py: SOL strategy

Usage:
  # Start scheduler service
  python -m src.strategies.swing.scheduler

  # Run one check immediately
  python -m src.strategies.swing.scheduler --run-now

  # Show status
  python -m src.strategies.swing.scheduler --status

  # Dry-run mode (pre-launch validation)
  python -m src.strategies.swing.scheduler --execute --dry-run

  # Execute mode (live trading)
  python -m src.strategies.swing.scheduler --execute --mainnet
"""

from .config import (
    SYMBOL_CONFIGS,
    ENSEMBLE_CONFIG,
    BREAKOUT_CONFIG,
    RISK_CONFIG,
    get_symbol_config,
    get_supported_symbols,
)
from .strategy_base import SwingSignal, SwingStrategy
from .strategies.registry import (
    register_strategy,
    create_strategy,
    create_all_strategies,
    get_registered_strategies,
)
from .providers.data_provider import DataProvider, BinanceDataProvider, LocalFileDataProvider
from .trading_port import TradingPort
from .mocks.mock_executor import MockTradingPort
from .strategies.ensemble import SwingEnsembleStrategy
from .strategies.breakout import SwingBreakoutStrategy
from .scheduler import SwingScheduler
from .services.executor import SwingExecutor
from .services.notification_manager import SwingNotificationManager

__all__ = [
    # Config
    'SYMBOL_CONFIGS',
    'ENSEMBLE_CONFIG',
    'BREAKOUT_CONFIG',
    'RISK_CONFIG',
    'get_symbol_config',
    'get_supported_symbols',
    # Strategy Base
    'SwingSignal',
    'SwingStrategy',
    'register_strategy',
    'create_strategy',
    'create_all_strategies',
    'get_registered_strategies',
    # Data Provider
    'DataProvider',
    'BinanceDataProvider',
    'LocalFileDataProvider',
    # Trading Port
    'TradingPort',
    'MockTradingPort',
    # Strategies
    'SwingEnsembleStrategy',
    'SwingBreakoutStrategy',
    # Core
    'SwingScheduler',
    'SwingExecutor',
    'SwingNotificationManager',
]
