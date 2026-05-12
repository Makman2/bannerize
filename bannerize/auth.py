"""OAuth 2.0 authorization-code flow for Bungie.net.

Bungie's OAuth endpoints (per the OAuth wiki on github.com/Bungie-net/api):
    authorize: https://www.bungie.net/en/oauth/authorize
    token:     https://www.bungie.net/platform/app/oauth/token/

Access tokens live 1 h, refresh tokens 90 days. Scopes are configured in the
application portal, NOT passed as a query parameter (Bungie rejects scope=).
"""
from __future__ import annotations

import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

import requests

from .config import Credentials, TokenSet


AUTHORIZE_URL = "https://www.bungie.net/en/oauth/authorize"
TOKEN_URL = "https://www.bungie.net/platform/app/oauth/token/"


class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """Tiny loopback HTTP server to catch the OAuth redirect."""

    server_version = "bannerize/0.1"
    code: Optional[str] = None
    state: Optional[str] = None

    def do_GET(self):  # noqa: N802
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        type(self).code = (params.get("code") or [None])[0]
        type(self).state = (params.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>You can close this tab.</h1>"
            b"<p>bannerize received the authorization code.</p></body></html>"
        )

    def log_message(self, *_args, **_kwargs):  # silence stderr
        return


def _run_callback_server(host: str, port: int) -> "http.server.HTTPServer":
    server = http.server.HTTPServer((host, port), _CodeCatcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def authorize_interactive(creds: Credentials, *, open_browser: bool = True) -> TokenSet:
    """Run the auth-code flow. Spins up a localhost listener on the redirect URI's port.

    The redirect URI registered in the Bungie portal must be an http://localhost URL
    or an https:// URL you can serve. For Confidential clients on a developer machine
    we recommend registering `http://localhost:7777/callback` and setting
    `BUNGIE_REDIRECT_URI` accordingly.
    """
    parsed = urllib.parse.urlparse(creds.redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError(
            f"Interactive auth needs a loopback redirect URI; got {creds.redirect_uri}"
        )

    state = secrets.token_urlsafe(16)
    qs = urllib.parse.urlencode(
        {"client_id": creds.client_id, "response_type": "code", "state": state}
    )
    url = f"{AUTHORIZE_URL}?{qs}"

    server = _run_callback_server(parsed.hostname, parsed.port or 80)
    try:
        if open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        print(f"Open this URL in your browser to authorize bannerize:\n  {url}\n")

        deadline = time.time() + 300
        while _CodeCatcher.code is None and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()

    if _CodeCatcher.code is None:
        raise TimeoutError("Authorization timed out (no code received).")
    if _CodeCatcher.state != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF, aborting.")

    return exchange_code(creds, _CodeCatcher.code)


def exchange_code(creds: Credentials, code: str) -> TokenSet:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": creds.client_id,
    }
    auth = None
    if creds.client_secret:
        auth = (creds.client_id, creds.client_secret)
    r = requests.post(TOKEN_URL, data=data, auth=auth, timeout=30)
    r.raise_for_status()
    return _tokens_from_response(r.json())


def refresh_tokens(creds: Credentials, tokens: TokenSet) -> TokenSet:
    if not tokens.refresh_token:
        raise RuntimeError(
            "No refresh token stored. Register a Confidential client at "
            "bungie.net/en/Application to receive refresh tokens, then re-authorize."
        )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": creds.client_id,
    }
    if creds.client_secret:
        data["client_secret"] = creds.client_secret
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    new = _tokens_from_response(r.json())
    new.membership_id = tokens.membership_id
    return new


def _tokens_from_response(body: dict) -> TokenSet:
    now = time.time()
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_at=now + float(body.get("expires_in", 3600)),
        refresh_expires_at=(
            now + float(body["refresh_expires_in"])
            if body.get("refresh_expires_in")
            else None
        ),
        membership_id=body.get("membership_id"),
    )


def ensure_fresh(creds: Credentials, tokens: TokenSet, *, skew: float = 60.0) -> TokenSet:
    """Refresh the access token if it's within `skew` seconds of expiry."""
    if time.time() + skew < tokens.expires_at:
        return tokens
    fresh = refresh_tokens(creds, tokens)
    fresh.save()
    return fresh
