"""Thin Bungie.net Platform client.

We only implement the four endpoints the bannerize pipeline actually uses:
    GET  /Platform/Destiny2/Manifest/
    GET  /Platform/User/GetMembershipsForCurrentUser/
    GET  /Platform/Destiny2/{type}/Profile/{id}/LinkedProfiles/
    GET  /Platform/Destiny2/{type}/Profile/{id}/?components=...

Everything else (search, item moves, vendors) is out of scope; if you need them,
use `bungio` instead — it's regenerated from openapi and covers the full surface.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

import requests

from . import __version__
from .auth import ensure_fresh
from .config import Credentials, TokenSet


PLATFORM_ROOT = "https://www.bungie.net/Platform"
USER_AGENT = f"bannerize/{__version__} (+https://github.com/makman2/bannerize)"


class BungieAPIError(RuntimeError):
    def __init__(self, status: int, error_code: Optional[int], message: str):
        super().__init__(f"Bungie API error ({status}, code={error_code}): {message}")
        self.status = status
        self.error_code = error_code
        self.message = message


class BungieClient:
    """Minimal sync HTTP client. Handles retries on 429 / network blips."""

    def __init__(
        self,
        creds: Credentials,
        tokens: Optional[TokenSet] = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
    ):
        self.creds = creds
        self.tokens = tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-Key": creds.api_key, "User-Agent": USER_AGENT}
        )

    def _headers(self, *, with_auth: bool) -> dict:
        h = {}
        if with_auth and self.tokens:
            self.tokens = ensure_fresh(self.creds, self.tokens)
            h["Authorization"] = f"Bearer {self.tokens.access_token}"
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        with_auth: bool = False,
    ) -> dict:
        url = f"{PLATFORM_ROOT}{path}"
        backoff = 1.0
        last_exc: Optional[Exception] = None
        for _ in range(self.max_retries + 1):
            try:
                r = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(with_auth=with_auth),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(backoff)
                backoff *= 2
                continue

            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue

            try:
                body = r.json()
            except ValueError:
                r.raise_for_status()
                raise BungieAPIError(r.status_code, None, r.text[:200])

            # Bungie wraps every response in {Response, ErrorCode, ErrorStatus, Message}.
            if isinstance(body, dict) and body.get("ErrorCode") not in (None, 1):
                code = body.get("ErrorCode")
                msg = body.get("Message", "")
                # Throttled — back off and retry.
                if code in (36, 51, 52):
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise BungieAPIError(r.status_code, code, msg)

            if r.status_code >= 400:
                raise BungieAPIError(r.status_code, None, r.text[:200])

            return body.get("Response", body)

        if last_exc:
            raise last_exc
        raise BungieAPIError(0, None, "Exhausted retries")

    # ----- endpoints -----

    def get_manifest(self) -> dict:
        return self.request("GET", "/Destiny2/Manifest/")

    def get_memberships_for_current_user(self) -> dict:
        return self.request(
            "GET", "/User/GetMembershipsForCurrentUser/", with_auth=True
        )

    def get_linked_profiles(
        self,
        membership_type: int,
        membership_id: str,
        *,
        get_all: bool = True,
    ) -> dict:
        path = (
            f"/Destiny2/{membership_type}/Profile/{membership_id}/LinkedProfiles/"
        )
        return self.request(
            "GET",
            path,
            params={"getAllMemberships": "true" if get_all else "false"},
            with_auth=True,
        )

    def get_profile(
        self,
        membership_type: int,
        destiny_membership_id: str,
        components: Iterable[int],
    ) -> dict:
        path = f"/Destiny2/{membership_type}/Profile/{destiny_membership_id}/"
        return self.request(
            "GET",
            path,
            params={"components": ",".join(str(c) for c in components)},
            with_auth=True,
        )


def pick_primary_membership(membership_data: dict) -> tuple[int, str]:
    """Choose the right (membershipType, destinyMembershipId) for GetProfile.

    Honours cross-save: if a membership has `crossSaveOverride != 0`, the
    profile that owns the data is the one whose `membershipType` matches that
    override. Otherwise the first destiny membership wins.
    """
    memberships = membership_data.get("destinyMemberships", [])
    if not memberships:
        raise RuntimeError("No Destiny memberships on this Bungie.net account.")

    primary_id = membership_data.get("primaryMembershipId")
    if primary_id:
        for m in memberships:
            if str(m.get("membershipId")) == str(primary_id):
                xs = m.get("crossSaveOverride", 0) or m.get("membershipType")
                return int(xs), str(m["membershipId"])

    # Fall back: any membership whose own type matches its crossSaveOverride.
    for m in memberships:
        xs = m.get("crossSaveOverride", 0)
        if xs and int(xs) == int(m.get("membershipType", 0)):
            return int(m["membershipType"]), str(m["membershipId"])

    m = memberships[0]
    return int(m["membershipType"]), str(m["membershipId"])
