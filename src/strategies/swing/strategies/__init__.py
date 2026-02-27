"""
Strategy implementations module

Contains:
  - registry: strategy registration mechanism
  - ensemble: BTC/ETH/BNB multi-period ensemble strategy
  - breakout: SOL single-period breakout strategy
"""

# Trigger strategy registration (@register_strategy decorator runs on import)
from . import ensemble  # noqa: F401
from . import breakout  # noqa: F401

from .registry import (
    register_strategy,
    create_strategy,
    create_all_strategies,
    get_registered_strategies,
)
from .ensemble import SwingEnsembleStrategy
from .breakout import SwingBreakoutStrategy

__all__ = [
    'register_strategy',
    'create_strategy',
    'create_all_strategies',
    'get_registered_strategies',
    'SwingEnsembleStrategy',
    'SwingBreakoutStrategy',
]
