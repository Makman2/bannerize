"""Gear-assets SQLite + per-item gear JSON.

`Destiny2.GetDestinyManifest` exposes `mobileGearAssetDataBases`: a list of
`{ version, path }` entries pointing to ZIP-wrapped SQLite databases. Each DB
holds a `DestinyGearAssetsDefinition` table keyed by `itemHash` (as a string).
The row's `json` column contains the gear-art-arrangement JSON described in
https://bungienetplatform.fandom.com/wiki/GearAssetsDefinition — it points at
TGXM filenames on the mobile CDN.
"""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from .manifest import BUNGIE_NET, Manifest


@dataclass
class GearAssetContent:
    platform: str
    geometry: list[str] = field(default_factory=list)
    textures: list[str] = field(default_factory=list)
    male_index_set: Optional[dict] = None
    female_index_set: Optional[dict] = None
    dye_index_set: Optional[dict] = None
    region_index_sets: dict = field(default_factory=dict)


@dataclass
class GearAsset:
    item_hash: int
    gear: list[str]
    content: list[GearAssetContent]
    raw: dict

    def mobile(self) -> Optional[GearAssetContent]:
        for c in self.content:
            if c.platform == "mobile":
                return c
        return None


def _open_gear_db(manifest: Manifest) -> sqlite3.Connection:
    """Download (if needed) and open the gear-assets SQLite."""
    entries = manifest.refs.mobile_gear_asset_dbs
    if not entries:
        raise RuntimeError(
            "Manifest has no mobileGearAssetDataBases entries — Bungie may have "
            "moved the gear assets. Re-run with --refresh-manifest."
        )
    # Prefer the highest-version entry.
    entry = max(entries, key=lambda e: int(e.get("version", 0)))
    url = entry["path"]
    if url.startswith("/"):
        url = BUNGIE_NET + url

    cache = manifest.cache_dir / f"gear_assets_v{entry.get('version', '0')}.sqlite"
    if not cache.exists():
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        # The artifact is a ZIP containing a single SQLite file.
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            inner = next(n for n in zf.namelist() if not n.endswith("/"))
            cache.write_bytes(zf.read(inner))
    return sqlite3.connect(str(cache))


class GearAssetsDB:
    """Wraps the gear-assets SQLite; lazy-opens on first lookup."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self._conn: Optional[sqlite3.Connection] = None

    def _conn_lazy(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _open_gear_db(self.manifest)
        return self._conn

    def lookup(self, item_hash: int) -> Optional[GearAsset]:
        # The mobile gear DB sometimes stores hashes as signed int32 keys.
        as_u32 = item_hash & 0xFFFFFFFF
        as_s32 = as_u32 if as_u32 < 0x80000000 else as_u32 - 0x100000000

        conn = self._conn_lazy()
        cur = conn.cursor()
        row = cur.execute(
            "SELECT json FROM DestinyGearAssetsDefinition WHERE id = ? OR id = ?",
            (as_u32, as_s32),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        content = [
            GearAssetContent(
                platform=c.get("platform", "unknown"),
                geometry=list(c.get("geometry", [])),
                textures=list(c.get("textures", [])),
                male_index_set=c.get("male_index_set"),
                female_index_set=c.get("female_index_set"),
                dye_index_set=c.get("dye_index_set"),
                region_index_sets=dict(c.get("region_index_sets", {})),
            )
            for c in data.get("content", [])
        ]
        return GearAsset(
            item_hash=as_u32,
            gear=list(data.get("gear", [])),
            content=content,
            raw=data,
        )


# ---- CDN download helpers ----

def _cdn_join(base: str, name: str) -> str:
    if name.startswith("/"):
        return BUNGIE_NET + name
    return base.rstrip("/") + "/" + name


def geometry_url(manifest: Manifest, filename: str) -> str:
    base = manifest.refs.mobile_gear_cdn.get(
        "Geometry", "/common/destiny2_content/geometry/platform/mobile/geometry"
    )
    if base.startswith("/"):
        base = BUNGIE_NET + base
    return _cdn_join(base, filename)


def texture_url(manifest: Manifest, filename: str) -> str:
    base = manifest.refs.mobile_gear_cdn.get(
        "Texture", "/common/destiny2_content/geometry/platform/mobile/textures"
    )
    if base.startswith("/"):
        base = BUNGIE_NET + base
    return _cdn_join(base, filename)


def download_cached(url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    target = cache_dir / name
    if target.exists() and target.stat().st_size > 0:
        return target
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    target.write_bytes(r.content)
    return target
