"""
HD wallet address manager.

Responsibilities:
1. Derive child addresses from mnemonic
2. Maintain address pool (pre-generated)
3. Assign unique addresses to orders
4. Manage address state lifecycle

Derivation path: m/44'/60'/0'/0/{index}
"""

import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

from bip_utils import (
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
)

from ..database import DatabaseManager, WalletDAO
from src.core.config import settings


class HDWalletManager:
    """HD wallet address manager."""

    # Address pool config
    POOL_MIN_SIZE = 10          # Minimum available addresses in pool
    POOL_BATCH_SIZE = 20        # Addresses to pre-generate per batch
    POOL_ALERT_THRESHOLD = 20   # Alert threshold
    POOL_ALERT_COOLDOWN_SEC = 3600  # Alert cooldown (1 hour)

    # Address states
    STATUS_AVAILABLE = 'AVAILABLE'      # Available for assignment
    STATUS_ASSIGNED = 'ASSIGNED'        # Assigned, awaiting payment
    STATUS_USED = 'USED'                # Payment received
    STATUS_COLLECTING = 'COLLECTING'    # Collection in progress

    def __init__(self, mnemonic: Optional[str] = None):
        """
        Initialize the HD wallet manager.

        Args:
            mnemonic: Mnemonic phrase; reads from env if not provided
        """
        self.logger = logging.getLogger(__name__)
        self.db = DatabaseManager()
        self.wallet_dao = WalletDAO()

        # Derive BIP44 context, then discard mnemonic from memory
        raw_mnemonic = mnemonic
        if not raw_mnemonic and settings.HD_WALLET_MNEMONIC:
            raw_mnemonic = settings.HD_WALLET_MNEMONIC.get_secret_value()
        if not raw_mnemonic:
            raise ValueError("HD_WALLET_MNEMONIC not configured")

        seed = Bip39SeedGenerator(raw_mnemonic).Generate()
        del raw_mnemonic
        bip44_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
        del seed
        account = bip44_ctx.Purpose().Coin().Account(0)
        self._change_ctx = account.Change(Bip44Changes.CHAIN_EXT)

        # Pool alert cooldown timestamp
        self._last_pool_alert_time: Optional[datetime] = None

        self.logger.info("HD wallet manager initialized")

    def derive_address(self, index: int) -> Dict[str, str]:
        """
        Derive the address at the given index.

        Args:
            index: Derivation index

        Returns:
            {'address': '0x...', 'private_key': '0x...'}
        """
        addr_ctx = self._change_ctx.AddressIndex(index)
        address = addr_ctx.PublicKey().ToAddress()
        private_key = addr_ctx.PrivateKey().Raw().ToHex()

        return {
            'address': address,
            'private_key': f"0x{private_key}"
        }

    def get_current_max_index(self) -> int:
        """
        Get the current maximum derivation index.

        Reads from the wallet_state table (managed by WalletDAO).
        """
        return self.wallet_dao.get_current_index()

    def ensure_pool_size(self) -> int:
        """
        Ensure the address pool has enough available addresses.

        Uses WalletDAO.get_next_address_index() for atomic index allocation,
        guaranteeing concurrency safety (SELECT FOR UPDATE).

        Returns:
            Number of newly generated addresses
        """
        sql = """
            SELECT COUNT(*) as cnt
            FROM payment_addresses
            WHERE status = %s
        """
        result = self.db.execute_query(sql, (self.STATUS_AVAILABLE,), fetch_one=True)
        available_count = result['cnt'] if result else 0

        if available_count >= self.POOL_MIN_SIZE:
            return 0

        need_count = self.POOL_BATCH_SIZE
        generated = 0

        for _ in range(need_count):
            try:
                # Atomically get next index (SELECT FOR UPDATE)
                index = self.wallet_dao.get_next_address_index()
                self._create_address(index)
                generated += 1
            except Exception as e:
                self.logger.error(f"Address generation failed: {e}")
                break

        if generated > 0:
            self.logger.info(f"Address pool replenished: +{generated} addresses")

        return generated

    def _create_address(self, index: int) -> str:
        """
        Create and store a derived address.

        Security: only stores address and derive_index, never the private key.
        Private key is derived on demand during collection.

        Args:
            index: Derivation index

        Returns:
            Address string
        """
        wallet = self.derive_address(index)

        # Only store public info: address and index.
        # Private key is derived via derive_address(index) at collection time.
        sql = """
            INSERT INTO payment_addresses
            (address, derive_index, status, created_at)
            VALUES (%s, %s, %s, NOW())
        """
        self.db.execute_update(sql, (
            wallet['address'].lower(),  # lowercase for consistent storage
            index,
            self.STATUS_AVAILABLE
        ))

        return wallet['address']

    def allocate_address(self, order_id: str, telegram_id: int) -> Optional[str]:
        """
        Allocate an address to an order.

        Args:
            order_id: Order ID
            telegram_id: User Telegram ID

        Returns:
            Allocated address, or None if no address is available
        """
        # Ensure pool has addresses
        self.ensure_pool_size()

        # Atomic allocation: SELECT + UPDATE
        sql = """
            UPDATE payment_addresses
            SET status = %s,
                order_id = %s,
                telegram_id = %s,
                assigned_at = NOW()
            WHERE status = %s
            ORDER BY derive_index ASC
            LIMIT 1
        """
        affected = self.db.execute_update(sql, (
            self.STATUS_ASSIGNED,
            order_id,
            telegram_id,
            self.STATUS_AVAILABLE
        ))

        if affected == 0:
            self.logger.error("No available address to allocate")
            return None

        sql = """
            SELECT address FROM payment_addresses
            WHERE order_id = %s AND status = %s
        """
        result = self.db.execute_query(sql, (order_id, self.STATUS_ASSIGNED), fetch_one=True)

        if result:
            self.logger.info(f"Address allocated: order={order_id}, address={result['address']}")
            self._check_and_alert_pool_low()
            return result['address']

        return None

    def _check_and_alert_pool_low(self) -> None:
        """
        Check pool size and trigger alert if below threshold (with cooldown).

        Sends a CRITICAL alert to admin when available < POOL_ALERT_THRESHOLD.
        Cooldown: same alert not repeated within 1 hour.
        """
        stats = self.get_pool_stats()
        available = stats.get('available', 0)

        if available >= self.POOL_ALERT_THRESHOLD:
            return

        now = datetime.now()
        if self._last_pool_alert_time:
            elapsed = (now - self._last_pool_alert_time).total_seconds()
            if elapsed < self.POOL_ALERT_COOLDOWN_SEC:
                self.logger.debug(
                    f"Pool alert on cooldown ({(self.POOL_ALERT_COOLDOWN_SEC - elapsed)/60:.0f} min remaining)"
                )
                return

        from ..alert_manager import alert_manager
        msg = (
            f"Available addresses: {available} (threshold: {self.POOL_ALERT_THRESHOLD})\n"
            f"Total addresses: {stats.get('total', 0)}\n"
            f"Assigned: {stats.get('assigned', 0)}\n"
            f"Used: {stats.get('used', 0)}\n\n"
            f"Please check address generation or replenish manually."
        )
        alert_manager.sync_alert_critical(f"[POOL_LOW] Address pool alert\n\n{msg}")

        self._last_pool_alert_time = now
        self.logger.warning(f"Pool alert sent: available={available}")

    def get_address_by_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up address info by order ID.

        Args:
            order_id: Order ID

        Returns:
            Address info dict
        """
        sql = """
            SELECT address, derive_index, status, telegram_id,
                   received_amount, received_tx_hash, assigned_at
            FROM payment_addresses
            WHERE order_id = %s
        """
        return self.db.execute_query(sql, (order_id,), fetch_one=True)

    def get_order_by_address(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Look up order info by address.

        Args:
            address: BSC address

        Returns:
            Address record (includes order_id)
        """
        sql = """
            SELECT id, address, derive_index, order_id, telegram_id,
                   status, received_amount, assigned_at
            FROM payment_addresses
            WHERE LOWER(address) = LOWER(%s)
        """
        return self.db.execute_query(sql, (address,), fetch_one=True)

    def mark_received(
        self,
        address: str,
        amount: float,
        tx_hash: str
    ) -> bool:
        """
        Mark an address as payment received.

        Args:
            address: Receiving address
            amount: Received amount
            tx_hash: Transaction hash

        Returns:
            True if updated successfully
        """
        sql = """
            UPDATE payment_addresses
            SET status = %s,
                received_amount = %s,
                received_tx_hash = %s,
                received_at = NOW()
            WHERE LOWER(address) = LOWER(%s)
              AND status = %s
        """
        affected = self.db.execute_update(sql, (
            self.STATUS_USED,
            amount,
            tx_hash,
            address,
            self.STATUS_ASSIGNED
        ))

        if affected > 0:
            self.logger.info(f"Address marked received: address={address}, amount={amount}")
            return True

        return False

    def get_addresses_to_collect(self) -> List[Dict[str, Any]]:
        """
        Get the list of addresses pending collection.

        Security: only returns public info (address, index); private key
        is derived on demand by FundCollector.

        Returns:
            List of addresses pending collection (includes derive_index)
        """
        sql = """
            SELECT id, address, derive_index,
                   received_amount, received_tx_hash
            FROM payment_addresses
            WHERE status = %s
              AND received_amount > 0
        """
        return self.db.execute_query(sql, (self.STATUS_USED,)) or []

    def mark_collecting(self, address: str) -> bool:
        """Mark address as collection in progress."""
        sql = """
            UPDATE payment_addresses
            SET status = %s
            WHERE LOWER(address) = LOWER(%s) AND status = %s
        """
        affected = self.db.execute_update(sql, (
            self.STATUS_COLLECTING,
            address,
            self.STATUS_USED
        ))
        return affected > 0

    def mark_collected(self, address: str, tx_hash: str) -> bool:
        """
        Mark address collection as complete.

        Args:
            address: Address
            tx_hash: Collection transaction hash

        Returns:
            True if updated successfully
        """
        sql = """
            UPDATE payment_addresses
            SET collected_tx_hash = %s,
                collected_at = NOW()
            WHERE LOWER(address) = LOWER(%s) AND status = %s
        """
        affected = self.db.execute_update(sql, (
            tx_hash,
            address,
            self.STATUS_COLLECTING
        ))

        if affected > 0:
            self.logger.info(f"Address collection complete: address={address}, tx={tx_hash}")
            return True

        return False

    def get_assigned_addresses(self) -> List[Dict[str, Any]]:
        """
        Get all assigned addresses that have not yet received payment (for monitoring).

        Returns:
            List of assigned addresses
        """
        sql = """
            SELECT address, order_id, telegram_id, assigned_at
            FROM payment_addresses
            WHERE status = %s
        """
        return self.db.execute_query(sql, (self.STATUS_ASSIGNED,)) or []

    def release_address(self, address: str) -> bool:
        """
        Release an address back to the pool (used when order creation fails).

        Args:
            address: Address to release

        Returns:
            True if released successfully
        """
        sql = """
            UPDATE payment_addresses
            SET status = %s,
                order_id = NULL,
                telegram_id = NULL,
                assigned_at = NULL
            WHERE LOWER(address) = LOWER(%s)
              AND status = %s
        """
        affected = self.db.execute_update(sql, (
            self.STATUS_AVAILABLE,
            address,
            self.STATUS_ASSIGNED
        ))

        if affected > 0:
            self.logger.info(f"Address released to pool: address={address}")
            return True

        self.logger.warning(f"Address release failed (may already be used): address={address}")
        return False

    def release_expired_addresses(self, expire_hours: int = 24) -> int:
        """
        Release expired unpaid addresses back to the pool.

        Args:
            expire_hours: Expiry threshold in hours

        Returns:
            Number of addresses released
        """
        sql = """
            UPDATE payment_addresses
            SET status = %s,
                order_id = NULL,
                telegram_id = NULL,
                assigned_at = NULL
            WHERE status = %s
              AND assigned_at < DATE_SUB(NOW(), INTERVAL %s HOUR)
        """
        affected = self.db.execute_update(sql, (
            self.STATUS_AVAILABLE,
            self.STATUS_ASSIGNED,
            expire_hours
        ))

        if affected > 0:
            self.logger.info(f"Expired addresses released: {affected}")

        return affected

    def get_pool_stats(self) -> Dict[str, int]:
        """
        Get address pool statistics.

        Returns:
            Count per status
        """
        sql = """
            SELECT status, COUNT(*) as cnt
            FROM payment_addresses
            GROUP BY status
        """
        results = self.db.execute_query(sql) or []
        stats = {
            'available': 0,
            'assigned': 0,
            'used': 0,
            'collecting': 0,
            'total': 0
        }
        for row in results:
            status = row['status'].lower()
            stats[status] = row['cnt']
            stats['total'] += row['cnt']

        return stats
