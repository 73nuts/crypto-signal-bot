"""
Strategy implementations module

Contains:
  - registry: strategy registration mechanism
  - ensemble: BTC/ETH/BNB multi-period ensemble strategy
  - breakout: SOL single-period breakout strategy
"""

# Trigger strategy registration (@register_strategy decorator runs on import)
from . import (
    breakout,  # noqa: F401
    ensemble,  # noqa: F401
)
from .breakout import SwingBreakoutStrategy
from .ensemble import SwingEnsembleStrategy
from .registry import (
    create_all_strategies,
    create_strategy,
    get_registered_strategies,
    register_strategy,
)

__all__ = [
    'register_strategy',
    'create_strategy',
    'create_all_strategies',
    'get_registered_strategies',
    'SwingEnsembleStrategy',
    'SwingBreakoutStrategy',
]
