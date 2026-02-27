"""
Swing strategy centralized configuration

Single source of truth for all symbol and strategy parameters.

Usage:
    from src.strategies.swing.config import SYMBOL_CONFIGS, get_symbol_config

    config = get_symbol_config('BTC')
    print(config['strategy'])       # 'swing-ensemble'
    print(config['trailing_mult'])  # 0.5
"""

from typing import Any, Dict, List

# =============================================================================
# Strategy type constants
# =============================================================================

STRATEGY_ENSEMBLE = "swing-ensemble"
STRATEGY_BREAKOUT = "swing-breakout"

# =============================================================================
# Ensemble strategy parameters (BTC/ETH/BNB)
# =============================================================================

ENSEMBLE_CONFIG: Dict[str, Any] = {
    "donchian_periods": [20, 35, 50, 65, 80],
    "signal_threshold": 0.4,
    "atr_period": 14,
    "base_exit_period": 50,  # exit_period = base_exit_period * trailing_mult
}

# =============================================================================
# Breakout strategy parameters (SOL)
# =============================================================================

BREAKOUT_CONFIG: Dict[str, Any] = {
    "breakout_period": 20,
    "atr_period": 14,
    "stop_loss_atr": 2.0,
    "take_profit_atr": 6.0,
    "max_holding_days": 60,
}

# =============================================================================
# Symbol configurations (single source of truth)
# =============================================================================

SYMBOL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "BTC": {
        "strategy": STRATEGY_ENSEMBLE,
        "trailing_mult": 0.5,
        "trailing_period": 25,  # base_exit_period * trailing_mult = 50 * 0.5
        "stop_type": "TRAILING_LOWEST",
        "quantity_precision": 3,
        "price_precision": 2,
    },
    "ETH": {
        "strategy": STRATEGY_ENSEMBLE,
        "trailing_mult": 0.3,
        "trailing_period": 15,  # 50 * 0.3 = 15
        "stop_type": "TRAILING_LOWEST",
        "quantity_precision": 3,
        "price_precision": 2,
    },
    "BNB": {
        "strategy": STRATEGY_ENSEMBLE,
        "trailing_mult": 0.3,
        "trailing_period": 15,
        "stop_type": "TRAILING_LOWEST",
        "quantity_precision": 2,
        "price_precision": 2,
    },
    "SOL": {
        "strategy": STRATEGY_BREAKOUT,
        "trailing_mult": 2.0,  # ATR multiplier for stop-loss
        "trailing_period": None,  # breakout strategy does not use period-based stop
        "stop_type": "TRAILING_ATR",
        "quantity_precision": 0,
        "price_precision": 3,
    },
}

# =============================================================================
# Risk management parameters
# =============================================================================

RISK_CONFIG: Dict[str, Any] = {
    "default_risk_percent": 2.0,
    "max_position_value": 5000.0,
    "atr_stop_mult": 2.0,
    "max_leverage": 3,
    "leverage_buffer": 1.25,
    "min_notional_value": 10.0,
    "entry_slippage_warn": 0.005,  # 0.5%
    "exit_slippage_warn": 0.01,   # 1%
}

# =============================================================================
# Funding rate filter configuration
# =============================================================================

FUNDING_RATE_CONFIG: Dict[str, Any] = {
    # Warning threshold: 0.05%/8h = 68% annualized, log only
    "warn_threshold": 0.0005,
    # Reduce threshold: 0.07%/8h = 95% annualized, halve position
    "reduce_threshold": 0.0007,
    # Skip threshold: 0.1%/8h = 137% annualized, block long entry
    "skip_threshold": 0.001,
    # Feature flag
    "enabled": True,
}

# =============================================================================
# IV regime filter configuration (observation mode)
# =============================================================================

IV_FILTER_CONFIG: Dict[str, Any] = {
    # DVOL percentile thresholds
    "caution_percentile": 0.70,  # log warning above 70th percentile
    "skip_percentile": 0.85,     # suggest skipping above 85th percentile
    # History window
    "dvol_lookback_days": 90,
    # Cache TTL (seconds)
    "cache_ttl": 3600,
}

# =============================================================================
# Feature flags (for gradual rollout and rollback)
# =============================================================================

FEATURE_FLAGS: Dict[str, bool] = {
    "enable_is_recording": True,          # implementation shortfall recording
    "enable_funding_filter": True,        # funding rate filter
    "enable_iv_filter": True,             # IV regime filter (observation mode)
    "enable_iv_filter_blocking": False,   # IV regime blocking mode (Set True to enable blocking mode)
}

# =============================================================================
# Helper functions
# =============================================================================


def get_symbol_config(symbol: str) -> Dict[str, Any]:
    """
    Get configuration for a symbol.

    Args:
        symbol: Asset symbol (BTC/ETH/BNB/SOL).

    Returns:
        Configuration dict.

    Raises:
        ValueError: Unsupported symbol.
    """
    if symbol not in SYMBOL_CONFIGS:
        raise ValueError(f"Unsupported symbol: {symbol}, supported: {list(SYMBOL_CONFIGS.keys())}")
    return SYMBOL_CONFIGS[symbol]


def get_supported_symbols() -> List[str]:
    """Return the list of supported symbols."""
    return list(SYMBOL_CONFIGS.keys())


def get_ensemble_symbols() -> List[str]:
    """Return symbols using the ensemble strategy."""
    return [s for s, c in SYMBOL_CONFIGS.items() if c["strategy"] == STRATEGY_ENSEMBLE]


def get_breakout_symbols() -> List[str]:
    """Return symbols using the breakout strategy."""
    return [s for s, c in SYMBOL_CONFIGS.items() if c["strategy"] == STRATEGY_BREAKOUT]


def get_strategy_config(strategy_name: str) -> Dict[str, Any]:
    """
    Get configuration for a strategy.

    Args:
        strategy_name: Strategy name (swing-ensemble/swing-breakout).

    Returns:
        Strategy configuration dict.
    """
    if strategy_name == STRATEGY_ENSEMBLE:
        return ENSEMBLE_CONFIG
    elif strategy_name == STRATEGY_BREAKOUT:
        return BREAKOUT_CONFIG
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")
