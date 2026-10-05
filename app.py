"""A small LINE Messaging API echo bot."""

import os

from flask import Flask, jsonify, request
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        LINE_CHANNEL_SECRET=os.environ.get("LINE_CHANNEL_SECRET", ""),
        LINE_CHANNEL_ACCESS_TOKEN=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if config:
        app.config.update(config)

    def configured():
        return bool(
            app.config["LINE_CHANNEL_SECRET"]
            and app.config["LINE_CHANNEL_ACCESS_TOKEN"]
        )

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.get("/readyz")
    def ready():
        if not configured():
            return jsonify(status="not_configured"), 503
        return jsonify(status="configured")

    @app.post("/callback")
    def callback():
        if not configured():
            return jsonify(error="LINE credentials are not configured"), 503
        body = request.get_data(as_text=True)
        signature = request.headers.get("X-Line-Signature", "")
        try:
            events = WebhookParser(app.config["LINE_CHANNEL_SECRET"]).parse(
                body, signature
            )
        except InvalidSignatureError:
            return jsonify(error="Invalid signature"), 400
        except (ValueError, KeyError, TypeError):
            return jsonify(error="Invalid webhook body"), 400

        for event in events:
            if not (
                isinstance(event, MessageEvent)
                and isinstance(event.message, TextMessageContent)
            ):
                continue
            configuration = Configuration(
                access_token=app.config["LINE_CHANNEL_ACCESS_TOKEN"]
            )
            # The SDK uses urllib3; honor the cloud environment's HTTPS proxy.
            configuration.proxy = os.environ.get("HTTPS_PROXY")
            configuration.ssl_ca_cert = (
                os.environ.get("SSL_CERT_FILE")
                or os.environ.get("REQUESTS_CA_BUNDLE")
            )
            try:
                with ApiClient(configuration) as client:
                    MessagingApi(client).reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=event.message.text)],
                        ),
                        _request_timeout=10,
                    )
            except Exception:
                # Never log request bodies, tokens, or SDK exception payloads.
                app.logger.error("LINE reply request failed")
                return jsonify(error="LINE reply failed"), 502
        return jsonify(status="ok")

    return app
