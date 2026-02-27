"""
Fund collector.

Responsibilities:
1. Collect USDT from sub-addresses to the master wallet
2. Top up sub-addresses with BNB gas
3. Batch collection to optimize gas costs

Security design (production-grade):
- Database only stores public info: address + derive_index
- Private keys are derived from mnemonic + index at collection time (in memory, never persisted)
- A database breach only exposes addresses and indexes, not funds
- Risk only exists if the .env mnemonic is compromised

Collection flow:
1. Query list of addresses pending collection (with derive_index)
2. Derive private key from mnemonic + index at runtime
3. Check sub-address BNB balance; top up from master if insufficient
4. Sign USDT transfer from sub-address to master wallet
5. Wait for transaction confirmation
6. Update collection status (private key discarded from memory)
"""

import time
import logging
from typing import Optional, Dict

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .hd_wallet_manager import HDWalletManager
from ..config.constants import (
    BSC_GAS_PRICE_GWEI,
    BSC_MIN_BNB_FOR_GAS,
    BSC_USDT_BALANCE_TOLERANCE,
)
from src.core.config import settings


class FundCollector:
    """Fund collector."""

    # BNB gas config
    GAS_LIMIT_BNB = 21000           # BNB transfer gas
    GAS_LIMIT_USDT = 65000          # USDT transfer gas
    GAS_PRICE_GWEI = BSC_GAS_PRICE_GWEI
    MIN_BNB_FOR_GAS = BSC_MIN_BNB_FOR_GAS

    # USDT precision
    USDT_DECIMALS = 18

    def __init__(
        self,
        wallet_manager: Optional[HDWalletManager] = None,
        rpc_url: Optional[str] = None,
        usdt_contract: Optional[str] = None
    ):
        """
        Initialize the fund collector.

        Args:
            wallet_manager: HD wallet manager
            rpc_url: BSC RPC node
            usdt_contract: USDT contract address
        """
        self.logger = logging.getLogger(__name__)

        self.wallet_manager = wallet_manager or HDWalletManager()

        # Derive master address only; private key derived on demand
        master_wallet = self.wallet_manager.derive_address(0)
        self.master_address = Web3.to_checksum_address(master_wallet['address'])

        # Validate master address matches config
        config_master = (settings.HD_MASTER_ADDRESS or '').lower()
        if config_master and config_master != self.master_address.lower():
            self.logger.warning(
                f"Master address mismatch: config={config_master}, derived={self.master_address}"
            )

        # BSC config
        self.rpc_url = rpc_url or settings.BSC_RPC_URL
        self.usdt_contract = Web3.to_checksum_address(
            usdt_contract or settings.BSC_USDT_CONTRACT
        )

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # USDT contract ABI (transfer and balanceOf)
        self.usdt_abi = [
            {
                "constant": False,
                "inputs": [
                    {"name": "_to", "type": "address"},
                    {"name": "_value", "type": "uint256"}
                ],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            },
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function"
            }
        ]

        self.usdt_contract_obj = self.w3.eth.contract(
            address=self.usdt_contract,
            abi=self.usdt_abi
        )

        self.logger.info(f"Fund collector initialized: master={self.master_address}")

    def collect_all(self) -> Dict[str, any]:
        """
        Collect all pending addresses.

        Returns:
            Collection result summary
        """
        results = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'details': []
        }

        addresses = self.wallet_manager.get_addresses_to_collect()
        results['total'] = len(addresses)

        if not addresses:
            self.logger.info("No addresses pending collection")
            return results

        self.logger.info(f"Starting collection: {len(addresses)} addresses")

        for addr_info in addresses:
            address = addr_info['address']
            derive_index = addr_info['derive_index']
            amount = float(addr_info['received_amount'])

            # Security: derive private key from mnemonic + index at runtime (never stored)
            wallet = self.wallet_manager.derive_address(derive_index)
            private_key = wallet['private_key']

            try:
                tx_hash = self._collect_single(address, private_key, amount)
                if tx_hash:
                    results['success'] += 1
                    results['details'].append({
                        'address': address,
                        'amount': amount,
                        'tx_hash': tx_hash,
                        'status': 'success'
                    })
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'address': address,
                        'amount': amount,
                        'status': 'failed',
                        'error': 'Collection failed'
                    })
            except Exception as e:
                results['failed'] += 1
                results['details'].append({
                    'address': address,
                    'amount': amount,
                    'status': 'failed',
                    'error': str(e)
                })
                self.logger.error(f"Collection error {address}: {e}")

        self.logger.info(
            f"Collection complete: success={results['success']}, failed={results['failed']}"
        )
        return results

    def _collect_single(
        self,
        address: str,
        private_key: str,
        amount: float
    ) -> Optional[str]:
        """
        Collect a single address.

        Args:
            address: Sub-address
            private_key: Sub-address private key
            amount: USDT amount

        Returns:
            Transaction hash, or None on failure
        """
        address = Web3.to_checksum_address(address)

        # 1. Mark as collecting
        self.wallet_manager.mark_collecting(address)

        # 2. Check USDT balance (using tolerance constant)
        usdt_balance = self._get_usdt_balance(address)
        tolerance = float(BSC_USDT_BALANCE_TOLERANCE)
        if usdt_balance < (amount - tolerance):
            self.logger.warning(
                f"Insufficient USDT: {address}, expected={amount}, actual={usdt_balance}"
            )
            return None

        # 3. Check BNB balance; top up if insufficient
        bnb_balance = self._get_bnb_balance(address)
        if bnb_balance < self.MIN_BNB_FOR_GAS:
            self.logger.info(f"Topping up gas: {address}")
            if not self._send_gas_to_address(address):
                self.logger.error(f"Gas top-up failed: {address}")
                return None
            time.sleep(5)  # Wait for gas to arrive

        # 4. Execute USDT transfer
        tx_hash = self._transfer_usdt(
            from_address=address,
            private_key=private_key,
            to_address=self.master_address,
            amount=usdt_balance  # transfer full balance
        )

        if tx_hash:
            # 5. Wait for confirmation
            if self._wait_for_confirmation(tx_hash):
                # 6. Update status
                self.wallet_manager.mark_collected(address, tx_hash)
                self.logger.info(f"Collection successful: {address} -> {tx_hash}")
                return tx_hash

        return None

    def _get_usdt_balance(self, address: str) -> float:
        """Get USDT balance."""
        try:
            balance = self.usdt_contract_obj.functions.balanceOf(
                Web3.to_checksum_address(address)
            ).call()
            return balance / (10 ** self.USDT_DECIMALS)
        except Exception as e:
            self.logger.error(f"USDT balance query failed: {e}")
            return 0.0

    def _get_bnb_balance(self, address: str) -> float:
        """Get BNB balance."""
        try:
            balance = self.w3.eth.get_balance(
                Web3.to_checksum_address(address)
            )
            return self.w3.from_wei(balance, 'ether')
        except Exception as e:
            self.logger.error(f"BNB balance query failed: {e}")
            return 0.0

    def _send_gas_to_address(self, to_address: str) -> bool:
        """
        Send BNB from master wallet to sub-address for gas.

        Args:
            to_address: Sub-address

        Returns:
            True on success
        """
        try:
            # Derive private key on demand, discard after signing
            master_wallet = self.wallet_manager.derive_address(0)
            private_key = master_wallet['private_key']

            nonce = self.w3.eth.get_transaction_count(self.master_address)
            gas_price = self.w3.to_wei(self.GAS_PRICE_GWEI, 'gwei')

            tx = {
                'nonce': nonce,
                'to': Web3.to_checksum_address(to_address),
                'value': self.w3.to_wei(self.MIN_BNB_FOR_GAS, 'ether'),
                'gas': self.GAS_LIMIT_BNB,
                'gasPrice': gas_price,
                'chainId': 56  # BSC mainnet
            }

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            del private_key
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)

            self.logger.info(f"Gas top-up sent: {tx_hash.hex()}")
            return True

        except Exception as e:
            self.logger.error(f"Gas send failed: {e}")
            return False

    def _transfer_usdt(
        self,
        from_address: str,
        private_key: str,
        to_address: str,
        amount: float
    ) -> Optional[str]:
        """
        USDT transfer.

        Args:
            from_address: Sender address
            private_key: Sender private key
            to_address: Recipient address
            amount: USDT amount

        Returns:
            Transaction hash
        """
        try:
            from_address = Web3.to_checksum_address(from_address)
            to_address = Web3.to_checksum_address(to_address)

            nonce = self.w3.eth.get_transaction_count(from_address)
            gas_price = self.w3.to_wei(self.GAS_PRICE_GWEI, 'gwei')

            # Build transfer call
            amount_wei = int(amount * (10 ** self.USDT_DECIMALS))
            tx = self.usdt_contract_obj.functions.transfer(
                to_address, amount_wei
            ).build_transaction({
                'from': from_address,
                'nonce': nonce,
                'gas': self.GAS_LIMIT_USDT,
                'gasPrice': gas_price,
                'chainId': 56
            })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)

            self.logger.info(f"USDT transfer sent: {tx_hash.hex()}")
            return tx_hash.hex()

        except Exception as e:
            self.logger.error(f"USDT transfer failed: {e}")
            return None

    def _wait_for_confirmation(
        self,
        tx_hash: str,
        timeout: int = 120,
        confirmations: int = 3
    ) -> bool:
        """
        Wait for transaction confirmation.

        Args:
            tx_hash: Transaction hash
            timeout: Timeout in seconds
            confirmations: Required confirmations

        Returns:
            True if confirmed successfully
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt:
                    if receipt['status'] == 1:
                        current_block = self.w3.eth.block_number
                        if current_block - receipt['blockNumber'] >= confirmations:
                            return True
                    else:
                        self.logger.error(f"Transaction failed: {tx_hash}")
                        return False
            except Exception as e:
                self.logger.debug(f"Error while waiting for confirmation: {e}")
            time.sleep(3)

        self.logger.warning(f"Transaction confirmation timed out: {tx_hash}")
        return False

    def get_master_balance(self) -> Dict[str, float]:
        """Get master wallet balance."""
        return {
            'bnb': self._get_bnb_balance(self.master_address),
            'usdt': self._get_usdt_balance(self.master_address)
        }

    def estimate_collection_cost(self) -> Dict[str, any]:
        """
        Estimate collection cost.

        Returns:
            Cost estimate
        """
        addresses = self.wallet_manager.get_addresses_to_collect()
        gas_price = self.w3.to_wei(self.GAS_PRICE_GWEI, 'gwei')

        # Each address: 1 BNB transfer in + 1 USDT transfer out
        gas_per_address = (self.GAS_LIMIT_BNB + self.GAS_LIMIT_USDT) * gas_price

        total_usdt = sum(float(a['received_amount']) for a in addresses)
        total_gas_bnb = len(addresses) * self.w3.from_wei(gas_per_address, 'ether')

        return {
            'address_count': len(addresses),
            'total_usdt': total_usdt,
            'estimated_gas_bnb': float(total_gas_bnb),
            'addresses': [
                {'address': a['address'], 'amount': float(a['received_amount'])}
                for a in addresses
            ]
        }
