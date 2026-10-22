"""Explicitly mocked HTTP transport; never contacts Telegram or sends real messages."""

import io
import json
import secrets
import urllib.error
import urllib.request

import pytest
from pydantic import SecretStr

from vpn_api.auth.telegram import DeliveryUnknown, NoRedirect, TelegramSender


class FakeResponse(io.BytesIO):
    status = 200


class FakeOpener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[urllib.request.Request] = []
        self.fail = False

    def open(self, request: urllib.request.Request, *, timeout: int) -> FakeResponse:
        self.requests.append(request)
        assert timeout == 8
        if self.fail:
            raise urllib.error.URLError("simulated transport failure")
        return FakeResponse(self.body)


@pytest.mark.parametrize("response", [b'{"ok":true}', b'{"ok":false}', b"[]", b"invalid-json"])
def test_sender_uses_fixed_origin_and_sanitizes_failure(
    monkeypatch: pytest.MonkeyPatch, response: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    bot_token = SecretStr("123456789:" + secrets.token_urlsafe(32))
    code = f"{secrets.randbelow(100_000_000):08d}"
    opener = FakeOpener(response)
    handlers: list[object] = []

    def build(*args: object) -> FakeOpener:
        handlers.extend(args)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build)
    sender = TelegramSender(bot_token)
    if response == b'{"ok":true}':
        sender.send(111, code)
    else:
        with pytest.raises(DeliveryUnknown) as error:
            sender.send(111, code)
        assert str(error.value) == ""
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url.startswith("https://api.telegram.org/bot")
    assert request.get_method() == "POST"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data)["protect_content"] is True
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert vars(handlers[0])["proxies"] == {}
    assert isinstance(handlers[1], NoRedirect)
    handlers[1].redirect_request()
    assert bot_token.get_secret_value() not in caplog.text and code not in caplog.text


def test_sender_does_not_retry_unknown_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = FakeOpener(b"{}")
    opener.fail = True
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    sender = TelegramSender(SecretStr("123456789:" + secrets.token_urlsafe(32)))
    with pytest.raises(DeliveryUnknown):
        sender.send(111, "test message, no real delivery")
    assert len(opener.requests) == 1
