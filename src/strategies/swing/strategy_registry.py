"""Compatibility shim - actual implementation moved to strategies/registry.py"""
from src.strategies.swing.strategies.registry import *  # noqa: F401, F403
from src.strategies.swing.strategies.registry import (  # noqa: F401
    register_strategy,
    create_strategy,
    create_all_strategies,
    get_registered_strategies,
)
