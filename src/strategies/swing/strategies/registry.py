"""
Swing strategy registry

Provides:
  - @register_strategy decorator
  - create_strategy() factory function
  - create_all_strategies() batch creation

Usage:
    @register_strategy('swing-ensemble')
    class SwingEnsembleStrategy:
        ...

    # Create a single strategy
    strategy = create_strategy('BTC')

    # Create all strategies
    strategies = create_all_strategies()
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Dict, List, Type

if TYPE_CHECKING:
    from src.strategies.swing.strategy_base import SwingStrategy

from src.strategies.swing.config import (
    get_supported_symbols,
    get_symbol_config,
)

# Type alias
StrategyFactory = Callable[[str], 'SwingStrategy']

# Strategy registry (module-level singleton)
_strategy_registry: Dict[str, StrategyFactory] = {}

logger = logging.getLogger(__name__)


def register_strategy(strategy_name: str):
    """
    Strategy registration decorator.

    Args:
        strategy_name: Strategy name (e.g. 'swing-ensemble', 'swing-breakout').

    Usage:
        @register_strategy('swing-ensemble')
        class SwingEnsembleStrategy:
            def __init__(self, symbol: str):
                ...
    """
    def decorator(cls: Type) -> Type:
        if strategy_name in _strategy_registry:
            logger.warning(f"Strategy '{strategy_name}' already registered, overwriting")
        _strategy_registry[strategy_name] = cls
        logger.debug(f"Registered strategy: {strategy_name} -> {cls.__name__}")
        return cls
    return decorator


def create_strategy(symbol: str):
    """
    Create a strategy instance based on config.

    Args:
        symbol: Asset symbol (e.g. 'BTC', 'ETH', 'SOL').

    Returns:
        Strategy instance.

    Raises:
        ValueError: Unknown symbol or unregistered strategy.
    """
    config = get_symbol_config(symbol)  # raises ValueError if symbol not supported
    strategy_name = config['strategy']

    if strategy_name not in _strategy_registry:
        registered = list(_strategy_registry.keys())
        raise ValueError(
            f"Strategy not registered: '{strategy_name}', "
            f"registered strategies: {registered}"
        )

    factory = _strategy_registry[strategy_name]
    return factory(symbol)


def create_all_strategies() -> Dict[str, 'SwingStrategy']:
    """
    Create strategy instances for all supported symbols.

    Returns:
        Dict[symbol, strategy]
    """
    strategies = {}
    for symbol in get_supported_symbols():
        try:
            strategies[symbol] = create_strategy(symbol)
        except Exception as e:
            logger.error(f"Strategy creation failed [{symbol}]: {e}")
            raise

    logger.info(f"All strategies created: {list(strategies.keys())}")
    return strategies


def get_registered_strategies() -> List[str]:
    """
    Get the list of registered strategy names.

    Returns:
        List of strategy names.
    """
    return list(_strategy_registry.keys())


def is_strategy_registered(strategy_name: str) -> bool:
    """
    Check whether a strategy is registered.

    Args:
        strategy_name: Strategy name.

    Returns:
        True if registered.
    """
    return strategy_name in _strategy_registry
