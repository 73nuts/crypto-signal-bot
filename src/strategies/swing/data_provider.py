"""Compatibility shim - actual implementation moved to providers/data_provider.py"""
from src.strategies.swing.providers.data_provider import *  # noqa: F401, F403
from src.strategies.swing.providers.data_provider import (  # noqa: F401
    DataProvider,
    BinanceDataProvider,
    LocalFileDataProvider,
)
