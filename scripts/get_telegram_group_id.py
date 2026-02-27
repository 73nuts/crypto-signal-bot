#!/usr/bin/env python3
"""
Helper script to retrieve Telegram group IDs.
Usage: python scripts/get_telegram_group_id.py
"""

import os

import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

def get_updates():
    """Fetch the latest bot updates."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    print("Fetching bot updates...")
    print(f"API URL: {url}\n")

    try:
        response = requests.get(url)
        data = response.json()

        if not data.get('ok'):
            print(f"API call failed: {data}")
            return

        results = data.get('result', [])

        if not results:
            print("No messages received.")
            print("\nPlease follow these steps:")
            print("1. Confirm the bot has been added to the VIP group")
            print("2. Send a test message in the group (e.g. 'test')")
            print("3. Re-run this script")
            return

        print(f"Received {len(results)} messages\n")

        # Find group IDs
        groups_found = {}

        for update in results:
            # Check message
            if 'message' in update:
                chat = update['message'].get('chat', {})
                chat_type = chat.get('type')

                if chat_type in ['group', 'supergroup']:
                    chat_id = chat.get('id')
                    chat_title = chat.get('title', 'Unnamed Group')

                    if chat_id not in groups_found:
                        groups_found[chat_id] = chat_title

        if groups_found:
            print("=" * 60)
            print("Groups found:")
            print("=" * 60)

            for chat_id, title in groups_found.items():
                print(f"\nGroup name: {title}")
                print(f"Group ID:   {chat_id}")
                print("-" * 60)

            print("\nPlease copy the group ID above (negative number)")
        else:
            print("No group messages found")
            print("\nMessage types received:")
            for i, update in enumerate(results[:5], 1):
                if 'message' in update:
                    chat = update['message'].get('chat', {})
                    print(f"{i}. Type: {chat.get('type')}, Title: {chat.get('title', chat.get('first_name', 'N/A'))}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    get_updates()
