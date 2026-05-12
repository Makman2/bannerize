"""Decode TGXM render meshes into plain Python triangle data.

A TGXM geometry container holds:
  - `render_metadata.js`: JSON describing the meshes (despite the .js extension).
  - Several `*.indexbuffer.tgx`, `*.vertexbuffer.tgx` blobs referenced by the JSON.

For each render mesh we produce, after filtering to stage 0 + LOD 0, a list of
`MeshPart` records carrying float32 positions / normals / UVs in world space and
a triangle-list index buffer in `uint32`.

The decode formulas are pulled directly from Bungie's own `spasm_webgl_gear_shader.js`:

    gl_Position.xyz = a_position * u_position_scale + u_position_offset
    v_texcoord.xy   = a_texcoord * u_texcoord_scale + u_texcoord_offset

so we keep their math verbatim.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import Optional

from .tgxm import TGXMArchive


# --- vertex-format type table -------------------------------------------------

_TYPE_PREFIX = "_vertex_format_attribute_"
_SEMANTIC_PREFIX = "_tfx_vb_semantic_"

# Each entry: (struct_code, count, signed, bits)
_TYPE_TABLE: dict[str, tuple[str, int, bool, int]] = {
    "float":   ("<f", 1, True, 32),
    "float2":  ("<2f", 2, True, 32),
    "float3":  ("<3f", 3, True, 32),
    "float4":  ("<4f", 4, True, 32),
    "byte":    ("<b", 1, True, 8),
    "byte2":   ("<2b", 2, True, 8),
    "byte4":   ("<4b", 4, True, 8),
    "ubyte":   ("<B", 1, False, 8),
    "ubyte2":  ("<2B", 2, False, 8),
    "ubyte4":  ("<4B", 4, False, 8),
    "short":   ("<h", 1, True, 16),
    "short2":  ("<2h", 2, True, 16),
    "short4":  ("<4h", 4, True, 16),
    "ushort":  ("<H", 1, False, 16),
    "ushort2": ("<2H", 2, False, 16),
    "ushort4": ("<4H", 4, False, 16),
    "int":     ("<i", 1, True, 32),
    "int2":    ("<2i", 2, True, 32),
    "int4":    ("<4i", 4, True, 32),
    "uint":    ("<I", 1, False, 32),
    "uint2":   ("<2I", 2, False, 32),
    "uint4":   ("<4I", 4, False, 32),
}


def _normalize(value: float, *, signed: bool, bits: int) -> float:
    if bits >= 32:  # float types are pass-through
        return value
    if signed:
        max_val = (1 << (bits - 1)) - 1
        return max(value / max_val, -1.0)
    max_val = (1 << bits) - 1
    return value / max_val


def _decode_attr(
    buf: bytes,
    offset: int,
    type_name: str,
    normalized: bool,
) -> list[float]:
    if type_name not in _TYPE_TABLE:
        raise ValueError(f"Unsupported vertex attr type: {type_name}")
    code, count, signed, bits = _TYPE_TABLE[type_name]
    raw = struct.unpack_from(code, buf, offset)
    if normalized and bits < 32:
        return [_normalize(v, signed=signed, bits=bits) for v in raw]
    return [float(v) for v in raw]


# --- data classes -------------------------------------------------------------

@dataclass
class VertexElement:
    offset: int
    semantic: str  # stripped, e.g. "position", "texcoord", "normal"
    semantic_index: int
    type_name: str  # stripped, e.g. "short4"
    normalized: bool


@dataclass
class VertexBufferRef:
    file_name: str
    byte_size: int
    stride: int


@dataclass
class IndexBufferRef:
    file_name: str
    byte_size: int
    value_byte_size: int  # 2 = uint16, 4 = uint32


@dataclass
class StagePart:
    external_identifier: int
    primitive_type: int  # 3 = TRIANGLES, 5 = TRIANGLE_STRIP
    start_index: int
    index_count: int
    index_min: int
    index_max: int
    lod_name: str
    dye_slot: int


@dataclass
class RenderMesh:
    position_scale: tuple[float, float, float]
    position_offset: tuple[float, float, float]
    texcoord_scale: tuple[float, float]
    texcoord_offset: tuple[float, float]
    vertex_buffers: list[VertexBufferRef]
    index_buffer: IndexBufferRef
    layout_per_buffer: list[list[VertexElement]]  # one element-list per vertex_buffer
    stage_parts: list[StagePart]
    stage_part_offsets: list[int]  # stage_part_offsets[stage] -> first part index in this stage


@dataclass
class MeshPart:
    """Decoded triangle list ready for glTF export."""
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)  # uint32 triangle indices
    dye_slot: int = 0
    lod_name: str = ""

    @property
    def has_normals(self) -> bool:
        return len(self.normals) == len(self.positions) and self.positions


# --- render_metadata.js parser ------------------------------------------------

def _strip_semantic(s: str) -> str:
    return s[len(_SEMANTIC_PREFIX):] if s.startswith(_SEMANTIC_PREFIX) else s


def _strip_type(s: str) -> str:
    return s[len(_TYPE_PREFIX):] if s.startswith(_TYPE_PREFIX) else s


def _parse_metadata_json(raw: bytes) -> list[RenderMesh]:
    text = raw.decode("utf-8", errors="replace")
    # The file is JSON in practice; tolerate a leading "var X = " wrapper just in case.
    text = text.strip()
    if not text.startswith("{") and "=" in text:
        text = text.split("=", 1)[1].strip().rstrip(";")
    doc = json.loads(text)

    meshes_raw = doc.get("render_meshes") or doc.get("renderMeshes") or []
    out: list[RenderMesh] = []
    for m in meshes_raw:
        out.append(_parse_render_mesh(m))
    return out


def _parse_render_mesh(m: dict) -> RenderMesh:
    ps = m.get("position_scale", [1.0, 1.0, 1.0])
    po = m.get("position_offset", [0.0, 0.0, 0.0])
    # texcoord may appear as either separate scale/offset vec2s or a combined [sx,sy,tx,ty].
    if "texcoord0_scale_offset" in m:
        sx, sy, tx, ty = m["texcoord0_scale_offset"]
        tc_scale = (float(sx), float(sy))
        tc_offset = (float(tx), float(ty))
    else:
        ts = m.get("texcoord_scale", [1.0, 1.0])
        to = m.get("texcoord_offset", [0.0, 0.0])
        tc_scale = (float(ts[0]), float(ts[1]))
        tc_offset = (float(to[0]), float(to[1]))

    ib_raw = m["index_buffer"]
    ib = IndexBufferRef(
        file_name=ib_raw["file_name"],
        byte_size=int(ib_raw["byte_size"]),
        value_byte_size=int(ib_raw.get("value_byte_size", 2)),
    )

    vbs = [
        VertexBufferRef(
            file_name=v["file_name"],
            byte_size=int(v["byte_size"]),
            stride=int(v.get("stride_byte_size") or v.get("stride") or 0),
        )
        for v in m.get("vertex_buffers", [])
    ]

    # Vertex layouts: stage_part_vertex_stream_layout_definitions[0].formats[i].elements[]
    layouts: list[list[VertexElement]] = []
    defs = m.get("stage_part_vertex_stream_layout_definitions") or []
    if defs:
        formats = defs[0].get("formats") or []
        for fmt in formats:
            elements = []
            for el in fmt.get("elements", []):
                elements.append(
                    VertexElement(
                        offset=int(el.get("offset", 0)),
                        semantic=_strip_semantic(el.get("semantic", "")),
                        semantic_index=int(el.get("semantic_index", 0)),
                        type_name=_strip_type(el.get("type", "")),
                        normalized=bool(el.get("normalized", False)),
                    )
                )
            layouts.append(elements)
    # Ensure one layout per vertex buffer (pad with empty if mismatch).
    while len(layouts) < len(vbs):
        layouts.append([])

    parts = []
    for p in m.get("stage_part_list", []):
        lod = p.get("lod_category") or {}
        parts.append(
            StagePart(
                external_identifier=int(p.get("external_identifier", 0)),
                primitive_type=int(p.get("primitive_type", 3)),
                start_index=int(p.get("start_index", 0)),
                index_count=int(p.get("index_count", 0)),
                index_min=int(p.get("index_min", 0)),
                index_max=int(p.get("index_max", 0)),
                lod_name=str(lod.get("name", "")),
                dye_slot=int(p.get("gear_dye_change_color_index", 0)),
            )
        )

    return RenderMesh(
        position_scale=(float(ps[0]), float(ps[1]), float(ps[2])),
        position_offset=(float(po[0]), float(po[1]), float(po[2])),
        texcoord_scale=tc_scale,
        texcoord_offset=tc_offset,
        vertex_buffers=vbs,
        index_buffer=ib,
        layout_per_buffer=layouts,
        stage_parts=parts,
        stage_part_offsets=[int(x) for x in m.get("stage_part_offsets", [])],
    )


# --- vertex / index decode ----------------------------------------------------

def _read_indices(buf: bytes, value_byte_size: int) -> list[int]:
    if value_byte_size == 2:
        count = len(buf) // 2
        return list(struct.unpack_from(f"<{count}H", buf))
    if value_byte_size == 4:
        count = len(buf) // 4
        return list(struct.unpack_from(f"<{count}I", buf))
    raise ValueError(f"Unsupported index stride: {value_byte_size}")


def _strip_to_list(strip: list[int]) -> list[int]:
    """Convert a triangle strip to a triangle list, dropping degenerates."""
    out: list[int] = []
    for i in range(len(strip) - 2):
        a, b, c = strip[i], strip[i + 1], strip[i + 2]
        if a == b or b == c or a == c:
            continue
        # Flip every other triangle to preserve winding.
        if i % 2 == 0:
            out.extend((a, b, c))
        else:
            out.extend((a, c, b))
    return out


def _decode_vertex(
    vb_data: list[bytes],
    layouts: list[list[VertexElement]],
    strides: list[int],
    vertex_index: int,
) -> dict[str, list[float]]:
    """Pull all attributes for one vertex across all vertex buffers."""
    attrs: dict[str, list[float]] = {}
    for buf, layout, stride in zip(vb_data, layouts, strides):
        if not layout or stride == 0:
            continue
        base = vertex_index * stride
        if base + stride > len(buf):
            continue
        for el in layout:
            vals = _decode_attr(buf, base + el.offset, el.type_name, el.normalized)
            key = el.semantic if el.semantic_index == 0 else f"{el.semantic}{el.semantic_index}"
            attrs.setdefault(key, vals)
    return attrs


def decode_tgxm(
    archive: TGXMArchive,
    *,
    stages: tuple[int, ...] = (0,),
    lod_filter: tuple[str, ...] = ("0",),
) -> list[MeshPart]:
    """Top-level: parse render_metadata.js, decode every selected stage part."""
    # render_metadata.js lookup is forgiving on extension.
    meta_entry = next(
        (
            e
            for e in archive.entries
            if e.name.endswith("render_metadata.js")
            or e.name.endswith("render_metadata.json")
        ),
        None,
    )
    if meta_entry is None:
        # Some builds use a slightly different name.
        meta_entry = next(
            (e for e in archive.entries if "render_metadata" in e.name), None
        )
    if meta_entry is None:
        return []

    meshes = _parse_metadata_json(archive.data[meta_entry.offset : meta_entry.offset + meta_entry.size])
    out: list[MeshPart] = []

    for mesh in meshes:
        # Load all referenced buffers from the archive.
        try:
            vb_blobs = [archive.read(vb.file_name) for vb in mesh.vertex_buffers]
            idx_blob = archive.read(mesh.index_buffer.file_name)
        except KeyError:
            continue

        indices_all = _read_indices(idx_blob, mesh.index_buffer.value_byte_size)
        strides = [vb.stride for vb in mesh.vertex_buffers]

        # Decide which stage parts to keep.
        kept_parts: list[StagePart] = []
        if mesh.stage_part_offsets:
            for stage_idx in stages:
                if stage_idx >= len(mesh.stage_part_offsets):
                    continue
                start = mesh.stage_part_offsets[stage_idx]
                end = (
                    mesh.stage_part_offsets[stage_idx + 1]
                    if stage_idx + 1 < len(mesh.stage_part_offsets)
                    else len(mesh.stage_parts)
                )
                kept_parts.extend(mesh.stage_parts[start:end])
        else:
            kept_parts = list(mesh.stage_parts)

        # LOD filter.
        kept_parts = [
            p
            for p in kept_parts
            if not lod_filter or any(tag in p.lod_name for tag in lod_filter)
        ]

        for part in kept_parts:
            tri_indices = _build_part_indices(indices_all, part)
            if not tri_indices:
                continue
            mesh_part = _decode_part(mesh, vb_blobs, strides, tri_indices)
            mesh_part.dye_slot = part.dye_slot
            mesh_part.lod_name = part.lod_name
            if mesh_part.positions:
                out.append(mesh_part)

    return out


def _build_part_indices(all_indices: list[int], part: StagePart) -> list[int]:
    chunk = all_indices[part.start_index : part.start_index + part.index_count]
    if part.primitive_type == 5:  # TRIANGLE_STRIP
        return _strip_to_list(chunk)
    if part.primitive_type == 3:  # TRIANGLES
        return list(chunk)
    # Unknown primitive — skip rather than fabricate.
    return []


def _decode_part(
    mesh: RenderMesh,
    vb_blobs: list[bytes],
    strides: list[int],
    triangle_indices: list[int],
) -> MeshPart:
    """Decode the unique vertices touched by `triangle_indices` and remap them."""
    unique: dict[int, int] = {}
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    has_normals = True
    has_uvs = True

    psx, psy, psz = mesh.position_scale
    pox, poy, poz = mesh.position_offset
    tsx, tsy = mesh.texcoord_scale
    tox, toy = mesh.texcoord_offset

    remapped: list[int] = []
    for src_idx in triangle_indices:
        if src_idx in unique:
            remapped.append(unique[src_idx])
            continue
        attrs = _decode_vertex(vb_blobs, mesh.layout_per_buffer, strides, src_idx)

        pos = attrs.get("position")
        if not pos or len(pos) < 3:
            continue  # cannot reconstruct
        x = pos[0] * psx + pox
        y = pos[1] * psy + poy
        z = pos[2] * psz + poz
        positions.append((x, y, z))

        n = attrs.get("normal")
        if n and len(n) >= 3:
            # Normalize defensively — SNORM packing can leave it slightly off-unit.
            nx, ny, nz = n[0], n[1], n[2]
            mag = math.sqrt(nx * nx + ny * ny + nz * nz)
            if mag > 1e-6:
                normals.append((nx / mag, ny / mag, nz / mag))
            else:
                normals.append((0.0, 0.0, 1.0))
        else:
            has_normals = False

        uv = attrs.get("texcoord")
        if uv and len(uv) >= 2:
            u = uv[0] * tsx + tox
            v = uv[1] * tsy + toy
            # glTF/Blender expect V flipped relative to D-engine convention.
            uvs.append((u, 1.0 - v))
        else:
            has_uvs = False

        new_idx = len(positions) - 1
        unique[src_idx] = new_idx
        remapped.append(new_idx)

    if not has_normals:
        normals = []
    if not has_uvs:
        uvs = []

    return MeshPart(
        positions=positions,
        normals=normals,
        uvs=uvs,
        indices=remapped,
    )
