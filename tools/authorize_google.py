"""Run on your own computer to grant Google Calendar access (Desktop OAuth client)."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', required=True, help='Google Desktop OAuth client JSON file')
    parser.add_argument('--output', required=True, help='New private JSON file outside the repository')
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    if output.is_relative_to(repository) or output.exists():
        parser.error('Output must be a new file outside the repository')
    with open(args.client_file) as f:
        client = json.load(f).get('installed')
    if not client:
        parser.error('Use an OAuth client of type Desktop app')
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            valid = parsed.path == '/callback' and params.get('state') == [state]
            self.send_response(200 if valid else 400)
            self.end_headers()
            if not valid:
                self.wfile.write(b'Invalid OAuth callback')
                return
            result['code'] = params.get('code', [None])[0]
            result['error'] = params.get('error', [None])[0]
            self.wfile.write(b'You can close this window and return to the terminal.')

        def log_message(self, format, *args):
            pass  # OAuth callback query strings contain authorization codes.

    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        redirect_uri = f'http://127.0.0.1:{server.server_port}/callback'
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': client['client_id'], 'redirect_uri': redirect_uri,
            'response_type': 'code', 'scope': 'https://www.googleapis.com/auth/calendar.events',
            'access_type': 'offline', 'prompt': 'consent', 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        })
        print('Open this URL in a browser on this same computer and grant Calendar access:')
        print(url)
        webbrowser.open(url)
        server.timeout = 5
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get('code') or result.get('error'):
        parser.exit(1, 'Authorization was denied, invalid, or timed out. No credentials saved.\n')
    try:
        response = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': client['client_id'], 'client_secret': client['client_secret'],
            'code': result['code'], 'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code', 'code_verifier': verifier,
        }, timeout=20)
        response.raise_for_status()
        refresh = response.json()['refresh_token']
    except (requests.RequestException, ValueError, KeyError):
        parser.exit(1, 'Token exchange failed. No credentials saved.\n')
    values = {'GOOGLE_CLIENT_ID': client['client_id'],
              'GOOGLE_CLIENT_SECRET': client['client_secret'],
              'GOOGLE_REFRESH_TOKEN': refresh}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(values, f, indent=2)
    print(f'Private credentials saved to {output}. Enter them in secure deployment settings, never in chat or Git.')


if __name__ == '__main__':
    main()
