"""Conservative Chinese date parsing and Google Calendar event creation."""
import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo('Asia/Taipei')
DATE = r'(今天|明天|後天|(?:(\d{4})[/年-])?(\d{1,2})[/月-](\d{1,2})(?:日|號)?)'
TIME = r'(?:(上午|下午|晚上)\s*)?([0-9零〇一二兩三四五六七八九十]{1,3})(?::([0-9]{2})|[點時](半|[0-9零〇一二兩三四五六七八九十]{1,3}分)?)'
PATTERN = re.compile(r'^\s*' + DATE + r'\s*' + TIME + r'\s*(?:要\s*)?(.+?)\s*$')


def number(text):
    if text.isdigit():
        return int(text)
    digits = {'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '三': 3,
              '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    if text in digits:
        return digits[text]
    if '十' in text:
        left, right = text.split('十', 1)
        if (not left or left in digits) and (not right or right in digits):
            return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    raise ValueError('Unsupported number')


def parse_message(text, timestamp_ms):
    """Accept one explicit date/time at the beginning; leave ambiguity for review."""
    if re.search(r'取消|不要|不用|不開|改期|改到|可能|大概|每週|每月|每天|嗎|[?？]', text):
        return None
    match = PATTERN.fullmatch(text)
    if not match:
        return None
    date_text, year, month, day, period, hour, minute, chinese_minute, title = match.groups()
    if re.search(DATE, title) or re.search(TIME, title):
        return None
    try:
        reference = datetime.fromtimestamp(timestamp_ms / 1000, TZ)
        if date_text in ('今天', '明天', '後天'):
            date = (reference + timedelta(days={'今天': 0, '明天': 1, '後天': 2}[date_text])).date()
        else:
            date = datetime(int(year or reference.year), int(month), int(day)).date()
        hour = number(hour)
        minutes = int(minute) if minute is not None else (
            30 if chinese_minute == '半' else number(chinese_minute[:-1]) if chinese_minute else 0
        )
        if period:
            if not 1 <= hour <= 12 or (period == '晚上' and hour == 12):
                return None
            hour = hour % 12 + (12 if period in ('下午', '晚上') else 0)
        elif minute is None and hour <= 12:
            # "三點" needs 上午/下午; a 24-hour HH:MM is explicit.
            return None
        start = datetime(date.year, date.month, date.day, hour, minutes, tzinfo=TZ)
    except (ValueError, OverflowError, OSError):
        return None
    return {
        'summary': title,
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Asia/Taipei'},
        'end': {'dateTime': (start + timedelta(hours=1)).isoformat(), 'timeZone': 'Asia/Taipei'},
    }


class CalendarUnavailable(Exception):
    """A sanitized error that never includes OAuth credentials or API bodies."""


class GoogleCalendar:
    def __init__(self, config):
        self.config = config

    def create(self, event_id, event):
        calendar_id = self.config.get('GOOGLE_CALENDAR_ID', 'primary')
        identifier = hashlib.sha256(event_id.encode()).hexdigest()
        try:
            token_response = requests.post(
                'https://oauth2.googleapis.com/token',
                data={'client_id': self.config['GOOGLE_CLIENT_ID'],
                      'client_secret': self.config['GOOGLE_CLIENT_SECRET'],
                      'refresh_token': self.config['GOOGLE_REFRESH_TOKEN'],
                      'grant_type': 'refresh_token'},
                timeout=15,
            )
            token_response.raise_for_status()
            access_token = token_response.json()['access_token']
            response = requests.post(
                'https://www.googleapis.com/calendar/v3/calendars/' + quote(calendar_id, safe='') + '/events',
                headers={'Authorization': 'Bearer ' + access_token},
                params={'sendUpdates': 'none'},
                json={**event, 'id': identifier}, timeout=15,
            )
            # The deterministic ID also prevents duplicates after a crash/retry.
            if response.status_code != 409:
                response.raise_for_status()
            return identifier
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise CalendarUnavailable('Google Calendar request failed') from None
