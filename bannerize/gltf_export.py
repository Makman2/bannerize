"""Write decoded `MeshPart`s to a glTF 2.0 file Blender can import.

We deliberately write the glTF by hand (rather than depend on `pygltflib`) to
keep the dependency surface small and to avoid surprises with very large
binary buffers. We emit `.gltf` + `.bin` side by side; this round-trips cleanly
through Blender's bundled `io_scene_gltf2` importer.

Spec: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""
from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path
from typing import Iterable, Optional

from .geometry import MeshPart


GL_UNSIGNED_INT = 5125
GL_FLOAT = 5126
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963


def _pad(buf: bytearray, alignment: int = 4) -> None:
    while len(buf) % alignment != 0:
        buf.append(0)


def write_gltf(
    parts: Iterable[MeshPart],
    out_path: Path,
    *,
    name: str = "gear",
    diffuse_texture: Optional[Path] = None,
    base_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0),
) -> Path:
    parts = [p for p in parts if p.positions and p.indices]
    if not parts:
        raise ValueError("No mesh parts to export.")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path = out_path.with_suffix(".bin")

    buffer = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []

    def add_view(data: bytes, target: int, byte_stride: Optional[int] = None) -> int:
        _pad(buffer, 4)
        offset = len(buffer)
        buffer.extend(data)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target}
        if byte_stride:
            view["byteStride"] = byte_stride
        buffer_views.append(view)
        return len(buffer_views) - 1

    def add_accessor(
        view_idx: int,
        count: int,
        component_type: int,
        type_str: str,
        *,
        min_v: Optional[list[float]] = None,
        max_v: Optional[list[float]] = None,
    ) -> int:
        acc = {
            "bufferView": view_idx,
            "componentType": component_type,
            "count": count,
            "type": type_str,
        }
        if min_v is not None:
            acc["min"] = min_v
        if max_v is not None:
            acc["max"] = max_v
        accessors.append(acc)
        return len(accessors) - 1

    mesh_prims: list[dict] = []
    for i, part in enumerate(parts):
        # Positions
        pos_bytes = bytearray()
        min_p = [math.inf, math.inf, math.inf]
        max_p = [-math.inf, -math.inf, -math.inf]
        for x, y, z in part.positions:
            pos_bytes.extend(struct.pack("<3f", x, y, z))
            for j, v in enumerate((x, y, z)):
                if v < min_p[j]:
                    min_p[j] = v
                if v > max_p[j]:
                    max_p[j] = v
        pos_view = add_view(bytes(pos_bytes), TARGET_ARRAY_BUFFER, byte_stride=12)
        pos_acc = add_accessor(pos_view, len(part.positions), GL_FLOAT, "VEC3", min_v=min_p, max_v=max_p)

        attributes = {"POSITION": pos_acc}

        # Normals
        if part.has_normals:
            nrm_bytes = bytearray()
            for nx, ny, nz in part.normals:
                nrm_bytes.extend(struct.pack("<3f", nx, ny, nz))
            nrm_view = add_view(bytes(nrm_bytes), TARGET_ARRAY_BUFFER, byte_stride=12)
            nrm_acc = add_accessor(nrm_view, len(part.normals), GL_FLOAT, "VEC3")
            attributes["NORMAL"] = nrm_acc

        # UVs
        if part.uvs and len(part.uvs) == len(part.positions):
            uv_bytes = bytearray()
            for u, v in part.uvs:
                uv_bytes.extend(struct.pack("<2f", u, v))
            uv_view = add_view(bytes(uv_bytes), TARGET_ARRAY_BUFFER, byte_stride=8)
            uv_acc = add_accessor(uv_view, len(part.uvs), GL_FLOAT, "VEC2")
            attributes["TEXCOORD_0"] = uv_acc

        # Indices (uint32 to be safe for high vertex counts)
        idx_bytes = struct.pack(f"<{len(part.indices)}I", *part.indices)
        idx_view = add_view(idx_bytes, TARGET_ELEMENT_ARRAY_BUFFER)
        idx_acc = add_accessor(
            idx_view,
            len(part.indices),
            GL_UNSIGNED_INT,
            "SCALAR",
            min_v=[min(part.indices)],
            max_v=[max(part.indices)],
        )

        prim = {
            "attributes": attributes,
            "indices": idx_acc,
            "mode": 4,  # TRIANGLES
            "material": 0 if i == 0 else 0,  # share one material — recolor in Blender
            "extras": {"lod": part.lod_name, "dyeSlot": part.dye_slot},
        }
        mesh_prims.append(prim)

    meshes.append({"name": name, "primitives": mesh_prims})
    nodes.append({"name": name, "mesh": 0})

    # Materials + textures.
    materials = [
        {
            "name": f"{name}_mat",
            "pbrMetallicRoughness": {
                "baseColorFactor": list(base_color),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.6,
            },
            "doubleSided": True,
        }
    ]
    images: list[dict] = []
    textures: list[dict] = []
    if diffuse_texture and diffuse_texture.exists():
        images.append({"uri": diffuse_texture.name})
        textures.append({"source": 0})
        materials[0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        # Place the texture next to the .gltf.
        target = out_path.parent / diffuse_texture.name
        if target.resolve() != diffuse_texture.resolve():
            target.write_bytes(diffuse_texture.read_bytes())

    bin_path.write_bytes(bytes(buffer))

    doc = {
        "asset": {"version": "2.0", "generator": "bannerize"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"uri": bin_path.name, "byteLength": len(buffer)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": materials,
    }
    if images:
        doc["images"] = images
    if textures:
        doc["textures"] = textures

    out_path.write_text(json.dumps(doc, indent=2))
    return out_path
