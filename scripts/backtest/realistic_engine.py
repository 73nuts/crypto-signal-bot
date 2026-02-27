#!/usr/bin/env python3
"""
Realistic Backtest Engine v1.0

Backtest engine refactored based on architecture audit to eliminate "fairy tale" bias.

Core fixes:
- P0-1: next_open entry (eliminate look-ahead bias)
- P0-2: fee on notional
- P0-3: conservative constant slippage model
- P1-1: pessimistic intrabar assumption + SL price-through slippage
- P1-2: trailing stop written back to Position
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from scripts.data.resample import DataLoader


# ==================== Config ====================

# Execution parameters
MAX_LEVERAGE = 3
LEVERAGE_BUFFER = 1.25
DEFAULT_ATR_STOP_MULT = 2.0
RISK_PER_TRADE = 0.02
MAX_POSITION_VALUE = 5000.0
MIN_NOTIONAL_VALUE = 10.0

# Cost parameters
FEE_RATE = 0.0004  # 0.04% taker
SLIPPAGE_ENTRY_BPS = 5  # 0.05% entry slippage
SLIPPAGE_SL_BPS = 10  # 0.10% stop loss slippage (worse)
SLIPPAGE_TP_BPS = 3  # 0.03% take profit slippage

# Strategy parameters
DONCHIAN_PERIODS = [20, 35, 50, 65, 80]
SIGNAL_THRESHOLD = 0.4
ATR_PERIOD = 14

# Backtest parameters
INITIAL_CAPITAL = 10000
BACKTEST_START = '2020-01-01'
BACKTEST_END = '2025-11-30'
DATA_DIR = 'data/parquet'

# Symbol configuration
COIN_CONFIG = {
    'BTC': {'strategy': 'swing-ensemble', 'trailing_mult': 0.5},
    'ETH': {'strategy': 'swing-ensemble', 'trailing_mult': 0.3},
    'BNB': {'strategy': 'swing-ensemble', 'trailing_mult': 0.3},
    'SOL': {'strategy': 'swing-breakout', 'breakout_period': 20},
}


# ==================== Data structures ====================

@dataclass
class Position:
    """Open position state."""
    symbol: str
    direction: int  # 1=long, -1=short

    # Signal info
    signal_time: pd.Timestamp
    signal_price: float  # theoretical price at signal trigger
    entry_reason: str

    # Execution info
    entry_time: pd.Timestamp
    entry_fill_price: float
    quantity: float
    leverage: int
    margin: float
    notional: float  # qty * entry_fill_price

    # Risk management
    initial_stop: float  # stop price at entry (fixed, used to compute R)
    stop_loss: float  # current stop price (may be updated dynamically)
    take_profit: Optional[float] = None

    # State
    atr_at_entry: float = 0.0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = float('inf')
    strategy: str = ''

    # Costs
    fee_entry: float = 0.0
    slippage_entry: float = 0.0


@dataclass
class Trade:
    """Closed trade record."""
    symbol: str
    direction: int

    # Decision snapshot
    signal_time: pd.Timestamp
    signal_price: float
    entry_reason: str

    # Execution snapshot
    entry_time: pd.Timestamp
    entry_fill_price: float
    exit_time: pd.Timestamp
    exit_fill_price: float
    exit_reason: str

    # Position info
    quantity: float
    leverage: int
    margin: float
    notional_entry: float
    notional_exit: float

    # Risk management
    initial_stop: float
    stop_loss_at_exit: float
    take_profit: Optional[float]

    # Costs
    fee_entry: float
    fee_exit: float
    slippage_entry: float
    slippage_exit: float

    # Analysis metrics
    atr_at_entry: float
    r_multiple: float
    pnl_usd_gross: float
    pnl_usd_net: float
    pnl_pct_on_equity: float

    # Capital state
    equity_before: float
    equity_after: float
    hold_hours: float
    strategy: str = ''


@dataclass
class PendingEntry:
    """Pending entry signal awaiting execution."""
    symbol: str
    signal_time: pd.Timestamp
    signal_price: float
    entry_reason: str
    atr: float
    stop_loss: float
    take_profit: Optional[float]
    strategy: str
    config: dict


# ==================== Backtest engine ====================

class RealisticBacktestEngine:
    """
    Realistic backtest engine.

    Core improvements:
    1. Signal and execution separated (T-day signal, T+1 open execution)
    2. Fee calculated on notional
    3. Slippage model (different for entry/SL/TP)
    4. Pessimistic intrabar assumption
    5. Trailing stop written back to Position
    """

    def __init__(
        self,
        use_next_open: bool = True,
        use_fee: bool = True,
        use_slippage: bool = True,
        use_pessimistic_intrabar: bool = True,
        name: str = "realistic"
    ):
        """
        Args:
            use_next_open: P0-1 enter at next bar open
            use_fee: P0-2 apply fees
            use_slippage: P0-3 apply slippage
            use_pessimistic_intrabar: P1-1 pessimistic assumption
            name: engine name (for reporting)
        """
        self.use_next_open = use_next_open
        self.use_fee = use_fee
        self.use_slippage = use_slippage
        self.use_pessimistic_intrabar = use_pessimistic_intrabar
        self.name = name

        self.loader = DataLoader(DATA_DIR)
        self.trades: List[Trade] = []
        self.capital_history: List[Dict] = []

        # Account state
        self.equity = INITIAL_CAPITAL
        self.positions: Dict[str, Position] = {}
        self.pending_entries: Dict[str, PendingEntry] = {}

    @property
    def available_capital(self) -> float:
        """Available capital = equity - used margin."""
        used_margin = sum(p.margin for p in self.positions.values())
        return self.equity - used_margin

    # ==================== Position sizing ====================

    def calculate_safe_leverage(self, price: float, atr: float) -> int:
        """Calculate safe leverage."""
        stop_distance = atr * DEFAULT_ATR_STOP_MULT
        stop_distance_pct = stop_distance / price
        raw_leverage = 1.0 / (stop_distance_pct * LEVERAGE_BUFFER)
        return int(max(1, min(MAX_LEVERAGE, raw_leverage)))

    def calculate_position_size(
        self,
        price: float,
        atr: float,
        leverage: int
    ) -> Tuple[float, float, float]:
        """
        Calculate position size.

        Returns:
            (quantity, margin, notional)
        """
        available = self.available_capital
        risk_amount = available * RISK_PER_TRADE
        stop_distance = atr * DEFAULT_ATR_STOP_MULT

        if stop_distance <= 0:
            return 0, 0, 0

        # Risk-based position
        quantity = risk_amount / stop_distance
        notional = quantity * price

        # Position cap
        max_notional = min(
            available * leverage,
            MAX_POSITION_VALUE
        )

        if notional > max_notional:
            notional = max_notional
            quantity = notional / price

        if notional < MIN_NOTIONAL_VALUE:
            return 0, 0, 0

        margin = notional / leverage
        return quantity, margin, notional

    # ==================== Cost calculation ====================

    def apply_entry_slippage(self, price: float, direction: int) -> float:
        """Apply entry slippage."""
        if not self.use_slippage:
            return price
        slip = price * SLIPPAGE_ENTRY_BPS / 10000
        return price + slip * direction  # long buys higher, short sells lower

    def apply_exit_slippage(self, price: float, direction: int, exit_type: str) -> float:
        """Apply exit slippage."""
        if not self.use_slippage:
            return price

        if exit_type == 'SL':
            slip_bps = SLIPPAGE_SL_BPS
        elif exit_type == 'TP':
            slip_bps = SLIPPAGE_TP_BPS
        else:
            slip_bps = SLIPPAGE_ENTRY_BPS

        slip = price * slip_bps / 10000
        return price - slip * direction  # long sells lower, short buys higher

    def calculate_fee(self, notional: float) -> float:
        """Calculate fee."""
        if not self.use_fee:
            return 0.0
        return notional * FEE_RATE

    # ==================== Data loading ====================

    def load_all_data(self) -> Dict[str, pd.DataFrame]:
        """Load and preprocess data for all symbols."""
        all_data = {}

        for symbol, config in COIN_CONFIG.items():
            df = self.loader.load(symbol, '1d')
            if df is None or df.empty:
                print(f"Warning: No data for {symbol}")
                continue

            # ATR
            df['atr'] = self._calculate_atr(df, ATR_PERIOD)

            # Donchian channels
            for period in DONCHIAN_PERIODS:
                df[f'dc_upper_{period}'] = df['high'].rolling(period).max()
                df[f'dc_lower_{period}'] = df['low'].rolling(period).min()

            # Signal calculation
            if config['strategy'] == 'swing-ensemble':
                signals = pd.DataFrame(index=df.index)
                for period in DONCHIAN_PERIODS:
                    upper = df[f'dc_upper_{period}'].shift(1)
                    signal = (df['close'] > upper).astype(int)
                    signals[f's_{period}'] = signal
                df['ensemble'] = signals.mean(axis=1)
                df['trade_signal'] = df['ensemble'] > SIGNAL_THRESHOLD

                # Trailing stop period
                exit_period = max(int(DONCHIAN_PERIODS[2] * config['trailing_mult']), 5)
                df['trailing_stop_level'] = df['low'].rolling(exit_period).min().shift(1)
            else:
                # swing-breakout
                df['trade_signal'] = df['close'] > df['dc_upper_20'].shift(1)
                df['trailing_stop_level'] = None

            df = df[df['timestamp'] >= BACKTEST_START].copy()
            df = df.set_index('timestamp')

            all_data[symbol] = df

        return all_data

    def _calculate_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Calculate ATR."""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)

        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return tr.rolling(period).mean()

    # ==================== Main loop ====================

    def run(self) -> Dict[str, Any]:
        """Run backtest."""
        all_data = self.load_all_data()

        # Collect all trading dates
        all_dates = set()
        for df in all_data.values():
            all_dates.update(df.index.tolist())
        all_dates = sorted(all_dates)

        print(f"\n{'='*60}")
        print(f"Engine: {self.name}")
        print(f"  use_next_open: {self.use_next_open}")
        print(f"  use_fee: {self.use_fee}")
        print(f"  use_slippage: {self.use_slippage}")
        print(f"  use_pessimistic_intrabar: {self.use_pessimistic_intrabar}")
        print(f"Period: {all_dates[0].date()} ~ {all_dates[-1].date()}")
        print(f"Initial: ${INITIAL_CAPITAL:,}")
        print(f"{'='*60}\n")

        # Iterate over dates
        for i, date in enumerate(all_dates):
            # 1. Execute pending entries (T+1 open)
            if self.use_next_open:
                self._execute_pending_entries(all_data, date)

            # 2. Update position state (trailing stop, etc.)
            self._update_positions(all_data, date)

            # 3. Check exits
            for symbol in list(self.positions.keys()):
                if symbol not in all_data or date not in all_data[symbol].index:
                    continue
                bar = all_data[symbol].loc[date]
                self._check_exit(symbol, bar, date)

            # 4. Check entry signals
            for symbol, config in COIN_CONFIG.items():
                if symbol not in all_data or date not in all_data[symbol].index:
                    continue
                if symbol in self.positions:
                    continue
                if symbol in self.pending_entries:
                    continue

                bar = all_data[symbol].loc[date]
                self._check_entry_signal(symbol, bar, date, config)

            # 5. Record capital curve
            self._record_capital(date)

        return self._generate_report()

    # ==================== Entry logic ====================

    def _check_entry_signal(
        self,
        symbol: str,
        bar: pd.Series,
        date: pd.Timestamp,
        config: dict
    ):
        """Check entry signal (generate pending only, no direct entry)."""
        if not bar['trade_signal']:
            return

        price = bar['close']
        atr = bar['atr']
        if pd.isna(atr):
            atr = price * 0.03

        # Compute stop/target
        stop_loss = price - atr * DEFAULT_ATR_STOP_MULT
        take_profit = None
        if config['strategy'] == 'swing-breakout':
            take_profit = price + atr * 6

        entry_reason = 'DC_BREAKOUT' if config['strategy'] == 'swing-breakout' else 'ENSEMBLE'

        if self.use_next_open:
            # Record pending; execute on next bar
            self.pending_entries[symbol] = PendingEntry(
                symbol=symbol,
                signal_time=date,
                signal_price=price,
                entry_reason=entry_reason,
                atr=atr,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=config['strategy'],
                config=config
            )
        else:
            # Fairy-tale mode: enter at current bar close
            self._execute_entry(
                symbol=symbol,
                signal_time=date,
                signal_price=price,
                entry_time=date,
                entry_price=price,
                entry_reason=entry_reason,
                atr=atr,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=config['strategy']
            )

    def _execute_pending_entries(self, all_data: Dict, date: pd.Timestamp):
        """Execute pending entries using current bar open price."""
        for symbol in list(self.pending_entries.keys()):
            if symbol not in all_data or date not in all_data[symbol].index:
                continue
            if symbol in self.positions:
                del self.pending_entries[symbol]
                continue

            pending = self.pending_entries[symbol]
            bar = all_data[symbol].loc[date]
            entry_price = bar['open']

            # Recompute stop based on actual entry price
            atr = pending.atr
            stop_loss = entry_price - atr * DEFAULT_ATR_STOP_MULT
            take_profit = None
            if pending.take_profit is not None:
                take_profit = entry_price + atr * 6

            self._execute_entry(
                symbol=symbol,
                signal_time=pending.signal_time,
                signal_price=pending.signal_price,
                entry_time=date,
                entry_price=entry_price,
                entry_reason=pending.entry_reason,
                atr=atr,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=pending.strategy
            )

            del self.pending_entries[symbol]

    def _execute_entry(
        self,
        symbol: str,
        signal_time: pd.Timestamp,
        signal_price: float,
        entry_time: pd.Timestamp,
        entry_price: float,
        entry_reason: str,
        atr: float,
        stop_loss: float,
        take_profit: Optional[float],
        strategy: str
    ):
        """Execute entry."""
        # Apply slippage
        fill_price = self.apply_entry_slippage(entry_price, 1)  # long
        slippage_entry = fill_price - entry_price

        # Calculate position size
        leverage = self.calculate_safe_leverage(fill_price, atr)
        quantity, margin, notional = self.calculate_position_size(fill_price, atr, leverage)

        if quantity <= 0:
            return

        # Calculate fee
        fee_entry = self.calculate_fee(notional)

        # Deduct fee
        self.equity -= fee_entry

        # Create position
        position = Position(
            symbol=symbol,
            direction=1,
            signal_time=signal_time,
            signal_price=signal_price,
            entry_reason=entry_reason,
            entry_time=entry_time,
            entry_fill_price=fill_price,
            quantity=quantity,
            leverage=leverage,
            margin=margin,
            notional=notional,
            initial_stop=stop_loss,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_entry=atr,
            highest_since_entry=fill_price,
            strategy=strategy,
            fee_entry=fee_entry,
            slippage_entry=slippage_entry
        )

        self.positions[symbol] = position

        print(f"[{entry_time.date()}] ENTRY {symbol}: "
              f"signal={signal_price:.2f}, fill={fill_price:.2f}, "
              f"qty={quantity:.4f}, lev={leverage}x, fee=${fee_entry:.2f}")

    # ==================== Position update ====================

    def _update_positions(self, all_data: Dict, date: pd.Timestamp):
        """Update position state (P1-2: write trailing stop back to Position)."""
        for symbol, position in self.positions.items():
            if symbol not in all_data or date not in all_data[symbol].index:
                continue

            bar = all_data[symbol].loc[date]

            # Update highest price
            if bar['high'] > position.highest_since_entry:
                position.highest_since_entry = bar['high']

            # Update trailing stop (write back to Position)
            if position.strategy == 'swing-ensemble':
                trailing_level = bar['trailing_stop_level']
                if not pd.isna(trailing_level) and trailing_level > position.stop_loss:
                    position.stop_loss = trailing_level  # P1-2: write back

    # ==================== Exit logic ====================

    def _check_exit(self, symbol: str, bar: pd.Series, date: pd.Timestamp):
        """Check exit (P1-1: pessimistic assumption)."""
        position = self.positions[symbol]

        # Check trigger conditions
        hit_sl = bar['low'] <= position.stop_loss
        hit_tp = position.take_profit is not None and bar['high'] >= position.take_profit

        exit_price = None
        exit_reason = None

        if self.use_pessimistic_intrabar:
            # P1-1: pessimistic - stop loss takes priority within same bar
            if hit_sl and hit_tp:
                exit_price = position.stop_loss
                exit_reason = 'SL'
            elif hit_sl:
                exit_price = position.stop_loss
                exit_reason = 'SL' if position.stop_loss < position.entry_fill_price else 'TRAIL'
            elif hit_tp:
                exit_price = position.take_profit
                exit_reason = 'TP'
        else:
            # Fairy-tale mode: code order
            if hit_sl:
                exit_price = position.stop_loss
                exit_reason = 'SL' if position.stop_loss < position.entry_fill_price else 'TRAIL'
            elif hit_tp:
                exit_price = position.take_profit
                exit_reason = 'TP'

        # swing-breakout time exit
        if exit_price is None and position.strategy == 'swing-breakout':
            hold_days = (date - position.entry_time).days
            if hold_days > 60:
                exit_price = bar['close']
                exit_reason = 'TIME'

        if exit_price is not None:
            self._execute_exit(symbol, position, exit_price, exit_reason, date)

    def _execute_exit(
        self,
        symbol: str,
        position: Position,
        theoretical_exit_price: float,
        exit_reason: str,
        exit_time: pd.Timestamp
    ):
        """Execute exit."""
        # Apply slippage
        fill_price = self.apply_exit_slippage(
            theoretical_exit_price,
            position.direction,
            exit_reason
        )
        slippage_exit = theoretical_exit_price - fill_price

        # Calculate fee
        notional_exit = position.quantity * fill_price
        fee_exit = self.calculate_fee(notional_exit)

        # Calculate PnL
        pnl_gross = (fill_price - position.entry_fill_price) * position.quantity * position.direction
        pnl_net = pnl_gross - fee_exit

        # R multiple
        risk_per_share = abs(position.entry_fill_price - position.initial_stop)
        if risk_per_share > 0:
            r_multiple = (fill_price - position.entry_fill_price) / risk_per_share * position.direction
        else:
            r_multiple = 0

        equity_before = self.equity
        self.equity += pnl_net

        # Hold duration
        hold_hours = (exit_time - position.entry_time).total_seconds() / 3600

        # Record trade
        trade = Trade(
            symbol=symbol,
            direction=position.direction,
            signal_time=position.signal_time,
            signal_price=position.signal_price,
            entry_reason=position.entry_reason,
            entry_time=position.entry_time,
            entry_fill_price=position.entry_fill_price,
            exit_time=exit_time,
            exit_fill_price=fill_price,
            exit_reason=exit_reason,
            quantity=position.quantity,
            leverage=position.leverage,
            margin=position.margin,
            notional_entry=position.notional,
            notional_exit=notional_exit,
            initial_stop=position.initial_stop,
            stop_loss_at_exit=position.stop_loss,
            take_profit=position.take_profit,
            fee_entry=position.fee_entry,
            fee_exit=fee_exit,
            slippage_entry=position.slippage_entry,
            slippage_exit=slippage_exit,
            atr_at_entry=position.atr_at_entry,
            r_multiple=r_multiple,
            pnl_usd_gross=pnl_gross,
            pnl_usd_net=pnl_net,
            pnl_pct_on_equity=pnl_net / equity_before if equity_before > 0 else 0,
            equity_before=equity_before,
            equity_after=self.equity,
            hold_hours=hold_hours,
            strategy=position.strategy
        )
        self.trades.append(trade)

        del self.positions[symbol]

        pnl_pct = (fill_price - position.entry_fill_price) / position.entry_fill_price * 100
        print(f"[{exit_time.date()}] EXIT {symbol}: {exit_reason}, "
              f"fill={fill_price:.2f}, pnl={pnl_pct:+.2f}%, R={r_multiple:+.2f}, "
              f"fee=${fee_exit:.2f}, equity=${self.equity:.2f}")

    # ==================== Capital recording ====================

    def _record_capital(self, date: pd.Timestamp):
        """Record capital curve."""
        gross_exposure = sum(p.notional for p in self.positions.values())
        symbols_held = list(self.positions.keys())
        max_single = max([p.notional for p in self.positions.values()], default=0)

        self.capital_history.append({
            'date': date,
            'equity': self.equity,
            'gross_exposure': gross_exposure,
            'positions_count': len(self.positions),
            'symbols_held': ','.join(symbols_held) if symbols_held else '',
            'max_single_exposure': max_single / self.equity if self.equity > 0 else 0
        })

    # ==================== Report generation ====================

    def _generate_report(self) -> Dict[str, Any]:
        """Generate backtest report."""
        if not self.trades:
            return {'error': 'No trades'}

        # Convert to DataFrame
        trades_df = pd.DataFrame([t.__dict__ for t in self.trades])
        capital_df = pd.DataFrame(self.capital_history)

        # Portfolio-level metrics
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t.pnl_usd_net > 0]
        losing_trades = [t for t in self.trades if t.pnl_usd_net <= 0]

        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

        gross_profit = sum(t.pnl_usd_net for t in winning_trades)
        gross_loss = abs(sum(t.pnl_usd_net for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        avg_trade_return = np.mean([t.pnl_pct_on_equity for t in self.trades])
        avg_r_multiple = np.mean([t.r_multiple for t in self.trades])

        # Max drawdown
        equity_series = capital_df['equity']
        peak = equity_series.expanding().max()
        drawdown = (equity_series - peak) / peak
        max_drawdown = abs(drawdown.min())

        # Hold duration
        avg_hold_hours = np.mean([t.hold_hours for t in self.trades])

        # Cost stats
        total_fees = sum(t.fee_entry + t.fee_exit for t in self.trades)
        avg_slippage_entry = np.mean([t.slippage_entry for t in self.trades])
        avg_slippage_exit = np.mean([t.slippage_exit for t in self.trades])

        # Per-symbol stats
        per_symbol = {}
        for symbol in COIN_CONFIG.keys():
            symbol_trades = [t for t in self.trades if t.symbol == symbol]
            if not symbol_trades:
                continue

            s_winning = [t for t in symbol_trades if t.pnl_usd_net > 0]
            s_losing = [t for t in symbol_trades if t.pnl_usd_net <= 0]
            s_profit = sum(t.pnl_usd_net for t in s_winning)
            s_loss = abs(sum(t.pnl_usd_net for t in s_losing))

            per_symbol[symbol] = {
                'trades': len(symbol_trades),
                'win_rate': len(s_winning) / len(symbol_trades),
                'pf': s_profit / s_loss if s_loss > 0 else float('inf'),
                'avg_return': np.mean([t.pnl_pct_on_equity for t in symbol_trades]),
                'avg_r': np.mean([t.r_multiple for t in symbol_trades]),
                'avg_hold_hours': np.mean([t.hold_hours for t in symbol_trades])
            }

        # Acceptance criteria
        fails = []
        if profit_factor < 1.25:
            fails.append(f"PF={profit_factor:.2f}<1.25")
        if max_drawdown > 0.25:
            fails.append(f"MDD={max_drawdown:.1%}>25%")
        if avg_trade_return < 0.0015:
            fails.append(f"AVG={avg_trade_return:.4f}<0.15%")
        if total_trades < 200:
            fails.append(f"TRADES={total_trades}<200")

        # Passing symbols (PF>=1.2, trades>=10)
        passed_symbols = sum(
            1 for s in per_symbol.values()
            if s['trades'] >= 10 and s['pf'] >= 1.2
        )
        if passed_symbols < 2:
            fails.append(f"SYMBOLS={passed_symbols}<2")

        verdict = "PASS" if not fails else "FAIL"

        # Summary
        summary = {
            'engine': self.name,
            'start_date': str(self.trades[0].entry_time.date()),
            'end_date': str(self.trades[-1].exit_time.date()),
            'initial_capital': INITIAL_CAPITAL,
            'final_equity': self.equity,
            'total_return': (self.equity - INITIAL_CAPITAL) / INITIAL_CAPITAL,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_trade_return': avg_trade_return,
            'avg_r_multiple': avg_r_multiple,
            'max_drawdown': max_drawdown,
            'avg_hold_hours': avg_hold_hours,
            'total_fees': total_fees,
            'avg_slippage_entry_bps': avg_slippage_entry / np.mean([t.entry_fill_price for t in self.trades]) * 10000,
            'avg_slippage_exit_bps': avg_slippage_exit / np.mean([t.exit_fill_price for t in self.trades]) * 10000,
            'passed_symbols': passed_symbols,
            'verdict': verdict,
            'fail_reasons': fails
        }

        return {
            'summary': summary,
            'per_symbol': per_symbol,
            'trades': trades_df,
            'capital_history': capital_df
        }


# ==================== Entry point ====================

def run_comparison():
    """Run 4-version comparison experiment."""
    configs = [
        # 1. Baseline (fairy-tale)
        {
            'name': '1_baseline',
            'use_next_open': False,
            'use_fee': False,
            'use_slippage': False,
            'use_pessimistic_intrabar': False
        },
        # 2. P0: next_open + fee
        {
            'name': '2_P0_nextopen_fee',
            'use_next_open': True,
            'use_fee': True,
            'use_slippage': False,
            'use_pessimistic_intrabar': False
        },
        # 3. P0 + P1: add pessimistic intrabar
        {
            'name': '3_P0P1_pessimistic',
            'use_next_open': True,
            'use_fee': True,
            'use_slippage': False,
            'use_pessimistic_intrabar': True
        },
        # 4. P0 + P1 + slippage (full realistic)
        {
            'name': '4_full_realistic',
            'use_next_open': True,
            'use_fee': True,
            'use_slippage': True,
            'use_pessimistic_intrabar': True
        }
    ]

    results = []

    for cfg in configs:
        print(f"\n{'#'*60}")
        print(f"# Running: {cfg['name']}")
        print(f"{'#'*60}")

        engine = RealisticBacktestEngine(
            use_next_open=cfg['use_next_open'],
            use_fee=cfg['use_fee'],
            use_slippage=cfg['use_slippage'],
            use_pessimistic_intrabar=cfg['use_pessimistic_intrabar'],
            name=cfg['name']
        )

        result = engine.run()
        results.append(result)

    # Print comparison table
    print("\n" + "="*80)
    print("COMPARISON SUMMARY")
    print("="*80)

    headers = ['Engine', 'Trades', 'WinRate', 'PF', 'AvgRet%', 'AvgR', 'MDD%', 'Final$', 'Verdict']
    print(f"{headers[0]:<25} {headers[1]:>7} {headers[2]:>8} {headers[3]:>6} {headers[4]:>8} {headers[5]:>6} {headers[6]:>6} {headers[7]:>8} {headers[8]:<10}")
    print("-"*80)

    for r in results:
        s = r['summary']
        print(f"{s['engine']:<25} {s['total_trades']:>7} {s['win_rate']:>7.1%} {s['profit_factor']:>6.2f} "
              f"{s['avg_trade_return']*100:>7.2f}% {s['avg_r_multiple']:>6.2f} {s['max_drawdown']*100:>5.1f}% "
              f"{s['final_equity']:>8.0f} {s['verdict']:<10}")

    print("\n" + "="*80)
    print("PER-SYMBOL BREAKDOWN (Full Realistic)")
    print("="*80)

    full_result = results[-1]
    for symbol, stats in full_result['per_symbol'].items():
        print(f"{symbol}: trades={stats['trades']}, WR={stats['win_rate']:.1%}, "
              f"PF={stats['pf']:.2f}, AvgR={stats['avg_r']:.2f}, Hold={stats['avg_hold_hours']:.0f}h")

    print("\n" + "="*80)
    print(f"FINAL VERDICT: {full_result['summary']['verdict']}")
    if full_result['summary']['fail_reasons']:
        print(f"FAIL REASONS: {full_result['summary']['fail_reasons']}")
    print("="*80)

    return results


if __name__ == '__main__':
    run_comparison()
