import base64
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from app import create_app
from calendar_sync import CalendarUnavailable, GoogleCalendar, parse_message

STAMP = int(datetime(2026, 10, 5, 23, 30, tzinfo=ZoneInfo('Asia/Taipei')).timestamp() * 1000)


class FakeCalendar:
    def __init__(self):
        self.created = []
        self.fail = False

    def create(self, event_id, event):
        if self.fail:
            raise CalendarUnavailable()
        self.created.append((event_id, event))
        return 'test-google-event'


@pytest.fixture
def application(tmp_path):
    return create_app({
        'TESTING': True, 'LINE_CHANNEL_SECRET': 'test-only-secret',
        'LINE_ALLOWED_USER_ID': 'test-user', 'GOOGLE_CLIENT_ID': 'test-client',
        'GOOGLE_CLIENT_SECRET': 'test-only-google-secret',
        'GOOGLE_REFRESH_TOKEN': 'test-only-refresh',
        'DATABASE_PATH': str(tmp_path / 'messages.sqlite3'), 'CALENDAR_CLIENT': FakeCalendar(),
    })


def post(application, text='明天下午三點要開會', user='test-user', event_id='test-event'):
    payload = {'events': [{
        'type': 'message', 'timestamp': STAMP, 'replyToken': 'unused',
        'source': {'type': 'user', 'userId': user}, 'mode': 'active',
        'webhookEventId': event_id, 'deliveryContext': {'isRedelivery': False},
        'message': {'type': 'text', 'id': '1', 'text': text, 'quoteToken': 'unused'},
    }]}
    body = json.dumps(payload, ensure_ascii=False)
    sig = base64.b64encode(hmac.new(b'test-only-secret', body.encode(), hashlib.sha256).digest()).decode()
    return application.test_client().post('/callback', data=body,
        headers={'X-Line-Signature': sig, 'Content-Type': 'application/json'})


def rows(application):
    with sqlite3.connect(application.config['DATABASE_PATH']) as db:
        return db.execute('SELECT status, text FROM messages').fetchall()


def test_signed_message_creates_calendar_without_line_access_token(application):
    assert post(application).status_code == 200
    assert rows(application) == [('created', '明天下午三點要開會')]
    created = application.config['CALENDAR_CLIENT'].created
    assert len(created) == 1
    assert created[0][1]['summary'] == '開會'
    assert created[0][1]['start']['dateTime'] == '2026-10-06T15:00:00+08:00'
    assert created[0][1]['end']['dateTime'] == '2026-10-06T16:00:00+08:00'


def test_redelivery_does_not_create_twice(application):
    assert post(application).status_code == 200
    assert post(application).status_code == 200
    assert len(rows(application)) == 1
    assert len(application.config['CALENDAR_CLIENT'].created) == 1


def test_other_users_cannot_create_or_store_messages(application):
    assert post(application, user='other-user').status_code == 200
    assert rows(application) == []
    assert application.config['CALENDAR_CLIENT'].created == []


def test_invalid_signature_does_not_store_or_write(application):
    response = application.test_client().post('/callback', json={'events': []},
                                              headers={'X-Line-Signature': 'invalid'})
    assert response.status_code == 400
    assert rows(application) == []


def test_ambiguous_message_is_retained_for_review(application):
    assert post(application, text='下次有空再開會').status_code == 200
    assert rows(application) == [('review', '下次有空再開會')]
    assert application.config['CALENDAR_CLIENT'].created == []


def test_google_failure_retains_pending_then_retries(application):
    calendar = application.config['CALENDAR_CLIENT']
    calendar.fail = True
    assert post(application).status_code == 503
    assert rows(application)[0][0] == 'pending'
    calendar.fail = False
    result = application.test_cli_runner().invoke(args=['sync-pending'])
    assert result.exit_code == 0
    assert rows(application)[0][0] == 'created'
    assert len(calendar.created) == 1


def test_missing_google_credentials_still_collects(application):
    application.config['GOOGLE_REFRESH_TOKEN'] = ''
    assert application.test_client().get('/readyz').status_code == 503
    assert post(application).status_code == 503
    assert rows(application)[0][0] == 'pending'
    assert application.config['CALENDAR_CLIENT'].created == []


def test_health_and_missing_intake_settings(application):
    application.config['LINE_CHANNEL_SECRET'] = ''
    assert application.test_client().get('/healthz').json == {'status': 'ok'}
    assert post(application).status_code == 503


@pytest.mark.parametrize('text,start,title', [
    ('明天下午三點要開會', '2026-10-06T15:00:00+08:00', '開會'),
    ('2026/10/07 15:30 客戶會議', '2026-10-07T15:30:00+08:00', '客戶會議'),
    ('後天上午九點半 看醫生', '2026-10-07T09:30:00+08:00', '看醫生'),
    ('今天晚上九點 夜間作業', '2026-10-05T21:00:00+08:00', '夜間作業'),
    ('10月8日 14點30分 討論', '2026-10-08T14:30:00+08:00', '討論'),
])
def test_supported_dates(text, start, title):
    event = parse_message(text, STAMP)
    assert event['start']['dateTime'] == start
    assert event['summary'] == title


@pytest.mark.parametrize('text', [
    '明天三點開會', '明天開會', '下午三點開會', '2026/02/30 15:00 開會',
    '明天下午三點要開會嗎？', '明天下午三點不要開會', '明天下午三點可能開會',
    '明天下午三點到五點開會', '明天下午三點開會後天上午九點聚餐',
    '今天晚上十二點 夜間作業', '今天25:30 開會', '明天15:99 開會', '明天下午三點',
])
def test_ambiguous_or_invalid_is_not_guessed(text):
    assert parse_message(text, STAMP) is None


def test_google_api_oauth_and_duplicate_id(monkeypatch):
    calls = []
    class Response:
        def __init__(self, status, body): self.status_code, self.body = status, body
        def raise_for_status(self):
            if self.status_code >= 400: raise requests.HTTPError()
        def json(self): return self.body
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(200, {'access_token': 'test-access'}) if url.endswith('/token') else Response(409, {})
    monkeypatch.setattr(requests, 'post', fake_post)
    google = GoogleCalendar({'GOOGLE_CLIENT_ID': 'test-client', 'GOOGLE_CLIENT_SECRET': 'test-secret',
                             'GOOGLE_REFRESH_TOKEN': 'test-refresh', 'GOOGLE_CALENDAR_ID': 'a/b'})
    event = parse_message('明天下午三點要開會', STAMP)
    identifier = google.create('test-event', event)
    assert len(identifier) == 64
    assert calls[0][1]['data']['grant_type'] == 'refresh_token'
    assert calls[1][0].endswith('/calendars/a%2Fb/events')
    assert calls[1][1]['json']['id'] == identifier
    assert calls[1][1]['headers']['Authorization'] == 'Bearer test-access'


def test_google_error_is_sanitized(monkeypatch):
    def fail(*args, **kwargs): raise requests.ConnectionError('test-secret')
    monkeypatch.setattr(requests, 'post', fail)
    google = GoogleCalendar({'GOOGLE_CLIENT_ID': 'test-client', 'GOOGLE_CLIENT_SECRET': 'test-secret',
                             'GOOGLE_REFRESH_TOKEN': 'test-refresh'})
    with pytest.raises(CalendarUnavailable) as e:
        google.create('test-event', {})
    assert 'test-secret' not in str(e.value)
