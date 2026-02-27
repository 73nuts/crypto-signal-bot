"""
Performance card constants.

Centralizes colors, font paths, and layout values.
"""

# ============================================
# Deep-Sea Quant Pro color palette
# ============================================
COLORS = {
    'background': '#0D1117',
    'card_bg': '#161B22',

    'text_primary': '#F0F6FC',
    'text_secondary': '#8B949E',
    'text_header': '#C9D1D9',

    'gold': '#F3BA2F',             # Ignis brand color
    'accent': '#FF6B6B',

    'success': '#00FFC2',          # neon green (profit)
    'success_bg': '#0D3B2D',
    'danger': '#FF3B69',           # coral red (loss)
    'danger_bg': '#3B1A1A',

    'sparkline_positive': '#00FFC2',
    'sparkline_negative': '#FF3B69',
    'sparkline_shadow': '#F3BA2F',
    'zero_line': '#8B949E',
}

# ============================================
# Coin brand colors
# ============================================
COIN_COLORS = {
    'BTC': '#F7931A',
    'ETH': '#627EEA',
    'BNB': '#F3BA2F',
    'SOL': '#9945FF',
}

DEFAULT_COIN_COLOR = '#8B949E'

# ============================================
# Font paths (priority order)
# ============================================
FONT_PATHS_MONO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",  # Docker/Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",       # Docker/Linux (regular)
    "/System/Library/Fonts/Monaco.ttf",                           # macOS
    "/System/Library/Fonts/Menlo.ttc",                            # macOS
]

FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",      # Docker/Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",           # Docker/Linux (regular)
    "/System/Library/Fonts/Helvetica.ttc",                        # macOS
]

FONT_SIZES = {
    'title': 48,
    'subtitle': 28,
    'heading': 32,
    'body': 20,
    'small': 20,
    'table_header': 16,
    'badge': 12,
}

# ============================================
# Layout
# ============================================
LAYOUT = {
    'padding': 60,
    'row_height': 48,
    'coin_badge_size': 24,
    'sparkline_height': 100,
    'sparkline_width': 200,
    'image_width': 800,
}

# ============================================
# Trade table column definitions (anchor-based positioning)
# ============================================
# Column format: (name, anchor_x, anchor, header, is_mono)
# PIL anchor: 'lm' = left-middle, 'rm' = right-middle, 'mm' = center-middle
#
# Logical X positions:
#   Badge(76) Symbol(110) Date(205) Entry(390) Exit(530) Hold(560) P/L(740)
#
TRADE_TABLE_COLUMNS = [
    ('badge',  76,   'mm', '',       False),
    ('symbol', 110,  'lm', 'Sym',    False),
    ('date',   205,  'lm', 'Date',   False),
    ('entry',  390,  'rm', 'Entry',  True),
    ('exit',   530,  'rm', 'Exit',   True),
    ('hold',   560,  'lm', 'Hold',   False),
    ('pnl',    740,  'rm', 'P/L',    False),
]
