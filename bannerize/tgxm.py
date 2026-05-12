"""TGXM v1/v2 container parser.

The format is documented by Bungie's own `spasm_tgx_bin_loader.js` (shipped
inside lowlines/destiny-tgx-loader/bnet-src/spasm/). Header layout:

    +0x000  char     magic[4]            = "TGXM"
    +0x004  uint32   version             (1 or 2; D2 uses 2)
    +0x008  uint32   file_table_entry_size
    +0x00C  uint32   file_count

  v1: file table starts at 0x10. Each entry = 128-byte name + uint64 offset + uint64 size.
  v2: a 256-byte pack identifier follows at 0x10; file table starts at 0x110.
      Each entry = 256-byte name + uint64 offset + uint64 size  (272 bytes total).

All multibyte ints are little-endian. Offsets/sizes are u64 (read as 8 bytes);
they happen to fit in u32 in practice, but reading them as u64 matches Bungie's
JS reference and avoids surprises with large textures.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


MAGIC = b"TGXM"


@dataclass
class TGXMEntry:
    name: str
    offset: int
    size: int


@dataclass
class TGXMArchive:
    version: int
    pack_id: str
    entries: list[TGXMEntry]
    data: bytes  # the full container buffer; entries reference byte ranges

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def read(self, name: str) -> bytes:
        for e in self.entries:
            if e.name == name:
                return self.data[e.offset : e.offset + e.size]
        raise KeyError(f"TGXM entry not found: {name!r}")

    def find(self, predicate) -> list[TGXMEntry]:
        return [e for e in self.entries if predicate(e)]


def parse(buffer: bytes) -> TGXMArchive:
    if len(buffer) < 16 or buffer[:4] != MAGIC:
        raise ValueError("Not a TGXM container (bad magic).")
    version, entry_size, file_count = struct.unpack_from("<III", buffer, 4)

    if version == 1:
        name_len = 128
        cursor = 0x10
        pack_id = ""
    elif version == 2:
        name_len = 256
        pack_id = _read_cstr(buffer, 0x10, 256)
        cursor = 0x110
    else:
        raise ValueError(f"Unknown TGXM version: {version}")

    expected_entry = name_len + 16  # u64 offset + u64 size
    if entry_size and entry_size != expected_entry:
        # Honour the header's stated entry size if it disagrees.
        expected_entry = entry_size

    entries: list[TGXMEntry] = []
    for i in range(file_count):
        base = cursor + i * expected_entry
        name = _read_cstr(buffer, base, name_len)
        offset, size = struct.unpack_from("<QQ", buffer, base + name_len)
        entries.append(TGXMEntry(name=name, offset=offset, size=size))

    return TGXMArchive(version=version, pack_id=pack_id, entries=entries, data=buffer)


def _read_cstr(buf: bytes, start: int, length: int) -> str:
    chunk = buf[start : start + length]
    nul = chunk.find(b"\x00")
    if nul != -1:
        chunk = chunk[:nul]
    return chunk.decode("utf-8", errors="replace")


# ---- texture image dispatch ----

def sniff_image_extension(data: bytes) -> str:
    """Return 'png' or 'jpg' based on file magic; Bungie stores raw PNG/JPEG."""
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return "png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpg"
    # Per WriteCOLLADA.cs: dispatch only on bytes[1..3] == "PNG", default JPEG.
    if len(data) >= 4 and data[1:4] == b"PNG":
        return "png"
    return "jpg"
