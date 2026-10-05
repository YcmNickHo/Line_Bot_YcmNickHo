import base64
import hashlib
import hmac
import json

import pytest

import app as bot


@pytest.fixture
def client():
    return bot.create_app({
        "TESTING": True,
        "LINE_CHANNEL_SECRET": "test-only-secret",
        "LINE_CHANNEL_ACCESS_TOKEN": "test-only-token",
    }).test_client()


def signed_post(client, payload):
    body = json.dumps(payload, ensure_ascii=False)
    signature = base64.b64encode(hmac.new(
        b"test-only-secret", body.encode(), hashlib.sha256
    ).digest()).decode()
    return client.post("/callback", data=body, content_type="application/json",
                       headers={"X-Line-Signature": signature})


def text_event():
    return {
        "type": "message", "timestamp": 0, "replyToken": "test-reply-token",
        "source": {"type": "user", "userId": "test-user"},
        "mode": "active", "webhookEventId": "test-event",
        "deliveryContext": {"isRedelivery": False},
        "message": {"type": "text", "id": "1", "text": "你好 LINE",
                    "quoteToken": "test-quote-token"},
    }


def test_health_and_missing_credentials(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    client = bot.create_app({"TESTING": True}).test_client()
    assert client.get("/healthz").json == {"status": "ok"}
    assert client.get("/readyz").status_code == 503
    assert client.post("/callback").status_code == 503


def test_configured(client):
    assert client.get("/readyz").json == {"status": "configured"}


@pytest.mark.parametrize("signature", [None, "invalid"])
def test_rejects_invalid_signature(client, signature):
    headers = {} if signature is None else {"X-Line-Signature": signature}
    assert client.post("/callback", json={"events": []},
                       headers=headers).status_code == 400


def test_line_webhook_verification(client):
    assert signed_post(client, {"events": []}).json == {"status": "ok"}


def test_echoes_text_with_sdk(client, monkeypatch):
    replies = []
    monkeypatch.setattr(bot.MessagingApi, "reply_message",
                        lambda self, reply, **kwargs: replies.append(reply))
    response = signed_post(client, {"events": [text_event()]})
    assert response.status_code == 200
    assert len(replies) == 1
    assert replies[0].reply_token == "test-reply-token"
    assert replies[0].messages[0].text == "你好 LINE"


def test_reply_failure_is_reported_without_credentials(client, monkeypatch):
    def fail(self, reply, **kwargs):
        raise RuntimeError("test-only-token")
    monkeypatch.setattr(bot.MessagingApi, "reply_message", fail)
    response = signed_post(client, {"events": [text_event()]})
    assert response.status_code == 502
    assert "test-only-token" not in response.get_data(as_text=True)


def test_ignores_non_text_events(client, monkeypatch):
    def unexpected(self, reply, **kwargs):
        pytest.fail("A follow event must not trigger a text reply")
    monkeypatch.setattr(bot.MessagingApi, "reply_message", unexpected)
    event = text_event()
    event["type"] = "follow"
    del event["message"]
    assert signed_post(client, {"events": [event]}).status_code == 200
