#!/usr/bin/env python3
"""
Test Channel invite link generation.

Usage:
    python scripts/testing/test_channel_invite.py

Tests:
1. Verify channel configuration is loaded
2. Test generating channel invite links (Join Request mode)
3. Verify bot permissions
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from telegram.error import TelegramError

from src.core.config import settings
from telegram import Bot


async def test_channel_config():
    """Test channel configuration."""
    print("=" * 50)
    print("1. Checking channel configuration")
    print("=" * 50)

    configs = {
        'CHANNEL_BASIC': settings.TELEGRAM_CHANNEL_BASIC,
        'CHANNEL_PREMIUM': settings.TELEGRAM_CHANNEL_PREMIUM,
        'GROUP_BASIC': settings.TELEGRAM_GROUP_BASIC,
        'GROUP_PREMIUM': settings.TELEGRAM_GROUP_PREMIUM,
    }

    for key, value in configs.items():
        status = "OK" if value else "not configured"
        print(f"  {key}: {value or 'N/A'} [{status}]")

    # Check if channel is configured
    if not settings.TELEGRAM_CHANNEL_PREMIUM:
        print("\n[WARNING] TELEGRAM_CHANNEL_PREMIUM not configured")
        print("Add to .env.telegram:")
        print("  TELEGRAM_CHANNEL_PREMIUM=<your_channel_id>")
        return False

    return True


async def test_bot_permissions():
    """Test bot permissions."""
    print("\n" + "=" * 50)
    print("2. Checking bot permissions")
    print("=" * 50)

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN not configured")
        return False

    bot = Bot(token=token.get_secret_value())

    # Test channel/group permissions
    targets = [
        ('CHANNEL_BASIC', settings.TELEGRAM_CHANNEL_BASIC),
        ('CHANNEL_PREMIUM', settings.TELEGRAM_CHANNEL_PREMIUM),
        ('GROUP_BASIC', settings.TELEGRAM_GROUP_BASIC),
        ('GROUP_PREMIUM', settings.TELEGRAM_GROUP_PREMIUM),
    ]

    for name, chat_id in targets:
        if not chat_id:
            print(f"  {name}: skipped (not configured)")
            continue

        try:
            chat = await bot.get_chat(int(chat_id))
            me = await bot.get_chat_member(int(chat_id), (await bot.get_me()).id)

            print(f"  {name}:")
            print(f"    Title: {chat.title}")
            print(f"    Type: {chat.type}")
            print(f"    Bot Status: {me.status}")

            if me.status == 'administrator':
                can_invite = getattr(me, 'can_invite_users', False)
                print(f"    can_invite_users: {can_invite}")
                if not can_invite:
                    print("    [WARNING] Bot does not have 'Add Subscribers' permission!")

        except TelegramError as e:
            print(f"  {name}: [ERROR] {e}")

    return True


async def test_create_invite_link():
    """Test creating invite links."""
    print("\n" + "=" * 50)
    print("3. Test creating Join Request invite link")
    print("=" * 50)

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return False

    bot = Bot(token=token.get_secret_value())

    # Test premium channel
    channel_id = settings.TELEGRAM_CHANNEL_PREMIUM
    if not channel_id:
        print("  Skipped: TELEGRAM_CHANNEL_PREMIUM not configured")
        return False

    try:
        # Create Join Request mode invite link
        link = await bot.create_chat_invite_link(
            chat_id=int(channel_id),
            creates_join_request=True,
            name="TEST_INVITE"
        )

        print("  [SUCCESS] Invite link created:")
        print(f"    Link: {link.invite_link}")
        print(f"    Join Request: {link.creates_join_request}")
        print(f"    Name: {link.name}")

        # Cleanup: revoke test link
        await bot.revoke_chat_invite_link(
            chat_id=int(channel_id),
            invite_link=link.invite_link
        )
        print("  [CLEANUP] Test link revoked")

        return True

    except TelegramError as e:
        print(f"  [ERROR] Failed to create invite link: {e}")
        return False


async def main():
    """Main test flow."""
    print("\nChannel+Group split architecture test")
    print("=" * 50)

    # Test 1: configuration check
    config_ok = await test_channel_config()

    # Test 2: bot permission check
    perm_ok = await test_bot_permissions()

    # Test 3: create invite link
    invite_ok = await test_create_invite_link()

    # Summary
    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)
    print(f"  Config check:  {'PASS' if config_ok else 'FAIL'}")
    print(f"  Permissions:   {'PASS' if perm_ok else 'FAIL'}")
    print(f"  Invite link:   {'PASS' if invite_ok else 'FAIL'}")

    if config_ok and perm_ok and invite_ok:
        print("\n[SUCCESS] All tests passed. Ready to deploy.")
    else:
        print("\n[ATTENTION] Some tests failed, please check configuration.")


if __name__ == '__main__':
    asyncio.run(main())
