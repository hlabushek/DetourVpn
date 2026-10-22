"""Narrow Bot API sender. No URL/body/exception logging and no automatic send retries."""

import json
import urllib.error
import urllib.request
from typing import Protocol

from pydantic import SecretStr


class DeliveryUnknown(Exception):
    pass


class Sender(Protocol):
    def send(self, chat_id: int, text: str) -> None: ...


class TelegramSender:
    def __init__(self, bot_token: SecretStr) -> None:
        self._token = bot_token

    def send(self, chat_id: int, text: str) -> None:
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self._token.get_secret_value() + "/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text, "protect_content": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Disable environment proxies and redirects: token-bearing URLs stay on this origin.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=8) as response:  # noqa: S310 - fixed HTTPS origin
                result = json.loads(response.read(64 * 1024))
                if (
                    response.status != 200
                    or not isinstance(result, dict)
                    or result.get("ok") is not True
                ):
                    raise DeliveryUnknown
        except (OSError, ValueError, urllib.error.URLError):
            raise DeliveryUnknown from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None
