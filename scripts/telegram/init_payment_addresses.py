#!/usr/bin/env python3
"""
Pre-generate payment addresses and insert into database.

Usage:
    python scripts/telegram/init_payment_addresses.py [count]

Example:
    python scripts/telegram/init_payment_addresses.py 100

Prerequisites:
    1. HD_WALLET_MNEMONIC configured in .env
    2. wallet_state table initialized
    3. payment_addresses table created
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
# Load system-level config
load_dotenv('.env')
# Load Telegram business config (overrides same-name vars)
load_dotenv('.env.telegram', override=True)

import pymysql
from bip_utils import (
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
)


def get_db_connection():
    """Get database connection."""
    return pymysql.connect(
        host=os.getenv('MYSQL_HOST', 'localhost'),
        port=int(os.getenv('MYSQL_PORT', 3306)),
        user=os.getenv('MYSQL_USER', 'root'),
        password=os.getenv('MYSQL_PASSWORD', ''),
        database=os.getenv('MYSQL_DATABASE', 'crypto_signals'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def derive_address(change_ctx, index: int) -> str:
    """Derive address at the given index."""
    addr_ctx = change_ctx.AddressIndex(index)
    return addr_ctx.PublicKey().ToAddress().lower()


def init_wallet_state(conn):
    """Initialize wallet_state table."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) as cnt FROM wallet_state")
        result = cursor.fetchone()
        if result['cnt'] == 0:
            cursor.execute("INSERT INTO wallet_state (id, current_index) VALUES (1, 0)")
            conn.commit()
            print("[OK] wallet_state initialized")
        else:
            print("[OK] wallet_state already exists")


def get_current_index(conn) -> int:
    """Get current max derivation index."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT current_index FROM wallet_state WHERE id = 1")
        result = cursor.fetchone()
        return result['current_index'] if result else 0


def generate_addresses(count: int = 100):
    """Generate the specified number of payment addresses."""

    # Check mnemonic
    mnemonic = os.getenv('HD_WALLET_MNEMONIC')
    if not mnemonic or mnemonic.strip() == '':
        print("[ERROR] HD_WALLET_MNEMONIC not configured")
        sys.exit(1)

    # Initialize BIP44
    print(f"\n[INFO] Initializing HD wallet...")
    seed = Bip39SeedGenerator(mnemonic).Generate()
    bip44_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
    account = bip44_ctx.Purpose().Coin().Account(0)
    change_ctx = account.Change(Bip44Changes.CHAIN_EXT)

    # Verify master address
    master_addr = derive_address(change_ctx, 0)
    expected_master = os.getenv('HD_MASTER_ADDRESS', '').lower()
    if expected_master and master_addr != expected_master:
        print(f"[WARN] Master address mismatch:")
        print(f"       Derived:   {master_addr}")
        print(f"       Configured: {expected_master}")
        confirm = input("Continue? (y/N): ")
        if confirm.lower() != 'y':
            sys.exit(0)
    else:
        print(f"[OK] Master address verified: {master_addr}")

    # Connect to database
    conn = get_db_connection()
    print("[OK] Database connected")

    try:
        # Initialize wallet_state
        init_wallet_state(conn)

        # Get current index
        current_index = get_current_index(conn)
        print(f"[INFO] Current index: {current_index}")

        # Check existing address count
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM payment_addresses")
            existing = cursor.fetchone()['cnt']
            print(f"[INFO] Existing addresses: {existing}")

        # Generate new addresses
        start_index = current_index + 1
        end_index = start_index + count

        print(f"\n[INFO] Generating addresses: {start_index} ~ {end_index - 1}")
        print("-" * 50)

        generated = 0
        with conn.cursor() as cursor:
            for i in range(start_index, end_index):
                address = derive_address(change_ctx, i)

                try:
                    cursor.execute(
                        """INSERT INTO payment_addresses
                           (address, derive_index, status, created_at)
                           VALUES (%s, %s, 'AVAILABLE', NOW())""",
                        (address, i)
                    )
                    generated += 1

                    if generated % 10 == 0:
                        print(f"[PROGRESS] Generated {generated}/{count}")

                except pymysql.IntegrityError:
                    print(f"[SKIP] Index {i} already exists")
                    continue

            # Update wallet_state
            cursor.execute(
                "UPDATE wallet_state SET current_index = %s WHERE id = 1",
                (end_index - 1,)
            )

            conn.commit()

        print("-" * 50)
        print(f"[DONE] Successfully generated {generated} addresses")

        # Show stats
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT status, COUNT(*) as cnt
                   FROM payment_addresses GROUP BY status"""
            )
            stats = cursor.fetchall()
            print("\n[STATS] Address pool status:")
            for row in stats:
                print(f"        {row['status']}: {row['cnt']}")

    finally:
        conn.close()


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print("=" * 50)
    print("Payment Address Pre-generation Script")
    print("=" * 50)
    generate_addresses(count)
