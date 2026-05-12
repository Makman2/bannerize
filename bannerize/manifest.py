"""Manifest cache + definition lookup.

The Bungie manifest is delivered three ways. We use the per-table JSON
("`jsonWorldComponentContentPaths[locale][TableName]`") because:
  - we only need a handful of tables (not the 80+ MB monolith);
  - JSON is trivial to consume from pure Python;
  - downloads are cacheable by version + URL.

The gear-asset SQLite (a different artifact, found at
`mobileGearAssetDataBases[].path`) is handled by `gear_assets.py`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests

from .api import BungieClient
from .config import ensure_cache_dir


BUNGIE_NET = "https://www.bungie.net"

# Definition tables we resolve. Everything else can be added later.
DEFAULT_TABLES = (
    "DestinyInventoryItemDefinition",
    "DestinyClassDefinition",
    "DestinyRaceDefinition",
    "DestinyGenderDefinition",
    "DestinySocketCategoryDefinition",
    "DestinySocketTypeDefinition",
    "DestinyInventoryBucketDefinition",
    "DestinyEquipmentSlotDefinition",
    "DestinyPlugSetDefinition",
    "DestinyStatDefinition",
    "DestinyDamageTypeDefinition",
    "DestinySandboxPerkDefinition",
)


@dataclass
class ManifestRefs:
    version: str
    locale: str
    table_urls: dict[str, str]  # table name -> absolute URL
    mobile_gear_asset_dbs: list[dict]  # entries from manifest.mobileGearAssetDataBases
    mobile_gear_cdn: dict[str, str]  # geometry/texture/etc. base URLs


class Manifest:
    """Lazy-loading definition table cache."""

    def __init__(self, refs: ManifestRefs, cache_dir: Path):
        self.refs = refs
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._tables: dict[str, dict] = {}

    @classmethod
    def fetch(
        cls,
        client: BungieClient,
        *,
        locale: str = "en",
        tables: Iterable[str] = DEFAULT_TABLES,
        force: bool = False,
    ) -> "Manifest":
        raw = client.get_manifest()
        version = raw["version"]
        component_paths = raw["jsonWorldComponentContentPaths"][locale]
        table_urls = {t: f"{BUNGIE_NET}{component_paths[t]}" for t in tables if t in component_paths}

        refs = ManifestRefs(
            version=version,
            locale=locale,
            table_urls=table_urls,
            mobile_gear_asset_dbs=list(raw.get("mobileGearAssetDataBases", [])),
            mobile_gear_cdn=dict(raw.get("mobileGearCDN", {})),
        )

        cache_dir = ensure_cache_dir(f"manifest/{version}/{locale}")
        manifest = cls(refs, cache_dir)
        # Save the trimmed refs alongside.
        refs_path = cache_dir / "refs.json"
        if force or not refs_path.exists():
            refs_path.write_text(
                json.dumps(
                    {
                        "version": refs.version,
                        "locale": refs.locale,
                        "table_urls": refs.table_urls,
                        "mobile_gear_asset_dbs": refs.mobile_gear_asset_dbs,
                        "mobile_gear_cdn": refs.mobile_gear_cdn,
                    },
                    indent=2,
                )
            )
        return manifest

    # ----- table loading -----

    def table(self, name: str) -> dict:
        """Return the entire definition table as a dict keyed by `str(hash)`."""
        if name in self._tables:
            return self._tables[name]
        path = self.cache_dir / f"{name}.json"
        if not path.exists():
            url = self.refs.table_urls.get(name)
            if not url:
                raise KeyError(f"Manifest table not in refs: {name}")
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            path.write_bytes(r.content)
        self._tables[name] = json.loads(path.read_text())
        return self._tables[name]

    def get(self, table: str, hash_value: int) -> Optional[dict]:
        # Bungie hashes are unsigned 32-bit; the JSON tables key them as strings.
        return self.table(table).get(str(_to_u32(hash_value)))

    # convenience getters
    def item(self, hash_value: int) -> Optional[dict]:
        return self.get("DestinyInventoryItemDefinition", hash_value)

    def class_(self, hash_value: int) -> Optional[dict]:
        return self.get("DestinyClassDefinition", hash_value)

    def race(self, hash_value: int) -> Optional[dict]:
        return self.get("DestinyRaceDefinition", hash_value)

    def gender(self, hash_value: int) -> Optional[dict]:
        return self.get("DestinyGenderDefinition", hash_value)

    def socket_category(self, hash_value: int) -> Optional[dict]:
        return self.get("DestinySocketCategoryDefinition", hash_value)


def _to_u32(value: int) -> int:
    """Bungie occasionally serialises hashes as signed ints; normalize to u32."""
    return value & 0xFFFFFFFF
