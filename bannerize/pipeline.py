"""High-level orchestrator: profile fetch → per-item geometry → glTF on disk.

The pipeline writes a directory like:

    out/
      manifest_summary.json
      <character_id>/
        character.json
        <slot>_<itemHash>.gltf
        <slot>_<itemHash>.bin
        <slot>_<itemHash>_diffuse.png      (or .jpg)
      scene.json                            (assembly hint for scene.py)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .api import BungieClient, pick_primary_membership
from .config import Credentials, TokenSet, ensure_cache_dir
from .gear_assets import (
    GearAssetsDB,
    download_cached,
    geometry_url,
    texture_url,
)
from .geometry import decode_tgxm
from .gltf_export import write_gltf
from .manifest import Manifest
from .sockets import resolve_item_sockets
from .tgxm import parse as parse_tgxm, sniff_image_extension


# Components we always pull for rendering equipped gear.
PROFILE_COMPONENTS = (200, 205, 300, 305)


@dataclass
class ExportedItem:
    item_hash: int
    item_instance_id: Optional[str]
    item_name: str
    item_type: str
    bucket_hash: int
    slot: str
    gltf_path: str
    shader_plug_hash: Optional[int]
    ornament_plug_hash: Optional[int]
    used_item_hash: int  # which itemHash actually drove the geometry lookup


@dataclass
class ExportedCharacter:
    character_id: str
    class_name: str
    race_name: str
    gender_name: str
    light: int
    emblem_path: Optional[str]
    items: list[ExportedItem]


def fetch_and_export(
    creds: Credentials,
    tokens: TokenSet,
    out_dir: Path,
    *,
    membership_type: Optional[int] = None,
    destiny_membership_id: Optional[str] = None,
    refresh_manifest: bool = False,
) -> list[ExportedCharacter]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = BungieClient(creds, tokens)

    # 1. Identify the player.
    if not membership_type or not destiny_membership_id:
        memberships = client.get_memberships_for_current_user()
        membership_type, destiny_membership_id = pick_primary_membership(memberships)

    # 2. Load manifest definitions we need.
    manifest = Manifest.fetch(client, force=refresh_manifest)
    gear_db = GearAssetsDB(manifest)

    # 3. Pull profile components.
    profile = client.get_profile(
        membership_type, destiny_membership_id, PROFILE_COMPONENTS
    )

    characters = (profile.get("characters") or {}).get("data") or {}
    equipment = (profile.get("characterEquipment") or {}).get("data") or {}
    item_sockets = ((profile.get("itemComponents") or {}).get("sockets") or {}).get("data") or {}

    geometry_cache = ensure_cache_dir("geometry")
    texture_cache = ensure_cache_dir("textures")

    exported: list[ExportedCharacter] = []
    for char_id, char in characters.items():
        char_out = out_dir / char_id
        char_out.mkdir(parents=True, exist_ok=True)

        items_out: list[ExportedItem] = []
        char_equip = (equipment.get(char_id) or {}).get("items") or []

        for it in char_equip:
            item_hash = int(it["itemHash"])
            instance_id = it.get("itemInstanceId")
            bucket_hash = int(it.get("bucketHash", 0))
            item_def = manifest.item(item_hash) or {}

            # Resolve socket plugs (shader + ornament).
            sockets_data = item_sockets.get(str(instance_id)) if instance_id else None
            shader_hash = None
            ornament_hash = None
            if sockets_data and item_def.get("sockets"):
                resolution = resolve_item_sockets(
                    manifest, item_def, sockets_data.get("sockets", [])
                )
                shader_hash = resolution.shader_plug_hash
                ornament_hash = resolution.ornament_plug_hash

            # If an ornament is applied, prefer its gear asset.
            geometry_item_hash = ornament_hash or item_hash

            gear_asset = gear_db.lookup(geometry_item_hash)
            if gear_asset is None and ornament_hash is not None:
                gear_asset = gear_db.lookup(item_hash)
                geometry_item_hash = item_hash

            if gear_asset is None:
                # Many manifest items have no gear asset (consumables, etc.) — skip.
                continue
            mobile = gear_asset.mobile()
            if mobile is None or not mobile.geometry:
                continue

            slot = _slot_name(item_def, bucket_hash)
            display = (item_def.get("displayProperties") or {}).get("name", str(item_hash))
            item_type = item_def.get("itemTypeDisplayName", "")

            # Download all geometry TGXMs for this item, decode, merge.
            parts = []
            for geom_name in mobile.geometry:
                url = geometry_url(manifest, geom_name)
                try:
                    tgxm_path = download_cached(url, geometry_cache)
                except Exception as exc:
                    print(f"  [warn] failed to fetch {geom_name}: {exc}")
                    continue
                archive = parse_tgxm(tgxm_path.read_bytes())
                parts.extend(decode_tgxm(archive))

            if not parts:
                continue

            # Pull one diffuse texture (first one in the first texture TGXM).
            diffuse_path: Optional[Path] = None
            if mobile.textures:
                try:
                    tex_url = texture_url(manifest, mobile.textures[0])
                    tex_path = download_cached(tex_url, texture_cache)
                    tex_archive = parse_tgxm(tex_path.read_bytes())
                    diffuse_path = _extract_first_diffuse(
                        tex_archive, char_out, slot, geometry_item_hash
                    )
                except Exception as exc:
                    print(f"  [warn] failed to extract texture for {display}: {exc}")

            gltf_name = f"{slot}_{geometry_item_hash}.gltf"
            gltf_path = char_out / gltf_name
            write_gltf(parts, gltf_path, name=display, diffuse_texture=diffuse_path)

            items_out.append(
                ExportedItem(
                    item_hash=item_hash,
                    item_instance_id=instance_id,
                    item_name=display,
                    item_type=item_type,
                    bucket_hash=bucket_hash,
                    slot=slot,
                    gltf_path=str(gltf_path.relative_to(out_dir)),
                    shader_plug_hash=shader_hash,
                    ornament_plug_hash=ornament_hash,
                    used_item_hash=geometry_item_hash,
                )
            )

        class_def = manifest.class_(int(char.get("classHash", 0))) or {}
        race_def = manifest.race(int(char.get("raceHash", 0))) or {}
        gender_def = manifest.gender(int(char.get("genderHash", 0))) or {}

        exported_char = ExportedCharacter(
            character_id=char_id,
            class_name=(class_def.get("displayProperties") or {}).get("name", "Unknown"),
            race_name=(race_def.get("displayProperties") or {}).get("name", "Unknown"),
            gender_name=(gender_def.get("displayProperties") or {}).get("name", ""),
            light=int(char.get("light", 0)),
            emblem_path=char.get("emblemPath"),
            items=items_out,
        )
        exported.append(exported_char)

        (char_out / "character.json").write_text(
            json.dumps(asdict(exported_char), indent=2)
        )

    (out_dir / "scene.json").write_text(
        json.dumps([asdict(c) for c in exported], indent=2)
    )
    return exported


def _slot_name(item_def: dict, bucket_hash: int) -> str:
    # Best-effort human label. itemTypeDisplayName is e.g. "Helmet", "Gauntlets",
    # "Chest Armor", "Leg Armor", "Hunter Cloak", "Pulse Rifle".
    type_name = item_def.get("itemTypeDisplayName", "") or item_def.get("itemTypeAndTierDisplayName", "")
    type_name = type_name.replace(" ", "").replace("/", "_") or f"bucket{bucket_hash}"
    return type_name.lower()


def _extract_first_diffuse(
    archive,
    out_dir: Path,
    slot: str,
    item_hash: int,
) -> Optional[Path]:
    """Pull the first image-looking entry out of a texture TGXM as a sidecar file."""
    for entry in archive.entries:
        data = archive.data[entry.offset : entry.offset + entry.size]
        if not data:
            continue
        ext = sniff_image_extension(data)
        target = out_dir / f"{slot}_{item_hash}_diffuse.{ext}"
        target.write_bytes(data)
        return target
    return None
