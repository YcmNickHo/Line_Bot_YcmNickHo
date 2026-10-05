"""Collect signed LINE messages and create Google Calendar entries; never reply."""
import json
import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from calendar_sync import CalendarUnavailable, GoogleCalendar, parse_message

REQUIRED = ('LINE_CHANNEL_SECRET', 'LINE_ALLOWED_USER_ID', 'GOOGLE_CLIENT_ID',
            'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN')


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping({name: os.environ.get(name, '') for name in REQUIRED})
    app.config.update(
        GOOGLE_CALENDAR_ID=os.environ.get('GOOGLE_CALENDAR_ID', 'primary'),
        DATABASE_PATH=os.environ.get('DATABASE_PATH', str(Path(app.instance_path) / 'messages.sqlite3')),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if config:
        app.config.update(config)
    db_path = Path(app.config['DATABASE_PATH'])
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS messages (
            event_id TEXT PRIMARY KEY, text TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
            status TEXT NOT NULL, event_json TEXT, calendar_event_id TEXT)''')
    db_path.chmod(0o600)
    calendar = app.config.get('CALENDAR_CLIENT') or GoogleCalendar(app.config)

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/readyz')
    def ready():
        configured = all(app.config[name] for name in REQUIRED)
        return jsonify(status='configured' if configured else 'not_configured'), 200 if configured else 503

    @app.post('/callback')
    def callback():
        if not app.config['LINE_CHANNEL_SECRET'] or not app.config['LINE_ALLOWED_USER_ID']:
            return jsonify(error='LINE intake is not configured'), 503
        try:
            events = WebhookParser(app.config['LINE_CHANNEL_SECRET']).parse(
                request.get_data(as_text=True), request.headers.get('X-Line-Signature', '')
            )
        except InvalidSignatureError:
            return jsonify(error='Invalid signature'), 400
        except (ValueError, KeyError, TypeError):
            return jsonify(error='Invalid webhook body'), 400
        failed = False
        for event in events:
            if not isinstance(event, MessageEvent) or not isinstance(event.message, TextMessageContent):
                continue
            if getattr(event.source, 'user_id', None) != app.config['LINE_ALLOWED_USER_ID']:
                continue
            # LINE webhook event IDs give stable Google IDs across retries.
            event_id = str(event.webhook_event_id)
            if not event.webhook_event_id:
                return jsonify(error='Missing webhook event ID'), 400
            parsed = parse_message(event.message.text, event.timestamp)
            with sqlite3.connect(db_path, timeout=15) as db:
                db.execute('INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, NULL)',
                           (event_id, event.message.text, event.timestamp,
                            'pending' if parsed else 'review', json.dumps(parsed) if parsed else None))
                row = db.execute('SELECT status, event_json FROM messages WHERE event_id=?', (event_id,)).fetchone()
            if row[0] != 'pending':
                continue
            if not all(app.config[name] for name in REQUIRED):
                failed = True
                continue
            try:
                google_id = calendar.create(event_id, json.loads(row[1]))
            except CalendarUnavailable:
                app.logger.warning('Google Calendar synchronization failed; message retained')
                failed = True
                continue
            with sqlite3.connect(db_path, timeout=15) as db:
                db.execute("UPDATE messages SET status='created', calendar_event_id=? WHERE event_id=?",
                           (google_id, event_id))
        return jsonify(status='pending' if failed else 'ok'), 503 if failed else 200

    @app.cli.command('sync-pending')
    def sync_pending():
        """Retry retained messages after Google credentials/connectivity are restored."""
        import click
        if not all(app.config[name] for name in REQUIRED):
            raise click.ClickException('Required settings are missing')
        with sqlite3.connect(db_path) as db:
            rows = db.execute("SELECT event_id, event_json FROM messages WHERE status='pending'").fetchall()
        for event_id, event_json in rows:
            try:
                google_id = calendar.create(event_id, json.loads(event_json))
            except CalendarUnavailable:
                raise click.ClickException('Google synchronization failed; pending messages retained') from None
            with sqlite3.connect(db_path) as db:
                db.execute("UPDATE messages SET status='created', calendar_event_id=? WHERE event_id=?",
                           (google_id, event_id))
        click.echo(f'Synchronized {len(rows)} pending messages')

    return app
