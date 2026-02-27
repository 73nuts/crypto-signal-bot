"""
QR code generator.

Generates payment QR codes in memory (no file I/O).
Used for BSC USDT payment address QR codes.
"""

import io
import logging
from typing import Optional

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from PIL import Image

logger = logging.getLogger(__name__)


def generate_payment_qr(
    address: str,
    amount: Optional[float] = None,
    box_size: int = 10,
    border: int = 2
) -> io.BytesIO:
    """
    Generate a payment QR code in memory.

    Uses the raw address as QR content. EIP-681 URI format is future work.
    Returns a BytesIO containing PNG data.
    """
    qr_content = address

    qr = qrcode.QRCode(
        version=1,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    logger.debug(f"QR code generated: address={address[:10]}...")
    return buffer


def generate_payment_qr_with_logo(
    address: str,
    logo_path: Optional[str] = None,
    box_size: int = 10,
    border: int = 2
) -> io.BytesIO:
    """
    Generate a payment QR code with an optional centered logo.

    Uses high error correction (H) to accommodate the logo overlay.
    Returns a BytesIO containing PNG data.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(address)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

    if logo_path:
        try:
            logo = Image.open(logo_path)
            qr_width, qr_height = img.size
            logo_size = qr_width // 4
            logo = logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)

            logo_pos = ((qr_width - logo_size) // 2, (qr_height - logo_size) // 2)
            img.paste(logo, logo_pos)
        except Exception as e:
            logger.warning(f"Failed to overlay logo: {e}")

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    return buffer
