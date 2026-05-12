"""Interpret an item's sockets — find the currently-applied shader and ornament.

Bungie does not publish a stable "shader is always at index N" rule; the index
varies by item. The robust approach is:

  1. Walk `DestinyInventoryItemDefinition.sockets.socketCategories[]`.
  2. For each category, resolve `socketCategoryHash` via the manifest.
  3. Match the category by hash (these are stable across Bungie's API surface):
       2048875504 — ARMOR COSMETICS  (ornament + shader sockets)
       2685412949 — WEAPON COSMETICS (shader + tracker + memento)
  4. Map the category's `socketIndexes` into the live
     `itemComponents.sockets.data[instId].sockets[]` array to read the
     currently-plugged `plugHash`.
  5. Resolve each plugHash via `DestinyInventoryItemDefinition`; its
     `plug.plugCategoryIdentifier` string tells you what *kind* of plug it is.

We expose two facts per equipped item:
  - shader_plug_hash : the user-applied shader (or None for default)
  - ornament_plug_hash : the user-applied ornament that overrides geometry
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .manifest import Manifest


ARMOR_COSMETICS_CATEGORY = 2048875504
WEAPON_COSMETICS_CATEGORY = 2685412949

# plugCategoryIdentifier substrings (Bungie uses many strings; we match on these).
_SHADER_HINTS = ("shader",)
_ORNAMENT_HINTS = (
    "skins_",
    "universal_ornaments",
    "armor_skins_",
    "weapon_skins_",
    "exotic_all_skins",
    "ornaments",
)


@dataclass
class SocketResolution:
    shader_plug_hash: Optional[int]
    ornament_plug_hash: Optional[int]


def _plug_is_shader(plug_def: dict) -> bool:
    pcid = (plug_def.get("plug") or {}).get("plugCategoryIdentifier", "")
    return any(h in pcid for h in _SHADER_HINTS)


def _plug_is_ornament(plug_def: dict) -> bool:
    pcid = (plug_def.get("plug") or {}).get("plugCategoryIdentifier", "")
    return any(h in pcid for h in _ORNAMENT_HINTS)


def resolve_item_sockets(
    manifest: Manifest,
    item_def: dict,
    instance_sockets: list[dict],
) -> SocketResolution:
    """Identify shader + ornament plugs currently applied to an equipped item.

    `instance_sockets` is the live list at
    `Response.itemComponents.sockets.data[instId].sockets[]` from GetProfile.
    """
    shader: Optional[int] = None
    ornament: Optional[int] = None

    sockets_block = item_def.get("sockets") or {}
    categories = sockets_block.get("socketCategories") or []

    # Build the list of cosmetic socket indices.
    cosmetic_indices: list[int] = []
    for cat in categories:
        cat_hash = cat.get("socketCategoryHash")
        if cat_hash in (ARMOR_COSMETICS_CATEGORY, WEAPON_COSMETICS_CATEGORY):
            cosmetic_indices.extend(cat.get("socketIndexes", []))

    # Plus: ornaments on weapons live in the weapon-perks category (the first
    # socket of the weapon's perk row holds the ornament). We don't try to be
    # exhaustive — fall back to scanning *all* socket indices for plug-category
    # matches if the cosmetics category yielded nothing.
    scan_indices = cosmetic_indices or list(range(len(instance_sockets)))

    for idx in scan_indices:
        if idx >= len(instance_sockets):
            continue
        socket = instance_sockets[idx]
        plug_hash = socket.get("plugHash")
        if not plug_hash or not socket.get("isVisible", True):
            continue
        plug_def = manifest.item(plug_hash)
        if not plug_def:
            continue
        if shader is None and _plug_is_shader(plug_def):
            shader = plug_hash
        elif ornament is None and _plug_is_ornament(plug_def):
            ornament = plug_hash

    return SocketResolution(shader_plug_hash=shader, ornament_plug_hash=ornament)
