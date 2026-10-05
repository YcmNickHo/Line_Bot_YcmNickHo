# LINE 訊息 → Google 行事曆

接收你傳給 LINE 官方帳號（或 Bot 所在群組）的新文字訊息，保留訊息，將明確的日期、時間和事項新增到 Google 行事曆。**不聊天、不回覆 LINE 訊息，也不需要 Channel access token。** 無法讀取其他私人對話或歷史訊息。

## 目前支援

- `明天下午三點要開會` → 明天 15:00–16:00，標題「開會」。
- `2026/10/07 15:30 客戶會議` → 指定日期 15:30–16:30。
- `後天上午九點半 看醫生`、`10月8日 14點30分 討論`。

時間使用 **Asia/Taipei**；「今天／明天／後天」以 LINE 訊息的發送時間判定，每筆預設一小時。未寫年份的月／日使用訊息發送年份。這是有限格式的解析，並非任意自然語言 AI；日期需位於訊息開頭。日期不完整、無上午／下午的「三點」、重複時段、取消／改期／不確定訊息只保留為 `review`，不自動寫入。暫不支援全天、多筆事項、週期活動、修改／刪除既有活動或自動通知。

只處理 `LINE_ALLOWED_USER_ID` 指定使用者，避免群組其他人的訊息寫入你的行事曆。驗證 LINE 簽章後才收集。Google 使用固定事件 ID，重送訊息不會重複建立活動。

## 安裝與啟動

Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
.venv/bin/gunicorn --no-control-socket --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 120 'app:create_app()'
```

Codex 開發環境使用 `/workspace/.venvs/line-bot/bin/` 取代 `.venv/bin/`。測試模擬 Google API，不使用正式憑證、不新增真實行事曆活動。

在部署平台／環境安全設定中填入（勿貼到聊天或提交 Git）：

| 變數 | 用途 |
| --- | --- |
| `LINE_CHANNEL_SECRET` | 本機驗證 LINE 簽章；必須是真實值，不能用代理占位值。 |
| `LINE_ALLOWED_USER_ID` | LINE Developers 中你的 User ID；只收集這位使用者的訊息。 |
| `GOOGLE_CLIENT_ID` | Google Desktop OAuth client ID。 |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret，安全儲存。 |
| `GOOGLE_REFRESH_TOKEN` | 你登入並同意行事曆授權後取得的 refresh token，安全儲存。 |
| `GOOGLE_CALENDAR_ID` | 預設 `primary`，使用授權帳號的主要行事曆。 |
| `DATABASE_PATH` | SQLite 檔案位置，正式部署請指定持久磁碟上的路徑。 |

程式直接讀取環境變數，不會自動載入 `.env`。Google secret 與 refresh token 必須可供本機 token exchange 使用；不要填代理占位值。Requests 會使用平台 HTTPS proxy 與 CA 設定，保持 TLS 驗證。需要連線的網域為 `oauth2.googleapis.com`、`www.googleapis.com`。

## 授權 Google 行事曆

此步必須由你登入 Google 同意，我們不需要你的 Google 密碼。

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立專案、啟用 Google Calendar API，設定 Google Auth Platform／OAuth consent screen。
2. 若應用程式為 External 且處於 Testing，將自己的 Google 帳號加入 Test users。建立 **Desktop app** 類型的 OAuth client 並下載 JSON；不要將此檔案提交到儲存庫。
3. 在**自己的電腦**下載此專案並安裝依賴，執行：

   ```bash
   .venv/bin/python tools/authorize_google.py --client-file /安全路徑/client.json --output /安全路徑/calendar-credentials.json
   ```

   Windows 可改用 `.venv\Scripts\python.exe`。使用同一台電腦的瀏覽器登入並同意 `calendar.events` 權限。此腳本使用狀態驗證、PKCE 與 Google 支援的 loopback callback；不要在無法連入的雲端終端機執行。
4. 腳本會在儲存庫外建立私人 JSON 檔（不覆寫既有檔案），其中三個 `GOOGLE_*` 值請填入部署平台的安全環境設定，勿貼進聊天。
5. Testing 狀態的 External OAuth 應用程式，其 refresh token 通常會在七天後失效。長期運作需依 Google 規定調整應用程式發布狀態／授權設定；不要把 GitHub 推送視為完成 Google 授權。

參考：[Google Calendar events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)、[Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)。

## 連接 LINE 與部署

1. Channel secret 若曾貼到聊天，先在 LINE Developers 重設，再安全填入。只接收訊息不需要 Channel access token；已外洩的 token 仍應撤銷。
2. 部署到具有公開 HTTPS 網址的服務，設定上述變數並掛載持久磁碟。GitHub 與 Codex 開發環境本身不提供公開 Webhook 網址。
3. 將 LINE Developers 的 Webhook URL 設為 `https://你的網域/callback`，按 Verify、啟用 Use webhook 與 Webhook redelivery。必要時關閉官方帳號自動回覆與歡迎訊息。
4. 以指定使用者傳送 `明天下午三點要開會`，到 Google 行事曆確認事項日期／時間。尚未完成這步前，不代表真實 LINE → Google 收發已驗證。

## 健康檢查與訊息保存

- `GET /healthz`：程序正常為 200。
- `GET /readyz`：所有必要變數存在為 200；缺少變數為 503。這不驗證 Google 授權是否有效。
- `POST /callback`：LINE intake 缺少設定為 503；無效簽章為 400；Google 同步失败時保存訊息為 `pending` 並回傳 503。沒有待同步事項的有效 LINE 驗證請求為 200。

SQLite 預設為被 Git 忽略的 `instance/messages.sqlite3`，保存原始文字、發送時間、處理狀態及 Google event ID。**請使用持久磁碟並自行設定保留／刪除政策**；重建無持久磁碟的服務會遺失已收集資料。訊息不會暴露在公開 HTTP 路由中。

查看待確認／待同步訊息時，可在主機內部使用 SQLite 工具；勿將私人資料輸出到公開日誌。恢復 Google 授權後補送已保存的 pending 事項：

```bash
.venv/bin/flask --app 'app:create_app()' sync-pending
```

此指令會真正寫入行事曆；`review` 訊息不會被補送。LINE 官方訊息與憑證不會寫入 Git 或程式日誌。
