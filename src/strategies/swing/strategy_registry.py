"""Compatibility shim - actual implementation moved to strategies/registry.py"""
from src.strategies.swing.strategies.registry import *  # noqa: F401, F403
from src.strategies.swing.strategies.registry import (  # noqa: F401
    create_all_strategies,
    create_strategy,
    get_registered_strategies,
    register_strategy,
)
