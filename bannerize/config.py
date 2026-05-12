"""Configuration and credential handling.

Reads from environment variables first, then `~/.config/bannerize/config.json`.
Tokens (OAuth access/refresh) are persisted separately in
`~/.config/bannerize/tokens.json` because they rotate.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_DIR = Path(os.environ.get("BANNERIZE_HOME", "")) or (
    Path.home() / ".config" / "bannerize"
)
CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
TOKENS_PATH = DEFAULT_CONFIG_DIR / "tokens.json"
CACHE_DIR = DEFAULT_CONFIG_DIR / "cache"


@dataclass
class Credentials:
    api_key: str
    client_id: str
    client_secret: Optional[str] = None  # None for Public clients
    redirect_uri: str = "https://localhost:7777/callback"

    @classmethod
    def load(cls) -> "Credentials":
        api_key = os.environ.get("BUNGIE_API_KEY")
        client_id = os.environ.get("BUNGIE_CLIENT_ID")
        client_secret = os.environ.get("BUNGIE_CLIENT_SECRET")
        redirect_uri = os.environ.get("BUNGIE_REDIRECT_URI")

        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            api_key = api_key or data.get("api_key")
            client_id = client_id or data.get("client_id")
            client_secret = client_secret or data.get("client_secret")
            redirect_uri = redirect_uri or data.get("redirect_uri")

        if not api_key:
            raise RuntimeError(
                "Bungie API key not configured. Register an app at "
                "https://www.bungie.net/en/Application then set BUNGIE_API_KEY "
                "or create ~/.config/bannerize/config.json."
            )
        if not client_id:
            raise RuntimeError(
                "Bungie OAuth client id not configured (BUNGIE_CLIENT_ID)."
            )

        return cls(
            api_key=api_key,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri or "https://localhost:7777/callback",
        )


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # epoch seconds
    refresh_expires_at: Optional[float]
    membership_id: Optional[str] = None  # Bungie.net membership id

    def to_json(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "membership_id": self.membership_id,
        }

    @classmethod
    def from_json(cls, data: dict) -> "TokenSet":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data["expires_at"],
            refresh_expires_at=data.get("refresh_expires_at"),
            membership_id=data.get("membership_id"),
        )

    @classmethod
    def load(cls) -> Optional["TokenSet"]:
        if not TOKENS_PATH.exists():
            return None
        return cls.from_json(json.loads(TOKENS_PATH.read_text()))

    def save(self) -> None:
        TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKENS_PATH.write_text(json.dumps(self.to_json(), indent=2))
        try:
            os.chmod(TOKENS_PATH, 0o600)
        except OSError:
            pass


def ensure_cache_dir(subdir: str = "") -> Path:
    target = CACHE_DIR / subdir if subdir else CACHE_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target
