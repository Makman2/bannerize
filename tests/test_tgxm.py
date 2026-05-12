"""Smoke tests for the binary parsers — no network required."""
from __future__ import annotations

import struct

from bannerize.geometry import _strip_to_list, _decode_attr, _normalize
from bannerize.tgxm import parse as parse_tgxm, sniff_image_extension


def _build_tgxm_v2(files: dict[str, bytes]) -> bytes:
    """Build a tiny but spec-correct TGXM v2 archive."""
    name_len = 256
    entry_size = name_len + 16
    header_size = 0x110
    table_size = entry_size * len(files)

    # Plan offsets: header + pack-id + table, then payloads.
    cursor = header_size + table_size
    entries = []
    payload = bytearray()
    for name, blob in files.items():
        # Align each entry to 4 bytes for sanity.
        while (cursor + len(payload) - (header_size + table_size)) % 4 != 0:
            payload.append(0)
        offset = header_size + table_size + len(payload)
        entries.append((name, offset, len(blob)))
        payload.extend(blob)

    buf = bytearray()
    buf.extend(b"TGXM")
    buf.extend(struct.pack("<III", 2, entry_size, len(files)))  # version, entry size, count
    # 256-byte pack identifier
    pack_id = b"test-pack\x00" + b"\x00" * (256 - 10)
    buf.extend(pack_id)
    # File table
    for name, offset, size in entries:
        name_bytes = name.encode("utf-8")
        name_bytes = name_bytes + b"\x00" * (name_len - len(name_bytes))
        buf.extend(name_bytes)
        buf.extend(struct.pack("<QQ", offset, size))
    buf.extend(payload)
    return bytes(buf)


def test_tgxm_v2_roundtrip():
    archive = parse_tgxm(
        _build_tgxm_v2(
            {
                "render_metadata.js": b'{"render_meshes": []}',
                "0.indexbuffer.tgx": b"\x01\x00\x02\x00\x03\x00",
            }
        )
    )
    assert archive.version == 2
    assert archive.pack_id.startswith("test-pack")
    names = archive.names()
    assert "render_metadata.js" in names
    assert "0.indexbuffer.tgx" in names
    assert archive.read("render_metadata.js") == b'{"render_meshes": []}'
    assert archive.read("0.indexbuffer.tgx") == b"\x01\x00\x02\x00\x03\x00"


def test_strip_to_list_drops_degenerates():
    # 0,1,2,2,3,4 strip → triangles (0,1,2) and (2,4,3) (winding flipped on odd i)
    out = _strip_to_list([0, 1, 2, 2, 3, 4])
    # Degenerates at i=1 (1,2,2) and i=2 (2,2,3) are dropped.
    assert out == [0, 1, 2, 2, 4, 3]


def test_normalize_signed_snorm16():
    assert _normalize(32767, signed=True, bits=16) == 1.0
    assert _normalize(-32768, signed=True, bits=16) == -1.0
    assert _normalize(0, signed=True, bits=16) == 0.0


def test_decode_short4_normalized():
    buf = struct.pack("<4h", 32767, 0, -32768, 16384)
    out = _decode_attr(buf, 0, "short4", normalized=True)
    assert out[0] == 1.0
    assert out[1] == 0.0
    assert out[2] == -1.0
    assert abs(out[3] - 16384 / 32767) < 1e-6


def test_sniff_image_extension():
    assert sniff_image_extension(b"\x89PNG\r\n\x1a\n") == "png"
    assert sniff_image_extension(b"\xff\xd8\xff\xe0somejpeg") == "jpg"
