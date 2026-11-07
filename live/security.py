"""Small, dependency-free settings for the explicitly enabled public board API."""
import hmac
import os
from pathlib import Path


def board_token():
    filename = os.environ.get('LIVE_TOKEN_FILE')
    return Path(filename).read_text().strip() if filename else os.environ.get('LIVE_TOKEN', '').strip()


def require_public_token():
    try:
        token = board_token()
    except OSError as error:
        raise ValueError('Arduino device credential is unavailable') from error
    if len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError('Public Arduino requires a private device token of at least 32 characters')
    return token


def token_matches(expected, supplied):
    return bool(expected) and hmac.compare_digest(expected.encode(), (supplied or '').encode())
