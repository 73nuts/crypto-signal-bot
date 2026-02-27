"""
WeChat notification module via Server Chan (serverchan.cn).

Server Chan is a China-specific push notification service that converts
WeChat (the messaging app) into a one-way notification channel. It works
by sending HTTP POST requests to the Server Chan API, which then pushes
messages to the configured WeChat account.

Requires the SERVER_CHAN_KEY environment variable to be set. The key is
obtained from the Server Chan website after binding a WeChat account.
"""

import logging

import requests

from src.core.config import settings


class WeChatSender:
    """WeChat pusher - sends notifications via Server Chan."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.enabled = settings.WECHAT_ENABLED
        self.server_chan_key = settings.get_secret('SERVER_CHAN_KEY', '')

    def send(self, title, message):
        """Send WeChat push via Server Chan.

        Args:
            title: Push title
            message: Push content

        Returns:
            bool: Whether send succeeded
        """
        try:
            if not self.enabled:
                self.logger.debug("WeChat push disabled")
                return False

            if not self.server_chan_key:
                self.logger.warning("Server Chan key not configured, skipping WeChat push")
                return False

            url = f"https://sctapi.ftqq.com/{self.server_chan_key}.send"
            data = {"title": title, "desp": message}

            response = requests.post(url, data=data, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 0:
                    self.logger.info("WeChat push sent successfully")
                    return True
                else:
                    self.logger.error(f"WeChat push failed: {result.get('message', 'unknown error')}")
                    return False
            else:
                self.logger.error(f"WeChat push request failed: HTTP {response.status_code}")
                return False

        except Exception as e:
            self.logger.error(f"WeChat push failed: {e}")
            return False
