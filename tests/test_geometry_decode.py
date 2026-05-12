"""End-to-end test of TGXM → MeshPart decode on a hand-crafted archive.

Builds a TGXM v2 containing a render_metadata.js that describes:
  - one render mesh, one triangle list (3 indices), one vertex buffer
  - positions packed as SNORM16 short4 with position_scale = (2,2,2),
    position_offset = (10,20,30) — so the decoded positions should land at
    well-known floats we can assert against.
"""
from __future__ import annotations

import json
import math
import struct

from bannerize.geometry import decode_tgxm
from bannerize.tgxm import parse as parse_tgxm
from tests.test_tgxm import _build_tgxm_v2


def _short4(x: int, y: int, z: int, w: int = 0) -> bytes:
    return struct.pack("<4h", x, y, z, w)


def test_decode_simple_triangle():
    # Vertex buffer: 3 vertices × short4 (8 bytes each).
    # We deliberately use vertices at the SNORM extents so decode lands at
    # exactly position_offset ± position_scale.
    vb = _short4(32767, 0, 0) + _short4(0, 32767, 0) + _short4(0, 0, 32767)
    # Index buffer: a single triangle (0, 1, 2) as uint16.
    ib = struct.pack("<3H", 0, 1, 2)

    metadata = {
        "render_meshes": [
            {
                "position_scale": [2.0, 2.0, 2.0],
                "position_offset": [10.0, 20.0, 30.0],
                "texcoord0_scale_offset": [1.0, 1.0, 0.0, 0.0],
                "index_buffer": {
                    "file_name": "0.indexbuffer.tgx",
                    "byte_size": len(ib),
                    "value_byte_size": 2,
                },
                "vertex_buffers": [
                    {"file_name": "0.vertexbuffer.tgx", "byte_size": len(vb), "stride_byte_size": 8}
                ],
                "stage_part_vertex_stream_layout_definitions": [
                    {
                        "formats": [
                            {
                                "elements": [
                                    {
                                        "offset": 0,
                                        "semantic": "_tfx_vb_semantic_position",
                                        "semantic_index": 0,
                                        "type": "_vertex_format_attribute_short4",
                                        "normalized": True,
                                    }
                                ]
                            }
                        ]
                    }
                ],
                "stage_part_list": [
                    {
                        "primitive_type": 3,  # TRIANGLES
                        "start_index": 0,
                        "index_count": 3,
                        "index_min": 0,
                        "index_max": 2,
                        "lod_category": {"name": "lod_category_0"},
                        "gear_dye_change_color_index": 0,
                    }
                ],
                "stage_part_offsets": [0, 1],  # stage 0 covers parts [0,1)
            }
        ]
    }

    archive = parse_tgxm(
        _build_tgxm_v2(
            {
                "render_metadata.js": json.dumps(metadata).encode(),
                "0.indexbuffer.tgx": ib,
                "0.vertexbuffer.tgx": vb,
            }
        )
    )

    parts = decode_tgxm(archive)
    assert len(parts) == 1
    p = parts[0]
    assert len(p.positions) == 3
    assert len(p.indices) == 3

    # Vertex 0: pos_short=(32767,0,0) → normalized (1,0,0) → world (12,20,30).
    assert math.isclose(p.positions[0][0], 12.0, abs_tol=1e-3)
    assert math.isclose(p.positions[0][1], 20.0, abs_tol=1e-3)
    assert math.isclose(p.positions[0][2], 30.0, abs_tol=1e-3)
    # Vertex 1: → world (10, 22, 30).
    assert math.isclose(p.positions[1][1], 22.0, abs_tol=1e-3)
    # Vertex 2: → world (10, 20, 32).
    assert math.isclose(p.positions[2][2], 32.0, abs_tol=1e-3)
