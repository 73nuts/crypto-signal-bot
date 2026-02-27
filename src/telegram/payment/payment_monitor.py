"""
BSC payment listener.

Responsibilities:
1. Listen for USDT transfer events on BSC (via Web3 RPC eth_getLogs)
2. RPC cross-validation (eth_getTransactionReceipt)
3. Wait for block confirmations
4. Trigger order confirmation and membership activation

Validation flow:
1. Web3 RPC eth_getLogs detects Transfer events
2. Wait for 12 block confirmations
3. RPC eth_getTransactionReceipt cross-validation
4. Update order status + activate membership
"""

import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from src.core.config import settings
from src.core.structured_logger import get_logger
from src.core.tracing import TraceContext
from src.sagas.payment_saga import (
    process_payment,
    register_payment_saga,
    set_payment_confirmed_callback,
)

from ..alert_manager import alert_manager
from ..database import OrderDAO
from ..database.membership_plan_dao import MembershipPlanDAO
from .hd_wallet_manager import HDWalletManager


class PaymentMonitor:
    """BSC payment listener."""

    # BEP20 USDT Transfer event signature (with 0x prefix)
    TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex()

    # USDT precision (BSC USDT uses 18 decimals)
    USDT_DECIMALS = 18

    def __init__(
        self,
        wallet_manager: Optional[HDWalletManager] = None,
        rpc_url: Optional[str] = None,
        usdt_contract: Optional[str] = None,
        block_confirmations: int = 12,
        on_payment_confirmed: Optional[callable] = None,
    ):
        """
        Initialize the payment listener.

        Args:
            wallet_manager: HD wallet manager
            rpc_url: BSC RPC node URL
            usdt_contract: USDT contract address
            block_confirmations: Required block confirmations
            on_payment_confirmed: Payment confirmation callback
                signature: (order_id, telegram_id, plan_code) -> None
        """
        self.logger = get_logger(__name__)

        self.wallet_manager = wallet_manager or HDWalletManager()
        self.order_dao = OrderDAO()
        self.plan_dao = MembershipPlanDAO()

        # Register Saga and set callback
        register_payment_saga()
        if on_payment_confirmed:
            set_payment_confirmed_callback(on_payment_confirmed)
        self.on_payment_confirmed = on_payment_confirmed

        # BSC config
        self.usdt_contract = Web3.to_checksum_address(
            usdt_contract or settings.BSC_USDT_CONTRACT
        )
        self.block_confirmations = block_confirmations

        # RPC for block_number, eth_getLogs, and cross-validation
        self.rpc_url = rpc_url or settings.BSC_RPC_URL

        # Initialize Web3 with timeout to prevent BSC node hangs
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 20}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to BSC node: {self.rpc_url}")

        self.last_block = 0
        self.running = False

        self.logger.info(f"Payment listener initialized: RPC={self.rpc_url}")

    def start(self, poll_interval: int = 10):
        """
        Start listening (synchronous mode).

        Args:
            poll_interval: Polling interval in seconds
        """
        self.running = True
        self.last_block = self.w3.eth.block_number - self.block_confirmations

        self.logger.info(f"Payment listener started: from block {self.last_block}")

        while self.running:
            try:
                self._poll_once()
                time.sleep(poll_interval)
            except Exception as e:
                self.logger.error(f"Listener error: {e}", exc_info=True)
                time.sleep(30)  # Back off on error

    def stop(self):
        """Stop listening."""
        self.running = False
        self.logger.info("Payment listener stopped")

    # Maximum block range per query.
    # With precise topics[2] filtering, the result set is small enough for larger ranges.
    MAX_BLOCK_RANGE = 100

    def _poll_once(self) -> bool:
        """
        Execute one polling iteration (with catch-up logic).

        Returns:
            True: caught up to latest block, may sleep
            False: still catching up, should continue immediately
        """
        with TraceContext(operation="payment.poll"):
            return self._do_poll_once()

    def _do_poll_once(self) -> bool:
        """Actual polling implementation."""
        try:
            current_block = self.w3.eth.block_number
        except Exception as e:
            self.logger.error(f"[poll] Failed to get block number: {e}")
            return True  # Sleep on error

        safe_block = current_block - self.block_confirmations

        if safe_block <= self.last_block:
            self.logger.debug(
                f"[poll] Up to date: current={current_block}, safe={safe_block}, last={self.last_block}"
            )
            return True

        # Get list of addresses to watch
        assigned_addresses = self.wallet_manager.get_assigned_addresses()
        if not assigned_addresses:
            stats = self.wallet_manager.get_pool_stats()
            self.logger.warning(
                f"[diag] No pending payment addresses! Pool status: "
                f"available={stats.get('available', 0)}, "
                f"assigned={stats.get('assigned', 0)}, "
                f"used={stats.get('used', 0)}, "
                f"total={stats.get('total', 0)}"
            )
            self.last_block = safe_block
            return True

        address_map = {addr["address"].lower(): addr for addr in assigned_addresses}

        # Calculate query range (at most MAX_BLOCK_RANGE blocks)
        from_block = self.last_block + 1
        to_block = min(from_block + self.MAX_BLOCK_RANGE - 1, safe_block)
        blocks_behind = safe_block - to_block

        if blocks_behind > 0:
            self.logger.info(
                f"[RPC] Catch-up mode: blocks {from_block}~{to_block}, "
                f"{blocks_behind} behind, watching {len(address_map)} addresses"
            )
        else:
            self.logger.debug(
                f"[RPC] Live mode: blocks {from_block}~{to_block}, "
                f"watching {len(address_map)} addresses"
            )

        transfers = self._get_usdt_transfers(
            from_block=from_block,
            to_block=to_block,
            to_addresses=list(address_map.keys()),
        )

        for tx in transfers:
            to_addr = tx["to"].lower()
            if to_addr in address_map:
                addr_info = address_map[to_addr]
                self._process_transfer(tx, addr_info)

        self.last_block = to_block

        return blocks_behind == 0

    def _get_usdt_transfers(
        self, from_block: int, to_block: int, to_addresses: List[str]
    ) -> List[Dict]:
        """
        Fetch USDT transfer events (via Web3 RPC eth_getLogs).

        Single eth_getLogs call with topics[2] OR-filtered by address list.

        Args:
            from_block: Start block
            to_block: End block
            to_addresses: Target address list

        Returns:
            List of transfer events
        """
        transfers = []

        if not to_addresses:
            self.logger.debug("[poll] No pending addresses to watch")
            return transfers

        try:
            # Build topics[2] = padded address list (OR filter)
            padded_addresses = [
                "0x" + addr.lower().replace("0x", "").zfill(64) for addr in to_addresses
            ]

            logs = self.w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": self.usdt_contract,
                    "topics": [
                        self.TRANSFER_TOPIC,  # Transfer event
                        None,  # from: any
                        padded_addresses,  # to: OR filter
                    ],
                }
            )

            for log in logs:
                from_addr = "0x" + log["topics"][1].hex()[-40:]
                to_addr = "0x" + log["topics"][2].hex()[-40:]
                value = int(log["data"].hex(), 16)
                amount = value / (10**self.USDT_DECIMALS)

                transfers.append(
                    {
                        "tx_hash": log["transactionHash"].hex(),
                        "block_number": log["blockNumber"],
                        "from": from_addr,
                        "to": to_addr,
                        "amount": amount,
                        "log_index": log["logIndex"],
                    }
                )
                self.logger.info(
                    f"[RPC] Transfer detected: {log['transactionHash'].hex()[:16]}..., "
                    f"to={to_addr[:10]}..., amount={amount}"
                )

        except Exception as e:
            self.logger.error(f"[RPC] eth_getLogs error: {e}", exc_info=True)
            alert_manager.sync_alert_api_error("BSC_RPC", f"eth_getLogs failed: {e}")
            return transfers

        if transfers:
            self.logger.info(
                f"[RPC] Blocks {from_block}-{to_block}: found {len(transfers)} transfers"
            )
        else:
            self.logger.debug(f"[RPC] Blocks {from_block}-{to_block}: no new transfers")

        return transfers

    def _process_transfer(self, tx: Dict, addr_info: Dict):
        """
        Process a single transfer.

        Args:
            tx: Transfer info
            addr_info: Address association info
        """
        tx_hash = tx["tx_hash"]
        amount = tx["amount"]
        to_addr = tx["to"]
        order_id = addr_info["order_id"]
        telegram_id = addr_info["telegram_id"]

        self.logger.info(
            f"Transfer detected: tx={tx_hash[:16]}..., "
            f"to={to_addr[:10]}..., amount={amount}, order={order_id}"
        )

        try:
            # 1. Validate order
            order = self.order_dao.get_order_by_id(order_id)
            if not order:
                self.logger.warning(f"Order not found: {order_id}")
                return

            if order["status"] != "PENDING":
                self.logger.warning(f"Unexpected order status: {order_id} -> {order['status']}")
                return

            # 2. Check if order is expired
            if datetime.now() > order["expire_at"]:
                self.logger.warning(f"Order expired: {order_id}")
                self.order_dao.expire_order(order_id, order["version"])
                return

            # 3. Replay protection
            existing = self.order_dao.get_order_by_tx_hash(tx_hash)
            if existing:
                self.logger.warning(f"Transaction hash already used: {tx_hash}")
                return

            # 4. Amount validation (fixed tolerance ±0.05 USDT)
            expected = float(order["expected_amount"])
            tolerance = 0.05
            if amount < (expected - tolerance):
                self.logger.warning(
                    f"Insufficient amount: order={order_id}, "
                    f"expected={expected}, actual={amount}, diff={expected - amount:.2f}"
                )
                alert_manager.sync_alert_amount_mismatch(
                    order_id, str(expected), str(amount)
                )
                self.order_dao.fail_order(
                    order_id,
                    order["version"],
                    f"Insufficient amount: expected {expected}, actual {amount}",
                )
                return

            # Overpayment: log but allow through
            if amount > (expected + tolerance):
                self.logger.info(
                    f"Overpayment: order={order_id}, "
                    f"expected={expected}, actual={amount}, overpaid={amount - expected:.2f}"
                )

            # 5. RPC cross-validation
            if not self._verify_with_rpc(tx_hash, to_addr, amount):
                self.logger.warning(f"RPC verification failed: {tx_hash}")
                alert_manager.sync_alert_api_error(
                    "BSC_RPC", f"Transaction verification failed: {tx_hash[:20]}..."
                )
                return

            # 6. Confirm order
            success = self.order_dao.confirm_order(
                order_id=order_id,
                tx_hash=tx_hash,
                actual_amount=Decimal(str(amount)),
                from_address=tx["from"],
                version=order["version"],
            )

            if not success:
                self.logger.error(f"Order confirmation failed (concurrent conflict): {order_id}")
                return

            # 7. Mark address as received
            self.wallet_manager.mark_received(to_addr, amount, tx_hash)

            # 8. Process membership activation via Saga
            level = self.plan_dao.get_level_by_plan_code(order["membership_type"]) or 1

            from src.notifications.telegram_app import run_async

            run_async(
                process_payment(
                    order_id=order_id,
                    telegram_id=telegram_id,
                    plan_code=order["membership_type"],
                    tx_hash=tx_hash,
                    amount=amount,
                    to_address=to_addr,
                    duration_days=order["duration_days"],
                    level=level,
                )
            )

            self.logger.info(f"Payment confirmed: order={order_id}, tx={tx_hash[:16]}...")

        except Exception as e:
            self.logger.error(f"Transfer processing error: {e}", exc_info=True)
            alert_manager.sync_alert_payment_error(order_id, str(e))

    def _verify_with_rpc(
        self, tx_hash: str, expected_to: str, expected_amount: float
    ) -> bool:
        """
        RPC cross-validation (eth_getTransactionReceipt).

        Args:
            tx_hash: Transaction hash
            expected_to: Expected recipient address
            expected_amount: Expected amount

        Returns:
            True if validation passes, False otherwise
        """
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)

            if receipt is None:
                self.logger.warning(f"RPC transaction not found: {tx_hash}")
                return False

            # Validate transaction status
            if receipt["status"] != 1:
                self.logger.warning(f"Transaction failed: {tx_hash}")
                return False

            # Validate contract address
            if receipt["to"].lower() != self.usdt_contract.lower():
                self.logger.warning(f"Contract address mismatch: {tx_hash}")
                return False

            # Parse logs to validate amount and recipient
            for log in receipt.get("logs", []):
                if log["address"].lower() == self.usdt_contract.lower():
                    topics = log.get("topics", [])
                    if len(topics) >= 3 and topics[0].hex() == self.TRANSFER_TOPIC[2:]:
                        to_addr = "0x" + topics[2].hex()[-40:]
                        value = int(log["data"].hex(), 16)
                        amount = value / (10**self.USDT_DECIMALS)

                        if (
                            to_addr.lower() == expected_to.lower()
                            and abs(amount - expected_amount) <= 0.01
                        ):
                            return True

            self.logger.warning(f"RPC validation data mismatch: {tx_hash}")
            return False

        except Exception as e:
            self.logger.error(f"RPC validation error: {e}")
            # Fail-closed: network errors must not approve payments
            return False

    # _activate_membership and process_pending_callbacks removed.
    # Payment flow is handled by PaymentSaga with retry and compensation.

    def check_pending_payments(self) -> List[Dict]:
        """
        Check status of all pending payment orders (manual trigger).

        Returns:
            List of check results
        """
        results = []
        assigned = self.wallet_manager.get_assigned_addresses()

        for addr_info in assigned:
            address = addr_info["address"]
            order_id = addr_info["order_id"]

            balance = self._get_usdt_balance(address)

            results.append(
                {
                    "address": address,
                    "order_id": order_id,
                    "balance": balance,
                    "assigned_at": addr_info["assigned_at"],
                }
            )

        return results

    def _get_usdt_balance(self, address: str) -> float:
        """Get USDT balance for an address."""
        try:
            contract = self.w3.eth.contract(
                address=self.usdt_contract,
                abi=[
                    {
                        "constant": True,
                        "inputs": [{"name": "_owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function",
                    }
                ],
            )
            balance = contract.functions.balanceOf(
                Web3.to_checksum_address(address)
            ).call()
            return balance / (10**self.USDT_DECIMALS)
        except Exception as e:
            self.logger.error(f"Balance query failed: {e}")
            return 0.0
