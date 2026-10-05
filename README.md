# LINE Bot 基礎範本

使用 Python 3.12、Flask 與 LINE 官方 v3 SDK。收到文字訊息後回覆相同文字；其他事件不回覆。所有 Webhook 請求都會先驗證 LINE 簽章。

## 安裝與測試

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

測試使用測試專用值並模擬 LINE 回覆 API，不需要真實憑證，也不會傳送訊息。

## 啟動

在執行平台的安全環境設定中填入以下兩個值，請勿提交到 Git：

- `LINE_CHANNEL_SECRET`：Messaging API Channel 的 Channel secret，用於本機驗證 Webhook 簽章，必須提供實際值。
- `LINE_CHANNEL_ACCESS_TOKEN`：Channel access token，用於呼叫 `api.line.me`。Codex 雲端環境可使用代理憑證；一般部署平台使用實際值。

`.env.example` 僅說明變數名稱；應用程式不會自動載入 `.env`。

```bash
.venv/bin/gunicorn --bind "0.0.0.0:${PORT:-8000}" --workers 2 'app:create_app()'
```

- `GET /healthz`：程序健康檢查，正常為 200。
- `GET /readyz`：檢查兩個設定是否存在；缺少設定為 503。200 不代表 LINE 憑證已通過遠端驗證。
- `POST /callback`：LINE Webhook。缺少設定為 503；無效簽章為 400；回覆 API 失敗為 502。

雲端開發環境的虛擬環境位於 `/workspace/.venvs/line-bot`，請將上述 `.venv/bin/` 改為 `/workspace/.venvs/line-bot/bin/`。

## 連接 LINE

1. 在 LINE Official Account Manager 建立官方帳號，啟用 Messaging API，再至 LINE Developers Console 取得 Channel secret 與 Channel access token。
2. 將服務部署到具有公開 HTTPS 網址的平台，啟動上面的 Gunicorn 指令；若平台指定 `PORT`，指令會自動採用。
3. 在 LINE Developers 設定 Webhook URL 為 `https://你的服務網域/callback`，按 Verify 並啟用 Use webhook。
4. 若需要只由 Bot 回覆，關閉官方帳號的自動回覆／歡迎訊息。將帳號加為好友後傳送文字，確認收到相同文字。

GitHub 儲存庫與 Codex 開發環境本身不提供公開 Webhook 網址。真實 LINE 收發需要完成憑證設定與 HTTPS 部署。

參考：[LINE Messaging API 文件](https://developers.line.biz/en/docs/messaging-api/)。
