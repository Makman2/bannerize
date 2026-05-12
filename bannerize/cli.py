"""bannerize command-line entry point.

Subcommands:
    bannerize auth         — run the OAuth flow once and persist tokens.
    bannerize export       — fetch profile + write glTF gear into ./out (default).
    bannerize render       — invoke Blender with scene.py to build a .blend / PNG.
    bannerize geometry     — debug: decode a single TGXM file to glTF locally.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .auth import authorize_interactive
from .config import Credentials, TokenSet
from .geometry import decode_tgxm
from .gltf_export import write_gltf
from .pipeline import fetch_and_export
from .tgxm import parse as parse_tgxm


def _cmd_auth(args: argparse.Namespace) -> int:
    creds = Credentials.load()
    tokens = authorize_interactive(creds, open_browser=not args.no_browser)
    tokens.save()
    print("Saved tokens to ~/.config/bannerize/tokens.json")
    print(f"Access expires at {int(tokens.expires_at)} epoch")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    creds = Credentials.load()
    tokens = TokenSet.load()
    if tokens is None:
        print("No stored tokens — run `bannerize auth` first.", file=sys.stderr)
        return 1
    characters = fetch_and_export(
        creds,
        tokens,
        Path(args.out),
        refresh_manifest=args.refresh_manifest,
    )
    print(f"Exported {len(characters)} character(s).")
    for c in characters:
        print(
            f"  {c.class_name} ({c.race_name}, {c.gender_name}) — "
            f"Light {c.light}, {len(c.items)} item(s) with geometry"
        )
    return 0


def _cmd_geometry(args: argparse.Namespace) -> int:
    archive = parse_tgxm(Path(args.tgxm).read_bytes())
    parts = decode_tgxm(archive)
    if not parts:
        print("No mesh parts decoded — is this an item with mobile geometry?", file=sys.stderr)
        return 2
    out = Path(args.out)
    write_gltf(parts, out, name=out.stem)
    print(f"Wrote {out}  ({len(parts)} parts, {sum(len(p.indices) // 3 for p in parts)} tris)")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    scene_json = Path(args.scene)
    if not scene_json.exists():
        print(f"scene.json not found at {scene_json}", file=sys.stderr)
        return 1

    blender = args.blender or shutil.which("blender")
    if not blender:
        print(
            "Blender not found on PATH. Pass --blender /path/to/blender or install it.",
            file=sys.stderr,
        )
        return 1

    scene_script = Path(__file__).resolve().parent / "scene.py"
    out_blend = Path(args.out_blend).resolve()
    out_png = Path(args.out_image).resolve() if args.out_image else None

    cmd = [
        blender,
        "--background",
        "--python",
        str(scene_script),
        "--",
        "--scene-json",
        str(scene_json.resolve()),
        "--out-blend",
        str(out_blend),
    ]
    if args.hdri:
        cmd += ["--hdri", str(Path(args.hdri).resolve())]
    if out_png:
        cmd += ["--out-image", str(out_png)]
    if args.samples:
        cmd += ["--samples", str(args.samples)]

    env = dict(os.environ)
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bannerize")
    sub = p.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="run OAuth flow and save tokens")
    auth.add_argument("--no-browser", action="store_true")
    auth.set_defaults(func=_cmd_auth)

    exp = sub.add_parser("export", help="fetch profile and write glTFs to disk")
    exp.add_argument("--out", default="out", help="output directory (default: ./out)")
    exp.add_argument("--refresh-manifest", action="store_true")
    exp.set_defaults(func=_cmd_export)

    geo = sub.add_parser(
        "geometry", help="decode a single .tgxm to glTF (debug helper)"
    )
    geo.add_argument("tgxm", help="path to a .tgxm geometry container")
    geo.add_argument("out", help="output .gltf path")
    geo.set_defaults(func=_cmd_geometry)

    rnd = sub.add_parser("render", help="build a Cycles-ready Blender scene")
    rnd.add_argument("--scene", default="out/scene.json")
    rnd.add_argument("--out-blend", default="out/banner.blend")
    rnd.add_argument("--out-image", default=None, help="optional PNG render target")
    rnd.add_argument("--hdri", default=None, help="optional .exr/.hdr environment map")
    rnd.add_argument("--samples", type=int, default=128)
    rnd.add_argument("--blender", default=None, help="path to blender executable")
    rnd.set_defaults(func=_cmd_render)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
