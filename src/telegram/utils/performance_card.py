"""
Performance card image generator.

Produces a tall shareable image containing trade records, statistics,
a cumulative-returns sparkline, and coin badges.
Visual style: Deep-Sea Quant Pro.
"""

import io
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .performance_constants import (
    COIN_COLORS,
    COLORS,
    DEFAULT_COIN_COLOR,
    FONT_PATHS_MONO,
    FONT_PATHS_REGULAR,
    FONT_SIZES,
    LAYOUT,
    TRADE_TABLE_COLUMNS,
)

logger = logging.getLogger(__name__)


def generate_performance_card(
    trades: List[Dict],
    stats: Dict,
    year: int = None,
    bot_username: str = "IgnisQuantBot",
    width: int = None,
    scale: int = 2,
) -> io.BytesIO:
    """
    Generate a high-resolution performance card image.

    Args:
        trades: list of trade dicts [{symbol, entry, exit, pnl, date, hold_days}, ...]
        stats: summary dict {win_rate, avg_rr, total_trades, max_drawdown, ...}
        year: statistics year (defaults to current year)
        bot_username: bot handle shown in footer
        width: logical image width (rendered width = width * scale)
        scale: resolution multiplier (default 2x for Retina)

    Returns:
        BytesIO containing PNG data.
    """
    try:
        if width is None:
            width = LAYOUT['image_width']

        if year is None:
            year = datetime.now().year

        trade_count = len(trades)
        sparkline_section_height = LAYOUT['sparkline_height'] + 50
        height = 160 + sparkline_section_height + 150 + 90 + (trade_count * LAYOUT['row_height']) + 100

        render_width = width * scale
        render_height = height * scale

        img = Image.new('RGB', (render_width, render_height), COLORS['background'])
        draw = ImageDraw.Draw(img)

        fonts = _load_fonts_scaled(scale)

        y_offset = 30 * scale

        y_offset = _draw_header(draw, fonts, render_width, y_offset, year, scale)
        y_offset = _draw_sparkline_section(draw, fonts, trades, render_width, y_offset, scale)
        y_offset = _draw_stats_card(draw, fonts, stats, render_width, y_offset, scale)
        y_offset = _draw_trades_list(draw, fonts, trades, render_width, y_offset, scale)
        _draw_footer(draw, fonts, bot_username, render_width, render_height, scale)

        buffer = io.BytesIO()
        img.save(buffer, format='PNG', optimize=True)
        buffer.seek(0)

        logger.info(f"Performance card generated: {trade_count} trades, {render_width}x{render_height} ({scale}x)")
        return buffer

    except Exception as e:
        logger.error(f"Performance card generation failed: {e}", exc_info=True)
        raise


# ============================================
# Font loading
# ============================================

def _load_fonts() -> Dict:
    """Load fonts at 1x scale."""
    return _load_fonts_scaled(scale=1)


def _load_fonts_scaled(scale: int = 1) -> Dict:
    """Load fonts scaled for high-DPI rendering."""
    fonts = {}

    regular_font_path = _find_font(FONT_PATHS_REGULAR)
    for name, size in FONT_SIZES.items():
        fonts[name] = _load_single_font(regular_font_path, size * scale)

    mono_font_path = _find_font(FONT_PATHS_MONO)
    fonts['mono_body'] = _load_single_font(mono_font_path, FONT_SIZES['body'] * scale)
    fonts['mono_heading'] = _load_single_font(mono_font_path, FONT_SIZES['heading'] * scale)

    return fonts


def _find_font(paths: List[str]) -> Optional[str]:
    """Return the first existing font path, or None."""
    import os
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def _load_single_font(path: Optional[str], size: int) -> ImageFont:
    """Load a font file; falls back to PIL default on failure."""
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ============================================
# Drawing components
# ============================================

def _draw_pill(
    draw: ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont,
    text_color: str,
    bg_color: str,
    padding_x: int = 8,
    padding_y: int = 4,
    radius: int = 6,
) -> int:
    """Draw a rounded pill label. Returns the right-edge X coordinate."""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    pill_width = text_width + padding_x * 2
    pill_height = text_height + padding_y * 2

    draw.rounded_rectangle(
        [x, y, x + pill_width, y + pill_height],
        radius=radius,
        fill=bg_color
    )

    draw.text((x + padding_x, y + padding_y), text, font=font, fill=text_color)

    return x + pill_width


def _draw_coin_badge(
    draw: ImageDraw,
    symbol: str,
    x: int,
    y: int,
    font: ImageFont,
    size: int = 22,
) -> int:
    """Draw a circular coin badge with initial letter. Returns the right-edge X coordinate."""
    color = COIN_COLORS.get(symbol, DEFAULT_COIN_COLOR)

    draw.ellipse(
        [x, y, x + size, y + size],
        fill=color,
        outline='#FFFFFF33',
        width=1
    )

    letter = symbol[0] if symbol else '?'
    center_x = x + size // 2
    center_y = y + size // 2
    draw.text((center_x, center_y), letter, font=font, fill='#FFFFFF', anchor='mm')

    return x + size


def _draw_dashed_line(
    draw: ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: str,
    dash_length: int = 5,
    gap_length: int = 3,
):
    """Draw a horizontal dashed line."""
    x = x1
    while x < x2:
        end_x = min(x + dash_length, x2)
        draw.line([(x, y1), (end_x, y2)], fill=color, width=1)
        x += dash_length + gap_length


def _hex_to_rgba(hex_color: str, alpha: int) -> str:
    """Placeholder: PIL RGB mode does not support RGBA directly; returns hex as-is."""
    return hex_color


# ============================================
# Section drawing
# ============================================

def _draw_header(draw: ImageDraw, fonts: Dict, width: int, y: int, year: int, scale: int = 1) -> int:
    """Draw the title/header section."""
    title = "IGNIS QUANT"
    bbox = draw.textbbox((0, 0), title, font=fonts['title'])
    title_width = bbox[2] - bbox[0]
    draw.text(
        ((width - title_width) // 2, y),
        title,
        font=fonts['title'],
        fill=COLORS['gold']
    )
    y += 60 * scale

    subtitle = f"{year} Trading Performance"
    bbox = draw.textbbox((0, 0), subtitle, font=fonts['subtitle'])
    subtitle_width = bbox[2] - bbox[0]
    draw.text(
        ((width - subtitle_width) // 2, y),
        subtitle,
        font=fonts['subtitle'],
        fill=COLORS['text_secondary']
    )
    y += 50 * scale

    return y


def _draw_sparkline_section(
    draw: ImageDraw,
    fonts: Dict,
    trades: List[Dict],
    width: int,
    y: int,
    scale: int = 1,
) -> int:
    """Draw the cumulative returns sparkline section."""
    padding = LAYOUT['padding'] * scale
    sparkline_height = LAYOUT['sparkline_height'] * scale
    sparkline_width = width - 2 * padding - 100 * scale

    draw.text(
        (padding, y),
        "Cumulative Returns",
        font=fonts['small'],
        fill=COLORS['text_secondary']
    )
    y += 25 * scale

    if len(trades) >= 2:
        _draw_sparkline(
            draw,
            trades,
            x=padding,
            y=y,
            width=sparkline_width,
            height=sparkline_height,
            scale=scale,
        )

        cumulative = sum(t.get('pnl', 0) for t in trades)
        cum_str = f"+{cumulative:.1f}%" if cumulative >= 0 else f"{cumulative:.1f}%"
        cum_color = COLORS['success'] if cumulative >= 0 else COLORS['danger']

        draw.text(
            (padding + sparkline_width + 10 * scale, y + sparkline_height // 2 - 10 * scale),
            cum_str,
            font=fonts['mono_heading'],
            fill=cum_color
        )
    else:
        draw.text(
            (padding, y + sparkline_height // 2 - 10 * scale),
            "Insufficient data for trend",
            font=fonts['small'],
            fill=COLORS['text_secondary']
        )

    return y + sparkline_height + 20 * scale


def _draw_sparkline(
    draw: ImageDraw,
    trades: List[Dict],
    x: int,
    y: int,
    width: int,
    height: int,
    scale: int = 1,
):
    """Draw a smoothed cumulative-return sparkline with gradient fill and key-point annotations."""
    cumulative = [0.0]
    for t in trades:
        cumulative.append(cumulative[-1] + t.get('pnl', 0))

    min_val = min(cumulative)
    max_val = max(cumulative)
    val_range = max_val - min_val if max_val != min_val else 1.0

    step = width / (len(cumulative) - 1) if len(cumulative) > 1 else width
    raw_points = []
    for i, val in enumerate(cumulative):
        px = x + i * step
        py = y + height - ((val - min_val) / val_range) * height
        raw_points.append((px, py))

    smooth_points = _catmull_rom_spline(raw_points, segments=10)

    if min_val < 0 < max_val:
        zero_y = y + height - ((0 - min_val) / val_range) * height
    elif min_val >= 0:
        zero_y = y + height  # all positive: zero line at bottom
    else:
        zero_y = y  # all negative: zero line at top

    final_val = cumulative[-1]
    line_color = COLORS['sparkline_positive'] if final_val >= 0 else COLORS['sparkline_negative']

    _draw_gradient_fill(draw, smooth_points, zero_y, line_color, scale)

    if y < zero_y < y + height:
        _draw_dashed_line(draw, x, int(zero_y), x + width, int(zero_y), COLORS['zero_line'])

    if len(smooth_points) >= 2:
        line_width = 3 * scale
        draw.line(smooth_points, fill=line_color, width=line_width)

    _draw_key_points(draw, raw_points, cumulative, line_color, scale)


def _catmull_rom_spline(points: List[Tuple[float, float]], segments: int = 10) -> List[Tuple[float, float]]:
    """Catmull-Rom spline interpolation. Returns a smoothed point list."""
    if len(points) < 2:
        return points

    result = []

    extended = [points[0]] + list(points) + [points[-1]]

    for i in range(1, len(extended) - 2):
        p0, p1, p2, p3 = extended[i - 1], extended[i], extended[i + 1], extended[i + 2]

        for t_idx in range(segments):
            t = t_idx / segments

            t2 = t * t
            t3 = t2 * t

            x = 0.5 * ((2 * p1[0]) +
                       (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)

            y = 0.5 * ((2 * p1[1]) +
                       (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)

            result.append((x, y))

    result.append(points[-1])
    return result


def _draw_gradient_fill(
    draw: ImageDraw,
    points: List[Tuple[float, float]],
    zero_y: float,
    color: str,
    scale: int = 1,
):
    """Draw a gradient fill between the sparkline and the zero baseline."""
    if len(points) < 2:
        return

    def hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    base_rgb = hex_to_rgb(color)
    bg_rgb = hex_to_rgb(COLORS['background'])

    num_layers = 20
    for layer in range(num_layers):
        alpha = 0.3 * (1 - layer / num_layers)

        blended = tuple(int(base_rgb[i] * alpha + bg_rgb[i] * (1 - alpha)) for i in range(3))
        layer_color = '#{:02x}{:02x}{:02x}'.format(*blended)

        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]

            layer_y1 = y1 + (zero_y - y1) * (layer / num_layers)
            layer_y2 = y2 + (zero_y - y2) * (layer / num_layers)

            draw.line([(x1, layer_y1), (x2, layer_y2)], fill=layer_color, width=1)


def _draw_key_points(
    draw: ImageDraw,
    points: List[Tuple[float, float]],
    values: List[float],
    color: str,
    scale: int = 1,
):
    """
    Draw annotated data points on the sparkline.

    Visual hierarchy: regular points -> max/min highlights -> end point.
    """
    if len(points) < 2:
        return

    normal_radius = 4 * scale
    highlight_radius = 5 * scale
    end_radius = 6 * scale

    max_idx = max(range(1, len(values)), key=lambda i: values[i])
    min_idx = min(range(1, len(values)), key=lambda i: values[i])
    end_idx = len(points) - 1
    special_indices = {max_idx, min_idx, end_idx}

    for i in range(1, len(points)):
        if i in special_indices:
            continue
        px, py = points[i]
        _draw_dot(draw, px, py, normal_radius, color)

    if max_idx != end_idx:
        px, py = points[max_idx]
        _draw_dot_with_border(draw, px, py, highlight_radius, color, '#FFFFFF')

    if min_idx not in (0, end_idx, max_idx) and values[min_idx] < 0:
        px, py = points[min_idx]
        _draw_dot_with_border(draw, px, py, highlight_radius, COLORS['danger'], '#FFFFFF')

    end_x, end_y = points[end_idx]
    _draw_dot_with_border(draw, end_x, end_y, end_radius, color, '#FFFFFF')


def _draw_dot(draw: ImageDraw, x: float, y: float, radius: int, color: str):
    """Draw a solid filled circle."""
    draw.ellipse(
        [x - radius, y - radius, x + radius, y + radius],
        fill=color
    )


def _draw_dot_with_border(
    draw: ImageDraw,
    x: float,
    y: float,
    radius: int,
    fill_color: str,
    border_color: str,
    border_width: int = 2,
):
    """Draw a circle with a border ring."""
    outer_radius = radius + border_width
    draw.ellipse(
        [x - outer_radius, y - outer_radius, x + outer_radius, y + outer_radius],
        fill=border_color
    )
    draw.ellipse(
        [x - radius, y - radius, x + radius, y + radius],
        fill=fill_color
    )


def _draw_sparkline_shadow(
    draw: ImageDraw,
    points: List[Tuple[float, float]],
    zero_y: float,
    x: int,
    width: int,
    height: int,
    top_y: int,
):
    """Draw a gradient shadow under the sparkline."""
    if len(points) < 2:
        return

    shadow_base = COLORS['sparkline_shadow']

    for i in range(len(points) - 1):
        px1, py1 = points[i]
        px2, py2 = points[i + 1]

        start_y = min(py1, py2)
        end_y = zero_y

        if start_y > end_y:
            start_y, end_y = end_y, start_y

        total_rows = int(end_y - start_y)
        if total_rows <= 0:
            continue

        layers = [
            (0.15, 0),
            (0.08, total_rows // 3),
            (0.03, total_rows * 2 // 3),
        ]

        for alpha, offset in layers:
            if offset < total_rows:
                blend_color = _blend_color(shadow_base, COLORS['background'], alpha)
                layer_y = start_y + offset
                draw.line([(px1, layer_y), (px2, layer_y)], fill=blend_color, width=1)


def _blend_color(fg_hex: str, bg_hex: str, alpha: float) -> str:
    """Alpha-blend two hex colors."""
    def hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def rgb_to_hex(rgb):
        return '#{:02x}{:02x}{:02x}'.format(*rgb)

    fg = hex_to_rgb(fg_hex)
    bg = hex_to_rgb(bg_hex)

    blended = tuple(int(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))
    return rgb_to_hex(blended)


def _draw_stats_card(
    draw: ImageDraw, fonts: Dict, stats: Dict, width: int, y: int, scale: int = 1
) -> int:
    """Draw the 4-column statistics card."""
    padding = LAYOUT['padding'] * scale
    card_height = 120 * scale

    draw.rounded_rectangle(
        [padding, y, width - padding, y + card_height],
        radius=15 * scale,
        fill=COLORS['card_bg']
    )

    card_width = width - 2 * padding
    col_width = card_width // 4
    stats_items = [
        ("Win Rate", f"{stats.get('win_rate', 0):.1f}%", COLORS['success']),
        ("Avg R/R", f"{stats.get('avg_rr', 0):.1f}:1", COLORS['gold']),
        ("Trades", str(stats.get('total_trades', 0)), COLORS['text_primary']),
        ("Max DD", f"{stats.get('max_drawdown', 0):.1f}%", COLORS['danger']),
    ]

    for i, (label, value, color) in enumerate(stats_items):
        col_x = padding + (i * col_width) + (col_width // 2)

        bbox = draw.textbbox((0, 0), label, font=fonts['small'])
        label_width = bbox[2] - bbox[0]
        draw.text(
            (col_x - label_width // 2, y + 20 * scale),
            label,
            font=fonts['small'],
            fill=COLORS['text_secondary']
        )

        bbox = draw.textbbox((0, 0), value, font=fonts['mono_heading'])
        value_width = bbox[2] - bbox[0]
        draw.text(
            (col_x - value_width // 2, y + 50 * scale),
            value,
            font=fonts['mono_heading'],
            fill=color
        )

    return y + card_height + 30 * scale


def _draw_trades_list(
    draw: ImageDraw, fonts: Dict, trades: List[Dict], width: int, y: int, scale: int = 1
) -> int:
    """Draw the trade records table using PIL anchor-based positioning."""
    padding = LAYOUT['padding'] * scale
    row_height = LAYOUT['row_height'] * scale
    badge_size = LAYOUT['coin_badge_size'] * scale

    draw.text(
        (padding, y),
        "Recent Trades",
        font=fonts['subtitle'],
        fill=COLORS['text_primary']
    )
    y += 40 * scale

    header_font = fonts['table_header']
    for name, anchor_x, anchor, header, _ in TRADE_TABLE_COLUMNS:
        if header:
            draw.text(
                (anchor_x * scale, y),
                header,
                font=header_font,
                fill=COLORS['text_secondary'],
                anchor=anchor
            )
    y += 28 * scale

    draw.line(
        [(padding, y), (width - padding, y)],
        fill='#30363D',
        width=1 * scale
    )
    y += 12 * scale

    for trade in trades:
        _draw_trade_row_anchor(draw, fonts, trade, y, badge_size, row_height, scale)
        y += row_height

    return y + 10 * scale


def _draw_trade_row_anchor(
    draw: ImageDraw,
    fonts: Dict,
    trade: Dict,
    y: int,
    badge_size: int,
    row_height: int,
    scale: int = 1,
) -> None:
    """Draw a single trade row using anchor-based vertical centering."""
    symbol = trade.get('symbol', 'N/A')
    row_center_y = y + row_height // 2

    for name, anchor_x, anchor, _, is_mono in TRADE_TABLE_COLUMNS:
        font = fonts['mono_body'] if is_mono else fonts['body']
        scaled_x = anchor_x * scale

        if name == 'badge':
            badge_x = scaled_x - badge_size // 2
            badge_y = row_center_y - badge_size // 2
            _draw_coin_badge(draw, symbol, badge_x, badge_y, fonts['badge'], badge_size)

        elif name == 'symbol':
            draw.text((scaled_x, row_center_y), symbol, font=font,
                      fill=COLORS['text_primary'], anchor=anchor)

        elif name == 'date':
            date_str = trade.get('date', '-')
            draw.text((scaled_x, row_center_y), date_str, font=font,
                      fill=COLORS['text_secondary'], anchor=anchor)

        elif name == 'entry':
            entry = trade.get('entry', 0)
            entry_str = f"${entry:,.0f}"
            draw.text((scaled_x, row_center_y), entry_str, font=font,
                      fill=COLORS['text_primary'], anchor=anchor)

        elif name == 'exit':
            exit_p = trade.get('exit', 0)
            exit_str = f"${exit_p:,.0f}"
            draw.text((scaled_x, row_center_y), exit_str, font=font,
                      fill=COLORS['text_primary'], anchor=anchor)

        elif name == 'hold':
            hold_days = trade.get('hold_days', 0)
            _draw_hold_days_anchor(draw, hold_days, scaled_x, row_center_y, font, anchor)

        elif name == 'pnl':
            pnl = trade.get('pnl', 0)
            _draw_pnl_pill(draw, pnl, scaled_x, row_center_y, fonts['mono_body'], anchor)


def _draw_hold_days_anchor(
    draw: ImageDraw,
    days: int,
    x: int,
    y: int,
    font: ImageFont,
    anchor: str,
) -> None:
    """Draw hold-duration text with the number in a bright color and 'd' dimmed."""
    if not days:
        draw.text((x, y), "-", font=font, fill=COLORS['text_secondary'], anchor=anchor)
        return

    hold_str = f"{days}d"
    if anchor == 'ls':
        num_str = str(days)
        draw.text((x, y), num_str, font=font, fill=COLORS['text_primary'], anchor='ls')
        bbox = draw.textbbox((0, 0), num_str, font=font)
        num_width = bbox[2] - bbox[0]
        draw.text((x + num_width, y), "d", font=font,
                  fill=COLORS['text_secondary'], anchor='ls')
    else:
        draw.text((x, y), hold_str, font=font,
                  fill=COLORS['text_primary'], anchor=anchor)


def _draw_pnl_pill(
    draw: ImageDraw,
    pnl: float,
    x: int,
    y: int,
    font: ImageFont,
    anchor: str = 'lm',
) -> None:
    """Draw a P&L pill badge with color-coded background, respecting anchor alignment."""
    pnl_str = f"+{pnl:.1f}%" if pnl > 0 else f"{pnl:.1f}%"
    text_color = COLORS['success'] if pnl > 0 else COLORS['danger']
    bg_color = COLORS['success_bg'] if pnl > 0 else COLORS['danger_bg']

    bbox = draw.textbbox((0, 0), pnl_str, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    padding_h = 8
    padding_v = 4
    pill_width = text_width + padding_h * 2
    pill_height = text_height + padding_v * 2

    if anchor.startswith('r'):
        pill_x = x - pill_width
    elif anchor.startswith('m'):
        pill_x = x - pill_width // 2
    else:
        pill_x = x

    pill_y = y - pill_height // 2

    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_width, pill_y + pill_height],
        radius=pill_height // 2,
        fill=bg_color
    )

    text_x = pill_x + pill_width // 2
    text_y = pill_y + pill_height // 2
    draw.text((text_x, text_y), pnl_str, font=font, fill=text_color, anchor='mm')


def _draw_footer(
    draw: ImageDraw, fonts: Dict, bot_username: str, width: int, height: int, scale: int = 1
):
    """Draw the footer section with bot handle and timestamp."""
    y = height - 80 * scale

    draw.line(
        [(40 * scale, y), (width - 40 * scale, y)],
        fill=COLORS['text_secondary'],
        width=1 * scale
    )
    y += 20 * scale

    footer_text = f"@{bot_username} | Daily Trend-Following Strategy"
    bbox = draw.textbbox((0, 0), footer_text, font=fonts['small'])
    text_width = bbox[2] - bbox[0]
    draw.text(
        ((width - text_width) // 2, y),
        footer_text,
        font=fonts['small'],
        fill=COLORS['text_secondary']
    )

    timestamp = datetime.now().strftime("%Y-%m-%d")
    bbox = draw.textbbox((0, 0), timestamp, font=fonts['small'])
    ts_width = bbox[2] - bbox[0]
    draw.text(
        ((width - ts_width) // 2, y + 25 * scale),
        timestamp,
        font=fonts['small'],
        fill=COLORS['text_secondary']
    )


# ============================================
# Convenience helpers
# ============================================

def generate_card_from_db(year: int = 2025) -> Optional[io.BytesIO]:
    """Generate a performance card from the database. Returns None on failure."""
    try:
        from src.core.config import settings
        from src.trading.position_manager import PositionManager

        pm = PositionManager(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            password=settings.MYSQL_PASSWORD.get_secret_value() if settings.MYSQL_PASSWORD else '',
            database=settings.MYSQL_DATABASE
        )

        raw_trades = pm.get_closed_trades(year=year, limit=50)
        if not raw_trades:
            logger.warning(f"No trades found for year {year}")
            return None

        trades = []
        for t in raw_trades:
            hold_days = 0
            if t.get('opened_at') and t.get('closed_at'):
                delta = t['closed_at'] - t['opened_at']
                hold_days = max(delta.days, 1)

            trades.append({
                'date': t['closed_at'].strftime('%m/%d') if t.get('closed_at') else 'N/A',
                'symbol': t.get('symbol', '').replace('USDT', ''),
                'entry': float(t.get('entry_price', 0)),
                'exit': float(t.get('exit_price', 0)),
                'hold_days': hold_days,
                'pnl': float(t.get('realized_pnl_percent', 0)),
            })

        stats = pm.get_trade_stats(year=year)

        return generate_performance_card(trades, stats, year=year)

    except Exception as e:
        logger.error(f"Failed to generate performance card from DB: {e}")
        return None
