#!/usr/bin/env python3
"""
HD wallet mnemonic generator for payment address derivation.

SECURITY WARNING:
  - Run this script on an air-gapped (offline) machine only
  - Never share or commit the generated mnemonic — it controls ALL derived payment addresses
  - Write the mnemonic down by hand; do not screenshot, photograph, or paste it anywhere
  - Clear terminal history after use: `history -c`
  - Anyone who obtains the mnemonic can steal all funds at all derived addresses

Usage:
1. Install dependency: pip install bip_utils
2. Disconnect from the internet (air-gap the machine)
3. Run: python generate_hd_wallet.py
4. Write down the 12-word mnemonic by hand
5. Close the terminal and clear history
"""

import sys
import secrets

try:
    from bip_utils import (
        Bip39MnemonicGenerator,
        Bip39SeedGenerator,
        Bip39WordsNum,
        Bip44,
        Bip44Coins,
        Bip44Changes,
    )
except ImportError:
    print("=" * 60)
    print("Error: bip_utils library not found")
    print("Install it with: pip install bip_utils")
    print("=" * 60)
    sys.exit(1)


def generate_wallet():
    """Generate an HD wallet."""

    print("\n" + "=" * 60)
    print("Offline HD Wallet Generator")
    print("=" * 60)
    print("\nSecurity warning: ensure you are offline before continuing!\n")

    # 1. Generate 12-word mnemonic (using OS secure random)
    mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_12)
    mnemonic_str = mnemonic.ToStr()

    # 2. Derive seed from mnemonic
    seed = Bip39SeedGenerator(mnemonic_str).Generate()

    # 3. Create BIP44 wallet (ETH/BSC shared derivation path)
    bip44_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)

    # 4. Derive account (m/44'/60'/0')
    account = bip44_ctx.Purpose().Coin().Account(0)

    # 5. Derive external chain (m/44'/60'/0'/0)
    change = account.Change(Bip44Changes.CHAIN_EXT)

    # Display mnemonic
    print("-" * 60)
    print("Mnemonic (keep safe, never share with anyone):")
    print("-" * 60)
    print(f"\n{mnemonic_str}\n")
    print("-" * 60)

    # Display master address (index=0)
    addr_0 = change.AddressIndex(0)
    print(f"\nMaster wallet address (for MetaMask import):")
    print(f"   {addr_0.PublicKey().ToAddress()}\n")

    # Display first 5 child addresses as preview
    print("-" * 60)
    print("Child address preview (VIP system will derive automatically):")
    print("-" * 60)
    for i in range(5):
        addr = change.AddressIndex(i)
        address = addr.PublicKey().ToAddress()
        print(f"   [{i}] {address}")

    print("\n" + "-" * 60)
    print("Generation complete!")
    print("-" * 60)
    print("\nNext steps:")
    print("1. Write down the 12 mnemonic words by hand (no screenshots)")
    print("2. Import this mnemonic into MetaMask")
    print("3. Configure the mnemonic in the server .env file")
    print("4. Close the terminal and clear command history")
    print("\nWarning: the mnemonic is the sole credential; loss is unrecoverable!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    generate_wallet()
