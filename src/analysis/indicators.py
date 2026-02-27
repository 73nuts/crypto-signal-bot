"""
Technical indicators calculation module.
Provides pure-function technical indicator calculations with support for multiple column name formats.
"""

import pandas as pd


class TechnicalIndicators:
    """Technical indicator calculator - pure static methods, stateless."""

    @staticmethod
    def _get_column_name(df, standard_name):
        """Get compatible column name (case-insensitive)."""
        capitalized = standard_name.capitalize()
        if capitalized in df.columns:
            return capitalized
        elif standard_name.lower() in df.columns:
            return standard_name.lower()
        else:
            return standard_name.lower()

    @classmethod
    def calculate_ma(cls, df, period=25):
        """Calculate moving average.

        Args:
            df: DataFrame with price data
            period: Moving average period

        Returns:
            Series: Moving average values
        """
        close_col = cls._get_column_name(df, 'close')
        return df[close_col].rolling(window=period).mean()

    @classmethod
    def calculate_macd(cls, df, fast=12, slow=26, signal=9):
        """Calculate MACD indicator.

        Args:
            df: DataFrame with price data
            fast: Fast EMA period
            slow: Slow EMA period
            signal: Signal line period

        Returns:
            Tuple: (macd, signal_line, histogram)
        """
        close_col = cls._get_column_name(df, 'close')

        exp1 = df[close_col].ewm(span=fast).mean()
        exp2 = df[close_col].ewm(span=slow).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal).mean()
        histogram = macd - signal_line

        return macd, signal_line, histogram

    @classmethod
    def calculate_rsi(cls, df, period=14):
        """Calculate RSI indicator.

        Args:
            df: DataFrame with price data
            period: RSI period

        Returns:
            Series: RSI values
        """
        close_col = cls._get_column_name(df, 'close')

        delta = df[close_col].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @classmethod
    def calculate_atr(cls, df, period=14):
        """Calculate ATR (Average True Range).

        Args:
            df: DataFrame with price data
            period: ATR period

        Returns:
            Series: ATR values
        """
        high_col = cls._get_column_name(df, 'high')
        low_col = cls._get_column_name(df, 'low')
        close_col = cls._get_column_name(df, 'close')

        tr1 = df[high_col] - df[low_col]
        tr2 = abs(df[high_col] - df[close_col].shift())
        tr3 = abs(df[low_col] - df[close_col].shift())
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return atr

    @classmethod
    def calculate_adx(cls, df, period=14):
        """Calculate ADX (Average Directional Index).

        Args:
            df: DataFrame with price data
            period: ADX period

        Returns:
            Tuple: (adx, plus_di, minus_di)
        """
        high_col = cls._get_column_name(df, 'high')
        low_col = cls._get_column_name(df, 'low')
        close_col = cls._get_column_name(df, 'close')

        # Calculate directional movement
        plus_dm = df[high_col].diff()
        minus_dm = -df[low_col].diff()

        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0

        # Calculate true range
        tr1 = df[high_col] - df[low_col]
        tr2 = abs(df[high_col] - df[close_col].shift())
        tr3 = abs(df[low_col] - df[close_col].shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Smoothed moving averages
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

        # ADX calculation
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        adx = dx.rolling(window=period).mean()

        return adx, plus_di, minus_di

    @classmethod
    def calculate_volume_ma(cls, df, period=20):
        """Calculate volume moving average.

        Args:
            df: DataFrame with volume data
            period: Volume MA period

        Returns:
            Series: Volume moving average
        """
        volume_col = cls._get_column_name(df, 'volume')
        return df[volume_col].rolling(window=period).mean()

    @classmethod
    def calculate_volatility(cls, df, period=20):
        """Calculate volatility.

        Args:
            df: DataFrame with price data
            period: Volatility calculation period

        Returns:
            Series: Volatility values
        """
        close_col = cls._get_column_name(df, 'close')
        returns = df[close_col].pct_change()
        volatility = returns.rolling(window=period).std()
        return volatility

    @classmethod
    def calculate_bollinger_bands(cls, df, period=20, std_dev=2):
        """Calculate Bollinger Bands.

        Args:
            df: DataFrame with price data
            period: Moving average period (default 20)
            std_dev: Standard deviation multiplier (default 2)

        Returns:
            Tuple[Series, Series, Series]: (upper_band, middle_band, lower_band)
        """
        close_col = cls._get_column_name(df, 'close')

        # Middle band = MA20
        middle_band = df[close_col].rolling(window=period).mean()

        # Standard deviation
        std = df[close_col].rolling(window=period).std()

        # Upper band = middle + 2× std dev
        upper_band = middle_band + (std_dev * std)

        # Lower band = middle - 2× std dev
        lower_band = middle_band - (std_dev * std)

        return upper_band, middle_band, lower_band

    @classmethod
    def calculate_obv(cls, df):
        """Calculate OBV (On-Balance Volume).

        OBV accumulates volume to determine money flow direction:
        - Close up -> OBV += volume
        - Close down -> OBV -= volume
        - Close unchanged -> OBV unchanged

        Args:
            df: DataFrame with OHLCV data

        Returns:
            Series: OBV values
        """
        close_col = cls._get_column_name(df, 'close')
        volume_col = cls._get_column_name(df, 'volume')

        obv = pd.Series(index=df.index, dtype='float64')
        obv.iloc[0] = 0

        for i in range(1, len(df)):
            if df[close_col].iloc[i] > df[close_col].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] + df[volume_col].iloc[i]
            elif df[close_col].iloc[i] < df[close_col].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] - df[volume_col].iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i-1]

        return obv

    @classmethod
    def calculate_cmf(cls, df, period=20):
        """Calculate CMF (Chaikin Money Flow).

        CMF measures money flow intensity, range -1 to +1:
        - CMF > 0.1: Strong buying pressure (inflow)
        - CMF < -0.1: Strong selling pressure (outflow)
        - -0.1 < CMF < 0.1: Neutral

        Args:
            df: DataFrame with OHLCV data
            period: Calculation period (default 20)

        Returns:
            Series: CMF values
        """
        close_col = cls._get_column_name(df, 'close')
        high_col = cls._get_column_name(df, 'high')
        low_col = cls._get_column_name(df, 'low')
        volume_col = cls._get_column_name(df, 'volume')

        # Money Flow Multiplier
        # ((close - low) - (high - close)) / (high - low)
        # Simplified as: (2*close - high - low) / (high - low)
        mf_multiplier = ((2 * df[close_col] - df[high_col] - df[low_col]) /
                        (df[high_col] - df[low_col]))

        # Handle high == low case (avoid division by zero)
        mf_multiplier = mf_multiplier.fillna(0)

        # Money Flow Volume
        mf_volume = mf_multiplier * df[volume_col]

        # CMF = sum(MF Volume, period) / sum(Volume, period)
        cmf = (mf_volume.rolling(window=period).sum() /
               df[volume_col].rolling(window=period).sum())

        return cmf

    # ============================================
    # Trend structure indicators
    # ============================================

    @classmethod
    def calculate_ema(cls, df, period=50):
        """Calculate EMA (Exponential Moving Average).

        Args:
            df: DataFrame with price data
            period: EMA period (common: 50, 200)

        Returns:
            Series: EMA values
        """
        close_col = cls._get_column_name(df, 'close')
        return df[close_col].ewm(span=period, adjust=False).mean()

    @classmethod
    def calculate_swing_high(cls, df, period=10):
        """Calculate Swing High (recent peak).

        Args:
            df: DataFrame with price data
            period: Lookback period

        Returns:
            Series: Swing High values
        """
        high_col = cls._get_column_name(df, 'high')
        return df[high_col].rolling(window=period).max()

    @classmethod
    def calculate_swing_low(cls, df, period=10):
        """Calculate Swing Low (recent trough).

        Args:
            df: DataFrame with price data
            period: Lookback period

        Returns:
            Series: Swing Low values
        """
        low_col = cls._get_column_name(df, 'low')
        return df[low_col].rolling(window=period).min()

    @classmethod
    def detect_higher_high(cls, df, lookback=20):
        """Detect Higher High (new high above prior peak).

        Args:
            df: DataFrame with price data
            lookback: Lookback period

        Returns:
            Series: Boolean, True means new high
        """
        high_col = cls._get_column_name(df, 'high')

        current_high = df[high_col]
        prev_high = df[high_col].shift(1).rolling(window=lookback).max()

        return current_high > prev_high

    @classmethod
    def detect_higher_low(cls, df, lookback=20):
        """Detect Higher Low (pullback low above prior low).

        Key structure confirmation during trend pullbacks:
        - Pullback low above prior low = trend continuation
        - Pullback low below prior low = potential trend reversal

        Args:
            df: DataFrame with price data
            lookback: Lookback period

        Returns:
            Series: Boolean, True means pullback low did not break prior low
        """
        low_col = cls._get_column_name(df, 'low')

        current_low = df[low_col]
        prev_low = df[low_col].shift(1).rolling(window=lookback).min()

        return current_low > prev_low

    @classmethod
    def detect_reversal_candle(cls, df):
        """Detect bullish reversal candlestick patterns.

        Identifies bullish reversal patterns:
        1. Hammer: long lower wick, small body at top
        2. Bullish Engulfing: bullish candle fully covers prior bearish candle
        3. Morning Star: bearish + doji/small body + bullish (3-candle)

        Returns:
            Dict[str, Series]: {
                'hammer': Hammer signal,
                'bullish_engulfing': Bullish engulfing signal,
                'morning_star': Morning star signal,
                'any_bullish': Any bullish reversal signal
            }
        """
        close_col = cls._get_column_name(df, 'close')
        open_col = cls._get_column_name(df, 'open')
        high_col = cls._get_column_name(df, 'high')
        low_col = cls._get_column_name(df, 'low')

        close = df[close_col]
        open_ = df[open_col]
        high = df[high_col]
        low = df[low_col]

        # Calculate candle features
        body = abs(close - open_)
        upper_wick = high - pd.concat([close, open_], axis=1).max(axis=1)
        lower_wick = pd.concat([close, open_], axis=1).min(axis=1) - low
        candle_range = high - low

        # Guard against division by zero
        candle_range = candle_range.replace(0, float('nan'))

        # 1. Hammer: lower wick > 2× body, upper wick < body, bullish close
        hammer = (
            (lower_wick > 2 * body) &
            (upper_wick < body) &
            (close > open_)
        )

        # 2. Bullish Engulfing: current bullish body fully covers prior bearish body
        prev_close = close.shift(1)
        prev_open = open_.shift(1)
        prev_is_bearish = prev_close < prev_open

        bullish_engulfing = (
            prev_is_bearish &
            (close > open_) &                    # Current candle is bullish
            (open_ <= prev_close) &              # Open <= prior close
            (close >= prev_open)                 # Close >= prior open
        )

        # 3. Morning Star: [bearish] + [doji/small body] + [bullish]
        body_ratio = body / candle_range
        prev_body_ratio = body_ratio.shift(1)
        prev2_close = close.shift(2)
        prev2_open = open_.shift(2)
        prev2_is_bearish = prev2_close < prev2_open

        morning_star = (
            prev2_is_bearish &                   # 2 candles ago is bearish
            (prev_body_ratio < 0.3) &            # Prior candle is small body/doji
            (close > open_) &                    # Current candle is bullish
            (close > (prev2_close + prev2_open) / 2)  # Close above midpoint of 2-ago candle
        )

        any_bullish = hammer | bullish_engulfing | morning_star

        return {
            'hammer': hammer.fillna(False),
            'bullish_engulfing': bullish_engulfing.fillna(False),
            'morning_star': morning_star.fillna(False),
            'any_bullish': any_bullish.fillna(False)
        }

    @classmethod
    def detect_bearish_reversal_candle(cls, df):
        """Detect bearish reversal candlestick patterns.

        Identifies bearish reversal patterns:
        1. Shooting Star: long upper wick, small body at bottom
        2. Bearish Engulfing: bearish candle fully covers prior bullish candle
        3. Evening Star: bullish + doji/small body + bearish (3-candle)

        Returns:
            Dict[str, Series]: {
                'shooting_star': Shooting star signal,
                'bearish_engulfing': Bearish engulfing signal,
                'evening_star': Evening star signal,
                'any_bearish': Any bearish reversal signal
            }
        """
        close_col = cls._get_column_name(df, 'close')
        open_col = cls._get_column_name(df, 'open')
        high_col = cls._get_column_name(df, 'high')
        low_col = cls._get_column_name(df, 'low')

        close = df[close_col]
        open_ = df[open_col]
        high = df[high_col]
        low = df[low_col]

        # Calculate candle features
        body = abs(close - open_)
        upper_wick = high - pd.concat([close, open_], axis=1).max(axis=1)
        lower_wick = pd.concat([close, open_], axis=1).min(axis=1) - low
        candle_range = high - low

        # Guard against division by zero
        candle_range = candle_range.replace(0, float('nan'))

        # 1. Shooting Star: upper wick > 2× body, lower wick < body, bearish close
        shooting_star = (
            (upper_wick > 2 * body) &
            (lower_wick < body) &
            (close < open_)
        )

        # 2. Bearish Engulfing: current bearish body fully covers prior bullish body
        prev_close = close.shift(1)
        prev_open = open_.shift(1)
        prev_is_bullish = prev_close > prev_open

        bearish_engulfing = (
            prev_is_bullish &
            (close < open_) &                    # Current candle is bearish
            (open_ >= prev_close) &              # Open >= prior close
            (close <= prev_open)                 # Close <= prior open
        )

        # 3. Evening Star: [bullish] + [doji/small body] + [bearish]
        body_ratio = body / candle_range
        prev_body_ratio = body_ratio.shift(1)
        prev2_close = close.shift(2)
        prev2_open = open_.shift(2)
        prev2_is_bullish = prev2_close > prev2_open

        evening_star = (
            prev2_is_bullish &                   # 2 candles ago is bullish
            (prev_body_ratio < 0.3) &            # Prior candle is small body/doji
            (close < open_) &                    # Current candle is bearish
            (close < (prev2_close + prev2_open) / 2)  # Close below midpoint of 2-ago candle
        )

        any_bearish = shooting_star | bearish_engulfing | evening_star

        return {
            'shooting_star': shooting_star.fillna(False),
            'bearish_engulfing': bearish_engulfing.fillna(False),
            'evening_star': evening_star.fillna(False),
            'any_bearish': any_bearish.fillna(False)
        }

    @classmethod
    def detect_pullback_zone(cls, df, ma_tolerance=0.02):
        """Detect pullback zone.

        Checks whether price is near MA25/EMA50 support zone:
        - Price within MA25 ± tolerance
        - Or within EMA50 ± tolerance

        Args:
            df: DataFrame with price data (must have ma25, ema50 already calculated)
            ma_tolerance: Tolerance ratio (default 2%)

        Returns:
            Series: Boolean, True means in pullback zone
        """
        close_col = cls._get_column_name(df, 'close')
        close = df[close_col]

        # Check near MA25
        if 'ma25' in df.columns:
            ma25 = df['ma25']
            in_ma25_zone = (close >= ma25 * (1 - ma_tolerance)) & (close <= ma25 * (1 + ma_tolerance))
        else:
            in_ma25_zone = pd.Series(False, index=df.index)

        # Check near EMA50
        if 'ema50' in df.columns:
            ema50 = df['ema50']
            in_ema50_zone = (close >= ema50 * (1 - ma_tolerance)) & (close <= ema50 * (1 + ma_tolerance))
        else:
            in_ema50_zone = pd.Series(False, index=df.index)

        return in_ma25_zone | in_ema50_zone

    @classmethod
    def add_all_indicators(
        cls,
        df: pd.DataFrame,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9
    ) -> pd.DataFrame:
        """Add all technical indicators to DataFrame in one pass (in-place).

        Added indicator columns:
        - ma25: 25-period moving average
        - macd: MACD fast line
        - macd_signal: MACD signal line
        - macd_hist: MACD histogram (macd - macd_signal)
        - rsi: 14-period RSI
        - atr: 14-period ATR
        - adx: 14-period ADX
        - plus_di / minus_di: ADX DI+ and DI-
        - volume_ma: 20-period volume moving average
        - volatility: Price volatility
        - bb_upper / bb_middle / bb_lower: Bollinger Bands
        - obv: On-Balance Volume
        - cmf: Chaikin Money Flow (-1 to +1)

        Args:
            df: DataFrame with OHLCV data (must have close, high, low, volume columns)
            macd_fast: MACD fast period (default 12)
            macd_slow: MACD slow period (default 26)
            macd_signal: MACD signal period (default 9)

        Returns:
            pd.DataFrame: Same DataFrame with all indicator columns added

        Example:
            >>> df = exchange_client.fetch_ohlcv('1h', 100)
            >>> df = TechnicalIndicators.add_all_indicators(df, 12, 26, 9)
            >>> print(df.columns.tolist())
            ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'datetime',
             'ma25', 'macd', 'macd_signal', 'macd_hist', 'rsi', 'atr', 'adx',
             'plus_di', 'minus_di', 'volume_ma', 'volatility',
             'bb_upper', 'bb_middle', 'bb_lower', 'obv']
        """
        close_col = cls._get_column_name(df, 'close')

        # MA25
        df['ma25'] = cls.calculate_ma(df, 25)

        # MACD
        df['macd'], df['macd_signal'], df['macd_hist'] = cls.calculate_macd(
            df, macd_fast, macd_slow, macd_signal
        )

        # RSI
        df['rsi'] = cls.calculate_rsi(df, 14)

        # ATR
        df['atr'] = cls.calculate_atr(df, 14)

        # ADX
        df['adx'], df['plus_di'], df['minus_di'] = cls.calculate_adx(df, 14)

        # Volume MA
        df['volume_ma'] = cls.calculate_volume_ma(df, 20)

        # Volatility
        df['returns'] = df[close_col].pct_change()
        df['volatility'] = cls.calculate_volatility(df, 20)

        # Bollinger Bands
        df['bb_upper'], df['bb_middle'], df['bb_lower'] = cls.calculate_bollinger_bands(df, 20, 2)

        # OBV
        df['obv'] = cls.calculate_obv(df)
        df['obv_ma'] = df['obv'].rolling(window=20).mean()  # OBV 20-period MA

        # CMF
        df['cmf'] = cls.calculate_cmf(df, 20)

        # ============================================
        # Trend structure indicators
        # ============================================

        # EMA50, EMA200 (trend identification)
        df['ema50'] = cls.calculate_ema(df, 50)
        df['ema200'] = cls.calculate_ema(df, 200)

        # Swing High/Low (structure identification)
        df['swing_high'] = cls.calculate_swing_high(df, 10)
        df['swing_low'] = cls.calculate_swing_low(df, 10)

        # Higher High / Higher Low (trend structure)
        df['higher_high'] = cls.detect_higher_high(df, 20)
        df['higher_low'] = cls.detect_higher_low(df, 20)

        # Pullback zone detection
        df['in_pullback_zone'] = cls.detect_pullback_zone(df, 0.02)

        # Candlestick reversal patterns
        bullish_patterns = cls.detect_reversal_candle(df)
        df['hammer'] = bullish_patterns['hammer']
        df['bullish_engulfing'] = bullish_patterns['bullish_engulfing']
        df['morning_star'] = bullish_patterns['morning_star']
        df['any_bullish_reversal'] = bullish_patterns['any_bullish']

        bearish_patterns = cls.detect_bearish_reversal_candle(df)
        df['shooting_star'] = bearish_patterns['shooting_star']
        df['bearish_engulfing'] = bearish_patterns['bearish_engulfing']
        df['evening_star'] = bearish_patterns['evening_star']
        df['any_bearish_reversal'] = bearish_patterns['any_bearish']

        return df
